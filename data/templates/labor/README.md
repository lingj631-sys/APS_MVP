# 人力主数据模板（L3）

把本目录的三个 CSV 复制到 `data/` 根目录后，`labor_capacity` 算子才会读取：

| 文件 | 列 | 说明 |
|---|---|---|
| `Labor-Availability.csv` | site, date, shift, available_headcount | 站点/日期/班次可用人数；date 用 `2026-10-12`，shift 用 `早班/中班/晚班` |
| `Line-Staffing.csv` | site, line, part_no, crew_required, changeover_extra, skill | 产线/料号定员；`part_no=*` 表示该线默认人数 |
| `Crew-Pool.csv` | site, pool_id, line | 共享班组；同一 pool 的多条线不能同班同时开 |

校验规则：

- 人数必须为正整数/正数；负数、0、非数字会报错；
- 同一 `(site, date, shift)` 不能重复；
- 同一 `(site, line, part_no)` 不能重复；
- 同一产线不能同时属于两个 pool。

没有文件时 `labor_capacity` 自动跳过，不影响现有计划；规则里的 `available_headcount` 可作为兜底人数。
