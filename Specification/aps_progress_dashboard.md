# APS MVP 整体进度与完成度

> **更新日期**：2026-10-30
> **结论**：Phase 0 已闭环；Phase 1 规则内核可用且调度器算子全量接线；Phase B 大部分完成；Phase C 增量子集（班内序列数值化、软库存、提前期、Campaign、路线预指派）已落地。**整体处于快速收敛中**。
> **一句话**：系统可跑通 WL/SY38 排产、17 项校验、规则集编辑、严格模板上传；班内 sequence、软库存、C4 提前期、C2-b-0 路线分配、C5-a Campaign 已落地；主模型内 Assign、库存硬约束、Pull/Slack 仍待完善。

---

## 1. 总进度

| 阶段 | 估算完成度 | 结论 |
|---|---:|---|
| Phase 0：正确性止血 | **100%** | 已闭环，含 ACT-06 拆批连续 |
| Phase 1：规则内核化 | **100%** | 19 模板、18 个已编译且在调度器全量接线闭环；解除旧布尔门控；routing 规划于 Phase 2 |
| Phase B：32 规则数据/算子扩展 | **约 75%** | MOQ/MPQ、软产能、时间窗、冻结、scrap、库存策略数据层与软库存已接线；B4 硬上限可选 |
| Phase C：时间/路由/序列 | **约 60%** | C1-b-3/4、C2-a/C2-b-a/C2-b-0、C4、C5-a 已做；主模型内 Assign、Pull/Slack 未做 |
| **整体** | **约 85%** | 可演示、可基础测试；规则内核 18 算子已实现端到端闭环接入 |

---

## 2. 当前版本与测试

| 项 | 当前值 |
|---|---|
| 分支 | `feat/shaojing-dev` |
| WL active | `WL_PHASE1 v4`，13 条拆分规则 |
| SY38 active | `SY38_PHASE1 v2`，13 条 |
| 内核模板 | 19 个；18 个已编译且调度器全量接线；`routing` 未编译（Phase 2） |
| 全量测试 | **111/111 PASS**（含 105 基线 + `test_operator_wiring.py` 6 项） |
| 快速测试 | **67/67 PASS，约 61s** |
| 服务 | `http://127.0.0.1:8000`（单实例开发服） |

### 测试套件

| 套件 | 项数 | 状态 |
|---|---:|---|
| `tests/test_domain.py` | 4 | PASS |
| `tests/test_kernel.py` | 26 | PASS |
| `tests/test_independent_audit.py` | 6 | PASS |
| `tests/test_golden_baseline.py` | 13 | PASS |
| `tests/test_split_batch.py` | 2 | PASS |
| `tests/test_rule_sets.py` | 9 | PASS |
| `tests/test_labor.py` | 5 | PASS |
| `tests/test_aps_pipeline.py` | 8 | PASS |
| `tests/test_regressions.py` | 8 | PASS |
| `tests/test_sequence.py` | 2 | PASS |
| `tests/test_scrap.py` | 1 | PASS |
| `tests/test_routing.py` | 5 | PASS |
| `tests/test_rule_catalog.py` | 3 | PASS |
| `tests/test_inventory_policy.py` | 4 | PASS |
| `tests/test_capacity_kernel.py` | 7 | PASS |
| `tests/test_operator_wiring.py` | 6 | PASS |
| `tests/test_campaign.py` | 2 | PASS |
| **全量** | **111** | **PASS** |

---

## 3. 32 条规则状态

### Phase B

| 项 | 状态 | 说明 |
|---|---|---|
| B1 MOQ/MPQ | ✅ 代码/数据 | `basis=shift/order`、`quantity_source=MPQ/MOQ`、`tail_exempt`、`moq_override`；尚未写入 active seed |
| B2 scrap | ✅ opt-in | `build_requirement_plan(apply_scrap=True)`，默认关闭 |
| B3 软产能 | ✅ | soft 双边计罚，不再硬加 ceiling |
| B4 库存策略 | 🟡 部分 | 数据层 + 逐班投影 + soft/hard 时段库存上限惩罚约束 |
| B5 时间窗/冻结 | ✅ | `allowed_dates`；`freeze.use_baseline` |
| B6 目录/site override | ✅ 数据层 | 32 条目录 + `effective_for_site()`；尚未完全替换旧审计映射 |

### Phase C

| 项 | 状态 | 说明 |
|---|---|---|
| C1-a 序列原语 | ✅ | 同模优先、SDST 矩阵、production/setup/capacity 审计 |
| C1-b-lite setup 占容量 | ✅ | `setup_minutes_per_start` |
| C1-b-3 班内 sequence + start/end | ✅ 可选 | `sequence_setup=True` 时构建 `first/last/trans` + MTZ + `start/end/setup`；Gantt 页展示 sequence/setup；默认关闭 |
| C1-b 跨班 carry-over | ✅ 可选 | 上一班 last mold → 下一班 first mold |
| C1-b-4 技术员 setup 区间 | ✅ 可选 | `setup_technicians_available` + `AddCumulative`；技术员数按 Setup-Matrix 边需求；过小会不可行 |
| C2-a Routing 数据层 | ✅ | 候选线/机台、认证、优先级 |
| C2-b-a 路线重指派 | ✅ 启发式 | 主线过载移备线；已接站点开关与 `/api/solve.routing_decisions`；非 CP-SAT Assign |
| C2-b-0 CP-SAT 路线分配阶段 | ✅ 可选 | `solve_routing_assignment()` 独立 CP-SAT 选址 + 容量；仍未与主排产模型联立 |
| C2-b CP-SAT Assign | ❌ | 未实现 |
| C3-lite 技术员容量 | ✅ 近似 | 按班次 run start 数限制 |
| C4 Operation LeadTime/Transfer | ✅ 软约束 | `lead_time_days` + setup/queue/inspection/move + transfer batch 次数已接 soft gap；仍非机台级硬排程 |
| C5 Campaign/Pull/Slack | 🟡 部分 | Campaign Group + 交期 Slack 软惩罚已接；Pull 策略未独立实现 |

---

## 4. 今日（2026-10-30）完成的关键收口

- C1-b-3：`seq_start/seq_end/seq_setup` + `sequence_plan` API；Gantt 页新增 sequence/setup 明细。
- C1-b-4：setup 技术员区间 `AddCumulative`，站点设置 `setup_technicians_available`。
- C2-b-0：`solve_routing_assignment()` 独立 CP-SAT 路线分配阶段 + `routing_decisions` API。
- C4-a：`lead_time_days` 软提前期 + `leadtime_enabled`。
- C5-a：Campaign Group 软约束与测试用例。
- C5-b：交期 Slack 软约束。
- 算子接线断层消除与门控解耦闭环。

---

## 5. 仍待完成的事项（按优先级）

1. **C1-b 生产化**：已有可选 `start/end/setup`、Gantt 明细、按边技术员人数；仍需真实 Setup-Matrix、默认开关策略。
2. **C2-b 主模型内 Assign(i,m)**：已有上游 CP-SAT 分配阶段，但尚未与主排产模型联立。
3. **B4-b 真实库存策略对接**：需真实 Inventory-Policy.csv 与发货日历。
4. **C4 完整版**：Operation-LeadTime 的 queue/inspection/transfer batch 进入求解器。
5. **C5 Pull/Slack 策略**（Campaign 分组已做）。
6. **32 规则正式审计映射**：淘汰旧 17 项写死阈值，全面对接 RuleSet。
