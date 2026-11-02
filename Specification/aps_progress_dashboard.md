# APS MVP 整体进度与完成度

> **更新日期**：2026-10-31
> **结论**：Phase 0 已闭环；Phase 1 规则内核可用；Phase B 大部分完成；Phase C 只完成到可验证的增量子集。**整体仍未完成**，不能标记为 100%。
> **一句话**：现有系统可跑通 WL/SY38 排产、17 项校验、规则集编辑、新版 15 表主数据上传；班内 sequence、FG/SF 软/硬库存选项、C4 提前期、C2-b-0 路线分配、C2-b-1 班次级路线容量、C2-b-2 主模型内 Assign（容量/交期闭环）、C2-b-3b 小规模同模型 sequence、C2-b-3a 大规模回退精排、C5 Campaign/Slack、SC-09 Jitter、SC-10 三班平滑、SC-08 顺位倒置代理、BS-05 同模合批 + SuperOrder 分组、BS-06 交期退避 + 截断建议 + 自动顺延、SC-01 D+1 备货时钟、BS-04 逆向拉动偏好、C2-b-3c 候选线裁剪、HC-16-lite 连续工单实验、四档工作日历已落地；硬 95% 失败不再静默软回退；独立 Pull 求解器仍未完成。
> **主数据升级提示**：2026-10 新版 15 表已接入；人数/静置时间/模具/产品族已补模拟值，C4 改由“静置 + 首批转运”推导，详见 `Specification/aps_masterdata_algorithm_impact.md`。

---

## 1. 总进度

| 阶段 | 估算完成度 | 结论 |
|---|---:|---|
| Phase 0：正确性止血 | **100%** | 已闭环，含 ACT-06 拆批连续 |
| Phase 1：规则内核化 | **95%** | 20 模板、19 编译；routing 仍未编译 |
| Phase B：32 规则数据/算子扩展 | **约 86%** | 32 条规则已无 needs_model；BS-04 为 Pull 软偏好；其余 partial 集中在真实数据/业务阈值 |
| Phase C：时间/路由/序列 | **约 80%** | C1-b-3/4、C2-a/C2-b-a/C2-b-0/C2-b-1/C2-b-2/C2-b-3a/C2-b-3b、C4、C5-a/C5-b 已做；大规模同模型 sequence、独立 Pull 未做 |
| **整体** | **约 85%** | 可演示、可基础测试；不是完整 32 规则求解器 |

---

## 2. 当前版本与测试

| 项 | 当前值 |
|---|---|
| 分支 | `main` @ `f186f77`（已推送；工作区干净） |
| WL active | `WL_PHASE1 v4`，13 条拆分规则 |
| SY38 active | `SY38_PHASE1 v2`，13 条 |
| 内核模板 | 20 个；19 个已编译；`routing` 未编译 |
| 全量测试 | **159/159 PASS（最新完整跑）** |
| 快速测试 | **60/60 PASS，约 62s** |
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
| `tests/test_routing.py` | 7 | PASS |
| `tests/test_rule_catalog.py` | 4 | PASS |
| `tests/test_inventory_policy.py` | 4 | PASS |
| `tests/test_capacity_kernel.py` | 8 | PASS |
| `tests/test_campaign.py` | 2 | PASS |
| `tests/test_master_schema.py` | 12 | PASS |
| `tests/test_calendar_modes.py` | 6 | PASS |
| `tests/test_master_export.py` | 3 | PASS |
| `tests/test_assign_in_model.py` | 4 | PASS |
| `tests/test_jitter.py` | 2 | PASS |
| `tests/test_hc16.py` | 1 | PASS |
| `tests/test_labor_smoothing.py` | 1 | PASS |
| `tests/test_sc08.py` | 1 | PASS |
| `tests/test_campaign_batching.py` | 1 | PASS |
| `tests/test_slack_fallback.py` | 1 | PASS |
| `tests/test_campaign_batches.py` | 2 | PASS |
| `tests/test_truncation_suggestions.py` | 2 | PASS |
| `tests/test_bs06_auto.py` | 1 | PASS |
| `tests/test_d1_clock.py` | 1 | PASS |
| `tests/test_pull.py` | 1 | PASS |
| `tests/test_hard_capacity_strict.py` | 1 | PASS |
| `tests/test_candidate_pruning.py` | 3 | PASS |
| **全量** | **149** | **PASS** |

---

## 3. 32 条规则状态

### Phase B

| 项 | 状态 | 说明 |
|---|---|---|
| B1 MOQ/MPQ | ✅ 代码/数据 | `basis=shift/order`、`quantity_source=MPQ/MOQ`、`tail_exempt`、`moq_override`；尚未写入 active seed |
| B2 scrap | ✅ opt-in | `build_requirement_plan(apply_scrap=True)`，默认关闭 |
| B3 软产能 | ✅ | soft 双边计罚，不再硬加 ceiling |
| B4 库存策略 | 🟡/✅ 部分 | 数据层 + 投影 + API；FG/SF soft penalty，另可选 hard 上限；均默认关闭 |
| B5 时间窗/冻结 | ✅ | `allowed_dates`；`freeze.use_baseline` |
| B6 目录/site override | ✅ 数据层 | 32 条目录 + `effective_for_site()`；尚未完全替换旧审计映射 |
| B7 SC-09 Jitter | ✅ opt-in | 相对上一版基线，软罚完成班次偏移 + 产线切换；`jitter_enabled` 默认关闭 |
| B8 HC-16-lite 工单连续 | 🟡 实验 | 每工单最多 1 块（`max_blocks_per_task=1`）；10 天全量开启会与 95% 班次地板/物料/PM 冲突导致不可行，暂不默认；HC-16-hard 同模跨工单防穿插未做 |
| B9 SC-10 三班负荷平滑 | ✅ opt-in | 按日最小化早/中/晚班机时 L1 偏差（方差代理）；`labor_smoothing_enabled` 默认关闭 |
| B10 SC-08 顺位前置填补 | 🟡 原型 | 同线自然交期顺序 vs 完成顺序的配对倒置罚；`sc08_enabled` 默认关闭，未做平方序位 |
| B11 BS-05 同模合批 | 🟡 软约束 | 同模/campaign 每多一个生产段罚 60,000；新增 `CampaignBatch` 分组对象与 `/api/solve.campaign_batches`；不改变订单 identity |
| B12 BS-06 交期裕度退避 | 🟡 回退+建议 | 合批导致延期时自动回退不合批方案；新增 `truncation_suggestions`：按 batch 建议顺延尾单并校验 MOQ；不自动改计划 |
| B13 SC-01 D+1 时钟 | 🟡 soft | 日末 FG 库存对次日需求做 100 权重短少罚；早班追产尚未做 |
| B14 BS-04 逆向拉动 | 🟡 偏好 | `pull_enabled` 把 FG/SF 完成偏好从“越早越好”改为“越接近交期/窗口越好”；不是独立 Pull 求解器 |

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
| C2-b-1 班次级路线容量 | ✅ 可选 | 上游分配改用真实工作日历（FIVE_TWO/FOUR_THREE 等）+ PM 停机扣减 + 产线白名单；`routing_capacity_hours` 可审计 |
| C2-b-2 主模型内 Assign | ✅ 可选 | `assign(task,line)` + `x/active(task,line,shift)`；分线容量/PM；exactly-one |
| C2-b-3a 分配后 sequence 精排 | ✅ 可选 | 候选项 >2 时回退：先分配，再固定线做第二次 sequence 求解 |
| C2-b-3b 同模型 sequence（小规模） | ✅ 可选 | 候选项 ≤2 时 assign + sequence 在同一 CP-SAT；>2 自动回退 3a |
| 大规模同模型 sequence | ❌ | 候选项 >2 时变量规模过大，尚未在同一模型联立 |
| C3-lite 技术员容量 | ✅ 近似 | 按班次 run start 数限制 |
| C4 Operation LeadTime/Transfer | ✅ 软约束 | `lead_time_days` + setup/queue/inspection/move + transfer batch 次数已接 soft gap；仍非机台级硬排程 |
| C5 Campaign/Pull/Slack | 🟡 部分 | Campaign Group + 交期 Slack 软惩罚已接；Pull 策略未独立实现 |
| 全连续时间 | ❌ | 仍是班次粒度 + 班内近似 |

---

## 3.1 2026-10-31 主数据 15 表升级后的算法影响

> 完整矩阵见 `Specification/aps_masterdata_algorithm_impact.md`。

| 影响 | 补数后现状 | 算法后果 |
|---|---|---|
| 人力 `人数`（Routing） | 已模拟 FG=3、SF=2；任务 crew>0 | HC-15/SC-10 有人力口径；仍缺 `Line-Staffing` 精细数据 |
| SFN `生产提前期` 删除 | `lead_time_days=0`，但 C4 改读 `工序提前期-提前准备时间` | C4 由“静置 + 首批转运”推导，不再依赖 lead_time_days |
| `产线基础数据` | 3 行：PGLINKAGE/轴套6/轴套7（8h×3） | 班次几何/TRP 按表生效 |
| `特殊班次/同时生产/产品族` | 特殊班次 0/17、同时生产 4/17、产品族已回填 | 前两者仍无消费点；Campaign 仍读 CSV |
| `预计入库时间` | 7/7 已填 | 模型仍按日期+班次取整，未细化到时刻 |
| 日历四档 + 班次几何 | 已接日历/推荐/求解器/审计 | BS-01 升级；默认拓扑不变 |
| CT/产线/TRP/目标库存改源 | 已从 Routing/产线基础数据/库存策略派生 | 数据口径统一，旧版仍兼容 |

**判断**：补数后“主干可用、人力/前置期空转”已消除；剩余缺口集中在特殊班次、同时生产、ETA 时刻、MOQ 与大规模同模型 sequence。

---

## 4. 本轮/近期已完成

### 2026-10-29 主要变更

- WL 基于 v3 新建并激活 **v4**，补齐最新模板参数，默认行为保守。
- 第三页旧 27 条配置块删除，改为 **32 条规则目录（HC/SC/BS）**。
- 新增主数据页签：
  - Routing-Machines
  - Setup-Matrix
  - Inventory-Policy
  - Operation-LeadTime
- Excel 上传改为**Schema 驱动的模板校验**：15 表/必需表头按 `master_schema` 校验，列顺序可变，兼容旧 25 表；不再要求与 `dataSource.xlsx` 逐列一致。
- 上传按站点保存：`data/uploads/current_WL.xlsx` / `current_SY38.xlsx`。
- 第六页新增班内 sequence/SDST 开关，站点级持久化。
- C1-b-3：`seq_start/seq_end/seq_setup` + `sequence_plan` API；Gantt 页新增 sequence/setup 明细。
- C1-b-4：setup 技术员区间 `AddCumulative`，站点设置 `setup_technicians_available`。
- 测试扩到 **101 全量 / 57 快速**。
- 支持 `FACTORY_SITE` 固定单厂实例。
- C2-b-0：`solve_routing_assignment()` 独立 CP-SAT 路线分配阶段 + `routing_decisions` API。
- B4-b：FG/SF 库存上限 soft penalty + `inventory_policy_enabled`。
- C4-a：`lead_time_days` 软提前期 + `leadtime_enabled`。
- UI 空白页修复：`sequencePlan` 未 expose；Playwright 验证 `pageerror=0`。
- HC-05：新增 `capability_violations()` 与 `/api/solve.routing_capability_violations`，审计未认证产线。
- 32 条目录状态校准：partial 20 / needs_model 5 / preprocess 4 / configurable 2 / postprocess 1。
- 修复：
  - `_compile_capacity_kernel` 二次调用 `total_objective()` 导致 500；
  - `sequence_setup` 接入时 `site` 未定义导致 500；
  - 删除旧 27 条时漏删闭合 `</div>`，导致第三页之后空白。

### 2026-10-31 主要变更

- 15 表主数据 v2 接入：header-driven、列序可变、灰色列可删；Tab-6 按站点下载/上传（`?site=`、`no-store`）。
- 第一张「填写说明」重构：表用途 + 字段含义/单位/限制 + 跨表一致性 + 校验规则，共 221 行，由 Schema 生成。
- 四档工作日历 + 班次几何：做四休三/做五休二/做六休一/做七无休；3×8h / 2×12h 驱动日历与求解器。
- C4 改为“静置 + 首批转运”：`提前准备时间` 45~480min，不再依赖 `生产提前期 (days)`。
- C2-b-1 班次级路线容量：真实工作日历 − PM，候选线白名单，`routing_capacity_hours`。
- C2-b-2 主模型内 Assign：`assign(task,line)` + 分线容量/PM；`routing_assignment`。
- C2-b-3a/3b：>2 候选项先分配再 sequence 精排；≤2 候选项同模型 sequence。
- SC-09 Jitter：相对上一版基线软罚完成班次偏移/产线切换，站点开关 `jitter_enabled`；当前目录 partial 21 / needs_model 4 / preprocess 4 / configurable 2 / postprocess 1。
- HC-16-lite 实验开关：每工单最多 1 块；10 天全量开启会因 95% 地板/物料/PM 冲突不可行，证明 HC-16 不能简单“全局限块”，需要按 campaign + 例外建模。
- SC-10 三班平滑：按日最小化早/中/晚班机时 L1 偏差。
- SC-08 原型：同线自然交期顺序 vs 完成顺序的配对倒置罚。
- BS-05 同模合批：同模/campaign 额外生产段罚。
- BS-06 交期退避：合批导致延期时回退不合批方案，记录 late/slack 变化。
- BS-05 SuperOrder 分组：同模/同零件、视距内订单形成 `CampaignBatch`，API 返回 batch/order/task 清单。
- BS-06 截断建议：按 batch 建议顺延尾单、保留量校验 MOQ；API 返回 `truncation_suggestions`。
- SC-01 D+1：日末库存对次日需求软罚。
- BS-04 Pull：完成偏好从“越早越好”改为“越接近交期/窗口越好”。
- 硬 95%：规则集 `capacity_utilization` 为 hard 时，默认不做软回退；失败返回 `HARD_95_INFEASIBLE`，需显式打开软回退。
- C2-b-3c：每个任务候选线裁剪到主+1备，候选面 ≤6 个任务时同模型 sequence；>6 仍回退 C2-b-3a。当前目录 partial 26 / needs_model 0。
- 全量测试 **159/159**，快速 **60/60**；SY38 10 天实跑 18 任务 / OTD 100% / 33 sequence / 9 校验。

### 仍保留的兼容项

- 第 5 页 17 项合规审计仍使用旧 27 条规则映射；
- 旧版 `/api/rules` 仍供部分求解开关使用；
- 32 条目录目前是展示 + 元数据，不是全部可编辑/全部已编译。

---

## 5. 关键缺口（下一阶段）

> **业务问题已外置**：所有需要物流排产专家确认的规则语义、阈值、数据口径，统一放在
> [`APS_业务规则确认清单.xlsx`](../APS_业务规则确认清单.xlsx)。本文件不再重复业务问题，只保留技术缺口。

### 5.1 技术缺口（本文件负责）

1. **HC-16 正式模板**：当前只有 lite 开关，需新增可配 nature 的 hc16 模板，V6 才能设 hard。
2. **C2-b-3 大规模同模型 sequence**：已做候选线裁剪（主+1备，≤6 任务同模型 sequence）；>6 个候选任务仍回退 C2-b-3a，需要列生成/候选面进一步压缩才能完全闭合。
2. **独立 Pull 求解器**：当前 `pull_enabled` 只是“完成偏好翻转”的软 Pull，不是逆向倒排求解器；真正的 Pull 需要逆向时间变量 + 顺排/拉动混合仲裁。
3. **BS-06 有延期路径验收**：自动截断/顺延代码已接，但还没有真实延期场景的端到端验收；需要构造或采集一个“合批导致其他订单 Slack<0”的用例。
4. **HC-16-hard**：当前只有 HC-16-lite（每工单 1 块）和 BS-05 软合批；跨工单同模窗口硬约束尚未实现，依赖业务确认“超级工单范围/例外/优先级”后再做。
5. **全连续时间**：仍是班次粒度 + 班内近似；连续时间版需要重写时间变量与容量约束。
6. **内核 `routing` 模板**：20 个模板中 `routing` 仍未编译（19/20 = 95% 的直接原因）；调度器侧已有 C2-b Assign，但内核侧还不能用规则集驱动。
7. **低负荷开班/工单合并**：WCA1894 出现 3×60 拆分到早班，12 号早班仅 13.5%。当前 soft 95 + HC-16 soft 只罚分，不能阻止低负荷开班；需要 shift-open 固定成本或后处理合并。
8. **SF MPQ tail-aware**：L755774（SF）出现 3/88/123 等非 MPQ100 班次块；当前 SF unit=piece 且 piece-lot 可拆，整单总量也未向上取整。需要“整单 MPQ + 最多 1 尾块”策略，不能简单每班硬 100。
9. **HC-16 hard v0 不可用**：V6 hard 在 WL 直接 400；当前只是“每工单最多 1 块”的近似，正式同模窗口/交期 override 尚未完成。
10. **工程化**：每厂独立实例部署、确定性停止条件、CI、规则审批日志、真实数据回归基准。

### 5.2 业务/数据缺口（不在此文档，见 Excel）

- 需要业务拍板的规则：HC-16、SC-01、HC-08、HC-11、HC-14、SC-10、BS-04、BS-05、BS-06、SC-08、SC-02、HC-12 等；
- 需要提供的主数据：Setup-Matrix、班次人数、L/R 配对、MOQ、模具可用套数、Inventory-Policy、试产窗口、PM 降载、主备线效率、订单/预测分类；
- 统一入口：`APS_业务规则确认清单.xlsx` 的「业务确认清单」和「数据提供清单」两个 sheet。

### 5.3 规则确认决策（2026-11-01）

- **SC-02 105%**：业务确认 105% 为**通用**上浮，不限制“单产品/无换型”；当前通用 105% 实现保留。
- **HC-16**：业务希望软/硬在 rule-sets 页面自行配置；V6 候选可将 HC-16 设为 hard。已新增可配 nature 的 hc16 模板 v0（工单连续近似），但同模窗口硬模型与交期 override 仍未完成；hc16_lite_enabled 仍仅测试用。
- **规则主数据**：确认后的口径应回写 Light China APS Rules.xlsx，但保留 J 列答复作为审计轨迹；建议另存 v1.1，不覆盖原文件。

### 5.4 验收顺序

1. 先做 BS-06 延期场景端到端验收（技术缺口 #3）；
2. 业务确认 HC-16/SC-01/BS-04 后，再做对应硬化；
3. 最后做大规模同模型 sequence 与全连续时间（技术缺口 #1/#5）。

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
- 模板下载：`GET /api/inputs/template?site=WL|SY38`（15 表，按站点预填，`no-store`）；
- 上传要求：15 表/表头按 `master_schema` 校验，兼容旧 25 表（`layout=v2/legacy`）；
- 文件按站点保存：`data/uploads/current_<site>.xlsx`，不覆盖仓库模板。

---

## 7. 风险与说明

| 风险 | 影响 | 缓解 |
|---|---|---|
| 当前单实例是全局 `session_state` | 多人同时操作会互相影响 | 正式使用按每厂一个实例；或后续做 per-user session |
| Setup Matrix 仍是 mock | sequence 开关效果不代表真实换型 | 先用 mock 验证模型，再替换业务数据 |
| 大规模同模型 sequence 未联立 | 候选项 >2 时回退两阶段，选线不反悔 | 列生成/候选线裁剪，或 sequence 负荷回灌 assign |
| Labor 主数据仍缺 | HC-15/SC-10 只能用人数，不能用班次上限/共享池 | 补 `Labor-Availability`/`Line-Staffing` |
| SY38 模板来自 legacy seed | 首次上传前需沈阳物流复核 | 用真实 SY38 模板替换后再启用 |
| 159 项测试通过不等于 32 条规则全部完成 | 部分规则仍是 preprocess/audit | 以本文能力表为准，不以测试数代替完成度 |
| 工作区改动未提交前不可复现 | 交接风险 | 本次提交/推送后以 Git 版本为准 |

---

## 8. 验证记录

```text
全量：159/159 PASS（BS-06 自动裁剪后）
快速：60/60 PASS，约 62s
WL active：WL_PHASE1 v4 / 13 rules
SY38 active：SY38_PHASE1 v2 / 13 rules
/api/rule-catalog：32 条
/api/inputs（WL）：17 FG / 22 SF / 65 订单 / 3 条产线
严格上传：原模板通过；错误表头拒绝
真实 /api/solve（WL，SC-09）：jitter 开启后 21 个任务完成班次/产线变化从 15 降到 12（样本）
真实 /api/solve（SY38，C2-b-3b）：
  18 tasks / OTD 100% / 12 assignment（3 次移线）/ 33 sequence / 9 checks / 75.1s
```
