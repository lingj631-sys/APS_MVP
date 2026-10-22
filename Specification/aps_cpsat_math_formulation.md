# APS CP-SAT 运筹求解引擎原理与数学建模规范

本文档详尽阐述了 APS_MVP 系统的核心运筹算法引擎——基于 **Google OR-Tools CP-SAT** 的离散班次槽位装箱与两阶段物料流动态平衡的底层原理、数学建模方程体系及工程映射。

---

## 一、CP-SAT 求解引擎底层机理

传统制造业排产通常在 **MIP（混合整数规划）** 与 **CP（约束规划）** 之间做艰难权衡：
* **MIP（如 Gurobi/Cplex）**：基于单纯形法与分支定界，长于处理连续线性松弛；但在处理复杂逻辑（如“跨产线排他独占”、“工序前道产出才允许后道开工”、“班次低于95%扣罚”）时，必须引入大量大 $M$ 法辅助变量，松弛界极其松散，容易陷入组合爆炸。
* **CP（传统约束编程）**：基于约束传播与变量域缩减，长于表达复杂离散逻辑，但缺乏全局数学对偶下界（Dual Bound）指引，难以在大规模解空间中进行方向性剪枝。

**CP-SAT（Constraint Programming + Satisfiability）** 融合了约束编程与现代 SAT 求解器的核心技术，是目前国际运筹学公认处理工业调度最强悍的技术路线之一。

```text
                        CP-SAT 核心解算引擎机理
┌────────────────────────────────────────────────────────────────────────┐
│ 1. 纯整型标度 (Integer-Only Scale)                                     │
│    • 引擎内核完全基于 64 位整数运算，消除了浮点精度误差与舍入漂移。   │
│    • 系统中所有节拍、工时与效率全部按 TIME_SCALE=1000 放大为整型毫秒。 │
├────────────────────────────────────────────────────────────────────────┤
│ 2. 约束传播与定义域缩减 (Constraint Propagation & Domain Pruning)      │
│    • 某个工单决定在第 s 班次排产后，算法自动推导其前序半成品排程范围， │
│      瞬间将不可能的取值从其他决策变量的定义域中剔除。                  │
├────────────────────────────────────────────────────────────────────────┤
│ 3. 冲突驱动子句学习 (CDCL - Conflict-Driven Clause Learning)           │
│    • 当搜索陷入局部不可行（如模具冲突或容量超载）时，SAT 引擎自动反推  │
│      冲突极小割集，生成一条全局合规“冲突子句”永久记录，杜绝重复踩坑。   │
├────────────────────────────────────────────────────────────────────────┤
│ 4. 线程分工与大邻域局部搜索 (Portfolio Search & LNS)                   │
│    • 多 Worker 并行竞争：部分线程负责松弛寻界，部分线程深挖整数解；    │
│    • 结合 LNS（冻结 80% 变量，重排 20% 变量）在毫秒内完成局部密集优化。│
└────────────────────────────────────────────────────────────────────────┘
```

---

## 二、数学建模体系与形式化定义 (Mathematical Formulation)

我们将多基地（温岭 WL、沈阳 SY38）双工序协同排产抽象为：**多机器、多资源约束、具备级联提前期流水齐套的离散时间槽混合整数优化模型（Multi-Resource Discrete Shift-Slot Flow Model with Kitting）**。

### 1. 集合与索引 (Sets & Indices)

| 符号 | 业务物理定义 | 示例取值 |
| :--- | :--- | :--- |
| $L$ | 产线集合 | $l \in \{\text{PGLINKAGE}, \text{轴套6}\}$ (WL) 或 $\{\text{PU07}, \text{1400T-6}\}$ (SY38) |
| $S = \{0, 1, \dots, N-1\}$ | 离散排产班次槽位集合 | 10 天视区下 WL 为 30 个班次，SY38 为 20 个班次 |
| $T = T_{\text{FG}} \cup T_{\text{SF}}$ | 生产任务集合 | $T_{\text{FG}}$ 为产成品任务，$T_{\text{SF}}$ 为自制半成品任务 |
| $M$ | 工装模具物理实体集合 | $m \in M$（如冲压双模 `MOLD_WL_AXLE_01`） |
| $P$ | 物料料号集合 | $p \in P$ |

### 2. 核心模型参数 (Parameters)

| 符号 | 参数含义 | 单位 / 形式 |
| :--- | :--- | :--- |
| $C_l$ | 产线 $l$ 单班额定容量上限 | 整型毫秒（WL 为 $28,800,000\text{ms}$，SY38 为 $43,200,000\text{ms}$） |
| $C_l^{\max}$ | 产线 $l$ 单班弹性容量上限 | 若允许 105% 弹性，为 $1.05 \times C_l$，否则为 $C_l$ |
| $C_l^{\min}$ | 产线 $l$ 单班 95% 负荷门槛 | $0.95 \times C_l$（整型毫秒） |
| $Q_t$ | 任务 $t$ 的总计划批数 ($\text{lots}$) | 正整数 |
| $lot\_size_t$ | 任务 $t$ 单批包含的件数 | 成品为 MPQ 整倍数，半成品为 1 件 |
| $p_t$ | 任务 $t$ 单批纯加工时间 ($\text{lot\_millis}$) | 毫秒：$\text{round}\left(\frac{lot\_size_t \times CT}{\text{TRP}} \times 1000\right)$ |
| $due_t$ | 任务 $t$ 承诺客户交付的截止班次序号 | 整数 $\in [0, N-1]$，对齐早班截单线 |
| $BOM_{t, c}$ | 成品 $t$ 消耗半成品 $c$ 的配比系数 | 浮点数/整数 |
| $Stock_c$ | 半成品任务 $c$ 对应料号的期初在库物理库存 | 非负实数 |
| $PM_l$ | 产线 $l$ 计划性停机预防维保的班次索引集合 | $PM_l \subset S$ |
| $ETA_t$ | 任务 $t$ 所需关键物料预计到货质检放行班次 | 整数 $\in S$ |

### 3. 决策变量 (Decision Variables)

* **$x_{t, s} \in \mathbb{N}_{\ge 0}$**：**核心产出决策**，任务 $t$ 在班次 $s$ 分配生产的批次数。
* **$a_{t, s} \in \{0, 1\}$**：**开工指示变量**，指示任务 $t$ 在班次 $s$ 是否开工排产（$1$ 为开工，$0$ 为未开工）。
* **$end_t \in [0, N-1]$**：**完工班次变量**，任务 $t$ 最后一批完成产出的班次索引。
* **$late_t \in \mathbb{N}_{\ge 0}$**：**交付延误班次数**，任务 $t$ 完工晚于客户交期承诺的差额班次数。
* **$load_{l, s} \in [0, C_l^{\max}]$**：**班次纯加工工时**，产线 $l$ 在班次 $s$ 投入的纯加工总毫秒数。
* **$run_{l, s} \in \{0, 1\}$**：**产线开机变量**，产线 $l$ 在班次 $s$ 是否存在排产（开机为 1，停机为 0）。
* **$tail_{l, s} \in \{0, 1\}$**：**尾班豁免指示变量**，指示班次 $s$ 是否为该产线在整个计划期内的最后一个生产班次。
* **$gap_{l, s} \in \mathbb{N}_{\ge 0}$**：**负荷率缺口变量**，班次 $s$ 未达到 95% 门槛的赤字毫秒数。

---

### 4. 核心业务约束方程组 (Constraints)

#### ① 需求闭环守恒约束 (Demand Conservation)
每个工单任务的所有生产批次，必须在计划期有效窗口 $[lo_t, hi_t]$ 内 100% 封闭排完，严禁多排或漏排：
$$\sum_{s=lo_t}^{hi_t} x_{t, s} = Q_t \quad \forall t \in T$$

#### ② 单班最小起投批量与开工联动 (Semi-Continuous / Big-M)
当班次开工时，产出量必须受单班最小包装量（MPQ）限制，杜绝零星碎片排产：
$$x_{t, s} \ge min\_lots_t \cdot a_{t, s} \quad \forall t \in T, \forall s \in S$$
$$x_{t, s} \le per\_shift_t \cdot a_{t, s} \quad \forall t \in T, \forall s \in S$$

#### ③ 完工班次捕捉与准时交付保障 (Makespan & OTD)
完工班次必须不小于该任务所有生产批次所在的班次：
$$end_t \ge s \cdot a_{t, s} \quad \forall t \in T, \forall s \in [lo_t, hi_t]$$
在**硬约束准时交付**模式下，强制施加：
$$end_t \le due_t \quad \forall t \in T_{\text{FG}}$$
在**软约束交付损失最小化**模式下，允许适度延期并记录滞后班次数：
$$late_t \ge end_t - due_t, \quad late_t \ge 0 \quad \forall t \in T_{\text{FG}}$$

#### ④ 两工序流水动态平衡齐套约束 (Material Balance / JIT Flow)
**制造业两阶段流水精髓**：半成品第 $s-1$ 班及以前的累计产出加上在库期初库存，必须大于等于成品第 $s$ 班装配的累计消耗量：
$$\sum_{\tau=0}^{s} x_{t, \tau} \cdot lot\_size_t \cdot BOM_{t, c} \le \sum_{\tau=0}^{s-1} x_{c, \tau} \cdot lot\_size_c + Stock_c \quad \forall s \in S, \forall c \in \text{Children}(t)$$
*该约束保证冲压/注塑件在第 $s-1$ 班下线后，第 $s$ 班即可投入总装/喷涂，实现前后道无缝叠加密接。*

#### ⑤ 顺位紧邻前拉全序约束 (Sequential Pulling - R-WL-12 / R-SF-02)
同一产线、相同料号的不同工单，按客户交付期严格建立全序依赖链，严禁跳序插单：
$$end_{t_A} \le end_{t_B} \quad \forall (t_A, t_B) \text{ on same } (line, part) \text{ where } due_{t_A} < due_{t_B}$$

#### ⑥ 工装模具物理跨线独占约束 (Tooling Exclusivity - R-WL-01)
同一套模具在同一个班次内，严禁被多条产线并发占用：
$$\sum_{t \text{ uses mold } m} a_{t, s} \le available\_qty_m \quad \forall m \in M, \forall s \in S$$

#### ⑦ 设备计划性预防维保（PM）停机硬避让 (Downtime Maintenance - R-WL-27)
在产线检修维保班次，产线有效产能硬性清零：
$$x_{t, s} = 0 \quad \forall s \in PM_l, \forall t \text{ where } line(t) = l$$

#### ⑧ 关键在途物料预计到货（ETA）齐套约束 (Material Arrival - R-WL-21)
在采购在途件质检放行班次之前，工单严禁提前开工：
$$x_{t, s} = 0 \quad \forall s < ETA_t$$

#### ⑨ 班次额定容量与 95%~105% 黄金负荷区间控制 (Capacity & Utilization)
产线在班次 $s$ 的总加工耗时等于各项任务批次耗时之和：
$$load_{l, s} = \sum_{t \in T, line(t)=l} x_{t, s} \cdot p_t \le C_l^{\max} \quad \forall l \in L, \forall s \in S$$
施加 95% 负荷率门槛，结合**尾班豁免机制**（$tail_{l, s}=1$ 免于考核）：
$$load_{l, s} + C_l^{\min} \cdot tail_{l, s} \ge C_l^{\min} \cdot run_{l, s} - gap_{l, s} \quad \forall l \in L, \forall s \in S$$

---

### 5. 目标函数与三阶分层求解体系 (Hierarchical Objectives)

车间排产涉及**准时交付、设备负荷均衡、换模停机最小化**三大冲突目标。系统采用**词典序分层求解策略（Lexicographic Tiers）**：

$$\min \quad Z = W_{\text{late}} \cdot \sum_{t \in T_{\text{FG}}} (w_{\text{tier}(t)} \cdot late_t) + W_{\text{util}} \cdot \sum_{l, s} gap_{l, s} + W_{\text{excess}} \cdot \sum_{l, s} over_{l, s} + W_{\text{chg}} \cdot \sum \text{换型惩罚}$$

```text
               三阶分层求解执行流水线 (Three-Tier Pipeline)
┌────────────────────────────────────────────────────────────────────────┐
│ 第 1 阶：种子可行预热 (Hint Tier - 5% 时限)                             │
│   • 目标: Min(0)                                                       │
│   • 仅校验基础物理容量与交期边界，5 秒内为 CP-SAT 快速提供首个合规拓扑。│
├────────────────────────────────────────────────────────────────────────┤
│ 第 2 阶：高保真排产求解 (Main Solve Tier - 95% 负荷约束)                │
│   • 优先权: 准时交付 (W_late=10^8) > 95% 负荷达标 (W_util=10^4)       │
│   • 驱动各产线负荷自动向 95%~100% 黄金区间紧凑收敛，实现尾班智能豁免。│
├────────────────────────────────────────────────────────────────────────┤
│ 第 3 阶：后置换型集中压缩 (Compaction Tier - 后置贪心优化)              │
│   • 冻结第 2 阶求得的交付与 95% 达标状态（gap_cap 封顶）；              │
│   • 引入相邻班次开工相关性，自动将跨班次分散的小批次合并为大生产块，    │
│     显著消除多余换模停机。                                             │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 三、代码工程落地与映射索引 (Code Mapping)

| 数学建模概念 | 代码文件 | 核心实现函数 / 变量名 |
| :--- | :--- | :--- |
| **纯整型标度** | `src/data_loader.py` | `TIME_SCALE = 1000`, `lot_millis(...)` |
| **工厂拓扑接缝** | `src/shift_manager.py` | `PlantTopology`, `WL_TOPOLOGY`, `SY38_TOPOLOGY` |
| **决策变量集合** | `src/scheduler.py` | `x[task_id, s]`, `active[task_id, s]`, `end_of[task_id]` |
| **需求封闭守恒** | `src/scheduler.py` | `_build_model` 中的 `model.Add(sum(x) == task.lots)` |
| **流水齐套平衡** | `src/scheduler.py` | `_add_material_balance(...)` |
| **全序顺位前拉** | `src/scheduler.py` | `_build_model` 中按 `sorted_dues` 分组添加链式约束 |
| **95% 负荷与尾班** | `src/scheduler.py` | `_add_line_capacity(...)` 中的 `tail` 与 `gap` 变量 |
| **种子与分层求解** | `src/scheduler.py` | `_build_hint(...)`, `_solve_tier(...)`, `_solve_compact(...)` |
| **产销推移总账** | `src/balance_builder.py` | `build_validation_matrix(...)` |
| **独立合规验证器** | `src/validator.py` | `validate_schedule(...)` 中的 17 项 (WL) 与 9 项 (SY38) 独立断言 |
