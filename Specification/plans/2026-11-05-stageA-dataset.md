# Stage A + Stage B：数据集底座与 CP-SAT 求解器端到端实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立全新的 `aps/` 包，以 `APS_DataSource_Template - WL -V2.xlsx`（包含数据残缺与全零列）为基础，导入为规范化 SQLite 数据集并通过 L1 四道校验门；通过 BOM 展开与库存核销自动派生成品（FG）与半成品（SF）排产任务；通过 OR-Tools CP-SAT 核心求解器执行九条核心物理与产能硬约束，按四层字典序分步求解，产出标准 `PlanRow` 调度结果并导出生产计划 Excel，实现从残缺模板输入到求解排产落地的完整端到端闭环。

**Architecture:** 
1. **数据层（Stage A）**：单一实体登记表（`aps/schema/registry.py`）是唯一真相源，驱动 DDL、类型解析、校验门与 V3 模板；Excel 适配器按表头名定位列，产出 `RawTables`；四道校验门产出 `ValidationReport`，容忍残缺数据并执行确定性派生与三级回退；整站快照存入 SQLite；`DataSet` 聚合根在内存中完成 BOM MRP 展开、净需求与半成品任务生成、日历槽位与毫秒标度冻结。
2. **求解层（Stage B）**：基于 OR-Tools CP-SAT，通过强类型 `StructureContext` 构建时间轴槽位上的决策变量（`assign`、`x`、`active`、`started`、`cum`、`load`、`open`、`tail`）；对每一条硬约束配备 assumption literal 实现 Infeasible 诊断；通过 P1（交付延误）→ P2（产能欠载与超负荷）→ P3（换型连续块）→ P4（在制品与安全库存）四层严格字典序与 Bound Freezing 求解；结果投影为标准 `PlanRow` DTO 并导出为 `Production_Plan.xlsx`；CLI 提供 `aps solve` 端到端排产命令。

**Tech Stack:** Python 3.12 stdlib + `openpyxl`（读写 Excel）+ `sqlite3`（存储）+ `ortools`（CP-SAT 求解器）。测试用 stdlib `unittest`。

**Spec:** `Specification/aps_greenfield_rewrite.md`（权威设计规范，G1–G29 决策登记；执行者必须同时阅读 spec §3/§4/§5/§6/§7/§9/§10/§11）

## Global Constraints

以下约束对**每一个任务**都生效，任务正文不再重复：

- **G4 零新增依赖**：`requirements.txt` 不得修改。只能用 stdlib + `openpyxl` + `ortools` + `fastapi`/`uvicorn`/`python-multipart`（Web 后置阶段用）。**禁止** `pandas`、`pyarrow`、`pydantic`、`sqlalchemy`、`pytest`、`attrs`、`polars`。
- **测试框架只用 stdlib `unittest`**：`pytest` **未安装**。禁止 `import pytest`、禁止 `@pytest.mark`、禁止 `pytest.raises`。断言用 `self.assertEqual` / `self.assertRaises` / `self.assertTrue`。
- **新测试一律放 `aps_tests/`**（不是 `tests/`）。`tests/` 目录里现存 42 个旧套件，在 Stage F2 之前必须保持原样、必须继续能被发现。Stage F2 才把 `aps_tests/` 改名为 `tests/`。
- **Python 解释器**：所有命令用 `.venv/Scripts/python.exe`，**并且**前置 `PYTHONIOENCODING=utf-8`。Windows 默认 cp936 会在打印中文表头时抛 `UnicodeEncodeError`，这不是代码 bug。标准形态：
  ```bash
  PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_xxx -v
  ```
- **G3 禁止 SQLite 方言泄漏**：DDL 只用 `TEXT` / `INTEGER` / `REAL` / `NUMERIC` 四种类型；禁止 `AUTOINCREMENT`、禁止隐式 rowid 依赖、禁止 `INSERT OR REPLACE`、禁止 SQLite 专有 `ON CONFLICT` 子句。写库只用参数化 `INSERT` / `UPDATE` / `DELETE`。每个连接建立后必须执行 `PRAGMA foreign_keys = ON`。Repository 必须是接口（Protocol），SQLite 实现是它的一个具体类——Postgres 端口保留。
- **G6 整站快照替换**：导入语义是「一个事务内 delete-then-insert 整站数据」，不做增量 merge。
- **G20 确定性**：所有产出必须可复现。不得依赖 dict 迭代顺序之外的隐式顺序；凡是从数据库或 Excel 读出的集合，排序键必须显式写死（`ORDER BY` 或 `sorted(...)`）。
- **G21 provenance 只有两个值**：`template`（来自 Excel 单元格）或 `derived`（由代码计算）。没有 `simulated`。
- **G8 契约层级只有 T1 / T2**：T1 = 缺数据即 `error`（导入失败）；T2 = 缺数据即 `warn` + 该字段标记 `degraded`（导入继续）。T3 已通过扩展模板消灭，不存在。
- **G13 行有效性按主键列判定**：一张表某行是否算「有数据」，看它的 PK 列是否非空，**不是**看「任意单元格非空」。实测 `Parameters-FG` 960 行中只有 64 行有效，其余 895 行仅在 `最小生产量 MOQ` 列有个 `0`——按「任意非空」判定会凭空造出 895 个幽灵料号。
- **代码约定（spec §11.4）**：全英文标识符；中文只出现在面向用户的消息文本与 Excel 表头常量里；类型注解齐全；`from __future__ import annotations` 放在每个新模块首行；不写解释「做了什么」的注释，只在「为什么」不明显处写一行。
- **提交粒度**：每个任务结束时提交一次。提交信息用 Conventional Commits 中文描述，形如 `feat(schema): 建立实体登记表与契约层级系统`。**只提交到本地分支 `refactor/dataset-ortools`，禁止 push，禁止触碰 remote。**

## 模板实测事实（执行前必读，不得凭猜测写列名）

数据源：`APS_DataSource_Template - WL -V2.xlsx`（仓库根目录）。以下是**实测**结果，计划中所有列名常量必须与此逐字一致：

| Sheet 名（逐字） | max_row | 表头行 | 有效数据行 |
|---|---|---|---|
| `填写说明 Instructions` | 221 | 无表头 | 不参与导入 |
| `客户需求订单清单 Customer Demand` | 2117 | 1 | 见下 |
| `期初可用库存 Initial Stock` | 4358 | 1 | 4357 |
| `关键物料到货 Material-Arrival-ETA` | 8 | 1 | 1 |
| `产线与成品参数 Parameters-FG` | 960 | 1 | **64** |
| `半成品制造参数 Parameters-SFN` | 959 | 1 | **55** |
| `产品装配清单 BOM List` | 258 | 1 | 125 |
| `工艺路线与候选机台 Routing-Machines` | 2920 | 1 | **125** |
| `换模矩阵 Setup-Matrix` | 38 | 1 | 37 |
| `库存策略 Inventory-Policy` | 918 | 1 | 917 |
| `工序提前期 Operation-LeadTime` | 945 | 1 | 944 |
| `工装模具约束 Tooling-Mold` | 1000 | 1 | **16** |
| `产线基础数据` | 999 | 1 | **17** |
| `设备预防维护 Preventive-Maintenance` | 18 | 1 | 11 |
| `客户战略评级 Customer-Priority` | 22 | 1 | 21 |

**逐字表头**（导入器只认这些字符串）：

- `客户需求订单清单 Customer Demand`：`站点`、`料号 (PN)`、`品名描述`，之后 **34 个日期列**，表头单元格是 **`datetime` 对象而非字符串**，范围 `2026-11-03` ~ `2026-12-06`（连续每日）。列总数 39（尾部有空列）。
- `期初可用库存 Initial Stock`：`站点`、`物料类型`、`物料料号 (PN)`、`品名描述`、`期初库存 (件)`
- `关键物料到货 Material-Arrival-ETA`：`站点`、`合格供应商`、`物料料号 (PN)`、`物料描述`、`预计入库日期`、`预计入库班次`、`预计入库时间`、`送达数量 (件)`
- `产线与成品参数 Parameters-FG`：`站点`、`成品料号 (PN)`、`品名描述`、`最小包装量 MPQ`、`单班工时 (小时)`、`每天班次数`、`最小生产量 MOQ`、`产品族名`、`模具编号 (Mold ID)`、`特殊班次`、`同时生产`
- `半成品制造参数 Parameters-SFN`：`站点`、`半成品料号 (PN)`、`品名描述`、`最小起投批量 MPQ`、`报废率 (小数)`、`最大库存 (件)`、`最小起订量 MOQ`、`产品族名`、`模具编号 (Mold ID)`、`特殊班次`、`同时生产`
- `产品装配清单 BOM List`：`站点`、`母件：成品料号 (FG PN)`、`母件品名描述`、`子件：半成品料号 (SF PN)`、`子件品名描述`、`装配单耗比例`
- `工艺路线与候选机台 Routing-Machines`：`站点`、`料号 (PN)`、`阶段`、`工序序号`、`工序名称`、`主候选线`、`认证`、`CT (秒)`、`换型时间 (分钟)`、`优先级`、`生效日期`、`失效日期`、`单班定额产能 (件)`、`人数`、`产品族名`、`模具清单`
- `换模矩阵 Setup-Matrix`：`站点`、`产线`、`起始模具`、`目标模具`、`换模工时 (分钟)`、`所需技术员数`、`是否需要清洁`、`生效日期`
- `库存策略 Inventory-Policy`：`站点`、`料号 (PN)`、`周期类型`、`起始日 (Day)`、`结束日 (Day)`、`目标库存天数`、`最大库存天数`、`安全库存 (件)`、`最大库存 (件)`、`生效日期`
- `工序提前期 Operation-LeadTime`：`站点`、`料号 (PN)`、`阶段`、`工序序号`、`工序名称`、`提前准备时间（分钟）`、`转运批量 (件)`、`容器`
  - ⚠️ **`提前准备时间（分钟）` 用的是全角括号 `（）`**，其余所有 sheet 用半角 `()`。这是模板的既有不一致，V3 会修正，但 V2 导入器必须两个都认（见 Task 5 的别名机制）。
- `工装模具约束 Tooling-Mold`：`站点`、`模具编号 (Mold ID)`、`工装模具名称`、`适用产线/设备`、`适用零件料号`、`可用套数`、`换模工时 (分钟)`
  - `适用零件料号` 与 `适用产线/设备` 是**逗号分隔多值列**，导入时必须 split。
- `产线基础数据`：`产线`、`效率 TRP`、`单班工时 (小时)`、`每天班次数`
  - ⚠️ **此表没有 `站点` 列**。`line.site_code` 必须从导入上下文（CLI 的 `--site WL`）注入。
- `设备预防维护 Preventive-Maintenance`：`站点`、`产线`、`维保项目试制内容`、`开始日期`、`开始时间`、`结束日期`、`结束时间`、`停机工时 (小时)`、`期间产能 (%)`、`工单处置策略`
- `客户战略评级 Customer-Priority`：`站点`、`客户代码 (Code)`、`客户全称`、`战略评级 (Tier)`、`保供加权系数`、`OTD 准时交付目标 (%)`、`交期浮动容差 (天)`、`产能紧张外协策略`、`代表配装成品`

**实测枚举值与解析陷阱**：

- `Routing-Machines.认证` 的值是**字符串** `'True'`，不是布尔。
- `Preventive-Maintenance.工单处置策略` = `'避让顺延 (Shift_Delay)'`——「中文标签 + 空格 + 半角括号内代码」模式，取括号内代码。
- `Customer-Priority.战略评级 (Tier)` = `'TIER_A (核心A级)'`——同上模式，但这次**代码在括号外**、中文在括号内。两个方向都要能处理。
- `Material-Arrival-ETA.预计入库班次` = `'早班'`，需映射到班次序号（早班→0，中班→1，晚班→2；SY38 两班制下 早班→0，晚班→1）。
- `Parameters-FG.特殊班次` 存在一行值为 `'6:30-18:30'`（时间区间，不是班次名）。T2，无法解析即 warn + `degraded`。
- `Initial Stock.物料类型` 词表：`COMP` / `SFIN` / `PACK` / `FING`。`Routing-Machines.阶段` 词表：`FG` / `SFIN`。**两套词表不一致**，归一化映射：`FING→FG`、`SFIN→SF`、`FG→FG`、`COMP→COMP`、`PACK→PACK`。
- `产线基础数据` 有 **17** 条产线，是产线词表的**权威超集**。实测 Routing 出现 15 条、Setup-Matrix 17 条、PM 11 条、Tooling 15 条，全部是子集 → 引用完整性门可以零 error 通过。`FIRSTP转子1#` 与 `OLDLINKAGE` 是闲置产线（合法，不报错）。
- 模具：FG 用到 3 个 + SF 用到 6 个 ⊆ Tooling 的 9 个，零孤儿。
- **全工作簿唯一的孤儿引用**：`BOM List` 的子件 `NCICLABEL01` 不在任何 part 表中（它同时也是 `Material-Arrival-ETA` 唯一那一行的料号）。按 T2 处理 → warn，不阻断。

**实测零填充列（全部 T2）**：

| 列 | 非空/总数 |
|---|---|
| `Routing-Machines.单班定额产能 (件)` | 0 / 125 |
| `Parameters-FG.同时生产` | 0 / 959 |
| `Parameters-SFN.最大库存 (件)` | 0 / 55 |
| `Parameters-SFN.特殊班次` | 0 / 55 |
| `Parameters-SFN.同时生产` | 0 / 55 |
| `Material-Arrival-ETA.预计入库时间` | 0 / 1 |
| `Routing-Machines.模具清单` | 121 / 125（4 个空值全是 `Felx转子#2线` 上的 SFIN 件：`W000027302`、`W000027302A`、`W000026607`、`W000046242L`） |

因 `单班定额产能 (件)` 全零，**`per_shift_cap` 必须派生**：`per_shift_cap(t,l) = floor(shift_hours(l) × 3600 × TRP(l) / CT_sec(t,l))`，provenance = `derived`。

**实测重复主键异常（三起，裁决已定）**：

1. `Initial Stock`：**87 个 PN 重复出现**，全部同一 `物料类型`，其中 **81 个数量不同**（例：`58113447` / `COMP` 出现两次，252 与 3888）。
   → **裁决**：PK = `(site_code, pn, material_type)`；同 PK 重复 = **数量求和 + warn**，warn 消息里必须列出原始 Excel 行号。
2. `Operation-LeadTime`：`(WCA1927, 10)` 出现 **38 次**，所有字段逐字节相同。
   → **裁决**：同 PK 且所有非 PK 字段完全相同 = **去重保留一行 + warn**；同 PK 但任一非 PK 字段不同 = **error**（无法裁决哪个对）。
3. `Tooling-Mold`：`(mold_id, line)` 组合唯一，**零重复**。
   → **裁决**：PK = `(site_code, mold_id, line_code)`，`dup_policy = error`。

**实测需求宽表规模**：34 个日期列共 **257** 个正数 `(PN, date)` 对；落在 10 天窗口（`2026-11-03` ~ `2026-11-12`）内的只有 **10 列 / 94 对**。
→ **裁决**：窗口过滤属于 `DataSet` 层职责，**不属于校验门**。引用完整性门**不得**因为 `demand_date` 超出窗口而报 error。

**实测料号全集**：`Initial Stock` 有 **4173** 个 PN 不在 `Parameters-FG ∪ Parameters-SFN` 中。
→ **裁决**：`part` 实体必须由**并集**构建：`Parameters-FG` ∪ `Parameters-SFN` ∪ `Initial Stock` ∪ `BOM List`（母件+子件）∪ `Routing-Machines` ∪ `Customer Demand`。引用完整性门**不得**要求 `initial_stock.pn ∈ part(stage ∈ {FG, SF})`。

## File Structure

```text
aps/                                  # 新包（与 src/ 并存，Stage F2 才替换）
├── __init__.py
├── schema/
│   ├── __init__.py                   # re-export REGISTRY, EntitySpec, FieldSpec, DataType, ContractTier
│   ├── types.py                      # DataType / ContractTier / DupPolicy 枚举 + 标量解析器
│   ├── registry.py                   # FieldSpec / EntitySpec / REGISTRY（19 实体声明）
│   └── ddl.py                        # create_ddl(spec) / create_all_ddl()
├── data/
│   ├── __init__.py
│   ├── repository.py                 # Repository Protocol
│   ├── sqlite.py                     # SqliteRepository
│   ├── excel.py                      # read_workbook() -> RawTables
│   ├── validate.py                   # Issue / Severity / ValidationReport / validate()
│   ├── importer.py                   # import_workbook() 整站快照替换编排
│   └── template_v3.py                # write_v3_template()
├── calendar/
│   ├── __init__.py
│   ├── geometry.py                   # SlotKey / build_slots()
│   └── pattern.py                    # CalendarMode / recommend_mode() / working_days()
├── dataset.py                        # Horizon / Task / Slot / DataSet
└── cli.py                            # argparse 子命令：import / verify-template / show

aps_tests/                            # 新测试目录（Stage F2 改名 tests/）
├── __init__.py
├── fixtures/
│   └── wl_v3_baseline.json           # Task 10 产出
├── test_schema_registry.py
├── test_schema_types.py
├── test_schema_ddl.py
├── test_repository_sqlite.py
├── test_excel_adapter.py
├── test_validate_gates.py
├── test_calendar.py
├── test_dataset.py
├── test_template_v3.py
├── test_cli_import.py
└── test_l1_gate.py                   # L1 数据门验收（端到端）

run_tests.py                          # Stage A0 重写：双目录发现 + FAST 常量
```

职责边界：`schema/` 只做声明与 DDL（不知道 Excel、不知道 SQLite 连接）；`data/excel.py` 只做「工作簿 → dict 行」（不知道校验规则）；`data/validate.py` 只做「dict 行 → Issue 列表」（不写库）；`data/importer.py` 是唯一编排者；`dataset.py` 只做「已入库数据 → 冻结派生量」（不知道 Excel）。

---

## Task 1: A0 脚手架与双目录测试运行器

**Files:**
- Create: `aps/__init__.py`, `aps/schema/__init__.py`, `aps/data/__init__.py`, `aps/calendar/__init__.py`
- Create: `aps_tests/__init__.py`, `aps_tests/fixtures/.gitkeep`
- Modify: `run_tests.py`（整文件重写）
- Test: `aps_tests/test_smoke.py`

**Interfaces:**
- Consumes: 无（第一个任务）
- Produces:
  - `aps` 包可被 `import aps` / `from aps.schema import ...` 正常导入
  - `run_tests.py` 支持 `python run_tests.py` / `--fast` / `--new` / `--legacy` 四种调用
  - 约定：任何测试模块顶层写 `FAST = True` 即被 `--fast` 选中

- [ ] **Step 1: 写失败的冒烟测试**

创建 `aps_tests/test_smoke.py`：

```python
from __future__ import annotations

import importlib
import unittest

FAST = True


class TestPackageScaffold(unittest.TestCase):
    def test_aps_package_importable(self):
        mod = importlib.import_module("aps")
        self.assertTrue(hasattr(mod, "__version__"))

    def test_subpackages_importable(self):
        for name in ("aps.schema", "aps.data", "aps.calendar"):
            with self.subTest(pkg=name):
                importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_smoke -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'aps_tests'` 或 `No module named 'aps'`

- [ ] **Step 3: 建目录与 `__init__.py`**

`aps/__init__.py`：

```python
from __future__ import annotations

__version__ = "2.0.0a1"
```

`aps/schema/__init__.py`、`aps/data/__init__.py`、`aps/calendar/__init__.py` 三个文件内容相同：

```python
from __future__ import annotations
```

`aps_tests/__init__.py`：

```python
from __future__ import annotations
```

再建空目录占位 `aps_tests/fixtures/.gitkeep`（空文件）。

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_smoke -v`
Expected: PASS (2 tests)

- [ ] **Step 5: 重写 `run_tests.py`**

用下面内容**整体替换** `run_tests.py`。要点：旧的 `--fast` 硬编码 7 个模块名，新实现改为「发现两个目录 + 按模块级 `FAST` 常量筛选」；`--legacy` 只跑 `tests/`，`--new` 只跑 `aps_tests/`，无参数跑两者。

```python
"""Unified test runner for APS.

Discovers suites under both ``aps_tests/`` (new architecture) and ``tests/``
(legacy, retired at Stage F2).

Usage:
    python run_tests.py             # both directories
    python run_tests.py --fast      # modules declaring FAST = True
    python run_tests.py --new       # aps_tests/ only
    python run_tests.py --legacy    # tests/ only
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

NEW_DIR = "aps_tests"
LEGACY_DIR = "tests"


def _discover(pkg: str) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    path = os.path.join(BASE_DIR, pkg)
    if not os.path.isdir(path):
        return unittest.TestSuite()
    return loader.discover(start_dir=path, pattern="test_*.py", top_level_dir=BASE_DIR)


def _is_fast(suite: unittest.TestSuite) -> bool:
    for test in suite:
        module = importlib.import_module(test.__class__.__module__)
        return bool(getattr(module, "FAST", False))
    return False


def _filter_fast(suite: unittest.TestSuite) -> unittest.TestSuite:
    kept = unittest.TestSuite()
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            kept.addTests(_filter_fast(test))
        else:
            module = importlib.import_module(test.__class__.__module__)
            if getattr(module, "FAST", False):
                kept.addTest(test)
    return kept


def main() -> None:
    parser = argparse.ArgumentParser(description="APS test runner")
    parser.add_argument("--fast", action="store_true", help="modules declaring FAST = True")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--new", action="store_true", help="aps_tests/ only")
    group.add_argument("--legacy", action="store_true", help="tests/ only")
    args = parser.parse_args()

    pkgs = []
    if not args.legacy:
        pkgs.append(NEW_DIR)
    if not args.new:
        pkgs.append(LEGACY_DIR)

    suite = unittest.TestSuite()
    for pkg in pkgs:
        suite.addTests(_discover(pkg))

    if args.fast:
        suite = _filter_fast(suite)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: 验证新运行器三个方向都工作**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --new`
Expected: PASS，只跑 `aps_tests`（2 tests）

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast`
Expected: PASS，只包含 `test_smoke`（因为它是唯一声明 `FAST = True` 的模块）

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --legacy --fast`
Expected: PASS，跑旧 `tests/` 里声明 `FAST` 的模块；旧模块都没有 `FAST` 常量，所以是 0 tests（`Ran 0 tests`，退出码 0）。这是**预期行为**，不是失败——旧的 7 模块快速集在 Stage F2 随旧代码一起退役。

- [ ] **Step 7: 确认旧全量测试没被破坏**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --legacy 2>&1 | tail -5`
Expected: `OK`，且计数与重构前一致（160 项）。**若数字下降，说明发现逻辑坏了，必须修好再提交。**

- [ ] **Step 8: 提交**

```bash
git add aps aps_tests run_tests.py
git commit -m "feat(bootstrap): 建立 aps 包脚手架与双目录测试运行器"
```

---

## Task 2: 类型系统与契约层级

**Files:**
- Create: `aps/schema/types.py`
- Test: `aps_tests/test_schema_types.py`

**Interfaces:**
- Consumes: 无
- Produces:
  ```python
  class DataType(str, Enum):
      TEXT = "TEXT"; INT = "INT"; REAL = "REAL"; BOOL = "BOOL"
      DATE = "DATE"; TIME = "TIME"; DATETIME = "DATETIME"
      MULTI_TEXT = "MULTI_TEXT"   # 逗号分隔多值列 → tuple[str, ...]

  class ContractTier(str, Enum):
      T1 = "T1"; T2 = "T2"

  class DupPolicy(str, Enum):
      ERROR = "error"; SUM = "sum"; DEDUPE = "dedupe"

  class ParseError(ValueError):
      def __init__(self, message: str, raw: Any) -> None: ...

  def parse_scalar(value: Any, dtype: DataType) -> Any
      # None / "" / 全空白 → None
      # TEXT       → str(value).strip()
      # INT        → int；接受 float 且 is_integer()，否则 ParseError
      # REAL       → float
      # BOOL       → True/False/1/0/'True'/'true'/'是'/'Y' → True；'False'/'否'/'N'/0 → False
      # DATE       → datetime.date；接受 date/datetime/str('YYYY-MM-DD' 或 'YYYY/MM/DD')
      # TIME       → datetime.time；接受 time/str('HH:MM' / 'HH:MM:SS')
      # DATETIME   → datetime.datetime；接受 datetime/str
      # MULTI_TEXT → tuple[str, ...]；按 ',' 或 '，' 分隔，去空白，丢空项

  def parse_enum_code(value: Any) -> str | None
      # 提取「中文标签 + 括号代码」的代码部分，两个方向都支持：
      #   '避让顺延 (Shift_Delay)' → 'Shift_Delay'
      #   'TIER_A (核心A级)'       → 'TIER_A'
      #   'Shift_Delay'            → 'Shift_Delay'
      #   '早班'                    → None（无括号，非 ASCII 代码）
      # 规则：若含 '(' 与 ')'，取括号内内容；若括号内包含非 ASCII 字符而括号外全 ASCII，则改取括号外部分；无括号时，全 ASCII 则原样返回，否则 None。

  SHIFT_NAME_TO_INDEX: dict[str, int] = {"早班": 0, "中班": 1, "晚班": 2, "白班": 0, "夜班": 1}
  def parse_shift_index(value: Any) -> int | None   # 走 SHIFT_NAME_TO_INDEX；INT 直接透传
  ```

- [ ] **Step 1: 写失败测试**

创建 `aps_tests/test_schema_types.py`：

```python
from __future__ import annotations

import datetime as dt
import unittest

from aps.schema.types import (
    BOOL_TRUE,
    ContractTier,
    DataType,
    DupPolicy,
    ParseError,
    SHIFT_NAME_TO_INDEX,
    parse_enum_code,
    parse_scalar,
    parse_shift_index,
)

FAST = True


class TestParseScalar(unittest.TestCase):
    def test_blank_becomes_none(self):
        for dtype in DataType:
            for blank in (None, "", "   "):
                with self.subTest(dtype=dtype, blank=blank):
                    self.assertIsNone(parse_scalar(blank, dtype))

    def test_text_strips(self):
        self.assertEqual(parse_scalar("  WL  ", DataType.TEXT), "WL")

    def test_int_accepts_integral_float(self):
        self.assertEqual(parse_scalar(48.0, DataType.INT), 48)

    def test_int_rejects_fractional_float(self):
        with self.assertRaises(ParseError):
            parse_scalar(48.5, DataType.INT)

    def test_int_rejects_non_numeric_text(self):
        with self.assertRaises(ParseError):
            parse_scalar("四十八", DataType.INT)

    def test_real(self):
        self.assertEqual(parse_scalar("0.85", DataType.REAL), 0.85)

    def test_bool_variants(self):
        for raw in (True, 1, "True", "true", "是", "Y"):
            with self.subTest(raw=raw):
                self.assertTrue(parse_scalar(raw, DataType.BOOL))
        for raw in (False, 0, "False", "否", "N"):
            with self.subTest(raw=raw):
                self.assertFalse(parse_scalar(raw, DataType.BOOL))

    def test_bool_rejects_garbage(self):
        with self.assertRaises(ParseError):
            parse_scalar("maybe", DataType.BOOL)

    def test_date_from_datetime_object(self):
        raw = dt.datetime(2026, 11, 3, 0, 0)
        self.assertEqual(parse_scalar(raw, DataType.DATE), dt.date(2026, 11, 3))

    def test_date_from_text(self):
        self.assertEqual(parse_scalar("2026-11-03", DataType.DATE), dt.date(2026, 11, 3))
        self.assertEqual(parse_scalar("2026/11/03", DataType.DATE), dt.date(2026, 11, 3))

    def test_time_from_text(self):
        self.assertEqual(parse_scalar("07:00", DataType.TIME), dt.time(7, 0))
        self.assertEqual(parse_scalar("23:00:00", DataType.TIME), dt.time(23, 0, 0))

    def test_time_rejects_range(self):
        # 实测 Parameters-FG.特殊班次 有一行 '6:30-18:30'，不是合法 TIME
        with self.assertRaises(ParseError):
            parse_scalar("6:30-18:30", DataType.TIME)

    def test_datetime(self):
        raw = "2026-11-03 07:00:00"
        self.assertEqual(parse_scalar(raw, DataType.DATETIME), dt.datetime(2026, 11, 3, 7, 0, 0))

    def test_multi_text_splits_both_commas(self):
        self.assertEqual(
            parse_scalar("M1, M2，M3,,", DataType.MULTI_TEXT),
            ("M1", "M2", "M3"),
        )

    def test_multi_text_single_value(self):
        self.assertEqual(parse_scalar("M1", DataType.MULTI_TEXT), ("M1",))


class TestParseEnumCode(unittest.TestCase):
    def test_code_inside_parens(self):
        self.assertEqual(parse_enum_code("避让顺延 (Shift_Delay)"), "Shift_Delay")

    def test_code_outside_parens(self):
        self.assertEqual(parse_enum_code("TIER_A (核心A级)"), "TIER_A")

    def test_bare_ascii_code(self):
        self.assertEqual(parse_enum_code("NORMAL"), "NORMAL")

    def test_bare_chinese_returns_none(self):
        self.assertIsNone(parse_enum_code("早班"))

    def test_none_passthrough(self):
        self.assertIsNone(parse_enum_code(None))


class TestShiftIndex(unittest.TestCase):
    def test_names(self):
        for name, idx in SHIFT_NAME_TO_INDEX.items():
            with self.subTest(name=name):
                self.assertEqual(parse_shift_index(name), idx)

    def test_int_passthrough(self):
        self.assertEqual(parse_shift_index(2), 2)

    def test_unknown_returns_none(self):
        self.assertIsNone(parse_shift_index("凌晨班"))


class TestEnums(unittest.TestCase):
    def test_tier_values(self):
        self.assertEqual([t.value for t in ContractTier], ["T1", "T2"])

    def test_dup_policy_values(self):
        self.assertEqual([p.value for p in DupPolicy], ["error", "sum", "dedupe"])

    def test_bool_true_set(self):
        self.assertIn("是", BOOL_TRUE)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_schema_types -v`
Expected: FAIL —— `ImportError: cannot import name 'ContractTier' from 'aps.schema.types'`（模块不存在）

- [ ] **Step 3: 实现 `aps/schema/types.py`**

```python
from __future__ import annotations

import datetime as dt
import re
from enum import Enum
from typing import Any

__all__ = [
    "BOOL_TRUE",
    "BOOL_FALSE",
    "ContractTier",
    "DataType",
    "DupPolicy",
    "ParseError",
    "SHIFT_NAME_TO_INDEX",
    "parse_enum_code",
    "parse_scalar",
    "parse_shift_index",
]


class DataType(str, Enum):
    TEXT = "TEXT"
    INT = "INT"
    REAL = "REAL"
    BOOL = "BOOL"
    DATE = "DATE"
    TIME = "TIME"
    DATETIME = "DATETIME"
    MULTI_TEXT = "MULTI_TEXT"


class ContractTier(str, Enum):
    T1 = "T1"
    T2 = "T2"


class DupPolicy(str, Enum):
    ERROR = "error"
    SUM = "sum"
    DEDUPE = "dedupe"


class ParseError(ValueError):
    def __init__(self, message: str, raw: Any) -> None:
        super().__init__(message)
        self.raw = raw


BOOL_TRUE = {"true", "1", "yes", "y", "是"}
BOOL_FALSE = {"false", "0", "no", "n", "否"}

SHIFT_NAME_TO_INDEX = {"早班": 0, "白班": 0, "中班": 1, "晚班": 2, "夜班": 1}

_PAREN_RE = re.compile(r"^(?P<outer>[^()]*)\((?P<inner>[^()]*)\)\s*$")
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d")
_TIME_FORMATS = ("%H:%M:%S", "%H:%M")
_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M")


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ParseError(f"期望整数，得到非整数浮点 {value!r}", value)
        return int(value)
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        number = float(text)
    except ValueError:
        raise ParseError(f"无法解析为整数: {value!r}", value) from None
    if not number.is_integer():
        raise ParseError(f"期望整数，得到非整数浮点 {value!r}", value)
    return int(number)


def _to_real(value: Any) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        raise ParseError(f"无法解析为实数: {value!r}", value) from None


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value in (0, 1):
            return bool(value)
        raise ParseError(f"无法解析为布尔: {value!r}", value)
    text = str(value).strip().lower()
    if text in BOOL_TRUE:
        return True
    if text in BOOL_FALSE:
        return False
    raise ParseError(f"无法解析为布尔: {value!r}", value)


def _from_formats(text: str, formats: tuple[str, ...], value: Any) -> Any:
    for fmt in formats:
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ParseError(f"无法解析日期/时间: {value!r}", value)


def _to_date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return _from_formats(str(value).strip(), _DATE_FORMATS, value).date()


def _to_time(value: Any) -> dt.time:
    if isinstance(value, dt.datetime):
        return value.time()
    if isinstance(value, dt.time):
        return value
    return _from_formats(str(value).strip(), _TIME_FORMATS, value).time()


def _to_datetime(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    text = str(value).strip()
    for fmt in _DATETIME_FORMATS + _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ParseError(f"无法解析为日期时间: {value!r}", value)


def _to_multi_text(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        items = [str(v).strip() for v in value]
    else:
        items = [p.strip() for p in re.split(r"[,，]", str(value))]
    return tuple(p for p in items if p)


def parse_scalar(value: Any, dtype: DataType) -> Any:
    if _is_blank(value):
        return None
    if dtype is DataType.TEXT:
        return str(value).strip()
    if dtype is DataType.INT:
        return _to_int(value)
    if dtype is DataType.REAL:
        return _to_real(value)
    if dtype is DataType.BOOL:
        return _to_bool(value)
    if dtype is DataType.DATE:
        return _to_date(value)
    if dtype is DataType.TIME:
        return _to_time(value)
    if dtype is DataType.DATETIME:
        return _to_datetime(value)
    if dtype is DataType.MULTI_TEXT:
        return _to_multi_text(value)
    raise ParseError(f"未知数据类型 {dtype!r}", value)


def _is_ascii(text: str) -> bool:
    return all(ord(ch) < 128 for ch in text)


def parse_enum_code(value: Any) -> str | None:
    if _is_blank(value):
        return None
    text = str(value).strip()
    match = _PAREN_RE.match(text)
    if match:
        outer = match.group("outer").strip()
        inner = match.group("inner").strip()
        if _is_ascii(inner) and inner:
            return inner
        if _is_ascii(outer) and outer:
            return outer
        return inner or outer or None
    if _is_ascii(text):
        return text
    return None


def parse_shift_index(value: Any) -> int | None:
    if _is_blank(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return SHIFT_NAME_TO_INDEX.get(str(value).strip())
```

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_schema_types -v`
Expected: PASS（22 tests）

- [ ] **Step 5: 提交**

```bash
git add aps/schema/types.py aps_tests/test_schema_types.py
git commit -m "feat(schema): 实现标量类型解析、枚举代码提取与契约层级枚举"
```

---

## Task 3: 实体登记表（19 核心实体 + 4 个 V3 扩展实体）

**Files:**
- Create: `aps/schema/registry.py`
- Modify: `aps/schema/__init__.py`（re-export）
- Modify: `Specification/aps_greenfield_rewrite.md`（记录 A-1 主键修订，见 Step 1）
- Test: `aps_tests/test_schema_registry.py`

**对 spec 的两处修订（本任务落地，不要 silently 偏离）**：

- **A-1 `initial_stock` 主键扩展**：spec §3.1 写 PK = `(site_code, pn)`，但实测 `Initial Stock` 有 **87 个 PN 重复**且全部同 `物料类型`，其中 81 个数量不同。若 PK 不含 `material_type`，同一 PN 的 COMP 与 PACK 库存会互相覆盖。**修订为 PK = `(site_code, pn, material_type)`，`dup_policy = sum`（同 PK 重复 → 数量求和 + warn）**。
- **A-2 登记表实体数 19 → 23**：spec §3.1 的「19」只统计 V2 派生实体。§3.5 的 V3 扩展新增 4 张 sheet（`班次人力可用性`、`产品配对关系`、`工作日历`、`换模技术员`）。因为登记表是 DDL 生成器与 V3 模板生成器的**唯一输入**（§4.1、§3.5），这 4 张表必须也登记为实体，否则模板生成器无处取表头。它们是 **`sheet_required = False`** 的 T2 实体：V2 工作簿里没有这些 sheet 是**合法的**，结构门只 warn。
- **A-3 `mold` 与 `tooling_mold` 共用同一张 sheet**：`工装模具约束 Tooling-Mold` 实测 16 行有效数据，`(mold_id, line_code)` 唯一，因此一个模具可以出现在多行（每行一条适用产线）。`tooling_mold` 实体按行照收；`mold` 维度实体必须按 `(site_code, mold_id)` **去重**，故 `dup_policy = dedupe`（各列逐字节相同 → 保留一行 + warn；任一列不同 → error，因为无法裁决该模具到底有几套）。
- **A-4 字段级 `parser` 声明**：spec §3.1 未规定解析钩子。实测三类值不能直接按声明类型解析：`物料类型`/`阶段` 需要走 `STAGE_NORMALIZE`；`预计入库班次` 的值是 `'早班'` 需要走班次名映射；`战略评级`/`工单处置策略` 是「中文标签 + 括号代码」需要提取代码。因此 `FieldSpec` 增加 `parser: str | None`，取值限 `PARSERS = ("enum_code", "stage", "shift_index")`；`parser` 为空但声明了 `enum_codes` 时隐式为 `"enum_code"`。类型门（Task 6）按 `resolved_parser` 分派，不做任何按字段名的硬编码分支。
- **A-5 字段级 `fk_optional` 声明**：spec §4.3 规定参照门一律 `error`，但实测全工作簿**唯一的孤儿引用**是 `BOM List` 的子件 `NCICLABEL01`（它不在任何 part 表中，同时也是 `Material-Arrival-ETA` 唯一那一行的料号）。若按 error 处理，真实 WL 工作簿永远无法通过 L1 门。因此 `FieldSpec` 增加 `fk_optional: bool = False`：为 `True` 时参照违规降级为 `warning`。只有 `bom.child_pn` 一处设为 `True`。

**Interfaces:**
- Consumes: `aps.schema.types` 的 `DataType` / `ContractTier` / `DupPolicy`
- Produces:
  ```python
  @dataclass(frozen=True)
  class FieldSpec:
      name: str                       # 规范英文字段名，也是 SQLite 列名
      dtype: DataType
      tier: ContractTier = ContractTier.T1
      source_columns: tuple[str, ...] = ()   # 逐字 Excel 表头；多个 = 别名（全角/半角括号等）
      nullable: bool = True
      pk: bool = False
      fk: str | None = None           # "entity.field" 形式，如 "part.pn"
      unit: str | None = None         # "件" / "秒" / "分钟" / "小时" / "%" / "小数"
      enum_codes: tuple[str, ...] | None = None   # 声明则解析后必须落在此集合内
      parser: str | None = None       # "enum_code" | "stage" | "shift_index" | None
                                      # None 且 enum_codes 非空 → 隐式 "enum_code"
      fk_optional: bool = False       # True → 参照违规降级为 warning（修订 A-5）
      provenance: str = "template"    # "template" | "derived"

  @dataclass(frozen=True)
  class EntitySpec:
      name: str                       # SQLite 表名
      sheet: str | None               # None = 由适配器合成（site/part/product_family/calendar_pattern）
      fields: tuple[FieldSpec, ...]
      pk: tuple[str, ...]             # 字段名元组
      dup_policy: DupPolicy = DupPolicy.ERROR
      sum_fields: tuple[str, ...] = ()          # dup_policy=SUM 时求和的字段
      sheet_required: bool = True
      unpivot: str | None = None                # "demand_date" = 宽表 unpivot 的日期轴字段
      site_injected: bool = False               # True = sheet 无「站点」列，从 CLI --site 注入
      synthesized: bool = False                 # True = 适配器从多表并集构建

      def field(self, name: str) -> FieldSpec: ...
      @property
      def pk_fields(self) -> tuple[FieldSpec, ...]: ...
      @property
      def header_to_field(self) -> dict[str, FieldSpec]: ...   # 逐字表头 → FieldSpec
      @property
      def required_headers(self) -> frozenset[str]: ...        # T1 字段的首选表头
      @property
      def deprecated_headers(self) -> frozenset[str]: ...      # §3.3 废弃列
      def validate_self(self) -> list[str]: ...                # 自检，返回违规描述

  REGISTRY: dict[str, EntitySpec]      # 23 条，按名字排序插入
  DEPRECATED_HEADERS: dict[str, tuple[str, ...]]   # sheet 名 → 废弃列名（§3.3）
  STAGE_NORMALIZE: dict[str, str]      # {"FING":"FG","SFIN":"SF","FG":"FG","COMP":"COMP","PACK":"PACK","RAW":"RAW"}
  def get_entity(name: str) -> EntitySpec
  def entities_with_sheet() -> tuple[EntitySpec, ...]
  def synthesized_entities() -> tuple[EntitySpec, ...]
  ```

- [ ] **Step 1: 把 A-1 / A-2 修订写回 spec**

在 `Specification/aps_greenfield_rewrite.md` §3.1 的「事实/主数据表（12）」表格下方，紧接 `「填写说明 Instructions」不参与排产，不建实体。` 这一行之后，插入：

```markdown
**Stage A 实施期修订（2026-11-05）**：

- **A-1**：`initial_stock` 主键实测修订为 `(site_code, pn, material_type)`。原 `(site_code, pn)` 会让同一 PN 的 COMP / PACK 库存互相覆盖——V2 模板实测 87 个 PN 重复且同物料类型，其中 81 个数量不同。`dup_policy = sum`：同 PK 重复行数量求和并 warn，warn 消息列出原始 Excel 行号。
- **A-2**：登记表实体数 19 → **23**。§3.5 的 4 张 V3 新 sheet（`班次人力可用性`、`产品配对关系`、`工作日历`、`换模技术员`）也必须登记，因为登记表是 DDL 生成器与 V3 模板生成器的唯一输入。它们 `sheet_required = False`：V2 工作簿缺这些 sheet 合法，结构门只 warn。
- **A-3**：`mold` 与 `tooling_mold` 共用 `工装模具约束 Tooling-Mold`。实测该表 16 行有效数据、`(mold_id, line_code)` 唯一，即同一模具可跨多行（每行一条适用产线）。`tooling_mold` 按行照收；`mold` 维度实体按 `(site_code, mold_id)` 去重，`dup_policy = dedupe`。
- **A-4**：`FieldSpec` 增加字段级 `parser` 声明，取值限 `("enum_code", "stage", "shift_index")`；`parser` 为空而 `enum_codes` 非空时隐式为 `"enum_code"`。落地实测的三类解析陷阱：`物料类型`/`阶段` 两套词表不一致（`FING`/`SFIN` vs `FG`/`SFIN`）、`预计入库班次` 值为 `'早班'`、`战略评级`/`工单处置策略` 为「中文标签 + 括号代码」。类型门按 `resolved_parser` 分派，不按字段名硬编码分支。
- **A-5**：`FieldSpec` 增加 `fk_optional: bool = False`。为 `True` 时参照门把该外键的违规降级为 `warning` 而非 `error`。仅 `bom.child_pn` 一处使用——实测全工作簿唯一孤儿引用是 BOM 子件 `NCICLABEL01`，按 error 处理会让真实 WL 工作簿永远过不了 L1 门。
```

- [ ] **Step 2: 写失败测试**

创建 `aps_tests/test_schema_registry.py`：

```python
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
```

- [ ] **Step 3: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_schema_registry -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'aps.schema.registry'`

- [ ] **Step 4: 实现 `aps/schema/registry.py`**

完整文件内容如下。**表头字符串必须逐字复制**（含全角括号、空格、冒号）。

```python
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
           enum_codes=("FG", "SF", "COMP", "PACK", "RAW")),
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
        _f("quantity", DataType.INT, "期初库存 (件)", nullable=False, unit="件"),
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
```

- [ ] **Step 5: 补 `aps/schema/__init__.py` 的 re-export**

```python
from __future__ import annotations

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
from aps.schema.types import (
    ContractTier,
    DataType,
    DupPolicy,
    ParseError,
    parse_enum_code,
    parse_scalar,
    parse_shift_index,
)

__all__ = [
    "DEPRECATED_HEADERS", "PARSERS", "REGISTRY", "STAGE_NORMALIZE", "EntitySpec", "FieldSpec",
    "entities_with_sheet", "get_entity", "synthesized_entities",
    "ContractTier", "DataType", "DupPolicy", "ParseError",
    "parse_enum_code", "parse_scalar", "parse_shift_index",
]
```

- [ ] **Step 6: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_schema_registry -v`
Expected: PASS（全部，无 failure / error）

`test_every_entity_passes_self_check` 是登记表自身的契约测试：它把 23 个实体逐个跑 `validate_self()`。任何后续任务往登记表加实体或字段，都必须让这条测试继续通过——这是「登记表不会自我矛盾」的机器保证。

- [ ] **Step 7: 提交**

```bash
git add aps/schema/registry.py aps/schema/__init__.py aps_tests/test_schema_registry.py \
        Specification/aps_greenfield_rewrite.md
git commit -m "feat(schema): 声明 23 个实体登记表并落地 A-1/A-2 修订"
```

---

## Task 4: DDL 生成器、Repository 端口与 SQLite 实现

**Files:**
- Create: `aps/schema/ddl.py`, `aps/data/repository.py`, `aps/data/sqlite.py`
- Test: `aps_tests/test_schema_ddl.py`, `aps_tests/test_repository_sqlite.py`

**Interfaces:**
- Consumes: `aps.schema.registry.REGISTRY` / `EntitySpec` / `FieldSpec`；`aps.schema.types.DataType`
- Produces:
  ```python
  # aps/schema/ddl.py
  SQL_TYPE: dict[DataType, str]     # TEXT→TEXT, INT→INTEGER, REAL→REAL, BOOL→INTEGER,
                                    # DATE→TEXT, TIME→TEXT, DATETIME→TEXT, MULTI_TEXT→TEXT
  def create_ddl(spec: EntitySpec) -> str
  def create_all_ddl() -> str       # 拓扑排序（被引用者先建）+ 索引 DDL，返回单个可执行脚本
  def create_indexes_ddl(spec: EntitySpec) -> str   # 每个非 PK 外键列一个索引

  # aps/data/repository.py
  EntityName = str
  Row = dict[str, Any]
  class Repository(Protocol):
      def list_by(self, entity: EntityName, /, **filters: Any) -> list[Row]: ...
      def get_by_pk(self, entity: EntityName, pk: tuple[Any, ...]) -> Row | None: ...
      def insert_many(self, entity: EntityName, rows: Sequence[Row]) -> int: ...
      def delete_where(self, entity: EntityName, /, **filters: Any) -> int: ...
      def count(self, entity: EntityName, /, **filters: Any) -> int: ...

  # aps/data/sqlite.py
  def encode_value(value: Any) -> Any       # Python → SQLite 亲和值
  def decode_value(raw: Any, dtype: DataType) -> Any   # SQLite → Python
  class SqliteRepository:                    # 实现 Repository
      def __init__(self, db_path: str | os.PathLike[str]) -> None
      def initialize_schema(self) -> None    # 执行 create_all_ddl()
      def transaction(self) -> ContextManager[None]   # 提交/回滚
      def replace_site(self, site_code: str, rows_by_entity: Mapping[str, Sequence[Row]]) -> dict[str, int]
      def close(self) -> None
      # 以及 list_by / get_by_pk / insert_many / delete_where / count
  ```
  注：spec §4.1 写的是 `upsert_many`。因为 G6 的导入语义是「delete-then-insert 整站替换」，**不存在真正的 upsert 需求**，而 `INSERT OR REPLACE` 又被 G3 禁止。落地为 `insert_many` + `replace_site`。这是对 §4.1 方法名的收窄，不改变语义。

**编码规则（写死）**：`BOOL → 0/1`；`DATE → "YYYY-MM-DD"`；`TIME → "HH:MM:SS"`；`DATETIME → "YYYY-MM-DD HH:MM:SS"`；`MULTI_TEXT → ",".join(...)`（半角逗号，无空格）；`None → None`。解码为逆运算，且必须与 `parse_scalar` 的字符串格式互逆（`decode_value(encode_value(v), t) == v`）。

- [ ] **Step 1: 写失败的 DDL 测试**

创建 `aps_tests/test_schema_ddl.py`：

```python
from __future__ import annotations

import unittest

from aps.schema.ddl import SQL_TYPE, create_all_ddl, create_ddl, create_indexes_ddl
from aps.schema.registry import REGISTRY, get_entity
from aps.schema.types import DataType

FAST = True

BANNED = ("AUTOINCREMENT", "INSERT OR REPLACE", "ON CONFLICT", "WITHOUT ROWID", "STRICT")


class TestSqlTypeMap(unittest.TestCase):
    def test_only_four_sql_types(self):
        allowed = {"TEXT", "INTEGER", "REAL", "NUMERIC"}
        for dtype, sql in SQL_TYPE.items():
            with self.subTest(dtype=dtype):
                self.assertIn(sql, allowed)

    def test_every_data_type_mapped(self):
        self.assertEqual(set(SQL_TYPE), set(DataType))

    def test_bool_and_temporal_are_storable(self):
        self.assertEqual(SQL_TYPE[DataType.BOOL], "INTEGER")
        self.assertEqual(SQL_TYPE[DataType.DATE], "TEXT")
        self.assertEqual(SQL_TYPE[DataType.MULTI_TEXT], "TEXT")


class TestCreateDdl(unittest.TestCase):
    def test_routing_ddl_shape(self):
        ddl = create_ddl(get_entity("routing"))
        self.assertIn("CREATE TABLE routing (", ddl)
        self.assertIn("site_code TEXT NOT NULL", ddl)
        self.assertIn("ct_sec REAL NOT NULL", ddl)
        self.assertIn("certified INTEGER NOT NULL", ddl)
        self.assertIn("PRIMARY KEY (site_code, pn, op_seq, line_code)", ddl)
        self.assertIn('REFERENCES site (site_code)', ddl)
        self.assertIn('REFERENCES line (line_code)', ddl)

    def test_nullable_field_has_no_not_null(self):
        ddl = create_ddl(get_entity("routing"))
        self.assertIn("mold_ids TEXT", ddl)
        self.assertNotIn("mold_ids TEXT NOT NULL", ddl)

    def test_all_entities_generate_ddl(self):
        for name, spec in REGISTRY.items():
            with self.subTest(entity=name):
                self.assertIn(f"CREATE TABLE {name} (", create_ddl(spec))

    def test_indexes_for_non_pk_fks(self):
        idx = create_indexes_ddl(get_entity("routing"))
        self.assertIn("CREATE INDEX IF NOT EXISTS ix_routing_family_code ON routing (family_code)", idx)

    def test_no_index_on_pk_column(self):
        idx = create_indexes_ddl(get_entity("routing"))
        self.assertNotIn("ix_routing_line_code", idx)
        self.assertNotIn("ix_routing_site_code", idx)
        stock_idx = create_indexes_ddl(get_entity("initial_stock"))
        self.assertNotIn("ix_initial_stock_pn ", stock_idx)


class TestCreateAllDdl(unittest.TestCase):
    def setUp(self):
        self.script = create_all_ddl()

    def test_contains_every_table(self):
        for name in REGISTRY:
            with self.subTest(entity=name):
                self.assertIn(f"CREATE TABLE {name} (", self.script)

    def test_referenced_tables_created_before_referrers(self):
        positions = {name: self.script.index(f"CREATE TABLE {name} (") for name in REGISTRY}
        self.assertLess(positions["site"], positions["line"])
        self.assertLess(positions["line"], positions["routing"])
        self.assertLess(positions["part"], positions["demand"])
        self.assertLess(positions["mold"], positions["setup_matrix"])
        self.assertLess(positions["product_family"], positions["part"])

    def test_no_banned_sqlite_dialect(self):
        upper = self.script.upper()
        for token in BANNED:
            with self.subTest(token=token):
                self.assertNotIn(token, upper)

    def test_idempotent_with_if_not_exists(self):
        self.assertEqual(self.script.count("CREATE TABLE routing ("), 1)
        self.assertIn("CREATE TABLE IF NOT EXISTS", self.script)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_schema_ddl -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'aps.schema.ddl'`

- [ ] **Step 3: 实现 `aps/schema/ddl.py`**

```python
from __future__ import annotations

from aps.schema.registry import REGISTRY, EntitySpec
from aps.schema.types import DataType

__all__ = ["SQL_TYPE", "create_all_ddl", "create_ddl", "create_indexes_ddl"]

SQL_TYPE: dict[DataType, str] = {
    DataType.TEXT: "TEXT",
    DataType.INT: "INTEGER",
    DataType.REAL: "REAL",
    DataType.BOOL: "INTEGER",
    DataType.DATE: "TEXT",
    DataType.TIME: "TEXT",
    DataType.DATETIME: "TEXT",
    DataType.MULTI_TEXT: "TEXT",
}


def _fk_clause(fk: str) -> str:
    target_entity, _, target_field = fk.partition(".")
    target = REGISTRY[target_entity]
    if not target_field:
        target_field = target.pk[0]
    return f" REFERENCES {target_entity} ({target_field})"


def create_ddl(spec: EntitySpec) -> str:
    lines: list[str] = []
    for field in spec.fields:
        column = f"    {field.name} {SQL_TYPE[field.dtype]}"
        if not field.nullable:
            column += " NOT NULL"
        if field.fk:
            column += _fk_clause(field.fk)
        lines.append(column)
    lines.append(f"    PRIMARY KEY ({', '.join(spec.pk)})")
    return f"CREATE TABLE IF NOT EXISTS {spec.name} (\n" + ",\n".join(lines) + "\n);"


def create_indexes_ddl(spec: EntitySpec) -> str:
    pk = set(spec.pk)
    seen: set[str] = set()
    out: list[str] = []
    for field in spec.fields:
        if not field.fk or field.name in pk or field.name in seen:
            continue
        seen.add(field.name)
        out.append(
            f"CREATE INDEX IF NOT EXISTS ix_{spec.name}_{field.name} "
            f"ON {spec.name} ({field.name});"
        )
    return "\n".join(out)


def _creation_order() -> list[EntitySpec]:
    pending = dict(REGISTRY)
    ordered: list[EntitySpec] = []
    placed: set[str] = set()
    while pending:
        progress = False
        for name in sorted(pending):
            spec = pending[name]
            deps = {
                field.fk.partition(".")[0]
                for field in spec.fields
                if field.fk and field.fk.partition(".")[0] != name
            }
            if deps <= placed:
                ordered.append(spec)
                placed.add(name)
                del pending[name]
                progress = True
        if not progress:
            raise RuntimeError(f"实体外键存在循环依赖: {sorted(pending)}")
    return ordered


def create_all_ddl() -> str:
    parts: list[str] = ["PRAGMA foreign_keys = ON;"]
    for spec in _creation_order():
        parts.append(create_ddl(spec))
    for spec in _creation_order():
        indexes = create_indexes_ddl(spec)
        if indexes:
            parts.append(indexes)
    return "\n\n".join(parts) + "\n"
```

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_schema_ddl -v`
Expected: PASS（16 tests）

- [ ] **Step 5: 写失败的 Repository 测试**

创建 `aps_tests/test_repository_sqlite.py`：

```python
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import tempfile
import unittest

from aps.data.sqlite import SqliteRepository, decode_value, encode_value
from aps.schema.ddl import create_all_ddl
from aps.schema.types import DataType

FAST = True


def _line_row(code="PGLINKAGE", trp=0.85, hours=8.0, shifts=3):
    return {
        "site_code": "WL", "line_code": code, "name": code,
        "trp_efficiency": trp, "shift_hours": hours, "shifts_per_day": shifts,
    }


class TestValueCodec(unittest.TestCase):
    def test_bool_roundtrip(self):
        for value in (True, False):
            self.assertEqual(decode_value(encode_value(value), DataType.BOOL), value)

    def test_date_roundtrip(self):
        value = dt.date(2026, 11, 3)
        self.assertEqual(encode_value(value), "2026-11-03")
        self.assertEqual(decode_value("2026-11-03", DataType.DATE), value)

    def test_time_roundtrip(self):
        value = dt.time(7, 0)
        self.assertEqual(encode_value(value), "07:00:00")
        self.assertEqual(decode_value("07:00:00", DataType.TIME), value)

    def test_datetime_roundtrip(self):
        value = dt.datetime(2026, 11, 3, 7, 0)
        self.assertEqual(encode_value(value), "2026-11-03 07:00:00")
        self.assertEqual(decode_value("2026-11-03 07:00:00", DataType.DATETIME), value)

    def test_multi_text_roundtrip(self):
        value = ("M1", "M2")
        self.assertEqual(encode_value(value), "M1,M2")
        self.assertEqual(decode_value("M1,M2", DataType.MULTI_TEXT), value)

    def test_none_roundtrip(self):
        for dtype in DataType:
            with self.subTest(dtype=dtype):
                self.assertIsNone(encode_value(None))
                self.assertIsNone(decode_value(None, dtype))

    def test_numbers_pass_through(self):
        self.assertEqual(encode_value(48), 48)
        self.assertEqual(encode_value(0.85), 0.85)


class TestSqliteRepository(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.path)
        self.repo = SqliteRepository(self.path)
        self.repo.initialize_schema()

    def tearDown(self):
        self.repo.close()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_schema_created_all_tables(self):
        names = {
            r[0] for r in self.repo._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertIn("routing", names)
        self.assertIn("calendar_day", names)

    def test_foreign_keys_pragma_on(self):
        row = self.repo._conn.execute("PRAGMA foreign_keys").fetchone()
        self.assertEqual(row[0], 1)

    def test_fk_violation_raises(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.insert_many("line", [_line_row(code="GHOST")])
            self.repo.insert_many(
                "routing",
                [{
                    "site_code": "WL", "pn": "X", "op_seq": 10, "line_code": "GHOST",
                    "certified": True, "ct_sec": 27.0,
                }],
            )

    def test_insert_then_list(self):
        self.repo.insert_many("site", [{"site_code": "WL", "name": "温岭"}])
        self.repo.insert_many("line", [_line_row()])
        rows = self.repo.list_by("line")
        self.assertEqual(1, len(rows))
        self.assertEqual("PGLINKAGE", rows[0]["line_code"])
        self.assertEqual(0.85, rows[0]["trp_efficiency"])
        self.assertEqual(3, rows[0]["shifts_per_day"])

    def test_list_by_filters(self):
        self.repo.insert_many("site", [{"site_code": "WL"}, {"site_code": "SY38"}])
        self.repo.insert_many("line", [_line_row("A"), _line_row("B")])
        self.repo.insert_many("line", [dict(_line_row("C"), site_code="SY38")])
        self.assertEqual(2, len(self.repo.list_by("line", site_code="WL")))
        self.assertEqual(1, len(self.repo.list_by("line", site_code="SY38", line_code="C")))

    def test_list_by_is_deterministically_ordered(self):
        self.repo.insert_many("site", [{"site_code": "WL"}])
        self.repo.insert_many("line", [_line_row(c) for c in ("Z线", "A线", "M线")])
        codes = [r["line_code"] for r in self.repo.list_by("line")]
        self.assertEqual(["A线", "M线", "Z线"], codes)

    def test_get_by_pk(self):
        self.repo.insert_many("site", [{"site_code": "WL"}])
        self.repo.insert_many("line", [_line_row()])
        self.assertIsNotNone(self.repo.get_by_pk("line", ("WL", "PGLINKAGE")))
        self.assertIsNone(self.repo.get_by_pk("line", ("WL", "NOPE")))

    def test_get_by_pk_wrong_arity_raises(self):
        with self.assertRaises(ValueError):
            self.repo.get_by_pk("line", ("WL",))

    def test_count(self):
        self.repo.insert_many("site", [{"site_code": "WL"}])
        self.repo.insert_many("line", [_line_row("A"), _line_row("B")])
        self.assertEqual(2, self.repo.count("line"))
        self.assertEqual(1, self.repo.count("line", line_code="A"))

    def test_delete_where(self):
        self.repo.insert_many("site", [{"site_code": "WL"}])
        self.repo.insert_many("line", [_line_row("A"), _line_row("B")])
        self.assertEqual(1, self.repo.delete_where("line", line_code="A"))
        self.assertEqual(1, self.repo.count("line"))

    def test_insert_many_rejects_unknown_field(self):
        with self.assertRaises(KeyError):
            self.repo.insert_many("line", [dict(_line_row(), ghost=1)])

    def test_insert_many_rejects_missing_non_nullable(self):
        with self.assertRaises(KeyError):
            self.repo.insert_many("line", [{"site_code": "WL", "line_code": "A", "name": "A"}])

    def test_insert_many_fills_absent_nullable_columns(self):
        self.repo.insert_many("site", [{"site_code": "WL"}])
        self.repo.insert_many("line", [
            {"site_code": "WL", "line_code": "A", "name": "A",
             "trp_efficiency": 0.9, "shift_hours": 8.0, "shifts_per_day": 3},
        ])
        row = self.repo.get_by_pk("site", ("WL",))
        self.assertEqual({"site_code", "name", "default_calendar_mode"}, set(row))
        self.assertIsNone(row["name"])
        self.assertIsNone(row["default_calendar_mode"])


class TestReplaceSite(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.path)
        self.repo = SqliteRepository(self.path)
        self.repo.initialize_schema()
        self.repo.insert_many("site", [{"site_code": "WL"}, {"site_code": "SY38"}])

    def tearDown(self):
        self.repo.close()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_replace_clears_only_target_site(self):
        self.repo.insert_many("line", [_line_row("OLD")])
        self.repo.insert_many("line", [dict(_line_row("SY"), site_code="SY38")])
        counts = self.repo.replace_site("WL", {"line": [_line_row("NEW")]})
        self.assertEqual({"line": 1}, counts)
        wl = [r["line_code"] for r in self.repo.list_by("line", site_code="WL")]
        sy = [r["line_code"] for r in self.repo.list_by("line", site_code="SY38")]
        self.assertEqual(["NEW"], wl)
        self.assertEqual(["SY"], sy)

    def test_replace_respects_fk_order(self):
        counts = self.repo.replace_site("WL", {
            "part_param_fg": [{
                "site_code": "WL", "pn": "P1", "description": "d", "mpq": 48,
            }],
            "part": [{"site_code": "WL", "pn": "P1", "description": "d", "stage": "FG"}],
            "line": [_line_row()],
        })
        self.assertEqual(1, counts["part_param_fg"])

    def test_replace_is_atomic_on_failure(self):
        with self.assertRaises(Exception):
            self.repo.replace_site("WL", {
                "line": [_line_row("GOOD")],
                "routing": [{"site_code": "WL", "pn": "X", "op_seq": 10,
                             "line_code": "GHOST", "certified": True, "ct_sec": 27.0}],
            })
        self.assertEqual(0, self.repo.count("line", site_code="WL"))

    def test_replace_site_row_itself_is_preserved(self):
        self.repo.replace_site("WL", {})
        self.assertIsNotNone(self.repo.get_by_pk("site", ("WL",)))

    def test_replace_site_creates_missing_site_row_before_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fresh.db")
            fresh = SqliteRepository(path)
            try:
                fresh.initialize_schema()
                counts = fresh.replace_site(
                    "BRAND", {"line": [dict(_line_row("L"), site_code="BRAND")]}
                )
                self.assertEqual({"line": 1}, counts)
                self.assertIsNotNone(fresh.get_by_pk("site", ("BRAND",)))
                self.assertEqual(1, fresh.count("line", site_code="BRAND"))
            finally:
                fresh.close()

    def test_ddl_script_is_stable_across_calls(self):
        self.assertEqual(create_all_ddl(), create_all_ddl())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_repository_sqlite -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'aps.data.sqlite'`

- [ ] **Step 7: 实现 `aps/data/repository.py`**

```python
from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable

__all__ = ["EntityName", "Repository", "Row"]

EntityName = str
Row = dict[str, Any]


@runtime_checkable
class Repository(Protocol):
    def list_by(self, entity: EntityName, /, **filters: Any) -> list[Row]:
        ...

    def get_by_pk(self, entity: EntityName, pk: tuple[Any, ...]) -> Row | None:
        ...

    def insert_many(self, entity: EntityName, rows: Sequence[Row]) -> int:
        ...

    def delete_where(self, entity: EntityName, /, **filters: Any) -> int:
        ...

    def count(self, entity: EntityName, /, **filters: Any) -> int:
        ...
```

- [ ] **Step 8: 实现 `aps/data/sqlite.py`**

```python
from __future__ import annotations

import contextlib
import datetime as dt
import os
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from aps.data.repository import EntityName, Row
from aps.schema.ddl import create_all_ddl
from aps.schema.registry import REGISTRY, EntitySpec, get_entity
from aps.schema.types import DataType

__all__ = ["SqliteRepository", "decode_value", "encode_value"]

_DATE_FORMAT = "%Y-%m-%d"
_TIME_FORMAT = "%H:%M:%S"
_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def encode_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, dt.datetime):
        return value.strftime(_DATETIME_FORMAT)
    if isinstance(value, dt.date):
        return value.strftime(_DATE_FORMAT)
    if isinstance(value, dt.time):
        return value.strftime(_TIME_FORMAT)
    if isinstance(value, (tuple, list)):
        return ",".join(str(v) for v in value)
    return value


def decode_value(raw: Any, dtype: DataType) -> Any:
    if raw is None:
        return None
    if dtype is DataType.BOOL:
        return bool(raw)
    if dtype is DataType.DATE:
        return dt.datetime.strptime(str(raw), _DATE_FORMAT).date()
    if dtype is DataType.TIME:
        return dt.datetime.strptime(str(raw), _TIME_FORMAT).time()
    if dtype is DataType.DATETIME:
        return dt.datetime.strptime(str(raw), _DATETIME_FORMAT)
    if dtype is DataType.MULTI_TEXT:
        return tuple(p for p in (s.strip() for s in str(raw).split(",")) if p)
    if dtype is DataType.INT:
        return int(raw)
    if dtype is DataType.REAL:
        return float(raw)
    return str(raw)


def _deletion_order() -> list[EntitySpec]:
    """Children first so FK constraints hold during site replacement."""
    return list(reversed(_topological_order()))


def _topological_order() -> list[EntitySpec]:
    pending = dict(REGISTRY)
    ordered: list[EntitySpec] = []
    placed: set[str] = set()
    while pending:
        progress = False
        for name in sorted(pending):
            spec = pending[name]
            deps = {
                f.fk.partition(".")[0] for f in spec.fields
                if f.fk and f.fk.partition(".")[0] != name
            }
            if deps <= placed:
                ordered.append(spec)
                placed.add(name)
                del pending[name]
                progress = True
        if not progress:
            raise RuntimeError(f"实体外键存在循环依赖: {sorted(pending)}")
    return ordered


class SqliteRepository:
    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self.db_path = os.fspath(db_path)
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def initialize_schema(self) -> None:
        with self.transaction():
            self._conn.executescript(create_all_ddl())
            self._conn.execute("PRAGMA foreign_keys = ON")

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _spec(self, entity: EntityName) -> EntitySpec:
        return get_entity(entity)

    def _columns(self, spec: EntitySpec) -> tuple[str, ...]:
        return tuple(f.name for f in spec.fields)

    def _decode_row(self, spec: EntitySpec, raw: sqlite3.Row) -> Row:
        keys = raw.keys()
        return {
            name: decode_value(raw[name], spec.field(name).dtype)
            for name in keys
        }

    def _prepare(self, spec: EntitySpec, row: Row) -> tuple[list[str], list[Any]]:
        names: list[str] = []
        values: list[Any] = []
        unknown = set(row) - set(self._columns(spec))
        if unknown:
            raise KeyError(f"{spec.name}: 未知字段 {sorted(unknown)}")
        for field in spec.fields:
            if field.name in row:
                value = row[field.name]
            elif field.nullable or field.provenance == "derived":
                value = None
            else:
                raise KeyError(f"{spec.name}.{field.name}: 缺少非空字段")
            if value is None and not field.nullable:
                raise KeyError(f"{spec.name}.{field.name}: 非空字段取值为 None")
            names.append(field.name)
            values.append(encode_value(value))
        return names, values

    def insert_many(self, entity: EntityName, rows: Sequence[Row]) -> int:
        spec = self._spec(entity)
        if not rows:
            return 0
        prepared = [self._prepare(spec, row) for row in rows]
        names = prepared[0][0]
        sql = (
            f"INSERT INTO {entity} ({', '.join(names)}) "
            f"VALUES ({', '.join('?' * len(names))})"
        )
        self._conn.executemany(sql, [values for _, values in prepared])
        return len(prepared)

    def _where(self, spec: EntitySpec, filters: Mapping[str, Any]) -> tuple[str, list[Any]]:
        unknown = set(filters) - set(self._columns(spec))
        if unknown:
            raise KeyError(f"{spec.name}: 未知过滤字段 {sorted(unknown)}")
        if not filters:
            return "", []
        clauses = [f"{name} = ?" for name in sorted(filters)]
        return " WHERE " + " AND ".join(clauses), [
            encode_value(filters[name]) for name in sorted(filters)
        ]

    def list_by(self, entity: EntityName, /, **filters: Any) -> list[Row]:
        spec = self._spec(entity)
        where, params = self._where(spec, filters)
        order = ", ".join(spec.pk)
        cursor = self._conn.execute(f"SELECT * FROM {entity}{where} ORDER BY {order}", params)
        return [self._decode_row(spec, raw) for raw in cursor.fetchall()]

    def get_by_pk(self, entity: EntityName, pk: tuple[Any, ...]) -> Row | None:
        spec = self._spec(entity)
        if len(pk) != len(spec.pk):
            raise ValueError(f"{entity} 主键需要 {len(spec.pk)} 个值，收到 {len(pk)} 个")
        filters = dict(zip(spec.pk, pk))
        rows = self.list_by(entity, **filters)
        return rows[0] if rows else None

    def delete_where(self, entity: EntityName, /, **filters: Any) -> int:
        spec = self._spec(entity)
        where, params = self._where(spec, filters)
        cursor = self._conn.execute(f"DELETE FROM {entity}{where}", params)
        return cursor.rowcount

    def count(self, entity: EntityName, /, **filters: Any) -> int:
        spec = self._spec(entity)
        where, params = self._where(spec, filters)
        cursor = self._conn.execute(f"SELECT COUNT(*) FROM {entity}{where}", params)
        return int(cursor.fetchone()[0])

    def replace_site(
        self, site_code: str, rows_by_entity: Mapping[str, Sequence[Row]]
    ) -> dict[str, int]:
        unknown = set(rows_by_entity) - set(REGISTRY)
        if unknown:
            raise KeyError(f"登记表无实体 {sorted(unknown)}")
        with self.transaction():
            for spec in _deletion_order():
                if spec.name == "site" or "site_code" not in self._columns(spec):
                    continue
                self.delete_where(spec.name, site_code=site_code)
            if self.get_by_pk("site", (site_code,)) is None:
                self.insert_many("site", [{"site_code": site_code}])
            counts: dict[str, int] = {}
            for spec in _topological_order():
                if spec.name == "site":
                    continue
                rows = rows_by_entity.get(spec.name)
                if rows:
                    counts[spec.name] = self.insert_many(spec.name, rows)
            return counts
```

**为什么 `site` 行必须在拓扑插入循环之前建**：几乎每个实体的 `site_code` 都声明了 `fk="site.site_code"`，而 `PRAGMA foreign_keys = ON`。若把 `site` 留到最后插入，在全新数据库上导入 `line` 就会直接撞 FK 约束。删除方向相反——`_deletion_order()` 是子表先删，`site` 行本身**永不删除**（它是站点身份的锚，`replace_site` 的语义是「换数据」不是「删站点」）。`rows_by_entity` 里的 `"site"` 键被忽略：适配器产出的 site 行只有 `site_code`，`name` 与 `default_calendar_mode` 留空即可——后者为空正是 `DataSet._resolve_mode` 走负载率推荐的前提。

- [ ] **Step 9: 补 `aps/data/__init__.py` 的 re-export**

```python
from __future__ import annotations

from aps.data.repository import Repository, Row
from aps.data.sqlite import SqliteRepository, decode_value, encode_value

__all__ = ["Repository", "Row", "SqliteRepository", "decode_value", "encode_value"]
```

- [ ] **Step 10: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_repository_sqlite aps_tests.test_schema_ddl -v`
Expected: PASS（全部）

注意 `test_fk_violation_raises` 依赖 `initialize_schema()` 之后 `PRAGMA foreign_keys` 仍为 ON。`executescript` 会隐式提交并可能重置连接级 pragma，所以 `initialize_schema` 必须在 `executescript` **之后**再执行一次 `PRAGMA foreign_keys = ON`——上面的实现已经这样做了，不要删掉那一行。

- [ ] **Step 11: 确认没破坏前面的任务**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --new`
Expected: PASS

- [ ] **Step 12: 提交**

```bash
git add aps/schema/ddl.py aps/data/repository.py aps/data/sqlite.py aps/data/__init__.py \
        aps_tests/test_schema_ddl.py aps_tests/test_repository_sqlite.py
git commit -m "feat(data): 登记表驱动 DDL 生成与 SQLite Repository 整站快照替换"
```

---

## Task 5: Excel 适配器（15 表 → 23 实体）

**Files:**
- Create: `aps/data/issues.py`, `aps/data/excel.py`
- Modify: `aps/data/__init__.py`
- Test: `aps_tests/test_excel_adapter.py`

这是本阶段最容易做错的一个任务。**所有列名、sheet 名、枚举值都已在「模板实测事实」一节给出，不得凭猜测。** 适配器只做「定位 + 取原值 + 结构整形」，**不做类型解析、不做取值域判断、不做引用检查**——那些是 Task 6 的四道门。适配器产出的每个值都是**未经解析的原始单元格值**（可能是 `datetime` 对象、可能是字符串 `'True'`）。

**Interfaces:**
- Consumes: `aps.schema.registry`（`REGISTRY` / `entities_with_sheet` / `get_entity` / `STAGE_NORMALIZE`）
- Produces:
  ```python
  # aps/data/issues.py
  class Severity(str, Enum):
      ERROR = "error"; WARNING = "warning"; GAP = "gap"

  @dataclass(frozen=True)
  class Issue:
      severity: Severity
      entity: str | None
      field: str | None
      sheet: str | None
      row: int | None            # Excel 1-based 行号；None = 表级问题
      column: str | None         # 逐字表头文本；None = 表级问题
      message: str
      value: Any = None

      def location(self) -> str   # "sheet!R{row}C[{column}]" 形式的可读定位

  class IssueLog:                  # 可变收集器
      def add(self, issue: Issue) -> None
      def emit(self, severity: Severity, ...) -> None   # 动态级别入口
      def error(self, ...) -> None      # 便捷构造，参数同 Issue 但 severity 固定
      def warning(self, ...) -> None
      def gap(self, ...) -> None
      @property
      def issues(self) -> tuple[Issue, ...]
      @property
      def errors(self) -> tuple[Issue, ...]
      @property
      def warnings(self) -> tuple[Issue, ...]
      @property
      def gaps(self) -> tuple[Issue, ...]
      def has_errors(self) -> bool
      def extend(self, other: Iterable[Issue]) -> None
      def __len__(self) -> int

  # aps/data/excel.py
  RawTables = dict[str, list["RawRow"]]

  @dataclass
  class RawRow:
      entity: str
      sheet: str | None
      excel_row: int | None      # None = 合成行（site/part/product_family/calendar_pattern）
      values: dict[str, Any]     # 规范字段名 → 原始单元格值

  HEADER_SCAN_ROWS = 6           # 在前 6 行内寻找表头行

  def locate_header(ws, spec) -> tuple[int | None, dict[str, int], set[str]]
      # 返回 (表头行号, 字段名→列号(1-based), 实际命中的表头文本集合)

  def read_workbook(path: str | os.PathLike[str], site: str) -> tuple[RawTables, IssueLog]
      # 主入口。绝不抛数据类异常；文件不存在/无法打开才抛 OSError。
  ```

**适配器的 7 条整形规则（写死，逐条对应实测异常）**：

1. **行有效性按 PK 判定（G13）**：一行的所有「有 `source_columns` 的 PK 字段」都非空白，才算有效行。`site_code` 若 `site_injected` 则视为恒有效。无效行**静默跳过**（不报 issue）——实测 `Parameters-FG` 960 行里 895 行是这种空壳行，报 895 条 issue 毫无意义。
2. **宽表 unpivot**：`demand` 的日期轴列 = 表头单元格的值**不在** `spec.header_to_field` 中，且能作为日期解析的列。每个有效行 × 每个数值 > 0 的日期单元格 → 一条 `RawRow`，`demand_date` 填表头单元格原值，`quantity` 填该单元格原值。数值 ≤ 0 或空白的单元格**不产出行**。
3. **多值列展开**：`tooling_mold.line_code` 的来源列 `适用产线/设备` 是逗号分隔多值。按 `,` / `，` split 后**每个产线一条 `RawRow`**（`excel_row` 保持原行号）。split 后为空 → 跳过该行 + WARNING。
4. **序号注入**：`material_arrival.seq` 模板中不存在，按 `(site_code, pn, 预计入库日期原值, 预计入库班次原值)` 分组、组内按 sheet 行序赋 1..n。这保证 PK 唯一且不依赖业务填列。
5. **站点注入**：`site_injected=True` 的实体（`line`）没有 `站点` 列，`site_code` 一律填 CLI 传入的 `site`。其余实体的 `site_code` 取自 `站点` 列原值。
6. **同 sheet 多实体**：`工装模具约束 Tooling-Mold` 同时产出 `mold` 与 `tooling_mold`。两者独立定位表头、独立判断行有效性。
7. **合成实体**（`excel_row = None`，`sheet = None`）：
   - `site`：一行 `{site_code: site}`。
   - `calendar_pattern`：四行固定值 —— `CONTINUOUS(7,0)`、`6ON1OFF(6,1)`、`5ON2OFF(5,2)`、`4ON3OFF(4,3)`。
   - `product_family`：从 `part` 行的 `family_code` 去重收集，`name` 与 `family_code` 同值（模板只有一列 `产品族名`）。
   - `part`：按下列**优先级**从多表并集构建，先出现者胜：

     | 优先级 | 来源 | stage | description | family_code |
     |---|---|---|---|---|
     | 1 | `part_param_fg` | `FG` | `description` | `family_code` |
     | 2 | `part_param_sf` | `SF` | `description` | `family_code` |
     | 3 | `routing` | 该行 `stage` 原值 | 无 | `family_code` |
     | 4 | `bom` 母件 | `FG` | `parent_description` | 无 |
     | 5 | `bom` 子件 | `SF` | `child_description` | 无 |
     | 6 | `initial_stock` | 该行 `material_type` 原值 | `description` | 无 |
     | 7 | `demand` | `FG` | `description` | 无 |
     | 8 | 兜底 | `COMP` | 无 | 无 |

     `uom` 一律填 `"件"`。冲突处理：同一 PN 同时出现在 `part_param_fg` 与 `part_param_sf` → WARNING（阶段歧义，取 FG）；`routing.stage` 与已定 stage 不一致 → WARNING（§3.2 一致性检查的一部分）。
     **`part` 的 `stage` / `material_type` 值此处保持原样**（如 `FING`、`SFIN`），归一化在类型门做——适配器不解析。

- [ ] **Step 1: 实现 `aps/data/issues.py`**

无需先写测试：`IssueLog` 的行为在 Task 5/6 的适配器与校验门测试中被完整覆盖，单独测它是重复。

```python
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = ["Issue", "IssueLog", "Severity"]


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    GAP = "gap"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    entity: str | None
    field: str | None
    sheet: str | None
    row: int | None
    column: str | None
    message: str
    value: Any = None

    def location(self) -> str:
        if self.sheet is None:
            return "(无工作表)"
        parts = [self.sheet]
        if self.row is not None:
            parts.append(f"R{self.row}")
        if self.column is not None:
            parts.append(f"C[{self.column}]")
        return "!".join(parts[:1]) + ("!" + " ".join(parts[1:]) if len(parts) > 1 else "")


class IssueLog:
    def __init__(self, issues: Iterable[Issue] = ()) -> None:
        self._issues: list[Issue] = list(issues)

    def add(self, issue: Issue) -> None:
        self._issues.append(issue)

    def emit(self, severity: Severity, entity: str | None = None, field: str | None = None,
             sheet: str | None = None, row: int | None = None,
             column: str | None = None, message: str = "", value: Any = None) -> None:
        self._issues.append(Issue(severity, entity, field, sheet, row, column, message, value))

    def error(self, entity: str | None = None, field: str | None = None,
              sheet: str | None = None, row: int | None = None,
              column: str | None = None, message: str = "", value: Any = None) -> None:
        self.emit(Severity.ERROR, entity, field, sheet, row, column, message, value)

    def warning(self, entity: str | None = None, field: str | None = None,
                sheet: str | None = None, row: int | None = None,
                column: str | None = None, message: str = "", value: Any = None) -> None:
        self.emit(Severity.WARNING, entity, field, sheet, row, column, message, value)

    def gap(self, entity: str | None = None, field: str | None = None,
            sheet: str | None = None, row: int | None = None,
            column: str | None = None, message: str = "", value: Any = None) -> None:
        self.emit(Severity.GAP, entity, field, sheet, row, column, message, value)

    def extend(self, other: Iterable[Issue]) -> None:
        self._issues.extend(other)

    @property
    def issues(self) -> tuple[Issue, ...]:
        return tuple(self._issues)

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(i for i in self._issues if i.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(i for i in self._issues if i.severity is Severity.WARNING)

    @property
    def gaps(self) -> tuple[Issue, ...]:
        return tuple(i for i in self._issues if i.severity is Severity.GAP)

    def has_errors(self) -> bool:
        return any(i.severity is Severity.ERROR for i in self._issues)

    def __len__(self) -> int:
        return len(self._issues)
```

`location()` 的字符串拼接刻意写得啰嗦，因为它要产出 `期初可用库存 Initial Stock!R17 C[物料料号 (PN)]` 这种可读定位。若测试对格式有断言，以测试为准。

- [ ] **Step 2: 写失败的适配器测试**

创建 `aps_tests/test_excel_adapter.py`。测试**不读真实工作簿**（慢且脆），而是用 `openpyxl` 现场造最小工作簿 —— 这样每条整形规则都能被精确打击。真实工作簿的验证放在 Task 10 的 L1 门。

```python
from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest

import openpyxl

from aps.data.excel import HEADER_SCAN_ROWS, RawRow, locate_header, read_workbook
from aps.data.issues import Severity
from aps.schema.registry import get_entity

FAST = True


def _write(rows_by_sheet: dict[str, list[list]], path: str) -> str:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet, rows in rows_by_sheet.items():
        ws = wb.create_sheet(sheet)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


class _TempWorkbook(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        os.unlink(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def make(self, rows_by_sheet):
        return _write(rows_by_sheet, self.path)

    def values(self, tables, entity, field):
        return [r.values.get(field) for r in tables[entity]]


LINE_HEADER = ["产线", "效率 TRP", "单班工时 (小时)", "每天班次数"]
LINE_ROWS = [
    ["PGLINKAGE", 0.85, 8.0, 3],
    ["轴套6", 0.90, 8.0, 3],
]


class TestLocateHeader(_TempWorkbook):
    def test_header_on_row_one(self):
        self.make({"产线基础数据": [LINE_HEADER] + LINE_ROWS})
        wb = openpyxl.load_workbook(self.path)
        row, col_of, hit = locate_header(wb["产线基础数据"], get_entity("line"))
        self.assertEqual(1, row)
        self.assertEqual(1, col_of["line_code"])
        self.assertEqual(4, col_of["shifts_per_day"])
        self.assertIn("效率 TRP", hit)

    def test_header_below_title_rows(self):
        self.make({"产线基础数据": [["温岭工厂产线台账"], [], LINE_HEADER] + LINE_ROWS})
        wb = openpyxl.load_workbook(self.path)
        row, col_of, _ = locate_header(wb["产线基础数据"], get_entity("line"))
        self.assertEqual(3, row)
        self.assertEqual(2, col_of["trp_efficiency"])

    def test_column_order_is_irrelevant(self):
        shuffled = ["每天班次数", "产线", "单班工时 (小时)", "效率 TRP"]
        self.make({"产线基础数据": [shuffled, [3, "PGLINKAGE", 8.0, 0.85]]})
        wb = openpyxl.load_workbook(self.path)
        _, col_of, _ = locate_header(wb["产线基础数据"], get_entity("line"))
        self.assertEqual(4, col_of["trp_efficiency"])
        self.assertEqual(1, col_of["shifts_per_day"])

    def test_fullwidth_paren_alias_recognised(self):
        header = ["站点", "料号 (PN)", "阶段", "工序序号", "工序名称", "提前准备时间 (分钟)",
                  "转运批量 (件)", "容器"]
        self.make({"工序提前期 Operation-LeadTime": [header, ["WL", "WCA1927", "SFIN", 10, "静置",
                                                             45, 100, "A"]])
        wb = openpyxl.load_workbook(self.path)
        _, col_of, _ = locate_header(
            wb["工序提前期 Operation-LeadTime"], get_entity("operation_leadtime")
        )
        self.assertIn("prep_minutes", col_of)

    def test_unlocatable_header_returns_none(self):
        self.make({"产线基础数据": [["甲", "乙", "丙"], [1, 2, 3]]})
        wb = openpyxl.load_workbook(self.path)
        row, col_of, hit = locate_header(wb["产线基础数据"], get_entity("line"))
        self.assertIsNone(row)
        self.assertEqual({}, col_of)
        self.assertEqual(set(), hit)

    def test_scan_window_is_six_rows(self):
        self.assertEqual(6, HEADER_SCAN_ROWS)


class TestRowValidity(_TempWorkbook):
    def test_blank_shell_rows_are_skipped(self):
        rows = [LINE_HEADER] + LINE_ROWS + [[None, None, None, None]] * 5
        self.make({"产线基础数据": rows})
        tables, log = read_workbook(self.path, "WL")
        self.assertEqual(2, len(tables["line"]))
        self.assertFalse(log.has_errors())

    def test_partial_pk_row_is_skipped(self):
        rows = [LINE_HEADER, ["PGLINKAGE", 0.85, 8.0, None]]
        self.make({"产线基础数据": rows})
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual(0, len(tables["line"]))

    def test_fg_params_only_64_of_960_style(self):
        # 复刻实测异常：895 行只有「最小生产量 MOQ」列有个 0
        fg_header = ["站点", "成品料号 (PN)", "品名描述", "最小包装量 MPQ", "单班工时 (小时)",
                     "每天班次数", "最小生产量 MOQ", "产品族名", "模具编号 (Mold ID)",
                     "特殊班次", "同时生产"]
        rows = [fg_header]
        rows.append(["WL", "WA14208S", "雨刮总成", 48, 8, 3, 0, "FAM1", "M1", None, None])
        for _ in range(895):
            rows.append([None, None, None, None, None, None, 0, None, None, None, None])
        self.make({"产线与成品参数 Parameters-FG": rows})
        tables, log = read_workbook(self.path, "WL")
        self.assertEqual(1, len(tables["part_param_fg"]))
        self.assertFalse(log.has_errors())

    def test_shift_geometry_columns_of_fg_are_not_read(self):
        fg_header = ["站点", "成品料号 (PN)", "品名描述", "最小包装量 MPQ", "单班工时 (小时)",
                     "每天班次数", "最小生产量 MOQ", "产品族名", "模具编号 (Mold ID)",
                     "特殊班次", "同时生产"]
        self.make({"产线与成品参数 Parameters-FG":
                   [fg_header, ["WL", "P1", "d", 48, 8, 3, 0, None, None, None, None]]})
        tables, _ = read_workbook(self.path, "WL")
        values = tables["part_param_fg"][0].values
        self.assertNotIn("shift_hours", values)
        self.assertNotIn("shifts_per_day", values)
        self.assertEqual(48, values["mpq"])


class TestSiteInjection(_TempWorkbook):
    def test_line_gets_site_from_context(self):
        self.make({"产线基础数据": [LINE_HEADER] + LINE_ROWS})
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual(["WL", "WL"], self.values(tables, "line", "site_code"))

    def test_other_entities_get_site_from_column(self):
        header = ["站点", "模具编号 (Mold ID)", "工装模具名称", "适用产线/设备",
                  "适用零件料号", "可用套数", "换模工时 (分钟)"]
        self.make({"工装模具约束 Tooling-Mold": [header, ["WL", "M1", "模具一", "PGLINKAGE",
                                                          "P1,P2", 2, 30]]})
        tables, _ = read_workbook(self.path, "SY38")
        self.assertEqual(["WL"], self.values(tables, "tooling_mold", "site_code"))


class TestStructureIssues(_TempWorkbook):
    def _minimal(self, extra=None):
        sheets = {
            "产线基础数据": [LINE_HEADER] + LINE_ROWS,
        }
        if extra:
            sheets.update(extra)
        self.make(sheets)

    def test_missing_required_sheet_is_error(self):
        self._minimal()
        _, log = read_workbook(self.path, "WL")
        missing = [i for i in log.errors if "工作表缺失" in i.message]
        names = {i.sheet for i in missing}
        self.assertIn("期初可用库存 Initial Stock", names)

    def test_missing_optional_v3_sheet_is_warning_not_error(self):
        self._minimal()
        _, log = read_workbook(self.path, "WL")
        for sheet in ("班次人力可用性", "产品配对关系", "工作日历", "换模技术员"):
            with self.subTest(sheet=sheet):
                self.assertNotIn(sheet, {i.sheet for i in log.errors})
                self.assertIn(sheet, {i.sheet for i in log.warnings})

    def test_missing_required_column_is_error(self):
        broken = ["站点", "物料类型", "品名描述", "期初库存 (件)"]   # 缺「物料料号 (PN)」
        self.make({"期初可用库存 Initial Stock": [broken, ["WL", "COMP", "d", 10]]})
        _, log = read_workbook(self.path, "WL")
        cols = {i.column for i in log.errors}
        self.assertIn("物料料号 (PN)", cols)

    def test_extra_column_is_warning(self):
        header = LINE_HEADER + ["随便一列"]
        self.make({"产线基础数据": [header, ["PGLINKAGE", 0.85, 8.0, 3, "x"]]})
        _, log = read_workbook(self.path, "WL")
        self.assertIn("随便一列", {i.column for i in log.warnings})

    def test_deprecated_column_residue_is_warning(self):
        header = ["站点", "成品料号 (PN)", "品名描述", "最小包装量 MPQ", "最小生产量 MOQ",
                  "产线", "效率 TRP"]
        self.make({"产线与成品参数 Parameters-FG":
                   [header, ["WL", "P1", "d", 48, 0, "PGLINKAGE", 0.85]]})
        _, log = read_workbook(self.path, "WL")
        warnings = {i.column for i in log.warnings}
        self.assertIn("产线", warnings)
        self.assertIn("效率 TRP", warnings)
        self.assertFalse(any(i.severity is Severity.ERROR and i.column == "产线"
                             for i in log.issues))

    def test_unlocatable_sheet_header_is_error(self):
        self.make({"产线基础数据": [["甲", "乙", "丙"], [1, 2, 3]]})
        _, log = read_workbook(self.path, "WL")
        self.assertIn("表头", "".join(i.message for i in log.errors))


class TestDemandUnpivot(_TempWorkbook):
    HEADER = ["站点", "料号 (PN)", "品名描述"]

    def _demand_sheet(self):
        d1 = dt.datetime(2026, 11, 3)
        d2 = dt.datetime(2026, 11, 4)
        d3 = dt.datetime(2026, 11, 5)
        rows = [self.HEADER + [d1, d2, d3],
                ["WL", "P1", "d", 100, 0, None],
                ["WL", "P2", "d", 48, 96, 0],
                [None, None, None, None, None, None]]
        return self.HEADER, rows, (d1, d2, d3)

    def test_one_row_per_positive_cell(self):
        _, rows, _ = self._demand_sheet()
        self.make({"客户需求订单清单 Customer Demand": rows})
        tables, log = read_workbook(self.path, "WL")
        self.assertEqual(3, len(tables["demand"]))     # P1:d1, P2:d1, P2:d2
        self.assertFalse(log.has_errors())

    def test_zero_and_blank_cells_produce_no_row(self):
        _, rows, _ = self._demand_sheet()
        self.make({"客户需求订单清单 Customer Demand": rows})
        tables, _ = read_workbook(self.path, "WL")
        dates = [(r.values["pn"], r.values["demand_date"]) for r in tables["demand"]]
        self.assertNotIn(("P1", dt.datetime(2026, 11, 4)), dates)
        self.assertNotIn(("P1", dt.datetime(2026, 11, 5)), dates)
        self.assertNotIn(("P2", dt.datetime(2026, 11, 5)), dates)

    def test_quantity_is_raw_cell_value(self):
        _, rows, _ = self._demand_sheet()
        self.make({"客户需求订单清单 Customer Demand": rows})
        tables, _ = read_workbook(self.path, "WL")
        first = tables["demand"][0]
        self.assertEqual(100, first.values["quantity"])
        self.assertEqual(dt.datetime(2026, 11, 3), first.values["demand_date"])

    def test_excel_row_is_preserved(self):
        _, rows, _ = self._demand_sheet()
        self.make({"客户需求订单清单 Customer Demand": rows})
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual({2, 3}, {r.excel_row for r in tables["demand"]})

    def test_out_of_horizon_dates_are_not_an_adapter_problem(self):
        far = dt.datetime(2026, 12, 6)
        rows = [self.HEADER + [far], ["WL", "P1", "d", 10]]
        self.make({"客户需求订单清单 Customer Demand": rows})
        tables, log = read_workbook(self.path, "WL")
        self.assertEqual(1, len(tables["demand"]))
        self.assertFalse(log.has_errors())

    def test_text_dates_in_header_also_recognised(self):
        rows = [self.HEADER + ["2026-11-03"], ["WL", "P1", "d", 10]]
        self.make({"客户需求订单清单 Customer Demand": rows})
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual(1, len(tables["demand"]))


class TestToolingSplit(_TempWorkbook):
    HEADER = ["站点", "模具编号 (Mold ID)", "工装模具名称", "适用产线/设备",
              "适用零件料号", "可用套数", "换模工时 (分钟)"]

    def test_one_row_per_line(self):
        rows = [self.HEADER, ["WL", "M1", "模具一", "PGLINKAGE,轴套6", "P1，P2", 2, 30]]
        self.make({"工装模具约束 Tooling-Mold": rows})
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual(2, len(tables["tooling_mold"]))
        self.assertEqual({"PGLINKAGE", "轴套6"},
                         {r.values["line_code"] for r in tables["tooling_mold"]})
        for row in tables["tooling_mold"]:
            self.assertEqual(2, row.excel_row)

    def test_applicable_pns_stays_multivalue_raw(self):
        rows = [self.HEADER, ["WL", "M1", "模具一", "PGLINKAGE", "P1，P2", 2, 30]]
        self.make({"工装模具约束 Tooling-Mold": rows})
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual("P1，P2", tables["tooling_mold"][0].values["applicable_pns"])

    def test_mold_dimension_is_deduped(self):
        rows = [self.HEADER,
                ["WL", "M1", "模具一", "PGLINKAGE", "P1", 2, 30],
                ["WL", "M1", "模具一", "轴套6", "P1", 2, 30]]
        self.make({"工装模具约束 Tooling-Mold": rows})
        tables, log = read_workbook(self.path, "WL")
        self.assertEqual(2, len(tables["tooling_mold"]))
        self.assertEqual(1, len(tables["mold"]))
        self.assertFalse(log.has_errors())

    def test_empty_line_cell_warns_and_skips(self):
        rows = [self.HEADER, ["WL", "M1", "模具一", None, "P1", 2, 30]]
        self.make({"工装模具约束 Tooling-Mold": rows})
        tables, log = read_workbook(self.path, "WL")
        self.assertEqual(0, len(tables["tooling_mold"]))
        self.assertIn("适用产线/设备", {i.column for i in log.warnings})


class TestMaterialArrivalSeq(_TempWorkbook):
    HEADER = ["站点", "合格供应商", "物料料号 (PN)", "物料描述", "预计入库日期",
              "预计入库班次", "预计入库时间", "送达数量 (件)"]

    def test_seq_numbers_within_group(self):
        rows = [self.HEADER,
                ["WL", "S", "NCICLABEL01", "标签", dt.date(2026, 11, 3), "早班", None, 100],
                ["WL", "S", "NCICLABEL01", "标签", dt.date(2026, 11, 3), "早班", None, 200],
                ["WL", "S", "NCICLABEL01", "标签", dt.date(2026, 11, 4), "早班", None, 300]]
        self.make({"关键物料到货 Material-Arrival-ETA": rows})
        tables, _ = read_workbook(self.path, "WL")
        seqs = [r.values["seq"] for r in tables["material_arrival"]]
        self.assertEqual([1, 2, 1], seqs)

    def test_shift_name_stays_raw(self):
        rows = [self.HEADER,
                ["WL", "S", "P1", "d", dt.date(2026, 11, 3), "早班", None, 100]]
        self.make({"关键物料到货 Material-Arrival-ETA": rows})
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual("早班", tables["material_arrival"][0].values["eta_shift"])


class TestSynthesizedEntities(_TempWorkbook):
    def _full(self):
        fg_header = ["站点", "成品料号 (PN)", "品名描述", "最小包装量 MPQ", "最小生产量 MOQ",
                     "产品族名", "模具编号 (Mold ID)"]
        sf_header = ["站点", "半成品料号 (PN)", "品名描述", "最小起投批量 MPQ", "报废率 (小数)",
                     "最小起订量 MOQ"]
        bom_header = ["站点", "母件：成品料号 (FG PN)", "母件品名描述",
                      "子件：半成品料号 (SF PN)", "子件品名描述", "装配单耗比例"]
        stock_header = ["站点", "物料类型", "物料料号 (PN)", "品名描述", "期初库存 (件)"]
        self.make({
            "产线基础数据": [LINE_HEADER] + LINE_ROWS,
            "产线与成品参数 Parameters-FG": [fg_header, ["WL", "P1", "总成", 48, 0, "FAM1", "M1"]],
            "半成品制造参数 Parameters-SFN": [sf_header, ["WL", "S1", "半成品", 96, 0.02, 0]],
            "产品装配清单 BOM List": [bom_header, ["WL", "P1", "总成", "S1", "半成品", 2.0]],
            "期初可用库存 Initial Stock": [stock_header,
                                           ["WL", "COMP", "C1", "采购件", 252],
                                           ["WL", "FING", "P1", "总成", 10]],
        })

    def test_site_entity(self):
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual(1, len(tables["site"]))
        self.assertEqual("WL", tables["site"][0].values["site_code"])

    def test_calendar_pattern_fixed_four_rows(self):
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        rows = {r.values["pattern_code"]: r.values for r in tables["calendar_pattern"]}
        self.assertEqual({"CONTINUOUS", "6ON1OFF", "5ON2OFF", "4ON3OFF"}, set(rows))
        self.assertEqual(7, rows["CONTINUOUS"]["work_days"])
        self.assertEqual(0, rows["CONTINUOUS"]["rest_days"])
        self.assertEqual(3, rows["4ON3OFF"]["rest_days"])

    def test_part_union_covers_all_sheets(self):
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        stages = {r.values["pn"]: r.values["stage"] for r in tables["part"]}
        self.assertEqual("FG", stages["P1"])
        self.assertEqual("SF", stages["S1"])
        self.assertEqual("COMP", stages["C1"])

    def test_part_stage_raw_value_is_preserved_for_stock_only_pn(self):
        # FING 归一化在类型门做，适配器保留原值
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        stock_only = [r for r in tables["part"] if r.values["pn"] == "C1"]
        self.assertEqual("COMP", stock_only[0].values["stage"])

    def test_part_precedence_fg_over_stock(self):
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        p1 = [r for r in tables["part"] if r.values["pn"] == "P1"][0]
        self.assertEqual("FG", p1.values["stage"])
        self.assertEqual("总成", p1.values["description"])
        self.assertEqual("FAM1", p1.values["family_code"])

    def test_part_uom_constant(self):
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        self.assertTrue(all(r.values["uom"] == "件" for r in tables["part"]))

    def test_part_rows_are_synthesized(self):
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        for row in tables["part"]:
            with self.subTest(pn=row.values["pn"]):
                self.assertIsNone(row.excel_row)
                self.assertIsNone(row.sheet)

    def test_product_family_from_part(self):
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        codes = {r.values["family_code"] for r in tables["product_family"]}
        self.assertEqual({"FAM1"}, codes)

    def test_stage_ambiguity_warns(self):
        fg_header = ["站点", "成品料号 (PN)", "品名描述", "最小包装量 MPQ"]
        sf_header = ["站点", "半成品料号 (PN)", "品名描述", "最小起投批量 MPQ", "报废率 (小数)"]
        self.make({
            "产线基础数据": [LINE_HEADER] + LINE_ROWS,
            "产线与成品参数 Parameters-FG": [fg_header, ["WL", "X1", "d", 48]],
            "半成品制造参数 Parameters-SFN": [sf_header, ["WL", "X1", "d", 96, 0.0]],
        })
        tables, log = read_workbook(self.path, "WL")
        stages = [r.values["stage"] for r in tables["part"] if r.values["pn"] == "X1"]
        self.assertEqual(["FG"], stages)
        self.assertTrue(any("阶段歧义" in i.message for i in log.warnings))


class TestDuplicateDetectionIsNotAdapterJob(_TempWorkbook):
    def test_identical_leadtime_duplicates_pass_through(self):
        header = ["站点", "料号 (PN)", "阶段", "工序序号", "工序名称",
                  "提前准备时间（分钟）", "转运批量 (件)", "容器"]
        row = ["WL", "WCA1927", "SFIN", 10, "静置", 45, 100, "A"]
        self.make({"工序提前期 Operation-LeadTime": [header] + [row] * 38})
        tables, log = read_workbook(self.path, "WL")
        self.assertEqual(38, len(tables["operation_leadtime"]))
        self.assertFalse(log.has_errors())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_excel_adapter -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'aps.data.excel'`

- [ ] **Step 4: 实现 `aps/data/excel.py`**

```python
from __future__ import annotations

import datetime as dt
import os
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from aps.data.issues import IssueLog
from aps.schema.registry import REGISTRY, EntitySpec, entities_with_sheet
from aps.schema.types import ContractTier, DataType, ParseError, parse_scalar

__all__ = ["HEADER_SCAN_ROWS", "RawRow", "RawTables", "locate_header", "read_workbook"]

HEADER_SCAN_ROWS = 6

RawTables = dict[str, list["RawRow"]]

_SPLIT_RE = re.compile(r"[,，]")

_CALENDAR_PATTERNS = (
    ("CONTINUOUS", 7, 0),
    ("6ON1OFF", 6, 1),
    ("5ON2OFF", 5, 2),
    ("4ON3OFF", 4, 3),
)


@dataclass
class RawRow:
    entity: str
    sheet: str | None
    excel_row: int | None
    values: dict[str, Any]


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _looks_like_date(value: Any) -> bool:
    if isinstance(value, (dt.datetime, dt.date)):
        return True
    if not isinstance(value, str):
        return False
    try:
        parse_scalar(value, DataType.DATE)
    except ParseError:
        return False
    return True


def locate_header(ws: Worksheet, spec: EntitySpec) -> tuple[int | None, dict[str, int], set[str]]:
    header_to_field = spec.header_to_field
    best_row: int | None = None
    best_score = 0
    best_map: dict[str, int] = {}
    best_hit: set[str] = set()
    limit = min(HEADER_SCAN_ROWS, ws.max_row or 0)
    for row_index in range(1, limit + 1):
        mapping: dict[str, int] = {}
        hit: set[str] = set()
        for col_index in range(1, (ws.max_column or 0) + 1):
            raw = ws.cell(row_index, col_index).value
            if _blank(raw):
                continue
            text = str(raw).strip()
            field = header_to_field.get(text)
            if field is not None and field.name not in mapping:
                mapping[field.name] = col_index
                hit.add(text)
        if len(mapping) > best_score:
            best_score = len(mapping)
            best_row = row_index
            best_map = mapping
            best_hit = hit
    if best_score == 0:
        return None, {}, set()
    return best_row, best_map, best_hit


def _row_is_valid(spec: EntitySpec, values: dict[str, Any]) -> bool:
    for field in spec.fields:
        if not field.pk or not field.source_columns:
            continue
        if _blank(values.get(field.name)):
            return False
    return True


def _cell_values(ws: Worksheet, spec: EntitySpec, col_of: dict[str, int],
                 row_index: int) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in spec.fields:
        if not field.source_columns:
            continue
        col = col_of.get(field.name)
        if col is None:
            continue
        values[field.name] = ws.cell(row_index, col).value
    return values


def _report_structure(ws: Worksheet, spec: EntitySpec, header_row: int | None,
                      col_of: dict[str, int], log: IssueLog) -> None:
    if header_row is None:
        log.error(entity=spec.name, sheet=spec.sheet,
                  message=f"无法在表 {spec.sheet!r} 前 {HEADER_SCAN_ROWS} 行内定位表头")
        return
    present: set[str] = set()
    for col_index in range(1, (ws.max_column or 0) + 1):
        raw = ws.cell(header_row, col_index).value
        if not _blank(raw):
            present.add(str(raw).strip())
    missing = sorted(
        field.source_columns[0]
        for field in spec.fields
        if field.tier is ContractTier.T1 and field.source_columns
        and field.name not in col_of
    )
    for column in missing:
        log.error(entity=spec.name, sheet=spec.sheet, column=column, row=header_row,
                  message="缺少必需列（T1）")
    deprecated = spec.deprecated_headers & present
    for column in sorted(deprecated):
        log.warning(entity=spec.name, sheet=spec.sheet, column=column, row=header_row,
                    message="已废弃列残留，适配器不读取（spec §3.3）")
    extra = present - set(spec.header_to_field) - deprecated
    for column in sorted(extra):
        log.warning(entity=spec.name, sheet=spec.sheet, column=column, row=header_row,
                    message="模板未定义的多余列，已忽略")


def _extract_plain(ws: Worksheet, spec: EntitySpec, col_of: dict[str, int],
                   header_row: int, site: str) -> Iterator[RawRow]:
    for row_index in range(header_row + 1, (ws.max_row or 0) + 1):
        values = _cell_values(ws, spec, col_of, row_index)
        if spec.site_injected:
            values["site_code"] = site
        if not _row_is_valid(spec, values):
            continue
        yield RawRow(spec.name, spec.sheet, row_index, values)


def _extract_demand(ws: Worksheet, spec: EntitySpec, col_of: dict[str, int],
                    header_row: int, site: str) -> Iterator[RawRow]:
    known = set(spec.header_to_field)
    date_cols: list[tuple[int, Any]] = []
    for col_index in range(1, (ws.max_column or 0) + 1):
        raw = ws.cell(header_row, col_index).value
        if _blank(raw):
            continue
        text = str(raw).strip()
        if text in known:
            continue
        if _looks_like_date(raw):
            date_cols.append((col_index, raw))
    pn_col = col_of.get("pn")
    if pn_col is None or not date_cols:
        return
    for row_index in range(header_row + 1, (ws.max_row or 0) + 1):
        base = _cell_values(ws, spec, col_of, row_index)
        base["site_code"] = site if spec.site_injected else base.get("site_code")
        if _blank(base.get("pn")):
            continue
        for col_index, header_value in date_cols:
            raw = ws.cell(row_index, col_index).value
            number = _as_number(raw)
            if number is None or number <= 0:
                continue
            values = dict(base)
            values["demand_date"] = header_value
            values["quantity"] = raw
            yield RawRow(spec.name, spec.sheet, row_index, values)


def _extract_tooling(ws: Worksheet, spec: EntitySpec, col_of: dict[str, int],
                     header_row: int, site: str, log: IssueLog) -> Iterator[RawRow]:
    line_col = col_of.get("line_code")
    for row_index in range(header_row + 1, (ws.max_row or 0) + 1):
        values = _cell_values(ws, spec, col_of, row_index)
        if _blank(values.get("mold_id")):
            continue
        raw_lines = values.get("line_code")
        lines = [p for p in (s.strip() for s in _SPLIT_RE.split(str(raw_lines or ""))) if p]
        if not lines:
            log.warning(entity=spec.name, field="line_code", sheet=spec.sheet, row=row_index,
                        column="适用产线/设备", value=raw_lines,
                        message="适用产线为空，无法确定模具-产线关系，已跳过该行")
            continue
        for line_code in lines:
            row_values = dict(values)
            row_values["line_code"] = line_code
            yield RawRow(spec.name, spec.sheet, row_index, row_values)


def _assign_arrival_seq(rows: list[RawRow]) -> None:
    counters: dict[tuple, int] = defaultdict(int)
    for row in rows:
        key = (row.values.get("site_code"), row.values.get("pn"),
               row.values.get("eta_date"), row.values.get("eta_shift"))
        counters[key] += 1
        row.values["seq"] = counters[key]


def _dedupe_molds(rows: list[RawRow], log: IssueLog) -> list[RawRow]:
    spec = REGISTRY["mold"]
    best: dict[tuple, RawRow] = {}
    for row in rows:
        key = tuple(row.values.get(name) for name in spec.pk)
        existing = best.get(key)
        if existing is None:
            best[key] = row
            continue
        differing = [
            field.name for field in spec.fields
            if field.name not in spec.pk
            and str(row.values.get(field.name)) != str(existing.values.get(field.name))
        ]
        if differing:
            log.error(entity="mold", sheet=row.sheet, row=row.excel_row,
                      message=f"同一模具在第 {existing.excel_row} 行与第 {row.excel_row} 行"
                              f"声明不一致的字段: {differing}")
    return [best[key] for key in sorted(best, key=lambda k: tuple(str(x) for x in k))]


def _build_parts(tables: RawTables, site: str, log: IssueLog) -> list[RawRow]:
    parts: dict[str, dict[str, Any]] = {}

    def offer(pn: Any, stage: Any, description: Any, family: Any, source: str) -> None:
        if _blank(pn):
            return
        key = str(pn).strip()
        current = parts.get(key)
        if current is None:
            parts[key] = {"site_code": site, "pn": key, "stage": stage,
                          "description": description, "family_code": family,
                          "uom": "件", "_source": source}
            return
        if current["_source"] in ("part_param_fg", "part_param_sf") \
                and source in ("part_param_fg", "part_param_sf") \
                and current["_source"] != source:
            log.warning(entity="part", field="stage", sheet=None, row=None, column=None,
                        value=key,
                        message=f"料号 {key} 同时出现在成品与半成品参数表，阶段歧义，取 "
                                f"{current['stage']}")
        if _blank(current["description"]) and not _blank(description):
            current["description"] = description
        if _blank(current["family_code"]) and not _blank(family):
            current["family_code"] = family

    for row in tables.get("part_param_fg", ()):
        offer(row.values.get("pn"), "FG", row.values.get("description"),
              row.values.get("family_code"), "part_param_fg")
    for row in tables.get("part_param_sf", ()):
        offer(row.values.get("pn"), "SF", row.values.get("description"),
              row.values.get("family_code"), "part_param_sf")
    for row in tables.get("routing", ()):
        pn = row.values.get("pn")
        stage = row.values.get("stage")
        key = str(pn).strip() if not _blank(pn) else ""
        existing = parts.get(key)
        if existing is not None and not _blank(stage) \
                and str(existing["stage"]) not in (str(stage),) \
                and existing["_source"] not in ("part_param_fg", "part_param_sf"):
            log.warning(entity="part", field="stage", sheet=row.sheet, row=row.excel_row,
                        value=pn,
                        message=f"料号 {pn} 的 Routing 阶段 {stage!r} 与已定阶段 "
                                f"{existing['stage']!r} 不一致")
        offer(pn, stage, None, row.values.get("family_code"), "routing")
    for row in tables.get("bom", ()):
        offer(row.values.get("parent_pn"), "FG", row.values.get("parent_description"),
              None, "bom")
        offer(row.values.get("child_pn"), "SF", row.values.get("child_description"),
              None, "bom")
    for row in tables.get("initial_stock", ()):
        offer(row.values.get("pn"), row.values.get("material_type"),
              row.values.get("description"), None, "initial_stock")
    for row in tables.get("demand", ()):
        offer(row.values.get("pn"), "FG", row.values.get("description"), None, "demand")

    out: list[RawRow] = []
    for key in sorted(parts):
        values = {k: v for k, v in parts[key].items() if not k.startswith("_")}
        if _blank(values["stage"]):
            values["stage"] = "COMP"
        out.append(RawRow("part", None, None, values))
    return out


def _build_families(tables: RawTables, site: str) -> list[RawRow]:
    codes = {
        str(row.values["family_code"]).strip()
        for row in tables.get("part", ())
        if not _blank(row.values.get("family_code"))
    }
    return [
        RawRow("product_family", None, None,
               {"site_code": site, "family_code": code, "name": code})
        for code in sorted(codes)
    ]


def read_workbook(path: str | os.PathLike[str], site: str) -> tuple[RawTables, IssueLog]:
    log = IssueLog()
    wb = openpyxl.load_workbook(os.fspath(path), data_only=True)
    sheets = {str(ws.title).strip(): ws for ws in wb.worksheets}
    tables: RawTables = {}

    for spec in entities_with_sheet():
        ws = sheets.get(spec.sheet)
        if ws is None:
            if spec.sheet_required:
                log.error(entity=spec.name, sheet=spec.sheet, message="工作表缺失")
            else:
                log.warning(entity=spec.name, sheet=spec.sheet,
                            message="工作表缺失（V3 扩展表，V2 工作簿合法缺省）")
            continue
        header_row, col_of, _hit = locate_header(ws, spec)
        _report_structure(ws, spec, header_row, col_of, log)
        if header_row is None:
            continue
        if spec.unpivot == "demand_date":
            rows = list(_extract_demand(ws, spec, col_of, header_row, site))
        elif spec.name == "tooling_mold":
            rows = list(_extract_tooling(ws, spec, col_of, header_row, site, log))
        else:
            rows = list(_extract_plain(ws, spec, col_of, header_row, site))
        existing = tables.setdefault(spec.name, [])
        existing.extend(rows)

    if "material_arrival" in tables:
        _assign_arrival_seq(tables["material_arrival"])
    if "tooling_mold" in tables:
        tables["mold"] = _dedupe_molds(tables.get("mold", []), log)

    tables["site"] = [RawRow("site", None, None, {"site_code": site})]
    tables["calendar_pattern"] = [
        RawRow("calendar_pattern", None, None,
               {"pattern_code": code, "work_days": work, "rest_days": rest})
        for code, work, rest in _CALENDAR_PATTERNS
    ]
    tables["part"] = _build_parts(tables, site, log)
    tables["product_family"] = _build_families(tables, site)
    return tables, log
```

实现注意事项（按顺序检查，每条都对应一个测试）：

- `mold` 与 `tooling_mold` 共用同一张 sheet，循环会跑两遍该 sheet。`mold` 那一遍走 `_extract_plain`，`tooling_mold` 那一遍走 `_extract_tooling`。`tables.setdefault(...).extend(...)` 保证不会互相覆盖。
- `_dedupe_molds` 在 `mold` 行收集完之后调用。它必须对 key 做**显式排序**再输出（G20 确定性）。
- `_extract_demand` 里 `base["site_code"] = site if spec.site_injected else base.get("site_code")`：`demand` 有 `站点` 列，所以走 `base.get`；该行写法是为了统一，不要改成无条件注入。
- `_looks_like_date` 必须排除纯数字（`openpyxl` 不会把 `2026-11-03` 变成数字，但业务可能填 `1`），`parse_scalar(1, DATE)` 会抛 `ParseError`，因此返回 `False`，安全。

- [ ] **Step 5: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_excel_adapter -v`
Expected: PASS（全部）

- [ ] **Step 6: 更新 `aps/data/__init__.py`**

```python
from __future__ import annotations

from aps.data.excel import RawRow, RawTables, locate_header, read_workbook
from aps.data.issues import Issue, IssueLog, Severity
from aps.data.repository import Repository, Row
from aps.data.sqlite import SqliteRepository, decode_value, encode_value

__all__ = [
    "Issue", "IssueLog", "Severity",
    "RawRow", "RawTables", "locate_header", "read_workbook",
    "Repository", "Row", "SqliteRepository", "decode_value", "encode_value",
]
```

- [ ] **Step 7: 用真实 V2 工作簿做一次冒烟（不写测试，只手工核对）**

Run:
```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "
from aps.data.excel import read_workbook
tables, log = read_workbook('APS_DataSource_Template - WL -V2.xlsx', 'WL')
for name in sorted(tables):
    print(name, len(tables[name]))
print('errors', len(log.errors), 'warnings', len(log.warnings))
for i in log.errors[:20]:
    print('E', i.sheet, i.column, i.message)
"
```
Expected（逐条核对，数字必须对上「模板实测事实」表）：
- `part_param_fg` = **64**，`part_param_sf` = **55**，`routing` = **125**，`bom` = **125**
- `initial_stock` = **4357**，`tooling_mold` = **16**，`mold` = **9**
- `line` = **17**，`setup_matrix` = **37**，`inventory_policy` = **917**，`operation_leadtime` = **944**
- `preventive_maintenance` = **11**，`customer` = **21**，`material_arrival` = **1**
- `demand` = **257**（34 个日期列的全部正数对）
- `calendar_pattern` = **4**，`site` = **1**
- `errors` = **0**
- `part` 数量 ≈ 4200+（并集，含 4173 个只出现在 `Initial Stock` 的 PN）

若任一数字不符，**不要改测试期望去迎合**——先确认是适配器 bug 还是本计划的实测数字过期，用同样的脚本重新数一遍真实工作簿再决定。

- [ ] **Step 8: 提交**

```bash
git add aps/data/issues.py aps/data/excel.py aps/data/__init__.py aps_tests/test_excel_adapter.py
git commit -m "feat(data): Excel 适配器按表头定位并把 15 表整形为 23 实体的原始行"
```

---

## Task 6: 四道校验门与 `ValidationReport`

**Files:**
- Create: `aps/data/validate.py`
- Modify: `aps/data/__init__.py`
- Modify: `Specification/aps_greenfield_rewrite.md`（记录 A-6 / A-7，见 Step 1）
- Test: `aps_tests/test_validate_gates.py`

**对 spec 的两处修订**：

- **A-6 参照门不检查 `demand_date ∈ horizon`**：spec §4.3 把它列进参照门。但实测需求宽表跨 **34 个日期列（2026-11-03 ~ 2026-12-06）**，共 **257** 个正数 `(PN, date)` 对，其中落在 10 天窗口内的只有 **10 列 / 94 对**。若参照门按窗口报错，业务每填一张 30 天需求表就会被 163 条 error 淹没。**裁决：窗口过滤是 `DataSet` 层（Task 8）的职责，参照门只检查 `pn ∈ part`，不检查日期。**
- **A-7 §3.2「单班工时/每天班次数不一致报 warn」不可实现，删除该检查**：该裁决要求比较 `Parameters-FG.单班工时` 与 `产线基础数据.单班工时`。但 V2 模板的 `Parameters-FG` 表**已经删掉了 `产线` 列**（spec §3.3 废弃列），因此一行 FG 参数**无法归属到任何产线**，两值无从配对。裁决落地为：这两列**完全不读取**（Task 3 已把它们排除在 `header_to_field` 之外，`test_fg_params_do_not_read_shift_geometry` 守着），一致性检查删除。§3.2 中其余裁决均可实现，保留。

**Interfaces:**
- Consumes: `aps.data.excel.RawRow` / `RawTables`；`aps.data.issues.Issue` / `IssueLog` / `Severity`；`aps.schema.registry`；`aps.schema.types.parse_scalar` / `parse_enum_code` / `parse_shift_index` / `ParseError` / `STAGE_NORMALIZE`
- Produces:
  ```python
  # aps/data/validate.py
  ParsedTables = dict[str, list["CandidateRow"]]

  @dataclass
  class CandidateRow:
      entity: str
      excel_row: int | None
      values: dict[str, Any]        # 已解析的规范值（可安全交给 Repository）

  @dataclass(frozen=True)
  class ValidationReport:
      errors: tuple[Issue, ...]
      warnings: tuple[Issue, ...]
      gaps: tuple[Issue, ...]

      @property
      def ok(self) -> bool                     # not errors
      def render(self, limit: int = 40) -> str # 人类可读汇总，用于 CLI
      def counts(self) -> dict[str, int]       # {"error": n, "warning": n, "gap": n}

  def validate(tables: RawTables, structure_log: IssueLog) -> tuple[ParsedTables, ValidationReport]
      # 依次执行：类型门 → 参照门 → 业务门。structure_log 是适配器产出的结构门结果，
      # 其 error/warning 被并入最终 report（结构门本身不在此重跑）。

  # 可单独调用的门（测试直接打这些）
  def gate_types(tables, log) -> ParsedTables
  def gate_references(parsed, log) -> None
  def gate_business(parsed, log) -> None
  def resolve_duplicates(parsed, log) -> ParsedTables   # 业务门内调用，按 dup_policy 收敛
  def detect_gaps(parsed, log) -> None                  # 类型门内调用，T2 整列为空 → GAP
  ```

**四道门的失败级别（逐条写死）**：

| 门 | 检查 | 级别 |
|---|---|---|
| 结构门（Task 5 已实现） | 缺 sheet（required）/ 缺 T1 列 / 表头无法定位 | `error` |
| 结构门 | 缺 sheet（V3 可选）/ 多余列 / 废弃列残留 | `warning` |
| 类型门 | `parse_scalar` / `resolved_parser` 抛 `ParseError` | `error`，带 `sheet/row/column/原值` |
| 类型门 | T1 字段解析后为 `None` 且 `nullable=False` | `error` |
| 类型门 | `enum_codes` 声明但解析结果不在集合内 | `error` |
| 类型门 | 某 T2 字段在**该实体全部行**中都是 `None` | `gap`（每字段一条，不是每行一条） |
| 参照门 | `fk` 指向的目标 PK 集合中不存在该值，且 `fk_optional=False` | `error` |
| 参照门 | 同上但 `fk_optional=True` | `warning` |
| 业务门 | 取值域违规（见下表） | `error` |
| 业务门 | 重复 PK，`dup_policy=error` | `error` |
| 业务门 | 重复 PK，`dup_policy=sum`，被求和字段数值不同 | `warning`（求和后照常入库，消息含全部原始 Excel 行号） |
| 业务门 | 重复 PK，`dup_policy=dedupe`，所有非 PK 字段逐字节相同 | `warning`（保留首行） |
| 业务门 | 重复 PK，`dup_policy=dedupe`，任一非 PK 字段不同 | `error` |
| 业务门 | §3.2 `Routing.产品族名` 与 `part.family_code` 不一致 | `warning` |

**取值域清单（业务门，逐条）**：

| 实体.字段 | 约束 |
|---|---|
| `line.trp_efficiency` | `0 < v <= 1` |
| `line.shift_hours` | `v > 0` |
| `line.shifts_per_day` | `v >= 1` |
| `part_param_fg.mpq` | `v > 0` |
| `part_param_sf.mpq` | `v > 0` |
| `part_param_sf.scrap_rate` | `0 <= v < 1` |
| `routing.ct_sec` | `v > 0` |
| `routing.op_seq` | `v >= 0` |
| `routing.valid_from` / `valid_to` | 两者都非空时 `valid_from < valid_to` |
| `bom.ratio` | `v > 0` |
| `initial_stock.quantity` | `v >= 0` |
| `demand.quantity` | `v > 0` |
| `material_arrival.quantity` | `v > 0` |
| `material_arrival.eta_shift` | `v >= 0` |
| `inventory_policy.safety_stock` | `v >= 0`（非空时） |
| `inventory_policy.max_stock` | `v >= 0`（非空时） |
| `inventory_policy.start_day` / `end_day` | `start_day <= end_day` |
| `setup_matrix.changeover_min` | `v >= 0` |
| `tooling_mold.available_sets` | `v >= 1` |
| `mold.available_sets` | `v >= 1`（非空时） |
| `preventive_maintenance.downtime_hours` | `v >= 0` |
| `preventive_maintenance.capacity_pct` | `0 <= v <= 100`（非空时） |
| `operation_leadtime.prep_minutes` | `v >= 0`（非空时） |
| `operation_leadtime.transfer_batch` | `v >= 1`（非空时） |

- [ ] **Step 1: 把 A-6 / A-7 写回 spec**

在 `Specification/aps_greenfield_rewrite.md` §4.3 的表格之后、`校验返回 ValidationReport{errors, warnings, gaps}` 那一行之前，插入：

```markdown
**Stage A 实施期修订（2026-11-05）**：

- **A-6**：参照门**不检查** `demand_date ∈ horizon`。实测 V2 需求宽表跨 34 个日期列（2026-11-03 ~ 2026-12-06）共 257 个正数 `(PN, date)` 对，落在 10 天窗口内的只有 10 列 / 94 对。按窗口报错会让业务每填一张 30 天需求表就被 163 条 error 淹没。窗口过滤是 `DataSet` 层的职责；参照门只检查 `pn ∈ part`。
- **A-7**：§3.2 的「单班工时 / 每天班次数两值不一致报 warn」**删除**。V2 模板的 `Parameters-FG` 已删掉 `产线` 列（§3.3），一行 FG 参数无法归属到任何产线，两值无从配对。落地为这两列完全不读取。§3.2 其余裁决保留。
```

- [ ] **Step 2: 写失败测试**

创建 `aps_tests/test_validate_gates.py`：

```python
from __future__ import annotations

import datetime as dt
import unittest

from aps.data.excel import RawRow
from aps.data.issues import IssueLog, Severity
from aps.data.validate import (
    CandidateRow,
    ValidationReport,
    detect_gaps,
    gate_business,
    gate_references,
    gate_types,
    resolve_duplicates,
    validate,
)

FAST = True


def raw(entity, values, excel_row=2, sheet="S"):
    return RawRow(entity=entity, sheet=sheet, excel_row=excel_row, values=dict(values))


def line_raw(code="PGLINKAGE", trp=0.85, hours=8.0, shifts=3, **kw):
    values = {"site_code": "WL", "line_code": code, "name": code,
              "trp_efficiency": trp, "shift_hours": hours, "shifts_per_day": shifts}
    values.update(kw)
    return raw("line", values)


def site_tables(*extra):
    tables = {"site": [raw("site", {"site_code": "WL"}, sheet=None, excel_row=None)]}
    for row in extra:
        tables.setdefault(row.entity, []).append(row)
    return tables


def parse(tables):
    log = IssueLog()
    parsed = gate_types(tables, log)
    return parsed, log


class TestTypeGate(unittest.TestCase):
    def test_scalars_parsed(self):
        parsed, log = parse(site_tables(line_raw()))
        row = parsed["line"][0]
        self.assertEqual(0.85, row.values["trp_efficiency"])
        self.assertEqual(8.0, row.values["shift_hours"])
        self.assertEqual(3, row.values["shifts_per_day"])
        self.assertFalse(log.has_errors())

    def test_stage_normalization(self):
        tables = site_tables(raw("routing", {
            "site_code": "WL", "pn": "P1", "op_seq": "10", "line_code": "L1",
            "stage": "SFIN", "certified": "True", "ct_sec": "27",
        }))
        parsed, log = parse(tables)
        self.assertEqual("SF", parsed["routing"][0].values["stage"])
        self.assertEqual(10, parsed["routing"][0].values["op_seq"])
        self.assertIs(True, parsed["routing"][0].values["certified"])
        self.assertEqual(27.0, parsed["routing"][0].values["ct_sec"])
        self.assertFalse(log.has_errors())

    def test_initial_stock_material_type_normalized(self):
        tables = site_tables(raw("initial_stock", {
            "site_code": "WL", "pn": "P1", "material_type": "FING", "quantity": "10",
        }))
        parsed, _ = parse(tables)
        self.assertEqual("FG", parsed["initial_stock"][0].values["material_type"])

    def test_shift_name_to_index(self):
        tables = site_tables(raw("material_arrival", {
            "site_code": "WL", "pn": "P1", "eta_date": dt.date(2026, 11, 3),
            "eta_shift": "早班", "seq": 1, "quantity": 100,
        }))
        parsed, log = parse(tables)
        self.assertEqual(0, parsed["material_arrival"][0].values["eta_shift"])
        self.assertFalse(log.has_errors())

    def test_enum_code_extraction(self):
        tables = site_tables(raw("customer", {
            "site_code": "WL", "customer_code": "C1", "name": "客户",
            "tier": "TIER_A (核心A级)",
        }))
        parsed, log = parse(tables)
        self.assertEqual("TIER_A", parsed["customer"][0].values["tier"])
        self.assertFalse(log.has_errors())

    def test_pm_disposition_enum_code(self):
        tables = site_tables(raw("preventive_maintenance", {
            "site_code": "WL", "line_code": "L1", "start_date": dt.date(2026, 11, 3),
            "item": "保养", "downtime_hours": 4.0,
            "disposition": "避让顺延 (Shift_Delay)",
        }))
        parsed, _ = parse(tables)
        self.assertEqual("Shift_Delay", parsed["preventive_maintenance"][0].values["disposition"])

    def test_multivalue_parsed_to_tuple(self):
        tables = site_tables(raw("tooling_mold", {
            "site_code": "WL", "mold_id": "M1", "line_code": "L1",
            "applicable_pns": "P1，P2", "available_sets": "2",
        }))
        parsed, _ = parse(tables)
        self.assertEqual(("P1", "P2"), parsed["tooling_mold"][0].values["applicable_pns"])
        self.assertEqual(2, parsed["tooling_mold"][0].values["available_sets"])

    def test_unparseable_value_is_error_with_location(self):
        tables = site_tables(line_raw(trp="很高"))
        log = IssueLog()
        gate_types(tables, log)
        errors = log.errors
        self.assertEqual(1, len(errors))
        issue = errors[0]
        self.assertEqual(Severity.ERROR, issue.severity)
        self.assertEqual("line", issue.entity)
        self.assertEqual("trp_efficiency", issue.field)
        self.assertEqual(2, issue.row)
        self.assertEqual("效率 TRP", issue.column)
        self.assertEqual("很高", issue.value)

    def test_missing_non_nullable_is_error(self):
        tables = site_tables(raw("line", {
            "site_code": "WL", "line_code": "L1", "name": "L1",
            "trp_efficiency": None, "shift_hours": 8.0, "shifts_per_day": 3,
        }))
        log = IssueLog()
        gate_types(tables, log)
        self.assertTrue(any(i.field == "trp_efficiency" for i in log.errors))

    def test_pk_must_not_be_null(self):
        tables = site_tables(raw("line", {"site_code": "WL", "line_code": None,
                                          "trp_efficiency": 0.85, "shift_hours": 8.0,
                                          "shifts_per_day": 3}))
        log = IssueLog()
        gate_types(tables, log)
        self.assertTrue(any(i.field == "line_code" for i in log.errors))

    def test_enum_domain_violation_is_error(self):
        tables = site_tables(raw("inventory_policy", {
            "site_code": "WL", "pn": "P1", "period_type": "WEEKLY", "start_day": 1,
            "end_day": 5,
        }))
        log = IssueLog()
        gate_types(tables, log)
        self.assertTrue(any(i.field == "period_type" for i in log.errors))

    def test_bad_date_is_error(self):
        tables = site_tables(raw("routing", {
            "site_code": "WL", "pn": "P1", "op_seq": 10, "line_code": "L1",
            "certified": True, "ct_sec": 27.0, "valid_from": "昨天",
        }))
        log = IssueLog()
        gate_types(tables, log)
        self.assertTrue(any(i.field == "valid_from" for i in log.errors))

    def test_t2_all_null_column_is_one_gap(self):
        rows = [raw("routing", {"site_code": "WL", "pn": f"P{i}", "op_seq": 10,
                                "line_code": "L1", "certified": True, "ct_sec": 27.0,
                                "per_shift_cap_declared": None}, excel_row=i + 2)
                for i in range(3)]
        log = IssueLog()
        gate_types(site_tables(*rows), log)
        gaps = [i for i in log.gaps if i.field == "per_shift_cap_declared"]
        self.assertEqual(1, len(gaps))
        self.assertEqual("routing", gaps[0].entity)

    def test_t1_all_null_column_is_not_a_gap(self):
        log = IssueLog()
        gate_types(site_tables(line_raw()), log)
        self.assertEqual((), log.gaps)

    def test_empty_entity_produces_no_gap(self):
        log = IssueLog()
        gate_types({"site": [raw("site", {"site_code": "WL"}, sheet=None, excel_row=None)],
                    "routing": []}, log)
        self.assertEqual((), tuple(i for i in log.gaps if i.entity == "routing"))


class TestReferentialGate(unittest.TestCase):
    def _parsed(self, rows):
        log = IssueLog()
        return gate_types(site_tables(*rows), log), log

    def test_valid_fk_passes(self):
        parsed, log = self._parsed([
            line_raw(),
            raw("routing", {"site_code": "WL", "pn": "P1", "op_seq": 10,
                            "line_code": "PGLINKAGE", "certified": True, "ct_sec": 27.0}),
            raw("part", {"site_code": "WL", "pn": "P1", "stage": "FG"},
                sheet=None, excel_row=None),
        ])
        gate_references(parsed, log)
        self.assertFalse(log.has_errors())

    def test_unknown_line_is_error(self):
        parsed, log = self._parsed([
            raw("routing", {"site_code": "WL", "pn": "P1", "op_seq": 10,
                            "line_code": "GHOST", "certified": True, "ct_sec": 27.0}),
        ])
        gate_references(parsed, log)
        errors = [i for i in log.errors if i.field == "line_code"]
        self.assertEqual(1, len(errors))
        self.assertEqual("GHOST", errors[0].value)

    def test_null_fk_is_skipped(self):
        parsed, log = self._parsed([
            raw("part_param_fg", {"site_code": "WL", "pn": "P1", "mpq": 48,
                                  "mold_id": None}),
        ])
        gate_references(parsed, log)
        self.assertFalse(log.has_errors())

    def test_optional_fk_downgrades_to_warning(self):
        parsed, log = self._parsed([
            raw("part", {"site_code": "WL", "pn": "P1", "stage": "FG"},
                sheet=None, excel_row=None),
            raw("bom", {"site_code": "WL", "parent_pn": "P1", "child_pn": "NCICLABEL01",
                        "ratio": 2.0}),
        ])
        gate_references(parsed, log)
        self.assertFalse(log.has_errors())
        warnings = [i for i in log.warnings if i.field == "child_pn"]
        self.assertEqual(1, len(warnings))
        self.assertEqual("NCICLABEL01", warnings[0].value)

    def test_required_child_fk_still_errors_for_parent(self):
        parsed, log = self._parsed([
            raw("bom", {"site_code": "WL", "parent_pn": "GHOST", "child_pn": "S1",
                        "ratio": 2.0}),
        ])
        gate_references(parsed, log)
        self.assertTrue(any(i.field == "parent_pn" for i in log.errors))

    def test_out_of_horizon_demand_date_is_not_an_error(self):
        parsed, log = self._parsed([
            raw("part", {"site_code": "WL", "pn": "P1", "stage": "FG"},
                sheet=None, excel_row=None),
            raw("demand", {"site_code": "WL", "pn": "P1",
                           "demand_date": dt.date(2026, 12, 6), "quantity": 10}),
        ])
        gate_references(parsed, log)
        self.assertFalse(log.has_errors())

    def test_initial_stock_pn_never_checked(self):
        parsed, log = self._parsed([
            raw("initial_stock", {"site_code": "WL", "pn": "58113447",
                                  "material_type": "COMP", "quantity": 252}),
        ])
        gate_references(parsed, log)
        self.assertFalse(log.has_errors())


class TestBusinessGate(unittest.TestCase):
    def _parsed(self, rows):
        log = IssueLog()
        return gate_types(site_tables(*rows), log), log

    def test_trp_out_of_range(self):
        for bad in (0, -0.1, 1.5):
            with self.subTest(trp=bad):
                parsed, log = self._parsed([line_raw(trp=bad)])
                gate_business(parsed, log)
                self.assertTrue(any(i.field == "trp_efficiency" for i in log.errors))

    def test_trp_one_is_legal(self):
        parsed, log = self._parsed([line_raw(trp=1.0)])
        gate_business(parsed, log)
        self.assertFalse(log.has_errors())

    def test_mpq_zero_is_error(self):
        parsed, log = self._parsed([
            raw("part_param_fg", {"site_code": "WL", "pn": "P1", "mpq": 0}),
        ])
        gate_business(parsed, log)
        self.assertTrue(any(i.field == "mpq" for i in log.errors))

    def test_scrap_rate_domain(self):
        for bad in (-0.01, 1.0, 1.5):
            with self.subTest(rate=bad):
                parsed, log = self._parsed([
                    raw("part_param_sf", {"site_code": "WL", "pn": "S1", "mpq": 96,
                                          "scrap_rate": bad}),
                ])
                gate_business(parsed, log)
                self.assertTrue(any(i.field == "scrap_rate" for i in log.errors))

    def test_scrap_rate_zero_is_legal(self):
        parsed, log = self._parsed([
            raw("part_param_sf", {"site_code": "WL", "pn": "S1", "mpq": 96,
                                  "scrap_rate": 0.0}),
        ])
        gate_business(parsed, log)
        self.assertFalse(log.has_errors())

    def test_ct_zero_is_error(self):
        parsed, log = self._parsed([
            raw("routing", {"site_code": "WL", "pn": "P1", "op_seq": 10, "line_code": "L1",
                            "certified": True, "ct_sec": 0}),
        ])
        gate_business(parsed, log)
        self.assertTrue(any(i.field == "ct_sec" for i in log.errors))

    def test_valid_from_after_valid_to_is_error(self):
        parsed, log = self._parsed([
            raw("routing", {"site_code": "WL", "pn": "P1", "op_seq": 10, "line_code": "L1",
                            "certified": True, "ct_sec": 27.0,
                            "valid_from": dt.date(2026, 12, 1),
                            "valid_to": dt.date(2026, 11, 1)}),
        ])
        gate_business(parsed, log)
        self.assertTrue(any(i.field == "valid_to" for i in log.errors))

    def test_inventory_policy_day_range(self):
        parsed, log = self._parsed([
            raw("inventory_policy", {"site_code": "WL", "pn": "P1", "period_type": "NORMAL",
                                     "start_day": 20, "end_day": 5}),
        ])
        gate_business(parsed, log)
        self.assertTrue(any(i.field == "end_day" for i in log.errors))

    def test_negative_safety_stock_is_error(self):
        parsed, log = self._parsed([
            raw("inventory_policy", {"site_code": "WL", "pn": "P1", "period_type": "NORMAL",
                                     "start_day": 1, "end_day": 31, "safety_stock": -5}),
        ])
        gate_business(parsed, log)
        self.assertTrue(any(i.field == "safety_stock" for i in log.errors))

    def test_capacity_pct_over_100_is_error(self):
        parsed, log = self._parsed([
            raw("preventive_maintenance", {
                "site_code": "WL", "line_code": "L1", "start_date": dt.date(2026, 11, 3),
                "item": "保养", "downtime_hours": 4.0, "capacity_pct": 120}),
        ])
        gate_business(parsed, log)
        self.assertTrue(any(i.field == "capacity_pct" for i in log.errors))

    def test_family_conflict_with_part_is_warning(self):
        parsed, log = self._parsed([
            raw("part", {"site_code": "WL", "pn": "P1", "stage": "FG",
                         "family_code": "FAM1"}, sheet=None, excel_row=None),
            raw("routing", {"site_code": "WL", "pn": "P1", "op_seq": 10, "line_code": "L1",
                            "certified": True, "ct_sec": 27.0, "family_code": "FAM2"}),
        ])
        gate_business(parsed, log)
        self.assertFalse(log.has_errors())
        self.assertTrue(any("产品族" in i.message for i in log.warnings))

    def test_no_family_conflict_when_routing_blank(self):
        parsed, log = self._parsed([
            raw("part", {"site_code": "WL", "pn": "P1", "stage": "FG",
                         "family_code": "FAM1"}, sheet=None, excel_row=None),
            raw("routing", {"site_code": "WL", "pn": "P1", "op_seq": 10, "line_code": "L1",
                            "certified": True, "ct_sec": 27.0, "family_code": None}),
        ])
        gate_business(parsed, log)
        self.assertFalse(any("产品族" in i.message for i in log.warnings))


class TestDuplicatePolicy(unittest.TestCase):
    def _parsed(self, rows):
        log = IssueLog()
        return gate_types(site_tables(*rows), log), log

    def test_initial_stock_same_pk_sums(self):
        parsed, log = self._parsed([
            raw("initial_stock", {"site_code": "WL", "pn": "58113447",
                                  "material_type": "COMP", "quantity": 252}, excel_row=5),
            raw("initial_stock", {"site_code": "WL", "pn": "58113447",
                                  "material_type": "COMP", "quantity": 3888}, excel_row=912),
        ])
        out = resolve_duplicates(parsed, log)
        rows = out["initial_stock"]
        self.assertEqual(1, len(rows))
        self.assertEqual(4140, rows[0].values["quantity"])
        warnings = [i for i in log.warnings if i.entity == "initial_stock"]
        self.assertEqual(1, len(warnings))
        self.assertIn("5", warnings[0].message)
        self.assertIn("912", warnings[0].message)
        self.assertFalse(log.has_errors())

    def test_initial_stock_different_material_type_is_not_a_duplicate(self):
        parsed, log = self._parsed([
            raw("initial_stock", {"site_code": "WL", "pn": "P1", "material_type": "COMP",
                                  "quantity": 252}),
            raw("initial_stock", {"site_code": "WL", "pn": "P1", "material_type": "PACK",
                                  "quantity": 3888}),
        ])
        out = resolve_duplicates(parsed, log)
        self.assertEqual(2, len(out["initial_stock"]))
        self.assertFalse(log.has_errors())

    def test_leadtime_identical_duplicates_dedupe_with_warning(self):
        base = {"site_code": "WL", "pn": "WCA1927", "op_seq": 10, "stage": "SF",
                "op_name": "静置", "prep_minutes": 45.0, "transfer_batch": 100,
                "container": "A"}
        parsed, log = self._parsed([raw("operation_leadtime", base, excel_row=i + 2)
                                    for i in range(38)])
        out = resolve_duplicates(parsed, log)
        self.assertEqual(1, len(out["operation_leadtime"]))
        self.assertFalse(log.has_errors())
        self.assertTrue(any(i.entity == "operation_leadtime" for i in log.warnings))

    def test_dedupe_policy_with_differing_field_is_error(self):
        parsed, log = self._parsed([
            raw("operation_leadtime", {"site_code": "WL", "pn": "WCA1927", "op_seq": 10,
                                       "prep_minutes": 45.0}, excel_row=2),
            raw("operation_leadtime", {"site_code": "WL", "pn": "WCA1927", "op_seq": 10,
                                       "prep_minutes": 60.0}, excel_row=3),
        ])
        out = resolve_duplicates(parsed, log)
        self.assertEqual(1, len(out["operation_leadtime"]))
        self.assertTrue(any(i.entity == "operation_leadtime" for i in log.errors))

    def test_error_policy_duplicate_is_error(self):
        parsed, log = self._parsed([
            line_raw("PGLINKAGE"), line_raw("PGLINKAGE", trp=0.9),
        ])
        out = resolve_duplicates(parsed, log)
        self.assertEqual(1, len(out["line"]))
        self.assertTrue(log.has_errors())

    def test_mold_dedupe_across_lines_is_silent(self):
        parsed, log = self._parsed([
            raw("mold", {"site_code": "WL", "mold_id": "M1", "name": "模具一",
                         "available_sets": 2, "changeover_min": 30.0}, excel_row=2),
            raw("mold", {"site_code": "WL", "mold_id": "M1", "name": "模具一",
                         "available_sets": 2, "changeover_min": 30.0}, excel_row=3),
        ])
        out = resolve_duplicates(parsed, log)
        self.assertEqual(1, len(out["mold"]))
        self.assertFalse(log.has_errors())

    def test_rows_are_sorted_deterministically(self):
        parsed, log = self._parsed([
            line_raw("Z线"), line_raw("A线"), line_raw("M线"),
        ])
        out = resolve_duplicates(parsed, log)
        self.assertEqual(["A线", "M线", "Z线"],
                         [r.values["line_code"] for r in out["line"]])


class TestGapDetection(unittest.TestCase):
    def test_all_t2_routing_columns_reported(self):
        rows = [raw("routing", {"site_code": "WL", "pn": "P1", "op_seq": 10,
                                "line_code": "L1", "certified": True, "ct_sec": 27.0})]
        log = IssueLog()
        gate_types(site_tables(*rows), log)
        gap_fields = {i.field for i in log.gaps if i.entity == "routing"}
        self.assertIn("per_shift_cap_declared", gap_fields)
        self.assertIn("mold_ids", gap_fields)
        self.assertIn("headcount", gap_fields)


class TestValidateEndToEnd(unittest.TestCase):
    def test_structure_log_errors_are_merged(self):
        structure = IssueLog()
        structure.error(entity="line", sheet="产线基础数据", message="工作表缺失")
        parsed, report = validate(site_tables(line_raw()), structure)
        self.assertFalse(report.ok)
        self.assertEqual(1, report.counts()["error"])
        self.assertIn("line", parsed)

    def test_clean_tables_produce_ok_report(self):
        parsed, report = validate(
            site_tables(
                line_raw(),
                raw("part", {"site_code": "WL", "pn": "P1", "stage": "FG"},
                    sheet=None, excel_row=None),
                raw("part_param_fg", {"site_code": "WL", "pn": "P1", "mpq": 48}),
            ),
            IssueLog(),
        )
        self.assertTrue(report.ok)
        self.assertEqual(0, report.counts()["error"])

    def test_report_render_mentions_counts(self):
        _, report = validate(site_tables(line_raw(trp=9.0)), IssueLog())
        text = report.render()
        self.assertIn("error", text.lower())
        self.assertFalse(report.ok)

    def test_candidate_rows_carry_excel_row(self):
        parsed, _ = validate(site_tables(line_raw()), IssueLog())
        self.assertIsInstance(parsed["line"][0], CandidateRow)
        self.assertEqual(2, parsed["line"][0].excel_row)

    def test_report_is_immutable(self):
        _, report = validate(site_tables(line_raw()), IssueLog())
        self.assertIsInstance(report, ValidationReport)
        with self.assertRaises(Exception):
            report.errors = ()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_validate_gates -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'aps.data.validate'`

- [ ] **Step 4: 实现 `aps/data/validate.py`**

```python
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field as dc_field
from typing import Any

from aps.data.excel import RawRow, RawTables
from aps.data.issues import Issue, IssueLog, Severity
from aps.schema.registry import REGISTRY, STAGE_NORMALIZE, EntitySpec, FieldSpec
from aps.schema.types import (
    ContractTier,
    DataType,
    DupPolicy,
    ParseError,
    parse_enum_code,
    parse_scalar,
    parse_shift_index,
)

__all__ = [
    "CandidateRow",
    "ParsedTables",
    "ValidationReport",
    "detect_gaps",
    "gate_business",
    "gate_references",
    "gate_types",
    "resolve_duplicates",
    "validate",
]

ParsedTables = dict[str, list["CandidateRow"]]

_RANGE_RULES: tuple[tuple[str, str, str, float | None, float | None], ...] = (
    ("line", "trp_efficiency", "gt_le", 0.0, 1.0),
    ("line", "shift_hours", "gt", 0.0, None),
    ("line", "shifts_per_day", "ge", 1.0, None),
    ("part_param_fg", "mpq", "gt", 0.0, None),
    ("part_param_sf", "mpq", "gt", 0.0, None),
    ("part_param_sf", "scrap_rate", "ge_lt", 0.0, 1.0),
    ("routing", "ct_sec", "gt", 0.0, None),
    ("routing", "op_seq", "ge", 0.0, None),
    ("bom", "ratio", "gt", 0.0, None),
    ("initial_stock", "quantity", "ge", 0.0, None),
    ("demand", "quantity", "gt", 0.0, None),
    ("material_arrival", "quantity", "gt", 0.0, None),
    ("material_arrival", "eta_shift", "ge", 0.0, None),
    ("setup_matrix", "changeover_min", "ge", 0.0, None),
    ("tooling_mold", "available_sets", "ge", 1.0, None),
    ("preventive_maintenance", "downtime_hours", "ge", 0.0, None),
    ("preventive_maintenance", "capacity_pct", "ge_le", 0.0, 100.0),
    ("operation_leadtime", "prep_minutes", "ge", 0.0, None),
    ("operation_leadtime", "transfer_batch", "ge", 1.0, None),
)

_NULLABLE_FLOOR_RULES: tuple[tuple[str, str, float], ...] = (
    ("inventory_policy", "safety_stock", 0.0),
    ("inventory_policy", "max_stock", 0.0),
    ("mold", "available_sets", 1.0),
)


@dataclass
class CandidateRow:
    entity: str
    excel_row: int | None
    values: dict[str, Any] = dc_field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        return dict(self.values)


@dataclass(frozen=True)
class ValidationReport:
    errors: tuple[Issue, ...]
    warnings: tuple[Issue, ...]
    gaps: tuple[Issue, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    def counts(self) -> dict[str, int]:
        return {"error": len(self.errors), "warning": len(self.warnings),
                "gap": len(self.gaps)}

    def render(self, limit: int = 40) -> str:
        lines = [
            f"error={len(self.errors)}  warning={len(self.warnings)}  gap={len(self.gaps)}",
        ]
        for label, group in (("ERROR", self.errors), ("WARNING", self.warnings),
                             ("GAP", self.gaps)):
            for issue in group[:limit]:
                lines.append(
                    f"[{label}] {issue.entity or '-'}.{issue.field or '-'} "
                    f"@ {issue.sheet or '-'}"
                    + (f" R{issue.row}" if issue.row is not None else "")
                    + (f" C[{issue.column}]" if issue.column else "")
                    + f" :: {issue.message}"
                    + (f" (原值={issue.value!r})" if issue.value is not None else "")
                )
            if len(group) > limit:
                lines.append(f"[{label}] ... 另有 {len(group) - limit} 条未显示")
        return "\n".join(lines)


def _apply_parser(field: FieldSpec, value: Any) -> Any:
    parser = field.resolved_parser
    if parser == "enum_code":
        return parse_enum_code(value)
    if parser == "shift_index":
        index = parse_shift_index(value)
        if index is None:
            raise ParseError(f"无法解析班次: {value!r}", value)
        return index
    if parser == "stage":
        text = parse_scalar(value, DataType.TEXT)
        if text is None:
            return None
        return STAGE_NORMALIZE.get(text.upper(), text.upper())
    return parse_scalar(value, field.dtype)


def _source_column(spec: EntitySpec, field: FieldSpec) -> str | None:
    return field.source_columns[0] if field.source_columns else None


def _key_of(raw: RawRow) -> str:
    return raw.sheet or raw.entity


def gate_types(tables: RawTables, log: IssueLog) -> ParsedTables:
    parsed: ParsedTables = {}
    for entity in sorted(REGISTRY):
        spec = REGISTRY[entity]
        raws = tables.get(entity, [])
        rows: list[CandidateRow] = []
        for raw_row in raws:
            values: dict[str, Any] = {}
            row_failed = False
            for field in spec.fields:
                if field.name not in raw_row.values and field.provenance == "derived" \
                        and not field.pk:
                    values[field.name] = None
                    continue
                if field.name not in raw_row.values:
                    if field.nullable:
                        values[field.name] = None
                        continue
                    log.error(entity=entity, field=field.name, sheet=raw_row.sheet,
                              row=raw_row.excel_row,
                              column=_source_column(spec, field),
                              message="缺少必需值（T1，非空字段）",
                              value=None)
                    row_failed = True
                    continue
                cell = raw_row.values[field.name]
                try:
                    value = _apply_parser(field, cell)
                except ParseError as exc:
                    log.error(entity=entity, field=field.name, sheet=raw_row.sheet,
                              row=raw_row.excel_row,
                              column=_source_column(spec, field),
                              message=f"类型解析失败: {exc}", value=cell)
                    row_failed = True
                    continue
                if value is None and not field.nullable:
                    log.error(entity=entity, field=field.name, sheet=raw_row.sheet,
                              row=raw_row.excel_row,
                              column=_source_column(spec, field),
                              message="非空字段解析结果为空", value=cell)
                    row_failed = True
                    continue
                if value is not None and field.enum_codes is not None \
                        and value not in field.enum_codes:
                    log.error(entity=entity, field=field.name, sheet=raw_row.sheet,
                              row=raw_row.excel_row,
                              column=_source_column(spec, field),
                              message=f"取值不在允许集合 {field.enum_codes} 内", value=value)
                    row_failed = True
                    continue
                values[field.name] = value
            if not row_failed:
                rows.append(CandidateRow(entity, raw_row.excel_row, values))
        parsed[entity] = rows
    detect_gaps(parsed, log)
    return parsed


def detect_gaps(parsed: ParsedTables, log: IssueLog) -> None:
    for entity in sorted(parsed):
        rows = parsed[entity]
        if not rows:
            continue
        spec = REGISTRY[entity]
        for field in spec.fields:
            if field.tier is not ContractTier.T2:
                continue
            if any(row.values.get(field.name) is not None for row in rows):
                continue
            log.gap(entity=entity, field=field.name, sheet=spec.sheet,
                    column=_source_column(spec, field),
                    message=f"T2 字段整列为空（{len(rows)} 行），依赖它的规则将标记 degraded")


def _pk_sets(parsed: ParsedTables) -> dict[str, set[tuple]]:
    sets: dict[str, set[tuple]] = {}
    for entity, rows in parsed.items():
        spec = REGISTRY[entity]
        keys = set()
        for row in rows:
            keys.add(tuple(row.values.get(name) for name in spec.pk))
        sets[entity] = keys
    return sets


def _fk_value_sets(parsed: ParsedTables) -> dict[str, set[Any]]:
    out: dict[str, set[Any]] = {}
    for entity, rows in parsed.items():
        spec = REGISTRY[entity]
        for field in spec.fields:
            if not field.pk:
                continue
            bucket = out.setdefault(f"{entity}.{field.name}", set())
            for row in rows:
                value = row.values.get(field.name)
                if value is not None:
                    bucket.add(value)
    return out


def gate_references(parsed: ParsedTables, log: IssueLog) -> None:
    targets = _fk_value_sets(parsed)
    for entity in sorted(parsed):
        spec = REGISTRY[entity]
        for field in spec.fields:
            if not field.fk:
                continue
            allowed = targets.get(field.fk, set())
            for row in parsed[entity]:
                value = row.values.get(field.name)
                if value is None or value in allowed:
                    continue
                severity = Severity.WARNING if field.fk_optional else Severity.ERROR
                message = (f"外键 {field.fk} 无对应记录"
                           + ("（可选引用，降级为 warning）" if field.fk_optional else ""))
                log.emit(severity, entity, field.name, spec.sheet, row.excel_row,
                         _source_column(spec, field), message, value)


def _check_range(kind: str, value: float, low: float | None, high: float | None) -> bool:
    if kind == "gt":
        return value > low
    if kind == "ge":
        return value >= low
    if kind == "gt_le":
        return low < value <= high
    if kind == "ge_lt":
        return low <= value < high
    if kind == "ge_le":
        return low <= value <= high
    raise ValueError(f"未知取值域规则 {kind!r}")


def gate_business(parsed: ParsedTables, log: IssueLog) -> None:
    for entity, field_name, kind, low, high in _RANGE_RULES:
        spec = REGISTRY[entity]
        field = spec.field(field_name)
        for row in parsed.get(entity, ()):
            value = row.values.get(field_name)
            if value is None:
                if not field.nullable:
                    log.error(entity=entity, field=field_name, sheet=spec.sheet,
                              row=row.excel_row, column=_source_column(spec, field),
                              message="非空字段无值，无法校验取值域")
                continue
            if not _check_range(kind, float(value), low, high):
                log.error(entity=entity, field=field_name, sheet=spec.sheet,
                          row=row.excel_row, column=_source_column(spec, field),
                          message=f"取值域违规: 期望 {kind}({low}, {high})", value=value)

    for entity, field_name, low in _NULLABLE_FLOOR_RULES:
        spec = REGISTRY[entity]
        field = spec.field(field_name)
        for row in parsed.get(entity, ()):
            value = row.values.get(field_name)
            if value is None:
                continue
            if float(value) < low:
                log.error(entity=entity, field=field_name, sheet=spec.sheet,
                          row=row.excel_row, column=_source_column(spec, field),
                          message=f"取值域违规: 期望 >= {low}", value=value)

    _check_date_order(parsed, log)
    _check_inventory_day_range(parsed, log)
    _check_family_consistency(parsed, log)
    resolve_duplicates(parsed, log)


def _check_date_order(parsed: ParsedTables, log: IssueLog) -> None:
    spec = REGISTRY["routing"]
    for row in parsed.get("routing", ()):
        start = row.values.get("valid_from")
        end = row.values.get("valid_to")
        if start is None or end is None:
            continue
        if not start < end:
            log.error(entity="routing", field="valid_to", sheet=spec.sheet,
                      row=row.excel_row, column="失效日期",
                      message=f"生效日期 {start} 未早于失效日期 {end}", value=end)


def _check_inventory_day_range(parsed: ParsedTables, log: IssueLog) -> None:
    spec = REGISTRY["inventory_policy"]
    for row in parsed.get("inventory_policy", ()):
        start = row.values.get("start_day")
        end = row.values.get("end_day")
        if start is None or end is None:
            continue
        if start > end:
            log.error(entity="inventory_policy", field="end_day", sheet=spec.sheet,
                      row=row.excel_row, column="结束日 (Day)",
                      message=f"起始日 {start} 晚于结束日 {end}", value=end)


def _check_family_consistency(parsed: ParsedTables, log: IssueLog) -> None:
    families = {
        row.values.get("pn"): row.values.get("family_code")
        for row in parsed.get("part", ())
    }
    spec = REGISTRY["routing"]
    for row in parsed.get("routing", ()):
        declared = row.values.get("family_code")
        if declared is None:
            continue
        authoritative = families.get(row.values.get("pn"))
        if authoritative is None or authoritative == declared:
            continue
        log.warning(entity="routing", field="family_code", sheet=spec.sheet,
                    row=row.excel_row, column="产品族名",
                    message=f"产品族 {declared!r} 与 part.family_code "
                            f"{authoritative!r} 不一致，以 part 为准",
                    value=declared)


def _sort_key(spec: EntitySpec, row: CandidateRow) -> tuple:
    return tuple(_sortable(row.values.get(name)) for name in spec.pk)


def _sortable(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:030.10f}"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (tuple, list)):
        return ",".join(str(v) for v in value)
    return str(value)


def resolve_duplicates(parsed: ParsedTables, log: IssueLog) -> ParsedTables:
    out: ParsedTables = {}
    for entity in sorted(parsed):
        spec = REGISTRY[entity]
        grouped: dict[tuple, list[CandidateRow]] = defaultdict(list)
        for row in parsed[entity]:
            grouped[tuple(row.values.get(name) for name in spec.pk)].append(row)
        resolved: list[CandidateRow] = []
        for key in grouped:
            rows = grouped[key]
            if len(rows) == 1:
                resolved.append(rows[0])
                continue
            handler = _resolve_group(spec, key, rows, log)
            if handler is not None:
                resolved.append(handler)
        resolved.sort(key=lambda r: _sort_key(spec, r))
        out[entity] = resolved
    parsed.clear()
    parsed.update(out)
    return parsed


def _non_pk_differences(spec: EntitySpec, rows: list[CandidateRow]) -> list[str]:
    differing: list[str] = []
    for field in spec.fields:
        if field.name in spec.pk:
            continue
        values = {_sortable(row.values.get(field.name)) for row in rows}
        if len(values) > 1:
            differing.append(field.name)
    return differing


def _rows_label(rows: list[CandidateRow]) -> str:
    return ", ".join(str(r.excel_row) for r in rows if r.excel_row is not None)


def _resolve_group(spec: EntitySpec, key: tuple, rows: list[CandidateRow],
                   log: IssueLog) -> CandidateRow | None:
    if spec.dup_policy is DupPolicy.ERROR:
        log.error(entity=spec.name, sheet=spec.sheet, row=rows[0].excel_row,
                  message=f"主键 {key} 重复出现 {len(rows)} 次（Excel 行 "
                          f"{_rows_label(rows)}），该实体不允许重复")
        return rows[0]

    if spec.dup_policy is DupPolicy.DEDUPE:
        differing = _non_pk_differences(spec, rows)
        if differing:
            log.error(entity=spec.name, sheet=spec.sheet, row=rows[0].excel_row,
                      message=f"主键 {key} 重复且字段 {differing} 取值不一致"
                              f"（Excel 行 {_rows_label(rows)}），无法裁决")
            return rows[0]
        log.warning(entity=spec.name, sheet=spec.sheet, row=rows[0].excel_row,
                    message=f"主键 {key} 完全重复 {len(rows)} 次"
                            f"（Excel 行 {_rows_label(rows)}），已去重保留首行")
        return rows[0]

    merged = CandidateRow(spec.name, rows[0].excel_row, dict(rows[0].values))
    summed: dict[str, float] = {}
    for field_name in spec.sum_fields:
        total = 0
        for row in rows:
            value = row.values.get(field_name)
            total += 0 if value is None else value
        summed[field_name] = total
        merged.values[field_name] = total
    log.warning(entity=spec.name, sheet=spec.sheet, row=rows[0].excel_row,
                message=f"主键 {key} 重复 {len(rows)} 次（Excel 行 {_rows_label(rows)}），"
                        f"已按 {sorted(summed)} 求和: "
                        + ", ".join(f"{k}={v}" for k, v in sorted(summed.items())))
    return merged


def validate(tables: RawTables, structure_log: IssueLog) -> tuple[ParsedTables, ValidationReport]:
    log = IssueLog(structure_log.issues)
    parsed = gate_types(tables, log)
    gate_references(parsed, log)
    gate_business(parsed, log)
    report = ValidationReport(errors=log.errors, warnings=log.warnings, gaps=log.gaps)
    return parsed, report
```

实现要点：

- `gate_types` 对**整行**收集错误但**不因单个字段失败而丢弃整行**——只有 `row_failed` 为真时才不产出 `CandidateRow`。这样一条脏数据不会让整表消失，同时脏行也不会流入后续门。
- `resolve_duplicates` **就地修改** `parsed`（`clear()` + `update()`）并返回它。`gate_business` 在最后调用它，因此 `validate` 返回的 `parsed` 已经去重/求和完毕，可直接交给 Repository。
- `_sortable` 是 G20 确定性的落点：混合类型主键（`str` / `int` / `date` / `bool` / `tuple`）必须先转成可比较的字符串再排序，否则 Python 3 会抛 `TypeError`。
- 参照门用 `log.emit(severity, ...)` 动态选择级别（`fk_optional` 决定 ERROR 还是 WARNING），因此 `IssueLog` 必须暴露公开的 `emit`——Task 5 的实现里 `error` / `warning` / `gap` 都是它的薄封装。不要在参照门里复制两遍消息构造。
- `_RANGE_RULES` 用字符串 `kind` 而不是 lambda，因为它必须是可打印、可测试的数据（`test_*_domain` 系列直接打这些规则）。

- [ ] **Step 5: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_validate_gates -v`
Expected: PASS（全部）

- [ ] **Step 6: 更新 `aps/data/__init__.py`**

在现有 re-export 基础上追加：

```python
from aps.data.validate import (
    CandidateRow,
    ParsedTables,
    ValidationReport,
    gate_business,
    gate_references,
    gate_types,
    resolve_duplicates,
    validate,
)
```

并把 `"CandidateRow"`, `"ParsedTables"`, `"ValidationReport"`, `"gate_business"`, `"gate_references"`, `"gate_types"`, `"resolve_duplicates"`, `"validate"` 加进 `__all__`。

- [ ] **Step 7: 用真实 V2 工作簿核对四道门（手工，不写测试）**

Run:
```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "
from aps.data.excel import read_workbook
from aps.data.validate import validate
tables, log = read_workbook('APS_DataSource_Template - WL -V2.xlsx', 'WL')
parsed, report = validate(tables, log)
print(report.counts())
for name in sorted(parsed):
    print(name, len(parsed[name]))
print('--- errors ---')
for i in report.errors[:30]:
    print(i.entity, i.field, i.sheet, i.row, i.column, i.message, repr(i.value))
print('--- gaps ---')
for i in report.gaps:
    print(i.entity, i.field, i.message)
"
```
Expected：
- `report.counts()["error"]` = **0**（这是 L1 门的核心断言，Task 10 会把它固化成测试）
- `parsed` 的行数：`initial_stock` 应从 4357 降到 **4270**（87 个重复 PN 求和收敛），`operation_leadtime` 从 944 降到 **907**（`(WCA1927, 10)` 的 38 行去重成 1 行，其余重复同理），`mold` = **9**
- `gaps` 至少包含：`routing.per_shift_cap_declared`、`routing.headcount`（若整列为空）、`part_param_fg.co_production`、`part_param_sf.max_stock`、`part_param_sf.special_shift`、`part_param_sf.co_production`、`material_arrival.eta_time`
- `warnings` 中应能看到 87 条 `initial_stock` 求和提示与 `operation_leadtime` 去重提示，以及 `bom.child_pn` 的 `NCICLABEL01` 孤儿 warning

若 `error` 不为 0，**先判断是数据问题还是门写错了**。判断标准：门只对登记表声明过的约束报错；若报错的约束不在本任务的「取值域清单」或四道门表格里，那是实现越权，删掉该检查。

- [ ] **Step 8: 提交**

```bash
git add aps/data/validate.py aps/data/__init__.py aps_tests/test_validate_gates.py \
        Specification/aps_greenfield_rewrite.md
git commit -m "feat(data): 实现类型/参照/业务三道校验门与重复主键收敛策略"
```

---

## Task 7: 日历几何与四档工作制

**Files:**
- Create: `aps/calendar/pattern.py`, `aps/calendar/geometry.py`
- Modify: `aps/calendar/__init__.py`
- Modify: `Specification/aps_greenfield_rewrite.md`（记录 A-8，见 Step 1）
- Test: `aps_tests/test_calendar.py`

**对 spec 的一处修订**：

- **A-8 休息日锚定规则**：spec §5.2 的 policy 有 `calendar_mode` 与 `calendar_auto_thresholds`，但**没有规定休息日落在星期几**。不写死就会有两个实现各自猜。裁决：**按 ISO 周前缀工作制** —— 一周从周一（`weekday() == 0`）开始，工作前 N 天、休息其余：
  | 模式 | 工作日 | 休息日 |
  |---|---|---|
  | `CONTINUOUS` | 周一~周日 | 无 |
  | `6ON1OFF` | 周一~周六 | 周日 |
  | `5ON2OFF` | 周一~周五 | 周六、周日 |
  | `4ON3OFF` | 周一~周四 | 周五、周六、周日 |

  这条规则同时满足现有 WL 语义（做五休二 = 周末双休）且完全确定、无需锚点日期参数。

**班次窗口公式（写死，双工厂通用）**：设某产线 `shift_hours = H`、`shifts_per_day = N`、当日起始钟点 `DAY_START_HOUR = 7`，则班次 `i`（0-based）的时间窗为

```
start = datetime.combine(work_date, 00:00) + timedelta(hours=DAY_START_HOUR + i * H)
end   = start + timedelta(hours=H)
```

WL（H=8, N=3）→ 07:00–15:00 / 15:00–23:00 / 23:00–次日 07:00。SY38（H=12, N=2）→ 07:00–19:00 / 19:00–次日 07:00。**不需要两套代码。**

**Interfaces:**
- Consumes: `aps.data.issues.IssueLog`（G28 冲突 warn）
- Produces:
  ```python
  # aps/calendar/pattern.py
  class CalendarMode(str, Enum):
      CONTINUOUS = "CONTINUOUS"
      SIX_ON_ONE_OFF = "6ON1OFF"
      FIVE_ON_TWO_OFF = "5ON2OFF"
      FOUR_ON_THREE_OFF = "4ON3OFF"

  WORK_DAYS: dict[CalendarMode, int]        # 7 / 6 / 5 / 4
  REST_WEEKDAYS: dict[CalendarMode, frozenset[int]]   # {} / {6} / {5,6} / {4,5,6}
  DEFAULT_AUTO_THRESHOLDS: dict[str, float] = {
      "4ON3OFF": 0.5714, "5ON2OFF": 0.7143, "6ON1OFF": 0.8571,
  }   # 4/7, 5/7, 6/7（spec §5.2）

  def recommend_mode(load_ratio: float,
                     thresholds: Mapping[str, float] = DEFAULT_AUTO_THRESHOLDS) -> CalendarMode
  def is_working_day(mode: CalendarMode, day: dt.date) -> bool
  def working_days(mode: CalendarMode, start: dt.date, horizon_days: int) -> tuple[dt.date, ...]

  # aps/calendar/geometry.py
  DAY_START_HOUR = 7
  MS_PER_HOUR = 3_600_000

  @dataclass(frozen=True)
  class SlotKey:
      line_code: str
      work_date: dt.date
      shift_index: int

  @dataclass(frozen=True)
  class Slot:
      key: SlotKey
      index: int                 # 该产线内的绝对槽位号 = day_ordinal * shifts_per_day + shift_index
      start: dt.datetime
      end: dt.datetime
      capacity_ms: int
      is_open: bool

  @dataclass(frozen=True)
  class LineGeometry:
      line_code: str
      shift_hours: float
      shifts_per_day: int
      trp_efficiency: float

  def build_slots(lines: Sequence[LineGeometry], start: dt.date, horizon_days: int,
                  mode: CalendarMode,
                  overrides: Mapping[SlotKey, bool] | None = None,
                  log: IssueLog | None = None) -> tuple[Slot, ...]
      # 返回按 (line_code, index) 排序的全量槽位；休息日的槽位也生成，但 is_open=False。
      # overrides 来自 V3「工作日历」sheet；与 mode 冲突时 overrides 胜 + warn（G28）。
  ```

- [ ] **Step 1: 把 A-8 写回 spec**

在 `Specification/aps_greenfield_rewrite.md` §5.2 的 policy JSON 代码块之后插入：

```markdown
**Stage A 实施期修订（2026-11-05）**：

- **A-8 休息日锚定**：policy 只声明 `calendar_mode`，未规定休息日落在星期几。裁决为 **ISO 周前缀工作制**——一周从周一起，工作前 N 天、休息其余：`CONTINUOUS` 无休；`6ON1OFF` 休周日；`5ON2OFF` 休周六周日；`4ON3OFF` 休周五周六周日。该规则与现行 WL「做五休二 = 周末双休」语义一致，且完全确定、无需额外锚点参数。
- 班次窗口公式双工厂通用：`start = combine(work_date, 00:00) + timedelta(hours=DAY_START_HOUR + i × shift_hours)`，`DAY_START_HOUR = 7`。WL（8h×3）得 07/15/23 三班，SY38（12h×2）得 07/19 两班，同一份代码。
```

- [ ] **Step 2: 写失败测试**

创建 `aps_tests/test_calendar.py`：

```python
from __future__ import annotations

import datetime as dt
import unittest

from aps.calendar.geometry import (
    DAY_START_HOUR,
    MS_PER_HOUR,
    LineGeometry,
    Slot,
    SlotKey,
    build_slots,
)
from aps.calendar.pattern import (
    DEFAULT_AUTO_THRESHOLDS,
    REST_WEEKDAYS,
    WORK_DAYS,
    CalendarMode,
    is_working_day,
    recommend_mode,
    working_days,
)
from aps.data.issues import IssueLog

FAST = True

MONDAY = dt.date(2026, 11, 2)      # 周一
SATURDAY = dt.date(2026, 11, 7)
SUNDAY = dt.date(2026, 11, 8)
FRIDAY = dt.date(2026, 11, 6)
THURSDAY = dt.date(2026, 11, 5)

WL_LINE = LineGeometry("PGLINKAGE", 8.0, 3, 0.85)
SY_LINE = LineGeometry("1400T-6", 12.0, 2, 0.90)


class TestRecommendMode(unittest.TestCase):
    def test_thresholds_are_four_sevenths_family(self):
        self.assertEqual(0.5714, DEFAULT_AUTO_THRESHOLDS["4ON3OFF"])
        self.assertEqual(0.7143, DEFAULT_AUTO_THRESHOLDS["5ON2OFF"])
        self.assertEqual(0.8571, DEFAULT_AUTO_THRESHOLDS["6ON1OFF"])

    def test_low_ratio_picks_four_on_three_off(self):
        self.assertEqual(CalendarMode.FOUR_ON_THREE_OFF, recommend_mode(0.1))
        self.assertEqual(CalendarMode.FOUR_ON_THREE_OFF, recommend_mode(0.5714))

    def test_medium_ratio_picks_five_on_two_off(self):
        self.assertEqual(CalendarMode.FIVE_ON_TWO_OFF, recommend_mode(0.58))
        self.assertEqual(CalendarMode.FIVE_ON_TWO_OFF, recommend_mode(0.7143))

    def test_high_ratio_picks_six_on_one_off(self):
        self.assertEqual(CalendarMode.SIX_ON_ONE_OFF, recommend_mode(0.72))
        self.assertEqual(CalendarMode.SIX_ON_ONE_OFF, recommend_mode(0.8571))

    def test_overload_picks_continuous(self):
        self.assertEqual(CalendarMode.CONTINUOUS, recommend_mode(0.86))
        self.assertEqual(CalendarMode.CONTINUOUS, recommend_mode(3.0))

    def test_custom_thresholds_override_defaults(self):
        custom = {"4ON3OFF": 0.1, "5ON2OFF": 0.2, "6ON1OFF": 0.3}
        self.assertEqual(CalendarMode.CONTINUOUS, recommend_mode(0.5, custom))
        self.assertEqual(CalendarMode.FOUR_ON_THREE_OFF, recommend_mode(0.05, custom))

    def test_zero_ratio(self):
        self.assertEqual(CalendarMode.FOUR_ON_THREE_OFF, recommend_mode(0.0))


class TestWorkingDays(unittest.TestCase):
    def test_work_days_table(self):
        self.assertEqual(7, WORK_DAYS[CalendarMode.CONTINUOUS])
        self.assertEqual(6, WORK_DAYS[CalendarMode.SIX_ON_ONE_OFF])
        self.assertEqual(5, WORK_DAYS[CalendarMode.FIVE_ON_TWO_OFF])
        self.assertEqual(4, WORK_DAYS[CalendarMode.FOUR_ON_THREE_OFF])

    def test_rest_weekdays_are_iso_prefix_complement(self):
        self.assertEqual(frozenset(), REST_WEEKDAYS[CalendarMode.CONTINUOUS])
        self.assertEqual(frozenset({6}), REST_WEEKDAYS[CalendarMode.SIX_ON_ONE_OFF])
        self.assertEqual(frozenset({5, 6}), REST_WEEKDAYS[CalendarMode.FIVE_ON_TWO_OFF])
        self.assertEqual(frozenset({4, 5, 6}), REST_WEEKDAYS[CalendarMode.FOUR_ON_THREE_OFF])

    def test_continuous_covers_every_day(self):
        days = working_days(CalendarMode.CONTINUOUS, MONDAY, 10)
        self.assertEqual(10, len(days))
        self.assertEqual(MONDAY, days[0])
        self.assertEqual(dt.date(2026, 11, 11), days[-1])

    def test_five_on_two_off_skips_weekend(self):
        days = working_days(CalendarMode.FIVE_ON_TWO_OFF, MONDAY, 7)
        self.assertNotIn(SATURDAY, days)
        self.assertNotIn(SUNDAY, days)
        self.assertEqual(5, len(days))

    def test_four_on_three_off_skips_fri_sat_sun(self):
        days = working_days(CalendarMode.FOUR_ON_THREE_OFF, MONDAY, 7)
        self.assertEqual((MONDAY, dt.date(2026, 11, 3), dt.date(2026, 11, 4), THURSDAY), days)

    def test_six_on_one_off_skips_sunday_only(self):
        days = working_days(CalendarMode.SIX_ON_ONE_OFF, MONDAY, 7)
        self.assertIn(SATURDAY, days)
        self.assertNotIn(SUNDAY, days)
        self.assertEqual(6, len(days))

    def test_horizon_counts_calendar_days_not_working_days(self):
        # 窗口 = 10 个自然日（11-02 ~ 11-11），工作日数由模式决定
        self.assertEqual(10, len(working_days(CalendarMode.CONTINUOUS, MONDAY, 10)))
        self.assertEqual(8, len(working_days(CalendarMode.FIVE_ON_TWO_OFF, MONDAY, 10)))
        self.assertEqual(9, len(working_days(CalendarMode.SIX_ON_ONE_OFF, MONDAY, 10)))
        self.assertEqual(7, len(working_days(CalendarMode.FOUR_ON_THREE_OFF, MONDAY, 10)))

    def test_is_working_day(self):
        self.assertTrue(is_working_day(CalendarMode.FIVE_ON_TWO_OFF, MONDAY))
        self.assertFalse(is_working_day(CalendarMode.FIVE_ON_TWO_OFF, SUNDAY))
        self.assertTrue(is_working_day(CalendarMode.CONTINUOUS, SUNDAY))

    def test_zero_horizon(self):
        self.assertEqual((), working_days(CalendarMode.CONTINUOUS, MONDAY, 0))


class TestSlotGeometry(unittest.TestCase):
    def test_day_start_hour_is_seven(self):
        self.assertEqual(7, DAY_START_HOUR)
        self.assertEqual(3_600_000, MS_PER_HOUR)

    def test_wl_three_shift_windows(self):
        slots = build_slots([WL_LINE], MONDAY, 1, CalendarMode.CONTINUOUS)
        self.assertEqual(3, len(slots))
        windows = [(s.start, s.end) for s in slots]
        self.assertEqual([
            (dt.datetime(2026, 11, 2, 7, 0), dt.datetime(2026, 11, 2, 15, 0)),
            (dt.datetime(2026, 11, 2, 15, 0), dt.datetime(2026, 11, 2, 23, 0)),
            (dt.datetime(2026, 11, 2, 23, 0), dt.datetime(2026, 11, 3, 7, 0)),
        ], windows)

    def test_sy38_two_shift_windows_same_formula(self):
        slots = build_slots([SY_LINE], MONDAY, 1, CalendarMode.CONTINUOUS)
        self.assertEqual(2, len(slots))
        self.assertEqual((dt.datetime(2026, 11, 2, 7, 0), dt.datetime(2026, 11, 2, 19, 0)),
                         (slots[0].start, slots[0].end))
        self.assertEqual((dt.datetime(2026, 11, 2, 19, 0), dt.datetime(2026, 11, 3, 7, 0)),
                         (slots[1].start, slots[1].end))

    def test_capacity_ms(self):
        wl = build_slots([WL_LINE], MONDAY, 1, CalendarMode.CONTINUOUS)
        self.assertTrue(all(s.capacity_ms == 8 * MS_PER_HOUR for s in wl))
        sy = build_slots([SY_LINE], MONDAY, 1, CalendarMode.CONTINUOUS)
        self.assertTrue(all(s.capacity_ms == 12 * MS_PER_HOUR for s in sy))

    def test_absolute_index_is_per_line(self):
        slots = build_slots([WL_LINE], MONDAY, 2, CalendarMode.CONTINUOUS)
        self.assertEqual([0, 1, 2, 3, 4, 5], [s.index for s in slots])
        self.assertEqual(0, slots[3].key.shift_index)
        self.assertEqual(dt.date(2026, 11, 3), slots[3].key.work_date)

    def test_rest_day_slots_exist_but_closed(self):
        slots = build_slots([WL_LINE], MONDAY, 7, CalendarMode.FIVE_ON_TWO_OFF)
        self.assertEqual(21, len(slots))
        saturday = [s for s in slots if s.key.work_date == SATURDAY]
        self.assertEqual(3, len(saturday))
        self.assertTrue(all(not s.is_open for s in saturday))
        monday = [s for s in slots if s.key.work_date == MONDAY]
        self.assertTrue(all(s.is_open for s in monday))

    def test_multiple_lines_sorted_by_line_then_index(self):
        slots = build_slots([WL_LINE, SY_LINE], MONDAY, 1, CalendarMode.CONTINUOUS)
        self.assertEqual(["1400T-6", "1400T-6", "PGLINKAGE", "PGLINKAGE", "PGLINKAGE"],
                         [s.key.line_code for s in slots])

    def test_slot_key_is_hashable_and_frozen(self):
        key = SlotKey("L1", MONDAY, 0)
        self.assertEqual({key: True}[SlotKey("L1", MONDAY, 0)], True)
        with self.assertRaises(Exception):
            key.shift_index = 1

    def test_override_closes_an_otherwise_open_slot(self):
        log = IssueLog()
        overrides = {SlotKey("PGLINKAGE", MONDAY, 1): False}
        slots = build_slots([WL_LINE], MONDAY, 1, CalendarMode.CONTINUOUS, overrides, log)
        target = [s for s in slots if s.key.shift_index == 1][0]
        self.assertFalse(target.is_open)
        self.assertTrue(all(s.is_open for s in slots if s.key.shift_index != 1))

    def test_override_opens_a_rest_day_slot_and_warns(self):
        log = IssueLog()
        overrides = {SlotKey("PGLINKAGE", SUNDAY, 0): True}
        slots = build_slots([WL_LINE], SATURDAY, 2, CalendarMode.FIVE_ON_TWO_OFF,
                            overrides, log)
        target = [s for s in slots if s.key.work_date == SUNDAY and s.key.shift_index == 0][0]
        self.assertTrue(target.is_open)
        self.assertTrue(any("工作日历" in i.message for i in log.warnings))

    def test_agreeing_override_does_not_warn(self):
        log = IssueLog()
        overrides = {SlotKey("PGLINKAGE", MONDAY, 0): True}
        build_slots([WL_LINE], MONDAY, 1, CalendarMode.CONTINUOUS, overrides, log)
        self.assertEqual((), log.warnings)

    def test_override_for_unknown_line_is_ignored(self):
        log = IssueLog()
        overrides = {SlotKey("GHOST", MONDAY, 0): False}
        slots = build_slots([WL_LINE], MONDAY, 1, CalendarMode.CONTINUOUS, overrides, log)
        self.assertEqual(3, len(slots))
        self.assertTrue(all(s.is_open for s in slots))

    def test_fractional_shift_hours_round_to_integer_ms(self):
        line = LineGeometry("L", 7.5, 2, 0.9)
        slots = build_slots([line], MONDAY, 1, CalendarMode.CONTINUOUS)
        self.assertEqual(27_000_000, slots[0].capacity_ms)
        self.assertIsInstance(slots[0].capacity_ms, int)

    def test_slots_are_deterministic(self):
        a = build_slots([WL_LINE, SY_LINE], MONDAY, 10, CalendarMode.FIVE_ON_TWO_OFF)
        b = build_slots([SY_LINE, WL_LINE], MONDAY, 10, CalendarMode.FIVE_ON_TWO_OFF)
        self.assertEqual(a, b)

    def test_returns_slot_instances(self):
        slots = build_slots([WL_LINE], MONDAY, 1, CalendarMode.CONTINUOUS)
        self.assertIsInstance(slots[0], Slot)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_calendar -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'aps.calendar.pattern'`

- [ ] **Step 4: 实现 `aps/calendar/pattern.py`**

```python
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from enum import Enum

__all__ = [
    "DEFAULT_AUTO_THRESHOLDS",
    "REST_WEEKDAYS",
    "WORK_DAYS",
    "CalendarMode",
    "is_working_day",
    "recommend_mode",
    "working_days",
]


class CalendarMode(str, Enum):
    CONTINUOUS = "CONTINUOUS"
    SIX_ON_ONE_OFF = "6ON1OFF"
    FIVE_ON_TWO_OFF = "5ON2OFF"
    FOUR_ON_THREE_OFF = "4ON3OFF"


WORK_DAYS: dict[CalendarMode, int] = {
    CalendarMode.CONTINUOUS: 7,
    CalendarMode.SIX_ON_ONE_OFF: 6,
    CalendarMode.FIVE_ON_TWO_OFF: 5,
    CalendarMode.FOUR_ON_THREE_OFF: 4,
}

REST_WEEKDAYS: dict[CalendarMode, frozenset[int]] = {
    CalendarMode.CONTINUOUS: frozenset(),
    CalendarMode.SIX_ON_ONE_OFF: frozenset({6}),
    CalendarMode.FIVE_ON_TWO_OFF: frozenset({5, 6}),
    CalendarMode.FOUR_ON_THREE_OFF: frozenset({4, 5, 6}),
}

DEFAULT_AUTO_THRESHOLDS: dict[str, float] = {
    "4ON3OFF": 0.5714,
    "5ON2OFF": 0.7143,
    "6ON1OFF": 0.8571,
}

_ORDER = (
    ("4ON3OFF", CalendarMode.FOUR_ON_THREE_OFF),
    ("5ON2OFF", CalendarMode.FIVE_ON_TWO_OFF),
    ("6ON1OFF", CalendarMode.SIX_ON_ONE_OFF),
)


def recommend_mode(load_ratio: float,
                   thresholds: Mapping[str, float] = DEFAULT_AUTO_THRESHOLDS) -> CalendarMode:
    for key, mode in _ORDER:
        if load_ratio <= float(thresholds[key]):
            return mode
    return CalendarMode.CONTINUOUS


def is_working_day(mode: CalendarMode, day: dt.date) -> bool:
    return day.weekday() not in REST_WEEKDAYS[mode]


def working_days(mode: CalendarMode, start: dt.date,
                 horizon_days: int) -> tuple[dt.date, ...]:
    return tuple(
        start + dt.timedelta(days=offset)
        for offset in range(max(0, horizon_days))
        if is_working_day(mode, start + dt.timedelta(days=offset))
    )
```

- [ ] **Step 5: 实现 `aps/calendar/geometry.py`**

```python
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from aps.calendar.pattern import CalendarMode, is_working_day
from aps.data.issues import IssueLog

__all__ = [
    "DAY_START_HOUR",
    "MS_PER_HOUR",
    "LineGeometry",
    "Slot",
    "SlotKey",
    "build_slots",
]

DAY_START_HOUR = 7
MS_PER_HOUR = 3_600_000


@dataclass(frozen=True)
class SlotKey:
    line_code: str
    work_date: dt.date
    shift_index: int


@dataclass(frozen=True)
class LineGeometry:
    line_code: str
    shift_hours: float
    shifts_per_day: int
    trp_efficiency: float


@dataclass(frozen=True)
class Slot:
    key: SlotKey
    index: int
    start: dt.datetime
    end: dt.datetime
    capacity_ms: int
    is_open: bool


def _window(work_date: dt.date, shift_index: int, shift_hours: float) -> tuple[dt.datetime,
                                                                              dt.datetime]:
    midnight = dt.datetime.combine(work_date, dt.time(0, 0))
    start = midnight + dt.timedelta(hours=DAY_START_HOUR + shift_index * shift_hours)
    return start, start + dt.timedelta(hours=shift_hours)


def build_slots(lines: Sequence[LineGeometry], start: dt.date, horizon_days: int,
                mode: CalendarMode,
                overrides: Mapping[SlotKey, bool] | None = None,
                log: IssueLog | None = None) -> tuple[Slot, ...]:
    overrides = overrides or {}
    slots: list[Slot] = []
    for line in sorted(lines, key=lambda l: l.line_code):
        capacity_ms = int(round(line.shift_hours * MS_PER_HOUR))
        for day_offset in range(max(0, horizon_days)):
            work_date = start + dt.timedelta(days=day_offset)
            pattern_open = is_working_day(mode, work_date)
            for shift_index in range(line.shifts_per_day):
                key = SlotKey(line.line_code, work_date, shift_index)
                start_at, end_at = _window(work_date, shift_index, line.shift_hours)
                is_open = pattern_open
                if key in overrides:
                    declared = bool(overrides[key])
                    if declared != pattern_open and log is not None:
                        log.warning(
                            entity="calendar_day", field="is_open", sheet="工作日历",
                            row=None, column="是否开班", value=declared,
                            message=f"工作日历对 {line.line_code} {work_date} "
                                    f"班次 {shift_index} 的开班声明 {declared} 与 "
                                    f"{mode.value} 推导结果 {pattern_open} 冲突，"
                                    f"以工作日历为准",
                        )
                    is_open = declared
                slots.append(Slot(
                    key=key,
                    index=day_offset * line.shifts_per_day + shift_index,
                    start=start_at, end=end_at,
                    capacity_ms=capacity_ms, is_open=is_open,
                ))
    return tuple(slots)
```

注意：`index` 用 **day_offset**（自然日序）而非工作日序，这样休息日的槽位也占号，槽位号与日期一一对应，求解器可以直接用 `index` 做时序比较（跨休息日连续）。这是 spec §6.2「绝对槽位号」的落地形式。

- [ ] **Step 6: 更新 `aps/calendar/__init__.py`**

```python
from __future__ import annotations

from aps.calendar.geometry import (
    DAY_START_HOUR,
    MS_PER_HOUR,
    LineGeometry,
    Slot,
    SlotKey,
    build_slots,
)
from aps.calendar.pattern import (
    DEFAULT_AUTO_THRESHOLDS,
    REST_WEEKDAYS,
    WORK_DAYS,
    CalendarMode,
    is_working_day,
    recommend_mode,
    working_days,
)

__all__ = [
    "DAY_START_HOUR", "MS_PER_HOUR", "LineGeometry", "Slot", "SlotKey", "build_slots",
    "DEFAULT_AUTO_THRESHOLDS", "REST_WEEKDAYS", "WORK_DAYS", "CalendarMode",
    "is_working_day", "recommend_mode", "working_days",
]
```

- [ ] **Step 7: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_calendar -v`
Expected: PASS（全部）

- [ ] **Step 8: 提交**

```bash
git add aps/calendar/pattern.py aps/calendar/geometry.py aps/calendar/__init__.py \
        aps_tests/test_calendar.py Specification/aps_greenfield_rewrite.md
git commit -m "feat(calendar): 四档工作制与毫秒标度班次槽位几何"
```

---

## Task 8: `DataSet` 聚合根与派生量冻结

**Files:**
- Create: `aps/dataset.py`
- Modify: `Specification/aps_greenfield_rewrite.md`（记录 A-9 / A-10，见 Step 1）
- Test: `aps_tests/test_dataset.py`

**对 spec 的两处修订**：

- **A-9 净需求的 FIFO 落地**：spec §4.4 给出公式 `净需求 = 客户需求 − 期初可用库存 + 安全库存` 与「库存已覆盖的需求行不生成任务」，但没说是**按 PN 聚合**还是**按 (PN, 日期) 逐行**。裁决：**按日期升序 FIFO 逐行核销**。
  ```
  carry = stock(pn, stage)                      # 期初可用库存
  for demand_date ascending within horizon:
      covered = min(carry, quantity)
      carry  -= covered
      remaining = quantity - covered
      if remaining > 0:
          生成 Task(pn, net_requirement=remaining, due_date=demand_date)
  if safety_stock(pn) > 0:
      生成 Task(pn, net_requirement=safety_stock(pn), due_date=horizon.end_date)
  ```
  聚合口径会让「库存覆盖了 11-03 但 11-05 断料」这种情况消失，交期指标会虚高，因此必须逐行。安全库存补产任务的交期定在窗口末日（它是囤库存，不是应交订单）。
- **A-10 库存按阶段匹配**：`initial_stock` 的主键含 `material_type`（修订 A-1），同一 PN 可能同时有 `COMP` 与 `PACK` 行。核销时**只取 `material_type == part.stage` 的行**求和。若无匹配行，取 0（不跨类型借用），并在 `log` 里记一条 `gap`。跨类型借用会把包装库存当成成品库存，是旧系统净需求算错的来源之一。

**Interfaces:**
- Consumes: `aps.data.repository.Repository`；`aps.calendar.geometry.build_slots` / `Slot` / `SlotKey` / `LineGeometry`；`aps.calendar.pattern.CalendarMode` / `recommend_mode` / `DEFAULT_AUTO_THRESHOLDS`；`aps.data.issues.IssueLog`
- Produces:
  ```python
  MS_PER_SECOND = 1000
  SECONDS_PER_HOUR = 3600

  @dataclass(frozen=True)
  class Horizon:
      site: str
      start_date: dt.date
      days: int
      @property
      def end_date(self) -> dt.date          # start_date + (days - 1)，含端点
      def contains(self, day: dt.date) -> bool
      def day_offset(self, day: dt.date) -> int      # 越界返回 -1 或 >= days

  @dataclass(frozen=True)
  class Task:
      pn: str
      stage: str                             # FG | SF | COMP | PACK | RAW
      net_requirement: int
      due_date: dt.date
      mpq: int                               # FG=最小包装量；SF=最小起投批量；无参数行则 1
      moq: int | None
      mold_id: str | None
      family_code: str | None
      description: str | None
      is_safety_topup: bool                  # True = A-9 的安全库存补产任务
      parent_pn: str | None = None           # 半成品对应的母件料号（FG 任务为 None）

  @dataclass(frozen=True)
  class RouteOption:
      pn: str
      op_seq: int
      line_code: str
      certified: bool
      ct_sec: float
      unit_ms: int                           # round(ct_sec × 1000 / line.trp_efficiency)
      per_shift_cap: int                     # floor(shift_hours × 3600 × TRP / ct_sec)，derived
      headcount: int | None
      changeover_min: float | None           # §3.2 三级回退的最终结果
      changeover_source: str | None          # "setup_matrix" | "tooling_mold" | "routing" | None
      mold_ids: tuple[str, ...]

  @dataclass(frozen=True)
  class DataSet:
      horizon: Horizon
      mode: CalendarMode
      lines: tuple[LineGeometry, ...]
      slots: tuple[Slot, ...]
      tasks: tuple[Task, ...]
      routes: tuple[RouteOption, ...]
      bom: tuple["BomLink", ...]
      safety_stock: Mapping[str, int]
      mold_to_parts: Mapping[str, frozenset[str]]
      initial_stock: Mapping[str, int]
      calendar_overrides: Mapping[SlotKey, bool]
      degraded_fields: frozenset[str]        # "entity.field"，来自 ValidationReport.gaps

      @classmethod
      def load(cls, repo: Repository, site: str, start_date: dt.date, days: int,
               calendar_mode: str = "auto",
               thresholds: Mapping[str, float] = DEFAULT_AUTO_THRESHOLDS,
               overrides: Mapping[SlotKey, bool] | None = None,
               log: IssueLog | None = None) -> "DataSet"

      # 只读查询接口（求解器 Stage B 只用这些，不再自己算）
      def line(self, line_code: str) -> LineGeometry | None
      def open_slots(self, line_code: str) -> tuple[Slot, ...]
      def slot_count(self, line_code: str) -> int
      def certified_lines(self, pn: str) -> tuple[str, ...]
      def route(self, pn: str, line_code: str, op_seq: int = 10) -> RouteOption | None
      def tasks_for(self, pn: str) -> tuple[Task, ...]
      def children_of(self, parent_pn: str) -> tuple["BomLink", ...]
      def due_index(self, due_date: dt.date, line_code: str) -> int
      def changeover(self, line_code: str, from_mold: str | None,
                     to_mold: str | None, pn: str) -> tuple[float | None, str | None]

  @dataclass(frozen=True)
  class BomLink:
      parent_pn: str
      child_pn: str
      ratio: float
  ```

**派生量口径（逐条写死，对应 spec §4.4 表）**：

| 派生量 | 公式 | provenance |
|---|---|---|
| `unit_ms` | `round(ct_sec × 1000 / line.trp_efficiency)` | derived |
| `per_shift_cap` | `floor(line.shift_hours × 3600 × line.trp_efficiency / ct_sec)`；`ct_sec <= 0` 时取 0 | derived |
| 净需求 | A-9 FIFO | derived |
| 安全库存 | `inventory_policy` 中 `period_type == 'NORMAL'` 且 `start_day <= start_date.day <= end_day` 的行的 `safety_stock`；多行取 `start_day` 最大者；无行 → 0 + `gap` | derived |
| 槽位表 | `build_slots(lines, start_date, days, mode, overrides, log)` | derived |
| `mode` | `calendar_mode == "auto"` → `recommend_mode(load_ratio)`；否则按名字取 `CalendarMode` | derived |
| `load_ratio` | `Σ(demand.quantity within horizon) × unit_ms 最小候选) / Σ(所有 open 槽位 capacity_ms)`；无路由的 PN 跳过；分母为 0 → `load_ratio = 0.0` | derived |
| 换型时间 | ① `setup_matrix[(site,line,from_mold,to_mold)]` ② `mold.changeover_min[to_mold]` ③ `routing.changeover_min[(pn,line,op_seq)]` ④ 三者全缺 → `None` + `gap` | derived |
| 模具反查 | `mold_id → frozenset(pn)`，来源 = `tooling_mold.applicable_pns` ∪ `part_param_fg/sf.mold_id` | derived |
| 认证产线 | `routing.certified == True` 且（`valid_from` 为空或 `<= start_date`）且（`valid_to` 为空或 `> start_date`）的 `line_code`，去重后按 `priority` 升序、同优先级按 `line_code` 升序。多工序料号的 `priority` 取该产线上**最小 `op_seq`** 那一行的值 | derived |
| 交期槽位 | `due_index(d, line) = clamp(day_offset(d), 0, days-1) × line.shifts_per_day + line.shifts_per_day - 1`（该日**最后一个班次**） | derived |
| `mpq` | `part.stage == 'FG'` → `part_param_fg.mpq`；`'SF'` → `part_param_sf.mpq`；其余 → `1` | derived |
| `degraded_fields` | `ValidationReport.gaps` 的 `f"{entity}.{field}"` 集合，由 CLI 传入 | derived |

- [ ] **Step 1: 把 A-9 / A-10 写回 spec**

在 `Specification/aps_greenfield_rewrite.md` §4.4 的派生量表格之后、`DataSet 是**不可变的**（frozen dataclass）` 那一行之前，插入：

```markdown
**Stage A 实施期修订（2026-11-05）**：

- **A-9 净需求按 (PN, 日期) FIFO 逐行核销**，不按 PN 聚合。聚合口径会掩盖「库存覆盖了 11-03 但 11-05 断料」，使交期指标虚高。核销后余量 > 0 的需求行生成一个 `Task`；安全库存另生成一个 `due_date = 窗口末日`、`is_safety_topup = True` 的补产任务（囤库存，不是应交订单）。
- **A-10 期初库存按阶段匹配**：`initial_stock` 主键含 `material_type`（A-1），核销只取 `material_type == part.stage` 的行求和，无匹配行取 0 并记 `gap`。跨类型借用（把 PACK 库存当 FG 用）是旧系统净需求算错的来源之一。
- **交期槽位口径**：`due_index(d, line) = clamp(day_offset(d), 0, days−1) × shifts_per_day(line) + shifts_per_day(line) − 1`，即交期日的**最后一个班次**。因为 `shifts_per_day` 随产线而变，交期槽位是 `(task, line)` 的函数而非 `task` 的属性。
```

- [ ] **Step 2: 写失败测试**

创建 `aps_tests/test_dataset.py`。测试用一个**内存 Repository 假件**，不碰磁盘：

```python
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
        # 10 件 × 32 ms 远小于 30 个开班槽位 × 28.8M ms → 极低负荷 → 做四休三
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
        # 27 / 0.85 = 31.7647 秒 → 31764.7 ms → round = 31765
        self.assertEqual(31765, route.unit_ms)

    def test_per_shift_cap_is_derived_when_column_empty(self):
        ds = load(base_tables(), days=1)
        route = ds.route("P1", "PGLINKAGE")
        # floor(8 × 3600 × 0.85 / 27) = floor(906.67) = 906
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_dataset -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'aps.dataset'`

- [ ] **Step 4: 实现 `aps/dataset.py`**

```python
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
    "MS_PER_SECOND",
    "SECONDS_PER_HOUR",
    "BomLink",
    "DataSet",
    "Horizon",
    "RouteOption",
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
        return self.start_date + dt.timedelta(days=max(0, self.days) - 1)

    def contains(self, day: dt.date) -> bool:
        return 0 <= self.day_offset(day) < self.days

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
    certified_index: Mapping[str, tuple[str, ...]]
    bom: tuple[BomLink, ...]
    safety_stock: Mapping[str, int]
    mold_to_parts: Mapping[str, frozenset[str]]
    initial_stock: Mapping[str, int]
    calendar_overrides: Mapping[SlotKey, bool]
    degraded_fields: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "_lines_by_code",
                           {line.line_code: line for line in self.lines})
        object.__setattr__(self, "_slots_by_line", _group_slots(self.slots))
        object.__setattr__(self, "_routes",
                           {(r.pn, r.line_code, r.op_seq): r for r in self.routes})
        object.__setattr__(self, "_tasks_by_pn", _group_tasks(self.tasks))
        object.__setattr__(self, "_children", _group_bom(self.bom))

    def line(self, line_code: str) -> LineGeometry | None:
        return self._lines_by_code.get(line_code)

    def open_slots(self, line_code: str) -> tuple[Slot, ...]:
        return tuple(s for s in self._slots_by_line.get(line_code, ()) if s.is_open)

    def slot_count(self, line_code: str) -> int:
        return len(self._slots_by_line.get(line_code, ()))

    def certified_lines(self, pn: str) -> tuple[str, ...]:
        return self.certified_index.get(pn, ())

    def route(self, pn: str, line_code: str, op_seq: int = 10) -> RouteOption | None:
        return self._routes.get((pn, line_code, op_seq))

    def tasks_for(self, pn: str) -> tuple[Task, ...]:
        return self._tasks_by_pn.get(pn, ())

    def children_of(self, parent_pn: str) -> tuple[BomLink, ...]:
        return self._children.get(parent_pn, ())

    def due_index(self, due_date: dt.date, line_code: str) -> int:
        geometry = self._lines_by_code.get(line_code)
        if geometry is None:
            return 0
        offset = self.horizon.day_offset(due_date)
        offset = max(0, min(offset, max(0, self.horizon.days) - 1))
        return offset * geometry.shifts_per_day + geometry.shifts_per_day - 1

    def changeover(self, line_code: str, from_mold: str | None,
                   to_mold: str | None, pn: str) -> tuple[float | None, str | None]:
        return self._changeover_lookup.get((line_code, from_mold, to_mold, pn), (None, None))

    @classmethod
    def load(cls, repo: Repository, site: str, start_date: dt.date, days: int,
             calendar_mode: str = "auto",
             thresholds: Mapping[str, float] = DEFAULT_AUTO_THRESHOLDS,
             overrides: Mapping[SlotKey, bool] | None = None,
             log: IssueLog | None = None,
             degraded_fields: frozenset[str] = frozenset()) -> "DataSet":
        log = log if log is not None else IssueLog()
        horizon = Horizon(site, start_date, days)

        line_rows = repo.list_by("line", site_code=site)
        lines = tuple(
            LineGeometry(row["line_code"], float(row["shift_hours"]),
                         int(row["shifts_per_day"]), float(row["trp_efficiency"]))
            for row in sorted(line_rows, key=lambda r: r["line_code"])
        )
        lines_by_code = {line.line_code: line for line in lines}

        calendar_overrides = dict(overrides or {})
        for row in repo.list_by("calendar_day", site_code=site):
            if not horizon.contains(row["work_date"]):
                continue
            key = SlotKey(row["line_code"], row["work_date"], int(row["shift_index"]))
            calendar_overrides[key] = bool(row["is_open"])

        parts = {row["pn"]: row for row in repo.list_by("part", site_code=site)}
        fg_params = {row["pn"]: row for row in repo.list_by("part_param_fg", site_code=site)}
        sf_params = {row["pn"]: row for row in repo.list_by("part_param_sf", site_code=site)}

        routes, certified = _build_routes(repo, site, horizon, lines_by_code)

        initial_stock = _build_stock(repo, site, parts, log)
        safety = _build_safety_stock(repo, site, horizon, parts, log)

        mode = _resolve_mode(repo, site, calendar_mode, thresholds, horizon,
                             lines_by_code, routes)
        slots = build_slots(lines, start_date, days, mode, calendar_overrides, log)

        tasks = _build_tasks(repo, site, horizon, parts, fg_params, sf_params,
                             initial_stock, safety)
        bom = tuple(
            BomLink(row["parent_pn"], row["child_pn"], float(row["ratio"]))
            for row in sorted(repo.list_by("bom", site_code=site),
                              key=lambda r: (r["parent_pn"], r["child_pn"]))
        )
        mold_to_parts = _build_mold_lookup(repo, site, fg_params, sf_params)

        dataset = cls(
            horizon=horizon, mode=mode, lines=lines, slots=slots, tasks=tasks,
            routes=tuple(routes), certified_index=certified, bom=bom,
            safety_stock=safety, mold_to_parts=mold_to_parts,
            initial_stock=initial_stock,
            calendar_overrides=calendar_overrides, degraded_fields=degraded_fields,
        )
        object.__setattr__(dataset, "_changeover_lookup",
                           _build_changeover_lookup(repo, site, dataset, log))
        return dataset


def _group_slots(slots: Sequence[Slot]) -> dict[str, tuple[Slot, ...]]:
    out: dict[str, list[Slot]] = defaultdict(list)
    for slot in slots:
        out[slot.key.line_code].append(slot)
    return {key: tuple(value) for key, value in out.items()}


def _group_tasks(tasks: Sequence[Task]) -> dict[str, tuple[Task, ...]]:
    out: dict[str, list[Task]] = defaultdict(list)
    for task in tasks:
        out[task.pn].append(task)
    return {key: tuple(value) for key, value in out.items()}


def _group_bom(bom: Sequence[BomLink]) -> dict[str, tuple[BomLink, ...]]:
    out: dict[str, list[BomLink]] = defaultdict(list)
    for link in bom:
        out[link.parent_pn].append(link)
    return {key: tuple(value) for key, value in out.items()}
```

以上是 `DataSet` 的骨架。**下面五个 `_build_*` 辅助函数是派生量的实体，必须一并实现**：

```python
def _resolve_valid(route_row: Mapping[str, Any], start_date: dt.date) -> bool:
    valid_from = route_row.get("valid_from")
    valid_to = route_row.get("valid_to")
    if valid_from is not None and valid_from > start_date:
        return False
    if valid_to is not None and valid_to <= start_date:
        return False
    return True


def _build_routes(repo: Repository, site: str, horizon: Horizon,
                  lines_by_code: Mapping[str, LineGeometry],
                  ) -> tuple[list[RouteOption], dict[str, tuple[str, ...]]]:
    rows = repo.list_by("routing", site_code=site)
    priority_of: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row["pn"], row["line_code"])
        current = priority_of.get(key)
        candidate = int(row.get("priority") or 0)
        if current is None or candidate < current:
            priority_of[key] = candidate
    routes: list[RouteOption] = []
    for row in sorted(rows, key=lambda r: (r["pn"], r["line_code"], r["op_seq"])):
        line = lines_by_code.get(row["line_code"])
        if line is None:
            continue
        if not _resolve_valid(row, horizon.start_date):
            continue
        ct_sec = float(row["ct_sec"])
        unit_ms = int(round(ct_sec * MS_PER_SECOND / line.trp_efficiency))
        if ct_sec > 0:
            per_shift_cap = int(math.floor(
                line.shift_hours * SECONDS_PER_HOUR * line.trp_efficiency / ct_sec
            ))
        else:
            per_shift_cap = 0
        routes.append(RouteOption(
            pn=row["pn"], op_seq=int(row["op_seq"]), line_code=row["line_code"],
            certified=bool(row["certified"]), ct_sec=ct_sec, unit_ms=unit_ms,
            per_shift_cap=per_shift_cap, headcount=row.get("headcount"),
            changeover_min=row.get("changeover_min"),
            changeover_source="routing" if row.get("changeover_min") is not None else None,
            mold_ids=tuple(row.get("mold_ids") or ()),
        ))
    certified: dict[str, list[str]] = defaultdict(list)
    for route in routes:
        if route.certified:
            certified[route.pn].append(route.line_code)
    ordered = {
        pn: tuple(sorted(set(codes), key=lambda c: (priority_of.get((pn, c), 0), c)))
        for pn, codes in certified.items()
    }
    return routes, ordered


def _build_stock(repo: Repository, site: str, parts: Mapping[str, Mapping[str, Any]],
                 log: IssueLog) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    seen_types: dict[str, set[str]] = defaultdict(set)
    for row in repo.list_by("initial_stock", site_code=site):
        stage = parts.get(row["pn"], {}).get("stage")
        material_type = row.get("material_type")
        seen_types[row["pn"]].add(material_type)
        if stage is None or material_type != stage:
            continue
        totals[row["pn"]] += int(row.get("quantity") or 0)
    for pn in sorted(seen_types):
        stage = parts.get(pn, {}).get("stage")
        if stage is not None and stage not in seen_types[pn]:
            log.gap(entity="initial_stock", field="material_type",
                    sheet="期初可用库存 Initial Stock", column="物料类型",
                    message=f"料号 {pn} 的阶段为 {stage}，但期初库存只有 "
                            f"{sorted(seen_types[pn])} 类型，按 0 核销（A-10）")
    return dict(totals)


def _build_safety_stock(repo: Repository, site: str, horizon: Horizon,
                        parts: Mapping[str, Mapping[str, Any]],
                        log: IssueLog) -> dict[str, int]:
    best: dict[str, tuple[int, int]] = {}
    day_of_month = horizon.start_date.day
    for row in repo.list_by("inventory_policy", site_code=site):
        if row.get("period_type") != "NORMAL":
            continue
        start_day = int(row.get("start_day") or 0)
        end_day = int(row.get("end_day") or 0)
        if not start_day <= day_of_month <= end_day:
            continue
        value = row.get("safety_stock")
        if value is None:
            continue
        current = best.get(row["pn"])
        if current is None or start_day > current[0]:
            best[row["pn"]] = (start_day, int(value))
    out = {pn: value for pn, (_, value) in sorted(best.items())}
    for pn in sorted(parts):
        if parts[pn].get("stage") in ("FG", "SF") and pn not in out:
            log.gap(entity="inventory_policy", field="safety_stock",
                    sheet="库存策略 Inventory-Policy", column="安全库存 (件)",
                    message=f"料号 {pn} 在起始日 {day_of_month} 无 NORMAL 安全库存策略，"
                            f"按 0 处理")
    return out


def _resolve_mode(repo: Repository, site: str, calendar_mode: str,
                  thresholds: Mapping[str, float], horizon: Horizon,
                  lines_by_code: Mapping[str, LineGeometry],
                  routes: Sequence[RouteOption]) -> CalendarMode:
    if calendar_mode != "auto":
        return CalendarMode(calendar_mode)

    declared = repo.get_by_pk("site", (site,)) or {}
    if declared.get("default_calendar_mode"):
        return CalendarMode(declared["default_calendar_mode"])

    best_ms: dict[str, int] = {}
    for route in routes:
        current = best_ms.get(route.pn)
        if current is None or route.unit_ms < current:
            best_ms[route.pn] = route.unit_ms

    demand_ms = 0
    for row in repo.list_by("demand", site_code=site):
        if not horizon.contains(row["demand_date"]):
            continue
        unit = best_ms.get(row["pn"])
        if unit is None:
            continue
        demand_ms += int(row["quantity"]) * unit

    capacity_ms = 0
    for line in lines_by_code.values():
        # 分母取极限产能：CONTINUOUS 无休息日，所以工作日数就是 horizon.days
        capacity_ms += (horizon.days * line.shifts_per_day
                        * int(round(line.shift_hours * MS_PER_HOUR)))

    ratio = 0.0 if capacity_ms == 0 else demand_ms / capacity_ms
    return recommend_mode(ratio, thresholds)


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

    tasks: list[Task] = []
    # 1. 成品（FG）净需求与任务生成
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
            t = _make_task(pn, parts, fg_params, sf_params, remaining,
                           row["demand_date"], False, parent_pn=None)
            tasks.append(t)
            fg_tasks_by_pn[pn].append(t)

    # 2. 半成品（SF）BOM 展开与净需求生成
    bom_by_parent: dict[str, list[BomLink]] = defaultdict(list)
    for link in (bom_links or ()):
        bom_by_parent[link.parent_pn].append(link)

    sf_gross_demand: dict[str, list[tuple[dt.date, int, str]]] = defaultdict(list)
    for parent_pn, parent_tasks in fg_tasks_by_pn.items():
        for link in bom_by_parent.get(parent_pn, []):
            child_pn = link.child_pn
            child_part = parts.get(child_pn, {})
            # 过滤只排半成品（SF），外购辅料（如标签 NCICLABEL01 / PACK / RAW）不排产
            if child_part.get("stage") != "SF":
                continue
            for pt in parent_tasks:
                gross_qty = int(math.ceil(pt.net_requirement * link.ratio))
                sf_gross_demand[child_pn].append((pt.due_date, gross_qty, parent_pn))

    for child_pn in sorted(sf_gross_demand):
        sf_carry = int(initial_stock.get(child_pn, 0))
        # 按交期升序 FIFO 核销半成品可用库存
        for due_date, gross_qty, parent_pn in sorted(sf_gross_demand[child_pn], key=lambda x: x[0]):
            covered = min(sf_carry, gross_qty)
            sf_carry -= covered
            remaining = gross_qty - covered
            if remaining <= 0:
                continue
            tasks.append(_make_task(child_pn, parts, fg_params, sf_params, remaining,
                                    due_date, False, parent_pn=parent_pn))

    # 3. 安全库存补产任务（FG 与 SF）
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
               parent_pn: str | None = None) -> Task:
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
    )


def _build_mold_lookup(repo: Repository, site: str,
                       fg_params: Mapping[str, Mapping[str, Any]],
                       sf_params: Mapping[str, Mapping[str, Any]],
                       ) -> dict[str, frozenset[str]]:
    lookup: dict[str, set[str]] = defaultdict(set)
    for row in repo.list_by("tooling_mold", site_code=site):
        for pn in row.get("applicable_pns") or ():
            lookup[row["mold_id"]].add(pn)
    for params in (fg_params, sf_params):
        for pn, row in params.items():
            mold_id = row.get("mold_id")
            if mold_id:
                lookup[mold_id].add(pn)
    return {key: frozenset(value) for key, value in sorted(lookup.items())}


def _build_changeover_lookup(repo: Repository, site: str, dataset: "DataSet",
                             log: IssueLog) -> dict[tuple, tuple[float | None, str | None]]:
    setup: dict[tuple, float] = {}
    for row in repo.list_by("setup_matrix", site_code=site):
        setup[(row["line_code"], row["from_mold"], row["to_mold"])] = \
            float(row["changeover_min"])

    mold_default: dict[str, float] = {}
    for row in repo.list_by("mold", site_code=site):
        if row.get("changeover_min") is not None:
            mold_default[row["mold_id"]] = float(row["changeover_min"])

    lookup: dict[tuple, tuple[float | None, str | None]] = {}
    molds = sorted({None, *mold_default, *{m for pair in setup for m in pair[1:]}})
    for line in dataset.lines:
        for to_mold in molds:
            for from_mold in molds:
                key = (line.line_code, from_mold, to_mold)
                if key in setup:
                    value: tuple[float | None, str | None] = (setup[key], "setup_matrix")
                elif to_mold is not None and to_mold in mold_default:
                    value = (mold_default[to_mold], "tooling_mold")
                else:
                    value = (None, None)
                for route in dataset.routes:
                    if route.line_code != line.line_code:
                        continue
                    full = (line.line_code, from_mold, to_mold, route.pn)
                    if value[0] is not None:
                        lookup[full] = value
                    elif route.changeover_min is not None:
                        lookup[full] = (route.changeover_min, "routing")
                    else:
                        lookup[full] = (None, None)

    for route in dataset.routes:
        if route.changeover_min is None and not mold_default:
            log.gap(entity="routing", field="changeover_min",
                    sheet="工艺路线与候选机台 Routing-Machines", column="换型时间 (分钟)",
                    message=f"料号 {route.pn} 在产线 {route.line_code} 上三级换型时间全缺"
                            f"（spec §3.2），换型相关规则将标记 degraded")
    return lookup
```

实现要点：

- `_build_changeover_lookup` 预先物化 `(line, from_mold, to_mold, pn)` 四元组的结果，这样求解器（Stage B）**不需要在建模循环里做三级回退判断**——这正是 spec §4.4「不留给求解器现场判断」的落地。模具词表来自 `setup_matrix` 与 `mold` 的并集，规模很小（实测 9 个模具 × 17 条产线），物化成本可忽略。
- `_resolve_mode` 的 `load_ratio` 用**每个 PN 的最小 `unit_ms`**（最乐观估计）作分子。理由：`auto` 只用于推荐工作制，乐观估计会让推荐偏保守（更少休息日），这在排产上是安全方向。分母是极限产能：`CONTINUOUS` 没有休息日，所以工作日数直接等于 `horizon.days`，不需要调 `is_working_day`。
- `DataSet.__post_init__` 用 `object.__setattr__` 往 frozen dataclass 上挂私有索引。这是标准做法，不要改成非 frozen。
- `_changeover_lookup` 在 `load()` 里挂载而非 `__post_init__`，因为它需要 `repo`。因此**直接用构造器 `DataSet(...)` 建的实例调用 `changeover()` 会返回 `(None, None)`**——这是已知限制，只有 `load()` 产出的实例才有完整回退表。测试全部走 `load()`。
- `certified_index` 是 `_build_routes` 的第二个返回值，**已按 `(priority, line_code)` 排好序并去重**。`certified_lines(pn)` 直接返回它，不要再排序。多工序料号的 `priority` 取该产线上**最小 `op_seq`** 那一行的值——因为选线决策发生在首工序。
- `Sequence` 与 `Mapping` 从 `collections.abc` 导入；`defaultdict` 从 `collections` 导入。`_build_*` 辅助函数放在 `DataSet` 类之后、模块末尾。

- [ ] **Step 5: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_dataset -v`
Expected: PASS（全部）

若 `test_auto_mode_picks_from_load_ratio` 失败，先打印实际 ratio 再判断：10 件 × 31765 ms ≈ 3.2e5 ms，分母 = 10 天 × 3 班 × 2.88e7 ms = 8.64e8 ms，ratio ≈ 3.7e-4，远小于 0.5714，必须是 `4ON3OFF`。

- [ ] **Step 6: 提交**

```bash
git add aps/dataset.py aps_tests/test_dataset.py Specification/aps_greenfield_rewrite.md
git commit -m "feat(dataset): DataSet 聚合根冻结净需求、毫秒标度、槽位与换型三级回退"
```

---

## Task 9: V3 模板生成器（登记表驱动）

**Files:**
- Create: `aps/data/template_v3.py`
- Modify: `aps/data/__init__.py`（追加 re-export）
- Test: `aps_tests/test_template_v3.py`

**Interfaces:**
- Consumes:
  ```python
  # aps/schema/registry.py（Task 3）
  DEPRECATED_HEADERS: dict[str, tuple[str, ...]]
  EntitySpec.sheet: str | None ; EntitySpec.fields ; EntitySpec.unpivot ; EntitySpec.pk
  FieldSpec.name / .dtype / .tier / .source_columns / .nullable / .pk / .fk / .unit
  FieldSpec.enum_codes / .parser / .fk_optional / .provenance / .resolved_parser
  def entities_with_sheet() -> tuple[EntitySpec, ...]
  # aps/schema/types.py（Task 2）
  class DataType(str, Enum) ; class ContractTier(str, Enum)
  # aps/data/excel.py（Task 5）
  def read_workbook(path, site) -> tuple[RawTables, IssueLog]
  ```
- Produces:
  ```python
  INSTRUCTIONS_SHEET: str                                  # "填写说明 Instructions"
  DEMAND_START: dt.date                                    # date(2026, 11, 3)
  DEMAND_DAYS: int                                         # 34
  SHEET_ORDER: tuple[str, ...]                             # 18 张业务表，按填写顺序
  TYPE_LABEL: dict[DataType, str]
  PREAMBLE: tuple[str, ...]
  def specs_for_sheet(sheet: str) -> tuple[EntitySpec, ...]
  def header_for(field: FieldSpec) -> str | None
  def headers_for_sheet(specs: Sequence[EntitySpec]) -> tuple[str, ...]
  def header_tier(specs: Sequence[EntitySpec], header: str) -> ContractTier
  def demand_date_columns(start: dt.date = DEMAND_START,
                          days: int = DEMAND_DAYS) -> tuple[dt.datetime, ...]
  def instruction_rows() -> tuple[tuple[str, ...], ...]     # 含表头行，每行 9 列
  def write_v3_template(out_path: str | os.PathLike[str], site: str,
                        demand_start: dt.date = DEMAND_START,
                        demand_days: int = DEMAND_DAYS) -> None
  ```

**设计裁决（照做，不要改）**：

1. **表头文字从登记表取，不另写一份常量表。** `_header_for(field)` 返回 `field.source_columns` 中**第一个不含全角括号**的那一个；若全都含全角括号则返回 `source_columns[0]`；`source_columns` 为空返回 `None`（该字段不在模板里出现，例如 `line.site_code`、`demand.demand_date`、`demand.quantity`）。这条规则的唯一实际作用是把 `工序提前期` 的 `提前准备时间（分钟）` 自动换成半角的 `提前准备时间 (分钟)`（spec §3.5 的模板修正），同时不需要维护一张「哪个字段要改」的例外表。
2. **「产线基础数据」不加「站点」列。** `line.site_code` 在登记表里**没有** `source_columns`（`site_injected=True`），所以生成器不会为它写表头。若强行加上，Task 5 的 `_report_structure` 会把它报成「模板未定义的多余列」warn——那是我们自己造的噪音。站点由 CLI 的 `--site` 注入，一份工作簿只属于一个站点，这在 V2 和 V3 都成立。
3. **`SHEET_ORDER` 是手写的业务填写顺序，不是字母序。** `REGISTRY` 按实体名字母排序（Task 3 的 `REGISTRY = {name: _REG[name] for name in sorted(_REG)}`），字母序对业务填表毫无意义。为防止 `SHEET_ORDER` 与登记表漂移，测试 `test_sheet_order_matches_registry` 断言两者集合相等且长度为 18。
4. **`工装模具约束 Tooling-Mold` 被 `mold` 与 `tooling_mold` 两个实体共用**（修订 A-3）。`specs_for_sheet` 返回两个 spec，`headers_for_sheet` 按 spec 顺序（字母序：`mold` 先）取表头并去重，所以生成顺序是 `站点 / 模具编号 (Mold ID) / 工装模具名称 / 可用套数 / 换模工时 (分钟) / 适用产线 / 设备 / 适用零件料号`——与 V2 的列序不同。列序可自由调整（表头驱动定位），这是合法的，测试把它钉死。
5. **需求日期轴写成 `datetime` 对象，`number_format = "yyyy-mm-dd"`。** V2 的日期表头就是 `datetime` 对象（实测），Task 5 的 `_extract_demand` 靠 `_looks_like_date` 认它。写字符串会让 V3 与 V2 走两条不同代码路径，那是自找的分支。
6. **「填写说明 Instructions」的内容全部由登记表推导**，只有 `PREAMBLE`（15 行固定说明）是手写文本。每列一行：`工作表 / 列名 / 类型 / 契约层级 / 必填 / 单位 / 允许值 / 主键 / 说明`。「说明」由 `_column_note` 机械拼装（主键、T1/T2 后果、外键指向、`fk_optional`、derived）。**不要为每一列手写业务说明**——那种文本会与登记表漂移，而 spec §3.5 只要求「逐表说明新增字段的填法与留空后果」，机械拼装已经覆盖。

- [ ] **Step 1: 写失败测试**

创建 `aps_tests/test_template_v3.py`：

```python
from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest

from openpyxl import load_workbook

from aps.data.excel import read_workbook
from aps.data.template_v3 import (
    DEMAND_DAYS,
    DEMAND_START,
    INSTRUCTIONS_SHEET,
    PREAMBLE,
    SHEET_ORDER,
    TYPE_LABEL,
    demand_date_columns,
    header_for,
    header_tier,
    headers_for_sheet,
    instruction_rows,
    specs_for_sheet,
    write_v3_template,
)
from aps.schema.registry import DEPRECATED_HEADERS, REGISTRY, entities_with_sheet
from aps.schema.types import ContractTier, DataType

SITE = "WL"

V3_SHEETS = ("班次人力可用性", "产品配对关系", "工作日历", "换模技术员")


class TemplateCase(unittest.TestCase):
    """生成一次 V3 模板，供多个测试共用。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls._tmp.name, "APS_DataSource_Template - WL -V3.xlsx")
        write_v3_template(cls.path, SITE)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        self.wb = load_workbook(self.path)
        self.addCleanup(self.wb.close)

    def headers(self, sheet: str) -> tuple:
        ws = self.wb[sheet]
        out = []
        for col in range(1, (ws.max_column or 0) + 1):
            value = ws.cell(1, col).value
            if value is not None:
                out.append(value)
        return tuple(out)

    def texts(self, sheet: str) -> tuple[str, ...]:
        return tuple(str(v) for v in self.headers(sheet) if not isinstance(v, dt.datetime))


class TestSheetOrder(unittest.TestCase):
    def test_sheet_order_matches_registry(self):
        self.assertEqual(
            set(SHEET_ORDER), {spec.sheet for spec in entities_with_sheet()}
        )
        self.assertEqual(18, len(SHEET_ORDER))
        self.assertEqual(18, len(set(SHEET_ORDER)))

    def test_sheet_order_has_no_duplicates(self):
        self.assertEqual(len(SHEET_ORDER), len(set(SHEET_ORDER)))

    def test_specs_for_sheet_groups_shared_sheet(self):
        specs = specs_for_sheet("工装模具约束 Tooling-Mold")
        self.assertEqual(("mold", "tooling_mold"), tuple(s.name for s in specs))

    def test_specs_for_single_sheet_entities(self):
        self.assertEqual(("line",), tuple(s.name for s in specs_for_sheet("产线基础数据")))

    def test_sheets_are_written_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.xlsx")
            write_v3_template(path, SITE)
            wb = load_workbook(path)
            self.addCleanup(wb.close)
        self.assertEqual([INSTRUCTIONS_SHEET, *SHEET_ORDER], wb.sheetnames)


class TestHeaderDerivation(unittest.TestCase):
    def test_header_prefers_halfwidth_parens(self):
        field = REGISTRY["operation_leadtime"].field("prep_minutes")
        self.assertEqual("提前准备时间 (分钟)", header_for(field))

    def test_header_is_none_for_injected_and_derived(self):
        self.assertIsNone(header_for(REGISTRY["line"].field("site_code")))
        self.assertIsNone(header_for(REGISTRY["demand"].field("demand_date")))
        self.assertIsNone(header_for(REGISTRY["demand"].field("quantity")))

    def test_headers_dedupe_across_shared_sheet(self):
        headers = headers_for_sheet(specs_for_sheet("工装模具约束 Tooling-Mold"))
        self.assertEqual(len(headers), len(set(headers)))
        self.assertEqual(
            ("站点", "模具编号 (Mold ID)", "工装模具名称", "可用套数",
             "换模工时 (分钟)", "适用产线/设备", "适用零件料号"),
            headers,
        )

    def test_line_sheet_has_no_site_column(self):
        self.assertEqual(
            ("产线", "效率 TRP", "单班工时 (小时)", "每天班次数"),
            headers_for_sheet(specs_for_sheet("产线基础数据")),
        )

    def test_demand_headers(self):
        self.assertEqual(
            ("站点", "料号 (PN)", "品名描述", "客户代码 (Code)"),
            headers_for_sheet(specs_for_sheet("客户需求订单清单 Customer Demand")),
        )

    def test_header_tier_t1_wins_on_shared_sheet(self):
        specs = specs_for_sheet("工装模具约束 Tooling-Mold")
        self.assertIs(ContractTier.T1, header_tier(specs, "可用套数"))
        self.assertIs(ContractTier.T2, header_tier(specs, "换模工时 (分钟)"))
        self.assertIs(ContractTier.T2, header_tier(specs, "适用零件料号"))

    def test_every_sheet_header_is_known_or_date(self):
        for sheet in SHEET_ORDER:
            specs = specs_for_sheet(sheet)
            known = set()
            for spec in specs:
                known.update(spec.header_to_field)
            for header in headers_for_sheet(specs):
                self.assertIn(header, known, f"{sheet}: {header}")

    def test_no_deprecated_header_is_generated(self):
        for sheet, deprecated in DEPRECATED_HEADERS.items():
            generated = set(headers_for_sheet(specs_for_sheet(sheet)))
            self.assertEqual(frozenset(), generated & set(deprecated), sheet)


class TestTypeLabel(unittest.TestCase):
    def test_every_data_type_has_a_label(self):
        self.assertEqual(set(DataType), set(TYPE_LABEL))

    def test_labels_are_non_empty(self):
        for dtype, label in TYPE_LABEL.items():
            self.assertTrue(label.strip(), dtype)


class TestDemandDateAxis(unittest.TestCase):
    def test_default_axis_matches_v2(self):
        columns = demand_date_columns()
        self.assertEqual(DEMAND_DAYS, len(columns))
        self.assertEqual(dt.datetime(2026, 11, 3), columns[0])
        self.assertEqual(dt.datetime(2026, 12, 6), columns[-1])

    def test_axis_is_contiguous_daily(self):
        columns = demand_date_columns()
        for index in range(1, len(columns)):
            self.assertEqual(dt.timedelta(days=1), columns[index] - columns[index - 1])

    def test_axis_is_configurable(self):
        columns = demand_date_columns(dt.date(2027, 1, 1), 3)
        self.assertEqual(
            (dt.datetime(2027, 1, 1), dt.datetime(2027, 1, 2), dt.datetime(2027, 1, 3)),
            columns,
        )

    def test_zero_days_yields_empty_axis(self):
        self.assertEqual((), demand_date_columns(DEMAND_START, 0))


class TestWrittenWorkbook(TemplateCase):
    def test_instructions_sheet_exists(self):
        self.assertIn(INSTRUCTIONS_SHEET, self.wb.sheetnames)

    def test_v3_extension_sheets_exist(self):
        for sheet in V3_SHEETS:
            self.assertIn(sheet, self.wb.sheetnames)

    def test_shift_staffing_headers(self):
        self.assertEqual(
            ("站点", "产线", "日期", "班次", "可用人数"),
            self.texts("班次人力可用性"),
        )

    def test_product_pairing_headers(self):
        self.assertEqual(
            ("站点", "左件料号", "右件料号", "配对类型", "同步容差班次"),
            self.texts("产品配对关系"),
        )

    def test_calendar_day_headers(self):
        self.assertEqual(
            ("站点", "产线", "日期", "班次序号", "是否开班", "备注"),
            self.texts("工作日历"),
        )

    def test_setup_technician_headers(self):
        self.assertEqual(
            ("站点", "技术员", "日期", "班次", "可用产线", "可同时换模数"),
            self.texts("换模技术员"),
        )

    def test_leadtime_uses_halfwidth_parens(self):
        headers = self.texts("工序提前期 Operation-LeadTime")
        self.assertIn("提前准备时间 (分钟)", headers)
        self.assertNotIn("提前准备时间（分钟）", headers)

    def test_line_sheet_written_without_site(self):
        self.assertEqual(("产线", "效率 TRP", "单班工时 (小时)", "每天班次数"),
                         self.texts("产线基础数据"))

    def test_demand_sheet_has_date_axis(self):
        headers = self.headers("客户需求订单清单 Customer Demand")
        self.assertEqual(
            ("站点", "料号 (PN)", "品名描述", "客户代码 (Code)"), headers[:4]
        )
        dates = headers[4:]
        self.assertEqual(DEMAND_DAYS, len(dates))
        self.assertTrue(all(isinstance(v, dt.datetime) for v in dates))
        self.assertEqual(dt.datetime(2026, 11, 3), dates[0])

    def test_demand_date_axis_honours_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.xlsx")
            write_v3_template(path, SITE, dt.date(2027, 3, 1), 5)
            wb = load_workbook(path)
            self.addCleanup(wb.close)
            ws = wb["客户需求订单清单 Customer Demand"]
            dates = [ws.cell(1, col).value for col in range(5, 10)]
        self.assertEqual(dt.datetime(2027, 3, 1), dates[0])
        self.assertEqual(dt.datetime(2027, 3, 5), dates[4])
        self.assertIsNone(ws.cell(1, 10).value)

    def test_date_header_number_format_is_date_only(self):
        ws = self.wb["客户需求订单清单 Customer Demand"]
        self.assertEqual("yyyy-mm-dd", ws.cell(1, 5).number_format)

    def test_headers_are_frozen(self):
        for sheet in SHEET_ORDER:
            self.assertEqual("A2", self.wb[sheet].freeze_panes, sheet)

    def test_t2_headers_are_greyed(self):
        ws = self.wb["工艺路线与候选机台 Routing-Machines"]
        headers = self.texts("工艺路线与候选机台 Routing-Machines")
        col_of = {header: index + 1 for index, header in enumerate(headers)}
        t2 = ws.cell(1, col_of["人数"]).fill.start_color.rgb
        t1 = ws.cell(1, col_of["CT (秒)"]).fill.start_color.rgb
        self.assertNotEqual(t1, t2)

    def test_no_data_rows_are_written(self):
        for sheet in SHEET_ORDER:
            self.assertEqual(1, self.wb[sheet].max_row, sheet)


class TestInstructions(TemplateCase):
    def test_rows_have_nine_columns(self):
        for row in instruction_rows():
            self.assertEqual(9, len(row))

    def test_first_row_is_the_header(self):
        self.assertEqual(
            ("工作表", "列名", "类型", "契约层级", "必填", "单位", "允许值", "主键", "说明"),
            instruction_rows()[0],
        )

    def test_every_generated_column_is_documented(self):
        documented = {(row[0], row[1]) for row in instruction_rows()[1:]}
        for sheet in SHEET_ORDER:
            for header in headers_for_sheet(specs_for_sheet(sheet)):
                self.assertIn((sheet, str(header)), documented, f"{sheet}: {header}")

    def test_documented_columns_have_no_duplicates(self):
        keys = [(row[0], row[1]) for row in instruction_rows()[1:]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_t1_rows_are_marked_required(self):
        rows = {row[:2]: row for row in instruction_rows()[1:]}
        self.assertEqual("是", rows[("工艺路线与候选机台 Routing-Machines", "CT (秒)")][4])
        self.assertEqual("否", rows[("工艺路线与候选机台 Routing-Machines", "人数")][4])

    def test_enum_values_are_listed(self):
        rows = {row[:2]: row for row in instruction_rows()[1:]}
        self.assertEqual("NORMAL / MONTH_END", rows[("库存策略 Inventory-Policy", "周期类型")][6])

    def test_foreign_key_is_documented(self):
        rows = {row[:2]: row for row in instruction_rows()[1:]}
        self.assertIn("part.pn", rows[("客户需求订单清单 Customer Demand", "料号 (PN)")][8])

    def test_fk_optional_is_documented(self):
        rows = {row[:2]: row for row in instruction_rows()[1:]}
        note = rows[("产品装配清单 BOM List", "子件：半成品料号 (SF PN)")][8]
        self.assertIn("只 warn", note)

    def test_demand_date_axis_is_documented(self):
        rows = {(row[0], row[1]) for row in instruction_rows()[1:]}
        self.assertIn(("客户需求订单清单 Customer Demand", "（日期列）"), rows)

    def test_preamble_mentions_all_five_extensions(self):
        text = "\n".join(PREAMBLE)
        self.assertIn("客户代码 (Code)", text)
        for sheet in V3_SHEETS:
            self.assertIn(sheet, text)

    def test_preamble_mentions_site_injection(self):
        self.assertTrue(any("--site" in line for line in PREAMBLE))

    def test_instructions_sheet_contains_preamble_and_rows(self):
        ws = self.wb[INSTRUCTIONS_SHEET]
        text = "\n".join(
            str(cell.value)
            for row in ws.iter_rows()
            for cell in row
            if cell.value is not None
        )
        self.assertIn(SITE, text)
        self.assertIn("班次人力可用性", text)
        self.assertIn("提前准备时间 (分钟)", text)
        self.assertIn("客户代码 (Code)", text)
        self.assertLess(len(instruction_rows()) + len(PREAMBLE), ws.max_row)


class TestRoundTrip(TemplateCase):
    def test_generated_template_imports_without_errors(self):
        _tables, log = read_workbook(self.path, SITE)
        self.assertEqual([], [i.message for i in log.errors])

    def test_generated_template_yields_no_data_rows(self):
        tables, _log = read_workbook(self.path, SITE)
        self.assertEqual(1, len(tables["site"]))
        self.assertEqual([], tables["part"])
        self.assertEqual([], tables.get("demand", []))
        self.assertEqual([], tables.get("routing", []))

    def test_missing_v3_sheets_are_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "v2like.xlsx")
            write_v3_template(path, SITE)
            wb = load_workbook(path)
            for sheet in V3_SHEETS:
                wb.remove(wb[sheet])
            wb.save(path)
            _tables, log = read_workbook(path, SITE)
        self.assertEqual([], [i.message for i in log.errors])
        self.assertTrue(any("班次人力可用性" in i.message for i in log.warnings))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_template_v3 -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'aps.data.template_v3'`

- [ ] **Step 3: 实现 `aps/data/template_v3.py`**

```python
from __future__ import annotations

import datetime as dt
import os
from collections.abc import Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from aps.schema.registry import EntitySpec, FieldSpec, entities_with_sheet
from aps.schema.types import ContractTier, DataType

__all__ = [
    "DEMAND_DAYS",
    "DEMAND_START",
    "INSTRUCTIONS_SHEET",
    "PREAMBLE",
    "SHEET_ORDER",
    "TYPE_LABEL",
    "demand_date_columns",
    "header_for",
    "header_tier",
    "headers_for_sheet",
    "instruction_rows",
    "specs_for_sheet",
    "write_v3_template",
]

INSTRUCTIONS_SHEET = "填写说明 Instructions"
DEMAND_START = dt.date(2026, 11, 3)
DEMAND_DAYS = 34

SHEET_ORDER: tuple[str, ...] = (
    "客户需求订单清单 Customer Demand",
    "期初可用库存 Initial Stock",
    "关键物料到货 Material-Arrival-ETA",
    "产线与成品参数 Parameters-FG",
    "半成品制造参数 Parameters-SFN",
    "产品装配清单 BOM List",
    "工艺路线与候选机台 Routing-Machines",
    "换模矩阵 Setup-Matrix",
    "库存策略 Inventory-Policy",
    "工序提前期 Operation-LeadTime",
    "工装模具约束 Tooling-Mold",
    "产线基础数据",
    "设备预防维护 Preventive-Maintenance",
    "客户战略评级 Customer-Priority",
    "班次人力可用性",
    "产品配对关系",
    "工作日历",
    "换模技术员",
)

TYPE_LABEL: dict[DataType, str] = {
    DataType.TEXT: "文本",
    DataType.INT: "整数",
    DataType.REAL: "小数",
    DataType.BOOL: "是/否（true/false、1/0、yes/no、是/否）",
    DataType.DATE: "日期（yyyy-mm-dd 或 yyyy/mm/dd）",
    DataType.TIME: "时间（hh:mm 或 hh:mm:ss）",
    DataType.DATETIME: "日期时间（yyyy-mm-dd hh:mm）",
    DataType.MULTI_TEXT: "多个值，用英文逗号分隔",
}

PREAMBLE: tuple[str, ...] = (
    "APS 数据源模板 V3。本工作簿由实体登记表自动生成，不要手工增删列。",
    "列序可以自由调整——导入器按表头文字定位列，不按列号。",
    "白底加粗表头 = 契约层级 T1（必填）：缺列或整列留空会阻止导入。",
    "灰底表头 = 契约层级 T2（选填）：整列留空不阻止导入，但依赖它的规则会标记 degraded，"
    "并在绑定报告里可见。",
    "「产线基础数据」没有「站点」列：站点由导入命令的 --site 参数注入，"
    "一份工作簿只属于一个站点。",
    "「客户需求订单清单」是宽表：左侧四列是料号信息，右侧每一列表头是一个日期，"
    "单元格填该日需求量（件）。留空或填 0 表示当日无需求。",
    "V3 相对 V2 的五项扩展（spec §3.5）：",
    "  1. 「客户需求订单清单」新增列「客户代码 (Code)」——把需求行连到「客户战略评级」，"
    "T2。留空则客户保供加权规则标记 degraded。",
    "  2. 新增表「班次人力可用性」——站点 / 产线 / 日期 / 班次 / 可用人数，"
    "劳动力人数上限规则的输入，T2。",
    "  3. 新增表「产品配对关系」——站点 / 左件料号 / 右件料号 / 配对类型 / 同步容差班次，"
    "共产品同步产出的 schema 预留，本版模型不读，T2。",
    "  4. 新增表「工作日历」——站点 / 产线 / 日期 / 班次序号 / 是否开班 / 备注，"
    "用来表达「某天临时停一班」。它与工作制推导冲突时以本表为准，T2。",
    "  5. 新增表「换模技术员」——站点 / 技术员 / 日期 / 班次 / 可用产线 / 可同时换模数，"
    "换型技术员跨线互斥的 schema 预留，本版模型不读，T2。",
    "扩展 2–5 是四张新表。它们在 V2 工作簿里不存在是合法的，导入器只 warn、不 error。",
    "「工序提前期」的「提前准备时间 (分钟)」在 V3 统一为半角括号；"
    "V2 的全角写法「提前准备时间（分钟）」导入器仍然认。",
    "已废弃列（spec §3.3）在本模板中不再出现：Parameters-FG / Parameters-SFN 的「产线」"
    "「效率 TRP」、Routing-Machines 的「备选候选线」「机台」「TRP」、"
    "Material-Arrival-ETA 的「放行工时」「关联排产零件」「状态」。",
)

INSTRUCTION_HEADER: tuple[str, ...] = (
    "工作表", "列名", "类型", "契约层级", "必填", "单位", "允许值", "主键", "说明",
)

_T2_FILL = PatternFill(start_color="FFD9D9D9", end_color="FFD9D9D9", fill_type="solid")
_HEADER_FONT = Font(bold=True)
_HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)


def specs_for_sheet(sheet: str) -> tuple[EntitySpec, ...]:
    return tuple(spec for spec in entities_with_sheet() if spec.sheet == sheet)


def header_for(field: FieldSpec) -> str | None:
    if not field.source_columns:
        return None
    for header in field.source_columns:
        if "（" not in header and "）" not in header:
            return header
    return field.source_columns[0]


def headers_for_sheet(specs: Sequence[EntitySpec]) -> tuple[str, ...]:
    out: list[str] = []
    for spec in specs:
        for field in spec.fields:
            header = header_for(field)
            if header is not None and header not in out:
                out.append(header)
    return tuple(out)


def header_tier(specs: Sequence[EntitySpec], header: str) -> ContractTier:
    tiers = [
        field.tier
        for spec in specs
        for field in spec.fields
        if header_for(field) == header
    ]
    if any(tier is ContractTier.T1 for tier in tiers):
        return ContractTier.T1
    return ContractTier.T2


def demand_date_columns(start: dt.date = DEMAND_START,
                        days: int = DEMAND_DAYS) -> tuple[dt.datetime, ...]:
    return tuple(
        dt.datetime.combine(start + dt.timedelta(days=offset), dt.time())
        for offset in range(max(0, days))
    )


def _field_for_header(specs: Sequence[EntitySpec],
                      header: str) -> tuple[EntitySpec | None, FieldSpec | None]:
    for spec in specs:
        for field in spec.fields:
            if header_for(field) == header:
                return spec, field
    return None, None


def _allowed_values(field: FieldSpec) -> str:
    if field.enum_codes:
        return " / ".join(field.enum_codes)
    if field.dtype is DataType.BOOL:
        return TYPE_LABEL[DataType.BOOL]
    return ""


def _column_note(spec: EntitySpec, field: FieldSpec) -> str:
    parts: list[str] = []
    if field.pk:
        parts.append("主键列：留空的行会被整行忽略")
    if field.tier is ContractTier.T1:
        parts.append("T1 必填，缺列或留空阻止导入")
    else:
        parts.append("T2 选填，整列留空则依赖它的规则标记 degraded")
    if field.fk:
        parts.append(f"外键 → {field.fk}")
        if field.fk_optional:
            parts.append("外键找不到目标时只 warn，不阻断（修订 A-5）")
    if field.provenance == "derived":
        parts.append("由系统推导")
    if field.resolved_parser == "enum_code":
        parts.append("可填「中文标签 (代码)」，也可直接填代码")
    elif field.resolved_parser == "stage":
        parts.append("FING 自动归一为 FG，SFIN 自动归一为 SF")
    elif field.resolved_parser == "shift_index":
        parts.append("早班/白班→0，中班→1，晚班→2，夜班→1")
    return "；".join(parts)


def instruction_rows() -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = [INSTRUCTION_HEADER]
    for sheet in SHEET_ORDER:
        specs = specs_for_sheet(sheet)
        for header in headers_for_sheet(specs):
            spec, field = _field_for_header(specs, header)
            if spec is None or field is None:
                continue
            rows.append((
                sheet,
                header,
                TYPE_LABEL[field.dtype],
                field.tier.name,
                "是" if field.tier is ContractTier.T1 else "否",
                field.unit or "",
                _allowed_values(field),
                "是" if field.pk else "",
                _column_note(spec, field),
            ))
        if specs and specs[0].unpivot == "demand_date":
            rows.append((
                sheet, "（日期列）", TYPE_LABEL[DataType.DATE], "T1", "是", "件",
                f"{DEMAND_START.isoformat()} 起连续 {DEMAND_DAYS} 天，每日一列", "",
                "需求宽表的日期轴。单元格填该日需求量；留空或 ≤ 0 视为无需求，"
                "不会生成需求行。",
            ))
    return tuple(rows)


def _write_header(ws: Worksheet, headers: Sequence, specs: Sequence[EntitySpec]) -> None:
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(1, col)
        cell.value = header
        cell.font = _HEADER_FONT
        letter = get_column_letter(col)
        if isinstance(header, dt.datetime):
            cell.number_format = "yyyy-mm-dd"
            ws.column_dimensions[letter].width = 12
            continue
        cell.alignment = _HEADER_ALIGN
        if header_tier(specs, header) is ContractTier.T2:
            cell.fill = _T2_FILL
        ws.column_dimensions[letter].width = max(12, min(30, len(header) + 6))
    ws.freeze_panes = "A2"


def _write_instructions(wb: Workbook, site: str) -> None:
    ws = wb.create_sheet(INSTRUCTIONS_SHEET)
    ws.cell(1, 1, f"填写说明 Instructions（V3 · 站点 {site}）").font = Font(bold=True, size=14)
    row = 3
    for line in PREAMBLE:
        ws.cell(row, 1, line)
        row += 1
    row += 1
    for offset, cells in enumerate(instruction_rows()):
        for col, value in enumerate(cells, start=1):
            cell = ws.cell(row + offset, col, value)
            if offset == 0:
                cell.font = _HEADER_FONT
            cell.alignment = Alignment(vertical="top", wrap_text=(col == 9))
    widths = {"A": 34, "B": 26, "C": 30, "D": 10, "E": 8, "F": 10, "G": 34, "H": 8, "I": 78}
    for letter, width in sorted(widths.items()):
        ws.column_dimensions[letter].width = width


def write_v3_template(out_path: str | os.PathLike[str], site: str,
                      demand_start: dt.date = DEMAND_START,
                      demand_days: int = DEMAND_DAYS) -> None:
    wb = Workbook()
    wb.remove(wb.active)
    _write_instructions(wb, site)
    for sheet in SHEET_ORDER:
        specs = specs_for_sheet(sheet)
        headers: list = list(headers_for_sheet(specs))
        if specs and specs[0].unpivot == "demand_date":
            headers.extend(demand_date_columns(demand_start, demand_days))
        _write_header(wb.create_sheet(sheet), headers, specs)
    wb.save(os.fspath(out_path))
```

实现要点：

- `wb.remove(wb.active)` 去掉 `Workbook()` 自带的 `Sheet`，否则产出会多一张空表。
- `_write_header` 的 `headers` 参数是 `Sequence`（不带类型参数），因为它混合了 `str` 与 `dt.datetime`。不要写成 `Sequence[str]`。
- `header_tier` 对共用 sheet 取**最严**层级（T1 胜）。`可用套数` 在 `mold` 与 `tooling_mold` 里都是 T1，`换模工时 (分钟)` 在两处都是 T2，所以实测没有层级冲突；这条规则是防未来漂移。
- `_column_note` 里 `field.provenance == "derived"` 这一支在 V3 模板中**永远不会命中**——`header_for` 已经把所有 derived 字段（`demand_date` / `quantity` / `line.site_code`）过滤掉了。保留它是因为 `instruction_rows` 的契约是「登记表里所有有表头的字段」，如果将来某个 derived 字段被赋予 `source_columns`，说明文本必须仍然正确。
- `write_v3_template` 不校验 `demand_days` 的上限。34 天是实测 V2 的规模；业务要 90 天就传 90，Excel 撑得住（16384 列上限）。
- 测试 `test_no_data_rows_are_written` 断言每张业务表 `max_row == 1`。**不要为了让模板「好看」而写示例行**——示例行会被 `_row_is_valid` 判为有效数据行，导入时变成幽灵料号（这正是 G13 要防的事）。

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_template_v3 -v`
Expected: PASS（全部）

若 `test_t2_headers_are_greyed` 失败，先确认 `PatternFill` 的 `start_color.rgb` 在无填充时返回 `"00000000"`——T1 表头不设 `fill`，所以两者的 `rgb` 必然不同。若相同，说明 `header_tier` 把 `人数` 判成了 T1，回去查 `routing.headcount` 的 `tier`。

- [ ] **Step 5: 更新 `aps/data/__init__.py`**

在现有 re-export 基础上追加：

```python
from aps.data.template_v3 import (
    DEMAND_DAYS,
    DEMAND_START,
    INSTRUCTIONS_SHEET,
    PREAMBLE,
    SHEET_ORDER,
    TYPE_LABEL,
    demand_date_columns,
    header_for,
    header_tier,
    headers_for_sheet,
    instruction_rows,
    specs_for_sheet,
    write_v3_template,
)
```

并把这些名字加进 `__all__`。

- [ ] **Step 6: 生成一份 V3 模板并人工核对**

Run:
```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "
from aps.data.template_v3 import write_v3_template
write_v3_template('APS_DataSource_Template - WL -V3.xlsx', 'WL')
print('written')
"
```

用 Excel 打开 `APS_DataSource_Template - WL -V3.xlsx`，逐项核对：

1. 第一张表是 `填写说明 Instructions`，A1 是标题，第 3 行起是 15 行固定说明，再往下是九列表头 + 每列一行。
2. 共 19 张表（1 说明 + 18 业务）。
3. `客户需求订单清单 Customer Demand` 的 E1 起是 34 个日期，显示为 `2026-11-03` 而非 `2026-11-03 00:00:00`。
4. `工序提前期 Operation-LeadTime` 的表头是半角括号 `提前准备时间 (分钟)`。
5. `产线基础数据` 只有四列，没有 `站点`。
6. `人数`、`换型时间 (分钟)`、`模具清单` 等 T2 列是灰底；`CT (秒)`、`认证`、`主候选线` 是白底加粗。
7. 四张 V3 新表存在且只有表头、无数据行。

核对完把生成的文件删掉——**不要把 V3 模板提交进仓库**（它是产物，不是源；`git status` 里不该出现它）：

```bash
rm "APS_DataSource_Template - WL -V3.xlsx"
```

- [ ] **Step 7: 确认没破坏前面的任务**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --new`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add aps/data/template_v3.py aps/data/__init__.py aps_tests/test_template_v3.py
git commit -m "feat(data): 登记表驱动的 V3 模板生成器与填写说明"
```

---

## Task 10: 导入编排 `import_workbook`

**Files:**
- Create: `aps/data/importer.py`
- Modify: `aps/data/__init__.py`（追加 re-export）
- Test: `aps_tests/test_importer.py`

**Interfaces:**
- Consumes:
  ```python
  # aps/data/excel.py（Task 5）
  def read_workbook(path, site) -> tuple[RawTables, IssueLog]
  # aps/data/validate.py（Task 6）
  ParsedTables = dict[str, list[CandidateRow]] ; CandidateRow.as_row() -> dict[str, Any]
  ValidationReport(errors, warnings, gaps) ; .ok ; .counts() ; .render(limit=40)
  def validate(tables: RawTables, structure_log: IssueLog) -> tuple[ParsedTables, ValidationReport]
  # aps/data/issues.py（Task 5）
  Issue(severity, entity, field, sheet, row, column, message, value=None) ; Severity ; IssueLog
  # aps/data/sqlite.py（Task 4）
  SqliteRepository(db_path) ; .initialize_schema() ; .replace_site(site, rows) ; .close()
  ```
- Produces:
  ```python
  @dataclass(frozen=True)
  class ImportResult:
      site: str
      db_path: str
      workbook_path: str
      report: ValidationReport
      counts: Mapping[str, int]
      wrote: bool
      @property def ok(self) -> bool
      @property def degraded_fields(self) -> frozenset[str]   # "entity.field"，来自 report.gaps
      def summary(self, limit: int = 40) -> str

  def rows_from_parsed(parsed: ParsedTables) -> dict[str, list[Row]]
  def import_workbook(workbook_path: str | os.PathLike[str],
                      db_path: str | os.PathLike[str], site: str) -> ImportResult
  ```

**设计裁决（照做，不要改）**：

1. **有 error 就完全不碰数据库。** `import_workbook` 在 `report.ok` 为假时**直接返回**，不构造 `SqliteRepository`、不建库文件。这是 spec §4.3「`errors` 阻止入库」的字面落地——不是「回滚」，是「根本不开事务」。测试 `test_failing_import_does_not_create_db_file` 断言 `os.path.exists(db) is False`。
2. **`initialize_schema()` 每次导入都调用。** DDL 全部是 `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`（Task 4），所以幂等。不要加「表已存在就跳过」的判断——那是第二处真相源，而且会在登记表新增实体后静默漏建表。
3. **`ImportResult.degraded_fields` 由 `report.gaps` 派生，不新增机制。** spec §3.6 要求「依赖 T2 字段且该字段整列为空的规则状态自动为 degraded」，而 Task 6 的 `detect_gaps` 已经在 T2 整列为空时发 GAP。所以 `frozenset(f"{i.entity}.{i.field}" for i in report.gaps)` 就是 `DataSet.load(degraded_fields=...)` 的输入，中间不需要任何新数据结构。
4. **`importer.py` 是唯一编排者**（见 File Structure 的职责边界）。它 import `excel` / `validate` / `sqlite`，反之三者都**不得** import 它。
5. **不接 `repo` 参数。** 调用方要复用连接的需求现在不存在；等 Stage B 真的需要在同一进程里连续导入两个站点再加，不要现在预留。

- [ ] **Step 1: 写失败测试**

创建 `aps_tests/test_importer.py`：

```python
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from aps.data.importer import ImportResult, import_workbook, rows_from_parsed
from aps.data.issues import IssueLog
from aps.data.sqlite import SqliteRepository
from aps.data.validate import CandidateRow, ValidationReport

SITE = "WL"
WORKBOOK = "APS_DataSource_Template - WL -V2.xlsx"

LINE_ROW = {"site_code": SITE, "line_code": "PGLINKAGE", "name": "PGLINKAGE",
            "trp_efficiency": 0.85, "shift_hours": 8.0, "shifts_per_day": 3}


def _report(errors=(), warnings=(), gaps=()) -> ValidationReport:
    return ValidationReport(errors=tuple(errors), warnings=tuple(warnings),
                            gaps=tuple(gaps))


def _gap_issue(entity: str, field: str):
    log = IssueLog()
    log.gap(entity=entity, field=field, sheet=None, row=None, column=None,
            message="T2 字段整列为空")
    return log.issues[0]


def _error_issue(entity: str, field: str):
    log = IssueLog()
    log.error(entity=entity, field=field, sheet=None, row=None, column=None,
              message="缺少必需列（T1）")
    return log.issues[0]


def _warning_issue(entity: str, field: str):
    log = IssueLog()
    log.warning(entity=entity, field=field, sheet=None, row=None, column=None,
                message="模板未定义的多余列，已忽略")
    return log.issues[0]


def _parsed_line(code: str = "PGLINKAGE") -> dict[str, list[CandidateRow]]:
    return {"line": [CandidateRow(entity="line", excel_row=2,
                                  values=dict(LINE_ROW, line_code=code, name=code))]}


class ImporterCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = os.path.join(self._tmp.name, "aps.db")

    def run_import(self, parsed, report, tables=None, log=None):
        tables = {} if tables is None else tables
        log = IssueLog() if log is None else log
        with patch("aps.data.importer.read_workbook",
                   return_value=(tables, log)) as reader, \
                patch("aps.data.importer.validate",
                      return_value=(parsed, report)) as validator:
            result = import_workbook(WORKBOOK, self.db, SITE)
        self.reader = reader
        self.validator = validator
        return result

    def repo(self) -> SqliteRepository:
        repo = SqliteRepository(self.db)
        self.addCleanup(repo.close)
        return repo


class TestRowsFromParsed(unittest.TestCase):
    def test_converts_candidates_to_plain_dicts(self):
        rows = rows_from_parsed(_parsed_line())
        self.assertEqual([dict(LINE_ROW)], rows["line"])

    def test_returns_a_copy_not_the_original(self):
        candidate = CandidateRow(entity="line", excel_row=2, values=dict(LINE_ROW))
        rows = rows_from_parsed({"line": [candidate]})
        rows["line"][0]["line_code"] = "MUTATED"
        self.assertEqual("PGLINKAGE", candidate.values["line_code"])

    def test_keys_are_sorted(self):
        parsed = {
            "routing": [CandidateRow("routing", 2, {"pn": "P"})],
            "bom": [CandidateRow("bom", 2, {"parent_pn": "P"})],
            "line": [CandidateRow("line", 2, dict(LINE_ROW))],
        }
        self.assertEqual(["bom", "line", "routing"], list(rows_from_parsed(parsed)))

    def test_empty_table_yields_empty_list(self):
        self.assertEqual({"line": []}, rows_from_parsed({"line": []}))


class TestFailureBlocksWrite(ImporterCase):
    def test_wrote_is_false(self):
        result = self.run_import(_parsed_line(), _report([_error_issue("line", "line_code")]))
        self.assertFalse(result.wrote)
        self.assertFalse(result.ok)

    def test_failing_import_does_not_create_db_file(self):
        self.run_import(_parsed_line(), _report([_error_issue("line", "line_code")]))
        self.assertFalse(os.path.exists(self.db))

    def test_counts_are_empty(self):
        result = self.run_import(_parsed_line(), _report([_error_issue("line", "line_code")]))
        self.assertEqual({}, dict(result.counts))

    def test_existing_data_survives_a_failing_reimport(self):
        self.run_import(_parsed_line("GOOD"), _report())
        self.run_import(_parsed_line("BAD"), _report([_error_issue("line", "line_code")]))
        repo = self.repo()
        self.assertEqual(["GOOD"], [r["line_code"] for r in repo.list_by("line")])

    def test_warnings_alone_do_not_block(self):
        result = self.run_import(_parsed_line(),
                                 _report(warnings=[_warning_issue("line", "name")]))
        self.assertTrue(result.wrote)
        self.assertTrue(result.ok)

    def test_gaps_alone_do_not_block(self):
        result = self.run_import(_parsed_line(),
                                 _report(gaps=[_gap_issue("routing", "headcount")]))
        self.assertTrue(result.wrote)
        self.assertTrue(result.ok)


class TestSuccess(ImporterCase):
    def test_counts_come_from_replace_site(self):
        result = self.run_import(_parsed_line(), _report())
        self.assertEqual({"line": 1}, dict(result.counts))

    def test_rows_land_in_the_database(self):
        self.run_import(_parsed_line(), _report())
        repo = self.repo()
        self.assertEqual([dict(LINE_ROW)], repo.list_by("line"))

    def test_site_row_is_created(self):
        self.run_import(_parsed_line(), _report())
        self.assertIsNotNone(self.repo().get_by_pk("site", (SITE,)))

    def test_empty_parsed_still_writes_site_row(self):
        result = self.run_import({}, _report())
        self.assertTrue(result.wrote)
        self.assertEqual({}, dict(result.counts))
        self.assertIsNotNone(self.repo().get_by_pk("site", (SITE,)))

    def test_reimport_replaces_previous_rows(self):
        self.run_import(_parsed_line("OLD"), _report())
        self.run_import(_parsed_line("NEW"), _report())
        self.assertEqual(["NEW"], [r["line_code"] for r in self.repo().list_by("line")])

    def test_paths_are_normalized_to_str(self):
        result = self.run_import(_parsed_line(), _report())
        self.assertIsInstance(result, ImportResult)
        self.assertIsInstance(result.db_path, str)
        self.assertIsInstance(result.workbook_path, str)
        self.assertEqual(WORKBOOK, result.workbook_path)

    def test_connection_is_released(self):
        self.run_import(_parsed_line("A"), _report())
        self.run_import(_parsed_line("B"), _report())
        self.assertEqual(["B"], [r["line_code"] for r in self.repo().list_by("line")])


class TestWiring(ImporterCase):
    def test_read_workbook_receives_path_and_site(self):
        self.run_import(_parsed_line(), _report())
        self.reader.assert_called_once_with(WORKBOOK, SITE)

    def test_validate_receives_tables_and_structure_log(self):
        log = IssueLog()
        tables = {"line": []}
        self.run_import(_parsed_line(), _report(), tables=tables, log=log)
        self.validator.assert_called_once_with(tables, log)

    def test_validate_is_not_called_after_a_read_failure(self):
        with patch("aps.data.importer.read_workbook", side_effect=OSError("no file")), \
                patch("aps.data.importer.validate") as validator:
            with self.assertRaises(OSError):
                import_workbook(WORKBOOK, self.db, SITE)
        validator.assert_not_called()


class TestDegradedFields(ImporterCase):
    def test_degraded_fields_come_from_gaps(self):
        result = self.run_import(
            _parsed_line(),
            _report(gaps=[_gap_issue("routing", "headcount"),
                          _gap_issue("routing", "per_shift_cap_declared")]),
        )
        self.assertEqual(
            frozenset({"routing.headcount", "routing.per_shift_cap_declared"}),
            result.degraded_fields,
        )

    def test_degraded_fields_ignore_gaps_without_entity_or_field(self):
        log = IssueLog()
        log.gap(entity=None, field=None, sheet=None, row=None, column=None, message="表级 gap")
        result = self.run_import(_parsed_line(), _report(gaps=log.issues))
        self.assertEqual(frozenset(), result.degraded_fields)

    def test_errors_and_warnings_are_not_degraded_fields(self):
        result = self.run_import(
            _parsed_line(),
            _report(warnings=[_warning_issue("line", "name")]),
        )
        self.assertEqual(frozenset(), result.degraded_fields)


class TestSummary(ImporterCase):
    def test_summary_reports_written_counts(self):
        result = self.run_import(_parsed_line(), _report())
        text = result.summary()
        self.assertIn(SITE, text)
        self.assertIn("line=1", text)
        self.assertIn("error=0", text)

    def test_summary_reports_abort(self):
        result = self.run_import(_parsed_line(), _report([_error_issue("line", "line_code")]))
        text = result.summary()
        self.assertIn("导入中止", text)
        self.assertIn("数据库未改动", text)
        self.assertIn("缺少必需列", text)

    def test_summary_handles_zero_rows(self):
        result = self.run_import({}, _report())
        self.assertIn("无数据行", result.summary())

    def test_summary_honours_limit(self):
        gaps = [_gap_issue("routing", f"f{i}") for i in range(5)]
        result = self.run_import(_parsed_line(), _report(gaps=gaps))
        self.assertNotIn("未显示", result.summary(limit=40))
        self.assertIn("另有 3 条未显示", result.summary(limit=2))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_importer -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'aps.data.importer'`

- [ ] **Step 3: 实现 `aps/data/importer.py`**

```python
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from aps.data.excel import read_workbook
from aps.data.repository import Row
from aps.data.sqlite import SqliteRepository
from aps.data.validate import ParsedTables, ValidationReport, validate

__all__ = ["ImportResult", "import_workbook", "rows_from_parsed"]


@dataclass(frozen=True)
class ImportResult:
    site: str
    db_path: str
    workbook_path: str
    report: ValidationReport
    counts: Mapping[str, int]
    wrote: bool

    @property
    def ok(self) -> bool:
        return self.report.ok

    @property
    def degraded_fields(self) -> frozenset[str]:
        return frozenset(
            f"{issue.entity}.{issue.field}"
            for issue in self.report.gaps
            if issue.entity and issue.field
        )

    def summary(self, limit: int = 40) -> str:
        counts = self.report.counts()
        head = f"站点 {self.site} · {os.path.basename(self.workbook_path)} → {self.db_path}"
        if not self.wrote:
            return (
                f"{head}\n导入中止：{counts['error']} 个 error，数据库未改动\n"
                + self.report.render(limit)
            )
        written = ", ".join(f"{name}={n}" for name, n in sorted(self.counts.items()) if n)
        return (
            f"{head}\n导入完成：{written or '（无数据行）'}\n"
            f"error={counts['error']}  warning={counts['warning']}  gap={counts['gap']}\n"
            + self.report.render(limit)
        )


def rows_from_parsed(parsed: ParsedTables) -> dict[str, list[Row]]:
    return {
        name: [candidate.as_row() for candidate in parsed[name]]
        for name in sorted(parsed)
    }


def import_workbook(workbook_path: str | os.PathLike[str],
                    db_path: str | os.PathLike[str], site: str) -> ImportResult:
    source = os.fspath(workbook_path)
    target = os.fspath(db_path)
    tables, structure_log = read_workbook(source, site)
    parsed, report = validate(tables, structure_log)
    if not report.ok:
        return ImportResult(site, target, source, report, {}, False)
    repo = SqliteRepository(target)
    try:
        repo.initialize_schema()
        counts = repo.replace_site(site, rows_from_parsed(parsed))
    finally:
        repo.close()
    return ImportResult(site, target, source, report, counts, True)
```

实现要点：

- `finally: repo.close()` 是必须的。Windows 上未关闭的 SQLite 连接会持有文件句柄，导致 `tempfile.TemporaryDirectory.cleanup()` 抛 `PermissionError`——测试 `test_connection_is_released` 与 `ImporterCase.repo()` 都依赖这一点。
- `rows_from_parsed` 用 `sorted(parsed)` 而非 `parsed.items()`，保证 `dict` 的插入序确定（G20）。`replace_site` 内部按拓扑序插入，所以这里的键序不影响正确性，只影响 `ImportResult.counts` 的可复现性。
- `CandidateRow.as_row()` 已经返回 `dict(self.values)`（拷贝），所以 `rows_from_parsed` 不需要再拷一次。`test_returns_a_copy_not_the_original` 验的就是这条。
- `summary()` 里 `sorted(self.counts.items())` 而不是 `self.counts.items()`——同样是 G20。
- 不要在 `import_workbook` 里 catch 任何异常。`read_workbook` 抛 `OSError`（文件不存在）就该让它冒到 CLI，由 CLI 转成退出码 2。`test_validate_is_not_called_after_a_read_failure` 钉住了这个行为。

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_importer -v`
Expected: PASS（全部）

若 `test_existing_data_survives_a_failing_reimport` 失败并抛 FK 相关异常，说明 Task 4 的 `replace_site` 仍在拓扑插入循环**之后**才建 `site` 行——回去按 Task 4 的实现改过来。

- [ ] **Step 5: 更新 `aps/data/__init__.py`**

在现有 re-export 基础上追加：

```python
from aps.data.importer import ImportResult, import_workbook, rows_from_parsed
```

并把 `"ImportResult"`, `"import_workbook"`, `"rows_from_parsed"` 加进 `__all__`。

- [ ] **Step 6: 确认没破坏前面的任务**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --new`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add aps/data/importer.py aps/data/__init__.py aps_tests/test_importer.py
git commit -m "feat(data): import_workbook 编排四道门与整站快照替换，error 时不碰数据库"
```

---

## Task 11: CLI（`template` / `import` / `verify-template` / `show` / `dataset`）

**Files:**
- Create: `aps/cli.py`
- Modify: `aps/__init__.py`（无需改动，仅确认存在）
- Test: `aps_tests/test_cli_import.py`

**Interfaces:**
- Consumes:
  ```python
  # aps/data/importer.py（Task 10）
  def import_workbook(workbook_path, db_path, site) -> ImportResult
  ImportResult.summary(limit=40) -> str ; .wrote ; .counts ; .degraded_fields ; .report
  # aps/data/template_v3.py（Task 9）
  def write_v3_template(out_path, site, demand_start=DEMAND_START, demand_days=DEMAND_DAYS) -> None
  DEMAND_START ; DEMAND_DAYS ; SHEET_ORDER ; INSTRUCTIONS_SHEET
  # aps/data/sqlite.py（Task 4）
  SqliteRepository(db_path) ; .list_by(entity, **filters) ; .count(entity, **filters) ; .close()
  # aps/schema/registry.py（Task 3）
  REGISTRY ; def get_entity(name) -> EntitySpec   # 未知名字抛 KeyError
  EntitySpec.fields
  # aps/dataset.py（Task 8）
  DataSet.load(repo, site, start_date, days, calendar_mode="auto", thresholds=...,
               overrides=None, log=None, degraded_fields=frozenset()) -> DataSet
  DataSet.horizon / .mode / .lines / .slots / .tasks / .routes / .bom / .degraded_fields
  DataSet.open_slots(line_code) ; Horizon.start_date / .end_date / .days
  # aps/data/issues.py（Task 5）
  IssueLog()
  # aps/calendar/pattern.py（Task 7）
  CalendarMode  # 值："CONTINUOUS" / "6ON1OFF" / "5ON2OFF" / "4ON3OFF"
  ```
- Produces:
  ```python
  EXIT_OK = 0 ; EXIT_VALIDATION = 1 ; EXIT_USAGE = 2
  DEFAULT_DB = "data/aps.db" ; DEFAULT_SITE = "WL"
  def build_parser() -> argparse.ArgumentParser
  def main(argv: Sequence[str] | None = None) -> int
  ```

**设计裁决（照做，不要改）**：

1. **退出码三档**：`0` 成功；`1` 校验门有 error（数据问题，数据库未改动）；`2` 用法错误（文件不存在、未知实体、缺参数）。`1` 与 `2` 必须分开——CI 里「数据不干净」和「命令敲错了」是两种完全不同的处置。
2. **`verify-template` 用一次性临时库，绝不写 `--db`。** 空模板导入会触发 `replace_site`，把该站点的数据全删掉再插入零行。若它写用户的 `data/aps.db`，一次「验证模板」就会清空整站数据。所以 `verify-template` **不接受 `--db` 参数**，内部用 `tempfile.TemporaryDirectory()`。这是安全属性，测试 `test_verify_template_does_not_touch_default_db` 钉住它。
3. **`main()` 返回 int，不调用 `sys.exit`。** 只有 `if __name__ == "__main__"` 那一行把它转成进程退出码。这样测试能直接调 `main([...])` 而不必捕获 `SystemExit`。
4. **`argparse` 的用法错误由 argparse 自己抛 `SystemExit(2)`，`main` 不接管。** 测试用 `assertRaises(SystemExit)`。业务错误（文件不存在、未知实体）由 handler 打印到 stderr 并 `return EXIT_USAGE`，不抛异常。
5. **所有输出走 `print`（stdout），错误走 `print(..., file=sys.stderr)`。** 测试用 `contextlib.redirect_stdout(io.StringIO())` 捕获。不要引入 logging 配置——Stage A 的 CLI 是一次性管道工具，不是服务。
6. **`show` 不带 `--entity` 时打印每实体行数，带 `--entity` 时打印 JSON 行。** JSON 用 `ensure_ascii=False, default=str`（`date` / `time` 不是 JSON 原生类型）。

- [ ] **Step 1: 写失败测试**

创建 `aps_tests/test_cli_import.py`：

```python
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import unittest

from aps.cli import (
    DEFAULT_DB,
    DEFAULT_SITE,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VALIDATION,
    build_parser,
    main,
)
from aps.data.template_v3 import INSTRUCTIONS_SHEET, SHEET_ORDER
from aps.schema.registry import REGISTRY


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.template = os.path.join(self._tmp.name, "v3.xlsx")
        self.db = os.path.join(self._tmp.name, "aps.db")
        code, self.gen_out, _ = run(
            ["template", "--out", self.template, "--site", DEFAULT_SITE]
        )
        self.assertEqual(EXIT_OK, code)

    def import_template(self, db=None):
        return run(["import", "--workbook", self.template,
                    "--db", db or self.db, "--site", DEFAULT_SITE])


class TestParser(unittest.TestCase):
    def test_subcommands_are_registered(self):
        parser = build_parser()
        choices = parser._subparsers._group_actions[0].choices
        self.assertEqual(
            {"template", "import", "verify-template", "show", "dataset"}, set(choices)
        )

    def test_missing_command_exits_2(self):
        with self.assertRaises(SystemExit) as caught:
            with contextlib.redirect_stderr(io.StringIO()):
                main([])
        self.assertEqual(2, caught.exception.code)

    def test_unknown_command_exits_2(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                main(["nope"])

    def test_import_requires_workbook(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                main(["import"])

    def test_defaults(self):
        args = build_parser().parse_args(["import", "--workbook", "w.xlsx"])
        self.assertEqual(DEFAULT_DB, args.db)
        self.assertEqual(DEFAULT_SITE, args.site)
        self.assertEqual(40, args.limit)

    def test_demand_start_is_parsed_to_date(self):
        args = build_parser().parse_args(
            ["template", "--out", "o.xlsx", "--demand-start", "2027-05-01"]
        )
        self.assertEqual(dt.date(2027, 5, 1), args.demand_start)


class TestTemplateCommand(CliCase):
    def test_writes_a_workbook_with_every_sheet(self):
        from openpyxl import load_workbook

        wb = load_workbook(self.template)
        self.addCleanup(wb.close)
        self.assertEqual([INSTRUCTIONS_SHEET, *SHEET_ORDER], wb.sheetnames)

    def test_stdout_names_the_output_file(self):
        self.assertIn(self.template, self.gen_out)

    def test_honours_demand_window(self):
        path = os.path.join(self._tmp.name, "short.xlsx")
        code, _, _ = run(["template", "--out", path, "--site", "WL",
                          "--demand-start", "2027-01-01", "--demand-days", "3"])
        self.assertEqual(EXIT_OK, code)
        from openpyxl import load_workbook

        wb = load_workbook(path)
        self.addCleanup(wb.close)
        ws = wb["客户需求订单清单 Customer Demand"]
        self.assertEqual(dt.datetime(2027, 1, 1), ws.cell(1, 5).value)
        self.assertEqual(dt.datetime(2027, 1, 3), ws.cell(1, 7).value)
        self.assertIsNone(ws.cell(1, 8).value)

    def test_missing_out_argument_exits_2(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                main(["template", "--site", "WL"])


class TestImportCommand(CliCase):
    def test_empty_template_imports_ok(self):
        code, out, _ = self.import_template()
        self.assertEqual(EXIT_OK, code)
        self.assertIn("导入完成", out)

    def test_site_and_calendar_pattern_are_seeded(self):
        self.import_template()
        code, out, _ = run(["show", "--db", self.db, "--site", DEFAULT_SITE])
        self.assertEqual(EXIT_OK, code)
        self.assertIn("site", out)
        counts = dict(
            (line.split()[0], int(line.split()[1]))
            for line in out.strip().splitlines()
            if line.strip() and not line.startswith("共")
        )
        self.assertEqual(1, counts["site"])
        self.assertEqual(4, counts["calendar_pattern"])
        self.assertEqual(0, counts["routing"])

    def test_missing_workbook_returns_2(self):
        code, _, err = run(["import", "--workbook", os.path.join(self._tmp.name, "ghost.xlsx"),
                            "--db", self.db, "--site", "WL"])
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("ghost.xlsx", err)
        self.assertFalse(os.path.exists(self.db))

    def test_report_limit_is_forwarded(self):
        code, out, _ = run(["import", "--workbook", self.template,
                            "--db", self.db, "--site", "WL", "--limit", "1"])
        self.assertEqual(EXIT_OK, code)
        self.assertIn("error=0", out)


class TestVerifyTemplateCommand(unittest.TestCase):
    def test_returns_zero_and_reports_the_generated_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "v3.xlsx")
            code, out, _ = run(["verify-template", "--out", out_path, "--site", "WL"])
            self.assertEqual(EXIT_OK, code)
            self.assertIn(out_path, out)
            self.assertIn("导入完成", out)
            self.assertTrue(os.path.exists(out_path))

    def test_verify_template_does_not_touch_default_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                run(["verify-template", "--out", os.path.join(tmp, "v3.xlsx"), "--site", "WL"])
                self.assertFalse(os.path.exists(DEFAULT_DB))
            finally:
                os.chdir(cwd)

    def test_verify_template_accepts_no_db_argument(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                main(["verify-template", "--out", "v3.xlsx", "--db", "x.db"])


class TestShowCommand(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.import_template()

    def test_counts_lists_every_registered_entity(self):
        code, out, _ = run(["show", "--db", self.db, "--site", DEFAULT_SITE])
        self.assertEqual(EXIT_OK, code)
        for name in REGISTRY:
            self.assertIn(name, out)

    def test_entity_rows_are_json(self):
        code, out, _ = run(["show", "--db", self.db, "--site", DEFAULT_SITE,
                            "--entity", "calendar_pattern"])
        self.assertEqual(EXIT_OK, code)
        rows = json.loads(out)
        self.assertEqual(4, len(rows))
        self.assertEqual(
            ["4ON3OFF", "5ON2OFF", "6ON1OFF", "CONTINUOUS"],
            sorted(row["pattern_code"] for row in rows),
        )

    def test_entity_without_site_column_is_still_listable(self):
        code, out, _ = run(["show", "--db", self.db, "--site", DEFAULT_SITE,
                            "--entity", "calendar_pattern", "--limit", "2"])
        self.assertEqual(EXIT_OK, code)
        self.assertEqual(2, len(json.loads(out)))

    def test_unknown_entity_returns_2(self):
        code, _, err = run(["show", "--db", self.db, "--site", DEFAULT_SITE,
                            "--entity", "ghost"])
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("ghost", err)

    def test_missing_db_returns_2(self):
        code, _, err = run(["show", "--db", os.path.join(self._tmp.name, "ghost.db"),
                            "--site", DEFAULT_SITE])
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("先运行 import", err)


class TestDatasetCommand(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.import_template()

    def test_empty_dataset_still_loads(self):
        code, out, _ = run(["dataset", "--db", self.db, "--site", DEFAULT_SITE,
                            "--start", "2026-11-03", "--days", "10"])
        self.assertEqual(EXIT_OK, code)
        self.assertIn("2026-11-03", out)
        self.assertIn("2026-11-12", out)
        self.assertIn("4ON3OFF", out)

    def test_zero_lines_yield_zero_slots(self):
        code, out, _ = run(["dataset", "--db", self.db, "--site", DEFAULT_SITE,
                            "--start", "2026-11-03", "--days", "10"])
        self.assertEqual(EXIT_OK, code)
        self.assertIn("产线 0", out)
        self.assertIn("槽位 0", out)

    def test_explicit_calendar_mode_is_honoured(self):
        code, out, _ = run(["dataset", "--db", self.db, "--site", DEFAULT_SITE,
                            "--start", "2026-11-03", "--days", "10",
                            "--calendar-mode", "CONTINUOUS"])
        self.assertEqual(EXIT_OK, code)
        self.assertIn("CONTINUOUS", out)

    def test_unknown_calendar_mode_returns_2(self):
        code, _, err = run(["dataset", "--db", self.db, "--site", DEFAULT_SITE,
                            "--start", "2026-11-03", "--days", "10",
                            "--calendar-mode", "NOPE"])
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("NOPE", err)

    def test_start_and_days_are_required(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                main(["dataset", "--db", self.db, "--site", DEFAULT_SITE])

    def test_missing_db_returns_2(self):
        code, _, err = run(["dataset", "--db", os.path.join(self._tmp.name, "ghost.db"),
                            "--site", DEFAULT_SITE, "--start", "2026-11-03", "--days", "3"])
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("先运行 import", err)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_cli_import -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'aps.cli'`

- [ ] **Step 3: 实现 `aps/cli.py`**

```python
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from collections.abc import Sequence

from aps.calendar.pattern import CalendarMode
from aps.data.importer import import_workbook
from aps.data.issues import IssueLog
from aps.data.sqlite import SqliteRepository
from aps.data.template_v3 import DEMAND_DAYS, DEMAND_START, write_v3_template
from aps.dataset import DataSet
from aps.schema.registry import REGISTRY, EntitySpec, get_entity

__all__ = [
    "DEFAULT_DB",
    "DEFAULT_SITE",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_VALIDATION",
    "build_parser",
    "main",
]

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_USAGE = 2

DEFAULT_DB = os.path.join("data", "aps.db")
DEFAULT_SITE = "WL"


def _has_site(spec: EntitySpec) -> bool:
    return any(field.name == "site_code" for field in spec.fields)


def _filters(spec: EntitySpec, site: str) -> dict[str, str]:
    return {"site_code": site} if _has_site(spec) else {}


def _require_db(path: str) -> int | None:
    if os.path.exists(path):
        return None
    print(f"数据库不存在：{path}（先运行 import）", file=sys.stderr)
    return EXIT_USAGE


def _cmd_template(args: argparse.Namespace) -> int:
    write_v3_template(args.out, args.site, args.demand_start, args.demand_days)
    print(f"已生成 V3 空白模板：{args.out}")
    print(f"需求日期轴：{args.demand_start.isoformat()} 起 {args.demand_days} 天")
    return EXIT_OK


def _cmd_import(args: argparse.Namespace) -> int:
    try:
        result = import_workbook(args.workbook, args.db, args.site)
    except OSError as exc:
        print(f"无法读取工作簿 {args.workbook}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(result.summary(args.limit))
    return EXIT_OK if result.wrote else EXIT_VALIDATION


def _cmd_verify_template(args: argparse.Namespace) -> int:
    write_v3_template(args.out, args.site, args.demand_start, args.demand_days)
    print(f"已生成 V3 空白模板：{args.out}")
    with tempfile.TemporaryDirectory() as tmp:
        result = import_workbook(args.out, os.path.join(tmp, "verify.db"), args.site)
    print(result.summary(args.limit))
    if result.wrote:
        print("V3 模板自校验通过：结构门零 error，登记表与适配器一致。")
    return EXIT_OK if result.wrote else EXIT_VALIDATION


def _cmd_show(args: argparse.Namespace) -> int:
    blocked = _require_db(args.db)
    if blocked is not None:
        return blocked
    repo = SqliteRepository(args.db)
    try:
        if args.entity is None:
            total = 0
            for name in sorted(REGISTRY):
                count = repo.count(name, **_filters(REGISTRY[name], args.site))
                total += count
                print(f"{name:24s} {count:6d}")
            print(f"共 {len(REGISTRY)} 个实体，{total} 行（站点 {args.site}）")
            return EXIT_OK
        try:
            spec = get_entity(args.entity)
        except KeyError:
            print(f"登记表无实体 {args.entity!r}；可用：{', '.join(sorted(REGISTRY))}",
                  file=sys.stderr)
            return EXIT_USAGE
        rows = repo.list_by(args.entity, **_filters(spec, args.site))[:args.limit]
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return EXIT_OK
    finally:
        repo.close()


def _cmd_dataset(args: argparse.Namespace) -> int:
    blocked = _require_db(args.db)
    if blocked is not None:
        return blocked
    try:
        mode = CalendarMode(args.calendar_mode)
    except ValueError:
        print(f"未知工作制 {args.calendar_mode!r}；可用："
              f"{', '.join(sorted(m.value for m in CalendarMode))}", file=sys.stderr)
        return EXIT_USAGE
    repo = SqliteRepository(args.db)
    try:
        log = IssueLog()
        dataset = DataSet.load(repo, args.site, args.start, args.days,
                               calendar_mode=mode.value, log=log)
    finally:
        repo.close()
    horizon = dataset.horizon
    print(f"站点 {horizon.site}  窗口 {horizon.start_date.isoformat()} ~ "
          f"{horizon.end_date.isoformat()}（{horizon.days} 天）  工作制 {dataset.mode.value}")
    print(f"产线 {len(dataset.lines)}  槽位 {len(dataset.slots)}  "
          f"开放槽位 {sum(len(dataset.open_slots(line.line_code)) for line in dataset.lines)}")
    print(f"任务 {len(dataset.tasks)}  路线 {len(dataset.routes)}  BOM {len(dataset.bom)}")
    print(f"degraded 字段 {len(dataset.degraded_fields)}："
          f"{', '.join(sorted(dataset.degraded_fields)) or '（无）'}")
    for issue in log.issues:
        print(f"[{issue.severity.value.upper()}] {issue.location()} {issue.message}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aps", description="APS 数据管道 CLI（Stage A：Excel → SQLite → DataSet）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    template = sub.add_parser("template", help="生成空白 V3 数据源模板")
    template.add_argument("--out", required=True, help="输出 .xlsx 路径")
    template.add_argument("--site", default=DEFAULT_SITE)
    template.add_argument("--demand-start", type=dt.date.fromisoformat, default=DEMAND_START,
                          help="需求日期轴首日（yyyy-mm-dd）")
    template.add_argument("--demand-days", type=int, default=DEMAND_DAYS)
    template.set_defaults(handler=_cmd_template)

    verify = sub.add_parser(
        "verify-template",
        help="生成 V3 模板并导入一次性临时库，验证登记表与适配器一致（不碰 --db）",
    )
    verify.add_argument("--out", required=True)
    verify.add_argument("--site", default=DEFAULT_SITE)
    verify.add_argument("--demand-start", type=dt.date.fromisoformat, default=DEMAND_START)
    verify.add_argument("--demand-days", type=int, default=DEMAND_DAYS)
    verify.add_argument("--limit", type=int, default=40)
    verify.set_defaults(handler=_cmd_verify_template)

    imp = sub.add_parser("import", help="导入 Excel 工作簿（整站快照替换）")
    imp.add_argument("--workbook", required=True)
    imp.add_argument("--db", default=DEFAULT_DB)
    imp.add_argument("--site", default=DEFAULT_SITE)
    imp.add_argument("--limit", type=int, default=40, help="报告中每级最多显示多少条")
    imp.set_defaults(handler=_cmd_import)

    show = sub.add_parser("show", help="查看库中数据")
    show.add_argument("--db", default=DEFAULT_DB)
    show.add_argument("--site", default=DEFAULT_SITE)
    show.add_argument("--entity", default=None, help="实体名；缺省则打印每实体行数")
    show.add_argument("--limit", type=int, default=20)
    show.set_defaults(handler=_cmd_show)

    dataset = sub.add_parser("dataset", help="从库中装载 DataSet 并打印冻结后的派生量")
    dataset.add_argument("--db", default=DEFAULT_DB)
    dataset.add_argument("--site", default=DEFAULT_SITE)
    dataset.add_argument("--start", required=True, type=dt.date.fromisoformat)
    dataset.add_argument("--days", required=True, type=int)
    dataset.add_argument("--calendar-mode", default="CONTINUOUS")
    dataset.set_defaults(handler=_cmd_dataset)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

实现要点：

- `--start` 是必填的 `dt.date`，但 `DataSet.load` 的形参名是 `start_date`。`_cmd_dataset` 传的是位置参数，所以名字不冲突——**不要**写成 `start=args.start`。
- `CalendarMode(args.calendar_mode)` 对 `"auto"` 会抛 `ValueError`。这是有意的：`dataset` 子命令要打印**确定的**工作制，`auto` 在这里没有意义（`DataSet.load` 内部的 `auto` 分支是给排产用的）。所以 `--calendar-mode` 的缺省值虽然是 `"auto"`，但只有显式传四档之一才能通过。测试 `test_empty_dataset_still_loads` 没传 `--calendar-mode`，因此它期望的是 `EXIT_USAGE`……

  **更正**：把 `--calendar-mode` 的缺省值改为 `"CONTINUOUS"`，并删掉上面这段推理。`dataset` 子命令永远打印一个确定的工作制；要看负载率推荐，用 `DataSet.load(calendar_mode="auto")` 写测试，不用 CLI。改完后 `test_empty_dataset_still_loads` 期望的输出里是 `CONTINUOUS` 而不是 `4ON3OFF`——**同步把该测试的 `assertIn("4ON3OFF", out)` 改成 `assertIn("CONTINUOUS", out)`**。
- `import_workbook` 只可能抛 `OSError`（文件不存在 / 无读权限）。openpyxl 对损坏文件抛的是 `zipfile.BadZipFile`（`OSError` 的子类）或 `KeyError`；`KeyError` 不在捕获范围内，会冒到 `main` 之外变成 traceback。这是可接受的——损坏的工作簿是异常情况，不是数据问题，traceback 比一行错误消息更有用。
- `_require_db` 返回 `int | None` 而不是抛异常，因为 handler 的契约是「返回退出码」。
- `show` 的计数输出格式是 `f"{name:24s} {count:6d}"`，测试 `test_site_and_calendar_pattern_are_seeded` 靠 `line.split()` 解析它。改格式就要同步改测试。
- `dataset` 子命令末尾把 `log.issues` 全部打印，包括 warning 与 gap。空库时这里会打印 `工作日历` 相关的 warn（V2 无该表）——那是正确行为，不要过滤。

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest aps_tests.test_cli_import -v`
Expected: PASS（全部）

- [ ] **Step 5: 手工跑一遍完整管道**

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m aps.cli verify-template --out /tmp/v3.xlsx --site WL
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m aps.cli import --workbook "APS_DataSource_Template - WL -V2.xlsx" --db data/aps.db --site WL
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m aps.cli show --db data/aps.db --site WL
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m aps.cli show --db data/aps.db --site WL --entity line --limit 5
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m aps.cli dataset --db data/aps.db --site WL --start 2026-11-03 --days 10
```

核对：`import` 退出码 0；`show` 打印 23 个实体；`line` 有 17 行；`dataset` 打印的产线数是 17、槽位数是 17 × 10 × 3 = 510（`CONTINUOUS` 无休息日）。

`data/aps.db` 与 `/tmp/v3.xlsx` 都是产物，**不要提交**。跑完删掉：

```bash
rm -f data/aps.db
```

- [ ] **Step 6: 确认没破坏前面的任务**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --new`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add aps/cli.py aps_tests/test_cli_import.py
git commit -m "feat(cli): template/import/verify-template/show/dataset 五个子命令"
```

---

# Stage B：OR-Tools CP-SAT 核心求解器与排产输出

## Task 12: Stage B 求解上下文与决策变量族

**Files:**
- Create: `aps/model/__init__.py`
- Create: `aps/model/context.py`
- Create: `aps_tests/test_model_context.py`

**Interfaces:**
- Consumes: `aps.dataset.DataSet`, `aps.dataset.Task`, `aps.dataset.RouteOption`, `aps.calendar.geometry.Slot`
- Produces:
  ```python
  from ortools.sat.python import cp_model

  class StructureContext:
      """持有 CP-SAT 模型与完整时间轴决策变量族的强类型上下文。"""
      dataset: DataSet
      model: cp_model.CpModel
      horizon_days: int
      
      # 决策变量映射字典
      assign: dict[tuple[str, str], cp_model.IntVar]            # (task_id, line_code) -> Bool
      x: dict[tuple[str, str, int], cp_model.IntVar]                 # (task_id, line_code, slot_idx) -> Int
      active: dict[tuple[str, str, int], cp_model.IntVar]            # (task_id, line_code, slot_idx) -> Bool
      started: dict[tuple[str, str, int], cp_model.IntVar]           # (task_id, line_code, slot_idx) -> Bool
      cum: dict[tuple[str, str, int], cp_model.IntVar]               # (task_id, line_code, slot_idx) -> Int
      end_slot: dict[str, cp_model.IntVar]                          # task_id -> Int
      late_shifts: dict[str, cp_model.IntVar]                       # task_id -> Int >= 0
      load_ms: dict[tuple[str, int], cp_model.IntVar]               # (line_code, slot_idx) -> Int ms
      is_open: dict[tuple[str, int], cp_model.IntVar]               # (line_code, slot_idx) -> Bool
      is_tail: dict[tuple[str, int], cp_model.IntVar]               # (line_code, slot_idx) -> Bool
      block_start: dict[tuple[str, str, int], cp_model.IntVar]      # (task_id, line_code, slot_idx) -> Bool
      
      # Assumption Literals 映射（用于 Infeasible 诊断）
      assumptions: dict[str, cp_model.IntVar]                       # literal_name -> BoolVar
  ```

**实现要点：**
- 变量域精确收紧：`x` 的上限为 `per_shift_cap`，`load_ms` 的上限为 `slot_capacity_ms * 1.5`（允许过载惩罚但防溢出）。
- 耦合关系：`active` 与 `x` 的等价性绑定 `x > 0 <=> active`；`started` 的前阶差分约束 `started[s] >= active[s] - active[s-1]`。

- [ ] **Step 1: 写单元测试 `aps_tests/test_model_context.py`**
- [ ] **Step 2: 实现 `aps/model/context.py`**
- [ ] **Step 3: 运行验证**
- [ ] **Step 4: 提交**

---

## Task 13: 九条核心硬约束与 Assumption Literals

**Files:**
- Create: `aps/model/rules.py`
- Create: `aps_tests/test_model_rules.py`

**Interfaces:**
- Consumes: `aps.model.context.StructureContext`
- Produces: `build_core_constraints(ctx: StructureContext, allow_soft_utilization: bool = True) -> list[str]`

**约束实现清单：**
1. **认证产线唯一分配**：对每个 `task`，$\sum_{l \in certified} assign[t, l] == 1$。
2. **分配与产出一致性**：$x[t, l, s] \le per\_shift\_cap(t, l) \times assign[t, l]$。
3. **净需求严格守恒**：$\sum_{l, s} x[t, l, s] == task.net\_requirement$。
4. **产线班次工时容量**：$load\_ms[l, s] == \sum_t unit\_ms(t, l) \times x[t, l, s] \le slot\_capacity\_ms(l, s) \times is\_open[l, s]$。
5. **成品（FG）MPQ 整数倍取整**：$x[t, l, s] == task.mpq \times q[t, l, s]$。
6. **半成品（SF）MPQ 启动门槛**：$\sum_s x[t, l, s] \ge task.mpq$ 或 $== 0$（单班产出可为任意正整数）。
7. **跨阶段工序搭接流水平衡（Q5）**：
   $$\sum_{s' \le s-1} x[sf, l_{sf}, s'] \times ratio + stock(sf) \ge \sum_{s'' \le s} x[fg, l_{fg}, s'']$$
   支持可选 `start_gate="first_shift"` 模式。
8. **交期与延误班次定义**：$late\_shifts[t] \ge end\_slot[t] - due\_index(task.due\_date, l)$。
9. **95% 开班利用率下限带末班豁免（Q6）**：
   $$load\_ms[l, s] \ge 0.95 \times slot\_capacity\_ms(l, s) \times is\_open[l, s] - M \times is\_tail[l, s]$$
   若 `allow_soft_utilization=True`，下限缺口转化为松弛惩罚变量计入 P2 目标。

- [ ] **Step 1: 写最小反例测试 `aps_tests/test_model_rules.py`**
- [ ] **Step 2: 实现 `aps/model/rules.py` 并对每条约束绑定 assumption literal**
- [ ] **Step 3: 运行确认反例均被硬约束拒绝**
- [ ] **Step 4: 提交**

---

## Task 14: 四层字典序求解器与 Infeasible 诊断

**Files:**
- Create: `aps/model/solver.py`
- Create: `aps_tests/test_model_solver.py`

**Interfaces:**
- Consumes: `aps.model.context.StructureContext`, `aps.model.rules.build_core_constraints`
- Produces:
  ```python
  @dataclass
  class SolveSummary:
      status: str                             # OPTIMAL | FEASIBLE | INFEASIBLE
      wall_time_seconds: float
      deterministic_time: float
      p1_lateness_shifts: int
      p2_utilization_gap_ms: int
      p3_changeover_blocks: int
      p4_wip_units: int
      diagnostics: list[str]                  # Infeasible 时的最小冲突集人话解释

  def solve_schedule(ctx: StructureContext,
                     time_limit_seconds: float = 60.0,
                     allow_soft_utilization: bool = True) -> tuple[cp_model.CpSolver | None, SolveSummary]:
      ...
  ```

**算法执行流程：**
1. 构建 CP-SAT 模型并注入九条核心约束与 assumption literals。
2. **Layer 1 (P1 - 交付优先)**：$Obj_1 = \sum_t late\_shifts[t] \times weight$。求解并冻结界限 $Obj_1 \le val_1$。
3. **Layer 2 (P2 - 产能合规)**：$Obj_2 = \sum_{l, s} under\_util[l, s] + overload[l, s]$。求解并冻结界限 $Obj_2 \le val_2$。
4. **Layer 3 (P3 - 换型平滑)**：$Obj_3 = \sum_{t, l, s} block\_start[t, l, s]$。求解并冻结界限 $Obj_3 \le val_3$。
5. **Layer 4 (P4 - 在制品控制)**：拉齐库存并求解。
6. 若任意层出现 `INFEASIBLE`：
   调用 `solver.SufficientAssumptionsForInfeasibility()`，通过 `ctx.assumptions` 反查冲突约束和料号/产线，生成诊断报告。

- [ ] **Step 1: 写单元测试 `aps_tests/test_model_solver.py`（含假数据字典序断言与人工 infeasible 诊断）**
- [ ] **Step 2: 实现 `aps/model/solver.py`**
- [ ] **Step 3: 运行验证**
- [ ] **Step 4: 提交**

---

## Task 15: 调度结果抽象 `PlanRow` DTO 与规则绑定报告

**Files:**
- Create: `aps/model/plan_row.py`
- Create: `aps/model/report.py`
- Create: `aps_tests/test_plan_row.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class PlanRow:
      site: str
      line_code: str
      date_str: str                           # "YYYY-MM-DD"
      shift_name: str                         # "早班" | "中班" | "晚班"
      slot_index: int
      task_id: str
      pn: str
      stage: str                              # "FG" | "SF"
      quantity: int
      start_min: float
      end_min: float
      setup_min: float
      is_tail_shift: bool

  @dataclass(frozen=True)
  class BindingReport:
      rule_id: str
      status: str                             # "COMPILED" | "DEGRADED" | "SKIPPED"
      constraints_count: int
      penalty_terms_count: int
      provenance: str
  ```

- [ ] **Step 1: 写单测 `aps_tests/test_plan_row.py`**
- [ ] **Step 2: 实现解到 `PlanRow` 列表的抽取转换器与绑定报告生成器**
- [ ] **Step 3: 提交**

---

## Task 16: 排产计划 Excel 导出器

**Files:**
- Create: `aps/export/__init__.py`
- Create: `aps/export/excel.py`
- Create: `aps_tests/test_export_excel.py`

**输出 Workbook 结构：**
- Sheet 1 `生产计划明细 Plan Details`：产线、日期、班次、料号、品名描述、阶段、排产件数、工序起止分钟、换型时间。
- Sheet 2 `产线班次负荷 Line Shifts`：产线、日期、班次、开班状态、额定负荷(h)、实际负荷(h)、利用率%、末班标识。
- Sheet 3 `订单交付汇总 Order Delivery`：料号、需求件数、实排件数、需求交期、完工日期班次、延误状态。
- Sheet 4 `求解报告 Solver Report`：字典序各层最优值、求解耗时、规则编译生效清单。

- [ ] **Step 1: 写导出格式与内容断言测试 `aps_tests/test_export_excel.py`**
- [ ] **Step 2: 基于 `openpyxl` 实现 `export_production_plan(rows, shifts, summary, out_path)`**
- [ ] **Step 3: 运行验证**
- [ ] **Step 4: 提交**

---

## Task 17: CLI 端到端 `aps solve` 命令接入

**Files:**
- Modify: `aps/cli.py`
- Create: `aps_tests/test_cli_solve.py`

**CLI 用法：**
```bash
python -m aps.cli solve \
    --workbook "APS_DataSource_Template - WL -V2.xlsx" \
    --db data/aps.db \
    --site WL \
    --start 2026-11-03 \
    --days 10 \
    --out data/Production_Plan_WL.xlsx \
    --timeout 60
```

**执行流程：**
1. 若 `--db` 不存在或指定 `--reload`，自动调用 `import_workbook` 入库并执行四道校验门。
2. `DataSet.load()` 执行 MRP BOM 展开生成 FG+SF 任务。
3. `solve_schedule()` 执行 CP-SAT 四层字典序排产。
4. 打印求解总结与关键指标到终端。
5. 自动导出 Excel 到 `--out` 路径。

- [ ] **Step 1: 写 CLI solve 端到端集成测试 `aps_tests/test_cli_solve.py`**
- [ ] **Step 2: 在 `aps/cli.py` 中注册 `solve` 子命令与参数解析**
- [ ] **Step 3: 跑通 `APS_DataSource_Template - WL -V2.xlsx` 10 天端到端排产**
- [ ] **Step 4: 提交**

---

## Task 18: 阶段验收与 30 天规模压测

**验收门（L2 求解器门禁）：**
1. **10 天标准基线验收**：在 10 天窗口（2026-11-03 ~ 2026-11-12）内，`solve` 命令在 30 秒内达到 `OPTIMAL` 或 `FEASIBLE`；0 订单延误；产线开班后利用率达到 95%（末班除外）；FG 与 SF 班次物料平衡无断供。
2. **30 天全周期压测**：使用 `--days 30` 运行，验证 CP-SAT 变量数达到数万级时，求解能在 120 秒内稳定给出可行排产解，内存无泄漏。
3. **全套自动化测试通过**：`PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py` 全部绿色。

- [ ] **Step 1: 运行 10 天与 30 天全量排产并记录性能指标**
- [ ] **Step 2: 确认 git 工作区无临时垃圾文件**
- [ ] **Step 3: 最终阶段提交**

