"""
Shift calendar manager and dynamic calendar scheduler for the APS MVP.

Implements R-AR-01 (Line-Calendar decoupling), R-CL-01..03 (Three-tier intelligent
calendar selection: 4/3, 5/2, 7/0), and R-BK-01 (holiday backwards due date shift).

Option B three-shift pattern:
- 早班 07:00-15:00, 中班 15:00-23:00, 晚班 23:00-07:00(+1)
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set

SHIFT_HOURS = 8.0
SHIFT_SECONDS = int(SHIFT_HOURS * 3600)
MIN_SHIFT_UTILIZATION = 0.95
MIN_SHIFT_SECONDS = int(-(-SHIFT_SECONDS * MIN_SHIFT_UTILIZATION // 1))  # ceil
SHIFT_NAMES = ("早班", "中班", "晚班")


class CalendarMode(str, Enum):
    AUTO = "AUTO"              # Auto-select based on demand load ratio
    FOUR_THREE = "FOUR_THREE"  # 做四休三: Mon-Thu worked, Fri-Sun off
    FIVE_TWO = "FIVE_TWO"      # 做五休二: Mon-Fri worked, Sat-Sun off
    SEVEN_ZERO = "SEVEN_ZERO"  # 连续生产: Mon-Sun worked, no off days


# Monday is 0, Sunday is 6
WORKING_DAYS_BY_MODE: Dict[CalendarMode, Set[int]] = {
    CalendarMode.FOUR_THREE: {0, 1, 2, 3},       # Mon, Tue, Wed, Thu
    CalendarMode.FIVE_TWO: {0, 1, 2, 3, 4},       # Mon, Tue, Wed, Thu, Fri
    CalendarMode.SEVEN_ZERO: {0, 1, 2, 3, 4, 5, 6}, # All 7 days
}


@dataclass
class ShiftBucket:
    index: int
    date_str: str          # e.g. "10/12"
    shift_name: str        # "早班", "中班", "晚班"
    start_dt: datetime
    end_dt: datetime
    weekday: int           # 0..6 (Mon..Sun)
    is_work_shift: bool = True


@dataclass
class LineCalendar:
    line: str
    mode: CalendarMode
    working_weekdays: Set[int] = field(default_factory=set)

    def __post_init__(self):
        if not self.working_weekdays and self.mode in WORKING_DAYS_BY_MODE:
            self.working_weekdays = WORKING_DAYS_BY_MODE[self.mode].copy()

    def is_workday(self, dt: datetime) -> bool:
        return dt.weekday() in self.working_weekdays


def decide_calendar_mode(demand_hours: float, full_weekly_capacity_hours: float) -> CalendarMode:
    """R-CL-01..03: Select calendar mode by load ratio against full 7-day capacity."""
    if full_weekly_capacity_hours <= 0:
        return CalendarMode.SEVEN_ZERO
    ratio = demand_hours / full_weekly_capacity_hours
    if ratio <= 4.0 / 7.0:
        return CalendarMode.FOUR_THREE
    elif ratio <= 5.0 / 7.0:
        return CalendarMode.FIVE_TWO
    else:
        return CalendarMode.SEVEN_ZERO


def generate_shift_calendar(
    start_date: datetime,
    num_days: int,
    calendar_mode: CalendarMode = CalendarMode.SEVEN_ZERO,
    custom_working_days: Optional[Set[int]] = None,
    filter_non_working: bool = True,
    day_shift_overrides: Optional[Dict[str, Any]] = None,
) -> List[ShiftBucket]:
    """Build calendar shifts according to the specified calendar pattern or custom overrides.

    If `filter_non_working` is True, shifts on off-days are excluded from the
    solver slots, turning off-day avoidance into a structural guarantee.
    """
    working_days = custom_working_days or WORKING_DAYS_BY_MODE.get(
        calendar_mode, WORKING_DAYS_BY_MODE[CalendarMode.SEVEN_ZERO]
    )

    shifts: List[ShiftBucket] = []
    slot_idx = 0
    for day_idx in range(num_days):
        day = start_date + timedelta(days=day_idx)
        date_str = f"{day.month}/{day.day}"

        # Check custom day overrides if provided
        day_override = (
            (day_shift_overrides.get(date_str) or day_shift_overrides.get(day.strftime("%Y-%m-%d")))
            if day_shift_overrides else None
        )

        if day_override is not None:
            is_day_working = bool(day_override.get("is_workday", True))
        else:
            is_day_working = day.weekday() in working_days

        if not is_day_working and filter_non_working:
            continue

        boundaries = [
            datetime(day.year, day.month, day.day, 7, 0),
            datetime(day.year, day.month, day.day, 15, 0),
            datetime(day.year, day.month, day.day, 23, 0),
        ]
        for i, shift_name in enumerate(SHIFT_NAMES):
            if day_override and "shifts" in day_override:
                is_shift_working = is_day_working and bool(day_override["shifts"].get(shift_name, True))
            else:
                is_shift_working = is_day_working

            if not is_shift_working and filter_non_working:
                continue

            shifts.append(
                ShiftBucket(
                    index=slot_idx,
                    date_str=date_str,
                    shift_name=shift_name,
                    start_dt=boundaries[i],
                    end_dt=boundaries[i] + timedelta(hours=SHIFT_HOURS),
                    weekday=day.weekday(),
                    is_work_shift=is_shift_working,
                )
            )
            slot_idx += 1
    return shifts


def adjust_due_datetime_for_holidays(
    due_dt: datetime,
    working_weekdays: Set[int],
    shifts: Optional[List[ShiftBucket]] = None,
) -> datetime:
    """R-BK-01: Shift holiday due date back to the end of the last working shift (15:00)."""
    if shifts:
        valid_shifts = [s for s in shifts if s.is_work_shift and s.end_dt <= due_dt]
        if valid_shifts:
            return valid_shifts[-1].end_dt
    curr = due_dt
    while curr.weekday() not in working_weekdays:
        curr -= timedelta(days=1)
    # Target morning shift finish time on the working day
    return datetime(curr.year, curr.month, curr.day, 15, 0, 0)


def last_shift_on_or_before(shifts: List[ShiftBucket], moment: datetime) -> int:
    """Index of the latest work shift that fully completes by `moment` (0 if none)."""
    completed = [s.index for s in shifts if s.end_dt <= moment]
    return completed[-1] if completed else 0


def evaluate_calendar_recommendation(
    plan: Any,
    start_date: datetime,
    horizon_days: int,
    current_calendar_mode: str = "SEVEN_ZERO",
) -> Dict[str, Any]:
    """R-CL-01..03, R-EX-04: Evaluate capacity vs demand and recommend optimal calendar mode."""
    # 1. Total theoretical max capacity across horizon (7x3 continuous)
    max_theoretical_hours = float(horizon_days * 24.0)

    # 2. Compute requirements per line
    line_req_hours: Dict[str, float] = {}
    line_stage: Dict[str, str] = {}
    for task in getattr(plan, "tasks", []):
        line = task.line
        line_stage[line] = task.stage
        sec = (task.lots * task.lot_millis) / 1000.0
        line_req_hours[line] = line_req_hours.get(line, 0.0) + (sec / 3600.0)

    if not line_req_hours:
        bottleneck_line = "N/A"
        bottleneck_stage = "FG"
        bottleneck_req_hours = 0.0
        bottleneck_ratio = 0.0
    else:
        bottleneck_line = max(line_req_hours, key=line_req_hours.get)
        bottleneck_stage = line_stage.get(bottleneck_line, "FG")
        bottleneck_req_hours = line_req_hours[bottleneck_line]
        bottleneck_ratio = bottleneck_req_hours / max_theoretical_hours if max_theoretical_hours > 0 else 0.0

    # 3. Simulate the 3 tiers
    mode_specs = [
        ("FOUR_THREE", "做四休三 (4天×3班)", "做四休三 (4×3)", "极度空闲", "周一至周四满产，周五至周日停工", CalendarMode.FOUR_THREE),
        ("FIVE_TWO", "做五休二 (5天×3班)", "做五休二 (5×3)", "正常均衡", "周一至周五标准排产，周末双休保养", CalendarMode.FIVE_TWO),
        ("SEVEN_ZERO", "连续生产 (7天×3班)", "连续生产 (7×3)", "产能紧张/保供", "周一至周日全开工，全量释放理论产能", CalendarMode.SEVEN_ZERO),
    ]

    tier_evaluations = []
    for code, name, short_name, tier_label, work_pattern, cm in mode_specs:
        test_shifts = generate_shift_calendar(start_date, horizon_days, calendar_mode=cm, filter_non_working=True)
        avail_hours = float(len(test_shifts) * SHIFT_HOURS)
        avail_workdays = len(set(s.date_str for s in test_shifts))
        load_rate = (bottleneck_req_hours / avail_hours) if avail_hours > 0 else 999.0

        if load_rate > 1.001:
            status = "OVERLOAD"
            status_text = f"严重超载 (缺口 {bottleneck_req_hours - avail_hours:.1f}h)"
            badge_color = "rose"
            is_feasible = False
        elif load_rate >= 0.95:
            status = "CRITICAL"
            status_text = f"临界饱和 ({load_rate*100:.1f}%)"
            badge_color = "amber"
            is_feasible = True
        elif load_rate >= 0.70:
            status = "OPTIMAL"
            status_text = f"达标健康 ({load_rate*100:.1f}%)"
            badge_color = "emerald"
            is_feasible = True
        else:
            status = "UNDERLOAD"
            status_text = f"负荷较低 ({load_rate*100:.1f}%)"
            badge_color = "blue"
            is_feasible = True

        tier_evaluations.append({
            "mode_code": code,
            "mode_name": name,
            "mode_short_name": short_name,
            "tier_label": tier_label,
            "work_pattern": work_pattern,
            "workdays": avail_workdays,
            "total_shifts": len(test_shifts),
            "available_hours": round(avail_hours, 1),
            "load_rate": round(load_rate * 100, 1),
            "status": status,
            "status_text": status_text,
            "badge_color": badge_color,
            "is_feasible": is_feasible,
        })

    # 4. Make recommendation
    # Standard rules:
    # R-CL-01: ratio <= 4/7 (57.14%) -> FOUR_THREE
    # R-CL-02: 4/7 < ratio <= 5/7 (71.43%) -> FIVE_TWO
    # R-CL-03: ratio > 5/7 -> SEVEN_ZERO
    # Extreme: ratio > 1.0 -> SEVEN_ZERO + Warning
    if bottleneck_ratio <= 4.0 / 7.0:
        recommended_mode = "FOUR_THREE"
        recommended_name = "做四休三 (4天×3班)"
        recommended_short_name = "做四休三 (4×3)"
        rule_id = "R-CL-01"
        confidence = "AI 节能推荐"
        summary_text = (
            f"当前 {horizon_days} 天计划期内，瓶颈产线【{bottleneck_line}】总需求工时为 {bottleneck_req_hours:.1f}h，"
            f"仅占全量理论产能的 {bottleneck_ratio*100:.1f}%（门槛 ≤ 57.1%）。"
            f"根据排产规则 R-CL-01，属于【极度空闲】状态，推荐采用做四休三工作制，集中在周一至周四满产，"
            f"周五至周末停产关机，预计可节约 3 天能耗与用工成本，周末提货需求前置由库存覆盖（R-BK-01）。"
        )
        reasons = [
            f"瓶颈工时需求较低（{bottleneck_req_hours:.1f}h / 极限 {max_theoretical_hours:.1f}h，负荷率 {bottleneck_ratio*100:.1f}%）；",
            "做四休三可用工时充足，生产完成后集中停线，显著降低车间待机电耗与人力待工成本；",
            "联动 R-BK-01 规则：休息日客户提货需求已逆向倒排至周四前完成备料储备。"
        ]
    elif bottleneck_ratio <= 5.0 / 7.0:
        recommended_mode = "FIVE_TWO"
        recommended_name = "做五休二 (5天×3班)"
        recommended_short_name = "做五休二 (5×3)"
        rule_id = "R-CL-02"
        confidence = "AI 标准推荐"
        summary_text = (
            f"当前 {horizon_days} 天计划期内，瓶颈产线【{bottleneck_line}】工时需求为 {bottleneck_req_hours:.1f}h，"
            f"负荷率为 {bottleneck_ratio*100:.1f}%（处于 57.1% ~ 71.4% 的均衡区间）。"
            f"根据排产规则 R-CL-02，属于【正常均衡】状态，推荐执行标准法定做五休二工作制，"
            f"周一至周五均衡生产，周末正常双休与安排产线 TPM 保养检修。"
        )
        reasons = [
            f"产线负荷适中（瓶颈工时 {bottleneck_req_hours:.1f}h / 极限 {max_theoretical_hours:.1f}h，负荷率 {bottleneck_ratio*100:.1f}%）；",
            "5天开工工时可平准化覆盖全部订单交期，工序无需周六日连续开工；",
            "周末两天正常停机，为冲压模具清理与装配机台 TPM 保养预留合规窗口。"
        ]
    elif bottleneck_ratio <= 1.0:
        recommended_mode = "SEVEN_ZERO"
        recommended_name = "连续生产 (7天×3班)"
        recommended_short_name = "连续生产 (7×3)"
        rule_id = "R-CL-03"
        confidence = "AI 强力保供推荐"
        summary_text = (
            f"当前 {horizon_days} 天计划期内，瓶颈产线【{bottleneck_line}】工时需求达 {bottleneck_req_hours:.1f}h，"
            f"理论负荷率高达 {bottleneck_ratio*100:.1f}%（突破 5/7 = 71.4% 的高负荷门槛）。"
            f"根据排产规则 R-CL-03，属于【产能紧张】状态，必须取消常规周末排休，启动 7×3 连续生产，"
            f"释放全量 {max_theoretical_hours:.0f} 小时产能，以确保 100% 达成客户准时交付。"
        )
        reasons = [
            f"订单需求高企，瓶颈产线工时已达 {bottleneck_req_hours:.1f}h，若采用做五休二将造成严重交付缺口；",
            "根据 R-CL-03 升阶机制：打破常规周末休假，全开 21 班次释放极限交付能力；",
            "全量开工后负荷率平准至达标区间，避免因批次换型与排产碎片化造成延期违约。"
        ]
    else:
        recommended_mode = "SEVEN_ZERO"
        recommended_name = "连续生产 (7天×3班) + 周期扩展"
        recommended_short_name = "连续生产 (超载预警)"
        rule_id = "R-EX-04"
        confidence = "AI 极端超载告警"
        summary_text = (
            f"⚠️ 极端超载预警！当前 {horizon_days} 天订单总工时（{bottleneck_req_hours:.1f}h）已超出 7×3 满产极限（{max_theoretical_hours:.1f}h），"
            f"超载率达 {bottleneck_ratio*100:.1f}%。根据规则 R-EX-04，建议立即将计划期扩展至 15 天或启动战略客户梯次保供分流。"
        )
        reasons = [
            f"当前所选 {horizon_days} 天视区内订单过密，瓶颈产线出现绝对工时赤字（缺口 {bottleneck_req_hours - max_theoretical_hours:.1f}h）；",
            "建议优先在上方切换为【15天扩展计划】，以拉长缓冲视区平准生产；",
            "结合 R-CR-01 / R-EX-04 规则：优先保供战略客户，非关键客户订单建议协商延期或安排外协。"
        ]

    # Check match with current mode
    is_current_mode_matched = (current_calendar_mode == recommended_mode)

    return {
        "recommended_mode": recommended_mode,
        "recommended_name": recommended_name,
        "recommended_short_name": recommended_short_name,
        "rule_id": rule_id,
        "confidence": confidence,
        "summary_text": summary_text,
        "reasons": reasons,
        "bottleneck_line": bottleneck_line,
        "bottleneck_stage": bottleneck_stage,
        "bottleneck_req_hours": round(bottleneck_req_hours, 1),
        "bottleneck_avail_hours": round(max_theoretical_hours, 1),
        "bottleneck_ratio": round(bottleneck_ratio, 3),
        "is_current_mode_matched": is_current_mode_matched,
        "tier_evaluations": tier_evaluations,
        "line_loads": [
            {
                "line": l,
                "stage": line_stage.get(l, "FG"),
                "req_hours": round(h, 1),
                "full_capacity_hours": round(max_theoretical_hours, 1),
                "ratio": round(h / max_theoretical_hours * 100, 1) if max_theoretical_hours > 0 else 0,
            }
            for l, h in sorted(line_req_hours.items())
        ],
    }
