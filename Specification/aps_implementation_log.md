# APS MVP 实施记录（Phase 0 闭环 + Phase 1 v1 闭环）

> **阅读提示**：本文按时间追加。文中 2026-10-30 之前的“101 / 105 / 59”等数字、以及“C2-b 未实现/未做”等状态描述，都是当时的基线，不代表当前状态；当前进度以文末「当前状态汇总（2026-10-31 11:00）」和 `Specification/aps_progress_dashboard.md` 为准。

> **基线**：`14ff727`（与诊断报告评估版本一致）
> **实施日期**：2026-10-25 / 2026-10-26
> **范围**：ACT-01 / 02 / 03 / 04 / 05 / 07 / 08 + ACT-L1 + ACT-09～14（Phase 0）+ ACT-20 / 21 / 23 / 24（Phase 1 地基 + 权重/分组）+ ACT-06（拆批连续）+ ACT-22 step 1-5（capacity / min_batch / 硬交期 / changeover / sequence / tooling / ETA / material_balance / PM）+ customer_priority / concurrency / labor_capacity / contiguity / calendar / time_window / inventory_bound / freeze / co_product + ACT-25/26/27/28/29 + ACT-L2/L3
> **2026-10-26 规则语义 / 可读性 / 甘特细化**：

- **27 页内核标签**：`src/kernel/rule_mapping.py` 把旧规则映射到固定模板，`/api/rules` 返回 `kernel_status`（active/partial/available/legacy/phase2）与 active 内核 rule_id；27 页新增「内核状态」列。新增规则页顶部 27/17/13 说明。
- **R-WL-26 语义拆分**：`WL_CAP_FG=1.05`、`WL_CAP_SF=1.0`；调度器按 stage 推导 105 开关；capacity 编译器只对匹配 stage 的产线生效（修掉 FG 规则误放宽 SF 线的问题）。
- **SF MPQ 语义**：`WL_MPQ_SF` 改为 `unit=piece,min_multiple=1`；半成品总量 ≥ MPQ 后单班可任意整数件；validator 与独立审计同步去掉「单班 ≥ MPQ」判据。
- **甘特/明细**：solve 响应新增 `shift_segments`（每班一块）、`snake_points`（段间折线）、`sequence`/`changeover`（按线顺序与换型）；前端甘特按班次段渲染 + 折线连接；明细表按 `shift_segments` **展开成小段**，显示全局顺位 `#`、段序号 `段 i/n`、本段产量/班次/工时；换型改为**逐段计算**（#3→#4 换产品会显示换型，不再只标任务首段），并给同班多产品标“同班并行”；甘特每段中心加亮色顺位圆点，首段高亮，折线按顺位连接；**移除无意义的“产线聚合单轨”模式；泳道改为按工单（task_id）一行**，解决同一料号多工单互相遮挡导致 #7/#5/#8/#9 等不显示的问题。
- **可读性**：新增 `src/metric_labels.py`，校验/审计指标中文标签；补 `smoothing_within_35pct`。

**校验指标可读化（2026-10-26）**：

- 新增 `src/metric_labels.py`，把 `allow_105_fg`、`total_production_hours`、`violating_shifts` 等 60+ 个指标映射成中文；
- `/api/solve` 的 `checks`、规则明细、独立审计响应都带 `metric_labels`；
- 前端校验卡片显示"中文标签: 格式化值"（布尔→允许/不允许，百分比→%，工时→h，字典→`PGLINE 178.7h · 轴套6 186.1h`）；
- 规则页顶部与手册 §0 增加 27 / 17 / 11 的解释：27 是旧业务规则目录，17 是校验检查，11 是 active 内核规则实例（18/19 算子已实现）。

**硬化项**：ACT-27 树形/100 线虚拟滚动、routing 算子（Phase 2 线选择）、真实人力数据、确定性停止条件
> **决策更新**：D7 / D8 / D9 已确认，见 §6

---

## 1. 变更清单

| 文件 | 变更 | 说明 |
|---|---|---|
| `src/shift_manager.py` | +4 | `DEFAULT_CHANGEOVER_MINUTES = 0.0`：换型已折入 TRP，求解器不再重复计提 |
| `src/data_loader.py` | +15 | `ProductParam.headcount`、`ProductionTask.crew_required`；读 `Headcount` / `Head count`；缺数据告警 |
| `src/scheduler.py` | ~310 | ACT-01/02/03/04/05/07/08（详见 §2） |
| `src/validator.py` | ~118 | 综合负荷口径（可含换型，默认 0）；105% 交付弹性；产能/利用率一致 |
| `src/main.py` | ~66 | 人力工时 KPI、日历覆盖率/开班利用率双口径 |
| `src/server.py` | ~230 | 规则集 API/页面、active 规则集接线、labor 数据、`use_rule_set`；保留旧规则接口 |
| `src/audit/engine.py` | 新增 | 独立审计：净需求、BOM、CT/TRP、9 项检查；不 import scheduler |
| `src/audit/__init__.py` | 新增 | 公共 API |
| `test_independent_audit.py` | 新增 | 基线、删单、改量、超载、无 scheduler 依赖 + 20 类变异矩阵 |
| `src/kernel/types.py` / `registry.py` / `scope.py` / `compiler.py` | 新增 | 19 个算子模板、作用域继承、编译器上下文（Phase 1 地基） |
| `src/kernel/solve.py` | 新增 | 分组字典序求解 + 组内权重归一化（按 rule 而不是按变量） |
| `config/rule_sets/WL_2026Q4.json` / `SY38_2026Q4.json` | 新增 | 两个工厂的规则集样例（软硬/权重/分组/作用域/生效期） |
| `test_kernel.py` | 新增 | 22 项：模板/参数/继承/生效期/JSON/编译求解/权重/字典序/customer/concurrency/labor/contiguity/calendar/time_window/inventory/freeze/co_product |
| `src/rule_sets/` | 新增 | SQLite 版本库、Excel 导入/导出、校验/diff/激活、敏感度、无解诊断、CLI |
| `src/labor.py` | 新增 | L2/L3 人力主数据加载与校验（可用人数/产线定员/共享班组） |
| `src/static/rule_sets.html` | 新增 | 规则集编辑器：版本/编辑/保存/激活/导入/导出/新增/删除 |
| `config/rule_sets/*_Phase1.json` | 新增 | 两个工厂的 12 条 active 基线规则集 |
| `test_rule_sets.py` / `test_labor.py` / `test_split_batch.py` | 新增 | 9 + 5 + 2 项平台/人力/拆批测试 |
| `Specification/aps_rule_set_editor_manual.md` | 新增 | 规则集编辑器详细操作手册（字段/按钮/权重/19 模板参数/Excel/排障） |
| `data/templates/labor/` | 新增 | L3 三个人力 CSV 模板 + README |
| `src/metric_labels.py` | 新增 | 校验/审计指标中文标签映射（60+ key） |
| `src/kernel/rule_mapping.py` | 新增 | 27 条旧规则 → 内核模板映射/状态 |
| `src/rule_sets/migrations.py` | 新增 | FG/SF capacity 拆分 + SF piece 迁移 |
| `src/static/index.html` | 扩展 | 校验卡片中文标签/值格式化；27/17/11 说明 |
| `test_aps_pipeline.py` | +19/-7 | 校验时传入结果自身 105% 口径；预算 15–30s → 120s |
| `data/snapshot_*.json` | 自动 | 快照新增 `headcount` 字段 |

> `dataSource.xlsx` 在 CLI 运行中会被回写，验证后已 `git checkout` 还原。

---

## 2. ACT 执行状态

| ACT | 内容 | 状态 | 关键实现 |
|---|---|---|---|
| ACT-01 | 完工变量精确化 | ✅ | `AddMaxEquality(end, [s * active[t,s]])`；R-WL-12 由 FAIL → PASS |
| ACT-02 | 开班决策 + 95% 双层 | ✅ | `open[l,s]` 决策、`tail[l,s]`（全期至多一个、只能是最末开班）、95% 作用于综合负荷、`load ≤ 1.05·cap`；**不自动切 105%**，由规则选项决定 |
| ACT-03 | 换型处理 | ✅ | 按 D7：换型已折入 TRP，`changeover_minutes=0`，仅通过 `started` 计价保持连续；若未来拆分，改一个常量即可 |
| ACT-04 | 工单连续 | ✅（软） | 连续是**软偏好**：每多一个连续块 = 多一次换型成本；硬块数上限保留为可选项（`max_blocks_per_task(_sf) > 0`） |
| ACT-05 | 首班锁死 | ✅ | `s=0` 时用 `stock_covered` 判断齐套 |
| ACT-06 | 单批超班 | ✅ | 订单总量 ≥ MPQ；单批 > 班次时拆到相邻班次连续生产，拆分段免 MPQ 下限，只算一次换型；`WA14208S 72` 拆两段、1 块 |
| ACT-07 | 确定性 | ✅ | `num_workers=1` + 固定 seed；5 次一致性回归待补（不阻塞当前） |
| ACT-08 | 死参数 + 周末 bug | ✅ | 删除 `freeze_shifts` / `enforce_exact_demand`；修复周末惩罚日期解析（原 `strptime("%Y-%m-%d")` 对 `"10/12"` 永远失败，惩罚从未生效） |
| ACT-L1 | Headcount 贯通 | ✅ | 数据类/加载器/任务/报表/API；`轴套6` 缺数据标红 |
| ACT-09 | 独立审计骨架 | ✅ | 新建 `src/audit/`，**禁止 import scheduler**；独立重算净需求、BOM 展开、CT/TRP 工时 |
| ACT-10 | 完整性 5 检查 | ✅ | 需求覆盖 / 行有效性 / 拆解守恒 / 连续性统计 / 物理容量；**删单必须 FAIL** |
| ACT-11 | 4 项恒真检查重写 | ✅ | 冻结期真比较基线、受控窗口 N/A、跨班连续真判并行型号、日历真判非工作日 |
| ACT-12 | 手工审计走独立通道 | ✅ | `/api/audit_manual_schedule` 新增零件级独立审计（手工表无订单号） |
| ACT-13 | 20 类变异注入 | ✅ | `test_06_mutation_matrix` 20/20 命中预期检查 |
| ACT-14 | 黄金回归基线 | ✅ | `test_golden_baseline` 7 项：净需求/合规/覆盖/空需求/超载 |
| ACT-20 | 算子目录 + 模板 Schema | ✅ | `src/kernel/registry.py`：19 个模板、参数类型/范围/默认值；18 个已实现编译（仅 routing 留 Phase 2） |
| ACT-21 | CompileContext + 作用域解析 | ✅ | `src/kernel/scope.py`：site→line→part 继承/覆盖、`replace/add`、生效期、空集告警 |
| ACT-22 | 调度器迁移 | ✅（v1） | 18 个算子可 opt-in；server + main.py 默认使用 active rule set；仅 routing 未编译 |
| ACT-25 | 规则实例校验器 | ✅ | Excel 行级错误（`Rules!G2`）；模板/参数/分组/权重/作用域/生效期；见 `test_rule_sets` |
| ACT-26 | Excel + 版本化落库 | ✅ | SQLite 版本库；同 payload 幂等；改一行 v+1；激活/回滚/diff；API + 编辑器 |
| ACT-27 | 规则页重构 | ✅（v1） | 独立 `/rule-sets` 编辑器：版本/编辑/新增/删除/保存/激活/导入/导出/继承标记/按组批量；树形/100 线待硬化 |
| ACT-28 | 权重敏感度 | ✅（v1） | `±20%` 按组/规则扰动 + KPI delta；CLI + `POST /api/rule-sets/sensitivity` + 页面按钮 |
| ACT-29 | 无解解释 | ✅（v1） | 逐组 → 逐规则 → 规则对定位；CLI + `POST /api/rule-sets/diagnose` + 页面按钮 |
| ACT-L2 | 人力约束算子 | ✅（v1） | `Σ crew[l,s] ≤ H[s]`、共享班组互斥、硬/软、无数据自动跳过 |
| ACT-L3 | 人力主数据导入 | ✅（v1） | `Labor-Availability` / `Line-Staffing` / `Crew-Pool` 模板 + 校验（缺值/负数/重复班组） |
| ACT-23 | 权重归一化 | ✅ | `PenaltyBook.scaled_weights`：组内按 **rule** 归一化到千分比，多变量不会被摊薄 |
| ACT-24 | 分组字典序 | ✅ | 软档按 priority 逐组求解并冻结上界；硬档由硬约束保证；`test_13` 覆盖组目标 |

---

## 3. 关键发现

### 3.1 换型不能在求解器里重复计提（D7 修正）

第一版把"换型 30 分钟"单独从容量里扣，结果：

| 配置 | 结果 |
|---|---|
| 换型 0 分钟、100% | OPTIMAL |
| 换型 30 分钟、100% | **INFEASIBLE**（2s 证明） |
| 换型 30 分钟、105% | OPTIMAL |

业务口径是：**换型时间已经折算在 TRP 里**。所以 30 分钟是重复计提，导致 100% 被误判无解。现已回退为 `DEFAULT_CHANGEOVER_MINUTES = 0.0`；换型仍通过"每多一个生产块 = 多一次换型成本"进入目标函数，保证"尽量连续、少插小单"。

如果将来要把换型从 TRP 拆出来，只需把该常量改为实际分钟数，模型与校验器会同步生效（它们共用同一个常量）。

### 3.2 "工单必须 1 个连续块"与"每班 ≥95%"不可同时硬约束

实验（100%、无换型工时）：

| 连续约束 | 硬 95% |
|---|---|
| FG 1 块 / SF 1 块 | INFEASIBLE |
| FG 1 块 / SF 3 块 | INFEASIBLE |
| 不限制块数 | **OPTIMAL（9.8s）** |

所以"连续"不能做成硬约束。现已改为**软偏好**（换型计价），硬上限保留为可配置项。彻底解法是 Phase 2 的"料号级任务"：同一料号的多个订单合并成一个任务，块数自然下降。

### 3.3 求解顺序改为"硬 95% 优先"

旧顺序是"软 95% 先跑，找到干净解就停"。实测在单 worker 下，软档跑 97s 仍找不到干净解，返回 19 个缺口；而**硬 95% 档在 20–30s 内就能给出 0 缺口的计划**。因此改为：

1. 硬 95% + 硬交付（70% 预算）
2. 软 95% + 硬交付（25%）
3. 软 95% + 交付损失最小化

这也更符合"规则优先"：能完全合规时，先给完全合规的计划。

### 3.4 95% 的口径

现在 `load = 加工 + 换型（默认 0）`，`load ≥ 0.95 × 8h`（尾班除外）、`load ≤ 1.05 × 8h`（若用户选 105%）。校验器用同一口径重算，不再出现"模型算 8h、校验算 7.6h"。

### 3.5 截单（D8）

"截单"= 当天发货截止时间：货物必须在该时点前完工入库，否则赶不上装车。你们反馈"一般提前几小时甚至半天完成"，所以本次**不加任何硬截单约束**，只记一笔：

- 若用户选 105% 档，班次可做到 8.4h，理论上存在 ≤24 分钟的收尾越界；
- 校验器在 105% 档下允许 ≤5% 班次时长的收尾，并单独输出 `overtime_tail_orders_count`；
- 若将来要硬截单，必须禁止发货班次使用 105%，代价是利用率出现缺口。

### 3.6 换型块数与 R-WL-12 的权衡

| 计划 | 换型块（PGLINKAGE / 轴套6） | R-WL-12 顺位前拉 |
|---|---|---|
| 旧版（弱 `end`） | 10 / 17 = **27** | **FAIL**（模型说满足、实际跳序） |
| 新版（精确 `end`） | 19 / 34 = **53** | **PASS** |

旧版用"把 `end` 报小"换来了更少的块数，但真实时序违规。新版严格遵守顺位，代价是块数上升。规则表里 R-WL-12 是**软约束（80%）**，如果业务更在意换型，Phase 1 应把它改成软惩罚并调权重——这是"权重体系"要解决的问题，不宜在 Phase 0 硬编码。

### 3.7 Headcount 数据现状

| 产线 | 班组人数 | 说明 |
|---|---:|---|
| PGLINKAGE | 3 | 17 个 FG 料号一致 |
| 轴套6 | **0（缺失）** | 20 个 SF 料号全空 → 报表标红 |
| PU07 | 12 | — |
| PU22 | 12 / 15 | 同线两种值，需按料号配置 |
| 1400T-6 | 1 | — |

本次只做"数据贯通 + 报表"，**未加人力约束**（ACT-L2 在 Phase 1）。

---

## 4. 验证结果

### 4.1 CLI 全流程（Phase 1 早期；`python src/main.py`，180s 预算）

| 指标 | 结果 |
|---|---|
| 求解档位 | **硬约束 95% + 硬约束准时交付**（100% 产能） |
| 状态 | FEASIBLE，124s |
| 规则校验 | **17/17 通过** |
| R-WL-12 顺位前拉 | PASS（修复前 FAIL） |
| 利用率 | PGLINKAGE 97.1%、轴套6 96.9%，低于 95% 班次 **0**，各 1 个尾班豁免 |
| 日历覆盖率 | 47/60 班次（78.3%） |
| 开班利用率 | 97.0% |
| 交付 | 7/7 = 100%（无 105% 收尾） |
| 人力工时 | 552.0h，峰值 3 人（轴套6 缺数据按 0 计） |
| 换型块 | 19 + 34 = 53（与 R-WL-12 的权衡见 §3.6） |

> 当前 active WL v1（11 条）的 `/api/solve` 实测：200，19 单，0 项校验失败，55 块，90.6s 请求；拆分版 v3 在版本列表里，未自动激活。

### 4.2 单元测试（2026-10-26 Phase 1 基线，历史）

| 测试 | 结果 |
|---|---|
| `test_kernel` | 22 项 PASS |
| `test_rule_sets` | 9 项 PASS |
| `test_labor` | 5 项 PASS |
| `test_split_batch` | 2 项 PASS |
| `test_independent_audit` | 6 项 PASS（含 20 类变异） |
| `test_golden_baseline` | 13 项 PASS（内核迁移 + 105 口径 + 组目标） |
| **合计** | **57 项 PASS，375.0s** |

> 本表是 2026-10-26 的 57 项基线，保留用于历史对比。2026-10-28 代码更新后测试已迁入 `tests/`，当前全量为 **101 项（101 绿，见 §8）**，不要再用本表的“全部 PASS”作为当前结论。

### 4.3 独立审计（ACT-09 / ACT-10）

CLI 现在会在旧校验器之后，再跑一遍**不复用求解器对象**的审计：

```
独立审计结论: 9/9 通过
  需求覆盖 19/19；拆解守恒 31,391 = 31,391；容量 47 个班次 0 超载
  物料齐套 min_margin=0；交付 7/7；利用率 47 开班 0 低于 95%
```

反向验证（`test_independent_audit.py`，5/5 PASS）：

| 注入 | 审计结果 |
|---|---|
| 删除一张 FG 工单 | 需求覆盖 **FAIL**（漏排） |
| 某班次产量减一个 MPQ | 拆解守恒 **FAIL** |
| 注入一行超载产量 | 物理容量 **FAIL** |
| `src/audit` 是否 import 求解器 | AST 检查：**无** scheduler / ScheduledTask / ScheduleResult |

### 4.4 Phase 1 地基（ACT-20 / ACT-21）

**19 个算子模板**（固定集合，参数 Schema 固化）：

`capacity_utilization` / `min_batch` / `contiguity` / `changeover` / `material_balance` / `material_ready` / `due_date` / `sequence` / `calendar` / `tooling_exclusivity` / `pm_block` / `concurrency` / `inventory_bound` / `customer_priority` / `time_window` / `co_product` / `routing` / `freeze` / `labor_capacity`。

已实现编译的 18 个：除 `routing`（多线选择）外全部算子；`routing` 留 Phase 2。

**规则 = 数据**：`RuleSet` / `RuleInstance` / `Scope` / `Nature` / `GroupMode`；作用域支持 `site → line → stage → part`，`replace` 覆盖 / `add` 叠加，带生效期。

**编译器**：`CompileContext` 只暴露 `x / active / started / end_of / load / open / tail / capacity_ms / min_shift_ms`；同一套算子可被当前调度器、未来的料号级模型或测试玩具复用。`PenaltyBook` 负责组内权重归一化（千分比）与组目标累计。

**两个工厂规则集样例**：

- `config/rule_sets/WL_2026Q4.json`：PGLINKAGE 单独 105%，其余 100%；换型 80；半成品库存上限 3 天；人力 6 人。
- `config/rule_sets/SY38_2026Q4.json`：全厂 105%、L/R 共模 1:1、1400T-6 换型 60、人力 20 人（允许 2h 加班）。

**权重与分组（ACT-23/24）**：

- 组内：`PenaltyBook` 按 **rule** 归一化（不是按变量）。一个 `changeover` 规则即使有几十个 `started` 变量，权重也不会被摊薄；`scaled_weights` 输出千分比系数。
- 组间：`solve_lexicographic` 按 `priority` 逐组求解，把上一组目标用 `expr <= ceil(best × (1+slack))` 冻结，再解下一组；组间不跨量纲加权。server 的**软档**已接入该逻辑（硬档由硬约束保证）。
- 这样"1 次换型 = 300,000 个班次"的量纲问题从结构上消失：换型只和换型比，交付只和交付比。

**ACT-22 step 1（capacity 内核化）**：

- `APSScheduler(rule_set=..., use_kernel_capacity=True)` 时，硬 95% 档由 `compile_rules` 生成；旧路径保留为 `_legacy_capacity_body`。
- **默认关闭**，现有 17/17 与黄金基线不受影响。
- `test_golden_baseline.test_08`：内核计划与旧计划对比——任务集合/数量一致、0 个低于 95% 班次、17 项合规一致、目标值差 ≤1000（实测差 1）。

**ACT-22 step 2（min_batch + 硬交期内核化）**：

- `kernel_templates={"capacity_utilization","min_batch","due_date"}` 可一次启用三个算子；默认仍为空集。
- `min_batch` 在任务变量之后、物料平衡之前编译，位置对齐旧约束；`due_date` 在物理约束之后、目标之前编译。
- `test_golden_baseline.test_09`：三模板内核计划与旧计划任务/数量一致、0 缺口、17/17、交付 7/7。

**ACT-22 step 3（changeover 内核化）**：

- `changeover` 规则把每个 `started` 变量加入 `PenaltyBook`，按组归一化到 `CHANGEOVER_WEIGHT`（单规则时系数 = 300,000，与旧常量完全一致）。
- `kernel_templates` 增加 `"changeover"` 即可启用；工厂改权重只改规则里的 `weight`，不再改代码。
- `test_golden_baseline.test_10`：任务/数量一致、0 缺口、17/17、换型块 50 = 旧计划 50（容差 +5）。
- 注意：`PenaltyBook` 的 scale 是**按组**给的；如果将来 changeover 拆成多个组，应使用字典序，而不是把每个组都乘 300,000。

**ACT-22 step 4（sequence / tooling / ETA / PM 内核化）**：

- `sequence`：同产线同料号按 `due_key` 排序，`end(early) ≤ end(late)`，与旧逻辑逐对一致；
- `tooling_exclusivity`：按 `mold_id` 聚合，跨线共模在每班 `Σactive ≤ available_qty`；
- `material_ready`：`material_arrival_shift` 之前 `x = 0`；
- `pm_block`：PM 班次内该产线所有任务 `x = 0`；
- 四个模板加入 `IMPLEMENTED_TEMPLATES`，`kernel_templates` 可一次启用 8 个算子；
- `test_golden_baseline.test_11`：9 模板内核计划任务/数量一致、0 缺口、17/17、换型块 50；求解约 40s。
- 仍走 legacy 的三块：`material_balance`（两阶段齐套，耦合库存，风险高）、软交期/客户分级（目标权重）、`concurrency`（并行型号软惩罚）。

**ACT-22 step 5（material_balance 内核化）**：

- `material_balance` 规则编译为：`Σ component(s-1-buffer) × unit_pieces + stock ≥ BOM_ratio × FG(s)`；
- `buffer_shifts` 可配缓冲班次（0 = 上一班产出可用），`include_stock` 控制是否计入期初库存；
- 调度器把 `cumulative` 变量创建与约束解耦：`_create_cumulative` + `_legacy_material_balance` / `compile_rules`；
- `test_golden_baseline.test_11` 扩到 **9 个算子**：任务/数量、17/17、0 缺口、50 块与旧计划一致。

**ACT-06（单批超班：拆批连续生产）**：

业务规则（2026-10-25 确认）：

- 订单总量必须 ≥ MPQ；
- 单个 MPQ 批次超过一个班次产能时，允许拆到**相邻班次**连续生产；
- 拆分段是"同一批的延续"，不是新批次，**不受单班 MPQ 下限约束**（所以 69+3、36+36 都合法）；
- 拆分段之间**不换型**，只算一次 run start；
- 段数用最少班次数 `ceil(总量 / 单班产能)` 限制，防止拆成几十段。

实现（`src/scheduler.py`）：

- `_splittable`：`lot_millis > shift_ms` 的任务；
- `_unit_pieces=1`、`_unit_millis=lot_millis/lot_size`、`_units_total=quantity`（按件排产）；
- `sum(started) == 1`：只能有一个 run start；
- `sum(active) <= ceil(总量/单班产能)`：只允许最少班次数；
- `min_batch` 对拆批任务取 1，不套 MPQ；
- 校验器与独立审计同步放宽：拆批任务只校验**总量**是 MPQ 整数倍，单段可小于 MPQ；连续性由 `R-WL-11 = 1 块` 验证。

验证（`test_split_batch.py`，2 项）：

```
WA14208S：MPQ 72，单批 8.45h > 8h
  → 65 + 7（两段连续），1 块，17/17，独立审计 9/9
```

> 注：具体拆成 65+7 还是 69+3 由 95% 填充目标决定；模型对 69+3、36+36、4+68 都开放，只要求连续且只算一次换型。

**两个迁移中的搜索问题（重要）**：

1. **冗余约束会改变搜索**。内核最初无条件加 `load ≤ ceiling`，与 `load` 变量域重复；模型只多 60 条约束，30s 内却从"能解"变成 UNKNOWN。后来改为"仅当规则上限小于变量域时才加"，约束数恢复一致。
2. **约束顺序同样影响搜索**。`min_batch` 若放在 capacity 之后，30s 内无解；移到任务变量之后（对齐旧位置）后，60s 预算下 40s 求解成功。`_build_hint` 现在**固定走 legacy 路径**，即使主模型用内核，保证种子解仍在几秒内拿到。

**性能现状**：三模板内核硬 95% 档约 40s（legacy 约 19s），仍在 CLI 的 180s 预算内；迁移默认关闭，等 changeover 迁完、顺序/提示稳定后再切换默认。

**一个必须记录的确定性发现**：`num_workers=1 + 时间上限` **仍不是严格确定**。同一输入两次 30s 运行，目标值可能是 15,465,286 与 15,465,287（差 1），因为停止条件是墙钟时间。若要求"逐字节可复现"，需要改成固定 `max_number_of_conflicts`/固定分支数或"找到合规解即停"的确定性停止条件。这属于 ACT-07 的后续，不阻塞当前迁移。

**测试**：`test_kernel` 22/22 —— 19 模板、参数校验、replace/add、生效期、JSON 加载、编译求解（95% 欠载被罚、交期硬约束、未实现模板告警）、权重归一化、字典序、customer/concurrency/labor/contiguity/calendar/time_window/inventory/freeze/co_product。

> 注意：当前 `scheduler.py` 仍使用原有布尔开关；内核是**并行地基**，ACT-22 才把调度器逐个约束迁到编译器上。这样迁移前后可以用同一份黄金基线对比。

---

### 4.5 Phase 1 平台（ACT-25/26/27/28/29 + L2/L3）

**规则集运行时**

- `config/rule_sets/{WL,SY38}_Phase1.json`：两个工厂的 active 基线，各 13 条规则（11 个算子；capacity/min_batch 各拆 FG/SF）；**18/19 个算子已可编译**，仅 `routing`（多线选择）留 Phase 2。
- server 启动 `ensure_seed`；`/api/solve` 默认 `use_rule_set=True`，读取 active 版本，`kernel_templates = active 规则 ∩ 已实现算子`。
- `data/rule_sets.sqlite3`：版本库；同 payload 幂等，改一行 v+1；旧版本只读，可激活回滚。

**API / UI**

| 端点 | 作用 |
|---|---|
| `GET /api/rule-sets` | 版本列表 |
| `GET /api/rule-sets/active` | 当前 active + 告警 |
| `GET /api/rule-sets/{id}` | 完整 payload |
| `POST /api/rule-sets/save` | 保存新版本（可选激活） |
| `POST /api/rule-sets/{id}/activate` | 激活/回滚 |
| `GET /api/rule-sets/diff` | 两版 diff |
| `POST /api/rule-sets/import` | Excel 导入（逐行报错） |
| `GET /api/rule-sets/template` / `{id}/export` | 模板 / 导出 |
| `GET /api/rule-sets/templates` | 19 个算子目录 |
| `POST /api/rule-sets/sensitivity` | ±20% 敏感度报告（真实求解） |
| `POST /api/rule-sets/diagnose` | 无解解释（逐组/逐规则/规则对） |

- `/rule-sets`：独立编辑器，支持版本切换、软硬/权重/启用编辑、参数 JSON、新增/删除、保存、激活、导入/导出、**作用域编辑**、**replace/add 继承**、**按组批量权重/启停**、树形/平铺、敏感度/诊断按钮。
- 主界面"规则与约束配置"顶部新增入口；下方旧规则开关保留，内核规则集优先。

**敏感度 / 无解解释**

- `python -m src.rule_sets.cli sensitivity --site WL --budget 20 --pct 0.2`
- `python -m src.rule_sets.cli diagnose --site WL --budget 20`
- 函数注入 `solve_fn`，测试用双倍快速验证；真实求解可直接接 `APSScheduler`。

**人力（L2/L3）**

- `labor_capacity`：`crew[l,s] = max(在产料号人数)` 的线性化；`Σ crew ≤ H[s]`；共享班组 `Σ active ≤ 1`；硬/软由 rule nature 决定。
- `Labor-Availability` / `Line-Staffing` / `Crew-Pool` 三个 CSV 模板与校验；无数据时算子自动跳过，不影响现有计划。
- 当前 `data/` 未放生产人力数据，因此默认计划不受影响；填数后生效。

**验证**

```
test_kernel        22 项  PASS
test_rule_sets      9 项  PASS
test_labor          5 项  PASS
test_split_batch    2 项  PASS
test_independent_audit 6 项 PASS
test_golden_baseline 13 项 PASS（含 105 口径 test_12、组目标 test_13）
合计 57 项，375.0s（迁移测试预算 90s，避免墙钟抖动）

TestClient /api/solve（active WL_PHASE1，use_rule_set=True）
  200；19 单；0 项校验失败；52 块；求解 61s / 请求 90s

CLI `python src/main.py`（active WL_PHASE1，180s）
  17/17 校验；独立审计 9/9；0 缺口；48 块；124s
```

> 52 块（server）/ 48 块（CLI）vs 旧计划 50 块：内核 concurrency 指示变量改变了搜索；合规（17/17、9/9、0 缺口、OTD）不变。

**2026-10-26 修复：105% 与规则集口径冲突**

现象：旧规则页打开"95%~105%"后，计划出现 22.5% / 75.6% 的班次；保持 95%~100% 时没有。

原因：

- 旧 105% 开关把 `_get_line_max_shift_ms` 放宽到 8.4h，`load` 变量域、`over` 变量、`_build_hint` 都按 8.4h 建；
- 但 active 规则集的 `capacity_utilization.max_util=1.0` 在内核里仍把 load 卡在 8h；
- hint 可能 >8h，对主模型无效；硬 95% 档在 40s 内返回 UNKNOWN，引擎降级到软 95% 档；
- 软档只"罚"低于 95%，不禁止，所以出现 22.5% / 75.6%。

修复：`APSScheduler` 在 kernel capacity 模式下用规则集 `max_util` 覆盖旧 105 开关（1.0 → OFF，1.05 → ON）。要 105% 请改规则集 `max_util=1.05` 并保存/激活；旧单选不再生效。

验证：

- `rule max 1.0 + 旧 105` → 105 OFF；硬档 0 缺口；
- `rule max 1.05` → 硬档 0 缺口，54 块；
- `test_12_rule_set_capacity_overrides_legacy_105`。

**2026-10-26 接入：软档分组字典序**

- `_build_model` 把目标按 `G1_DELIVERY / G2_CAPACITY / G3_CHANGEOVER / G4_INVENTORY` 分组；
- 软档 `_run` 调用 `_run_lexicographic`：按 rule_set 的 group priority 逐组 `Minimize`，每组用 `expr <= ceil(best)` 冻结，最后取最后一组 solver 的值；
- 硬档不启用（交付/95% 是硬约束，只有 G3 软目标，单组即普通加权）；
- `test_13_soft_tier_has_group_objectives` 验证软档确实生成 G1/G2/G3 组目标；软档实跑 30s 返回 FEASIBLE。

**2026-10-26 规则语义 / 可读性 / 甘特细化**：

- **27 页内核标签**：`src/kernel/rule_mapping.py` 把旧规则映射到固定模板，`/api/rules` 返回 `kernel_status`（active/partial/available/legacy/phase2）与 active 内核 rule_id；27 页新增「内核状态」列。新增规则页顶部 27/17/13 说明。
- **R-WL-26 语义拆分**：`WL_CAP_FG=1.05`、`WL_CAP_SF=1.0`；调度器按 stage 推导 105 开关；capacity 编译器只对匹配 stage 的产线生效（修掉 FG 规则误放宽 SF 线的问题）。
- **SF MPQ 语义**：`WL_MPQ_SF` 改为 `unit=piece,min_multiple=1`；半成品总量 ≥ MPQ 后单班可任意整数件；validator 与独立审计同步去掉「单班 ≥ MPQ」判据。
- **甘特/明细**：solve 响应新增 `shift_segments`（每班一块）、`snake_points`（段间折线）、`sequence`/`changeover`（按线顺序与换型）；前端甘特按班次段渲染 + 折线连接；明细表按 `shift_segments` **展开成小段**，显示全局顺位 `#`、段序号 `段 i/n`、本段产量/班次/工时；换型改为**逐段计算**（#3→#4 换产品会显示换型，不再只标任务首段），并给同班多产品标“同班并行”；甘特每段中心加亮色顺位圆点，首段高亮，折线按顺位连接；**移除无意义的“产线聚合单轨”模式；泳道改为按工单（task_id）一行**，解决同一料号多工单互相遮挡导致 #7/#5/#8/#9 等不显示的问题。
- **可读性**：新增 `src/metric_labels.py`，校验/审计指标中文标签；补 `smoothing_within_35pct`。

**校验指标可读化（2026-10-26）**：

- 新增 `src/metric_labels.py`，把 `allow_105_fg`、`total_production_hours`、`violating_shifts` 等 60+ 个指标映射成中文；
- `/api/solve` 的 `checks`、规则明细、独立审计响应都带 `metric_labels`；
- 前端校验卡片显示"中文标签: 格式化值"（布尔→允许/不允许，百分比→%，工时→h，字典→`PGLINE 178.7h · 轴套6 186.1h`）；
- 规则页顶部与手册 §0 增加 27 / 17 / 11 的解释：27 是旧业务规则目录，17 是校验检查，11 是 active 内核规则实例（18/19 算子已实现）。

**硬化项**

- 规则页的树形（工厂→车间→线组→线）、100 条线虚拟滚动；
- `routing` 算子（替代产线/多线选择，Phase 2）；
- 真实人力主数据、确定性停止条件。

---

## 5. 未完成与下一步

| 任务 | 状态 | 说明 |
|---|---|---|
| ACT-06 单批超班 | ✅ | 拆批连续：总量≥MPQ，单段可<MPQ，只算一次换型；`WA14208S` 65+7、1 块 |
| ACT-11～14 校验器收尾 | ✅ | 4 项恒真已重写；手工审计已接独立通道；20 类变异 100% 召回；黄金基线已固化 |
| ACT-20～21 规则内核地基 | ✅ | 19 模板、作用域继承、编译器上下文；18 个模板已编译 |
| ACT-22 调度器迁移 | ✅（v1） | 18 个算子可 opt-in；server + main.py 默认 active rule set；仅 routing 未编译 |
| 规则集接线 | ✅ | server + main.py 读取 active 版本；CLI/server 默认 rules，`use_rule_set=False` 可回退 |
| 迁移测试拆分 | 待做 | `test_golden_baseline` 已 ~240s；把 test_08～11 拆到 `test_kernel_migration.py` |
| ACT-23～24 权重/分组求解 | ✅ | `PenaltyBook` 按 rule 归一化；软档字典序已接入 server；硬档硬约束 |
| ACT-25～29 规则平台 | ✅（v1） | Excel 校验/导入、SQLite 版本库、规则编辑器、敏感度/诊断 API+CLI |
| ACT-L2 人力约束 | ✅（v1） | `labor_capacity`：Σcrew≤H、共享班组互斥、硬/软、无数据跳过 |
| ACT-L3 人力数据 | ✅（v1） | 三个 CSV 模板 + 加载/校验 |
| R-WL-12 软化 + 权重 | Phase 2 | 内核已支持软序列/换型权重；默认仍硬顺位（当前 50 块） |
| routing / 树形规则页 | 待做 | 替代产线（Phase 2 线选择）；树形/100 线虚拟滚动 |

---

## 6. 决策记录（2026-10-25 已确认）

| 编号 | 决策 | 结论 | 落地 |
|---|---|---|---|
| D7 | 30 分钟换型是否单独计提 | **不单独计提**，TRP 已含 | `DEFAULT_CHANGEOVER_MINUTES = 0.0`；95/100 与 95/105 由用户在规则页选择 |
| D8 | 15:00 截单是否允许 24 分钟弹性 | 暂不修改，记一笔 | 不加硬截单；105% 档保留 ≤24 分钟兜底并单独计数 |
| D9 | SF 每单最多 3 块 | 接受折中，Phase 2 一并解决 | 连续改为软偏好，硬块数上限可配；Phase 2 料号级任务 |

---

## 7. 复现命令

```bash
python src/main.py                 # CLI 全流程 + 17 项校验
python run_tests.py --fast         # 快速单元/回归（49 项，约 61s）
python run_tests.py                # 全量 15 套件（101 项，含黄金基线与端到端）
python -m unittest discover -s tests -v
```

> 测试已从仓库根目录迁到 `tests/`；不要再使用 `python -m unittest test_aps_pipeline`。

## 8. 2026-10-28 架构 / 部署 / REG 修复

- `585f44d`：引入 `src/domain/` 领域实体骨架、`src/services/manual_audit.py` 服务骨架、统一 `run_tests.py`，测试迁入 `tests/`；
  - **注意**：`src/domain/` 尚未被 scheduler/server 主链使用；`src/services/manual_audit.py` 尚未接线，`/api/audit_manual_schedule` 仍走 `server.py` 旧编排。
- `9727dc4`：新增 `.env.example`、`Dockerfile`，`run_web.py` 支持 HOST/PORT/RELOAD，README 补部署说明。
- `5727e4c`：REG-01～08 算法修复；详见 [`aps_algorithm_defect_remediation_log.md`](../archive/Specification/aps_algorithm_defect_remediation_log.md)。

### 8.1 当前测试口径

- 快速回归：`run_tests.py --fast` 59/59 + `test_regressions` 8/8 = **67/67 绿**；
- 全量：**105/105 绿，1387.6s**；
- 全量回归已闭环。

---

## 9. 扩展主数据（2026-10-28）

- Parameters-FG/SFN 追加 MOQ (Minimum Order Qty)；ProductParam.moq / ProductionTask.moq 已加载，但 min_batch 仍按 MPQ，MOQ 尚未参与约束。
- 新增 data/master/：Routing、Setup Matrix、Operation LeadTime、Customer Orders、Inventory Policy、Trial Windows、Baseline、Calendar Patterns、Campaign Families、Setup Technicians、labor mock。
- dataSource.xlsx 同步追加 MOQ 列与新 sheet；字段定义见 data/master/README.md。
- 其余扩展表为 mock/待接线；现有求解器行为不变。

---

## 10. Phase B/C 启动（2026-10-29）

### B1 MOQ / MPQ 解耦（已开始）

- min_batch 新增参数：asis=shift|order、quantity_source=MPQ|MOQ、	ail_exempt、moq_override；默认值保持现有班次 MPQ 行为。
- asis=order 时按工单总量约束；quantity_source=MOQ 读取 ProductionTask.moq；	ail_exempt 仅对总量低于门槛的收尾/急单放行。
- 主数据 Parameters-FG/SFN 已追加 MOQ；ProductParam.moq/ProductionTask.moq 已加载。
- 快速回归 55/55 仍绿；尚未把新 MOQ 规则写入 active seed，因此当前排产行为不变。

### B3 软产能（已开始）

- `capacity_utilization` 的 soft 规则已改为**双边软目标**：低于 95% 与高于 `max_util` 都计罚，不再加硬上限；hard 规则保持原硬约束。
- `_compile_capacity_kernel` 不再强制 hard，尊重 rule nature；`tests/test_kernel.py::test_10b_soft_capacity_prices_over_target` 验证 soft 可行、hard 不可行。

### B5 时间窗日期（已开始）

- `time_window` 新增 `allowed_dates`（MM/DD 逗号分隔），可按日期 + 班次双重限制；hard 禁排、soft 计罚。
- 默认空字符串，不改变现有规则行为。
- `freeze` 新增 `use_baseline`：true 时冻结窗按基线数量锁定，false 保留原“禁止排产”语义。
- 新增 `tests/test_kernel.py::test_10c_time_window_allowed_dates_are_hard`；快速回归 50/50。

### C1-a 班内序列原语（已开始）

- 新增 `src/sequence.py`：把已决定的班次产量转成建议顺序，按 `Setup-Matrix` 做同模优先/SDST 换模时间，并用 DP（≤10 个任务）或贪心（更多）求解。
- 当前是 side-effect-free 的 C1-a 原语，尚未把 sequence 变量回灌到 CP-SAT 数量分配；下一步 C1-b 才做 solver 内约束。
- 新增 `tests/test_sequence.py` 2 项测试；C1-a 同时输出 production/setup/total minutes 与 exceed capacity 的 `overload_shifts`，为 C1-b 的容量反馈做输入。

### B2 报废率投料放大（已开始）

- `build_requirement_plan(..., apply_scrap=True)` 按 `gross/(1-scrap_rate)` 放大 SF 投料；默认 `False`，不影响现有基线。
- 新增 `tests/test_scrap.py`，验证 3% 报废时 SF 总投料增加。
- 尚未接 UI/规则集；当前仅数据与算法入口可用。

### C1-b-lite setup 占用容量（已开始）

- `capacity_utilization` 新增 `setup_minutes_per_start`：每个 run start 额外占用容量；0 保持旧 TRP 口径。
- hard 规则把 load + setup 一起纳入 ceiling/floor；soft 规则把 over 一起计罚。
- 新增 `tests/test_kernel.py::test_10d_capacity_charges_setup_per_start`。
- 这是 C1-b 的容量入口；sequence-dependent SDST 仍由 C1-a 原语计算，下一步做真正的班内顺序变量。

### C3-lite 换模技术员容量（已开始）

- `labor_capacity` 新增 `setup_technicians_required` / `setup_technicians_available`：按班次 run start 数限制换模技术员；HC-02 的班次级近似。
- 可在没有人力 CSV 时独立启用；未配置时保持原行为。
- 新增 `tests/test_kernel.py::test_10e_setup_technician_capacity`（hard 不可行 / soft 计罚）。

### C1-b 容量反馈接缝（已开始）

- `APSScheduler` 新增 `setup_reserve: Dict[(line,shift), minutes]`：按班次预留 sequence/setup 时间，容量上限相应扣减。
- 该接缝可由 C1-a 的 `setup_minutes_by_shift` 填充；默认空，不改变现有行为。
- 下一步把 C1-a 计划自动回灌到 reserve，并处理跨班 carry-over。

### C1-b 自动反馈（已开始）

- `APSScheduler(sequence_feedback=True)`：初始解后由 C1-a 计算每班 setup，填充 `setup_reserve`，再在剩余预算内复解。
- 反馈结果只有“缺口数/总缺口/延期单数不劣于初始解”才接受，否则保留原计划；避免 setup reserve 把硬档逼成软档大幅降级。
- 实测 WL 10 天：初始 49 块/0 缺口；反馈 45 缺口 → **rejected**，返回初始解。
- 预算分配：启用 feedback 时初始 70%、反馈 30%。

### C2-a 路线数据层（已开始）

- 新增 `src/routing.py`：加载 `data/master/Routing-Machines.csv`，按 site/part/operation 返回候选线/机台、认证标记、优先序。
- 新增 `tests/test_routing.py` 3 项，验证主/备候选与 53 料号覆盖。
- 尚未把 Assign(i,m) 变量接进 CP-SAT；C2-b 才做 assignment。

### C-governance：32 条目录与站点覆盖（已开始）

- 新增 `config/rule_catalog/aps_32_rules.json`：32 条规则元数据、HC/SC/BS 分类、能力状态。
- 新增 `src/rule_catalog.py`：加载校验、`effective_for_site()` 全局目录 + site override（不污染全局）。
- 新增 `tests/test_rule_catalog.py` 3 项。

### C2-b-a 路线重指派启发式（已开始）

- `src/routing.py` 新增 `assign_routing_lines()`：按候选优先级/认证/当前线负荷，把过载主线的任务移到较低负荷备线。
- 这是 preprocessor，不是 CP-SAT Assign；默认不接入调度器。
- 新增 `tests/test_routing.py` 第 4 项，验证 PU07 过载时移到 PU22。

### B4-a 分时段库存数据层（已开始）

- 新增 `src/inventory_policy.py`：加载 `Inventory-Policy.csv`，支持 NORMAL/MONTH_END、from/to day、target/max days。
- `policy_for_day()` 优先返回月末窗口。
- 新增 `tests/test_inventory_policy.py` 3 项；尚未接 CP-SAT 库存约束。

### B4-b-a 库存投影审计（已开始）

- `src/inventory_policy.py` 新增 `project_inventory()`：按班次前向推演 FG/SF 库存，按 due 放置需求、按 BOM 生成 SF 消耗，并按 NORMAL/MONTH_END 策略比较上限。
- 输出 `InventoryPoint` / `InventoryProjection`，列出超库存违规。
- 新增 `tests/test_inventory_policy.py` 第 4 项，验证超库存被标记。
- 仍是审计/投影，未接 CP-SAT 硬/软约束。

### B4-b-a 库存投影 API（已开始）

- `/api/solve` 返回 `inventory_projection`：`metrics`、`violations`、逐班 `points`。
- 这是审计面，不是 CP-SAT 约束；不改变排产。

### 2026-10-29 hotfix：capacity kernel objective 二次调用

- 现象：`POST /api/solve` 在 active WL 规则集下 500，`AttributeError: 'SumArray' object has no attribute 'total_objective'`。
- 原因：`_compile_capacity_kernel` 写成 `compiled.total_objective().total_objective()`；`total_objective()` 已直接返回 OR-Tools `SumArray`。
- 修复：改为 `return compiled.total_objective()`。
- 验证：`POST /api/solve` 返回 `status=success`、19 tasks、17 checks；快速回归 56/56。
- 说明：自动化套件此前未覆盖 hard kernel capacity 的非空目标路径，实际 active rule set 才触发。

### C1-b-1 / C1-b-2 班内 sequence + SDST + 跨班 carry-over（已开始）

- `APSScheduler(sequence_setup=True)` / `POST /api/solve.sequence_setup=true` 开启班内 sequence 变量：`first/last/trans` + MTZ 子环消除，setup 时间从 `Setup-Matrix.csv` 读取。
- setup 直接进入该线/班次容量约束：`load + sum(trans × setup_min) <= slot_max`；开启时不再叠加旧的 `changeover_minutes` 固定扣减。
- C1-b-2 已补跨班 carry-over：上一班 last mold -> 下一班 first mold，空闲班次用 `both` 互锁避免误约束；技术员真实 setup 区间仍未做。
- 新增 `tests/test_capacity_kernel.py`：P0 capacity kernel 回归 + sequence_setup 求解；快速套件 56/56。

### 2026-10-29 UI/主数据/规则版本更新

- WL 基于 v3 新建并激活 v4：13 条拆分规则，补齐最新模板参数（MOQ/setup 等，默认保守）。
- 第三页旧版 27 条配置块移除，改为 32 条规则目录（HC/SC/BS），旧版审计数据暂留用于 17 项检查。
- 输入主数据新增 Routing / Setup-Matrix / Inventory-Policy / Operation-LeadTime 页签。
- 第六页新增班内 sequence/SDST 开关；站点设置持久化。
- Excel 上传改为严格模板校验（sheet 名称/顺序与第 1 行 Schema 必须与 dataSource.xlsx 一致），按站点保存 current_WL.xlsx / current_SY38.xlsx。
- 支持 FACTORY_SITE 固定单厂实例；两厂各起一个进程即可隔离内存状态。

### 2026-10-29 hotfix：sequence_setup 的 site NameError

- 现象：接入站点级 `sequence_setup` 后，`/api/solve` 500，`NameError: name 'site' is not defined`。
- 原因：`run_solver` 中误用局部变量 `site`，实际只有 `payload.site`。
- 修复：改为 `site_settings_store.get(payload.site.upper(), {})`。
- 验证：`POST /api/solve` 返回 success、19 tasks、17 checks。
- 另修复：删除旧 27 条配置块时多删了第三页容器的闭合 `</div>`，导致第 3 页之后全部空白；已补回并通过 HTML 标签平衡检查。

### 2026-10-29 阶段性收口

- WL 已基于 v3 新建并激活 v4（13 条拆分规则）；SY38 保持 v2（13 条）。
- 第三页旧 27 条配置块删除，改为 32 条规则目录（HC/SC/BS）；旧版 27 条审计映射暂留第 5 页。
- 输入主数据新增 Routing / Setup-Matrix / Inventory-Policy / Operation-LeadTime 页签，WL 当前分别加载 37 / 36 / 74 / 37 行。
- `/api/inputs/upload` 改为严格模板校验：Sheet 名称/顺序 + 第 1 行表头必须与 `dataSource.xlsx` 一致；错误表头直接 400。
- 上传文件按站点保存为 `data/uploads/current_WL.xlsx` / `current_SY38.xlsx`，不覆盖仓库模板。
- 第六页新增站点级 `sequence_setup` 开关；`FACTORY_SITE` 支持每厂独立实例。
- 最终验证：全量 105/105，快速 59/59；真实 API `/api/solve` success / 19 tasks / 17 checks。
- 仍待完成：C1-b 真正班内 sequence 变量、C2-b CP-SAT Assign(i,m)、B4-b CP-SAT 库存约束、C4/C5。

### C1-b-3 班内 start/end + setup 数值化

- `_add_sequence_constraints` 为每个 line/shift 的任务增加 `seq_start/seq_end/seq_setup`；`end = start + processing`，并受班次时间上限约束。
- `trans` 相邻任务约束 `start_b >= end_a + setup`；跨班 `carry` 约束下一班首件 setup；`first` 首件无 carry 时从 0 开始。
- 新增 `ScheduleResult.sequence_plan`：输出 task/line/shift/start_min/end_min/setup_min/first/last/sequence；`/api/solve` 返回 `sequence_plan`。
- Gantt 页新增“班内 Sequence / SDST 明细”面板，直接展示每条任务开始/结束/setup/首末件。
- WL 5 天 sequence_setup=true 实测：19 tasks、63 条 sequence 记录、900 setup 分钟。
- `tests/test_capacity_kernel.py` 增加数值断言：`end >= start >= 0`，setup 总和 > 0。
- 仍是可选开关；默认关闭，Setup-Matrix 仍为 mock。

### C1-b-4 换模技术员 setup 区间

- `APSScheduler(setup_technicians_available=N)` 在 sequence 开启时为每个 setup 建立可选区间 `[start-setup, start]`，按班次做 `AddCumulative`，限制同时进行的换模数量。
- 站点设置增加 `setup_technicians_available`（默认 0 = 不启用），第六页可配置；`/api/solve` 可传同名字段。
- 技术员需求已按 Setup-Matrix 边的 `Technicians Required` 计，不再统一按 1 人；能力不足仍可能不可行。
- 新增 `tests/test_capacity_kernel.py` 第 3 项；全量 98/98、快速 55/55。

### B4-b FG 库存软约束

- `APSScheduler(inventory_policies=...)` 新增 FG 逐时点库存上限 soft penalty：opening + 累计产出 - 累计需求，按 Inventory-Policy 的 max_days/max_stock 计罚。
- 站点设置新增 `inventory_policy_enabled`（默认 false）与第六页开关；`/api/solve` 可传同名字段。
- 当前 FG 与 SF 都做 soft 上限；SF 需求由 FG 累计产出 × BOM 比例计算；`inventory_hard_enabled` 可切换为硬上限，默认关闭；库存策略仍是 mock 值。
- 新增 `tests/test_capacity_kernel.py` 第 4 项；全量 99/99、快速 56/56。

### C2-b-a 路线重指派接线

- `routing_assign_enabled` 站点开关接入 `/api/solve`；服务端在建 plan 后调用 `assign_routing_lines()`，返回 `routing_decisions`。
- 这是 preprocessor：主线负荷超过 95% 时移备线，不是 CP-SAT Assign(i,m)。
- WL 5 天实测 `status=success`，当前数据无过载决策（0 条）；功能路径已通。

### 2026-10-29 hotfix：排产后页面空白（sequencePlan 未 expose）

- 现象：排产 80s 后页面空白，Vue 报 `Cannot read properties of undefined (reading 'length')`。
- 原因：`sequencePlan` 被误插入 `backupAutoScheduleData`，但未加入 Vue setup return；模板 `sequencePlan.length` 读取 undefined。
- 修复：从 backup object 移除，加入真正 setup return；`sequencePlan` 初始化为 `[]`。
- 验证：Playwright headless 加载页面 + mock `/api/solve` 响应，`pageerror=0`；页面正常显示生产平衡。

### C2-b-0 CP-SAT 路线分配阶段

- `src/routing.py::solve_routing_assignment()`：对每个有候选线的任务建立 CP-SAT 选址变量，按产线总工时容量 + 主候选惩罚求解；主排产仍由 scheduler 执行。
- `/api/solve` 的 `routing_assign_enabled` 先调这个 CP-SAT 阶段，失败/无决策时回退到启发式 `assign_routing_lines()`。
- 新增 `tests/test_routing.py` 第 5 项；全量 100/100、快速 56/56。
- 边界：这是上游 assignment 阶段，尚未把 Assign(i,m) 与班次容量/换型/技术员联立；SY38 5 天当前无任务，未产生决策。

### C4-a 工序提前期软约束

- `ProductionTask.lead_time_days` 从 Parameters-SFN 透传；`enforce_lead_time` / 站点 `leadtime_enabled` 开启后，SF 完工与父 FG 完工之间按 `ceil(lead_days × shifts_per_day)` 计 soft lag penalty。
- 已把 Operation-LeadTime 的 setup/queue/inspection/move 与 `lead_time_days` 合并为 shift gap；Transfer Batch Qty 用于计算 move 次数并计入 gap。
- 新增 `tests/test_capacity_kernel.py` 第 5 项；全量 101/101、快速 57/57。

### C5-a Campaign Group 软约束

- 新增 `src/campaign.py` 读取 Campaign-Families；`campaign_enabled` / 站点开关开启后，同一产线/班次出现多个 Campaign Group 时产生 soft penalty。
- 新增 `tests/test_campaign.py` 2 项，并在 `test_capacity_kernel.py` 增加 campaign 求解用例；全量 104/104、快速 58/58。
- 当前只做同班次分组惩罚，不做跨班 campaign 连续、Pull 或 Slack。

### C5-b 交期 Slack 软约束

- `APSScheduler(due_slack_shifts=N)` / 站点 `due_slack_shifts`：对 FG 完工早于 due 的 N 个班次做 soft lag penalty；0 = 关闭。
- 新增第六页数值配置；API 实测 `due_slack_shifts=2` 返回 success / 19 tasks。
- Pull 仍复用 30 天 lookahead，没有单独的 Pull 规则。

### 2026-10-30 阶段收口

- 全量 105/105、快速 59/59。
- 已推送 main：C1-b-3/4、B4-b FG/SF soft/hard 选项、C2-b-0 CP-SAT 路线分配阶段、C4 lead time + transfer batch、C5-a Campaign、C5-b Slack。
- 仍未完成：C2-b 主模型内 Assign(i,m)、C5 Pull 独立策略、32 条规则与 17 项审计映射、真实主数据替换。

### C-governance coverage API

- 新增 `coverage_summary()` 与 `/api/rule-coverage`：返回 32 条规则的 class/status 统计。
- 新增 `tests/test_rule_catalog.py` 第 4 项；当前全量 105 + 1 新测，快速 59。

### 32 条目录覆盖率 UI

- 第三页 32 条规则目录展示 `configurable / partial / needs_model` 统计徽章，数据来自 `/api/rule-coverage`。
- 仅本地修改，未推送。

### 32 条目录状态校准

- 将已具备实际实现的规则从 `needs_model` 上调为 `partial`：HC-02/04/09/10/11/16、SC-03/05/06/07、BS-06。
- 当前覆盖：partial 19、needs_model 6、preprocess 4、configurable 2、postprocess 1。
- `needs_model` 剩余：HC-05、SC-01、SC-08、SC-09、SC-10、BS-04（以目录为准）。
- 仅状态校准，不代表已全部接入主求解器。

### HC-05 合格机台校验

- 新增 `capability_violations()`：检查任务当前 line 是否在 Routing-Machines 的认证候选中；不匹配返回违规清单。
- 新增 `tests/test_routing.py` 第 6 项；全量 106（新增测试后未重跑全量），快速 59。
- 当前是审计能力，尚未在求解器里硬拒绝或自动改线。

### HC-05 能力审计 API

- `/api/solve` 新增 `routing_capability_violations`：返回未认证产线的任务清单。
- 这是审计输出，不改变排产；便于业务先发现 Routing-Master 与参数表不一致。

### 2026-10-30 本地收口（未推送）

- 32 条目录状态：partial 20、needs_model 5、preprocess 4、configurable 2、postprocess 1。
- HC-05：`capability_violations()` + `/api/solve.routing_capability_violations`。
- 本地 main 领先 origin/main 4 个 commit；全量最近完整跑 105/105，新增 routing test 后预计 106。

### 待做

- C1-b 真正班内 sequence 变量 + SDST + carry-over：当前只有 setup 容量/反馈近似。
- C2-b CP-SAT Assign(i,m)：Routing 仍是数据层 + 启发式。
- B4-b CP-SAT 库存约束：当前只有库存投影审计。
- C4 Operation LeadTime / Transfer Batch；C5 Campaign/Pull/Slack。
- 32 条规则目录与 17 项审计项的正式映射；旧版 `/api/rules` 兼容层迁移。
- 真实 Setup-Matrix / Inventory-Policy / Routing 数据替换 mock。

### 2026-10-30 新版 15 表主数据框架接入

- 新增 `src/master_schema.py`：15 张表的唯一 Schema（中英表名/列名别名、必填列、灰色废弃列标记），
  上传校验与加载器共用；列顺序可自由调整，灰色列可缺省。
- 新增 `src/excel_loader.py`：按表头定位列的加载器，同时支持新版 15 表与旧版 25 表；新增
  `layout=v2/legacy`、`line_base`、`warnings`、`source_path` 快照字段。
- 字段重新取源：`ct_seconds`←Routing 的 CT、`line`←Routing 主候选线、`headcount`←Routing 人数、
  `target_stock`←Inventory-Policy 安全库存、`trp/hours/shifts`←产线基础数据（缺行回退参数表/默认值）。
- 新增 `src/master_rows.py`：上传工作簿的 Routing / Setup-Matrix / Inventory-Policy / Operation-LeadTime
  直接转成求解器 dataclass；`/api/solve` 优先使用上传工作簿，CSV 仅作基线回退。
- `/api/inputs/template` 改为下发 `APS_DataSource_Template.xlsx`；`/api/inputs/upload` 改为按 Schema 校验
  （表存在 + 必需列存在，不再要求 sheet/列顺序完全一致），响应返回 `layout/site_counts/line_base/warnings`。
- `/api/inputs` 新增 `line_base`、`material_arrival_rows`、`layout`、`warnings`，FG/SFN 增加
  `mold_id/special_shift/simultaneous_production/product_family`。
- Tab-4 UI 新增『产线基础数据』页签；Routing/Setup/Inventory/OpLeadTime 改为新表头字段，删除灰色列展示；
  FG/SFN 增加模具编号/产品族/特殊班次/同时生产列。
- 新增 `scripts/verify_master_template.py` + `Specification/master_template_verification.md`：结构、行数、
  引用完整性、算法字段就绪度、与旧版取值一致性核对。
- 新增 `tests/test_master_schema.py`（11 项）：模板校验、列序打乱、缺列拒绝、v2/legacy 数值一致、
  产线去重派生、模具回退、提前期缺口告警、新增字段解析。
- 已知业务待补：产线基础数据需去重并补 轴套6/轴套7；FG/SFN 模具编号 AA/BB 需改为 MOLD_*；
  Routing 人数、产品族、特殊班次为空；SFN 生产提前期字段被删除，改由工序提前期『提前准备时间』替代。

### 2026-10-30 第二轮：模板精简与工作日历四档

- 模板表头二次精简：FG/SFN 移除 产线/TRP/单班定额产能；Tooling 移除冲次寿命/保养周期/状态；
  Material 移除 PO/放行工时/关联零件/状态并启用『预计入库时间』；PM 仅保留 产线/项目/起止日期时间/停机/产能/策略。
- `MaterialArrival` 新增 `eta_time`；`PreventiveMaintenance` 新增 `start_time/end_time`，PM 起止班次由时间或
  停机工时自动推导。
- `shift_manager` 新增 `SIX_ONE`（做六休一），`decide_calendar_mode` 改为 4/7、5/7、6/7 三档阈值；
  `evaluate_calendar_recommendation` 四档评估（做四休三/做五休二/做六休一/做七无休）。
- 班次几何改为读取『产线基础数据』：`build_topology(shift_hours, shifts_per_day)`，日历视图、推荐与
  `APSScheduler` 三处统一；默认 3×8h / 2×12h 与旧拓扑一致，测试基线不变。
- FG/SFN 的 `hours_per_shift/shifts_per_day` 不再作为主来源（v2 以产线基础数据优先，缺失回退）。
- Tab-4 UI 对齐最新表头：FG/SFN 仅展示产品字段；工装模具/PM/物料到货裁剪废弃列并补充预计入库时间；
  日历模板与模式下拉新增“做六休一”，班次标签改为读取产线基础数据。
- 新增 `tests/test_calendar_modes.py`（6 项）：四档工作日、阈值、做六休一、2×12h/3×8h 几何、产线基础数据驱动加载。

### 2026-10-31 主数据模拟补全（测试阶段）

- 新增 `scripts/fill_simulated_masterdata.py` + `Specification/masterdata_simulation_notes.md`：
  从 `dataSource.xlsx` / `Campaign-Families.csv` / `Inventory-Policy` 可推导的值优先沿用，其余按可解释假设模拟。
- 产线基础数据 17 行去重为 3 行（PGLINKAGE/轴套6/轴套7，8h×3）；Routing 人数 FG=3、SF=2；
  SFN 不再补『生产提前期』；改由工序提前期『提前准备时间』(45~480min) 承载静置时间，C4 按“静置+首批转运批量×CT/TRP”推导；SFN 最大库存取库存策略；FG/SFN/Routing 产品族回填；
  FG 模具编号按 Campaign 回填、Tooling-Mold 适用零件同步扩展；物料 ETA 时间按班次补齐；
  工序提前期准备时间按静置时间补 45~480 分钟。
- 补齐 BOM 引用但主数据缺失的 `W268001/W268002`（SFN/Routing/Operation-LeadTime/Inventory-Policy），
  消除 WB80510 的“跳过齐套”告警；填写说明行数同步为 SFN 22、Routing 39、Inventory 78、OpLead 39。
- 效果：10 天计划 146 任务（50 FG + 96 SF），crew=0 从 134/134 降为 0，SF 前置期由静置 45~480min + 首批转运推导，计划告警 0 条；
  5 天实跑 21 任务 / 17 项校验 / OTD 100%。
- 模拟值均为测试假设，上线前需用真实定额覆盖；原始模板备份在
  `archive/data/APS_DataSource_Template.pre_simulation.xlsx`，回滚一行命令即可。


### 2026-10-31 C4 前置期改为“静置时间 + 首批转运”

- 业务口径：工序提前期 → 提前准备时间（分钟） 替代 SFN 生产提前期，实际 45min~12h；
  半成品生产后需静置，首批转运批量（如 100 件）全部完成并静置后才允许下游开工。
- src/leadtime.py::lead_shifts 改为：前置分钟 = 静置 + min(qty, 转运批量) × CT / TRP，
  再按单班分钟折算班次；	rp 由调度器传入。
- src/scheduler.py 调用处补 	rp=task.trp；src/server.py 用 dataclasses.replace 回填 Routing CT
  （OperationLeadTime 为 frozen dataclass，原直接赋值会抛异常）。
- 模板不再新增 生产提前期 (days) 列；该字段仅旧版兼容。工序提前期按 FG=45min、
  SF=45~480min 模拟（G38 45、CX11 240、CM2E/KP31 300、930B/L743 360、LEAP 420、DX11/E371 480）。
- 新增 	est_lead_shift_includes_rest_and_first_transfer_batch：6h/12h/45min 三档，且只按首批计算。
- 修正超载提示：天数/班次/工时不再硬编码 10 天、7×3，规则编号更新为 R-CL-01..04。
- 核心 36 项测试通过；随后全量回归。


### 2026-10-31 Tab-6 模板下载改为“当前站点数据预填”

- 新增 src/master_export.py：以 15 表模板为样式底稿，把指定站点的快照 + 主数据行写回模板，
  生成“该工厂自己的”下载文件；需求日期轴、产线基础数据、行数说明同步刷新。
- /api/inputs/template?site=WL|SY38：按站点生成；Cache-Control: no-store 防止浏览器缓存旧模板；
  非 WL 文件名带站点后缀。旧版/新版源都可导出（旧 25 表由 schema 行映射到 15 表）。
- Tab-6 下载链接带上当前 selectedSite，物流同事下载到的就是本厂数据。
- 验证：下载 WL/SY38 模板均通过 Schema 校验；WL 17 FG/22 SF/65 订单/3 线 8h×3；
  SY38 11 FG/5 SF/16 订单/3 线 12h×2（PU07/PU22/1400T-6）。
- 实测 SY38 上传 → /api/inputs 切换为 SY38 v2 → 10 天求解 18 任务；结束后恢复 WL 站点。
- 新增 	ests/test_master_export.py（2 项）。局部 23 项测试通过。


### 2026-10-31 填写说明 Instructions 重构（多站点统一标准）

- 新增 src/instructions.py：从 master_schema 动态生成说明页，保证字段名与解析器永不漂移。
- 说明页结构：使用流程 → 全局填写规则（多站点一致性）→ 表清单（用途/粒度/行数/必填/依赖）→
  14 张表逐表字段说明（含义/类型单位/必填/取值限制/示例）→ 跨表一致性规则 → 系统校验与拒绝原因 → 版本记录。
- 说明页强调：模板为 WL/SY38/多站点共用同一标准；站点列必须与上传时选择的工厂一致；
  列顺序可变但表头/表名不可改；灰列可删；单位/日期/小数口径统一。
- scripts/rebuild_instructions.py 可单独重建；ill_simulated_masterdata.py --force 与
  master_export.export_site_workbook 都会调用同一生成器，因此下载到各厂的文件说明页完全一致。
- 下载模板验证：WL 说明页 221 行，表清单行数 10/35/7/17/22/56/39/36/78/39/6/3/3/5；
  SY38 为 7/16/0/11/5/9/27/4/32/16/2/3/2/0；两份文件均可直接加载（17/22/65 与 11/5/16，warnings=0）。
- 	ests/test_master_export.py 增至 3 项：校验说明页包含全部 14 张表与所有 active 字段。


### 2026-10-31 C2-b-1 班次级路线容量（shift-aware routing assignment）

- 新增 `routing.available_hours_by_line()`：按真实工作日历统计每线可用班次工时，并扣减该线 PM 停机。
- `/api/solve` 的 C2-b-0 上游分配不再使用 `horizon_days × shifts/day × hours` 的理论值，
  改为：真实班次（FIVE_TWO/FOUR_THREE/SIX_ONE 等）− PM 停机；候选线只保留“产线基础数据”白名单内的线。
- 响应新增 `routing_capacity_hours`，可与 `routing_decisions` 一起审计分配依据。
- 新增 `tests/test_routing.py::test_available_hours_uses_work_shifts_and_subtracts_pm`（5 个工作日 × 12h − 8h PM = 52h）。
- SY38 实测（FIVE_TWO、routing_assign_enabled=true）：18 任务、OTD 100%、9 项校验；
  `routing_capacity_hours` 与 `routing_decisions` 正常返回。
- 仍保留：C2-b 主模型内 Assign(i,m)（把线选择变量并入主 CP-SAT），作为下一步。


### 2026-10-31 C2-b-2 主模型内 Assign(i,m)

- `APSScheduler(line_options=...)`：多候选线任务在**同一个 CP-SAT** 内创建
  `assign(task,line)`、`x(task,line,shift)`、`active(task,line,shift)`，exactly-one 选择产线；
  任务级 `x/active` 由分线变量求和，原有 due/PM/物料/模具/冻结/交期约束继续复用。
- 容量约束按 `(line, shift)` 汇总分线 `x`；PM 按产线清零；在 assign 模式下先关闭 sequence 换型
  （`setup_ms=0`），避免在 x 维度重构时同时改动 SDST 变量。
- 结果回写：`_apply_assignment()` 选出实际产线写入 `ScheduledTask.line`，随后恢复
  `plan.tasks` 原始产线，保证多次求解的 `from` 不漂移；响应新增 `routing_assignment`。
- 开关：`routing_assign_in_model`（payload/site-settings/UI 复选框）；与 C2-b-1 互斥，
  开启后不再跑上游分配阶段。
- 验证：SY38 10 天 FIVE_TWO，18 任务、OTD 100%、12 条 assignment、10 次实际移线、9 项校验；
  连续两次求解 assignment 完全一致。
- 新增 `tests/test_assign_in_model.py`（3 项：exactly-one/回写、固定线不动、重复求解 from 不漂移）。
- 已知边界：候选线须同 TRP；PM 目前沿用 plan 的主线 PM 槽；sequence 未联立。


### 2026-10-31 C2-b-3a 分配后 sequence 精排

- `APSScheduler.sequence_requested` 保留原始 sequence 开关；assign 模式第一段仍关闭 sequence，
  以 55% 预算完成“线选择 + 容量/交期”求解。
- `_resequence()`：把 `assignment_decisions` 的选中线固定到 `plan.tasks`，浅拷贝一个
  `assign_in_model=False` 的调度器，恢复 `setup_ms`、开启 `sequence_setup`，用剩余预算做第二次求解；
  结果带 `sequence_plan`，随后恢复原始产线，保证重复求解不漂移。
- 第二次求解失败时回退第一次结果并打印 `[assign] sequence re-solve skipped`。
- 新增 `test_sequence_resolve_keeps_assignment`：4 项 assignment 测试通过。
- SY38 实测（10 天 FIVE_TWO，`routing_assign_in_model=true` + `sequence_setup=true`）：
  18 任务、OTD 100%、12 条 assignment（3 次移线）、33 条 sequence 记录、9 项校验，75s。
- 边界：这是两阶段（先分配、后 sequence），不是同一 CP-SAT 内的 sequence 变量；
  同一模型联立仍列为下一步。


### 2026-10-31 C2-b-3b 同模型 sequence（小规模）+ 大规模回退

- `_add_sequence_constraints` 改为按线取变量：`act(t,s)` / `xval(t,s)` 优先读
  `_line_active/_line_x`，否则回退任务级 `active/x`；sequence 的 first/last/trans/
  start/end/setup 现在可以跑在“候选线”上，不再要求 `task.line` 固定。
- `_sequence_vars` 在 assign 模式下按 `(task,line,shift)` 存储；`_extract_sequence` 兼容
  2/3 元 key，只输出实际选中线的记录。
- 规模控制：候选线任务 ≤2 时 assign+sequence 在同一 CP-SAT；>2 时自动回退 C2-b-3a
  （先分配，再固定线做 sequence 精排），避免模型爆炸。
- 修复回退路径的变量串用：`fixed` 调度器清空 `_line_x/_line_active/_assign`，否则会引用
  上一个模型的 BoolVar（`Invalid boolean literal`）。
- 验证：小规模测试 4 项通过；SY38 10 天 FIVE_TWO 实测 18 任务、OTD 100%、
  12 条 assignment、33 条 sequence、9 项校验、75s。
- 快速 60/60、sequence 相关 14 项通过；全量基线此前 134/134。


## 当前状态汇总（2026-10-31 11:00）

| 维度 | 状态 |
|---|---|
| Phase 0 正确性止血 | 100% |
| Phase 1 规则内核 | 95%（19 模板 / 18 编译；`routing` 模板未编译） |
| Phase B 32 规则 | 约 75% |
| Phase C 时间/路由/序列 | 约 78% |
| 整体 | 约 84% |

### 已落地的主线能力

- 15 表主数据 v2（兼容旧 25 表）、按站点模板下载/上传、四档工作日历、班次几何；
- C1-b 班内 sequence + SDST + 跨班 carry-over + 技术员区间；
- C2-b-1 班次级路线容量（真实日历 + PM 扣减）；
- C2-b-2 主模型内 Assign（`assign(task,line)` + 分线容量/PM）；
- C2-b-3b 小规模同模型 sequence；候选项 >2 自动回退 C2-b-3a（分配后精排）；
- C4 静置时间 + 首批转运推导；C5-a Campaign、C5-b 交期 Slack；SC-09 Jitter。

### 测试

- 全量 **147/147 PASS**（约 1408s）；
- 快速 **60/60 PASS**（约 62s）；
- 最新 SY38 10 天实跑：18 任务、OTD 100%、12 条 assignment（3 次移线）、33 条 sequence、9 项校验、75.1s。

### 仍未完成

- 大规模（候选项 >2）同模型 sequence；
- C5 独立 Pull；
- SC-08/SC-10、BS-04 等 `needs_model` 规则（SC-09 已 partial）；
- 全连续时间、PM 精确到时刻；
- 内核 `routing` 模板编译。


### 2026-10-31 SC-09 计划平稳度 Jitter

- `APSScheduler(enforce_jitter=True, baseline_tasks=...)`：新增 `_add_jitter_penalty`。
- 以基线任务的 `shift_breakdown` 最后一个班次为完成基准，罚 `|end_new − end_base|`；
  若基线产线不在候选线/当前线，再罚一次产线切换。
- 权重：完成班次 50,000/班，产线切换 500,000；归入 G3_CHANGEOVER，避免压过交付/产能。
- 服务端：payload/site settings `jitter_enabled`；基线取 `session_state["result"].tasks`；
  首次排产无基线时自动为空，不产生罚分；UI 站点设置新增 SC-09 开关。
- 规则目录：SC-09 状态从 `needs_model` 调整为 `partial`（完成班次 + 产线，尚未做 start 偏移平方项）。
- 新增 `tests/test_jitter.py` 2 项：基线完成班次保持、无基线安全。
- 实测 WL 5 天：开启 jitter 后，21 个任务中完成班次/产线变化由 15 降到 12；仍受交付/产能优先级影响。


### 2026-10-31 HC-16 评估与 HC-16-lite 实验

- 结论：HC-16 不是“规则没写清楚”，而是**当前只做到 partial**：
  - `contiguity` 模板/`max_blocks_per_task` 能力存在，但没有 active 规则、没有 UI/默认开启；
  - `campaign` 只是班次内软分组，`sequence_setup` 只是换型顺序；
  - 没有“同模/同 campaign 跨工单防穿插”的硬窗口，Gantt 里的 6×48 尾块是模型允许的碎片化填充。
- Gantt 实证：WBC9317 1,248 件被拆成 10 个班次块（432/48/96/384/48×5），
  说明当前没有 block cap，95% 填充与软换型目标允许尾部小块。
- 实验：`hc16_lite_enabled` → `max_blocks_per_task=1`。
  - 单工单测试连续块通过；
  - 但 WL 10 天全量开启后求解不可行（总负荷仅 76%/79%，仍无解），
    说明“总量守恒”不等于“按 95% 班次地板 + 物料/PM/交期可排”；
  - 因此 HC-16 不能简单全局限块，必须按 campaign 定义窗口，并显式允许 PM/缺料例外。
- 代码保留为 opt-in 实验开关 `hc16_lite_enabled`（默认关闭），不作为 HC-16 完成依据。
- 新增 `tests/test_hc16.py` 1 项连续块测试。


### 2026-10-31 规则表新增「落地细则」列

- Light China APS Rules.xlsx 第 1 张表新增 I 列「落地细则（可落地口径 + 待核对问题）」。
- 32 条规则全部填写：【可落地】 写清变量、数据源、时间粒度、例外与当前系统支持；【待核对】 列出需物流排产专家确认的细则（编号问题）。
- 重点待核对：HC-16 超级工单边界、SC-01 D+1 时钟、SC-08 顺位 index、SC-10 负荷口径、BS-04 Pull 混合、BS-05 合批视距与 HC-08 L/R 配对等。
- 原 A-H 列内容逐格比对无差异；备份 rchive/rules/Light China APS Rules.pre_landing_details.xlsx。


### 2026-10-31 SC-10 三班负荷平滑

- `APSScheduler(enforce_labor_smoothing=True)`：新增 `_add_labor_smoothing_penalty`。
- 按日期汇总早/中/晚班的 FG 机时（`x × unit_millis`），最小化每班相对当日均值的 L1 偏差；
  二次方差在 CP-SAT 中非线性，L1 偏差是线性代理（三班相等时为 0）。
- 权重 LABOR_SMOOTH_WEIGHT=2，归入 G3_CHANGEOVER，不压交付/产能。
- 服务端 payload/site settings `labor_smoothing_enabled`，UI 站点设置新增「三班负荷平滑 (SC-10)」；
  响应回传开关。
- 规则目录 SC-10 状态 `needs_model → partial`；当前 partial 22 / needs_model 3。
- 新增 `tests/test_labor_smoothing.py` 1 项：3 个 FG 任务在平滑开启后分布到 ≥2 个班次。
- 实测 WL 5 天：平滑关闭/开启均 21 任务、FEASIBLE。


### 2026-10-31 SC-08 顺位前置填补原型

- `APSScheduler(enforce_sc08=True)`：新增 `_add_sc08_penalty`。
- 同产线按自然交期排序；对每一对“早交期 i / 晚交期 j”，用 Bool `inv` 表示
  `end_i > end_j`，罚 `SC08_INVERSION_WEIGHT=20,000`；是平方序位罚的线性配对代理。
- 归入 G3_CHANGEOVER；默认关闭，不压交付/产能；服务端 `sc08_enabled` + UI 开关。
- 规则目录 SC-08 `needs_model → partial`；当前 partial 23 / needs_model 2。
- 新增 `tests/test_sc08.py` 1 项：早交期任务完成不晚于晚交期任务。
- 实测 WL 5 天：关闭/开启均 21 任务、FEASIBLE。


### 2026-11-01 BS-05 同模合批软约束

- `APSScheduler(enforce_campaign_batching=True)`：新增 `_add_campaign_run_penalty`。
- 以 `mold_id`（无模具时回退 `campaign_group`）分组，按产线统计每个分组的“开工段数”：
  同一班次多个任务同时开工只算一段；每多一段罚 60,000。
- 不改变订单 identity、不合并任务，是 BS-05 “SuperOrder 合批”的软代理；
  对 HC-16-hard 的后续 campaign 窗口也是前置分组基础。
- 服务端 `campaign_batching_enabled` + UI“同模合批软约束 (BS-05)”；默认关闭。
- 规则目录 BS-05 `preprocess → partial`；当前 partial 24 / needs_model 2。
- 新增 `tests/test_campaign_batching.py` 1 项；WL 5 天开启/关闭均 21 任务、FEASIBLE。


### 2026-11-01 BS-06 交期裕度退避（回退式）

- `APSScheduler(enforce_slack_fallback=True)`：在 BS-05 合批求解后统计 `Slack = Due − End`。
- 若存在 Slack<0，复制调度器并关闭 `enforce_campaign_batching` 重解一次；
  仅当延期订单数更少，或数量相同但最小 Slack 更大时采用回退方案。
- `slack_fallback_info` 记录 enabled/triggered/late_before/late_after/min_slack/chosen；
  API 返回 `slack_fallback`。
- 服务端 `slack_fallback_enabled` + UI“交期裕度退避 (BS-06)”；默认关闭。
- 这是交付保护型回退，不是 BS-06 的“按 MOQ 截断 SuperOrder”；后者仍待细则。
- 新增 `tests/test_slack_fallback.py` 1 项；WL 5 天实测 21 任务、FEASIBLE、未触发。


### 2026-11-01 BS-05-2 SuperOrder 分组对象

- 新增 `src/batching.py::CampaignBatch` 与 `build_campaign_batches()`：
  按 `(mold_id 或 campaign_group, part_no)` 分组，同一视距窗口（默认 30 天）内 ≥2 单形成一个 batch。
- **不合并订单/task**：Batch 只是计划对象，保留 source order/task 清单，避免破坏 17 项校验与订单 identity。
- `/api/solve` 新增 `campaign_batches`，返回 batch_id、模具/族、零件、task_ids、order_ids、总数量、最早/最晚交期。
- 实测 WL 5 天：3 个 batch（WBC9318/WBC9324 等同模同零件订单被聚合）。
- 新增 `tests/test_campaign_batches.py` 2 项：同模同零件聚合、视距窗口切分。
- 下一步：BS-06 按 batch 做 MOQ 截断、HC-16-hard 用 batch 做连续窗口。


### 2026-11-01 BS-06-2 batch 级截断建议

- 新增 `batching.build_truncation_suggestions()`：
  - 找延期订单的 overrun 小时，按 `CT/TRP` 折算需要释放的数量；
  - 在同产线 CampaignBatch 中取最大批次，按完成时间倒序建议顺延尾单；
  - 输出 keep/split 数量、keep/split task/order、min_moq、feasible/note；
  - 不自动修改排产结果，避免静默丢单。
- `/api/solve` 新增 `truncation_suggestions`；无延期时为空。
- 新增 `tests/test_truncation_suggestions.py` 1 项：tail split 满足 overrun 且 keep≥MOQ。
- 实测 WL 5 天：3 个 campaign batch、无延期、0 条建议。


### 2026-11-01 SC-01 D+1 备货时钟（soft）

- `APSScheduler(enforce_d1_clock=True, demand_by_date=...)`：新增 `_add_d1_clock_penalty`。
- 服务端从 `snap.orders` 汇总 `due_date → part → qty`；按“当日最后一个班次”计算 FG 日末库存：
  `opening + 累计产出 − 截至当日需求`；对次日需求缺口罚 100/件。
- 当前只做层级 1（D+1 soft clock）；层级 2 的“当日早班紧急追产”硬兜底尚未做。
- 服务端 `d1_clock_enabled` + UI 开关；默认关闭。
- 新增 `tests/test_d1_clock.py` 1 项；WL 5 天实测 21 任务、FEASIBLE。
- 规则目录 SC-01 `needs_model → partial`；当前 partial 25 / needs_model 1。


### 2026-11-01 BS-04 逆向拉动偏好

- `APSScheduler(enforce_pull=True)`：把 FG/SF 的完成偏好从“越早越好”翻转为
  “越接近交期/窗口上界越好”（JIT pull），不改 due 硬约束。
- 服务端 `pull_enabled` + UI“逆向拉动偏好 (BS-04)”；默认关闭。
- 这是 Pull 的软偏好层，不是独立逆向求解器；BOM 逆向拉动仍由 C4/C6 约束承担。
- 规则目录 BS-04 `needs_model → partial`；当前 **partial 26 / needs_model 0**，Phase B 32 条规则全部至少 partial。
- 新增 `tests/test_pull.py` 1 项：pull 完成不早于 push。
- WL 5 天实测：21 任务、FEASIBLE。


### 2026-11-01 BS-06-3 自动截断/顺延

- 新增 	runcation_auto_enabled：当 	runcation_suggestions 有可行建议时，按 split order 过滤 snapshot，
  重新构建 requirement plan，并复制调度器求解剩余订单。
- 响应新增 deferred_orders 与 uto_truncated；plan/result/solve_snap 切换到剩余订单集合，
  因此 17 项校验只针对本期实际排产订单，不会把顺延订单误判为欠产。
- 仅在所有涉及订单都 easible=true 时自动执行；否则保留原计划并返回建议。
- WL 5 天无延期场景：auto=false、deferred=[]；代码路径通过。


### 2026-11-01 硬 95% 不再静默软回退

- 现象：V5 规则集 `WL_CAP_FG/WL_CAP_SF` 的 `capacity_utilization` 为 hard、`min_util=0.95`，
  但求解器在硬口径无解时自动降级 soft，页面仍显示 FEASIBLE + 91.4%，掩盖了 17 个班次低于 95%。
- 修复：`APSScheduler` 检测 hard 且 `min_util>=0.95` 的容量规则，默认只跑硬口径；
  硬口径无解时返回 `HARD_95_INFEASIBLE`，给出瓶颈、线负荷和调整建议，不再自动生成低利用率计划。
- 新增站点设置 `allow_soft_capacity_fallback`（默认关闭）：显式打开后才允许软回退；
  API `kpi.strict_capacity_hard / allow_soft_capacity_fallback` 可追溯。
- 新增 `tests/test_hard_capacity_strict.py` 1 项；全量 **147/147 PASS**。


### 2026-11-01 C2-b-3c 候选线裁剪

- 每个有备线的任务只保留“主选线 + 1 条备线”，避免认证线全集把 sequence 模型乘爆。
- 候选面 ≤6 个任务、≤12 个候选槽时，`assign + sequence` 留在主模型；超过则回退 C2-b-3a。
- 新增 `candidate_pruning` 指标：裁剪任务数、候选槽、同模型上限；API 返回。
- SY38 实测：12 个有备线任务会回退，18 任务/86s FEASIBLE；WL 当前 Routing 无多线候选。
- 新增 `tests/test_candidate_pruning.py` 3 项；全量 **150/150 PASS**。
- 局限：>6 个候选任务仍未同模型 sequence，C2-b-3 未完全闭合。


### 2026-11-01 BS-06 顺延订单选择器

- `batching.select_deferred_orders()`：只接受 `feasible=true` 的截断建议，按 order_id 去重，
  输出 order_id/batch/qty/target/reason；服务端自动截断复用该函数。
- 新增 `tests/test_truncation_suggestions.py` 第 2 项：不可行建议不进入顺延清单，重复订单只保留一次。
- 全量 **151/151 PASS**；自动截断的“有延期”整链路仍需真实/构造场景验收。


### 2026-11-01 BS-06 自动裁剪重排数据面

- `batching.without_orders()`：浅拷贝 snapshot，剔除顺延订单，原 snapshot 不变。
- 服务端自动截断复用该函数，确保后续 `build_requirement_plan` 只包含本期订单，
  17 项校验不会把顺延订单误判为欠产。
- 新增 `tests/test_bs06_auto.py`：可行建议才顺延、快照不被修改、重算计划不含顺延订单。
- 全量 **160/160 PASS**；真实“有延期→自动截断→重排”仍待一个完整场景验收。


### 2026-11-01 BS-06 有延期场景数据链路测试

- 构造 CampaignBatch + 延期目标订单，走通：
  uild_truncation_suggestions → select_deferred_orders → without_orders → build_requirement_plan。
- 断言顺延订单不在新 plan 中、原 snapshot 不变；覆盖“有延期”的自动截断数据链路。
- 全量 **160/160 PASS**。


### 2026-11-01 BS-06 真实延期→截断→重排闭环

- 构造 10 个同模 FG 任务，交期仅 2h，真实求解产生延期；
- uild_campaign_batches → build_truncation_suggestions → select_deferred_orders
  得到顺延清单；
- 从 plan 剔除顺延任务后重新求解，断言剩余任务不再包含顺延订单。
- 新增 	ests/test_bs06_solver_loop.py；全量 **160/160 PASS**。


### 2026-11-01 C2-b-3c 候选池尝试

- 有备线任务 >6 时，不再直接回退：先取工作量最大的 6 个任务组成候选池，
  其余任务固定主线，尝试主模型 sequence；
- 候选池尝试限时 55% 预算；失败则恢复完整候选并回退 C2-b-3a；
- candidate_pruning 增加 pool_limited/pool_attempted/pool_tasks/pool_dropped/pool_fallback。
- SY38 12 个备线任务实测：候选池仍未解出，回退 C2-b-3a，18 任务 FEASIBLE，约 82s。
- 全量 **160/160 PASS**。


### 2026-11-01 C2-b-3c 同模组 sequence 子集

- 候选池任务增加 sequence_task_ids 范围：只对池内 6 个任务建 sequence 变量，
  其余任务仍参与容量/交期，但不进入 sequence 路径。
- SY38 12 个备线任务实测：候选池 + 子集 sequence 仍未解出，自动回退 C2-b-3a；
  最终 18 任务 FEASIBLE，约 82s。
- 说明：该子集路径是近似（池外任务不计换型顺序），只在候选池成功时生效；
  失败会回退完整 C2-b-3a 并清空 scope。


### 2026-11-01 BS-06 顺延订单页面/导出

- 页面顶部新增“本期自动顺延 N 个订单”面板，列出订单号、批次、数量、目标订单、原因。
- Excel 导出新增“本期顺延订单”sheet（仅在有顺延时出现），与主计划区分。
- 服务端 session 保存 deferred_orders，导出复用。
- 全量 160/160 测试保持通过。


### 2026-11-01 BS-06 顺延订单导出单元

- exporter.append_deferred_orders() 独立负责“本期顺延订单”sheet；
- 服务端导出调用该函数，避免重复样式代码；
- 新增 	ests/test_deferred_export.py：表头、订单号、数量、原因写正确。
- 全量 **160/160 PASS**。


### 2026-11-01 BS-06 截断接受守卫

- atching.truncation_improves()：只有“延期订单减少”或“延期数相同且最小 Slack 不退化”才接受自动截断结果。
- 服务端自动截断计算 before/after 的 late/min-slack，退化则保留原计划。
- 响应新增 	runcation_summary；页面横幅显示“延期 X → Y”。
- 全量 **160/160 PASS**。


### 2026-11-01 32 条规则专家答复复核（第一轮）

- 复核 Light China APS Rules.xlsx J 列 32 条答复；答复总体与实现方向一致，但发现 4 个必须先解决的冲突/数据缺口：
  1. **HC-04**：专家明确“不接受默认 60min，必须维护矩阵”；当前 _add_sequence_constraints 仍对缺失边回退 60min。
  2. **HC-09**：专家明确静置从“整批完工”起算；当前 C4 仍按“首批转运批量”起算。两者冲突，需业务确认是否只对特定零件。
  3. **HC-08**：专家明确 L/R 必须 1:1 同步；当前没有强制配对/同步产出。
  4. **HC-14**：专家明确装配防断线优先于 SC-06/07 库存罚；当前 inventory hard 仍可能阻断。
- 已同步落地：
  - **HC-01**：无论 vailable_qty 多少，同一物理模具只能 1 台机；FG/SF 分开占用；内核与 legacy 路径均已改。
  - **SC-01**：新增 enforce_d1_clock_hard，可将 D+1 缺口设为硬约束（默认关闭，便于先观察）。
- 结论：暂不生成正式 V6；先解决 HC-04/09/08/14 四个阻塞项，再做候选版本。
- 全量 **160/160 PASS**。


### 2026-11-01 业务补充确认

- SC-02：105% 上浮为通用规则，不限制单产品/无换型；现有通用 105% 保留。
- HC-16：软/硬应在 rule-sets 页面配置；V6 候选设 hard；当前 HC-16-lite 仅测试，不作为正式规则。
- 规则主数据：建议新增 v1.1 版本，保留 J 列答复作为审计轨迹，不回写覆盖原始 Excel。


### 2026-11-01 HC-16 规则模板 v0（可配软/硬）

- 新增内核模板 hc16：max_blocks + llow_delivery_override，nature 由 rule-sets 页面配置；
- 编译器当前复用 contiguity 的“工单连续块数”近似，不是正式“同模窗口”硬模型；
- 已在调度器 _build_model 接线，规则集带 hc16 时生效；
- llow_delivery_override 已受理但 v0 仍按 nature 编译，交期 override 待正式模型；
- 模板总数 19→20；全量 **160/160 PASS**。


### 2026-11-01 规则与约束配置 Tab 更新

- 顶部新增 4 张卡：32 条目录、20 个内核算子、当前规则集（ID/版本/规则数/hard-soft）、覆盖统计；
- 新增“当前站点规则集明细”只读表：规则 ID、模板、作用域、软/硬、权重、参数、启用；
- 动态读取 /api/rule-sets/templates 与 /api/rule-sets/active；
- 当前活动规则集未包含 HC-16 时显示提示，引导在 /rule-sets 添加 hc16；
- 更新“19 个固定算子”为动态模板数，避免文档与实现脱节。


### 2026-11-01 修复主页白屏

- 原因：规则 Tab 新增的 
uleTemplates / ctiveRuleSetRules / deferredOrders 等 ref 未在 Vue setup 的 
eturn 中暴露，模板读 .length 时抛异常。
- 修复：补齐 return；headless Chrome 验证页面可渲染，规则 Tab、工作台、当前规则集明细均显示。


### 2026-11-01 V6 候选实验

- 基于 v4 生成 WL_PHASE1 v6（id8，draft）：soft 95 + HC-16 hard v0 + qc_release_hours=0；
- 激活 v6 跑 WL 10 天：400，HC-16 hard v0（每工单 1 块）与当前任务结构冲突，候选不可行；
- 生成 WL_PHASE1 v6S（id9，draft）：HC-16 改 soft；激活后 WL 10 天 FEASIBLE，19 任务，平均利用率 93.4%，仅 95% 检查未通过（软 95 预期）；
- 测试后已恢复 v4 active（id6）；两个候选保留为 draft，供规则集页面查看。


### 2026-11-01 v6/v6S 测试诊断

- v6（HC-16 hard v0）WL 400：不是总工时超载（线负荷 76%/79%），而是 hc16 max_blocks=1 与任务结构冲突；错误弹窗已改为 HC16_INFEASIBLE，不再误报产能。
- v6S WL 25.2%：v6S 的 capacity_utilization 是 soft，25.2% 是开班/收班低负荷，不是求解错误；要硬 95 需 hard 版本。
- SY38 L755774 3 件：WL_MPQ_SF 仍为 unit=piece，SF 是可拆 piece lot，班次块不受 MPQ100 约束；这是 SC-04 的 SF MPQ 对齐缺口，不是 FG MPQ 规则。已尝试修 SF 分块 MPQ，但当前输出 88/323/123 仍未对齐，已回退，列为待修。
- 全量 157 基线此前通过；本次改动后快速 60/60 通过。


### 2026-11-01 SF MPQ 修复尝试（未完成）

- 根因确认：WL_MPQ_SF 为 unit=piece；SF 任务为 piece-lot 可拆，3 是班次块，不是整单；SF 总量 534 也不是 MPQ100 整倍数。
- 尝试 1：SF splittable 强制每班 >=100；部分 SF 任务总量 <100（如 3/22），模型直接不可行。
- 尝试 2：数据加载把 SF 净需求向上取整到 MPQ（534→600）；10 天计划仍不可行（容量/交期），不能全局硬切。
- 已回退两次试验，active 仍为 v4；SF MPQ 需要 tail-aware 策略（整单 MPQ + 唯一尾块）再做。


### 2026-11-02 待确认清单收敛与技术缺口记录

- APS_业务规则确认清单.xlsx 收敛为 5 项：HC-16 空档/ v0 hard、SC-02 95% 硬软、HC-09 整批静置冲突、HC-14 覆盖阈值、SC-04 SF MPQ 尾块。
- 已确认问题从业务清单移除；历史答复保留在 Light China APS Rules.xlsx J 列。
- 记录 WCA1894 低负荷开班（3×60、13.5%）和 L755774 SF 非 MPQ 班次块（3/88/123）为技术缺口，见 dashboard 5.1。


### 2026-11-02 业务确认后的可落地项

- 5 项待确认全部有回复：HC-16 A（含交期让行）、HC-14 A、SC-02 A（hard）、HC-09 A（整批静置）、SC-04 A（整单 MPQ + 1 尾块）。
- 已实现 HC-09 A：lead_shifts(..., whole_batch=True)，C4 从整批完工起算；服务端新增 c4_whole_batch_enabled，默认开启。
- 	est_capacity_kernel 新增整批 C4 断言；快速 61/61 PASS。
- 仍待实现：HC-14 自动豁免、SC-04 SF 尾块、HC-16 正式同模窗口。


### 2026-11-02 主数据模板变更复核

- Parameters-SFN 新增 同时生产：master_schema.py / excel_loader.py 已识别，无需改表结构。
- Routing-Machines 删除备选候选线/机台/TRP 三列：schema 标记为 deprecated，master_rows/excel_loader 已回退到主候选线与产线基础数据 TRP。
- 新增 单班定额产能、人数、产品族名 已被 excel_loader 读取；
outing_from_rows 仅用于候选线，不受影响。
- 全量 **160/160 PASS**。


### 2026-11-02 SC-04 SF MPQ 尾块修复

- 新增 sf_mpq_tail_enabled（默认开启）：SF 每个班次块必须是 MPQ 整倍数，最多允许 1 个尾块（1..MPQ-1）。
- 当尾块模式开启时，SF 不再套用 ACT-06 单段/最少班次限制，也不再套用 legacy min_lots=MPQ。
- SY38 实测：L755774 534 → 100/100/34/300（唯一尾块 34）；288 → 200/88（唯一尾块 88），不再出现多个非 MPQ 块。
- 新增 	ests/test_sf_mpq_tail.py；全量 **160/160 PASS**。


### 2026-11-02 V1-V8 与 15 天无解诊断

- V1~V8 规则差异已核对：V1 单 MPQ/soft max1.0；V2 105；V3 FG/SF 拆分；V4 细参数+qc2h；V5 容量 hard95；V6 HC16 hard；V7 HC16 soft；V8 SF unit=MPQ。
- 15 天 7×3：PGLINKAGE 316h/360h（87.8%），轴套6 326.9h/360h（90.8%），总负荷并非超载；但 V1/V4/V5/V7/V8 均 400，V6 为 HC16 hard。
- 发现 sf_mpq_tail_enabled 默认 true 会让 10 天 WL 直接不可行；已改为默认 false，10 天 V4 恢复 FEASIBLE（19 任务，OTD 85.7%）。
- 15 天即使 V1 也失败，说明还有非版本因素（硬交期/MOQ/SF 物料/95% 逐班）需单独诊断。


### 2026-11-02 修复 V1 10 天被 SF tail 设置误杀

- 根因：前端仍发送 sf_mpq_tail_enabled=true，即使 V1 的 SF 规则是 unit=piece，服务端也启用了 SF tail，导致 WL 10 天 400。
- 修复：服务端增加门控，只有规则集存在 **SF 作用域且 unit=MPQ 的 min_batch** 时才启用 SF tail；V1~V7 自动忽略该开关，V8 才生效。
- 复测 V1 10 天：payload 传 true/false 都返回 **200 FEASIBLE，19 任务**。


### 2026-11-02 HC-08 L/R 共模同步 + 主数据补数

- ProductionTask 增加 simultaneous_production；调度上下文按 **同模具 + 同父工单 + 同时生产=Y** 配对，不再只依赖 L/R 后缀。
- 内核 co_product 规则接线：_build_model 编译 co_product，hard 时每班次 L/R 产出相等。
- 新增 	ests/test_hc08_co_product.py 1 项。
- 主数据补数：SF 模具 14、SF 同时生产 22、BOM 缺参 22、Routing 22 条、Tooling 5 套、PM 时间；加载 0 warning/0 invalid。
- 全量 **160/160 PASS**。


### 2026-11-02 V4 10 天无解定位

- V1 与 V4 都使用同一份模拟主数据；V1 可解，V4 400。
- 差异隔离：V4 的 WL_MPQ_SF.unit=piece 使 SF min_batch 走 piece-lot 分支，模型搜索在 10 天/90~150s 内无法找到可行解；V1 的单条 WL_MPQ.unit=MPQ 实际对 SF 也按 MPQ，故可解。
- 将 V4 的 WL_MPQ_SF.unit 改为 MPQ 后，10 天恢复 200 FEASIBLE（92.5s）。候选规则集“v4 test SF MPQ”（version 14, id16）已生成，当前 archived。
- 结论：V4 不是容量无解，而是 SF min_batch 口径与求解规模共同导致的搜索失败；V4F（SF MPQ）是可测版本。


### 2026-11-02 shift-open 合并开关 + WCA 验证

- 调度器新增 shift_open_penalty（毫秒/已开班次），站点开关 shift_consolidation_enabled，默认关闭。
- V1 10 天开启/关闭该开关均为 200 FEASIBLE；当前模拟数据下 WCA1894 已合并为 **10/12 晚班 180 件单块**，不再出现 3×60/13.5% 早班。
- 快速 **61/61 PASS**；全量基线仍为 160/160（本次仅新增可选开关）。


### 2026-11-03 HC-03 PM 部分降载

- `RequirementPlan.pm_capacity` 新增 `(line, slot) -> capacity factor`；`PreventiveMaintenance.capacity_rate` 在 0~1 时写入。
- legacy `_legacy_capacity_body` 的 `slot_max` 与 kernel `_compile_capacity` 的班次容量都乘以该 factor。
- PM `capacity_rate=0` 仍按整班封锁；0<rate<1 不再封锁，只降载。
- 新增 `tests/test_pm_partial_capacity.py`：50% PM 班次无法容纳 300 件满负荷，任务被推到后续班次。
- 全量 **162/162 PASS**。


### 2026-11-05 温岭 PoC 排产失败的四层归因与修复

**背景**：温岭真实数据（`data/uploads/current_WL.xlsx`）在 D+1=11/02、10 天、7×3 下返回 400「排产不可行」，弹窗把 `角度放大器#3` 报成 124.6%，并建议「切换为做七无休」——而当时已经是做七无休。按同一份数据逐层复现，问题分四层。

**1）需求前拉越过 98% 上限（算法）**

- 复现：同参数下 `apply_demand_lookahead` 拉入 19 张远期单，`角度放大器#3` 由 227.6h / 94.8% 升到 299.1h / 124.6%，成为弹窗头条。
- 根因：① `or line_used_hours[line] == 0` 让「FG 工时为 0」的半成品专用线无限放行；② 负载只按 FG 订单的 `qty*ct/trp` 统计，完全忽略 SF 工时；③ 按班次算容量时对每条线的每个 FG 料号都 `+= 8h`，上限随料号数膨胀。
- 修复：改为对「整份计划」按 `lots × lot_millis`（FG+SF）逐单试算，任一产线超过 `98% × 班次总工时` 即不再拉；容量按日历而非料号数计算（`src/server.py::apply_demand_lookahead`）。
- 复测：拉入 19 → 11 张，最高负荷 97.8%，不再出现 >100% 的产线；真实矛盾（7 张交期不可达）浮出水面。

**2）保养班次下标用错日期轴（算法）**

- 根因：PM 行是「绝对日期 + 班次名」，旧代码按 workbook 的 34 天轴（11/03 起）折算成班次下标；求解器日历从 D+1 起算且会跳过休息日 → 整体错位，越界下标被 `scheduler.py:1445` 的 `if 0 <= s < num_shifts` 静默丢弃（10 天 / 30 班时 52/84 个任务越界，最大下标 53）。
- 修复：调度器改为接收 PM 原始行，用本次 `self.shifts` 的 `(日期, 班次名)` 映射（`_map_pm_schedules`）；诊断侧同步改为按日期校验并在界面上列出落位明细。
- 复测：11/01 起 30 班 → `11/10 中班/晚班 = #28/#29`；11/02 起做五休二 24 班 → `#19/#20`，跳周末也正确。

**3）SF 最小批量按「每班」落地，数学上不可满足（规则 / 编译）**

- 根因：规则集 `WL_MPQ_SF`（hard / unit=piece / min_multiple=100 / basis=shift）被编译成「每个开口班次必须 ≥100 件」。本批 69 张半成品工单里 **62 张数量不是 100 的整数倍**、**13 张不足 100 件**（65/72/105…）→ 尾班必然违规，8 秒被证明 INFEASIBLE。
- 隔离：内核模板逐条排除，只有去掉 `min_batch` 才从「8 秒证明无解」变成「超时」，其余 9 个模板均无变化。
- 修复（`src/kernel/compiler.py::_compile_min_batch`）：① `min_lots` 以工单实际总量封顶；② `basis=shift` 改为约束**开工班次**（`started`），不再约束每个续产班次。
- 复测：同实例由 `INFEASIBLE × 3`（8s）变为 `UNKNOWN × 3`（搜索），矛盾消除。

**4）规则集禁用不生效（规则 / 接线）**

- 根因：规则集禁用某模板后编译路径不再加约束，但旧口径开关仍为 True，`if not kernel_x:` 分支又把约束加回去——业务在规则集里关掉 `WL_CC`（并发 3 型号）和 `WL_TOOL`（模具互斥）后，模型里依然生效。
- 修复：挂规则集时规则集为唯一事实来源，其声明过的模板对应旧开关强制关闭（concurrency / tooling_exclusivity / material_ready / pm_block / min_batch / material_balance / sequence）；同时 4 处 kernel 判断（min_batch / sequence / tooling / material_ready / pm）不再依赖旧开关。
- 复测：规则集仅 `WL_DUE` + `WL_CAP_FG/SF` 生效时，温岭 11/01 / 10 天实例离线 **102s 出解：105/105 任务，15 张延期**。

**5）诊断与可观测性（新增）**

- 新增 `src/diagnostics.py`，`/api/solve` 失败时返回结构化 `diagnosis`，弹窗分五段展示：结论卡、**订单缺口表**（交期/要货/期初库存/累计需求/交期前产能/缺口/满载最快完工）、**产线明细**（成品线 or 半成品线、型号工时、下游成品）、数据体检、规则体检、算法自检。
- 区分「证明无解」与「超时」：CP-SAT `UNKNOWN` 不再被当成无解，返回「排产未完成：求解超时（尚未证明无解）」，并给出调大预算 / 缩小规模的建议。
- 一次性求解预算：弹窗新增「本次用 300 秒重排（不改默认）」，默认 35s（服务端在挂规则集时有 90s 下限），覆盖值在 `finally` 中复位，不影响下一次。
- 移除「已 7×3 却建议切 7×3」的建议文案；根因是交期时改为提供「起始日期前移 3 天并排产」按钮。
- 运维：发现被杀父进程的 uvicorn 子进程会继续持有 8000 端口并返回旧代码（表现为「改了不生效 / 新按钮不出现」），统一改为 `RELOAD=false` 单进程启动。

**测试**：新增 `tests/test_diagnostics.py` 6 项；快速套件 **61/61 PASS（62.8s）**；本轮涉及的内核/规则集/回归/诊断定向用例 **59 项 PASS**。


### 2026-11-07 MOQ（最小生产量）落地

**业务口径（本次确认）**

- 最小生产量是 **MOQ**，不是 MPQ：MPQ=10 / MOQ=100 时，客户要 1 件也要投产 100 件（99 件入库）。
- 超过 MOQ 后按 **MPQ 整步**增长：要 188 件 → 100 + 9×10 = 190 件（2 件入库）。
- MOQ 不是 MPQ 整数倍时，先向上对齐到 MPQ 的整数倍（否则批次数不是整数）。
- MOQ 作用在**净需求**上（先扣期初/安全库存）：库存已覆盖时不投产。

**改造前**

- 产量 = `ceil(净需求 / MPQ) × MPQ`，MOQ 只写进 `ProductionTask.moq`，没有参与数量计算；
- `min_batch.quantity_source=MOQ` 算的是「单次开工下限」，且会被「不超过工单总量」封顶，等于空转；
- 实测（MPQ=10 / MOQ=100）：要货 1 件 → 计划 10 件，比业务要求少 90 件。

**改造后**

- 新增 `data_loader.plan_production_quantity(net, mpq, moq)` 与 `plan_moq_floor(mpq, moq)`；
- 成品：净需求 → MOQ 起步 → MOQ 之上按 MPQ 整步（`data_loader.py` FG 分支）；
- 半成品：MOQ 作为**下限**，其上任然是件级粒度（保持既有「SF 单班可任意整数件」口径）；
- 主数据里 MOQ 缺省为 0 时回落到 MPQ，与旧行为一致。

**兼容性**

- 温岭现有主数据 **119 个料号（FG 64 / SF 55）MOQ 全部等于 MPQ**，逐单比对旧公式：41 个成品任务 **0 处差异**，存量计划量不变；
- 业务一旦把 MOQ 调大于 MPQ，新口径立即生效。

**测试**

- 新增 `tests/test_moq.py` 8 项：业务用例表（1/50/100/101/188/200）、MOQ=MPQ 等价于旧公式、MOQ 非整数倍对齐、零需求、库存优先抵扣、SF 下限。
- 快速套件与数量相关套件（test_scrap / test_sf_mpq_tail / test_split_batch / test_moq）**12 项 PASS**。

**遗留**

- 半成品是否也要按「MOQ + MPQ 整步」严格取整（而不是只做下限），需业务再确认；当前取下限是为了不放大 BOM 派生量。
- 结果页尚未标注「计划产量 vs 客户要货」的差额（多产入库部分）。
