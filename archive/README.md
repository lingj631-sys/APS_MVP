# 历史归档区 (Archive)

> **归档日期**：2026-10-29
> **原则**：此处只读留存，不参与构建、不被代码引用。现行文档一律放在 `Specification/`。
> **来源**：早期迭代中已被后续文档取代的规格、评审、整改与进度台账，以及旧版 APS Database 草稿。

---

## 1. 归档的文档清单

| 文档 | 原日期 | 归档原因 | 取代它的现行文档 |
|---|---|---|---|
| `aps_checkList.md` | 2026-10-03 | 项目立项期的物流专家访谈提纲，早于功能规格定型 | `aps_functional_spec.md` |
| `aps_ipo_overview.md` | 2026-10-18 | Input-Process-Output 概览 v0.3，所关联的规格已到 v0.7 | `aps_functional_spec.md` |
| `aps_algorithm_review.md` | 2026-10-18 | 单一版本的算法评审，其 P0/P1 结论已被完整诊断与 REG 修复覆盖 | `aps_issues_and_defects.md` → `aps_progress_dashboard.md` |
| `aps_rules_registry.md` | 2026-10-21 | 旧 27+9 条规则台账，规则体系已升级为 HC/SC/BS 32 条目录 | `aps_new_rule_set_gap_analysis.md`、`aps_rule_set_editor_manual.md` |
| `aps_project_progress.md` | 2026-10-23 | v0.7.0 里程碑台账，进度口径已改为能力矩阵 | `aps_progress_dashboard.md` |
| `aps_issues_and_defects.md` | 2026-10-23 | 缺陷清单，其中 REG-01～08 已修复闭环 | `aps_implementation_log.md`、`archive/Specification/aps_algorithm_defect_remediation_log.md` |
| `aps_diagnosis_and_architecture_gap_report.md` | 2026-10-25 | 66 项差距评估，为一次性诊断报告，结论已进入整改与实施台账 | `aps_implementation_log.md` |
| `aps_remediation_action_plan.md` | 2026-10-25 | ACT-xx 整改计划已执行，执行结果按条记账 | `aps_implementation_log.md`、`aps_progress_dashboard.md` |
| `aps_cpsat_math_formulation.md` | 2026-10-27 | CP-SAT 数学建模说明，属阶段性讲解材料，非现行约束口径 | `config/rule_catalog/`、`aps_progress_dashboard.md` §3 |
| `aps_algorithm_defect_remediation_log.md` | 2026-10-28 | REG-01～08 修复台账，结论已并入实施记录第 8 节 | `aps_implementation_log.md` |

> 说明：上表"取代它的现行文档"用于追溯，不代表内容完全等价。若需查阅 REG 修复的原始逐条证据，仍请回到本目录。

## 2. 归档的数据草稿

| 文件 | 说明 |
|---|---|
| `APS Database- Simulation draft.backup.xlsx` | 旧主数据源的早期备份 |
| `APS Database- Simulation draft.pre_new_tables.xlsx` | 扩展主数据表之前的快照 |
| `APS Database- Simulation draft.scheduled.xlsx` | 一次排产回写后的中间产物 |

这三份均未被 Git 跟踪（`.gitignore` 已忽略 `*.backup.xlsx`、`*.pre_new_tables.xlsx`、`*.scheduled.xlsx`），因此仅存在于本地。
现行数据源是仓库根目录的 `dataSource.xlsx`；代码仅在 `dataSource.xlsx` 缺失时才回退到 `APS Database- Simulation draft.xlsx`。

## 3. 如何取回

```powershell
# 已跟踪的 Markdown：用 git mv 移回，保留历史
git mv archive/Specification/aps_rules_registry.md Specification/

# 未跟踪的 Excel：直接移动
Move-Item "archive/data/APS Database- Simulation draft.backup.xlsx" .
```

查看单份文档的完整历史（跨重命名）：

```powershell
git log --follow --oneline -- archive/Specification/aps_rules_registry.md
```
