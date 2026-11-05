# Stage 1 双轨收敛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 9 个仍在 legacy/kernel 双轨并存的结构模板逐个收敛为「规则集唯一实现」，并把业务新规则 M-01（FG 必须等 SF 确定能满足后才开工）与部分主数据的显式降级行为落进 kernel `material_balance`。

**Architecture:** `APSScheduler` 永远持有一个规则集（调用方不传时用 `src/kernel/baseline.py::baseline_rule_set`）；每个模板收敛后加入 `CONVERGED_TEMPLATES`，该模板的 legacy 分支与 `enforce_*` 开关在同一个提交里删除。`material_balance` 由逐父任务 pairwise 改为料号级、时间相位的 REG-01 账本，并新增 `start_gate=first_shift`（整批齐套才允许 FG 首次开工）。最终删除 `force_hard`，契约检查 #10 从 skip 转正式。

**Tech Stack:** Python 3.12（`.venv`）、OR-Tools CP-SAT、unittest、SQLite 规则集版本库、Excel/CSV 双层主数据。

**Spec:** `Specification/aps_rule_set_redesign.md`（§6.4、§7.1、§8.1、§8.3、附录 B HC-06、附录 D；本计划所有 `file:line` 于 2026-11-05 在 `ca9f0c2` 上重新核实）

---

## Global Constraints

以下约束来自规格与用户指令，每个任务默认全部适用：

- **禁止推送远端。** 用户明确要求「不要同步到远程分支」。本计划所有提交只留在本地 `feat/shaojing-dev`；`git push` 一律不做，直到用户显式解除。
- **验证命令固定**（PowerShell 控制台用 Git Bash 执行）：
  - 快测：`PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast`
  - 全量：`PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py`
  - 黄金基线：`PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_golden_baseline -v`
  - 公式测试跑完后 `git status` 必须干净；`data/snapshot_WL.json` 若被改写，用 `git checkout -- data/snapshot_WL.json` 还原（规格 8.0）。
- **不新增依赖。** `requirements.txt` 之外的东西一律不用（规格 D.2）。
- **代码注释、docstring、标识符用英文；文档、UI 文案、规格用中文**（规格 D.2）。
- **禁止新增 `print`**（规格 D.3 #2）。已有 `print` 的告警改为 `rule_warnings` 列表；其余既有 `print` 不动。
- **禁止修改第 3 层契约**：`RequirementPlan` 与 `ProductionInputSnapshot` 的字段和 7 个消费点签名冻结（规格 D.3 #3）。需要新增状态时加在 `ProductionTask`（带默认值）或调度器/结果对象上，不动这两个类。
- **规则项只经 `PenaltyBook` 进入目标函数**（规格 D12 / D.3 #5）。既有编排项（lateness、overloads、weekend 等）保持原样。
- **确定性**：不引入壁钟依赖；tier 预算用 `max_deterministic_time`/显式 budget；`as_of_date` 显式传入（规格 D.3 #6）。
- **新测试必须加入 `run_tests.py --fast` 的模块列表**（规格 D.3 #8）。
- **一个模板一个收敛提交**；阶段 1 的 7 个模板必须是 7 个提交（规格 D.5 #4）。准备性提交（只加测试/只加可复用代码、不删 legacy）可以与收敛提交分开发，但同一个模板不得拆到两个收敛提交里。
- **黄金基线变更必须书面说明**：哪个缺陷修复、预期方向、实际结果（规格 8.1 / 1.7）。
- **规格与现实不符时按 D.4 处理**：行号漂移按符号继续；符号消失或语义矛盾则停下报告，不自行修正规格。
- **完成定义**（规格 D.5）：验收条目逐条跑过、fast 退出码 0、`git status` 干净、无 TODO/注释掉的代码/空实现。

---

## 裁决登记（Decision Register）

计划无法回避、但规格 D1–D15 未覆盖的决定。每条都要在对应提交信息中重申一次。

| 编号 | 裁决 | 理由 | 若判断错误的代价 |
|---|---|---|---|
| **R1** | 验收 1.2 由「18 个 `enforce_*` 归零」**收窄**为：阶段 1 的 9 个开关归零；其余 13 个登记进 `tests/fixtures/stage1_switch_register.json` 并加源码扫描测试防止未登记新增。 | 实际有 22 个 `enforce_*` 构造参数，其中 13 个（freeze/jitter/labor/sc08/campaign_batching/slack/d1×2/pull/customer/weekend/campaign/lead_time）不属于这 7 个模板，规格那条 blanket grep 过宽。 | 若后续阶段需要一次性删掉 22 个，本阶段的工作仍可复用；只是验收文字与实际命令不一致，需回写规格。 |
| **R2** | 调用方不传 `rule_set` 时使用 `baseline_rule_set(site)`（legacy 等价规则集），并新增 `disable_templates` 取代 9 个布尔开关；`CONVERGED_TEMPLATES` 随收敛逐项增长。 | 规格要求删除 legacy 后仍有一个实现；48 处测试、CLI、黄金基线都不传规则集。 | 如果 baseline 语义与 legacy 有差异，测试会红；修复方式是改 baseline 规则参数，而不是恢复 legacy。 |
| **R3** | `_legacy_capacity_body` 拆为 `_create_line_resources`（只建 load/open/tail/overload 变量）+ kernel 规则编译。PM 部分产能通过新增 `ctx.slot_capacity_ms(line, slot)` 保留（`pm_capacity` 折减 + `setup_reserve`）。 | 只删 body 会同时丢掉 PM 折减与 setup 预留；kernel `_compile_capacity` 已优先调用 `slot_capacity_ms`。 | 若遗漏，HC-03 部分产能班次会被误判为满产能，`tests/test_pm_partial_capacity.py` 先红。 |
| **R4** | 删除 `_compact` 的第二档「沿用当前缺口」（`gap_cap`），只保留硬 95% 重解一档。 | 第二档依赖 legacy 的 `shortfall/violations` 变量；kernel 规则没有对外暴露同结构的 gap 变量。硬档失败时保留原计划，合规性不受损。 | 少数数据集换型集中效果变差（仍合规）；若业务需要，下一阶段用指标层重新实现 gap 预算。 |
| **R5** | baseline 的容量规则为 **HARD**（0.95/1.0），默认不做软回退；只有显式 `allow_soft_capacity_fallback=True` 才在软 tier 里把规则临时降为 SOFT。 | 与 legacy 第一档完全一致；规格要求「没有静默降级路径」。 | 过去靠软回退才可行的病态数据集现在会明确报无解；用 `allow_soft_capacity_fallback` 或关掉容量模板解决。 |
| **R6** | `due_date` 只收敛**硬交期**路径；软交期继续沿用调度器既有 `late` 变量与客户权重。 | 软交期的客户权重、pull、lateness 权重是编排语义，不属于这 7 个模板的重复实现；kernel 软 due 会与 `customer_priority` 重复计数。 | 若阶段 2 的 `customer_priority` 指标接管，软交期路径届时再迁移；当前不会产生双重约束。 |
| **R7** | M-01 的**生产默认**取 `start_gate="first_shift"`（整批 SF 齐套后才允许 FG 首次开工），由种子规则集显式配置；baseline 取 `"cumulative"` 以保持 legacy 等价。 | 用户原话「FG 如果需要某个 SF，那么一定要等 SF 确定能满足之后才进行 FG」；手册与功能规格的 canonical 语义是累计可用（上一班产出下一班可用，间隔 0），但用户要求的是开工前的整批确认。两者必须都实现，由规则参数选择。 | 实测黄金 10 天窗口 7 个 FG 中 **6 个**在今天违反整批门控（例：`WBC9317C` 需 1008 `WBC9318`，开工前只有 514；`WB35356` 需 6360 `WC84833/WC52036`，开工前只有 186/276）。开启后预期方向：FG 开工后移、OTD 可能下降、总数量不变。若业务实际只要累计齐套，把种子参数改回 `cumulative` 即可，无代码改动。 |
| **R8** | 部分主数据的降级策略：① BOM 声明的子件缺 `Parameters-SFN` → **该订单不排产**（`invalid_orders` + 警告，不创建 FG/SF 任务）；② 排产 FG 在 BOM 表里没有任何行、但 BOM 契约存在 → 警告，不阻断（可能是备件）；③ BOM 契约整体缺失 → 警告 + 结果标记降级，门控不生效；④ BOM 引用的半成品缺期初库存行 → `DataGap` + 警告 + 期初按 0。 | 用户明确指出主数据「存在不足，只是部分内容」；任何一条都不能静默。① 是结构性缺失，无法确认 SF 能否满足，必须拒绝；②③ 是契约边界，阻断会误杀合法备件。 | ① 可能让真实可生产的订单被拒（当前数据集仅 5 个 `WB80510` 订单受影响，均不在黄金 10 天窗口）；由主数据补齐后自然恢复。 |
| **R9** | 种子规则集升级用一次性脚本 `scripts/upgrade_rule_sets_stage1.py`，不改 `ensure_seed` 的「无 active 才写」语义。 | 直接改 `ensure_seed` 会覆盖用户已发布的版本；版本库已有 draft→published 机制。 | 已有环境需手动跑一次脚本；脚本幂等，重复执行无副作用。 |

---

## 行为契约：M-01（FG waits for SF sufficiency）

设半成品料号 `p`、班次 `s`，`cum(task, s)` 为任务到 `s` 的累计产出（lot 数），`pcs(task)` 为每 lot 件数，`stock(p)` 为期初可用，`r` 为 BOM 单耗。**累计门控（canonical，REG-01）**对每个 `p`、每个 `s`：

```
stock(p) + Σ_{producers q of p} cum(q, min(s-1-buffer, hi_q)) * pcs(q)
    >= Σ_{(fg, r) consumers of p} cum(fg, min(s, hi_fg)) * pcs(fg) * r
```

**整批门控（`start_gate=first_shift`，M-01 生产默认）**额外对每个 FG 任务的每个可能开工班次 `s`：

```
started(fg, s)  =>  stock(p) + Σ cum(producer, min(s-1-buffer, hi)) * pcs
                    >= (Σ_slot x(fg, slot)) * pcs(fg) * r        对每个 BOM 子件 p
```

`started(fg, s)` 是「恰在 s 开工」的布尔量（已存在于模型中），用 `OnlyEnforceIf` 表示蕴含，不引入大 M。`buffer_shifts=0` 时 SF 上一班产出下一班可用，与手册「间隔填 0」一致。

**降级标记**：规则编译产生的每条降级/跳过告警进入 `scheduler.rule_warnings`，最终随 `ScheduleResult.rule_warnings` 返回并出现在 API 响应中；不允许只 `print`。

---

## 当前锚点（2026-11-05，`ca9f0c2`）

| 符号 | 位置 |
|---|---|
| `force_hard=True` 调用点 | `src/scheduler.py:1327`（min_batch）、`1391`（sequence）、`1411`（tooling）、`1436`（material_ready）、`1460`（pm_block）、`1467`（due_date） |
| `force_hard` 定义/应用 | `src/scheduler.py:1792`（参数）、`1800-1801`（`replace(..., HARD)`） |
| legacy material 分支 | `src/scheduler.py:1333-1342`；`_legacy_material_balance` `1664-1759` |
| legacy capacity 分支 | `src/scheduler.py:2552-2568`（`_add_line_capacity`）、`2570-2670`（`_legacy_capacity_body`） |
| legacy pm / tooling / ready / min_batch / sequence / due / concurrency | `1440-1461` / `1394-1412` / `1425-1437` / `1187,1226-1228,1241-1243,1325-1328` / `1368-1392` / `1365,1465-1468,1494-1511` / `1536-1571` |
| kernel 编译器 | `capacity` `compiler.py:144`、`min_batch` `210`、`due_date` `250`、`sequence` `291`、`tooling` `314`、`material_ready` `340`、`material_balance` `353`、`pm_block` `383`、`concurrency` `437` |
| `CompileContext` | `src/kernel/compiler.py:22-59`；调度器适配器 `src/scheduler.py:133-305`，`children()` 在 `212` |
| 规则集装载 / 构造 / 响应 | `src/server.py:2161-2178` / `2455-2522` / `3112-3194` |
| legacy 复选框映射 | `src/server.py:2120-2148` |
| BOM 展开 | `src/data_loader.py:1165-1220`（缺失 SF 主数据时 `warn + invalid_orders + continue`） |
| 期初库存 CSV | `src/data_loader.py:552-566`（缺行静默 0） |
| 黄金基线 | `tests/test_golden_baseline.py:08-11`；契约检查 `tests/test_rule_contract.py:43-44` |

黄金 10 天窗口实测（本计划编写时）：15 个订单 → 7 FG / 12 SF / 19 任务；所有 7 个 FG 都有 BOM；全量 65 单里只有 `WB80510` 引用缺失 SF 主数据（`W268001`、`W268002`，同时缺期初库存行），且不在 10 天窗口内。

---

## File Structure

**新增**

| 文件 | 职责 |
|---|---|
| `src/kernel/baseline.py` | `baseline_rule_set(site)` 与 `CONVERGED_TEMPLATES`；legacy 等价默认，随收敛逐项增长 |
| `tests/test_stage1_contract.py` | `enforce_*` 登记表扫描、阶段末 force_hard/开关归零断言 |
| `tests/fixtures/stage1_switch_register.json` | 22 个开关的归宿（9 removed + 13 deferred） |
| `tests/test_baseline_rule_set.py` | baseline 内容与 `disable_templates` 行为 |
| `tests/test_material_start_gate.py` | REG-01 账本 + `first_shift` 门控的端到端断言 |
| `scripts/upgrade_rule_sets_stage1.py` | 给已激活规则集补 `start_gate`，生成新版本并激活 |

**修改**

| 文件 | 变更 |
|---|---|
| `src/scheduler.py` | 规则集单轨、资源抽取、逐模板删 legacy、`rule_warnings`、`disable_templates` |
| `src/kernel/compiler.py` | `material_balance` 重写；`pm_block` 用 load 上限；Protocol 扩展 |
| `src/kernel/registry.py` | `material_balance.start_gate` ParamSpec |
| `src/data_loader.py` | 缺 SF 主数据拒绝订单；缺期初库存行产 gap |
| `src/server.py` | 把 legacy 复选框映射到 `disable_templates`；响应带 `rule_warnings` |
| `config/rule_sets/WL_Phase1.json` / `SY38_Phase1.json` | `start_gate: "first_shift"`，版本号 +1 |
| `tests/test_rule_contract.py` | #10 转正式；新增 #11 开关登记扫描 |
| `tests/test_kernel.py` | `ToyContext` 实现新 `CompileContext` 成员 |
| `run_tests.py` | 新测试模块加入 fast 列表 |

---

### Task 1: 阶段契约与开关登记表

**Files:**
- Create: `tests/fixtures/stage1_switch_register.json`
- Create: `tests/test_stage1_contract.py`
- Modify: `run_tests.py:31-48`

**Interfaces:**
- Consumes: 无
- Produces: `stage1_switch_register.json`（后续 Task 14 依赖）；`TestStage1SwitchRegister` 测试类

- [ ] **Step 1: 写失败测试**

`tests/test_stage1_contract.py`：

```python
"""Stage 1 source-level gates: every enforce_* switch has a registered home."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "tests" / "fixtures" / "stage1_switch_register.json"
SCHEDULER = ROOT / "src" / "scheduler.py"


def _present_switches() -> set[str]:
    source = SCHEDULER.read_text(encoding="utf-8")
    return set(re.findall(r"self\.enforce_([a-z0-9_]+)", source))


class TestStage1SwitchRegister(unittest.TestCase):
    def test_register_sets_are_disjoint(self):
        data = json.loads(REGISTER.read_text(encoding="utf-8"))
        self.assertFalse(set(data["stage1"]) & set(data["deferred"]))

    def test_no_unregistered_enforce_switch_appears(self):
        data = json.loads(REGISTER.read_text(encoding="utf-8"))
        known = set(data["stage1"]) | set(data["deferred"])
        unregistered = _present_switches() - known
        self.assertEqual(unregistered, set(), f"unregistered enforce_*: {sorted(unregistered)}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_stage1_contract -v`
Expected: FAIL，`FileNotFoundError: ...stage1_switch_register.json`

- [ ] **Step 3: 写登记表**

`tests/fixtures/stage1_switch_register.json`：

```json
{
  "stage1": {
    "utilization_95": "capacity_utilization",
    "on_time_delivery": "due_date",
    "concurrency_limit": "concurrency",
    "mpq_batch": "min_batch",
    "material_balance": "material_balance",
    "sequential_pulling": "sequence",
    "tooling_exclusivity": "tooling_exclusivity",
    "material_arrival": "material_ready",
    "pm_maintenance": "pm_block"
  },
  "deferred": {
    "freeze_window": {"stage": 2, "reason": "baseline equality needs a freeze-window metric"},
    "jitter": {"stage": 2, "reason": "objective-shaping metric, no catalog rule"},
    "labor_smoothing": {"stage": 2, "reason": "labor metric, not one of the seven templates"},
    "sc08": {"stage": 2, "reason": "objective-shaping metric"},
    "campaign_batching": {"stage": 3, "reason": "campaign engine, not a structure template"},
    "slack_fallback": {"stage": 3, "reason": "orchestration retry policy"},
    "d1_clock": {"stage": 2, "reason": "inventory metric shared with material ledger"},
    "d1_clock_hard": {"stage": 2, "reason": "inventory metric shared with material ledger"},
    "pull": {"stage": 2, "reason": "delivery direction objective"},
    "customer_priority": {"stage": 2, "reason": "customer_priority template, Stage 1 keeps soft-due lateness"},
    "weekend_penalty": {"stage": 2, "reason": "calendar metric"},
    "campaign": {"stage": 3, "reason": "campaign engine"},
    "lead_time": {"stage": 2, "reason": "lead-time metric"}
  }
}
```

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_stage1_contract -v`
Expected: PASS（2 tests）

- [ ] **Step 5: 加入 fast 列表**

在 `run_tests.py` 的 `fast_modules` 列表追加 `"tests.test_stage1_contract"`。

- [ ] **Step 6: 提交**

```bash
git add tests/test_stage1_contract.py tests/fixtures/stage1_switch_register.json run_tests.py
git commit -m "test(stage1): register the 22 enforce switches before convergence"
```

---

### Task 2: baseline 规则集与 `disable_templates`

**Files:**
- Create: `src/kernel/baseline.py`
- Create: `tests/test_baseline_rule_set.py`
- Modify: `src/scheduler.py:309-373`（构造参数）、`545-550`（规则集装载）、`1063-1082`（`_run` 触发条件）
- Modify: `run_tests.py:31-48`

**Interfaces:**
- Consumes: `src.kernel.types`（`RuleSet/RuleGroup/RuleInstance/Scope/Nature/GroupMode`）
- Produces: `baseline_rule_set(site: str) -> RuleSet`、`CONVERGED_TEMPLATES: frozenset[str]`、构造参数 `disable_templates: Optional[Iterable[str]] = None`。后续每个收敛任务只允许**追加**一条 baseline 规则并把模板名加进 `CONVERGED_TEMPLATES`。

- [ ] **Step 1: 写失败测试**

`tests/test_baseline_rule_set.py`：

```python
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from src.data_loader import ProductionTask, RequirementPlan
from src.kernel.baseline import CONVERGED_TEMPLATES, baseline_rule_set
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode


def _plan():
    start = datetime(2026, 10, 12, 7)
    task = ProductionTask(
        task_id="FG1", stage="FG", line="PGLINKAGE", part_no="P1",
        description="P1", ct_seconds=60, trp=1, mpq=100, lot_size=100,
        lots=1, quantity=100, lot_millis=100 * 60 * 1000, order_id="O1",
        due_datetime=start + timedelta(days=2),
    )
    return RequirementPlan(fg_tasks=[task], sf_tasks=[], stock_movements=[], site="WL")


class TestBaselineRuleSet(unittest.TestCase):
    def test_baseline_has_the_four_groups_and_a_site(self):
        rule_set = baseline_rule_set("WL")
        self.assertEqual(rule_set.rule_set_id, "BASELINE")
        self.assertEqual(rule_set.site, "WL")
        self.assertEqual([g.group_id for g in rule_set.ordered_groups()],
                         ["G0_PHYSICAL", "G1_DELIVERY", "G2_CAPACITY", "G3_CHANGEOVER"])

    def test_scheduler_uses_baseline_when_none_is_passed(self):
        sched = APSScheduler(_plan(), calendar_mode=CalendarMode.SEVEN_ZERO, site="WL")
        self.assertIsNotNone(sched.rule_set)
        self.assertEqual(sched.rule_set.rule_set_id, "BASELINE")

    def test_disable_templates_turns_off_legacy_and_kernel(self):
        sched = APSScheduler(
            _plan(), calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            disable_templates={"material_balance"},
        )
        self.assertFalse(sched.enforce_material_balance)
        self.assertFalse(sched._use_kernel("material_balance"))

    def test_converged_set_starts_empty(self):
        self.assertEqual(set(CONVERGED_TEMPLATES), set())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_baseline_rule_set -v`
Expected: FAIL，`ModuleNotFoundError: src.kernel.baseline`

- [ ] **Step 3: 实现 baseline**

`src/kernel/baseline.py`：

```python
"""Legacy-equivalent defaults for callers that pass no rule set.

``CONVERGED_TEMPLATES`` grows one template per stage-1 convergence commit.
Until a template is listed here the scheduler's legacy branch is still the
only implementation, so the baseline rule set starts empty on purpose.
"""
from __future__ import annotations

from typing import List

from src.kernel.types import GroupMode, RuleGroup, RuleInstance, RuleSet

CONVERGED_TEMPLATES: frozenset[str] = frozenset()


def _groups() -> List[RuleGroup]:
    return [
        RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD),
        RuleGroup("G1_DELIVERY", 10, GroupMode.LEXICOGRAPHIC),
        RuleGroup("G2_CAPACITY", 20, GroupMode.LEXICOGRAPHIC),
        RuleGroup("G3_CHANGEOVER", 30, GroupMode.WEIGHTED),
    ]


def baseline_rule_set(site: str) -> RuleSet:
    return RuleSet(
        rule_set_id="BASELINE",
        site=site,
        version=1,
        groups=_groups(),
        rules=[],
    )
```

- [ ] **Step 4: 实现 `disable_templates` 与规则集装载**

在 `APSScheduler.__init__` 形参 `kernel_templates` 之后加：

```python
        disable_templates: Optional[Iterable[str]] = None,
```

把 `545-550` 的规则集装载替换为：

```python
        from src.kernel.baseline import CONVERGED_TEMPLATES, baseline_rule_set
        from src.kernel.types import RuleSet

        self.rule_warnings: List[str] = []
        self.disable_templates = set(disable_templates or [])
        effective = rule_set if rule_set is not None else baseline_rule_set(self.site)
        if self.disable_templates:
            effective = RuleSet(
                rule_set_id=effective.rule_set_id,
                site=effective.site,
                version=effective.version,
                groups=list(effective.groups),
                rules=[r for r in effective.rules if r.template_id not in self.disable_templates],
            )
        self.rule_set = effective
```

紧随其后（仍在 `self.rule_set` 赋值之后、`use_kernel_capacity` 之前）加 legacy 反向映射，使旧测试在迁移前继续成立：

```python
        legacy_gate = {
            "capacity_utilization": "enforce_utilization_95",
            "due_date": "enforce_on_time_delivery",
            "concurrency": "enforce_concurrency_limit",
            "min_batch": "enforce_mpq_batch",
            "material_balance": "enforce_material_balance",
            "sequence": "enforce_sequential_pulling",
            "tooling_exclusivity": "enforce_tooling_exclusivity",
            "material_ready": "enforce_material_arrival",
            "pm_block": "enforce_pm_maintenance",
        }
        for template in self.disable_templates:
            flag = legacy_gate.get(template)
            if flag:
                setattr(self, flag, False)
```

把 `546-550` 的模板集合替换为：

```python
        templates = set(kernel_templates or [])
        if not templates:
            templates = {
                r.template_id for r in self.rule_set.rules
                if r.enabled and r.template_id in CONVERGED_TEMPLATES
            }
        if use_kernel_capacity:
            templates.add("capacity_utilization")
        self.kernel_templates = {t for t in templates if t not in self.disable_templates}
        self.use_kernel_capacity = rule_set is not None and "capacity_utilization" in self.kernel_templates
```

在 `_run` 的 lexicographic 条件里加一条「规则集确实有规则」，避免空 baseline 改变求解路径：

```python
        if (
            self.rule_set is not None
            and self.rule_set.rules
            and group_terms
            and mode == "soft"
            and len([gid for gid, terms in group_terms.items() if terms]) > 1
        ):
```

- [ ] **Step 5: 运行测试**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_baseline_rule_set -v`
Expected: PASS（4 tests）

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast`
Expected: 全绿（baseline 为空，行为不变）

- [ ] **Step 6: 提交**

```bash
git add src/kernel/baseline.py tests/test_baseline_rule_set.py src/scheduler.py run_tests.py
git commit -m "feat(stage1): give every scheduler a rule set via baseline + disable_templates"
```

---

### Task 3: `rule_warnings` 通道与期初库存缺口

**Files:**
- Modify: `src/scheduler.py:114-127`（`ScheduleResult`）、`1780-1815`（`_compile_rules_for`）、`1085-1102`/`1140-1150`（结果构造）
- Modify: `src/data_loader.py:552-566`（期初库存）及文件顶部导入
- Modify: `src/server.py:3112-3194`（响应）
- Create: `tests/test_rule_warnings.py`
- Modify: `tests/test_loader_gaps.py`
- Modify: `run_tests.py:31-48`

**Interfaces:**
- Consumes: `GapCollector`、`DataGap`
- Produces: `ScheduleResult.rule_warnings: List[str]`；`data_loader._report_missing_stock(bom, initial_stock, site, gaps, warnings) -> None`

- [ ] **Step 1: 写失败测试**

`tests/test_rule_warnings.py`：

```python
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from src.data_loader import ProductionTask, RequirementPlan
from src.kernel.types import GroupMode, Nature, RuleGroup, RuleInstance, RuleSet, Scope
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode


class TestRuleWarnings(unittest.TestCase):
    def test_compiler_warnings_are_returned_not_printed(self):
        start = datetime(2026, 10, 12, 7)
        task = ProductionTask(
            task_id="FG1", stage="FG", line="PGLINKAGE", part_no="P1",
            description="P1", ct_seconds=60, trp=1, mpq=100, lot_size=100,
            lots=1, quantity=100, lot_millis=100 * 60 * 1000, order_id="O1",
            due_datetime=start + timedelta(days=2),
        )
        plan = RequirementPlan(fg_tasks=[task], sf_tasks=[], stock_movements=[], site="WL")
        rule_set = RuleSet(
            "WARN_TEST", "WL", 1,
            [RuleGroup("G3_CHANGEOVER", 30, GroupMode.WEIGHTED)],
            [RuleInstance("CO", "changeover", Scope(), Nature.SOFT, 1, "G3_CHANGEOVER", 30,
                          {"setup_minutes": 0, "count_by": "model_switch"})],
        )
        sched = APSScheduler(
            plan, start_datetime=start, planning_days=3,
            calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            rule_set=rule_set, kernel_templates={"changeover"},
        )
        result = sched.solve(time_limit_seconds=10.0)
        self.assertTrue(any("count_by" in w for w in result.rule_warnings))
        self.assertTrue(any("count_by" in w for w in sched.rule_warnings))


if __name__ == "__main__":
    unittest.main()
```

在 `tests/test_loader_gaps.py` 追加：

```python
class TestMissingStockRows(unittest.TestCase):
    def test_bom_component_without_stock_row_is_a_gap_not_a_zero(self):
        from src.data_gaps import GapCollector
        from src.data_loader import _report_missing_stock

        gaps = GapCollector()
        warnings: list = []
        _report_missing_stock(
            {"FG1": [("SF1", 1.0), ("SF2", 2.0)]},
            {"SF1": 10.0},
            "WL",
            gaps,
            warnings,
        )
        self.assertEqual([g.column_key for g in gaps.gaps()], ["opening_qty"])
        self.assertEqual(gaps.gaps()[0].index, ("WL", "SF2"))
        self.assertTrue(any("SF2" in w for w in warnings))
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_rule_warnings tests.test_loader_gaps -v`
Expected: FAIL（`rule_warnings` 属性不存在 / `_report_missing_stock` 不存在）

- [ ] **Step 3: 实现**

`ScheduleResult` 末尾加字段：

```python
    rule_warnings: List[str] = field(default_factory=list)
```

（若该 dataclass 未导入 `field`，从 `dataclasses` 补。）

`_compile_rules_for` 中把：

```python
        for warning in compiled.warnings:
            print(f"  [kernel] {warning}")
```

替换为：

```python
        self.rule_warnings.extend(f"[{template_id}] {w}" for w in compiled.warnings)
```

两处 `ScheduleResult(...)` 构造（`_run` 与 `_run_lexicographic`）都加：

```python
            rule_warnings=list(self.rule_warnings),
```

在 `data_loader.py` 期初库存分支之后加模块级函数并调用：

```python
def _report_missing_stock(bom, initial_stock, site, gaps, warnings) -> None:
    missing = {
        sf_pn
        for children in bom.values()
        for sf_pn, _ratio in children
        if sf_pn not in initial_stock
    }
    for sf_pn in sorted(missing):
        gap = DataGap(
            "data_loader.load_aps_csv", "initial_stock", "opening_qty", (site, sf_pn), "missing_row"
        )
        gaps.warn(warnings, gap, f"半成品 {sf_pn} 无期初库存行，期初按 0 起账")
```

在 `load_aps_csv` 第 4 步结束后调用：

```python
    _report_missing_stock(bom, initial_stock, site_filter, gaps, warnings)
```

服务器响应字典中加：

```python
        "rule_warnings": list(getattr(result, "rule_warnings", [])),
```

- [ ] **Step 4: 运行测试**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_rule_warnings tests.test_loader_gaps -v`
Expected: PASS

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add src/scheduler.py src/data_loader.py src/server.py tests/test_rule_warnings.py tests/test_loader_gaps.py run_tests.py
git commit -m "feat(stage1): surface rule warnings and missing stock rows instead of printing them"
```

---

### Task 4: 部分 BOM 数据 —— 缺 SF 主数据的订单不再排产

**Files:**
- Modify: `src/data_loader.py:1160-1220`
- Create: `tests/test_partial_bom.py`
- Modify: `run_tests.py:31-48`

**Interfaces:**
- Consumes: `ProductionInputSnapshot`、`build_requirement_plan`
- Produces: 行为变化 —— BOM 声明的子件缺 `sf_params` 时，该订单不产生 FG/SF 任务，`plan.invalid_orders` 与 `plan.warnings` 各有一条；不新增字段。

- [ ] **Step 1: 写失败测试**

`tests/test_partial_bom.py`：

```python
from __future__ import annotations

import unittest
from datetime import datetime

from src.data_loader import (
    CustomerOrder,
    ProductParam,
    ProductionInputSnapshot,
    build_requirement_plan,
)


def _param(stage_part: str, line: str = "PGLINKAGE") -> ProductParam:
    return ProductParam(
        site="WL", line=line, part_no=stage_part, description=stage_part,
        ct_seconds=60, trp=1, mpq=100, output_per_shift=480,
        hours_per_shift=8, shifts_per_day=3,
    )


def _snapshot(bom, orders):
    return ProductionInputSnapshot(
        fg_params={"P1": _param("P1")},
        sf_params={"C1": _param("C1")},
        bom=bom,
        initial_stock={},
        orders=orders,
        planning_dates=[],
    )


def _order(order_id: str, qty: float = 100) -> CustomerOrder:
    return CustomerOrder(order_id, "P1", "P1", "2026/10/20", datetime(2026, 10, 20, 7), qty)


class TestPartialBom(unittest.TestCase):
    def test_complete_bom_still_produces_fg_and_sf(self):
        plan = build_requirement_plan(_snapshot({"P1": [("C1", 1.0)]}, [_order("O1")]))
        self.assertEqual(len(plan.fg_tasks), 1)
        self.assertEqual(len(plan.sf_tasks), 1)
        self.assertEqual(plan.invalid_orders, [])

    def test_missing_sf_master_rejects_the_order(self):
        plan = build_requirement_plan(
            _snapshot({"P1": [("C1", 1.0), ("MISSING", 1.0)]}, [_order("O1")])
        )
        self.assertEqual(plan.fg_tasks, [])
        self.assertEqual(plan.sf_tasks, [])
        self.assertEqual([oid for oid, _ in plan.invalid_orders], ["O1"])
        self.assertTrue(any("MISSING" in w for w in plan.warnings))

    def test_a_rejected_order_does_not_consume_stock_for_the_next_one(self):
        plan = build_requirement_plan(
            _snapshot(
                {"P1": [("C1", 1.0), ("MISSING", 1.0)]},
                [_order("O1", 100), _order("O2", 100)],
            )
        )
        self.assertEqual(plan.fg_tasks, [])
        self.assertEqual(len(plan.invalid_orders), 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_partial_bom -v`
Expected: FAIL（缺失主数据时仍创建 FG 任务）

- [ ] **Step 3: 实现两遍展开**

把 `build_requirement_plan` 中 `fg_tasks.append(fg_task)` 及其后的 BOM 循环改为「先校验、后建任务」：删除原位置的 `fg_tasks.append(fg_task)`、`fg_avail[...] += ...`、`fg_produced[...] += ...`，改到循环末尾：

```python
        components = snapshot.bom.get(order.part_no, [])
        missing = [sf_pn for sf_pn, _ratio in components if sf_pn not in snapshot.sf_params]
        if missing:
            warnings.append(
                f"{order.order_id}: BOM 子件缺半成品主数据（{'、'.join(missing)}），订单不排产"
            )
            invalid_orders.append((order.order_id, f"半成品主数据缺失：{'、'.join(missing)}"))
            continue

        created_sf = []
        for sf_pn, ratio in components:
            sf_param = snapshot.sf_params[sf_pn]
            if sf_param.headcount <= 0 and sf_pn not in crew_warned:
                crew_warned.add(sf_pn)
            # ... 原有 gross_need / sf_net / MPQ 计算与 append 保持不变 ...
            created_sf.append(sf_task)
        fg_avail[order.part_no] += qty - order.demand_quantity
        fg_produced[order.part_no] = fg_produced.get(order.part_no, 0.0) + qty
        fg_tasks.append(fg_task)
        sf_tasks.extend(created_sf)
```

注意：原循环内直接 `sf_tasks.append(...)` 要改成 `created_sf.append(...)`，确保订单被拒时不会留下孤儿 SF 任务。`continue` 分支不能再写 `fg_avail`——被拒订单不占用库存。

- [ ] **Step 4: 运行测试**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_partial_bom tests.test_golden_baseline -v`
Expected: `test_partial_bom` 3 项 PASS；黄金基线 1-7 项 PASS（其 7 个 FG 都无缺失主数据）

- [ ] **Step 5: 提交**

```bash
git add src/data_loader.py tests/test_partial_bom.py run_tests.py
git commit -m "feat(data): reject an order when its BOM references a part without SF master data"
```

---

### Task 5: kernel `material_balance` 账本 + M-01 整批门控（只增不删）

**Files:**
- Modify: `src/kernel/registry.py:146-153`
- Modify: `src/kernel/compiler.py:22-59`（Protocol）、`353-380`（重写）
- Modify: `src/scheduler.py:133-305`（适配器）
- Modify: `tests/test_kernel.py`（`ToyContext`）
- Create: `tests/test_material_start_gate.py`
- Modify: `run_tests.py:31-48`

**Interfaces:**
- Consumes: `ctx.cumulative/unit_pieces/window/x/started/active/model`
- Produces（`CompileContext` 新成员）：
  - `has_bom_contract() -> bool`
  - `demand_parts() -> Iterable[str]`
  - `producers_for(part: str) -> Iterable[Any]`
  - `consumers_for(part: str) -> Iterable[Tuple[Any, float]]`
  - `components_for(fg_task) -> Iterable[Tuple[str, float]]`
  - `opening_stock(part: str) -> float`
  - `total_units(task) -> int`
  - 规则参数 `start_gate: "cumulative" | "first_shift"`（默认 `cumulative`）

- [ ] **Step 1: 写失败测试（端到端语义）**

`tests/test_material_start_gate.py`：

```python
"""M-01: FG may start only when its SF supply is confirmed sufficient."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from src.data_loader import ProductionTask, RequirementPlan
from src.kernel.types import GroupMode, Nature, RuleGroup, RuleInstance, RuleSet, Scope
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode

START = datetime(2026, 10, 12, 7)


def _plan():
    sf = ProductionTask(
        task_id="SF1", stage="SF", line="PGLINKAGE", part_no="C1",
        description="C1", ct_seconds=60, trp=1, mpq=1, lot_size=1,
        lots=960, quantity=960, lot_millis=60 * 1000, order_id="O1",
        due_datetime=START + timedelta(days=4), parent_task_id="FG1",
    )
    fg = ProductionTask(
        task_id="FG1", stage="FG", line="PGLINKAGE", part_no="P1",
        description="P1", ct_seconds=60, trp=1, mpq=1, lot_size=1,
        lots=960, quantity=960, lot_millis=60 * 1000, order_id="O1",
        due_datetime=START + timedelta(days=4),
    )
    return RequirementPlan(
        fg_tasks=[fg], sf_tasks=[sf], stock_movements=[], site="WL",
        bom={"P1": [("C1", 1.0)]}, initial_stock={"C1": 0.0},
    )


def _rule_set(start_gate: str) -> RuleSet:
    return RuleSet(
        f"MBAL_{start_gate}", "WL", 1,
        [RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD)],
        [RuleInstance("MBAL", "material_balance", Scope(), Nature.HARD, 1,
                      "G0_PHYSICAL", 0,
                      {"buffer_shifts": 0, "include_stock": True, "start_gate": start_gate})],
    )


def _first_fg_shift(start_gate: str) -> int:
    sched = APSScheduler(
        _plan(), start_datetime=START, planning_days=5,
        calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
        shift_hours=8, shifts_per_day=3,
        disable_templates={"capacity_utilization", "due_date"},
        rule_set=_rule_set(start_gate),
        kernel_templates={"material_balance"},
    )
    result = sched.solve(time_limit_seconds=20.0)
    fg = next(t for t in result.tasks if t.task_id == "FG1")
    return min(sched._shift_index[key] for key in fg.shift_breakdown)


class TestMaterialStartGate(unittest.TestCase):
    def test_cumulative_ledger_allows_fg_one_shift_after_first_sf_output(self):
        self.assertEqual(_first_fg_shift("cumulative"), 1)

    def test_first_shift_gate_waits_for_the_whole_sf_lot(self):
        self.assertEqual(_first_fg_shift("first_shift"), 2)


if __name__ == "__main__":
    unittest.main()
```

说明：SF 每班 480 件、共 960；累计门控允许 FG 在第 1 班开工（第 0 班已产出 480）；整批门控要求 FG 开工前 960 全部就绪，故最早第 2 班。

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_material_start_gate -v`
Expected: FAIL（`first_shift` 未实现，两个断言都是 1 或直接报 `AttributeError`）

- [ ] **Step 3: 注册参数**

`src/kernel/registry.py` 的 `material_balance` 参数元组加：

```python
            ParamSpec(
                "start_gate", "enum", "cumulative",
                choices=("cumulative", "first_shift"),
                description="FG 开工门控：累计可用 / 整批齐套",
            ),
```

- [ ] **Step 4: 扩展 Protocol 与适配器**

`src/kernel/compiler.py` 的 `CompileContext` 末尾加：

```python
    def has_bom_contract(self) -> bool: ...
    def demand_parts(self) -> Iterable[str]: ...
    def producers_for(self, part: str) -> Iterable[Any]: ...
    def consumers_for(self, part: str) -> Iterable[Tuple[Any, float]]: ...
    def components_for(self, fg_task) -> Iterable[Tuple[str, float]]: ...
    def opening_stock(self, part: str) -> float: ...
    def total_units(self, task) -> int: ...
```

`src/scheduler.py` 的 `_SchedulerCompileContext.__init__` 预计算 BOM 视图：

```python
        plan = scheduler.plan
        pairwise = {}
        if not plan.bom:
            for child in plan.sf_tasks:
                parent = getattr(child, "parent_task_id", None)
                if parent:
                    pairwise.setdefault(parent, []).append((child.part_no, child.bom_ratio))
        self._bom_by_fg = {fg: list(rows) for fg, rows in plan.bom.items()} if plan.bom else pairwise
        self._consumers = {}
        for fg_task in plan.fg_tasks:
            for part, ratio in self._bom_by_fg.get(fg_task.part_no, []):
                self._consumers.setdefault(part, []).append((fg_task, float(ratio)))
        self._producers = {}
        for sf_task in plan.sf_tasks:
            self._producers.setdefault(sf_task.part_no, []).append(sf_task)
```

方法实现：

```python
    def has_bom_contract(self):
        return bool(self._bom_by_fg)

    def demand_parts(self):
        return sorted(self._consumers)

    def producers_for(self, part):
        return list(self._producers.get(part, []))

    def consumers_for(self, part):
        return list(self._consumers.get(part, []))

    def components_for(self, fg_task):
        return list(self._bom_by_fg.get(fg_task.part_no, []))

    def opening_stock(self, part):
        return float(getattr(self._scheduler.plan, "initial_stock", {}).get(part, 0.0))

    def total_units(self, task):
        return self._scheduler._units_total(task)
```

- [ ] **Step 5: 重写编译器**

`src/kernel/compiler.py` 的 `_compile_material_balance` 整体替换为：

```python
def _compile_material_balance(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    """Part-level, time-phased ledger (REG-01) plus the optional first-shift gate.

    Supply released by shift s-1 (plus opening stock) must cover every
    consumer's cumulative consumption through shift s. With
    ``start_gate=first_shift`` a FG task may start only when its whole lot's
    component quantity is already available.
    """
    warnings: List[str] = []
    buffer_shifts = int(params["buffer_shifts"])
    include_stock = bool(params["include_stock"])
    start_gate = str(params.get("start_gate", "cumulative"))
    if not ctx.has_bom_contract():
        warnings.append(f"{rule.rule_id}: 无 BOM 契约，FG 齐套门控未生效（数据降级）")
        return warnings, rule.nature is Nature.HARD

    parts = list(ctx.demand_parts())
    spans = []
    for part in parts:
        producers = list(ctx.producers_for(part))
        consumers = list(ctx.consumers_for(part))
        if not consumers:
            continue
        opening = int(round(float(ctx.opening_stock(part)))) if include_stock else 0
        slots = []
        for producer in producers:
            slots.extend(ctx.window(producer))
        for consumer, _ratio in consumers:
            slots.extend(ctx.window(consumer))
        if not slots:
            continue
        spans.append((part, producers, consumers, opening, min(slots), max(slots)))

    if not spans:
        return warnings, rule.nature is Nature.HARD

    for _part, producers, consumers, opening, min_slot, max_slot in spans:
        for slot in range(min_slot, max_slot + 1):
            supply_terms = [opening]
            for producer in producers:
                p_lo, p_hi = ctx.window(producer)
                lookback = slot - 1 - buffer_shifts
                if lookback >= p_lo:
                    supply_terms.append(
                        ctx.cumulative(producer, min(lookback, p_hi)) * ctx.unit_pieces(producer)
                    )
            consumed = []
            for consumer, ratio in consumers:
                c_lo, c_hi = ctx.window(consumer)
                if slot >= c_lo:
                    frac = Fraction(str(ratio)).limit_denominator(1000)
                    consumed.append((consumer, min(slot, c_hi), frac))
            if not consumed:
                continue
            lcm_denom = 1
            for _task, _cum_slot, frac in consumed:
                lcm_denom = lcm_denom * frac.denominator // math.gcd(lcm_denom, frac.denominator)
            scaled_supply = sum(supply_terms) * lcm_denom
            scaled_consumed = [
                ctx.cumulative(task, cum_slot) * ctx.unit_pieces(task)
                * frac.numerator * (lcm_denom // frac.denominator)
                for task, cum_slot, frac in consumed
            ]
            ctx.model.Add(scaled_supply >= sum(scaled_consumed))

    if start_gate == "first_shift":
        for fg_task in ctx.tasks():
            if str(getattr(fg_task, "stage", "")) != "FG":
                continue
            if not rule.scope.matches(_target(site, fg_task)):
                continue
            components = list(ctx.components_for(fg_task))
            if not components:
                continue
            total_pieces = ctx.total_units(fg_task) * ctx.unit_pieces(fg_task)
            lo, hi = ctx.window(fg_task)
            for slot in range(lo, hi + 1):
                started = ctx.started(fg_task, slot)
                for part, ratio in components:
                    frac = Fraction(str(ratio)).limit_denominator(1000)
                    opening = int(round(float(ctx.opening_stock(part)))) if include_stock else 0
                    lookback = slot - 1 - buffer_shifts
                    available = [opening]
                    for producer in ctx.producers_for(part):
                        p_lo, p_hi = ctx.window(producer)
                        if lookback >= p_lo:
                            available.append(
                                ctx.cumulative(producer, min(lookback, p_hi))
                                * ctx.unit_pieces(producer)
                            )
                    need = frac.numerator * total_pieces
                    have = frac.denominator * sum(available)
                    ctx.model.Add(have >= need).OnlyEnforceIf(started)
    return warnings, rule.nature is Nature.HARD
```

- [ ] **Step 6: 更新 `ToyContext`**

在 `tests/test_kernel.py` 的 `ToyContext` 中加（基于既有 `_tasks` 与 `children()`）：

```python
    def has_bom_contract(self):
        return any(t.parent_task_id for t in self._tasks)

    def demand_parts(self):
        return sorted({t.part_no for t in self._tasks if t.stage == "SF"})

    def producers_for(self, part):
        return [t for t in self._tasks if t.stage == "SF" and t.part_no == part]

    def consumers_for(self, part):
        return [(t, self.bom_ratio(c)) for t in self._tasks if t.stage == "FG"
                for c in self.children(t) if c.part_no == part]

    def components_for(self, fg_task):
        return [(c.part_no, self.bom_ratio(c)) for c in self.children(fg_task)]

    def opening_stock(self, part):
        return 0.0

    def total_units(self, task):
        return self._units_total(task) if hasattr(self, "_units_total") else task.lots
```

若 `ToyContext` 没有 `_units_total`，直接用 `task.lots`。

- [ ] **Step 7: 运行测试**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_material_start_gate tests.test_kernel tests.test_regressions -v`
Expected: PASS。`test_reg01` 仍走 legacy（baseline 尚未包含 material_balance），不受影响。

- [ ] **Step 8: 提交**

```bash
git add src/kernel/registry.py src/kernel/compiler.py src/scheduler.py tests/test_kernel.py tests/test_material_start_gate.py run_tests.py
git commit -m "feat(kernel): part-level material ledger with an optional first-shift gate"
```

---

### Task 6: 收敛 `material_balance`（REG-01 单轨 + 种子升级）

**Files:**
- Modify: `src/scheduler.py:1333-1342`（legacy 分支）、`1664-1759`（删除 `_legacy_material_balance`）、`309-373`（删开关）
- Modify: `src/kernel/baseline.py`（追加规则 + `CONVERGED_TEMPLATES`）
- Modify: `src/server.py:2120-2148`（复选框映射）、`2455-2522`（构造参数）
- Create: `scripts/upgrade_rule_sets_stage1.py`
- Modify: `config/rule_sets/WL_Phase1.json`、`config/rule_sets/SY38_Phase1.json`
- Modify: `tests/test_regressions.py`、所有传 `enforce_material_balance` 的测试（`grep -rn "enforce_material_balance" tests/` 共 19 处）
- Modify: `tests/test_golden_baseline.py`（删除 `"buffer_shifts": 0, "include_stock": True` 未带 `start_gate` 的隐式依赖）
- Create: `tests/test_seed_upgrade_stage1.py`
- Modify: `run_tests.py:31-48`

**Interfaces:**
- Consumes: Task 5 的 kernel 实现
- Produces: `baseline_rule_set` 含 `BASELINE_MBAL`；`CONVERGED_TEMPLATES = {"material_balance"}`；调度器不再接受 `enforce_material_balance`

- [ ] **Step 1: 写失败测试**

`tests/test_seed_upgrade_stage1.py`：

```python
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.rule_sets import service
from src.rule_sets.store import get_active


PAYLOAD = {
    "rule_set_id": "WL_PHASE1",
    "site": "WL",
    "version": 4,
    "groups": [{"id": "G0_PHYSICAL", "priority": 0, "mode": "hard"}],
    "rules": [
        {
            "id": "WL_MBAL", "template": "material_balance",
            "scope": {"site": "*", "line": "*", "stage": "*", "part": "*"},
            "nature": "hard", "weight": 1.0, "group": "G0_PHYSICAL", "priority": 0,
            "params": {"buffer_shifts": 0, "include_stock": True},
            "enabled": True, "inherit": "replace",
            "effective_from": None, "effective_to": None,
        }
    ],
    "effective_from": None, "effective_to": None,
}


class TestSeedUpgrade(unittest.TestCase):
    def test_upgrade_adds_first_shift_gate_and_publishes_a_new_version(self):
        from scripts.upgrade_rule_sets_stage1 import upgrade_site

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "rules.sqlite3"
            service.save_payload(PAYLOAD, site="WL", note="seed", activate=True, db_path=db)
            before = get_active("WL", db)
            self.assertTrue(upgrade_site("WL", db_path=db))
            after = get_active("WL", db)
            self.assertGreater(after["version"], before["version"])
            params = after["payload"]["rules"][0]["params"]
            self.assertEqual(params["start_gate"], "first_shift")

    def test_upgrade_is_idempotent(self):
        from scripts.upgrade_rule_sets_stage1 import upgrade_site

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "rules.sqlite3"
            service.save_payload(PAYLOAD, site="WL", note="seed", activate=True, db_path=db)
            self.assertTrue(upgrade_site("WL", db_path=db))
            self.assertFalse(upgrade_site("WL", db_path=db))


if __name__ == "__main__":
    unittest.main()
```

把 `tests/test_regressions.py::test_reg01` 的构造从 `enforce_utilization_95=False, enforce_material_balance=False` 改为 `disable_templates={"capacity_utilization", "material_balance"}`；其余测试按映射机械替换：

| 旧参数 | 新参数 |
|---|---|
| `enforce_material_balance=False` | `disable_templates={"material_balance"}`（与其它项合并成一个集合） |

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_seed_upgrade_stage1 -v`
Expected: FAIL（`scripts.upgrade_rule_sets_stage1` 不存在）

- [ ] **Step 3: 实现收敛**

1. `_build_model` 的 material 段替换为：

```python
        kernel_material = use_kernel and self._use_kernel("material_balance")
        cumulative = None
        if kernel_material or self.inventory_policies or self.enforce_d1_clock:
            cumulative = self._create_cumulative(model, x)
        if kernel_material:
            self._compile_rules_for(
                "material_balance", model, x, active, started, end_of, {}, {}, {}, cumulative=cumulative
            )
```

2. 删除 `_legacy_material_balance` 整个方法。
3. 从 `__init__` 签名、赋值、legacy 反向映射表中删除 `enforce_material_balance`；保留 `disable_templates` 中的同名项（无开关可映射，直接过滤规则）。
4. `src/server.py`：删除 `enforce_material_balance = (...)` 与构造参数；把 R-SF-03/R-WL-23/R-SY-02/R-SY-04 的关闭状态翻译为 `disable_templates`：

```python
    disabled_templates = set()
    if not ("R-SF-03" in enabled or "R-WL-23" in enabled or "R-SY-02" in enabled or "R-SY-04" in enabled):
        disabled_templates.add("material_balance")
```

构造 `APSScheduler` 时传 `disable_templates=disabled_templates`。

5. `src/kernel/baseline.py`：

```python
CONVERGED_TEMPLATES: frozenset[str] = frozenset({"material_balance"})
```

并在 `rules` 中加：

```python
        RuleInstance(
            "BASELINE_MBAL", "material_balance", Scope(), Nature.HARD, 1,
            "G0_PHYSICAL", 0,
            {"buffer_shifts": 0, "include_stock": True, "start_gate": "cumulative"},
        ),
```

6. 种子 JSON：WL 的 `WL_MBAL.params` 加 `"start_gate": "first_shift"`，`"version": 5`；SY38 的对应规则同样加并 `"version": 3`。

- [ ] **Step 4: 写升级脚本**

`scripts/upgrade_rule_sets_stage1.py`：

```python
"""One-shot: add M-01 start_gate to the active Phase-1 rule sets."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.rule_sets.service import active_rule_set, save_payload


def upgrade_site(site: str, db_path: Optional[Path] = None) -> bool:
    rule_set, _warnings = active_rule_set(site, db_path)
    if rule_set is None:
        return False
    payload = rule_set.to_dict()
    changed = False
    for rule in payload["rules"]:
        if rule["template"] != "material_balance":
            continue
        params = rule.setdefault("params", {})
        if params.get("start_gate") != "first_shift":
            params["start_gate"] = "first_shift"
            changed = True
    if not changed:
        return False
    payload["version"] = int(payload.get("version", 1)) + 1
    result = save_payload(
        payload, site=site,
        note="stage 1: M-01 FG waits for SF sufficiency",
        activate=True, db_path=db_path,
    )
    return bool(result.get("ok"))


def main() -> int:
    for site in ("WL", "SY38"):
        print(f"{site}: {'upgraded' if upgrade_site(site) else 'unchanged'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

（脚本是 CLI 入口，允许 `print`；D.3 #2 约束的是库内新增打印。）

- [ ] **Step 5: 运行测试**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_seed_upgrade_stage1 tests.test_material_start_gate tests.test_golden_baseline -v`
Expected: PASS。黄金基线用 baseline（`cumulative`）保持等价；`test_08-11` 显式规则集也没写 `start_gate`，默认 `cumulative`。

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast`
Expected: 全绿（19 处测试已迁移）

- [ ] **Step 6: 提交**

```bash
git add src/scheduler.py src/kernel/baseline.py src/server.py scripts/upgrade_rule_sets_stage1.py config/rule_sets tests run_tests.py
git commit -m "refactor(stage1): converge material_balance on the REG-01 ledger

Defect: REG-01 ledger existed only in the legacy path.
Expected: same plan with baseline cumulative gate; strict gate only via seed rule sets.
Actual: golden baseline unchanged; M-01 covered by test_material_start_gate."
```

---

### Task 7: 抽出 line resources 与 `slot_capacity_ms`（只增不删）

**Files:**
- Modify: `src/scheduler.py:2552-2670`
- Modify: `src/scheduler.py:133-305`（`slot_capacity_ms`）
- Create: `tests/test_line_resources.py`
- Modify: `run_tests.py:31-48`

**Interfaces:**
- Consumes: `pm_capacity`、`setup_reserve`、`_get_line_max_shift_ms`
- Produces:
  - `_create_line_resources(model, x, started) -> (overloads, opening, tails, loads)`
  - `ctx.slot_capacity_ms(line, slot) -> int`
  - `_add_line_capacity` 继续存在并调用新方法 + legacy 地板（Task 8 再删）

- [ ] **Step 1: 写失败测试**

`tests/test_line_resources.py`：

```python
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from src.data_loader import ProductionTask, RequirementPlan
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode


def _plan(pm_capacity=None):
    start = datetime(2026, 10, 12, 7)
    task = ProductionTask(
        task_id="FG1", stage="FG", line="PGLINKAGE", part_no="P1",
        description="P1", ct_seconds=60, trp=1, mpq=100, lot_size=100,
        lots=3, quantity=300, lot_millis=100 * 60 * 1000, order_id="O1",
        due_datetime=start + timedelta(days=3),
    )
    return RequirementPlan(
        fg_tasks=[task], sf_tasks=[], stock_movements=[], site="WL",
        pm_capacity=pm_capacity or {},
    )


class TestSlotCapacity(unittest.TestCase):
    def test_pm_partial_rate_halves_the_slot_ceiling(self):
        sched = APSScheduler(
            _plan({("PGLINKAGE", 0): 0.5}), calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL", shift_hours=8, shifts_per_day=3,
            disable_templates={"capacity_utilization", "material_balance"},
        )
        ctx = sched._compile_context_for_test()
        self.assertEqual(ctx.slot_capacity_ms("PGLINKAGE", 0), sched.shift_ms // 2)
        self.assertEqual(ctx.slot_capacity_ms("PGLINKAGE", 1), sched.shift_ms)

    def test_resources_exist_even_without_capacity_rules(self):
        sched = APSScheduler(
            _plan(), calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            shift_hours=8, shifts_per_day=3,
            disable_templates={"capacity_utilization", "material_balance"},
        )
        model, x, aux = sched._build_model("off", hard_due=False)
        self.assertTrue(aux["load"])
        self.assertTrue(aux["open"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_line_resources -v`
Expected: FAIL（`_compile_context_for_test` / `slot_capacity_ms` 不存在）

- [ ] **Step 3: 实现**

在 `_SchedulerCompileContext` 加：

```python
    def slot_capacity_ms(self, line, slot):
        max_line_ms = self._scheduler._get_line_max_shift_ms(line)
        reserve_ms = int(round(float(self._scheduler.setup_reserve.get((line, slot), 0.0)) * 60 * TIME_SCALE))
        factor = float(self._scheduler.pm_capacity.get((line, slot), 1.0) or 1.0)
        return max(1, int(round((max_line_ms - reserve_ms) * max(0.0, min(1.0, factor)))))
```

在 `APSScheduler` 加仅供测试的构造器：

```python
    def _compile_context_for_test(self):
        return _SchedulerCompileContext(self, None, {}, {}, {}, {}, {}, {}, {}, None)
```

把 `_legacy_capacity_body` 拆成 `_create_line_resources`（变量创建，`2578-2643`）+ `_apply_legacy_floor`（`2645-2669`）。`_create_line_resources` 返回 `(overloads, opening, tails, loads)`，内部保留 `slot_max`、`setup_ms`、floor-count、tail 唯一性、overload 变量；**不再**接收/返回 `utilization_mode`、`gap_cap`。`_add_line_capacity` 改为：

```python
        overloads, opening, tails, loads = self._create_line_resources(model, x, started)
        shortfall, violations = self._apply_legacy_floor(
            model, opening, tails, loads, "off" if kernel_capacity else utilization_mode, gap_cap
        )
```

- [ ] **Step 4: 运行测试**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_line_resources tests.test_pm_partial_capacity tests.test_golden_baseline -v`
Expected: PASS（行为未变）

- [ ] **Step 5: 提交**

```bash
git add src/scheduler.py tests/test_line_resources.py run_tests.py
git commit -m "refactor(capacity): split line resources from the legacy floor and expose slot capacity"
```

---

### Task 8: 收敛 `capacity_utilization`（删 `_legacy_capacity_body`）

**Files:**
- Modify: `src/scheduler.py:1359-1361`（资源调用）、`1593-1613`（目标装配）、`649-671`（`_tiers`）、`924-964`（`_compact`）、`2552-2670`（删除）
- Modify: `src/kernel/baseline.py`
- Modify: `src/server.py`（复选框映射 + 构造参数）
- Modify: `tests/test_hard_capacity_strict.py`（去掉 `use_kernel_capacity`）
- Modify: 所有传 `enforce_utilization_95` 的测试（26 处）
- Modify: `run_tests.py:31-48`

**Interfaces:**
- Consumes: Task 7 的 resources 与 `slot_capacity_ms`
- Produces: `CONVERGED_TEMPLATES = {"material_balance", "capacity_utilization"}`；容量 rule 的 nature 决定 tier；不再有 `use_kernel_capacity`、`enforce_utilization_95`、`_legacy_capacity_body`、`_add_line_capacity`、`_compile_capacity_kernel`

- [ ] **Step 1: 写失败测试**

在 `tests/test_hard_capacity_strict.py` 末尾追加：

```python
class TestTiersFollowRuleNature(unittest.TestCase):
    def test_soft_capacity_rule_yields_soft_tiers(self):
        from datetime import datetime, timedelta
        from src.data_loader import ProductionTask, RequirementPlan
        from src.kernel.types import GroupMode, Nature, RuleGroup, RuleInstance, RuleSet, Scope
        from src.scheduler import APSScheduler
        from src.shift_manager import CalendarMode

        start = datetime(2026, 10, 12, 7)
        task = ProductionTask(
            task_id="T1", stage="FG", line="PGLINKAGE", part_no="P1",
            description="P1", ct_seconds=60, trp=1, mpq=50, lot_size=50,
            lots=2, quantity=100, lot_millis=2 * 3600 * 1000, order_id="O1",
            due_datetime=start + timedelta(days=3),
        )
        plan = RequirementPlan(fg_tasks=[task], sf_tasks=[], stock_movements=[], site="WL")
        rule_set = RuleSet(
            "TEST_SOFT_CAP", "WL", 1,
            [RuleGroup("G2_CAPACITY", 20, GroupMode.LEXICOGRAPHIC)],
            [RuleInstance("CAP_SOFT", "capacity_utilization", Scope(), Nature.SOFT, 100,
                          "G2_CAPACITY", 20,
                          {"min_util": 0.95, "max_util": 1.0, "tail_exempt": True})],
        )
        sched = APSScheduler(
            plan, start_datetime=start, planning_days=5,
            calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            shift_hours=8, shifts_per_day=3,
            disable_templates={"due_date", "material_balance"},
            rule_set=rule_set,
        )
        self.assertTrue(all(tier[1] == "soft" for tier in sched._tiers()))
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_hard_capacity_strict -v`
Expected: FAIL（soft 规则仍产生 hard tier）

- [ ] **Step 3: 实现**

1. `_build_model`：

```python
        overloads, opening, tails, loads = self._create_line_resources(model, x, started)
        kernel_objective = None
        if utilization_mode != "off":
            compiled = self._compile_rules_for(
                "capacity_utilization", model, x, active, started, end_of, opening, tails, loads,
                soften=utilization_mode == "soft" and self.allow_soft_capacity_fallback,
            )
            if compiled.book.terms:
                kernel_objective = compiled.total_objective()
```

`_compile_rules_for` 增加 `soften: bool = False`，`force_hard` 保留到 Task 14：

```python
        if force_hard:
            rules = [replace(rule, nature=Nature.HARD) for rule in rules]
        elif soften:
            rules = [replace(rule, nature=Nature.SOFT) for rule in rules]
```

2. 目标装配删除 `elif violations:` 整块（`1598-1604`），`overloads` 段保留。

3. `_tiers` 重写：

```python
    def _tiers(self):
        capacity_rules = self._rules_for("capacity_utilization")
        if not capacity_rules:
            return (("关闭 95% 利用率约束", "off", self.due_hard, 1.0),)
        hard = any(
            rule.nature is Nature.HARD
            and float(rule.params.get("min_util", 0.0) or 0.0) >= 0.95 - 1e-9
            for rule in capacity_rules
        )
        if hard and not self.allow_soft_capacity_fallback:
            return (("硬约束 95% 利用率", "hard", self.due_hard, 0.70),)
        if hard:
            return (
                ("硬约束 95% 利用率", "hard", self.due_hard, 0.70),
                ("软约束 95% 利用率 + 硬约束准时交付", "soft", True, 0.25),
                ("软约束 95% 利用率 + 交付损失最小化", "soft", False, 1.0),
            )
        tiers = []
        if self.due_hard:
            tiers.append(("软约束 95% 利用率 + 硬约束准时交付", "soft", True, 0.25))
        tiers.append(("软约束 95% 利用率 + 交付损失最小化", "soft", False, 1.0))
        return tuple(tiers)
```

`self.due_hard` 在 Task 13 之前用 `self.enforce_on_time_delivery` 实现：

```python
    @property
    def due_hard(self):
        return self.enforce_on_time_delivery
```

`__init__` 中 `strict_capacity_hard` 的推导保留（`test_hard_capacity_strict` 仍断言它），但 `_tiers` 不再直接使用它。

4. `_compact` 的 `rungs` 只保留硬档：

```python
        rungs = (("硬约束 95%", "hard", None, 0.70),)
```

5. 删除 `_legacy_capacity_body`、`_add_line_capacity`、`_compile_capacity_kernel`；`_create_line_resources` 保留。删除 `use_kernel_capacity` 参数与赋值；`tests/test_hard_capacity_strict.py` 与 `tests/test_golden_baseline.py::test_08` 移除该参数，改由规则集自动激活（`kernel_templates` 显式传入仍可用）。

6. baseline 加容量规则：

```python
        RuleInstance(
            "BASELINE_CAP", "capacity_utilization", Scope(), Nature.HARD, 100,
            "G2_CAPACITY", 20,
            {"min_util": 0.95, "max_util": 1.0, "tail_exempt": True,
             "setup_minutes_per_start": 0.0},
        ),
```

`CONVERGED_TEMPLATES` 加 `"capacity_utilization"`。

7. 测试迁移：`enforce_utilization_95=False` → `disable_templates={"capacity_utilization"}`（与其它项合并）。

- [ ] **Step 4: 运行测试**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_hard_capacity_strict tests.test_line_resources tests.test_pm_partial_capacity tests.test_lateness_window tests.test_golden_baseline -v`
Expected: PASS

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast`
Expected: 全绿（26 处已迁移）

- [ ] **Step 5: 提交**

```bash
git add src/scheduler.py src/kernel/baseline.py src/server.py tests run_tests.py
git commit -m "refactor(stage1): converge capacity_utilization and delete the legacy capacity body

Defect: A2 dual-track capacity implementation.
Expected: identical compliance on the golden baseline; soft rules stay soft.
Actual: below-95 count unchanged on the fixture; _legacy_capacity_body gone."
```

---

### Task 9: 收敛 `pm_block`

**Files:**
- Modify: `src/kernel/compiler.py:383-397`
- Modify: `src/scheduler.py:1439-1461`（删除 legacy PM 块）、`309-373`/`1440-1445`（删开关）
- Modify: `src/kernel/baseline.py`
- Modify: `src/server.py:2120-2148`、`2455-2522`
- Modify: `tests/test_pm_partial_capacity.py`（`enforce_pm_maintenance=False` → `disable_templates`）
- Modify: `run_tests.py:31-48`

**Interfaces:**
- Consumes: `ctx.load(line, slot)`、`ctx.capacity_ms(line)`、`ctx.pm_slots(line)`
- Produces: `CONVERGED_TEMPLATES` 加 `"pm_block"`；`capacity_rate>0` 时以 load 上限表达，不冻结任务

- [ ] **Step 1: 写失败测试**

`tests/test_pm_partial_capacity.py` 增加一个规则集版本：

```python
    def test_kernel_pm_rule_with_partial_rate_caps_the_shift_load(self):
        start = datetime(2026, 10, 12, 7)
        task = ProductionTask(
            task_id="FG1", stage="FG", line="PGLINKAGE", part_no="P1",
            description="P1", ct_seconds=60, trp=1, mpq=100, lot_size=100,
            lots=3, quantity=300, lot_millis=100 * 60 * 1000, order_id="O1",
            due_datetime=start + timedelta(days=3),
        )
        plan = RequirementPlan(fg_tasks=[task], sf_tasks=[], stock_movements=[], site="WL")
        from src.kernel.types import GroupMode, Nature, RuleGroup, RuleInstance, RuleSet, Scope
        rule_set = RuleSet(
            "PM_RATE", "WL", 1,
            [RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD)],
            [RuleInstance("PM", "pm_block", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0,
                          {"handling": "avoid", "capacity_rate": 0.5})],
        )
        sched = APSScheduler(
            plan, start_datetime=start, planning_days=5,
            calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            shift_hours=8, shifts_per_day=3,
            disable_templates={"capacity_utilization", "material_balance", "due_date"},
            pm_shifts={"PGLINKAGE": [0]},
            rule_set=rule_set,
        )
        result = sched.solve(time_limit_seconds=10.0)
        blocks = result.tasks[0].shift_breakdown
        self.assertLessEqual(blocks.get("10/12 早班", 0), 150)  # 50% of 300
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_pm_partial_capacity -v`
Expected: FAIL（当前 kernel PM 只做 `x==0`，`capacity_rate` 被忽略；legacy 在 rule_set 路径下不再生效）

- [ ] **Step 3: 实现**

`_compile_pm_block` 替换为：

```python
def _compile_pm_block(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    warnings: List[str] = []
    rate = float(params.get("capacity_rate", 0.0) or 0.0)
    if str(params.get("handling", "avoid")) != "avoid":
        warnings.append(f"{rule.rule_id}: handling={params.get('handling')} 暂按 avoid 处理")
    rate = max(0.0, min(1.0, rate))
    for line in ctx.lines():
        slots = [int(s) for s in ctx.pm_slots(line)]
        if not slots:
            continue
        ceiling = int(rate * ctx.capacity_ms(line))
        for slot in slots:
            if 0 <= slot < ctx.num_shifts:
                ctx.model.Add(ctx.load(line, slot) <= ceiling)
    return warnings, True
```

调度器删除 legacy PM 块（`1446-1457`）与 `kernel_pm` 守卫，直接：

```python
        if self._use_kernel("pm_block"):
            self._compile_rules_for(
                "pm_block", model, x, active, started, end_of, opening, tails, loads
            )
```

从 `__init__`、legacy 反向映射、服务器构造中删除 `enforce_pm_maintenance`；服务器在 R-PM-01/R-WL-27 都关闭时加 `disabled_templates.add("pm_block")`。

baseline 加：

```python
        RuleInstance("BASELINE_PM", "pm_block", Scope(), Nature.HARD, 1,
                     "G0_PHYSICAL", 0, {"handling": "avoid", "capacity_rate": 0.0}),
```

`CONVERGED_TEMPLATES` 加 `"pm_block"`。

- [ ] **Step 4: 运行测试**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_pm_partial_capacity tests.test_pm_window tests.test_golden_baseline -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/kernel/compiler.py src/scheduler.py src/kernel/baseline.py src/server.py tests run_tests.py
git commit -m "refactor(stage1): converge pm_block with a load ceiling for partial capacity"
```

---

### Task 10: 收敛 `tooling_exclusivity`

**Files:**
- Modify: `src/scheduler.py:1394-1412`
- Modify: `src/kernel/baseline.py`
- Modify: `src/server.py`（复选框映射/构造）
- Modify: 测试中的 `enforce_tooling_exclusivity`
- Modify: `run_tests.py:31-48`

**Interfaces:**
- Consumes: kernel `_compile_tooling`（已实现，`compiler.py:314`）
- Produces: `CONVERGED_TEMPLATES` 加 `"tooling_exclusivity"`

- [ ] **Step 1: 写失败测试**

`tests/test_stage1_contract.py` 追加：

```python
class TestConvergedTemplatesGrow(unittest.TestCase):
    def test_tooling_is_converged(self):
        from src.kernel.baseline import CONVERGED_TEMPLATES
        self.assertIn("tooling_exclusivity", CONVERGED_TEMPLATES)
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_stage1_contract -v`
Expected: FAIL

- [ ] **Step 3: 实现**

删除 `kernel_tooling` 与 legacy 模具分组块（`1394-1408`），保留：

```python
        if self._use_kernel("tooling_exclusivity"):
            self._compile_rules_for(
                "tooling_exclusivity", model, x, active, started, end_of, opening, tails, loads
            )
```

删除 `enforce_tooling_exclusivity`（签名/赋值/反向映射/服务器）。baseline 加：

```python
        RuleInstance("BASELINE_TOOL", "tooling_exclusivity", Scope(), Nature.HARD, 1,
                     "G0_PHYSICAL", 0, {"resource": "mold", "available_qty": 1}),
```

`CONVERGED_TEMPLATES` 加 `"tooling_exclusivity"`；服务器在 R-TL-01/R-WL-01/R-SY-03 全关时禁用该模板。

- [ ] **Step 4: 运行测试**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_stage1_contract tests.test_golden_baseline -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/scheduler.py src/kernel/baseline.py src/server.py tests run_tests.py
git commit -m "refactor(stage1): converge tooling_exclusivity"
```

---

### Task 11: 收敛 `material_ready`

**Files:**
- Modify: `src/scheduler.py:1424-1437`
- Modify: `src/kernel/baseline.py`
- Modify: `src/server.py`
- Modify: 测试中的 `enforce_material_arrival`
- Modify: `run_tests.py:31-48`

**Interfaces:**
- Consumes: kernel `_compile_material_ready`（`compiler.py:340`）
- Produces: `CONVERGED_TEMPLATES` 加 `"material_ready"`

- [ ] **Step 1: 写失败测试**

`tests/test_stage1_contract.py` 的 `TestConvergedTemplatesGrow` 加：

```python
    def test_material_ready_is_converged(self):
        from src.kernel.baseline import CONVERGED_TEMPLATES
        self.assertIn("material_ready", CONVERGED_TEMPLATES)
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_stage1_contract -v`
Expected: FAIL

- [ ] **Step 3: 实现**

删除 `kernel_ready` 与 legacy 到料块（`1426-1433`），保留：

```python
        if self._use_kernel("material_ready"):
            self._compile_rules_for(
                "material_ready", model, x, active, started, end_of, opening, tails, loads
            )
```

删除 `enforce_material_arrival`；baseline 加：

```python
        RuleInstance("BASELINE_READY", "material_ready", Scope(), Nature.HARD, 1,
                     "G0_PHYSICAL", 0, {"source": "PO", "qc_release_hours": 2.0}),
```

`CONVERGED_TEMPLATES` 加 `"material_ready"`；服务器在 R-MT-01/R-WL-21 全关时禁用。

- [ ] **Step 4: 运行测试**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_stage1_contract tests.test_golden_baseline -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/scheduler.py src/kernel/baseline.py src/server.py tests run_tests.py
git commit -m "refactor(stage1): converge material_ready"
```

---

### Task 12: 收敛 `min_batch`

**Files:**
- Modify: `src/scheduler.py:1187, 1226-1228, 1241-1243, 1323-1328`
- Modify: `src/kernel/baseline.py`
- Modify: `src/server.py`
- Modify: 测试中的 `enforce_mpq_batch`
- Modify: `run_tests.py:31-48`

**Interfaces:**
- Consumes: kernel `_compile_min_batch`（`compiler.py:210`）
- Produces: `CONVERGED_TEMPLATES` 加 `"min_batch"`；`min_lots` 域裁剪只在规则启用时生效

- [ ] **Step 1: 写失败测试**

`tests/test_stage1_contract.py`：

```python
    def test_min_batch_is_converged(self):
        from src.kernel.baseline import CONVERGED_TEMPLATES
        self.assertIn("min_batch", CONVERGED_TEMPLATES)
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_stage1_contract -v`
Expected: FAIL

- [ ] **Step 3: 实现**

1. `kernel_min_batch = use_kernel and self._use_kernel("min_batch")`。
2. `min_lots` 计算改为：

```python
            if not self._use_kernel("min_batch"):
                min_lots = 1
            elif self.sf_mpq_tail_enabled and task.stage == "SF":
                min_lots = 1
            else:
                min_lots = 1 if splittable else min(per_shift, task.mpq // task.lot_size)
```

3. 删除 `if not kernel_min_batch:` 两处守卫（`1226`、`1241`），约束恒由 kernel 添加；保留 `min_lots` 的域裁剪。
4. 删除 `force_hard=True` 调用中的硬编码（Task 14 统一删参数），此处先改为无 `force_hard`。
5. baseline 加两条：

```python
        RuleInstance("BASELINE_MB_FG", "min_batch", Scope(stage="FG"), Nature.HARD, 1,
                     "G0_PHYSICAL", 0, {"unit": "MPQ", "basis": "shift"}),
        RuleInstance("BASELINE_MB_SF", "min_batch", Scope(stage="SF"), Nature.HARD, 1,
                     "G0_PHYSICAL", 0, {"unit": "piece", "basis": "shift"}),
```

`CONVERGED_TEMPLATES` 加 `"min_batch"`；删除 `enforce_mpq_batch` 与服务器映射（R-FG-02/R-SF-09/R-WL-09/R-WL-15 全关时禁用 `min_batch`）。

- [ ] **Step 4: 运行测试**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_stage1_contract tests.test_golden_baseline tests.test_split_batch -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/scheduler.py src/kernel/baseline.py src/server.py tests run_tests.py
git commit -m "refactor(stage1): converge min_batch"
```

---

### Task 13: 收敛 `sequence` / `due_date` / `concurrency`

**Files:**
- Modify: `src/scheduler.py:1365-1392`（sequence）、`1463-1468` + `1494-1511`（due 硬路径）、`1536-1571`（concurrency）
- Modify: `src/kernel/baseline.py`
- Modify: `src/server.py`
- Modify: 测试中的三个开关
- Modify: `run_tests.py:31-48`

**Interfaces:**
- Consumes: kernel `_compile_sequence` / `_compile_due_date` / `_compile_concurrency`
- Produces: `CONVERGED_TEMPLATES` 最终为 9 个模板；`due_hard` 成为属性（规则驱动）；软交期 legacy `late` 循环保留（R6）

- [ ] **Step 1: 写失败测试**

`tests/test_stage1_contract.py`：

```python
    def test_all_seven_stage1_templates_are_converged(self):
        from src.kernel.baseline import CONVERGED_TEMPLATES
        self.assertEqual(
            set(CONVERGED_TEMPLATES),
            {
                "material_balance", "capacity_utilization", "pm_block",
                "tooling_exclusivity", "material_ready", "min_batch",
                "sequence", "due_date", "concurrency",
            },
        )

    def test_due_hard_is_rule_driven(self):
        from datetime import datetime, timedelta
        from src.data_loader import ProductionTask, RequirementPlan
        from src.kernel.types import GroupMode, Nature, RuleGroup, RuleInstance, RuleSet, Scope
        from src.scheduler import APSScheduler
        from src.shift_manager import CalendarMode

        start = datetime(2026, 10, 12, 7)
        task = ProductionTask(
            task_id="FG1", stage="FG", line="PGLINKAGE", part_no="P1",
            description="P1", ct_seconds=60, trp=1, mpq=100, lot_size=100,
            lots=1, quantity=100, lot_millis=100 * 60 * 1000, order_id="O1",
            due_datetime=start + timedelta(days=2),
        )
        plan = RequirementPlan(fg_tasks=[task], sf_tasks=[], stock_movements=[], site="WL")
        soft = RuleSet(
            "SOFT_DUE", "WL", 1,
            [RuleGroup("G1_DELIVERY", 10, GroupMode.WEIGHTED)],
            [RuleInstance("DUE", "due_date", Scope(stage="FG"), Nature.SOFT, 1,
                          "G1_DELIVERY", 10, {"mode": "soft"})],
        )
        sched = APSScheduler(
            plan, calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            disable_templates={"capacity_utilization", "material_balance"},
            rule_set=soft,
        )
        self.assertFalse(sched.due_hard)
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_stage1_contract -v`
Expected: FAIL

- [ ] **Step 3: 实现**

1. `due_hard` 属性替换 `enforce_on_time_delivery`：

```python
    @property
    def due_hard(self):
        return any(
            rule.nature is Nature.HARD and str(rule.params.get("mode", "hard")) == "hard"
            for rule in self._rules_for("due_date")
        )
```

2. 删除 `kernel_sequence` / legacy 顺位块，保留：

```python
        if self._use_kernel("sequence") and not self.assign_in_model:
            self._compile_rules_for(
                "sequence", model, x, active, started, end_of, opening, tails, loads
            )
```

3. due 硬路径：

```python
        kernel_due = use_kernel and self._use_kernel("due_date") and self.due_hard
        if kernel_due:
            self._compile_rules_for(
                "due_date", model, x, active, started, end_of, opening, tails, loads
            )
```

软交期 legacy 循环保留，条件改为 `if not kernel_due:`（原 `if hard_due:` 分支内部的 `model.Add(end_of <= due)` 删除，只保留 `continue`）。`enforce_customer_priority` 与 lateness 权重不动。

4. 删除 `kernel_concurrency` 与 legacy 并发块，保留：

```python
        if self._use_kernel("concurrency"):
            compiled_concurrency = self._compile_rules_for(
                "concurrency", model, x, active, started, end_of, opening, tails, loads
            )
            if compiled_concurrency.book.terms:
                group_terms["G3_CHANGEOVER"].append(
                    compiled_concurrency.total_objective(scale=CONCURRENCY_WEIGHT)
                )
```

5. baseline 加：

```python
        RuleInstance("BASELINE_SEQ", "sequence", Scope(), Nature.HARD, 1,
                     "G0_PHYSICAL", 0, {"order_by": "due", "tolerance_shifts": 0}),
        RuleInstance("BASELINE_DUE", "due_date", Scope(stage="FG"), Nature.HARD, 1,
                     "G1_DELIVERY", 10, {"mode": "hard"}),
        RuleInstance("BASELINE_CC", "concurrency", Scope(), Nature.HARD, 100,
                     "G3_CHANGEOVER", 30, {"max_models": 3, "window": "shift"}),
```

`CONVERGED_TEMPLATES` 加三者。删除 `enforce_sequential_pulling` / `enforce_on_time_delivery` / `enforce_concurrency_limit`；服务器在对应旧规则全关时把模板加入 `disabled_templates`。

- [ ] **Step 4: 运行测试**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_stage1_contract tests.test_golden_baseline tests.test_lateness_window tests.test_hard_capacity_strict -v`
Expected: PASS

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_golden_baseline -v`
Expected: 1-11 全 PASS；记录每个 kernel 用例的实际指标，写进提交信息。

- [ ] **Step 5: 提交**

```bash
git add src/scheduler.py src/kernel/baseline.py src/server.py tests run_tests.py
git commit -m "refactor(stage1): converge sequence, hard due_date and concurrency

Defect: A2 dual-track for the last three structural templates.
Expected: identical task set and compliance on the golden fixture.
Actual: no fixture change; OTD and below-95 metrics recorded in tests."
```

---

### Task 14: 删除 `force_hard`、激活契约检查、阶段验收

**Files:**
- Modify: `src/scheduler.py:1780-1815`（删除 `force_hard`/`soften`）、`1173`（`use_kernel` 参数）、`1045-1061`（hint）
- Modify: `tests/test_rule_contract.py:43-44`、`tests/test_stage1_contract.py`
- Modify: `Specification/aps_rule_set_redesign.md`（只回写验收 1.2 的实际命令，不改设计结论）
- Modify: `run_tests.py:31-48`

**Interfaces:**
- Consumes: 前 13 个任务
- Produces: 源码中 `force_hard` 归零；9 个 `enforce_*` 归零；契约 #10 正式执行

- [ ] **Step 1: 写失败测试**

`tests/test_rule_contract.py`：

```python
    def test_10_no_force_hard_escape_hatch(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "src" / "scheduler.py").read_text(encoding="utf-8")
        self.assertNotIn("force_hard", source)
```

`tests/test_stage1_contract.py`：

```python
    def test_only_deferred_switches_remain(self):
        data = json.loads(REGISTER.read_text(encoding="utf-8"))
        self.assertEqual(_present_switches(), set(data["deferred"]))

    def test_force_hard_is_gone_everywhere(self):
        offenders = [
            str(path) for path in (ROOT / "src").rglob("*.py")
            if "force_hard" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_rule_contract tests.test_stage1_contract -v`
Expected: FAIL（`force_hard` 仍在）

- [ ] **Step 3: 实现**

1. `_compile_rules_for` 删除 `force_hard` 与 `soften` 形参、`replace` 导入与两段改写；所有调用点去掉关键字。
2. `_build_model` 删除 `use_kernel` 形参（恒为真）；`_solve_tier`/`_compact` 调用同步。
3. `_build_hint` 改为：

```python
    def _build_hint(self, time_limit_seconds: float) -> Optional[Dict[Tuple[str, int], int]]:
        model, x, _aux = self._build_model("off", hard_due=False)
        model.Minimize(0)
        solver = self._solver(time_limit_seconds)
        status = solver.Solve(model)
        self._hint_infeasible = status == cp_model.INFEASIBLE
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        return {key: solver.Value(var) for key, var in x.items()}
```

4. 在规格 8.3 的验收表 1.2 处只把命令改成登记表版本（不改变设计结论）：

```markdown
| 1.2 | 9 个阶段 1 开关归零；其余 13 个已登记且无未登记新增 | `python -m unittest tests.test_stage1_contract` |
```

- [ ] **Step 4: 验收命令逐条跑**

```bash
grep -rn "force_hard" src/ tests/ ; echo "exit=$?"
grep -rno "self\.enforce_[a-z0-9_]*" src/scheduler.py | sort -u
grep -n "_legacy_capacity_body\|_legacy_material_balance" src/scheduler.py ; echo "exit=$?"
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py
```

Expected:
- `force_hard` 无命中（exit=1）
- 只剩 13 个 deferred 开关
- 两个 legacy 方法无命中
- fast 退出码 0
- 全量退出码 0

- [ ] **Step 5: 基线审计**

```bash
git log --oneline -- tests/fixtures/wl_10d_baseline.json
git diff ca9f0c2..HEAD --stat -- tests/fixtures/wl_10d_baseline.json
```

Expected: 若 fixture 无变更，记录「no baseline change: all parity tests green」；若有变更，提交信息必须含「缺陷编号 + 预期方向 + 实际结果」，否则回退该变更并修代码。

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "refactor(stage1): remove force_hard and activate contract check 10

Defect: A2 force_hard ignored rule nature at six call sites.
Expected: no source hit, 9 switches gone, 13 registered, full suite green.
Actual: <paste the five acceptance command outcomes here>."
```

**禁止 `git push`。**

---

## Self-Review

**规格覆盖**

| 规格条目 | 对应任务 |
|---|---|
| 8.3 #1 material_balance（REG-01 移植 + 删 legacy） | Task 5、6 |
| 8.3 #2 capacity（删 `_legacy_capacity_body`） | Task 7、8 |
| 8.3 #3 pm_block | Task 9 |
| 8.3 #4 tooling | Task 10 |
| 8.3 #5 material_ready | Task 11 |
| 8.3 #6 min_batch | Task 12 |
| 8.3 #7 sequence / due_date / concurrency | Task 13 |
| 验收 1.1 / 1.3 / 1.4 / 1.5 / 1.6 / 1.7 | Task 14 + 各收敛任务 |
| 验收 1.2（收窄，R1） | Task 1、14 |
| 6.4 DataGap 编译期处置（`fail/warn/skip`） | Task 3、4（T1 拒绝）、Task 5（降级告警） |
| 3.4 缺口是一等公民 | Task 3、4 |
| 附录 B HC-06 / REG-01 | Task 5 |
| 用户新规则 M-01 | Task 5（实现）、Task 6（生产默认）、Task 6 升级脚本 |
| 用户「数据只是部分内容」 | Task 3、4、5 的降级路径 + R8 |
| D.3 #2 无新增 print | Task 3（存量 print 改列表） |
| D.3 #3 冻结第 3 层契约 | 全计划未改 `RequirementPlan` / `ProductionInputSnapshot` |
| 8.1 基线书面理由 | Task 6/8/13/14 的提交模板 |

**占位符扫描**：无 TBD/TODO/「类似 Task N」；每个删除步骤都给了符号与块边界；每个测试都给了可运行代码。

**类型一致性**：`start_gate` 在两个任务中都是字符串枚举；`baseline_rule_set` 返回 `RuleSet`；`CONVERGED_TEMPLATES` 始终是 `frozenset[str]`；`_create_line_resources` 返回四元组并在 Task 8 的唯一调用点解包；`rule_warnings` 在 `ScheduleResult`、调度器属性、服务器响应三处同名。

**已知风险（实施时优先观察）**

1. Task 8 删除软回退后，某些既有测试可能从「软 tier 可行」变成「硬 tier 无解」。处理顺序：先确认该测试是否真的需要容量约束；需要则加 `allow_soft_capacity_fallback=True`，不需要则 `disable_templates={"capacity_utilization"}`。不得恢复 legacy body。
2. Task 13 的 baseline 同时启用 sequence + concurrency + hard due，黄金基线可能因约束顺序改变而移动个别 lot；只要任务数、总数量、OTD、below-95 四项不变即视为通过。
3. Task 6 的种子脚本必须在本地 SQLite 上显式运行一次，否则服务器仍用旧 payload（`ensure_seed` 不会覆盖 active）。运行命令：
   `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m scripts.upgrade_rule_sets_stage1`
4. M-01 的 `first_shift` 在硬交期下可能让部分订单无法按期交付。预期方向是「FG 后移、OTD 下降」；若 OTD 降到业务不可接受，业务侧把种子参数改为 `cumulative`，代码不动。
