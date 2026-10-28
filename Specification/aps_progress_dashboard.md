# APS MVP 整体进度与完成度

> **口径**：按行动计划的 151 人日估算；"完成"= 代码 + 测试 + 文档，且在当前工作区验证过。
> **结论**：Phase 0 闭环（100%），Phase 1 约 90%（v1 功能闭环，硬化项待做），Phase 2 未开始；整体约 **63%**。
> **一句话**：模型与校验已从"demo 级"提升到"可复核级"；"规则可配"目前只到**库层**，还没到**产品层**；多厂/多线/MRP 仍是 0。

---

## 1. 总进度

| 阶段 | 计划人日 | 已完成（估算） | 完成度 | 结论 |
|---|---:|---:|---:|---|
| Phase 0：正确性止血 | 45 | 45 | **100%** | 全部闭环（含 ACT-06 拆批连续） |
| Phase 1：规则内核化 | 55 | 50 | **90%** | 18 算子 + 规则平台 v1；树形/100 线/routing 待硬化 |
| Phase 2：多厂/MRP/扩展 | 51 | 0 | **0%** | 未启动 |
| **合计** | **151** | **95** | **约 63%** | Phase 1 v1 闭环；Phase 2 未开始 |

> Phase 1 的 40% 里，ACT-22 按"8/11 个约束组已迁、软档字典序已接默认"计 55%。

---

## 2. 对照最初五个要求

| # | 要求 | 完成度 | 现在能做到 | 关键缺口 |
|---|---|---:|---|---|
| 1 | CP-SAT 核心算法 / 求解器使用合理性 | **95%** | 精确完工、开班决策、硬/软 95%、hint、单 worker、超班拆批连续 | 墙钟时间上限导致 ±1 非严格确定；无班内排序 |
| 2 | 第二套独立校验是否有缺陷/漏洞 | **95%** | 9 项检查、20 类变异注入、禁止 import scheduler、黄金基线 | 内核计划尚未跑独立审计；手工表只到零件级 |
| 3 | 算法固定 + 每厂规则（软硬/权重） | **90%** | 19 模板、18 编译、作用域继承、权重归一化、SQLite 版本库、Excel 导入、规则编辑器、server/main 默认 active 规则集 | routing（Phase 2）；规则页树形/100 线待硬化 |
| 4 | 规则分组 / 按优先级分组 | **90%** | `PenaltyBook` 按 rule 归一化；软档按 G1→G2→G3 逐组冻结；硬档硬约束 | 无跨组敏感度视图 |
| 5 | 多厂/多线/一 FG 对多 SF/多线同产品/MRP | **10%** | 数据层已有 site/line；headcount 已贯通 | 数据模型、替代路由、MRP、跨厂约束**全部未开始** |

---

## 3. 能力记分卡

| 能力 | 完成度 | 证据 | 主要缺口 |
|---|---:|---|---|
| 模型语义与约束正确性 | 95% | CLI 17/17；黄金基线稳定；ACT-06 拆批连续（65+7，1 块） | 班内无排序 |
| 独立校验 | 95% | `src/audit/engine.py` 658 行；9/9；20 类变异；不 import solver | 内核计划未审计；零件级仅手工表 |
| 规则内核（固定算子） | 95% | 19 模板；18 编译；`src/kernel` 约 1,700 行 | routing（Phase 2 线选择） |
| 分组 / 优先级 / 权重 | 90% | 按 rule 归一化；软档字典序已接 server；22 项内核测试；敏感度 CLI/API/页面 | 无跨组敏感度视图 |
| 规则平台（Excel/版本/UI/无解解释） | 80% | 版本库、Excel 导入、编辑器、作用域/继承/按组启停、敏感度/诊断 API+UI | 100 线虚拟滚动 |
| 多厂 / 多线 / 替代路由 | 5% | 仅 `site`/`line` 字段 | 无路由决策、无跨厂产能/日历 |
| MRP 物料可行性 | 15% | BOM 展开、净需求、期初库存、ETA 门控、两阶段齐套 | 无 PO/在途/提前期/QC/安全库存 |
| 人力约束 | 55% | `labor_capacity` 算子（硬/软、共享班组）+ 三个 CSV 加载/校验 + KPI | 真实人力数据未接入；轴套6 缺 headcount |
| 确定性 / 可复现 | 50% | 单 worker + 固定 seed | 时间上限导致目标值 ±1 |
| 文档 / 可运维 | 90% | 诊断、行动计划、实施记录、规则集操作手册 | 缺运维手册/告警手册 |

---

## 4. 已交付

### 4.1 文档

| 文件 | 内容 |
|---|---|
| [诊断与架构差距报告](./aps_diagnosis_and_architecture_gap_report.md) | 1,075 行；66 个差距（P0 21 / P1 30 / P2 15）；§8 目标架构 |
| [整改行动计划](./aps_remediation_action_plan.md) | 8 个 Sprint / 16 周 / 151 人日；ACT-01～39 + ACT-L1～L4；19 个算子 |
| [实施记录](./aps_implementation_log.md) | Phase 0 + Phase 1；决策 D7/D8/D9；测试与发现 |
| [规则集编辑器操作手册](./aps_rule_set_editor_manual.md) | 字段/按钮/权重/19 模板参数/Excel/排障/配方 |

### 4.2 代码

| 模块 | 规模 | 说明 |
|---|---:|---|
| `src/audit/engine.py` | 658 行 | 独立审计：净需求、BOM、CT/TRP、9 项检查；不 import scheduler |
| `src/kernel/` | 1,110 行 | types / registry / scope / compiler / solve / `__init__` |
| `src/scheduler.py` | +约 300 行 | 精确完工、开班决策、95% 双层、首班锁死、内核 opt-in |
| `src/validator.py` | +349 行 | 综合负荷、105% 交付弹性、4 项恒真重写、N/A |
| `src/data_loader.py` | +15 行 | `headcount` / `crew_required` 贯通 |
| `src/main.py` / `src/server.py` | +80 行 | 人力 KPI、日历覆盖、独立审计、`crew_required` |

### 4.3 测试

| 套件 | 项数 | 耗时 | 状态 |
|---|---:|---:|---|
| `tests/test_domain.py` | 4 | 0.1s | PASS |
| `tests/test_kernel.py` | 22 | 0.1s | PASS |
| `tests/test_independent_audit.py` | 6（含 20 类变异） | 60s（60s 预算） | PASS |
| `tests/test_golden_baseline.py` | 13（含 4 项内核迁移 + 105 口径 + 组目标） | ~391s | **10 PASS / 3 FAIL**（08/09/10） |
| `tests/test_split_batch.py` | 2（WA14208S 拆批） | 0.03s | PASS |
| `tests/test_rule_sets.py` | 9（Excel/版本/敏感度/诊断） | 0.3s | PASS |
| `tests/test_labor.py` | 5（人力主数据） | 0.1s | PASS |
| `tests/test_aps_pipeline.py` | 8 | — | **6 PASS / 2 FAIL**（03/04） |
| `tests/test_regressions.py` | 8（REG-01~08） | 0.6s | PASS |
| **全量** | **77** | ~665s | **72 PASS / 5 FAIL（未闭环）** |

> 快速回归（`python run_tests.py --fast` + `test_regressions`）= **56/56 PASS**；全量 77 项仍有 5 项红，不能标记为“全部 PASS”。

### 4.4 配置

| 文件 | 说明 |
|---|---|
| `config/rule_sets/WL_2026Q4.json` | PGLINKAGE 105%、换型 80、SF 库存 3 天、人力 6 人 |
| `config/rule_sets/SY38_2026Q4.json` | 全厂 105%、L/R 共模、1400T-6 换型 60、人力 20 人 |
| `tests/fixtures/wl_10d_baseline.json` | 黄金基线：19 单 / 31,391 件 / OTD 100% |

---

## 5. 还没做（按优先级）

### P0：不补会影响"能不能用"

1. **规则页树形/100 线**：工厂→车间→线组→线的树、100 条线虚拟滚动；继承标记与按组批量已做。
2. **routing 算子**：替代产线/多线选择，属于 Phase 2 线选择模型。
3. **真实人力数据**：填 `Labor-Availability` / `Line-Staffing` / `Crew-Pool` 后 `labor_capacity` 才生效；轴套6 仍缺 headcount。
4. **确定性**：固定 `max_number_of_conflicts` 或"合规即停"，替换墙钟上限。

### P1：规则平台与扩展

6. **敏感度 / 无解解释 API/UI**：函数与 CLI 已有，页面/接口未接。
7. **多厂/多线/替代路由**：数据模型与路由决策都未开始。
8. **MRP 物料可行性**：PO/在途/提前期/QC/安全库存未建模。
9. **规则审批与审计日志**：谁在何时改了哪条权重；当前只有版本与 note。
10. **运行监控**：求解耗时、失败率、规则命中率。

### P2：体验与运维

11. 计划版本对比、规则模板库、用户手册。
12. 100 条线压力测试与缓存。

---

## 6. 风险与阻塞

| 风险 | 影响 | 缓解 |
|---|---|---|
| 规则页是 v1 平铺 | 树形/继承/批量/100 线尚未实现 | 下一轮做树形与虚拟滚动；当前可编辑/版本/导入 |
| 内核路径慢（61s vs 19s） | server 请求 90s，UI 等待长 | 约束顺序/提示已优化；后续 LNS + 确定性停止 |
| 迁移测试 240s | 回归慢，容易跳过 | 拆 `test_kernel_migration.py` |
| 单 worker + 时间上限 ±1 | 无法承诺逐字节复现 | 改确定性停止条件 |
| Phase 2 为 0 | 五个要求里的第 5 条基本未动 | 单独立项，不要混在 Phase 1 |
| 人力数据未接 | `labor_capacity` 目前惰性 | 填三个 CSV 后生效；轴套6 缺 headcount |
| 无 CI | 回归靠手工 | 先拆测试，再上 GitHub Actions/本地脚本 |

---

## 7. 建议的下一步（3 件）

1. **规则页树形/100 线**：工厂→车间→线组→线的树、100 条线虚拟滚动；当前 `/rule-sets` 已可编辑/版本/导入/继承标记/按组批量/敏感度/诊断。
2. **routing + 真实人力数据**：替代产线（Phase 2 线选择）；填 `Labor-Availability` / `Line-Staffing` / `Crew-Pool` 后 `labor_capacity` 生效。
3. **确定性 + 测试拆分**：固定停止条件；把 `test_08～11` 拆到 `test_kernel_migration.py`。

> Phase 2（多厂/多线/路由/MRP）建议单独立项：它需要新的数据模型和约束，不是把现有 scheduler 再改几行能解决的。

---

## 8. 验证现状（最近一次）

```
CLI（active WL_PHASE1，180s）：
  17/17 规则校验；独立审计 9/9
  19 单 / 31,391 件；0 个低于 95% 班次；OTD 100%
  换型 48 块；求解 124s；人力 552h / 3 人（轴套6 缺 headcount）

内核拆分版（WL_PHASE1 v3，13 条规则 / 11 个算子 / 18 算子可用；当前 active 以编辑器为准，可能仍是旧 v1 11 条）：
  /api/solve 200；19 单；0 项校验失败；52 块；求解 61s / 请求 90s
  0 个低于 95% 班次；OTD 100%
  main.py 同样读取 active 规则集

ACT-06 拆批（WA14208S，MPQ 72，单批 8.45h > 8h）：
  65 + 7 两段连续；1 块；17/17；独立审计 9/9

规则平台：
  test_kernel 22 项（19 模板 / 18 编译 / 权重 / 字典序 / 全部算子）
  test_rule_sets 9 项（Excel 行级错误/幂等/版本/diff/回滚/敏感度/诊断）
  test_labor 5 项（可用人数/定员/共享班组/模板）
  /rule-sets：版本/编辑/继承标记/按组批量/敏感度/诊断/导入导出
```
