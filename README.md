# 自研智能排产系统 (APS MVP)

基于 Google OR-Tools (CP-SAT) 运筹优化求解器的多基地双阶段自动排产决策中枢（MVP 工业演示版）。

> **Slogan**：*精益平准 · 智排未来：面向两阶段协同的高级排产与动态平衡决策中枢*

## 1. 项目背景与目标

本项目针对流程/离散制造企业的排产痛点，旨在将物流排产专家的经验规则算法化、工程化：
* **多工厂统一支持**：
  * **WL 工厂 (WE34 车间)**：雨刮总装线 `PGLINKAGE` + 冲压线 `轴套6`，8 小时 × 3 班制（27 项排产规则体系）；
  * **SY38 工厂 (沈阳产线)**：自动化涂装线 `PU07/PU22` + 大型注塑机 `1400T-6`，12 小时 × 2 班制（9 项专属注塑涂装规则体系）。
* **核心哲学**：以**准时交付第一**为最高优先级，兼顾产线高利用率与连续紧凑排产；全面引入 **30 天需求前瞻平准合批机制**，在月初低负荷时顺位拉动在产料号远期需求，削峰填谷，囤积安全库存并消除碎片小单换型浪费。
* **物理约束**：
  * **净需求排产**：`需要生产数量 = 客户需求 - 可用期初库存 + 安全库存(Target Stock)`，库存已覆盖的需求行不生成生产任务。
  * **上下工序按班次累计齐套**：`x` 班次的半成品产出（含库存已覆盖部分）可供 `x+1` 班次装配，两阶段按件流水搭接，不再是"半成品全部完工后成品才能开工"。
  * **MPQ 分级取整**：成品总量与各班次产出严格按 MPQ 整倍数排产（如 48、96、192）；半成品只要求**总量**为整数件且 ≥ MPQ，单班可以生产任意正整数件（甚至 1 件），不再要求单班 ≥ MPQ。
  * **Option B 三班制**：早班 (07:00~15:00)、中班 (15:00~23:00)、晚班 (23:00~07:00)，班次即模型的最小时间原子。
  * **班次产能利用率 ≥ 95%**（WE34 第一优先级）：按"产线 × 班次"聚合口径校验，每条产线允许最后一个收线班次不足 95%。
  * **单班最小投产批量**：成品一个班次开工某型号时，当班产出按 MPQ 批次取整；半成品 MPQ 是**订单总量门槛**，总量达标后单班可逐件/小批生产，避免为凑单班批量把负荷推到 105%。
  * **换型集中**：换型按"型号连续生产块"计数并计价，块数最少即换型次数最少；同一条产线上优先把一个型号跑完再切下一个。
  * **毫秒整数时间标度**：单件工时 `CT / TRP` 是非整数秒（如 27/0.85 ≈ 31.76s），CP-SAT 只接受整数系数，模型内部统一以毫秒计时长、以"件"计半成品产量。

## 2. 目录结构

```text
APS_MVP/
├── ARCHITECTURE.md                      # 系统架构设计说明书 (Deep Modules 规范、接缝与测试表面)
├── dataSource.xlsx                      # 标准主数据源与排产输入/输出工作簿
├── requirements.txt                     # Python 依赖项 (openpyxl, ortools, fastapi, uvicorn, ...)
├── run_web.py                           # Web 服务一键启动入口 (支持代码热重载)
├── run_tests.py                         # 统一自动化测试运行器 (支持 --fast 极速单测模式)
├── config/                              # 工厂排产规则配置库
│   ├── rule_catalog/                    # 32 条全局规则目录 / 能力状态
│   └── rule_sets/                       # 双工厂 Phase 1 基线与 Q4 规则集 (JSON)
├── data/                                # 主数据与业务输入源
│   ├── *.csv                            # BOM、需求、期初库存、参数、模具、PM与到货
│   ├── master/                          # 扩展主数据 mock：Routing/Setup/LeadTime/Inventory/Calendar/...
│   └── templates/labor/                 # 人工劳动力定员三阶模板 (Crew-Pool, Availability, Line-Staffing)
├── Specification/                       # 业务功能规格与工程治理规范 (5 份现行文档)
│   ├── aps_functional_spec.md           # 业务功能规格书 (v0.7 工业级数据闭环)
│   ├── aps_new_rule_set_gap_analysis.md # 32 条新规则清单与规则内核能力对账
│   ├── aps_rule_set_editor_manual.md    # 规则集编辑器操作手册与 19 个模板参数详表
│   ├── aps_implementation_log.md        # 实施日志与代码变更记录台账
│   └── aps_progress_dashboard.md        # 当前进度、能力矩阵与关键缺口
├── archive/                             # 历史归档 (只读留存，不参与构建)
│   ├── Specification/                   # 已被取代的 10 份规格/评审/整改文档
│   └── data/                            # 旧版 APS Database 三份草稿备份
├── src/                                 # 系统核心源代码
│   ├── domain/                          # 核心领域实体层 (Site, Line, ShiftSlot 值对象, Routing 多线工艺路线, Part)
│   ├── services/                        # 应用编排服务层 (手工排产 Excel 审计解析、DTO 转换)
│   ├── kernel/                          # 规则内核平台深模块 (19 算子编译、多目标字典序求解、作用域继承)
│   ├── audit/                           # 独立二次审计引擎深模块 (零依赖求解器、时序动态离散仿真账本)
│   ├── rule_sets/                       # 规则集服务深模块 (SQLite 版本库、Excel 双向转换、冲突诊断)
│   ├── static/                          # 前端工程控制台 (Vue + Tailwind 工业级工作台与规则编辑器)
│   ├── scheduler.py                     # Google OR-Tools CP-SAT 核心求解器 (开班决策、大单拆批连续)
│   ├── routing.py                       # Phase C 工艺路线/候选机台数据层 (C2-a)
│   ├── inventory_policy.py              # Phase B 分时段库存策略数据层 (B4-a)
│   ├── sequence.py                      # Phase C 班内换型/序列原语 (C1-a)
│   ├── validator.py                     # 求解器内置合规校验器与穿透明细矩阵
│   ├── data_loader.py                   # 主数据加载、BOM 展开、净需求计算与快照管理
│   ├── shift_manager.py                 # 双工厂物理拓扑 (WE34 8h×3班 vs SY38 12h×2班) 与动态日历解耦
│   ├── labor.py                         # 劳动力资源考勤与定员定额主数据处理
│   ├── metric_labels.py                 # 工业合规指标与量纲中文标准化字典
│   ├── exporter.py                      # 单 Sheet 冻结窗 Production plan-Done 导出
│   ├── prechecker.py                    # 前置粗能力宏观可行性预检
│   ├── balance_builder.py               # 生产动态平衡与 PSI 数据构建
│   └── server.py                        # FastAPI 接入层（手工审计编排仍暂留此文件，待下沉 src/services/manual_audit.py）
└── tests/                               # 15 大自动化测试套件 (增加 capacity kernel/库存策略测试)
    ├── fixtures/                        # 黄金基线快照 (wl_10d_baseline.json)
    └── test_*.py                        # 单元与集成测试用例
```

## 3. 环境准备与运行

### 扩展主数据（2026-10-28）

`data/master/` 已补 32 条新规则所需的 mock 主数据：

- `Parameters-FG.csv` / `Parameters-SFN.csv`：新增 `MOQ (Minimum Order Qty)`，与既有 `MPQ` 区分；
- `Routing-Machines.csv`、`Setup-Matrix.csv`、`Operation-LeadTime.csv`；
- `Customer-Orders.csv`、`Inventory-Policy.csv`、`Trial-Windows.csv`、`Baseline-Schedule.csv`；
- `Calendar-Patterns.csv`、`Campaign-Families.csv`、`Setup-Technicians.csv`；
- `labor/` 下三张人力 mock 表。

说明与字段定义见 **[data/master/README.md](data/master/README.md)**。  
当前只有 MOQ 进入 `ProductParam/ProductionTask`；其余表是待接线 mock，不会改变现有排产。

### 安装依赖
```bash
pip install -r requirements.txt
```

### 一键启动可视化 Web 演示系统 (本地 UI)
```bash
python run_web.py
```
启动后在浏览器打开：**`http://127.0.0.1:8000`** 或 **`http://localhost:8000`**

### 单工厂实例部署（推荐）

每个工厂一个独立进程，使用 `FACTORY_SITE` 固定站点，避免共享内存状态：

```powershell
# WL / WE34
$env:FACTORY_SITE="WL"; $env:PORT="8000"; python run_web.py

# SY38
$env:FACTORY_SITE="SY38"; $env:PORT="8001"; python run_web.py
```

- WL: `http://127.0.0.1:8000`
- SY38: `http://127.0.0.1:8001`
- 固定实例会拒绝对其他站点的切换；
- 上传文件按站点保存：`data/uploads/current_WL.xlsx` / `current_SY38.xlsx`。

### Docker / 环境变量部署
```bash
cp .env.example .env          # Windows: copy .env.example .env
docker build -t aps-mvp .
docker run --env-file .env -p 8000:8000 aps-mvp
```
- 容器使用非 root 用户运行，健康检查为 `GET /health`；
- `HOST`、`PORT`、`RELOAD`、`APP_ENV`、`DEFAULT_SITE` 等变量见 `.env.example`；
- `RELOAD` 默认本地为 `true`，`APP_ENV=production` 时为 `false`。

### 自动化测试
```bash
python run_tests.py --fast   # 快速单元/回归（56 项，约 61s）
python run_tests.py          # 全量 15 个套件（99 项，含黄金基线与端到端，约 23 分钟）
```

Web 界面主要特性：
- **工厂站点一键切换 (WL / SY38)**：支持多基地无缝切换，日历（8h×3班 vs 12h×2班）、产线设备、订单、BOM、专属规则及生产策略全要素联动；
- **① 生产平衡与负荷总览 (参考 `Production Plan Validation.xlsx`)**：
  - 顶部产线负荷概览：按班次展示 `Loading Hours`、`TRP`、`Loading Rate`（利用率 95%~105% 达标高亮显示，空班带 `ℹ️` 算法决策考量诊断气泡）；
  - 标题栏产品状态过滤器：一键切换“只看排产计划的产品”与“看所有”，精准聚焦在产料号；
  - 底部物料动态平衡表 (PSI)：按物料展开 4 行（`Customer demand`、`Production plan`、`Stock Balance`、`Coverage days`），支持产成品 (FG) 与半成品 (SFIN) 分页或同屏展示；
- **② 排产工单甘特明细 (按工单独立泳道 + 班次小段)**：
  - 不再提供“产线聚合单轨”；同一料号的多张工单各自占一条子泳道，避免互相遮挡；
  - 每个班次一个生产小段，段中心用顺位圆点 + 折线连接，首段高亮；明细表按段展开，显示全局顺位、段序号、本段产量/工时与逐段换型；
- **③ 规则与约束配置 (工厂专属规则体系)**：
  - WL 工厂呈现 27 条全要素排产规则，SY38 呈现 9 条注塑涂装专属规则；
  - 支持 **9 大业务类型**与 **5 级可行性约束**双维极速筛选；
  - 突出**一句话精炼规则名称**，支持点击一键展开 **Col G 规范化 AI 逻辑代码卡片**（数学公式、业务边界与场景示例）；
  - 支持独立勾选控制，动态联动 CP-SAT 求解引擎与后置独立审计；
- **④ 输入与排产主数据工作台 (工业级浏览·变更·分析)**：
  - 在网页中直接维护订单需求数量、交期、期初库存；
  - 包含模具工装约束看板、设备 PM 维保看板、关键物料到货看板与客户战略评级看板；
- **⑤ 规则合规审计矩阵与手工 Excel 上传闭环**：
  - `/api/solve` 额外返回 `inventory_projection`（B4-b-a）：按班次推演 FG/SF 库存并按 NORMAL/MONTH_END 策略标违规；
  - **核心计算数据穿透**：无论 PASS 还是 FAIL，17 项独立硬指标验证器与规则均支持展开查看公式算式、核心 KPI 徽章及 step-by-step 逐班验算底表（如 R-WL-11 跨班连续生产继承免换模计算）；
  - **手工调整排产 Excel 上传独立二次审计**：支持将导出的排产文件在本地手工调整后重新上传，一键执行独立合规审计并即时标红违规，支持与算法原方案自由比对；
- **⑥ ⚙️ 生产策略与系统设置 (全新专属工作台)**：
  - 灵活启闭 **30 天需求前瞻平准合批**，自定义前瞻拉单天数与上限；
  - 调节计划周期（5/10/15天）、工作制模式（连续生产/做五休二/做四休三）；
  - 所有策略依托工厂站点独立持久化存储；
- **执行运算与导出**：一键触发求解（带动态计时与状态反馈），支持点击【导出结果 Excel】下载生成好的排产工作簿。

### 一键执行控制台排产 (CLI)
```bash
python src/main.py
```
流程执行步骤：
1. **前置粗能力宏观预检 (`prechecker.py`)**：核算产线总工时负荷与交期瓶颈，发生绝对超载时抛出 `CAPACITY_OVERLOAD_EXCEPTION` 并生成《交期调整建议表》；
2. **三阶排班日历推荐 (`shift_manager.py`)**：按负荷比自动推荐并绑定日历模式（$\le 4/7$ 做四休三、$\le 5/7$ 做五休二、$> 5/7$ 连续生产）；
3. **CP-SAT 多阶段优化求解 (`scheduler.py`)**：
   - 求解预算 90 秒，深度融入顺位前拉、冻结期时间围栏、模具物理独占、BOM关键物料到货齐套、设备PM维保避让、战略客户保供等全要素约束；
4. **报表与 Excel 回写 (`exporter.py`)**：
   - 自动回写至 `APS Database- Simulation draft.xlsx` 的 **`Production plan-Done`** 表格（分班产量、工时汇总与利用率预警）；
5. **17 项独立硬指标校验与 36 条规则遵循矩阵 (`validator.py`)**：
   - 独立验算 17 项指标（全数达成），并在控制台自动输出 36 条规则（WL 27 + SY38 9）遵循全景状态矩阵。

> 口径说明：半成品总量 $\ge$ MPQ 后按件排产，单班可为任意正整数件；成品按 MPQ 整数倍排产；换型集中打磨将单班并行型号数严格压制在 $\le 3$ 满足 R-FG-13；`准时交付` 在全部运行中保持 100.0%。
