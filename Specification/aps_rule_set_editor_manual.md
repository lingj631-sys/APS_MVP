# APS 规则集编辑器操作手册

> 适用版本：Phase 1 v1（2026-10-26）
> 对应页面：`/rule-sets`（主界面「规则与约束配置」顶部入口）
> 运行时真相：`data/rule_sets.sqlite3` 里的 **active 版本**；Excel 和页面只是编辑入口。

---

## 0. 先分清 27 / 17 / 13

页面上经常同时出现三个数字，它们不是同一层东西：

| 数字 | 含义 | 来源 |
|---:|---|---|
| **27** | WL 的**业务规则目录/旧版规则页**条数（SY38 是 9） | `src/rules.py` / 规则 Excel；页面「规则与约束配置」 |
| **17** | **校验检查项**数量（17/17 表示 17 项检查全过） | `src/validator.py` 的 `Check`；每项可关联多个规则 ID |
| **13** | **拆分版内核规则实例**数量（WL_PHASE1 v3/seed；11 个算子，capacity/min_batch 各拆 FG/SF） | `/rule-sets` 的 active 版本为准；旧 v1 仍是 11 条 |

另外还有 **9** 项独立审计（`src/audit` 的 9/9），和 17 项校验器不是同一套。

> 13 是**拆分版（v3/seed）**的实例数（11 个算子），不是"只能有 13 条"：19 个模板里 18 个已实现，拆分版放了 13 条（capacity/min_batch 各拆 FG/SF）；其余 7 个模板（contiguity / calendar / time_window / inventory_bound / freeze / co_product / labor_capacity）可以在 `/rule-sets` 里"新增规则"补上。`routing` 仍未实现（Phase 2）。**当前 active 版本以 `/rule-sets` 为准，可能仍是旧 v1 的 11 条。**

### 27 和 13 是什么关系

- 不是 1:1 映射，也不是"13 条是 27 条的子集"。
- 27 条是**业务规则目录**：包含求解约束、校验规则、测试场景（如 R-WL-16～19）、数据规则等；有些规则只用于校验/展示，不是求解器约束。
- 13 条是**拆分版内核规则实例**：每条对应一个固定算子（`capacity_utilization`、`min_batch`、`due_date`……）。多个 27 规则可能落到同一个算子；拆分版把 capacity 和 min_batch 各拆成 FG/SF 两条，所以实例数比算子数多 2。
- 27 页的 `engine_switches` 把部分规则 ID 映射到旧布尔开关（如 R-WL-26 → `enforce_utilization_95`），但这不是内核规则集的反向生成。
- 27 页显示的"权重 8000% / 2500%"是**旧业务规则权重**，和内核规则集的 0~1000 相对权重不是同一套标尺，不能直接互抄。

### 以哪个为准

- **已迁入内核的模板**：以 `/rule-sets` 的 active 版本为准。典型：`capacity_utilization` 的 `max_util`、`due_date` 的软硬、`min_batch`、`material_balance`、`concurrency`、`customer_priority` 等。
- **尚未迁移/只做校验/测试场景**：仍看 27 页的旧开关或只参与校验展示。
- **容量 105%**：只看规则集 capacity 规则的 `max_util`；拆分版 v3 是 `WL_CAP_FG=1.05`、`WL_CAP_SF=1.0`（105% 只给成品，半成品保持 100%）；如果 active 仍是旧 v1，则只有一条旧 `WL_CAP`。27 页的 95%~105% 单选不覆盖规则集。
- 27 页的**已接管规则开关已置灰只读**（仍显示启用），修改请到 `/rule-sets`；`partial`/`available` 规则仍可能被旧开关 gate，调参时保持对应开关为开启。最终应把 27 页收敛成校验目录或完全由规则集生成；这一步还没做完。

---

## 1. 先理解三件事

### 1.1 算法是固定的，规则是数据

系统只有 **19 个固定算子模板**（`min_batch`、`capacity_utilization`、`changeover`……）。工厂不能写新算子，只能：

- 选一个模板；
- 定义它作用在哪些产线/阶段/料号（作用域）；
- 定义它是硬约束还是软约束；
- 软约束给一个权重；
- 把它放进一个分组；
- 填这个模板的参数。

### 1.2 硬约束 vs 软约束

| 类型 | 含义 | 权重 |
|---|---|---|
| `hard` | 必须满足；不满足就是无解 | 忽略 |
| `soft` | 进入目标函数；违反按权重罚，允许违反 | 生效 |

系统会**先尝试硬 95% 档**；如果硬档在时间预算内无解，会降级到软 95% 档。软档只"罚"低于 95%，不"禁止"，所以软档计划可能出现低负荷班次。看到低于 95% 时，先看求解日志的「排产口径」是不是硬约束。

### 1.3 作用域决定"谁被这条规则管"

作用域有四个维度：

```
site（工厂） / line（产线） / stage（FG/SF） / part（料号）
```

空值或 `*` 表示"全部"。例如：

```json
{"line": "PGLINKAGE"}                 // 只管 PGLINKAGE
{"stage": "FG"}                       // 只管成品
{"line": "PGLINKAGE", "stage": "FG"}  // 只管 PGLINKAGE 的成品
{}                                    // 全厂所有任务
```

更具体的作用域优先级更高；同一模板同一分组里，具体规则会**覆盖**宽规则（`inherit=replace`），也可以选择**叠加**（`inherit=add`）。

---

## 2. 页面总览

页面从上到下分三块：

1. **版本与操作栏**：工厂、版本、状态、导入/导出/激活/保存/敏感度/诊断。
2. **批量与视图栏**：版本元信息、按组批量、树形/平铺、新增规则。
3. **规则表**：每条规则一行，可直接改软硬、权重、启用、参数。

### 2.1 顶部按钮

| 控件 | 含义 | 什么时候用 |
|---|---|---|
| 工厂 | WL / SY38 | 两个工厂各自有独立的 active 规则集 |
| 版本 | 该工厂的历史版本列表；`active` 是当前生效版本 | 回滚、对比、查看旧版本 |
| 状态胶囊 | `active` / `draft` / `archived` | 确认当前是不是生效版本 |
| 刷新 | 重新拉版本列表与当前 payload | 导入/保存后页面没更新时 |
| 下载 Excel 模板 | 生成 `Settings / Groups / Rules / Templates` 四个 Sheet | 喜欢用 Excel 批量编辑时 |
| 导入 Excel | 解析 → 逐行校验 → diff → 生成新版本 | Excel 编辑后回传 |
| 导出当前版本 | 把当前版本导出为 Excel | 备份、评审、离线修改 |
| 激活当前版本 | 把当前版本设为 active；其他版本自动 archived | 保存后确认生效，或回滚 |
| 敏感度 ±20% | 对每个软组权重做 ±20% 扰动，跑真实求解，输出 KPI delta | 调权重前评估影响 |
| 无解诊断 | 逐组 → 逐规则 → 规则对放宽，定位冲突 | 排产报"无可行解"时 |
| 保存为新版本 | 保存为 v+1；勾选"保存后立即激活"则同时生效 | 改完规则后 |

> 敏感度和诊断会跑多次真实求解（默认 15s/次），一次点击可能要几分钟；生产上建议用 CLI 或后台任务。

### 2.2 第二行

| 控件 | 含义 |
|---|---|
| 版本元信息 | `rule_set_id · v版本 · 创建时间` |
| 批量分组 | 选一个分组，例如 `G0_PHYSICAL · hard · P0` |
| 批量权重 | 输入 0~1000，点"应用到该组"，该组所有规则权重一起改 |
| 启用/停用该组 | 把所选分组的所有规则批量 `enabled=true/false`；保存后生效 |
| 切换平铺/树形 | 树形按 `产线 · 阶段` 分组；平铺按原始顺序 |
| 保存后立即激活 | 不勾选只生成 draft；勾选则保存即生效 |
| 新增规则 | 从 19 个模板里选一个，生成默认参数的新规则 |

### 2.3 规则表字段

| 列 | 含义 | 怎么改 |
|---|---|---|
| 规则 ID | 唯一标识；Excel 按它做 diff | 新增时自动生成，建议用 `WL_` / `SY38_` 前缀 |
| 模板 | 19 个固定算子之一 | 新增时选；已有规则不建议改模板 |
| 作用域 | 空 = 全部；可填 line / stage / part | 页面直接改 line/stage/part；site 用 Excel/JSON；越具体优先级越高 |
| 分组 | 规则属于哪个目标层 | 新增时选；一般不改 |
| 继承 | `基础` / `覆盖`（由作用域具体程度自动判断） | 下拉选 `replace`（覆盖）或 `add`（叠加） |
| 软/硬 | `hard` 必须满足；`soft` 进目标 | 下拉切换 |
| 权重 | 0~1000；**只在软约束、同组内比较时才有意义** | 见第 3 节 |
| 启用 | 关掉后不参与编译，但保留在版本里 | 勾选/取消 |
| 参数 JSON | 模板参数，必须符合 Schema | 直接编辑 JSON；保存时校验 |
| 删除 | 从当前草稿移除该规则 | 保存后才真正进入新版本 |

---

## 3. 权重到底是什么意思（1 还是 100？）

### 3.1 一句话

**权重是"同组软约束之间的相对重要性"，不是分钟、不是金额、不是绝对罚分。**

### 3.2 归一化规则

假设同一分组里有两条软规则：

| 规则 | 权重 |
|---|---:|
| A | 100 |
| B | 1 |

系统计算：

```
A 的系数 = 100 / (100+1) × scale ≈ 0.990 × scale
B 的系数 = 1   / (100+1) × scale ≈ 0.010 × scale
```

所以 A 每单位的罚分大约是 B 的 **99 倍**。

如果改成：

| 规则 | 权重 |
|---|---:|
| A | 1 |
| B | 1 |

则 A、B 每单位罚分相等。

### 3.3 三个必须知道的结论

1. **只有一条软规则时，权重 1 和 100 没有区别。**
   因为归一化后它总是拿到满额系数（scale），改 1→100 不会改变计划。

2. **不同组的权重不直接比较。**
   组间 priority 由内核字典序求解器使用：**软档**按 G1→G2→G3 逐组求解并冻结；**硬档**的交付/95% 本身就是硬约束。跨组权重不直接比较。

3. **硬约束的权重被忽略。**
   `hard` 只看约束是否满足；把 hard 规则权重从 1 改到 100 不会让它"更硬"。

### 3.4 实用建议

- 用 **1~1000 的整数** 表达比例即可，不必追求精确；例如：
  - 换型 : 并行型号 = 3 : 1 → 权重 300 / 100；
  - 交付 : 换型 = 10 : 1 → 放在不同组；硬交期/硬 95% 已能防止被换型软权重买掉。
- 想表达"这条规则明显更重要"，让它的权重至少是组内其他规则之和的 2~3 倍。
- 单规则组不要反复调权重；调了也不会变。
- 改完用「敏感度 ±20%」看 KPI 是否按直觉变化；如果没变化，通常是单规则、硬约束，或该组优先级太低。

### 3.5 分组与优先级

| 分组 | 典型含义 | 模式 | 优先级 |
|---|---|---|---:|
| `G0_PHYSICAL` | 物理约束：MPQ、模具、PM、物料齐套、冻结 | hard | 0 |
| `G1_DELIVERY` | 交期、客户分级 | lexicographic | 10 |
| `G2_CAPACITY` | 95% 下限、105% 上限、人力 | lexicographic | 20 |
| `G3_CHANGEOVER` | 换型、连续、并行型号 | weighted | 30 |
| `G4_INVENTORY` | 库存/覆盖天数 | weighted | 40 |

- **组间**：软档按 priority 逐组求解（G1 交付 → G2 产能 → G3 换型），前一组最优值冻结后再解下一组；硬档的交付/95% 是硬约束，不参与软权重比较。
- **组内**：`hard` 组只加约束；`weighted` 组按权重归一化；`lexicographic` 组也是先组内加权、再作为一层。
- 一个组没有规则时会有"分组没有任何规则"告警；不影响求解，可删组或补规则。

---

## 4. 19 个算子模板与 JSON 参数

> 状态：18/19 已实现编译；`routing` 留 Phase 2（多线选择）。
> 标注【预留】的参数表示 Schema 已登记、当前编译器未使用；保留是为了向后兼容，不要依赖它改变结果。

### 4.1 `capacity_utilization` — 班次产能利用率

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `min_util` | ratio 0.95 | 已开班的最低利用率；0.95 = 95% |
| `max_util` | ratio 1.0 | 班次上限倍数；1.0 = 100%，1.05 = 105% |
| `tail_exempt` | bool true | 每条线最后一个开班免 95%（收尾班） |
| `include_setup` | bool false | 【预留】换型工时是否另计入容量；TRP 已含换型时保持 false |
| `setup_minutes_per_start` | number 0 | C1-b-lite：每个 run start 额外占用容量的分钟数；0 = 沿用 TRP 已含换型 |

示例：

```json
{"min_util":0.95,"max_util":1.0,"tail_exempt":true}
```

要点：

- **规则集的 `max_util` 是 100/105 的唯一开关**；旧规则页的 95%~105% 单选在规则集模式下不生效。
- 当前 WL 拆成 `WL_CAP_FG`（1.05）和 `WL_CAP_SF`（1.0）：**105% 只允许成品用于 MPQ 拼单；半成品最高 100%**。
- 95% 是**下限**；105% 是**上限**，不是"允许低于 95%"，也不是"越高越好"。
- **soft 规则**：当前内核已支持双边软目标，低于 95% 和高于 `max_util` 都计罚，但**不再加硬上限**；hard 规则仍保持 95% 下限 + 上限硬约束（SC-02 / 新规则）。
- `tail_exempt` 只豁免每条线**最后一个开班**；中间的 22.5% 不是 tail，是异常/软档。
- `soft` 产能规则用于 SC-02 的 95%~100% 双边目标；`hard` 产能规则仍用于硬性 95% 合规。

### 4.2 `min_batch` — 最小投产批量 / MPQ

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `unit` | enum MPQ | `MPQ` = 按 MPQ 整批；`piece` = 按件 |
| `rounding` | enum ceil | 【预留】取整方式 |
| `min_multiple` | int 1 | `unit=piece` 时的最小件数倍数 |
| `basis` | enum shift | `shift` = 每班门槛；`order` = 工单总量门槛 |
| `quantity_source` | enum MPQ | `MPQ` = 用包装模数；`MOQ` = 用主数据 MOQ |
| `tail_exempt` | bool false | `basis=order` 时，尾数/急单总量低于 MOQ 允许豁免 |
| `moq_override` | number 0 | 临时覆盖主数据 MOQ；0 表示用 `ProductionTask.moq` |

示例（WL 当前拆分）：成品 `{"unit":"MPQ"}`；半成品 `{"unit":"piece","min_multiple":1}`。

HC-11 的 MOQ 示例：

```json
{"unit":"piece","basis":"order","quantity_source":"MOQ","tail_exempt":true}
```

要点：

- 默认 `basis=shift, quantity_source=MPQ`，保持现有排产行为不变。
- `basis=order` 只加“工单总量 ≥ 门槛”约束，不要求每班 ≥ MOQ；`tail_exempt=true` 时，工单总量低于门槛直接放行（用于收尾/急单）。
- `quantity_source=MOQ` 读取 `ProductParam.moq` / `ProductionTask.moq`；当前主数据 mock 的 MOQ 暂等于 MPQ，替换真实数据后生效。
- `moq_override > 0` 可临时覆盖主数据，便于规则试验。

### 4.3 `contiguity` — 连续生产 / 块数上限

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `max_blocks` | int 1 | 一个工单最多允许几个连续生产块 |
| `mode` | enum soft | 兼容字段；内核以 rule 的 `hard/soft` 为准 |

示例：`{"max_blocks":1,"mode":"soft"}`

要点：`max_blocks=1` 表示一个连续块；硬档可能让模型无解，建议先用 soft，再按需改 hard。

### 4.4 `changeover` — 换型

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `setup_minutes` | number 0 | 【预留】换型分钟；TRP 已含换型时保持 0 |
| `count_by` | enum run_start | 按 run start 计价；`model_switch` 暂未实现 |
| `cross_shift_inherit` | bool true | 【预留】跨班继承 |

示例：`{"setup_minutes":0,"count_by":"run_start"}`

要点：换型是软目标（G3），权重相对并行型号/连续规则生效。

### 4.5 `material_balance` — 两阶段齐套

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `buffer_shifts` | int 0 | 半成品提前几个班次可用；0 = 上一班产出本班可用 |
| `include_stock` | bool true | 是否计入期初库存 |

示例：`{"buffer_shifts":0,"include_stock":true}`

要点：约束是 `Σ component(s-1-buffer) × unit + stock ≥ BOM_ratio × FG(s)`；hard 规则，轻易不要改成 soft。

### 4.6 `material_ready` — 物料到货门控

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `source` | enum PO | 【预留】PO/ETA/STOCK |
| `qc_release_hours` | number 2 | 【预留】质检放行小时 |

要点：当前按任务自带的 `material_arrival_shift` 在到货前禁止开工；参数暂未参与编译。

### 4.7 `due_date` — 交期 / 截单

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `mode` | enum hard | `hard` = 不晚于交期；`soft` = 拖期罚 |
| `cutoff_time` | str 15:00 | 【预留】截单时间 |
| `tolerance_minutes` | number 0 | 【预留】容忍分钟 |

示例：`{"mode":"hard"}`

要点：硬交期在硬 95% 档；软交期在软档，和 `customer_priority` 配合使用。

### 4.8 `sequence` — 同料号顺位

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `order_by` | enum due | `due` = 按交期先到先排；`fifo`/`priority` 暂按 due |
| `tolerance_shifts` | int 0 | 【预留】顺位容忍班次 |

示例：`{"order_by":"due"}`

### 4.9 `calendar` — 工作日历

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `mode` | enum SEVEN_ZERO | `FIVE_TWO` 周末停；`FOUR_THREE` 周五~周日停；`SEVEN_ZERO`/`AUTO` 不停 |
| `weekend_off` | bool false | true 等价于五休二 |

示例：`{"mode":"FIVE_TWO","weekend_off":true}`

要点：hard 会禁止周末生产，可能导致无解；一般用 soft 做偏好。

### 4.10 `tooling_exclusivity` — 模具独占

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `available_qty` | int 1 | 同模具同班次最多允许几条线/几个任务同时用 |
| `resource` | enum mold | 【预留】mold/tool |

示例：`{"available_qty":1}`

### 4.11 `pm_block` — 设备 PM 避让

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `handling` | enum avoid | 【预留】avoid/split/suspend；当前统一 avoid |
| `capacity_rate` | ratio 0 | 【预留】PM 期间剩余产能比例 |

要点：PM 班次内该产线所有任务 `x=0`。

### 4.12 `concurrency` — 并行型号上限

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `max_models` | int 3 | 同产线同班次最多几个不同料号 |
| `window` | enum shift | shift；day 暂按 shift |

示例：`{"max_models":3}`

要点：`hard` = 直接禁止超限；`soft` = 超限罚。SY38 常见 `max_models=2`。

### 4.13 `inventory_bound` — 库存/提前生产

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `max_days` | number 5 | 最早只能在交期前 N 天生产（v1 近似库存上限） |
| `min_days` | number 0 | 【预留】最小覆盖天数 |
| `safety_stock` | number 0 | 【预留】安全库存 |

示例：`{"max_days":5}`

要点：当前实现是"不要过早生产"；不是完整的 PSI 库存上下限。hard 可能导致无解，建议 soft。

### 4.14 `customer_priority` — 客户分级

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `tier_weights` | str `STRATEGIC:10,A:3,B:1` | 各客户等级的拖期权重 |
| `due_window_days` | int 1 | 【预留】 |

示例：`{"tier_weights":"STRATEGIC:10,A:3,B:1"}`

要点：只用于软交期；硬交期下不参与。

### 4.15 `time_window` — 受控时间窗

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `allowed_shifts` | str `早班,中班` | 只允许这些班次生产 |
| `allowed_dates` | str 空 | 只允许这些日期（`MM/DD`，逗号分隔）；空=不限制日期 |
| `min_duration_hours` | number 0 | 【预留】最短持续时间 |

示例：

```json
{"allowed_shifts":"早班,中班","allowed_dates":"10/15,10/16"}
```

### 4.16 `co_product` — 联产品 / 共模同步

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `ratio` | str `1:1` | 两个 L/R 料号的产出比例 |
| `group_code` | str 空 | 【预留】 |

示例：`{"ratio":"1:1"}`

要点：当前按料号末尾 `L`/`R` + 同产线配对；`1:1` 表示同班等量，`2:1` 表示前者是后者两倍。

### 4.17 `routing` — 替代产线（**Phase 2，未实现**）

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `max_candidates` | int 2 | 最多几条候选线 |
| `mode` | enum primary | primary/balanced |

当前编译器未实现；需要"任务选线"变量，属于 Phase 2 多线模型。

### 4.18 `freeze` — 冻结期（v1）

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `fence_shifts` | int 3 | 计划开始后的前 N 个班次 |
| `mode` | enum hard | 兼容字段；内核以 nature 为准 |
| `use_baseline` | bool false | true = 冻结窗内锁定基线数量；false = 只禁止排产 |

要点：

- `use_baseline=false`：v1 的“冻结前 N 班禁止生产”；
- `use_baseline=true`：冻结窗内按基线任务 `shift_breakdown` 保持数量，未在基线中的任务为 0；需要传入 `baseline_tasks`。

### 4.19 `labor_capacity` — 人力上限 / 共享班组

| 参数 | 类型/默认 | 含义 |
|---|---|---|
| `available_headcount` | int 0 | 无 CSV 数据时的兜底可用人数；0 = 不用兜底 |
| `crew_source` | enum line | 【预留】line/part |
| `allow_overtime` | bool false | 【预留】 |
| `overtime_max_hours` | number 0 | 【预留】 |
| `setup_technicians_required` | int 0 | 每个 run start 需要的换模技术员数 |
| `setup_technicians_available` | int 0 | 每班可用的换模技术员数；0 = 不启用 |

要点：

- 有 `data/Labor-Availability.csv` 时按 CSV 的站点/日期/班次可用人数；
- 无数据时 `available_headcount=0` → 人力部分自动跳过；
- `setup_technicians_*` 可在无人力 CSV 时独立启用，按班次 run start 数限制换模技术员（HC-02 近似）；
- 共享班组来自 `Crew-Pool.csv`；同池两条线不能同班同时开；
- `hard` 人力不足会无解；建议先 `soft` 观察。

---

## 5. Excel 模板

### 5.1 四个 Sheet

| Sheet | 列 | 说明 |
|---|---|---|
| `Settings` | `key, value` | site / rule_set_id / version / note |
| `Groups` | `group_id, priority, mode` | mode = hard / weighted / lexicographic |
| `Rules` | 见下 | 每条规则一行 |
| `Templates` | 模板说明 | 只读参考，不影响导入 |

### 5.2 Rules 列

| 列 | 必填 | 说明 |
|---|---|---|
| `rule_id` | 是 | 唯一；重复会报错 |
| `template_id` | 是 | 必须是 19 个模板之一 |
| `site` | 否 | 空 = `*`；一般留空，用规则集的 site |
| `line` | 否 | 空 = 全部产线 |
| `stage` | 否 | FG / SF；空 = 全部 |
| `part` | 否 | 料号；空 = 全部 |
| `nature` | 是 | hard / soft |
| `weight` | 是 | 0~1000 |
| `group_id` | 是 | 必须在 Groups 里存在 |
| `priority` | 否 | 默认 0 |
| `inherit` | 否 | replace / add；默认 replace |
| `effective_from` | 否 | ISO 日期；空 = 立即 |
| `effective_to` | 否 | ISO 日期；空 = 长期 |
| `enabled` | 否 | true/false；默认 true |
| `params_json` | 否 | JSON 对象；空 = `{}` |

### 5.3 导入行为

- 逐行校验；错误会定位到单元格，例如 `Rules!G2: nature 必须是 hard/soft`。
- 同一份 Excel 重复导入：payload 相同 → 不产生新版本（幂等）。
- 改一行：生成 v+1；旧版本 archived，可回滚。
- 导入默认生成 draft；勾选"立即激活"或在页面点"激活当前版本"。

---

## 6. 版本与 API

### 6.1 版本规则

- 每个 `(site, rule_set_id)` 一条版本链：v1、v2、v3…
- `active` 只有一个；激活新版本会把旧 active 置为 `archived`。
- 同一 payload 重复保存不产生新版本。
- 旧版本只读；激活旧版本 = 回滚。
- 每次保存有 `note`；diff 会列出新增/删除/修改的规则与字段。

### 6.2 主要 API

| 方法/路径 | 作用 |
|---|---|
| `GET /api/rule-sets?site=WL` | 版本列表 |
| `GET /api/rule-sets/active?site=WL` | 当前 active |
| `GET /api/rule-sets/{id}` | 某版本完整 payload |
| `POST /api/rule-sets/save` | 保存新版本 |
| `POST /api/rule-sets/{id}/activate` | 激活/回滚 |
| `GET /api/rule-sets/diff?from_id=&to_id=` | 两版 diff |
| `POST /api/rule-sets/import` | Excel 导入 |
| `GET /api/rule-sets/template?site=WL` | 下载模板 |
| `GET /api/rule-sets/{id}/export` | 导出 |
| `POST /api/rule-sets/sensitivity` | 敏感度 |
| `POST /api/rule-sets/diagnose` | 无解诊断 |

---

## 7. 常见操作配方

### 7.1 把成品的 95%~100% 改成 95%~105%

1. 打开 `/rule-sets`，确认 active 版本。
2. 找到 `WL_CAP_FG`（`capacity_utilization`，作用域 FG）。
3. 参数 JSON 改成：

```json
{"min_util":0.95,"max_util":1.05,"tail_exempt":true}
```

4. 保存为新版本 → 勾选"立即激活"。
5. 回主界面重新排产。

> 不要改 `WL_CAP_SF`；半成品保持 `max_util=1.0`。也不要只点旧规则页的 95%~105% 单选；规则集模式下它以 `max_util` 为准。

### 7.2 只让 PGLINKAGE 用 105%，其他线 100%

新增一条 `capacity_utilization` 规则：

```json
{
  "id": "WL_CAP_PGL_105",
  "template": "capacity_utilization",
  "scope": {"line": "PGLINKAGE"},
  "nature": "soft",
  "weight": 100,
  "group": "G2_CAPACITY",
  "priority": 20,
  "inherit": "replace",
  "params": {"min_util":0.95,"max_util":1.05,"tail_exempt":true}
}
```

当前阶段规则 `WL_CAP_FG=1.05`、`WL_CAP_SF=1.0` 作为兜底；`scope.line=PGLINKAGE` 的规则 specificity 更高，只覆盖 PGLINKAGE，其他线仍走阶段规则。

### 7.3 降低换型次数

- 提高 `changeover` 权重（相对同组 `concurrency`）；
- 或把 `contiguity` 改成 `hard` + `max_blocks=1`（可能无解，先 soft）；
- 或提高 `concurrency.max_models` 的惩罚权重。

### 7.4 客户分级保供

```json
{"tier_weights":"STRATEGIC:10,A:3,B:1"}
```

只对软交期生效；硬交期下先保证不晚于交期。

### 7.5 冻结前 3 个班次

```json
{"fence_shifts":3,"mode":"hard"}
```

`hard` 会让前 3 班完全不能生产；如果计划需要抢交期，改 `soft`。

### 7.6 导入失败怎么排查

1. 看页面返回的 `errors`，格式是 `Sheet!Cell` + 原因；
2. 常见原因：
   - `nature` 不是 hard/soft；
   - `weight` 超出 0~1000；
   - `template_id` 拼错；
   - `group_id` 在 Groups 里不存在；
   - `params_json` 不是合法 JSON，或参数越界；
   - `rule_id` 重复。
3. 修正后重新导入；同一份内容重复导入不会产生新版本。

### 7.7 无解怎么排查

1. 先看求解日志的「排产口径」：是硬档 UNKNOWN，还是直接 INFEASIBLE；
2. 用页面「无解诊断」或 CLI：

```bash
python -m src.rule_sets.cli diagnose --site WL --budget 20
```

3. 诊断会输出冲突组/冲突规则/规则对；
4. 常见冲突：硬交期 + 硬 95% + MPQ；硬日历（周末停）+ 交期；硬人力 + 多线同时开；硬冻结 + 紧急单。
5. 临时处理：把最可疑的规则改 `soft`，或降低 hard 规则的范围。

### 7.8 权重调了没变化

- 该组只有一条软规则 → 归一化后无差别；
- 规则是 `hard` → 权重被忽略；
- 该组优先级太低，前面组已经决定了计划；
- 计划在时间预算内没有重新搜索到不同解（看 `objective` 和块数是否变化）。

---

## 8. 常见告警与解释

| 告警 | 含义 | 处理 |
|---|---|---|
| `分组 G4_INVENTORY 没有任何规则` | 组是空的 | 删组或补规则；不影响求解 |
| `无可用人力数据，labor_capacity 跳过` | 没有 Labor CSV，且 `available_headcount=0` | 填 CSV 或设兜底人数 |
| `capacity max_util=1 -> 105% mode OFF` | 规则集覆盖了旧 105 开关 | 要 105 就改 `max_util=1.05` |
| `模板 X 已登记但编译器未实现` | 例如 routing | Phase 2；当前不生效 |
| `rule_id 重复` | Excel 两行同 ID | 改唯一 ID |
| `分组不存在` | Rules 里的 group_id 不在 Groups | 补组或改 group |
| `参数非法` | JSON 类型/范围错 | 按模板 Schema 改 |

---

## 9. 术语表

| 术语 | 含义 |
|---|---|
| 规则集（RuleSet） | 一组规则实例 + 分组的版本化集合 |
| 规则实例（RuleInstance） | 一条具体的规则：模板 + 作用域 + 软硬 + 权重 + 参数 |
| 模板 / 算子 | 19 个固定算法之一 |
| 作用域（Scope） | site/line/stage/part 的匹配条件 |
| 继承 / 覆盖 | 具体作用域覆盖宽作用域（replace）或叠加（add） |
| 硬约束 | 必须满足 |
| 软约束 | 进目标，按权重罚 |
| 分组（Group） | 目标层；有 priority 和 mode |
| 字典序 | 软档按组优先级逐层求解并冻结上界；硬档由硬约束保证 |
| 归一化 | 组内权重按比例折算成系数 |
| active | 当前生效版本 |
| diff | 两版规则的增删改对比 |
| TRP | 时间利用率/效率；换型已折入 TRP |
| tail | 每条线最后一个开班，允许低于 95% |
| 拆批 | 单批超过一个班次时拆到相邻班次连续生产 |

---

## 10. 一页速查

```
改 FG 100→105：WL_CAP_FG.params.max_util = 1.05 → 保存并激活；WL_CAP_SF 保持 1.0
改权重：同组内比较；单规则/硬规则改权重无效
加规则：模板 → 作用域 → 分组 → 软硬 → 权重 → 参数
导入：下载模板 → 改 Rules → 导入 → 看 errors → 激活
回滚：版本列表 → 选旧版本 → 激活
无解：看排产口径 → diagnose → 放宽 hard/缩小作用域
```

> 本手册对应 Phase 1 v1；`routing`、树形 100 线虚拟滚动、真实人力数据、确定性停止条件属于后续硬化/Phase 2。
