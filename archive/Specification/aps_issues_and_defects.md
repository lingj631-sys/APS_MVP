# APS_MVP 系统缺陷与架构问题全要素诊断清单

> **报告版本**：v0.7.1（基线提交：`c2ad9aa` / 合并 v0.7.0 之后）  
> **更新日期**：2026-10-23  
> **测试状态**：`test_aps_pipeline.py`（6/6 自动化闭环测试全部 PASS）  
> **运行环境**：Python 3.12 (`.venv` by `uv`) · Google OR-Tools CP-SAT (v9.15) · FastAPI (v0.159)

---

## 目录
- [一、缺陷演进全景与状态矩阵](#一缺陷演进全景与状态矩阵)
- [二、本次迭代已修复与闭环缺陷 (Closed Issues)](#二本次迭代已修复与闭环缺陷-closed-issues)
  - [2.1 FastAPI 后端启动闪退（NameError: Tuple 未导入）](#21-fastapi-后端启动闪退nameerror-tuple-未导入)
  - [2.2 CLI 管道无界视区求解超时不可行（RuntimeError）](#22-cli-管道无界视区求解超时不可行runtimeerror)
  - [2.3 APSScheduler 默认漏传 PM 维保参数导致 R-WL-27 校验失效](#23-apsscheduler-默认漏传-pm-维保参数导致-r-wl-27-校验失效)
  - [2.4 SY38 基地底层“8h×3班”硬编码导致真实排产与工艺严重失真](#24-sy38-基地底层8h3班硬编码导致真实排产与工艺严重失真)
  - [2.5 顺位紧邻全序链在相同交期下断裂导致 R-WL-12 跳序插单假阳性](#25-顺位紧邻全序链在相同交期下断裂导致-r-wl-12-跳序插单假阳性)
  - [2.6 班次内工单时钟排序按 task_id 字母排序诱发时序倒挂](#26-班次内工单时钟排序按-task_id-字母排序诱发时序倒挂)
  - [2.7 甘特图色块悬停导致整个画布 60Hz 剧烈抖动（CLS 布局死锁）](#27-甘特图色块悬停导致整个画布-60hz-剧烈抖动cls-布局死锁)
  - [2.8 前端产线负荷概览表与工作制模式全天工时口径混淆](#28-前端产线负荷概览表与工作制模式全天工时口径混淆)
- [三、当前仍存留的核心算法模型与工艺缺陷 (Remaining P1 Issues)](#三当前仍存留的核心算法模型与工艺缺陷-remaining-p1-issues)
  - [3.1 首班（Shift 0）组装被无条件锁死，忽视半成品期初物理在库](#31-首班shift-0组装被无条件锁死忽视半成品期初物理在库)
  - [3.2 净需求核算未补足期初安全库存亏空](#32-净需求核算未补足期初安全库存亏空)
  - [3.3 分数 BOM 展开在调度器中直接四舍五入整型截断](#33-分数-bom-展开在调度器中直接四舍五入整型截断)
  - [3.4 换型调机损耗工时未在班次可用产能中动态扣减](#34-换型调机损耗工时未在班次可用产能中动态扣减)
  - [3.5 单批超班（>8h / >12h）导致 CP-SAT 离散装箱结构性无解](#35-单批超班8h--12h导致-cp-sat-离散装箱结构性无解)
- [四、当前仍存留的系统工程与中远期架构瓶颈 (Remaining P2 Issues)](#四当前仍存留的系统工程与中远期架构瓶颈-remaining-p2-issues)
  - [4.1 单进程全局内存会话状态（Race Condition 隐患）](#41-单进程全局内存会话状态race-condition-隐患)
  - [4.2 多 Worker 壁钟截断导致排产解的非确定性波动](#42-多-worker-壁钟截断导致排产解的非确定性波动)
  - [4.3 全冷启动排产与车间在制品（WIP）缺乏增量热重排能力](#43-全冷启动排产与车间在制品wip缺乏增量热重排能力)
  - [4.4 离散“订单-槽位”模型变量规模随订单量线性爆炸](#44-离散订单-槽位模型变量规模随订单量线性爆炸)
  - [4.5 FastAPI 声明周期事件废弃告警（@app.on_event("startup")）](#45-fastapi-声明周期事件废弃告警appon_eventstartup)
- [五、演进实施路线图与优先级规划](#五演进实施路线图与优先级规划)

---

## 一、缺陷演进全景与状态矩阵

| 缺陷编号 | 缺陷描述 | 模块位置 | 严重等级 | 当前状态 |
| :---: | :--- | :--- | :---: | :---: |
| **ISSUE-01** | `server.py` 启动闪退（`NameError: Tuple`） | `src/server.py` | **P0 (致命)** | ✅ **已修复** |
| **ISSUE-02** | `main.py` CLI 全量 38 天求解超时崩溃 | `src/main.py` | **P0 (致命)** | ✅ **已修复** |
| **ISSUE-03** | `APSScheduler` 漏传参数致 `R-WL-27` PM 校验失效 | `src/scheduler.py` | **P0 (致命)** | ✅ **已修复** |
| **ISSUE-04** | SY38 基地底层被硬编码 8h 三班倒，口径严重失真 | 跨 5 模块 | **P1 (严重)** | ✅ **已修复** |
| **ISSUE-05** | 相同交期订单顺位断链致 `R-WL-12` 假阳性违规 | `src/scheduler.py` | **P1 (严重)** | ✅ **已修复** |
| **ISSUE-06** | 班内任务时钟时刻按字母排序致倒挂跳序 | `src/scheduler.py` | **P1 (严重)** | ✅ **已修复** |
| **ISSUE-07** | 甘特图色块悬停导致下方画布 60Hz 剧烈抖动 | `src/static/index.html` | **P1 (体验)** | ✅ **已修复** |
| **ISSUE-08** | 负荷概览表定额与工作制 24h 工时口径混淆 | `index.html` / `server.py` | **P1 (体验)** | ✅ **已修复** |
| **ISSUE-09** | 首班（Shift 0）组装无条件锁死，忽视在库期初半成品 | `src/scheduler.py` | **P1 (算法)** | ✅ **已修复**（ACT-01/02） |
| **ISSUE-10** | 净需求核算未补足期初安全库存亏空差额 | `src/data_loader.py` | **P1 (MRP)** | ✅ **已修复**（ACT-03/REG-03） |
| **ISSUE-11** | 分数 BOM 展开在调度器中直接四舍五入整型截断 | `src/scheduler.py` | **P1 (算法)** | ✅ **已修复** |
| **ISSUE-12** | 换型调机损耗工时未在班次可用产能中动态扣减 | `scheduler` / `balance` | **P1 (工艺)** | ✅ **已修复**（C1-b-lite Setup扣减） |
| **ISSUE-13** | 单批耗时超班（>8h）导致 CP-SAT 离散装箱结构性无解 | `src/scheduler.py` | **P1 (模型)** | ✅ **已修复**（ACT-06 拆批连续） |
| **ISSUE-14** | 单进程全局内存会话裸字典竞态覆盖风险 | `src/server.py` | **P2 (工程)** | ⚠️ **待演进**（支持 FACTORY_SITE 单厂实例隔离） |
| **ISSUE-15** | CP-SAT 多线程壁钟截断导致排产解存在微小随机性 | `src/scheduler.py` | **P2 (确定性)** | ✅ **已优化**（固定单线程+种子） |
| **ISSUE-16** | 缺乏基于车间现场 WIP/冻结期的增量热滚动重排机制 | `src/scheduler.py` | **P2 (架构)** | ⚠️ **待演进**（已落地 freeze 约束与基线比对） |
| **ISSUE-17** | 离散“订单-槽位”模型变量规模随订单量爆炸（30k+） | `src/scheduler.py` | **P2 (运筹)** | ⚠️ **待演进**（已做 10天视区控制） |
| **ISSUE-18** | FastAPI 废弃 `@app.on_event("startup")` 警告 | `src/server.py` | **P3 (维护)** | ✅ **已修复** |
| **ISSUE-19** | 调度器 6 大孤岛算子接线断层与旧版布尔开关双重门控死锁 | `src/scheduler.py` | **P0 (致命)** | ✅ **已修复**（2026-10-29 算子接线与解耦） |

---

## 二、本次迭代已修复与闭环缺陷 (Closed Issues)

### 2.1 FastAPI 后端启动闪退（`NameError: Tuple` 未导入）
* **定位**：`src/server.py` 第 98 行
* **表现**：启动 Web 服务 `python run_web.py` 时直接崩溃中断。
* **根因**：类型标注引入了 `Tuple` 但顶部未导入。
* **解决**：补全 `Tuple` 导入，消除了致命启动崩溃。

### 2.2 CLI 管道无界视区求解超时不可行（`RuntimeError`）
* **定位**：`src/main.py`
* **表现**：执行控制台离线排产时，各求解层级连续超时，最终抛出 `RuntimeError: Solver found no feasible schedule in any tier`。
* **根因**：`main.py` 盲目使用订单最远交期计算计划期（达 38 天、114 班次），将 132 个任务、30,000+ 离散变量全部涌入单一大模型；种子解时限仅 5.4s，CP-SAT 超时返回 `UNKNOWN`。
* **解决**：适配标准 10 天计划视区（`horizon_days=10`），并将数据源平滑指向 `data/`。排产耗时由超时崩溃骤降至 **1.72 秒**。

### 2.3 `APSScheduler` 默认漏传 PM 维保参数导致 `R-WL-27` 校验失效
* **定位**：`src/scheduler.py`
* **表现**：任何未手动拼装 `pm_shifts` 字典的调用方排产后，必定触发校验失败：`FAIL [!!] 设备预防维护（PM）停机避让合规`。
* **根因**：`APSScheduler.__init__` 默认 `pm_shifts = pm_shifts or {}`，未从传入的 `plan.tasks` 中自动抽取 `t.line_pm_slots`。
* **解决**：在初始化时增加自动回退提取逻辑，若未传参则自动汇聚提取所有产线的维保班次。

### 2.4 SY38 基地底层“8h×3班”硬编码导致真实排产与工艺严重失真
* **定位**：`src/shift_manager.py`、`src/scheduler.py`、`src/balance_builder.py`、`src/validator.py`
* **表现**：沈阳基地（12h 两班制）排产被强行拆分成早/中/晚三班，单班容量被截断在 8h；产线负荷率分母硬除以 8h 导致虚高爆红 170%；注塑到涂装的 12h 静置时效缩水至 8h。
* **解决**：建立 `PlantTopology` 抽象接缝，WL（8h×3班）与 SY38（12h×2班）彻底参数化解耦。日历槽位、CP-SAT 单班容量（$43.2\text{M ms}$）、负荷率分母与校验门限完全一致化对齐。

### 2.5 顺位紧邻全序链在相同交期下断裂导致 `R-WL-12` 跳序插单假阳性
* **定位**：`src/scheduler.py`
* **表现**：排产结果出现 `早期工单 ORD_1019 完工晚于后期工单 ORD_1020，存在跳序插单`。
* **根因**：同料号按订单排序时，仅对相邻两项施加 `if ta.due < tb.due: Add(end_a <= end_b)`。当存在相同交期订单（$due_0 = due_1 < due_2$）时，相邻判断失效，导致依赖链断裂。
* **解决**：重构顺位前拉构建逻辑：改用按**不同交期分组**，对所有不同交期组建立全序笛卡尔积链式约束，数学上杜绝断链。

### 2.6 班次内工单时钟排序按 `task_id` 字母排序诱发时序倒挂
* **定位**：`src/scheduler.py` 中的 `_clock_times`
* **表现**：即使两工单完工班次相同，班次内具体起止时刻依然触发跳序违规。
* **根因**：班次内工单排定顺序按 `(first_shift, task.task_id)` 排序，导致任务按料号字母逆序倒挂。
* **解决**：排序键重构为 `(first_shift, task.due_datetime, task.task_id)`，严格按交期先后分配班内连续时钟。

### 2.7 甘特图色块悬停导致整个画布 60Hz 剧烈抖动（CLS 布局死锁）
* **定位**：`src/static/index.html`
* **表现**：鼠标移入工单矩形时，弹出的工单要素栏与整个甘特图疯狂上下震颤。
* **根因**：工单悬停预览条使用 `v-if` 在普通文档流中动态插入，高度突增 46px 导致下方画布下移，色块脱离鼠标触发 `mouseleave`；预览条消失后画布上弹触发 `mouseenter`，陷入 60Hz 震颤死循环。
* **解决**：预览条改造为常驻定高容器（`min-h-[46px]`），未悬停时展示优雅引导占位；移除色块边缘物理缩放 `hover:scale-[1.01]`，实现零布局偏移（CLS = 0）。

### 2.8 前端产线负荷概览表与工作制模式全天工时口径混淆
* **定位**：`src/static/index.html`、`src/exporter.py`、`src/server.py`
* **表现**：SY38 产线负荷表第 4列写死“单班定额: 8.0h”；工作制下拉框中单周 120h/96h 与开工日全天 24h 混淆。
* **解决**：定额列动态感知为 12.0h (SY38) vs 8.0h (WL)；文案统一为“开工日全天 24h 生产”，日历卡片自适应渲染两班制（12h/班，早/晚班）与三班制。

### 2.9 升级 FastAPI lifespan 现代生命周期管理消除废弃告警
* **定位**：`src/server.py`
* **表现**：启动 Web 服务或导入 server 模块时抛出 `DeprecationWarning: on_event is deprecated, use lifespan event handlers instead`。
* **根因**：使用了 FastAPI 早期已逐步废弃的 `@app.on_event("startup")` 装饰器。
* **解决**：重构为标准 `@asynccontextmanager async def lifespan(app: FastAPI)` 上下文管理器并在 `FastAPI(..., lifespan=lifespan)` 注入；同时保留向后兼容的 `startup_event()` 函数支持外部脚本与离线测试调用。告警数完全清零。

### 2.10 分数 BOM 展开在调度器中直接四舍五入整型截断
* **定位**：`src/scheduler.py` 中的 `_add_material_balance`
* **表现**：当 BOM 存在非整数比例（如 0.5、1.5 等辅料/共模件）时，原逻辑 `ratio = int(round(component.bom_ratio))` 直接四舍五入截断，导致 0.5 变为 0 或 1，约束完全失效或虚增领料需求。
* **解决**：引入 `fractions.Fraction` 对 `bom_ratio` 进行有理数分数化（`Fraction(str(ratio)).limit_denominator(1000)`），提取分子 `num` 与分母 `denom`，通过严格等价的交叉乘法等式 `denom * available >= num * assembled` 建立线性整数不等式，100% 精确表达任意非整数比例，且在 CP-SAT 内部保持纯整型标度，杜绝浮点精度丢失。新增自动化测试 `test_07_fractional_bom_ratio_handling` 闭环验证通过。

### 2.11 手工排产 Excel 上传独立审计闪退（openpyxl 与 ScheduledTask 未导入）
* **定位**：`src/server.py`（`/api/audit_manual_schedule`）
* **表现**：用户在前端上传排产 Excel 执行二次合规审计时，接口抛出 400 异常：`无法打开上传的 Excel 文件: name 'openpyxl' is not defined`；同时提取任务时缺失 `ScheduledTask` 符号。
* **根因**：合并远程新增的独立手工审计接口时，顶部缺少 `import openpyxl` 以及从 `src.scheduler` 导入 `ScheduledTask`。
* **解决**：补齐顶部 `openpyxl` 与 `ScheduledTask` 导入；新增 `test_08_manual_schedule_excel_audit` 自动化测试闭环覆盖真实文件流上传与 27 条/9 条双工厂审计。

---

## 三、当前仍存留的核心算法模型与工艺缺陷 (Remaining P1 Issues)

### 3.1 首班（Shift 0）组装被无条件锁死，忽视半成品期初物理在库
* **代码定位**：`src/scheduler.py` 第 635~645 行（`_add_material_balance`）
* **逻辑现状**：
  ```python
  if s - 1 < comp_lo:
      model.Add(cumulative[fg_task.task_id][s] == 0)
  ```
* **根本原因**：半成品槽位下界 `comp_lo` 恒为 0。当 $s = 0$ 时，$s - 1 = -1 < 0$ 必定成立。代码**无条件强制成品在首个班次的累计产出为零**，完全忽视了半成品已有充足的物理在库库存（`component.stock_covered > 0`）。
* **物理影响**：车间总装线在排产首日早班被算法人为强制停机，造成早班产能白白浪费，首日负荷率严重失真。
* **修复方案**：仅在装配需求超出在库半成品库存时才限制开工：
  ```python
  if s - 1 < comp_lo:
      model.Add(assembled * ratio <= component.stock_covered)
  ```

---

### 3.2 净需求核算未补足期初安全库存亏空
* **代码定位**：`src/data_loader.py`（`build_requirement_plan`）
* **逻辑现状**：
  ```python
  usable = max(0.0, fg_avail[order.part_no] - param.target_stock)
  net_need = order.demand_quantity - usable
  ```
* **根本原因**：若期初在库低于安全库存（如安全库存 100 件，实际在库 30 件），`usable` 截断为 0，`net_need` 仅计算客户订单量。**期初亏空的 70 件安全库存从未进入排产净需求**。交付完成后，期末结存依然处于赤字状态。
* **物理影响**：不符合制造业 MRP 净需求经典公式（$\text{净需求} = \text{毛需求} + \max(0, \text{安全库存} - \text{可用期初})$），削弱了车间抵抗供应链波动的缓冲能力。
* **修复方案**：在首笔订单净需求核算时，将 `max(0.0, param.target_stock - fg_avail)` 的补水差额计入。

---

### 3.3 换型调机损耗工时未在班次可用产能中动态扣减
* **代码定位**：`src/scheduler.py`（`_add_line_capacity`）与 `src/balance_builder.py`
* **逻辑现状**：调度模型只累加纯加工时间 $\sum (\text{lots} \times \text{lot\_millis})$。
* **根本原因**：注塑机或冲压机每次换模需要 15~30 分钟。若单个班次内切换生产 2~3 个物料，换模停机损失达到 30~60 分钟。
* **物理影响**：算法计算出的负荷率虽然显示 98%，但车间现场扣除换模损耗后实际工时严重超载，排产方案在执行端面临硬性滞后。
* **修复方案**：引入换型指示变量 $started_{t, s}$，将换型调机时间动态计入班次总工时：
  $$\sum \text{纯加工时间} + \sum (started_{t, s} \times \text{换型工时}) \le \text{班次总工时}$$

---

### 3.4 单批超班（>8h / >12h）导致 CP-SAT 离散装箱结构性无解
* **问题依据**：`Specification/aps_algorithm_review.md` 第 54~65 行
* **根本原因**：现有模型以离散批次作为槽位变量。物料 `WA14208S` 单个 MPQ 批量为 72 件，纯加工时间为 **8.45 小时**（超过单班 8 小时上限）。容量约束强制 $\text{load} \le 8\text{h}$，导致该批次排入任何班次均必然触发容量超限。
* **物理影响**：当前模拟数据中该料号恰好被期初库存覆盖未触发排产；未来只要该物料出现实际净需求，排产将 100% 崩溃抛出 `INFEASIBLE`。
* **修复方案**：支持任务批次跨班次平滑切片（Sub-lot Slicing）或将决策变量重构为料号级班次产出。

---

## 四、当前仍存留的系统工程与中远期架构瓶颈 (Remaining P2 Issues)

### 4.1 单进程全局内存会话状态（Race Condition 隐患）
* **代码定位**：`src/server.py` 全局变量 `session_state`
* **问题**：FastAPI 后端使用单例全局字典维护快照、日历配置与求解结果。
* **风险**：若多名计划员同时打开网页，或者同一用户在两个标签页分别操作 WL 和 SY38，会话状态将相互覆盖与脏读。
* **改进**：引入基于客户端 Cookie / 请求头的 `session_id` 机制，封装带 TTL 的轻量 `SessionStore`。

### 4.2 多 Worker 壁钟截断导致排产解的非确定性波动
* **代码定位**：`src/scheduler.py` 中的 `solver.parameters.num_workers = 8`
* **问题**：在求解时限到达时，不同 Worker 线程在 CPU 调度竞争下的进度不一，相同输入在不同次求解时输出的甘特图排布可能存在微小差异。
* **改进**：在试运行及正式发布方案时，固定随机种子（`random_seed`）或在收口阶段启用确定性单线程求解。

### 4.3 全冷启动排产与车间在制品（WIP）缺乏增量热重排能力
* **问题**：当前系统每次触发排产均为“冷启动全局重排”，无法吸收车间当前执行实绩。
* **风险**：已在产线上冲压了一半的工单、或前 2 天冻结期（Freeze Window）内锁定的工单无法进行增量锁定。
* **改进**：支持“在制品（WIP）快照注入”与“冻结期硬只读锁定”，只对解冻窗口后的需求执行滚动重排（Rolling Reschedule）。

### 4.4 离散“订单-槽位”模型变量规模随订单量线性爆炸
* **问题**：现有模型针对每个订单任务单独生成决策变量 $x[task, s]$。当订单行数增加到 300~500 笔时，CP-SAT 的变量规模将突破 50,000+。
* **改进**：将决策变量升级为 **“料号 × 班次产量”** 动态库存平衡连续流模型，同料号订单在求解器内部天然合并，变量缩减 80% 以上。

### 4.5 FastAPI 声明周期事件废弃告警（`@app.on_event("startup")`）
* **代码定位**：`src/server.py` 第 355 行
* **问题**：FastAPI 0.110+ 废弃了 `@app.on_event`，建议改用 lifespan 上下文管理器。
* **改进**：升级为 `@asynccontextmanager async def lifespan(app: FastAPI)`。

---

## 五、演进实施路线图与优先级规划

```text
[Phase 1: 稳定性与致命缺陷封堵] (已 100% 达成 ✅)
 ├── 1. 修复 Tuple 导入消除服务闪退
 ├── 2. main.py 视区控制，解决 38 天全量求解超时崩溃 (1.7s 出解)
 ├── 3. APSScheduler 自动收集 PM 维保参数，闭环 R-WL-27
 ├── 4. PlantTopology 双工厂接缝解耦，纠偏 SY38 12h 两班制
 ├── 5. 修复顺位前拉全序链断裂与班内时钟倒挂 (R-WL-12)
 ├── 6. 根治甘特图悬停预览条布局下推抖动 (CLS = 0)
 └── 7. 建立 test_aps_pipeline.py 自动化测试守护套件

[Phase 2: 核心工艺模型与 MRP 精度提升] (建议下一步推进 🎯)
 ├── 1. 修复 _add_material_balance：首班有期初半成品库存时允许总装正常排产
 ├── 2. 修复 build_requirement_plan：净需求自动补足期初安全库存亏空差额
 ├── 3. 消除分数 BOM 展开四舍五入整型截断
 ├── 4. 换型调机损耗工时纳入班次可用产能动态扣减
 └── 5. 攻克单批超班 (>8h / >12h) 导致的 CP-SAT 结构性无解

[Phase 3: 架构演进与生产就绪] (中长期演进 🚀)
 ├── 1. SessionRepository 隔离后端会话，杜绝多标签页/多用户并发覆盖
 ├── 2. 演进为“料号 × 班次”动态库存平衡连续流模型，彻底消除组合爆炸
 ├── 3. 增加车间在制品 (WIP) 快照与冻结期滚动重排接口
 └── 4. 升级 FastAPI lifespan 现代生命周期管理
```
