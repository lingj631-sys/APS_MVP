"""Fixed operator catalog.

Nineteen operators are frozen product-side: the set and their parameter schemas
do not change per factory. A factory only supplies ``RuleInstance`` values.
Only the operators listed in ``IMPLEMENTED_TEMPLATES`` have a compiler today;
the rest are declared so a rule set can be authored, validated and reviewed
before its compiler lands.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str  # ratio | int | number | bool | enum | str
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    choices: Tuple[str, ...] = ()
    description: str = ""

    def coerce(self, value: Any) -> Any:
        if self.type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            lowered = str(value).strip().lower()
            if lowered in ("true", "1", "yes", "y"):
                return True
            if lowered in ("false", "0", "no", "n"):
                return False
            raise ValueError(f"{self.name}: 不是布尔值 ({value!r})")
        if self.type == "ratio":
            number = float(value)
            if self.min is not None and number < self.min - 1e-12:
                raise ValueError(f"{self.name}: {number} < {self.min}")
            if self.max is not None and number > self.max + 1e-12:
                raise ValueError(f"{self.name}: {number} > {self.max}")
            return number
        if self.type == "int":
            if int(value) != float(value):
                raise ValueError(f"{self.name}: 需要整数，得到 {value!r}")
            number = int(value)
            if self.min is not None and number < self.min:
                raise ValueError(f"{self.name}: {number} < {self.min}")
            if self.max is not None and number > self.max:
                raise ValueError(f"{self.name}: {number} > {self.max}")
            return number
        if self.type == "number":
            number = float(value)
            if self.min is not None and number < self.min - 1e-12:
                raise ValueError(f"{self.name}: {number} < {self.min}")
            if self.max is not None and number > self.max + 1e-12:
                raise ValueError(f"{self.name}: {number} > {self.max}")
            return number
        if self.type == "enum":
            text = str(value)
            if text not in self.choices:
                raise ValueError(f"{self.name}: {text!r} 不在 {self.choices}")
            return text
        return str(value)


@dataclass(frozen=True)
class TemplateSpec:
    template_id: str
    description: str
    params: Tuple[ParamSpec, ...]
    default_nature: str
    default_group: str

    def validate(self, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        supplied = dict(params or {})
        known = {spec.name for spec in self.params}
        unknown = set(supplied) - known
        if unknown:
            raise ValueError(f"{self.template_id}: 未知参数 {sorted(unknown)}")
        resolved = {}
        for spec in self.params:
            resolved[spec.name] = spec.coerce(supplied.get(spec.name, spec.default))
        return resolved

    def param(self, name: str) -> ParamSpec:
        for spec in self.params:
            if spec.name == name:
                return spec
        raise KeyError(name)


CAP = ParamSpec("min_util", "ratio", 0.95, 0.0, 1.0, description="已开班最低利用率")
CAP_MAX = ParamSpec("max_util", "ratio", 1.0, 0.5, 1.5, description="班次产能上限倍数")
TAIL = ParamSpec("tail_exempt", "bool", True, description="允许最后一个开班免 95%")
SETUP = ParamSpec("include_setup", "bool", False, description="换型工时是否另计入容量（TRP 已含时保持 False）")

TEMPLATES: Dict[str, TemplateSpec] = {
    "capacity_utilization": TemplateSpec(
        "capacity_utilization", "班次产能利用率区间（开班决策 + 95% 下限 / setup 占用）",
        (
            CAP, CAP_MAX, TAIL, SETUP,
            ParamSpec("setup_minutes_per_start", "number", 0.0, 0.0, 480.0),
        ),
        "soft", "G2_CAPACITY",
    ),
    "min_batch": TemplateSpec(
        "min_batch", "最小投产批量 / MPQ 整倍 / MOQ 启动门槛",
        (
            ParamSpec("unit", "enum", "MPQ", choices=("MPQ", "piece")),
            ParamSpec("rounding", "enum", "ceil", choices=("ceil", "floor", "nearest")),
            ParamSpec("min_multiple", "int", 1, 1, 100),
            ParamSpec("basis", "enum", "shift", choices=("shift", "order")),
            ParamSpec("quantity_source", "enum", "MPQ", choices=("MPQ", "MOQ")),
            ParamSpec("tail_exempt", "bool", False),
            ParamSpec("moq_override", "number", 0.0, 0.0),
        ),
        "hard", "G0_PHYSICAL",
    ),
    "contiguity": TemplateSpec(
        "contiguity", "工单连续生产（块数上限）",
        (
            ParamSpec("max_blocks", "int", 1, 1, 1000),
            ParamSpec("mode", "enum", "soft", choices=("hard", "soft")),
        ),
        "soft", "G3_CHANGEOVER",
    ),
    "changeover": TemplateSpec(
        "changeover", "换型次数/工时（TRP 已含换型时 setup_minutes=0）",
        (
            ParamSpec("setup_minutes", "number", 0.0, 0.0, 480.0),
            ParamSpec("count_by", "enum", "run_start", choices=("run_start", "model_switch")),
            ParamSpec("cross_shift_inherit", "bool", True),
        ),
        "soft", "G3_CHANGEOVER",
    ),
    "material_balance": TemplateSpec(
        "material_balance", "前后工序齐套与半成品缓冲",
        (
            ParamSpec("buffer_shifts", "int", 0, 0, 20),
            ParamSpec("include_stock", "bool", True),
        ),
        "hard", "G0_PHYSICAL",
    ),
    "material_ready": TemplateSpec(
        "material_ready", "关键物料 ETA/PO 到货门控",
        (
            ParamSpec("source", "enum", "PO", choices=("PO", "ETA", "STOCK")),
            ParamSpec("qc_release_hours", "number", 2.0, 0.0, 240.0),
        ),
        "hard", "G0_PHYSICAL",
    ),
    "due_date": TemplateSpec(
        "due_date", "交期/截单（班次级）",
        (
            ParamSpec("cutoff_time", "str", "15:00"),
            ParamSpec("tolerance_minutes", "number", 0.0, 0.0, 1440.0),
            ParamSpec("mode", "enum", "hard", choices=("hard", "soft")),
        ),
        "hard", "G1_DELIVERY",
    ),
    "sequence": TemplateSpec(
        "sequence", "同料号顺位前拉（FIFO）",
        (
            ParamSpec("order_by", "enum", "due", choices=("due", "fifo", "priority")),
            ParamSpec("tolerance_shifts", "int", 0, 0, 20),
        ),
        "soft", "G1_DELIVERY",
    ),
    "calendar": TemplateSpec(
        "calendar", "工作日历/班次模式",
        (
            ParamSpec("mode", "enum", "SEVEN_ZERO", choices=("AUTO", "SEVEN_ZERO", "FIVE_TWO", "FOUR_THREE")),
            ParamSpec("weekend_off", "bool", False),
        ),
        "hard", "G2_CAPACITY",
    ),
    "tooling_exclusivity": TemplateSpec(
        "tooling_exclusivity", "模具/工装物理独占",
        (
            ParamSpec("resource", "enum", "mold", choices=("mold", "tool")),
            ParamSpec("available_qty", "int", 1, 1, 100),
        ),
        "hard", "G0_PHYSICAL",
    ),
    "pm_block": TemplateSpec(
        "pm_block", "设备 PM 维保避让",
        (
            ParamSpec("handling", "enum", "avoid", choices=("avoid", "split", "suspend")),
            ParamSpec("capacity_rate", "ratio", 0.0, 0.0, 1.0),
        ),
        "hard", "G0_PHYSICAL",
    ),
    "concurrency": TemplateSpec(
        "concurrency", "单班并行型号/换模频次上限",
        (
            ParamSpec("max_models", "int", 3, 1, 50),
            ParamSpec("window", "enum", "shift", choices=("shift", "day")),
        ),
        "soft", "G3_CHANGEOVER",
    ),
    "inventory_bound": TemplateSpec(
        "inventory_bound", "库存上下限 / 覆盖天数",
        (
            ParamSpec("min_days", "number", 0.0, 0.0, 365.0),
            ParamSpec("max_days", "number", 5.0, 0.0, 365.0),
            ParamSpec("safety_stock", "number", 0.0, 0.0),
        ),
        "soft", "G4_INVENTORY",
    ),
    "customer_priority": TemplateSpec(
        "customer_priority", "客户分级保供",
        (
            ParamSpec("tier_weights", "str", "STRATEGIC:10,A:3,B:1"),
            ParamSpec("due_window_days", "int", 1, 0, 30),
        ),
        "soft", "G1_DELIVERY",
    ),
    "time_window": TemplateSpec(
        "time_window", "受控时间窗（首批/试制：日期 + 班次）",
        (
            ParamSpec("allowed_shifts", "str", "早班,中班"),
            ParamSpec("allowed_dates", "str", ""),
            ParamSpec("min_duration_hours", "number", 0.0, 0.0, 24.0),
        ),
        "hard", "G0_PHYSICAL",
    ),
    "co_product": TemplateSpec(
        "co_product", "联产品/共模同步产出（如 L/R 1:1）",
        (
            ParamSpec("ratio", "str", "1:1"),
            ParamSpec("group_code", "str", ""),
        ),
        "hard", "G0_PHYSICAL",
    ),
    "routing": TemplateSpec(
        "routing", "替代产线 / 多线可产",
        (
            ParamSpec("max_candidates", "int", 2, 1, 20),
            ParamSpec("mode", "enum", "primary", choices=("primary", "balanced")),
        ),
        "hard", "G0_PHYSICAL",
    ),
    "freeze": TemplateSpec(
        "freeze", "冻结期锁定 / 基线保持",
        (
            ParamSpec("fence_shifts", "int", 3, 0, 200),
            ParamSpec("mode", "enum", "hard", choices=("hard", "soft")),
            ParamSpec("use_baseline", "bool", False),
        ),
        "hard", "G0_PHYSICAL",
    ),
    "labor_capacity": TemplateSpec(
        "labor_capacity", "人力上限 / 共享班组 / 换模技术员",
        (
            ParamSpec("available_headcount", "int", 0, 0, 10000),
            ParamSpec("crew_source", "enum", "line", choices=("line", "part")),
            ParamSpec("allow_overtime", "bool", False),
            ParamSpec("overtime_max_hours", "number", 0.0, 0.0, 24.0),
            ParamSpec("setup_technicians_required", "int", 0, 0, 100),
            ParamSpec("setup_technicians_available", "int", 0, 0, 10000),
        ),
        "hard", "G2_CAPACITY",
    ),
}

IMPLEMENTED_TEMPLATES = frozenset(
    {
        "capacity_utilization",
        "min_batch",
        "due_date",
        "changeover",
        "tooling_exclusivity",
        "pm_block",
        "material_ready",
        "material_balance",
        "sequence",
        "customer_priority",
        "concurrency",
        "labor_capacity",
        "contiguity",
        "calendar",
        "time_window",
        "inventory_bound",
        "freeze",
        "co_product",
    }
)


def get_template(template_id: str) -> TemplateSpec:
    try:
        return TEMPLATES[template_id]
    except KeyError as exc:
        raise KeyError(f"unknown template: {template_id}") from exc


def validate_params(template_id: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return get_template(template_id).validate(params)
