from __future__ import annotations

import unittest

from aps.schema.registry import (
    DEPRECATED_HEADERS,
    PARSERS,
    REGISTRY,
    STAGE_NORMALIZE,
    EntitySpec,
    FieldSpec,
    entities_with_sheet,
    get_entity,
    synthesized_entities,
)
from aps.schema.types import ContractTier, DataType, DupPolicy

FAST = True

CORE_19 = (
    "site", "line", "part", "mold", "customer", "product_family", "calendar_pattern",
    "demand", "initial_stock", "material_arrival", "part_param_fg", "part_param_sf",
    "bom", "routing", "setup_matrix", "inventory_policy", "operation_leadtime",
    "tooling_mold", "preventive_maintenance",
)
V3_4 = ("shift_staffing", "product_pairing", "calendar_day", "setup_technician")


class TestRegistryShape(unittest.TestCase):
    def test_has_23_entities(self):
        self.assertEqual(len(REGISTRY), 23)

    def test_core_19_present(self):
        for name in CORE_19:
            with self.subTest(entity=name):
                self.assertIn(name, REGISTRY)

    def test_v3_4_present_and_optional(self):
        for name in V3_4:
            with self.subTest(entity=name):
                self.assertIn(name, REGISTRY)
                self.assertFalse(REGISTRY[name].sheet_required)

    def test_optional_sheets_are_exactly_the_four_v3_ones(self):
        optional = {e.sheet for e in REGISTRY.values() if not e.sheet_required}
        self.assertEqual(
            {"班次人力可用性", "产品配对关系", "工作日历", "换模技术员"}, optional
        )

    def test_all_core_sheets_required(self):
        for name in CORE_19:
            spec = REGISTRY[name]
            if spec.sheet is not None:
                with self.subTest(entity=name):
                    self.assertTrue(spec.sheet_required)

    def test_registry_is_insertion_sorted(self):
        self.assertEqual(list(REGISTRY), sorted(REGISTRY))

    def test_get_entity_unknown_raises(self):
        with self.assertRaises(KeyError):
            get_entity("nope")

    def test_sheeted_and_synthesized_partition(self):
        sheeted = {e.name for e in entities_with_sheet()}
        synth = {e.name for e in synthesized_entities()}
        self.assertEqual(sheeted | synth, set(REGISTRY))
        self.assertEqual(sheeted & synth, set())
        self.assertEqual(synth, {"site", "part", "product_family", "calendar_pattern"})


class TestFieldAccessors(unittest.TestCase):
    def test_field_lookup(self):
        routing = get_entity("routing")
        self.assertEqual(routing.field("ct_sec").dtype, DataType.REAL)
        with self.assertRaises(KeyError):
            routing.field("nope")

    def test_header_to_field_uses_verbatim_headers(self):
        routing = get_entity("routing")
        self.assertIs(routing.header_to_field["CT (秒)"], routing.field("ct_sec"))
        self.assertIs(routing.header_to_field["认证"], routing.field("certified"))

    def test_fullwidth_paren_alias(self):
        lead = get_entity("operation_leadtime")
        self.assertIs(lead.header_to_field["提前准备时间（分钟）"], lead.field("prep_minutes"))
        self.assertIs(lead.header_to_field["提前准备时间 (分钟)"], lead.field("prep_minutes"))

    def test_required_headers_only_t1(self):
        routing = get_entity("routing")
        required = routing.required_headers
        self.assertIn("CT (秒)", required)
        self.assertNotIn("单班定额产能 (件)", required)   # T2
        self.assertNotIn("模具清单", required)             # T2

    def test_pk_fields(self):
        routing = get_entity("routing")
        self.assertEqual(
            [f.name for f in routing.pk_fields],
            ["site_code", "pn", "op_seq", "line_code"],
        )


class TestAdjudications(unittest.TestCase):
    def test_fg_params_do_not_read_shift_geometry(self):
        # §3.2：单班工时/每天班次数以 line 为准，FG 表该两列不读取
        fg = get_entity("part_param_fg")
        headers = set(fg.header_to_field)
        self.assertNotIn("单班工时 (小时)", headers)
        self.assertNotIn("每天班次数", headers)

    def test_line_has_no_site_column_so_site_injected(self):
        line = get_entity("line")
        self.assertTrue(line.site_injected)
        self.assertNotIn("站点", set(line.header_to_field))

    def test_initial_stock_pk_amendment_a1(self):
        stock = get_entity("initial_stock")
        self.assertEqual(stock.pk, ("site_code", "pn", "material_type"))
        self.assertEqual(stock.dup_policy, DupPolicy.SUM)
        self.assertEqual(stock.sum_fields, ("quantity",))

    def test_operation_leadtime_dedupe(self):
        lead = get_entity("operation_leadtime")
        self.assertEqual(lead.pk, ("site_code", "pn", "op_seq"))
        self.assertEqual(lead.dup_policy, DupPolicy.DEDUPE)

    def test_tooling_mold_pk(self):
        self.assertEqual(get_entity("tooling_mold").pk, ("site_code", "mold_id", "line_code"))
        self.assertEqual(DupPolicy.ERROR, get_entity("tooling_mold").dup_policy)

    def test_mold_shares_sheet_with_tooling_and_dedupes(self):
        mold = get_entity("mold")
        self.assertEqual("工装模具约束 Tooling-Mold", mold.sheet)
        self.assertEqual(mold.sheet, get_entity("tooling_mold").sheet)
        self.assertEqual(mold.pk, ("site_code", "mold_id"))
        self.assertEqual(DupPolicy.DEDUPE, mold.dup_policy)

    def test_parser_declarations_are_amendment_a4(self):
        self.assertEqual("stage", get_entity("initial_stock").field("material_type").parser)
        self.assertEqual("shift_index", get_entity("material_arrival").field("eta_shift").parser)

    def test_bom_child_reference_is_optional_amendment_a5(self):
        child = get_entity("bom").field("child_pn")
        self.assertEqual("part.pn", child.fk)
        self.assertTrue(child.fk_optional)
        self.assertFalse(get_entity("bom").field("parent_pn").fk_optional)

    def test_initial_stock_pn_has_no_fk(self):
        # 实测 4173 个库存 PN 不在 FG∪SF 参数表中，加 fk 会全量报错
        self.assertIsNone(get_entity("initial_stock").field("pn").fk)

    def test_exactly_one_fk_optional_field_in_registry(self):
        optional = [
            (name, f.name)
            for name, spec in REGISTRY.items() for f in spec.fields if f.fk_optional
        ]
        self.assertEqual([("bom", "child_pn")], optional)

    def test_demand_is_unpivot(self):
        demand = get_entity("demand")
        self.assertEqual(demand.unpivot, "demand_date")

    def test_stage_normalize_map(self):
        self.assertEqual(
            STAGE_NORMALIZE,
            {"FING": "FG", "SFIN": "SF", "FG": "FG", "SF": "SF",
             "COMP": "COMP", "PACK": "PACK", "RAW": "RAW"},
        )

    def test_deprecated_headers_cover_section_3_3(self):
        fg = DEPRECATED_HEADERS["产线与成品参数 Parameters-FG"]
        self.assertIn("产线", fg)
        self.assertIn("效率 TRP", fg)
        routing = DEPRECATED_HEADERS["工艺路线与候选机台 Routing-Machines"]
        self.assertEqual({"备选候选线", "机台", "TRP"}, set(routing))
        eta = DEPRECATED_HEADERS["关键物料到货 Material-Arrival-ETA"]
        self.assertEqual({"放行工时", "关联排产零件", "状态"}, set(eta))

    def test_synthesized_parts_and_families(self):
        part = get_entity("part")
        self.assertTrue(part.synthesized)
        self.assertIsNone(part.sheet)
        fam = get_entity("product_family")
        self.assertTrue(fam.synthesized)


class TestValueParsers(unittest.TestCase):
    def test_stage_parser_on_all_stage_columns(self):
        for entity, field_name in (("initial_stock", "material_type"),
                                   ("routing", "stage"),
                                   ("operation_leadtime", "stage")):
            with self.subTest(entity=entity):
                self.assertEqual("stage", get_entity(entity).field(field_name).parser)

    def test_shift_index_parser_on_eta_shift(self):
        spec = get_entity("material_arrival").field("eta_shift")
        self.assertEqual("shift_index", spec.parser)
        self.assertEqual(DataType.INT, spec.dtype)

    def test_enum_code_parser_is_implicit_from_enum_codes(self):
        self.assertEqual("enum_code", get_entity("inventory_policy").field("period_type")
                         .resolved_parser)

    def test_enum_code_parser_is_explicit_where_no_domain_declared(self):
        self.assertEqual("enum_code", get_entity("customer").field("tier").resolved_parser)
        self.assertEqual("enum_code",
                         get_entity("preventive_maintenance").field("disposition").resolved_parser)

    def test_no_parser_where_plain_scalar(self):
        self.assertIsNone(get_entity("routing").field("ct_sec").resolved_parser)

    def test_all_parsers_in_vocabulary(self):
        for name, spec in REGISTRY.items():
            for field in spec.fields:
                if field.parser is not None:
                    with self.subTest(entity=name, field=field.name):
                        self.assertIn(field.parser, PARSERS)


class TestSelfValidation(unittest.TestCase):
    def test_every_entity_passes_self_check(self):
        for name, spec in REGISTRY.items():
            with self.subTest(entity=name):
                self.assertEqual([], spec.validate_self())

    def test_self_check_catches_pk_not_declared_as_field(self):
        bad = EntitySpec(
            name="bad", sheet=None,
            fields=(FieldSpec(name="a", dtype=DataType.TEXT),),
            pk=("a", "ghost"),
        )
        self.assertTrue(any("ghost" in m for m in bad.validate_self()))

    def test_self_check_catches_pk_field_not_flagged(self):
        bad = EntitySpec(
            name="bad", sheet=None,
            fields=(FieldSpec(name="a", dtype=DataType.TEXT, pk=False),),
            pk=("a",),
        )
        self.assertTrue(any("pk=True" in m for m in bad.validate_self()))

    def test_self_check_catches_t1_pk_that_is_nullable(self):
        bad = EntitySpec(
            name="bad", sheet=None,
            fields=(FieldSpec(name="a", dtype=DataType.TEXT, pk=True, nullable=True),),
            pk=("a",),
        )
        self.assertTrue(any("nullable" in m for m in bad.validate_self()))

    def test_self_check_catches_missing_source_column(self):
        bad = EntitySpec(
            name="bad", sheet="S",
            fields=(FieldSpec(name="a", dtype=DataType.TEXT, pk=True, nullable=False),),
            pk=("a",),
        )
        self.assertTrue(any("source_columns" in m for m in bad.validate_self()))

    def test_self_check_allows_site_injected_without_source(self):
        ok = EntitySpec(
            name="ok", sheet="S", site_injected=True,
            fields=(FieldSpec(name="site_code", dtype=DataType.TEXT, pk=True, nullable=False),),
            pk=("site_code",),
        )
        self.assertEqual([], ok.validate_self())

    def test_self_check_catches_unknown_fk_target(self):
        bad = EntitySpec(
            name="bad", sheet="S",
            fields=(
                FieldSpec(name="a", dtype=DataType.TEXT, pk=True, nullable=False,
                          source_columns=("A",)),
                FieldSpec(name="b", dtype=DataType.TEXT, fk="ghost.x", source_columns=("B",)),
            ),
            pk=("a",),
        )
        self.assertTrue(any("ghost" in m for m in bad.validate_self()))

    def test_self_check_catches_unknown_parser(self):
        bad = EntitySpec(
            name="bad", sheet="S",
            fields=(FieldSpec(name="a", dtype=DataType.TEXT, pk=True, nullable=False,
                              source_columns=("A",), parser="magic"),),
            pk=("a",),
        )
        self.assertTrue(any("parser" in m for m in bad.validate_self()))

    def test_self_check_catches_fk_optional_without_fk(self):
        bad = EntitySpec(
            name="bad", sheet="S",
            fields=(FieldSpec(name="a", dtype=DataType.TEXT, pk=True, nullable=False,
                              source_columns=("A",), fk_optional=True),),
            pk=("a",),
        )
        self.assertTrue(any("fk_optional" in m for m in bad.validate_self()))

    def test_self_check_catches_sum_field_not_in_fields(self):
        bad = EntitySpec(
            name="bad", sheet="S", dup_policy=DupPolicy.SUM, sum_fields=("nope",),
            fields=(FieldSpec(name="a", dtype=DataType.TEXT, pk=True, nullable=False,
                              source_columns=("A",)),),
            pk=("a",),
        )
        self.assertTrue(any("nope" in m for m in bad.validate_self()))


if __name__ == "__main__":
    unittest.main()
