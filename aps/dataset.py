from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field as dc_field
from typing import Any

from aps.calendar.geometry import (
    MS_PER_HOUR,
    LineGeometry,
    Slot,
    SlotKey,
    build_slots,
)
from aps.calendar.pattern import (
    DEFAULT_AUTO_THRESHOLDS,
    CalendarMode,
    recommend_mode,
)
from aps.data.issues import IssueLog
from aps.data.repository import Repository

__all__ = [
    "BomLink",
    "DataSet",
    "Horizon",
    "MS_PER_SECOND",
    "RouteOption",
    "SECONDS_PER_HOUR",
    "Task",
]

MS_PER_SECOND = 1000
SECONDS_PER_HOUR = 3600


@dataclass(frozen=True)
class Horizon:
    site: str
    start_date: dt.date
    days: int

    @property
    def end_date(self) -> dt.date:
        return self.start_date + dt.timedelta(days=max(0, self.days - 1))

    def contains(self, day: dt.date) -> bool:
        if self.days <= 0:
            return False
        return self.start_date <= day <= self.end_date

    def day_offset(self, day: dt.date) -> int:
        return (day - self.start_date).days


@dataclass(frozen=True)
class Task:
    pn: str
    stage: str
    net_requirement: int
    due_date: dt.date
    mpq: int
    moq: int | None
    mold_id: str | None
    family_code: str | None
    description: str | None
    is_safety_topup: bool
    parent_pn: str | None = None
    customer_code: str | None = None
    weight: int = 1


@dataclass(frozen=True)
class RouteOption:
    pn: str
    op_seq: int
    line_code: str
    certified: bool
    ct_sec: float
    unit_ms: int
    per_shift_cap: int
    headcount: int | None
    changeover_min: float | None
    changeover_source: str | None
    mold_ids: tuple[str, ...]


@dataclass(frozen=True)
class BomLink:
    parent_pn: str
    child_pn: str
    ratio: float


@dataclass(frozen=True)
class DataSet:
    horizon: Horizon
    mode: CalendarMode
    lines: tuple[LineGeometry, ...]
    slots: tuple[Slot, ...]
    tasks: tuple[Task, ...]
    routes: tuple[RouteOption, ...]
    bom: tuple[BomLink, ...]
    safety_stock: Mapping[str, int]
    mold_to_parts: Mapping[str, frozenset[str]]
    initial_stock: Mapping[str, int]
    calendar_overrides: Mapping[SlotKey, bool]
    mold_available_sets: Mapping[str, int] = dc_field(default_factory=dict)
    degraded_fields: frozenset[str] = frozenset()

    _setup_matrix: Mapping[tuple[str, str, str], float] = dc_field(
        default_factory=dict, repr=False, compare=False, hash=False
    )
    _mold_changeover: Mapping[str, float] = dc_field(
        default_factory=dict, repr=False, compare=False, hash=False
    )

    _lines_by_code: dict[str, LineGeometry] = dc_field(
        default_factory=dict, init=False, repr=False, compare=False, hash=False
    )
    _slots_by_line: dict[str, list[Slot]] = dc_field(
        default_factory=dict, init=False, repr=False, compare=False, hash=False
    )
    _certified_by_pn: dict[str, list[str]] = dc_field(
        default_factory=dict, init=False, repr=False, compare=False, hash=False
    )
    _routes_by_key: dict[tuple[str, str, int], RouteOption] = dc_field(
        default_factory=dict, init=False, repr=False, compare=False, hash=False
    )
    _tasks_by_pn: dict[str, list[Task]] = dc_field(
        default_factory=dict, init=False, repr=False, compare=False, hash=False
    )
    _children_by_parent: dict[str, list[BomLink]] = dc_field(
        default_factory=dict, init=False, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        lines_by_code = {l.line_code: l for l in self.lines}
        object.__setattr__(self, "_lines_by_code", lines_by_code)

        slots_by_line: dict[str, list[Slot]] = defaultdict(list)
        for s in self.slots:
            slots_by_line[s.key.line_code].append(s)
        object.__setattr__(self, "_slots_by_line", dict(slots_by_line))

        certified_by_pn: dict[str, list[str]] = defaultdict(list)
        for r in self.routes:
            if r.certified and r.line_code not in certified_by_pn[r.pn]:
                certified_by_pn[r.pn].append(r.line_code)
        object.__setattr__(self, "_certified_by_pn", dict(certified_by_pn))

        routes_by_key = {(r.pn, r.line_code, r.op_seq): r for r in self.routes}
        object.__setattr__(self, "_routes_by_key", routes_by_key)

        tasks_by_pn: dict[str, list[Task]] = defaultdict(list)
        for t in self.tasks:
            tasks_by_pn[t.pn].append(t)
        object.__setattr__(self, "_tasks_by_pn", dict(tasks_by_pn))

        children_by_parent: dict[str, list[BomLink]] = defaultdict(list)
        for b in self.bom:
            children_by_parent[b.parent_pn].append(b)
        object.__setattr__(self, "_children_by_parent", dict(children_by_parent))

    def line(self, line_code: str) -> LineGeometry | None:
        return self._lines_by_code.get(line_code)

    def open_slots(self, line_code: str) -> tuple[Slot, ...]:
        return tuple(s for s in self._slots_by_line.get(line_code, ()) if s.is_open)

    def slot_count(self, line_code: str) -> int:
        return len(self._slots_by_line.get(line_code, ()))

    def certified_lines(self, pn: str) -> tuple[str, ...]:
        return tuple(self._certified_by_pn.get(pn, ()))

    def route(self, pn: str, line_code: str, op_seq: int | None = None) -> RouteOption | None:
        if op_seq is not None:
            return self._routes_by_key.get((pn, line_code, op_seq))
        candidates = [r for (p, l, s), r in self._routes_by_key.items() if p == pn and l == line_code]
        if not candidates:
            return None
        return min(candidates, key=lambda r: r.op_seq)

    def tasks_for(self, pn: str) -> tuple[Task, ...]:
        return tuple(self._tasks_by_pn.get(pn, ()))

    def children_of(self, parent_pn: str) -> tuple[BomLink, ...]:
        return tuple(self._children_by_parent.get(parent_pn, ()))

    def due_index(self, due_date: dt.date, line_code: str) -> int:
        line = self._lines_by_code.get(line_code)
        if line is None:
            raise KeyError(f"未知产线 {line_code!r}")
        offset = max(0, min(self.horizon.day_offset(due_date), self.horizon.days - 1))
        return offset * line.shifts_per_day + line.shifts_per_day - 1

    def changeover(self, line_code: str, from_mold: str | None,
                   to_mold: str | None, pn: str) -> tuple[float | None, str | None]:
        if from_mold and to_mold and (line_code, from_mold, to_mold) in self._setup_matrix:
            return self._setup_matrix[(line_code, from_mold, to_mold)], "setup_matrix"
        if from_mold is None and to_mold and to_mold in self._mold_changeover:
            return self._mold_changeover[to_mold], "tooling_mold"
        route = self._routes_by_key.get((pn, line_code, 10))
        if route is not None and route.changeover_min is not None:
            return route.changeover_min, "routing"
        if to_mold and to_mold in self._mold_changeover:
            return self._mold_changeover[to_mold], "tooling_mold"
        return None, None

    @classmethod
    def load(cls, repo: Repository, site: str, start_date: dt.date, days: int,
             calendar_mode: str = "auto",
             thresholds: Mapping[str, float] = DEFAULT_AUTO_THRESHOLDS,
             overrides: Mapping[SlotKey, bool] | None = None,
             log: IssueLog | None = None,
             degraded_fields: frozenset[str] = frozenset()) -> "DataSet":
        horizon = Horizon(site, start_date, days)

        lines = _load_lines(repo, site)
        lines_by_code = {l.line_code: l for l in lines}

        parts = {r["pn"]: r for r in repo.list_by("part", site_code=site)}
        fg_params = {r["pn"]: r for r in repo.list_by("part_param_fg", site_code=site)}
        sf_params = {r["pn"]: r for r in repo.list_by("part_param_sf", site_code=site)}
        setup_matrix = {
            (r["line_code"], r["from_mold"], r["to_mold"]): r
            for r in repo.list_by("setup_matrix", site_code=site)
        }
        molds = {r["mold_id"]: r for r in repo.list_by("mold", site_code=site)}
        bom_links = tuple(
            BomLink(parent_pn=r["parent_pn"], child_pn=r["child_pn"], ratio=float(r["ratio"]))
            for r in repo.list_by("bom", site_code=site)
        )

        initial_stock = _load_initial_stock(repo, site, parts, log)
        safety_stock = _load_safety_stock(repo, site, start_date, parts, log)
        tasks = _build_tasks(repo, site, horizon, parts, fg_params, sf_params,
                             initial_stock, safety_stock, bom_links)
        routes = _build_routes(repo, site, start_date, lines_by_code,
                               setup_matrix, molds, fg_params, sf_params, log)
        mold_lookup = _build_mold_lookup(repo, site, fg_params, sf_params)

        cal_overrides = _load_calendar_overrides(repo, site, overrides)

        if calendar_mode == "auto":
            load_ratio = _calculate_load_ratio(repo, site, horizon, routes, lines, days)
            resolved_mode = recommend_mode(load_ratio, thresholds)
        elif isinstance(calendar_mode, CalendarMode):
            resolved_mode = calendar_mode
        else:
            mode_str = str(calendar_mode).upper().strip()
            aliases = {
                "SIX_ON_ONE_OFF": CalendarMode.SIX_ON_ONE_OFF,
                "FIVE_ON_TWO_OFF": CalendarMode.FIVE_ON_TWO_OFF,
                "FOUR_ON_THREE_OFF": CalendarMode.FOUR_ON_THREE_OFF,
            }
            if mode_str in aliases:
                resolved_mode = aliases[mode_str]
            else:
                try:
                    resolved_mode = CalendarMode(mode_str)
                except ValueError:
                    resolved_mode = CalendarMode[mode_str]

        slots = build_slots(lines, start_date, days, resolved_mode, cal_overrides, log)

        sm_lookup = {
            (r["line_code"], r["from_mold"], r["to_mold"]): float(r["changeover_min"])
            for r in repo.list_by("setup_matrix", site_code=site)
            if r.get("changeover_min") is not None
        }
        mold_co_lookup = {
            r["mold_id"]: float(r["changeover_min"])
            for r in repo.list_by("mold", site_code=site)
            if r.get("changeover_min") is not None
        }

        total_mold_sets: dict[str, int] = defaultdict(int)
        try:
            for r in repo.list_by("tooling_mold", site_code=site):
                m_id = r.get("mold_id")
                if m_id:
                    total_mold_sets[m_id] += int(r.get("available_sets") or 1)
        except Exception:
            pass

        if not total_mold_sets:
            try:
                for r in repo.list_by("mold", site_code=site):
                    m_id = r.get("mold_id")
                    if m_id and r.get("available_sets") is not None:
                        total_mold_sets[m_id] = int(r["available_sets"])
            except Exception:
                pass
        mold_sets = dict(total_mold_sets)

        return cls(
            horizon=horizon,
            mode=resolved_mode,
            lines=lines,
            slots=slots,
            tasks=tasks,
            routes=routes,
            bom=bom_links,
            safety_stock=safety_stock,
            mold_to_parts=mold_lookup,
            initial_stock=initial_stock,
            calendar_overrides=cal_overrides,
            mold_available_sets=mold_sets,
            degraded_fields=degraded_fields,
            _setup_matrix=sm_lookup,
            _mold_changeover=mold_co_lookup,
        )


def _load_lines(repo: Repository, site: str) -> tuple[LineGeometry, ...]:
    lines: list[LineGeometry] = []
    for row in repo.list_by("line", site_code=site):
        lines.append(LineGeometry(
            line_code=row["line_code"],
            shift_hours=float(row["shift_hours"]),
            shifts_per_day=int(row["shifts_per_day"]),
            trp_efficiency=float(row["trp_efficiency"]),
        ))
    lines.sort(key=lambda l: l.line_code)
    return tuple(lines)


def _load_initial_stock(repo: Repository, site: str,
                        parts: Mapping[str, Mapping[str, Any]],
                        log: IssueLog | None) -> dict[str, int]:
    stock_by_pn: dict[str, int] = defaultdict(int)
    for row in repo.list_by("initial_stock", site_code=site):
        pn = row["pn"]
        material_type = row.get("material_type")
        part = parts.get(pn)
        part_stage = part.get("stage") if part else None
        if part_stage is not None and material_type == part_stage:
            stock_by_pn[pn] += int(round(float(row.get("quantity") or 0)))
    if log is not None:
        all_stock_pns = {r["pn"] for r in repo.list_by("initial_stock", site_code=site)}
        for pn in sorted(all_stock_pns - set(stock_by_pn)):
            log.gap(entity="initial_stock", field="material_type",
                    message=f"料号 {pn} 的期初库存没有匹配到 stage 的记录，取 0")
    return dict(stock_by_pn)


def _load_safety_stock(repo: Repository, site: str, start_date: dt.date,
                       parts: Mapping[str, Mapping[str, Any]],
                       log: IssueLog | None) -> dict[str, int]:
    safety_by_pn: dict[str, int] = {}
    day_num = start_date.day
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in repo.list_by("inventory_policy", site_code=site):
        if row.get("period_type") == "NORMAL":
            grouped[row["pn"]].append(row)

    for pn in sorted(parts):
        rows = grouped.get(pn, [])
        matching = [r for r in rows if r["start_day"] <= day_num <= r["end_day"]]
        if not matching:
            safety_by_pn[pn] = 0
            if log is not None and rows:
                log.gap(entity="inventory_policy", field="safety_stock",
                        message=f"料号 {pn} 当前日期 {start_date} 落在策略区间外，安全库存取 0")
            continue
        best = max(matching, key=lambda r: (r["start_day"], r.get("end_day") or 0))
        safety_by_pn[pn] = int(best.get("safety_stock") or 0)
    return safety_by_pn


def _build_tasks(repo: Repository, site: str, horizon: Horizon,
                 parts: Mapping[str, Mapping[str, Any]],
                 fg_params: Mapping[str, Mapping[str, Any]],
                 sf_params: Mapping[str, Mapping[str, Any]],
                 initial_stock: Mapping[str, int],
                 safety: Mapping[str, int],
                 bom_links: Sequence[BomLink] | None = None) -> tuple[Task, ...]:
    demand_by_pn: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in repo.list_by("demand", site_code=site):
        if horizon.contains(row["demand_date"]):
            demand_by_pn[row["pn"]].append(row)

    # 战略客户保供权重查找表
    customer_weights: dict[str, int] = {}
    try:
        cust_rows = repo.list_by("customer_priority", site_code=site)
    except Exception:
        cust_rows = []
    for r in cust_rows:
        w = r.get("supply_weight")
        if w is None:
            tier_str = str(r.get("tier") or "")
            w = 10 if "TIER_A" in tier_str else (3 if "TIER_B" in tier_str else 1)
        customer_weights[r["customer_code"]] = max(1, int(round(w)))

    tasks: list[Task] = []
    fg_tasks_by_pn: dict[str, list[Task]] = defaultdict(list)
    for pn in sorted(demand_by_pn):
        carry = int(initial_stock.get(pn, 0))
        for row in sorted(demand_by_pn[pn], key=lambda r: r["demand_date"]):
            quantity = int(row["quantity"])
            covered = min(carry, quantity)
            carry -= covered
            remaining = quantity - covered
            if remaining <= 0:
                continue
            c_code = row.get("customer_code")
            c_weight = customer_weights.get(c_code, 1) if c_code else 1
            t = _make_task(pn, parts, fg_params, sf_params, remaining,
                           row["demand_date"], False, parent_pn=None,
                           customer_code=c_code, weight=c_weight)
            tasks.append(t)
            fg_tasks_by_pn[pn].append(t)

    bom_by_parent: dict[str, list[BomLink]] = defaultdict(list)
    for link in (bom_links or ()):
        bom_by_parent[link.parent_pn].append(link)

    sf_gross_demand: dict[str, list[tuple[dt.date, int, str]]] = defaultdict(list)
    for parent_pn, parent_tasks in fg_tasks_by_pn.items():
        for link in bom_by_parent.get(parent_pn, []):
            child_pn = link.child_pn
            child_part = parts.get(child_pn, {})
            if child_part.get("stage") != "SF":
                continue
            sf_cfg = sf_params.get(child_pn) or {}
            scrap_rate = float(sf_cfg.get("scrap_rate") or 0.0)
            scrap_divisor = max(0.01, 1.0 - scrap_rate) if 0.0 <= scrap_rate < 1.0 else 1.0
            for pt in parent_tasks:
                nominal_qty = pt.net_requirement * link.ratio
                gross_qty = int(math.ceil(nominal_qty / scrap_divisor))
                sf_gross_demand[child_pn].append((pt.due_date, gross_qty, parent_pn))

    for child_pn in sorted(sf_gross_demand):
        sf_carry = int(initial_stock.get(child_pn, 0))
        for due_date, gross_qty, parent_pn in sorted(sf_gross_demand[child_pn], key=lambda x: x[0]):
            covered = min(sf_carry, gross_qty)
            sf_carry -= covered
            remaining = gross_qty - covered
            if remaining <= 0:
                continue
            tasks.append(_make_task(child_pn, parts, fg_params, sf_params, remaining,
                                    due_date, False, parent_pn=parent_pn))

    for pn in sorted(safety):
        amount = int(safety[pn])
        if amount <= 0 or pn not in parts:
            continue
        tasks.append(_make_task(pn, parts, fg_params, sf_params, amount,
                                horizon.end_date, True, parent_pn=None))

    tasks.sort(key=lambda t: (t.due_date, t.pn, t.is_safety_topup))
    return tuple(tasks)


def _make_task(pn: str, parts: Mapping[str, Mapping[str, Any]],
               fg_params: Mapping[str, Mapping[str, Any]],
               sf_params: Mapping[str, Mapping[str, Any]],
               amount: int, due_date: dt.date, topup: bool,
               parent_pn: str | None = None,
               customer_code: str | None = None,
               weight: int = 1) -> Task:
    part = parts.get(pn, {})
    stage = part.get("stage") or "COMP"
    fg = fg_params.get(pn)
    sf = sf_params.get(pn)
    if stage == "FG" and fg is not None:
        mpq = int(fg.get("mpq") or 1)
        moq = fg.get("moq")
        mold_id = fg.get("mold_id")
        family = fg.get("family_code")
    elif stage == "SF" and sf is not None:
        mpq = int(sf.get("mpq") or 1)
        moq = sf.get("moq")
        mold_id = sf.get("mold_id")
        family = sf.get("family_code")
    else:
        mpq, moq, mold_id, family = 1, None, None, None
    return Task(
        pn=pn, stage=stage, net_requirement=amount, due_date=due_date,
        mpq=mpq, moq=moq, mold_id=mold_id,
        family_code=family or part.get("family_code"),
        description=part.get("description"), is_safety_topup=topup,
        parent_pn=parent_pn,
        customer_code=customer_code,
        weight=weight,
    )


def _build_mold_lookup(repo: Repository, site: str,
                       fg_params: Mapping[str, Mapping[str, Any]],
                       sf_params: Mapping[str, Mapping[str, Any]],
                       ) -> dict[str, frozenset[str]]:
    lookup: dict[str, set[str]] = defaultdict(set)
    for row in repo.list_by("tooling_mold", site_code=site):
        mold_id = row["mold_id"]
        for pn in row.get("applicable_pns") or ():
            lookup[mold_id].add(str(pn).strip())
    for pn, p in fg_params.items():
        m_id = p.get("mold_id")
        if m_id:
            lookup[m_id].add(pn)
    for pn, p in sf_params.items():
        m_id = p.get("mold_id")
        if m_id:
            lookup[m_id].add(pn)
    return {k: frozenset(v) for k, v in lookup.items()}


def _build_routes(repo: Repository, site: str, start_date: dt.date,
                  lines_by_code: Mapping[str, LineGeometry],
                  setup_matrix: Mapping[tuple[str, str, str], Mapping[str, Any]],
                  molds: Mapping[str, Mapping[str, Any]],
                  fg_params: Mapping[str, Mapping[str, Any]],
                  sf_params: Mapping[str, Mapping[str, Any]],
                  log: IssueLog | None) -> tuple[RouteOption, ...]:
    all_routes = repo.list_by("routing", site_code=site)
    valid_routes: list[Mapping[str, Any]] = []
    for r in all_routes:
        v_from = r.get("valid_from")
        v_to = r.get("valid_to")
        if v_from is not None and v_from > start_date:
            continue
        if v_to is not None and v_to <= start_date:
            continue
        valid_routes.append(r)

    op_min: dict[tuple[str, str], int] = {}
    priority_map: dict[tuple[str, str], int] = {}
    for r in valid_routes:
        key = (r["pn"], r["line_code"])
        op = int(r.get("op_seq") or 10)
        prio = int(r.get("priority") or 999)
        if key not in op_min or op < op_min[key]:
            op_min[key] = op
            priority_map[key] = prio

    options: list[RouteOption] = []
    for r in valid_routes:
        line = lines_by_code.get(r["line_code"])
        if line is None:
            continue
        ct_sec = float(r["ct_sec"])
        trp = line.trp_efficiency
        unit_ms = int(round(ct_sec * MS_PER_SECOND / trp)) if trp > 0 else 0
        per_shift = int(math.floor(line.shift_hours * SECONDS_PER_HOUR * trp / ct_sec)) if ct_sec > 0 else 0

        mold_ids = tuple(r.get("mold_ids") or ())
        raw_co = r.get("changeover_min")
        co_min = float(raw_co) if raw_co is not None else None
        co_source = "routing" if co_min is not None else None

        if co_min is None and log is not None:
            log.gap(entity="routing", field="changeover_min",
                    message=f"料号 {r['pn']} 产线 {line.line_code} 换型时间无定义")

        options.append(RouteOption(
            pn=r["pn"],
            op_seq=int(r.get("op_seq") or 10),
            line_code=line.line_code,
            certified=bool(r.get("certified")),
            ct_sec=ct_sec,
            unit_ms=unit_ms,
            per_shift_cap=per_shift,
            headcount=r.get("headcount"),
            changeover_min=co_min,
            changeover_source=co_source,
            mold_ids=mold_ids,
        ))

    def sort_key(opt: RouteOption) -> tuple:
        prio = priority_map.get((opt.pn, opt.line_code), 999)
        return (opt.pn, opt.op_seq, -int(opt.certified), prio, opt.line_code)

    options.sort(key=sort_key)
    return tuple(options)


def _load_calendar_overrides(repo: Repository, site: str,
                             overrides: Mapping[SlotKey, bool] | None) -> dict[SlotKey, bool]:
    out: dict[SlotKey, bool] = dict(overrides or {})
    for row in repo.list_by("calendar_day", site_code=site):
        key = SlotKey(row["line_code"], row["work_date"], int(row["shift_index"]))
        if key not in out:
            out[key] = bool(row["is_open"])
    return out


def _calculate_load_ratio(repo: Repository, site: str, horizon: Horizon,
                          routes: Sequence[RouteOption],
                          lines: Sequence[LineGeometry],
                          days: int) -> float:
    min_unit_by_pn: dict[str, int] = {}
    for r in routes:
        if not r.certified:
            continue
        if r.pn not in min_unit_by_pn or r.unit_ms < min_unit_by_pn[r.pn]:
            min_unit_by_pn[r.pn] = r.unit_ms

    total_demand_ms = 0
    for row in repo.list_by("demand", site_code=site):
        if horizon.contains(row["demand_date"]):
            unit_ms = min_unit_by_pn.get(row["pn"])
            if unit_ms:
                total_demand_ms += int(row["quantity"]) * unit_ms

    total_cap_ms = sum(
        int(round(l.shift_hours * MS_PER_HOUR)) * l.shifts_per_day * days
        for l in lines
    )
    return float(total_demand_ms / total_cap_ms) if total_cap_ms > 0 else 0.0
