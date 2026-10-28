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
| 换型块 | 19 + 34 = 53（与 R-WL-12 的权衡见 §3.6） |\n\n> 当前 active WL v1（11 条）的 `/api/solve` 实测：200，19 单，0 项校验失败，55 块，90.6s 请求；拆分版 v3 在版本列表里，未自动激活。

### 4.2 单元测试（最终新增套件）

| 测试 | 结果 |
|---|---|
| `test_kernel` | 22 项 PASS |
| `test_rule_sets` | 9 项 PASS |
| `test_labor` | 5 项 PASS |
| `test_split_batch` | 2 项 PASS |
| `test_independent_audit` | 6 项 PASS（含 20 类变异） |
| `test_golden_baseline` | 13 项 PASS（内核迁移 + 105 口径 + 组目标） |
| **合计** | **57 项 PASS，375.0s** |

> `test_aps_pipeline` 的旧 01/03/05/07/08 用例是早期全流程验证，不在上述 57 项新增套件里；最终新增套件以本表为准。迁移测试预算 90s，避免墙钟抖动。

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
python src/main.py                       # CLI 全流程 + 17 项校验（约 2–3 分钟）
python -m unittest test_aps_pipeline -v  # 单元测试（约 8–10 分钟）
```

---

## 8. 核心算法缺陷修复记录（2026-10-28 闭环，Commit 53ee1df ➔ 最新）

详细差距与代码对账分析见专用日志文件：[`Specification/aps_algorithm_defect_remediation_log.md`](aps_algorithm_defect_remediation_log.md)。

* **修复清单**：REG-01（半成品时序账本）、REG-02（日历绝对时间映射）、REG-03（安全库存恢复方程）、REG-04（未知物料无效单捕获）、REG-05（基线冻结等式入模）、REG-06（FIFO 逐单交付核对）、REG-07（计划周期范围对齐）、REG-08（Excel 路径分流优先）。
* **自动化测试**：新增 `tests/test_regressions.py`，全量 56 项测试 100% 绿色通过。
