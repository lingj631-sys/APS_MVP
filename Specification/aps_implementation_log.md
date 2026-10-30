# APS MVP 实施记录（Phase 0 闭环 + Phase 1 v1 闭环）

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

### 2026-10-29 P0 重大修复：调度器算子接线断层与旧布尔门控死锁闭环

- **现象与问题**：
  - 规则内核 `src/kernel/compiler.py` 虽已实现 18 个模板的编译函数，但调度器 `APSScheduler._build_model` 仅编译了 12 个模板，遗漏了 `contiguity`、`freeze`、`calendar`、`time_window`、`co_product`、`inventory_bound` 6 大核心物理算子，形成“界面已配置、后台静默不编译”的断层；
  - 调度器内算子编译受制于旧版 `self.enforce_xxx` 布尔标志（与门死锁），若用户未勾选旧版 27 条复选框，内核算子直接被短路；
  - 调度器默认未从 `rule_set` 自动推导 `kernel_templates` 集合，未显式传参时模板集合为空。
- **解决方案与实施**：
  1. **构建红灯反馈环**：新增 `tests/test_operator_wiring.py`（6 个测试），针对上述断层先跑出红灯失败。
  2. **算子全量接入**：在 `APSScheduler._build_model` 关键构模时段补齐 `contiguity`、`freeze`、`calendar`、`time_window`、`co_product`、`inventory_bound` 的编译调用。
  3. **门控彻底解耦**：将所有算子判断由 `self.enforce_xxx and ...` 重构为规则集优先（`use_kernel and self._use_kernel(template_id)`），当且仅当规则集未定义该算子时才退回旧版布尔逻辑。
  4. **软惩罚统一吸收**：实现 `_absorb_penalty_book` 函数，将编译产生的 `PenaltyBook` 软惩罚项自动归并至字典序目标分组（G1~G4），并消除 CP-SAT 线性表达式直接求布尔值的 `NotImplementedError` 隐患。
  5. **默认模板自动识别**：`APSScheduler.__init__` 支持在传入 `rule_set` 时自动识别其启用的已实现模板，无需调用方手工装配。
- **验证结果**：
  - 新增 `tests/test_operator_wiring.py` 6/6 全部绿色通过（0.10s）；
  - 将用例加入 `run_tests.py --fast`，快速回归套件扩充至 **60/60 全部 PASS（60.5s）**；
  - 黄金基线 `tests/test_golden_baseline.py` 13/13 项全绿通过（720s），既有排产业务与物理不变量零退化。

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

### 待做

- C1-b 真正班内 sequence 变量 + SDST + carry-over：当前只有 setup 容量/反馈近似。
- C2-b CP-SAT Assign(i,m)：Routing 仍是数据层 + 启发式。
- B4-b CP-SAT 库存约束：当前只有库存投影审计。
- C4 Operation LeadTime / Transfer Batch；C5 Campaign/Pull/Slack。
- 32 条规则目录与 17 项审计项的正式映射；旧版 `/api/rules` 兼容层迁移。
- 真实 Setup-Matrix / Inventory-Policy / Routing 数据替换 mock。
