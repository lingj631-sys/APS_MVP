# 新规则清单 vs APS 规则内核 / 规则集编辑器 能力对账分析

> **分析对象**：`Light China APS Rules.xlsx` 第一个 sheet「Rules and Constraints - Formula」  
> **代码基线**：`feat/shaojing-dev` @ `5727e4c`（18/19 算子已编译，`routing` 未实现）  
> **分析日期**：2026-10-28  
> **本文只做对账，不改代码。**

---

## 0. 结论先行

### 0.1 规则规模

Excel 实际是 **32 条规则**，不是 39 条：

| 分类 | 数量 | 说明 |
|---|---:|---|
| **HC 硬约束** | 16 | HC-01 ~ HC-16，E 列基本为「硬约束 / 刚性 100%」；HC-11 带尾数豁免 |
| **SC 软约束** | 10 | SC-01 ~ SC-10，E 列为权重，含复合/分时段权重 |
| **BS 业务策略** | 6 | BS-01 ~ BS-06，属于前置/后置/迭代仲裁机制，不是普通约束 |
| **合计** | **32** | HC 16 + SC 10 + BS 6 |

G 列只是旧规则对照，**不是 1:1 映射**；不少新规则（HC-02/04/05/10/16、SC-03/09/10、多条 BS）在原表中没有直接对应。旧 27+9 条不能直接当作新 32 条的运行时实现。

### 0.2 最重要的判断

**这 32 条规则不能直接塞进当前 19 个模板 / 规则集编辑器里完成。**

- **严格意义上的「只改配置、不改代码」：只能做到 1 条接近完整**：HC-15 班次在岗总人数（前提是补人力数据）；
- **可以用现有算子做“班次级近似”的：约 8~10 条**（HC-01/03/06/08/12/13、SC-02/04/05 等）；
- **必须新增算子或改造核心模型的：20 条以上**；
- **必须走前置/后处理，不能作为 CP-SAT 约束的：HC-07、BS-01/02/03/05 等**。

### 0.3 根因不是“模板不够多”，而是模型粒度不同

当前模型是：

```text
x[task, shift] ∈ 整数          # 一个任务在一个班次做多少件
```

新规则 H 列大量使用：

```text
S_i, C_i                        # 任务开始/完工时刻
SetupStatus(m, t)               # 某机台某时刻是否在换模
Assign(i, m)                    # 任务分配到哪台机
ChangeoverCost(Family_i, Family_j)
Inv_t, Demand_{t+k}             # 逐时点库存/需求
```

也就是 **连续时间 + 机台分配 + 工序顺序 + 换模矩阵 + 逐时点库存**，而不是“班次桶”。  
这不是加几个 JSON 参数能解决的，必须先决定：**继续班次级近似，还是升级到时间/序列级模型。**

---

## 1. 当前框架到底能做什么

### 1.1 内核能力

当前固定 19 个模板，18 个已编译：

```text
capacity_utilization  min_batch  contiguity  changeover
material_balance     material_ready  due_date  sequence
calendar             tooling_exclusivity  pm_block  concurrency
inventory_bound      customer_priority  time_window  co_product
routing（未实现）     freeze       labor_capacity
```

每个模板的公共能力是：

- 作用域：`site / line / stage / part`；
- 刚性：`hard / soft`；
- 软约束权重、组、组内优先级、继承 `replace / add`；
- 固定参数 Schema（不能写自定义公式）；
- 可多实例、按作用域叠加/覆盖。

### 1.2 当前模型的关键边界

| 边界 | 当前事实 | 对新规则的影响 |
|---|---|---|
| 时间粒度 | **班次桶**，无班内开始/结束时刻 | HC-01/02/04/09/10/16、SC-05/08/09 等无法精确表达 |
| 机台分配 | 任务在数据加载时已绑定 `line`，**没有 Assign(i,m)** | HC-05、SC-03 无法实现 |
| 工序 | 只有 FG/SF 两个阶段，**没有 operation 级工序路线** | HC-09/10、BS-04 无法做工序倒排/转移批量 |
| 换型 | `changeover` 只按 run_start 计罚；`setup_minutes` 明确“未计入容量” | HC-04 的硬扣工时、SC-05 的 SDST 矩阵都没有 |
| MOQ | `min_batch` 是 **MPQ/件** 的班次下限；不是 MOQ 总量/首启门槛 | HC-11 只能近似 |
| 库存 | `inventory_bound` 只做“提前 max_days 不排”的近似；`min_days/safety_stock` 未实现 | SC-06/07 的逐时点库存/月末权重无法表达 |
| 冻结 | `freeze` 是“冻结窗内禁止排产”；REG-05 的基线等式是调度器另一条路径 | HC-12 不能只靠规则集配置 |
| 日历 | `calendar` 只有 `FOUR_THREE / FIVE_TWO / SEVEN_ZERO`；`AUTO` 不生效 | BS-01 的 6/1、7/2、周负荷自适应没有 |
| 策略 | 规则集只有 hard/soft rule，没有 strategy 对象 | BS-01~06 无法在编辑器里表达 |
| 权重 | 组内相对归一化；没有分时段/复合权重 | SC-01/06/07 的 90/99、40/80、60/85 不能直接配置 |

### 1.3 规则集编辑器的真实能力

它能做：

- 选 19 个模板之一；
- 配作用域（站点/线/阶段/料号）；
- 配 hard/soft、权重、组、优先级、继承；
- 配模板固定参数；
- Excel 导入、版本、激活、回滚；
- 诊断/敏感度。

它不能做：

- 新增模板或自定义约束表达式；
- 新增决策变量（如 `Assign`、`SetupStatus`、`S_i/C_i`）；
- 按日期/小时/订单类型/客户/模具族/颜色配置作用域；
- 写复合权重、分时段权重、二次目标；
- 表达 BS 类前置/后置策略；
- 把 H 列公式直接变成可执行规则。

---

## 2. 逐条对账

图例：

- 🟢 可配置（近似，需数据）
- 🟡 可配置一部分 / 班次级近似
- 🟠 需要扩参数或扩算子
- 🔴 需要核心模型改造 / 新算子
- ⚙️ 前置或后处理，不是 CP-SAT 约束
- ⛔ 当前不可

### 2.1 HC 硬约束（16 条）

| ID | 新规则 | 当前可复用 | 判断 | 核心差异 | 需要补充 |
|---|---|---|---|---|---|
| HC-01 | 模具/工装跨设备独占互斥 | `tooling_exclusivity` | 🟡 | 现在按“同班次 active 数”互斥，不能按 `[S_i,C_i)` 精确重叠；装卸模时间未建模 | 模具-设备-料号关系（已有部分）；换模/卸模时间 |
| HC-02 | 换模师傅/调机工物理容量独占 | `labor_capacity` + setup 技术员参数 | 🟡/🔴 | C3-lite 已能做班次 run start 数限制；仍不能按 setup 时间区间/技能匹配 | 技术员班次/技能矩阵/换模任务时长 |
| HC-03 | 设备 PM 避让 | `pm_block` | 🟡 | 只能按 PM 班次清零，不能按小时切分/顺延到 PM 后 | PM 窗口（已有 CSV） |
| HC-04 | 换模/换线占机工时刚性扣减 | `capacity_utilization.setup_minutes_per_start` | 🟠/🟡 | C1-b-lite 已按 run start 扣固定 setup；仍非 SDST/sequence-dependent | 从/到模具族的换模时间、机台 |
| HC-05 | 工艺能力匹配与合格机台清单 | `routing`（未实现） | 🔴 | 当前任务线固定，没有 `Assign(i,m)`；domain/routing 只是骨架 | 工艺路线、候选机台、吨位/认证、CT |
| HC-06 | 动态 BOM 齐套开工 | `material_ready` + `material_balance` | 🟡 | 只能按到料班次/前道累计；没有 QC 小时、父件/子件多级时点 | 到货/QC/前道完工时点（部分已有） |
| HC-07 | 工艺报废率投料逆向补偿 | `build_requirement_plan(apply_scrap=True)` | ⚙️/🟡 | B2 已实现 opt-in 投料放大 `gross/(1-scrap)`；默认关闭，未接 UI/规则 | SF 报废率（已有 3%）、投料取整规则 |
| HC-08 | 左右件 L/R 共模同步产出 | `co_product` | 🟡 | 现在是班次产量比例相等，不是同一冲次/同一时刻；配对关系需明确 | L/R 配对、型腔比例、同模关系 |
| HC-09 | 半成品装配安全前置期 | `material_balance.buffer_shifts` | 🟠 | 只有班次 buffer，没有 Setup + Operation + Queue + 检验的工序倒排 | 工序/CT/换模/静置/检验时间 |
| HC-10 | 工序重叠与转移批量 | `material_balance` 近似 | 🔴 | 当前是“前道 s-1 累计可供后道 s”，没有首箱/转移批/重叠开工 | 转移批量、容器、Queue、质检 |
| HC-11 | MOQ 与跨班连续作业 | `min_batch` | 🟠/🟢 | B1 已加 `basis=order`/`quantity_source=MOQ`/`tail_exempt`；尚未写入 active seed | 真实 MOQ、尾数/收尾判定 |
| HC-12 | 近端时间围栏冻结 | `freeze` + 调度器 baseline | 🟡 | `freeze` 是“禁排”，新规则是“锁定基线”；基线来源/手工特批未建模 | 已下发基线、冻结窗、特批单 |
| HC-13 | 特殊首批/试产受控窗口 | `time_window` | 🟡/🟢 | B5 已支持 `allowed_dates` + 班次名；仍不能到小时/自动识别试制标记 | 试制/首批标记、小时级窗口 |
| HC-14 | 装配防断线绝对优先（超库豁免） | `material_balance` + `inventory_bound` | 🟠 | 没有“断线风险”条件、没有超库豁免；库存只有 max_days 近似 | 安全库存/最大库存/日需求/断线定义 |
| HC-15 | 班次在岗总人数红线 | `labor_capacity` | 🟢/🟡 | 模板支持 `available_headcount`/共享班组；但人力 CSV 未加载，且不含换模技术员 | Line-Staffing、Labor-Availability、Crew-Pool |
| HC-16 | 同模连产防穿插硬约束 | `contiguity` / `concurrency` | 🔴 | 只能限制块数/并行型号数，不能禁止第三方产品插入，也没有班内顺序 | 同模批次、Campaign、顺序 |

### 2.2 SC 软约束（10 条）

| ID | 新规则 | 当前可复用 | 判断 | 核心差异 | 需要补充 |
|---|---|---|---|---|---|
| SC-01 | D+1 备货主时钟 + 当日早班追产 | `due_date` / `customer_priority` | 🔴 | 没有“今日 24:00 库存满足明日发货”和“今日早班追产”两条时钟 | 每日发货计划、D+1 需求、欠产/追产标记 |
| SC-02 | 班次产能利用率 95%~100% 软目标 | `capacity_utilization` | 🟢/🟠 | B3 已实现 soft 双边计罚；权重/组仍需按新规则标定 | 利用率权重标定 |
| SC-03 | 首选高效主产线 | `routing`（未实现） | 🔴 | 没有候选线，无法比较 CT/TRP 并罚备线 | 各线 CT/TRP/产能/认证 |
| SC-04 | 包装规格 MPQ 整倍数软约束 | `min_batch` | 🟠 | `min_batch` 是硬约束；没有“最多 1 个散箱”的尾批豁免 | MPQ、尾批规则 |
| SC-05 | SDST 换模矩阵 + 跨班继承 | `changeover` | 🟠/🔴 | 只有 run_start 计罚；`setup_minutes`/`count_by`/`cross_shift_inherit` 未真正实现，无 from→to 矩阵 | 模具族/颜色/规格换模矩阵、跨班继承规则 |
| SC-06 | 半成品分时段库存 | `inventory_bound` | 🟠 | 只有 `max_days` 近似；`min_days/safety_stock` 未实现，无平时/月末权重 | 分时段目标/最大库存、月末窗口、日需求 |
| SC-07 | 成品分时段库存/月末压降 | `inventory_bound` | 🟠 | 同上；无 5 天/2 天动态窗口 | 成品库存策略、月末窗口、发货需求 |
| SC-08 | 顺位前置填补防跳单 | `sequence` | 🟠 | 当前是硬性 due 先后；没有 order index 二次罚分/软前拉 | 自然交期顺序、允许前拉窗口 |
| SC-09 | 计划平稳/Jitter 抑制 | `freeze` | 🔴 | 没有上一版基线偏差目标；freeze 只处理冻结窗 | 基线计划、解冻区、机台/日期权重 |
| SC-10 | 三班人力平滑 | `labor_capacity` | 🔴 | 只有人数上限，没有班次负荷方差目标 | 班次人力/负荷数据、权重 |

### 2.3 BS 业务策略（6 条）

| ID | 新规则 | 当前可复用 | 判断 | 核心差异 | 需要补充 |
|---|---|---|---|---|---|
| BS-01 | 三阶智能工作日历自适应 | `calendar` + 日历推荐 | ⚙️/🟠 | 只有 4/3、5/2、7/3；没有 6/1、7/2、按周 ρ 自动升降阶 | 周负荷率、日历模式、产能参数 |
| BS-02 | 跨期平准/逆向削峰填谷 | `apply_demand_lookahead`（server 前置） | ⚙️ | 是前置拉单，不是求解器内的跨期平准；不在规则集 | 30 天预测、可拉订单、产能窗口 |
| BS-03 | 极限超载不可行/交期平移建议 | `prechecker` | ⚙️ | 只有预检建议，未和求解结果形成闭环仲裁 | 产能上限、可延期订单、缓冲规则 |
| BS-04 | 逆向拉动排产 | 无 | 🔴 | 当前是班次正向桶模型，没有工序级反向倒排 | 工序/CT/LeadTime/Queue/BOM |
| BS-05 | 同模合批 Campaign Batching | `apply_demand_lookahead` 近似 | ⚙️/🟠 | 有 30 天拉单，但没有 SuperOrder、订单类型、持有成本豁免 | 订单类型/预测置信度、同模族、视距窗口 |
| BS-06 | Slack Fallback 动态仲裁 | 无 | 🔴 | 没有“先合批、超交期再切分”的迭代仲裁 | 交期裕度、MOQ、切分规则、优先级 |

---

### 2.4 工厂差异与规则版本

你提到“规则普遍适用，但不同工厂仍可不同”。当前实现已经有基础：

- `RuleSet.site` + `data/rule_sets.sqlite3` 的 per-site active 版本；
- 规则 `Scope.site` 可限制到工厂/车间/产线；
- `config/rule_sets/WL_Phase1.json`、`SY38_Phase1.json` 已是两套独立 active 基线。

但还缺一层“**全局规则目录 + 工厂覆盖**”的正式语义：

| 方案 | 说明 | 建议 |
|---|---|---|
| A. 每工厂复制一套 32 条 | 简单，当前编辑器已支持 per-site 版本 | 短期可用 |
| B. 全局模板 + site override | 一条规则可被工厂启用/禁用/改权重/改参数；需要新增“继承/覆盖”层 | 中期目标 |

建议先按 A 落地（不阻塞），同时把 32 条做成**全局规则目录**，工厂 active 版本引用目录；后续再加 B 的 `site override`，避免每个工厂复制 32 条后逐渐漂移。

---

### 2.5 关键公式摘录（为什么需要换模型）

只摘录最能说明模型粒度差异的公式，完整公式见 Excel H 列。

```text
HC-04  S_j ≥ C_i + SetupTime(Family_i → Family_j, m)
HC-05  Assign(i,m)=1 ⇒ m ∈ CertifiedMachines(Product_i)
HC-07  InputQty_i = ceil(DemandQty_i / (1 - ScrapRate_i))
HC-08  OutputQty(L) = OutputQty(R) = Strokes × CavityPerStroke
HC-09  S_SFIN = S_FING + TotalTime_FING - LeadOffset - TotalProcess_SFIN
HC-10  S_FING ≥ S_SFIN + Setup_SFIN + (Batch_Transfer × CT_SFIN) + QueueTime
HC-11  Q_i ≥ MOQ_m；T_rem < Q_i×CT 时强制跨班；尾数/急单允许 Q_i<MOQ
HC-16  i,j∈B_mold，S_j>S_i ⇒ 任何 k∉B_mold 不得插入 [S_i,C_j)
SC-05  min 0.80 Σ ChangeoverCost(Family_i,Family_j) × X_{i,j,m}
SC-06  min 0.40 Σ_{t∉End}(Inv_t-MaxStock)^+ + 0.80 Σ_{t∈End}(Inv_t-TargetDays×DailyDem)^+
SC-07  min 0.60 Σ_{t∉End}(Inv_t-Σ_{k=1}^5 Dem_{t+k})^+ + 0.85 Σ_{t∈End}(Inv_t-2×AvgDem)^+
BS-04  C_FG,i ≤ Due_i - Buffer；C_SFIN,j ≤ S_FG,i - TransferTime
BS-06  min Slack_j≥0；SuperOrder 切分为 Q_keep + Q_split，Q_keep ≥ MOQ
```

这些公式共同要求：

```text
连续/可排序时间  +  机台分配  +  工序路线  +  换模矩阵  +  逐时点库存
```

而当前模型只有：

```text
x[task, shift] 的班次产量
```

---

## 3. 哪些现在就能配置？

### 3.1 严格“配置即可”的只有 HC-15 一条（且要补数据）

```json
{
  "id": "HC15_HEADCOUNT",
  "template": "labor_capacity",
  "scope": {"site": "WL"},
  "nature": "hard",
  "weight": 1,
  "group": "G2_CAPACITY",
  "params": {
    "available_headcount": 10,
    "crew_source": "part"
  }
}
```

前提：

- `data/Line-Staffing.csv`、`Labor-Availability.csv`、`Crew-Pool.csv` 要有真实数据；
- 当前模板按“产线/班次”聚合，不是按每台设备逐一分配；
- 不含 HC-02 的换模技术员并发。

### 3.2 可做“班次级近似”的规则

| 规则 | 近似方式 | 近似后仍缺什么 |
|---|---|---|
| HC-01 | `tooling_exclusivity` + `available_qty` | 不能按小时判断重叠，只能同班次互斥 |
| HC-03 | `pm_block` + PM CSV | PM 只能整班清零，不能 PM 中间切分 |
| HC-06 | `material_ready` + `material_balance` | 只有班次级 ETA/前道累计，没有 QC 小时 |
| HC-08 | `co_product` + `ratio` | 班次总量比例，不是同一冲次 |
| HC-12 | `freeze` + `fence_shifts` | 只能禁排，不能锁基线；特批未建模 |
| HC-13 | `time_window` + `allowed_shifts` | 只能早/中/晚，不能日期+小时 |
| SC-02 | `capacity_utilization` soft/hard + 95/100 | 硬档强制 hard，软目标不生效 |
| SC-04 | `min_batch` + `unit=MPQ` | 是硬约束，不是软包装偏好；无尾批豁免 |
| SC-05 | `changeover` + weight | 只有开工次数，没有 SDST/换模时长 |

### 3.3 不能靠配置，必须改代码的典型

- **HC-02**：没有换模状态变量；
- **HC-04**：setup time 没进容量；
- **HC-05 / SC-03**：`routing` 未实现；
- **HC-07**：scrap_rate 未使用；
- **HC-09 / HC-10**：没有工序级 LeadTime/Transfer Batch；
- **HC-11**：没有 MOQ/尾数豁免；
- **HC-16 / SC-05 / SC-08**：没有班内顺序和 SDST 矩阵；
- **SC-01 / SC-06 / SC-07 / SC-09 / SC-10**：没有 D+1 库存、分时段库存、Jitter、人力方差目标；
- **BS-01/02/03/04/05/06**：没有 strategy 层，且部分需要前置/后置/迭代求解。

---

## 4. 差距分层（按改造难度）

### G1 时间粒度：班次桶 vs 连续时间（最高优先级）

涉及：HC-01、HC-02、HC-04、HC-09、HC-10、HC-16、SC-05、SC-09、BS-04。

当前 `x[task, shift]` 只能回答“这个班做多少”，不能回答：

```text
这个任务几点开始、几点结束？
这个班内先做 A 还是先做 B？
换模从几点到几点？换模工是否被占用？
```

**结论**：只要新规则要求 `[S_i, C_i)`、`SetupStatus(m,t)`、`ChangeoverCost`，就必须升级时间模型或增加班内序列变量；否则只能做班次级近似，不能声称满足 H 列。

### G2 机台分配/工艺路线

涉及：HC-05、SC-03、BS-04。

当前任务在加载时就绑定产线，`routing` 只有模板声明，没有编译。需要：

```text
Assign(i, m) ∈ {0,1}
m ∈ CertifiedMachines(Product_i)
```

并引入 operation 级路线和候选机台。

### G3 换型/换模技术员/SDST

涉及：HC-02、HC-04、SC-05、HC-16。

需要：

- from→to 模具/家族/颜色/规格的换模时间矩阵；
- 换模开始/结束时间；
- 换模技术员班次和技能容量；
- `SetupStatus(m,t)` 资源占用；
- 与容量扣减、跨班继承、同模连产统一。

### G4 多级工序与转移批量

涉及：HC-09、HC-10、BS-04。

当前只有 FG/SF 两个阶段和“下一班可供”近似。需要 operation 级：

```text
工序路线 → Setup/Run/Queue/Inspection → Transfer Batch → 后道可开工
```

### G5 MOQ / MPQ / 尾数

涉及：HC-11、SC-04。

当前 `min_batch` 是班次 MPQ/件下限。新规则要：

- MOQ 是总量/首启门槛，不是班次下限；
- 尾数/收尾/急单豁免；
- MPQ 是包装模数，可作为软目标；
- 两者解耦。

### G6 逐时点库存 / 分时段权重

涉及：SC-01、SC-06、SC-07、HC-14。

当前 `inventory_bound` 只做“提前 max_days 不排”的近似，不能表达：

```text
Inv_t, Demand_{t+k}, 月末窗口, 90/99、40/80、60/85 复合权重
```

### G7 人力 / 换模技术员

涉及：HC-02、HC-15、SC-10。

`labor_capacity` 能做生产人数红线（HC-15），但不能做换模技术员并发（HC-02），也没有班次人力方差目标（SC-10）。

### G8 策略层（BS）

BS-01~06 不是普通约束，应该拆成：

- 前置：日历选择、需求拉取、Campaign 合批；
- 求解内：Pull Scheduling、Slack Fallback；
- 后置：超载告警、交期平移建议。

当前规则集编辑器没有 strategy 对象；把它们硬塞进 hard/soft rule 会导致语义错误。

---

## 5. 需要你补充的材料

### 5.1 主数据

| 材料 | 用途 | 当前状态 |
|---|---|---|
| 工艺路线 / 候选机台 / 认证机台清单 | HC-05、SC-03、BS-04 | 缺；`routing` 只有骨架 |
| 换模矩阵（from→to 模具/家族/颜色/规格 + 分钟） | HC-04、SC-05、HC-16 | 缺；现有 Tooling 只有单个换模时间 |
| 换模技术员班次/技能/人数 | HC-02 | 缺 |
| MOQ（按料号/机台）与尾数豁免规则 | HC-11、SC-04 | 缺；现有只有 MPQ |
| 工序级 CT/Setup/Queue/Inspection/Transfer Batch | HC-09、HC-10、BS-04 | 缺；现有只有单件 CT/TRP |
| 每日发货计划 / D+1 需求 / 欠产追产标记 | SC-01 | 缺；现有只有客户日需求 |
| 订单类型/预测置信度/可拉视距 | BS-02、BS-05 | 缺；现有 Customer Demand 只有日期+数量 |
| 分时段库存目标/最大库存/月末窗口 | SC-06、SC-07、HC-14 | 部分有 Target/Max，缺时间分段 |
| 试制/首批标记与允许日期+时段 | HC-13 | 缺 |
| 已下发基线/冻结窗/特批单 | HC-12、SC-09 | 缺 |
| 周负荷率/6-1/7-2 日历模式 | BS-01 | 缺 |
| 人力可用性/班组池/换模技术员 | HC-15、SC-10 | 模板有，数据未加载 |

### 5.2 规则口径确认

1. **时间粒度**：新规则是否要求分钟级 `S_i/C_i`，还是允许班次级近似？这是第一决策。
2. **HC-04 与现行 D7 冲突**：当前假设 TRP 已含换型、`DEFAULT_CHANGEOVER_MINUTES=0`；新规则要求硬扣换模时间。到底以哪个为准？
3. **MOQ vs MPQ**：MOQ 是总量门槛还是首启门槛？尾数豁免由谁判定？
4. **SC-01**：D+1 库存和客户交期是什么关系？早班追产是否允许突破其他 hard 规则？
5. **HC-14 vs SC-06/07**：超库豁免的触发阈值和优先级如何仲裁？
6. **BS-01**：日历是排产前选择，还是求解器内部变量？6/1、7/2 是否要新增模式？
7. **权重**：90%、0.9、30%~50%、复合 90/99 如何统一到当前 0~1000/组内归一化？跨组是否允许比较？
8. **多工厂**：32 条是所有工厂通用，还是 WL/SY38 分开？适用范围列的“全工序/FING/SFIN”是否足够？
9. **BS-06**：切分 SuperOrder 时，MOQ 和交期红线谁优先？
10. **数据责任**：换模矩阵、工艺路线、订单类型由谁维护、更新频率？

---

## 6. 建议的落地路径

### Phase A：规则目录与数据准备（不写求解代码）

- 把 32 条规则做成**独立规则目录**（不是直接塞进现有模板）：
  - 编号/分类/适用层级/硬软/权重/公式/数据依赖；
  - 标注 `configurable / partial / needs-operator / needs-model / pre-post`；
- 在规则编辑器里增加“能力状态/差距”标签，避免业务误以为可配置；
- 补齐 5.1 的主数据和 5.2 的口径；
- 对现有可近似规则（HC-01/03/06/08/12/13/15）做试点，但必须标注“班次级近似”。

### Phase B：扩展现有模板（中等改造）

- `capacity_utilization`：真正支持 soft 双边 95~100，尊重 rule nature；
- `min_batch`：增加 MOQ/尾数/首启；区分 MPQ 包装软目标；
- `inventory_bound`：按时间分段、max/target days、月末窗口；
- `material_balance`：增加 transfer batch / lead offset / queue；
- `freeze`：基线锁定而不是简单禁排；
- `time_window`：日期+班次+小时+试制标记；
- `changeover`：setup_minutes 真正进容量；跨班继承；
- `sequence`：从硬 due 顺序扩展为软 order index。

### Phase C：新增核心能力（大改造）

- **Routing/Assignment**：`routing` 真正编译，任务可分配候选机台；
- **Sequence/SDST**：班内顺序、换模矩阵、同模连产、防穿插；
- **Setup Technician**：换模资源容量；
- **Operation Lead Time / Transfer Batch**：工序级倒排与重叠；
- **Pull/Campaign/Slack**：BS-04/05/06 的迭代仲裁；
- **Adaptive Calendar**：BS-01 的周负荷/6-1/7-2。

### Phase D：策略层

- 把 BS-01~06 从“规则”提升为 `strategy`：
  - 前置：日历/需求拉取/合批；
  - 求解：pull/slack；
  - 后置：超载/交期平移；
- 规则集编辑器增加“策略”页，而不是继续塞进 Rules。

---

## 7. 一句话回答你的三个问题

### “能不能通过配置实现？”

**不能完整实现。**  
严格配置只能覆盖 HC-15 一条（且要补数据）；约 8~10 条可做班次级近似；其余需要扩算子、改模型或前置/后处理。

### “差异在哪里？”

差异不在“模板数量”，而在：

1. **班次桶 vs 连续时间/班内顺序**；
2. **固定产线 vs 候选机台分配**；
3. **换型计罚 vs 换模矩阵/技术员/硬扣工时**；
4. **MPQ 班次下限 vs MOQ 总量/尾数**；
5. **前道累计近似 vs 工序级 LeadTime/Transfer Batch**；
6. **max_days 近似 vs 逐时点/月末库存**；
7. **hard/soft rule vs BS 策略层**。

### “需要补充什么材料？”

优先级最高的是：

1. **工艺路线 + 候选/认证机台**；
2. **换模矩阵 + 换模技术员班次**；
3. **MOQ 与尾数豁免规则**；
4. **工序级 LeadTime / Queue / Transfer Batch**；
5. **每日发货/D+1 需求 / 订单类型/预测置信度**；
6. **分时段库存目标/月末窗口**；
7. **基线计划/冻结单/试制窗口**；
8. **周负荷/6-1/7-2 日历规则**；
9. **32 条规则的权重/冲突仲裁口径**。

---

## 8. 附：当前代码/文档基线提醒

本次分析基于 `5727e4c`。当前全量测试已修绿：

- 快速回归 58/58 绿（fast 52 + regressions 8，含新增模块单测另计）；
- 全量 97/97 绿（1386.0s）；
- WL BOM 校验修复、SY38 拓扑测试改用可行日历、golden 迁移预算调整；
- `src/services/manual_audit.py` 仍已抽取但未接线；
- `src/domain/` 仍是 Phase 2 骨架，尚未被 scheduler/server 使用。

因此，**新规则落地已有绿色基线**；Phase C 可以直接在该基线上扩展。

---

## 9. 本轮已补的主数据（2026-10-28）

已按新规则补齐第一批 mock 主数据，详见 [`data/master/README.md`](../data/master/README.md)：

| 已补内容 | 位置 | 当前状态 |
|---|---|---|
| MOQ 与 MPQ 分列 | `data/Parameters-FG.csv`、`Parameters-SFN.csv`、`dataSource.xlsx` | **MOQ 已加载到 `ProductParam.moq` / `ProductionTask.moq`，尚未参与约束** |
| Routing / 认证机台 | `data/master/Routing-Machines.csv` | mock，未接线 |
| Setup 矩阵 / 换模技术员 | `data/master/Setup-Matrix.csv`、`Setup-Technicians.csv` | mock，未接线 |
| 工序 LeadTime / Transfer Batch | `data/master/Operation-LeadTime.csv` | mock，未接线 |
| 订单类型 / 预测置信度 / 追产 / 试制 | `data/master/Customer-Orders.csv`、`Trial-Windows.csv` | mock，未接线 |
| 分时段库存 / 月末窗口 | `data/master/Inventory-Policy.csv` | mock，未接线 |
| 基线 / 冻结 | `data/master/Baseline-Schedule.csv` | mock，未接线 |
| 日历模式 / Campaign 分组 | `data/master/Calendar-Patterns.csv`、`Campaign-Families.csv` | mock，未接线 |
| 人力三表 | `data/master/labor/` | mock，未接线 |

注意：`min_batch` 仍按 MPQ 执行；MOQ 只是主数据准备。在这些表接线之前，现有求解器行为不变。
