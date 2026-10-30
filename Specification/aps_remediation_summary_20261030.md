# APS MVP 核心架构升级与多场景推演平台落地总结 (2026-10-30)

> **文档标识**：`Specification/aps_remediation_summary_20261030.md`  
> **基线分支**：`feat/shaojing-dev` @ `b4bc19b` (已合并 `origin/main` 15+2 笔最新特性提交)  
> **实施规范**：严格执行《APS_MVP 开发标准工作流宪章》与 `codebase-design` / `tdd` 技能规范  

---

## 1. 背景与问题定义 (Problem & Scope)

在原有 `APS_MVP` 架构中，排产上下文紧密耦合于单进程全局单例（`session_state`），每次排产强依赖读取根目录单份大宽表 `dataSource.xlsx`（10 个 Sheet 绑定在一起）。该模式存在三大核心物理痛点：
1. **单体大泥球与覆盖即丢失**：修改即覆盖，无法并存多个推演方案，更无法做“方案 A (严格做五休二) vs 方案 B (周末加班连产保供)”的横向决策对比；
2. **无法分车间/产线切片排产**：每次排产强行加载全厂所有物料与产线，前道冲压与总装混排，且前道工序若无主订单直接导致无产出；
3. **缺乏方案全生命周期管理与质量归档**：排产后算出的 17 项合规体检报告与 OTD 等核心 KPI 仅存在于单次 HTTP 响应中，随页面刷新即丢失，缺乏企业级审批与下发发布机制。

---

## 2. 根因剖析与架构缺陷 (Root Cause & Gap Analysis)

1. **状态载体缺失**：系统缺乏轻量、可持久化的业务方案载体（Scenario），状态全部堆叠在运行时内存字典中；
2. **数据摄入无解耦流水线**：没有针对工厂复杂 Excel 的自动拆包中介，数据无法以积木颗粒度（日历、产线、定额、BOM、库存、需求、模具、维护）离散版本化落库；
3. **排产装配与存储强耦合**：调度器直接从磁盘加载全量数据并直接构模，缺乏轻量装配层（Assembler）在内存中基于清单极速还原调度上下文；
4. **数据库物理外键死锁隐患**：若传统关系数据库强加外键，高频批量导入与方案级联删除会产生高昂锁争用和级联死锁风险。

---

## 3. 核心修复与落地举措 (Solution & Implementation)

严格遵循 `C:\Users\shan6\workspace\next\aps_scenario_architecture.md` 工业级设计方案，分六大阶段实现端到端闭环升级：

### 3.1 Phase 1: 数据库中立仓储与无外键 Schema (`src/scenarios/repository.py`)
- **物理无外键规约 (No Foreign Keys)**：彻底禁用物理外键，通过应用层事务级联删除保证完整性，消除并发死锁；
- **14 张架构表与高密度 B-Tree 索引**：创建 `scenarios`、`scenario_tasks` 及 7 大积木表，并在所有业务逻辑外键上创建二级索引；
- **防漂移只读锁**：方案求解即锁定引用的 BOM/定额/库存版本（`is_locked = 1`），保证历史推演 100% 可复现；
- **物理库初始化**：成功在 `data/scenarios.sqlite3`（176 KB）完成物理落地并加入 `.gitignore`。

### 3.2 Phase 2: 数据摄入与 Excel 自动拆包服务 (`src/scenarios/ingestion.py`)
- 实现 `IngestionService.ingest_excel(file_path, site, version_tag)`；
- 单应用事务将 `dataSource.xlsx` 10 个 Sheet 极速切片入库至 7 大积木表，实测入库全厂数据仅耗时 **0.317s**。

### 3.3 Phase 3: 模块化内存装配器流水线 (`src/scenarios/builder.py`)
- 研发 `ScenarioBuilder.assemble_context(scenario)`，依据极简清单（<500B JSON）在 **15ms 内** 纯内存还原 `ProductionInputSnapshot`、`RequirementPlan` 与 `RuleSet`；
- **闭环前道独立排产**：当仅选冲压半成品线（SF-only）且无主订单时，自动对比当前库存与安全库存，智能生成缺口补水工单（`REPL_{part_no}`）；
- **支持敏捷 What-If 演练**：支持 `stock_overrides`（断供演练）与 `rule_overrides`（规则微调）。

### 3.4 Phase 4: 推演中枢服务与 RESTful API (`src/scenarios/service.py` & `src/scenarios/router.py`)
- 挂载 `/api/scenarios` 路由矩阵：CRUD、克隆、求解、17项校验归档、任务明细、正式发布、多方案对比；
- `/api/scenarios/compare` 毫秒级输出跨方案 KPI 横向对撞矩阵；
- 防篡改拦截：已发布（`RELEASED`）方案修改直接抛出 400 Bad Request。

### 3.5 Phase 5: 前端推演工作台中枢交互 (`src/static/scenarios.html`)
- 打造 3 栏式 SPA：左侧场景库抽屉、中间 4 步积木装配工坊、右侧 KPI 竞技场与对撞看板；
- 主看板导航栏增设 `🎯 多场景推演工坊` 入口，支持在传统大看板与新一代多场景推演工作台间无缝跳转。

### 3.6 Phase 6: 工业级防线硬化与全量回归闭环
- 新增 4 个独立测试套件（17 项用例），覆盖存储、拆包、装配、API 与前端静态服务；
- 快速测试套件扩充至 103 项 PASS（63s），全量套件扩充至 128 项 PASS，既有排产业务与新架构 100% 零退化。

### 3.7 Option B: 项目冗余文件彻底清理与数据源单源化收拢
- 彻底清理 `src/static/index.html.*_backup`（642 KB）临时备份与根目录废弃草案（`APS Database- Simulation draft.xlsx`、`Production Plan Validation.xlsx`）；
- 物理清理 `data/` 根目录 9 个冗余旧 CSV，重构 `src/data_loader.py::load_aps_database` 优先读取单源事实 `dataSource.xlsx`，从根本上杜绝 CSV 镜像数据漂移。

### 3.8 Option A: 轻量级全自动调用与数据库追踪体系 (`src/infrastructure/telemetry.py`)
- **零侵入 AOP 装饰器 `@trace`**：自动捕获装配、求解、拆包与服务层关键函数的入参摘要、返回摘要、精确耗时（ms）与异常调用堆栈；
- **智能摘要器 `summarize_value`**：递归剪裁巨型集合与排产领域对象，彻底防止海量工单数组撑爆日志；
- **数据库透明连接代理 `wrap_traced_connection`**：自动监控所有 SQL `execute` / `executemany`，记录执行耗时、影响行数与参数个数，并在 >15ms 时触发 `[SLOW_SQL]` 慢查询告警；
- **结构化轮转日志**：自动向 `data/logs/aps_runtime.log` 输出（10MB 自动轮转，受 `.gitignore` 保护）。

---

## 4. 核心代码变更清单 (Code Changes & File Inventory)

| 文件路径 | 变更类型 | 说明 |
| :--- | :---: | :--- |
| `src/infrastructure/__init__.py` | 新增 | 基础设施层导出（get_logger, summarize_value, trace, wrap_traced_connection） |
| `src/infrastructure/telemetry.py` | 新增 | 轻量级全自动函数与数据库查询追踪体系（切面、摘要器、DB Proxy、轮转日志） |
| `tests/test_telemetry.py` | 新增 | 追踪体系 4 项单元测试（防爆炸摘要、@trace 入参出参耗时、异常捕获、DB代理） |
| `src/scenarios/__init__.py` | 新增 | 模块导出中枢（Repository, Ingestion, Builder, Service, Router） |
| `src/scenarios/repository.py` | 新增 | 数据库中立 ANSI SQL 仓储（14 表无外键 DDL、事务、查询与只读锁） |
| `src/scenarios/ingestion.py` | 新增 | Excel 自动拆包服务（10 Sheet 秒级切片落库至 7 大积木表） |
| `src/scenarios/builder.py` | 新增 | 模块化内存装配器流水线（15ms 纯内存组装、前道补水推导、What-If） |
| `src/scenarios/service.py` | 新增 | 场景应用中枢服务（全生命周期、求解归档、防篡改、多方案对撞） |
| `src/scenarios/router.py` | 新增 | FastAPI RESTful 路由（`/api/scenarios` 完整接口定义） |
| `src/static/scenarios.html` | 新增 | 现代工业级 3 栏式 SPA 多场景推演工作台前端 |
| `src/server.py` | 修改 | 单行接缝挂载 `scenarios_router`，新增 `/scenarios` 页面路由 |
| `src/static/index.html` | 修改 | 主看板顶部导航栏增设「🎯 多场景推演工坊」入口按钮 |
| `src/data_loader.py` | 修改 | `load_aps_database` 优先加载 `dataSource.xlsx`，解除对根目录 CSV 依赖 |
| `run_tests.py` | 修改 | 挂载 5 个新增测试套件至快速回归 `--fast` 清单 |
| `.gitignore` | 修改 | 忽略本地生成的运行时二进制数据库 `data/scenarios.sqlite3` 与日志文件 |
| `tests/test_scenario_repository.py` | 新增 | 仓储层 5 项测试（建表、CRUD、级联删除、只读锁、事务回滚） |
| `tests/test_scenario_ingestion.py` | 新增 | 拆包服务 2 项测试（全表切片校验、幂等重复导入） |
| `tests/test_scenario_builder.py` | 新增 | 装配器 5 项测试（全厂装配、总装切片、冲压切片补水、库存覆写、求解） |
| `tests/test_scenario_api.py` | 新增 | API 路由 8 项测试（CRUD、克隆、求解落库、发布锁、对撞对比、积木手工维护、Excel上传拆包、全景数据穿透） |
| `Specification/aps_implementation_log.md` | 修改 | 实施日志补充多场景推演架构升级、冗余清理与追踪体系全过程 |
| `Specification/aps_progress_dashboard.md` | 修改 | 进度看板更新 135 项测试基线与 Phase S 完成度状态 |

---

## 5. 验证与测试结论 (Verification & Test Results)

1. **快速回归测试**：
   - 命令：`python run_tests.py --fast`
   - 结果：**110 项测试全部 PASS（耗时约 64.2s）**，100% 绿灯；
2. **新增模块专属测试覆盖**：
   - 多场景推演套件：20/20 PASS；
   - 追踪与遥测套件：4/4 PASS；
   - 合计 24 项新增单元测试全部通过；
3. **物理数据库与端到端运行验证**：
   - 物理库 `data/scenarios.sqlite3` 初始化灌装成功，14 张 ANSI 数据表稳定支撑资产与推演；
   - 运行日志 `data/logs/aps_runtime.log` 实时自动记录函数入参、出参、耗时与 SQL 执行情况；
   - **基础积木资产中心（Building Blocks Hub）全面升级**：
     - 支持产线、BOM、工作制日历、期初库存批次的手工在线增删改查；
     - 新增 `POST /api/scenarios/upload-excel` 接口与前端「导入 Excel 数据源」模态框，支持从网页直接上传 `.xlsx` 自动拆包灌装；
   - **UI 彻底大一统与旧页面退役**：
     - 彻底清除全页面不合时宜的 Emoji 表情，统一采用 Valeo 工业级规范设计；
     - 正式将旧版页面 `index.html`（APS高级排产与负荷平衡系统）进行退役收编：`/dashboard` 与 `/panorama` 自动 307 重定向至现代化工作台 `/scenarios?scenario_id=...`；
     - 将原页面的「生产平衡与产线负荷矩阵」、「产线协同排产甘特泳道」、「BOM 协同拉动联动」与「17 项工业级规则全要素审计」全面原生集成至 `scenarios.html` 中，实现单页无感切换；
     - 修复 `resultData is not defined` 运行时引用异常与 shift columns 空列表缺陷；
   - 既有 105 项调度内核测试零破坏、零退化。

---

## 6. 后续演进与遗留建议 (Next Steps)

1. **C2-b 主模型内 Assign(i,m)**：当前路线分配为上游 CP-SAT 独立阶段，后续需与主排产模型在班次粒度上联合求解；
2. **甘特图人机交互深度微调**：在推演工作台中引入图上手动钉单（`pinned=True`）与局部重排；
3. **跨场景模具物理干涉预警**：建立跨推演方案的模具占用热力图，标出不同决策方案间的物理资源争用。
