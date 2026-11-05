# APS 2.0 规格与工程文档索引 (Specifications Index)

本目录归档了 APS 智能排产系统 2.0 的核心设计规格书、技术实施规划与实施台账。

---

## 一、 现行核心文档 (Current Specifications)

| 文档路径 | 状态 | 描述与核心内容 |
| :--- | :---: | :--- |
| **[`aps_greenfield_rewrite.md`](aps_greenfield_rewrite.md)** | **现行规范 (SSOT)** | **APS 2.0 架构重构核心规格书**。系统定义 23 实体契约、四道校验门、三级换模回退、四档工作制时钟几何、MRP BOM 展开与 Stage A 修订（A-1 至 A-10）。 |
| **[`aps_remediation_summary_20261105.md`](aps_remediation_summary_20261105.md)** | **实施台账** | **2026-11-05 实施日志与工业闭环台账**。记录 Stage A + Stage B 全部 20 个实施任务及 16 项工业级问题闭环处理方案与测试数据。 |
| **[`plans/2026-11-05-stageA-dataset.md`](plans/2026-11-05-stageA-dataset.md)** | **执行规划** | **Stage A + Stage B 整体实施规划书**（8,500+ 行详尽蓝图），涵盖从数据摄入到 CP-SAT 求解、PlanRow 映射与 Excel 导出的全部 18 个 Task。 |

---

## 二、 历史归档文档 (Historical Archive)

目录 **[`history/`](history/)** 包含了旧版架构（Phase 0 / Phase 1）演进期间的历史文档，已完成历史使命并只读归档留存，供溯源查阅：
- `aps_functional_spec.md`：旧版 v0.7 业务功能规格书；
- `aps_progress_dashboard.md`：旧版 2026-10-31 进度面板；
- `aps_new_rule_set_gap_analysis.md`：旧版 32 规则对照表；
- `aps_rule_set_editor_manual.md` & `aps_rule_set_redesign.md`：已废弃的旧版规则集编辑器文档；
- `plans/2026-11-03-stage0-foundation.md` & `plans/2026-11-05-stage1-dual-track.md`：已废弃的旧规划草案。
