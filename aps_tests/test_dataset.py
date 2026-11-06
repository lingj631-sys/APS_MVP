from __future__ import annotations

import datetime as dt
import unittest

from aps.calendar.geometry import MS_PER_HOUR, SlotKey
from aps.calendar.pattern import CalendarMode
from aps.data.issues import IssueLog
from aps.dataset import BomLink, DataSet, Horizon, RouteOption, Task

FAST = True

SITE = "WL"
START = dt.date(2026, 11, 2)      # 周一
LINE = {"site_code": SITE, "line_code": "PGLINKAGE", "name": "PGLINKAGE",
        "trp_efficiency": 0.85, "shift_hours": 8.0, "shifts_per_day": 3}


class FakeRepo:
    """最小 Repository 假件：只实现 DataSet.load 用到的读取方法。"""

    def __init__(self, tables):
        self.tables = {name: [dict(row) for row in rows] for name, rows in tables.items()}

    def list_by(self, entity, /, **filters):
        rows = self.tables.get(entity, [])
        out = []
        for row in rows:
            if all(_eq(row.get(k), v) for k, v in filters.items()):
                out.append(dict(row))
        spec_pk = _PK[entity]
        return sorted(out, key=lambda r: tuple(_sk(r.get(k)) for k in spec_pk))

    def get_by_pk(self, entity, pk):
        spec_pk = _PK[entity]
        for row in self.list_by(entity):
            if tuple(row.get(k) for k in spec_pk) == tuple(pk):
                return row
        return None

    def insert_many(self, entity, rows):
        self.tables.setdefault(entity, []).extend(dict(r) for r in rows)
        return len(rows)

    def delete_where(self, entity, /, **filters):
        before = len(self.tables.get(entity, []))
        self.tables[entity] = [
            r for r in self.tables.get(entity, [])
            if not all(_eq(r.get(k), v) for k, v in filters.items())
        ]
        return before - len(self.tables[entity])

    def count(self, entity, /, **filters):
        return len(self.list_by(entity, **filters))


_PK = {
    "site": ("site_code",),
    "line": ("site_code", "line_code"),
    "part": ("site_code", "pn"),
    "product_family": ("site_code", "family_code"),
    "mold": ("site_code", "mold_id"),
    "customer": ("site_code", "customer_code"),
    "calendar_pattern": ("pattern_code",),
    "demand": ("site_code", "pn", "demand_date"),
    "initial_stock": ("site_code", "pn", "material_type"),
    "material_arrival": ("site_code", "pn", "eta_date", "eta_shift", "seq"),
    "part_param_fg": ("site_code", "pn"),
    "part_param_sf": ("site_code", "pn"),
    "bom": ("site_code", "parent_pn", "child_pn"),
    "routing": ("site_code", "pn", "op_seq", "line_code"),
    "setup_matrix": ("site_code", "line_code", "from_mold", "to_mold"),
    "inventory_policy": ("site_code", "pn", "period_type", "start_day"),
    "operation_leadtime": ("site_code", "pn", "op_seq"),
    "tooling_mold": ("site_code", "mold_id", "line_code"),
    "preventive_maintenance": ("site_code", "line_code", "start_date", "item"),
    "shift_staffing": ("site_code", "line_code", "work_date", "shift_index"),
    "product_pairing": ("site_code", "left_pn", "right_pn"),
    "calendar_day": ("site_code", "line_code", "work_date", "shift_index"),
    "setup_technician": ("site_code", "technician", "work_date", "shift_index"),
}


def _eq(left, right):
    return left == right


def _sk(value):
    if value is None:
        return ""
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (int, float)):
        return f"{value:030.10f}"
    if isinstance(value, (tuple, list)):
        return ",".join(str(v) for v in value)
    return str(value)


def base_tables(**overrides):
    tables = {
        "site": [{"site_code": SITE, "name": "温岭", "default_calendar_mode": None}],
        "line": [dict(LINE)],
        "part": [{"site_code": SITE, "pn": "P1", "description": "总成", "stage": "FG",
                  "family_code": "FAM1", "uom": "件"}],
        "part_param_fg": [{"site_code": SITE, "pn": "P1", "description": "总成",
                           "mpq": 48, "moq": None, "family_code": "FAM1",
                           "mold_id": None, "special_shift": None, "co_production": None}],
        "routing": [{"site_code": SITE, "pn": "P1", "op_seq": 10, "line_code": "PGLINKAGE",
                     "stage": "FG", "op_name": "总装", "certified": True, "ct_sec": 27.0,
                     "changeover_min": None, "priority": 1, "valid_from": None,
                     "valid_to": None, "per_shift_cap_declared": None, "headcount": None,
                     "family_code": None, "mold_ids": ()}],
        "demand": [],
        "initial_stock": [],
        "bom": [],
        "inventory_policy": [],
        "setup_matrix": [],
        "mold": [],
        "tooling_mold": [],
        "calendar_day": [],
    }
    tables.update(overrides)
    return tables


def load(tables, days=10, mode="CONTINUOUS", log=None, **kw):
    return DataSet.load(FakeRepo(tables), SITE, START, days, calendar_mode=mode,
                        log=log, **kw)


class TestHorizon(unittest.TestCase):
    def test_end_date_is_inclusive(self):
        h = Horizon(SITE, START, 10)
        self.assertEqual(dt.date(2026, 11, 11), h.end_date)

    def test_contains(self):
        h = Horizon(SITE, START, 10)
        self.assertTrue(h.contains(START))
        self.assertTrue(h.contains(h.end_date))
        self.assertFalse(h.contains(dt.date(2026, 11, 12)))
        self.assertFalse(h.contains(dt.date(2026, 11, 1)))

    def test_day_offset(self):
        h = Horizon(SITE, START, 10)
        self.assertEqual(0, h.day_offset(START))
        self.assertEqual(9, h.day_offset(h.end_date))
        self.assertEqual(-1, h.day_offset(dt.date(2026, 11, 1)))
        self.assertEqual(10, h.day_offset(dt.date(2026, 11, 12)))

    def test_zero_days(self):
        h = Horizon(SITE, START, 0)
        self.assertFalse(h.contains(START))


class TestSlotsAndGeometry(unittest.TestCase):
    def test_slots_built_for_every_calendar_day(self):
        ds = load(base_tables(), days=7, mode="5ON2OFF")
        self.assertEqual(21, len(ds.slots))
        self.assertEqual(21, ds.slot_count("PGLINKAGE"))
        self.assertEqual(15, len(ds.open_slots("PGLINKAGE")))

    def test_explicit_mode_is_honoured(self):
        ds = load(base_tables(), days=7, mode="CONTINUOUS")
        self.assertEqual(CalendarMode.CONTINUOUS, ds.mode)
        self.assertEqual(21, len(ds.open_slots("PGLINKAGE")))

    def test_auto_mode_picks_from_load_ratio(self):
        demand = [{"site_code": SITE, "pn": "P1", "demand_date": START,
                   "description": None, "quantity": 10, "customer_code": None}]
        ds = load(base_tables(demand=demand), days=10, mode="auto")
        self.assertEqual(CalendarMode.FOUR_ON_THREE_OFF, ds.mode)

    def test_auto_mode_saturates_to_continuous(self):
        demand = [{"site_code": SITE, "pn": "P1", "demand_date": START,
                   "description": None, "quantity": 10 ** 9, "customer_code": None}]
        ds = load(base_tables(demand=demand), days=10, mode="auto")
        self.assertEqual(CalendarMode.CONTINUOUS, ds.mode)

    def test_calendar_day_overrides_win(self):
        log = IssueLog()
        overrides = {"calendar_day": [
            {"site_code": SITE, "line_code": "PGLINKAGE", "work_date": START,
             "shift_index": 0, "is_open": False, "note": "临时停线"},
        ]}
        ds = load(base_tables(**overrides), days=1, mode="CONTINUOUS", log=log)
        closed = [s for s in ds.slots if s.key.shift_index == 0]
        self.assertEqual(1, len(closed))
        self.assertFalse(closed[0].is_open)
        self.assertTrue(any("工作日历" in i.message for i in log.warnings))

    def test_slot_capacity_ms(self):
        ds = load(base_tables(), days=1)
        self.assertTrue(all(s.capacity_ms == 8 * MS_PER_HOUR for s in ds.slots))

    def test_line_lookup(self):
        ds = load(base_tables(), days=1)
        self.assertEqual(0.85, ds.line("PGLINKAGE").trp_efficiency)
        self.assertIsNone(ds.line("GHOST"))


class TestUnitScaling(unittest.TestCase):
    def test_unit_ms_is_ct_over_trp_in_milliseconds(self):
        ds = load(base_tables(), days=1)
        route = ds.route("P1", "PGLINKAGE")
        self.assertEqual(31765, route.unit_ms)

    def test_per_shift_cap_is_derived_when_column_empty(self):
        ds = load(base_tables(), days=1)
        route = ds.route("P1", "PGLINKAGE")
        self.assertEqual(906, route.per_shift_cap)

    def test_declared_capacity_is_ignored_because_column_is_t2_gap(self):
        tables = base_tables()
        tables["routing"][0]["per_shift_cap_declared"] = 5
        ds = load(tables, days=1)
        self.assertEqual(906, ds.route("P1", "PGLINKAGE").per_shift_cap)

    def test_unit_ms_is_integer(self):
        ds = load(base_tables(), days=1)
        self.assertIsInstance(ds.route("P1", "PGLINKAGE").unit_ms, int)


class TestCertifiedLines(unittest.TestCase):
    def test_certified_only(self):
        tables = base_tables()
        tables["line"].append(dict(LINE, line_code="轴套6", name="轴套6"))
        tables["routing"].append(dict(tables["routing"][0], line_code="轴套6",
                                      certified=False, priority=2))
        ds = load(tables, days=1)
        self.assertEqual(("PGLINKAGE",), ds.certified_lines("P1"))

    def test_ordered_by_priority_then_code(self):
        tables = base_tables()
        tables["line"].append(dict(LINE, line_code="轴套6", name="轴套6"))
        tables["line"].append(dict(LINE, line_code="ALINE", name="ALINE"))
        tables["routing"][0]["priority"] = 9
        tables["routing"].append(dict(tables["routing"][0], line_code="轴套6", priority=1))
        tables["routing"].append(dict(tables["routing"][0], line_code="ALINE", priority=5))
        ds = load(tables, days=1)
        self.assertEqual(("轴套6", "ALINE", "PGLINKAGE"), ds.certified_lines("P1"))

    def test_priority_tie_breaks_on_line_code(self):
        tables = base_tables()
        tables["line"].append(dict(LINE, line_code="轴套6", name="轴套6"))
        tables["line"].append(dict(LINE, line_code="ALINE", name="ALINE"))
        tables["routing"][0]["priority"] = 1
        tables["routing"].append(dict(tables["routing"][0], line_code="轴套6", priority=1))
        tables["routing"].append(dict(tables["routing"][0], line_code="ALINE", priority=1))
        ds = load(tables, days=1)
        self.assertEqual(("ALINE", "PGLINKAGE", "轴套6"), ds.certified_lines("P1"))

    def test_multi_op_priority_uses_smallest_op_seq(self):
        tables = base_tables()
        tables["line"].append(dict(LINE, line_code="ALINE", name="ALINE"))
        tables["routing"][0]["priority"] = 9
        tables["routing"].append(dict(tables["routing"][0], line_code="ALINE",
                                      op_seq=20, priority=1))
        tables["routing"].append(dict(tables["routing"][0], line_code="ALINE",
                                      op_seq=10, priority=7))
        ds = load(tables, days=1)
        self.assertEqual(("ALINE", "PGLINKAGE"), ds.certified_lines("P1"))

    def test_validity_window_filters(self):
        tables = base_tables()
        tables["routing"][0]["valid_from"] = dt.date(2026, 12, 1)
        ds = load(tables, days=1)
        self.assertEqual((), ds.certified_lines("P1"))

    def test_expired_route_filtered(self):
        tables = base_tables()
        tables["routing"][0]["valid_to"] = dt.date(2026, 11, 1)
        ds = load(tables, days=1)
        self.assertEqual((), ds.certified_lines("P1"))

    def test_unknown_pn(self):
        ds = load(base_tables(), days=1)
        self.assertEqual((), ds.certified_lines("GHOST"))


class TestNetting(unittest.TestCase):
    def _demand(self, day, qty, pn="P1"):
        return {"site_code": SITE, "pn": pn, "demand_date": day, "description": None,
                "quantity": qty, "customer_code": None}

    def _stock(self, qty, pn="P1", material_type="FG"):
        return {"site_code": SITE, "pn": pn, "material_type": material_type,
                "description": None, "quantity": qty}

    def test_no_demand_no_task(self):
        ds = load(base_tables(), days=10)
        self.assertEqual((), ds.tasks)

    def test_demand_without_stock_becomes_task(self):
        tables = base_tables(demand=[self._demand(START, 100)])
        ds = load(tables, days=10)
        self.assertEqual(1, len(ds.tasks))
        task = ds.tasks[0]
        self.assertEqual(100, task.net_requirement)
        self.assertEqual(START, task.due_date)
        self.assertFalse(task.is_safety_topup)

    def test_stock_covers_earliest_demand_first(self):
        tables = base_tables(
            demand=[self._demand(START, 60), self._demand(dt.date(2026, 11, 4), 60)],
            initial_stock=[self._stock(100)],
        )
        ds = load(tables, days=10)
        tasks = ds.tasks
        self.assertEqual(1, len(tasks))
        self.assertEqual(20, tasks[0].net_requirement)
        self.assertEqual(dt.date(2026, 11, 4), tasks[0].due_date)

    def test_fully_covered_demand_produces_no_task(self):
        tables = base_tables(demand=[self._demand(START, 60)],
                             initial_stock=[self._stock(100)])
        ds = load(tables, days=10)
        self.assertEqual((), ds.tasks)

    def test_stock_of_wrong_stage_is_not_borrowed(self):
        tables = base_tables(demand=[self._demand(START, 60)],
                             initial_stock=[self._stock(100, material_type="PACK")])
        log = IssueLog()
        ds = load(tables, days=10, log=log)
        self.assertEqual(60, ds.tasks[0].net_requirement)
        self.assertTrue(any(i.entity == "initial_stock" for i in log.gaps))

    def test_safety_topup_task(self):
        tables = base_tables(
            demand=[self._demand(START, 100)],
            inventory_policy=[{"site_code": SITE, "pn": "P1", "period_type": "NORMAL",
                               "start_day": 1, "end_day": 30, "target_stock_days": None,
                               "max_stock_days": None, "safety_stock": 48,
                               "max_stock": None, "valid_from": None}],
        )
        ds = load(tables, days=10)
        topups = [t for t in ds.tasks if t.is_safety_topup]
        self.assertEqual(1, len(topups))
        self.assertEqual(48, topups[0].net_requirement)
        self.assertEqual(dt.date(2026, 11, 11), topups[0].due_date)
        self.assertEqual(2, len(ds.tasks))

    def test_out_of_horizon_demand_ignored(self):
        tables = base_tables(demand=[self._demand(dt.date(2026, 12, 6), 100)])
        ds = load(tables, days=10)
        self.assertEqual((), ds.tasks)

    def test_initial_stock_index_is_stage_matched(self):
        tables = base_tables(initial_stock=[self._stock(10), self._stock(5, "P1", "PACK")])
        ds = load(tables, days=10)
        self.assertEqual(10, ds.initial_stock["P1"])

    def test_mpq_comes_from_fg_params(self):
        tables = base_tables(demand=[self._demand(START, 100)])
        ds = load(tables, days=10)
        self.assertEqual(48, ds.tasks[0].mpq)

    def test_mpq_defaults_to_one_without_params(self):
        tables = base_tables(
            part=[{"site_code": SITE, "pn": "P9", "description": None, "stage": "FG",
                   "family_code": None, "uom": "件"}],
            demand=[self._demand(START, 10, pn="P9")],
        )
        ds = load(tables, days=10)
        self.assertEqual(1, ds.tasks[0].mpq)

    def test_tasks_sorted_by_due_date_then_pn(self):
        tables = base_tables(
            part=[
                {"site_code": SITE, "pn": "P1", "description": None, "stage": "FG",
                 "family_code": None, "uom": "件"},
                {"site_code": SITE, "pn": "A1", "description": None, "stage": "FG",
                 "family_code": None, "uom": "件"},
            ],
            demand=[self._demand(START, 10), self._demand(START, 20, pn="A1")],
        )
        ds = load(tables, days=10)
        self.assertEqual(["A1", "P1"], [t.pn for t in ds.tasks])


class TestSafetyStockResolution(unittest.TestCase):
    def _policy(self, start_day, end_day, safety, period="NORMAL", pn="P1"):
        return {"site_code": SITE, "pn": pn, "period_type": period, "start_day": start_day,
                "end_day": end_day, "target_stock_days": None, "max_stock_days": None,
                "safety_stock": safety, "max_stock": None, "valid_from": None}

    def test_matching_normal_window(self):
        tables = base_tables(inventory_policy=[self._policy(1, 30, 48)])
        ds = load(tables, days=10)
        self.assertEqual(48, ds.safety_stock["P1"])

    def test_month_end_window_not_used(self):
        tables = base_tables(inventory_policy=[self._policy(1, 30, 48, period="MONTH_END")])
        ds = load(tables, days=10)
        self.assertEqual(0, ds.safety_stock.get("P1", 0))

    def test_out_of_window_day(self):
        tables = base_tables(inventory_policy=[self._policy(10, 20, 48)])
        log = IssueLog()
        ds = load(tables, days=10, log=log)
        self.assertEqual(0, ds.safety_stock.get("P1", 0))
        self.assertTrue(any(i.entity == "inventory_policy" for i in log.gaps))

    def test_latest_start_day_wins(self):
        tables = base_tables(inventory_policy=[self._policy(1, 30, 48),
                                               self._policy(1, 15, 96)])
        ds = load(tables, days=10)
        self.assertEqual(48, ds.safety_stock["P1"])


class TestChangeoverFallback(unittest.TestCase):
    def _tables(self, **kw):
        tables = base_tables(**kw)
        tables["part_param_fg"][0]["mold_id"] = "M1"
        tables["mold"] = [{"site_code": SITE, "mold_id": "M1", "name": "模具一",
                           "available_sets": 2, "changeover_min": 25.0}]
        return tables

    def test_routing_level_is_last_resort(self):
        tables = self._tables()
        tables["routing"][0]["changeover_min"] = 40.0
        ds = load(tables, days=1)
        minutes, source = ds.changeover("PGLINKAGE", "M0", "M1", "P1")
        self.assertEqual(40.0, minutes)
        self.assertEqual("routing", source)

    def test_mold_level_beats_routing(self):
        tables = self._tables()
        tables["routing"][0]["changeover_min"] = 40.0
        ds = load(tables, days=1)
        minutes, source = ds.changeover("PGLINKAGE", None, "M1", "P1")
        self.assertEqual(25.0, minutes)
        self.assertEqual("tooling_mold", source)

    def test_setup_matrix_beats_everything(self):
        tables = self._tables(setup_matrix=[
            {"site_code": SITE, "line_code": "PGLINKAGE", "from_mold": "M0",
             "to_mold": "M1", "changeover_min": 12.0, "technician_count": None,
             "needs_cleaning": None, "valid_from": None},
        ])
        tables["routing"][0]["changeover_min"] = 40.0
        ds = load(tables, days=1)
        minutes, source = ds.changeover("PGLINKAGE", "M0", "M1", "P1")
        self.assertEqual(12.0, minutes)
        self.assertEqual("setup_matrix", source)

    def test_all_missing_reports_gap(self):
        tables = base_tables()
        log = IssueLog()
        ds = load(tables, days=1, log=log)
        minutes, source = ds.changeover("PGLINKAGE", "M0", "M1", "P1")
        self.assertIsNone(minutes)
        self.assertIsNone(source)
        self.assertTrue(any("换型时间" in i.message for i in log.gaps))


class TestMoldReverseLookup(unittest.TestCase):
    def test_from_tooling_applicable_pns(self):
        tables = base_tables(
            mold=[{"site_code": SITE, "mold_id": "M1", "name": "模具一",
                   "available_sets": 2, "changeover_min": 25.0}],
            tooling_mold=[{"site_code": SITE, "mold_id": "M1", "line_code": "PGLINKAGE",
                           "applicable_pns": ("P1", "P2"), "available_sets": 2,
                           "changeover_min": 25.0}],
        )
        ds = load(tables, days=1)
        self.assertEqual(frozenset({"P1", "P2"}), ds.mold_to_parts["M1"])

    def test_from_part_params_when_tooling_silent(self):
        tables = base_tables()
        tables["part_param_fg"][0]["mold_id"] = "M9"
        ds = load(tables, days=1)
        self.assertEqual(frozenset({"P1"}), ds.mold_to_parts["M9"])

    def test_union_of_both_sources(self):
        tables = base_tables(
            tooling_mold=[{"site_code": SITE, "mold_id": "M9", "line_code": "PGLINKAGE",
                           "applicable_pns": ("P7",), "available_sets": 1,
                           "changeover_min": None}],
        )
        tables["part_param_fg"][0]["mold_id"] = "M9"
        ds = load(tables, days=1)
        self.assertEqual(frozenset({"P1", "P7"}), ds.mold_to_parts["M9"])


class TestBom(unittest.TestCase):
    def test_children_of(self):
        tables = base_tables(bom=[
            {"site_code": SITE, "parent_pn": "P1", "parent_description": None,
             "child_pn": "S1", "child_description": None, "ratio": 2.0},
            {"site_code": SITE, "parent_pn": "P1", "parent_description": None,
             "child_pn": "S2", "child_description": None, "ratio": 1.5},
        ])
        ds = load(tables, days=1)
        children = ds.children_of("P1")
        self.assertEqual(("S1", "S2"), tuple(c.child_pn for c in children))
        self.assertIsInstance(children[0], BomLink)
        self.assertEqual(2.0, children[0].ratio)

    def test_no_children(self):
        ds = load(base_tables(), days=1)
        self.assertEqual((), ds.children_of("P1"))


class TestDueIndex(unittest.TestCase):
    def test_last_shift_of_due_day(self):
        ds = load(base_tables(), days=10)
        self.assertEqual(2, ds.due_index(START, "PGLINKAGE"))
        self.assertEqual(5, ds.due_index(dt.date(2026, 11, 3), "PGLINKAGE"))

    def test_clamped_when_beyond_horizon(self):
        ds = load(base_tables(), days=10)
        self.assertEqual(29, ds.due_index(dt.date(2026, 12, 6), "PGLINKAGE"))

    def test_clamped_when_before_horizon(self):
        ds = load(base_tables(), days=10)
        self.assertEqual(2, ds.due_index(dt.date(2026, 10, 1), "PGLINKAGE"))

    def test_depends_on_line_geometry(self):
        tables = base_tables()
        tables["line"].append({"site_code": SITE, "line_code": "1400T-6", "name": "1400T-6",
                               "trp_efficiency": 0.9, "shift_hours": 12.0,
                               "shifts_per_day": 2})
        ds = load(tables, days=10)
        self.assertEqual(1, ds.due_index(START, "1400T-6"))


class TestImmutabilityAndDegradation(unittest.TestCase):
    def test_dataset_is_frozen(self):
        ds = load(base_tables(), days=1)
        with self.assertRaises(Exception):
            ds.mode = CalendarMode.CONTINUOUS

    def test_task_and_route_are_frozen(self):
        ds = load(base_tables(demand=[{"site_code": SITE, "pn": "P1",
                                       "demand_date": START, "description": None,
                                       "quantity": 10, "customer_code": None}]), days=1)
        with self.assertRaises(Exception):
            ds.tasks[0].net_requirement = 999
        self.assertIsInstance(ds.routes[0], RouteOption)
        self.assertIsInstance(ds.tasks[0], Task)

    def test_degraded_fields_come_from_gaps(self):
        log = IssueLog()
        load(base_tables(), days=1, log=log)
        ds = DataSet.load(FakeRepo(base_tables()), SITE, START, 1,
                          calendar_mode="CONTINUOUS", log=log,
                          degraded_fields=frozenset({"routing.per_shift_cap_declared"}))
        self.assertIn("routing.per_shift_cap_declared", ds.degraded_fields)

    def test_degraded_fields_default_empty(self):
        ds = load(base_tables(), days=1)
        self.assertEqual(frozenset(), ds.degraded_fields)


class TestBomMrpExplosion(unittest.TestCase):
    def test_fg_demand_explodes_to_sf_tasks(self):
        tables = base_tables(
            part=[
                {"site_code": SITE, "pn": "P1", "description": "成品总成", "stage": "FG", "family_code": "FAM1", "uom": "件"},
                {"site_code": SITE, "pn": "S1", "description": "半成品转子", "stage": "SF", "family_code": "FAM1", "uom": "件"},
            ],
            part_param_fg=[
                {"site_code": SITE, "pn": "P1", "description": "成品总成", "mpq": 48, "moq": None, "family_code": "FAM1",
                 "mold_id": None, "special_shift": None, "co_production": None},
            ],
            part_param_sf=[
                {"site_code": SITE, "pn": "S1", "description": "半成品转子", "mpq": 96, "scrap_rate": 0.0, "max_stock": None,
                 "moq": None, "family_code": "FAM1", "mold_id": None, "special_shift": None, "co_production": None},
            ],
            bom=[
                {"site_code": SITE, "parent_pn": "P1", "parent_description": None,
                 "child_pn": "S1", "child_description": None, "ratio": 2.0},
            ],
            demand=[
                {"site_code": SITE, "pn": "P1", "demand_date": START, "description": None, "quantity": 100, "customer_code": None},
            ],
            initial_stock=[
                {"site_code": SITE, "pn": "S1", "material_type": "SF", "description": None, "quantity": 50},
            ],
        )
        ds = load(tables, days=10)
        fg_tasks = [t for t in ds.tasks if t.stage == "FG"]
        sf_tasks = [t for t in ds.tasks if t.stage == "SF"]
        self.assertEqual(1, len(fg_tasks))
        self.assertEqual(100, fg_tasks[0].net_requirement)
        self.assertEqual(1, len(sf_tasks))
        self.assertEqual(150, sf_tasks[0].net_requirement)
        self.assertEqual("P1", sf_tasks[0].parent_pn)
        self.assertEqual(96, sf_tasks[0].mpq)

    def test_fg_demand_explodes_to_sf_tasks_with_scrap_rate(self):
        # 100 FG net req * 2.0 ratio = 200 nominal SF pieces needed.
        # With 10% scrap rate (0.10), gross needed is ceil(200 / 0.90) = 223.
        # Minus 50 initial stock => net requirement = 173.
        tables = base_tables(
            part=[
                {"site_code": SITE, "pn": "P1", "description": "成品总成", "stage": "FG", "family_code": "FAM1", "uom": "件"},
                {"site_code": SITE, "pn": "S1", "description": "半成品转子", "stage": "SF", "family_code": "FAM1", "uom": "件"},
            ],
            part_param_fg=[
                {"site_code": SITE, "pn": "P1", "description": "成品总成", "mpq": 48, "moq": None, "family_code": "FAM1",
                 "mold_id": None, "special_shift": None, "co_production": None},
            ],
            part_param_sf=[
                {"site_code": SITE, "pn": "S1", "description": "半成品转子", "mpq": 96, "scrap_rate": 0.10, "max_stock": None,
                 "moq": None, "family_code": "FAM1", "mold_id": None, "special_shift": None, "co_production": None},
            ],
            bom=[
                {"site_code": SITE, "parent_pn": "P1", "parent_description": None,
                 "child_pn": "S1", "child_description": None, "ratio": 2.0},
            ],
            demand=[
                {"site_code": SITE, "pn": "P1", "demand_date": START, "description": None, "quantity": 100, "customer_code": None},
            ],
            initial_stock=[
                {"site_code": SITE, "pn": "S1", "material_type": "SF", "description": None, "quantity": 50},
            ],
        )
        ds = load(tables, days=10)
        sf_tasks = [t for t in ds.tasks if t.stage == "SF"]
        self.assertEqual(1, len(sf_tasks))
        self.assertEqual(173, sf_tasks[0].net_requirement)


if __name__ == "__main__":
    unittest.main()
