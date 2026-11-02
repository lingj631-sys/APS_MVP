# 主数据模拟值说明（2026-10-31）

> 目的：新版 15 表框架中有若干算法必需字段为空，先用**可解释的模拟值**补齐，保证排产链路完整；后续可用真实值直接覆盖。
> 原始文件备份：`archive/data/APS_DataSource_Template.pre_simulation.xlsx`
> 生成脚本：`scripts/fill_simulated_masterdata.py`（默认拒绝覆盖；重建需加 `--force`）

---

## 0. 提前准备时间（分钟）的算法语义

业务口径：`工序提前期 → 提前准备时间（分钟）` 替代原来的 `生产提前期 (days)`，取值 **45min ~ 12h**：

- 部分半成品无需静置，可在 **45min** 内极限转运；
- 多数需要生产后静置，**一般不超过半天（4~12h）**，由业务按产品填写。

C4 计算 SF→FG 前置期时：

```text
前置分钟 = 提前准备时间（静置/等待）
         + min(工单数量, 转运批量) × CT / TRP      # 首批 100 件全部做完才允许转运
前置班次 = ceil(前置分钟 / 单班分钟)
```

即“静置 6h + 生产 100 件”的时间，作为该半成品相对成品开工的**最晚启动提前量**；不是把整张工单全部做完才算，而是首批转运批量完成并静置即可。C4 软约束以**成品工单开始班次**为比较基准（parent 开始 ≈ 完成班次 − 占用班次）。

---

## 1. 修改清单

| # | 工作表 | 字段 / 范围 | 模拟值 | 依据 |
|---|---|---|---|---|
| 1 | 产线基础数据 | A2:D4（原 17 行） | `PGLINKAGE 0.71 8 3`、`轴套6 0.85 8 3`、`轴套7 0.85 8 3` | PGLINKAGE / 轴套6 来自旧 `dataSource.xlsx`；轴套7 旧表无产品，按同车间同班次假设与轴套6 一致 |
| 2 | Routing-Machines | 人数 | FG 行 = 3，SF 行 = 2 | FG 取自旧 FG `Headcount=3`；SF 旧表为空，按冲压线 2 人/班模拟 |
| 3 | 工序提前期 | 提前准备时间（分钟） | FG 全部 45；SF 按产品族：G38=45、CX11=240、CM2E/KP31=300、930B/L743=360、LEAP=420、DX11/E371=480 | 覆盖“45min 极限转运”与“4~12h 静置”两种场景；业务可按实际改 |
| 4 | Parameters-SFN | 最大库存 (件) | 取自 Inventory-Policy 同料号 NORMAL `最大库存` | 避免两处口径不一致 |
| 5 | Parameters-FG | 产品族名 | 按 `data/master/Campaign-Families.csv` 的 `Product Family` 回填 17/17 | 复用既有 mock 分组 |
| 6 | Parameters-FG | 模具编号 | 按 Campaign-Families 回填 17/17（`MOLD_WL_LINK_01/02`） | 原 AA/BB 非法；FG 总装夹具按 LINK 系列 |
| 7 | Parameters-SFN | 产品族名 | 按 Campaign-Families 回填 22/22 | 同上 |
| 8 | Parameters-SFN | 模具编号 | 仅 Tooling-Mold『适用零件料号』覆盖的 8 个 SF 回填（AXLE_01~04），其余 12 个留空 | 没有模具主数据的零件不臆造 |
| 9 | Tooling-Mold | 适用零件料号 | LINK_01/LINK_02 扩展到全部 FG 零件 | 与第 6 项保持一致 |
| 10 | Routing-Machines | 产品族名 | 按 Campaign-Families 回填 39/39 | 为后续族级换模/合批预留 |
| 11 | Routing-Machines | 单班定额产能 | 清空原 P10 的文本批注 | 数值列不应混入文本；该列建议后续删除 |
| 12 | 关键物料到货 | 预计入库时间 | 按班次补 `早班 08:00`、`中班 14:00`、`晚班 22:00` | 与现有班次边界一致 |
| 13 | Parameters-SFN / Routing / Operation-LeadTime / Inventory-Policy | 新增 `W268001`、`W268002` | CX11 DS/PS 轴套分总成；MPQ=100、MOQ=100、报废 0.03；CT=25s、人数 2；静置 240min、转运批量 100；库存 NORMAL/MONTH_END 安全库存 50/25 | WB80510 的 BOM 引用了这两个半成品，原逻辑会“跳过齐套” |
| 14 | 填写说明 | 本工厂行数 | SFN 20→22、Routing 37→39、Inventory 74→78、OpLead 37→39 | 与新增行保持一致 |

> 注：不再新增 `生产提前期 (days)` 列；该列仅保留为旧版兼容字段（schema 中标记 deprecated）。

---

## 2. 未模拟的字段及原因

| 字段 | 处理 | 原因 |
|---|---|---|
| FG/SFN `特殊班次` | 留空 | 说明页提到“温岭特殊班次对应规则 HC09”，但语义（哪种产品、哪个班次模板）未定义；填错会误触发规则 |
| FG `同时生产` | 保留原 4 行 `Y` | 左右件共模配对关系未给出，不臆造 |
| PM `开始时间/结束时间` | 留空 | 求解器按“日期 + 停机工时”推导封锁班次；填具体时刻反而会缩短 48h 大修窗口 |
| Customer-Priority `交期浮动容差/外协策略` | 保留原文本 | 目前仅展示，未进入求解器；若要数值化需先定义口径 |
| SFN 12 个无模具零件 | 留空 | Tooling-Mold 没有对应模具，属真实缺口；需要业务补模具主数据后再填 |

---

## 3. 回滚与重建

回滚：

```powershell
Copy-Item archive\data\APS_DataSource_Template.pre_simulation.xlsx APS_DataSource_Template.xlsx -Force
```

重建全部模拟值：

```powershell
python scripts/fill_simulated_masterdata.py --force
```

默认不带 `--force` 时会拒绝执行，避免覆盖你后续手工修改的模板。

---

## 4. 模拟后的量化变化

| 指标 | 模拟前 | 模拟后 |
|---|---:|---:|
| FG 任务 crew=0 | 134/134 | **0/146** |
| 有模具号的任务 | 41/134 | **60/146**（FG 全量 + 8 个 SF 零件） |
| 计划告警 | 15（含齐套跳过） | **0** |
| 10 天 FG 计划量 | 40,332 | 40,332（+408 来自安全库存口径） |
| 10 天 SF 计划量 | 74,933（跳 W268） | **77,717**（补齐 CX11 半成品） |
| C4 前置期 | SFN 列缺失，约束空转 | 由静置 45~480min + 首批 100 件加工时间推导 |

> 所有模拟值只保证算法链路完整，不代表真实业务定额；上线前请用真实数据覆盖。


---

## 2026-11-02 二次补数（新模板对齐）

> 模拟文件：APS_DataSource_Template_simulated.xlsx
> 原始模板：APS_DataSource_Template.xlsx（保留给测试/对照）
> 脚本：scripts/simulate_missing_masterdata.py、scripts/fill_missing_bom_params.py、scripts/fill_missing_routing.py

| 区域 | 原缺口 | 本次模拟 |
|---|---|---|
| Parameters-FG | 特殊班次 17 空、同时生产 13 空 | 特殊班次=常规；同时生产=N（已有 4 个 Y 保留） |
| Parameters-SFN | 模具 14 空、特殊班次 22 空、同时生产 22 空 | 按产品族补 AXLE_05~09；特殊=常规；同时生产=Y（L/R 假设） |
| BOM List | 28 个母件/8 个子件不在参数表；描述空 | 补 14 个 FG、8 个 SF 模拟参数；描述从参数表回填，已无空值 |
| Routing-Machines | 单班定额产能 39 空；新增模拟件无路线 | 按 8h×3600/CT×TRP 计算；补 22 条模拟路线（FG 总装/轴套6） |
| Tooling-Mold | 缺 AXLE_05~09 | 补 5 套模具，可用套数=1 |
| Preventive-Maintenance | 开始/结束时间空 | 08:00–16:00；48h 大修结束日期改 10/15 |

补数后 load_workbook_snapshot + uild_requirement_plan：

- 65 订单 / 31 FG / 30 SF / 28 BOM / 146 任务；
- invalid_orders = 0；
- warnings = 0。

说明：同时生产=Y 是按 SF 的 DS/PS 成对关系模拟，需业务确认真实配对；模拟路线/CT/人数也需真实数据覆盖。

**C4 口径已按 2026-11 业务答复改为“整批完工后起算静置”**，上面旧章节的“首批转运起算”仅保留为历史说明。
