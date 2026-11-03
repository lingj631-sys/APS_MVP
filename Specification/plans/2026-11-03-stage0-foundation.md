# 阶段 0：地基与排雷 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给主数据 schema 加上主键/外键/取值约束/契约分层与两张缺失的新表，并把 loader 里编造的默认值和硬编码年份全部换成机器可读的 `DataGap`，为阶段 1 的双轨收敛和阶段 2 的指标层铺好地基。

**Architecture:** 单点扩展 `src/master_schema.py`（新增字段全带默认值，既有 15 表声明零改动），新增 `src/data_gaps.py` 承载 `DataGap` 值类型并在 `ProductionInputSnapshot` 末尾追加一个带默认值的 `gaps` 字段作为传输通道，然后逐区域把 `excel_loader.py` / `data_loader.py` 里的 `_number(val, 编造值)` 改成「取值或产 gap」。行为变化一律先落在 `warn` 档，跑全量测试看暴露面，再逐字段决定是否升 `fail`。

**Tech Stack:** Python 3 + `dataclasses` + `openpyxl`；测试用标准库 `unittest`（无 pytest）；求解器 OR-Tools CP-SAT（本阶段只碰 `scheduler.py` 的延误窗口一处）。

**Spec:** `Specification/aps_rule_set_redesign.md`（**rev.3**）。本计划实现其 §8.2 阶段 0，验收清单见该节；交接约定见附录 D。

> rev.2 是 commit `17a383b`。撰写本计划时对着真实模板核实，发现 rev.2 有三处实测错误（行数、行集合、日期轴定位方式），已更正为 rev.3——见规格头部表格的「rev.3 更正的三处实测错误」，对应下面的偏差 5、6、7。

## Global Constraints

以下每条都逐字来自规格，**每个任务的要求都隐含包含本节**：

- 一律用 `.venv/Scripts/python.exe`，一律前缀 `PYTHONIOENCODING=utf-8`（Windows 控制台 cp1252 会产出 61 个假的 `UnicodeEncodeError` 失败）
- fast 测试命令：`PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast`，当前基线 **61 项 OK**
- 全量测试命令：`PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py`（含 90–300 秒求解器）
- 新增测试模块**必须**加进 `run_tests.py:32-40` 的 `fast_modules` 列表，否则「fast 全绿」是假的（D.3 第 8 条）
- 新文件一律加 `from __future__ import annotations`（现有 32/48 个 `src/*.py` 如此）
- 代码注释 / docstring / 标识符用**英文**；文档 / UI 文案用**中文**
- 数据类一律 `@dataclass(frozen=True)`，除非有可变需求
- **禁止**在 loader 里补默认值（D.3 第 1 条）；**禁止**新增 `print`（第 2 条）；**禁止**改第 3 层契约的既有字段与 7 个消费点签名（第 3 条）
- **禁止**无理由重切 `tests/fixtures/wl_10d_baseline.json`；每次变更提交信息必须写明「哪个缺陷修复导致 + 预期变化方向 + 实际是否符合」（§8.1）
- 一个任务一个提交；提交前 `git status` 必须干净，特别是 `data/snapshot_WL.json`（内含绝对路径，跑测试就会产生 diff，用 `git checkout -- data/snapshot_WL.json` 还原）
- 不新增第三方依赖

---

## 规格偏差与本计划的解释（开工前必读）

规格附录 D.4 要求：发现规格有错或不完整时**停下报告，不要顺手修正**。以下七项是本计划撰写时对着真实模板与代码核出来的，已按推荐方案写进任务，但**执行者应在开工前确认，若有异议先提出**：

**偏差 1｜`DataGap` 的首字段名。** §3.4 定义为 `DataGap(metric_id, sheet_key, column_key, index, reason)`。但阶段 0 的生产者是 loader，loader 没有 `metric_id`。
→ 本计划把首字段命名为 `owner: str`：指标层填 `metric_id`，loader 填 `"{module}.{function}"`。字段顺序与其余四个字段完全按规格。

**偏差 2｜在「冻结的」快照上加字段。** §5.2 与 D.3 第 3 条冻结 `ProductionInputSnapshot`。但 §5.6 要求 loader 产出 `DataGap`，gap 必须有传输通道，而全局收集器正是本次要消灭的东西（D.3 第 1、2 条）。
→ 本计划在 `ProductionInputSnapshot` **末尾追加** `gaps: List[DataGap] = field(default_factory=list)`。已核实：全部 9 处构造点（`tests/test_regressions.py:38,100,130,176,236` 等）均用关键字参数，追加带默认值的尾字段对它们零影响；7 个消费点签名不变。这是对「冻结」的解释——冻结的是**既有字段与消费点契约**，不是禁止追加。

**偏差 3｜硬编码年份 2026 的站点数被低估。** 规格 M6 只列了 `excel_loader.py:511`、`data_loader.py:455,462`、`server.py:1163-1172`。实测 `grep -rn "datetime(2026" src/` 有 **11 处**，另含 `data_loader.py:259`、`exporter.py:493`、`main.py:42`、`rule_sets/cli.py:35`、`scheduler.py:312`、`server.py:413`。
→ 任务 5 覆盖全部 11 处，不只规格列的 4 处。

**偏差 4｜编造默认值不止在 loader 里，也在 dataclass 字段默认值里。** 规格 §5.6 与 M 系列只列了 `excel_loader.py` / `data_loader.py` 的调用点。实测**字段默认值本身就是编造的**：

| dataclass | 字段 | 编造值 |
|---|---|---|
| `LineBase`（`data_loader.py:51-58`） | `trp` / `hours_per_shift` / `shifts_per_day` | `0.71` / `8.0` / `3` |
| `ToolingMold`（`:72-83`） | `available_qty` / `changeover_time_min` / `max_life_strokes` / `maintenance_cycle_strokes` / `status` | `1` / `30.0` / `500000` / `50000` / `"可用 (Available)"` |
| `ProductParam`（`:25-47`） | `hours_per_shift` / `shifts_per_day` | `8.0` / `3` |

只改 loader 的 `_number(val, 0.71)` 调用点，字段默认值会立刻把同一个数补回来——**改了等于没改**。
→ 任务 7 先改 dataclass 默认值（T1 字段改为无默认值 / 必填，deprecated 字段改为中性零值），再改 loader 调用点。两步在同一个任务里，因为分开做中间态必然测试全红。

**偏差 5｜`part_pairing` 的行集合：规格 §4.6.2 的两条规则选出的料号不一致。** 规格同时说「扫描『品名描述』按 LHD/RHD/左驾/右驾 关键词归并」和「标了『同时生产 = Y』的 4 个料号必须落 `unpaired`」。对着模板实测，这两条选出的是**不同的料号集合**：

| 规则 | 选出的料号 |
|---|---|
| 仅关键词 | `WCA1894`(L)、`WCB8574`(R)、`WB80510`(L)、`WA14208S`(左驾)、`WD48696S`(左驾) |
| 仅『同时生产=Y』 | `WB35356`、`WB80510`、`WD48696`、`WD59238` |

交集只有 `WB80510`。只用关键词会得到 1 paired + 3 unpaired = **4 行**（`WB35356`/`WD48696`/`WD59238` 描述里没有任何 L/R 字样），达不到验收 0.5 要求的 5 行；只用 Y 标记会得到 0 paired。

→ 本计划采用的合并规则：**一个料号有行，当且仅当它能按（型号 token, L/R 侧）配出对手件，或业务标了『同时生产=Y』。** 实测恰好产出 1 paired（`WCA1894`↔`WCB8574`）+ 4 unpaired（`WB35356`、`WB80510`、`WD48696`、`WD59238`）= 5 行，与验收 0.5 一致。

这条规则的代价是 `WA14208S`（吉利DX11左驾，备件）与 `WD48696S`（领克L743左驾，备件）**不出行**——它们描述里有『左驾』，但既没有对手件也没有 Y 标记。判断依据：HC-08 只对『声明了要同时生产』或『真的配成对』的料号有意义，一个配不上对的备件行不携带任何信息，写进去只会让绑定报告多两条无意义的 `unpaired`。若业务认为备件也应登记，改 `_simultaneous_flagged` 的判据即可，不影响其它任务。

此规则已同步写进规格 §4.6.2（rev.3）。

**偏差 6｜`line_headcount` 行数是 297，不是规格 rev.2 写的 90。** 规格 §4.6.1 写「日期轴 10 天 → 90 行」。实测模板 `Customer Demand` 表头声明了 **33** 个日期列（`10/13`–`11/15`，其中 25 列有量），10 天是黄金基线 `wl_10d_baseline.json` 的**求解窗口**，不是模板的**数据轴**。3 产线 × 33 日期 × 3 班次 = **297 行**。
→ 已同步更正规格 §4.6.1（rev.3）与验收 0.5。取全轴而非只取有量的 25 天，理由：人数可用性是产能事实，与当天有没有订单无关；只覆盖 25 天的话，计划员一补 `10/18` 的需求就立刻缺人数行。

**偏差 7｜日期轴取不到：`read_master_rows` 丢弃 `date_axis=True` 的列。** `read_master_rows`（`excel_loader.py:645`）只遍历 `spec.columns`，而 `DEMAND` 的 `columns` 只有 `site`/`part_no`/`description`（`:103-107`），日期列是动态识别的。实测：

```
$ .venv/Scripts/python.exe -c "from src.excel_loader import read_master_rows as r; print(sorted(r('APS_DataSource_Template.xlsx')['demand'][0]))"
['description', 'part_no', 'site']
```

→ 种子脚本改走 `_demand_header_row` + `_demand_dates`（`excel_loader.py:191,200`），也就是 `load_workbook_snapshot:490` 自己用的那条路，因此写出的日期与求解器看到的同源。**且必须沿用列序、不得 `sorted()`**——`_demand_dates` 返回 `"M/D"` 字符串，跨月时 `"11/10" < "11/2"`。这条已写进规格 §5.9 事实 2 的补充说明。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/data_gaps.py` | `DataGap` 值类型 + 收集器助手。**唯一**的 gap 定义处，阶段 2 的指标层直接复用 | 新建 |
| `src/master_schema.py` | 15→17 表的声明式 schema：列、主键、外键、取值约束、契约分层 | 修改（`Column:50-63`、`SheetSpec:66-83`、`SHEETS:398-413`，各表声明） |
| `src/data_loader.py` | dataclass 定义 + CSV 回退加载器。`ProductionInputSnapshot:136-150` 在此 | 修改 |
| `src/excel_loader.py` | 工作簿 → 快照。编造默认值的主战场 | 修改（`:301-303`、`:337-342`、`:388-408`、`:489`、`:511`、`:525-538`、`:551-555`、`:572`、`:580-582`、`:599-604`） |
| `src/planning_calendar.py` | 年度/起始时刻的唯一声明处，取代 11 处散落的 `datetime(2026, ...)` | 新建（任务 5） |
| `scripts/seed_simulated_masterdata.py` | D15 的落地物：确定性地把两张新表的模拟数据写进模板 | 新建（任务 6） |
| `src/scheduler.py` | 只碰 `_build_windows:1157-1164` 与常量 `LATENESS_SLACK_SHIFTS:69` | 修改 |
| `tests/test_data_gaps.py` | `DataGap` 与收集器助手的单测 | 新建 |
| `tests/test_master_schema.py` | schema 新字段、17 表、主键/外键/取值约束的单测（既有文件，追加） | 修改 |
| `tests/test_seed_masterdata.py` | 种子脚本的确定性与 provenance 断言 | 新建 |
| `tests/test_loader_gaps.py` | 「缺数据必产 gap、必不编造」的回归网 | 新建 |
| `tests/test_kernel.py` | 删 `:289-290` 的数量断言，换成契约检查骨架 | 修改 |
| `run_tests.py` | `fast_modules:32-40` 追加 5 个新建模块（`test_data_gaps`、`test_planning_calendar`、`test_seed_masterdata`、`test_loader_gaps`、`test_rule_contract`）+ 1 个既有模块（`test_campaign`，任务 13 起承担切换安全网）。规格 §8.0 明确：**没进这个列表的测试，「fast 全绿」就是假的** | 修改 |

**依赖顺序**：任务 1 → 2 → 3 → 4 → 5 → 6 是链（后者用前者的类型；任务 6 的种子脚本用任务 5 的 `DEFAULT_PLANNING_YEAR` / `parse_month_day` 把 `"M/D"` 变成真日期，所以**日历必须先于种子脚本**）。任务 7–10 互相独立，但都依赖任务 1。任务 11–13 独立。

---

### Task 1: `DataGap` 值类型与快照传输通道

**Files:**
- Create: `src/data_gaps.py`
- Modify: `src/data_loader.py:136-150`（`ProductionInputSnapshot`）
- Test: `tests/test_data_gaps.py`
- Modify: `run_tests.py:32-40`

**Interfaces:**
- Consumes: 无（本任务是链首）
- Produces:
  - `DataGap(owner: str, sheet_key: str, column_key: str, index: Tuple[Any, ...], reason: str)` — frozen dataclass
  - `GapCollector` — 有 `add(owner, sheet_key, column_key, index, reason) -> None`、`extend(gaps: Iterable[DataGap]) -> None`、`gaps() -> List[DataGap]`、`warn(messages: List[str], gap: DataGap, text: str) -> None`
  - `ProductionInputSnapshot.gaps: List[DataGap]`（尾字段，默认空列表）

- [ ] **Step 1: 写失败测试**

`tests/test_data_gaps.py`：

```python
from __future__ import annotations

import unittest

from src.data_gaps import DataGap, GapCollector


class TestDataGap(unittest.TestCase):
    def test_is_frozen_and_hashable(self):
        gap = DataGap("excel_loader.load_workbook_snapshot", "line_base", "trp",
                      ("WL", "PGLINKAGE"), "missing")
        with self.assertRaises(Exception):
            gap.reason = "other"          # frozen
        self.assertIn(gap, {gap})          # hashable

    def test_field_order_matches_spec(self):
        gap = DataGap("owner", "sheet", "column", ("idx",), "reason")
        self.assertEqual(
            (gap.owner, gap.sheet_key, gap.column_key, gap.index, gap.reason),
            ("owner", "sheet", "column", ("idx",), "reason"),
        )

    def test_index_is_always_a_tuple(self):
        gap = DataGap("o", "s", "c", "PGLINKAGE", "r")
        self.assertEqual(gap.index, ("PGLINKAGE",))


class TestGapCollector(unittest.TestCase):
    def test_collects_and_warns_together(self):
        collector = GapCollector()
        messages: list = []
        gap = DataGap("owner", "line_base", "trp", ("WL", "L1"), "missing")
        collector.warn(messages, gap, "产线 L1 缺 TRP")
        self.assertEqual(collector.gaps(), [gap])
        self.assertEqual(messages, ["产线 L1 缺 TRP"])

    def test_extend_dedupes_identical_gaps(self):
        collector = GapCollector()
        gap = DataGap("o", "s", "c", ("i",), "r")
        collector.extend([gap, gap])
        self.assertEqual(len(collector.gaps()), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_data_gaps -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'src.data_gaps'`

- [ ] **Step 3: 实现 `src/data_gaps.py`**

```python
"""Machine-readable record of a master-data value that is absent.

A gap is not an exception and not a silent default.  Loaders produce them,
the metric layer (stage 2) consumes them, and the binding report turns them
into ``degraded`` / ``skipped_data_gap`` / ``simulated`` statuses.

``owner`` is the metric id for metric-produced gaps and ``"{module}.{function}"``
for loader-produced ones.  See spec section 3.4 (deviation 1 in the plan).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Sequence, Tuple


@dataclass(frozen=True)
class DataGap:
    owner: str
    sheet_key: str
    column_key: str
    index: Tuple[Any, ...]
    reason: str

    def __post_init__(self):
        if not isinstance(self.index, tuple):
            object.__setattr__(self, "index", (self.index,))


class GapCollector:
    """Accumulates gaps alongside the human-readable warning list.

    Deliberately not frozen: a single collector lives for one load call and is
    drained into ``ProductionInputSnapshot.gaps`` at the end.
    """

    def __init__(self) -> None:
        self._gaps: List[DataGap] = []
        self._seen: set = set()

    def add(self, owner: str, sheet_key: str, column_key: str,
            index: Any, reason: str) -> DataGap:
        gap = DataGap(owner, sheet_key, column_key,
                      index if isinstance(index, tuple) else (index,), reason)
        if gap not in self._seen:
            self._seen.add(gap)
            self._gaps.append(gap)
        return gap

    def extend(self, gaps: Iterable[DataGap]) -> None:
        for gap in gaps:
            if gap not in self._seen:
                self._seen.add(gap)
                self._gaps.append(gap)

    def warn(self, messages: List[str], gap: DataGap, text: str) -> None:
        """Record a gap and its planner-facing message in one call."""
        self.extend([gap])
        messages.append(text)

    def gaps(self) -> List[DataGap]:
        return list(self._gaps)
```

- [ ] **Step 4: 给 `ProductionInputSnapshot` 追加尾字段**

`src/data_loader.py`，在 `layout: str = ""`（`:150`）之后追加。**只追加，不动任何既有字段**：

```python
    gaps: List["DataGap"] = field(default_factory=list)
```

并在文件顶部的 `typing` 导入块之后加：

```python
from src.data_gaps import DataGap
```

若这造成循环导入（`data_gaps` 不导入 `data_loader`，所以不该发生），改为在 `TYPE_CHECKING` 下导入并把注解写成字符串。

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_data_gaps -v`
Expected: PASS（5 项）

- [ ] **Step 6: 把新模块加进 fast 列表**

`run_tests.py:32-40` 的 `fast_modules` 追加 `"tests.test_data_gaps"`。

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast`
Expected: `Ran 66 tests ... OK`（61 + 5）

- [ ] **Step 7: 确认 9 处既有构造点未被破坏**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_regressions -v`
Expected: PASS（证明追加尾字段对关键字构造零影响 —— 偏差 2 的验证）

- [ ] **Step 8: 还原快照噪音并提交**

```bash
git checkout -- data/snapshot_WL.json 2>/dev/null || true
git add src/data_gaps.py src/data_loader.py tests/test_data_gaps.py run_tests.py
git commit -m "feat: add DataGap value type and gap channel on the input snapshot"
```

---

### Task 2: schema 字段扩展

**Files:**
- Modify: `src/master_schema.py:50-63`（`Column`）、`:66-83`（`SheetSpec`）
- Test: `tests/test_master_schema.py`（追加）

**Interfaces:**
- Consumes: 无
- Produces:
  - `Column` 新字段：`primary_key: bool = False`、`foreign_key: Optional[Tuple[str, str]] = None`、`check: str = ""`、`contract_tier: str = "t1_required"`、`editable: bool = True`
  - `SheetSpec` 新字段：`primary_key: Tuple[str, ...] = ()`、`surrogate_key: str = "row_id"`
  - `SheetSpec.primary_key_columns() -> Tuple[Column, ...]`
  - `CONTRACT_TIERS: Tuple[str, ...]`、`CHECK_FORMS: Tuple[str, ...]`
  - `parse_check(expr: str) -> Tuple[str, Tuple[Any, ...]]`、`check_value(expr: str, value: Any) -> bool`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_master_schema.py`：

```python
class TestSchemaContractFields(unittest.TestCase):
    def test_new_column_fields_default_safely(self):
        col = Column("x", "X")
        self.assertFalse(col.primary_key)
        self.assertIsNone(col.foreign_key)
        self.assertEqual(col.check, "")
        self.assertEqual(col.contract_tier, "t1_required")
        self.assertTrue(col.editable)

    def test_existing_15_sheets_still_declare_unchanged(self):
        # defaults mean zero edits to the existing declarations
        self.assertEqual(TEMPLATE_SHEET_COUNT, 15)
        for spec in INPUT_SHEETS:
            self.assertEqual(spec.surrogate_key, "row_id")

    def test_new_sheet_fields_default_safely(self):
        spec = SheetSpec(key="k", title="T", columns=())
        self.assertEqual(spec.primary_key, ())
        self.assertEqual(spec.surrogate_key, "row_id")

    def test_contract_tier_vocabulary_is_closed(self):
        self.assertEqual(CONTRACT_TIERS,
                         ("t1_required", "t2_declared_empty", "t3_absent"))

    def test_check_forms_are_the_four_declared(self):
        self.assertEqual(CHECK_FORMS, (">=", "<=", ">", "<", "between", "in", "nonempty"))

    def test_parse_and_evaluate_each_check_form(self):
        self.assertTrue(check_value(">=0", 0))
        self.assertFalse(check_value(">=0", -1))
        self.assertTrue(check_value("between(1, 3)", 2))
        self.assertFalse(check_value("between(1, 3)", 4))
        self.assertTrue(check_value("in('actual','simulated')", "simulated"))
        self.assertFalse(check_value("in('actual','simulated')", "guessed"))
        self.assertTrue(check_value("nonempty", " x "))
        self.assertFalse(check_value("nonempty", "   "))

    def test_unparseable_check_is_rejected_at_parse_time(self):
        with self.assertRaises(ValueError):
            parse_check("len(x) > 3")

    def test_primary_key_columns_resolves_from_either_source(self):
        spec = SheetSpec(key="k", title="T", primary_key=("a",), columns=(
            Column("a", "A"), Column("b", "B", primary_key=True)))
        self.assertEqual([c.key for c in spec.primary_key_columns()], ["a", "b"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_master_schema -v`
Expected: FAIL — `ImportError: cannot import name 'CONTRACT_TIERS'`

- [ ] **Step 3: 扩展 `Column` 与 `SheetSpec`**

`src/master_schema.py:50-63` 替换为（逐字照抄规格 §5.8）：

```python
@dataclass(frozen=True)
class Column:
    """One logical column of a master-data sheet."""

    key: str
    header: str
    aliases: Tuple[str, ...] = ()
    required: bool = False
    kind: str = "text"  # text | number | int | date | datetime | bool
    note: str = ""
    deprecated: bool = False  # gray column: not part of the new framework
    primary_key: bool = False
    foreign_key: Optional[Tuple[str, str]] = None  # (sheet_key, column_key)
    check: str = ""
    contract_tier: str = "t1_required"
    editable: bool = True

    def candidates(self) -> Tuple[str, ...]:
        return (self.header,) + tuple(a for a in self.aliases if a and a != self.header)
```

`:66-83` 的 `SheetSpec` 追加两个字段与方法：

```python
    primary_key: Tuple[str, ...] = ()
    surrogate_key: str = "row_id"

    def primary_key_columns(self) -> Tuple[Column, ...]:
        """Natural key columns: explicit ``primary_key`` plus per-column flags."""
        by_key = {c.key: c for c in self.columns}
        declared = tuple(by_key[k] for k in self.primary_key if k in by_key)
        flagged = tuple(c for c in self.columns if c.primary_key and c not in declared)
        return declared + flagged
```

- [ ] **Step 4: 实现取值约束（不做表达式解析器）**

在 `SheetSpec` 之后、`# The 15 sheets` 分节注释之前插入。规格 §5.8 明确只支持四种形态：

```python
CONTRACT_TIERS: Tuple[str, ...] = ("t1_required", "t2_declared_empty", "t3_absent")

#: The only constraint forms supported.  Deliberately not an expression
#: language -- a dispatch table keeps save-time validation trivial.
CHECK_FORMS: Tuple[str, ...] = (">=", "<=", ">", "<", "between", "in", "nonempty")

_CMP_RE = re.compile(r"^(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)$")
_BETWEEN_RE = re.compile(r"^between\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)$")
_IN_RE = re.compile(r"^in\((.*)\)$")


def parse_check(expr: str) -> Tuple[str, Tuple[Any, ...]]:
    """Return ``(form, args)`` or raise ``ValueError`` for anything else."""
    text = (expr or "").strip()
    if not text:
        return ("", ())
    if text == "nonempty":
        return ("nonempty", ())
    match = _CMP_RE.match(text)
    if match:
        return (match.group(1), (float(match.group(2)),))
    match = _BETWEEN_RE.match(text)
    if match:
        return ("between", (float(match.group(1)), float(match.group(2))))
    match = _IN_RE.match(text)
    if match:
        items = [p.strip().strip("'\"") for p in match.group(1).split(",")]
        if not all(items):
            raise ValueError(f"empty member in check expression: {expr!r}")
        return ("in", tuple(items))
    raise ValueError(f"unsupported check expression: {expr!r}")


def check_value(expr: str, value: Any) -> bool:
    """Evaluate a parsed constraint.  ``None`` never satisfies a constraint."""
    form, args = parse_check(expr)
    if not form:
        return True
    if value is None:
        return False
    if form == "nonempty":
        return str(value).strip() != ""
    if form == "in":
        return str(value).strip() in args
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if form == ">=":
        return number >= args[0]
    if form == "<=":
        return number <= args[0]
    if form == ">":
        return number > args[0]
    if form == "<":
        return number < args[0]
    return args[0] <= number <= args[1]
```

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_master_schema -v`
Expected: PASS

- [ ] **Step 6: 全量回归 + 提交**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast
git checkout -- data/snapshot_WL.json 2>/dev/null || true
git add src/master_schema.py tests/test_master_schema.py
git commit -m "feat: add primary key, foreign key, check and contract tier to master schema"
```

Expected: fast 66 项 OK（新增字段全带默认值，既有声明零改动 —— 验收 0.2）

---

### Task 3: 声明 16 张表的主键与外键

**Files:**
- Modify: `src/master_schema.py` 各 `SheetSpec` 声明
- Test: `tests/test_master_schema.py`（追加）

**Interfaces:**
- Consumes: `SheetSpec.primary_key`、`Column.foreign_key`（任务 2）
- Produces: 每张表的 `primary_key` 元组已填；`FOREIGN_KEYS: Dict[str, Tuple[Tuple[str, str, str], ...]]`（`sheet_key → ((column_key, target_sheet, target_column), ...)`）；`validate_references(rows_by_sheet) -> List[str]`

主键取值逐字来自规格 §5.9 登记表。

- [ ] **Step 1: 写失败测试**

```python
class TestNaturalKeys(unittest.TestCase):
    EXPECTED_PK = {
        "demand": ("site", "part_no", "demand_date"),
        "stock": ("site", "part_no"),
        "material_arrival": ("site", "supplier", "part_no", "eta_date"),
        "parameters_fg": ("site", "part_no"),
        "parameters_sfn": ("site", "part_no"),
        "bom": ("site", "fg_part_no", "sf_part_no"),
        "routing": ("site", "part_no", "stage", "primary_line"),
        "setup_matrix": ("site", "line", "from_mold", "to_mold"),
        "inventory_policy": ("site", "part_no", "period_type"),
        "operation_leadtime": ("site", "part_no", "stage"),
        "tooling": ("site", "mold_id"),
        "line_base": ("site", "line"),
        "pm": ("site", "line", "pm_item", "start_date"),
        "customer": ("site", "customer_code"),
        "line_headcount": ("site", "line", "work_date", "shift_no"),
        "part_pairing": ("site", "part_no"),
    }

    def test_every_input_sheet_declares_its_natural_key(self):
        for spec in INPUT_SHEETS:
            with self.subTest(sheet=spec.key):
                self.assertEqual(spec.primary_key, self.EXPECTED_PK[spec.key])

    def test_primary_key_columns_exist_on_the_sheet(self):
        for spec in INPUT_SHEETS:
            keys = {c.key for c in spec.columns}
            for column_key in spec.primary_key:
                self.assertIn(column_key, keys, f"{spec.key}.{column_key}")

    def test_foreign_keys_point_at_real_columns(self):
        for sheet_key, triples in FOREIGN_KEYS.items():
            for column_key, target_sheet, target_column in triples:
                self.assertIn(target_sheet, SHEET_BY_KEY)
                target_keys = {c.key for c in SHEET_BY_KEY[target_sheet].columns}
                self.assertIn(target_column, target_keys,
                              f"{sheet_key}.{column_key} -> {target_sheet}.{target_column}")

    def test_line_base_gains_a_site_column(self):
        # Spec 5.9 fact 1: line_base was the only sheet without `site`, so
        # same-named lines across sites would overwrite each other.
        spec = SHEET_BY_KEY["line_base"]
        self.assertIn("site", {c.key for c in spec.columns})
        site = next(c for c in spec.columns if c.key == "site")
        self.assertFalse(site.required)  # optional: empty means "all sites"

    def test_validate_references_reports_the_eight_known_bom_orphans(self):
        rows = {
            "parameters_sfn": [{"site": "WL", "part_no": "SF1"}],
            "bom": [{"site": "WL", "fg_part_no": "FG1", "sf_part_no": "SF_MISSING",
                     "ratio": 1.0}],
            "parameters_fg": [{"site": "WL", "part_no": "FG1"}],
        }
        problems = validate_references(rows)
        self.assertTrue(any("SF_MISSING" in p for p in problems))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_master_schema.TestNaturalKeys -v`
Expected: FAIL — `ImportError: cannot import name 'FOREIGN_KEYS'`

- [ ] **Step 3: 给每张表声明主键**

在每个 `SheetSpec(...)` 里加 `primary_key=(...)`，取值照 Step 1 的 `EXPECTED_PK`。例：

```python
BOM = SheetSpec(
    key="bom",
    title="产品装配清单 BOM List",
    legacy_titles=("BOM List", "BOM-List"),
    primary_key=("site", "fg_part_no", "sf_part_no"),
    columns=(...),
)
```

`demand` 是日期轴宽表，没有字面的 `demand_date` 列——它由 unpivot 产生（规格 §5.9 事实 2）。声明为 `primary_key=("site", "part_no", "demand_date")` 并加注释说明该列在阶段 4 的 unpivot 里生成，本阶段不参与 Excel 校验。

- [ ] **Step 4: 给 `line_base` 加可选 `site` 列**

```python
LINE_BASE = SheetSpec(
    key="line_base",
    title="产线基础数据",
    legacy_titles=("Line-Base", "LineBase"),
    required=False,
    primary_key=("site", "line"),
    columns=(
        Column("site", "站点", ("Site",),
               note="可选；留空表示全站点通用。2026-11 新增，见规格 5.9 事实 1"),
        Column("line", "产线", ("Line", "Production Line"), required=True),
        Column("trp", "效率 TRP", ("Line TRP", "TRP"), required=True, kind="number",
               check=">0"),
        Column("hours_per_shift", "单班工时 (小时)", ("No. of hours/shift",),
               required=True, kind="number", check=">0"),
        Column("shifts_per_day", "每天班次数", ("No. of shift/day"),
               required=True, kind="int", check="between(1, 3)"),
    ),
    description="2026-10 新增：产线级 TRP / 工时 / 班次，从 FG/SFN 参数表拆出",
)
```

**`site` 必须 `required=False`**：现行模板的 3 行数据没有站点列，设成必填会让既有工作簿校验失败。留空 = 全站点通用，由 loader 解释。

- [ ] **Step 5: 声明外键并实现引用校验**

在 `SHEET_BY_KEY` 之后插入：

```python
def _collect_foreign_keys() -> Dict[str, Tuple[Tuple[str, str, str], ...]]:
    out: Dict[str, Tuple[Tuple[str, str, str], ...]] = {}
    for spec in SHEETS:
        triples = tuple(
            (c.key, c.foreign_key[0], c.foreign_key[1])
            for c in spec.columns if c.foreign_key
        )
        if triples:
            out[spec.key] = triples
    return out


FOREIGN_KEYS: Dict[str, Tuple[Tuple[str, str, str], ...]] = _collect_foreign_keys()


def validate_references(rows_by_sheet: Dict[str, List[dict]]) -> List[str]:
    """Report foreign-key violations as human-readable strings.

    Stage 0 only *reports*.  Stage 4 decides whether the generated DDL
    enforces them -- spec 5.9 fact 3: eight BOM children are missing from
    parameters_sfn today, so ENFORCED from day one would fail to insert.
    """
    problems: List[str] = []
    for sheet_key, triples in FOREIGN_KEYS.items():
        rows = rows_by_sheet.get(sheet_key) or []
        for column_key, target_sheet, target_column in triples:
            target_rows = rows_by_sheet.get(target_sheet) or []
            allowed = {r.get(target_column) for r in target_rows
                       if r.get(target_column) is not None}
            if not allowed:
                continue  # target sheet absent: nothing to check against
            for row in rows:
                value = row.get(column_key)
                if value is not None and value not in allowed:
                    problems.append(
                        f"{sheet_key}.{column_key}={value} 不在 "
                        f"{target_sheet}.{target_column} 中"
                    )
    return problems
```

然后在各表声明里补 `foreign_key=`（照规格 §5.9）：

```python
# material_arrival
Column("part_no", "物料料号 (PN)", ("Part No.", "Part No", "PO Number"),
       required=True, foreign_key=("parameters_sfn", "part_no")),
# bom
Column("fg_part_no", "母件：成品料号 (FG PN)", ("SAP总成料号", "FG Part No"),
       required=True, foreign_key=("parameters_fg", "part_no")),
Column("sf_part_no", "子件：半成品料号 (SF PN)", ("半成品料号", "SF Part No"),
       required=True, foreign_key=("parameters_sfn", "part_no")),
# routing
Column("primary_line", "主候选线", ("Primary Line",), required=True,
       foreign_key=("line_base", "line")),
# setup_matrix
Column("line", "产线", ("Line",), required=True, foreign_key=("line_base", "line")),
# pm
Column("line", "产线", ("Production Line",), required=True,
       foreign_key=("line_base", "line")),
```

- [ ] **Step 6: 跑测试确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_master_schema -v`
Expected: PASS

- [ ] **Step 7: 确认既有工作簿仍能加载**

`line_base` 加了列，必须确认 3 行既有数据仍被正确读取：

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "from src.excel_loader import read_master_rows as r; print(r('APS_DataSource_Template.xlsx')['line_base'])"`
Expected: 3 行，每行 `site` 为 `None`，`trp/hours_per_shift/shifts_per_day` 取值不变（`PGLINKAGE 0.71 8.0 3`、`轴套6 0.85 8.0 3`、`轴套7 0.85 8.0 3`）

- [ ] **Step 8: 提交**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast
git checkout -- data/snapshot_WL.json 2>/dev/null || true
git add src/master_schema.py tests/test_master_schema.py
git commit -m "feat: declare natural keys and foreign keys for the 16 master-data tables"
```

---

### Task 4: 两张新 sheet（`line_headcount` / `part_pairing`）

**Files:**
- Modify: `src/master_schema.py`（新增两个 `SheetSpec` + 追加到 `SHEETS:398-413`）
- Modify: `src/instructions.py`（新表的填写说明行，若该文件按表组织）
- Test: `tests/test_master_schema.py`（追加）

**Interfaces:**
- Consumes: `Column` 的 `foreign_key` / `check` / `contract_tier`（任务 2、3）
- Produces: `SHEET_BY_KEY["line_headcount"]`、`SHEET_BY_KEY["part_pairing"]`；`TEMPLATE_SHEET_COUNT` 由 15 变 **17**

- [ ] **Step 1: 写失败测试**

```python
class TestNewSheets(unittest.TestCase):
    def test_sheet_count_goes_from_15_to_17(self):
        self.assertEqual(TEMPLATE_SHEET_COUNT, 17)

    def test_line_headcount_columns(self):
        spec = SHEET_BY_KEY["line_headcount"]
        self.assertEqual(spec.title, "班次可用人数 Line-Shift-Headcount")
        self.assertFalse(spec.required)   # absent sheet -> DataGap, not a hard fail
        keys = [c.key for c in spec.columns]
        self.assertEqual(keys, ["site", "line", "work_date", "shift_no",
                                "available_headcount", "source"])
        self.assertEqual(spec.primary_key,
                         ("site", "line", "work_date", "shift_no"))

    def test_line_headcount_checks(self):
        cols = {c.key: c for c in SHEET_BY_KEY["line_headcount"].columns}
        self.assertEqual(cols["shift_no"].check, "between(1, 3)")
        self.assertEqual(cols["available_headcount"].check, ">=0")
        self.assertEqual(cols["available_headcount"].contract_tier, "t1_required")
        self.assertEqual(cols["source"].check, "in('actual','simulated')")
        self.assertEqual(cols["line"].foreign_key, ("line_base", "line"))

    def test_part_pairing_columns(self):
        spec = SHEET_BY_KEY["part_pairing"]
        self.assertEqual(spec.title, "左右件配对表 Part-Pairing")
        self.assertFalse(spec.required)
        keys = [c.key for c in spec.columns]
        self.assertEqual(keys, ["site", "part_no", "paired_part_no", "pair_group",
                                "cavity_ratio", "pair_status", "source"])
        self.assertEqual(spec.primary_key, ("site", "part_no"))

    def test_part_pairing_checks(self):
        cols = {c.key: c for c in SHEET_BY_KEY["part_pairing"].columns}
        self.assertEqual(cols["cavity_ratio"].check, ">0")
        self.assertEqual(cols["pair_status"].check, "in('paired','unpaired')")
        self.assertTrue(cols["pair_status"].required)
        self.assertEqual(cols["part_no"].foreign_key, ("parameters_fg", "part_no"))
        self.assertEqual(cols["paired_part_no"].foreign_key,
                         ("parameters_fg", "part_no"))

    def test_new_sheets_are_not_required_for_upload(self):
        # REQUIRED_SHEET_KEYS must stay as it is: a workbook without the two
        # new sheets is still valid, the dependent rules just report gaps.
        self.assertNotIn("line_headcount", REQUIRED_SHEET_KEYS)
        self.assertNotIn("part_pairing", REQUIRED_SHEET_KEYS)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_master_schema.TestNewSheets -v`
Expected: FAIL — `KeyError: 'line_headcount'`

- [ ] **Step 3: 实现两个 `SheetSpec`**

在 `CUSTOMER`（`:379-395`）之后、`SHEETS`（`:398`）之前插入。逐字照抄规格 §4.6.1 / §4.6.2：

```python
LINE_HEADCOUNT = SheetSpec(
    key="line_headcount",
    title="班次可用人数 Line-Shift-Headcount",
    legacy_titles=("Line-Shift-Headcount", "Shift-Headcount"),
    required=False,          # 缺 sheet 时 HC-15/SC-10 出 DataGap，不 fail 整个求解
    primary_key=("site", "line", "work_date", "shift_no"),
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("line", "产线", ("Line",), required=True,
               foreign_key=("line_base", "line")),
        Column("work_date", "日期", ("Date",), required=True, kind="date"),
        Column("shift_no", "班次序号", ("Shift", "班次"), required=True, kind="int",
               check="between(1, 3)"),
        Column("available_headcount", "可用人数", ("Available Headcount",),
               required=True, kind="int", check=">=0", contract_tier="t1_required"),
        Column("source", "数据来源", ("Source",), kind="text",
               check="in('actual','simulated')", note="simulated 行在绑定报告出 simulated 状态"),
    ),
    description="HC-15 班次人数红线 / SC-10 三班人力平滑的唯一数据源",
)

PART_PAIRING = SheetSpec(
    key="part_pairing",
    title="左右件配对表 Part-Pairing",
    legacy_titles=("Part-Pairing", "LR-Pairing"),
    required=False,
    primary_key=("site", "part_no"),
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("part_no", "料号 (PN)", ("PN", "Part No."), required=True,
               foreign_key=("parameters_fg", "part_no")),
        Column("paired_part_no", "配对料号 (PN)", ("Paired PN",),
               foreign_key=("parameters_fg", "part_no")),
        Column("pair_group", "配对组", ("Pair Group",), kind="text",
               note="同组多件成套；留空表示两两配对"),
        Column("cavity_ratio", "腔数比例", ("Cavity Ratio",), kind="number",
               check=">0", note="本件:配对件，1.0 表示等量"),
        Column("pair_status", "配对状态", ("Pair Status",), kind="text",
               check="in('paired','unpaired')", required=True),
        Column("source", "数据来源", ("Source",), kind="text",
               check="in('actual','simulated')"),
    ),
    description="HC-08 左右件同时生产的配对关系；模板原有『同时生产 Y/N』只表达意图，不表达和谁配",
)
```

- [ ] **Step 4: 追加到 `SHEETS` 元组**

`src/master_schema.py:398-413`，在 `CUSTOMER,` 之后加两行：

```python
    LINE_HEADCOUNT,
    PART_PAIRING,
)
```

`TEMPLATE_SHEET_COUNT = len(SHEETS)`（`:435`）自动变 17。**不要改 `REQUIRED_SHEET_KEYS`**（`:423-433`）——新表可选。

- [ ] **Step 5: 更新受影响的既有断言**

`tests/test_master_schema.py` 里任何硬编码 15 的断言改为 17：

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_master_schema -v`
若有失败，逐个看是不是表数断言。**只改数字，不要放宽其他断言。**

- [ ] **Step 6: 确认旧工作簿仍被识别为 v2 布局**

`detect_layout`（`:503-513`）可能按表数判定。跑：

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "import openpyxl; from src.master_schema import detect_layout; print(detect_layout(openpyxl.load_workbook('APS_DataSource_Template.xlsx')))"`
Expected: `v2`（若变成别的值，说明 `detect_layout` 依赖表数，必须改成按表名判定后再继续）

- [ ] **Step 7: 全量回归 + 提交**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast
git checkout -- data/snapshot_WL.json 2>/dev/null || true
git add src/master_schema.py tests/test_master_schema.py
git commit -m "feat: add line_headcount and part_pairing sheets (spec 4.6.1/4.6.2)"
```

Expected: fast 全绿；验收 0.2 达成

---

### Task 5: 消灭硬编码年份 2026（11 处）

**Files:**
- Create: `src/planning_calendar.py`
- Modify: `src/excel_loader.py:511`、`src/data_loader.py:259,455,462`、`src/exporter.py:493`、`src/main.py:42`、`src/rule_sets/cli.py:35`、`src/scheduler.py:312`、`src/server.py:413,1163,1170`
- Test: `tests/test_planning_calendar.py`
- Modify: `run_tests.py:32-40`

**Interfaces:**
- Consumes: 无
- Produces:
  - `DEFAULT_PLANNING_YEAR: int = 2026`
  - `DEFAULT_START_DATETIME: datetime`
  - `due_datetime(month: int, day: int, due_hour: int, year: int = DEFAULT_PLANNING_YEAR) -> datetime`
  - `start_datetime(year: int = DEFAULT_PLANNING_YEAR) -> datetime`
  - `parse_month_day(text: str) -> Optional[Tuple[int, int]]`

**为什么单点声明而不是彻底参数化到底：** 目标是把 11 处散落的字面量收敛到 1 处，让「改年份」成为一次编辑而不是全仓搜索。把 `year` 一路穿到每个调用点属于阶段 4 的 Repository 工作（那时年度来自快照上下文），本阶段不做——YAGNI。

- [ ] **Step 1: 写失败测试**

`tests/test_planning_calendar.py`：

```python
from __future__ import annotations

import unittest
from datetime import datetime

from src.planning_calendar import (
    DEFAULT_PLANNING_YEAR,
    DEFAULT_START_DATETIME,
    due_datetime,
    parse_month_day,
    start_datetime,
)


class TestPlanningCalendar(unittest.TestCase):
    def test_year_is_declared_exactly_once(self):
        self.assertEqual(DEFAULT_PLANNING_YEAR, 2026)
        self.assertEqual(DEFAULT_START_DATETIME, datetime(2026, 10, 12, 7, 0, 0))

    def test_due_datetime_takes_an_explicit_year(self):
        self.assertEqual(due_datetime(10, 20, 15), datetime(2026, 10, 20, 15, 0))
        self.assertEqual(due_datetime(10, 20, 15, year=2027),
                         datetime(2027, 10, 20, 15, 0))

    def test_start_datetime_takes_an_explicit_year(self):
        self.assertEqual(start_datetime(2027), datetime(2027, 10, 12, 7, 0, 0))

    def test_parse_month_day_accepts_the_slash_form(self):
        self.assertEqual(parse_month_day("10/20"), (10, 20))
        self.assertEqual(parse_month_day("1/5"), (1, 5))

    def test_parse_month_day_returns_none_for_junk(self):
        for junk in ("", None, "10-20", "abc", "10/", "99/99"):
            self.assertIsNone(parse_month_day(junk), junk)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_planning_calendar -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.planning_calendar'`

- [ ] **Step 3: 实现 `src/planning_calendar.py`**

```python
"""Single declaration site for the planning year and horizon start.

Eleven call sites used to spell ``datetime(2026, ...)`` inline (spec defect
M6, plan deviation 3).  They all route through here now, so changing the
planning year is one edit instead of a repo-wide search.

Threading an explicit ``year`` through every caller is stage 4 work: that is
when the year starts coming from the snapshot context rather than a constant.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

DEFAULT_PLANNING_YEAR = 2026

#: Horizon start used by main.py, rule_sets/cli.py and the scheduler default.
_START_MONTH, _START_DAY, _START_HOUR = 10, 12, 7

DEFAULT_START_DATETIME = datetime(DEFAULT_PLANNING_YEAR, _START_MONTH, _START_DAY,
                                  _START_HOUR, 0, 0)


def start_datetime(year: int = DEFAULT_PLANNING_YEAR) -> datetime:
    return datetime(year, _START_MONTH, _START_DAY, _START_HOUR, 0, 0)


def due_datetime(month: int, day: int, due_hour: int,
                 year: int = DEFAULT_PLANNING_YEAR) -> datetime:
    return datetime(year, month, day, due_hour, 0)


def parse_month_day(text: object) -> Optional[Tuple[int, int]]:
    """Parse the ``"M/D"`` form used by the demand date axis.

    Returns ``None`` for anything unparseable -- callers must handle that
    explicitly instead of falling back to a hardcoded date.
    """
    if text is None:
        return None
    parts = str(text).strip().split("/")
    if len(parts) != 2:
        return None
    try:
        month, day = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return month, day
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_planning_calendar -v`
Expected: PASS（5 项）

- [ ] **Step 5: 逐个替换 11 处调用点**

| 文件:行 | 现状 | 改成 |
|---|---|---|
| `main.py:42` | `START_DT = datetime(2026, 10, 12, 7, 0, 0)` | `START_DT = DEFAULT_START_DATETIME` |
| `rule_sets/cli.py:35` | 同上 | 同上 |
| `scheduler.py:312` | `start_datetime: datetime = datetime(2026, 10, 12, 7, 0, 0)` | `start_datetime: datetime = DEFAULT_START_DATETIME` |
| `server.py:413` | `start_day = datetime(2026, 10, 12)` | `start_day = DEFAULT_START_DATETIME.date()` |
| `exporter.py:493` | `return datetime(2026, month, day)` | `return due_datetime(month, day, 0)` |
| `excel_loader.py:511` | `due_datetime=datetime(2026, month, day, due_hour, 0)` | `due_datetime=due_datetime(month, day, due_hour)` |
| `data_loader.py:462` | `dt = datetime(2026, month, day, due_hour, 0)` | `dt = due_datetime(month, day, due_hour)` |
| `data_loader.py:259` | `dt = datetime(2026, 10, 20, 15, 0)` | `dt = due_datetime(10, 20, 15)` |
| `server.py:1163` | `o.due_datetime = datetime(2026, month, day, 15, 0)` | `o.due_datetime = due_datetime(month, day, 15)` |
| `server.py:1170` | `dt = datetime(2026, month, day, 15, 0)` | `dt = due_datetime(month, day, 15)` |
| `server.py:1172` | `dt = datetime(2026, 10, 20, 15, 0)` | `dt = due_datetime(10, 20, 15)` |

**注意 `excel_loader.py:511` 的命名冲突**：局部变量名可能也叫 `due_datetime`。若冲突，导入时用别名：`from src.planning_calendar import due_datetime as _due_dt`。

同时把 `server.py:1160-1172` 与 `excel_loader.py:505-515` 里 `month, day = [int(p) for p in item.due_date.split("/")]` 这种手工解析换成 `parse_month_day(...)`，并**删掉 `except Exception: pass`**——解析失败必须产 `DataGap`（任务 9 处理），不能静默。

- [ ] **Step 6: 验证归零（验收 0.7）**

Run: `grep -rn "datetime(2026" src/`
Expected: **无输出**（`src/planning_calendar.py` 里是 `datetime(DEFAULT_PLANNING_YEAR, ...)` 与 `datetime(year, ...)`，不匹配该 pattern）

Run: `grep -rn "datetime(DEFAULT_PLANNING_YEAR\|datetime(year" src/`
Expected: 只在 `src/planning_calendar.py` 出现

- [ ] **Step 7: 加进 fast 列表 + 全量回归 + 提交**

`run_tests.py:32-40` 追加 `"tests.test_planning_calendar"`。

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py
git checkout -- data/snapshot_WL.json 2>/dev/null || true
git add src/planning_calendar.py tests/test_planning_calendar.py run_tests.py \
        src/main.py src/exporter.py src/excel_loader.py src/data_loader.py \
        src/scheduler.py src/server.py src/rule_sets/cli.py
git commit -m "refactor: route all 11 hardcoded datetime(2026,...) sites through planning_calendar"
```

Expected: 全量测试绿；黄金基线**不应变化**（纯等价替换）。若基线变了，停下报告——说明某处替换不等价。


---

### Task 6: 种子脚本 `scripts/seed_simulated_masterdata.py`

**Files:**
- Create: `scripts/seed_simulated_masterdata.py`
- Test: `tests/test_seed_masterdata.py`
- Modify: `run_tests.py:32-40`

**Interfaces:**
- Consumes: `SHEET_BY_KEY["line_headcount"]` / `["part_pairing"]`（任务 4）、`read_master_rows`（`excel_loader.py:645`）、`DEFAULT_PLANNING_YEAR` / `parse_month_day`（任务 5）、`_demand_header_row` / `_demand_dates`（`excel_loader.py:191,200`）
- Produces:
  - `build_headcount_rows(master_rows: Dict[str, List[dict]], site: str, dates: Sequence[str]) -> List[dict]`
  - `build_pairing_rows(fg_rows: Sequence[dict], site: str) -> List[dict]`
  - `SHIFT_FACTORS: Dict[int, float] = {1: 1.0, 2: 1.0, 3: 0.8}`
  - CLI：`--check`（只报告）/ `--force`（落盘）/ `--template PATH` / `--site SITE`

派生规则逐字来自规格 §4.6.1 / §4.6.2。**确定性、不读时钟、不用随机数。**

**⚠️ 日期轴不能用 `read_master_rows(...)["demand"]` 的键取。** `read_master_rows`（`excel_loader.py:645`）只遍历 `spec.columns`，**不处理 `date_axis=True`**，所以 demand 行的键只有 `site` / `part_no` / `description`——日期列全被丢掉。实测：

```
$ .venv/Scripts/python.exe -c "from src.excel_loader import read_master_rows as r; print(sorted(r('APS_DataSource_Template.xlsx')['demand'][0]))"
['description', 'part_no', 'site']
```

必须走 `_demand_header_row` + `_demand_dates`，这也是 `load_workbook_snapshot`（`:490`）自己用的那条路，所以种子写出的日期与求解器看到的日期同源。`_demand_dates` 返回**列序**，直接沿用，**不要 `sorted()`**（`"M/D"` 字符串跨月排序是错的：`"11/1" < "10/13"`）。

**行数是 297，不是 90。** 模板 demand 表头声明了 **33** 个日期列（`10/13`–`11/15`，其中 25 列有量），× 3 条产线 × 3 班次 = 297。规格 §4.6.1 rev.2 写的「10 天 → 90 行」是把黄金基线的 10 天**求解窗口**误当成了模板的**数据轴**，已在 rev.3 更正。人数可用性与当天有没有订单无关，覆盖全轴才不会在计划员补一个空日期时立刻缺行。

- [ ] **Step 1: 写失败测试**

`tests/test_seed_masterdata.py`：

```python
from __future__ import annotations

import unittest
from datetime import date, timedelta
from pathlib import Path

from scripts.seed_simulated_masterdata import (
    SHIFT_FACTORS,
    build_headcount_rows,
    build_pairing_rows,
)

TEMPLATE = Path(__file__).resolve().parents[1] / "APS_DataSource_Template.xlsx"

LINE_BASE = [
    {"site": None, "line": "PGLINKAGE", "trp": 0.71,
     "hours_per_shift": 8.0, "shifts_per_day": 3},
    {"site": None, "line": "轴套6", "trp": 0.85,
     "hours_per_shift": 8.0, "shifts_per_day": 3},
    {"site": None, "line": "轴套7", "trp": 0.85,
     "hours_per_shift": 8.0, "shifts_per_day": 3},
]

ROUTING = [
    {"part_no": "P1", "stage": "FG", "primary_line": "PGLINKAGE", "headcount": 3.0},
    {"part_no": "P2", "stage": "SF", "primary_line": "轴套6", "headcount": 2.0},
]

DATES = [d.isoformat() for d in
         (date(2026, 10, 13) + timedelta(days=n) for n in range(33))]   # the real template axis

FG = [
    # the real 17 Parameters-FG rows of APS_DataSource_Template.xlsx, verbatim.
    # A fixture copied from the template makes this test a regression on the
    # actual pairing rule rather than on an invented subset.
    {"part_no": "WA14208", "description": "DX11电机连杆总成", "simultaneous_production": None},
    {"part_no": "WAB6404", "description": "E371 A1 传动杆总成", "simultaneous_production": None},
    {"part_no": "WB35356", "description": "930B传动杆总成", "simultaneous_production": "Y"},
    {"part_no": "WB80510", "description": "CX11 LHD传动杆总成（新）", "simultaneous_production": "Y"},
    {"part_no": "WC07674", "description": "CM2E 电机及连杆机构", "simultaneous_production": None},
    {"part_no": "WD48696", "description": "L743 传动杆总成", "simultaneous_production": "Y"},
    {"part_no": "WD59238", "description": "E371传动杆总成", "simultaneous_production": "Y"},
    {"part_no": "W144813S", "description": "Geely DCY11前雨刮电机传动杆总成 (备件)",
     "simultaneous_production": None},
    {"part_no": "W000076959VA", "description": "领克 01&03 前雨刮电机传动杆",
     "simultaneous_production": None},
    {"part_no": "W243255S", "description": "宝马欧洲G38前雨刮电机传动杆成品 (备件)",
     "simultaneous_production": None},
    {"part_no": "WA14208S", "description": "吉利DX11左驾前雨刮电机传动杆 (备件)",
     "simultaneous_production": None},
    {"part_no": "WD48696S", "description": "领克L743左驾前雨刮电机传动杆总成 (备件)",
     "simultaneous_production": None},
    {"part_no": "WBC9317", "description": "LEAP CU1207&1307 传动杆总成",
     "simultaneous_production": None},
    {"part_no": "WBC9317C", "description": "LEAP CU1207&1307 传动杆总成-三厂",
     "simultaneous_production": None},
    {"part_no": "WCF8126", "description": "DX11电机连杆总成(新）", "simultaneous_production": None},
    {"part_no": "WCB8574", "description": "Chery KP31 RHD 传动杆", "simultaneous_production": None},
    {"part_no": "WCA1894", "description": "Chery KP31 LHD 传动杆", "simultaneous_production": None},
]


class TestHeadcountSeed(unittest.TestCase):
    def rows(self):
        return build_headcount_rows(
            {"line_base": LINE_BASE, "routing": ROUTING}, "WL", DATES)

    def test_row_count_is_lines_times_dates_times_shifts(self):
        self.assertEqual(len(DATES), 33)               # the real template date axis
        self.assertEqual(len(self.rows()), 3 * 33 * 3)  # 297, spec 4.6.1 (rev.3)

    def test_deterministic(self):
        self.assertEqual(self.rows(), self.rows())

    def test_derives_from_routing_headcount_not_invented(self):
        rows = {(r["line"], r["shift_no"]): r["available_headcount"]
                for r in self.rows() if r["work_date"] == DATES[0]}
        # PGLINKAGE routing headcount = 3; factors 1.0 / 1.0 / 0.8 -> 3, 3, 2
        self.assertEqual(rows[("PGLINKAGE", 1)], 3)
        self.assertEqual(rows[("PGLINKAGE", 2)], 3)
        self.assertEqual(rows[("PGLINKAGE", 3)], 2)
        # 轴套6 routing headcount = 2 -> 2, 2, 1
        self.assertEqual(rows[("轴套6", 1)], 2)
        self.assertEqual(rows[("轴套6", 3)], 1)

    def test_line_without_routing_row_falls_back_to_minimum_one(self):
        rows = {(r["line"], r["shift_no"]): r["available_headcount"]
                for r in self.rows() if r["work_date"] == DATES[0]}
        self.assertEqual(rows[("轴套7", 1)], 1)   # no routing row -> floor at 1

    def test_every_row_is_marked_simulated(self):
        self.assertTrue(all(r["source"] == "simulated" for r in self.rows()))
        self.assertTrue(all(r["site"] == "WL" for r in self.rows()))

    def test_shift_factors_are_the_specified_three(self):
        self.assertEqual(SHIFT_FACTORS, {1: 1.0, 2: 1.0, 3: 0.8})


class TestPairingSeed(unittest.TestCase):
    def rows(self):
        return build_pairing_rows(FG, "WL")

    def test_row_population_is_five(self):
        # Spec 4.6.2 + acceptance 0.5: exactly 1 paired and 4 unpaired.
        self.assertEqual(len(self.rows()), 5)
        self.assertEqual(sum(1 for r in self.rows() if r["pair_status"] == "paired"), 1)
        self.assertEqual(sum(1 for r in self.rows() if r["pair_status"] == "unpaired"), 4)

    def test_only_the_defensible_pair_is_paired(self):
        paired = [r for r in self.rows() if r["pair_status"] == "paired"]
        self.assertEqual(paired[0]["part_no"], "WCA1894")
        self.assertEqual(paired[0]["paired_part_no"], "WCB8574")
        self.assertEqual(paired[0]["cavity_ratio"], 1.0)

    def test_the_four_simultaneous_production_parts_are_unpaired(self):
        by_part = {r["part_no"]: r for r in self.rows()}
        # WB80510 is "CX11 LHD" but no CX11 RHD exists in FG params; the other
        # three carry no L/R keyword at all.  Spec 4.6.2: record unpaired,
        # never fabricate a partner.
        for part_no in ("WB35356", "WB80510", "WD48696", "WD59238"):
            self.assertEqual(by_part[part_no]["pair_status"], "unpaired")
            self.assertIsNone(by_part[part_no]["paired_part_no"])
            self.assertIsNone(by_part[part_no]["cavity_ratio"])

    def test_spare_parts_with_an_lr_keyword_but_no_intent_get_no_row(self):
        # WA14208S "吉利DX11左驾..." and WD48696S "领克L743左驾..." are 备件 with
        # no discoverable R partner and no 同时生产=Y flag.  HC-08 can never fire
        # on them, so they carry no information and get no row.
        parts = {r["part_no"] for r in self.rows()}
        self.assertNotIn("WA14208S", parts)
        self.assertNotIn("WD48696S", parts)

    def test_parts_without_any_lr_keyword_or_flag_are_absent(self):
        parts = {r["part_no"] for r in self.rows()}
        self.assertNotIn("WA14208", parts)   # "DX11电机连杆总成"
        self.assertNotIn("WBC9317", parts)   # "LEAP CU1207&1307 传动杆总成"
        self.assertNotIn("WCB8574", parts)   # consumed as WCA1894's partner

    def test_deterministic_and_marked_simulated(self):
        self.assertEqual(self.rows(), self.rows())
        self.assertTrue(all(r["source"] == "simulated" for r in self.rows()))
        self.assertTrue(all(r["site"] == "WL" for r in self.rows()))


class TestPlanningDatesFromTemplate(unittest.TestCase):
    """The date axis must come off the worksheet, not read_master_rows."""

    def test_reads_the_declared_axis_in_column_order(self):
        from scripts.seed_simulated_masterdata import _planning_dates
        dates = _planning_dates(str(TEMPLATE))
        self.assertEqual(len(dates), 33)              # 10/13 .. 11/15
        self.assertEqual(dates[0], "2026-10-13")
        self.assertEqual(dates[-1], "2026-11-15")
        self.assertEqual(len(set(dates)), 33)         # no duplicates

    def test_column_order_is_not_lexicographic(self):
        # Guards against a future refactor reaching for sorted() on the raw
        # "M/D" labels: the axis crosses a month boundary, and "11/10" sorts
        # before "11/2" as a string.
        import openpyxl
        from src.excel_loader import _demand_dates, _demand_header_row
        from src.master_schema import SHEET_BY_KEY, resolve_sheet
        wb = openpyxl.load_workbook(str(TEMPLATE), data_only=True)
        try:
            ws = resolve_sheet(wb, SHEET_BY_KEY["demand"])
            labels = [label for _col, label
                      in _demand_dates(ws, _demand_header_row(ws))]
        finally:
            wb.close()
        self.assertNotEqual(labels, sorted(labels))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_seed_masterdata -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.seed_simulated_masterdata'`

若报的是 `No module named 'scripts'`，在 `scripts/` 下建空 `__init__.py`。

- [ ] **Step 3: 实现种子脚本**

`scripts/seed_simulated_masterdata.py`：

```python
"""Seed the two D15 sheets with explicit, derived, provenance-tagged data.

Spec: Specification/aps_rule_set_redesign.md sections 4.6.1, 4.6.2 and 5.12.

Hard requirements (spec 5.12):
  1. refuses to write without --force
  2. deterministic -- no clock, no randomness, no dict-iteration dependence
  3. only ever writes rows with source='simulated'
  4. derives every value from an existing column; never invents a partner
  5. re-runs verify_master_template.py afterwards

Reference implementation for the xlsx write mechanics (style preservation,
--check/--force convention): git show 3b47ceb:scripts/fill_simulated_masterdata.py

Usage:
    .venv/Scripts/python.exe scripts/seed_simulated_masterdata.py --check
    .venv/Scripts/python.exe scripts/seed_simulated_masterdata.py --force
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import openpyxl

from src.excel_loader import _demand_dates, _demand_header_row, read_master_rows
from src.master_schema import SHEET_BY_KEY, resolve_sheet
from src.planning_calendar import DEFAULT_PLANNING_YEAR, parse_month_day

DEFAULT_TEMPLATE = BASE_DIR / "APS_DataSource_Template.xlsx"
DEFAULT_SITE = "WL"

#: Spec 4.6.1: available = floor(routing_headcount * factor), minimum 1.
SHIFT_FACTORS: Dict[int, float] = {1: 1.0, 2: 1.0, 3: 0.8}

#: Spec 4.6.2: the only pairing evidence the descriptions carry.
_LR_KEYWORDS = ("LHD", "RHD", "左驾", "右驾")
_MODEL_RE = re.compile(r"([A-Z]{1,3}[- ]?\d+[A-Z]?)")


def _routing_headcount_by_line(routing_rows: Sequence[dict]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for row in routing_rows:
        line = row.get("primary_line")
        headcount = row.get("headcount")
        if line and headcount is not None:
            out.setdefault(str(line), float(headcount))
    return out


def build_headcount_rows(master_rows: Dict[str, List[dict]], site: str,
                         dates: Sequence[str]) -> List[dict]:
    """line_base x dates x 1..shifts_per_day -> available headcount rows."""
    headcount_by_line = _routing_headcount_by_line(master_rows.get("routing") or [])
    rows: List[dict] = []
    for line_row in sorted(master_rows.get("line_base") or [],
                           key=lambda r: str(r.get("line"))):
        line = str(line_row.get("line") or "")
        if not line:
            continue
        shifts_per_day = int(line_row.get("shifts_per_day") or 0)
        base = headcount_by_line.get(line)
        for work_date in dates:
            for shift_no in range(1, min(shifts_per_day, 3) + 1):
                if base is None:
                    available = 1
                else:
                    available = max(1, int(math.floor(base * SHIFT_FACTORS[shift_no])))
                rows.append({
                    "site": site,
                    "line": line,
                    "work_date": work_date,
                    "shift_no": shift_no,
                    "available_headcount": available,
                    "source": "simulated",
                })
    return rows


def _lr_side(description: str) -> Optional[str]:
    text = (description or "").upper()
    if "LHD" in text or "左驾" in (description or ""):
        return "L"
    if "RHD" in text or "右驾" in (description or ""):
        return "R"
    return None


def _model_token(description: str) -> str:
    match = _MODEL_RE.search((description or "").upper())
    return match.group(1).replace(" ", "").replace("-", "") if match else ""


def _simultaneous_flagged(row: dict) -> bool:
    return str(row.get("simultaneous_production") or "").strip().upper() \
        in ("Y", "YES", "TRUE", "是")


def build_pairing_rows(fg_rows: Sequence[dict], site: str) -> List[dict]:
    """One row per part HC-08 could ever fire on; nothing invented.

    A part gets a row iff it actually pairs with another part by
    (model token, L/R side), **or** the business flagged it 同时生产=Y.
    Spare parts whose description says 左驾 but which have neither a
    discoverable partner nor the flag carry no information for HC-08, so
    they get no row -- that is why WA14208S and WD48696S are absent while
    WB35356 / WD48696 / WD59238 are present as ``unpaired``.

    Spec 4.6.2 states both the keyword rule and "the four 同时生产=Y parts
    must land unpaired" without saying how the two populations combine;
    this is the only reading that satisfies acceptance 0.5 (1 paired +
    4 unpaired).  Recorded as plan deviation 5.
    """
    ordered = sorted(fg_rows, key=lambda r: str(r.get("part_no")))
    by_model_side: Dict[tuple, dict] = {}
    for row in ordered:
        description = str(row.get("description") or "")
        side = _lr_side(description)
        model = _model_token(description)
        if side and model:
            by_model_side.setdefault((model, side), row)

    rows: List[dict] = []
    emitted: set = set()
    for row in ordered:
        part_no = str(row.get("part_no") or "")
        description = str(row.get("description") or "")
        side = _lr_side(description)
        model = _model_token(description)
        partner = by_model_side.get((model, "R" if side == "L" else "L")) \
            if (side and model) else None
        pairs = partner is not None and str(partner.get("part_no")) != part_no
        if not pairs and not _simultaneous_flagged(row):
            continue
        if part_no in emitted:
            continue
        if pairs:
            rows.append({
                "site": site, "part_no": part_no,
                "paired_part_no": str(partner.get("part_no")),
                "pair_group": None, "cavity_ratio": 1.0,
                "pair_status": "paired", "source": "simulated",
            })
            emitted.add(str(partner.get("part_no")))
        else:
            rows.append({
                "site": site, "part_no": part_no,
                "paired_part_no": None,
                "pair_group": None, "cavity_ratio": None,
                "pair_status": "unpaired", "source": "simulated",
            })
        emitted.add(part_no)
    return rows
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_seed_masterdata -v`
Expected: PASS（14 项）

若 `test_only_the_defensible_pair_is_paired` 失败，**先打印 `build_pairing_rows` 的实际输出再调**——不要为了让测试过而放宽断言。规格 §4.6.2 明确只有 `WCA1894 ↔ WCB8574` 一对可辩护。

- [ ] **Step 5: 实现写盘与 CLI**

追加到同一文件：

```python
def _planning_dates(template_path: str) -> List[str]:
    """Demand date axis as ISO strings, in worksheet column order.

    ``read_master_rows`` iterates ``spec.columns`` only and therefore drops
    every ``date_axis=True`` column, so the axis has to come off the
    worksheet -- via the same two helpers ``load_workbook_snapshot`` uses.
    Column order is already chronological; sorting ``"M/D"`` strings would
    not be (``"11/1" < "10/13"``).
    """
    wb = openpyxl.load_workbook(template_path, data_only=True)
    try:
        ws = resolve_sheet(wb, SHEET_BY_KEY["demand"])
        if ws is None:
            return []
        header_row = _demand_header_row(ws)
        if header_row is None:
            return []
        out: List[str] = []
        for _col, label in _demand_dates(ws, header_row):
            parsed = parse_month_day(label)
            if parsed is None:
                continue
            iso = date(DEFAULT_PLANNING_YEAR, parsed[0], parsed[1]).isoformat()
            if iso not in out:
                out.append(iso)
        return out
    finally:
        wb.close()


def _write_sheet(wb, sheet_key: str, rows: List[dict]) -> int:
    spec = SHEET_BY_KEY[sheet_key]
    ws = resolve_sheet(wb, spec)
    if ws is None:
        ws = wb.create_sheet(spec.title)
        ws.append([c.header for c in spec.active_columns()])
    columns = list(spec.active_columns())
    existing = ws.max_row
    for row in rows:
        ws.append([row.get(c.key) for c in columns])
    return ws.max_row - existing


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--site", default=DEFAULT_SITE)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true",
                       help="report what would be written; do not touch the file")
    group.add_argument("--force", action="store_true", help="actually write")
    args = parser.parse_args(argv)

    if not args.force:
        # Spec 5.12 requirement 1: refuse by default, like the deleted
        # fill_simulated_masterdata.py did.
        print("refusing to write without --force (spec 5.12); showing the plan:")

    master_rows = read_master_rows(args.template)
    dates = _planning_dates(args.template)
    if not dates:
        print("no demand date axis found; nothing to seed")
        return 1
    headcount = build_headcount_rows(master_rows, args.site, dates)
    pairing = build_pairing_rows(master_rows.get("parameters_fg") or [], args.site)

    print(f"line_headcount: {len(headcount)} rows "
          f"({len(dates)} dates, source=simulated)")
    for row in headcount[:6]:
        print("   ", row)
    print(f"part_pairing:   {len(pairing)} rows "
          f"({sum(1 for r in pairing if r['pair_status'] == 'paired')} paired, "
          f"{sum(1 for r in pairing if r['pair_status'] == 'unpaired')} unpaired)")
    for row in pairing:
        print("   ", row)

    if not args.force:
        return 0

    wb = openpyxl.load_workbook(args.template)
    try:
        _write_sheet(wb, "line_headcount", headcount)
        _write_sheet(wb, "part_pairing", pairing)
        wb.save(args.template)
    finally:
        wb.close()
    print(f"written to {args.template}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: 验证拒绝语义（验收 0.4）**

```bash
md5sum APS_DataSource_Template.xlsx
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/seed_simulated_masterdata.py --check
md5sum APS_DataSource_Template.xlsx
git status --short
```
Expected: 两次 md5 相同；`git status` 无输出

- [ ] **Step 7: 验证确定性与行数（验收 0.3、0.5）**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/seed_simulated_masterdata.py --force
md5sum APS_DataSource_Template.xlsx
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "from src.excel_loader import read_master_rows as r; d=r('APS_DataSource_Template.xlsx'); print('headcount', len(d['line_headcount']), 'pairing', len(d['part_pairing']))"
```
Expected: `headcount 297`；`pairing` 中 paired 1 行、unpaired 4 行（共 5 行）

再跑一次 `--force` **前先 `git checkout -- APS_DataSource_Template.xlsx`**，然后重复上面两步，确认 md5 与第一次一致（确定性）。

**注意**：`--force` 会追加行，重复执行不幂等。这是有意的（与既有模板改写脚本一致），但意味着测试里不能反复 `--force`。若要让它幂等，先删掉该 sheet 里 `source='simulated'` 的既有行再写——**本任务不实现幂等，留作已知限制记录在提交信息里**。

- [ ] **Step 8: 重跑模板对账（规格 5.12 要求 5）**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verify_master_template.py
```
Expected: 生成 `Specification/master_template_verification.md`，新增两张表出现在对账结果里且无 P1 级问题。在 `Specification/masterdata_simulation_notes.md` 追加一条记录（日期、两张表、行数、派生规则）。

- [ ] **Step 9: 加进 fast 列表 + 回归 + 提交**

`run_tests.py:32-40` 追加 `"tests.test_seed_masterdata"`。

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast
git checkout -- data/snapshot_WL.json 2>/dev/null || true
git add scripts/seed_simulated_masterdata.py tests/test_seed_masterdata.py run_tests.py \
        APS_DataSource_Template.xlsx Specification/master_template_verification.md \
        Specification/masterdata_simulation_notes.md
git commit -m "feat: seed simulated line_headcount and part_pairing data (D15)

297 headcount rows (3 lines x the 33-date demand axis x 3 shifts) derived
from Routing headcount x shift factors; the axis comes off the worksheet
because read_master_rows drops date_axis columns;
1 defensible L/R pair (WCA1894/WCB8574) plus 4 unpaired LHD parts whose
counterparts do not exist in FG params -- recorded unpaired, not invented.

Known limitation: --force appends, so it is not idempotent."
```

---

### Task 7: 拆地雷 2（区域 A）— `line_base` 与 `tooling`

**Files:**
- Modify: `src/data_loader.py:51-58`（`LineBase`）、`:72-83`（`ToolingMold`）
- Modify: `src/excel_loader.py:290-315`（line_base 块）、`:317-345`（tooling 块）
- Test: `tests/test_loader_gaps.py`（新建）
- Modify: `run_tests.py:32-40`

**Interfaces:**
- Consumes: `DataGap` / `GapCollector`（任务 1）
- Produces:
  - `LineBase` 的 `trp` / `hours_per_shift` / `shifts_per_day` **不再有默认值**（必填位置参数）
  - `ToolingMold.available_qty: int = 1` 保留（规格 §4.5：默认 1 是显式业务裁决）；`changeover_time_min: float = 0.0`；`max_life_strokes` / `current_strokes` / `maintenance_cycle_strokes: int = 0`；`status: str = ""`
  - `load_workbook_snapshot` 内可用的闭包助手 `_gap(sheet_key, column_key, index, reason) -> None`

**分类原则（本任务与任务 8、9 共用，逐条判定，不要凭感觉）：**

| 类 | 判据 | 处置 |
|---|---|---|
| A | 列是 `deprecated=True` | 中性零值（`0` / `0.0` / `""`），**不产 gap**——该列不属于新框架 |
| B | T1 必填列（`required=True` 且 `contract_tier='t1_required'`）缺失 | 产 gap + warn，**跳过整行**（该行无法构成合法记录） |
| C | 非必填但参与求解的列缺失 | 产 gap + warn，字段取「不会暗中帮忙」的中性值 |
| D | 规格已裁决的显式业务默认值 | 保留，并在代码注释里指向规格条款 |

`available_qty` 属 D（§4.5）。`changeover_time_min` 属 C，中性值 `0.0`——**注意这正是缺陷 B3 的取值**。阶段 0 故意让它可见：gap + 警告文案明说「按 0 计，待 §4.4 优先级表在阶段 2 裁决」。这不是终态，是把静默的编造变成有声的欠账。

- [ ] **Step 1: 写失败测试**

`tests/test_loader_gaps.py`：

```python
from __future__ import annotations

import unittest

from src.data_gaps import DataGap
from src.data_loader import LineBase, ToolingMold


class TestDataclassDefaultsAreNotFabricated(unittest.TestCase):
    def test_line_base_requires_its_three_t1_fields(self):
        with self.assertRaises(TypeError):
            LineBase(line="L1")                       # no silent 0.71 / 8.0 / 3
        entry = LineBase(line="L1", trp=0.71, hours_per_shift=8.0, shifts_per_day=3)
        self.assertEqual(entry.trp, 0.71)

    def test_tooling_changeover_default_is_zero_not_thirty(self):
        mold = ToolingMold(site="WL", mold_id="M1", mold_name="n",
                           applicable_lines=["L1"], applicable_parts=["P1"])
        self.assertEqual(mold.changeover_time_min, 0.0)

    def test_tooling_available_qty_keeps_the_documented_business_default(self):
        # Spec 4.5: default 1 is an explicit business ruling, not a fabrication.
        mold = ToolingMold(site="WL", mold_id="M1", mold_name="n",
                           applicable_lines=["L1"], applicable_parts=["P1"])
        self.assertEqual(mold.available_qty, 1)

    def test_tooling_deprecated_fields_are_neutral_zero(self):
        mold = ToolingMold(site="WL", mold_id="M1", mold_name="n",
                           applicable_lines=["L1"], applicable_parts=["P1"])
        self.assertEqual(mold.max_life_strokes, 0)
        self.assertEqual(mold.maintenance_cycle_strokes, 0)
        self.assertEqual(mold.status, "")


class TestLoaderEmitsGaps(unittest.TestCase):
    """End-to-end: a workbook with holes must produce gaps, not guesses."""

    def _load(self, tmp_path, rows_line_base, rows_tooling):
        import openpyxl
        from src.excel_loader import load_workbook_snapshot
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        # minimal v2 workbook -- see tests/test_master_schema for the layout helper
        from tests.workbook_builder import build_minimal_workbook
        path = build_minimal_workbook(tmp_path, line_base=rows_line_base,
                                      tooling=rows_tooling)
        return load_workbook_snapshot(str(path), "WL")

    def test_missing_trp_produces_a_gap_and_drops_the_line(self):
        snap = self._load(
            "line_base_missing_trp",
            rows_line_base=[{"site": "WL", "line": "L1", "trp": None,
                             "hours_per_shift": 8.0, "shifts_per_day": 3}],
            rows_tooling=[])
        gaps = [g for g in snap.gaps if g.column_key == "trp"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].sheet_key, "line_base")
        self.assertEqual(gaps[0].index, ("WL", "L1"))
        self.assertNotIn("L1", {lb.line for lb in snap.line_base})
        self.assertTrue(any("L1" in w and "TRP" in w for w in snap.warnings))

    def test_missing_changeover_produces_a_gap_and_keeps_the_mold_at_zero(self):
        snap = self._load(
            "tooling_missing_changeover",
            rows_line_base=[{"site": "WL", "line": "L1", "trp": 0.71,
                             "hours_per_shift": 8.0, "shifts_per_day": 3}],
            rows_tooling=[{"site": "WL", "mold_id": "M1", "mold_name": "n",
                           "applicable_lines": "L1", "applicable_parts": "P1",
                           "available_qty": 1, "changeover_time_min": None}])
        gaps = [g for g in snap.gaps if g.column_key == "changeover_time_min"]
        self.assertEqual(len(gaps), 1)
        mold = next(m for m in snap.tooling_molds if m.mold_id == "M1")
        self.assertEqual(mold.changeover_time_min, 0.0)
        self.assertTrue(any("换模工时" in w for w in snap.warnings))

    def test_explicit_zero_available_qty_is_not_overwritten_to_one(self):
        # `_int(val, 1) or 1` turned an explicit 0 into 1 -- a real bug.
        snap = self._load(
            "tooling_zero_qty",
            rows_line_base=[{"site": "WL", "line": "L1", "trp": 0.71,
                             "hours_per_shift": 8.0, "shifts_per_day": 3}],
            rows_tooling=[{"site": "WL", "mold_id": "M1", "mold_name": "n",
                           "applicable_lines": "L1", "applicable_parts": "P1",
                           "available_qty": 0, "changeover_time_min": 30.0}])
        mold = next(m for m in snap.tooling_molds if m.mold_id == "M1")
        self.assertEqual(mold.available_qty, 0)


if __name__ == "__main__":
    unittest.main()
```

**测试需要一个最小工作簿构造器。** 若 `tests/` 下已有类似 helper（先 `grep -rn "openpyxl.Workbook" tests/`），复用它；没有就新建 `tests/workbook_builder.py`：

```python
"""Build minimal v2-layout workbooks for loader tests."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence

import openpyxl

from src.master_schema import SHEET_BY_KEY


def build_minimal_workbook(tmp_name: str, **rows_by_sheet: Sequence[dict]) -> Path:
    """Create a workbook containing every input sheet, filled only where asked.

    ``rows_by_sheet`` keys are sheet keys; values are dicts keyed by column key.
    Sheets not mentioned get a header row and no data.
    """
    out_dir = Path(__file__).resolve().parent / "fixtures" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{tmp_name}.xlsx"

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for spec in SHEET_BY_KEY.values():
        if spec.key == "instructions":
            continue
        ws = wb.create_sheet(spec.title)
        columns = list(spec.active_columns())
        ws.append([c.header for c in columns])
        for row in rows_by_sheet.get(spec.key) or []:
            ws.append([row.get(c.key) for c in columns])
    wb.save(path)
    wb.close()
    return path
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_loader_gaps -v`
Expected: FAIL — `TypeError` 断言失败（`LineBase(line="L1")` 今天能成功，因为 `trp` 有默认值 0.71）

- [ ] **Step 3: 改 dataclass 字段默认值**

`src/data_loader.py:51-58`：

```python
@dataclass
class LineBase:
    """Line-level capacity data (new 产线基础数据 sheet).

    The three capacity fields are required: spec 5.6 forbids the loader from
    inventing 0.71 / 8.0 / 3, and a field default would re-invent them.
    """

    line: str
    trp: float
    hours_per_shift: float
    shifts_per_day: int
    source: str = ""
```

`src/data_loader.py:72-83`：

```python
@dataclass
class ToolingMold:
    site: str
    mold_id: str
    mold_name: str
    applicable_lines: List[str]
    applicable_parts: List[str]
    # Spec 4.5: one physical mold runs on one machine -- an explicit business
    # ruling, so the default stays.  Deprecated stroke/status columns are
    # neutral zero instead of invented values (spec 5.6).
    available_qty: int = 1
    changeover_time_min: float = 0.0
    max_life_strokes: int = 0
    current_strokes: int = 0
    maintenance_cycle_strokes: int = 0
    status: str = ""
```

**注意字段顺序**：`LineBase` 去掉默认值后，`source: str = ""` 仍在最后，位置参数顺序不变（`line, trp, hours_per_shift, shifts_per_day`）。既有调用点若用关键字传参则不受影响；用位置传参的也不受影响，因为前四个位置没变。

- [ ] **Step 4: 在 loader 里建 gap 收集器**

`src/excel_loader.py`，`load_workbook_snapshot`（`:222`）内 `warnings: List[str] = []`（`:239`）之后插入：

```python
        gaps = GapCollector()
        owner = "excel_loader.load_workbook_snapshot"

        def _gap(sheet_key: str, column_key: str, index, reason: str = "missing"):
            return gaps.add(owner, sheet_key, column_key, index, reason)
```

顶部导入：`from src.data_gaps import GapCollector`。

- [ ] **Step 5: 改 line_base 块（`:290-315`）**

把 `_number(line_table.val(row, "trp"), 0.71)` 这类调用换成「取值或产 gap 并跳行」：

```python
        if line_table is not None:
            for row in line_table.rows():
                line = _text(line_table.val(row, "line"))
                if not line:
                    continue
                index = (site_filter or "", line)
                trp_raw = line_table.val(row, "trp")
                hours_raw = line_table.val(row, "hours_per_shift")
                shifts_raw = line_table.val(row, "shifts_per_day")
                missing = [
                    (key, raw) for key, raw in (
                        ("trp", trp_raw),
                        ("hours_per_shift", hours_raw),
                        ("shifts_per_day", shifts_raw),
                    ) if raw is None or _text(raw) == ""
                ]
                if missing:
                    for column_key, _ in missing:
                        gap = _gap("line_base", column_key, index)
                        gaps.warn(warnings, gap,
                                  f"产线基础数据: 产线 {line} 缺 {column_key}，该行已跳过")
                    continue
                entry = LineBase(
                    line=line,
                    trp=_number(trp_raw),
                    hours_per_shift=_number(hours_raw),
                    shifts_per_day=_int(shifts_raw),
                    source="产线基础数据",
                )
                existing = line_base.get(line)
                if existing is not None and (
                    abs(existing.trp - entry.trp) > 1e-9
                    or abs(existing.hours_per_shift - entry.hours_per_shift) > 1e-9
                    or existing.shifts_per_day != entry.shifts_per_day
                ):
                    warnings.append(
                        f"产线基础数据: 产线 {line} 存在相互冲突的多行，已取最后一行"
                        f"（TRP={entry.trp}, 工时={entry.hours_per_shift}, 班次={entry.shifts_per_day}）"
                    )
                line_base[line] = entry
```

冲突告警那段逻辑**原样保留**——它不是编造，是真实的重复行检测。

- [ ] **Step 6: 改 tooling 块（`:317-345`）**

```python
                index = (site_filter or "", mold_id)
                changeover_raw = tooling.val(row, "changeover_time_min")
                if changeover_raw is None or _text(changeover_raw) == "":
                    gap = _gap("tooling", "changeover_time_min", index)
                    gaps.warn(warnings, gap,
                              f"工装模具 {mold_id}: 缺换模工时，按 0 分钟计"
                              f"（阶段 2 按规格 4.4 优先级表裁决）")
                    changeover = 0.0
                else:
                    changeover = _number(changeover_raw)
                qty_raw = tooling.val(row, "available_qty")
                # Spec 4.5 keeps the default 1, but `or 1` silently rewrote an
                # explicit 0 into 1 -- that was a bug, not a default.
                available_qty = _int(qty_raw, 1) if qty_raw is not None and _text(qty_raw) != "" else 1
                tooling_molds.append(
                    ToolingMold(
                        site=_text(tooling.val(row, "site")) or site_filter,
                        mold_id=mold_id,
                        mold_name=_text(tooling.val(row, "mold_name")),
                        applicable_lines=lines,
                        applicable_parts=parts,
                        available_qty=available_qty,
                        changeover_time_min=changeover,
                        # deprecated columns: neutral zero, no gap (class A)
                        max_life_strokes=_int(tooling.val(row, "max_life_strokes"), 0),
                        current_strokes=_int(tooling.val(row, "current_strokes"), 0),
                        maintenance_cycle_strokes=_int(
                            tooling.val(row, "maintenance_cycle_strokes"), 0),
                        status=_text(tooling.val(row, "status")),
                    )
                )
```

- [ ] **Step 7: 把 gaps 接到快照**

`src/excel_loader.py:625-640` 的 `return ProductionInputSnapshot(...)` 追加一行：

```python
            gaps=gaps.gaps(),
```

- [ ] **Step 8: 跑测试确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_loader_gaps -v`
Expected: PASS（7 项）

- [ ] **Step 9: 跑全量测试，记录暴露面**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py`

**这一步的目的是观察，不是让它立刻全绿。** 把失败清单抄下来，逐条判定：

- 失败原因是「测试自己构造了缺数据的 fixture」→ 修 fixture（补上真实值），**不要**改回默认值
- 失败原因是「真实模板里那一列本来就空」→ 记下 `(sheet_key, column_key)`，这是要在任务 8/9 之后统一裁决的 T1/T2 归属
- 失败原因是黄金基线变了 → 按 §8.1 写书面理由，判断变化方向是否符合预期

- [ ] **Step 10: 加进 fast 列表 + 提交**

`run_tests.py:32-40` 追加 `"tests.test_loader_gaps"`。

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast
git checkout -- data/snapshot_WL.json 2>/dev/null || true
git add src/data_loader.py src/excel_loader.py tests/test_loader_gaps.py \
        tests/workbook_builder.py run_tests.py
git commit -m "fix: stop fabricating line_base and tooling defaults; emit DataGap instead

LineBase.trp/hours/shifts lose their 0.71/8.0/3 field defaults (they would
re-invent the values the loader stopped guessing). Tooling deprecated stroke
columns go to neutral zero; changeover_time_min keeps 0.0 but now announces
itself as a gap pending the spec 4.4 precedence ruling in stage 2.

Also fixes a real bug: `_int(val, 1) or 1` rewrote an explicit
available_qty=0 into 1."
```

---

### Task 8: 拆地雷 2（区域 B）— FG / SF 参数表

**Files:**
- Modify: `src/excel_loader.py:370-435`（fg/sf 参数块）、`src/data_loader.py:25-47`（`ProductParam`）
- Test: `tests/test_loader_gaps.py`（追加）

**Interfaces:**
- Consumes: `_gap` 闭包（任务 7）
- Produces: `ProductParam.hours_per_shift` / `shifts_per_day` **不再有 8.0 / 3 默认值**（改为必填）；`build_requirement_plan` 的调用方式不变

**逐站点编造的班次几何必须删掉。** `excel_loader.py:393-398` 与 `:400-406` 有 `(8.0 if site_filter == "WL" else 12.0)` 和 `(3 if site_filter == "WL" else 2)`——这是按站点名硬编码的产能，属于最危险的一类编造：换个站点就静默换一套产能。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_loader_gaps.py`：

```python
class TestShiftGeometryIsNeverGuessed(unittest.TestCase):
    def test_product_param_requires_shift_geometry(self):
        from src.data_loader import ProductParam
        with self.assertRaises(TypeError):
            ProductParam("WL", "L1", "P1", "desc", 10.0, 0.71, 1.0, 100.0)

    def test_missing_line_base_row_is_a_gap_not_a_site_specific_default(self):
        from src.excel_loader import load_workbook_snapshot
        from tests.workbook_builder import build_minimal_workbook
        path = build_minimal_workbook(
            "no_line_base",
            parameters_fg=[{"site": "WL", "part_no": "P1", "description": "d",
                            "mpq": 10.0}],
            routing=[{"site": "WL", "part_no": "P1", "stage": "FG",
                      "primary_line": "L_ORPHAN", "ct_seconds": 10.0,
                      "headcount": 3.0}],
        )
        snap = load_workbook_snapshot(str(path), "WL")
        gaps = [g for g in snap.gaps if g.column_key == "hours_per_shift"]
        self.assertTrue(gaps, "missing shift geometry must be reported, not guessed")
        self.assertFalse(any("WL" in g.reason for g in gaps))

    def test_output_per_shift_is_derived_not_read_from_a_dead_column(self):
        # Spec 4.6 item 8: the column is deprecated; the value is computed.
        from src.excel_loader import load_workbook_snapshot
        from tests.workbook_builder import build_minimal_workbook
        path = build_minimal_workbook(
            "derived_output",
            line_base=[{"site": "WL", "line": "L1", "trp": 0.71,
                        "hours_per_shift": 8.0, "shifts_per_day": 3}],
            parameters_fg=[{"site": "WL", "part_no": "P1", "description": "d",
                            "mpq": 10.0}],
            routing=[{"site": "WL", "part_no": "P1", "stage": "FG",
                      "primary_line": "L1", "ct_seconds": 60.0,
                      "headcount": 3.0}],
        )
        snap = load_workbook_snapshot(str(path), "WL")
        param = snap.fg_params["P1"]
        # 8h * 3600 / 60s * 0.71 = 340.8
        self.assertAlmostEqual(param.output_per_shift, 340.8, places=1)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_loader_gaps.TestShiftGeometryIsNeverGuessed -v`
Expected: FAIL

- [ ] **Step 3: 改 `ProductParam` 字段默认值**

`src/data_loader.py:25-47`，把 `hours_per_shift: float = 8.0` 与 `shifts_per_day: int = 3` 移到必填区（`output_per_shift` 之后、`target_stock` 之前）：

```python
@dataclass
class ProductParam:
    site: str
    line: str
    part_no: str
    description: str
    ct_seconds: float
    trp: float
    mpq: float
    output_per_shift: float
    hours_per_shift: float
    shifts_per_day: int
    target_stock: float = 0.0
    ...  # 其余字段与默认值不变
```

**这会破坏所有按位置构造 `ProductParam` 的调用点。** 先找出它们：

Run: `grep -rn "ProductParam(" src/ tests/`

逐个检查：位置传参且少于 10 个的必须补齐。`tests/test_regressions.py:40` 那种 `ProductParam("WL", "L1", "PART_A", "A", 10.0, 1.0, 1.0, 2880.0)` 是 8 个位置参数——需要补 `hours_per_shift, shifts_per_day`。**补真实值（8.0, 3），不要为了少改而把字段默认值加回去。**

- [ ] **Step 4: 改 loader 的班次几何块**

`src/excel_loader.py:388-406`。删掉两处按站点硬编码，改成「产线基础数据 → 参数表显式列 → gap」：

```python
                index = (site_filter or "", pn)
                base = line_base.get(line) if line else None
                hours = (base.hours_per_shift if base else 0.0) or _number(
                    table.val(row, "hours_per_shift"), 0.0)
                shifts = (base.shifts_per_day if base else 0) or _int(
                    table.val(row, "shifts_per_day"), 0)
                if not hours or not shifts:
                    for column_key, value in (("hours_per_shift", hours),
                                              ("shifts_per_day", shifts)):
                        if not value:
                            gap = _gap("line_base", column_key, (index[0], line or ""))
                            gaps.warn(
                                warnings, gap,
                                f"{pn}: 产线 {line or '(未定)'} 缺 {column_key}"
                                f"（产线基础数据与参数表均未给出），该行已跳过")
                    continue
```

`layout == "v2"` 与 `else` 两个分支合并成上面这一段——**按站点名分叉的 `(8.0 if site_filter == "WL" else 12.0)` 与 `(3 if site_filter == "WL" else 2)` 全部删除**。旧版（legacy 25 表）工作簿的班次几何同样只从参数表的显式列取；取不到就跳行并告警。

- [ ] **Step 5: 删掉 moq 回退到 mpq 的编造**

`src/excel_loader.py:408`：

```python
                moq = _number(table.val(row, "moq"), 0.0) or mpq
```

`moq → mpq` 是把两个语义不同的量（最小起订量 vs 最小包装量）混为一谈。改为：

```python
                moq_raw = table.val(row, "moq")
                moq = _number(moq_raw) if moq_raw is not None and _text(moq_raw) != "" else 0.0
```

`0.0` 是「无 MOQ 约束」的中性值，不是编造的替代值——**不产 gap**（该列非必填）。

- [ ] **Step 6: 跑测试确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_loader_gaps -v`
Expected: PASS

- [ ] **Step 7: 全量回归 + 基线判定**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py`

黄金基线**可能变化**：真实模板里 `轴套7` 在 Routing 缺行，之前靠回退拿到班次几何，现在会走 gap 路径。若基线变了，提交信息必须写明：

> B? / M?：删除按站点硬编码的班次几何（WL→8h/3班，其他→12h/2班）。预期变化方向：非 WL 站点的产能不再被静默改写；WL 站点若产线基础数据完整则结果不变。实际结果：<填写>。

- [ ] **Step 8: 提交**

```bash
git checkout -- data/snapshot_WL.json 2>/dev/null || true
git add src/data_loader.py src/excel_loader.py tests/test_loader_gaps.py tests/
git commit -m "fix: remove site-hardcoded shift geometry and the moq->mpq conflation

(8.0 if site=='WL' else 12.0) and (3 if site=='WL' else 2) silently gave
every non-WL site a different capacity. ProductParam.hours_per_shift and
shifts_per_day are now required fields so no default can re-invent them."
```

---

### Task 9: 拆地雷 2（区域 C）— PM / 到货 / 客户评级 / BOM / 订单

**Files:**
- Modify: `src/excel_loader.py:480-520`（订单与 due_hour）、`:520-545`（PM）、`:545-585`（到货）、`:590-610`（客户评级）、`:455-470`（BOM ratio）
- Test: `tests/test_loader_gaps.py`（追加）

**Interfaces:**
- Consumes: `_gap`（任务 7）
- Produces: 无新公共接口；`ProductionInputSnapshot.gaps` 覆盖全部 15+2 表

**逐条处置表**（规格 §5.6 列的调用点，本任务全覆盖）：

| 位置 | 编造值 | 类 | 处置 |
|---|---|---|---|
| `:489` | `due_hour` 硬编码 `15` / `20` | C | 提为模块级常量 `DEFAULT_DUE_HOUR = 15`，并在缺失时产 gap（`demand.due_hour`） |
| `:511` | 日期解析失败 `except: pass` | B | 删除 `pass`；解析失败产 gap + warn |
| `:525` | `downtime_hours` 默认 `8.0` | C | 缺失产 gap，值取 `0.0`（0 小时停机 = 不占产能，中性） |
| `:529-537` | 用 downtime 反推 PM 结束日期与班次 | B | **删除反推**；`end_date` 是 `required=True`，缺失就跳行 + gap |
| `:538` | 编造 `PM-{site}-{line}-{date}` 工单号 | C | 保留但标 `synthetic=True` 语义：改为 `pm_plan_no` 缺失时用同格式生成，并产一条 `reason="synthetic_id"` 的 gap |
| `:551,553,555` | `capacity_rate=0` 等 | A/C | `capacity_rate` 缺失 → 不产 gap（0 表示「未声明降载」），但删除多余的编造分支 |
| `:572` | 编造 `PO-{part}-{row}` 采购单号 | C | 同 `:538` 处理，`reason="synthetic_id"` |
| `:580,582` | `qc_release=2.0` / `status="质检合格"` | A | 两列都是 `deprecated=True` → 中性零值，不产 gap |
| `:599-604` | `tier="TIER_A"` / `weight=3` / `otd=98.0` | B/C | `tier` 是 `required=True` → 缺失跳行 + gap；`priority_weight` / `otd_target_rate` 非必填 → 中性 `0` + gap |
| `:462` | BOM `ratio → 1.0` | B | `ratio` 是 `required=True` → 缺失跳行 + gap |
| `:600` 附近 | `delivery_window` 回退 `"0天严格保供"`、`subcontract_allowed` 回退 `"协商外协"` | B | **删除回退**，原样保留空字符串。这两个字段是自由文本（规格 4.6 #6 待数值化），编造一个「严格保供」会静默改变业务语义 |

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_loader_gaps.py`。每类挑一个代表点做端到端断言（其余靠 Step 3 的 grep 验收覆盖）：

```python
class TestNoInventedBusinessSemantics(unittest.TestCase):
    def _snap(self, name, **rows):
        from src.excel_loader import load_workbook_snapshot
        from tests.workbook_builder import build_minimal_workbook
        path = build_minimal_workbook(name, **rows)
        return load_workbook_snapshot(str(path), "WL")

    def test_missing_bom_ratio_drops_the_row_instead_of_assuming_one(self):
        snap = self._snap(
            "bom_no_ratio",
            bom=[{"site": "WL", "fg_part_no": "FG1", "sf_part_no": "SF1",
                  "ratio": None}])
        self.assertEqual(snap.bom.get("FG1", []), [])
        self.assertTrue(any(g.sheet_key == "bom" and g.column_key == "ratio"
                            for g in snap.gaps))

    def test_missing_customer_tier_drops_the_row_instead_of_assuming_tier_a(self):
        snap = self._snap(
            "customer_no_tier",
            customer=[{"site": "WL", "customer_code": "C1",
                       "customer_name": "n", "tier": None}])
        self.assertEqual([t for t in snap.customer_tiers if t.customer_code == "C1"], [])
        self.assertTrue(any(g.column_key == "tier" for g in snap.gaps))

    def test_blank_delivery_window_is_not_filled_with_a_strict_supply_promise(self):
        snap = self._snap(
            "customer_no_window",
            customer=[{"site": "WL", "customer_code": "C1",
                       "customer_name": "n", "tier": "TIER_A",
                       "delivery_window": None, "subcontract_allowed": None}])
        tier = next(t for t in snap.customer_tiers if t.customer_code == "C1")
        self.assertEqual(tier.delivery_window, "")
        self.assertEqual(tier.subcontract_allowed, "")

    def test_missing_pm_end_date_drops_the_row_instead_of_being_back_computed(self):
        snap = self._snap(
            "pm_no_end",
            pm=[{"site": "WL", "line": "L1", "pm_item": "保养",
                 "start_date": "2026-10-13", "end_date": None,
                 "downtime_hours": 8.0, "handling_strategy": "顺延"}])
        self.assertEqual(snap.pm_schedules, [])
        self.assertTrue(any(g.column_key == "end_date" for g in snap.gaps))

    def test_synthetic_identifiers_are_flagged_as_gaps(self):
        snap = self._snap(
            "pm_no_plan_no",
            pm=[{"site": "WL", "line": "L1", "pm_item": "保养",
                 "start_date": "2026-10-13", "end_date": "2026-10-13",
                 "downtime_hours": 8.0, "handling_strategy": "顺延",
                 "pm_plan_no": None}])
        self.assertTrue(any(g.reason == "synthetic_id" for g in snap.gaps))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_loader_gaps.TestNoInventedBusinessSemantics -v`
Expected: FAIL（5 项全红）

- [ ] **Step 3: 按处置表逐条改**

改完用这条命令自查是否还有残留的字面量编造：

Run: `grep -n ", 0.71)\|, 8.0)\|, 3)\|, 30.0)\|500000\|50000\|, 98.0)\|TIER_A\|质检合格\|0天严格保供\|协商外协\|, 2.0)\|, 1.0)" src/excel_loader.py`
Expected: 无输出（或每处都在注释里说明是规格裁决的显式业务值）

Run: `grep -n "except Exception:" -A 1 src/excel_loader.py`
Expected: 不再有 `pass` 紧跟其后

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_loader_gaps -v`
Expected: PASS

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py`
Expected: 全绿或失败项已按任务 7 Step 9 的三分法逐条判定

- [ ] **Step 5: 提交**

```bash
git checkout -- data/snapshot_WL.json 2>/dev/null || true
git add src/excel_loader.py tests/test_loader_gaps.py
git commit -m "fix: replace invented PM/arrival/tier/BOM defaults with DataGap

Required columns (bom.ratio, customer.tier, pm.end_date) now drop the row
and report a gap instead of assuming 1.0 / TIER_A / a back-computed date.
Blank delivery_window and subcontract_allowed stay blank -- filling them
with '0天严格保供' silently changed business semantics.
Synthetic PM/PO identifiers are kept but flagged reason='synthetic_id'."
```

---

### Task 10: 拆地雷 2（区域 D）— CSV 回退路径

**Files:**
- Modify: `src/data_loader.py:329,330,332-333,367,368,406,496-501,527-531,555-558,579-583`
- Test: `tests/test_loader_gaps.py`（追加）

**Interfaces:**
- Consumes: `DataGap` / `GapCollector`（任务 1）
- Produces: CSV 路径产出的快照同样带 `gaps`

**为什么不能跳过这个任务：** `server.py:1979/2157/2168` 有 `routing_from_rows(...) or load_routing(csv)` 这类回退。只修 xlsx 路径，缺数据时系统会静默退回 CSV 路径继续编造——A2 双轨在数据层的翻版。

- [ ] **Step 1: 写失败测试**

```python
class TestCsvPathAlsoReportsGaps(unittest.TestCase):
    def test_csv_tooling_missing_changeover_is_a_gap(self):
        import csv
        import tempfile
        from pathlib import Path
        from src.data_loader import load_tooling_data

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Tooling-Mold.csv"
            with path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Site", "Mold ID", "Mold Name",
                                 "Applicable Lines", "Applicable Part Numbers",
                                 "Available Qty", "Changeover Time (min)"])
                writer.writerow(["WL", "M1", "n", "L1", "P1", "1", ""])
            molds, gaps = load_tooling_data(str(path))

        self.assertEqual(len(molds), 1)
        self.assertEqual(molds[0].changeover_time_min, 0.0)
        self.assertTrue(any(g.column_key == "changeover_time_min" for g in gaps))
```

**若 `load_tooling_data` 今天只返回 `molds`**，本任务把它改成返回 `(molds, gaps)` 二元组——**先 grep 全部调用点**（`grep -rn "load_tooling_data" src/ tests/`）并逐个更新。不要为了兼容而保留旧签名的包装函数（D.3 反对向后兼容 shim）。

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_loader_gaps.TestCsvPathAlsoReportsGaps -v`
Expected: FAIL

- [ ] **Step 3: 按 xlsx 路径的同一分类表改 CSV 路径**

| 位置 | 编造值 | 类 | 处置 |
|---|---|---|---|
| `:329,367` | `_to_float(row.get("Line TRP"), 0.71)` | B | 缺失 → gap + 跳行 |
| `:330,368` | MPQ 默认 | B | `mpq` 是 `required=True` → gap + 跳行 |
| `:332-333` | `hours/shifts` 默认 8/3 | B | 同任务 8，删除按站点分叉 |
| `:406` | BOM `ratio → 1.0` | B | gap + 跳行 |
| `:496-501` | tooling 五个默认 | A/D | `available_qty` 保留 1（§4.5）；`changeover_time_min` → 0.0 + gap；三个冲次列 → 0，不产 gap（deprecated） |
| `:527-531` | PM `downtime_hours=8.0` 与反推 | B/C | 同任务 9 |
| `:555-558` | 到货字段 | C | 缺失 → gap，中性值 |
| `:579-583` | `tier` / `priority_weight=3` / `otd=98.0` | B/C | `tier` 缺失跳行；其余中性 0 + gap |

- [ ] **Step 4: 验证归零**

Run: `grep -n ", 0.71)\|, 8.0)\|, 3)\|, 30.0)\|500000\|50000\|, 98.0)\|, 2.0)" src/data_loader.py`
Expected: 无输出（或仅剩 `LineBase`/`ToolingMold` dataclass 定义处已在任务 7 改掉的行）

- [ ] **Step 5: 全量回归 + 提交**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast
git checkout -- data/snapshot_WL.json 2>/dev/null || true
git add src/data_loader.py tests/test_loader_gaps.py
git commit -m "fix: CSV fallback path reports the same gaps as the xlsx path

Without this, a missing column silently fell back to load_routing(csv) and
kept inventing values -- the data-layer twin of defect A2."
```

---

### Task 11: 删数量断言，建契约检查骨架

**Files:**
- Modify: `tests/test_kernel.py:288-293`
- Create: `tests/test_rule_contract.py`
- Modify: `run_tests.py:32-40`

**Interfaces:**
- Consumes: `TEMPLATES` / `IMPLEMENTED_TEMPLATES`（`src/kernel/registry.py`）
- Produces: 10 条契约检查的骨架；本阶段 **#1–#9 标 `skipTest`，#10 实装**（规格 §8.2：「加契约检查 #10，此阶段先标 skip」指 #1–#9，#10 是 `force_hard` 归零检查，阶段 0 只登记不强制）

**为什么删 `len(TEMPLATES) == 20`：** 它锁数量不锁语义，既挡不住空壳模板，又让每次正当扩展都要改测试——这正是「19 个算子」话术被冻结在 UI（`rule_sets.html:378`）和手册里的原因（规格 §7.1）。

- [ ] **Step 1: 写新测试文件**

`tests/test_rule_contract.py`：

```python
"""Contract checks from spec section 7.1.

Stage 0 only installs the scaffold.  Checks 1-9 need the metric layer
(stage 2) and are skipped with an explicit reason; check 10 is implemented
but expected to fail until stage 1 removes force_hard, so it is skipped too.

A skipped check must say which stage turns it on -- an unconditional
skipTest with no reason is how a contract check quietly dies.
"""
from __future__ import annotations

import unittest


class TestRuleContract(unittest.TestCase):
    def test_01_every_template_has_a_non_empty_body(self):
        self.skipTest("stage 2: needs the metric registry")

    def test_02_removing_a_data_dependency_must_produce_a_gap(self):
        self.skipTest("stage 2: needs DataGap-producing metrics")

    def test_03_every_metric_declares_a_dimension(self):
        self.skipTest("stage 2: needs the metric registry")

    def test_04_every_metric_declares_catalog_refs(self):
        self.skipTest("stage 2: needs the metric registry")

    def test_05_each_plus_hard_produces_at_least_one_constraint(self):
        self.skipTest("stage 2: needs the generic compiler")

    def test_06_requires_is_a_superset_of_actual_context_access(self):
        self.skipTest("stage 2: needs the recording proxy (spec 6.5)")

    def test_07_every_catalog_rule_is_covered_or_registered_unenforced(self):
        self.skipTest("stage 2: needs the 32-rule mapping register (spec B)")

    def test_08_scope_attributes_are_in_the_dimension_vocabulary(self):
        self.skipTest("stage 2: needs the dimension vocabulary (spec 3.3)")

    def test_09_op_minimize_is_mutually_exclusive_with_nature(self):
        self.skipTest("stage 2: save-time validation (spec D11)")

    def test_10_no_force_hard_escape_hatch(self):
        self.skipTest("stage 1: force_hard is removed at the end of stage 1")


class TestTemplateRegistryIsNotFrozenByCount(unittest.TestCase):
    """Replaces tests/test_kernel.py:289-290 (len(TEMPLATES) == 20)."""

    def test_registry_declares_its_templates_without_pinning_a_count(self):
        from src.kernel.registry import IMPLEMENTED_TEMPLATES, TEMPLATES
        self.assertTrue(TEMPLATES)
        self.assertTrue(IMPLEMENTED_TEMPLATES.issubset(set(TEMPLATES)))

    def test_the_two_templates_the_old_assertion_actually_cared_about(self):
        from src.kernel.registry import TEMPLATES
        self.assertIn("capacity_utilization", TEMPLATES)
        self.assertIn("labor_capacity", TEMPLATES)
```

- [ ] **Step 2: 删掉旧的数量断言**

`tests/test_kernel.py:288-293`，把

```python
    def test_01_registry_has_20_templates(self):
        self.assertEqual(len(TEMPLATES), 20)
        self.assertTrue(IMPLEMENTED_TEMPLATES.issubset(set(TEMPLATES)))
        self.assertIn("capacity_utilization", TEMPLATES)
        self.assertIn("labor_capacity", TEMPLATES)
```

整段删除（其有效部分已搬到 `tests/test_rule_contract.py`）。若 `TEMPLATES` / `IMPLEMENTED_TEMPLATES` 的导入因此变成未使用，一并删掉导入。

- [ ] **Step 3: 跑测试**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_rule_contract tests.test_kernel -v`
Expected: `test_rule_contract` 10 项 skipped + 2 项 PASS；`test_kernel` 少一项且全绿

- [ ] **Step 4: 加进 fast 列表 + 提交**

`run_tests.py:32-40` 追加 `"tests.test_rule_contract"`。

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast
git checkout -- data/snapshot_WL.json 2>/dev/null || true
git add tests/test_rule_contract.py tests/test_kernel.py run_tests.py
git commit -m "test: replace the len(TEMPLATES)==20 count assertion with contract scaffolding

The count locked a number, not a contract: it could not catch a hollow
template and it made every legitimate extension a test edit. Ten checks
from spec 7.1 are installed, each skipped with the stage that turns it on."
```

---

### Task 12: 修 B2 — 延误变量域解耦到 horizon

**Files:**
- Modify: `src/scheduler.py:69`（`LATENESS_SLACK_SHIFTS = 6`）、`:1157-1164`（`_build_windows`）
- Test: `tests/test_lateness_window.py`（新建）
- Modify: `run_tests.py:32-40`

**Interfaces:**
- Consumes: 无
- Produces: `_build_windows()` 的 FG 上界从 `min(num_shifts-1, due_shift + 6)` 变成 `num_shifts - 1`

**为什么必须在阶段 0 做：** 规格 §8.2 明说这会**阻塞阶段 1 的回归验证**——超载数据集今天直接 INFEASIBLE → `RuntimeError`（`scheduler.py:712`），阶段 1 每删一个 legacy 块都要跑基线，跑不动就没法验证。

**这个改动会改变计划结果。** 延误从「最多晚 6 个班次否则不可行」变成「最多晚到 horizon 末尾」，超载场景下求解器会产出带延误的计划而不是崩掉。基线**预期变化**，必须按 §8.1 写书面理由。

- [ ] **Step 1: 写失败测试**

`tests/test_lateness_window.py`：

```python
from __future__ import annotations

import unittest


class TestLatenessWindow(unittest.TestCase):
    def test_fg_window_reaches_the_end_of_the_horizon(self):
        """An overloaded dataset must produce a late plan, not INFEASIBLE."""
        from tests.test_regressions import _overloaded_snapshot   # reuse the fixture
        from src.scheduler import ProductionScheduler

        snap = _overloaded_snapshot()
        scheduler = ProductionScheduler(snap)
        windows = scheduler._build_windows()
        horizon = scheduler.num_shifts - 1
        for task in snap.orders and [] or []:
            pass
        upper_bounds = {hi for _, hi in windows.values()}
        self.assertIn(horizon, upper_bounds,
                      "at least one task must be allowed to slip to the horizon end")

    def test_no_window_exceeds_the_horizon(self):
        from tests.test_regressions import _overloaded_snapshot
        from src.scheduler import ProductionScheduler

        scheduler = ProductionScheduler(_overloaded_snapshot())
        horizon = scheduler.num_shifts - 1
        for task_id, (lo, hi) in scheduler._build_windows().items():
            self.assertLessEqual(hi, horizon, task_id)
            self.assertGreaterEqual(lo, 0, task_id)
            self.assertLessEqual(lo, hi, task_id)

    def test_sf_window_still_precedes_its_parent(self):
        from tests.test_regressions import _overloaded_snapshot
        from src.scheduler import ProductionScheduler

        snap = _overloaded_snapshot()
        scheduler = ProductionScheduler(snap)
        windows = scheduler._build_windows()
        for task in snap.sf_tasks:
            parent_hi = windows[task.parent_task_id][1]
            self.assertLessEqual(windows[task.task_id][1], max(0, parent_hi - 1))


if __name__ == "__main__":
    unittest.main()
```

**若 `tests/test_regressions.py` 里没有 `_overloaded_snapshot`**，就地写一个：用 `tests/workbook_builder.build_minimal_workbook` 造一个需求量远超产能的快照（例如单产线单班次、CT 使得一件就要 2 个班次、订单要 10 件、horizon 只有 5 个班次）。**不要为了让测试好写而放宽断言。**

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_lateness_window -v`
Expected: FAIL — 上界被 `due_shift + 6` 截断，`horizon` 不在 `upper_bounds` 里；或求解直接抛 `RuntimeError`（INFEASIBLE）

- [ ] **Step 3: 改 `_build_windows`**

`src/scheduler.py:1157-1164`：

```python
    def _build_windows(self) -> Dict[str, Window]:
        """Task placement windows.

        FG tasks may slip anywhere inside the horizon.  The old
        ``due_shift + LATENESS_SLACK_SHIFTS`` cap turned an overloaded
        dataset into INFEASIBLE -> RuntimeError instead of a late plan
        (spec defect B2).  Lateness is penalised, not forbidden.
        """
        windows: Dict[str, Window] = {}
        for task in self.plan.fg_tasks:
            windows[task.task_id] = (0, self.num_shifts - 1)
        for task in self.plan.sf_tasks:
            parent_hi = windows[task.parent_task_id][1]
            windows[task.task_id] = (0, max(0, min(parent_hi - 1, self.num_shifts - 1)))
        return windows
```

- [ ] **Step 4: 删掉常量**

`src/scheduler.py:69` 删除 `LATENESS_SLACK_SHIFTS = 6`。

Run: `grep -rn "LATENESS_SLACK_SHIFTS" src/ tests/`
Expected: 无输出。若测试引用了它，改掉测试（它在测一个已被删除的启发式）。

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_lateness_window -v`
Expected: PASS（3 项）

- [ ] **Step 6: 全量回归 + 基线判定（验收 0.8、0.9）**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py`

黄金基线**大概率变化**。提交信息必须含：

> B2：删除 `LATENESS_SLACK_SHIFTS=6` 的窗口硬截断。预期变化方向：WL 10 天基线在产能足够时**不应变化**（窗口上界本来就没被 6 个班次卡住）；若变化，说明基线数据集本身处于超载状态，之前是靠截断掩盖的。实际结果：<填写>。

**如果 WL 基线变了**，先判断是「之前的计划本来就不可行/被截断」还是「新窗口让求解器找到了更差的解」。后者说明罚值不足以阻止无谓延误——那是 B1（权重塔）的症状，记录到阶段 2，**不要在这里加回窗口截断**。

- [ ] **Step 7: 加进 fast 列表 + 提交**

`run_tests.py:32-40` 追加 `"tests.test_lateness_window"`。

```bash
git checkout -- data/snapshot_WL.json 2>/dev/null || true
git add src/scheduler.py tests/test_lateness_window.py run_tests.py \
        tests/fixtures/wl_10d_baseline.json
git commit -m "fix(B2): decouple the lateness window from a 6-shift cap

An overloaded dataset used to go INFEASIBLE -> RuntimeError instead of
producing a late plan, which blocks stage 1's per-template regression gate.
Lateness is penalised, not forbidden.

Baseline: <expected direction> / <actual result>."
```

---

### Task 13: 删废弃列 + Campaign 改读模板『产品族名』

**Files:**
- Modify: `src/master_schema.py:159-160,173-174`（Parameters-FG 的 `hours_per_shift` / `shifts_per_day` / `output_per_shift`）、`:212-213`（SFN 的 `output_per_shift`）、`:249`（Routing 的 `output_per_shift`）
- Modify: `src/campaign.py:1-48`（加 `campaign_families_from_rows`、`Sequence` 导入、`load_campaign_families` docstring）、`src/server.py:59,2193-2196`（唯一切换点）、`src/instructions.py:201`
- Modify: `run_tests.py:32-40`（追加 `tests.test_campaign`）
- Test: `tests/test_campaign.py`（既有，追加）
- **不改**：`src/scheduler.py:1818,1850`、`src/batching.py:55,73,97`。它们只读 `family.campaign_group`，而该字段在新旧两条路径下都由 `product_family` 填，签名与语义都不变。把它们列进修改范围只会诱导无意义的改动。
- **不改**：`src/master_export.py:23-38`（`_campaign_map`）。这是 CSV 的**第二个独立读取点**，只用于导出时给空的 `product_family` / `mold_id` 兜底（顺序是 `tool_parts` → CSV）。本任务不动它——它就是「CSV 作为回退」的一部分。记录在此以免被误认为遗漏。

**Interfaces:**
- Consumes: `read_master_rows`（`excel_loader.py:645`）产出的 `parameters_fg` / `parameters_sfn` 行里的 `part_no` / `product_family` / `mold_id` / `description` 键
- Produces: `campaign_families_from_rows(site: str, fg_rows: Sequence[dict], sf_rows: Sequence[dict]) -> Dict[str, CampaignFamily]`；`load_campaign_families` 降级为**仅供 CSV 基线回退**并在 docstring 里写明

**规格依据**：§4.6 第 8、9 项；`master_template_verification.md` 自己写着「数据已备，Campaign 仍读独立 CSV」；『产品族名』已回填 **39/39**（FG 17 + SFN 22）。

**`CampaignFamily` 的真实定义（`src/campaign.py:10-20`，实测照抄）**：

```python
@dataclass(frozen=True)   # 实际装饰器以文件为准
class CampaignFamily:
    site: str
    part_no: str
    mold_id: str
    product_family: str
    color: str
    spec: str
    campaign_group: str
    sequence_priority: int
    note: str = ""
```

**9 个字段里只有 `campaign_group` 被消费。** 实测全仓引用：`scheduler.py:1819,1823`（`_add_campaign_penalty`）与 `batching.py:74,98`（`getattr(family, "campaign_group", "")`）。`mold_id` / `color` / `spec` / `sequence_priority` / `note` **零消费者**——`batching.py:72` 用的 mold 是 `task.mold_id`，来自 `data_loader.py:830,888` 的 `param.mold_id or mold_map[...]`，与 `CampaignFamily` 无关。

**`campaign_group = product_family` 是实测等式，不是猜测。** `data/master/Campaign-Families.csv` 53 行里 `Campaign Group == Product Family` 命中 **53/53**；把 CSV 的 37 个 WL 料号与模板逐一对账，`product_family` **全部相同**。CSV 其余列全是常量或复制品：`Color` 恒为 `-`，`Sequence Priority` 恒为 `10`，`Spec` 就是品名描述，`Note` 恒为 `"模拟分组；真实颜色/规格待补"`——**CSV 自己就标着它是模拟数据**。

所以这次替换不是「换个数据源」，是**把一个自认模拟的 CSV 换成模板里已回填的真列**，并且顺带补上 CSV 缺的 2 个料号（`W268001`、`W268002` 在模板里有、CSV 里没有）。

⚠️ **但别把等价性测试当成对『产品族名』本身的验证。** `Specification/masterdata_simulation_notes.md` 第 5–7、10 条记着：模板的『产品族名』（FG 17/17、SFN 22/22、Routing 39/39）与『模具编号』**当初就是照 `Campaign-Families.csv` 回填的**。两个「源」不独立，模板是 CSV 的副本。所以下面那个对账测试必然通过，它证明的是「切换不会改变分组结果」，不是「分组数据是对的」。族划分的业务正确性仍是规格 §8.7 待业务确认的事项。

切换依然值得做，理由与数据正确性无关：**同一份数据只应有一个计划员会去编辑的落点。** 模板是那个落点，CSV 不是。

**本阶段是「废弃」不是「删除」。** CSV 仍有 6 处代码命中（`src/master_export.py:25`、`src/server.py:2194`、`tests/test_campaign.py:17,27,30`、`tests/test_capacity_kernel.py:172`），删除会连带打断 `test_capacity_kernel`（在 fast 列表里）。真正删文件留到阶段 4 的 Repository 落地后。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_campaign.py`：

```python
class TestCampaignReadsTheTemplate(unittest.TestCase):
    def test_campaign_group_comes_from_the_product_family_column(self):
        from src.campaign import campaign_families_from_rows

        families = campaign_families_from_rows(
            "WL",
            fg_rows=[{"part_no": "FG1", "product_family": "FAM_A",
                      "mold_id": "M1", "description": "d1"},
                     {"part_no": "FG2", "product_family": "FAM_A",
                      "mold_id": None, "description": "d2"},
                     {"part_no": "FG3", "product_family": None,
                      "mold_id": None, "description": "d3"}],
            sf_rows=[{"part_no": "SF1", "product_family": "FAM_B",
                      "mold_id": None, "description": "s1"}],
        )
        # campaign_group is the only field any consumer reads
        # (scheduler.py:1819,1823; batching.py:74,98).
        self.assertEqual(families["FG1"].campaign_group, "FAM_A")
        self.assertEqual(families["FG2"].campaign_group, "FAM_A")
        self.assertEqual(families["SF1"].campaign_group, "FAM_B")
        self.assertEqual(families["FG1"].product_family, "FAM_A")
        self.assertEqual(families["FG1"].mold_id, "M1")
        self.assertEqual(families["FG1"].site, "WL")
        self.assertNotIn("FG3", families)   # blank family -> not a campaign member

    def test_blank_family_is_not_invented(self):
        from src.campaign import campaign_families_from_rows
        self.assertEqual(campaign_families_from_rows(
            "WL", fg_rows=[{"part_no": "FG1", "product_family": "  "}], sf_rows=[]), {})

    def test_the_real_template_reproduces_the_csv_groups(self):
        # Switch-safety regression, NOT validation of the family data:
        # masterdata_simulation_notes.md items 5-7 and 10 record that the
        # template's 产品族名 was itself backfilled from this CSV, so the two
        # are copies.  What this proves is that retiring the CSV as the
        # primary source cannot change any campaign group.
        import csv as _csv
        from pathlib import Path
        from src.campaign import campaign_families_from_rows
        from src.excel_loader import read_master_rows

        root = Path(__file__).resolve().parents[1]
        rows = read_master_rows(str(root / "APS_DataSource_Template.xlsx"))
        families = campaign_families_from_rows(
            "WL", rows["parameters_fg"], rows["parameters_sfn"])
        with (root / "data/master/Campaign-Families.csv").open(
                encoding="utf-8-sig", newline="") as fh:
            for record in _csv.DictReader(fh):
                if record["Site"] != "WL":
                    continue
                part = record["Part No."]
                self.assertIn(part, families)
                self.assertEqual(families[part].campaign_group,
                                 record["Campaign Group"], part)

    def test_output_per_shift_columns_are_gone_from_the_active_schema(self):
        from src.master_schema import SHEET_BY_KEY
        for sheet_key in ("routing", "parameters_fg", "parameters_sfn"):
            active = {c.key for c in SHEET_BY_KEY[sheet_key].active_columns()}
            self.assertNotIn("output_per_shift", active, sheet_key)

    def test_fg_params_no_longer_duplicate_line_base(self):
        from src.master_schema import SHEET_BY_KEY
        active = {c.key for c in SHEET_BY_KEY["parameters_fg"].active_columns()}
        self.assertNotIn("hours_per_shift", active)
        self.assertNotIn("shifts_per_day", active)
```

**`test_the_real_template_reproduces_the_csv_groups` 是切换安全网**：它证明退役 CSV 主源不会改变任何分组结果。若它失败，说明模板的『产品族名』与 CSV 已经分叉（有人改了其中一边），**停下报告**，不要为了让测试过而放宽断言。

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_campaign -v`
Expected: FAIL — `ImportError: cannot import name 'campaign_families_from_rows'`

- [ ] **Step 3: 实现 `campaign_families_from_rows`**

`src/campaign.py`，在 `load_campaign_families`（`:23`）之前插入：

```python
def campaign_families_from_rows(site: str,
                                fg_rows: Sequence[dict],
                                sf_rows: Sequence[dict]) -> Dict[str, CampaignFamily]:
    """Build campaign families from the template's 产品族名 column.

    Spec 4.6 item 9.  The template has carried this column filled in for all
    39 parts since 2026-10 while the campaign code kept reading
    data/master/Campaign-Families.csv -- the master-data twin of defect A2.
    The CSV is itself self-declared mock data (every Note reads
    "模拟分组；真实颜色/规格待补") and its Campaign Group equals its Product
    Family on 53/53 rows, so product_family is the group.

    Only campaign_group has consumers (scheduler.py:1819,1823;
    batching.py:74,98).  color / spec / sequence_priority are constants or
    duplicates in the CSV and stay empty here; they get either a real source
    or a deletion in stage 2, not a fabricated one now.

    A blank family means the part is not a campaign member.  Never guessed.
    """
    families: Dict[str, CampaignFamily] = {}
    for row in list(fg_rows) + list(sf_rows):
        part_no = str(row.get("part_no") or "").strip()
        family = str(row.get("product_family") or "").strip()
        if not part_no or not family:
            continue
        families[part_no] = CampaignFamily(
            site=site,
            part_no=part_no,
            mold_id=str(row.get("mold_id") or "").strip(),
            product_family=family,
            color="",
            spec=str(row.get("description") or "").strip(),
            campaign_group=family,
            sequence_priority=0,
            note="from template 产品族名",
        )
    return families
```

`Sequence` 若未在 `src/campaign.py` 导入，补 `from typing import Dict, Optional, Sequence`（该文件已有 `Dict` / `Optional`）。

- [ ] **Step 4: 把调用点从 CSV 切到模板行**

`load_campaign_families` 在 `src/` 里只有**一个**调用点（实测 `grep -rn "load_campaign_families" src/`：`server.py:59` 的 import 与 `server.py:2194` 的调用）。

`src/server.py:2193-2196` 现在是：

```python
    campaign_families = (
        load_campaign_families(os.path.join(DATA_DIR, "master", "Campaign-Families.csv"), site=payload.site.upper())
        if (campaign_enabled or campaign_batching_enabled) else {}
    )
```

改为：

```python
    campaign_families = {}
    if campaign_enabled or campaign_batching_enabled:
        site_code = payload.site.upper()
        campaign_families = campaign_families_from_rows(
            site_code,
            workbook_masters.get("parameters_fg") or [],
            workbook_masters.get("parameters_sfn") or [],
        ) or load_campaign_families(
            os.path.join(DATA_DIR, "master", "Campaign-Families.csv"), site=site_code)
```

`workbook_masters` 已在同一函数 `run_solver`（`server.py:1811`）的 `:1953` 无条件定义（无工作簿时为 `{}`），所以这里不需要再读一次工作簿。

`src/server.py:59` 的 import 改为：

```python
from src.campaign import campaign_families_from_rows, load_campaign_families
```

**保留 CSV 回退**，只在模板两列都空时触发；`load_campaign_families` 的 docstring 加一句 `Fallback only: the template's 产品族名 column is the primary source (spec 4.6 item 9).`

`tests/test_campaign.py` 与 `tests/test_capacity_kernel.py:171` 现有对 `load_campaign_families` 的调用**不要动**——回退路径必须继续被测到，否则它会静默腐烂。

- [ ] **Step 5: 把三个 `output_per_shift` 列与 FG 的两个班次列标为 deprecated**

`src/master_schema.py`：

```python
# PARAMETERS_FG (:159-160)
Column("hours_per_shift", "单班工时 (小时)", ("No. of hours/shift",),
       kind="number", deprecated=True,
       note="灰色列；统一由产线基础数据维护（规格 4.6 #8）"),
Column("shifts_per_day", "每天班次数", ("No. of shift/day"),
       kind="int", deprecated=True,
       note="灰色列；统一由产线基础数据维护（规格 4.6 #8）"),
# PARAMETERS_FG (:173-174) 与 PARAMETERS_SFN (:212-213) 与 ROUTING (:249)
Column("output_per_shift", "单班定额产能 (件)", ..., kind="number", deprecated=True,
       note="灰色列；由 CT × TRP × 单班工时自动计算（规格 4.6 #8）"),
```

**`deprecated=True` 的列仍可被别名解析读到**（`column_positions:485-495` 不区分），所以 legacy 25 表工作簿不会因此坏掉——这正是 deprecated 标记的用途。但 `active_columns()` 会排除它们，于是模板下载、UI 列、DDL 生成都不会再出现。

**注意与任务 8 的交互**：任务 8 Step 4 里 `_number(table.val(row, "hours_per_shift"), 0.0)` 读的是 FG 参数表的该列。标 deprecated 后这条回退路径仍然可用（`val` 按别名解析），但**它现在是最后手段**，主来源是 `line_base`。保持任务 8 写的优先级顺序不变：`line_base` → 参数表显式列 → gap。

- [ ] **Step 6: 更新填写说明**

`src/instructions.py:201` 那行 `("routing", "output_per_shift")` 的说明改为标注该列已废弃；同时给两张新表补说明行（`line_headcount` / `part_pairing` 的每一列）。

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/rebuild_instructions.py`
（该脚本导入 live `src.instructions`，是模板填写说明页的生成器）

- [ ] **Step 7: 跑测试 + 模板对账**

`run_tests.py:32-40` 的 `fast_modules` 追加 `"tests.test_campaign"`（它现在承担切换安全网，必须进 fast 门槛；实测该模块只读 CSV 与模板，秒级）。

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_campaign -v`
Expected: PASS（既有 3 项 + 新增 5 项）

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast`
Expected: 全绿

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verify_master_template.py`
Expected: `master_template_verification.md` 里 `output_per_shift` 与 `shift_columns_fg` 两条 ⚠️ 建议删除项消失或转为「已废弃」

- [ ] **Step 8: 提交**

```bash
git checkout -- data/snapshot_WL.json 2>/dev/null || true
git add src/master_schema.py src/campaign.py src/instructions.py src/server.py \
        run_tests.py tests/test_campaign.py \
        Specification/master_template_verification.md
git commit -m "feat: campaign reads the template's product family column; deprecate duplicate columns

Spec 4.6 items 8 and 9. output_per_shift is derivable from CT x TRP x
hours_per_shift, and FG's hours/shifts duplicated line_base -- both are now
gray columns, still readable by alias for legacy workbooks but excluded from
active_columns(), so template download, UI and DDL generation stop showing them.

Campaign-Families.csv is deprecated as the primary source, not deleted:
master_export.py and four test call sites still read it as a fallback. Note
the template's product family column was itself backfilled from this CSV
(masterdata_simulation_notes.md items 5-7, 10), so the new equivalence test
is a switch-safety net, not validation of the family data."
```

---

## 阶段 0 验收清单（规格 §8.2，逐条对应）

| # | 判据 | 验证方式 | 任务 |
|---|---|---|---|
| 0.1 | fast 测试全绿 | `run_tests.py --fast` 退出码 0（项数由 61 增至约 100+，因 fast 列表新增 6 个模块） | 全部 |
| 0.2 | schema 扩展未破坏既有表 | `python -m unittest tests.test_master_schema` 通过；`TEMPLATE_SHEET_COUNT` 15→17 | 2、3、4 |
| 0.3 | 种子脚本确定性 | 连跑两次 `--force`（中间 `git checkout --` 模板），md5 两次一致 | 6 |
| 0.4 | 种子脚本可拒绝 | 不带 `--force` 时不写盘，`git status` 干净 | 6 |
| 0.5 | 模拟数据行数符合规格 | `line_headcount` 恰 **297** 行（规格 rev.3 更正，原写 90）且 `source` 全 `simulated`；`part_pairing` paired 1 / unpaired 4 | 6 |
| 0.6 | loader 内不再有编造默认值 | 任务 7 Step 3 / 9 Step 3 / 10 Step 4 的三条 grep 均无输出 | 7、8、9、10 |
| 0.7 | 硬编码年份清除 | `grep -rn "datetime(2026" src/` 无输出 | 5 |
| 0.8 | B2 已解耦 | 超载数据集求解不再抛 `RuntimeError`，产出带延误的计划 | 12 |
| 0.9 | 基线变更有书面理由 | `git log tests/fixtures/wl_10d_baseline.json` 每条提交含「缺陷编号 + 预期方向 + 实际结果」 | 8、12 |

**追加判据（本计划新增，规格未列）：**

| # | 判据 | 验证方式 |
|---|---|---|
| 0.10 | dataclass 字段默认值也不再编造 | `grep -n "trp: float = 0.71\|changeover_time_min: float = 30.0\|hours_per_shift: float = 8.0" src/data_loader.py` 无输出（偏差 4） |
| 0.11 | CSV 回退路径与 xlsx 路径产同样的 gap | `tests.test_loader_gaps.TestCsvPathAlsoReportsGaps` 通过（任务 10） |
| 0.12 | 11 处年份站点全部收敛 | `grep -rn "datetime(DEFAULT_PLANNING_YEAR\|datetime(year" src/` 只在 `planning_calendar.py` 出现（偏差 3） |

---

## 已知限制（不在本阶段修，记录以免被误认为遗漏）

1. **种子脚本 `--force` 不幂等**——重复执行会追加重复行。规格 §5.12 未要求幂等；真正实现要等阶段 4 的 Repository（那时是 upsert 而不是 append）。
2. **`changeover_time_min` 缺失时取 0.0** 是缺陷 B3 的取值。阶段 0 故意让它可见（gap + 明确警告文案）；§4.4 的五源优先级裁决在阶段 2 落地。
3. **`line_headcount` / `part_pairing` 两张表本阶段只写入模板，还没有消费者。** HC-15 / SC-10 / HC-08 的指标在阶段 2 才建；`PAIR_SHIFT` 维度也在那时加。阶段 0 交付的是数据可用性，不是规则生效。
4. **外键只报告不强制**（`validate_references`）。规格 §5.9 事实 3：8 个 BOM 子件料号不在 `parameters_sfn` 中，DDL 一开始就 `ENFORCED` 会插入失败。阶段 4 决定何时转强制。
5. **`demand` 宽表的 unpivot 未实现**，所以 `demand` 的 `primary_key` 里的 `demand_date` 本阶段是名义声明。阶段 4 落地。
6. **`read_master_rows`（`excel_loader.py:645`）仍然读不到日期轴**——它只遍历 `spec.columns`，`date_axis=True` 的动态列被整个丢掉（偏差 7）。阶段 0 只在种子脚本里绕过它（直接用 `_demand_dates`），**没有修 `read_master_rows` 本身**：修它等于实现 unpivot，那是第 5 条，属阶段 4。在那之前，任何需要日期轴的代码都得自己走 `_demand_header_row` + `_demand_dates`。
7. **`CampaignFamily` 保留 4 个既无模板来源也无消费者的字段**（`color` / `spec` / `sequence_priority` / `note`，任务 13）。CSV 里它们分别是常量 `-`、品名描述副本、常量 `10`、常量「模拟分组」。阶段 0 填空值/零值而**不删字段**——删字段会动 `tests/test_campaign.py` 与 `tests/test_capacity_kernel.py` 的既有断言，超出本阶段范围。是补真来源还是删掉，阶段 2 随规则集重设计一起裁决。
8. **`data/master/Campaign-Families.csv` 是「废弃」不是「删除」**（任务 13）。仍有 6 处代码命中：`src/master_export.py:25`（导出时给空 `product_family`/`mold_id` 兜底）、`src/server.py:2194`（回退分支）、`tests/test_campaign.py:17,27,30`、`tests/test_capacity_kernel.py:172`。删文件要等阶段 4 的 Repository。
9. **模板『产品族名』与『模具编号』当初就是照这个 CSV 回填的**（`Specification/masterdata_simulation_notes.md` 第 5–7、10 条）。任务 13 的切换因此**不改变任何分组结果**，也不验证族划分的业务正确性——后者仍是规格 §8.7 待业务确认项。

---

## 阶段 1 计划的前提

阶段 1（双轨收敛，规格 §8.3）**另出一份计划**，不在本文档内。理由：

- 阶段 0 重写了 `excel_loader.py` / `data_loader.py` / `scheduler.py` 的大片区域，阶段 1 引用的 `file:line`（如 `scheduler.py:1657` 的 REG-01 账本、`:2563` 的 `_legacy_capacity_body`）在阶段 0 之后必然失准
- 规格附录 D.4 明确要求：符号找不到或逻辑与描述不符时**停下报告**，而不是按过期行号猜测

阶段 0 全部合并后，重新采集阶段 1 的 7 个模板的 legacy 与 kernel 双侧行号，再写那份计划。

