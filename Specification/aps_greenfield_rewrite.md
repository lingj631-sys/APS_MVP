# APS 深度重构设计：规范数据集 + OR-Tools 排产内核

> **日期**：2026-11-05
> **状态**：现行权威（设计文档）
> **取代**：`aps_rule_set_redesign.md` 及其 `plans/2026-11-03-stage0-foundation.md`、`plans/2026-11-05-stage1-dual-track.md` 整体作废，移入 `archive/Specification/`
> **本文档是什么**：架构设计。它规定*建成什么*与*为什么这样建*，不规定*按什么顺序敲哪一行代码*。实施计划另见 `Specification/plans/`，由本文档派生。

---

## 0. 背景与本次重构的性质

### 0.1 旧系统的规模与问题

`src/` 共 20,238 行 Python，42 个测试套件。三个巨型文件承担了大部分职责：

| 文件 | 行数 | 问题 |
|---|---|---|
| `src/server.py` | 3,878 | FastAPI 接入层 + 手工审计编排 + 业务逻辑混杂 |
| `src/scheduler.py` | 2,856 | CP-SAT 建模 + 规则编译 + 松弛回退 + 重排 + 压实全在一个类里 |
| `src/validator.py` | 1,660 | 与 `src/audit/engine.py` 职责重叠，同一件事两处实现 |

结构性失败有四类，都不是靠 code review 能修掉的：

1. **配置了但没生效**：32 条目录规则里只有 4 条真的编译进了模型；`routing` 模板从未被编译器接线。
2. **同一口径多处实现**：净需求、有效工时、换型时间这类派生量散在 `data_loader.py` / `master_rows.py` / `scheduler.py` 三处，是 REG-01~08 那批缺陷的直接来源。
3. **数据模型不规范**：15 表模板是业务填写视图，同一事实重复出现（`品名描述` 在 5 张表、产线名在 5 张表、模具编号在 4 张表），没有独立维度表，多处字段有多个可能取值来源。
4. **规则变更必须改代码**：调一个利用率门槛就是改 Python。

### 0.2 本次重构的性质

**完全推翻，从零重写。** 新建 `aps/` 包写全新代码，旧 `src/` 原封不动保留作对照物，直到新管道端到端通过验收，然后一次性删除。

旧系统的**业务知识**继承，**代码**不继承。具体地说：32 条规则目录的业务分类法、双工厂口径（WL 8h×3班 / SY38 12h×2班）、MPQ 分级取整、净需求公式、两阶段按班次流水搭接、毫秒整数标度——这些是业务事实，继续有效。`scheduler.py` 的 2,856 行实现、`kernel/` 的 19 算子编译器、`rule_sets/` 的 SQLite 版本库——这些是代码，全部丢弃。

### 0.3 旧 spec 的决策哪些活了下来

`aps_rule_set_redesign.md` 的 D1–D15 整体作废，但其中若干条在本次重新裁决后被独立采纳。**采纳的理由是它们本身正确，不是因为旧文档说过**：

| 旧决策 | 本次处置 | 对应新决策 |
|---|---|---|
| D7 指标不碰模型，按类型强制 | **采纳** | G15 |
| D10 SQLite + Repository 端口，禁方言 | **采纳** | G3 |
| D11 `op=minimize` 与 `nature` 互斥 | **采纳** | §5.2 |
| D12 目标项只经 PenaltyBook 进模型 | **改造采纳**：字典序分层，见 §6.4 | G12 |
| D13 确定性硬要求 | **采纳并加强** | G18 |
| D1 指标注册表 + 归约算子代数 | **采纳**（作为 B 档，但不再是改造既有代码，而是新地基的一部分） | G10 |
| D6 迁移 = clean break | **改造**：方案 1 新包并行 + 一次性切换，而非原地重写 | G14 |
| D3 双轨收敛作为阶段 1 | **作废**：从零重写不存在双轨 | — |
| D4 三个规则命名空间收敛为两个 | **作废**：新架构没有历史命名空间 | — |
| D9 `on_data_gap` 默认来自 `contract_tier` 三档 | **改造**：T3 消失，只剩 T1/T2 | G9 |

---

## 1. 决策登记（G1–G29）

每条都是 2026-11-05 与用户逐条确认的裁决（G27–G29 为设计自审时裁决的技术问题，见 §12.2）。**不要重新讨论，不要"优化"，不要在实施中悄悄偏离。** 若实施中发现某条走不通，停下来上报，不要自行改。

### 范围与关系

- **G1** 与旧 spec 关系：**完全推翻，从零重写**。旧 spec 与 stage0/stage1 计划整体归档作废，仅作业务知识参考。
- **G5** 交付面：**先做 CLI 管道，Web 后置**。删掉 `src/server.py`（3,878 行）与整个 Vue 前端 `src/static/`。Web 层作为后续阶段重新设计，不在本次范围内。
- **G6** 站点范围：**先只做 WL**（当前模板即 WL-V2，只有 WL 有真实数据）。但 **schema 必须多站点就绪**：`site_code` 作为列存在，任何字段、任何口径都不得把 WL 的具体值硬编码进代码。SY38 不做种子数据、不做验证。
- **G13** 建模范围：**目标覆盖 12 类约束，第一个端到端可运行里程碑只做 8 类**。物理资源 4 类（模具独占 / PM 避让 / ETA 齐套 / 劳动力人数上限）作为同一架构下的纯增量阶段（Stage E）。理由：这 4 类模板里都有列支撑、物理意义硬，不能永久缺席；但一次做 12 类会让实施失控。

### 数据层

- **G2** 「仿照数据集」的确切含义：**完整规范 schema**。内部定义一套规范化的实体/表/字段模型（主键外键、维度表 + 事实表、字段类型与单位、契约层级）。Excel 15 表只是其中一个**不完整的输入适配器**。
- **G3** 存储：**SQLite 单文件 + Repository 端口**。DDL 由实体登记表生成，不手写。禁用 SQLite 方言特性，预留 Postgres 移植。注意：SQLite 是嵌入式文件库，不是外部数据库服务，与"暂不引入外部数据库"不冲突。
- **G4** 依赖：**不新增任何依赖**。`requirements.txt` 保持 `openpyxl / ortools / fastapi / uvicorn / python-multipart` 五项不变。**不引入 pandas / pyarrow / polars**。
  - 环境实测版本（2026-11-05）：Python 3.12.14、ortools 9.15.6755、openpyxl 3.1.5、fastapi 0.159.1、pydantic 2.14.1（随 fastapi 传递安装）。
  - **pydantic 可用但刻意不用于实体建模**。理由：实体登记表已经是 schema 的唯一权威来源，若再用 pydantic 模型描述一遍字段，就出现两处 schema 定义，必然漂移。类型门校验器由登记表**通用驱动**，不需要每个实体写一个模型类。
- **G7** 数据入口：**数据全部从 Excel 导入，没有第二个入口**。不做手工数据编辑 UI，不做 CSV 回退路径，不做 API 写入。
- **G8** 缺数据处置：**扩展模板**（产出 V3），不编造数据。模板缺的 5 项由我们自己加 sheet/列并生成空模板交业务填。
  - 因此 **provenance 只有两值**：`template`（业务在 Excel 里填的）/ `derived`（DataSet 层推导的）。**没有 `simulated`**——编造数据不再是生产路径。开发期用测试 fixture，不走导入管道。
- **G9** 契约层级只剩 **T1 / T2** 两档，真正的 T3 消失（因为模板扩展后那 5 项有了位置）。详见 §3.4。
- **G20** 导入语义：**整站快照替换**。一次导入 = 一个事务；同站点重导 = 先删该站点全部行再插。**不做增量 merge**——Excel 是完整快照，增量 merge 会留下"上次有、这次删了"的幽灵行，这类 bug 极难查。跨站点互不影响。
- **G21** 派生量全收敛：**所有派生量在 DataSet 层计算一次并冻结，求解器只读不算**。这是"高局部性"的关键，直接针对 §0.1 问题 2。
- **G22** 冲突裁决表写死在本 spec（§3.2），不留给实施者现场判断。

### 规则架构

- **G10** 规则可扩展性：**A + B + C 三档全做**。A 策略参数文件（阈值/权重/开关/优先级/作用域，零代码）· B 指标注册表 + 归约算子（新增"聚合-比较"型规则零代码）· C 结构约束 Python 函数（新时序耦合必须写代码，但有统一签名、显式注册、契约测试兜底）。
- **G15** 类型隔离：**`MetricContext` 按类型不含 `model`**，只有 `StructureContext` 持有 `model`。这样"指标偷偷改模型"在类型层面不可能，不靠 code review。
- **G16** `rhs` 可引用另一个指标——这是整套设计里**唯一一处必须承认的复杂度**。不给它就会逼出通用表达式 DSL，那是更大的复杂度。
- **G17** 生效保证三机制：① 编译期字段检查 ② 运行时绑定报告（`compiled` 但 `constraint_count == 0` → 标红）③ 契约测试（**没有最小反例测试的规则不允许注册**，CI 强制）。详见 §5.4。

### 求解器

- **G11** 时间原子：**班次为最小时间原子 + 班内毫秒整数标度**。`unit_ms = round(CT_sec × 1000 / TRP)`。不采用细粒度时间片（变量爆炸），不采用连续时间 interval 模型（与"按班排产"的业务口径和报表形式不一致）。
- **G12** 目标函数：**严格字典序分层**。层间严格优先（bound freezing），**同层内的项必须可通约**；不可通约必须拆到不同层。详见 §6.4。
- **G18** 确定性（硬要求）：`num_workers=1` + 固定 `random_seed` + 固定 `max_deterministic_time`，且 **`max_deterministic_time` 必须先于墙钟时间预算生效**。同一 DataSet + 同一 policy → 逐位相同的解。
- **G23** **assumption literal 必须在建模时就加**到每条硬约束上，为 infeasible 最小不可满足核诊断服务。事后补不了。
- **G24** **PlanRow 是审计与导出的唯一出口接缝**。审计模块零依赖 `model`，只吃 PlanRow + DataSet。

### 验收与迁移

- **G14** 迁移策略：**方案 1 —— 新包并行 + 一次性切换**。新建 `aps/` 包，旧 `src/` 原封不动保留作对照物，新管道端到端通过验收后一次性 `git rm -r src/ tests/`。
  - 否决方案 2（先删后建）：中间状态长期不可运行，失去对照物。
  - 否决方案 3（绞杀者模式）：这正是旧 spec stage0/stage1 走的路线，已被 G1 否决；适配层自身会变成新技术债。
- **G19** 旧 golden baseline **只复用两条行为契约**：`on_time_delivery_pct = 100.0` 与 `below_95_shifts = 0`。`task_count = 19`、`total_quantity = 31391`、`min/max_calendar_coverage` **不复用**——它们依赖旧 netting 口径与旧数据快照，而我们要重推 netting 且模板要扩到 V3。新系统跑通后重新冻结 `aps_tests/fixtures/wl_v3_baseline.json`。
  - **这条区分极其重要**：若不复用与复用的界线不划清，实施者会去凑 `task_count=19` 那两个数字，反而把 netting 写错。
  - 推论：**新系统的排产结果允许与旧系统不同**，只要满足这两条行为契约 + L1/L2/L3 门。
- **G25** 分支处理：先在 `debug/bug-localization` 上提交在飞工作（不丢弃、不 stash），再从该 HEAD 开 `refactor/dataset-ortools`。不从 `main` 开的理由：`main` 严格落后 6 个提交，且 Stage A 的输入物 V2 模板未跟踪、只在提交后才存在。
- **G26** **Stage C5（把约束从 C 档迁到 B 档）必须迁一条验一条，不批量迁**。迁移会改变约束的表达方式，可能改变解；批量迁会让"解变了"无法归因。

### 自审时裁决的技术问题（详见 §12.2）

- **G27** 日历 `auto` 推荐阈值进 policy 文件（`calendar_auto_thresholds`），不进代码——否则就成了 WL 硬编码，违反 G6。
- **G28** V3「工作日历」sheet 填了就以它为准，`calendar_pattern` 只作未填时的推导来源；冲突报 `warn`。
- **G29** `material_arrival` 的 ETA 按**整班次二值**处理：`eta_shift` 折算绝对槽位号，该槽位及之后可用；`eta_time` 只用于报告展示，不进模型。

---

## 2. 非目标（明确排除，防范围蔓延）

- **不做 Web 层**。`src/server.py` 与 `src/static/` 在 Stage F 删除，不在本次重建。
- **不做 SY38 的种子数据与验证**（G6）。schema 多站点就绪 ≠ 双站点跑通。
- **不做手工排产 Excel 上传审计闭环**。审计模块只审求解器输出的 PlanRow。手工上传是 Web 层能力，随 Web 后置。
- **不做规则集版本库**（旧 `src/rule_sets/store.py` 的 SQLite 版本管理、语义 Diff、历史回滚）。策略参数是文件，用 git 管版本。
- **不做敏感度分析与冲突诊断 UI**（旧 `rule_sets/diagnose.py`、`sensitivity.py`）。infeasible 诊断做（§6.5），但只做 CLI 文本报告。
- **不做 30 天需求前瞻平准合批、冻结期时间围栏、库存策略 NORMAL/MONTH_END、工序提前期 C4、大单拆批跨班连续、共产品同步**。这些属于旧 32 条里的高阶规则，不在 12 类范围内。schema 为其中一部分留了字段（`inventory_policy`、`operation_leadtime`），但模型不读。
- **不引入外部数据库服务**（Postgres/MySQL）。Repository 端口预留移植能力，但本次只实现 SQLite。
- **不做增量数据 merge**（G20）。
- **不新增依赖**（G4）。

---

## 3. 数据集 schema

### 3.1 从 15 张表到 19 个规范实体

模板是**业务填写视图**，不是规范 schema。它有三类不规范：同一事实重复出现、维度表缺失、宽表。规范化后分两组：

**维度表（7）** —— 每个实体一处定义，其余表只存外键

| 实体 | 来源 | 关键字段 |
|---|---|---|
| `site` | 各表的「站点」列 | `site_code` PK · `name` · `default_calendar_mode` |
| `line` | 「产线基础数据」**吸收**各表的产线名 | `site_code` FK · `line_code` PK · `name` · `trp_efficiency` · `shift_hours` · `shifts_per_day` |
| `part` | **吸收**所有「品名描述」与「阶段」 | `site_code` FK · `pn` PK · `description` · `stage`(FG/SF/COMP/RAW) · `family_code` FK · `uom` |
| `mold` | 「工装模具约束」 | `mold_id` PK · `name` · `available_sets` · `changeover_min` |
| `customer` | 「客户战略评级」**整表吸收** | `customer_code` PK · `name` · `tier` · `supply_weight` · `otd_target_pct` · `due_tolerance_days` · `outsource_policy` |
| `product_family` | 各表的「产品族名」列 | `family_code` PK · `name` |
| `calendar_pattern` | 代码内枚举 + 模板扩展（§3.5） | `pattern_code` PK · `work_days` · `rest_days` |

**事实/主数据表（12）**

| 实体 | 来源 sheet | 主键 |
|---|---|---|
| `demand` | 客户需求订单清单（**宽表 unpivot**） | `site_code, pn, demand_date` |
| `initial_stock` | 期初可用库存 | `site_code, pn` |
| `material_arrival` | 关键物料到货 | `site_code, pn, eta_date, eta_shift, seq` |
| `part_param_fg` | 产线与成品参数 | `site_code, pn` |
| `part_param_sf` | 半成品制造参数 | `site_code, pn` |
| `bom` | 产品装配清单 | `site_code, parent_pn, child_pn` |
| `routing` | 工艺路线与候选机台 | `site_code, pn, op_seq, line_code` |
| `setup_matrix` | 换模矩阵 | `site_code, line_code, from_mold, to_mold` |
| `inventory_policy` | 库存策略 | `site_code, pn, period_type, start_day` |
| `operation_leadtime` | 工序提前期 | `site_code, pn, op_seq` |
| `tooling_mold` | 工装模具约束 | `site_code, mold_id, line_code` |
| `preventive_maintenance` | 设备预防维护 | `site_code, line_code, start_date, item` |

「填写说明 Instructions」不参与排产，不建实体。

Excel 适配器负责 15 表 → 19 实体的映射，**表头驱动定位列**（列序可自由调整），每个单元格错误带 `sheet / row / column / 原值` 定位。

### 3.2 冲突裁决表（写死，不留给实施者猜）

模板有多处同一字段多来源，这是旧系统 bug 的温床。**下表是最终裁决**：

| 字段 | 候选来源 | 裁决 |
|---|---|---|
| 单班工时 | `Parameters-FG.单班工时` **和** `产线基础数据.单班工时` | **以 `line` 为准**（产线基础数据）。FG 表该列**不读取**；两值不一致时报 `warn` |
| 每天班次数 | `Parameters-FG.每天班次数` **和** `产线基础数据.每天班次数` | 同上 |
| CT | `Routing-Machines.CT (秒)` | **唯一来源** |
| TRP | `产线基础数据.效率 TRP` | **唯一来源**。有效工时 = `CT / TRP` |
| 换型时间 | `Routing.换型时间(分钟)` / `Setup-Matrix.换模工时(分钟)` / `Tooling-Mold.换模工时(分钟)` | **三级优先级：`Setup-Matrix`（具体 from→to 对）> `Tooling-Mold`（模具级默认）> `Routing`（料号级默认）**。三者全缺 → DataGap |
| 人数 | `Routing.人数` | **唯一来源** |
| 安全库存 | `Inventory-Policy.安全库存` | **唯一来源** |
| 目标库存 | `Inventory-Policy.目标库存天数` | 唯一来源；不参与 MVP 模型（§2 非目标） |
| 模具编号 | `Parameters-FG/SFN.模具编号` / `Routing.模具清单` / `Tooling-Mold` | **FG/SFN 为准**。若不在 `Tooling-Mold` 中，按「适用零件料号」反查回退，并在数据提示中列出 |
| 产品族名 | `Parameters-FG` / `Parameters-SFN` / `Routing` | 以 `part.family_code` 为准（来自 Parameters 表）；`Routing.产品族名` 不一致时报 `warn` |

**裁决的落地形式**：这张表不是文档说明，而是登记表里的 `source_precedence` 声明。适配器按声明取数，一致性检查由业务门自动执行。表与代码不可能漂移。

### 3.3 已废弃列（模板 V2 已删，适配器不得读取）

FG/SFN 的 `产线` / `效率 TRP`；Routing 的 `备选候选线` / `机台` / `TRP`；物料到货的 `放行工时` / `关联排产零件` / `状态`。

旧文件若残留这些列，适配器**忽略且不报错**（结构门只对"缺必需列"报 error，对"多余列"报 warn）。

### 3.4 契约层级（T1 / T2 两档）

按**字段**声明，不是全局开关：

| 层级 | 含义 | 缺失时行为 |
|---|---|---|
| **T1** | 契约内且模板已供给 | `fail` —— 阻止入库、阻止求解 |
| **T2** | 契约内但业务可能未填（含 §3.5 新扩展的 5 项） | `warn` —— 照常入库，依赖它的指标标 `degraded`，求解继续，绑定报告可见 |

`on_data_gap` 的默认值来自字段的契约层级（T1 → `fail`，T2 → `warn`），规则实例可显式覆盖。

### 3.5 模板扩展清单（V2 → V3）

**这 5 项是 G8 的落地物。我们自己加，生成空模板交业务填，不等业务提。**

| # | 扩展内容 | 形式 | 卡住的规则 | 层级 |
|---|---|---|---|---|
| 1 | `demand.customer_code` | 「客户需求订单清单」**新增列** | 客户战略评级保供加权——当前需求行连不到客户。「客户战略评级」有`代表配装成品`列可反查，但一个客户对多个成品、一个成品可能供多个客户，反查脆弱 | T2 |
| 2 | 班次可用人数 | **新增 sheet**`班次人力可用性`：站点 / 产线 / 日期 / 班次 / 可用人数 | 劳动力人数上限。当前只有 `Routing.人数`（单产线单班定额），没有按班次的可用人力池 | T2 |
| 3 | L/R 配对表 | **新增 sheet**`产品配对关系`：站点 / 左件料号 / 右件料号 / 配对类型 / 同步容差班次 | 共产品同步产出（本次不做模型，但 schema 先就位） | T2 |
| 4 | 逐日班次日历 | **新增 sheet**`工作日历`：站点 / 产线 / 日期 / 班次序号 / 是否开班 / 备注 | 当前只有`单班工时 × 每天班次数`，工作日/休息日靠 `calendar_pattern` 推导，无法表达"某天临时停一班" | T2 |
| 5 | 换模技术员 | **新增 sheet**`换模技术员`：站点 / 技术员 / 日期 / 班次 / 可用产线 / 可同时换模数 | 换型技术员跨线互斥（本次不做模型，schema 先就位） | T2 |

扩展 1、2、4 影响 MVP 的 12 类范围（客户评级不在 12 类内，劳动力上限在 Stage E，日历影响所有阶段）。扩展 3、5 只为 schema 完整性，模型不读。

**V3 模板必须同时更新「填写说明 Instructions」sheet**，逐表说明新增字段的填法与留空后果。

### 3.6 provenance

每列两值标记：`template`（业务在 Excel 里填的）/ `derived`（DataSet 层推导的）。

绑定报告里，凡依赖 T2 字段且该字段整列为空的规则，状态自动为 `degraded`。这样"规则生效了"这句话永远带着"基于什么数据生效的"。

---

## 4. 数据层与管道

管道单向：**Excel → 四道校验门 → SQLite → DataSet → 求解器**。每段只认上一段的产物，不跨界。

### 4.1 SQLite 单文件与 Repository 端口

`data/aps.db` 一个文件，`site_code` 作列而非分库（G6 多站点就绪）。

**DDL 由实体登记表生成**，不手写 SQL。登记表同时是校验器、DDL 生成器、未来 UI 表单的唯一输入——避免三处漂移。

DDL 纪律（G3 预留 Postgres 移植）：
- 类型只用 `TEXT / INTEGER / REAL / NUMERIC`
- 不用 `INSERT OR REPLACE`、`ON CONFLICT` 的 SQLite 专有形式
- 不依赖隐式 rowid
- 主外键显式声明，`PRAGMA foreign_keys = ON`

Repository 端口只暴露四个方法：`list_by(entity, **filters)` · `get_by_pk(entity, pk)` · `upsert_many(entity, rows)` · `delete_where(entity, **filters)`。业务代码不得出现裸 SQL。

### 4.2 导入语义（G20）

一次导入 = 一个事务。同站点重导 = `delete_where(site_code=X)` 后 `upsert_many`。跨站点互不影响。失败整体回滚，不留半截数据。

### 4.3 四道校验门

| 门 | 检查什么 | 失败级别 |
|---|---|---|
| **结构门** | sheet 是否齐全 · 表头能否定位（列序可乱）· 必需列是否缺失 · 已废弃列（§3.3）是否残留 | 缺 sheet / 缺必需列 → `error`；多余列 / 废弃列残留 → `warn` |
| **类型门** | 每列按登记表声明的类型解析（含日期、时间、布尔的多种写法） | `error`，带 `sheet / row / column / 原值` |
| **参照门** | 主外键完整性：`pn ∈ part` · `line_code ∈ line` · `mold_id ∈ mold` · `family_code ∈ product_family` · `demand_date ∈ horizon` | `error` |
| **业务门** | 取值域（`MPQ > 0` · `CT > 0` · `TRP ∈ (0,1]` · `报废率 ∈ [0,1)` · `安全库存 ≥ 0` · `valid_from < valid_to`）+ §3.2 冲突裁决表的一致性检查 | 取值域 → `error`；冲突 → `warn` |

校验返回 `ValidationReport{errors, warnings, gaps}`。**校验不抛裸异常。** `errors` 阻止入库；`warnings` / `gaps` 照常入库，但会出现在绑定报告里。

这是"数据问题可见但不致命"的关键——旧系统在这里要么静默吞掉要么整体崩。

### 4.4 DataSet 聚合根（G21）

```
DataSet.load(site: str, horizon: Horizon, policy: Policy) -> DataSet
```

内部是 `tuple[Entity, ...]` + 索引字典（by pk / by fk）。**不用 pandas**（G4）。

**所有派生量在此计算一次并冻结，求解器只读不算**：

| 派生量 | 计算 |
|---|---|
| 净需求 | `客户需求 − 期初可用库存 + 安全库存`；库存已覆盖的需求行不生成任务 |
| 有效工时 | `unit_ms(t) = round(CT_sec × 1000 / TRP)` |
| 班次槽位表 | `(line, date, shift_index)` → 绝对槽位号 + 时间窗；由 `line.shift_hours × line.shifts_per_day` + `calendar_pattern` 生成 |
| 换型时间 | §3.2 三级回退的**最终结果**，不留给求解器现场判断 |
| 模具反查表 | `mold_id → 适用料号集合` |
| 认证产线 | `pn → [line_code]`，来自 `routing.certified == True` 且在生效期内 |
| 交期槽位 | `due_date → due_slot`（绝对槽位号） |
| MPQ / MOQ | 按 `part.stage` 从 `part_param_fg` / `part_param_sf` 取，FG 与 SF 口径不同（§6.3 第 5、6 条） |

DataSet 是**不可变的**（frozen dataclass）。求解器拿到的是快照，不会看到中途修改。

求解器只认 `DataSet`，不认 SQLite，也不认 Excel。

---

## 5. 三档规则架构与生效保证

### 5.1 为什么必须分三档

CP-SAT 约束不是同质的。「方便新增规则」必须分档谈，任何声称全都能配置化的方案都在撒谎：

| 档 | 变更类型 | 例子 | 零代码 |
|---|---|---|---|
| **A 参数级** | 改阈值、权重、开关、优先级顺序、作用域 | 利用率门槛 95%→92%；换型权重调高；某规则关掉 | ✅ 完全能 |
| **B 指标级** | 对**已有维度**做聚合再比较 | 「每产线每班次负荷 ms ≤ 额定容量 ms」；「某料号延误班次总数最小化」；「同班次并行型号数 ≤ 3」 | ✅ 能，需先建指标库 |
| **C 结构级** | 引入**新变量或新时序耦合** | FG 等 SF 齐套；共产品同班次同步；换型技术员跨线互斥；大单跨班拆批连续 | ❌ 必须写 Python |

旧系统 32 条目录规则只有 4 条真进了模型，根因不是缺配置层，而是**缺生效保证**。所以 §5.4 比 §5.2 更重要。

### 5.2 A 档 —— 策略参数文件

`config/policy/WL.json`，一个站点一个文件。业务可改的全部旋钮都在这里：

```json
{
  "site": "WL",
  "start_date": "2026-11-03",
  "horizon_days": 10,
  "calendar_mode": "auto",
  "calendar_auto_thresholds": {"4on3off": 0.5714, "5on2off": 0.7143, "6on1off": 0.8571},
  "utilization_floor_pct": 95.0,
  "utilization_ceiling_pct": 105.0,
  "priority_layers": [
    {"layer": "P0", "terms": ["late_shifts"]},
    {"layer": "P1", "terms": ["util_below_floor_hours", "util_above_ceiling_hours"]},
    {"layer": "P2", "terms": ["changeover_blocks"]},
    {"layer": "P3", "terms": ["changeover_minutes"]}
  ],
  "term_weights": {"util_below_floor_hours": 100, "util_above_ceiling_hours": 60},
  "rules": {
    "assign_exactly_one":   {"enabled": true},
    "min_batch_fg":         {"enabled": true,  "nature": "hard"},
    "concurrency":          {"enabled": true,  "max_models_per_shift": 3},
    "mold_exclusive":       {"enabled": false, "reason": "T2：tooling 数据未填"}
  },
  "solver": {
    "time_limit_seconds": 90,
    "layer_time_budget": [45, 25, 15, 10],
    "num_workers": 1,
    "random_seed": 42,
    "max_deterministic_time": 1.0e12
  }
}
```

- `calendar_mode`：`auto` | `continuous` | `5on2off` | `4on3off` | `6on1off`。`auto` 时按 `周总需求 / 极限产能` 比值推荐，**阈值取自 `calendar_auto_thresholds` 而非硬编码**（G27）：比值 ≤ `4on3off` 阈值 → 做四休三；≤ `5on2off` → 做五休二；≤ `6on1off` → 做六休一；否则连续生产。
- 「工作日历」sheet 填了就以它为准，`calendar_mode` 与其推导结果冲突时报 `warn`（G28）。
- `rules.<id>.enabled = false` 必须允许带 `reason` 字符串，绑定报告原样输出。
- 策略文件是**数据不是代码**，用 git 管版本（§2 非目标：不做版本库）。

### 5.3 B 档 —— 指标注册表

```python
@dataclass(frozen=True)
class MetricSpec:
    name: str                   # "line_shift_load_ms"
    dims: tuple[str, ...]       # ("line", "slot")   ← 维度即类型系统
    unit: str                   # ms | pieces | shifts | hours | count | ratio
    reduction: str              # sum | count | max | min | avg | count_if
    requires: tuple[str, ...]   # DataSet 字段全路径，编译期检查
    contract_tier: str          # T1 | T2
    compute: Callable[[MetricContext], Any]
```

规则实例是纯声明：

```json
{"kind": "metric",
 "metric": "line_shift_load_ms", "reduction": "sum",
 "op": "<=", "rhs": {"metric": "line_shift_capacity_ms"},
 "scope": {"line": "Felx转子#2线"},
 "nature": "hard",
 "on_data_gap": "fail"}
```

**维度即类型系统**：`dims` 决定这个指标能在什么 scope 下聚合、能与什么 `rhs` 比较。`dims=("line","slot")` 的指标不能与 `dims=("part",)` 的指标直接比较——编译期报错，不是运行时出鬼结果。

**`scope` 过滤固定维度词汇表**，不是开放属性名：`site` / `line` / `stage` / `family` / `part` / `customer_tier`。写别的键 → 编译期报错。

**`MetricContext` 按类型不含 `model`**（G15）。它只有 DataSet 投影与变量映射：`units(task,line,slot)` · `active(task,line,slot)` · `assign(task,line)` · `load(line,slot)` · `cum(task,line,slot)` · `open(line,slot)` · 以及 DataSet 的只读访问器。只有 `StructureContext` 持有 `model`。

**编译器是一个通用函数，不是 N 个分派分支。** 语义表：

| `reduction` | `op` | `nature=hard` | `nature=soft` |
|---|---|---|---|
| `sum` / `count` / `max` / `min` / `avg` / `count_if` | `<=` `>=` `==` | `model.Add(...)` | 松弛变量 + 惩罚项进目标 |
| — | `minimize` / `maximize` | **与 `nature` 互斥**，同时出现 → 编译期报错 | 直接作为目标项 |

`avg` 的实现必须是 `sum / count` 的整数化形式（CP-SAT 无除法），即 `sum ≤ avg_bound × count`。这条写进计划，否则实施者会写出浮点系数。

**初始指标库（12–16 个）**，Stage C 建：
`line_shift_load_ms` · `line_shift_capacity_ms` · `task_late_shifts` · `task_total_qty` · `line_shift_utilization_pct` · `line_shift_model_count`（并行型号数）· `line_changeover_blocks` · `line_changeover_minutes` · `task_first_slot` · `task_last_slot` · `part_stock_balance` · `line_shift_open`

每个指标必须有契约测试（§5.4 ③）。

### 5.4 C 档 —— 结构约束

```python
@structure_rule("fg_waits_for_sf", requires=("bom.ratio", "part.stage"))
def fg_waits_for_sf(ctx: StructureContext) -> None: ...
```

统一签名 · 导入时自动收集 · **未注册即不存在** · 必须声明 `requires`。

**MVP 8 类核心里属于 C 档的 6 条**：

| id | 作用 |
|---|---|
| `assign_exactly_one` | 每任务恰好选一条认证产线 |
| `fg_waits_for_sf` | x 班 SF 产出可供 x+1 班 FG（跨阶段时序耦合） |
| `mpq_rounding_fg` | FG 每班产出为 MPQ 整数倍 |
| `sf_total_mpq` | SF 只要求**总量** ≥ MPQ，单班可任意正整数 |
| `changeover_block_count` | 型号连续块计数（换型次数） |
| `utilization_tail_exempt` | 识别每条产线最后一个收线班次并豁免利用率下限 |

**Stage E 增量的 4 条**：`mold_exclusive` · `pm_block` · `material_ready_eta` · `labor_headcount_cap`

其余核心约束（班次产能、净需求守恒、交期）能用 B 档表达，Stage B 先以 C 档实现跑通，Stage C5 逐条迁移（G26）。

### 5.5 生效保证（G17）—— 让"方便新增"不等于"新增了但没生效"

**① 编译期字段检查**（求解前，不消耗求解时间）

| 规则 `requires` 的状态 | 规则状态 | 行为 |
|---|---|---|
| 全部 T1 且有数据 | `compiled` | 正常进模型 |
| 含 T2 字段且该列为空 | `degraded` | 按 `on_data_gap` 决定 `warn` / `skip` |
| 字段在 schema 里不存在 | `unenforced` | **若 `enabled=true` 则整体报错**，不允许静默通过 |
| `enabled = false` | `disabled` | 不进模型，报告里可见（含 `reason`） |

**② 运行时绑定报告**：每条规则输出 `状态` + `实际产生的约束数` + `惩罚项数` + `依赖字段的 provenance`。

> **`compiled` 但 `constraint_count == 0` → 报告标红，且 L2 验收门判 FAIL。**

这正是"声明了却没编译"（旧缺陷 A5/A7）的检测器。

**③ 契约测试**：每个 B 档指标和每个 C 档结构规则必须配一个**最小反例测试**——构造一个违反该规则的解，断言求解器拒绝它（hard）或惩罚它（soft）。

> **没有反例测试的规则不允许注册。** 由 CI 强制：注册表遍历 × 测试文件里是否存在对应 `test_contract_<rule_id>`。

这条最容易被实施者跳过，必须在计划里写成硬门。

---

## 6. CP-SAT 模型

### 6.1 时间轴与标度（G11）

槽位 = `(line, date, shift_index)`，绝对槽位号 `s = day_index × shifts_per_day + shift_index`。槽位表由 DataSet 冻结产出（§4.4），**求解器不重算**。

两个整数标度（CP-SAT 只接受整数系数）：
- **时长统一毫秒**：`unit_ms(t) = round(CT_sec × 1000 / TRP)`；`slot_capacity_ms(l,s) = shift_hours × 3_600_000`
- **产量统一件**

### 6.2 变量族

| 变量 | 类型 | 含义 |
|---|---|---|
| `assign[t,l]` | Bool | 任务 t 分配到认证产线 l |
| `x[t,l,s]` | Int `[0, per_shift_cap]` | t 在 l 的槽位 s 的产出件数 |
| `active[t,l,s]` | Bool | `x > 0 ⟺ active` |
| `started[t,l,s]` | Bool | 该槽位**开工**（换型块计数与首班识别） |
| `cum[t,l,s]` | Int | 累计产出（跨阶段齐套用） |
| `end[t]` | Int | 任务完工槽位 |
| `late[t]` | Int ≥ 0 | 相对交期槽位的延误班次数 |
| `load[l,s]` | Int | 产线 × 槽位负荷 ms |
| `open[l,s]` | Bool | 该产线该槽位是否开班 |
| `tail[l,s]` | Bool | 该产线最后一个收线班次（利用率豁免） |
| `block[t,l,s]` | Bool | 型号连续块起点（换型计数） |

**任务粒度** = 一个料号的净需求（`part` × `site`）。`x[t,l,s]` 天然支持跨班拆分；拆批**连续性**约束不在 MVP 范围（§2 非目标）。

**规模估算**：WL 19 任务 × 2 认证线 × 30 槽位（10天×3班）≈ 1.1k 个 `x` 加同量级 Bool。CP-SAT 很轻松。但 horizon 拉到 30 天、任务数上百时会显著变大——**计划里必须有一次规模压测任务**，不能等上线才发现。

### 6.3 九条硬约束（MVP 8 类核心）

1. **分配唯一**：`AddExactlyOne(assign[t,l] for l in certified_lines(t))`
2. **产出与分配一致**：`x[t,l,s] ≤ per_shift_cap(t,l) × assign[t,l]`
3. **净需求守恒**：`Σ_{l,s} x[t,l,s] == net_requirement(t)` ← 净需求在 DataSet 算，模型只守恒
4. **班次产能**：`load[l,s] == Σ_t unit_ms(t) × x[t,l,s] ≤ slot_capacity_ms(l,s) × open[l,s]`
5. **MPQ 取整（FG）**：`x[t,l,s] == mpq(t) × q[t,l,s]`，`q` 为 Int
6. **MPQ 门槛（SF）**：`Σ_s x[t,l,s] ≥ mpq(t)` 或 `== 0`；**单班可为任意正整数（甚至 1 件）**
7. **跨阶段齐套**：`cum_sf[child,s] ≥ ratio × cum_fg[parent, s−1]` ← x 班 SF 产出可供 x+1 班 FG
8. **交期**：`late[t] ≥ end[t] − due_slot(t)`，`late[t] ≥ 0`
9. **利用率下限带末班豁免**：`load[l,s] ≥ floor_ms × open[l,s] − M × tail[l,s]`

**第 7 条是最容易写错的一条**：`s−1` 偏移是两阶段按件流水搭接的核心。`s=0` 时右端为 0（无前置班次）。期初库存已覆盖部分计入 `cum_sf` 的初始值。

**第 6 条的业务口径**：SF 的 MPQ 是**订单总量门槛**，不是单班批量。总量达标后单班可逐件生产。若按单班 MPQ 取整，会把负荷推到 105% 以上。

### 6.4 字典序求解与 bound freezing（G12）

```python
for i, layer in enumerate(policy.priority_layers):
    obj = weighted_sum(layer.terms)          # 同层内加权和
    model.Minimize(obj)
    status = solver.Solve(model)
    if status not in (OPTIMAL, FEASIBLE):
        diagnose_and_stop()                  # §6.5
    model.Add(obj <= solver.Value(obj))      # bound freezing
```

**同层可通约纪律（G12）**：层间严格优先，**同层内的项必须可通约**（同量纲，或已归一化到同一量纲）。

- P1 的 `util_below_floor_hours` 与 `util_above_ceiling_hours` 都是「小时」→ 可通约，同层加权。
- 换型「次数」与「分钟」不可通约 → 必须拆成 P2 / P3 两档。
- **不可通约却塞进同一层 = 设计错误**，policy 加载器要能检出（按 `MetricSpec.unit` 比对同层各项单位，不一致则报错）。

**成本要说清楚**：4 档 = 4 次求解。90s × 4 = 最坏 6 分钟。缓解：bound freezing 后下层搜索空间大幅收窄；`layer_time_budget` 按层递减分配（45/25/15/10）。

### 6.5 infeasible 诊断是一等责任（G23）

字典序某层 infeasible 时，**不能只返回"无解"**。

- 每条硬约束在建模时加一个 **assumption literal**（`model.AddBoolOr([...]).OnlyEnforceIf(a_i)`）
- infeasible 时调 `solver.SufficientAssumptionsForInfeasibility()` 求最小不可满足核
- 把核里的 assumption 映射回规则 id 与作用域，输出人话报告：「`line_shift_capacity`（Felx转子#2线, 2026-11-07 中班）与 `net_requirement`（W590578TJ）冲突：该班次剩余容量 2,140 件·ms，但该料号净需求需要 3,800 件·ms」

**assumption literal 必须在建模时就加，事后补不了。** 这是 §6.3 九条约束的实现前提，不是可选增强。

### 6.6 确定性（G18）

`num_workers=1` · `random_seed=42` · 固定 `max_deterministic_time`。同一 DataSet + 同一 policy → 逐位相同的解。

**必须承认的取舍**：`max_time_in_seconds` 是墙钟时间，会因机器负载产生不同解，破坏可复现性。所以**同时设 `max_deterministic_time` 且让它先于墙钟预算生效**。代价：慢机器上可能提前停止、解质量下降。

这个代价值得付——绑定报告、回归测试、L2 门全部依赖确定性。

### 6.7 结果层：PlanRow 是唯一出口接缝（G24）

`ScheduleResult` → `PlanRow` 列表：

```
(site, pn, description, stage, line, date, shift_index, shift_name,
 quantity, load_ms, load_hours, utilization_pct,
 changeover_flag, sequence_no, cumulative_qty)
```

无论结果来自求解器、手工调整的 Excel、还是将来接 MES，都穿过同一个 DTO。审计模块**零依赖 `model`**，只吃 PlanRow + DataSet——这样审计才是真正的第三方裁判，而不是求解器自证。

---

## 7. 独立审计与验收标准

### 7.1 审计做时序离散仿真，不复用求解器任何中间量

`aps/audit/` 只吃 `PlanRow` + `DataSet`。按班次前向推演库存账本（期初 + SF 产出 − FG 消耗 − 客户需求），**独立重算**。

九项：

| # | 审计项 |
|---|---|
| 1 | 需求覆盖完整性：每张需求行的产出总量 + 期初库存 ≥ 需求量 |
| 2 | 班次拆解守恒：Σ 各班产出 == 工单总量 |
| 3 | 单班物理容量：`load_ms ≤ slot_capacity_ms` |
| 4 | 逐班物料齐套：SF 库存 ≥ FG 消耗（按 BOM ratio） |
| 5 | MPQ 整数件：FG 每班产出 % MPQ == 0；SF 总量 ≥ MPQ |
| 6 | 换型计数：连续块数与 PlanRow 的 `changeover_flag` 一致 |
| 7 | 利用率：含末班豁免口径 |
| 8 | 交期达成率 |
| 9 | **净需求正确性** |

**第 9 项审计的是数据层而非求解器**——独立重算 netting 并与 DataSet 比对。这是唯一能抓住 §4.4 派生量算错的检查，其他八项都抓不到。

**变异矩阵**：给审计器喂故意篡改的 PlanRow（删单 / 超载 / 跳序 / 欠产 / 改数量 / 挪班次），断言全部被拦截。这是审计器自己的测试，防止它退化成橡皮图章。

### 7.2 旧 golden baseline 只复用一半（G19）

旧 `tests/fixtures/wl_10d_baseline.json` 记的是**不变量而非具体排程**：

```json
{"site": "WL", "horizon_days": 10, "start_datetime": "2026-10-12T07:00:00",
 "expected": {"task_count": 19, "total_quantity": 31391,
              "on_time_delivery_pct": 100.0, "below_95_shifts": 0,
              "min_calendar_coverage": 0.7, "max_calendar_coverage": 1.0}}
```

| 不变量 | 处置 | 理由 |
|---|---|---|
| `on_time_delivery_pct = 100.0` | **复用为行为契约** | 与实现无关的业务承诺 |
| `below_95_shifts = 0` | **复用为行为契约** | 同上 |
| `task_count = 19` | **不复用** | 依赖旧 netting 口径与旧数据快照 |
| `total_quantity = 31391` | **不复用** | 同上 |
| `min/max_calendar_coverage` | **不复用** | 日历推荐逻辑重写 |

新系统跑通后重新冻结 `aps_tests/fixtures/wl_v3_baseline.json`。

### 7.3 四道验收门

| 门 | 判据 |
|---|---|
| **L1 数据门** | V2 模板导入 → 校验 `errors = 0`；19 实体行数与模板一致；派生量抽样人工核对（净需求 / unit_ms / 槽位表 / 换型三级回退各至少 3 个样本） |
| **L2 求解门** | 端到端跑通；`on_time_delivery_pct = 100`；`below_floor_shifts = 0`；绑定报告里**没有** `enabled=true` 却 `unenforced` 的规则；**没有** `compiled` 却 `constraint_count = 0` 的规则 |
| **L3 审计门** | 九项全 PASS；变异矩阵全拦截 |
| **确定性门** | 同一输入连跑 3 次，PlanRow 逐字节相同 |

L2 的后两条是 §5.5 生效保证的验收形式——它们把「规则真的进了模型」变成可自动判定的事。

### 7.4 测试策略：四类，职责不互相替代

| 类型 | 测什么 | 速度 |
|---|---|---|
| **契约测试** | 每个 B 档指标 + 每个 C 档结构规则的最小反例；登记表 ↔ DDL ↔ 校验器三处一致；同层可通约检查 | 秒级，CI 强制 |
| **单元测试** | DataSet 派生量 · 四道校验门 · bound freezing · 日历推荐 · policy 加载 | 秒级 |
| **审计变异测试** | 篡改 PlanRow → 断言拦截 | 秒级 |
| **端到端** | V2 模板 → 求解 → 导出 → 审计，L1/L2/L3 全过 | 分钟级，含求解器 |

`--fast` 跳过端到端（每次提交必跑）；全量含求解器（阶段门槛）。

**测试框架必须是标准库 `unittest`**（G4 的直接推论）。环境实测：**pytest 未安装**，旧测试全部基于 `unittest`。实施者若 `import pytest` 会直接 `ModuleNotFoundError`。这条必须在计划的 Global Constraints 里显式写出，否则第一个写测试的任务就会卡住。

推论：
- 断言用 `self.assertEqual` / `assertTrue` / `assertRaises`，不用 `assert x == y`
- 参数化用 `subTest()`，不用 `pytest.mark.parametrize`
- 契约测试的 CI 检查（§5.5 ③）遍历注册表 × `unittest` 测试方法名，方法名必须为 `test_contract_<rule_id>`
- fixture 用 `setUp` / `setUpClass`，不用 pytest fixture

**测试目录在并行期用 `aps_tests/`，Stage F2 才改名为 `tests/`。** 理由与 G14 的隔离逻辑一致：旧 `tests/`（42 套件）在 Stage A–E 期间必须保留可运行，作为对照物；新旧测试同处 `tests/` 会让 `run_tests.py` 的 discover 把两套混在一起跑，无法归因失败。

---

## 8. 目标代码结构

```
aps/
├── schema/
│   ├── registry.py      实体登记表：字段名·类型·单位·主外键·契约层级·source_precedence
│   ├── ddl.py           DDL 生成器（禁方言）
│   └── entities.py      dataclass 实体（由登记表驱动，不重复声明字段类型）
├── data/
│   ├── repository.py    Repository 端口（list_by / get_by_pk / upsert_many / delete_where）
│   ├── sqlite.py        SQLite 实现
│   ├── excel.py         Excel 适配器：V2/V3 → 19 实体，表头驱动，单元格级错误定位
│   ├── validate.py      四道校验门 + ValidationReport{errors, warnings, gaps}
│   └── template_v3.py   V3 模板生成器（§3.5 五项扩展 + 填写说明）
├── dataset.py           DataSet 聚合根：派生量冻结（不可变）
├── calendar/
│   ├── geometry.py      班次几何：shift_hours × shifts_per_day → 槽位
│   └── pattern.py       四档工作制 + auto 推荐
├── policy.py            【A档】策略参数加载 + 同层可通约检查
├── model/
│   ├── context.py       ModelContext / MetricContext / StructureContext（类型隔离）
│   ├── variables.py     §6.2 变量族 + assumption literal
│   ├── metrics/         【B档】MetricSpec · 初始指标库 · 通用编译函数
│   ├── structure/       【C档】结构约束，统一签名 + 显式注册
│   └── compile.py       规则实例 → 约束/惩罚项
├── solve.py             字典序分层 + bound freezing + 确定性参数
├── diagnose.py          infeasible 最小不可满足核 → 人话报告
├── binding.py           生效保证：编译期字段检查 + 绑定报告
├── result/
│   ├── plan_row.py      PlanRow DTO（唯一出口接缝）
│   └── metrics.py       利用率 · PSI · 延误统计
├── export/
│   └── excel.py         Production plan 导出
├── audit/
│   ├── engine.py        九项审计 + 时序库存仿真（零依赖 model）
│   └── mutation.py      变异矩阵
└── cli.py               端到端管道编排
```

`model/context.py` 是整个设计的枢纽：B 档指标**按类型拿不到 `model`**（只有 `StructureContext` 有），这样「指标偷偷改模型」在类型层面就不可能。

---

## 9. 阶段划分

每个阶段有独立验收门，**未过门不得进入下一阶段**。

| 阶段 | 内容 | 验收门 |
|---|---|---|
| **A0** | 清理与脚手架：清理死物（`src/scenarios/` 与 `src/infrastructure/` 的 `.pyc` 残骸——源文件已不存在，Python 不会从 `__pycache__` 导入；`src/static/index.html.original_backup`；`src/static/index.html.ui_master_backup`；顶层 `__pycache__/`）· 建 `aps/` 与 `aps_tests/` 空包骨架 · 重写 `run_tests.py` 支持双目录 discover（§10） | 旧系统仍能跑通 `run_tests.py --fast`；新 runner 对空 `aps_tests/` 返回 0 项而非报错 |
| **A** | 数据层：A1 实体登记表 · A2 DDL 生成器 + Repository · A3 Excel 适配器 · A4 四道校验门 · A5 DataSet 聚合根 + 派生量 · A6 V3 模板生成 | **L1** + 登记表↔DDL↔校验器一致性契约测试 |
| **B** | 求解器核心：B1 Context 类型隔离 · B2 变量族 + 九条硬约束（含 assumption literal）· B3 C 档 6 条 + 注册机制 · B4 policy 加载器 · B5 字典序 + bound freezing + 确定性 · B6 infeasible 诊断 · B7 绑定报告 · B8 PlanRow | **L2** + **确定性门** + 每条 C 档规则的最小反例测试 + 规模压测 |
| **C** | 指标层：C1 MetricSpec + 初始指标库 · C2 通用编译函数 · C3 `rhs` 引用指标 · C4 scope 谓词 · C5 从 C 档逐条迁移 | 每个指标的契约测试；**C5 迁一条验一条（G26）** |
| **D** | 审计与导出：D1 九项审计 + 时序仿真 · D2 变异矩阵 · D3 Excel 导出 · D4 CLI 编排 | **L3**；CLI 一条命令跑完全链路 |
| **E** | 物理资源增量：E1 `mold_exclusive` · E2 `pm_block` · E3 `material_ready_eta` · E4 `labor_headcount_cap` | 每条最小反例测试 + L2/L3 重跑 |
| **F** | 一次性切换：F1 冻结 `aps_tests/fixtures/wl_v3_baseline.json` · F2 `git rm -r src/ tests/` + `aps_tests/` 改名为 `tests/` · F3 重写 README / ARCHITECTURE.md，旧 spec 与两份计划移入 `archive/Specification/` · F4 删除清单落地（§10） | 全量测试通过；仓库里只剩 `aps/` 与 `tests/`（原 `aps_tests/`）；`git grep "from src"` 与 `git grep "import src"` 零命中 |

**Stage B 与 Stage C 的次序不可颠倒**：先用 C 档硬编码把九条约束跑通，拿到一个正确但不可配置的求解器；再建 B 档并把可表达的规则迁过去。反过来做会同时调试「建模错误」和「编译器错误」两类问题，无法归因。

---

## 10. 删除清单

### F2 删除

- 整个 `src/`（20,238 行 Python）
- 整个 `tests/`（42 套件）
- `run_web.py`（Web 后置，重写时再建）
- `scripts/`（11 个一次性数据修补脚本，随旧 schema 失效）
- `data/*.csv` 与 `data/master/*.csv`（旧加载器输入，Stage A 后不再被读取）
- `data/snapshot_WL.json` / `data/snapshot_SY38.json` / `data/site_settings.json` / `data/rule_sets.sqlite3`
- `dataSource.xlsx` / `APS Database- Simulation draft.xlsx` / `Production Plan Validation.xlsx` / `WL_WL_PHASE1_v6.xlsx`
- `aps_project_lifecycle.html`（顶层）

### F2 改名

- `aps_tests/` → `tests/`（§7.4：并行期物理隔离，切换时统一）
- `aps/` 保持为顶层包名，不改名

### Stage A0 重写（不是删除）

- `run_tests.py` —— 旧 runner 的 `--fast` 模块清单是硬编码的 7 个旧模块名，对新代码无效。Stage A0 重写为：discover `aps_tests/` 与 `tests/` 两个目录、`--fast` 按标记（模块级 `FAST = True` 常量）筛选而非硬编码清单。旧 `tests/` 在 Stage F2 之前仍需可运行，所以 runner 必须同时支持两个目录。

### 保留

- `APS_DataSource_Template - WL -V2.xlsx` —— Stage A 的输入与 L1 门的对账物
- `APS_业务规则确认清单.xlsx` —— 业务事实参考
- `requirements.txt`（依赖不变；fastapi/uvicorn 留给 Web 后置阶段）
- `Dockerfile`（改入口）· `.env.example` · `.gitignore`
- `data/uploads/` · `data/logs/`

### 归档不删（`archive/Specification/`）

- `aps_rule_set_redesign.md`（业务知识：缺陷索引、32 条目录规则映射）
- `plans/2026-11-03-stage0-foundation.md` · `plans/2026-11-05-stage1-dual-track.md`
- `2026-11-03-spec-review-priority-plan.md`（事实核对方法论）
- `aps_functional_spec.md` · `aps_new_rule_set_gap_analysis.md` · `aps_rule_set_editor_manual.md` · `aps_masterdata_algorithm_impact.md` · `aps_progress_dashboard.md` · `master_template_verification.md` · `masterdata_simulation_notes.md` · `tab6_template_upload_guide.md` · `aps_implementation_log.md`
- `config/rule_catalog/aps_32_rules.json`（业务分类法，Stage C 的 scope 词汇参考）
- `config/rule_sets/*.json`（4 份种子规则集，作为 policy 文件的初始值参考）

### 分支处理（G25，需用户批准）

工作区当前有在飞工作：`src/kernel/trace.py` + `tests/test_rule_trace.py`（未跟踪）、`src/scheduler.py` / `src/server.py` 已改、V2 模板未跟踪、stage1 计划未跟踪、三个旧模板已删未提交。

处置：
1. **先在 `debug/bug-localization` 上提交这些工作**——不丢弃、不 stash。
2. **从该 HEAD 开 `refactor/dataset-ortools`**。不从 `main` 开：`main` 严格落后 6 个提交，且 Stage A 的输入物 V2 模板只在第 1 步后才存在于版本控制中。

---

## 11. 环境与执行约定（交接给实施计划）

### 11.1 环境事实（照抄，不要自己试）

- OS：Windows 10.0.22631，shell 为 Git Bash
- Python：`.venv/Scripts/python.exe`，版本 3.12.14
- **所有 Python 调用必须带 `PYTHONIOENCODING=utf-8`**。否则中文输出触发 `UnicodeEncodeError: 'charmap' codec can't encode`，产生**假失败**。本次设计过程中已实测踩到。
- ortools 9.15.6755 · openpyxl 3.1.5 · fastapi 0.159.1 · pydantic 2.14.1（传递依赖，不用于实体建模，见 G4）
- `gh` CLI **未安装**，不要用它
- git remote 只有 `origin`

### 11.2 统一验证命令

```bash
# 快测试（秒级）—— 每个提交的最低门槛
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast

# 全量（含求解器，分钟级）—— 阶段门槛
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py

# 模板对账（改 schema 或登记表后必跑）
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m aps.cli verify-template --excel "APS_DataSource_Template - WL -V2.xlsx"

# 导入：Excel → 校验 → SQLite（整站快照替换，G20）
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m aps.cli import --site WL --excel "APS_DataSource_Template - WL -V2.xlsx"

# 端到端求解（含绑定报告输出）
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m aps.cli solve --site WL --policy config/policy/WL.json

# 导出 Production plan Excel
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m aps.cli export --site WL --out "data/exports/WL_plan.xlsx"

# 独立审计（只吃 PlanRow + DataSet，零依赖 model）
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m aps.cli audit --site WL

# 确定性门（连跑 3 次逐字节比对）
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m aps.cli determinism-check --site WL --runs 3

# 生成 V3 空模板（§3.5 五项扩展 + 填写说明）
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m aps.cli make-template --version v3 --site WL --out "APS_DataSource_Template - WL -V3.xlsx"
```

CLI 子命令即管道的分段：`import` / `solve` / `export` / `audit` 可独立运行，也可用 `pipeline` 一次跑完。**每个子命令都必须能独立运行**——这是 §4「每段只认上一段的产物」的可执行形式，也是实施者调试时的唯一入口。

### 11.3 禁止事项（每条对应本文档一个已裁决的决策）

| 禁止 | 因为 |
|---|---|
| 引入 pandas / pyarrow / polars 或任何新依赖 | G4 |
| **`import pytest`** 或任何 pytest 特性（fixture / `parametrize` / 裸 `assert`） | G4 + §7.4：**pytest 未安装**，测试必须用标准库 `unittest` |
| **把新测试写进 `tests/`** | §7.4：并行期新测试一律写进 `aps_tests/`，Stage F2 才改名 |
| 用 pydantic 模型描述实体字段 | G4：登记表是唯一 schema 来源，两处定义必然漂移 |
| 在业务代码里写裸 SQL | G3：只经 Repository 端口 |
| 用 SQLite 方言 DDL | G3：预留 Postgres 移植 |
| 增量 merge 导入 | G20：会留幽灵行 |
| 在求解器里计算派生量 | G21：全部在 DataSet 冻结 |
| 让 `MetricContext` 拿到 `model` | G15：类型层面禁止 |
| 注册没有最小反例测试的规则 | G17 ③ |
| 用 `max_time_in_seconds` 单独控制求解预算 | G18：破坏确定性 |
| 硬编码 WL 的具体值（产线名、料号、班次几何） | G6：schema 多站点就绪 |
| 硬编码年份 `2026` | 日期一律来自 policy `start_date` 与数据 |
| 校验失败抛裸异常 | §4.3：必须返回 `ValidationReport` |
| 批量迁移 C 档 → B 档 | G26：解变了无法归因 |
| 建通用表达式 DSL | G16：`rhs` 引用指标是承认的唯一复杂度 |
| 在 Stage B 之前建 B 档指标层 | §9：会同时调试两类错误，无法归因 |
| 丢弃工作区未提交的在飞工作 | G25 |

### 11.4 代码约定

- 实体、DataSet、MetricSpec、Policy 全部 `@dataclass(frozen=True)` 或等价不可变形式
- 每个模块文件目标 ≤ 400 行；超过即拆分信号（旧系统三大巨型文件是反面教材）
- 注释默认不写；只在 WHY 非显然时写一行（隐藏约束、微妙不变量、特定 bug 的 workaround）
- 类型标注全覆盖；`dims` / `unit` / `contract_tier` 用 `Literal` 或枚举，不用裸 `str`
- 测试文件命名 `aps_tests/test_<module>.py`（Stage F2 后为 `tests/`）；测试类继承 `unittest.TestCase`；**契约测试方法名必须为 `test_contract_<rule_id>`**（§5.5 ③ 的 CI 检查依赖此命名，遍历注册表比对该方法是否存在）
- 需要跑求解器的测试模块在模块级声明 `FAST = False`，其余声明 `FAST = True`；`run_tests.py --fast` 按此筛选，不硬编码模块清单

---

## 12. 已知风险与开放问题

### 12.1 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| **V3 模板扩展项业务不填** | 劳动力上限（Stage E）、客户评级保供永久 `degraded` | 契约层级 T2 保证不阻塞求解；绑定报告让缺失可见；G8 已把「向业务申请」变成「我们先生成空模板」，降低沟通成本 |
| **字典序 4 档 × 90s 太慢** | 端到端 6 分钟，开发迭代痛苦 | `layer_time_budget` 递减分配；`--fast` 测试用缩短预算的独立 policy；bound freezing 收窄下层空间 |
| **规模压测缺失导致 horizon 拉长后爆炸** | 30 天 + 上百任务时变量数增长快于线性 | Stage B 验收门包含规模压测任务，明确记录 10/20/30 天 × 20/50/100 任务的求解时间矩阵 |
| **Stage C5 迁移改变解** | 迁移后结果漂移，无法归因 | G26 迁一条验一条 + 确定性门保护 |
| **`avg` reduction 被实现成浮点** | CP-SAT 拒绝或静默截断 | §5.3 已写明整数化形式；契约测试覆盖 |
| **assumption literal 漏加** | infeasible 诊断退化 | §6.3 实现前提 + Stage B 验收门检查每条硬约束都有对应 literal |

### 12.2 自审时裁决的三个技术问题

这三项在初稿里是开放问题。设计文档不该把技术问题留给实施者现场判断（那正是 §0.1 问题 3 的成因），故在此裁决：

- **G27 日历 `auto` 推荐阈值进 policy，不进代码。** `≤4/7 / ≤5/7 / ≤6/7` 三档来自旧系统 WL 经验；SY38 是 12h×2班，同阈值是否成立未验证。写进代码就成了 WL 硬编码，违反 G6。落地形式：`policy.calendar_auto_thresholds = {"4on3off": 0.5714, "5on2off": 0.7143, "6on1off": 0.8571}`，超出则 `continuous`。
- **G28 逐日班次日历优先于 `calendar_pattern` 推导。** V3 新增的「工作日历」sheet（§3.5 第 4 项）填了就以它为准；`calendar_pattern` 只作为该 sheet 未填时的推导来源。两者都有且冲突 → `warn`，以逐日日历为准。理由：更具体的数据优先于更泛化的推导，且业务能表达"某天临时停一班"。
- **G29 `material_arrival` 的 ETA 按整班次二值处理。** `eta_shift` 折算为绝对槽位号，该槽位**及之后**物料可用；`eta_time`（可能为空）**只用于报告展示，不进模型**。不做班内部分可用——那是细粒度时间片，已被 G11 否决。

### 12.3 仍未裁决（业务问题，附裁决时点）

这两项需要业务输入，不能由工程裁决。每项都标明了**必须在哪个任务之前解决**，以及未解决时的降级行为：

1. **`initial_stock` 的 `COMP` / `RAW` 类型是否参与齐套。** 模板 `initial_stock` 有「物料类型」列，MVP 齐套约束（§6.3 第 7 条）只处理 FG/SF 两阶段。COMP/RAW 库存如何进入 `material_ready_eta`（Stage E3）未定。
   - **裁决时点**：Stage E3 开工前
   - **未解决的降级**：E3 只按 `material_arrival` 的 ETA 建模，`initial_stock` 中 COMP/RAW 行读入但不参与约束，绑定报告标 `degraded`
2. **导出 Excel 是否与旧 `Production plan-Done` sheet 格式兼容。** 旧格式有冻结窗、分班产量、工时汇总、利用率预警。G5 把 Web 后置，但导出仍在范围内（Stage D3）。
   - **裁决时点**：Stage D3 开工前
   - **未解决的降级**：按 §6.7 PlanRow 的字段顺序平铺成单 sheet，不做冻结窗与条件格式；业务反馈后再迭代

---

## 附录 A：与旧系统的能力对照

| 旧能力 | 新架构处置 |
|---|---|
| 19 算子内核编译器（`kernel/compiler.py` 816 行） | **丢弃**，由 B 档通用编译函数取代（§5.3） |
| 32 条规则目录（`config/rule_catalog/aps_32_rules.json`） | **归档保留**，作为业务分类法与 scope 词汇参考 |
| 4 份种子规则集 JSON | **归档保留**，作为 policy 文件初始值参考 |
| 规则集 SQLite 版本库 + Excel 双向转换 + 语义 Diff | **丢弃**（§2 非目标） |
| 独立二次审计引擎（`audit/engine.py` 743 行） | **重建**，九项 + 变异矩阵（§7.1）。设计思路继承，代码不继承 |
| 手工排产 Excel 上传审计 | **丢弃**（§2 非目标，随 Web 后置） |
| 双工厂拓扑（`shift_manager.py` 518 行） | **重建为 `aps/calendar/`**，WL 优先，SY38 数据不建（G6） |
| 15 表 Schema + 别名 + v2/legacy 双版式（`master_schema.py` 618 行 + `excel_loader.py` 702 行） | **重建为登记表驱动的适配器**。legacy 25 表版式**丢弃**（G7：数据只从 Excel 来，且只支持 V2/V3） |
| 30 天前瞻平准合批 · 冻结期围栏 · 库存策略 · C4 提前期 · 拆批连续 · 共产品同步 | **不做**（§2 非目标） |
| Vue + Tailwind 前端（`src/static/`） | **删除**（G5） |
| FastAPI 接入层（`server.py` 3,878 行） | **删除**（G5），Web 后置阶段重新设计 |
| 42 个测试套件 | **删除**，按 §7.4 四类重建 |
| 黄金基线不变量 | **复用 2 条**（G19） |
