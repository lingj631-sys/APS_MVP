from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

from aps.schema.types import ContractTier, DataType, DupPolicy

__all__ = [
    "DEPRECATED_HEADERS",
    "PARSERS",
    "REGISTRY",
    "STAGE_NORMALIZE",
    "EntitySpec",
    "FieldSpec",
    "entities_with_sheet",
    "get_entity",
    "synthesized_entities",
]

SITE = "站点"
PN = "料号 (PN)"
DESC = "品名描述"

STAGE_NORMALIZE = {
    "FING": "FG", "SFIN": "SF", "FG": "FG", "SF": "SF",
    "COMP": "COMP", "PACK": "PACK", "RAW": "RAW",
}

PARSERS = ("enum_code", "stage", "shift_index")

DEPRECATED_HEADERS: dict[str, tuple[str, ...]] = {
    "产线与成品参数 Parameters-FG": ("产线", "效率 TRP"),
    "半成品制造参数 Parameters-SFN": ("产线", "效率 TRP"),
    "工艺路线与候选机台 Routing-Machines": ("备选候选线", "机台", "TRP"),
    "关键物料到货 Material-Arrival-ETA": ("放行工时", "关联排产零件", "状态"),
}


@dataclass(frozen=True)
class FieldSpec:
    name: str
    dtype: DataType
    tier: ContractTier = ContractTier.T1
    source_columns: tuple[str, ...] = ()
    nullable: bool = True
    pk: bool = False
    fk: str | None = None
    unit: str | None = None
    enum_codes: tuple[str, ...] | None = None
    parser: str | None = None
    fk_optional: bool = False
    provenance: str = "template"

    @property
    def resolved_parser(self) -> str | None:
        if self.parser is not None:
            return self.parser
        return "enum_code" if self.enum_codes else None


@dataclass(frozen=True)
class EntitySpec:
    name: str
    sheet: str | None
    fields: tuple[FieldSpec, ...]
    pk: tuple[str, ...]
    dup_policy: DupPolicy = DupPolicy.ERROR
    sum_fields: tuple[str, ...] = ()
    sheet_required: bool = True
    unpivot: str | None = None
    site_injected: bool = False
    synthesized: bool = False
    _index: dict[str, FieldSpec] = dc_field(
        default_factory=dict, compare=False, repr=False, hash=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_index", {f.name: f for f in self.fields})

    def field(self, name: str) -> FieldSpec:
        try:
            return self._index[name]
        except KeyError:
            raise KeyError(f"{self.name} 无字段 {name!r}") from None

    @property
    def pk_fields(self) -> tuple[FieldSpec, ...]:
        return tuple(self._index[n] for n in self.pk)

    @property
    def header_to_field(self) -> dict[str, FieldSpec]:
        out: dict[str, FieldSpec] = {}
        for spec in self.fields:
            for header in spec.source_columns:
                out[header] = spec
        return out

    @property
    def required_headers(self) -> frozenset[str]:
        return frozenset(
            spec.source_columns[0]
            for spec in self.fields
            if spec.tier is ContractTier.T1 and spec.source_columns
        )

    @property
    def deprecated_headers(self) -> frozenset[str]:
        if self.sheet is None:
            return frozenset()
        return frozenset(DEPRECATED_HEADERS.get(self.sheet, ()))

    def validate_self(self) -> list[str]:
        problems: list[str] = []
        names = set(self._index)
        for pk_name in self.pk:
            if pk_name not in names:
                problems.append(f"{self.name}: 主键 {pk_name!r} 未在 fields 中声明")
                continue
            spec = self._index[pk_name]
            if not spec.pk:
                problems.append(f"{self.name}.{pk_name}: 在主键中但未标 pk=True")
            if spec.nullable:
                problems.append(f"{self.name}.{pk_name}: 主键字段不得 nullable")
            if spec.tier is not ContractTier.T1:
                problems.append(f"{self.name}.{pk_name}: 主键字段必须是 T1")
        for spec in self.fields:
            if not spec.source_columns and not (self.site_injected and spec.name == "site_code") \
                    and not self.synthesized and spec.provenance == "template":
                problems.append(f"{self.name}.{spec.name}: 缺少 source_columns")
            if spec.fk is not None:
                target_entity, _, target_field = spec.fk.partition(".")
                if target_entity not in REGISTRY:
                    problems.append(f"{self.name}.{spec.name}: 外键指向未知实体 {target_entity!r}")
                elif target_field and target_field not in REGISTRY[target_entity]._index:
                    problems.append(
                        f"{self.name}.{spec.name}: 外键指向未知字段 {spec.fk!r}"
                    )
            if spec.parser is not None and spec.parser not in PARSERS:
                problems.append(f"{self.name}.{spec.name}: 未知 parser {spec.parser!r}")
            if spec.fk_optional and spec.fk is None:
                problems.append(f"{self.name}.{spec.name}: fk_optional=True 但未声明 fk")
        for sum_name in self.sum_fields:
            if sum_name not in names:
                problems.append(f"{self.name}: sum_fields 含未声明字段 {sum_name!r}")
        if self.dup_policy is DupPolicy.SUM and not self.sum_fields:
            problems.append(f"{self.name}: dup_policy=SUM 但未声明 sum_fields")
        if self.unpivot is not None and self.unpivot not in names:
            problems.append(f"{self.name}: unpivot 字段 {self.unpivot!r} 未声明")
        return problems


def _f(name: str, dtype: DataType, *source: str, tier: ContractTier = ContractTier.T1,
       nullable: bool = True, pk: bool = False, fk: str | None = None,
       unit: str | None = None, enum_codes: tuple[str, ...] | None = None,
       parser: str | None = None, fk_optional: bool = False,
       provenance: str = "template") -> FieldSpec:
    return FieldSpec(
        name=name, dtype=dtype, tier=tier, source_columns=source, nullable=nullable,
        pk=pk, fk=fk, unit=unit, enum_codes=enum_codes, parser=parser,
        fk_optional=fk_optional, provenance=provenance,
    )


_SITE_PK = _f("site_code", DataType.TEXT, SITE, pk=True, nullable=False, fk="site.site_code")

_REG: dict[str, EntitySpec] = {}


def _add(spec: EntitySpec) -> None:
    _REG[spec.name] = spec


# ---------- 维度表（7） ----------

_add(EntitySpec(
    name="site", sheet=None, synthesized=True,
    pk=("site_code",),
    fields=(
        _f("site_code", DataType.TEXT, pk=True, nullable=False, provenance="derived"),
        _f("name", DataType.TEXT, provenance="derived"),
        _f("default_calendar_mode", DataType.TEXT, tier=ContractTier.T2, provenance="derived",
           enum_codes=("CONTINUOUS", "6ON1OFF", "5ON2OFF", "4ON3OFF")),
    ),
))

_add(EntitySpec(
    name="line", sheet="产线基础数据", site_injected=True,
    pk=("site_code", "line_code"),
    fields=(
        _f("site_code", DataType.TEXT, pk=True, nullable=False, fk="site.site_code"),
        _f("line_code", DataType.TEXT, "产线", pk=True, nullable=False),
        _f("name", DataType.TEXT, "产线"),
        _f("trp_efficiency", DataType.REAL, "效率 TRP", nullable=False, unit="ratio"),
        _f("shift_hours", DataType.REAL, "单班工时 (小时)", nullable=False, unit="小时"),
        _f("shifts_per_day", DataType.INT, "每天班次数", nullable=False, unit="班次"),
    ),
))

_add(EntitySpec(
    name="part", sheet=None, synthesized=True,
    pk=("site_code", "pn"),
    fields=(
        _f("site_code", DataType.TEXT, pk=True, nullable=False, fk="site.site_code",
           provenance="derived"),
        _f("pn", DataType.TEXT, pk=True, nullable=False, provenance="derived"),
        _f("description", DataType.TEXT, provenance="derived"),
        _f("stage", DataType.TEXT, nullable=False, provenance="derived",
           enum_codes=("FG", "SF", "COMP", "PACK", "RAW"), parser="stage"),
        _f("family_code", DataType.TEXT, tier=ContractTier.T2, fk="product_family.family_code",
           provenance="derived"),
        _f("uom", DataType.TEXT, tier=ContractTier.T2, provenance="derived"),
    ),
))

_add(EntitySpec(
    name="mold", sheet="工装模具约束 Tooling-Mold",
    pk=("site_code", "mold_id"),
    dup_policy=DupPolicy.DEDUPE,
    fields=(
        _SITE_PK,
        _f("mold_id", DataType.TEXT, "模具编号 (Mold ID)", pk=True, nullable=False),
        _f("name", DataType.TEXT, "工装模具名称"),
        _f("available_sets", DataType.INT, "可用套数", unit="套"),
        _f("changeover_min", DataType.REAL, "换模工时 (分钟)", tier=ContractTier.T2, unit="分钟"),
    ),
))

_add(EntitySpec(
    name="customer", sheet="客户战略评级 Customer-Priority",
    pk=("site_code", "customer_code"),
    fields=(
        _SITE_PK,
        _f("customer_code", DataType.TEXT, "客户代码 (Code)", pk=True, nullable=False),
        _f("name", DataType.TEXT, "客户全称"),
        _f("tier", DataType.TEXT, "战略评级 (Tier)", tier=ContractTier.T2, parser="enum_code"),
        _f("supply_weight", DataType.REAL, "保供加权系数", tier=ContractTier.T2),
        _f("otd_target_pct", DataType.REAL, "OTD 准时交付目标 (%)", tier=ContractTier.T2, unit="%"),
        _f("due_tolerance_days", DataType.INT, "交期浮动容差 (天)", tier=ContractTier.T2, unit="天"),
        _f("outsource_policy", DataType.TEXT, "产能紧张外协策略", tier=ContractTier.T2),
        _f("representative_pns", DataType.MULTI_TEXT, "代表配装成品", tier=ContractTier.T2),
    ),
))

_add(EntitySpec(
    name="product_family", sheet=None, synthesized=True,
    pk=("site_code", "family_code"),
    fields=(
        _f("site_code", DataType.TEXT, pk=True, nullable=False, fk="site.site_code",
           provenance="derived"),
        _f("family_code", DataType.TEXT, pk=True, nullable=False, provenance="derived"),
        _f("name", DataType.TEXT, provenance="derived"),
    ),
))

_add(EntitySpec(
    name="calendar_pattern", sheet=None, synthesized=True,
    pk=("pattern_code",),
    fields=(
        _f("pattern_code", DataType.TEXT, pk=True, nullable=False, provenance="derived",
           enum_codes=("CONTINUOUS", "6ON1OFF", "5ON2OFF", "4ON3OFF")),
        _f("work_days", DataType.INT, nullable=False, unit="天", provenance="derived"),
        _f("rest_days", DataType.INT, nullable=False, unit="天", provenance="derived"),
    ),
))

# ---------- 事实/主数据表（12） ----------

_add(EntitySpec(
    name="demand", sheet="客户需求订单清单 Customer Demand",
    pk=("site_code", "pn", "demand_date"),
    unpivot="demand_date",
    fields=(
        _SITE_PK,
        _f("pn", DataType.TEXT, PN, pk=True, nullable=False, fk="part.pn"),
        _f("demand_date", DataType.DATE, pk=True, nullable=False, provenance="derived"),
        _f("description", DataType.TEXT, DESC),
        _f("quantity", DataType.INT, nullable=False, unit="件", provenance="derived"),
        _f("customer_code", DataType.TEXT, "客户代码 (Code)", tier=ContractTier.T2,
           fk="customer.customer_code"),
    ),
))

_add(EntitySpec(
    name="initial_stock", sheet="期初可用库存 Initial Stock",
    pk=("site_code", "pn", "material_type"),
    dup_policy=DupPolicy.SUM, sum_fields=("quantity",),
    fields=(
        _SITE_PK,
        _f("pn", DataType.TEXT, "物料料号 (PN)", pk=True, nullable=False),
        _f("material_type", DataType.TEXT, "物料类型", pk=True, nullable=False,
           enum_codes=("FG", "SF", "COMP", "PACK", "RAW"), parser="stage"),
        _f("description", DataType.TEXT, DESC),
        _f("quantity", DataType.REAL, "期初库存 (件)", nullable=False, unit="件"),
    ),
))

_add(EntitySpec(
    name="material_arrival", sheet="关键物料到货 Material-Arrival-ETA",
    pk=("site_code", "pn", "eta_date", "eta_shift", "seq"),
    fields=(
        _SITE_PK,
        _f("pn", DataType.TEXT, "物料料号 (PN)", pk=True, nullable=False),
        _f("eta_date", DataType.DATE, "预计入库日期", pk=True, nullable=False),
        _f("eta_shift", DataType.INT, "预计入库班次", pk=True, nullable=False, unit="班次",
           parser="shift_index"),
        _f("seq", DataType.INT, pk=True, nullable=False, provenance="derived"),
        _f("supplier", DataType.TEXT, "合格供应商", tier=ContractTier.T2),
        _f("description", DataType.TEXT, "物料描述"),
        _f("eta_time", DataType.TIME, "预计入库时间", tier=ContractTier.T2),
        _f("quantity", DataType.INT, "送达数量 (件)", nullable=False, unit="件"),
    ),
))

_add(EntitySpec(
    name="part_param_fg", sheet="产线与成品参数 Parameters-FG",
    pk=("site_code", "pn"),
    fields=(
        _SITE_PK,
        _f("pn", DataType.TEXT, "成品料号 (PN)", pk=True, nullable=False, fk="part.pn"),
        _f("description", DataType.TEXT, DESC),
        _f("mpq", DataType.INT, "最小包装量 MPQ", nullable=False, unit="件"),
        _f("moq", DataType.INT, "最小生产量 MOQ", tier=ContractTier.T2, unit="件"),
        _f("family_code", DataType.TEXT, "产品族名", tier=ContractTier.T2,
           fk="product_family.family_code"),
        _f("mold_id", DataType.TEXT, "模具编号 (Mold ID)", tier=ContractTier.T2, fk="mold.mold_id"),
        _f("special_shift", DataType.TEXT, "特殊班次", tier=ContractTier.T2),
        _f("co_production", DataType.TEXT, "同时生产", tier=ContractTier.T2),
    ),
))

_add(EntitySpec(
    name="part_param_sf", sheet="半成品制造参数 Parameters-SFN",
    pk=("site_code", "pn"),
    fields=(
        _SITE_PK,
        _f("pn", DataType.TEXT, "半成品料号 (PN)", pk=True, nullable=False, fk="part.pn"),
        _f("description", DataType.TEXT, DESC),
        _f("mpq", DataType.INT, "最小起投批量 MPQ", nullable=False, unit="件"),
        _f("scrap_rate", DataType.REAL, "报废率 (小数)", nullable=False, unit="小数"),
        _f("max_stock", DataType.INT, "最大库存 (件)", tier=ContractTier.T2, unit="件"),
        _f("moq", DataType.INT, "最小起订量 MOQ", tier=ContractTier.T2, unit="件"),
        _f("family_code", DataType.TEXT, "产品族名", tier=ContractTier.T2,
           fk="product_family.family_code"),
        _f("mold_id", DataType.TEXT, "模具编号 (Mold ID)", tier=ContractTier.T2, fk="mold.mold_id"),
        _f("special_shift", DataType.TEXT, "特殊班次", tier=ContractTier.T2),
        _f("co_production", DataType.TEXT, "同时生产", tier=ContractTier.T2),
    ),
))

_add(EntitySpec(
    name="bom", sheet="产品装配清单 BOM List",
    pk=("site_code", "parent_pn", "child_pn"),
    fields=(
        _SITE_PK,
        _f("parent_pn", DataType.TEXT, "母件：成品料号 (FG PN)", pk=True, nullable=False,
           fk="part.pn"),
        _f("parent_description", DataType.TEXT, "母件品名描述"),
        _f("child_pn", DataType.TEXT, "子件：半成品料号 (SF PN)", pk=True, nullable=False,
           fk="part.pn", fk_optional=True),
        _f("child_description", DataType.TEXT, "子件品名描述"),
        _f("ratio", DataType.REAL, "装配单耗比例", nullable=False),
    ),
))

_add(EntitySpec(
    name="routing", sheet="工艺路线与候选机台 Routing-Machines",
    pk=("site_code", "pn", "op_seq", "line_code"),
    fields=(
        _SITE_PK,
        _f("pn", DataType.TEXT, PN, pk=True, nullable=False),
        _f("op_seq", DataType.INT, "工序序号", pk=True, nullable=False),
        _f("line_code", DataType.TEXT, "主候选线", pk=True, nullable=False, fk="line.line_code"),
        _f("stage", DataType.TEXT, "阶段", parser="stage",
           enum_codes=("FG", "SF", "COMP", "PACK", "RAW")),
        _f("op_name", DataType.TEXT, "工序名称"),
        _f("certified", DataType.BOOL, "认证", nullable=False),
        _f("ct_sec", DataType.REAL, "CT (秒)", nullable=False, unit="秒"),
        _f("changeover_min", DataType.REAL, "换型时间 (分钟)", tier=ContractTier.T2, unit="分钟"),
        _f("priority", DataType.INT, "优先级", tier=ContractTier.T2),
        _f("valid_from", DataType.DATE, "生效日期", tier=ContractTier.T2),
        _f("valid_to", DataType.DATE, "失效日期", tier=ContractTier.T2),
        _f("per_shift_cap_declared", DataType.INT, "单班定额产能 (件)", tier=ContractTier.T2,
           unit="件"),
        _f("headcount", DataType.INT, "人数", tier=ContractTier.T2, unit="人"),
        _f("family_code", DataType.TEXT, "产品族名", tier=ContractTier.T2,
           fk="product_family.family_code"),
        _f("mold_ids", DataType.MULTI_TEXT, "模具清单", tier=ContractTier.T2),
    ),
))

_add(EntitySpec(
    name="setup_matrix", sheet="换模矩阵 Setup-Matrix",
    pk=("site_code", "line_code", "from_mold", "to_mold"),
    fields=(
        _SITE_PK,
        _f("line_code", DataType.TEXT, "产线", pk=True, nullable=False, fk="line.line_code"),
        _f("from_mold", DataType.TEXT, "起始模具", pk=True, nullable=False, fk="mold.mold_id"),
        _f("to_mold", DataType.TEXT, "目标模具", pk=True, nullable=False, fk="mold.mold_id"),
        _f("changeover_min", DataType.REAL, "换模工时 (分钟)", nullable=False, unit="分钟"),
        _f("technician_count", DataType.INT, "所需技术员数", tier=ContractTier.T2, unit="人"),
        _f("needs_cleaning", DataType.BOOL, "是否需要清洁", tier=ContractTier.T2),
        _f("valid_from", DataType.DATE, "生效日期", tier=ContractTier.T2),
    ),
))

_add(EntitySpec(
    name="inventory_policy", sheet="库存策略 Inventory-Policy",
    pk=("site_code", "pn", "period_type", "start_day"),
    fields=(
        _SITE_PK,
        _f("pn", DataType.TEXT, PN, pk=True, nullable=False),
        _f("period_type", DataType.TEXT, "周期类型", pk=True, nullable=False,
           enum_codes=("NORMAL", "MONTH_END")),
        _f("start_day", DataType.INT, "起始日 (Day)", pk=True, nullable=False, unit="天"),
        _f("end_day", DataType.INT, "结束日 (Day)", nullable=False, unit="天"),
        _f("target_stock_days", DataType.INT, "目标库存天数", tier=ContractTier.T2, unit="天"),
        _f("max_stock_days", DataType.INT, "最大库存天数", tier=ContractTier.T2, unit="天"),
        _f("safety_stock", DataType.INT, "安全库存 (件)", tier=ContractTier.T2, unit="件"),
        _f("max_stock", DataType.INT, "最大库存 (件)", tier=ContractTier.T2, unit="件"),
        _f("valid_from", DataType.DATE, "生效日期", tier=ContractTier.T2),
    ),
))

_add(EntitySpec(
    name="operation_leadtime", sheet="工序提前期 Operation-LeadTime",
    pk=("site_code", "pn", "op_seq"),
    dup_policy=DupPolicy.DEDUPE,
    fields=(
        _SITE_PK,
        _f("pn", DataType.TEXT, PN, pk=True, nullable=False),
        _f("op_seq", DataType.INT, "工序序号", pk=True, nullable=False),
        _f("stage", DataType.TEXT, "阶段", parser="stage",
           enum_codes=("FG", "SF", "COMP", "PACK", "RAW")),
        _f("op_name", DataType.TEXT, "工序名称"),
        _f("prep_minutes", DataType.REAL, "提前准备时间（分钟）", "提前准备时间 (分钟)",
           tier=ContractTier.T2, unit="分钟"),
        _f("transfer_batch", DataType.INT, "转运批量 (件)", tier=ContractTier.T2, unit="件"),
        _f("container", DataType.TEXT, "容器", tier=ContractTier.T2),
    ),
))

_add(EntitySpec(
    name="tooling_mold", sheet="工装模具约束 Tooling-Mold",
    pk=("site_code", "mold_id", "line_code"),
    fields=(
        _SITE_PK,
        _f("mold_id", DataType.TEXT, "模具编号 (Mold ID)", pk=True, nullable=False,
           fk="mold.mold_id"),
        _f("line_code", DataType.TEXT, "适用产线/设备", pk=True, nullable=False,
           fk="line.line_code"),
        _f("applicable_pns", DataType.MULTI_TEXT, "适用零件料号", tier=ContractTier.T2),
        _f("available_sets", DataType.INT, "可用套数", nullable=False, unit="套"),
        _f("changeover_min", DataType.REAL, "换模工时 (分钟)", tier=ContractTier.T2, unit="分钟"),
    ),
))

_add(EntitySpec(
    name="preventive_maintenance", sheet="设备预防维护 Preventive-Maintenance",
    pk=("site_code", "line_code", "start_date", "item"),
    fields=(
        _SITE_PK,
        _f("line_code", DataType.TEXT, "产线", pk=True, nullable=False, fk="line.line_code"),
        _f("start_date", DataType.DATE, "开始日期", pk=True, nullable=False),
        _f("item", DataType.TEXT, "维保项目试制内容", pk=True, nullable=False),
        _f("start_time", DataType.TIME, "开始时间", tier=ContractTier.T2),
        _f("end_date", DataType.DATE, "结束日期", tier=ContractTier.T2),
        _f("end_time", DataType.TIME, "结束时间", tier=ContractTier.T2),
        _f("downtime_hours", DataType.REAL, "停机工时 (小时)", nullable=False, unit="小时"),
        _f("capacity_pct", DataType.REAL, "期间产能 (%)", tier=ContractTier.T2, unit="%"),
        _f("disposition", DataType.TEXT, "工单处置策略", tier=ContractTier.T2,
           parser="enum_code"),
    ),
))

# ---------- V3 扩展（4，修订 A-2） ----------

_add(EntitySpec(
    name="shift_staffing", sheet="班次人力可用性", sheet_required=False,
    pk=("site_code", "line_code", "work_date", "shift_index"),
    fields=(
        _SITE_PK,
        _f("line_code", DataType.TEXT, "产线", pk=True, nullable=False, fk="line.line_code"),
        _f("work_date", DataType.DATE, "日期", pk=True, nullable=False),
        _f("shift_index", DataType.INT, "班次", pk=True, nullable=False, unit="班次"),
        _f("available_headcount", DataType.INT, "可用人数", nullable=False, unit="人"),
    ),
))

_add(EntitySpec(
    name="product_pairing", sheet="产品配对关系", sheet_required=False,
    pk=("site_code", "left_pn", "right_pn"),
    fields=(
        _SITE_PK,
        _f("left_pn", DataType.TEXT, "左件料号", pk=True, nullable=False, fk="part.pn"),
        _f("right_pn", DataType.TEXT, "右件料号", pk=True, nullable=False, fk="part.pn"),
        _f("pair_type", DataType.TEXT, "配对类型", tier=ContractTier.T2),
        _f("sync_tolerance_shifts", DataType.INT, "同步容差班次", tier=ContractTier.T2, unit="班次"),
    ),
))

_add(EntitySpec(
    name="calendar_day", sheet="工作日历", sheet_required=False,
    pk=("site_code", "line_code", "work_date", "shift_index"),
    fields=(
        _SITE_PK,
        _f("line_code", DataType.TEXT, "产线", pk=True, nullable=False, fk="line.line_code"),
        _f("work_date", DataType.DATE, "日期", pk=True, nullable=False),
        _f("shift_index", DataType.INT, "班次序号", pk=True, nullable=False, unit="班次"),
        _f("is_open", DataType.BOOL, "是否开班", nullable=False),
        _f("note", DataType.TEXT, "备注", tier=ContractTier.T2),
    ),
))

_add(EntitySpec(
    name="setup_technician", sheet="换模技术员", sheet_required=False,
    pk=("site_code", "technician", "work_date", "shift_index"),
    fields=(
        _SITE_PK,
        _f("technician", DataType.TEXT, "技术员", pk=True, nullable=False),
        _f("work_date", DataType.DATE, "日期", pk=True, nullable=False),
        _f("shift_index", DataType.INT, "班次", pk=True, nullable=False, unit="班次"),
        _f("available_lines", DataType.MULTI_TEXT, "可用产线", tier=ContractTier.T2),
        _f("max_concurrent_setups", DataType.INT, "可同时换模数", tier=ContractTier.T2),
    ),
))

REGISTRY: dict[str, EntitySpec] = {name: _REG[name] for name in sorted(_REG)}


def get_entity(name: str) -> EntitySpec:
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(f"登记表无实体 {name!r}") from None


def entities_with_sheet() -> tuple[EntitySpec, ...]:
    return tuple(e for e in REGISTRY.values() if e.sheet is not None)


def synthesized_entities() -> tuple[EntitySpec, ...]:
    return tuple(e for e in REGISTRY.values() if e.synthesized)
