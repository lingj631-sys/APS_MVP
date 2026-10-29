# APS 扩展主数据（Mock / Schema 示例）

> 目的：为 2026-10-28 版 32 条新规则（HC/SC/BS）补齐主数据字段与表格。
> **当前状态**：`MOQ` 已接入 `ProductParam/ProductionTask`；本目录其余表格是**待接线的扩展主数据**，当前求解器不会读取，因此不影响现有排产；后续按模板/核心模型改造逐步接线。
> **数据性质**：除 `Parameters-FG/SFN` 的 MOQ 外，本目录全部为**模拟数据**，用于结构、校验和算法联调，不代表真实业务值。

## 0. 与现有 `data/` 的关系

- `data/Parameters-FG.csv`、`data/Parameters-SFN.csv`：已追加 `MOQ (Minimum Order Qty)` 列；
- `dataSource.xlsx`：同步追加 `MOQ` 列，并新增本目录对应的 sheet；
- 本目录：扩展主数据 CSV，便于版本管理、审阅和后续加载；
- `data/master/labor/`：人力三表的 mock 版本，避免直接覆盖 `data/templates/labor` 空模板。

## 1. MOQ 与 MPQ 的口径

| 字段 | 含义 | 当前用途 |
|---|---|---|
| `Mini Production QTY(MPQ)` | **Minimum Package Quantity**，包装/周转箱/托盘的最小包装模数 | 现有排产按 MPQ 做成品整批/半成品总量门槛 |
| `MOQ (Minimum Order Qty)` | **Minimum Order Quantity**，一次开机/下单的最小数量门槛 | 已加载到 `ProductParam.moq` / `ProductionTask.moq`，**尚未参与约束**；待 HC-11/SC-04 改造后启用 |

> 重要：当前 `min_batch` 仍按 MPQ 执行；MOQ 只是主数据准备，不能在未改算法前宣称“MOQ 已生效”。

## 2. 表格清单

| 文件 / Excel sheet | 用途 | 对应规则 | 当前是否读入求解器 |
|---|---|---|---|
| `Routing-Machines.csv` | 工艺路线、候选/认证机台、CT/TRP、换模时间 | HC-05、SC-03、BS-04 | 否 |
| `Setup-Matrix.csv` | from→to 模具/家族换模时间、技术员需求、清洗要求 | HC-02、HC-04、SC-05 | 否 |
| `Operation-LeadTime.csv` | 工序 Setup/Run/Queue/Inspection/Transfer Batch/Container | HC-09、HC-10、BS-04 | 否 |
| `Customer-Orders.csv` | 订单类型、预测置信度、交期/发货、追产/试制标记 | SC-01、HC-13、BS-05 | 否 |
| `Inventory-Policy.csv` | 平时/月末库存目标、最大库存、安全库存 | SC-06、SC-07、HC-14 | 否 |
| `Trial-Windows.csv` | 首批/试制受控窗口 | HC-13 | 否 |
| `Baseline-Schedule.csv` | 已下发基线、冻结/锁定状态 | HC-12、SC-09 | 否 |
| `Calendar-Patterns.csv` | 4/3、5/2、6/1、7/2 周负荷区间 | BS-01 | 否 |
| `Campaign-Families.csv` | 产品族/模具族/颜色/规格/Campaign 分组 | HC-16、SC-05、BS-05 | 否 |
| `Setup-Technicians.csv` | 换模技术员班次/技能/可用时段 | HC-02 | 否 |
| `labor/Labor-Availability.csv` | 班次可用人数 | HC-15、SC-10 | 否（模板格式） |
| `labor/Line-Staffing.csv` | 产线/料号定额人数 | HC-15 | 否（模板格式） |
| `labor/Crew-Pool.csv` | 共享班组池 | HC-15 | 否（模板格式） |

## 3. 字段说明

### 3.1 Routing-Machines.csv

```text
Site, Part No., Stage, Operation Seq, Operation Name,
Primary Line, Candidate Line, Machine ID, Certified,
CT Seconds, TRP, Setup Min, Priority, Effective From, Effective To
```

- `Candidate Line`：该工序可用的候选产线/机台；
- `Priority`：1=首选，2=备选；
- 同一 Part/Operation 可以有多行，表示多机台/多线可选；
- 当前数据每行优先给一个主候选；SY38 成品额外给一个备选，用于验证 routing 的候选逻辑。

### 3.2 Setup-Matrix.csv

```text
Site, Line, From Mold, From Family, To Mold, To Family,
Setup Minutes, Technicians Required, Cleaning Required, Effective From
```

- 对角线（同模具）`Setup Minutes=0`；
- 不同家族/颜色需要清洗时 `Cleaning Required=Y`；
- `Technicians Required` 用于 HC-02 的换模技术员容量。

### 3.3 Operation-LeadTime.csv

```text
Site, Part No., Stage, Operation Seq, Operation Name,
Setup Min, Run CT Seconds, Queue Min, Inspection Min,
Transfer Batch Qty, Container, Move Min
```

- `Transfer Batch Qty`：首箱/首托可流转数量，用于 HC-10；
- `Queue Min` / `Inspection Min` / `Move Min`：工序间等待/检验/搬运；
- 当前是单工序示例；真实多工序可继续加行（Operation Seq 递增）。

### 3.4 Customer-Orders.csv

```text
Site, Order ID, Part No., Customer, Order Type, Forecast Class,
Due Date, Due Shift, Demand Qty, Priority, Ship Date,
Catchup Flag, Trial Flag, Allowed Window Start, Allowed Window End
```

- `Order Type`：正式订单 / 预测单；
- `Forecast Class`：CONFIRMED / FORECAST；
- `Catchup Flag`：是否需要 D+1 欠产追产；
- `Trial Flag` + `Allowed Window`：HC-13 受控窗口；
- 这是一张**补充属性表**，不替代现有宽表 `Customer-Demand.csv`；后续需要按订单号与宽表/订单主表关联。

### 3.5 Inventory-Policy.csv

```text
Site, Part No., Period Type, From Day, To Day,
Target Days, Max Days, Safety Stock, Max Stock, Effective From
```

- `Period Type`：`NORMAL` / `MONTH_END`；
- 用 `From Day/To Day` 表达月末 3~5 天收敛；
- 用于 SC-06/SC-07 的分时段库存目标。

### 3.6 Baseline-Schedule.csv

```text
Site, Task ID, Line, Stage, Part No.,
Shift Date, Shift Name, Qty, Release Status, Locked
```

- `Locked=LOCKED` 表示冻结期基线；
- 真实使用时需要由“已下发计划/上次求解结果”生成，而不是手工维护。

### 3.7 Calendar-Patterns.csv

```text
Site, Pattern ID, Week Start, Workdays, Shifts Per Day, Shift Hours,
Shift Start Times, Rho Min, Rho Max, Note
```

- `Rho` 为周负荷率区间；
- WL 支持 4/3、5/2、6/1、7/2；SY38 支持 5/2、6/1、7/2；
- 当前只做数据准备，调度器仍用固定 `CalendarMode`。

### 3.8 Campaign-Families.csv

```text
Site, Part No., Mold ID, Product Family, Color, Spec,
Campaign Group, Sequence Priority, Note
```

- 用于同模/同族合批、SDST 矩阵、HC-16 防穿插；
- 当前 `Color`/`Spec` 为 mock/空值，真实业务需要补齐。

## 4. 模拟数据的使用边界

1. 这些表**不会自动驱动当前求解器**；当前排产仍使用 `Parameters-*`、BOM、需求、库存、模具/PM/到货等既有表。
2. 新增 `MOQ` 已进入 `ProductParam`/`ProductionTask`，但 `min_batch` 仍按 MPQ；在 HC-11 改造前不会改变排产结果。
3. 真实上线前必须替换 mock 数据，并做引用完整性校验：
   - 每个 Part 在 `Routing-Machines` 至少有一个认证候选；
   - `Setup-Matrix` 覆盖同线所有模具对；
   - `Operation-LeadTime` 覆盖所有需要倒排的工序；
   - `Customer-Orders` 的订单号与需求/交期主表可关联；
   - `Inventory-Policy` 的日期窗口不重叠；
   - `Baseline-Schedule` 的 Task ID 与当前计划一致。

## 5. 需要业务确认的口径

1. MOQ 是订单门槛还是开机门槛？是否允许尾数豁免？
2. 换模时间是否已含在 TRP？当前 D7 假设“含”，HC-04 要求“硬扣”，两者冲突。
3. Routing 是产线级还是机台级？候选机台是否需要吨位/认证字段？
4. `Customer-Demand.csv`（宽表）与 `Customer-Orders.csv`（订单属性表）的主键如何关联？
5. 月末窗口是自然月最后 3 天还是最后 5 天？按工厂是否不同？
6. 6/1、7/2 日历模式是否适用于所有产线，还是仅 WL/SY38 特定线？
