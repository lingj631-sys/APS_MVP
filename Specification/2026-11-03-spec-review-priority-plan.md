# 规格与阶段 0 计划评审：事实核对、优先级归类与改进建议

| 项 | 值 |
|---|---|
| 评审对象 | `Specification/aps_rule_set_redesign.md`（rev.3，以下简称**规格**）<br>`Specification/plans/2026-11-03-stage0-foundation.md`（以下简称**计划**） |
| 评审基线 | 工作区 `D:\Codes\APS_MVP`，`main` @ `149b127`（`git log` 前 8 条已核） |
| 评审方式 | 只读核对：grep / AST / 运行时反射 / 直接读 `APS_DataSource_Template.xlsx` / 跑 `run_tests.py --fast`。**未修改任何代码** |
| 产出 | 本文档：① 事实核对结论 ② 我认可的建议按优先级归类 ③ 我不认可的 ④ 两份文件都没覆盖的缺口 ⑤ 建议的最小推进路径 |
| 与两份文件的关系 | 本文档**不替代**它们。规格的第 2–7 节（数据模型、指标注册表、编译器语义）是设计资产，我基本认可；本文档只修正会害人的**数字、坐标、前提**，并对执行顺序提出不同意见 |

---

## 0. 结论摘要（先看这一节）

### 0.1 一句话结论

**诊断可信，设计方向正确，但阶段 0 计划按现状不可直接执行**——它有 5 处会让第一天就失败的前提错误（§3.1），以及 6 处会让验收判据判错的数据口径偏差（§3.2）。

### 0.2 三条总判断

| # | 判断 | 依据 |
|---|---|---|
| 1 | **规格的架构诊断站得住。** 我抽查的 28 条硬事实里 24 条完全成立，包括几个最容易被夸大的：`available = 1` 写死、6 处 `force_hard=True`、`_lateness_weight ≈ 7.8×10¹⁰`、REG-01 只在 legacy 修过、32 条目录规则 0 条 implemented、5 张 CSV 加载命中 0 | 见 §2 |
| 2 | **规格的"设计"部分（第 2–7 节）是这份文档真正的价值**，且与现有代码可接合：`PenaltyBook`、`solve_lexicographic`、`Scope`、`RequirementPlan` 都已在位，不是从零发明 | 见 §2.2 |
| 3 | **计划的问题不在任务分解，而在它继承的前提**。所有 `file:line` 声称来自 `89ee6e0`——**该 commit 在本仓库不存在**。行号已系统性漂移，且计划自己的测试代码引用了 3 个不存在的符号 | 见 §3.1 |

### 0.3 核对结果计数

| 类别 | 数量 | 说明 |
|---|---|---|
| 完全成立的事实性论断 | 24 条 | §2.1 |
| 方向正确但数字不准 | 6 条 | §3.2（`11 处`年份、`18 个`开关、`11 个`死参数、`33 列`、`7 个`消费点、`15 个`测试文件） |
| 明确错误、会害人的论断 | 5 条 | §3.1、§3.3（外键设计、`89ee6e0`、附录 C 清理记录、日期轴、计划内的失效符号） |
| 遗漏（比报告写的更严重） | 4 条 | §3.3（BOM 外键 50% 违规、`master_rows.py` 整条编造路径、无 CI、工作区脏） |
| 我认可的建议 | 23 条 | §4，分 P0–P3 |
| 我不建议照做的 | 6 条 | §5 |

### 0.4 如果你只做一件事

**先花半天把计划 rev.1 的坐标与口径修到 rev.2，再开工。** 具体是 §4.1 的 P0-1 至 P0-6。理由很朴素：计划里有 3 个符号和 1 处模块归属在当前仓库不存在/对不上，任何执行者（人或智能体）第一步就会撞上 `ImportError`，然后开始"顺手修正"，而附录 D.4 恰恰禁止顺手修正。

---

## 1. 评审方法与当前环境事实

### 1.1 我核对了什么

- **可 grep 的事实**：符号是否存在、行号是否对得上、字面量是否还在、开关数量
- **可运行的事实**：`run_tests.py --fast` 实际项数与耗时、`detect_layout` 实际返回值、`read_master_rows` 实际返回的键、`dataclasses.fields` 的实际默认值、`inspect.signature` 的实际参数个数
- **可读表的事实**：直接打开 `APS_DataSource_Template.xlsx`，数日期列、数行、数空列，并把规格 §5.9 提出的主键/外键**逐条跑在真实数据上**
- **我没有核的**：`rule_sets/store.py` 的五个缺陷（未逐条验证）、"REAL 只有 4 条"的规则覆盖评级（这是判断而非事实）、各 `file:line` 在历史 commit 上的准确性（因为该 commit 不存在）

所有核对命令见**附录 A**，可复跑。

### 1.2 当前环境事实与 D.1 冲突（重要）

计划 Global Constraints 与规格附录 D.1 把下面这些写成"照抄，不要自己试"。实测全部不成立：

| D.1 写的 | 本机实测 | 后果 |
|---|---|---|
| 一律用 `.venv/Scripts/python.exe` | **`.venv` 不存在**（`Test-Path` → False，且 `.gitignore` 含 `.venv/`） | 所有命令第一行就 `CommandNotFoundException` |
| `python` 不在 PATH；`py` 有但没有 ortools | `python` → `C:\Python314\python.exe`，**3.14.7**；`ortools 9.15.6755`、`openpyxl 3.1.5`、`fastapi 0.136.1` 全部已装 | 强制 `PYTHONIOENCODING=utf-8` 仍然有用（中文输出），但前缀要换成 `python` |
| Shell 是 Git Bash（POSIX sh） | 本会话 Shell 是 **PowerShell** | 计划里所有 `git checkout -- x 2>/dev/null \|\| true`、`md5sum`、`grep -rn`、`git add \`（续行符）**都不能直接跑**。需换成 `git checkout -- x 2>$null`、`Get-FileHash`、`Select-String` |

> **建议**：把"运行环境"从"照抄的常量"改成"启动时自检的一小段脚本"（检查解释器、编码、ortools 版本、`git status` 是否干净），失败就报错退出。写死在文档里的环境事实一定会再次漂移——这次就是证据。

### 1.3 仓库当前状态与计划的前提冲突

`git status --short` 现在**不干净**，且有 6 个已跟踪文件带未提交改动，全部与阶段 0 无关：

```
 M .gitignore                  (+3)
 M src/excel_loader.py         (+33/-…)
 M src/master_schema.py        (+45/-…)   新增 extent_of()，修 read-only sheet 的 max_row=None
 M src/server.py               (+201/-…)
 M src/static/index.html       (+153/-…)
 M tests/test_master_schema.py (+40)
?? "APS_DataSource_Template - Wenling Test.xlsx"
?? overload_modal.png
```

计划 Global Constraints 要求"提交前 `git status` 必须干净"，验收 0.4 要求"不带 `--force` 时 `git status` 干净"。**在这个状态下这两条永远不通过**，而且"一个任务一个提交"会把别人的 WIP 搅进去。

> **建议**：开工前先把这批 WIP 单独提交（它是一组独立且有价值的修复：WPS/JS 导出的工作簿缺 `<dimension>` 导致 `max_row=None` 崩溃），再开始阶段 0。否则阶段 0 的每个提交都夹带无关 diff。

### 1.4 一个不该被忽略的旁证

`git cat-file -t` 结果：

```
3b47ceb -> commit
149b127 -> commit
17a383b -> fatal: Not a valid object name    ← 规格说"rev.2 是 commit 17a383b"
89ee6e0 -> fatal: Not a valid object name    ← 规格说"file:line 是 89ee6e0 时点的快照"
```

另外规格头部写"分支 `feat/shaojing-dev`（起点 `3b47ceb`）"，实测：

- `3b47ceb` **不是** `feat/shaojing-dev` 的祖先（`merge-base --is-ancestor` → 1）
- `feat/shaojing-dev` 相对 `main` **领先 0 个提交、落后 46 个提交**（早已全量并入，是条死分支）

这两件事合起来说明：**两份文件的坐标体系来自另一份工作副本或另一次会话的状态**，不能当成本仓库的事实使用。这是 §3.1 的根源。

---

## 2. 事实核对：核实成立的论断

### 2.1 我逐条跑过的（24 条）

| # | 论断（规格/计划原文） | 实测证据（`149b127` 坐标） | 判定 |
|---|---|---|---|
| 1 | `available = 1` 写死在编译器，整条 tooling 数据链路成装饰 | `src/kernel/compiler.py:318` `available = 1`，上面 2 行注释自陈这是 HC-01 业务裁决 | ✅ |
| 2 | 6 处 `force_hard=True` 无视 `nature` | `scheduler.py:1320 / 1384 / 1404 / 1429 / 1453 / 1460`；对应 `min_batch`、`sequence`、`tooling_exclusivity`、`material_ready`、`pm_block`、`due_date` | ✅ |
| 3 | `LATENESS_SLACK_SHIFTS = 6` 把延误窗口硬截断到 `due+6` | 常量在 `scheduler.py:69`，唯一使用点在 `:1160` `min(num_shifts-1, _due_shift+6)` | ✅ |
| 4 | 目标函数把异构量相加，`_lateness_weight ≈ 7.8×10¹⁰` | `scheduler.py:1626` `model.Minimize(sum(all_terms))`，`all_terms` 是 4 个组的扁平合并；`:1633-1640` `_lateness_weight = n_lines × n_shifts × (n_shifts×min_shift_ms+1) + 1`，按 3×30×28.8M 代入 = **77,760,000,091** | ✅ 算术自洽 |
| 5 | 换型时间默认不占产能（B3） | `shift_manager.py:26` `DEFAULT_CHANGEOVER_MINUTES = 0.0`；`validator.py:25` `SETUP_SECONDS = int(...)` = 0 → 容量校验不含换型 | ✅ |
| 6 | 缺换模边静默回退 60 分钟（B4） | `scheduler.py:2301` `... else 60.0`；`sequence.py:189` `default_minutes: float = 60.0` | ✅ |
| 7 | REG-01 料号级时间相位账本只修在 legacy | `scheduler.py:1657` `_legacy_material_balance`，docstring 自称 "Per-shift time-phased material ledger (REG-01 / ALG-01)"；kernel 侧 `compiler.py:353-380` 仍是逐父任务 × 逐子件的 pairwise 近似 | ✅ 漂移真实存在 |
| 8 | `scope` 是四维精确字符串匹配 | `kernel/types.py:36-49`，`Scope(site,line,stage,part)` + `in ("*", target.x)` | ✅ |
| 9 | Scheduler 是 God object，63 参数 | `inspect.signature(APSScheduler.__init__)` → **63 个参数** | ✅ 数字精确 |
| 10 | 32 条目录规则，`status` 26 partial、**0 implemented** | `config/rule_catalog/aps_32_rules.json`：`{partial:26, preprocess:3, configurable:2, postprocess:1}`，合计 32，无 `implemented` | ✅ 精确 |
| 11 | 5 张主数据 CSV 零加载 | `Baseline-Schedule`/`Calendar-Patterns`/`Customer-Orders`/`Setup-Technicians`/`Trial-Windows` 在 `src/` 命中数 **0 / 0 / 0 / 0 / 0** | ✅ 精确 |
| 12 | `prechecker.has_exception` 至今无人消费（BS-03 空转） | 定义在 `prechecker.py:46`，全仓引用数 **1**（只有定义自身） | ✅ |
| 13 | `session_state` 是零锁全局单例；`GET /api/inputs?site=X` 会清空所有人的结果 | `server.py:396` 模块级 dict；`:1000-1010` `get_inputs()` 里 `session_state["site"]=site; ... ["result"]=None; ["matrix"]=None; ["checks"]=None` | ✅ 实存缺陷 |
| 14 | JSON 快照缓存"写了没人读" | `load_snapshot_json` 定义在 `data_loader.py:298`，**全仓 0 个调用者**；`save_snapshot_json` 有 4 个调用点（`data_loader.py:628`、`server.py:1329,1517`） | ✅ 精确 |
| 15 | `line_base` 是唯一没有 `site` 列的表 | `master_schema.py:358`，4 列；模板 3 行数据，`PGLINKAGE 0.71 8 3` 与规格实测值一致 | ✅ |
| 16 | `read_master_rows` 完全读不到日期轴 | `excel_loader.py:660`；`sorted(rows['demand'][0])` → `['description','part_no','site']`；日期列只在 `_demand_dates`（`:213`）里有 | ✅ 精确 |
| 17 | 换型时间有 5 个可能来源 | `routing.setup_min`、`setup_matrix.setup_minutes`、`tooling.changeover_time_min` 三列都在位，加 `0.0` 默认与 `60.0` 回退 = 5 | ✅ |
| 18 | dataclass 字段默认值本身就是编造的 | `LineBase`: `trp=0.71 / hours_per_shift=8.0 / shifts_per_day=3`；`ToolingMold`: `available_qty=1 / changeover_time_min=30.0 / max_life_strokes=500000 / maintenance_cycle_strokes=50000 / status="可用 (Available)"`；`ProductParam`: `hours_per_shift=8.0 / shifts_per_day=3` | ✅ 值全对 |
| 19 | loader 内约 25 处编造 | `excel_loader.py` 里带字面量默认值的 `_number/_int` 调用 **23 处**（`:316-318, 352-356, 385-387, 410, 413, 421-423, 477, 504, 540, 566, 587, 595-597, 614-619`） | ✅ 量级对 |
| 20 | `_int(val, 1) or 1` 把显式 0 改写成 1（计划称"真实 bug"） | `excel_loader.py:352` `available_qty=_int(tooling.val(row,"available_qty"), 1) or 1` | ✅ 真 bug |
| 21 | 8 个 BOM 子件料号不在 `parameters_sfn` | 实测 distinct 子件缺口 = **8**（`W000031997/1998/33143/33212/42905/42906/45744/45782`） | ✅ 精确 |
| 22 | T2 表格的实测填充率 | FG 17 行 / SFN 22 行 / Routing 39 行；`特殊班次` FG 0/17、SFN 0/22；`同时生产` FG 4/17（全为 `Y`）、SFN 0/22；PM `开始时间`/`结束时间` 0/3；`交期浮动容差` 全是自由文本（`0天严格保供`/`±1天窗口协商`/`+3天或顺延次月`） | ✅ 逐格对得上 |
| 23 | `PenaltyBook` / 字典序求解"已有实现，但只在特定条件下走" | `compiler.py:71` `class PenaltyBook`；`solve.py:63` `solve_lexicographic` | ✅ |
| 24 | fast 基线 61 项 | `python run_tests.py --fast` → `Ran 61 tests ... OK`，退出码 0 | ✅ 但耗时 **62 秒**，不是"秒级" |

### 2.2 规格里我判定为"设计正确、可落地"的部分

这几点我认为是这份规格真正的贡献，而且与现有代码**可接合**（不是纸上架构）：

1. **D7：`MetricContext` 类型里不存在 `model`** —— 用类型系统而不是纪律来保证"指标不建约束"。这是把 A5 从"靠 code review"变成"编译期不可能"的正确手法。
2. **§7.2 的 `out_of_scope` 状态** —— 全篇最高性价比的一条。我核到一个具体形态：`validate_rule_set` 确实把 unmatched scope 当软告警返回，而没有任何出口展示它。计划员把 `line` 写成带尾空格时，今天的结果是"零约束 + 零告警 + UI 显示已启用"。这个状态把 A5 从"计划员视角看不见"变成"必须看见"。
3. **D12 + PenaltyBook 单一入口** —— 现有 `PenaltyBook`（`compiler.py:71`）已经具备分组与按规则归一化权重的能力，`solve_lexicographic` 也已实现分组冻结。**这不是新建，是把已有的正确实现变成唯一入口**，风险比规格描述的更低。
4. **`contract_tier` 落在 schema 上（T1/T2/T3）** —— 把"文档约定"变成"可执行声明"。我核到 T2 表里每一条都能在真实模板上复现，说明分层是从数据推出来的，不是拍的。
5. **§4.4 换型时间优先级表** —— 五个来源的裁决是必要的，且优先级（SDST → Tooling → Routing → fail）符合精度递减直觉。
6. **删 `len(TEMPLATES) == 20`** —— 我核到该断言在 `tests/test_kernel.py` 里，且确实只锁数量。规格 §7.1 第 10 条（断言 `force_hard`/`enforce_*` 不再存在）是这次重构唯一的防倒退机制，这个判断我完全同意。

---

## 3. 事实核对：错误、偏差与遗漏

### 3.1 会直接让计划跑不起来的（阻断级，5 条）

#### E1｜`89ee6e0` 不存在，全部 `file:line` 失去坐标基准

规格附录 D.4 说"本文档所有 `file:line` 都是……`89ee6e0` 时点的快照"，并规定"行号对不上但符号还在 → 按符号找，继续做，不必报告"。问题是：

- 该 commit 不存在，**无法判断漂移是"有人改了代码"还是"文档写错了"**
- 实测漂移不是零星的：`excel_loader.py` 约 +15 行、`master_schema.py` +13 行、`scheduler.py` +1 行、`server.py` +100 行以上

**D.4 的处理规则因此失效**：它区分"符号还在（继续做）"与"符号不在了（停下报告）"，但当基准 commit 不存在时，你无法排除"文档抄错了"这种第三种情况——而实际情况正是第三种。

> **影响**：所有任务的 `Files:` 清单会改错文件；所有验收 grep 会漏掉真实站点（见 E10）。

#### E2｜计划自己的测试代码引用了 3 个不存在的符号

| 计划位置 | 计划写的 | 实际 | 后果 |
|---|---|---|---|
| Task 12 测试文件首行 | `from src.scheduler import ProductionScheduler` | 类名是 **`APSScheduler`**（`src/scheduler.py:308`），`ProductionScheduler` **不存在** | 新测试文件第一行 `ImportError` |
| Task 12 测试 | `for task in snap.sf_tasks` | `ProductionInputSnapshot` 的字段里**没有** `sf_tasks`/`fg_tasks`（它们在 `RequirementPlan` 上） | `AttributeError` |
| Task 12 测试 | `windows[task.parent_task_id]`，遍历 `snap.sf_tasks` | 同上 | 同上 |
| Task 12 测试 | `for task in snap.orders and [] or []:` | 死代码（恒为空），写进正式用例 | 无意义断言 |
| 规格 §5.2 / 计划多处 | `build_requirement_plan(snapshot)` 属于 `balance_builder.py` | 实际在 **`src/data_loader.py:677`**；`balance_builder.py` 里只有 `build_validation_matrix` | 按"Files"清单改会改错文件 |

另外 Task 6 的测试夹具自相矛盾：

```python
DATES = [d.isoformat() for d in (date(2026,10,13) + timedelta(days=n) for n in range(33))]
# 33 项 → 末项是 11/14
...
self.assertEqual(dates[-1], "2026-11-15")   # 断言 11/15 —— 不可能同时成立
```

#### E3｜日期轴是 **34** 列，不是 33；行数是 **306**，不是 297

这是我认为最需要立刻修正的一条，因为它是**验收判据本身**。

实测（`openpyxl` 直接读表 + `_demand_dates` 复核，两者一致）：

```
Customer Demand 表头：站点 / 料号 / 品名描述 + 34 个日期列
首列 10/13，末列 11/15；10/13→11/15 闭区间 = 34 天
其中 25 列有量（这一条规格写对了）
```

所以：

| 量 | 规格 rev.3 写的 | 实测 |
|---|---|---|
| 日期列数 | 33 | **34** |
| `line_headcount` 行数（3 线 × N × 3 班） | **297** | **306** |
| `_demand_dates` 返回长度 | — | **34**（`(4,'10/13')` … `(37,'11/15')`） |

**这意味着**：① 验收 0.5「恰好 297 行」永远不成立；② Task 6 的 `test_reads_the_declared_axis_in_column_order` 会红；③ 计划里那段 `range(33)` 的夹具注释"the real template axis"是错的。

> rev.2 把 90 改成 297 时，是从"10 天窗口"改到"模板轴"，方向对了，但**数错了一天**。这正好说明"对着模板实测"这句话在这次修订里没有真正执行到最后一列。

#### E4｜工作区脏 + 无 CI，两条关键前提同时不成立

- `git status` 不干净（§1.3），"提交前必须干净"的两条验收无法通过
- **仓库没有 `.github/`，没有任何 CI 配置**。规格 D5 的"静态契约（CI）+ 运行时绑定报告"两层保证，第一层**没有执行载体**；§7.1 的 10 条检查、阶段 2 验收 2.1「契约测试 1-9 全绿」目前只能靠人记得跑 `run_tests.py`
- 而 `run_tests.py --fast` 只覆盖 **7 / 40** 个测试模块（`:32-40`）。规格已经指出"没进这个列表的 fast 全绿是假的"，但没有指出比例是 7/40——**这道门槛名义上存在，实际覆盖面 17.5%**

> **建议**：D5 的第一层要么补一个最小 CI（一个 workflow，一条命令），要么坦白改成"本地 pre-commit 钩子"，不要写成"CI 强制"却什么都没有。

#### E5｜外键设计没有对着真实数据验证（2 个错误）

规格 §5.9 的登记表 + 计划 Task 3 Step 5 提出了 6 条跨表外键。我把它们**逐条跑在真实模板上**：

| 外键 | 源行数 | 违规数 | 判定 |
|---|---|---|---|
| `material_arrival.part_no → parameters_sfn.part_no` | 7 | **7（100%）** | ❌ **语义错误**，不是数据脏 |
| `bom.fg_part_no → parameters_fg.part_no` | 56 | **14 个母件缺失** | ⚠️ 规格完全没提 |
| `bom.sf_part_no → parameters_sfn.part_no` | 56 | **8 个子件缺失** | ✅ 规格提了（但只是冰山一角） |
| `routing.primary_line → line_base.line` | 39 | 0 | ✅ |
| `setup_matrix.line → line_base.line` | 36 | 0 | ✅ |
| `pm.line → line_base.line` | 3 | 0 | ✅ |

两个独立的问题：

1. **`material_arrival.part_no` 指向 `parameters_sfn` 是错的。** `material_arrival` 装的是**外购原材料**（`W000061536-RAW`、`WA14208-CHIP`、`WAB6404-PIN`、`WB35356-SUB`、`WD48696-SENSOR`……），半成品参数表永远不会有这些料号。这不是"数据待清洗"，是**建模错误**：关键物料需要自己的主数据表（或至少不加这条外键）。
2. **BOM 的 FK 违规是规格所述数量的 3.5 倍。** 规格只写"8 个 BOM 子件料号不在 `parameters_sfn`"，但**母件方向还有 14 个 FG 料号不在 `parameters_fg`**，合计 **56 行 BOM 里 28 行（50%）至少一端是孤儿**。

> **影响**：计划 Task 3 的 `validate_references()` 一落地，就会对真实模板输出 **7 + 28 = 35 条**问题记录。计划把它当"阶段 0 只报告不强制"（§5.9 事实 3），方向对，但**数量级差 4 倍**的告警会直接把报告变成噪音，没人会看。必须先裁决：删掉 material_arrival 外键、BOM 双向外键降为"数据质量报表"而非"引用校验"。

---

### 3.2 会让验收判据判错的（数字级，6 条）

| # | 规格/计划写的 | 实测 | 影响 |
|---|---|---|---|
| E6 | 硬编码年份 **11 处**（计划偏差 3 称"实测 11 处，覆盖全部"） | **13 处**；漏了 `server.py:1604`（`start_day = datetime(2026,10,12,start_hour,0,0)`）与 `server.py:2047`（`start_dt = datetime(2026,...)`） | 好在验收 0.7 用的是 `grep -rn "datetime(2026" src/` 无输出——**这条 grep 能兜住**。但按"Files 清单"改会漏 2 处 |
| E7 | `18 个 enforce_* 开关` | `APSScheduler.__init__` 有 **22 个** `enforce_*` 参数；`src/` 全文 **40 个** distinct 词元 | 阶段 1 验收 1.2「grep 无命中」的门槛要按 22/40 重写，否则"达标"是假的 |
| E8 | `11 个参数在 compiler.py 中引用数为 0` | 我用 `\bname\b` 在 `compiler.py` 全文匹配，得到 **16 个**；而且 `available_qty` 只出现在**注释**里（`:316`），所以"文本引用"这个判据本身不严 | 契约检查 #3 必须用 AST/记录型代理（规格 §6.5 已提出），不能靠 grep |
| E9 | `§5.2 的 7 个消费点签名冻结`（scheduler/validator/prechecker/balance_builder/audit/exporter/manual_audit） | 这 7 个里 **5 个根本不引用 `ProductionInputSnapshot`**（`grep` 命中 0）。它们消费的是 **`RequirementPlan`**（`data_loader.py:195`）；`scheduler.py:308` 的签名是 `__init__(plan: RequirementPlan, ...)` | 规格把两个契约混成一个。"冻结哪一层"这个问题必须重答（见 §5.1） |
| E10 | `约 15 个直接构造它们的测试文件` | **1 个**测试文件（`tests/test_regressions.py`，6 处构造）+ `test_aps_pipeline.py` 引用 1 次 | 风险被高估 7–15 倍。这反而是好消息：加字段比规格以为的安全得多（见 §5.1） |
| E11 | `Campaign-Families.csv 有 7 处命中` | 全仓 **8 处**：`src/` 2 处（`master_export.py:25`、`server.py:2403`）、`tests/` 4 处、`scripts/` 2 处 | 计划 Task 13 写的"6 处"（src+tests）是对的；规格的 7 处对不上 |

另外 `--fast` 实测 **62 秒**（`Ran 61 tests in 61.949s`），规格 §8.0 称"秒级"。这不影响正确性，但会影响"每个阶段最低门槛"的心理成本——62 秒足以让人不想跑。

---

### 3.3 会让人低估工作量的（遗漏级，4 条）

#### E12｜`master_rows.py`：整条主数据入口被漏掉，且它自己也在编造

规格 §5.1 列了"五套并存的持久化机制"，**没有 `src/master_rows.py`**。但它是一条**活的、被 server 直接使用的第二套 ingest 路径**：

```
server.py:50   from src.master_rows import (...)
server.py:940 / 1356 / 1448 / 2167   实际调用
excel_loader.py:660  也被它用
master_export.py      9 处
```

而它内部同样在编造默认值：

| 位置 | 编造 |
|---|---|
| `master_rows.py:63` | `trp = _num(row.get("trp"), 0.0) or ... or **1.0**` —— 缺 TRP 时按 100% 效率算 |
| `:106` | `technicians=_int(row.get("technicians"), 1) **or 1**` —— 与 `available_qty` 同款 bug |
| `:77` | `priority=_int(row.get("priority"), 1)` |
| `:127-128` | `from_day ... or 1` / `to_day ... or 31` |

> **影响**：计划 Task 7–10「拆地雷 2」只覆盖 `excel_loader.py` / `data_loader.py`。做完之后验收 0.6 的三条 grep 会**全部通过**，但缺数据时系统仍会静默走 `master_rows` 这条路继续编造——**这正是规格自己批判的 A2 双轨在数据层的翻版，而这次是新报告自己漏掉的**。

#### E13｜规格 §5.6 的清单本身不完整（loader 里的"内建默认"）

规格列了约 25 个调用点，但有 3 个 `load_params` 的局部常量不在清单里：

```
excel_loader.py:385   default_ct   = 25.0 if semi else 40.0
excel_loader.py:386   default_trp  = 0.85 if semi else 0.71     ← 正是 0.71
excel_loader.py:387   default_mpq  = 100.0 if semi else 1.0
```

`default_trp = 0.71` 会命中验收 0.6 的 grep，但计划的任务分解里**没有任何一步负责它**。

#### E14｜根目录 9 个 xlsx，没有"哪份是真源"的声明

```
APS_DataSource_Template.xlsx              ← 被代码当规范模板
APS_DataSource_Template - Wenling Test.xlsx
APS_DataSource_Template_simulated.xlsx
dataSource.xlsx / dataSource.backup.xlsx
APS Database- Simulation draft.xlsx
APS_业务规则确认清单.xlsx
Light China APS Rules.xlsx / Production Plan Validation.xlsx
```

规格把 Excel 定位成"导入/导出适配器"（§5.7），方向对。但只要根目录同时躺着 9 个同名系工作簿、且没有一个 `README` 说明哪份是 canonical，**下一次"体检"还会得出互相矛盾的结论**——这次两份文件的坐标冲突，很可能就是这个问题的下游症状之一。

#### E15｜附录 C 的"清理记录"与仓库状态不符

附录 C 声称 2026-11-03 删除了 12 项。实测**全部仍在**：

| 声称已删 | 实测 |
|---|---|
| `archive/`（整树 16 文件） | 存在，**18 个文件** |
| `aps_project_lifecycle.html`（根） | 存在 |
| `src/static/index.html.original_backup` | 存在 |
| `src/static/index.html.ui_master_backup` | 存在 |
| `data/master/labor/`（3 CSV） | 存在（3 个 CSV 仍在 git 索引里） |
| `scripts/` 下 8 个一次性脚本 | **全部 11 个脚本都在**，包括 `fill_simulated_masterdata.py` |

> **影响有两层**：① 规格第 5.12 条要求"参考 `git show 3b47ceb:scripts/fill_simulated_masterdata.py`"——其实文件就在 `scripts/` 里，不用恢复；② 更严重的是**附录 C 降低了整份文档的可信度**：一段声称"已核实、已执行、并附验证结果（清理后 --fast 61/61 OK）"的记录，与仓库状态完全相反。这直接支持 §1.4 的判断——这份文档的某些部分不是在当前工作副本上写的。

---

### 3.4 需要重答的两个建模问题（不是数字错，是设计错）

#### E16｜"冻结"到底冻哪个类型？

规格 §5.2 与 D.3 第 3 条把 `ProductionInputSnapshot` 与 `RequirementPlan` 并列称为"稳定契约"，并给出 7 个消费点。实测两者不是一回事：

```
第 3 层边界（真实）：build_requirement_plan(snapshot) -> RequirementPlan     data_loader.py:677
RequirementPlan 的消费者：scheduler / validator / prechecker / exporter / audit / balance_builder
ProductionInputSnapshot 的使用者：build_requirement_plan、build_validation_matrix、
                                  server（6 处）、services/manual_audit、1 个测试文件
```

**结论**：真正需要冻结、真正有 7 个消费点的是 **`RequirementPlan`**；`ProductionInputSnapshot` 是"第 2 层输出 / 第 3 层输入"，它的耦合面比规格以为的小得多（E10 独立印证：只有 1 个测试文件构造它）。

这个区分有两个直接后果：

1. 计划的**偏差 2**（在快照末尾追加 `gaps`）风险远低于规格的估计——但**它仍然是对 D.3 第 3 条的字面违反**，而这个口子一开，"冻结"就变成了"可以在末尾加字段"的软约束（见 §5.1 的替代方案）。
2. 阶段 4 的 Repository 改造真正需要保护的是 `RequirementPlan` 的形状，而不是快照的字段表。

#### E17｜`line_base` 的主键口径，规格与计划互相矛盾

- 规格 §5.9 登记表：`line_base | master_line_base | **(line)** | — `
- 规格 §5.9 事实 1：`line_base` 缺 `site` 列，"**阶段 4** 必须裁决：加 site 列（推荐）…"
- 计划 Task 3 `EXPECTED_PK`：`"line_base": ("site", "line")`，且 Task 3 Step 4 在**阶段 0** 就加 `site` 列

三处不一致。计划的处理（阶段 0 加可选 `site` 列、主键 `(site, line)`）在工程上是对的——早加列比晚加便宜，而且 `required=False` 不破坏既有工作簿。但**规格需要同步改**，否则阶段 4 会基于另一个口径实现 DDL。

---

## 4. 我认可的建议，按优先级归类

> 分级口径：
> **P0** = 开工前必须解决，否则计划跑不通或验收判错；
> **P1** = 高价值、低风险，且**不改变求解结果**（基线不动），建议一次性做完；
> **P2** = 高价值但会改行为/有风险，需按序做、每步一个提交；
> **P3** = 有价值但依赖业务供数或应推迟。

### 4.1 P0｜开工前必须解决（6 条，预计半天）

| # | 建议 | 为什么必做 | 验收方式 |
|---|---|---|---|
| **P0-1** | **重建坐标体系：把 `file:line` 全部换成「符号名 + 一条可跑的定位命令」** | E1/E14：基准 commit 不存在，行号已漂移 100+ 行 | 计划里每条引用都能用 `python -c "import inspect"` 或 `Select-String` 复现；不再出现裸行号 |
| **P0-2** | **修正日期轴口径：34 列 / 306 行**，并重写 Task 6 的夹具与断言 | E3：这是验收 0.5 的数字，且计划夹具自相矛盾 | `len(_demand_dates(...)) == 34`；`_planning_dates()` 产出 34 个 ISO 日期，末项 `2026-11-15`；`line_headcount` = **306** 行 |
| **P0-3** | **改正计划中 3 个失效符号**：`ProductionScheduler→APSScheduler`、`snap.sf_tasks→plan.sf_tasks`、`build_requirement_plan` 移到 `data_loader.py` | E2：否则第一个新测试文件就 `ImportError`，执行者会开始"顺手修正"（D.4 明确禁止） | 每个新测试文件能单独 `python -m unittest <module>` 跑起来（哪怕是红的断言，也不能是 ImportError） |
| **P0-4** | **重写运行环境契约**：改用 `python`（3.14 + ortools 9.15 已装）、去掉 `.venv` 前缀、把 bash 命令换成 PowerShell 等价命令、把环境自检写成脚本 | §1.2：现在的写法在本机 100% 跑不通 | `scripts/check_env.py` 退出码 0；README/计划里的命令逐条手动跑通 |
| **P0-5** | **先把当前 WIP 单独提交**（`extent_of` / read-only sheet 修复 + server/UI 改动） | §1.3：否则"一个任务一个提交""git status 干净"两条验收永远不通过 | `git status --short` 仅剩 2 个 untracked 参考文件（或一并处理）；`--fast` 仍 61 OK |
| **P0-6** | **裁决外键与主键口径**：① 删 `material_arrival.part_no → parameters_sfn`；② BOM 双向外键降级为"数据质量报表"，不进 `validate_references` 的硬校验；③ 统一 `line_base` 主键为 `(site, line)` 并同步规格 §5.9 | E5/E17：否则 35 条告警当天就把报告变噪音；`line_base` 三处口径不一致 | 在真实模板上 `validate_references` 的输出 **≤ 5 条**；规格 §5.9 表与计划 `EXPECTED_PK` 一致 |

### 4.2 P1｜高价值、低风险、不改求解结果（7 条）

> 这批的共同特征：**黄金基线不应变化**。如果基线变了，说明某一处不是等价变换，应当停下（这比计划的"预期变化"表述更严格）。

| # | 建议 | 规格出处 | 我的补充 |
|---|---|---|---|
| **P1-1** | **`DataGap` 值类型 + 拆编造默认值（分 3 个提交：`line_base`/`tooling`、FG/SF 参数、PM/到货/客户/BOM）** | §5.6 | **范围扩到 `master_rows.py`**（E12）与 `excel_loader.py:385-387` 的三个 `default_*`（E13）。否则验收 0.6 的三条 grep 通过而行为没变 |
| **P1-2** | **`planning_calendar.py` 收敛硬编码年份** | §5.6 地雷 1 | 站点清单按 **13 处**补全（E6）；验收继续用 `grep -rn "datetime(2026" src/` 无输出——这条 grep 比清单可靠 |
| **P1-3** | **`master_schema.py` 扩展 PK / FK / `check` / `contract_tier` / `editable` / `surrogate_key`（全带默认值）** | §5.8 | 我核过：现有 15 个 `SheetSpec` 声明确实零改动即可工作（新字段都有默认值），这条设计是安全的 |
| **P1-4** | **删 `len(TEMPLATES)==20`，装 10 条契约检查骨架并显式标注"由哪个阶段打开"** | §7.1 | 计划里"每条 skip 必须说明由哪个阶段打开"这条纪律要保留——这是防止契约检查悄悄死掉的唯一手段 |
| **P1-5** | **`GET /api/inputs` 的站点切换改为请求级**（约 20 行） | §8.8（规格把它排到阶段 4） | **我认为应提前到 P1**。理由：① 这是**实存的数据丢失 bug**（切站点清空全局 `result/matrix/checks`）；② 成本约 20 行、零依赖；③ 阶段 0 本来就要动 `server.py`。规格自己的 §8.8 也承认这是"最小止血手段"——那就不该等到阶段 4 |
| **P1-6** | **种子脚本：改幂等（先删 `source='simulated'` 再写）+ 不覆盖签入的模板** | §5.12 | 计划的"已知限制 1：`--force` 不幂等"我不接受。往**被 git 跟踪、业务会打开看的**规范模板里追加非幂等数据，是一次事故的配方。要么写到 `APS_DataSource_Template_simulated.xlsx`（该文件已存在！），要么先清后写 |
| **P1-7** | **把 `read_master_rows` 读不到日期轴这件事显式化**：加 docstring 警告 + 一个断言测试 + 一个 `read_master_rows_with_axis()` 薄封装 | §5.9 事实 2 | 规格只说"不要图省事用它"，但下一个写代码的人（含智能体）一定会用。**把陷阱变成类型/名称上的不可能，比写成文档更可靠** |

### 4.3 P2｜高价值但要改行为，按序做（6 条）

| # | 建议 | 前置条件 | 我的补充 |
|---|---|---|---|
| **P2-1** | **双轨收敛：7 个模板逐个删 legacy，每步一个提交** | 先完成 **`enforce_*` → 规则实例的完整映射登记** | ⚠️ **附录 B 是按 32 条目录规则组织的，不是按 22 个 `enforce_*` 开关组织的，所以它不能当作删除清单。** 至少 3 个开关在附录 B 里找不到明确归属：`enforce_weekend_penalty`（周末开班惩罚）、`enforce_customer_priority`（客户保供加权）、`enforce_sequential_pulling`（顺位拉动）。另有 `enforce_d1_clock_hard` 与 `enforce_d1_clock` 成对存在（同一规则 SC-01 的硬/软两档），在新模型里表现为两个规则实例而非一个开关。照 §8.3 直接"删全部开关"，会把**没有目录规则覆盖的行为**一起删掉。必须先出一张 **22 行**的对照表，每行写明"迁到哪条规则 / 已废弃（理由）/ 保留为站点设置" |
| **P2-2** | **B2：延误变量域解耦到 horizon** | 单独提交 + 书面基线理由 | 认可计划"必须在阶段 0 做"的判断（否则阶段 1 的回归门跑不动）。但建议它**独立成一个批次**，不要和其他阶段 0 任务混在同一个 PR——它是本阶段唯一预期改变计划结果的改动，混在一起就没法二分归因 |
| **P2-3** | **指标层：`MetricContext`/`StructureContext` 分离 + 7 个维度 + 通用编译器 `reduction × op × nature`** | P1-3 完成 | 设计认可。风险提示：`rhs` 引用指标（§2.4）会让编译器多一条路径，建议**第一版只支持标量 rhs**，指标引用留到第二阶段——HC-06/HC-09 可以先用手写 `structure` 模板过渡 |
| **P2-4** | **PenaltyBook 单一入口（D12）+ 删权重塔（B1）** | P2-3 | 我核到 `solve_lexicographic` 已在，风险比规格描述的更低。但注意：`solve.py:81` 的 `per_group_budget = time_limit_seconds / len(soft_groups)` **也是按壁钟切分**——D13 只点了 `scheduler.py:677,714,747`，漏了这里 |
| **P2-5** | **绑定报告：先做 `out_of_scope` / `bound` / `satisfied` / `violated` 四个状态，其余随后** | P2-3 | 建议**分期**。8 个状态一次做完，在还没有真实数据时，会让每条新规则都挂 `simulated` 或 `degraded` 徽标，徽标变成背景噪音——这正是这份规格最想消灭的"看起来生效了"。先把最有价值的 `out_of_scope` 做出来 |
| **P2-6** | **契约检查 1–10 上线** | P2-3、P2-1 | 第 10 条（断言 `force_hard`/`enforce_*` 不存在）是本设计唯一的防倒退机制，赞成。但要先解决 E4：**没有 CI**，这条检查没有执行载体 |

### 4.4 P3｜依赖业务或应推迟（4 条）

| # | 建议 | 推迟理由 |
|---|---|---|
| **P3-1** | `MasterDataRepository` + `SnapshotStore`（draft→published）+ 方言感知 DDL + 迁移机制 | 规格 D14 的裁决（不提前）我认可，理由成立：无真实用户，不构成生产事故。但 §8.8 的前提"无真实用户"要**写进代码注释或 README**，一旦上线就要重新评估 |
| **P3-2** | 消灭 `session_state` 全局单例 | 同上。但 P1-5 止血要先做 |
| **P3-3** | 4.6 表其余主数据扩展（换模技术员时段 / 冻结基线 / 试产窗口 / 订单属性 / 交期容差数值化） | 需业务供数或确认，工程无法闭环。规格 §8.7 的收敛（3 供数 + 2 确认）我认可 |
| **P3-4** | HC-08 / HC-15 / SC-10 的真实数据替换模拟数据 | 见 §5.2：模拟数据的生成规则本身要改，否则替换前的结论不可信 |

---

## 5. 我不建议照做的建议（6 条）

### 5.1 ❌ 在 `ProductionInputSnapshot` 末尾追加 `gaps` 字段（计划偏差 2）

**计划的论证是对的，结论是错的。**

计划说：已核实 9 处构造点全用关键字参数，追加带默认值的尾字段零影响。**我复核了，成立**——9 处构造点（`src/` 3 处 + `tests/test_regressions.py` 6 处）确实全用关键字参数，唯一构造它的测试文件只有 1 个（E10）。

但**技术上安全 ≠ 应该这么做**：

- D.3 第 3 条明确"禁止改第 3 层契约……字段和 7 个消费点签名冻结"。计划用"冻结的是既有字段，不是禁止追加"来解释——这是一个**滑动的定义**。今天追加 `gaps`，明天追加 `data_quality`，后天追加 `as_of_date`，"冻结"就名存实亡
- 更关键的是 E9：真正该冻结的是 `RequirementPlan`，快照本来就不是那 7 个消费点的输入。**为了给快照加字段而松动冻结原则，代价高、收益低**

**替代方案（成本几乎相同）**：

```python
# 方案 A：返回值解耦（推荐）
@dataclass(frozen=True)
class LoadResult:
    snapshot: ProductionInputSnapshot
    gaps: Tuple[DataGap, ...]

def load_workbook(path, site) -> LoadResult: ...

# 方案 B：只给 build_requirement_plan 加一个可选关键字参数
def build_requirement_plan(snapshot, *, apply_scrap=False,
                           gaps: Optional[List[DataGap]] = None) -> RequirementPlan: ...
```

方案 A 的额外好处：`LoadResult` 天然是阶段 4 `Repository.import_workbook() -> Sequence[ImportReport]`（§5.10）的形状，提前对齐。方案 B 的额外好处：`RequirementPlan` 上已经有 `warnings` 字段，`gaps` 可以跟着它走——**那是同一条通道，不需要新契约**。

### 5.2 ❌ 用「Routing 人数 × 班次系数」生成可用人数模拟数据

规格 §4.6.1 的派生规则：

```
available_headcount = max(1, floor(routing_headcount(line) × factor(shift_no)))
factor = {1: 1.0, 2: 1.0, 3: 0.8}
```

**这是循环定义**：`routing_headcount` 正是 HC-15 要比较的"需要多少人"。

按当前模板实测（`PGLINKAGE` 需要 3 人）：

| 班次 | 需要 | 可用（seed 生成） | HC-15 结果 |
|---|---|---|---|
| 1 | 3 | 3 | 恰好满足（约束不 binding） |
| 2 | 3 | 3 | 恰好满足 |
| 3 | 3 | 2 | **必然违反**（factor 0.8 强行制造缺口） |

后果：

1. **`source='simulated'` 的数据把规则结果预先决定了**：白/中班永远"正好够"，夜班永远"不够"。这不是"任意的模拟数据"，是**带方向的业务结论**被伪装成参数
2. 阶段 2 验收 2.8「HC-15 真的编译出约束」会通过，但它证明的只是"编译器把 3 ≤ 2 写进了模型"，**不能证明规则实现正确**——任何实现都会得到这个结论
3. 更糟的是它给了业务一个假信号："夜班人数不足"看起来是数据发现，其实是常数 `0.8`

**改写建议**：

- 改用**与需求量无关**的基准，例如按产线定员（`line_headcount = 产线定员`，一个与 Routing 人数不同源的手工常量，允许班次间不同），并**显式在 `Specification/masterdata_simulation_notes.md` 写明"此数据仅用于打通链路，不代表任何业务结论"**
- 或者更进一步：**承认这批数据不能用于验证规则语义**，把验收 2.8 从"HC-15 编译出约束"改成"HC-15 在 synthetic 夹具上编译出**预期数量**的约束，且 `out_of_scope` 负向用例能正确报错"

第二种更诚实。D15 的"先落模拟数据"我认可其动机（不阻塞工程），但**模拟数据的生成规则必须避免编码业务结论**。

### 5.3 ❌ 把「`grep enforce_* 无命中`」当作阶段 1 验收 1.2

见 P2-1。`enforce_*` 里有一部分是**合法的站点级开关**（如 `enforce_jitter` 切换纯目标项、`enforce_campaign_batching` 切换合批），它们的去向是"规则实例的 `enabled`"，不是"删除"。用 grep 归零当验收，会诱导执行者直接删掉开关而不做映射——**这正是规格 §7.1 第 10 条想防的"半年后某个赶工的改动"，只不过发生在反方向上**。

**改写**：验收 1.2 改为"存在一张 22 行的对照表，每行的去向字段 ∈ {迁到规则 X, 已废弃(理由), 保留为站点设置}，且 CI 校验表里的规则 X 都真实存在"。

### 5.4 ❌ 一次性做 13 个任务、11 个源文件、6 个新测试模块

计划的依赖图（任务 1→6 成链，7–10 依赖 1，11–13 独立）是对的，但把它当**一个批次**合并，会导致：

- 阶段 0 里混了"纯增量、基线不动"（P1）、"必然改基线"（B2）、"只报告不强制"（FK）三类性质完全不同的改动 → 出问题时无法二分
- `--fast` 从 61 涨到 100+、耗时从 62 秒涨到 2 分钟以上，回归门的心理成本继续上升

**改写**：拆成 4 个可独立合并的批次（见 §7）。

### 5.5 ⚠️ 8 个绑定报告状态一次做完

见 P2-5。建议先做 4 个。理由不是"做不完"，而是**信息价值递减**：`simulated`/`degraded` 在没有真实数据时会覆盖大多数规则，徽标从"告警"退化为"背景色"。

### 5.6 ⚠️ 把种子数据写进 `APS_DataSource_Template.xlsx`

见 P1-6。补充一点：仓库里已经有 `APS_DataSource_Template_simulated.xlsx`。**规范模板应当保持"业务可签字的干净输入"，模拟数据写到派生文件**——这条界线一旦模糊，验收 0.3/0.4（md5 一致、`git status` 干净）就变成了和 git 捉迷藏。

---

## 6. 两份文件都没覆盖的缺口

| # | 缺口 | 为什么重要 |
|---|---|---|
| G1 | **`master_rows.py` 这条第二主数据入口**（E12），含自己的编造默认值 | 不修它，A4（静默降级）在数据层只修了一半；而且验收 0.6 会假通过 |
| G2 | **没有 CI**（E4），而 D5 的第一层叫"静态契约（CI）" | 10 条契约检查没有执行载体 = 没有约束力 |
| G3 | **`fast` 只覆盖 7/40 测试模块**（17.5%） | 规格批评"没进列表的 fast 全绿是假的"，但没给出"应该覆盖多少"的目标 |
| G4 | **根目录 9 个 xlsx，无 canonical 声明**（E14） | 这次两份文档坐标冲突的根因之一；阶段 4 的 ExcelRepository 必须先回答"哪份是真源" |
| G5 | **`docs/ARCHITECTURE.md` 已过期**（基于 `5727e4c`，写着"19 类算子""15 表"，与代码不符） | 它是所有误解的上游。规格自己在讲"文档说谎"（A5 目录规则、手册第 210 行），却没点出架构文档也在说谎。**建议把"文档与代码同源校验"也做成一条契约检查** |
| G6 | **`validator.py` 的自证循环**（A8）虽然列了，但没给"交叉验证怎么做"的最小实现 | 规格只说"让 audit/engine 拿绑定报告做交叉验证"。这一步需要"报告可复现"+"审计器不读同一份 plan"两个条件，比一句话复杂 |
| G7 | **模拟数据的退出条件没有量化** | 4.6.3 说"业务供数后把 `source` 改 `actual` 即可"。但没有定义"什么算供数完成"（覆盖率？抽样？业务签字？），也没有"模拟数据用了多久要升级为阻塞项"的机制。当前阶段里已经有 297（实为 306）行 + 5 行模拟数据，很容易变成永久 |

---

## 7. 建议的下一步（可执行的最小路径）

### 7.1 四个可独立合并的批次

```
批次 A「把计划修到可执行」        预计半天   不改任何 src/ 代码
  A1  重建坐标：file:line → 符号名 + 定位命令            (P0-1)
  A2  日期轴 34 列 / 306 行；重写 Task 6 夹具与断言       (P0-2)
  A3  修 3 个失效符号（APSScheduler / plan.sf_tasks /
      build_requirement_plan 位置）                      (P0-3)
  A4  重写环境契约（python 3.14 / PowerShell / 自检脚本） (P0-4)
  A5  提交现有 WIP                                       (P0-5)
  A6  裁决外键与 line_base 主键，同步规格 §5.9            (P0-6)
  门槛：计划 rev.2 产出；每条任务能在本机跑通第一步
        （允许断言红，不允许 ImportError / CommandNotFound）

批次 B「地基：纯增量，基线不动」  预计 1–2 天
  B1  schema 扩展 PK/FK/check/contract_tier（全默认值）   (P1-3)
  B2  DataGap + 拆编造（3 个提交，含 master_rows.py）     (P1-1)
  B3  planning_calendar 收敛 13 处年份                    (P1-2)
  B4  删数量断言 + 契约骨架 + 注册进 fast 列表            (P1-4)
  B5  GET /api/inputs 请求级化                            (P1-5)
  门槛：黄金基线 md5 不变（这一批的"预期变化"必须是零）；
        git status 干净；--fast 全绿且耗时 < 90s

批次 C「两张新表 + 种子数据」    预计 1 天，依赖 B1
  C1  两个 SheetSpec + 不要求上传（required=False）      (P1-3)
  C2  种子脚本：幂等 + 写派生文件 + 生成规则去循环化      (P1-6, §5.2)
  C3  模板对账重跑 + 记录 provenance
  门槛：连跑两次 md5 一致（这次是真的幂等）；
        306 + 5 行；不触碰 APS_DataSource_Template.xlsx

批次 D「B2 延误窗口」            预计半天，独立 PR
  D1  LATENESS_SLACK_SHIFTS 删除，窗口解耦到 horizon     (P2-2)
  门槛：超载夹具产出带延误的计划而非 RuntimeError；
        基线重切 + 提交信息写明「缺陷编号 / 预期方向 / 实际结果」
```

### 7.2 三个"不要现在做"的

1. **不要在同一次提交里混"基线不动"与"基线必变"的改动**——否则 §8.1 的基线政策退化为橡皮图章
2. **不要在批次 B 之前动 `scheduler.py`**（除批次 D）——`scheduler.py` 是唯一"改一处、全盘皆变"的文件
3. **不要在 `enforce_*` 映射表产出之前开始阶段 1**——见 P2-1

### 7.3 给规格修订的建议（rev.4）

只改 7 处，其余保持：

| 位置 | 改成 |
|---|---|
| 头部 | 删掉 `17a383b` / `89ee6e0` / "`feat/shaojing-dev`（起点 `3b47ceb`）"；改为"坐标基准 = 当前 `main` HEAD，以符号名引用" |
| §4.6.1 | 日期轴 34 列、306 行；并把 `factor` 的循环定义问题改写（§5.2） |
| §4.6.2 | 加一句：`WA14208S`/`WD48696S` 不出行的判据已是最终裁决（计划偏差 5 已同步，规格正文需同改） |
| §5.2 | 区分 `ProductionInputSnapshot`（第 2 层输出）与 `RequirementPlan`（第 3 层契约）；消费点清单按实重列 |
| §5.9 | `line_base` 主键改 `(site, line)`；删 `material_arrival.part_no` 外键；BOM 双向外键的违规面改成 28/56 并说明处置 |
| §5.6 | 清单补 3 个 `default_*` 常量与 `master_rows.py` 一处整节 |
| 附录 C | 整节改为"**待执行**的清理清单"（附实测：12 项仍在），或删除后重写为真实状态 |

---

## 附录 A：核对命令（可复跑）

```powershell
# 环境（§1.2）
python -c "import sys,ortools,openpyxl,fastapi; print(sys.version, ortools.__version__)"
Test-Path .venv\Scripts\python.exe          # False

# 基线（§2.1 #24）
$env:PYTHONIOENCODING="utf-8"; python run_tests.py --fast    # Ran 61 tests ... OK, 62s

# 坐标基准（§1.4）
git cat-file -t 89ee6e0                     # fatal: Not a valid object name
git merge-base --is-ancestor 3b47ceb feat/shaojing-dev      # 1
git rev-list --count main..feat/shaojing-dev                # 0

# 日期轴（E3）
python -c "import openpyxl;ws=openpyxl.load_workbook('APS_DataSource_Template.xlsx')['客户需求订单清单 Customer Demand'];print(len([1 for c in range(4,ws.max_column+1) if ws.cell(1,c).value]))"
# → 34

# 主键 / 外键（E5）
python - <<'PY'
from src.excel_loader import read_master_rows
rows = read_master_rows('APS_DataSource_Template.xlsx')
sf = {str(r['part_no']).strip() for r in rows['parameters_sfn']}
fg = {str(r['part_no']).strip() for r in rows['parameters_fg']}
print('material_arrival violators:',
      sum(1 for r in rows['material_arrival'] if str(r['part_no']).strip() not in sf), '/', len(rows['material_arrival']))
print('bom rows violating either side:',
      sum(1 for r in rows['bom'] if str(r['fg_part_no']).strip() not in fg or str(r['sf_part_no']).strip() not in sf), '/', len(rows['bom']))
PY
# → 7/7  和  28/56

# 编造默认值（§2.1 #18）
python -c "import dataclasses;from src.data_loader import LineBase,ToolingMold;print([(f.name,f.default) for f in dataclasses.fields(LineBase)]);print([(f.name,f.default) for f in dataclasses.fields(ToolingMold)])"

# 开关数量（E7）
python -c "import inspect;from src.scheduler import APSScheduler;ps=list(inspect.signature(APSScheduler.__init__).parameters);print(len(ps), len([p for p in ps if p.startswith('enforce_')]))"
# → 63 22

# 消费点（E9）
Select-String -Path src\*.py,src\*\*.py -Pattern "ProductionInputSnapshot" | Group-Object Path

# 清理记录（E15）
Test-Path archive, aps_project_lifecycle.html, data\master\labor, src\static\index.html.original_backup
# → 全部 True
```

## 附录 B：缺陷严重度重排（相对规格附录 A/B 的意见）

规格的缺陷索引基本准确，我只调整三处的**处置顺序**：

| 缺陷 | 规格排在 | 我建议 | 理由 |
|---|---|---|---|
| A8（校验器自证 + `SETUP_SECONDS=0`） | 阶段 3 | **阶段 0 的裁决 + 阶段 2 的修** | `SETUP_SECONDS=0` 是 §4.4 裁决的直接后果，不依赖绑定报告；早改早受益 |
| A3（`session_state` 并发） | 阶段 4 | **止血提前到 P1（20 行），消灭整体留在阶段 4** | 规格自己承认这是"最小止血手段"，成本极低 |
| A4（静默降级） | 阶段 0 + 契约 #2 | 阶段 0 + 契约 #2 **+ 把 `master_rows.py` 纳入** | 漏掉这条路径，A4 只修一半 |

其余缺陷的编号、证据、归属阶段，我核对后认为可以用作后续工作的索引，不需要重排。

---

## 结语

这两份文件的质量高于仓库里既有的任何文档：它们承认了"配置了没反应"这样的结构性失败，给出了可执行的验收判据，还主动把自己不确定的地方标成"待核实"（rev.3 的三处更正就是证据）。

问题集中在**它继承的坐标与环境**：一份不存在的 commit、一个不存在的 `.venv`、一条比自己批判的缺陷更严重的"已清理"记录，以及一个差了 1 天的日期轴。这些都不是设计错误，是可以在一个半天里修完的执行前提——但如果不修，接下来每个执行者都会在第一个小时里遇到 `ImportError`，然后开始"顺手修正"，而那正是文件自己反复禁止的事。

所以我的建议很简单：**先做 §7.1 的批次 A（半天，不改代码），再开始批次 B。** 设计的价值在批次 B 之后才开始兑现，而在批次 A 之前它无法兑现。
