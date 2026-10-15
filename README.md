# 自研智能排产系统 (APS MVP)

基于 Google OR-Tools (CP-SAT) 运筹优化求解器的双阶段自动排产引擎（MVP 版本）。

## 1. 项目背景与目标

本项目针对流程/离散制造企业的排产痛点，旨在将物流排产专家的经验规则算法化、工程化：
* **聚焦范围**：单一代表性产品家族（雨刮传动杆总成 `PGLINKAGE` + 自制半成品 `轴套6` 产线）。
* **核心哲学**：不追求纯数学全局最优解，100% 接受次优解；以**准时交付第一**为最高优先级，兼顾产线高利用率与连续紧凑排产。
* **物理约束**：
  * **上下工序绝对齐套**：成品组装前，BOM 对应的司机侧（DS）与乘客侧（PS）轴套半成品必须 100% 完工。
  * **MPQ 批次整数约束**：所有生产任务及各班次产出严格按 MPQ（最小生产包装单元）整倍数排产，无任何零碎小数，合理产生周转库存。
  * **Option B 三班制**：早班 (07:00~15:00)、中班 (15:00~23:00)、晚班 (23:00~07:00)，精确切分班次产出。

## 2. 目录结构

```text
APS_MVP/
├── APS Database- Simulation draft.xlsx  # 主数据与排产输入/输出表格
├── requirements.txt                     # Python 依赖项 (openpyxl, ortools)
├── Specification/                       # 业务功能规格与调研文档
│   ├── aps_functional_spec.md           # 功能规格草案 (v0.2)
│   ├── aps_ipo_overview.md              # Input - Process - Output 概览与架构
│   └── aps_checkList.md                 # 物流专家访谈提纲
└── src/                                 # 排产系统源代码
    ├── data_loader.py                   # 读取 Excel 参数、BOM、需求与 MPQ 批次处理
    ├── shift_manager.py                 # Option B 三班制日历与 MPQ 整数批次切分器
    ├── scheduler.py                     # Google OR-Tools CP-SAT 核心两阶段数学排程模型
    ├── exporter.py                      # 自动格式化回写 Production plan-Done 工作表
    └── main.py                          # 主执行脚本与 KPI 评估报告
```

## 3. 环境准备与运行

### 安装依赖
```bash
pip install -r requirements.txt
```

### 一键执行排产
```bash
python src/main.py
```
运行完成后，算法将在 15 秒内完成优化，并在控制台输出准时交付率与负荷 KPI，同时自动将结果更新至 `APS Database- Simulation draft.xlsx` 的 **`Production plan-Done`** 表格中。
