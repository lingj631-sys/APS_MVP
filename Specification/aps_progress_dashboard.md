# APS MVP 整体进度与完成度

> **更新日期**：2026-10-30
> **结论**：Phase 0 已闭环；Phase 1 规则内核可用且调度器算子全量接线；Phase B 大部分完成；Phase C 增量子集（班内序列数值化、软硬库存、提前期、Campaign、路线预指派、交期 Slack）已落地。**整体处于快速收敛中**。
> **一句话**：系统可跑通 WL/SY38 排产、17 项校验、规则集编辑、严格模板上传；班内 sequence、FG/SF 软/硬库存选项、C4 提前期、C2-b-0 路线分配、C5 Campaign/Slack 已落地；主模型内 Assign、独立 Pull 仍未完成。

---

## 1. 总进度

| 阶段 | 估算完成度 | 结论 |
|---|---:|---|
| Phase 0：正确性止血 | **100%** | 已闭环，含 ACT-06 拆批连续 |
| Phase 1：规则内核化 | **100%** | 19 模板、18 个已编译且在调度器全量接线闭环；解除旧布尔门控；routing 规划于 Phase 2 |
| Phase B：32 规则数据/算子扩展 | **约 75%** | MOQ/MPQ、软产能、时间窗、冻结、scrap、库存 soft/hard 选项已做；真实策略值仍待校准 |
| Phase C：时间/路由/序列 | **约 60%** | C1-b-3/4、C2-a/C2-b-a/C2-b-0、C4、C5-a/C5-b 已做；主模型内 Assign、独立 Pull 未做 |
| **整体** | **约 85%** | 可演示、可基础测试；规则内核 18 算子已实现端到端闭环接入 |

---

## 2. 当前版本与测试

| 项 | 当前值 |
|---|---|
| 分支 | `feat/shaojing-dev` |
| WL active | `WL_PHASE1 v4`，13 条拆分规则 |
| SY38 active | `SY38_PHASE1 v2`，13 条 |
| 内核模板 | 19 个；18 个已编译且调度器全量接线；`routing` 未编译（Phase 2） |
| 全量测试 | **132/132 PASS**（含 105 基线 + operator wiring 6 项 + 多场景推演 17 项 + 追踪体系 4 项） |
| 快速测试 | **107/107 PASS，约 64s** |
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
| `tests/test_scenario_repository.py` | 5 | PASS |
| `tests/test_scenario_ingestion.py` | 2 | PASS |
| `tests/test_scenario_builder.py` | 5 | PASS |
| `tests/test_scenario_api.py` | 5 | PASS |
| `tests/test_telemetry.py` | 4 | PASS |
| **全量** | **132** | **PASS** |

---

## 3. 32 条规则状态与多场景推演架构

### Phase S：模块化多场景推演架构 (Scenario Platform)

| 项 | 状态 | 说明 |
|---|---|---|
| S1 中立仓储与无外键 Schema | ✅ | `src/scenarios/repository.py`：14 张表、无物理外键、高密度索引、级联事务 |
| S2 Excel 自动拆包服务 | ✅ | `src/scenarios/ingestion.py`：10 个 Sheet 0.3s 切片入库至 7 大积木表 |
| S3 内存装配器流水线 | ✅ | `src/scenarios/builder.py`：<500B 清单、15ms 纯内存组装、前道补水推导、What-If |
| S4 RESTful API 与方案对撞 | ✅ | `src/scenarios/router.py`：生命周期 CRUD、克隆、求解归档、多方案 KPI 对撞 |
| S5 前端推演工作台 | ✅ | `src/static/scenarios.html`：3 栏式 SPA（场景库抽屉/4步工坊/KPI竞技场） |
| S6 防漂移与防篡改硬化 | ✅ | 求解自动触发只读锁 `is_locked=1`，已发布 `RELEASED` 方案防篡改 400 拦截 |

### Phase B

| 项 | 状态 | 说明 |
|---|---|---|
| B1 MOQ/MPQ | ✅ 代码/数据 | `basis=shift/order`、`quantity_source=MPQ/MOQ`、`tail_exempt`、`moq_override`；尚未写入 active seed |
| B2 scrap | ✅ opt-in | `build_requirement_plan(apply_scrap=True)`，默认关闭 |
| B3 软产能 | ✅ | soft 双边计罚，不再硬加 ceiling |
| B4 库存策略 | 🟡/✅ 部分 | 数据层 + 投影 + API；FG/SF soft penalty，另可选 hard 上限；均默认关闭 |
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
| 全连续时间 | ❌ | 仍是班次粒度 + 班内近似 |

---

## 4. 本轮/近期已完成

### 2026-10-30 主要变更

- C1-b-3：`seq_start/seq_end/seq_setup` + `sequence_plan` API；Gantt 页新增 sequence/setup 明细。
- C1-b-4：setup 技术员区间 `AddCumulative`，站点设置 `setup_technicians_available`。
- C2-b-0：`solve_routing_assignment()` 独立 CP-SAT 路线分配阶段 + `routing_decisions` API。
- B4-b：FG/SF 库存上限 soft penalty 与 optional hard ceiling。
- C4-a：`lead_time_days` 软提前期 + `leadtime_enabled` + 非加工时长与 Transfer Batch 折算。
- C5-a：Campaign Group 软约束与测试用例。
- C5-b：交期 Slack 软约束与测试用例。
- 算子接线断层消除与门控解耦闭环。

---

## 5. 关键缺口（下一阶段）

### P0

1. **C1-b 生产化**：已有可选 `start/end/setup`、Gantt 明细、按边技术员人数；仍需真实 Setup-Matrix、默认开关策略。
2. **C2-b 主模型内 Assign(i,m)**：已有上游 CP-SAT 分配阶段，但尚未与主排产模型联立。
3. **B4-b 库存策略校准**：FG/SF soft 与 hard 选项均已接；仍需真实 Inventory-Policy 值，默认不要开 hard。

### P1

4. C4 完整版：queue/inspection/move/transfer batch 已接 soft gap；仍需机台级硬排程。
5. C5 Pull 独立策略（Campaign 与 Slack 已做）。
6. 32 条规则目录与 17 项审计的正式映射。
7. 真实 Setup-Matrix / Inventory-Policy / Routing 数据替换 mock。

### P2

8. 每厂独立实例的正式部署脚本/端口约定（代码已支持 `FACTORY_SITE`）。
9. 确定性求解停止条件、CI、规则审批日志。

---

## 6. 运行与部署

### 单实例开发

```powershell
python run_web.py
# http://127.0.0.1:8000
```

### 每厂一个实例（推荐）

```powershell
# WL / WE34
$env:FACTORY_SITE="WL"; $env:PORT="8000"; python run_web.py

# SY38
$env:FACTORY_SITE="SY38"; $env:PORT="8001"; python run_web.py
```

固定实例会拒绝对其他站点的切换；两个进程各自持有独立 `session_state`。

### 数据源上传

- UI：第六页 → 上传 Excel 数据源；
- 模板下载：`GET /api/inputs/template`；
- 上传要求：Sheet/表头与 `dataSource.xlsx` 严格一致；
- 文件按站点保存，不覆盖仓库模板。

---

## 7. 风险与说明

| 风险 | 影响 | 缓解 |
|---|---|---|
| 当前单实例是全局 `session_state` | 多人同时操作会互相影响 | 正式使用按每厂一个实例；或后续做 per-user session |
| Setup Matrix 仍是 mock | sequence 开关效果不代表真实换型 | 先用 mock 验证模型，再替换业务数据 |
| Routing/Inventory/LeadTime 未全接求解器 | UI 能看到，但排产不完全使用 | 明确标注数据层/审计层；下一阶段接 CP-SAT |
| 105 项测试通过不等于 32 条规则全部完成 | 部分规则仍是 preprocess/audit | 以本文能力表为准，不以测试数代替完成度 |
| 工作区改动未提交前不可复现 | 交接风险 | 本次提交/推送后以 Git 版本为准 |

---

## 8. 验证记录

```text
全量：111/111 PASS，约 1387.6s（C1-b-3/4/B4-b/C2-b-0/C4/C5-a/C5-b + operator wiring 后）
快速：67/67 PASS，约 61s
WL active：WL_PHASE1 v4 / 13 rules
SY38 active：SY38_PHASE1 v2 / 13 rules
/api/rule-catalog：32 条
/api/inputs（WL）：routing 37 / setup 36 / inventory 74 / leadtime 37
严格上传：原模板通过；错误表头拒绝
真实 /api/solve（sequence_setup=true）：success / 19 tasks / 63 sequence records / 900 setup min
```
