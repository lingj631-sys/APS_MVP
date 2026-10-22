# APS_MVP 系统缺陷与架构问题清单

本报告基于端到端自动化测试套件（`test_aps_pipeline.py`）、代码库静态审计及运筹算法规范（`Specification/`）全面整理，涵盖系统运行崩溃、求解器模型缺陷、业务规则失配及中长期架构瓶颈。

---

## 目录
- [一、P0 级：系统崩溃与致命阻塞缺陷](#一p0-级系统崩溃与致命阻塞缺陷)
  - [1.1 FastAPI 后端启动闪退（NameError: Tuple 未导入）](#11-fastapi-后端启动闪退nameerror-tuple-未导入)
  - [1.2 CLI 管道无界视区求解超时不可行（RuntimeError）](#12-cli-管道无界视区求解超时不可行runtimeerror)
  - [1.3 APSScheduler 默认漏传 PM 维保参数导致 R-WL-27 校验失效](#13-apsscheduler-默认漏传-pm-维保参数导致-r-wl-27-校验失效)
- [二、P1 级：核心算法模型与工艺规则缺陷](#二p1-级核心算法模型与工艺规则缺陷)
  - [2.1 首班（Shift 0）组装被无条件锁死，忽视在库期初库存](#21-首班shift-0组装被无条件锁死忽视在库期初库存)
  - [2.2 净需求核算未补足期初安全库存缺口](#22-净需求核算未补足期初安全库存缺口)
  - [2.3 SY38 基地“12h×2班”规格与底层“8h×3班”引擎硬编码冲突](#23-sy38-基地12h2班规格与底层8h3班引擎硬编码冲突)
  - [2.4 分数 BOM 展开直接四舍五入导致齐套关系失真](#24-分数-bom-展开直接四舍五入导致齐套关系失真)
  - [2.5 换型调机损耗未从班次额定可用产能中扣减](#25-换型调机损耗未从班次额定可用产能中扣减)
  - [2.6 单批超班（>8h）导致 CP-SAT 离散装箱结构性无解](#26-单批超班8h导致-cp-sat-离散装箱结构性无解)
- [三、P2 级：工程规范、并发安全与演进架构短板](#三p2-级工程规范并发安全与演进架构短板)
  - [3.1 原工程完全缺失自动化测试与 CI 门禁](#31-原工程完全缺失自动化测试与-ci-门禁)
  - [3.2 后端全局单例会话状态（Race Condition 隐患）](#32-后端全局单例会话状态race-condition-隐患)
  - [3.3 多 Worker 并行搜索导致同输入排产非确定性波动](#33-多-worker-并行搜索导致同输入排产非确定性波动)
  - [3.4 “订单任务槽位模型”组合爆炸，缺乏增量热重排机制](#34-订单任务槽位模型组合爆炸缺乏增量热重排机制)
- [四、修复优先级与实施路线图](#四修复优先级与实施路线图)

---

## 一、P0 级：系统崩溃与致命阻塞缺陷

### 1.1 FastAPI 后端启动闪退（`NameError: Tuple` 未导入）
* **代码定位**：`src/server.py` 第 98 行
* **现象复现**：
  在终端执行 `python run_web.py` 时直接崩溃中断：
  ```text
  File "C:\Users\shan6\workspace\APS_MVP\src\server.py", line 98, in <module>
      ) -> Tuple[ProductionInputSnapshot, Dict[str, Any]]:
           ^^^^^
  NameError: name 'Tuple' is not defined. Did you mean: 'tuple'?
  ```
* **根本原因**：第 18 行 `from typing import Any, Dict, List, Optional` 遗漏了 `Tuple` 类型注解。
* **业务影响**：整个系统 Web 界面与 REST API 服务无法启动，导致系统完全不可用。
* **建议方案**：在 `src/server.py` 顶部导入中显式补全 `Tuple`（或升级为 Python 3.12 原生小写 `tuple[...]`）。

---

### 1.2 CLI 管道无界视区求解超时不可行（`RuntimeError`）
* **代码定位**：`src/main.py` 第 61~100 行
* **现象复现**：
  直接执行主控制台脚本 `python src/main.py` 时，求解器持续超时并最终抛出异常：
  ```text
  Loading data from APS Database- Simulation draft.xlsx for Site WL...
  Loaded 65 demand lines -> 50 FG tasks + 82 SF tasks.
  [solver] 初始可行提示: UNKNOWN
  [solver] 软约束: UNKNOWN
    [warn] 上一口径无解，降级为: 软约束 95% 利用率 + 交付损失最小化
  [solver] 软约束: UNKNOWN
  RuntimeError: Solver found no feasible schedule in any tier.
  ```
* **根本原因**：
  1. **无界跨度引发组合爆炸**：`main.py` 按订单最晚交期直接推算计划期：`planning_days = (max(due_date) - start_dt).days + 1 + 3 = 38 天`。
  2. 65 笔订单、132 个任务以及 114 个连续班次全部涌入单一大模型，整数与布尔决策变量突破 30,000+。
  3. **种子预热时限过窄**：`hint` 阶段分配的时限仅为总预算的 6%（`90s * 0.06 = 5.4s`）。在无启发式构造引导的情况下，CP-SAT 求解器无法在 5 秒内求得初始可行装箱解，返回 `UNKNOWN`。
  4. 后续软约束分层求解接连超时，导致降级机制耗尽崩溃。
* **业务影响**：核心批处理入口（CLI Pipeline）完全失效，无法进行离线出表与回归验算。
* **建议方案**：
  1. 重构 `main.py`，对齐 `server.py` 的视区机制（默认入参 `horizon_days = 10` 并启用 30 天需求前瞻平准）。
  2. 将全周期（38天）排产按分治架构（Divide-and-Conquer）划分为滚动窗口（Rolling Horizon）求解。

---

### 1.3 `APSScheduler` 默认漏传 PM 维保参数导致 `R-WL-27` 校验失效
* **代码定位**：`src/scheduler.py` 第 125~155 行
* **现象复现**：
  当调用方仅执行 `APSScheduler(plan)` 时，排产计划在验证器中必定触发以下违规：
  ```text
  FAIL [!!] 设备预防维护（PM）停机避让合规 [R-WL-27]
      - 产线 PGLINKAGE 在维保停机班次 ('10/14', '晚班') 违规排入任务 FG::ORD_WL_WB35356_1020
      - 产线 轴套6 在维保停机班次 ('10/17', '晚班') 违规排入任务 SF::ORD_WL_WB35356_1020::WC84833
  ```
* **根本原因**：
  `APSScheduler.__init__` 的入参默认值为 `pm_shifts: Optional[Dict[str, List[int]]] = None`，内部实现为：
  ```python
  self.pm_shifts = pm_shifts or {}
  ```
  若调用方未手动从 `plan.tasks` 中遍历提取 `line_pm_slots` 并传入字典，求解器内部的 `self.pm_shifts` 恒为空，PM 维保避让约束被完全跳过。
* **业务影响**：车间计划排入检修停机时段，排产方案在现场物理不可执行。
* **建议方案**：在 `APSScheduler.__init__` 中增加自动回退提取逻辑：若 `pm_shifts is None`，自动从 `self.plan.tasks` 中汇聚所有产线的 `line_pm_slots`。

---

## 二、P1 级：核心算法模型与工艺规则缺陷

### 2.1 首班（Shift 0）组装被无条件锁死，忽视在库期初库存
* **代码定位**：`src/scheduler.py` 第 610~616 行（`_add_material_balance`）
* **逻辑缺陷**：
  ```python
  for s in range(fg_lo, fg_hi + 1):
      assembled = cumulative[fg_task.task_id][s] * fg_task.lot_size
      for component in components:
          comp_lo, comp_hi = self.windows[component.task_id]
          ratio = int(round(component.bom_ratio))
          if s - 1 < comp_lo:
              model.Add(cumulative[fg_task.task_id][s] == 0)  # <-- 此处缺陷
  ```
* **根本原因**：
  半成品的时间窗下界 `comp_lo` 恒为 0。在首个排产班次 $s = 0$ 时，$s - 1 = -1 < 0$ 必定成立。代码直接强制施加 `cumulative[fg_task.task_id][0] == 0`，**完全无视该任务是否已被半成品期初在库库存覆盖（`component.stock_covered > 0`）**。
* **业务影响**：
  车间总装流水线在排产首日早班被算法人为强制停工，不仅导致首日设备空转，还造成 95% 班次利用率硬约束无法达标。
* **建议方案**：
  修改约束条件，仅当 `assembled * ratio > component.stock_covered` 且 $s - 1 < comp_lo$ 时才限制产出：
  ```python
  if s - 1 < comp_lo:
      model.Add(assembled * ratio <= component.stock_covered)
  ```

---

### 2.2 净需求核算未补足期初安全库存缺口
* **代码定位**：`src/data_loader.py` 第 490~498 行（`build_requirement_plan`）
* **逻辑缺陷**：
  ```python
  usable = max(0.0, fg_avail[order.part_no] - param.target_stock)
  net_need = order.demand_quantity - usable
  ```
* **根本原因**：
  若某物料期初库存低于安全库存（例如安全库存 100 件，实际在库 30 件），`usable` 被截断为 0，`net_need` 仅计算该笔订单的采购量。**期初亏空的 70 件安全库存从未进入净需求中补齐**。订单交付完毕后，期末库存将归零或持续维持亏空状态。
* **业务影响**：不符合制造业 MRP/APS 净需求推导逻辑（$净需求 = 客户需求 + \max(0, 安全库存 - 可用期初库存)$），削弱了供应链应对突发扰动的缓冲能力。
* **建议方案**：重构可用库存与净需求扣减公式，动态核算安全库存补水增量。

---

### 2.3 SY38 基地“12h×2班”规格与底层“8h×3班”引擎硬编码冲突
* **代码定位**：
  - `src/shift_manager.py` 第 15~19 行、第 110~130 行
  - `src/data_loader.py` 第 465 行、第 480 行
  - `src/validator.py` 第 425~435 行
  - `src/balance_builder.py` 第 135 行
* **现状分析**：
  1. **业务规范与前端展示**：`Specification/aps_functional_spec.md` 与 Web 界面明确声明 SY38 沈阳车间为 **12 小时 × 2 班制（白班 08:00~20:00，夜班 20:00~08:00）**。
  2. **底层引擎硬编码**：`shift_manager.py` 全局硬编码为 `SHIFT_HOURS = 8.0`、`SHIFT_NAMES = ("早班", "中班", "晚班")`。
  3. **数据映射偏移错误**：`data_loader.py` 在换算关键物料 ETA 与 PM 维保班次时，固定采用 `d_idx * 3 + s_offset` 计算班次索引，直接导致两班制下的班次时序错位。
  4. **校验器口径欺骗**：`validator.py` 中的 `_check_capacity_sy` 打印虽然声称 `12小时/100%`，但底层判定阈值使用的是 `SHIFT_SECONDS = 28800`（即 8 小时）。
* **业务影响**：沈阳基地的排产方案本质上依然运行在温岭基地的 8 小时模板下，无法真实指导 12 小时两班制生产。
* **建议方案**：提取工厂日历配置抽象类（`PlantCalendarConfig`），根据站点（WL / SY38）动态加载班次时长（8h vs 12h）、班次数（3班 vs 2班）及班次名称。

---

### 2.4 分数 BOM 展开直接四舍五入导致齐套关系失真
* **代码定位**：`src/scheduler.py` 第 609 行
* **逻辑缺陷**：
  ```python
  ratio = int(round(component.bom_ratio))
  ```
* **根本原因**：直接将 BOM 比例转为整型。若未来引入非 1:1 的配比装配（如 0.5 单件辅料、1.5 包装套件），`0.5` 会被错误四舍五入为 `0` 或 `1`。
* **业务影响**：当配比为 0.5 时，齐套约束直接消失（$0 \times \text{assembled} = 0$），导致装配无前置物料约束；当配比为 1.5 时，被放大为 2 倍消耗，引发虚假断线报警。
* **建议方案**：使用乘法等式消除浮点与四舍五入：例如若 $ratio = p / q$，则表达为 $q \times \text{available} \ge p \times \text{assembled}$。

---

### 2.5 换型调机损耗未从班次额定可用产能中扣减
* **代码定位**：`src/scheduler.py`（`_add_line_capacity`）与 `src/balance_builder.py`
* **问题现状**：
  调度模型计算班次工时仅累加纯加工时间：
  $$\text{load\_ms} = \sum (\text{lots} \times \text{lot\_millis})$$
  实际注塑机或冲压机每次换模需要 15~30 分钟。若一个班次内穿插生产 2~3 个物料，换模损失将达到 30~60 分钟。
* **业务影响**：算法输出的利用率虽达到 98%，但车间现场扣除换型停机后实际有效工时超载，导致现场工人不得不超频加班或延期交付。
* **建议方案**：在决策变量中引入换模启动变量 `started`，并对班次可用产能进行动态扣减：
  $$\sum \text{加工工时} + \sum (\text{started} \times \text{换型工时}) \le \text{班次总工时}$$

---

### 2.6 单批超班（>8h）导致 CP-SAT 离散装箱结构性无解
* **问题依据**：`Specification/aps_algorithm_review.md` 第 54~65 行
* **根本原因**：
  现有模型决策变量 $x[task, s]$ 表示该任务在班次 $s$ 分配的批次数。
  物料 `WA14208S` 单个 MPQ 批量为 72 件，CT 300s，TRP 0.71，单批纯加工时间为 **8.45 小时**（超过单班 8 小时上限）。
  在当前约束下：
  ```python
  per_shift = max(1, min(task.lots, max_line_ms // task.lot_millis))  # max_line_ms // lot_millis == 0 -> 被 max(1, ...) 提为 1
  ```
  但容量约束又强制 `load <= max_line_ms`，导致该物料无论排在哪个班次都会发生容量超限。
* **业务影响**：当前模拟数据中该料号恰好被期初库存覆盖未触发排产；未来只要出现该料号的未覆盖需求，系统将 100% 报错不可行崩溃。
* **建议方案**：支持任务批次跨班次切片分割（Sub-lot Slicing）或重构为料号级班次产出变量。

---

## 三、P2 级：工程规范、并发安全与演进架构短板

### 3.1 原工程完全缺失自动化测试与 CI 门禁
* **现状**：项目根目录及子目录下此前未建立任何 `test_*.py` 单元或集成测试用例，所有逻辑正确性仅依赖手动运行脚本肉眼观察。
* **风险**：算法调整与规则修改极易引入隐蔽回归。
* **改进**：本次已搭建 `test_aps_pipeline.py`，需进一步补齐 Mock 数据用例并纳入提交前钩子（Pre-commit）。

---

### 3.2 后端全局单例会话状态（Race Condition 隐患）
* **代码定位**：`src/server.py` 全局变量 `session_state`
* **问题**：FastAPI 后端使用同一个内存单例字典维护当前站点、快照、求解结果与配置。
* **风险**：若多个计划员同时打开网页，或者一个用户在 WL 排产的同时另一个用户在 SY38 调整日历，会话状态将发生不可预知的相互覆盖与脏读。
* **改进**：为每个前端连接分配客户端 `session_id`，使用带有 TTL 的线程安全字典或轻量 SQLite 隔离会话。

---

### 3.3 多 Worker 并行搜索导致同输入排产非确定性波动
* **代码定位**：`src/scheduler.py` 第 354 行：`solver.parameters.num_workers = 8`
* **问题**：在求解时限截断（Wall Time Limit）机制下，多线程竞争会导致在时间截止点由不同 Worker 返回不同的局部非劣解。相同输入在两次求解时输出的甘特图排布可能存在微小差异。
* **改进**：在试运行及正式生成方案时，增加确定性搜索配置（如固定随机种子 `random_seed`、或终态采用 `num_workers = 1` 确定性收拢）。

---

### 3.4 “订单任务槽位模型”组合爆炸，缺乏增量热重排机制
* **架构短板**：
  1. 当前排产模型以“订单（Order）$\to$ 工序任务（Task）”为粒度创建变量。当订单行数增加到数百条时，CP-SAT 的变量与约束呈几何级数膨胀。
  2. 每次重新排产均为“冷启动重排”，未提供车间现场在制品（WIP）状态与前 2 天冻结期执行实绩的增量吸收能力。
* **演进建议**：
  参考 `Specification/aps_algorithm_review.md`，将决策变量重构为 **“料号 × 班次产量”** 动态库存平衡模型。同料号订单在求解器内部天然合批，变量规模缩减 80% 以上，并天然具备增量热滚动能力。

---

## 四、修复优先级与实施路线图

```text
[Phase 1: 致命修复与基线加固] (P0)
 ├── 1. 提交 server.py 中 Tuple 导入补丁，确保服务启动 100% 稳定
 ├── 2. 修复 APSScheduler.__init__，自动从 plan.tasks 提取 pm_shifts，闭环 R-WL-27
 └── 3. 改造 main.py，引入 horizon_days 视区控制，解决 CLI 管道无解崩溃

[Phase 2: 核心工艺模型调优] (P1)
 ├── 1. 修复 _add_material_balance：首班有期初库存时允许总装正常排产
 ├── 2. 修复 build_requirement_plan：净需求自动补足期初安全库存亏空
 ├── 3. 参数化工厂日历：彻底解耦 WL (8h×3班) 与 SY38 (12h×2班)
 └── 4. 消除分数 BOM 的四舍五入整型截断

[Phase 3: 架构演进与生产就绪] (P2)
 ├── 1. 换型调机损耗扣减班次可用有效工时
 ├── 2. 后端会话状态 Session 隔离，杜绝多用户并发覆盖
 └── 3. 演进为“料号 × 班次”库存平衡动态模型，攻克单批超班与长周期计算难题
```
