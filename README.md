# 自研智能排产系统 (APS 2.0)

基于 Google OR-Tools (CP-SAT) 运筹优化求解器的现代离散/流水制造高级排产系统（架构 2.0 纯净版）。

> **Slogan**：*精益平准 · 智排未来：面向两阶段协同的高级排产与动态平衡决策中枢*

---

## 1. 架构与技术特色

- **零依赖与极速响应**：严格遵循纯净依赖原则（无 pandas / pydantic / sqlalchemy），基于 Python 3.12 原生标准库 + OR-Tools + openpyxl 构建。全量 418 项自动化测试 10 秒内全绿通过。
- **单源真相与类型网关 (SSOT & Type Gate)**：
  - 核心定义 23 个实体注册表（7 维表、12 事实表、4 个 V3 扩展表）；
  - 四道渐进校验门（类型门、参照门、业务门、重复键合并门），错误绝对物理断流；
  - 自动将 T2 选填空列标记为 Gap 并实施求解降级，ERP 负库存安全置零。
- **MRP BOM 需求展开与库存冲销**：
  - 净需求 FIFO 逐行核销；
  - 由成品净需求自动展开 BOM 形成半成品毛需求，经半成品期初可用库存冲销后自动生成半成品工单；
  - 支持换模工时三级回退检索（换模矩阵 -> 模具基准 -> 工艺路线）。
- **CP-SAT 4 层字典序多目标优化 (Lexicographic Optimization)**：
  - **P1**: 客户订单准时交付率（OTD 延误班次最小化）；
  - **P2**: 单班 95% 产能利用率底线合规（带收线末班自动豁免机制）；
  - **P3**: 任务连续开工块最小化（换型平滑）；
  - **P4**: 紧凑生产与在制品库存控制。
- **不可行人话诊断 (Explainable Infeasibility)**：
  - 求解器为基石硬约束配备 Assumption Literals；遇不可行时秒级定位最小冲突集（MUS）并输出清晰业务解释。
- **全景报表导出 (Excel 4 工作表)**：
  - Sheet 1: `生产计划明细 Plan Details`
  - Sheet 2: `产线班次负荷 Line Shifts`
  - Sheet 3: `订单交付汇总 Order Delivery`
  - Sheet 4: `求解报告 Solver Report`

---

## 2. 目录结构

```text
APS_MVP/
├── aps/                                # 核心系统源码包
│   ├── calendar/                       # 四档工作制推导与 Slot 班次几何建模
│   ├── data/                           # 校验网关、Excel 适配、SQLite 快照仓储与模板生成
│   ├── export/                         # 4 工作表 Excel 排产总表流式导出器
│   ├── model/                          # CP-SAT 变量矩阵、9 大基石约束与 4 层字典序求解器
│   ├── schema/                         # 23 个实体注册表、标量解析器与纯 ANSI DDL
│   ├── web/                            # 现代化数据看板 Web 界面与静态资源
│   ├── cli.py                          # 统一命令行接口 (CLI)
│   └── dataset.py                      # 数据集聚合根 (MRP BOM 展开与 FIFO 净需求核销)
├── aps_tests/                          # 418 项自动化单元与集成测试套件
├── data/                               # 运行时输出数据目录 (数据库与排产表)
├── design-system/                      # 现代化 UI/UX 设计系统 (MASTER.md)
├── Specification/                      # 项目重构技术方案、实施日志与工业闭环台账
├── .env.example                        # 环境变量模板
├── .gitignore                          # Git 忽略配置
├── APS_DataSource_Template - WL -V2.xlsx # 工厂标准输入模板 (官方唯一输入源)
├── ARCHITECTURE.md                     # 架构 2.0 设计文档与分层拓扑图
├── Dockerfile                          # 生产级轻量容器配置
├── README.md                           # 本说明文档
├── requirements.txt                    # 生产依赖项清单
└── run_tests.py                        # 统一测试套件运行器
```

---

## 3. 快速上手

### 3.1 运行测试套件
```bash
# 运行全量测试套件 (418 tests)
python run_tests.py

# 极速运行测试
python run_tests.py --fast
```

### 3.2 命令行接口 (CLI)
系统内置统一命令行工具 `aps.cli`，支持以下子命令：

#### 1. 生成空白 V3 规范模板
```bash
python -m aps.cli template --out data/Template_V3.xlsx --site WL
```

#### 2. 自检并验证模板兼容性（不碰数据库）
```bash
python -m aps.cli verify-template --out data/Verify_Template.xlsx --site WL
```

#### 3. 将 Excel 数据源导入 SQLite 仓储
```bash
python -m aps.cli import --workbook "APS_DataSource_Template - WL -V2.xlsx" --db data/aps.db --site WL
```

#### 4. 查看库中实体行数与记录详情
```bash
# 查看所有 23 个实体的数据行数分布
python -m aps.cli show --db data/aps.db --site WL

# 查看具体实体的 JSON 数据
python -m aps.cli show --db data/aps.db --site WL --entity line --limit 5
```

#### 5. 检查冻结后的 DataSet 派生量
```bash
python -m aps.cli dataset --db data/aps.db --site WL --start 2026-11-03 --days 10
```

#### 6. 执行 CP-SAT 全量排产并导出 Excel
```bash
python -m aps.cli solve \
    --workbook "APS_DataSource_Template - WL -V2.xlsx" \
    --db data/aps.db \
    --site WL \
    --start 2026-11-03 \
    --days 10 \
    --out data/Production_Plan_WL.xlsx \
    --timeout 60
```
该命令会自动完成：数据源入库 ➔ 四道门校验 ➔ BOM MRP 展开 ➔ CP-SAT 字典序求解 ➔ 4 工作表 Excel 导出。
