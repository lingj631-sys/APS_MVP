# APS MVP 整体进度与完成度

> **更新日期**：2026-10-29
> **结论**：Phase 0 已闭环；Phase 1 规则内核可用；Phase B 大部分完成；Phase C 只完成到可验证的增量子集。**整体仍未完成**，不能标记为 100%。
> **一句话**：现有系统可跑通 WL/SY38 排产、17 项校验、规则集编辑、严格模板上传；但真正的班内序列、CP-SAT Routing Assign、CP-SAT 库存约束、C4/C5 还没落地。

---

## 1. 总进度

| 阶段 | 估算完成度 | 结论 |
|---|---:|---|
| Phase 0：正确性止血 | **100%** | 已闭环，含 ACT-06 拆批连续 |
| Phase 1：规则内核化 | **95%** | 19 模板、18 编译；routing 仍未编译 |
| Phase B：32 规则数据/算子扩展 | **约 70%** | MOQ/MPQ、软产能、时间窗、冻结、scrap、库存策略数据层已做；B4 求解器约束未做 |
| Phase C：时间/路由/序列 | **约 40%** | C1-a/C1-b-3、C2-a、C2-b-a、C3-lite 已做；CP-SAT Assign、C4/C5 未做 |
| **整体** | **约 75%** | 可演示、可基础测试；不是完整 32 规则求解器 |

---

## 2. 当前版本与测试

| 项 | 当前值 |
|---|---|
| 分支 | `main` @ `8758501`（本次 C1-b-3/4 改动尚未提交） |
| WL active | `WL_PHASE1 v4`，13 条拆分规则 |
| SY38 active | `SY38_PHASE1 v2`，13 条 |
| 内核模板 | 19 个；18 个已编译；`routing` 未编译 |
| 全量测试 | **99/99 PASS，约 1386.4s** |
| 快速测试 | **56/56 PASS，约 61s** |
| 服务 | `http://127.0.0.1:8000`（单实例开发服） |

### 测试套件

| 套件 | 项数 | 状态 |
|---|---:|---|
| `tests/test_domain.py` | 4 | PASS |
| `tests/test_kernel.py` | 26 | PASS |
| `tests/test_independent_audit.py` | 6 | PASS |
| `tests/test_golden_baseline.py` | 13 | PASS |
| `tests/test_split_batch.py` | 2 | PASS |
| `tests/test_rule_sets.py` | 9 | PASS |
| `tests/test_labor.py` | 5 | PASS |
| `tests/test_aps_pipeline.py` | 8 | PASS |
| `tests/test_regressions.py` | 8 | PASS |
| `tests/test_sequence.py` | 2 | PASS |
| `tests/test_scrap.py` | 1 | PASS |
| `tests/test_routing.py` | 4 | PASS |
| `tests/test_rule_catalog.py` | 3 | PASS |
| `tests/test_inventory_policy.py` | 4 | PASS |
| `tests/test_capacity_kernel.py` | 4 | PASS |
| **全量** | **99** | **PASS** |

---

## 3. 32 条规则状态

### Phase B

| 项 | 状态 | 说明 |
|---|---|---|
| B1 MOQ/MPQ | ✅ 代码/数据 | `basis=shift/order`、`quantity_source=MPQ/MOQ`、`tail_exempt`、`moq_override`；尚未写入 active seed |
| B2 scrap | ✅ opt-in | `build_requirement_plan(apply_scrap=True)`，默认关闭 |
| B3 软产能 | ✅ | soft 双边计罚，不再硬加 ceiling |
| B4 库存策略 | 🟡/✅ 部分 | 数据层 + 逐班投影 + API；新增 FG 库存上限 soft penalty，默认关闭 |
| B5 时间窗/冻结 | ✅ | `allowed_dates`；`freeze.use_baseline` |
| B6 目录/site override | ✅ 数据层 | 32 条目录 + `effective_for_site()`；尚未完全替换旧审计映射 |

### Phase C

| 项 | 状态 | 说明 |
|---|---|---|
| C1-a 序列原语 | ✅ | 同模优先、SDST 矩阵、production/setup/capacity 审计 |
| C1-b-lite setup 占容量 | ✅ | `setup_minutes_per_start` |
| C1-b-3 班内 sequence + start/end | ✅ 可选 | `sequence_setup=True` 时构建 `first/last/trans` + MTZ + `start/end/setup`；Gantt 页展示 sequence/setup；默认关闭 |
| C1-b 跨班 carry-over | ✅ 可选 | 上一班 last mold → 下一班 first mold |
| C1-b-4 技术员 setup 区间 | ✅ 可选 | `setup_technicians_available` + `AddCumulative`；仍按每个 setup 占 1 人近似；过小会不可行 |
| C2-a Routing 数据层 | ✅ | 候选线/机台、认证、优先级 |
| C2-b-a 路线重指派 | ✅ 启发式 | 主线过载移备线；非 CP-SAT Assign |
| C2-b CP-SAT Assign | ❌ | 未实现 |
| C3-lite 技术员容量 | ✅ 近似 | 按班次 run start 数限制 |
| C4 Operation LeadTime/Transfer | ❌ | 仅 UI 展示 |
| C5 Campaign/Pull/Slack | ❌ | 未实现 |
| 全连续时间 | ❌ | 仍是班次粒度 + 班内近似 |

---

## 4. 本轮/近期已完成

### 2026-10-29 主要变更

- WL 基于 v3 新建并激活 **v4**，补齐最新模板参数，默认行为保守。
- 第三页旧 27 条配置块删除，改为 **32 条规则目录（HC/SC/BS）**。
- 新增主数据页签：
  - Routing-Machines
  - Setup-Matrix
  - Inventory-Policy
  - Operation-LeadTime
- Excel 上传改为**严格模板校验**：Sheet 名称/顺序、第 1 行 Schema 必须与 `dataSource.xlsx` 一致。
- 上传按站点保存：`data/uploads/current_WL.xlsx` / `current_SY38.xlsx`。
- 第六页新增班内 sequence/SDST 开关，站点级持久化。
- C1-b-3：`seq_start/seq_end/seq_setup` + `sequence_plan` API；Gantt 页新增 sequence/setup 明细。
- C1-b-4：setup 技术员区间 `AddCumulative`，站点设置 `setup_technicians_available`。
- 测试扩到 **98 全量 / 55 快速**。
- 支持 `FACTORY_SITE` 固定单厂实例。
- 修复：
  - `_compile_capacity_kernel` 二次调用 `total_objective()` 导致 500；
  - `sequence_setup` 接入时 `site` 未定义导致 500；
  - 删除旧 27 条时漏删闭合 `</div>`，导致第三页之后空白。

### 仍保留的兼容项

- 第 5 页 17 项合规审计仍使用旧 27 条规则映射；
- 旧版 `/api/rules` 仍供部分求解开关使用；
- 32 条目录目前是展示 + 元数据，不是全部可编辑/全部已编译。

---

## 5. 关键缺口（下一阶段）

### P0

1. **C1-b 生产化**：已有可选 `start/end/setup`、Gantt 明细、技术员 setup 区间；仍需真实 Setup-Matrix、默认开关策略、按模具区分技术员人数。
2. **C2-b CP-SAT Assign(i,m)**：Routing 仍是数据层 + 启发式。
3. **B4-b CP-SAT 库存约束**：当前只有投影审计。

### P1

4. C4 Operation LeadTime / Transfer Batch 进入求解器。
5. C5 Campaign/Pull/Slack 策略。
6. 32 条规则目录与 17 项审计的正式映射。
7. 真实 Setup-Matrix / Inventory-Policy / Routing 数据替换 mock。

### P2

8. 每厂独立实例的正式部署脚本/端口约定（代码已支持 `FACTORY_SITE`）。
9. 确定性求解停止条件、CI、规则审批日志。

---

## 6. 运行与部署

### 单实例开发

```powershell
python run_web.py
# http://127.0.0.1:8000
```

### 每厂一个实例（推荐）

```powershell
# WL / WE34
$env:FACTORY_SITE="WL"; $env:PORT="8000"; python run_web.py

# SY38
$env:FACTORY_SITE="SY38"; $env:PORT="8001"; python run_web.py
```

固定实例会拒绝对其他站点的切换；两个进程各自持有独立 `session_state`。

### 数据源上传

- UI：第六页 → 上传 Excel 数据源；
- 模板下载：`GET /api/inputs/template`；
- 上传要求：Sheet/表头与 `dataSource.xlsx` 严格一致；
- 文件按站点保存，不覆盖仓库模板。

---

## 7. 风险与说明

| 风险 | 影响 | 缓解 |
|---|---|---|
| 当前单实例是全局 `session_state` | 多人同时操作会互相影响 | 正式使用按每厂一个实例；或后续做 per-user session |
| Setup Matrix 仍是 mock | sequence 开关效果不代表真实换型 | 先用 mock 验证模型，再替换业务数据 |
| Routing/Inventory/LeadTime 未全接求解器 | UI 能看到，但排产不完全使用 | 明确标注数据层/审计层；下一阶段接 CP-SAT |
| 99 项测试通过不等于 32 条规则全部完成 | 部分规则仍是 preprocess/audit | 以本文能力表为准，不以测试数代替完成度 |
| 工作区改动未提交前不可复现 | 交接风险 | 本次提交/推送后以 Git 版本为准 |

---

## 8. 验证记录

```text
全量：99/99 PASS，约 1386.4s（C1-b-3/4/B4-b 后）
快速：56/56 PASS，约 61s
WL active：WL_PHASE1 v4 / 13 rules
SY38 active：SY38_PHASE1 v2 / 13 rules
/api/rule-catalog：32 条
/api/inputs（WL）：routing 37 / setup 36 / inventory 74 / leadtime 37
严格上传：原模板通过；错误表头拒绝
真实 /api/solve（sequence_setup=true）：success / 19 tasks / 63 sequence records / 900 setup min
```
