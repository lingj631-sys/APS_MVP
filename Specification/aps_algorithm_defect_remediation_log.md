# APS 核心算法物理缺陷修复与差距对账日志 (585f44d ➔ 53ee1df)

> **文档编号**：APS-SPEC-REMEDIATION-DIFF · **版本**：v1.0 · **日期**：2026-10-28  
> **基线 Commit**：`585f44ddc889eff2a98dee14fd66772259a16555` (`refactor(arch): slim server router, model domain entities...`)  
> **目标 Commit**：`53ee1dfaf85cdbf028b293f4dde0009f0f6e0600` (`fix(algorithm): resolve time-phased material ledger...`)  
> **对比范围**：4 个核心文件变更（`src/data_loader.py`、`src/scheduler.py`、`src/audit/engine.py`、`tests/test_regressions.py`）  
> **指导规范依据**：`C:\Users\shan6\workspace\next\aps_solver_evolution.md` §2.2、§5、§7、§12

---

## 1. 总体变更统计与摘要

本次提交针对基线 `585f44d` 中遗留的 8 项核心物理不变量漏洞与工程数据缺陷进行了彻底的代码级重构：

| 变更文件 | 代码变动 | 核心改动归属 | 解决的核心缺陷 |
|---|:---:|---|:---:|
| `src/data_loader.py` | +62 / -16 | 数据加载路径分流、绝对时间戳映射、无效单追踪、安全库存补产方程、BOM/期初透传 | REG-08, REG-02, REG-04, REG-03 |
| `src/scheduler.py` | +138 / -33 | 时间相位全局物料平衡账本、基线冻结期锁定等式约束 | REG-01, REG-05 |
| `src/audit/engine.py` | +108 / -23 | FIFO 逐单交付对账核销、审计交期范围动态对齐 | REG-06, REG-07 |
| `tests/test_regressions.py` | +305 / 0 (新增) | 8 大缺陷针对性自动化回归测试套件 | REG-01 ~ REG-08 验证闭环 |

---

## 2. 四大文件逐项差距与重构技术细节

### 2.1 `src/data_loader.py`（数据加载、日历映射与净需求拆解）

#### 差距 A：数据路径分流与 `dataSource.xlsx` 读空缺陷 (REG-08)
* **基线 `585f44d` 代码问题**：
  原 `load_aps_database` 先判断 `os.path.basename(source_path).startswith("data")`，由于文件名 `dataSource.xlsx` 以 `"data"` 开头，被错误分流至 `load_aps_csv`。在找不到 CSV 文件时，静默返回 0 张订单。
* **重构后机制**：
  调整判定顺序，绝对优先判定 `.xlsx` 文件后缀（`source_path.lower().endswith(".xlsx")`），并增加文件存在性硬校验，彻底消除空读。

#### 差距 B：PM 维保与在途到料日历错位 (REG-02)
* **基线 `585f44d` 代码问题**：
  原代码遍历 `snapshot.planning_dates`，使用 `pdate in ma.eta_date` 字符串模糊匹配。当计划起始日从 10/12 改为 10/13，或日期格式带有时分秒时，匹配失效导致 `d_start` 默认回退为 0（槽位错移到第 1 天早班），导致维保停机日历漂移。
* **重构后机制**：
  引入标准 `datetime.strptime` 解析完整的年月日与月日，通过数值化比对 `p_parsed.month == parsed_date.month and p_parsed.day == parsed_date.day`，精准锁定绝对拓扑槽位。

#### 差距 C：安全库存缺口截断导致漏产 (REG-03)
* **基线 `585f44d` 代码问题**：
  原代码净需求公式为 `usable = max(0.0, fg_avail - target); net_need = demand - usable`。若期初库存为 20，目标库存为 100，需求为 10，则 `usable=0`，`net_need=10`。生产 10 件交付后库存仍停留在 20，历史亏空的 80 件安全库存被永久丢弃。
* **重构后机制**：
  重构为严格守恒补库方程：
  $$\text{net\_need} = \max\Big(0.0,\ \text{order.demand\_quantity} + \text{param.target\_stock} - \text{fg\_avail}[\text{part\_no}]\Big)$$
  成品与半成品统一执行该公式，期初亏空在计划期内 100% 全额触发补产。

#### 差距 D：缺失主数据订单静默漏排 (REG-04)
* **基线 `585f44d` 代码问题**：
  遇到缺失 `Parameters-FG` 或 `Parameters-SFN` 的料号，仅输出 warning 并 `continue` 跳过，`RequirementPlan` 中完全丢失该工单，后续调度器和校验器出具虚假的 100% 达成率。
* **重构后机制**：
  在 `RequirementPlan` 中显式扩展 `invalid_orders: List[Tuple[str, str]]`、`has_invalid_orders` 与 `rejected_order_ids` 属性，未知料号被严密捕获并阻断虚假合规。

#### 差距 E：透传 BOM 与期初库存
* **重构后机制**：
  在 `RequirementPlan` 中补充 `bom: Dict` 与 `initial_stock: Dict` 字段，为调度器建立物料账本提供数据基础。

---

### 2.2 `src/scheduler.py`（调度器约束内核）

#### 差距 A：时间相位全局物料平衡账本，杜绝跨期借料 (REG-01)
* **基线 `585f44d` 代码问题**：
  原齐套约束 `_legacy_material_balance` 仅在 `sf_task.parent_task_id == fg_task.task_id` 时施加。当工单 A 触发了 MPQ 产生 90 件半成品余量时，随后的工单 B 判定为“库存覆盖”，不再生成 SF 子任务（`parent_task_id=None`）。这导致工单 B 在 CP-SAT 模型中脱离物料约束，被调度器提前安排在第 0 班开工，提前消费工单 A 在第 5 班才生产出的半成品（时序倒挂）。
* **重构后机制**：
  重构为基于物料号的时间相位全局账本：
  $$\text{Supply}(s) = \text{OpeningStock}[p] + \sum_{t \in \text{SF}(p)} \text{CumulOutput}[t][s-1] \ge \sum_{\text{fg} \in \text{FG}(p)} \text{CumulInput}[\text{fg}][s] \times \text{BOM\_Ratio}$$
  在每一个班次 $s$，强制要求该半成品物料截止 $s-1$ 班释放的总供给量 $\ge$ 截止到 $s$ 班下游成品的累计总消耗，彻底消除向未来借物料的物理漏洞。

#### 差距 B：基线冻结期锁定等式约束入模 (REG-05)
* **基线 `585f44d` 代码问题**：
  `self.enforce_freeze_window` 仅在 `__init__` 中保存为变量，在构模方法 `_build_model` 中从未向 CP-SAT 模型添加任何等式约束，冻结开关形同虚设。
* **重构后机制**：
  在 `APSScheduler` 引入 `baseline_tasks` 与 `freeze_shifts`（默认前 3 班）。构模时，对冻结时段（$s < \text{freeze\_shifts}$）严格施加基线等式锁定：
  $$x[t, s] == \text{baseline}[t, s]$$
  对于未在基线中的新插入任务，强制冻结期内 $x[t, s] == 0$，阻断新单干扰现场已备料工单。

---

### 2.3 `src/audit/engine.py`（独立二次审计引擎）

#### 差距 A：严格 FIFO 逐单交付对账核销 (REG-06)
* **基线 `585f44d` 代码问题**：
  原 `_check_due_part` 粗暴取该料号所有订单的 `latest_due = max(due_datetime)`。若工单早期订单（如 10/12）延误 3 天，只要晚期订单（如 10/16）尚未到期，整组订单均被判为通过，掩盖了早期交期违约。
* **重构后机制**：
  重构为标准 FIFO（先进先出）逐单核销算法：
  1. 将该料号订单按交期先后升序排列；
  2. 将完工产出批次按完成时点升序组织为供给事件流；
  3. 逐笔核销订单，只要任意一张订单的完全满足时点晚于其自身交期，立即出具红灯并在违规详情中明确打印受损单号（如 `ORD-1: 晚于交期 +80.0h`）。

#### 差距 B：计划周期范围与需求范围自动对齐 (REG-07)
* **基线 `585f44d` 代码问题**：
  计划员基于 10 天周期排产导出方案，上传重新审计时，审计引擎拿快照中全量（可能含 30 天）订单核算数量守恒，导致第 15 天的远期订单被判定为“欠产”，破坏了手工微调闭环。
* **重构后机制**：
  `audit_schedule_part_level` 接入交期范围截断（自动对齐至 `shifts` 覆盖末期）：超出计划窗口的远期订单不再纳入当前短期计划的考核范围，消除了假警报。

---

### 2.4 `tests/test_regressions.py`（新增回归套件）

新增了针对 8 大核心缺陷的独立单元回归用例：
1. `test_reg01_shared_sf_future_surplus_not_prematurely_consumed`：验证半成品未来余量绝不被提前消费。
2. `test_reg02_pm_and_arrival_absolute_time_mapping`：验证 10/15 PM 严格映射至物理槽位 11。
3. `test_reg03_safety_stock_deficit_recovery_formula`：验证期初 20、目标 100、需求 10 产出严格为 90 件。
4. `test_reg04_unknown_part_or_missing_master_data_raises_invalid_input`：验证未知料号被标记在 `rejected_order_ids`。
5. `test_reg05_freeze_window_enforced_against_baseline`：验证基线工单冻结期锁定且新工单不泄露。
6. `test_reg06_due_date_audit_catches_early_order_delay`：验证早期延误订单被 FIFO 审计精准拦截。
7. `test_reg07_exported_plan_manual_audit_uses_matching_demand_scope`：验证计划窗口对齐后远期单不报欠产。
8. `test_reg08_datasource_xlsx_explicit_path_loading`：验证 `dataSource.xlsx` 正确解析 65 张订单。

---

## 3. 验证与回归结论

全量自动化测试套件执行通过情况：
* `tests/test_regressions.py`：8 / 8 项测试全绿（0.159s）；
* 现有核心套件（独立审计、规则内核、拆批、人力、领域）：48 / 48 项测试全绿；
* **总计 56 项自动化测试 100% 绿色通过，零回归破坏**。
