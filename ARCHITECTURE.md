# APS 智能排产系统架构设计文档 (Codebase Architecture 2.0)

> **设计哲学**：基于 **Deep Modules（深模块）** 架构 —— *强类型系统、单源真相、契约分层、高局部性、全链路 TDD*。  
> **运行环境**：Python 3.12+ / Google OR-Tools CP-SAT / openpyxl / SQLite3

---

## 1. 架构总览与分层拓扑

系统彻底摒弃了旧版“单体大泥球”与分散的 ad-hoc 脚本，构建了从数据契约网关、MRP 需求展开、到 CP-SAT 字典序排产与全景报表导出的纯净管道：

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        统一接入层 (CLI & Web)                          │
│     aps.cli (命令行子命令工具集)   |   aps.web/ (现代化看板资源与模板)   │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼─────────────────────────────────────┐
│                   核心排程求解层 (Scheduling & Optimization)           │
│  • aps.model.context: StructureContext 决策变量矩阵与紧致域收缩        │
│  • aps.model.rules: 9 大物理与产能基石硬约束 (MPQ/流水平衡/利用率底线) │
│  • aps.model.solver: 4 层字典序多目标优化引擎 (OTD/负荷/换型/在制品)   │
│  • aps.model.plan_row: PlanRow DTO 唯一排产接缝与 LineShiftSummary     │
│  • aps.model.report: 规则编译器生效状态与 BindingReport                │
└──────────────┬───────────────────────────────────┬─────────────────────┘
               │                                   │
┌──────────────▼────────────────────┐ ┌────────────▼─────────────────────┐
│       数据集聚合根 (DataSet)       │ │       全景计划导出 (Export)       │
│           aps.dataset             │ │          aps.export.excel         │
│  • FIFO 逐行可用库存冲销           │ │  • 生产计划明细 Plan Details      │
│  • 自动化 BOM MRP 半成品工单展开   │ │  • 产线班次负荷 Line Shifts       │
│  • 三级换模工时回退检索           │ │  • 订单交付汇总 Order Delivery    │
│  • 额定产能确定性派生             │ │  • 求解报告 Solver Report         │
└──────────────┬────────────────────┘ └──────────────────────────────────┘
               │
┌──────────────▼─────────────────────────────────────────────────────────┐
│                    日历时钟几何建模 (Calendar & Geometry)              │
│  • aps.calendar.pattern: 四档工作制 (4ON3OFF ~ CONTINUOUS) 与负荷推荐  │
│  • aps.calendar.geometry: 07:00 统一晨间时间窗与 Slot 离散化班次映射   │
└──────────────┬─────────────────────────────────────────────────────────┘
               │
┌──────────────▼─────────────────────────────────────────────────────────┐
│                     数据管道与验证网关 (Data Pipeline)                 │
│  • aps.data.excel: 动态表头扫描、34 列需求宽表 unpivot、模具去重合成   │
│  • aps.data.validate: 四道渐进校验门 (类型门/参照门/业务门/重复键门)   │
│  • aps.data.importer: 强隔离物理断流与整站原子快照替换                 │
│  • aps.data.template_v3: 由 REGISTRY 动态驱动的 V3 规范空白模板生成器  │
│  • aps.data.sqlite: 纯 ANSI DDL、外键索引自动推导、事务 SqliteRepository │
└──────────────┬─────────────────────────────────────────────────────────┘
               │
┌──────────────▼─────────────────────────────────────────────────────────┐
│                     契约类型与实体登记表 (Schema & Types)              │
│  • aps.schema.types: 8 种数据类型、7 种标量解析器、T1/T2 契约分级      │
│  • aps.schema.registry: 23 个核心与扩展实体元数据、自检机制            │
│  • aps.schema.ddl: 确定性拓扑排序 DDL 生成器                           │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心模块职责说明

### 2.1 契约与类型层 (`aps.schema`)
- **`types.py`**：定义 `DataType`, `ContractTier` (T1必填 / T2选填降级), `DupPolicy` (REJECT/SUM/FIRST)，提供具备中文括号提取、枚举容错与时间清洗的高韧性标量解析器。
- **`registry.py`**：全系统单一真相源（SSOT）。登记 23 个实体（7 维表、12 事实表、4 个 V3 扩展表），包含字段类型、主外键、来源列映射与反向自检逻辑。
- **`ddl.py`**：纯 ANSI SQL DDL 生成器。根据实体拓扑依赖生成建表语句，自动为复合主键表被引用的单列生成 `UNIQUE INDEX` 保证 SQLite `PRAGMA foreign_keys = ON` 物理有效。

### 2.2 数据入库与校验层 (`aps.data`)
- **`excel.py`**：Excel 读取适配器。自适应扫描前 6 行表头，处理需求宽表 unpivot，多产线模具拆分与合成零件主数据。
- **`validate.py`**：实现四道渐进校验门。发现 Error 立即中断；T2 选填字段留空自动记录为 Gap 并在求解时安全降级；ERP 账实差异负库存自动置零。
- **`importer.py`**：全流程导入编排。严格遵循“存在任何 Error 连数据库文件都不创建”的物理断流原则；校验通过后在单事务中执行整站快照替换。
- **`template_v3.py`**：直接由登记表派生生成 18 张表 + 完整说明页的规范 V3 模板，支持 T1/T2 视觉背景分级。

### 2.3 日历时钟几何 (`aps.calendar`)
- **`pattern.py`**：定义 4 档标准工作制（`4ON3OFF`, `5ON2OFF`, `6ON1OFF`, `CONTINUOUS`），支持基于周负荷比率自动推荐工作制。
- **`geometry.py`**：离散化班次槽位。以 `(line_code, work_date, shift_index)` 建模 `Slot`，双工厂统一采用 07:00 晨间起始时刻。

### 2.4 数据集与 MRP 展开 (`aps.dataset`)
- **`dataset.py`**：排产引擎的聚合根。由成品净需求 FIFO 逐行冲销，结合 BOM 单耗自动展开半成品毛需求并冲销半成品库存，派生出完整的 FG/SF 排产工单体系；内建三级换模工时回退机制。

### 2.5 CP-SAT 求解模型 (`aps.model`)
- **`context.py`**：`StructureContext`。构建强类型决策变量矩阵（`assign`, `x`, `active`, `started`, `cum`, `end_slot`, `late_shifts`, `load_ms`, `is_open`, `is_tail`, `block_start`），上界紧致收敛。
- **`rules.py`**：9 大基石硬约束构建，内建 Assumption Literals 假设体系；支持 $O(1)$ 哈希索引秒级构建。
- **`solver.py`**：4 层字典序多目标优化引擎（P1 交付率 -> P2 产能合规 -> P3 换模平滑 -> P4 在制品控制），实现 `SolutionSnapshot` 解快照隔离保护与最小冲突集（MUS）人话诊断。
- **`plan_row.py` & `report.py`**：调度方案输出标准承载接缝与规则编译器报告。

### 2.6 导出与 CLI 层 (`aps.export`, `aps.cli`)
- **`export/excel.py`**：openpyxl 高性能流式导出包含 4 张规范工作表的生产计划总表。
- **`cli.py`**：标准统一命令行入口，提供 `template`, `verify-template`, `import`, `show`, `dataset`, `solve` 6 大子命令。

---

## 3. 测试与验证标准

代码库全面贯彻 **TDD（测试驱动开发）**：
- 测试套件目录：`aps_tests/`
- 测试模块：13 个测试文件
- 单元测试与集成测试总计：**418 项**
- 执行时间：全套测试 **< 10 秒**（100% 绿灯）
- 测试运行入口：`python run_tests.py --fast`
