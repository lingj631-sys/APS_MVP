# APS 智能排产系统（MVP）诊断与架构差距评估报告

> **评估对象**：`D:\Codes\APS_MVP`（HEAD `14ff727`，v0.7.1 之后的工作区）
> **评估日期**：2026-10-25
> **评估范围**：`src/` 全部 Python 模块（7,758 行，另加测试 197 行）、`data/` 主数据、`Specification/` 8 份文档、前端 `src/static/index.html`（5,217 行）
> **评估方式**：静态代码审读 + 数据探针 + 真实求解运行 + 两次对照实验（确定性、删单漏检）
> **代码改动**：**无**。仅新增本报告文件；为取证运行过 CLI 流程，其回写的 `dataSource.xlsx` 已用 `git checkout` 还原至 HEAD（工作区当前 `git status` 干净）。

---

## 0. 一页结论

### 0.1 五条最重要的判断

1. **方向是对的，模型语义是漏的。** 用"班次槽位 `x[t,s]` + 两阶段齐套不等式"来离散化这个业务，是 MVP 阶段正确的选择（比连续时间 MIP 更贴合班次制、比启发式更可解释）。但当前有 **6 处 P0 级建模缺陷 + 1 处求解确定性缺陷**，会让模型输出"看起来 PASS、现场不可执行"的计划。
2. **95% 利用率这个核心 KPI 目前不可证伪。** 它只在"已开工的班次"内计算。实测：日历口径负荷 74.5%，系统报"平均利用率 97.1%、低于 95% 班次 0 个"。这意味着"跳班"可以无限换取漂亮的 95%。
3. **二次独立验证不独立，且存在可复现的漏检。** 我实测删除一整张成品工单（连同 2 张半成品工单）后，校验器仍给出 **准时交付 7/7 = 100%、需求守恒 100%、MPQ 合规 100%**。只有产能利用率一项因为空出的负荷而报警。
4. **你最看重的"核心算法固定 + 工厂自定义规则"，目前恰好是做反了**：算法是写死的，规则也是写死的。规则权重（80%/25%/35%/60%/90%）**只用于前端展示，求解器从不读取**；"规则→求解器开关"是 `server.py` 里一段硬编码 `if` 链，且大量开关背后没有实现。
5. **扩展性不是"多写几个 if"能解决的，是数据模型缺实体。** 当前"料号→产线"是一对一字符串，"BOM"只有两级，没有"工厂/车间/路由/原料"实体，也没有 MRP 的库存与采购层。第三个工厂接进来必须改代码（`rules.py`、`shift_manager.py`、`validator.py`、`server.py`、前端五处）。

### 0.2 与你的五条要求的符合度

| # | 你的要求 | 现状判断 | 评分 | 结论 |
|---|---|---|---|---|
| 1 | CP-SAT 核心算法与求解器使用合理 | 选型合理；6 处 P0 建模缺陷 + 1 处确定性缺陷，求解策略有随机性 | **6 / 10** | 部分满足 |
| 2 | 二次独立验证无缺陷无漏洞 | 有框架与穿透 UI，但非独立实现、4 项恒真、可漏检删单 | **3.5 / 10** | 不满足 |
| 3 | 核心算法固定、工厂自定义规则（软硬/权重可配） | 算法固定，规则也固定；无规则 Schema，权重是装饰品 | **2 / 10** | 不满足 |
| 4 | 约束规则分组 / 按优先级分组 | 只有"业务类型/约束性质"两个展示维度，无组、无优先级 | **1 / 10** | 完全不满足 |
| 5 | 多工厂 / 多产线 / 一成品对多半成品 / 多产线可产同一产品 / 推到 MRP | 1:N 的 BOM 结构已支持；N:1 路由、多级 BOM、MRP 全部缺失 | **2.5 / 10** | 大部分不满足 |

> **补充（2026-10-25）**：**人力资源（Headcount）维度当前完全缺失**——主数据里有列、代码不读、模型无变量、校验无检查。这会让"两条线同时开三班"隐含 18 人而系统毫不知情。详见 [§2.7](#27-人力资源headcount维度完全缺失) 与 G-63～G-66；整改任务见行动计划的 ACT-L1～L4。

### 0.3 缺陷分布

共识别 **66 项差距**：**P0（阻断生产可用）21 项**、**P1（影响正确性/可维护性）30 项**、**P2（工程完备性）15 项**。明细见 [§7 差距总清单](#7-差距总清单)。

### 0.4 一句话定位

> 这是一个**演示级（demo-grade）**系统：主线闭环、UI 完成度高、叙事完整；但距离**可交付生产级（production-grade）**还差一次"模型语义重写 + 规则内核化 + 校验器独立化 + 数据模型实体化 + 资源维度补全"的重构，工作量约 **16 周 / 2 人（151 人日）**，详见 [行动计划](aps_remediation_action_plan.md)。

---

## 1. 系统现状

### 1.1 代码与模块

| 模块 | 行数 | 职责 | 评价 |
|---|---:|---|---|
| `src/scheduler.py` | 780 | CP-SAT 班次槽位模型 + 三阶求解 | 核心，需重写语义 |
| `src/validator.py` | 1,500 | 17 项（WL）/ 9 项（SY38）审计 | 非独立实现 |
| `src/data_loader.py` | 1,009 | CSV/Excel → 快照 → 净需求 → 任务 | 净需求公式有误 |
| `src/server.py` | 2,131 | FastAPI 18 个路由 + 全局 session | 巨石 + 硬编码映射 |
| `src/rules.py` | 692 | 规则注册表（27 + 9） | 纯散文，无 schema |
| `src/shift_manager.py` | 433 | 拓扑 + 三阶日历 | 拓扑密封点，设计不错 |
| `src/exporter.py` | 600 | 单 Sheet PSI 导出 | 按 3 班布局写死 |
| `src/balance_builder.py` | 201 | 物料平衡矩阵 | 日均需求写死 `/7` |
| `src/prechecker.py` | 192 | 粗能力预检 | 只打印不阻断 |
| `src/main.py` | 220 | CLI 流水线 | 日历推荐算完不用 |
| `src/static/index.html` | 5,217 | 单页 Vue 应用 | 单文件巨石 |
| `test_aps_pipeline.py` | 197 | 8 个集成测试 | 断言"校验器通过"，循环论证 |

### 1.2 数据流

```
data/*.csv ──► load_aps_csv ──► ProductionInputSnapshot
                                      │
                       build_requirement_plan（净需求 + BOM 展开）
                                      │
                            RequirementPlan（FG/SF 任务）
                                      │
             prechecker（只打印） ──► APSScheduler._build_model ──► CP-SAT
                                      │
                              ScheduleResult（ScheduledTask[]）
                                      │
              ┌───────────────────────┼────────────────────────┐
              ▼                       ▼                        ▼
        exporter（xlsx）      balance_builder（PSI）     validator（17 项）
```

**关键点**：`validator` 的输入是 `ScheduleResult`，即求解器自己构造的对象；`balance_builder`、`exporter` 也都消费同一个对象。整条链路只有一个"真相源"，没有第二条独立计算路径。

### 1.3 一次真实运行的实测数据（`python src/main.py`，10 天视区）

```
15 demand lines -> 7 FG tasks + 12 semi-finished tasks
预检: PGLINKAGE 178.7h / 240.0h (74.5%) | 轴套6 186.1h / 240.0h (77.5%) -> PASS
[AI 排班推荐] 负荷比 74.5% -> 推荐日历模式: SEVEN_ZERO   ← 但下一行仍硬编码 SEVEN_ZERO
初始可行提示: OPTIMAL (4.3s)
软约束档: FEASIBLE | Objective 279 | 9.68s | 利用率缺口 0 班次
换型集中（硬约束 95%）: 型号生产块 56 -> 27
总耗时 53.20s | Objective 8,100,214
Makespan 206.3h | PGLINKAGE 开工 23 班 / 平均 97.1% / <95% 班次 0
轴套6 开工 24 班 / 平均 96.9% / <95% 班次 0
OTD 7/7 = 100%
17 项校验: 16/17 通过；R-WL-12 失败
```

**三个立刻能看出来的信号**：

- **19 个任务、30 个班次、2 条产线，用了 53 秒**，其中 37 秒花在"换型集中"的第二次重解上。这个规模下 53s 已经偏慢一个数量级（同规模 CP-SAT 正常在 1～5s 内可解）。
- **PGLINKAGE 只开了 23/30 个班次，却报 97.1%**。按日历口径是 `178.7 / 240 = 74.5%`。这是 §2.3-G02 的直接证据。
- **R-WL-12（顺位前拉）被判 FAIL**，而模型里明确加了"同产线同型号按交期严格顺位"的约束。二者矛盾，说明该约束是**形式上存在、语义上失效**的（见 §2.3-G01）。

### 1.4 规模画像

| 维度 | WL | SY38 |
|---|---|---|
| 成品料号 | 17 | 11 |
| 半成品料号 | 20 | 5 |
| 产线 | 2（PGLINKAGE / 轴套6） | 3（PU07 / PU22 / 1400T-6） |
| 班次 | 8h × 3 | 12h × 2 |
| BOM 边 | 56 | 9 |
| 单个半成品被几个成品共用 | 最多 6 | 最多 3 |
| 单批最长工时 | **8.45h（WA14208S）> 8h 班次** | 3.27h |

> `WA14208S` 一个 MPQ 批 = 8.45 小时 > 8 小时班次。当前没有净需求所以没触发；**一旦有需求，模型 100% 无解**。这是悬在头上的 P0（G-05）。

---

## 2. CP-SAT 核心算法与求解器使用评估

### 2.1 选型判断：合理

对这个业务（离散班次、整数批量、逻辑约束密集、需要可解释的分配），**CP-SAT 是正确选择**：

- 决策天然是整数（批次数、班次占用），不需要 MIP 的大 M 松弛；
- 模具独占、PM 避让、ETA 门控、客户分级这类"逻辑或/指示"约束，CP-SAT 的布尔通道与 `OnlyEnforceIf` 表达力远好于线性化；
- 30 秒～1 分钟的出解预期与 CP-SAT 的 anytime 特性匹配；
- 后续要做"规则可配置"，CP-SAT 的约束组合比手写启发式更容易参数化。

**结论：选型不用换。要换的是模型语义与工程约束。**

### 2.2 建模范式中值得保留的部分

1. **班次即时间原子**：`x[t,s]` 表示任务 t 在班次 s 投几个 lot，把"95% 班次负荷""跨班连续""班次产能"都变成线性不等式，避免了连续时间建模的排序变量爆炸。对两班/三班制工厂，这是最经济的抽象。
2. **两阶段流水齐套**（`scheduler.py:607-644`）：用累计量 `cum[t,s]` 表达"截至 s-1 班的前道产出 ≥ 截至 s 班的后道消耗"，并允许 s 班装配用 s-1 班产出，正确实现了"按件流水搭接"而不是"全部完工再装配"。
3. **分数 BOM 用既约分数交叉相乘**（`scheduler.py:634-644`）：`Fraction(str(ratio)).limit_denominator(1000)` 后 `denom*avail >= num*assembled`，彻底避免浮点与取整误差。这是全项目最漂亮的一处工程细节。
4. **`PlantTopology` 密封点**（`shift_manager.py:23-87`）：把 8h×3 / 12h×2 的班次几何抽出来，避免在算法核心硬编码 `weekend_off`。方向正确（虽然只实现了两个站点）。
5. **前端"核心计算数据穿透"**：每个校验项都能展开公式、指标徽章、逐班验算表。这是很好的产品化思路，生产级系统应当保留。

### 2.3 P0 级建模缺陷（会直接产出错误计划）

#### G-01｜完工变量 `end_t` 只是"下界"，不是"最后开工班次"

```python
# scheduler.py:454-457
end = model.NewIntVar(lo, hi, f"end.{task.task_id}")
for s in range(lo, hi + 1):
    model.Add(end >= s).OnlyEnforceIf(active[task.task_id, s])
```

只有 `end >= s`，**没有任何约束把 `end` 上界绑定到"最后一个 active 的 s"**。因此 `end_t` 是真实完工班次的**下界**，而不是等号。

后果：

- 顺位约束 `end(A) <= end(B)`（`scheduler.py:478`）可以被"把 A 的 end 报小"满足，而 A 的真实最后班次远晚于 B —— **R-WL-12 实际未生效**；
- 拖期惩罚 `late >= end - due`（`scheduler.py:522`）会**低估拖期**，软约束档可能接受实际已延误的方案；
- 目标里的 `sum(end_t)`（`scheduler.py:531-534`）只在下界意义上"尽早"，与业务理解的"完工时间"不是同一个量。

**实测证据**：CLI 运行中，模型明确加了 `end(1019) <= end(1020)`，但校验器报：

```
轴套6 WBC9324: 早期工单 ORD_WL_WBC9317C_1019 完工时间晚于后期工单
ORD_WL_WBC9317_1020，存在跳序插单
```

即"模型认为满足、真实时序违反"。这是本报告最重要的一条：**它让 `R-WL-12 / R-SF-02 / R-FG-05` 三条规则整体失效，而 UI 上仍显示 PASS。**

> 正确写法：`end >= s` 且 `end <= s + M*(1 - active[s])`，或直接定义 `end = max{s : active[s]=1}`（对每个 s 加 `end <= s + (hi-s)*(1-active[s])`）。

#### G-02｜95% 利用率只在"已开工班次"内判定，空班/跳班零成本

模型侧（`scheduler.py:679-701`）：`run[l,s]` 只在 `load>0` 时为真，`gap` 与 `below` 只对 `run=1` 的槽位生成；`shortfall` 与 `violations` 都不覆盖 `run=0` 的槽位。
校验侧（`validator.py:203-229`）：只遍历 `loads` 字典（即 `shift_seconds` 非空的班次）。
报表侧（`main.py:171-188`）：`worked` 也是 `loads>0` 的班次。

后果：

- 一条线可以只开 10 个班次、每班 100%，然后报"平均利用率 100%、低于 95% 班次 0 个"；
- 中间的空洞（跳班）不产生任何惩罚，`_check_utilization` 也看不见；
- 模型里唯一的"班次数"约束是**下界** `sum(running) >= ceil(work / max_line_ms)`（`scheduler.py:674`），只会推动"少开班"，不会推动"填满日历"。

**实测**：日历利用率 74.5%，报表 97.1%。这两个数字口径不同，而 UI 只展示后者。

> 业务必须先定义 95% 的分母：**是"日历班次"还是"开工班次"？** 如果是后者，这条规则在数学上几乎恒真，不具备约束力。

#### G-03｜换型/调机工时没有进入容量约束

模型：`load[l,s] = Σ x[t,s] * lot_millis`（`scheduler.py:656-664`），**没有 setup 项**。
校验：`validator.py:133-152` 算了 `setup_seconds = within_co * 1800`，也算了 `effective_seconds`，但判超载用的是 `is_over = seconds > limit_seconds`——**用的是纯加工秒，不是综合秒**。所以"含换型 1.0h"只是显示，不参与判定。

后果：一个班次排 3 个型号，物理上是 `8h + 1h = 9h`，模型与校验都认为合规。现场必然超载。

#### G-04｜同一工单可以被切成任意不相邻的班次（违反"跨班连续、严禁切断换线"）

模型只有 `sum(x[t,s]) == lots`（`scheduler.py:443`），**没有"active 必须连续"的约束**。因此同一个工单可以 10/12 做两个班、停三天、10/19 再做两个班。

**实测**：`FG::ORD_WL_WB35356_1020` 的班次序列跨 10/12～10/20，中间有多个空档；换型集中后仍然如此。这直接违反 R-WL-09，而且校验器**没有连续性检查**（`_check_continuous_merge` 只统计换型次数，且 `violations` 恒为空）。

> 最小修法：`active[t,s] + active[t,s+2] <= 1 + active[t,s+1]`（三连窗口）或引入 `first/last` 变量加通道约束。

#### G-05｜单批工时 > 班次工时时，模型结构性无解

```python
# scheduler.py:429-435
max_line_ms = self._get_line_max_shift_ms(task.line)
per_shift = max(1, min(task.lots, max_line_ms // task.lot_millis))
min_lots  = min(per_shift, task.mpq // task.lot_size)
```

当 `lot_millis > max_line_ms` 时，`max_line_ms // lot_millis == 0`，`per_shift` 被 `max(1, ...)` 抬成 1，但容量约束 `load <= max_line_ms` 又装不下这一批 → `sum(x) == lots` 与容量互斥 → **直接 INFEASIBLE / RuntimeError**。

**实测数据**：`WA14208S`（MPQ 72、CT 300s、TRP 0.71）= 72 × 300 / 0.71 = 30,423s = **8.45h > 8h**。当前该料号在 10 天视区内无净需求，所以没爆；只要来一张单就爆。

> 需要"批次切片（sub-lot slicing）"或"料号×班次"级变量：允许一批跨班，但班内切片可分割。

#### G-06｜排产首班被无条件锁死

```python
# scheduler.py:637-638
if s - 1 < comp_lo:          # comp_lo 恒为 0
    model.Add(cumulative[fg_task.task_id][s] == 0)
```

`comp_lo` 恒等于 0，所以对 `s=0` 无条件强制"成品首班累计产出 = 0"，**完全不看半成品期初库存是否充足**。

**实测**：PGLINKAGE 最早一笔成品开工是 `10/12 15:00`（第 2 个班次），10/12 早班整班空转。这不是业务决策，是模型 bug。

> 正确写法：`model.Add(assembled * ratio <= component.stock_covered)`，库存够就允许首班开工。

#### G-07～G-19（其余 13 项摘要）

| ID | 级别 | 问题 | 位置 |
|---|---|---|---|
| G-07 | P1 | 尾班豁免未限"每条线至多一个"；全空班时统计为空 | `scheduler.py:679-701` |
| G-08 | P1 | 目标量纲混用：班次数 / 毫秒 / 次数直接相加；`CHANGEOVER_WEIGHT=300_000` 相当于 33 个班次，注释却说"300 秒" | `scheduler.py:45-59, 531-591` |
| G-09 | **P0** | 非确定性：`num_workers=8`，同输入两次运行结果不同（实测见下） | `scheduler.py:373-379` |
| G-10 | P1 | 降级档由"搜索运气"触发：15s 内找不到硬交付解就自动放宽交付 | `scheduler.py:198-236` |
| G-11 | P1 | 无库存变量：R-WL-22/23、R-SY-06/07/09 的库存上限/覆盖天数在目标里无对应项 | `scheduler.py` 全文 |
| G-12 | P1 | 无班内顺序与顺序相关换型：SY38 换色矩阵、跨班继承模具不可表达 | `scheduler.py:536-559` |
| G-13 | P1 | 齐套只有下界无上界，半成品可无限堆库存 | `scheduler.py:640-644` |
| G-14 | P1 | 半成品无拖期/断线代价，只有 FG 有 `late` | `scheduler.py:515-529` |
| G-15 | P1 | 需求守恒是硬等式，产能不足即抛异常；无缺口变量/委外变量 | `scheduler.py:443` |
| G-16 | P2 | `LATENESS_SLACK_SHIFTS = 6` 硬编码，拖期窗口不可配 | `scheduler.py:59` |
| G-17 | P2 | `TIME_SCALE = 1000` 固定毫秒；无精度校验 | `data_loader.py:798-803` |
| G-18 | P2 | 未用 LNS/决策策略/对称破除；变量按 task×shift 全展开 | `scheduler.py:412-457` |
| G-19 | P2 | 预算分配 6% / 55% / 70% 是经验值，无自适应 | `scheduler.py:205, 216, 252` |

#### G-09 实测：非确定性

同一份数据、同一个 `random_seed=1`、同样 15 秒，连续跑两次：

| | run 0 | run 1 |
|---|---|---|
| 走的档位 | 软利用率 + **硬交付** | 硬交付求不出 → **降级为交付损失最小化** |
| 状态 | FEASIBLE | FEASIBLE |
| 目标值 | 820,949,006 | 2,521,634,388,755 |
| 低于 95% 的班次 | 1 个（合计 149s） | **12 个（合计 111,134s）** |
| 交付 | 100% | 出现拖期 |

**同一个输入，一次是 100% 交付，一次是降级交付。** 对生产系统这是不可接受的：计划员无法解释"为什么昨天排得出来、今天排不出来"。

根因是 `num_workers=8` 的多线程 portfolio 搜索 + 时间盒截止，二者叠加导致结果不可复现。

### 2.4 目标函数与求解策略

**当前目标**（`scheduler.py:531-591`，按档位动态拼装）：

```
min  Σ end_t
   + 300_000 · Σ run_starts          # 换型
   +  50_000 · Σ excess_models        # 并行型号超 3
   +  15_000 · Σ weekend_slots        # 周末开机
   + 200     · Σ overload             # 105% 溢出
   + W_late  · Σ (tier_mult · late)   # 拖期
   + W_gap   · Σ gap                  # 95% 缺口（软档）
```

问题：

1. **量纲不统一**：`end` 是班次（0～40），`gap` 是毫秒（0～28,800,000），`excess` 是型号个数。用 300,000 去买"一次换型"，等价于"宁可用 33 个班次的完工延迟换一次换型"——这与业务直觉（一次换型 30～45 分钟 ≈ 0.06 个班次）差 **7 个数量级**。
2. **权重没有标定依据**：`W_late = lines × num_shifts × W_violation + 1`（`scheduler.py:598-605`）是一个"保证压倒性"的构造，能保证优先级，但让目标值失去物理意义（8,100,214 里 8,100,000 全是换型）。
3. **单一加权和无法表达"分组优先级"**：所有规则被压成一个数，一旦两个软约束冲突，谁赢由常量大小决定，而不是业务说的"第一优先级/第二优先级"。
4. **三档降级 + `_SolutionStopper` 提前停**：设计上是"先找可行、再优化"，但"提前停"使解质量随机——同一份数据可能拿到 56 块也可能 27 块（实测两次分别是 51→25 与 56→27）。

### 2.5 CP-SAT 能力利用率：约 40%

当前只用了 CP-SAT 的"整数变量 + 线性约束 + 目标"三件套。以下能力全部未用，而它们恰好能解决上面的问题：

| 能力 | 可用于解决 | 现状 |
|---|---|---|
| `NewIntervalVar` + `AddNoOverlap` | 班内排序、顺序相关换型、共模设备互斥 | 未用 |
| `AddCircuit` | 同产线型号序列（TSP 式），颜色换色矩阵 | 未用 |
| `AddElement` / `AddAllowedAssignments` | 料号→候选产线路由（N:1 替代产线） | 未用 |
| `AddDecisionStrategy` | 大规模时的搜索方向控制 | 未用 |
| `AddHint` + 构造式启发（EDD/FFD） | 替代"先解一个松弛模型当 hint"（现在花 4.3s） | 未用 |
| `SufficientAssumptionsForInfeasibility` | 无解时告诉计划员"是哪条规则导致无解" | 未用（当前只抛 RuntimeError） |
| `num_workers=1` + 固定 seed | 可复现 | 反着用（8 workers） |
| `log_search_progress` | 调参诊断 | 关闭 |

> 对"规则可配置"这个目标，`SufficientAssumptionsForInfeasibility` 尤其关键：计划员改了一条规则导致无解，系统要能说"是你把 95% 调成硬约束且换型上限设为 1 导致的"，而不是一句 INFEASIBLE。

### 2.6 性能与规模推演

| 场景 | 任务数 | 班次数 | `x` 变量 | 预估总变量 | 当前架构可行性 |
|---|---:|---:|---:|---:|---|
| 现状（WL 10 天） | 19 | 30 | 570 | ~1.5k | 53s（偏慢） |
| 单厂 5 线 10 天 | 150 | 30 | 4,500 | ~15k | 困难（分钟级） |
| 5 厂 × 5 线 | 750 | 30 | 22,500 | ~80k | 不可行 |
| 300 订单行 × 30 班 | 600 | 30 | 18,000 | ~60k | 不可行（文档自述 30k+ 会 Memory Exceeded） |

结论：**必须按"工厂 / 产品家族"分解求解**（与你们 `aps_functional_spec.md` 里的 Hierarchical APS 主张一致），单模型不要超过 ~2 万变量。跨厂只做"共享前道资源"的顶层切片，不做全局联合求解。

### 2.7 人力资源（Headcount）维度完全缺失

#### 2.7.1 数据现状：有列，但是"死列"

| 文件 | 列名 | 实测取值 | 问题 |
|---|---|---|---|
| `data/Parameters-FG.csv` | `Headcount` | PGLINKAGE = **3**（17 行一致）；PU07 = **12**（7 行）；PU22 = **12**（2 行）/ **15**（2 行） | PU22 同一产线两种人数，口径未定义 |
| `data/Parameters-SFN.csv` | `Head count` | 1400T-6 = **1**（5 行）；**轴套6 = 全空**（20 行） | WL 的半成品线根本没有配置人数 |
| 两表末尾 | — | 12 行全空 + 1 行中文说明（"挑一个成品和半成品…"） | 手工维护残留，说明主数据无校验 |

#### 2.7.2 代码现状：从读到"丢"

- `ProductParam`（`data_loader.py:24-39`）**没有 headcount 字段**；
- `load_aps_csv`（`data_loader.py:268-339`）与 `_load_params`（`data_loader.py:764-795`）**都不读这一列**；
- `ProductionTask`（`data_loader.py:127-150`）没有人员属性；
- `scheduler.py` 的模型里没有任何"人"的变量或约束；
- `validator.py` 的 17 项检查里没有一项与人有关；
- `exporter.py`/`balance_builder.py` 的报表里没有人力工时。

> 也就是说：这一列从 CSV 读入时就被 `csv.DictReader` 收下，然后**在构造 dataclass 时静默丢弃**。

#### 2.7.3 后果（为什么这不是"报表缺一列"，而是模型缺一层）

1. **隐含的人力超配**：WL 两条线 × 3 班 × 3 人 = **18 人**（轴套6 的 Headcount 缺失，暂按 3 人估）。模型可以让两条线在所有班次同时开工，而系统不知道工厂到底有没有 18 个人。当前数据里没有"可用人数"字段，所以这个约束**根本无法表达**。
2. **班组共享不可表达**：如果 PGLINKAGE 与 轴套6 共用一个 3 人班组（只有开一条线时另一条才能开），模型会同时开两条，现场做不到。
3. **人力成本/工时不可见**：无法回答"这张计划要多少工时、峰值多少人、人均产出多少"，也就无法做"少开班省人"与"多开班保交付"的权衡。
4. **100 条线时必然崩**：100 条线 × 3 班 × 3 人 = 900 人，没有人力约束的排产是"理论上可行、组织上不可行"。
5. **换型/维保的人力峰值被忽略**：换模通常要 2 名技师，PM 要机修班组；这些"瞬时人力峰值"当前完全没有进入模型。

#### 2.7.4 为什么现在没暴露

只有 2 条线，且可用人数没有录入——**约束不存在，自然也不会违反**。这与 G-02（95% 只在开工班次里算）是同一类问题：**KPI 的分母/约束的边界没定义，指标就永远好看**。

#### 2.7.5 需要补齐的四层

| 层 | 内容 | 归属 |
|---|---|---|
| 需求侧 | 每条线/每个料号开一个班需要几人（`crew[line, part]`，缺省 `crew[line]`） | 主数据 |
| 供给侧 | 每个工厂、每天、每个班次可用几人（含请假/培训/借调） | 新增 `Labor-Availability` 表 |
| 约束侧 | `Σ_l crew[l,s] ≤ available[s]`；共享班组互斥；换型/PM 的瞬时人力峰值 | 模型（第 19 个算子 `labor_capacity`） |
| 目标侧 | 人力工时成本、峰值人数、人力平滑（避免今天 18 人明天 3 人） | 目标函数 / 报表 |

#### 2.7.6 一个具体的反例

以 SY38 为例：PU07（12 人）+ PU22（12～15 人）+ 1400T-6（1 人）= **25～28 人**同时开线。如果工厂实际只有 20 人，当前模型会给出一个"三线全开"的计划，现场只能停一条线——而系统会认为计划 100% 可行。

> 这正是"看起来 PASS、现场不可执行"的又一实例，和 G-01/G-02/G-03 属于同一类问题：**模型的资源维度不完整**。

---

## 3. 二次独立验证评估

### 3.1 "独立性"审计：目前不成立

一个真正独立的二次验证，至少应满足三条：**不同输入源、不同计算路径、可证伪**。当前三条都不满足：

| 独立性维度 | 现状 | 证据 |
|---|---|---|
| 输入源 | 校验器吃的是 `ScheduleResult`（求解器构造的对象）与 `RequirementPlan`（同一个 `data_loader` 生成的净需求） | `validator.py:14-17, 33-54` |
| 派生量 | `shift_seconds` 直接由求解器写入 `ScheduledTask`（`lots × lot_millis / 1000`），校验器不再从 CT/TRP/数量独立重算 | `scheduler.py:724`；`validator.py:89-91` |
| 需求来源 | 守恒检查对比的是 `plan.tasks[i].quantity`，与求解器的输入是同一份，没有回原始 Excel 重算"毛需求 − 库存 + 安全库存" | `validator.py:258-323` |
| 规则实现 | 校验函数与求解器共享 `ScheduledTask` 数据类、共享 `get_topology`、共享 `MIN_SHIFT_UTILIZATION` | 文件头 import |

**结论**：如果 `lot_millis` 算错、`build_requirement_plan` 净需求算错、`shift_seconds` 写错，校验器会**同步错**，不会报警。这是"同源自证"，不是"独立验证"。

### 3.2 17 项检查逐项评级

| # | 检查 | 规则 | 评级 | 说明 |
|---|---|---|---|---|
| 1 | 单班产能不超额定上限 | R-WL-26 | ⚠️ 弱 | 判超载用纯加工秒，不用含换型的 `effective_seconds`（`validator.py:145`） |
| 2 | 班次利用率 95%~105% | R-WL-26 | ⚠️ 口径缺陷 | 只统计开工班次；空班不可见（G-02） |
| 3 | 排产量等于净需求且守恒 | R-WL-13 | ❌ 自证 | 对比对象是同一份 plan；**删掉的任务不参与统计**（G-21） |
| 4 | 成品 MPQ 整倍 | R-WL-09 | ✅ 有效 | 独立重算取模 |
| 5 | 半成品整数 + 起投 ≥ MPQ | R-WL-15 | ✅ 有效 | 但 `min_lots = min(per_shift, mpq//1)` 可能把门槛降到 1（`scheduler.py:435`） |
| 6 | 半成品库存满足流水齐套 | R-WL-23 | ⚠️ 弱 | BOM 从 plan 反推（`plan.snapshot` 不存在），被库存覆盖掉的组件不进入仿真 |
| 7 | 库存流水账平衡且安全库存达标 | R-WL-22 | ❌ 自证 | `closing` 与 `opening+produced-consumed` 由同一函数生成，恒等 |
| 8 | 早班截单准时交付 | R-WL-20 | ✅ 有效 | 但漏检"未排产"的订单（`if not scheduled: continue`） |
| 9 | 特殊首批受控窗口 | R-WL-14 | ❌ 恒真 | `is_pilot` 属性从未被赋值 → 永远"无试制批次，自然合规" |
| 10 | 顺位前拉不跳序 | R-WL-12 | ✅ 有效（关键） | 正是它抓到了 G-01 |
| 11 | 时间围栏锁定 | R-WL-10 | ❌ 恒真 | `is_locked` 仅在人工审计路径临时 `setattr`；求解路径永远"无锁定工单" |
| 12 | 跨班连续生产无多余换模 | R-WL-11 | ❌ 恒真 | `violations` 从未 append，`passed=True` 永远成立 |
| 13 | 模具跨线独占 | R-WL-01 | ✅ 有效 | 但只查"同班同模多线"，不查时间区间重叠 |
| 14 | 物料 ETA 齐套 | R-WL-21 | ⚠️ 弱 | 用班次序号比较，ETA 解析失败时静默跳过 |
| 15 | PM 避让 | R-WL-27 | ✅ 有效 | 依赖 `line_pm_slots` 正确传入 |
| 16 | 战略客户交付保障 | R-WL-24 | ⚠️ 映射错误 | R-WL-24 实际是"拉单窗口"，客户优先级是 R-CR-01；且 `'WA' in part_no` 硬编码 |
| 17 | 三阶日历 + 前瞻平准 | R-WL-02~08, 25 | ❌ 近乎恒真 | 只校验"班次时长 = 8h"与"负荷 CV ≤ 35%"，CV 不达标也只写 `WARN` 不进 `violations`（`validator.py:1161-1193`） |

**统计**：真正独立有效的 **5 项**（4/5/8/10/13/15 中取强项），弱 **4 项**，恒真/自证 **6 项**。所谓"17 项独立验证全 PASS"，实际有效覆盖不到三分之一。

### 3.3 实测漏洞：删掉一张工单，仍然 100% 合规

**实验设计**：先正常求解得到基线（17 项全 PASS），然后从结果里删除一张成品工单及其 2 张半成品子工单（模拟计划员在 Excel 里删掉一整行），再跑同一套 `validate_schedule`。

**结果**：

```
baseline failed: []
dropped: FG::ORD_WL_WBC9317C_1013 children: 2
after-delete failed: ['班次产能利用率在95%~105%区间（尾班豁免）']

conservation: total_demand_pieces=29789, total_scheduled_pieces=29789, gap=0, 100.0%
delivery:     total_demand_orders=7, on_time=7, late=0, OTD=100.0%
mpq:          total_fg_tasks=6, compliance=100%
```

**为什么漏检**：

- `_check_conservation` 遍历的是 `result.tasks`，不是 `plan.tasks`。任务被删掉，分子分母同时消失，`gap = 0`。
- `_check_delivery` 的分母是 `len(plan.fg_tasks) = 7`，但 `late_count` 只统计存在的任务 → `on_time = 7 - 0 = 7` → **100%**。这是最严重的一处：**分母包含已删除的订单，分子也把它当"准时"**。
- `_check_fg_mpq` 只遍历存在的 FG 任务。

**后果**：手工上传审计（你宣传的"二次独立审计闭环"）**无法发现"漏排一整个订单"这种最常见的计划员错误**。这恰恰是二次审计最该抓的东西。

### 3.4 手工上传审计链路（`POST /api/audit_manual_schedule`）

`server.py:1636-1908` 的流程是：解析上传的 PSI/Production plan 表 → 用启发式重建 `ScheduledTask` → 复用同一个 `validate_schedule`。问题：

1. **复用 session 里的 `plan`**（`server.py:1662`）。如果用户上传的是 15 天计划的文件，而 session 里是 10 天的 plan，校验对象错位。
2. **重建是启发式的**：同一料号多订单时按 FIFO 均摊（`server.py:1826-1846`），把排产量硬分配给订单，**不是从文件里读出来的**。分配错了也不会报警。
3. **`shift_seconds` 用 `qty * ct/trp` 重算**（`server.py:1804`），与求解器的 `lots * lot_millis/1000` 不是同一口径（前者不含 MPQ 取整），两边数字会对不上。
4. **`allow_105` 默认 True**（`server.py:1899`），而求解默认 False → **审计比求解更宽松**。
5. **冻结期只在有 baseline 时才临时打 `is_locked`**（`server.py:1888-1897`），且阈值写死"前 3 个班次"。

### 3.5 合规矩阵的显示口径

`format_rule_compliance_matrix`（`validator.py:1470-1500`）对没有任何 check 映射的规则，直接显示 `rule.status.value`，而 `rules.py:119` 把所有规则硬编码为 `RuleStatus.IMPLEMENTED = "PASS"`。

**实测**：WL 的 27 条里，R-WL-16/17/18/19（4 条测试用例）没有任何 check，矩阵里显示 `PASS`。这就是"39 条规则全 PASS"的来源——**未被验证的规则默认显示通过**。

### 3.6 结论

> **当前不满足"二次独立验证无缺陷无漏洞"。**
> 需要做的不是"再加几项检查"，而是换一种验证范式：**独立实现 + 完整性检查 + 变异测试**（见 §8.5）。

---

## 4. 规则可配置化评估（你的核心诉求）

### 4.1 现状：规则是"散文 + 硬编码开关"

```
Excel「排产原则-约束条件」
   │ 8 列：站点 / 工序阶段 / 约束类型 / 规则名 / 约束性质 / 权重 / Col G 逻辑 / 行号
   ▼
rules.py::load_rules_from_excel  ──►  Rule dataclass（weight 只存不读）
   ▼                                       │
server.py::/api/rules  ──►  UI 展示权重     │
   ▼                                       │
server.py::run_solver  ──►  15 个硬编码 if ──► APSScheduler(bool...)
```

具体问题：

1. **无 Schema**：`Rule` 只有 `rule_id / name / weight / status / ai_logic` 等展示字段（`rules.py:46-61`），没有 `params`、`scope`、`threshold`、`group`、`priority`、`effective_date`。Col G 是【逻辑公式】/【业务边界】/【场景示例】三段自然语言，机器不可读。
2. **规则 ID 由行序生成**（`rules.py:93-100`）：`R-WL-{we_idx:02d}` 按 Excel 行号递增。**在 Excel 里插入一行，后面所有规则的 ID 全部平移**，而 `server.py:1052-1113` 的开关映射是按 ID 写死的 → 规则与实现的对应关系静默错位。
3. **开关不可配**：`APSScheduler.__init__` 只有 15 个布尔开关（`scheduler.py:114-140`），且其中 3 个是死参数——`enforce_freeze_window`、`freeze_shifts`、`enforce_exact_demand` 存进 `self` 后**从未被读取**（grep 全文只有赋值行）。
4. **权重是装饰品**：`rule.weight` 只出现在 `server.py:1123/1466/1937` 的 JSON 序列化与 `index.html:1611` 的展示，**求解器与校验器从未读取**（grep `.weight` 全库确认）。也就是说，你在 UI 上看到的"软约束（80%）"对排产结果**没有任何影响**。
5. **增加第三个工厂必须改五处代码**：
   - `rules.py:77` `rules_by_site = {"WL": {}, "SY38": {}}`
   - `rules.py:93-100` 站点白名单（其他站点 `continue`）
   - `shift_manager.py:84-87` `get_topology` 只有两个分支，**未知站点静默回退 WL**（12h 工厂会按 8h 排）
   - `validator.py:44-54` 按 site 二选一
   - `server.py:1052-1113` 开关映射 + 前端写死的 `RULE` / `PGLINKAGE`

### 4.2 目标能力模型

你要的其实是**"固定解释器 + 可配置程序"**。把它拆成四层：

```
┌─────────────────────────────────────────────────────────────┐
│  L4 规则实例（工厂可编辑）                                    │
│  rule_instance: template_id + scope + nature + weight +      │
│                 group + priority + params + effective + on   │
├─────────────────────────────────────────────────────────────┤
│  L3 规则模板（产品方固定，版本化）                            │
│  template: 参数 schema + 校验 + 编译函数（→ CP-SAT 项）        │
├─────────────────────────────────────────────────────────────┤
│  L2 约束算子内核（代码固定，不随工厂变化）                     │
│  capacity / min_batch / changeover / material / due / seq /   │
│  calendar / tooling / pm / eta / concurrency / inventory /    │
│  customer / routing / freeze / shared_resource               │
├─────────────────────────────────────────────────────────────┤
│  L1 数据模型（实体化）                                        │
│  site / line / routing / bom / inventory / demand / rule_set │
└─────────────────────────────────────────────────────────────┘
```

**关键约束**：L2 的算子集合是**封闭**的（固定算法），L3 的模板是**版本化**的（可增不改），L4 的实例是**每个工厂可自由定义**的（软硬、权重、阈值、范围、启停、生效期）。

这样既满足"求解器核心算法固定"，又满足"工厂自己定义规则"。

### 4.3 差距清单（详见 §7 的 G-31～G-39）

| 你要的能力 | 现状 | 差距 |
|---|---|---|
| 规则模板固定 | 无模板概念，只有 Excel 行 | 需新建 template registry（约 15 个算子） |
| 软/硬可配 | 硬编码在 `if` 链里，改一条要改代码 | 需 `nature: hard\|soft` 字段 + 编译器分支 |
| 权重可配 | 权重不生效 | 需权重进入目标函数，并按组归一 |
| 阈值可配 | 95%/105%、30min、3 型号、6 班、35% 全是常量 | 需 `params` 字段 |
| 作用域可配 | 只有"全站点"一个粒度 | 需 `site/line/part/stage/period` 作用域 |
| 生效期 | 无 | 需 `effective_from/to` |
| 版本与审计 | 无 | 需 rule_set 版本号 + 变更日志 |
| 新工厂接入 | 改 5 处代码 | 需纯数据接入 |

### 4.4 规则模板 Schema 草案（建议直接采用）

```yaml
# rule_templates/capacity_utilization.yaml   —— 产品方维护，代码固定
template_id: capacity_utilization
version: 3
description: 班次产能利用率区间约束（可硬可软，可配分母口径）
applies_to: [line, shift]
params_schema:
  min_util:      {type: ratio, default: 0.95, min: 0, max: 1}
  max_util:      {type: ratio, default: 1.00, min: 0.5, max: 1.5}
  denominator:   {type: enum, values: [calendar_shift, opened_shift], default: calendar_shift}
  tail_exempt:   {type: bool, default: true}
  max_tail_count:{type: int, default: 1}
  include_setup: {type: bool, default: true}
compile: |
  # 固定实现：读取 scope 生成 line×shift 的 load 变量与 gap 惩罚
  ...
```

```yaml
# rule_sets/WL_2026Q4.yaml   —— 工厂计划员维护，纯数据
rule_set_id: WL_2026Q4
version: 7
site: WL
effective: {from: 2026-10-01, to: 2026-12-31}
groups:
  - {id: G0_PHYSICAL,   priority: 0,  mode: hard}
  - {id: G1_DELIVERY,   priority: 10, mode: lexicographic}
  - {id: G2_CAPACITY,   priority: 20, mode: lexicographic}
  - {id: G3_CHANGEOVER, priority: 30, mode: weighted}
  - {id: G4_INVENTORY,  priority: 40, mode: weighted}

rules:
  - id: WL_CAP_95
    template: capacity_utilization
    scope: {line: PGLINKAGE, stage: FG}
    group: G2_CAPACITY
    nature: soft
    weight: 100
    params: {min_util: 0.95, max_util: 1.00, denominator: calendar_shift, tail_exempt: true, include_setup: true}

  - id: WL_MPQ_BATCH
    template: min_batch
    scope: {stage: FG}
    group: G0_PHYSICAL
    nature: hard
    params: {rounding: ceiling, unit: MPQ}

  - id: WL_CHANGEOVER
    template: changeover
    scope: {line: "*"}
    group: G3_CHANGEOVER
    nature: soft
    weight: 30
    params: {setup_minutes: 30, count_by: run_start, cross_shift_inherit: true}
```

**这份 YAML 就是"工厂自己定义规则"的落点**：工厂改 YAML，不改代码；产品方改 template，不动工厂配置。

### 4.5 规则 → CP-SAT 的编译设计

```
RuleInstance ──► TemplateCompiler.compile(ctx, instance)
                    │
                    ├─ nature == hard ──► ctx.model.Add(...)            # 约束
                    └─ nature == soft ──► ctx.add_penalty(var, weight, group)  # 惩罚变量
                                              │
                                     GroupAssembler
                                              │
                       ┌──────────────────────┴───────────────────────┐
                       ▼                                              ▼
              hard 组：直接加约束                          soft 组：按组归一的加权和
              （冲突即 INFEASIBLE）                        （组内 w_i / Σw，组间字典序）
```

**编译期必须解决的三个问题**：

1. **作用域求值**：`scope` 要能解析成具体的 `(task, slot)` 笛卡尔子集，且要做空集校验（规则没匹配到任何对象时报警，而不是静默通过——现在的 `enforce_controlled_window` 就是这样失效的）。
2. **参数校验**：`min_util <= max_util`、`weight > 0`、`threshold` 在物理范围内。非法配置必须在编译期拒绝，而不是求解期 INFEASIBLE。
3. **可解释性**：每个惩罚变量要能回溯到 `rule_instance.id`，这样 `SufficientAssumptionsForInfeasibility` 才能输出"是 WL_CAP_95 与 WL_CHANGEOVER 冲突"。

### 4.6 权重如何真正生效

当前是"加权和 + 手调常量"，不可维护。建议：

- **组内**：归一化 `w_i / Σ_group w`，乘组内惩罚总和，量纲统一为"分钟当量"（把班次、毫秒、次数都折算成分钟）。
- **组间**：**字典序（lexicographic）**，不是加权。CP-SAT 实现方式：
  ```
  for level in sorted(groups, key=priority):
      model.Minimize(objective[level])
      solve()
      model.Add(objective[level] <= best_value)   # 或 <= best*(1+ε) 允许 1% 让步
  ```
  这样"第一优先级"是数学保证的，不依赖常量大小。
- **权重上限保护**：任何单条软规则的权重不得超过组内总权重的 50%，防止一条规则压死其他规则。

---

## 5. 分组与优先级

### 5.1 现状

- **分组**：`ConstraintCategory`（9 类）与 `ConstraintNature`（5 级）只是**展示标签**，不参与模型构造。
- **优先级**：不存在。只有三种替代物：
  1. 目标函数里的常数大小（`W_late` vs `CHANGEOVER_WEIGHT`）；
  2. 客户分级 10x/3x/1x（`scheduler.py:526`，硬编码）；
  3. 三档降级（软利用率 → 硬交付 → 交付损失最小化），是**计算降级**，不是业务优先级。
- **组间冲突无仲裁**：95% 与交付冲突时，靠"档位顺序"；换型与库存冲突时，靠常量大小。

### 5.2 你要的分组应该长什么样

业务上的"分组"通常有三种含义，当前一个都没有：

| 分组类型 | 含义 | 例子 | 目标实现 |
|---|---|---|---|
| **约束域分组** | 按物理/业务对象聚簇 | 物理约束组、交付组、产能组、换型组、库存组 | `group` 字段 + 组级启停 |
| **优先级分层** | 组间有严格先后 | 交付 > 产能 > 换型 > 库存 | `priority` + 字典序求解 |
| **互斥/依赖组** | 组内规则只能生效其一，或 A 生效则 B 必须生效 | "做四休三"与"连续生产"互斥 | `group.mode: exclusive` + `requires` |

### 5.3 建议的分组模型

```yaml
groups:
  - id: G0_PHYSICAL
    priority: 0
    mode: hard            # 组内全部硬约束，冲突 = 无解
    members: [tooling_exclusive, pm_block, eta_block, mpq_batch, freeze]
  - id: G1_DELIVERY
    priority: 10
    mode: lexicographic   # 先保证交付，再谈其他
    members: [due_date, customer_priority, rush_order]
  - id: G2_CAPACITY
    priority: 20
    mode: lexicographic
    members: [utilization_95, concurrency_limit, calendar]
  - id: G3_CHANGEOVER
    priority: 30
    mode: weighted
    members: [changeover_min, cross_shift_inherit]
  - id: G4_INVENTORY
    priority: 40
    mode: weighted
    members: [month_end_stock, sf_stock_days, coverage_days]
```

求解顺序：`G0` 全部硬约束 → `G1` 最小化拖期并锁定上界 → `G2` 最小化产能缺口并锁定 → `G3` 最小化换型 → `G4` 最小化库存成本。

### 5.4 差距

- 没有组实体 → 需要新增 `rule_group` 表与 API；
- 没有优先级 → 需要把"单一加权和"改成"多轮字典序"，这是 `scheduler.py` 求解器的结构性改造；
- 没有组间互斥/依赖 → 需要 `constraint_graph`（哪些规则不能同时开）；
- 没有组级 UI → 现在是 27 条规则平铺的 checkbox 列表，计划员无法按组操作。

---

## 6. 扩展性评估

### 6.1 多工厂：结构上有，实质上是"两套硬编码"

| 能力 | 现状 | 证据 |
|---|---|---|
| 站点隔离 | ✅ CSV 的 `Site` 列过滤 | `data_loader.py:274` |
| 站点级拓扑 | ✅ 两套 `PlantTopology` | `shift_manager.py:63-81` |
| 站点级规则 | ⚠️ 只有两个前缀，其他站点 `continue` | `rules.py:77, 93-100` |
| 未知站点 | ❌ 静默回退 WL（12h 厂按 8h 排） | `shift_manager.py:84-87` |
| 站点级设置持久化 | ✅ `site_settings_store` | `server.py:74-109` |
| 多站点并发 | ❌ 单进程全局 `session_state` | `server.py:216-231` |
| 跨厂共享资源 | ❌ 无 | — |
| 分层协调（Hierarchical APS） | ❌ 仅文档主张 | `aps_functional_spec.md` |

### 6.2 多产线 / 替代产线

**当前数据结构**：`ProductParam.line` 是单个字符串；`ProductionTask.line` 在 `build_requirement_plan` 时被固定（`data_loader.py:911`）。因此：

- ✅ **一个成品对多条半成品线**：已支持。BOM 可以列多个 SF 组件，每个组件有自己的线；`_add_material_balance` 对所有组件逐一约束。WL 的 PGLINKAGE ← 轴套6 就是这个形态。
- ⚠️ **同一个半成品由多条线供应**：不支持。`sf_params[pn].line` 只有一个值。
- ❌ **多条产线都能生产同一成品（替代路由）**：不支持。没有 `routing` 表，没有 `y[t,l]` 分配变量。

**改造点**：

```yaml
# routing.csv
site,part_no,line,priority,ct_seconds,trp,mpq,capacity_share,min_run
WL,WA14208,PGLINKAGE,1,38,0.71,48,1.0,48
WL,WA14208,ALT_LINE,2,42,0.70,48,1.0,48
```

模型侧新增 `y[t,l] ∈ {0,1}` 与 `Σ_l y[t,l] = 1`（或允许拆分的 `x[t,l,s]`），产能约束改成对 `(l,s)` 求和。变量量增加 `|L|` 倍，需要配合"每料号最多 2 条候选线"的业务约束控制规模。

### 6.3 多级 BOM

**现状**：`RequirementPlan` 只有 `fg_tasks` 与 `sf_tasks` 两层；`bom` 是 `FG → [(SF, ratio)]` 的一层映射。`parent_task_id` 也只有一层。

**差距**：

- 无法表达"原材料 → 半成品 → 成品"的三级链路（SY38 的"注塑素件 → 涂装 → 总装"至少是三级）；
- 无法表达半成品之间的上下游（例如冲压 → 焊接 → 总装）；
- 无法表达损耗率：`Scrap rate(%)` 读进来了（`data_loader.py:323`），但**从未参与计算**；
- 无法表达提前期：`生产提前期(days)` 读进来了（`data_loader.py:322`），也**从未参与计算**。

### 6.4 MRP 层：完全缺失

你要的"推到 MRP 层，实现原材料的数据判断"，当前只有一层很薄的"到货门控"：

```python
# scheduler.py:496-503  仅此而已
if arr_shift > 0:
    for s in range(lo, min(arr_shift, hi + 1)):
        model.Add(x[t.task_id, s] == 0)
```

即"关键物料到货前不许开工"。它没有：

| MRP 能力 | 现状 |
|---|---|
| 多级 BOM 展开（毛需求 → 净需求） | ❌ 只有 FG→SF 一层 |
| 原材料期初库存 | ❌ `Initial-Stock.csv` 只有 FING/SFIN |
| 采购在途（PO/ETA）按物料做库存平衡 | ❌ 只按"关联料号"做门控 |
| 安全库存 / 订货批量 / 最小包装 | ❌ 无 |
| 提前期（采购/生产/检验） | ❌ 字段读入未用 |
| 损耗率 / 良率 | ❌ 字段读入未用 |
| 替代料 / 替代供应商 | ❌ 无 |
| 齐套率 / 缺料清单 | ❌ 无 |
| 库存流水（期初 + 入库 − 领用 = 期末） | ⚠️ `StockMovement` 只是报表算术，不是模型变量 |
| 与排程的耦合（缺料导致延期） | ❌ 只有硬门控，没有"缺料 → 建议采购/改期" |

### 6.5 能力矩阵（现状 vs 目标）

| 能力 | 现状 | 目标 | 改造量 |
|---|---|---|---|
| 多工厂（>2） | 硬编码 2 套 | 数据驱动 N 套 | 中 |
| 工厂内多产线 | ✅ 结构支持 | 保持 | — |
| 一成品对多半成品线 | ✅ 已支持 | 保持 | — |
| 同一半成品多线供应 | ❌ | 路由表 + 分配变量 | 中 |
| 同一成品多线可选 | ❌ | 路由表 + `y[t,l]` | 大 |
| 多级 BOM（≥3 级） | ❌ | 通用 BOM 图 + 层级展开 | 大 |
| 原材料/MRP | ❌ | 物料库存平衡 + PO + 安全库存 | 大 |
| 跨厂共享前道 | ❌ | 顶层容量切片 | 大 |
| 多用户并发 | ❌ | 无状态 Worker + 快照版本 | 中 |

---

## 7. 差距总清单

> **P0** = 会产生错误计划或阻断你的核心要求，必须在任何试点前修复。
> **P1** = 影响正确性、可维护性或扩展性，试点期间必须排期。
> **P2** = 工程完备性，可与功能迭代并行。

### A. 模型与求解（19 项）

| ID | 级别 | 问题 | 证据 | 影响 |
|---|---|---|---|---|
| G-01 | P0 | `end_t` 只是下界，未与最后开工班次绑定 | `scheduler.py:454-457`；实测 R-WL-12 判 FAIL | 顺位/拖期/完工目标全部失真 |
| G-02 | P0 | 95% 只在已开工班次内判定，空班零成本 | `scheduler.py:679-701`；`validator.py:203-229`；实测 74.5% vs 97.1% | 核心 KPI 不可证伪 |
| G-03 | P0 | 换型工时未进容量；校验算出却不用 | `scheduler.py:656-664`；`validator.py:133-152` | 现场必然超载 |
| G-04 | P0 | 工单可被切成任意不相邻班次 | `scheduler.py:436-443`；实测 WB35356 断续 | 违反 R-WL-09 |
| G-05 | P0 | 单批工时 > 班次工时时结构性无解 | `scheduler.py:429-435`；WA14208S = 8.45h | 一有需求即 INFEASIBLE |
| G-06 | P0 | 排产首班被无条件锁死 | `scheduler.py:637-638`；实测 PGLINKAGE 首班空 | 浪费整班产能 |
| G-07 | P1 | 尾班豁免未限"至多一个" | `scheduler.py:679-701` | 可被滥用 |
| G-08 | P1 | 目标量纲混用、常量无标定 | `scheduler.py:45-59, 531-591` | 优化方向不可控 |
| G-09 | P0 | `num_workers=8` 导致非确定性 | `scheduler.py:373-379`；实测两次结果不同 | 不可复现、不可审计 |
| G-10 | P1 | 降级由搜索运气触发 | `scheduler.py:198-236`；实测 run1 降级 | 服务水平随机波动 |
| G-11 | P1 | 无库存变量，库存类规则无对应项 | `scheduler.py` 全文 | R-WL-22/23、R-SY-06/07/09 无法实现 |
| G-12 | P1 | 无班内顺序与顺序相关换型 | `scheduler.py:536-559` | SY38 换色/共模不可表达 |
| G-13 | P1 | 齐套只有下界无上界 | `scheduler.py:640-644` | 半成品可无限堆积 |
| G-14 | P1 | 半成品无拖期代价 | `scheduler.py:515-529` | 断线风险不可量化 |
| G-15 | P1 | 需求守恒硬等式，无缺口/委外变量 | `scheduler.py:443` | 产能不足只能报错 |
| G-16 | P2 | 拖期窗口硬编码 6 班 | `scheduler.py:59` | 不可配 |
| G-17 | P2 | 时间标度固定，无精度校验 | `data_loader.py:798-803` | 长周期累积误差 |
| G-18 | P2 | 未用 LNS/决策策略/对称破除 | `scheduler.py:412-457` | 规模化受限 |
| G-19 | P2 | 预算分配为经验值 | `scheduler.py:205, 216, 252` | 解质量随预算波动 |

### B. 独立验证（11 项）

| ID | 级别 | 问题 | 证据 | 影响 |
|---|---|---|---|---|
| G-20 | P0 | 校验器复用求解器数据结构与派生量 | `validator.py:14-17, 89-91` | 同源错同源，不具独立性 |
| G-21 | P0 | 漏检"整行删除" | 实测：删 1 FG + 2 SF 后 OTD 仍 7/7、守恒 100% | 手工审计抓不到最该抓的错 |
| G-22 | P0 | 4 项恒真：冻结期、受控窗口、跨班连续、日历 | `validator.py:857-899, 902-935, 660-755, 1128-1194` | 虚假合规 |
| G-23 | P0 | 守恒/库存检查自证 | `validator.py:258-323, 550-596` | 净需求错误不可见 |
| G-24 | P1 | 规则→检查映射错位/缺失 | `validator.py:1080-1125`；R-WL-16~19 无检查仍 PASS | 矩阵误导 |
| G-25 | P1 | SY38 检查质量低 | `validator.py:1301-1323` 只判 ≥0h；`_check_lr_sync_sy` 硬编码料号 | 12h 缓冲规则未实现 |
| G-26 | P1 | 校验口径硬编码 8h | `validator.py:102, 125` 用 `SHIFT_SECONDS` | SY38/新工厂口径错 |
| G-27 | P1 | 手工审计不是独立复算 | `server.py:1636-1908` | 审计结果不可信 |
| G-28 | P1 | 测试自证：断言 `failed == 0` | `test_aps_pipeline.py:83-85` | 回归无保护 |
| G-29 | P2 | 无模型级不变量测试 | 全库无 | 约束正确性无保障 |
| G-30 | P2 | 未实现规则默认显示 PASS | `validator.py:1486-1493`；`rules.py:119` | 合规率虚高 |

### C. 规则可配置化（9 项）

| ID | 级别 | 问题 | 证据 | 影响 |
|---|---|---|---|---|
| G-31 | P0 | 规则是散文，无 Schema/参数/阈值 | `rules.py:46-61` | 无法机器执行、无法配置 |
| G-32 | P1 | 规则 ID 由 Excel 行序生成 | `rules.py:93-100` | 插行即错位 |
| G-33 | P0 | 规则→求解器开关是硬编码 if 链 | `server.py:1211-1237`；`scheduler.py:114-140` | 改规则要改代码 |
| G-34 | P0 | 权重只展示不生效 | grep `.weight`；`rules.py` 5 条有权重 | UI 误导，不可配置 |
| G-35 | P0 | 第三工厂需改 5 处代码 | `rules.py:77,93`；`shift_manager.py:84`；`validator.py:44`；`server.py:1052`；前端 | 无法规模化接入 |
| G-36 | P1 | 开关映射引用不存在的 legacy ID | `server.py:1226, 1230`；WL 无 R-FG-13 | Web 路径 WL 不启用并行型号约束 |
| G-37 | P1 | 关键参数散落为常量 | 95%/105%、30min、6 班、35%、3 型号 | 工厂无法自助调整 |
| G-38 | P1 | 无规则版本/生效期/变更审计 | 无 | 无法治理 |
| G-39 | P2 | 规则与主数据无联合校验（BOM 孤儿/缺 CT/倒挂交期） | 仅文档提及 | 脏数据进模型 |

### D. 分组与优先级（5 项）

| ID | 级别 | 问题 | 证据 | 影响 |
|---|---|---|---|---|
| G-40 | P0 | 无"规则组"实体 | `rules.py` 只有 category/nature | 无法按组启停/治理 |
| G-41 | P0 | 无优先级层级，单一加权和 | `scheduler.py:531-591` | 业务优先级不可表达 |
| G-42 | P1 | 组间冲突无仲裁机制 | 靠常量大小与档位顺序 | 冲突时结果不可解释 |
| G-43 | P1 | 客户分级权重硬编码 10/3/1 | `scheduler.py:526` | 主数据 `priority_weight` 未用 |
| G-44 | P2 | 无组级 UI/API | 前端 27 条平铺 | 计划员无法按组操作 |

### E. 扩展性（12 项）

| ID | 级别 | 问题 | 证据 | 影响 |
|---|---|---|---|---|
| G-45 | P0 | 料号一对一绑定产线 | `data_loader.py:911`；`ProductParam.line` | 不支持替代路由/多线可产 |
| G-46 | P0 | 只有两级 BOM，无原料层 | `data_loader.py:931-974` | 无法推到 MRP |
| G-47 | P0 | 工厂硬编码，未知站点回退 WL | `shift_manager.py:84-87` | 新工厂静默按 8h 排 |
| G-48 | P1 | 无工厂/车间/产线/路由实体 | `Site` 只是 CSV 列 | 数据模型无法演进 |
| G-49 | P1 | 单进程全局 session | `server.py:216-231` | 多用户/多站点互相覆盖 |
| G-50 | P1 | 导出按 3 班×4 列布局 | `exporter.py:27, 447-452, 537-539` | SY38 出现空列、利用率按 8h 算 |
| G-51 | P1 | 无跨厂共享资源/分层协调 | 仅文档主张 | 集团级排产不可行 |
| G-52 | P1 | MRP 层缺失（库存/PO/批量/提前期） | `scheduler.py:496-503` 仅门控 | 原材料无法参与决策 |
| G-53 | P1 | 无订单类型（正式/预测/波动） | `CustomerOrder` 无 type | R-WL-24 拉单窗口无法实现 |
| G-54 | P2 | 30 天前瞻不合并工单 | `server.py:155-192` | "合批"名不副实，反而增换型 |
| G-55 | P2 | 半成品安全库存按天给出时按 0 处理 | `data_loader.py:829-833` | 安全库存长期缺失 |
| G-56 | P2 | PM 只整班清零，无切片/挂起/恢复 | `scheduler.py:506-513` | 跨 PM 工单被强迫整班避让 |

### F. 工程与运维（6 项）

| ID | 级别 | 问题 | 证据 | 影响 |
|---|---|---|---|---|
| G-57 | P1 | 无运行指纹（快照哈希/规则版本/求解配置/计划哈希） | 无 | 不可复现、不可审计 |
| G-58 | P1 | 无模型约束的单元测试 | `test_aps_pipeline.py` | 约束正确性无保护 |
| G-59 | P1 | 单文件巨石（server 2,131 行 / HTML 5,217 行） | — | 维护成本高 |
| G-60 | P2 | 文档与实现漂移（39/36/27；17 项只列 14 项；E3 vs E8） | Specification 全文 | 决策依据不可靠 |
| G-61 | P2 | xlsx 既是主数据又是输出 | `main.py:106-107` | 无迁移/回滚 |
| G-62 | P2 | 大量 `except Exception: pass` | `data_loader.py:548, 859, 882` | 数据错误静默 |

### G. 人力资源（4 项）

| ID | 级别 | 问题 | 证据 | 影响 |
|---|---|---|---|---|
| G-63 | P0 | **Headcount 未进入模型**：无人员变量、无"同时开班人数上限"约束、无共享班组互斥 | `data_loader.py:24-39` 无字段；`scheduler.py` 全文无人员变量；`data/Parameters-FG.csv` 的 `Headcount` 列无人读取 | 多线同时开班的隐含人数无人校验；100 条线时计划不可执行 |
| G-64 | P1 | **人力未进入目标**：无人力工时成本、无峰值人数、无人力平滑 | `scheduler.py:531-591` 目标项无人员；`exporter.py`/`balance_builder.py` 无人力工时列 | 无法权衡"少开班省人"与"多开班保交付"；无法回答人均产出 |
| G-65 | P1 | **人力主数据缺失/不一致**：轴套6 无 Headcount；PU22 同线 12/15 两种值；两表共 13 行脏数据 | `data/Parameters-SFN.csv` 轴套6 全空；`Parameters-FG.csv` PU22 12/15；两表末尾 12 空行 + 1 说明行 | 约束无法建立；即便建立也不可信 |
| G-66 | P2 | **高级人力模型缺失**：班组共享、技能矩阵、多能工、加班、换型/PM 瞬时人力峰值 | 全库无相关字段与模型 | 现场"借人/顶岗/加班"的可行性无法评估 |

> 这 4 项是本次补充（2026-10-25），因此总数从 62 变为 **66**。对应整改任务：**ACT-L1～L4**（见行动计划 §3/§4/§5 与 §6.9）。

---

## 8. 目标架构建议

### 8.1 分层架构

```
┌────────────────────────────────────────────────────────────────┐
│  接入层   REST / 批量 / 定时     ← 无状态，按 site+horizon 隔离   │
├────────────────────────────────────────────────────────────────┤
│  应用层   SolveOrchestrator（快照 → 预检 → 编译 → 求解 → 校验）  │
├────────────────────────────────────────────────────────────────┤
│  规则层   RuleSet → RuleInstance → Compiler → CP-SAT 项          │
│           （工厂只改 RuleSet YAML）                              │
├────────────────────────────────────────────────────────────────┤
│  模型层   固定算子内核 Kernel（15 个算子，代码冻结）              │
│           capacity / batch / changeover / material / due / ...   │
├────────────────────────────────────────────────────────────────┤
│  数据层   Site / Line / Routing / BOM / Inventory / Demand / PO  │
│           每个实体有版本、有快照哈希                              │
├────────────────────────────────────────────────────────────────┤
│  验证层   独立校验器（不 import scheduler，不 import solver）      │
│           完整性 / 可行性 / 规则符合性 / 变异测试                 │
└────────────────────────────────────────────────────────────────┘
```

### 8.2 固定内核 + 可配置规则（落地接口）

```python
# kernel/operators/capacity.py  —— 代码冻结，随产品版本升级
class CapacityOperator(Operator):
    template_id = "capacity_utilization"

    def compile(self, ctx: CompileContext, rule: RuleInstance) -> None:
        for line in ctx.resolve_lines(rule.scope):
            for slot in ctx.resolve_slots(rule.scope):
                load = ctx.load_var(line, slot)          # Σ x·lot_ms + setup
                if rule.nature is Nature.HARD:
                    ctx.model.Add(load >= rule.p["min_util"] * ctx.capacity(line, slot))
                    ctx.model.Add(load <= rule.p["max_util"] * ctx.capacity(line, slot))
                else:
                    gap = ctx.new_int_var(0, ctx.capacity(line, slot), f"gap.{line}.{slot}")
                    ctx.model.Add(gap >= rule.p["min_util"] * ctx.capacity(line, slot) - load)
                    ctx.add_penalty(gap, rule.weight, rule.group)
```

**固定的是 `compile` 的形状与算子的语义；可变的是 `rule.p`（参数）、`rule.nature`（软硬）、`rule.weight`（权重）、`rule.scope`（作用域）、`rule.group`（分组）。**

### 8.3 数据模型（需要新增的实体）

```sql
site(site_id, name, timezone, calendar_template_id)
line(line_id, site_id, stage, shift_hours, shifts_per_day, rated_capacity, calendar_id)
routing(part_no, line_id, priority, ct_seconds, trp, mpq, capacity_share, min_run)  -- 关键：N:1
part(part_no, type, uom, safety_stock, scrap_rate, lead_time_days)
bom(parent_part, child_part, ratio, level, scrap_rate, lead_time_days)              -- 关键：多级
inventory_snapshot(snapshot_id, part_no, site_id, qty, as_of)
demand(order_id, part_no, due_dt, qty, order_type, priority, customer_id)
purchase_order(po_id, part_no, qty, eta_dt, qc_release_hours, status)
tooling(mold_id, available_qty, changeover_min, life_strokes)
tooling_line(mold_id, line_id)
pm_plan(line_id, start_slot, end_slot, capacity_rate)
labor_availability(site_id, work_date, shift_name, available_headcount, source)     -- 人力供给
line_staffing(line_id, part_no, crew_required, extra_on_changeover, skill_code)     -- 人力需求
crew_pool(pool_id, site_id, headcount, member_lines)                                -- 共享班组
rule_set(rule_set_id, site_id, version, effective_from, effective_to, status)
rule_instance(rule_set_id, rule_id, template_id, scope_json, nature, weight, group_id, priority, params_json, enabled)
rule_group(rule_set_id, group_id, priority, mode)
plan_run(run_id, snapshot_id, rule_set_id, solver_config, seed, plan_hash, objective, created_at)
```

**最关键的四张表**：`routing`（解决多线可产/替代产线）、`bom` 带 `level`（解决多级 BOM）、`rule_instance` + `rule_group`（解决规则可配置 + 分组优先级）、`labor_availability` + `line_staffing`（解决人力资源约束）。

### 8.4 求解器工程规范（可复现）

```python
solver.parameters.num_workers = 1              # 可复现的前提
solver.parameters.random_seed = 20260101
solver.parameters.max_time_in_seconds = 30
solver.parameters.log_search_progress = False
solver.parameters.cp_model_presolve = True
# 运行后落库：
plan_run.plan_hash = sha256(canonical(schedule))
plan_run.snapshot_hash = sha256(canonical(snapshot))
plan_run.rule_set_version = rule_set.version
plan_run.solver_version = "cp-sat 9.15.6755"
```

**建议**：把"确定性"当作一等公民。宁可慢 20%，也要可复现——生产计划不能用"今天排不出来"来解释。

### 8.5 独立校验器（重构要点）

| 要求 | 做法 |
|---|---|
| 不 import 求解器 | 校验器只依赖 `Snapshot` + `RuleSet` + `ScheduleRow[]`，自己重算 `lot_millis`、`shift_seconds`、`net_requirement` |
| 完整性优先 | 第一条检查就是"每条需求要么被库存覆盖、要么有排产任务；每个任务都被排程；班次拆解之和 = 任务量" |
| 口径独立 | 95% 的分子分母、换型工时、BOM 展开都用校验器自己的实现，与求解器**故意不同**（例如用 Decimal 而非 float） |
| 可证伪 | 每个 check 配一个"反例样例"：注入违规后必须 FAIL。用变异测试（mutation testing）保证检查有效 |
| 可解释 | 每个 FAIL 输出：违反的规则实例 ID、涉及的任务/班次、差额、建议动作 |

**必加的 5 条完整性检查**（当前全部缺失）：

1. 需求覆盖完整性（`Σ 排产 + 库存覆盖 = 毛需求`）
2. 任务排程完整性（`plan.tasks ⊆ result.tasks`）
3. 班次拆解完整性（`Σ_s x[t,s] = Q_t`，且 `x` 为整数）
4. 时间连续性（同一工单的 active 班次连续，或块数 ≤ 配置上限）
5. 容量物理性（`Σ 加工 + Σ 换型 ≤ 班次工时`，用 `effective_seconds`）

### 8.6 多工厂部署形态

- **不要**把多个工厂放进一个 CP-SAT 模型；
- 每个 `(site, family)` 一个求解任务，无状态 Worker 池执行；
- 跨厂只做"共享前道资源"的顶层容量切片（顶层 LP/CP，分钟级），把切片结果作为各厂的外部产能参数；
- 结果按 `plan_run` 版本化存储，支持"对比两版计划"与"回滚"。

---

## 9. 演进路线

### 阶段 A：止血（2～3 周，不改变架构）

目标：让现有模型"不产出错误计划"。

1. 修 G-01（`end` 上界绑定）、G-06（首班锁死）、G-04（连续性）、G-03（换型工时入容量）；
2. 修 G-21（校验器完整性：任务缺失必须 FAIL）、G-22（4 项恒真改成真检查）；
3. 修 G-34（权重至少进入目标函数）、G-09（`num_workers=1`）；
4. 修 G-02 的口径：先和业务确认 95% 的分母，再决定是"日历口径"还是"开工口径"；
5. 加 G-05 的 sub-lot slicing，消除潜在 INFEASIBLE；
6. 补 G-65 的人力主数据贯通与人力工时 KPI（只做数据与报表，约束留到阶段 B 的 `labor_capacity` 算子）。

**验收**：同一输入连续 5 次运行结果一致；删除任一工单校验必须 FAIL；随机注入 100 个违规，校验器召回率 100%。

### 阶段 B：规则化（6～8 周）

目标：把"规则"从代码里搬出来。

1. 定义 19 个约束算子的模板 Schema（§4.4）；
2. 把 `scheduler.py` 的 15 个布尔开关改写成"规则实例 → 编译器 → 模型项"；
3. 引入 `rule_set / rule_instance / rule_group` 三张表 + YAML 导入；
4. 实现分组优先级（字典序求解，§5.3）；
5. 前端改成"按组展示、按组启停、单条可编辑软硬/权重/阈值"。

**验收**：新工厂接入 = 新增一份 YAML + 一份主数据，零代码改动；工厂改权重后结果可解释地变化（有 diff 报告）。

### 阶段 C：扩展（8～12 周）

目标：多厂多线 + MRP。

1. `routing` 表 + `y[t,l]` 分配变量（多线可产）；
2. 多级 BOM + 原材料库存/PO/安全库存的物料平衡模型；
3. 按 `(site, family)` 分解 + 无状态 Worker；
4. 跨厂共享前道的顶层切片；
5. 运行指纹、计划版本、对比与回滚。

**验收**：3 个工厂 × 5 条线 × 30 天，单厂求解 ≤ 60s；MRP 层能回答"某个原材料晚到 2 天，会影响哪些订单"。

### 优先级建议（如果只能做三件事）

1. **修 G-01 + G-02 + G-03 + G-04**（模型语义）——否则计划不可执行；
2. **修 G-21 + G-22 + G-20**（独立验证）——否则你无法知道计划对不对；
3. **做 G-31 + G-33 + G-34 + G-40/41**（规则内核化）——否则每接一个工厂都是一次定制开发。

---

## 10. 附录

### 10.1 本次核验方法与复现

| 实验 | 方法 | 结论 |
|---|---|---|
| 真实运行 | `python src/main.py`（10 天视区） | 16/17 通过；R-WL-12 FAIL；53.2s |
| 确定性 | 同一输入连续两次 `solve(15s)` | 两次档位、目标值、拖期均不同 |
| 删单漏检 | 删除 1 FG + 2 SF 后重跑 `validate_schedule` | OTD 仍 100%、守恒 100% |
| 数据探针 | 遍历 `data/*.csv` 计算单批工时/产线/共用关系 | WA14208S = 8.45h > 8h |
| 规则台账 | `get_rules_for_site()` | WL 27 条（5 条有权重）、SY38 9 条（5 条有权重） |
| 权重生效性 | grep `.weight` 全库 | 只用于 UI/JSON，求解器未读 |

> 复现脚本放在 `%TEMP%\aps_*.py`，未写入仓库。

### 10.2 关键常量与参数位置（改造时逐个迁出）

| 常量 | 值 | 位置 | 应迁往 |
|---|---|---|---|
| `CHANGEOVER_WEIGHT` | 300,000 | `scheduler.py:57` | 规则实例 `weight` |
| `CONCURRENCY_WEIGHT` | 50,000 | `scheduler.py:58` | 规则实例 `weight` |
| `FG_COMPLETION_WEIGHT` | 1 | `scheduler.py:52` | 组内权重 |
| `LATENESS_SLACK_SHIFTS` | 6 | `scheduler.py:59` | 规则参数 |
| 换型时间 | 1,800s | `validator.py:133` | 主数据 `tooling.changeover_min` |
| 95% / 105% | 0.95 / 1.05 | `shift_manager.py:18` | 规则参数 |
| 并行型号上限 | 3 | `scheduler.py:551`；`validator.py:19` | 规则参数 |
| CV 门槛 | 35% | `validator.py:1161` | 规则参数 |
| 前瞻 30 天 / 拉单 10 天 | 30 / 10 | `server.py:84-86` | 站点策略 |
| 冻结期 | 前 3 班 | `server.py:1896` | 规则参数 |
| 求解预算 | 90s | `main.py:39` | 站点策略 |

### 10.3 评估边界与声明

- 本报告**没有修改任何代码**；为取证运行过 CLI 与探针脚本，`dataSource.xlsx` 的 `Production plan-Done` 回写已用 `git checkout` 还原，工作区 `git status` 仅剩两个与本次无关的未跟踪 CSS 文件。
- 本报告**没有**对 SY38 做完整端到端运行（其校验逻辑通过代码审读评估），SY38 侧的结论以静态证据为主。
- 本报告**未评估**前端交互性能、浏览器兼容性、部署运维（Docker/CI）等非架构项。
- 文档层的一致性（39 vs 36 vs 27、17 项只列 14 项、E3 vs E8）已在 `Specification/` 摘要中记录，本报告只在与实现相关处引用。

### 10.4 一句话给决策者

> 现有系统是一个**优秀的业务原型**，但它现在的"95% 利用率 100% 交付 39 条规则全 PASS"是**叙事层的结果，不是模型层的结果**。在做任何工厂试点之前，先把"模型语义、独立校验、规则内核"这三件事做实；否则试点会把 MVP 的问题放大成生产事故。
