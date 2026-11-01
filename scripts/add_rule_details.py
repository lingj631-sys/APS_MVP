"""Add a "落地细则" column to Light China APS Rules.xlsx.

Each cell contains what can already be grounded in the rule text/formula plus
the questions that must be confirmed with the logistics scheduling experts.
"""
from __future__ import annotations

import os
import shutil
from copy import copy

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

SRC = "Light China APS Rules.xlsx"
BACKUP = os.path.join("archive", "rules", "Light China APS Rules.pre_landing_details.xlsx")

DETAILS = {
"HC-01": """【可落地】以 Mold/Tool ID 建物理互斥资源；同一 mold_id 的占用区间 [开工, 完工) 不得重叠；占用上限取 Tooling-Mold.available_qty（>1 表示多套并行）。当前系统已有模具独占/同模连产，但 available_qty 尚未进入容量。
【待核对】1) available_qty>1 时是否允许同一模具在多机台并行？2) 换模/卸模/装模/搬运时间是否计入占用区间？3) FG 与 SFIN 共用同一模具时是否共享占用？4) 模具维修/保养期间是否按 0 可用处理？""",

"HC-02": """【可落地】以“换模事件”为容量对象：每个换模事件的 technician 需求取 Setup-Matrix.technicians；同一班次并发换模数 ≤ 当班在岗调机工数。当前有站点级 setup_technicians_available，但缺班次级人员日历。
【待核对】1) 调机工按全厂、车间、产线还是技能组共享？2) 换模跨班时人数如何分摊？3) 夜班/周末/节假日是否减员？4) 技术员不足时是硬排队，还是允许加班/外协？""",

"HC-03": """【可落地】PM 窗口内该线可用产能=0；任务可 PM 前完工、切分到 PM 后、或整体顺延。当前按班次封锁；PM 表有起止日期/时间、停机工时、capacity_rate、handling_strategy，可按策略选择避让/切分/挂起。
【待核对】1) PM 精确到时刻还是班次？2) capacity_rate=50% 这类部分降载如何表达？3) PM 是否允许延期/拆分？4) PM 与 HC-16 连产冲突时，PM 是否绝对优先？""",

"HC-04": """【可落地】换型时间取 Setup-Matrix(line, from_mold, to_mold, minutes)；相邻任务 S_j ≥ C_i + Setup；同模/同族为 0；当前 sequence_setup 可选，支持班内顺序、跨班继承、技术员区间。
【待核对】1) Setup 是否包含调机、试模、首件检验、清洗？2) 换型能否跨班或与生产并行？3) 矩阵无边时的默认 60min 是否可接受？4) 换型是否占用 HC-02 技术员（当前是）？""",

"HC-05": """【可落地】Assign(i,m)=1 仅当 m ∈ Routing-Machines 的 certified 候选线；当前已有 certified 字段与 routing violation 审计，主模型可选择候选线。
【待核对】1) 认证是否有有效期、客户专属、吨位/螺杆规格限制？2) 备线是否必须同吨位/同工艺？3) 认证清单以 Routing 还是设备台账为准？4) 认证失效时是否允许临时特批？""",

"HC-06": """【可落地】S_i ≥ max(BOM 父/子件可用时点、前道完工+转运/检验)；当前按 ETA 日期+班次限制开工，支持 material_ready 硬约束。
【待核对】1) 到货后是否必须 IQC 检验，检验时长多少？2) 分批到货按首批还是最后一批齐套？3) 安全库存/替代料是否可代用？4) 缺料时允许部分开工吗？""",

"HC-07": """【可落地】InputQty = ceil(DemandQty / (1 - ScrapRate))；ScrapRate 来自 Parameters-SFN；当前 apply_scrap 可选，默认关闭。
【待核对】1) 报废率按零件、工序、模具还是班次维护？2) 报废后补投是否再次计报废？3) 取整到件还是到 MPQ？4) 报废率是否随批量/新模/试产变化？""",

"HC-08": """【可落地】同模 L/R 成对产出，OutputL=OutputR=Strokes×CavityPerStroke；当前 Parameters-FG 有 simultaneous_production=Y/N，但缺 L/R 配对关系与腔数比例。
【待核对】1) L/R 料号如何配对（主从/组）？2) 腔数比例是否恒 1:1，是否存在 1:2/1:4？3) 单侧库存满时是否允许只产另一侧（与 HC-14 冲突）？4) 调机废品/报废如何在 L/R 间分摊？""",

"HC-09": """【可落地】SF 首工序最晚开工 = 成品开工 − LeadOffset − SF 加工时间；LeadOffset 由静置+首批转运推导。当前 C4 公式：前置分钟 = 提前准备时间 + min(工单量,转运批量)×CT/TRP，再按单班分钟折班次。
【待核对】1) LeadOffset 是否还含 Queue/Inspection/Move？2) 静置从首批完工还是整批完工起算？3) 前后工序允许重叠比例/安全缓冲？4) 多级 BOM 是否逐级累加？""",

"HC-10": """【可落地】S_FG ≥ S_SF + Setup + 首批批量×CT/TRP + 转运/等待；当前 C4 按首批转运批量（默认 100，可按零件维护）计算，而非整批完工。
【待核对】1) 标准周转容器批量按零件/容器/包装维护？2) 首箱是否必须质检放行后才可总装？3) TransferTime 是否含搬运/等待/检验？4) 后道可提前开工的最大提前量是多少？""",

"HC-11": """【可落地】单次开机批量 Q_i ≥ MOQ；不足时跨班连续；尾数/紧急交付可豁免。当前任务已带 MOQ，但 active 规则仍以 MPQ 为 quantity_source。
【待核对】1) MOQ 是订单量还是单次开机量？2) MOQ 按产品/模具/机台维护？3) 尾数豁免的判定条件与审批人？4) 跨班连续是否允许中间停顿/换班？""",

"HC-12": """【可落地】S_i < Now + FreezeFence 的工单锁定 start/line/sequence，重排不得自动挪移；当前 freeze_shifts/baseline 可选，可按基线锁定前 N 个班次。
【待核对】1) 冻结窗口按小时、班次还是天，WL/SY38 是否不同？2) 冻结对象是已下发、已开工还是已领料？3) 计划员特批如何留痕/回滚？4) 紧急插单/缺料能否突破冻结？""",

"HC-13": """【可落地】试产/首件任务的 [S_i, C_i] 必须完全落在 Trial-Windows 内；当前有 Trial-Windows.csv 数据层，窗口内可独占产线。
【待核对】1) 窗口按产品、产线、客户还是项目？2) 试产批量/最长时长限制？3) 窗口内是否独占产线（禁止其他工单）？4) 超窗是否允许特批或顺延？""",

"HC-14": """【可落地】当装配断线风险 Risk=1 时，允许 SF 库存突破 max_stock，超库罚分豁免；当前库存策略可 soft/hard，但缺“断线风险”触发变量。
【待核对】1) 断线风险的触发阈值（库存覆盖小时/天、欠料件数）？2) 允许超 max 多少、持续多久？3) 是否只对 L/R 共模生效？4) 与 SC-06/SC-07 库存罚的优先级？""",

"HC-15": """【可落地】每班开启产线所需 headcount 之和 ≤ 当班可用人数；当前 crew_required 来自 Routing 人数，已有 FG=3、SF=2 模拟值；缺 Line-Staffing/Labor-Availability 班次上限。
【待核对】1) 人数按线、班次、技能组维护，是否共享？2) 请假/借调/加班/实习生如何计入？3) 一人多机如何折算？4) 超编时停线还是允许加班？""",

"HC-16": """【可落地】以 mold_id + product_family（或 campaign_group）定义超级工单；窗口内只允许同 campaign 任务，PM/刚性缺料例外；当前仅 partial：contiguity 模板/max_blocks 未 active，campaign 只是班次内软分组，尚无跨班硬防穿插。
【待核对】1) 超级工单范围：同模、同族、同客户还是全部未完成订单？2) 合批视距：正式单 30 天/预测 10 天/波动 3-5 天是否适用？3) 允许例外：PM、缺料、换班、质量首件？4) 与 95% 利用率、交期冲突谁优先？5) 窗口内允许空档多久？6) 截断尾数是否允许低于 MOQ（BS-06）？""",

"SC-01": """【可落地】D+1 备货：当日 24:00 结余库存 ≥ 次日发货量；缺口罚 90%；昨日欠产当日早班补产，罚 99%。当前只有交期/lookahead，没有 D+1 库存时钟与早班追产。
【待核对】1) 24:00 是自然日还是班次日？2) 次日发货计划来源与锁定时间？3) 早班追产是否允许挤占其他订单/加班？4) 99% 是软罚还是硬约束？5) 与 SC-07 成品库存上限如何叠加？""",

"SC-02": """【可落地】Util = 排产工时 / 可用工时；目标 [95%,100%]，低于 95% 与高于 100% 双边罚；当前支持 hard/soft，105% 上限可选。
【待核对】1) 95% 是硬地板还是软目标？2) 尾班/换型班/试产班是否豁免？3) 100% 上限是否允许 105%，哪些线/阶段允许？4) 分母是日历工时还是扣 PM 后工时？""",

"SC-03": """【可落地】优先选择 CT/TRP 更优的主线；排入备线按效率损失计罚；当前 C2-b 已有上游分配 + 主模型 Assign，线路决策可审计。
【待核对】1) 效率用 CT、TRP、OEE 还是综合成本？2) 主线/备线的定义与优先级由谁维护？3) 备线换型/质量损失是否计入？4) 是否允许跨车间/跨工厂？""",

"SC-04": """【可落地】排产批量尽量对齐 MPQ（包装模数）；尾批最多允许 1 个散箱，不强行凑整超产；当前 FG 已按 MPQ 整批，SF 按总量/起投。
【待核对】1) MPQ 按包装箱、料架还是托盘，FG/SF 是否不同？2) 是否允许超产到整箱，超产上限？3) 尾批散箱上限是 1 箱还是 1 个？4) MPQ 与 MOQ 冲突时谁优先？""",

"SC-05": """【可落地】序列相关换型矩阵 SDST；同模/同族对角线为 0；跨班继承前班最后模具；排序偏好“同系列→规格由大到小→颜色由浅到深”；当前 sequence_setup 可选。
【待核对】1) 矩阵来源/维护频率/版本？2) Setup 是否含清洗、试模、首件？3) 颜色/规格排序是硬规则还是偏好？4) 跨班继承是否允许中间插入其他模具？""",

"SC-06": """【可落地】平时超 max_stock 罚 40%；月末最后 3-5 天收敛到 target_days，罚 80%；HC-14 断线豁免。当前 Inventory-Policy 支持 soft/hard，分时段策略可用。
【待核对】1) 月末窗口是最后 3 天还是 5 天，按自然日还是工作日？2) target_days 按零件/族维护？3) max_stock 单位是件还是天？4) 豁免的触发条件与最大超库量？""",

"SC-07": """【可落地】平时成品库存上限 = 未来 5 天发货量；月末上限 = 2 天日均发货量；超罚 60%/85%；当前目标库存来自安全库存，尚无 5 天/2 天动态上限。
【待核对】1) 5 天是滚动自然日还是发货日？2) 月末最后几天？3) 是否绝对禁止跨月备货，例外是什么？4) 与 SC-01 的 D+1 备货如何叠加？""",

"SC-08": """【可落地】按自然交期排序得到 NaturalDeliveryIndex，按实际排产顺序得到 ScheduleOrderIndex，偏离平方罚；当前有 due/slack，但没有 order index 与平方罚。
【待核对】1) NaturalDeliveryIndex 按交期、下单顺序还是客户优先级？2) 允许前拉的订单数/天数上限？3) 缺口填补是否必须“紧邻下一单”且连续？4) 用平方罚还是线性罚？""",

"SC-09": """【可落地】|Start_new − Start_base|/24h + 机台变更罚；当前实现为完成班次偏移 + 产线切换，尚未做 start 偏移平方项；权重归 G3，不压交付/产能。
【待核对】1) 基准是上一版计划、冻结基线还是已下发计划？2) 时间偏移按小时还是班次？3) 机台变更罚是否应大于时间偏移？4) 哪些任务豁免（插单、缺料、试产）？""",

"SC-10": """【可落地】按日统计早/中/晚班负荷，最小化三班方差；当前有人数/班次负荷，但没有平滑罚。
【待核对】1) Load 用机时、人数还是工单数？2) 2 班制工厂如何映射三班？3) 用方差、极差还是峰值？4) 与 HC-15 人数红线冲突时谁优先？""",

"BS-01": """【可落地】周负荷率 ρ = 周需求机时 / 5×24h 产能；ρ<0.65 做四休三，0.65-0.95 做五休二，>0.95 做六休一/做七无休；当前四档按 4/7、5/7、6/7 阈值推荐。
【待核对】1) 分母用 5×24h 还是扣除 PM/法定假日后的实际产能？2) 阈值按周、月还是滚动窗口？3) 法定假日/检修如何计入？4) 升降档提前多久决策、是否需审批？""",

"BS-02": """【可落地】远期缺口 ΔQ = Demand_far − Cap_far > 0 且近期有空闲 Cap_idle 时，前置量 = min(ΔQ, Cap_idle)；当前有 lookahead/max_pull_forward_days。
【待核对】1) 视距按订单类型（30/10/3-5 天）？2) 前置上限是否受库存/保质期/客户同意限制？3) 前置后库存罚/交期如何重算？4) 需求波动时是否回退？""",

"BS-03": """【可落地】7×24 满载仍 C_i > Due_i 时输出不可行集合；建议新交期 = C_i + Buffer；当前超载返回 400/瓶颈与建议。
【待核对】1) Buffer 按小时、天还是客户分级？2) 新交期是否需商务确认？3) 多个超期订单的优先级排序？4) 是否允许部分交付/替代料？""",

"BS-04": """【可落地】C_FG ≤ Due − Buffer；C_SF ≤ S_FG − TransferTime；S_SF = C_SF − LeadTime；当前以顺排+lookahead 为主，独立 Pull 未做。
【待核对】1) Buffer 按客户/运输/仓库？2) 逆向拉动是否允许与顺排混合（hybrid）？3) 半成品多父件如何分配？4) 缺料/产能不足时 Pull 是否降级 Push？""",

"BS-05": """【可落地】按订单性质视距（正式 ≤30 天、高准度预测 ≤10 天、波动预测 3-5 天）把同模需求合并为 SuperOrder；合并期库存持有罚=0；当前 Campaign 是软合批，无 SuperOrder 对象。
【待核对】1) 视距按客户/订单类型/预测等级？2) 合并键是同模、同族还是同客户？3) SuperOrder 是否跨交期、跨月？4) 库存罚豁免的范围与时长？""",

"BS-06": """【可落地】监控 Slack_j = Due_j − C_j；若 SuperOrder 导致其他订单 Slack<0，切分 SuperOrder，保留 Q_keep ≥ MOQ，余量顺延；当前无自动仲裁。
【待核对】1) 触发阈值是 Slack<0 还是小于安全缓冲？2) 切分粒度按班次、整批还是 MOQ 倍数？3) 优先保护哪些订单（客户等级/交期/利润）？4) 切分后是否允许再次合并？""",
}


def main() -> None:
    if not os.path.isfile(SRC):
        raise SystemExit(f"missing {SRC}")
    os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
    if not os.path.exists(BACKUP):
        shutil.copy2(SRC, BACKUP)

    wb = openpyxl.load_workbook(SRC)
    ws = wb.worksheets[0]

    # extend section header merges across the new column
    for rng in ("A19:H19", "A32:H32"):
        if rng in [str(r) for r in ws.merged_cells.ranges]:
            ws.unmerge_cells(rng)
    ws.merge_cells("A19:I19")
    ws.merge_cells("A32:I32")

    header = ws.cell(1, 9)
    header.value = "落地细则（可落地口径 + 待核对问题）"
    header.font = copy(ws.cell(1, 8).font)
    header.fill = copy(ws.cell(1, 8).fill)
    header.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    header.border = copy(ws.cell(1, 8).border)
    ws.column_dimensions["I"].width = 82

    # section headers: keep the new column visually part of the section
    for row, fill in ((20, "FF147D47"), (33, "FF59598C")):
        cell = ws.cell(row, 9)
        cell.value = "落地细则"
        cell.font = copy(ws.cell(row, 8).font)
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cell.border = copy(ws.cell(row, 8).border)

    filled = 0
    for r in range(2, ws.max_row + 1):
        rid = ws.cell(r, 1).value
        if not rid or not isinstance(rid, str) or "-" not in rid:
            continue
        if rid not in DETAILS:
            raise SystemExit(f"missing details for {rid}")
        cell = ws.cell(r, 9)
        cell.value = DETAILS[rid]
        cell.font = copy(ws.cell(r, 8).font)
        cell.fill = PatternFill("solid", fgColor="FFFFFFFF")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        cell.border = copy(ws.cell(r, 8).border)
        ws.row_dimensions[r].height = max(180, float(ws.row_dimensions[r].height or 0))
        filled += 1

    for row, fill in ((19, "FF0D6439"), (32, "FF3F3F72")):
        ws.cell(row, 9).fill = PatternFill("solid", fgColor=fill)

    wb.save(SRC)
    print(f"added landing details: {filled} rules; header I1; backup={BACKUP}")


if __name__ == "__main__":
    main()
