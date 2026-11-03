# APS 规则集重设计：指标驱动的组合式配置

| 项 | 值 |
|---|---|
| 状态 | 设计已确认，阶段 0 实施计划已产出 |
| 日期 | 2026-11-03（rev.2 同日：落 D14/D15，补 5.8–5.12 可编码规格与 8.0–8.7 验收清单）（rev.3 同日：对着真实模板核出 4.6.1 行数、4.6.2 行集合、5.9 事实 2 三处错误并更正） |
| 分支 | `feat/shaojing-dev`（起点 `3b47ceb`） |
| 范围 | 规则集子系统重设计 + 其前置的双轨收敛与数据层模块化 |
| 取代 | `aps_rule_set_editor_manual.md` 中「19 个算子 + params」的配置模型；`aps_new_rule_set_gap_analysis.md` 的能力对账结论 |
| 决策 | D1–D15（1.1 节），全部已裁决，实施时不再重新讨论 |
| 实施计划 | 阶段 0：`Specification/plans/2026-11-03-stage0-foundation.md`（13 个任务，含 7 条规格偏差的处置方案）。阶段 1 另出一份，前提是阶段 0 全部合并 |

**rev.3 更正的三处实测错误**（都是撰写 rev.2 时凭基线窗口和 CSV 推断、未对着模板核实的数字）：

| 位置 | rev.2 写的 | 实测 | 更正理由 |
|---|---|---|---|
| 4.6.1 | 日期轴 10 天 → `line_headcount` **90 行** | 模板 `Customer Demand` 表头声明 **33** 个日期列（`10/13`–`11/15`，25 列有量）→ 3 产线 × 33 × 3 = **297 行** | 10 天是黄金基线的**求解窗口**，不是模板的**数据轴**。人数可用性与当天有无订单无关，只覆盖 25 天会让计划员一补空日期就缺行 |
| 4.6.2 | 「按 LHD/RHD 关键词归并」+「4 个『同时生产=Y』料号落 unpaired」 | 两条规则选出的料号集合**不同**，交集只有 `WB80510`。单用关键词得 4 行，单用 Y 标记得 0 paired | 必须取并集才同时满足「1 对可辩护」与「4 个 unpaired」= 5 行 |
| 5.9 事实 2 | 「`header_row_for` 已负责动态日期列定位」 | `header_row_for`（`master_schema.py:462`）只找**表头行**；日期列由 `excel_loader._demand_dates`（`:200`）给出。且 `read_master_rows`（`:645`）只遍历 `spec.columns`，**完全读不到日期轴** | 照 rev.2 的说法写代码会得到空日期轴，种子脚本落 0 行 |

本文档由一次全仓审查驱动。审查核实的全部缺陷以 `file:line` 形式在附录 A 索引，正文引用时不再重复论证。

**面向实施者的读法**：这是一份**规格**，不是实施计划。第 2–7 节定义「做成什么样」（数据结构、签名、语义表都是可照抄的），第 8 节定义「按什么顺序做、怎么算做完」（每阶段一份验收清单，每条都能用一个命令判定）。附录 D 是交接约定。每个阶段开工前先产出该阶段的实施计划，把清单拆成任务。

---

## 0. 背景与动机

### 0.1 当前配置模型的四个结构性失败

**（1）配置与生效之间没有反馈回路。** `src/kernel/registry.py` 声明了 20 个模板及其参数 Schema，但其中 11 个参数在 `src/kernel/compiler.py` 中引用数为 **0**：

```
include_setup  cutoff_time  tolerance_minutes  tolerance_shifts  available_qty
qc_release_hours  crew_source  allow_overtime  due_window_days
cross_shift_inherit  rounding
```

工厂在规则集编辑器里配置这些值 → 校验通过 → 无任何效果。编辑器手册 `aps_rule_set_editor_manual.md:210` 自己承认了这点：「标注【预留】的参数表示 Schema 已登记、当前编译器未使用；保留是为了向后兼容，不要依赖它改变结果。」

最恶劣的一例是 `available_qty`：Tooling-Mold『可用套数』被完整加载（`excel_loader.py:337` → `data_loader.py:496`）、在 schema 里声明（`master_schema.py:332`）、通过 API 返回且可编辑（`server.py:1054`、`1194-1195`）、在 registry 里是正式 ParamSpec（`registry.py:191`）——然后 `compiler.py:318` 写死 `available = 1`。这不是 bug，是一条被硬编码进代码的业务裁决（HC-01：一套物理模具只能在一台机器上跑），代价是整条数据链路全部变成装饰。

**（2）同一约束存在两份实现，且已经漂移。** material balance / capacity / FIFO sequence / tooling / PM / concurrency 六类约束在 `scheduler.py` 的 legacy 路径与 `kernel/compiler.py` 各写一遍。`_compile_rules_for(..., force_hard=True)`（`scheduler.py:1319,1383,1403,1428,1452,1459`）**无视规则集配置的 `nature`，强行改硬**——6 个模板的 `nature` 字段是摆设。漂移已经发生：REG-01 的料号级时间相位物料账本只修在 legacy（`scheduler.py:1657`），kernel 侧仍是旧的逐父任务 pairwise 实现（`compiler.py:353-380`）。

**（3）业务目录与实际执行之间没有机器可验证的对应关系。** 仓库里存在三套互相竞争的「规则」命名空间：

- (a) 32 条业务目录 `HC-01…BS-06`（`config/rule_catalog/aps_32_rules.json`，纯文档 JSON，`status` 字段 26 条标 partial、**0 条标 implemented**）
- (b) legacy 分站复选框 `R-WL-xx / R-SY-xx` → 映射到 18 个 `enforce_*` 开关（`server.py:1563-1624`）
- (c) kernel `RuleInstance`，以 20 个 template id 为键

(b)→(c) 靠 `src/kernel/rule_mapping.py:13-51` 一张手写表 `LEGACY_KERNEL_MAP` 粘合，**(a)→(c) 完全没有连接**。业务确认清单上签字的 HC-xx 与编译进模型的约束之间没有任何可校验的映射。

**（4）数据缺失一律静默降级。** 换模矩阵加载失败 → print 后整个 SDST 能力悄悄关闭（`scheduler.py:447-461`）；精排失败 → 返回不含 setup 时间的第一版计划（`scheduler.py:883-885`）；PM 日期解析失败 → `except: pass`，封锁班次直接消失（`data_loader.py:739,767,785`）；交期解析失败 → 编造假交期 `datetime(2026,10,20,15,0)`（`data_loader.py:256-259`）；缺换模矩阵边 → 回退 60 分钟（`scheduler.py:2301`、`sequence.py:189`）。

### 0.2 规则覆盖的真实状况

独立核定 32 条目录规则后：**REAL（真实现）只有 4 条**（HC-03、HC-06、HC-12、SC-02），**STUB 1 条**（HC-10），其余 27 条 PARTIAL，其中 **16 条默认关闭或数据缺失即静默跳过**。没有一条是「目录声明、代码零实现」的纯谎言——目录 JSON 的自我标注（26 partial / 3 preprocess / 2 configurable / 1 postprocess）比旧文档诚实——但「能配的」与「有效的」之间的差距远大于业务方所知道的。

### 0.3 本次重设计要达成的

让「设计一条新规则」变成**选指标 + 设阈值 + 选作用域 + 定硬软**的组合操作，不改 Python 代码；并让「配了没反应」在结构上不可能发生。

---

## 1. 设计目标与非目标

### 1.1 已裁决的设计决策（D1–D15）

| # | 决策 | 内容 |
|---|---|---|
| D1 | 自由度层级 | 新规则 = 选参数 + 设指标的**组合式配置**，不是表达式 DSL |
| D2 | 规则边界 | 第一阶段只做「指标 + 阈值」类；架构为「结构/排序」类预留接入点，本次不实现 |
| D3 | 双轨处理 | 消灭 legacy 双轨（A2）**纳入本次重设计，作为第一阶段**——指标层的前提 |
| D4 | 命名空间 | 32 条业务目录保留为业务分类层，目录→规则的映射**机器可校**；legacy 复选框删除 |
| D5 | 生效保证 | **静态契约（CI）+ 运行时绑定报告**两层 |
| D6 | 数据迁移 | **干净切换**：种子规则集重写，SQLite 旧版本置 archived 且不可再激活 |

补充决策（设计过程中确认）：

| # | 决策 | 内容 |
|---|---|---|
| D7 | 指标无模型访问权 | `MetricContext` 类型里不存在 `model` 成员，指标**在类型层面**不可能建约束 |
| D8 | scope 属性词表固定 | 每个维度只能过滤已声明的属性，保存时可校验；不开放任意属性名 |
| D9 | 数据缺口分层 | `on_data_gap` 不设单一全局默认，由 schema 的 `contract_tier` 分层决定（第 4 节） |
| D10 | 存储引擎 | 开发阶段 SQLite，Repository 端口为 Postgres 预留；DDL 生成方言感知 |
| D11 | `op=minimize` 与 `nature` 互斥 | 纯目标项不是软约束，保存期拒绝同时声明 |
| D12 | 目标项单一入口 | 任何规则不得直接往 `model.Minimize` 加项，只经 PenaltyBook |
| D13 | 确定性 | 编译顺序按 `(group.priority, rule.id)` 排序；tier 预算用 `max_deterministic_time` 而非壁钟；`as_of_date` 显式传入并随快照持久化 |
| D14 | 阶段 4 不提前 | `session_state` 的消灭保持在阶段 4，**不因并发风险单独提前**。理由：当前无真实计划员在线使用，并发污染尚不构成生产事故；提前会打断「阶段 0→1→2」的指标层主线 |
| D15 | 缺数据先人为设定 | 4.6 节第 1、7 项（班次可用人数、L/R 配对表）**不等业务供数，阶段 0 内先落一批显式模拟数据**。但必须走「可见的种子路径 + provenance 标记」，**绝不允许退化成 loader 内的编造默认值**（5.6 节地雷 2）。绑定报告对这类数据出 `simulated` 状态 |

### 1.2 非目标（明确排除，防范围蔓延）

- 结构类规则的表达式 DSL
- Postgres 实现（只做端口预留与方言感知 DDL 生成器）
- 多租户/工厂隔离与鉴权（A3 的身份部分，独立议题）
- 求解器性能优化与料号级变量重构（B10 / ISSUE-17）

---

## 2. 规则实例数据模型

规则实例从 `{template, params}` 变为：

```json
{
  "id": "WL_WEEKEND_UTIL_BAND",
  "kind": "metric",
  "metric": "line_shift_utilization",
  "reduction": "each",
  "op": "between",
  "rhs": [0.80, 1.05],
  "scope": {
    "line": {"not": ["PGLINKAGE"]},
    "stage": "*",
    "part": "*",
    "shift": {"weekday": [5, 6]}
  },
  "nature": "hard",
  "group": "G2_CAPACITY",
  "weight": 140,
  "enabled": true,
  "catalog_refs": ["SC-02"],
  "params": {"tail_exempt": true},
  "on_data_gap": null
}
```

### 2.1 字段定义

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✓ | 规则集内唯一 |
| `kind` | ✓ | `metric`（可组合）/ `structure`（工程师手写模板） |
| `metric` | `kind=metric` 时✓ | 指向指标注册表 |
| `reduction` | 非 `GLOBAL` 维度✓ | `each` / `sum` / `max` / `min` / `count_violations` |
| `op` | ✓ | `>=` / `<=` / `==` / `between` / `minimize` / `maximize` |
| `rhs` | 约束类✓ | 标量、`[lo,hi]`（`between`）、或指标引用 `{"metric": "...", "offset": -1, "reduction": "..."}`；`minimize`/`maximize` 必须为空 |
| `scope` | ✓ | 谓词对象，见 2.3 |
| `nature` | 约束类✓ | `hard` / `soft`；与 `op=minimize/maximize` **互斥**（D11） |
| `group` | ✓ | 指向 `groups[]`，决定字典序层级 |
| `weight` | `soft` 或目标项✓ | 组内相对权重 |
| `enabled` | ✓ | |
| `catalog_refs` | ✓ | 落地的 HC/SC/BS 目录条目；空数组需在 `unenforced` 登记表有对应理由 |
| `params` | — | **仅**指标特有的非阈值旋钮（如 `tail_exempt`） |
| `on_data_gap` | — | 覆盖规则集级默认；`null` 表示不覆盖 |

### 2.2 六个关键变化

**（1）`kind` 区分两类规则。** `metric` 走通用编译路径；`structure` 走手写构建器（sequence 的 MTZ 排序、campaign 块、co-product 同步、模具占用分组）。两者共用同一个规则实例接口、同一套契约测试、同一份绑定报告——这是 D2 的第二类预留点。

**（2）`metric` 取代 `template`。** 不再指向 `_DISPATCH` 手写分派表。

**（3）`reduction` 是类型安全核心。** 「利用率 ≥ 0.95」这句话本身病态——利用率是逐班次的量，必须说明是对每个班次成立还是对总和成立。非 `GLOBAL` 维度的指标**必须**声明 reduction，否则保存时拒绝。

**（4）`op` + `rhs` 取代 params 里的阈值。** `between` 让双边带保持为一条规则（现有 `capacity_utilization` 的 `min_util`/`max_util` 合并为 `between [0.95,1.05]`）。`minimize`/`maximize` 无 rhs，是纯目标项——把 SC-09 jitter、SC-08 顺位填补这类「本来就没有阈值、只是要最小化」的规则正确归类。

**（5）`nature` 是硬/软的唯一真源。** 没有 `force_hard` 参数，没有 `enforce_*` 开关能覆盖它。A2 的覆盖通道从数据模型层面不存在。

**（6）`catalog_refs` 建立 (a)→(c) 的机器可校连接。** CI 校验：每个目录条目要么被至少一条启用规则的 `catalog_refs` 覆盖，要么进 `unenforced` 登记表并附理由（契约检查 #7）。

### 2.3 scope：从四维精确匹配到谓词对象

今天是 `site/line/stage/part` 四维精确字符串匹配（`kernel/types.py:43-49`），缺失即 `"*"`。扩展为：

- **多值列表**：`"line": ["轴套6", "轴套7"]`
- **取反**：`"line": {"not": ["PGLINKAGE"]}`
- **新增 `shift` 过滤族**：`{"weekday": [5,6]}` / `{"name": ["白班"]}` / `{"date_from": "...", "date_to": "..."}`
- **新增 `order` 过滤族**：`{"type": ["正式单"]}` / `{"customer_tier": ["A"]}`

**不引入任意表达式**（D8）。「周末班次」「A 客户订单」「试产单只排白班」三个真实需求刚好落在列表 + 取反的表达力内。

scope 过滤的是索引背后的**实体属性**（task → 它的 order → customer_tier；shift → weekday），不是索引分量的字符串比对。

### 2.4 `rhs` 引用指标：一处必须承认的复杂度

`rhs` 除标量外允许引用另一个指标。HC-09 半成品安全前置期（`task_start_shift >= parent.task_completion_shift + buffer`）与 HC-06 BOM 齐套本质是「指标 vs 指标」。

这也是 B2 中「SF 窗口 = `parent_hi - 1`（`scheduler.py:1162-1163`），与父任务实际开工班次无关，前置期语义错误」的正确修法。它让编译器多一条路径，但没有它这两条规则无法正确表达。

### 2.5 `params` 收窄

只放指标特有的非阈值旋钮。11 个死参数在这套模型里没有容身之处：一个参数要么是该指标声明的输入并被编译器读取，要么不存在。

---

## 3. 指标注册表

### 3.1 核心接缝：指标不碰模型（D7）

指标构建器**没有 `model` 访问权**——`MetricContext` 这个类型里根本不存在 `model` 成员。指标只能返回表达式；`model.Add`、松弛变量、辅助变量的创建全部归编译器。

三个直接结果：

1. `reduction` 和 `op` 可以完全通用，编译器对所有指标一视同仁
2. 今天 `compiler.py:167-170` 那种 `hasattr(ctx, "slot_capacity_ms")` 的未声明探测无法存在
3. `kind: structure` 拿到的是**另一个类型** `StructureContext`，它才有 `model`——第二类预留点落在这一层类型差异上

```python
class MetricContext(Protocol):      # 只读投影，无 model
    num_shifts: int
    def lines(self) -> Sequence[str]: ...
    def window(self, task_id) -> tuple[int, int]: ...
    def load(self, line, shift) -> LinearExpr: ...
    # 实际消费哪些由 MetricSpec.requires 声明，并由契约检查 #3 强制

class StructureContext(Protocol):   # 有 model，可创建决策变量
    model: CpModel
    ...
```

结构先于指标编译（第 6.1 节），因此指标可以引用结构创建的变量。

### 3.2 MetricSpec

```python
@dataclass(frozen=True)
class MetricSpec:
    metric_id: str
    display_name: str                     # 中文，UI 用
    description: str
    dimension: Dimension                  # GLOBAL/LINE_SHIFT/PART_SHIFT/TASK/ORDER/MOLD_SHIFT/PAIR_SHIFT
    unit: str                             # ratio/count/minutes/units/shifts
    domain: Optional[Tuple[float, float]] # 保存期校验 rhs
    requires: Tuple[str, ...]             # 消费的 MetricContext 成员，显式声明
    data_dependency: Tuple[Tuple[str,str], ...]  # (sheet_key, column_key) —— 见 4.3
    params: Tuple[ParamSpec, ...]         # 仅非阈值旋钮
    catalog_refs_default: Tuple[str, ...]
    status: str                           # available / unavailable（T3 缺数据）
    build: Callable[[MetricContext, dict], BuiltMetric]

@dataclass
class BuiltMetric:
    index: Sequence[Hashable]
    expr: Callable[[Hashable], LinearExpr]
    gaps: Sequence[DataGap]
```

`GLOBAL` 维度的 index 是单元素哨兵。

### 3.3 维度即类型系统

每个维度有**固定的可过滤属性词表**（D8），`scope` 的键必须落在词表内，否则保存时拒绝：

| 维度 | index 形状 | 可用 scope 属性 | 合法 reduction |
|---|---|---|---|
| `GLOBAL` | 标量 | 无（scope 必须为 `*`） | — |
| `LINE_SHIFT` | (line, shift) | `line`, `shift` | each/sum/max/min/count_violations |
| `PART_SHIFT` | (part, shift) | `part`, `stage`, `shift` | 同上 |
| `TASK` | task_id | `part`, `stage`, `line`, `order`, `shift` | 同上 |
| `ORDER` | order_id | `order`, `customer_tier`, `part` | each/sum/max/count_violations |
| `MOLD_SHIFT` | (mold, shift) | `mold`, `line`, `shift` | each/sum/max/count_violations |
| `PAIR_SHIFT` | (pair_group, shift) | `pair_group`, `part`, `shift` | each/sum/max/count_violations |

`PAIR_SHIFT` 是 D15 的直接后果：4.6.2 的配对表一旦存在，HC-08「左右件同时生产」就从「无数据源」变成可表达——但它跨两个料号，`PART_SHIFT` 的 scope 只能过滤单个 index 的属性，装不下。所以它是第 7 个维度，不是 `PART_SHIFT` 的一个变体。`pair_status == 'unpaired'` 的料号不进 index 集，对应指标出 `DataGap`（reason=`unpaired`）。

### 3.4 数据缺口是一等公民

指标缺数据时返回 `DataGap(metric_id, sheet_key, column_key, index, reason)`，**不抛异常、不返回空、不 print**。编译器收集后按 `on_data_gap` 策略处置（第 4 节分层、第 6.4 节处置）。

### 3.5 初始指标库

组织在 `src/kernel/metrics/` 下，一维度一模块：

```
src/kernel/metrics/
├── base.py         # MetricSpec / Dimension / BuiltMetric / DataGap / MetricContext
├── line_shift.py
├── part_shift.py
├── task.py
├── order.py
├── mold_shift.py
├── pair_shift.py
├── global_.py
└── registry.py     # METRICS 字典 + 保存期校验
```

**每个指标必须标注它修的是哪个已知缺陷，否则不进库。**

| 维度 | 指标 | 备注 |
|---|---|---|
| `LINE_SHIFT` | `utilization` | 取代 `capacity_utilization` 模板 |
| | `load_minutes` | |
| | `changeover_minutes` | **修 B3**：今天 `DEFAULT_CHANGEOVER_MINUTES = 0.0`（`shift_manager.py:26`），换型不占产能 |
| | `changeover_count` | |
| | `idle_minutes` | **修 B6**：今天无任何 hole 惩罚变量 |
| | `headcount_required` / `headcount_available` | HC-15 / SC-10。`headcount_required` 来自 Routing『人数』（T1）；`headcount_available` 来自 4.6.1 新表 `line_headcount`，**阶段 0 落模拟数据**（D15），绑定报告出 `simulated` |
| | `headcount_delta` | SC-10 三班平滑：跨班可用人数波动幅度，`op=minimize` |
| | `open` | 开班指示 |
| `PART_SHIFT` | `inventory` / `production` / `demand` | SC-06/SC-07 |
| | `shortfall` | **修 B8**：给 D+1 时钟正确的域上限，而非 `next_demand+1`（`scheduler.py:1916-1917`） |
| | `cumulative_inventory` | HC-06 齐套 |
| `TASK` | `lateness_shifts` | **修 B2**：域扩到整个 horizon，不再被 `due+6`（`LATENESS_SLACK_SHIFTS`，`scheduler.py:69,1160`）硬截断 |
| | `completion_shift` / `start_shift` | `start_shift` 作为 HC-09 的 `rhs` 引用目标 |
| | `blocks` / `setup_minutes` / `batch_size` | |
| `ORDER` | `lateness_shifts` / `completion_shift` | |
| `MOLD_SHIFT` | `occupancy` / `consecutive_blocks` | |
| `PAIR_SHIFT` | `pair_production_ratio` | HC-08 左右件同班次按腔数比例产出；数据来自 4.6.2 `part_pairing`，阶段 0 模拟（D15） |
| | `pair_shift_gap` | 配对件完成班次差，`op=minimize`；`pair_status='unpaired'` 的料号不进 index |
| `GLOBAL` | `total_changeover_count` / `campaign_run_count` / `total_lateness_shifts` / `total_open_shifts` | |

第一阶段约 33 个指标（原 30 + `headcount_delta` + `PAIR_SHIFT` 两个）。

**一条诚实的边界**：「同一客户订单不得跨两条线」看似可写成 `order_line_count <= 1`，但它需要为每个 (order, line) 建布尔变量——那是 `kind: structure`，不是指标。这类规则第一阶段仍走手写结构模板。

### 3.6 契约测试（每个指标强制，详见第 7.1 节）

指标注册即被契约测试覆盖。第 2 条（逐个移除 `data_dependency` 声明项必须产出 `DataGap`）与第 5 条（编译 `each + hard` 约束数 > 0）是这个设计的牙齿所在：它们把 A4 和 A7 从「靠 code review 发现」变成「构建失败」。

---

## 4. 数据契约

`on_data_gap` 的默认值不由拍板决定，而由**规范输入模板 `APS_DataSource_Template.xlsx`（15 表）实际供给什么**推导（D9）。数据缺口有三种性质完全不同的成因，必须分层。

### 4.1 T1｜契约内且模板已供给 → `fail`

缺了就是数据错误，必须终止求解并列出确切的 `(sheet_key, column_key)`。

覆盖：产线基础数据（TRP / 单班工时 / 每天班次数）、Routing-Machines（CT / 主候选线 / 人数 / 认证 / 生效失效日期）、Setup-Matrix（换模工时 / 所需技术员数）、Parameters-FG/SFN（MPQ / MOQ / 报废率 / 最大库存 / 产品族名 / 模具编号）、BOM List（装配单耗比例）、Inventory-Policy（安全库存 / 最大库存 / 目标库存天数）、Operation-LeadTime（提前准备时间 / 转运批量）、Customer Demand（日期轴）、Initial Stock、Material-Arrival-ETA、Tooling-Mold（可用套数 / 换模工时）、Customer-Priority（战略评级 / 保供加权系数 / OTD 目标）、Preventive-Maintenance（日期 / 停机工时 / 期间产能）。

### 4.2 T2｜契约内但整列为空 → `warn` + 指标标 `degraded`

模板声明了字段、业务没填。指标仍编译，但绑定报告显式列为「降级生效」。

| 字段 | 位置 | 实测 |
|---|---|---|
| 特殊班次 | Parameters-FG / SFN | **0/17、0/22 全空**；`excel_loader.py:436` 读进 dataclass 后**零消费者** |
| 同时生产 | Parameters-SFN | **0/22 全空**（FG 仅 4/17）——HC-08 左右件同步没有输入 |
| 单班定额产能 | Routing-Machines | **0/39 全空**；可由 CT×TRP×单班工时派生，建议删列 |
| PM 开始/结束时间 | Preventive-Maintenance | 全空 → 只有日期粒度，与 HC-03 班次粒度实现一致 |
| 交期浮动容差(天) | Customer-Priority | **自由文本**（`0天严格保供` / `±1天窗口协商` / `+3天或顺延次月`），无法数值化 |

### 4.3 T3｜契约外，模板根本没有这一列 → 指标注册为 `unavailable`，UI 显示「需主数据扩展」，禁止启用

这层把「缺的是模板设计」与「缺的是数据填写」分开——前者不该让求解 `fail`，因为工厂无论如何都填不出来。

**（1）班次级可用人数：完全没有数据源。** `headcount` 在 `master_schema.py` 出现 3 次——171 行、210 行（Parameters-FG/SFN，标 `deprecated=True`）、250 行（Routing，新增列）——**三处全是「需要多少人」，没有任何一处是「有多少人可用」**。所以 HC-15 班次人数红线、SC-10 三班人力平滑**在现行模板下不可能生效**。→ **已由 D15 处置**：4.6.1 新增 `line_headcount` 表并在阶段 0 落模拟数据，此项从 T3 降级为「T1 契约内 + `simulated` 来源」。

**（2）5 张主数据表在模板里没有对应 sheet**，只以 CSV 形态躺在 `data/master/`，且 Python 加载器命中数为 0：

| CSV | py 加载命中 | 对应目录规则 |
|---|---|---|
| `Baseline-Schedule.csv` | **0** | HC-12 时间围栏冻结 |
| `Calendar-Patterns.csv` | **0** | BS-01 四阶日历自适应 |
| `Customer-Orders.csv` | **0** | 订单类型 / 预测置信度 / 追产 / 试制 |
| `Setup-Technicians.csv` | **0** | HC-02 换模技术员容量 |
| `Trial-Windows.csv` | **0**（仅一句脚本散文） | HC-13 试产受控窗口 |

`Campaign-Families.csv` 有 7 处命中——但模板里已有『产品族名』列且回填 37/37，对账报告 `master_template_verification.md:352` 自己写着「数据已备，Campaign 仍读独立 CSV」。**同一份数据两个来源，这是 A2 双轨在主数据层的翻版。**

### 4.4 必须先裁决的冲突：换型时间有五个可能取值来源

`master_schema.py` 里同时存在三列换型时间：

| 行 | 列键 | 中文名 | 所在表 | 粒度 |
|---|---|---|---|---|
| 245 | `setup_min` | 换型时间 (分钟) | Routing-Machines | 料号 × 工序 |
| 272 | `setup_minutes` | 换模工时 (分钟) | Setup-Matrix | 产线 × 起始模具 × 目标模具（`required=True`） |
| 333 | `changeover_time_min` | 换模工时 (分钟) | Tooling-Mold | 模具 |

加上 `DEFAULT_CHANGEOVER_MINUTES = 0.0`（B3）和缺边回退 `else 60.0`（B4，`scheduler.py:2301`、`sequence.py:189`），同一个物理量有五个可能来源。

**裁决：优先级表**

> Setup-Matrix（SDST，最精确）→ 缺边则 Tooling-Mold『换模工时』→ 再缺则 Routing『换型时间』→ 三者皆缺 = `DataGap`（T1，`fail`）。
>
> **废除 `0.0` 默认与 `60.0` 回退。**

### 4.5 `available_qty` 的处置

改为真实读取的指标参数，**默认值 1**，并把 HC-01 那条业务裁决（「一套物理模具只能在一台机器上跑」）记为规则集里的显式覆盖——业务改主意时改配置，不改代码。

### 4.6 模板扩展清单

按优先级。第 1、7 项原本是硬阻塞（HC-15 / SC-10 / HC-08 三条目录规则没有数据源，无论指标层设计得多好都无法从 PARTIAL 变 REAL）——**已按 D15 裁决改为「阶段 0 内先落显式模拟数据」**，规格见 4.6.1 / 4.6.2。其余项仍需业务供数或业务确认。

| # | 新增 | 位置 | 解锁 | 阶段 0 处理 |
|---|---|---|---|---|
| 1 | **班次可用人数**（产线 × 日期 × 班次） | 新 sheet `line_headcount` | HC-15、SC-10 | **落模拟数据**（4.6.1） |
| 2 | **换模技术员班次可用时段** | 新 sheet（吸收 `Setup-Technicians.csv`） | HC-02 | 不动，登记 `unenforced` |
| 3 | **基线/冻结状态**（工单 × 冻结标识 × 冻结时刻） | 新 sheet（吸收 `Baseline-Schedule.csv`） | HC-12 真实冻结，而非当前等式近似 | 不动 |
| 4 | **试产受控窗口**（料号 × 窗口起止 × 是否独占产线） | 新 sheet（吸收 `Trial-Windows.csv`） | HC-13 | 不动 |
| 5 | **订单属性**（订单类型 / 预测置信度 / 追产标识） | Customer Demand 增列 | BS-03 超载仲裁、`scope.order` 过滤 | 不动 |
| 6 | **交期浮动容差数值化**（0/1/3 天 + 备注列分离） | Customer-Priority 改列 | 对账报告建议 #9 | 不动 |
| 7 | **L/R 配对表**（料号 × 配对料号 × 腔数比例） | 新 sheet `part_pairing` | HC-08 | **落模拟数据**（4.6.2） |
| 8 | 删除 `单班定额产能`、Parameters-FG 的 `单班工时/每天班次数` | — | 消除与派生值/产线基础数据的重复维护 | 阶段 0 执行（纯删列，无数据风险） |
| 9 | Campaign 改读模板『产品族名』，废弃独立 CSV | — | 消除主数据层双轨 | 阶段 0 执行 |

#### 4.6.1 新 sheet：`line_headcount`｜班次可用人数

`SheetSpec` 规格（阶段 0 直接照此写入 `src/master_schema.py`，并追加到 `SHEETS` 元组末尾）：

```python
LINE_HEADCOUNT = SheetSpec(
    key="line_headcount",
    title="班次可用人数 Line-Shift-Headcount",
    legacy_titles=("Line-Shift-Headcount", "Shift-Headcount"),
    required=False,          # 缺 sheet 时 HC-15/SC-10 出 DataGap，不 fail 整个求解
    primary_key=("site", "line", "work_date", "shift_no"),
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("line", "产线", ("Line",), required=True,
               foreign_key=("line_base", "line")),
        Column("work_date", "日期", ("Date",), required=True, kind="date"),
        Column("shift_no", "班次序号", ("Shift", "班次"), required=True, kind="int",
               check="between(1, 3)"),
        Column("available_headcount", "可用人数", ("Available Headcount",),
               required=True, kind="int", check=">=0", contract_tier="t1_required"),
        Column("source", "数据来源", ("Source",), kind="text",
               check="in('actual','simulated')", note="simulated 行在绑定报告出 simulated 状态"),
    ),
    description="HC-15 班次人数红线 / SC-10 三班人力平滑的唯一数据源",
)
```

**模拟数据生成规则**（写入种子脚本，见 5.8；必须确定性、可从现有模板派生，不得凭空发明）：

> 对 `line_base` 的每条产线 × 需求日期轴的每个日期 × `1..shifts_per_day` 的每个班次生成一行：
> `available_headcount = max(1, floor(routing_headcount(line) × factor(shift_no)))`，
> 其中 `routing_headcount(line)` 取 Routing『人数』该产线的值（缺行时按既有回退），`factor = {1: 1.0, 2: 1.0, 3: 0.8}`。
> `source` 一律填 `'simulated'`。
>
> **日期轴必须用 `excel_loader._demand_dates(ws, _demand_header_row(ws))` 取，按列序原样保留（不要 `sorted()`——`"M/D"` 字符串跨月排序是错的）。** 不能用 `read_master_rows(...)["demand"]` 的键：`read_master_rows`（`:645`）只遍历 `spec.columns`，**不处理 `date_axis=True`**，所以 demand 行的键只有 `site`/`part_no`/`description`，日期列全被丢掉。这条也记进 5.9 事实 2。

按当前模板实测：产线 3 条（`PGLINKAGE`、`轴套6`、`轴套7`），Routing 人数 `PGLINKAGE=3`、`轴套6=2`、`轴套7` 缺行回退，`shifts_per_day=3`。

**日期轴取『需求表头声明的全部日期列』，不是『有需求量的日期列』。** 实测模板 `Customer Demand` 表头有 **33** 个日期列（`10/13`–`11/15`），其中只有 25 列填了量。人数可用性是产能事实，与当天有没有订单无关；只覆盖有量的 25 天，会让计划员一补 `10/18` 的需求就立刻缺人数行。所以 → 3 × 33 × 3 = **297 行**。

> ⚠️ 本节 rev.2 曾写「日期轴 10 天 → 90 行」。那是把黄金基线（`wl_10d_baseline.json`）的 10 天窗口误当成了模板的日期轴。rev.3 更正为 297 行。基线窗口是**求解范围**，模板日期轴是**数据范围**，两者不是一回事。

#### 4.6.2 新 sheet：`part_pairing`｜L/R 配对表

```python
PART_PAIRING = SheetSpec(
    key="part_pairing",
    title="左右件配对表 Part-Pairing",
    legacy_titles=("Part-Pairing", "LR-Pairing"),
    required=False,
    primary_key=("site", "part_no"),
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("part_no", "料号 (PN)", ("PN", "Part No."), required=True,
               foreign_key=("parameters_fg", "part_no")),
        Column("paired_part_no", "配对料号 (PN)", ("Paired PN",),
               foreign_key=("parameters_fg", "part_no")),
        Column("pair_group", "配对组", ("Pair Group",), kind="text",
               note="同组多件成套；留空表示两两配对"),
        Column("cavity_ratio", "腔数比例", ("Cavity Ratio",), kind="number",
               check=">0", note="本件:配对件，1.0 表示等量"),
        Column("pair_status", "配对状态", ("Pair Status",), kind="text",
               check="in('paired','unpaired')", required=True),
        Column("source", "数据来源", ("Source",), kind="text",
               check="in('actual','simulated')"),
    ),
    description="HC-08 左右件同时生产的配对关系；模板原有『同时生产 Y/N』只表达意图，不表达和谁配",
)
```

**模拟数据生成规则**：

> **行集合**：一个料号有行，当且仅当它满足下面两条之一——(a) 能按（型号 token, L/R 侧）在 `parameters_fg` 里配出对手件；(b) 业务在 Parameters-FG 标了『同时生产 = Y』。
> **配对**：扫描『品名描述』，按 `LHD`/`RHD`/`左驾`/`右驾` 定侧，按型号 token（正则 `[A-Z]{1,3}[- ]?\d+[A-Z]?`）归并同型号左右件；配上的写 `pair_status='paired'` + `cavity_ratio = 1.0`，`source = 'simulated'`。
> 配不上的（含标了『同时生产 = Y』但描述里没有可识别对手件的）**一律写 `pair_status = 'unpaired'`、`paired_part_no` 留空，不得编造。**
>
> 为什么必须是 (a) ∪ (b) 而不是单用其一：实测两条规则选出的是**不同的料号集合**，交集只有 `WB80510`。单用关键词得 1 paired + 3 unpaired = 4 行（`WB35356`/`WD48696`/`WD59238` 描述里没有任何 L/R 字样）；单用 Y 标记得 0 paired。只有并集才同时满足「1 对可辩护」与「4 个 Y 料号落 unpaired」。
>
> 代价：`WA14208S`（吉利DX11左驾，备件）与 `WD48696S`（领克L743左驾，备件）描述里有『左驾』但既无对手件也无 Y 标记，**不出行**。依据是 HC-08 只对「声明要同时生产」或「真配成对」的料号有意义，配不上对的备件行不携带信息。业务若要登记备件，改判据 (b) 即可。

按当前模板实测（17 条 Parameters-FG），产出 **5 行**：1 paired（`WCA1894` Chery KP31 LHD 传动杆 ↔ `WCB8574` Chery KP31 RHD 传动杆）+ 4 unpaired（`WB35356`、`WB80510`、`WD48696`、`WD59238`，即全部『同时生产 = Y』料号）。其中 `WB80510` 是「CX11 LHD」，其 RHD 版本根本不在 FG 参数表里；另外 3 个描述里没有任何 L/R 字样。**这 4 个必须落 `unpaired`**，HC-08 对它们保持 `degraded`，这是对真实数据缺口的如实登记，不是种子脚本的失败。

#### 4.6.3 provenance：模拟数据必须可见

D15 的前提是「显式、可见、可追溯」，否则就退化成 5.6 节地雷 2 那种编造默认值。三条硬性要求：

1. **只经种子脚本写入模板/DB，绝不在 loader 里补。** `excel_loader.py` 与 `data_loader.py` 遇缺一律产 `DataGap`（5.6）。
2. **每张新表带 `source` 列**，取值 `actual` / `simulated`；种子脚本只写 `simulated`。业务日后填真实数据时改这一列即可，代码无需变更。
3. **绑定报告出 `simulated` 状态**（7.2 节状态集从 7 个扩到 8 个）：规则确实编译并参与了求解，但依据的是模拟数据——UI 必须视觉区分，防止「看起来生效了」被误读为「业务数据已就绪」。

---

## 5. 数据层模块化

### 5.1 现状：五套并存的持久化机制

| 机制 | 位置 | 问题 |
|---|---|---|
| SQLite | `data/rule_sets.sqlite3` | **仅规则集**；主数据不在内 |
| xlsx | 签入的模板 + `data/uploads/current_{site}.xlsx` | 上传后是事实真源，但不可增量编辑 |
| CSV | `data/*.csv`、`data/master/*.csv` | 与 xlsx 双轨，`routing_from_rows(...) or load_routing(csv)` 这种回退散布在 `server.py:1979/2157/2168` |
| JSON 快照缓存 | `data/snapshot_{site}.json` | **写了没人读**（`load_snapshot_json` 在 server 里无调用者）；且内含绝对路径，任何人跑一次测试就产生 diff |
| 内存字典 | `session_state`（`server.py:347-362`）、`site_settings_store`（`:240`） | 全局可变单例，~20 处写入，**零锁** |

**当前编辑会在重启时静默丢失。** `/api/inputs/update`（`server.py:1146-1249`）只改内存快照 + 写 JSON 缓存，而缓存无人读回。这不是设计取舍，是数据丢失 bug。

**并发风险不是理论上的。** 所有端点都是同步 `def`，FastAPI 丢进线程池，两个请求真的在并发改同一个字典。最尖锐的一例：`GET /api/inputs?site=SY38`（`server.py:919-925`）会**切换全局站点并清空所有人的 result/matrix/checks**。求解 90-300 秒期间有人上传工作簿，`session_state["snapshot"]` 被替换（`:1386`），而跑完的求解把 `result` 写回（`:2486`），对应的是已不存在的数据。今天唯一的隔离是 `server.py:1865` 那一次 deep copy。

**可编辑实体只有 6 类**（orders、initial_stock、tooling_molds、pm_schedules、material_arrivals、customer_tiers），且全部挤在一个 `POST /api/inputs/update` 里，没有任何 PUT/PATCH。fg/sf params、BOM、routing、setup_matrix、inventory_policy、leadtime、line_base **不可编辑**。

### 5.2 三层改造

```
第 1 层  Schema（声明式，唯一真源）
         src/master_schema.py  ← 扩展 PK / 唯一性 / 跨表外键 / 取值约束 / contract_tier
              │  生成 →  DB DDL、API Pydantic 模型、UI 表单定义、Excel 表头匹配、校验规则
              ▼
第 2 层  Repository（持久化无关的端口）
         MasterDataRepository  → ExcelRepository / SqliteRepository / (将来 PostgresRepository)
         SnapshotStore         → 不可变版本化快照，draft → published
              ▼
第 3 层  Assembly（契约不变）
         build_requirement_plan(snapshot) -> RequirementPlan
              ▼
         scheduler / validator / prechecker / balance_builder / audit / exporter
```

**第 3 层完全不动**——这是本次改造最重要的约束。`RequirementPlan` 与 `ProductionInputSnapshot` 是稳定契约，消费者有 `scheduler.py:311`、`validator.py:43`、`prechecker.py:50`、`balance_builder.py:58`、`audit/engine.py:534`、`exporter.py:59`、`services/manual_audit.py:79`，以及约 15 个直接构造它们的测试文件。DB 化对它们必须完全透明。

**已有的可复用资产**（不要重写）：

- `master_schema.py`（591 行）：15 个 `SheetSpec`，每列已声明 `Column(key, header, aliases, required, kind, note, deprecated)`；已有表名归一化（`norm :34-47`）、别名解析（`column_positions :485-495`）、日期轴表头定位（`header_row_for :462-471`）、工作簿校验（`validate_workbook :531-564`）、布局识别（`detect_layout :503-513`）。**缺的只是主键/唯一性/跨表外键/取值约束。**
- `read_master_rows(excel_path, site) -> Dict[sheet_key, List[dict]]`（`excel_loader.py:645`，kind 强转在 `:669-680`）：产出的正是数据库行的形状。

**第 1 层的关键收益**：schema 一处声明，DDL / API 模型 / UI 表单 / 校验全部派生。今天这四处各写一遍，所以才会出现『可用套数』那种「模板有列、加载了、API 可编辑、编译器写死 1」的五头不一致。

### 5.3 与指标层的接合点

> **`MetricSpec.data_dependency` 必须引用 `master_schema` 的列键 `(sheet_key, column_key)`，而不是自由文本文件名。**

于是：

- **静态**：契约测试可直接校验每个声明的依赖在 schema 里存在——写错列名 CI 就失败
- **运行时**：Repository 是唯一知道哪些列为 null/缺失的地方，`DataGap` 由它产出，指标层只消费
- **T1/T2/T3 分层落在 schema 上**：给 `Column` 加 `contract_tier` 字段（`t1_required / t2_declared_empty / t3_absent`），第 4 节那三层策略从「文档约定」变成「schema 里可执行的声明」。T3 的字段在 schema 里根本不存在，所以依赖它的指标注册即报 `unavailable`

### 5.4 编辑模型：draft → published

照搬规则集已验证的版本化模式，但修掉它的缺陷。主数据编辑写入 draft，**求解永远读一个已发布的不可变快照**（由 `snapshot_id` 标识）。这让「边求解边编辑」在结构上安全，而不是靠深拷贝碰运气。

`rule_sets/store.py` 需修的缺陷（新模式会复用其骨架）：

| 缺陷 | 位置 | 修法 |
|---|---|---|
| 每次调用新建连接 + `executescript(SCHEMA)` | `store.py:36-42` | 连接池 / 单例 + 启动时迁移一次 |
| `with conn:` 只提交不关闭，连接泄漏到 GC | 全模块 | `contextlib.closing` 或池化 |
| 无 WAL | — | `PRAGMA journal_mode=WAL` |
| 读最新版本号再插入 = 竞态窗口 | `store.py:127-136` | 唯一约束兜底 + 重试，或序列号 |
| `status` 无 CHECK 约束、无状态机 | DDL `:18-33` | 加 CHECK + 显式转换函数 |

### 5.5 存储引擎可移植性约束（D10）

开发阶段 SQLite、后续可能 Postgres，因此：

1. **主数据用关系列，不用 JSON payload。** `rule_sets` 那种不透明 JSON blob 适合整份规则集，但主数据要逐条编辑、要外键约束、要按列查询——DDL 由 `master_schema` 生成真实关系列，生成器**方言感知**（SQLite / Postgres 各一个 emitter）。
2. **只用两者共同的 SQL 子集。** 禁 `INSERT OR REPLACE`、禁 SQLite 动态类型怪癖、禁 `AUTOINCREMENT` 语义差异；主键统一用应用层生成的代理键。
3. **迁移机制必须是真的。** 今天 `store.py:41` 每次连接都 `executescript(CREATE TABLE IF NOT EXISTS)`——SQLite 单文件下能混过去，Postgres 下会炸。需要带版本号的迁移脚本 + 启动时执行一次。
4. **连接与事务抽象成端口。** SQLite 实现用 WAL + 单写者串行化，Postgres 实现用池；上层不知道差别。
5. **`payload_hash` 幂等模式保留**（`store.py:50-52`）——两种引擎都成立，且规则集已验证过。

### 5.6 必须先拆的两颗地雷

DB 化会把这两个从「难看」变成「错误数据永久固化」：

**（1）硬编码年份 2026。** `excel_loader.py:511` 把日期解析成 `datetime(2026, month, day, ...)`；`data_loader.py:455,462`、`server.py:1163-1172` 同样。Excel 日期列常只有月/日，靠硬编码年份补齐——入库前必须改成显式年度上下文参数。

**（2）编造的默认值。** `excel_loader.py` 约 15 处：`:301-303` TRP=0.71/hours=8/shifts=3、`:337-342` available_qty=1/changeover=30/max_life=500000/maintenance=50000、`:393-398` hours=8/12 & shifts=3/2 按站点、`:408` moq→mpq、`:489` due_hour 硬编码 15/20、`:525` downtime=8.0、`:529-537` 用 downtime 反推 PM 结束日期与班次、`:538` 编造 `PM-{site}-{line}-{date}` 工单号、`:551,553,555` capacity_rate=0 等、`:572` 编造 `PO-{part}-{row}` 采购单号、`:580,582` qc_release=2.0/status="质检合格"、`:599-604` tier="TIER_A"/weight=3/otd=98.0、`:462` BOM ratio→1.0。`data_loader.py` 对应位置：`:329,367` TRP、`:330,368` MPQ、`:332-333` hours/shifts、`:496-501` tooling、`:527-531` PM、`:555-558` arrival、`:579-583` tier、`:406` BOM ratio。

**这些今天每次加载都重新编造，入库后就变成「看起来是真实数据」的永久记录。** 必须全部改为 `DataGap`。

### 5.7 Excel 的新定位

不再是真源，而是**导入/导出适配器**：`read_master_rows` 变成 `ExcelRepository` 的导入路径，`master_export.export_site_workbook`（`master_export.py:160-211`）变成导出路径，两者都只对 Repository 说话。业务仍可用 xlsx 批量导入、导出审阅，但增量编辑走网页端。

### 5.8 Schema 扩展：精确字段定义

阶段 0 对 `src/master_schema.py` 的改动**只有下面这些**，不多不少。现有 `Column`（`:50-63`）与 `SheetSpec`（`:66-83`）都是 `frozen dataclass`，新增字段全部带默认值，因此对 15 张既有表的声明**零改动即可继续工作**。

```python
@dataclass(frozen=True)
class Column:
    key: str
    header: str
    aliases: Tuple[str, ...] = ()
    required: bool = False
    kind: str = "text"                 # text|number|int|date|datetime|bool
    note: str = ""
    deprecated: bool = False
    # --- 新增（阶段 0）---
    primary_key: bool = False          # 参与自然主键
    foreign_key: Optional[Tuple[str, str]] = None   # (sheet_key, column_key)
    check: str = ""                    # 取值约束表达式，见下表
    contract_tier: str = "t1_required" # t1_required | t2_declared_empty | t3_absent
    editable: bool = True              # False = UI 只读（派生列/废弃列）

@dataclass(frozen=True)
class SheetSpec:
    key: str
    title: str
    columns: Tuple[Column, ...]
    legacy_titles: Tuple[str, ...] = ()
    required: bool = True
    date_axis: bool = False
    description: str = ""
    # --- 新增（阶段 0）---
    primary_key: Tuple[str, ...] = ()  # 显式自然主键；为空时由 Column.primary_key 推导
    surrogate_key: str = "row_id"      # 编辑寻址用的代理键列名
```

`check` 只支持四种形态，**不做表达式解析器**——保存期用一张分派表判定即可，避免引入一个小型语言：

| 形态 | 例 | 语义 |
|---|---|---|
| `>=N` / `<=N` / `>N` / `<N` | `>=0` | 数值边界 |
| `between(a, b)` | `between(1, 3)` | 闭区间 |
| `in('x','y')` | `in('actual','simulated')` | 枚举 |
| `nonempty` | — | 去空白后长度 > 0 |

`primary_key` 与 `surrogate_key` 的分工必须说清楚，否则实现时会二选一并踩坑：

> **自然主键负责身份与外键**（`(site, part_no)` 这种，业务可读、跨表引用）；**代理键 `row_id` 负责编辑寻址**（UI 的 `PATCH /{row_id}`）。分开的原因：改一个自然主键分量（例如修正料号笔误）在 UI 上必须表现为「删旧行 + 插新行」，而代理键让前端持有的行句柄在整个编辑会话里保持稳定。`row_id` 由应用层生成（`sha1(site|sheet_key|自然主键|首次写入时间)` 的前 16 位），两种引擎语义一致，不依赖 `AUTOINCREMENT`。

### 5.9 实体登记表（DDL 生成器与 UI 表单的共同输入）

16 张输入表（现行 14 + 4.6 新增 2）。`可编辑` 列是阶段 4 的目标态——**今天只有 6 类可编辑，其余全是只读**（5.1 节）。

| sheet_key | 表名 | 自然主键 | 跨表外键 | 今天可编辑 |
|---|---|---|---|---|
| `demand` | `master_demand` | (site, part_no, demand_date) | — | ✅ |
| `stock` | `master_initial_stock` | (site, part_no) | — | ✅ |
| `material_arrival` | `master_material_arrival` | (site, supplier, part_no, eta_date) | part_no → parameters_sfn | ✅ |
| `parameters_fg` | `master_parameters_fg` | (site, part_no) | — | ❌ |
| `parameters_sfn` | `master_parameters_sfn` | (site, part_no) | — | ❌ |
| `bom` | `master_bom` | (site, fg_part_no, sf_part_no) | fg → parameters_fg；sf → parameters_sfn | ❌ |
| `routing` | `master_routing` | (site, part_no, stage, primary_line) | primary_line → line_base | ❌ |
| `setup_matrix` | `master_setup_matrix` | (site, line, from_mold, to_mold) | line → line_base | ❌ |
| `inventory_policy` | `master_inventory_policy` | (site, part_no, period_type) | — | ❌ |
| `operation_leadtime` | `master_operation_leadtime` | (site, part_no, stage) | — | ❌ |
| `tooling` | `master_tooling` | (site, mold_id) | — | ✅ |
| `line_base` | `master_line_base` | (line) | — | ❌ |
| `pm` | `master_pm` | (site, line, pm_item, start_date) | line → line_base | ✅ |
| `customer` | `master_customer_priority` | (site, customer_code) | — | ✅ |
| `line_headcount` | `master_line_headcount` | (site, line, work_date, shift_no) | line → line_base | ❌（新表） |
| `part_pairing` | `master_part_pairing` | (site, part_no) | part_no/paired_part_no → parameters_fg | ❌（新表） |

**两个实现前必须知道的事实：**

1. `line_base` 是唯一**没有 `site` 列**的表（实测 4 列：`line / trp / hours_per_shift / shifts_per_day`，3 行数据）。它被当成全站点共享，但 `master_line_base` 的主键只有 `(line)`——多站点入库后同名产线会互相覆盖。阶段 4 必须裁决：加 `site` 列（推荐，与其余 15 表一致），还是显式声明为全局表并在 DDL 里单独处理。
2. `demand` 是**日期轴宽表**（`date_axis=True`，3 个固定列 + 动态日期列）。入库必须先 unpivot 成 `(site, part_no, demand_date, quantity)` 长表；导出时再 pivot 回宽表。定位分两步：`header_row_for`（`master_schema.py:462-471`）只负责找**表头所在行**，动态**日期列**由 `excel_loader._demand_dates`（`:200`）给出（返回 `[(列号, "M/D")]`，按列序即时间序）。unpivot/pivot 是新增的一对纯函数，**不要塞进 Repository**。

   ⚠️ **`read_master_rows`（`excel_loader.py:645`）读不到日期轴。** 它只遍历 `spec.columns`，而 `DEMAND.columns` 只有 `site`/`part_no`/`description`，所以返回的 demand 行键里**没有任何日期**。任何需要日期轴的代码（种子脚本、unpivot、阶段 2 的指标）都必须自己走 `_demand_header_row` + `_demand_dates`，不能图省事用 `read_master_rows`。同样地，`"M/D"` 字符串**不得 `sorted()`**——轴跨月时 `"11/10" < "11/2"`。
3. 外键列出的 8 个 BOM 子件料号不在 `parameters_sfn` 中（对账报告 `master_template_verification.md` P2 项）。**外键约束一旦在 DDL 里生效，这批数据会直接插入失败。** 所以迁移顺序必须是：先清数据、后开约束，或者第一批 DDL 用 `NOT ENFORCED` 声明外键、只在校验层报 `warn`，等数据干净再转 `ENFORCED`。

### 5.10 Repository 端口与 SQLite 实现

```python
@dataclass(frozen=True)
class UpsertResult:
    inserted: int
    updated: int
    rejected: Sequence[Tuple[str, str]]   # (row_id, reason)

@dataclass(frozen=True)
class ImportReport:
    sheet_key: str
    read: int
    written: int
    gaps: Sequence[DataGap]
    errors: Sequence[str]

class MasterDataRepository(Protocol):
    def list_rows(self, sheet_key: str, *, site: str,
                  filters: Optional[Mapping[str, Any]] = None) -> Sequence[dict]: ...
    def get_row(self, sheet_key: str, row_id: str) -> Optional[dict]: ...
    def upsert_rows(self, sheet_key: str, rows: Sequence[dict], *,
                    draft_id: str, actor: str) -> UpsertResult: ...
    def delete_rows(self, sheet_key: str, row_ids: Sequence[str], *,
                    draft_id: str, actor: str) -> int: ...
    def gaps(self, sheet_key: Optional[str] = None) -> Sequence[DataGap]: ...
    def import_workbook(self, path: str, *, site: str, draft_id: str,
                        mode: str = "replace") -> Sequence[ImportReport]: ...
    def export_workbook(self, path: str, *, site: str, snapshot_id: str) -> None: ...

class SnapshotStore(Protocol):
    def create_draft(self, site: str, *, base_snapshot: Optional[str] = None) -> str: ...
    def publish(self, draft_id: str, *, note: str = "", as_of_date: str = "") -> str: ...
    def load(self, snapshot_id: str) -> ProductionInputSnapshot: ...
    def list_versions(self, site: str) -> Sequence[dict]: ...
```

三条实现约束：

- **`load()` 返回的 `ProductionInputSnapshot` 是阶段 3 之前的同一契约**（5.2 节第 3 层不动）。`scheduler.py:311`、`validator.py:43`、`prechecker.py:50`、`balance_builder.py:58`、`audit/engine.py:534`、`exporter.py:59`、`services/manual_audit.py:79` 与约 15 个测试文件都不许改。
- **`site` 永远是显式参数**，不从任何全局读。这是消灭 `session_state`（D14 排在阶段 4）的接口前提：先把签名改对，后面拆全局才只是体力活。
- **`gaps()` 是 `DataGap` 的唯一生产者。** 指标层只消费（5.3）。

DDL 方言映射（生成器按此表出两份 emitter）：

| `kind` | SQLite | Postgres | Python 边界类型 |
|---|---|---|---|
| `text` | `TEXT` | `TEXT` | `str` |
| `number` | `REAL` | `DOUBLE PRECISION` | `float` |
| `int` | `INTEGER` | `INTEGER` | `int` |
| `date` | `TEXT`（ISO `YYYY-MM-DD`） | `DATE` | `str`（ISO） |
| `datetime` | `TEXT`（ISO sep=`T`） | `TIMESTAMP` | `str`（ISO） |
| `bool` | `INTEGER` + `CHECK(col IN (0,1))` | `BOOLEAN` | `bool` |

> **`date`/`datetime` 在 SQLite 存 TEXT、在 Postgres 存原生类型——差异必须在 Repository 内部吸收，Python 边界统一是 ISO 字符串。** 现有代码已经在这么做了（`read_master_rows` 的 `excel_loader.py:676-678` 把 `datetime`/`date` 转 `isoformat`），照抄这个约定即可。

迁移机制：`src/db/migrations/` 下 `NNN_描述.sql` 顺序执行，`schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)` 记版本，**启动时执行一次**，取代 `store.py:41` 每次连接都 `executescript(CREATE TABLE IF NOT EXISTS)` 的做法。SQLite 与 Postgres 各一份 SQL（同一版本号两个文件：`001_init.sqlite.sql` / `001_init.pg.sql`），由方言感知生成器产出、人工审校后签入。

### 5.11 API 目标形态

| 现状 | 目标 | 理由 |
|---|---|---|
| `POST /api/inputs/update`：一个巨型 payload 塞 6 类实体，全 `Optional` | `PATCH /api/master/{sheet_key}/{row_id}` + `POST /api/master/{sheet_key}:batch` | 16 类实体不能继续挤一个端点；`Optional` 全开导致「传了 null 到底是清空还是不改」无法区分 |
| `GET /api/inputs?site=X`：**切换全局站点并清空所有人的 result/matrix/checks**（`server.py:919-925`） | `GET /api/master/{sheet_key}?site=X&draft_id=...`，站点仅请求级 | 修掉 5.1 节那个并发事故 |
| 无 | `GET /api/schema/{sheet_key}` → 由 `master_schema` 派生的表单定义（列、kind、required、check、editable、外键候选值） | 前端不再硬编码 16 张表的字段，schema 改一处 UI 自动跟 |
| `POST /api/solve` 读 `session_state` | `POST /api/solve {snapshot_id, rule_set_version, as_of_date}` | D13 确定性：三个输入全部显式 |
| 无 | `POST /api/snapshots/drafts`、`POST /api/snapshots/{draft_id}:publish` | 5.4 draft → published |
| 无 | `GET /api/rule-sets/{id}/binding-report` | 7.3 |

`GET /api/schema/{sheet_key}` 是**投入产出比最高的一个端点**：它让「新增一张表」的成本从「改 schema + 改 API 模型 + 改前端表单 + 改校验」四处降到「只改 schema」一处，正是 5.2 节第 1 层收益的兑现点。

### 5.12 种子与模拟数据脚本（D15 的落地物）

`scripts/seed_simulated_masterdata.py`，与既有 3 个可复用脚本同级：

```
用法：
  .venv/Scripts/python.exe scripts/seed_simulated_masterdata.py --check   # 只报告将写什么，不落盘
  ...                                                          --force    # 真正写入模板/DB
```

硬性要求：

1. **默认拒绝执行**，不带 `--force` 只打印 diff——沿用被删的 `scripts/fill_simulated_masterdata.py` 的既有约定（可从 `git show 3b47ceb:scripts/fill_simulated_masterdata.py` 取回作参考实现）。
2. **确定性**：不读时钟、不用随机数、不依赖字典迭代序。同一输入两次运行产出字节相同的 xlsx。
3. **只写 `source='simulated'` 的行**，且只写 4.6.1 / 4.6.2 两张新表；**绝不覆盖业务已填的 `source='actual'` 行**。
4. **派生而非发明**：每个模拟值必须能说出它从哪一列推出来（4.6.1 从 Routing『人数』× 班次系数；4.6.2 从『品名描述』的 LHD/RHD 关键词）。推不出来的写 `pair_status='unpaired'`，不编造。
5. 运行后自动重跑 `scripts/verify_master_template.py`，把结果差异写进 `Specification/master_template_verification.md`，并在 `Specification/masterdata_simulation_notes.md` 追加一条记录。

---

## 6. 编译器

### 6.1 一个通用函数，不是 20 个分派分支

`_DISPATCH`（`compiler.py:764-784`）对 `kind: metric` 的规则**消失**。编译器主循环八步：

```
resolve(作用域+继承) → validate(保存期) → order(结构先于指标)
  → spec.build(ctx, params) → 按 scope 过滤 index
  → 套用 reduction × op → 登记 BindingPlan → 汇入 PenaltyBook
```

只有 `kind: structure` 保留各自的构建器，且它们**先跑**——因为它们创建决策变量，指标随后可以引用。这就是 D2 的第二类接入点：将来加「同一订单不得跨线」，是加一个 structure builder + 若干读它变量的指标，编译器主循环不改。

编译顺序按 `(group.priority, rule.id)` 排序，不依赖字典插入序（D13）。

### 6.2 reduction × op × nature 的完整语义

穷举、无例外：

| reduction | op | hard | soft |
|---|---|---|---|
| `each` | `>=` | `Add(expr[i] >= rhs)` ∀i | `slack[i]≥0`；`Add(expr[i]+slack[i] >= rhs)`；罚 `w×Σslack` |
| `each` | `<=` | `Add(expr[i] <= rhs)` ∀i | `Add(expr[i]-slack[i] <= rhs)`；罚 `w×Σslack` |
| `each` | `==` | `Add(expr[i] == rhs)` ∀i | 双侧松弛 |
| `each` | `between` | 两条 Add | 两条松弛 |
| `sum` | `>=` `<=` `==` | `Add(Σexpr ⋛ rhs)` | 单条松弛 |
| `max` | `<=` | `m≥expr[i]` ∀i，`Add(m <= rhs)` | m 上挂松弛 |
| `min` | `>=` | 对称 | 对称 |
| `count_violations` | `<= k` | `bool[i]` 半连续化违约判定，`Add(Σbool <= k)` | 罚 `w×Σbool` |
| — | `minimize` | **无 rhs**，目标项 `+w×reduction(expr)` | `nature` 非法，保存期拒绝（D11） |
| — | `maximize` | 目标项 `-w×reduction(expr)` | 同上 |

`rhs` 为指标引用时（2.4 节），两侧都先 build 再逐 index 对齐；index 形状不兼容则保存期拒绝。

### 6.3 三条硬规则

**（1）`nature` 是硬/软的唯一真源。** 没有 `force_hard`。`scheduler.py` 里那 6 处 `_compile_rules_for(..., force_hard=True)`（1319/1383/1403/1428/1452/1459）随 legacy 路径一起消失。

**（2）目标项只有 PenaltyBook 一条入口（D12）。** 任何规则不得直接往 `model.Minimize` 加项。这是 B1 的修法——今天 `_lateness_weight()`（`scheduler.py:1633-1640`，基于 `_violation_weight()` `:1629-1631`）在 3 线 × 30 班 × 8 小时班的场景下取值 `3 × 30 × (30 × 28_800_000 + 1) + 1 ≈ 7.8×10^10`，与 CHANGEOVER 3×10^5、JITTER 5×10^5、CAMPAIGN 6×10^4、D1=100、inventory=50、lead=10、以及权重 1 的完成班次项混在同一个 `model.Minimize(sum(all_terms))` 里（`:1626`）。用 10^10 量级差手工模拟字典序会摧毁 LP 松弛界与搜索引导。那座手工权重塔必须删掉，改为 PenaltyBook（`compiler.py:70-111`）的分组字典序（`kernel/solve.py:63` 已有实现，今天只在特定条件下走）。另外 `objective_value=sum(bests.values())`（`:1145`）把异构量相加，数值无意义，需一并改为分层报告。

**（3）确定性（D13）。** tier 预算从**壁钟**切分（`scheduler.py:677,714,747`）换成 CP-SAT 的 `max_deterministic_time`——壁钟切分让计划质量随机器速度变化。`scope.py:22` 的 `date.today()` 改成求解请求显式传入的 `as_of_date`，并随快照持久化，否则同一输入不同日期出不同计划，绑定报告也不可复现。求解器已固定 `num_workers=1, random_seed=1`（`scheduler.py:1068-1069`），保留。

### 6.4 DataGap 在编译期的处置

`BuiltMetric.gaps` 非空时，按优先级取值：**规则级 `on_data_gap` → 规则集级 → schema 里该列的 `contract_tier` 默认**（T1=`fail`、T2=`warn`、T3 的指标根本注册不了）。

- `fail` → 抛 `DataGapError`，消息里列出确切的 `(sheet_key, column_key)` 与受影响索引
- `warn` → 照常编译，BindingPlan 标 `degraded`
- `skip` → 不编译，标 `skipped_data_gap`

三者都进绑定报告，**没有静默路径**。

### 6.5 让 `requires` 不说谎

`MetricSpec.requires` 的声明本身也会漂移——那就是 A5 重演。所以契约测试用一个**记录型代理**包住 ctx：构建指标后断言「实际访问的成员集合 ⊆ 声明的 `requires`」。越界即 CI 失败。

这样 `requires` 是可执行的，不是文档。同理 `CompileContext` 今天那 34 个方法（`compiler.py:21-59`）不必真的拆成多个窄接口——有了这个检查，单个上下文对象也不会被某个指标偷偷全量依赖。

---

## 7. 生效保证

### 7.1 静态契约（CI 强制，每个指标/结构模板都要过）

| # | 检查 | 杀掉的缺陷 |
|---|---|---|
| 1 | 全数据 ToyContext 上编译 `each + hard` → 约束数 > 0 | **A7**（登记未编译） |
| 2 | 逐个移除 `data_dependency` 声明项 → 必产出 `DataGap`，不抛异常、不返回空 | **A4**（静默降级） |
| 3 | 记录型代理断言「实际访问成员 ⊆ 声明的 `requires`」 | **A5**（声明说谎） |
| 4 | 声明的 `dimension` 与 `build` 返回的 index 形状一致 | 维度类型系统 |
| 5 | `scope` 属性词表 ⊆ 维度词表 | 拼错的过滤条件 |
| 6 | `rhs` 落在 `domain` 内 | 比率指标填 800 |
| 7 | **目录覆盖**：32 条 HC/SC/BS 每一条要么被某个已注册指标/结构的 `catalog_refs` 覆盖，要么进 `unenforced` 登记表并附理由 | **目录说谎** |
| 8 | **反向孤儿检查**：每个指标要么被至少一份种子规则集引用，要么显式标 `library_only` | 指标库膨胀 |
| 9 | 规则集 JSON → 校验 → 编译 → 绑定报告 → JSON 往返稳定 | 幂等性 |
| 10 | **断言 `force_hard` 与 18 个 `enforce_*` 开关不再存在** | 防双轨回潮 |

第 10 条几乎零成本，但它是这次重构唯一的防倒退机制——没有它，半年后某个赶工的改动会再写一条 legacy 分支，A2 就复活了。

同时删掉 `tests/test_kernel.py:289-290` 硬编码的 `len(TEMPLATES) == 20`。它锁的是**数量**而不是**契约**，既挡不住空壳模板，又让每次正当扩展都要改测试——这正是「19 个算子」话术被冻结在 UI（`rule_sets.html:378`）和手册里的原因。换成上面 10 条逐项检查。

### 7.2 运行时绑定报告

编译期建一张 `BindingPlan`：`rule_id → [(index, expr, op, rhs, slack_var|None)]`。求解后一遍遍历求值。成本 O(约束总数)，相对 90-300 秒的求解可忽略。

`PenaltyBook`（`compiler.py:70-111`）今天已按规则保存松弛变量，软约束罚值直接可取；硬约束的「是否绑定」需要额外留住约束句柄——这是唯一的新增记录负担。

**状态语义是核心**，因为它要区分「规则生效且起了作用」与「规则生效但根本没约束到任何东西」：

| status | 判定 | 含义 |
|---|---|---|
| `bound` | hard，且至少一条逐索引约束在最优解处取等 | 规则真实塑形了计划 |
| `satisfied` | hard 且有松弛 / soft 且罚值 = 0 | 满足但不是限制因素 |
| `violated` | soft 且罚值 > 0 | 被权衡掉了，报告给出代价 |
| `degraded` | 有 `DataGap` 且策略为 `warn` | 降级生效 |
| `skipped_data_gap` | 有 `DataGap` 且策略为 `skip` | 未编译 |
| **`out_of_scope`** | **scope 过滤后 `index_count == 0`** | **配了但一条约束都没产生** |
| `simulated` | 规则依赖的任一列在快照里 `source == 'simulated'` | 真实编译并参与求解，但依据的是模拟数据（4.6.3） |
| `disabled` | `enabled: false` | — |

八个状态。`simulated` 与 `degraded` 必须分开：`degraded` 是「数据缺了，规则降级」，`simulated` 是「数据在，但是我们编的」。把两者混为一谈会让 D15 的模拟数据看起来像业务已供数。`simulated` 可与 `bound`/`satisfied` 并存，报告里取更保守的那个作为徽标主色，但在明细里两者都要列出。

`out_of_scope` 是最有价值的一条。今天把 scope 写成 `line: "PGLINKAGE "`（多个尾空格）或一个不存在的线名，结果是零约束、零告警、UI 显示规则已启用——这就是 A5 从计划员座位上看到的样子。`validate_rule_set`（`scope.py:134-168`）已把「unmatched scopes」作为软告警返回，但只在保存时、且无人强制展示；绑定报告把它挪到求解后、且必须展示。

其余字段：`constraints_created`、`index_count`、`gaps`、`penalty_contribution`（含它在本组目标里的占比）、尽力而为的 `dominated_by`。

**报告必须随快照持久化**——键为 `(snapshot_id, rule_set_version, as_of_date)`。这让它接上 5.4 的不可变快照，也让 `src/audit/engine.py` 那个独立二次审计引擎能拿它做交叉验证，而不是像今天这样自证（A8：`validator.py:284-320` 与 `587-604` 用同一份 plan 数据恒等对账；`validator.py:25` 的 `SETUP_SECONDS = 0` 使容量校验也不含换型）。

### 7.3 出口

- `POST /api/solve` 响应增加 `binding_report`
- 新增 `GET /api/rule-sets/{id}/binding-report` 取最近一次
- 规则列表每条规则一个状态徽标，点开看逐索引明细；**status 不是 `bound`/`satisfied` 的一律视觉告警**
- 手册 `aps_rule_set_editor_manual.md:210` 那句「标注【预留】的参数…不要依赖它改变结果」**整句删除**——新模型里不存在「预留参数」

### 7.4 顺带修好 BS-03

`prechecker.py:46` 算出的 `has_exception` 至今无人消费，所以「超载不可行仲裁」这条目录规则一直空转。绑定报告给了它出口：预检结果并入容量类规则的 `status`，`on_data_gap = fail` 走同一条结构化错误通道。BS-03 不需要单独实现，它变成报告的一个消费方。

---

## 8. 迁移与分阶段

### 8.0 统一验证命令

所有阶段共用。**Windows 上 `python` 不在 PATH、`py` 缺 ortools，必须用 venv 解释器；控制台默认 cp1252 打不出中文，必须设 `PYTHONIOENCODING=utf-8`**——否则会得到 61 个假的 `UnicodeEncodeError` 失败：

```bash
# 快测试（61 项，秒级）—— 每个阶段的最低门槛
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py --fast

# 全量（含 90-300 秒的求解器）—— 阶段 1、2 的门槛
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe run_tests.py

# 黄金基线单跑
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m unittest tests.test_golden_baseline -v

# 模板对账（改 schema 或种子数据后必跑）
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verify_master_template.py
```

`run_tests.py --fast` 只覆盖 7 个模块（`test_domain / test_kernel / test_rule_sets / test_labor / test_split_batch / test_independent_audit / test_capacity_kernel`）。**新增的契约测试必须显式加进这个列表**（`run_tests.py:32-40`），否则「fast 全绿」是假的。

另：任何测试跑完后检查 `git status`——`data/snapshot_WL.json` 会被写入并产生 diff（内含绝对路径，缺陷 A6）。提交前用 `git checkout -- data/snapshot_WL.json` 还原，阶段 4 彻底删掉这个缓存。

### 8.1 贯穿始终的门槛：golden baseline 政策

`tests/fixtures/wl_10d_baseline.json` 是唯一的端到端回归锚。阶段 1、2 **必然**改变计划结果，所以必须先定政策，否则基线会变成橡皮图章：

> 每次基线变更必须附**书面理由**——哪个缺陷修复导致、预期变化方向是什么、实际变化是否符合预期。无理由的基线重切一律拒绝。

这条同时写进 PR 模板。

**本文档是设计规格，不是实施计划。** 范围覆盖 5 个阶段，每个阶段各自产出一份独立的实施计划（阶段 0 与阶段 1 可合并为一份）。每份计划以其阶段的「验收清单」作为完成判据；清单里每条都必须能用一个命令或一次 grep 客观判定，不接受「已完成」这种自述。

### 8.2 阶段 0｜地基与排雷（中，低风险）

- [ ] 扩展 `master_schema.py`：按 **5.8 节逐字段照抄**给 `Column`/`SheetSpec` 加 `primary_key` / `foreign_key` / `check` / `contract_tier` / `editable` / `surrogate_key`。新增字段全部带默认值
- [ ] 给 16 张表按 **5.9 节登记表**声明自然主键与外键；同时裁决 `line_base` 缺 `site` 列的问题（5.9 事实 1）
- [ ] 新增两张 sheet：`LINE_HEADCOUNT` / `PART_PAIRING`，**规格见 4.6.1 / 4.6.2，逐列照抄**，追加到 `SHEETS` 元组末尾（`master_schema.py:398-413`）
- [ ] 写 `scripts/seed_simulated_masterdata.py`（**5.12 节五条硬性要求**），落 297 行 `line_headcount` + 5 行 `part_pairing`（1 对 paired + 4 个 unpaired）
- [ ] 定义 `DataGap` 类型（生产者可替换，为阶段 4 铺路）
- [ ] 拆两颗地雷（5.6 节）：硬编码年份 2026（`excel_loader.py:511`、`data_loader.py:455,462`、`server.py:1163-1172`）、约 25 处编造默认值。**先全部改成 `DataGap + warn`**，跑全量测试看暴露出什么，再逐字段决定是否升 `fail`——用实测代替猜测
- [ ] 删 `tests/test_kernel.py:289-290` 的 `len(TEMPLATES)==20`，换成 7.1 节的逐项契约检查（本阶段先标 `skipTest`）
- [ ] **最小化修 B2**：`LATENESS_SLACK_SHIFTS=6` 的窗口硬截断（`scheduler.py:69,1160`）会让超载数据集直接 INFEASIBLE → `RuntimeError`（`:712`）。**这会阻塞阶段 1 的回归验证**，所以必须在这里先把延误变量域解耦到 horizon
- [ ] 4.6 表第 8、9 项：删 `单班定额产能` 与 Parameters-FG 的 `单班工时/每天班次数` 两个废弃列；Campaign 改读模板『产品族名』，废弃 `Campaign-Families.csv`（今天 7 处命中）

**验收清单**

| # | 判据 | 验证方式 |
|---|---|---|
| 0.1 | fast 61 项全绿 | `run_tests.py --fast` 退出码 0 |
| 0.2 | schema 扩展未破坏既有 15 表 | `python -m unittest tests.test_master_schema` 通过；`TEMPLATE_SHEET_COUNT` 由 15 变 17，测试同步更新 |
| 0.3 | 种子脚本确定性 | 连跑两次 `--force`，`md5sum APS_DataSource_Template.xlsx` 两次一致 |
| 0.4 | 种子脚本可拒绝 | 不带 `--force` 时不写盘，`git status` 干净 |
| 0.5 | 模拟数据行数符合规格 | `line_headcount` 恰好 297 行、`source` 全为 `simulated`；`part_pairing` 恰好 5 行，其中 `paired` 1 行、`unpaired` 4 行 |
| 0.6 | loader 内不再有编造默认值 | `grep -n "0.71\|available_qty = 1\|changeover.*30" src/excel_loader.py` 无命中（或每处都是显式 `DataGap`） |
| 0.7 | 硬编码年份清除 | `grep -rn "datetime(2026" src/` 无命中 |
| 0.8 | B2 已解耦 | 构造一个超载数据集求解，不再抛 `RuntimeError`，而是产出带延误的计划 |
| 0.9 | 基线变更有书面理由 | 若 `wl_10d_baseline.json` 变了，提交信息里写明哪个缺陷修复导致、预期方向、实际是否符合 |

### 8.3 阶段 1｜双轨收敛（大，最高风险）

按「先修已漂移的、再修低风险的」顺序，每个模板一个独立回归门：

| 序 | 模板 | 为什么这个顺序 |
|---|---|---|
| 1 | `material_balance` | **已漂移**——REG-01 料号级时间相位账本只在 legacy（`scheduler.py:1657`），kernel 还是旧 pairwise（`compiler.py:353-380`）。把 legacy 实现移植进 kernel 再删 legacy，收益最大 |
| 2 | `capacity_utilization` | kernel 路径已在规则集激活时生效（`use_kernel_capacity`，`scheduler.py:550`），删 `_legacy_capacity_body`（`:2563`）最直接 |
| 3 | `pm_block` | `scheduler.py:1439` vs `compiler.py:383` |
| 4 | `tooling_exclusivity` | `scheduler.py:1389` vs `compiler.py:314`；顺带落实 4.5 的 `available_qty` |
| 5 | `material_ready` | `scheduler.py:1419` vs `compiler.py:340` |
| 6 | `min_batch` | `scheduler.py:1219` vs `compiler.py:210` |
| 7 | `sequence` / `due_date` / `concurrency` | 风险递增，`sequence` 最后（牵着 SDST 与 MTZ） |

每步动作固定：删 legacy 块 → 删对应 `force_hard=True` 调用点 → 删对应 `enforce_*` 开关与构造参数 → 重验基线。**一步一个提交**，不要把 7 个模板揉成一次改动——出问题时无法定位是哪一个。

全部完成后彻底删除 `force_hard`，契约检查 #10 从 skip 转正式。

**验收清单**

| # | 判据 | 验证方式 |
|---|---|---|
| 1.1 | `force_hard` 归零 | `grep -rn "force_hard" src/ tests/` 无命中 |
| 1.2 | 18 个 `enforce_*` 开关归零 | `grep -rno "enforce_[a-z_]*" src/ \| sort -u` 无命中 |
| 1.3 | 6 处 `force_hard=True` 调用点已删 | `scheduler.py` 原 1319/1383/1403/1428/1452/1459 行位置无 `_compile_rules_for` 硬约束调用 |
| 1.4 | `_legacy_capacity_body` 已删 | `grep -n "_legacy_capacity_body" src/scheduler.py` 无命中 |
| 1.5 | kernel `material_balance` 已是料号级时间相位账本 | 单测断言 REG-01 语义；不再是 `compiler.py:353-380` 的 pairwise 近似 |
| 1.6 | 全量测试绿 | `run_tests.py`（不带 `--fast`）退出码 0 |
| 1.7 | 每次基线变更有书面理由 | `git log tests/fixtures/wl_10d_baseline.json` 的每条提交信息都含「缺陷编号 + 预期方向 + 实际结果」 |

### 8.4 阶段 2｜指标层落地（大，本次重设计的主体）

- [ ] `src/kernel/metrics/`：`base.py` + **七个**维度模块（`line_shift / part_shift / task / order / mold_shift / pair_shift / global_`）+ `registry.py`
- [ ] `MetricContext`（无 model）/ `StructureContext`（有 model）分离，按 **3.1 节 Protocol 逐成员照抄**
- [ ] 通用编译器：按 **6.2 节表格逐行实现** `reduction × op × nature`，`_DISPATCH`（`compiler.py:764-784`）对 `kind: metric` 消失
- [ ] 首批 **33** 个指标（3.5 节表），每个带 `catalog_refs` 与「修哪个缺陷」的标注
- [ ] 契约检查 1-9 全部上线（7.1 节），并把新测试模块加进 `run_tests.py:32-40` 的 fast 列表
- [ ] 种子规则集按 **2 节 JSON 形状**重写；SQLite 旧版本置 `archived` 且不可再激活（D6）
- [ ] 编辑器 UI 与手册重写；删掉 `registry.py` 的 11 个死参数与手册第 210 行那句自认；删掉 `rule_sets.html:378` 硬编码的「19 个算子」
- [ ] **B1 在此修**：删 `_lateness_weight`（`scheduler.py:1633-1640`）权重塔，目标项统一走 PenaltyBook 分组字典序（`kernel/solve.py:63` 已有实现）；同时改 `objective_value=sum(bests.values())`（`:1145`）为分层报告
- [ ] **B8 在此修**：`part_shift_shortfall` 给 D+1 时钟正确的域，不再用 `next_demand+1`（`:1916-1917`）

**验收清单**

| # | 判据 | 验证方式 |
|---|---|---|
| 2.1 | 契约测试 1-9 全绿 | `run_tests.py --fast` 通过，且 fast 列表里能看到新增的 `tests.test_metric_contract` |
| 2.2 | 指标确实不碰模型 | 静态断言：`MetricContext` 的成员表里不存在 `model`；契约测试用记录型代理验证 `requires` 不越界（6.5） |
| 2.3 | `_DISPATCH` 无 metric 分支 | `grep -n "metric" src/kernel/compiler.py` 在 `_DISPATCH` 字典里无命中 |
| 2.4 | 指标数与规格一致 | `len(METRICS) == 33`，且每个 `catalog_refs` 非空 |
| 2.5 | 32 条目录规则全部有归属 | 契约检查 #7 通过：每条规则要么被某条规则实例覆盖，要么在 `unenforced` 登记表（附录 B.3，3 条）里有理由 |
| 2.6 | 权重塔已删 | `grep -n "_lateness_weight\|_violation_weight" src/scheduler.py` 无命中 |
| 2.7 | 死参数已删 | `registry.py` 里 11 个从未被读取的参数归零；手册第 210 行那句「不要依赖它改变结果」已删 |
| 2.8 | HC-15 / SC-10 / HC-08 真的编译出约束 | 用阶段 0 的种子数据跑一次，三条规则的 `constraints_created > 0`（状态应为 `simulated`，不是 `out_of_scope`） |
| 2.9 | 基线重切有理由 | 同 1.7 |

### 8.5 阶段 3｜绑定报告（中）

- [ ] `BindingPlan` + 求解后遍历求值（7.2 节）
- [ ] `POST /api/solve` 响应增 `binding_report`；新增 `GET /api/rule-sets/{id}/binding-report`
- [ ] **八个状态**全部实现（7.2 节表），含 D15 新增的 `simulated`
- [ ] UI 状态徽标；非 `bound`/`satisfied` 一律视觉告警；`simulated` 与 `degraded` 必须能区分
- [ ] 报告随 `(snapshot_id, rule_set_version, as_of_date)` 持久化，供 `src/audit/engine.py` 交叉验证（修 A8 自证）
- [ ] 接线 BS-03：消费 `prechecker.py:46` 的 `has_exception`

**验收清单**

| # | 判据 | 验证方式 |
|---|---|---|
| 3.1 | `out_of_scope` 负向测试 | 故意把 scope 写成 `line: "PGLINKAGE "`（带尾空格），API 响应里该规则 status 必须是 `out_of_scope` |
| 3.2 | `simulated` 状态可产出 | 用阶段 0 种子数据求解，HC-15 的报告含 `simulated` |
| 3.3 | 报告已持久化 | 求解后重启进程，`GET /api/rule-sets/{id}/binding-report` 仍返回上次结果 |
| 3.4 | BS-03 不再空转 | 构造超载数据集，报告里对应容量规则 status 反映 `has_exception` |
| 3.5 | 全量测试绿 | `run_tests.py` 退出码 0 |

### 8.6 阶段 4｜数据层 Repository（大）

- [ ] `MasterDataRepository` 端口 + `SqliteRepository`（WAL、连接池、单写者串行化）+ `ExcelRepository`（降级为导入/导出适配器），**签名照 5.10 节**
- [ ] `SnapshotStore`：draft → published，求解只读不可变快照（5.4）
- [ ] 方言感知 DDL 生成器（**5.10 节 kind 映射表**）+ 带版本号的迁移机制（`src/db/migrations/` + `schema_migrations`）
- [ ] `demand` 宽表的 unpivot/pivot 一对纯函数（5.9 事实 2）
- [ ] 外键约束**先 `NOT ENFORCED` 后 `ENFORCED`**（5.9 事实 3：8 个 BOM 子件料号缺 SFN 参数，直接开约束会插入失败）
- [ ] **消灭 `session_state` 全局单例**，站点上下文改为请求级（D14：不提前，就在此阶段做）
- [ ] 修 `rule_sets/store.py` 五个缺陷（5.4 节表）
- [ ] API 改造按 **5.11 节表**：拆掉巨型 `POST /api/inputs/update`，新增 `GET /api/schema/{sheet_key}`
- [ ] 可编辑实体从 6 类扩到 **16 类**（5.9 登记表）；补上「重启后编辑仍在」

**验收清单**

| # | 判据 | 验证方式 |
|---|---|---|
| 4.1 | 重启不丢编辑 | `PATCH` 一行 → 重启 `run_web.py` → `GET` 该行仍是改后的值 |
| 4.2 | 并发不污染 | 两个线程同时 `POST /api/solve`（不同 site），各自结果的 `site` 与输入一致；`session_state` 已删（`grep -rn "session_state" src/` 无命中） |
| 4.3 | JSON 快照缓存已删 | `data/snapshot_*.json` 不存在；跑测试后 `git status` 干净（A6 噪音消失） |
| 4.4 | 第 3 层契约未动 | `grep -c "ProductionInputSnapshot" src/` 的 7 个消费点签名未变；约 15 个直接构造它的测试文件**一行未改** |
| 4.5 | 迁移机制是真的 | 删掉 sqlite 文件后启动，自动建表到最新版本；再启动一次不重复执行 |
| 4.6 | 16 类实体可编辑 | 逐个 `sheet_key` 走一遍 PATCH，全部 200 |
| 4.7 | 方言感知可验证 | 生成器对同一 schema 产出 SQLite 与 Postgres 两份 DDL，快照测试比对（Postgres 不实际连） |
| 4.8 | 全量测试绿 | `run_tests.py` 退出码 0 |

### 8.7 阶段 5｜业务前置条件（与工程并行，非工程可闭环）

D15 之后，4.6 节第 1、7 项已由模拟数据解锁，**不再是工程阻塞**。仍需业务供数的收敛为 3 项：

| 项 | 内容 | 现状 |
|---|---|---|
| #2 | 换模技术员班次可用时段 | HC-02 登记 `unenforced`；无可辩护的派生路径 |
| #3 | 基线/冻结状态 | HC-12 目前按等式近似，非真实冻结 |
| #4 | 试产受控窗口 | HC-13 无数据源 |

另有两项需要业务**确认**而非供数：#5 订单属性（BS-03 仲裁口径）、#6 交期浮动容差数值化（现为自由文本 `0天严格保供` / `±1天窗口协商` / `+3天或顺延次月`）。

**替换模拟数据的流程**：业务供数后，把对应行的 `source` 从 `simulated` 改为 `actual` 并填入真值即可——代码、schema、指标定义都不用动，绑定报告里的 `simulated` 状态会自动消失。这是 4.6.3 那个 provenance 设计的全部目的。

### 8.8 排期裁决（D14）

阶段 4 排在阶段 2、3 之后，是因为指标层只依赖阶段 0 的 schema 扩展，不依赖 Repository。

`session_state` 的并发污染是**当前就存在的真实缺陷**（`GET /api/inputs?site=SY38` 会清空所有人的求解结果，`server.py:919-925`），但**已裁决按原顺序处理，不单独提前**：当前没有真实计划员在线使用，不构成生产事故；提前拆 `session_state` 会打断「阶段 0→1→2」的指标层主线，而那条主线才是这次重设计的价值所在。

**这个裁决的前提是「无真实用户」。** 一旦系统开始给计划员日常使用，前提失效，应重新评估——届时最小止血手段不是重写 Repository，而是把 `GET /api/inputs` 的全局站点切换改成请求级参数（单点改动，约 20 行）。

---

## 附录 A：缺陷索引

本次设计要修的缺陷，按正文出现位置索引。

### 架构级

| ID | 缺陷 | 证据 | 修于 |
|---|---|---|---|
| A1 | God object：`__init__` 63 参数（18 个 `enforce_*`），114 处 `self.x =` | `scheduler.py:309-622` | 阶段 1（删 `enforce_*`） |
| A2 | 六类约束双实现；`force_hard=True` 覆盖 `nature` | `scheduler.py:1319,1383,1403,1428,1452,1459` | 阶段 1 |
| A3 | `session_state` 全局单例、零锁、同步求解占满线程池、身份仅信任可伪造的 `X-Forwarded-User` | `server.py:347-362,139-150,919-925` | 阶段 4（身份部分不在范围内） |
| A4 | 静默降级文化 | `scheduler.py:447-461,883-885`；`data_loader.py:739,767,785,256-259`；`excel_loader.py` 约 15 处 | 阶段 0 + 契约 #2 |
| A5 | 11 个模板参数声明但编译器零引用；`available = 1` 硬编码 | `registry.py:94-273`；`compiler.py:318` | 阶段 2 + 契约 #3 |
| A6 | 确定性未闭环：壁钟 tier 预算、`date.today()` 规则解析、快照存绝对路径 | `scheduler.py:677,714,747`；`scope.py:22` | 阶段 2（D13）+ 阶段 4 |
| A7 | `routing` 模板登记未编译；候选线按列表顺序硬裁剪为「主+1 备」 | `registry.py:245-252`；`compiler.py:764-784`；`scheduler.py:473-476` | 阶段 2 + 契约 #1 |
| A8 | 校验器自证；`SETUP_SECONDS = 0` 使容量校验不含换型 | `validator.py:284-320,587-604,25` | 阶段 3（交叉验证）+ 4.4 裁决 |

### 算法级

| ID | 缺陷 | 证据 | 修于 |
|---|---|---|---|
| B1 | 目标函数量纲混战，`_lateness_weight ≈ 7.8×10^10` 与权重 1 混在同一 `sum()`；`objective_value` 把异构量相加 | `scheduler.py:1626,1629-1640,1145` | 阶段 2（D12） |
| B2 | 软交期档被 `due+6` 窗口硬截断，超载即 INFEASIBLE→`RuntimeError`；SF 窗口语义错误 | `scheduler.py:69,1160,712,1162-1163` | 阶段 0（最小化）+ 阶段 2 |
| B3 | 换型时间默认不占产能 | `shift_manager.py:26`；`scheduler.py:494,2578`；`compiler.py:278-279` | 阶段 2 + 4.4 裁决 |
| B4 | 缺换模边静默回退 60 分钟；`setup_matrix_strict` 默认 `False`，`main.py` 从不传该参数 | `scheduler.py:2301,2258,341`；`sequence.py:189` | 阶段 0 + 4.4 裁决 |
| B5 | `assign_in_model` 开启后 FIFO/sequence/pm/capacity 全部旁路 | `scheduler.py:1365,1367,1437,2551,883-885` | 阶段 1 |
| B6 | 空闲「跳班」无 hole 惩罚 | `scheduler.py:2627-2636,978-999`；grep 无 `hole` | 阶段 2（`idle_minutes`） |
| B7 | 先到先停：`_SolutionStopper` 在第一个「干净」解上 `StopSearch` | `scheduler.py:75-84,1015,1029-1043,1177` | 阶段 2（字典序） |
| B8 | D+1 硬域上限致假性不可行 | `scheduler.py:1916-1919` | 阶段 2（`part_shift_shortfall`） |
| B9 | MOQ 的 `basis=order` 约束空转 | `compiler.py:234-243`；`scheduler.py:1239` | 阶段 2 |
| B10 | 规模天花板：sequence O(n²)、库存 O(parts×shifts×tasks)、订单级变量线性爆炸 | `scheduler.py:2349-2371,2414-2446,2629-2633,2104-2145` | **不在范围内** |
| B11 | 冻结基线整除截断；FG/SF 混线一律按 FG 的 105%；kernel 均摊最大 setup reserve；硬编码 `'WA' in part_no` 得 10 倍权重 | `scheduler.py:271,1311,644-647,1501`；`compiler.py:179-186` | 阶段 2 逐项 |

### 主数据层

| ID | 缺陷 | 证据 | 修于 |
|---|---|---|---|
| M1 | 换型时间五个可能来源 | `master_schema.py:245,272,333`；`shift_manager.py:26`；`scheduler.py:2301` | 4.4 裁决 |
| M2 | 班次级可用人数无任何数据源 | `master_schema.py:171,210,250` 全是「需要多少人」 | **D15 → 阶段 0**：4.6.1 新表 + 模拟数据 |
| M3 | 5 张主数据表无模板 sheet、0 加载器 | `data/master/{Baseline-Schedule,Calendar-Patterns,Customer-Orders,Setup-Technicians,Trial-Windows}.csv` | 4.6 扩展 #2-#5（#1/#7 已由 D15 在阶段 0 处理） |
| M4 | Campaign 数据双源 | 模板『产品族名』vs `Campaign-Families.csv`（7 处命中） | 4.6 扩展 #9 → 阶段 0 |
| M5 | 编辑重启即丢 | `server.py:1146-1249`；`load_snapshot_json` 无 server 调用者 | 阶段 4 |
| M6 | 硬编码年份 2026 | `excel_loader.py:511`；`data_loader.py:455,462`；`server.py:1163-1172` | 阶段 0 |
| M7 | 可编辑实体仅 6/16 类，无 PUT/PATCH | `server.py:557-607,1146-1249`；目标态见 5.9 登记表 | 阶段 4 |
| M8 | `line_base` 无 `site` 列，多站点同名产线会互相覆盖 | 实测 4 列 `line/trp/hours_per_shift/shifts_per_day`，3 行 | 阶段 0 裁决，阶段 4 落 DDL |
| M9 | 8 个 BOM 子件料号不在 `parameters_sfn` 中，外键一旦 ENFORCED 即插入失败 | `master_template_verification.md` P2 项 | 阶段 4（先 NOT ENFORCED） |

---

## 附录 B：32 条目录规则 → 新模型映射登记表

契约检查 #7 要求每条目录规则要么被覆盖、要么进 `unenforced` 登记表。本表是初始登记，实施时以 CI 校验为准。

**分类统计**：指标可组合 16 条｜结构类 8 条｜T3 缺数据阻塞 3 条｜非约束（预处理/后处理/求解控制）5 条。合计 32。

### B.1 指标可组合（16）

| 规则 | 指标 | reduction / op | 备注 |
|---|---|---|---|
| HC-04 换模工时刚性扣减 | `line_shift_changeover_minutes` | each / `<=` 班次余量 | 修 B3、B4；来源按 4.4 优先级表 |
| HC-06 BOM 齐套开工 | `part_shift_cumulative_inventory` vs `part_shift_demand` | each / `>=`（rhs 指标引用） | 2.4 节 |
| HC-09 半成品安全前置期 | `task_start_shift` vs 父 `task_completion_shift` | each / `>=`（rhs 指标引用 + offset） | 修 B2 的 SF 窗口语义 |
| HC-14 防断线超库豁免 | `part_shift_inventory` | each / `<=` + `params.threshold` | 2h 阈值改为显式参数，不再硬编码启发式 |
| SC-01 D+1 备货时钟 | `part_shift_shortfall` | each / `<=` | 修 B8 域上限 |
| SC-02 利用率 95~100% | `line_shift_utilization` | each / `between` | 取代 `capacity_utilization` 模板的 `min_util`/`max_util` |
| SC-04 MPQ 整倍 | `task_batch_size` | each / `==`（模运算） | CP-SAT 需 `AddModuloEquality`；注意今天实为硬约束 |
| SC-05 SDST 换模矩阵 | `task_setup_minutes` | each / `<=` 或 minimize | 修 B4 |
| SC-06 SF 分时段库存 | `part_shift_inventory`（`scope.stage=SF`） | each / `<=` | |
| SC-07 FG 分时段库存 | `part_shift_inventory`（`scope.stage=FG`） | each / `<=` | |
| SC-09 Jitter 抑制 | `task_completion_shift` | — / `minimize` | D11：纯目标项，不再有 `nature` |
| BS-04 逆向拉动 | `task_completion_shift` | — / `maximize` | 完成偏好翻转的正确表达 |
| BS-05 同模合批 Campaign | `campaign_run_count` | — / `minimize` | 取代硬编码 60,000 罚值（`scheduler.py:1838`） |
| HC-08 L/R 共模同步 | `pair_production_ratio` / `pair_shift_gap` | each / `<=` `params.ratio_tolerance` | **D15 解锁**：4.6.2 配对表落模拟数据；当前仅 1 对可辩护，4 个『同时生产=Y』料号为 `unpaired` → 报告出 `simulated`+`degraded` |
| HC-15 班次人数红线 | `headcount_required` vs `headcount_available` | each / `<=`（rhs 指标引用） | **D15 解锁**：4.6.1 新表落模拟数据 → 报告出 `simulated` |
| SC-10 三班人力平滑 | `headcount_delta` | — / `minimize` | 同上；纯目标项，按 D11 不带 `nature` |

### B.2 结构类（8）

| 规则 | 结构构建器 | 备注 |
|---|---|---|
| HC-01 模具跨设备独占 | 模具占用分组 | `available_qty` 改为真实参数（4.5 节） |
| HC-03 PM 避让 | PM 班次封锁 + 部分降载 | 当前唯一 REAL 之一，保持 |
| HC-05 合格机台/工艺能力 | routing / assign | **补 A7**：`routing` 模板登记未编译 |
| HC-10 工序重叠/转移批量 | 转移批量变量 | 当前 STUB（仅 s-1 班次近似） |
| HC-11 MOQ + 跨班连续 | contiguity / 拆批 | MOQ 部分见 B9（`basis=order` 空转） |
| HC-16 同模连产防穿插 | 同模窗口模型 | 当前 v0 是「每工单最多 1 块」近似 |
| SC-03 首选高效主产线 | routing 优先级 | 与 HC-05 共用结构 |
| SC-08 顺位前置填补 | 顺位变量 + 成对倒置 | 罚项走 `minimize`，结构只提供变量 |

### B.3 T3 缺数据阻塞（3）——登记为 `unenforced`，理由 = 主数据缺失

| 规则 | 缺失数据 | 4.6 扩展项 |
|---|---|---|
| HC-02 换模技术员容量 | 技术员班次可用时段 | #2 |
| HC-12 时间围栏冻结 | 基线/冻结状态（当前按等式近似） | #3 |
| HC-13 试产受控窗口 | 试产窗口表 | #4 |

这 3 条在指标注册表里对应指标标 `status: unavailable`，编辑器 UI 显示「需主数据扩展」且**禁止启用**；规则集保存时若引用则报错。

原本的 6 条里，HC-08 / HC-15 / SC-10 已由 D15（先落显式模拟数据）解锁并移入 B.1。**这三条不再阻塞，但其生效状态是 `simulated` 而非 `bound`**——工程上算通了，业务数据仍然是欠的，两者不能混。#2、#3、#4 三项没有可辩护的派生路径（技术员排班、冻结基线、试产窗口都无法从现有 15 表推导），所以保持 `unenforced`，仍需业务供数。

### B.4 非约束（5）

| 规则 | 类别 | 归属 |
|---|---|---|
| HC-07 报废率投料补偿 | preprocess | `data_loader.apply_scrap`，保持预处理 |
| BS-01 四阶日历自适应 | preprocess（推荐） | `shift_manager.py:300` 已算推荐；**当前未接线**——`main.py:94` 硬编码 `SEVEN_ZERO`。接线属独立任务 |
| BS-02 跨期平准/拉单 | preprocess | `server.py` 的 `apply_demand_lookahead` |
| BS-03 超载不可行仲裁 | postprocess | 7.4 节：绑定报告消费 `prechecker.py:46` 的 `has_exception` |
| BS-06 Slack 退避切分 | 求解控制策略 | `scheduler.py:795`，属 solver 循环，不是约束 |

---

## 附录 C：本次清理记录（2026-11-03）

设计前的一次全仓整理，删除以下已核实冗余文件（全部可从 `3b47ceb` 恢复）：

| 文件 | 证据 |
|---|---|
| `aps_project_lifecycle.html`（根） | md5 与 `src/static/` 副本字节相同；唯一引用 `server.py:3533` 读 STATIC_DIR 那份 |
| `src/static/index.html.original_backup` | 全仓 grep `original_backup` 0 命中 |
| `src/static/index.html.ui_master_backup` | 同上 |
| `archive/`（整树，16 文件） | `archive/README.md` 自述「只读留存，不参与构建、不被代码引用」；10 份规格文档已被取代 |
| `data/master/labor/`（3 CSV） | `load_labor_data` 只读 `data/` 根目录，从未被加载 |
| `scripts/` 下 8 个一次性脚本 | `add_rule_details.py`（源文件不存在）、`archive_business_confirmation.py`、`fill_missing_bom_params.py`、`fill_missing_routing.py`、`fill_simulated_masterdata.py`、`refine_business_confirmation.py`、`simulate_missing_masterdata.py`、`update_business_confirmation_20261103.py`（均已执行完） |

保留的可复用脚本：`build_business_confirmation.py`（业务确认清单的唯一生成器）、`rebuild_instructions.py`（导入 live `src.instructions`）、`verify_master_template.py`（生成 `master_template_verification.md`）。

同步修正的悬空引用：`README.md`、`Specification/aps_implementation_log.md`（2 处）、`Specification/masterdata_simulation_notes.md`（2 处）、`data/master/README.md`（2 处）、`Specification/aps_new_rule_set_gap_analysis.md`（1 处）——回滚路径改为 `git show 3b47ceb:<path>`。

审查同时确认：**没有死 Python 模块，没有失效测试**。42 个测试文件的 `from src.*` 导入逐一比对源码树，0 缺失；`src/rule_sets/migrations.py` 是唯一零引用模块。

清理后 `--fast` 测试 61/61 OK。

---

## 附录 D：实施交接约定

写给接手编码的智能体或工程师。这些不是风格偏好，是本仓库实测出来的约定与已知的坑。

### D.1 环境事实（照抄，不要自己试）

| 事实 | 后果 |
|---|---|
| `python` 不在 PATH；`py` 装了但**没有 ortools** | 一律用 `.venv/Scripts/python.exe` |
| Windows 控制台默认 cp1252，打不出中文 | 一律前缀 `PYTHONIOENCODING=utf-8`，否则得到 **61 个假的 `UnicodeEncodeError` 失败**，看起来像代码坏了 |
| Shell 是 Git Bash（POSIX sh），不是 cmd/PowerShell | 用 `/dev/null` 不用 `NUL`，用正斜杠 |
| `gh` CLI **未安装** | GitHub 操作走网页或 `git`，不要调 `gh` |
| 远端只有 `origin` | 个人 fork 已删除（2026-11-03） |

标准验证命令见 **8.0 节**。

### D.2 代码约定（实测）

- 48 个 `src/*.py` 里 **32 个**有 `from __future__ import annotations`——新文件一律加
- **代码注释、docstring、标识符用英文**；**文档、UI 文案、规格用中文**。不要混
- `master_schema.py` 那种分节注释风格（`# ---- 标题 ----` 横线块）沿用
- 数据类一律 `@dataclass(frozen=True)`；有可变需求的（如 `BuiltMetric`）才用普通 `@dataclass`
- 全量类型标注，容器用 `typing.Tuple/Sequence/Mapping`（与既有代码一致，不要混用内置泛型下标）
- **不写解释「做了什么」的注释**——命名已经说了。只在「为什么」不显然时写一行（隐藏约束、绕过的 bug、会让读者意外的行为）
- 不新增依赖。`requirements.txt` 里没有的东西不要用

### D.3 禁止事项（每条都对应本文档里一个已核实的缺陷）

1. **禁止在 loader 里补默认值。** `excel_loader.py` 那约 25 处编造值（5.6 节地雷 2）是这次要拆的东西，不是要学的模式。缺数据一律产 `DataGap`。
2. **禁止新增任何 `print`。** 告警走 `warnings` 列表或 `DataGap`，错误走异常。
3. **禁止改第 3 层契约。** `RequirementPlan` 与 `ProductionInputSnapshot` 的字段和 7 个消费点签名（5.2）冻结。约 15 个测试文件直接构造它们——**如果你发现必须改某个测试才能通过，先停下报告，不要改**。
4. **禁止无理由重切黄金基线。** `tests/fixtures/wl_10d_baseline.json` 的每次变更，提交信息必须写明「哪个缺陷修复导致 + 预期变化方向 + 实际是否符合」（8.1）。
5. **禁止直接往 `model.Minimize` 加项**（D12）。只经 PenaltyBook。
6. **禁止引入壁钟依赖。** tier 预算用 `max_deterministic_time`；`as_of_date` 显式传入。不要调 `date.today()`（`scope.py:22` 那处是要被修的）。
7. **禁止用数量断言代替契约断言。** `len(TEMPLATES) == 20` 这种（`test_kernel.py:289-290`）是反面教材——它锁数量不锁语义，既挡不住空壳又阻碍正当扩展。
8. **禁止把新测试只放进 `tests/` 却不加进 `run_tests.py:32-40` 的 fast 列表。** 那样「fast 全绿」是假的。

### D.4 规格与现实不符时

**本文档所有 `file:line` 都是 2026-11-03 在 `3b47ceb` 之后、`89ee6e0` 时点的快照。** 代码会漂移，行号会失准。处理规则：

- 行号对不上但符号还在 → 按符号找，继续做，不必报告
- 符号不存在了 / 逻辑与规格描述不符 → **停下报告，不要自行猜测意图**。规格里的每条结论都是核实过的，如果现实变了，说明有人在规格之外改了代码，那是需要先弄清楚的事
- 发现规格里有错（签名写错、语义自相矛盾）→ 停下报告。**不要「顺手修正」**：这份文档是后续每个阶段的输入，静默改动会让后面所有计划基于错的前提
- 需要做一个规格没覆盖的决定 → 停下问，不要自己定。D1–D15 之外的决定权不在实施者

### D.5 定义完成（每项任务）

1. 该任务对应阶段的验收清单条目**逐条跑过**，命令与输出留在提交信息或 PR 描述里
2. `run_tests.py --fast` 退出码 0（涉及求解行为的还要跑全量）
3. `git status` 干净——特别是 `data/snapshot_WL.json` 没有意外 diff（8.0 末尾）
4. 一个任务一个提交。阶段 1 的 7 个模板**必须**是 7 个提交（8.3）
5. 没有留下 TODO / 注释掉的代码 / 空实现。做不完就如实说做不完，不要用占位符伪装完成

### D.6 每阶段开工前

先产出该阶段的实施计划（用 `superpowers:writing-plans`），把验收清单拆成任务、标出依赖顺序。**阶段 0 与阶段 1 可合并为一份计划**，其余各自一份。计划里每条任务都要能指回本文档的某一节——指不回去的任务说明范围蔓延了。
