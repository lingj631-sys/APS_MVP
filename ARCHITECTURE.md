# APS 智能排产系统代码库架构设计文档 (Codebase Architecture)

> **设计基准**：基于 **Deep Modules（深模块）** 设计哲学 —— *窄接口、深实现、清晰接缝、高杠杆调用、高局部性维护*。  
> **适用版本**：APS MVP Phase 1 (Commit `5727e4c`，含 REG-01~08 缺陷闭环)

---

## 1. 架构总览与分层拓扑

系统遵循严格的分层解耦架构，核心算子内核与独立二次审计引擎保持代码物理隔离，确保生产排程的运算高效性与审计客观性。

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        接入层 (Access & UI Layer)                       │
│  src/server.py (FastAPI RESTful API)  |  src/static/ (Vue+Tailwind Web)│
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼─────────────────────────────────────┐
│                 应用与编排服务层 (Application Services Layer)            │
│  src/services/manual_audit.py (已抽取的手工审计服务；路由仍待接线)       │
│  src/main.py (CLI 排程编排)  |  src/prechecker.py (粗能力预检)           │
└──────────────┬───────────────────────────────────┬─────────────────────┘
               │                                   │
┌──────────────▼────────────────────┐ ┌────────────▼─────────────────────┐
│    规则集服务深模块 (Rule Sets)     │ │     独立二次审计深模块 (Audit)     │
│       src/rule_sets/              │ │            src/audit/             │
│  • SQLite 版本库 (store.py)        │ │  • 独立仿真引擎 (engine.py)        │
│  • Excel 导入导出 (excel.py)       │ │  • 零依赖 scheduler 外部裁判       │
│  • 诊断与敏感度 (diagnose.py)      │ │  • 变异与篡改拦截                 │
└──────────────┬────────────────────┘ └────────────▲─────────────────────┘
               │ (编译注入)                         │ (PlanRow / ShiftInfo)
┌──────────────▼────────────────────┐              │
│      规则内核深模块 (Kernel)        │              │
│           src/kernel/             │              │
│  • 19 类算子编译 (compiler.py)    │              │
│  • 多目标字典序求解 (solve.py)     │              │
│  • 作用域继承解析 (scope.py)       │              │
└──────────────┬────────────────────┘              │
               │ (CompileContext 接缝)             │
┌──────────────▼───────────────────────────────────┴─────────────────────┐
│                     排程求解核心 (Core Scheduler)                       │
│         src/scheduler.py (CP-SAT 班次槽位模型、开班决策、大单拆批连续)   │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼─────────────────────────────────────┐
│                  领域实体与工业拓扑 (Domain & Data Layer)                │
│  src/domain/ (Site/Line/ShiftSlot/Routing/Part 骨架；Phase 2 才接入主链) │
│  src/data_loader.py (多表加载/BOM展开) | src/shift_manager.py (日历解耦)  │
│  src/labor.py (劳动力定员三阶模板)     | src/metric_labels.py (工业量纲)  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心深模块 (Deep Modules) 规范

根据 **Codebase Design** 准则，一个深模块必须具备 **“窄接口（Small Interface）+ 深实现（Deep Implementation）”**。以下为系统的四大核心深模块：

### 2.1 规则内核模块 (`src/kernel`)
* **设计意图**：将“固定 CP-SAT 核心算法”与“工厂可变业务规则”彻底解耦。算法骨架冻结，规则由配置驱动自动编译。
* **公共接口 (Interface)**：
  ```python
  from src.kernel import compile_rules, solve_lexicographic, validate_params

  # 1. 编译规则至 CP-SAT
  compiled: CompiledRules = compile_rules(rule_instances, compile_context)

  # 2. 多目标严格分层字典序求解
  result: LexicographicResult = solve_lexicographic(cp_model, priority_groups)
  ```
* **深度实现 (Implementation)**：
  * 隐藏 19 类算子（`capacity`, `min_batch`, `due_date`, `labor_capacity`, `concurrency` 等）底层整数线性约束与惩罚项构建；
  * 隐藏工厂/车间/产线/料号 4 级作用域（Scope）继承与替换逻辑；
  * 隐藏软约束组内权重归一化与字典序边界（Bound Freezing）迭代收敛。
* **接缝 (Seam)**：`CompileContext` 协议。调度器只需提供变量映射投影，内核即可任意插拔。
* **杠杆 (Leverage)**：工厂新增或调整排产规则，无需修改 `scheduler.py` 任何一行代码。

---

### 2.2 独立二次审计模块 (`src/audit`)
* **设计意图**：作为完全独立的“外部客观裁判”，杜绝校验器自证陷阱。
* **公共接口 (Interface)**：
  ```python
  from src.audit import audit_schedule, AuditConfig, AuditReport

  report: AuditReport = audit_schedule(snapshot, plan_rows, shift_infos, config)
  ```
* **深度实现 (Implementation)**：
  * **零引用隔离**：代码物理级禁止 `import scheduler` 或使用求解器中间变量；
  * **时序离散仿真**：基于原始 BOM 与期初库存，逐班次前向动态推演库存账本，精确识别断线断料与安全齐套余量；
  * **9 项全要素审计**：涵盖需求覆盖完整性、班次拆解守恒、单班物理容量、物料齐套、MPQ 整数件、工单连续性、班次利用率（含尾班豁免）、交付达成率与冻结期防篡改。
* **接缝 (Seam)**：`PlanRow` 与 `ShiftInfo` 纯数据契约（Data Transfer Object）。无论来自算法输出、手工排产 Excel 还是第三方 MES，均穿过同一接缝接受统一裁决。
* **局部性 (Locality)**：所有合规判定规则全量收敛于此，测试通过变异矩阵（Mutation Matrix）直接打击该接缝。

---

### 2.3 规则集服务模块 (`src/rule_sets`)
* **设计意图**：提供面向工厂计划员与生产主管的企业级规则治理平台。
* **公共接口 (Interface)**：
  ```python
  from src.rule_sets import active_rule_set, save_payload, export_active_excel, import_excel, activate
  ```
* **深度实现 (Implementation)**：
  * 隐藏基于 SQLite 的规则版本库（存储、版本切换、语义 Diff 与历史回滚）；
  * 隐藏双向 Excel 模板生成与解析，支持单元格级报错定位；
  * 隐藏规则冲突与无解自动诊断引擎（`diagnose.py`）与敏感度分析（`sensitivity.py`）。
* **接缝 (Seam)**：REST API / CLI 业务服务接缝。

---

### 2.4 工厂拓扑与排班日历模块 (`src/shift_manager`)
* **设计意图**：封装物理制造现场的设备工作制与日历约束。
* **公共接口 (Interface)**：
  ```python
  from src.shift_manager import get_topology, generate_line_calendar, CalendarMode
  ```
* **深度实现 (Implementation)**：
  * 统一双工厂物理拓扑：温岭工厂（WE34: 8h × 3班，连续流水）与沈阳工厂（SY38: 12h × 2班，注塑涂装）；
  * 封装做四休三、做五休二与 7×3 连产日历模式生成与班次时间窗口换算。
* **接缝 (Seam)**：`PlantTopology` 数据接缝。调度器与审计引擎均通过此接缝获取额定工时。

---

## 3. 测试表面与回归矩阵 (The Interface is the Test Surface)

根据测试设计原则，**测试表面即为模块的公开接缝**。`tests/` 目录下的 15 大自动化测试套件与系统接缝的映射关系如下：

| 测试套件文件 | 覆盖的模块接缝 | 核心验证不变量 |
|---|---|---|
| `tests/test_domain.py` | `src/domain/` 实体层 | Site/Workshop/Line 拓扑、ShiftSlot 值对象、多线 Routing 候选能力 |
| `tests/test_kernel.py` | `src/kernel/` 公共接口 | 19 个模板编译、参数 Schema、作用域继承、字典序分层优化、权重归一化 |
| `tests/test_independent_audit.py` | `src/audit/` 公共接口 | 独立性（零依赖求解器）、20 类变异矩阵拦截（删单、超载、跳序、篡改、欠产） |
| `tests/test_golden_baseline.py` | `src/scheduler.py` + 内核 | 黄金基线 19 单回归、净需求守恒、105% 产能覆盖、过载告警 |
| `tests/test_split_batch.py` | `scheduler` 拆批连续性 | 超班单批（WA14208S 8.45h > 8h）相邻班次连续拆段、MPQ 豁免、换型合并 |
| `tests/test_rule_sets.py` | `src/rule_sets/` 服务接口 | SQLite 版本管理、Excel 双向导入导出幂等性、敏感度分析、冲突诊断 |
| `tests/test_labor.py` | `src/labor.py` 主数据 | 产线定员定额、考勤日历可用工时校验、异常数据防御拦截 |
| `tests/test_aps_pipeline.py` | 端到端流水线集成 | 双工厂加载、10 天全流程排程、17 项硬校验、Excel 冻结窗回写与手工上传闭环 |
| `tests/test_regressions.py` | REG-01~08 缺陷回归 | 时序物料账本、日历槽位、安全库存补产、未知料号、冻结期、FIFO 交期、计划窗口、xlsx 路径 |
| `tests/test_sequence.py` | `src/sequence.py` C1-a | 同模优先、SDST 换模矩阵、班内/跨班序列原语 |
| `tests/test_routing.py` | `src/routing.py` C2-a | 路线表加载、候选/认证机台、主候选线 |
| `tests/test_rule_catalog.py` | `src/rule_catalog.py` | 32 条目录唯一性、HC/SC/BS 分类、site override 不污染全局 |
| `tests/test_inventory_policy.py` | `src/inventory_policy.py` B4-a | NORMAL/MONTH_END 策略、月末窗口、双工厂覆盖 |
| `tests/test_capacity_kernel.py` | `src/scheduler.py` + active rule set | capacity kernel 目标接线（P0 回归）、C1-b-1 sequence/setup 求解 |

---

## 4. 待演进接缝与重构路线 (Phase 2 规划)

当前系统仍存在部分浅模块与需要进一步收敛的接缝，将在 Phase 2 持续深化：

1. **`src/rules.py` 渐进退役**：
   * *现状*：仍保留 27+9 条硬编码规则文字说明，主要作为前端静态兼容字典。
   * *方向*：完全由 `config/rule_sets/*.json` 与 `src/kernel/rule_mapping.py` 接管，退役静态硬编码。
2. **`src/server.py` 路由瘦身**：
   * *现状*：文件约 2,547 行；`src/services/manual_audit.py` 已抽取，但 `/api/audit_manual_schedule` 仍使用 `server.py` 内的旧编排，服务层尚未接线。
   * *方向*：接线 service 并删除重复路由，使 `server.py` 成为纯 HTTP Adapter。
3. **数据模型实体化 (ACT-30 ~ ACT-32)**：
   * *现状*：`src/domain/` 已落 Site/Workshop/Line/Routing/Part 值对象与 `test_domain.py`，但 scheduler/server 仍使用字符串绑定；`routing` 算子未实现。
   * *方向*：把 domain 实体接入 data_loader/scheduler，并在内核中激活 `routing` 实现多产线选择。
