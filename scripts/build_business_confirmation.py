"""Build the business-facing APS rule confirmation workbook."""
from __future__ import annotations

import os

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

PATH = "APS_业务规则确认清单.xlsx"

NAVY = "FF1E3A5F"
BLUE = "FF0B5394"
LIGHT_BLUE = "FFDDEBF7"
P0_FILL = "FFFCE4D6"
P1_FILL = "FFFFF2CC"
P2_FILL = "FFF2F2F2"
GREEN = "FFE2EFDA"
GREY = "FF7F7F7F"
WHITE = "FFFFFFFF"

THIN = Side(style="thin", color="FFBFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

HEADERS = [
    ("序号", 6),
    ("优先级", 8),
    ("规则编号", 10),
    ("规则名称", 24),
    ("当前系统的做法 / 为什么要确认", 34),
    ("需要业务确认的问题（请逐条回答）", 62),
    ("举例说明（帮助理解）", 46),
    ("可选方案 / 建议默认", 32),
    ("业务回复（请填写）", 34),
    ("确认人 / 日期", 14),
    ("不确认对排产的影响", 34),
    ("需要提供的数据 / 附件", 30),
    ("状态", 10),
]

ROWS = [
    dict(p="P0", id="HC-16", name="同模连产防穿插",
         current="目前只做到“每个工单最多 1 个生产块”的实验开关，且默认关闭；还没有做到“同一模具/同一超级工单开工后，其他模具不能插进来”。Gantt 里 WBC9317 出现多个 48 件小块，就是这个缺口造成的。",
         q="1) “超级工单”的合并范围是什么：同一模具？同一产品族？同一客户？还是计划期内所有未完成订单？\n2) 合批的视距是多少：正式订单 30 天、预测 10 天、波动 3~5 天，是否按这个口径？\n3) 哪些情况允许“穿插”：PM 停机、刚性缺料、换班、试产/首件？\n4) 当 HC-16 与 95% 利用率、客户交期冲突时，谁优先？\n5) 超级工单开始后，允许中间空档多久？（详见对话中的专项解释）",
         example="模具 M01 有 3 个订单：A 100 件、B 150 件、C 80 件，交期分别在周二、周三、周五。若周一早班开始生产 A，是否必须把 A+B+C 全部做完，才能换模具 M02？如果周二早班 M01 缺料 2 小时，这 2 小时能否让 M02 插进来？",
         options="A. 严格同模连续：开工后到批次结束前，只允许 PM/缺料，不允许其他模具；\nB. 允许“相邻班次”内不穿插，跨班可以重新排队；\nC. 只在同一班次内禁止穿插，跨班不限制。\n建议默认：A，但 PM/缺料例外需明确。",
         impact="不确认：系统只能继续用“软罚”尽量减少穿插，无法保证现场“开机后不换模”；但强行全局限块会导致 95% 利用率不可行（已实验验证）。",
         data="请提供 2~3 个真实“同模多订单”的例子：订单号、模具、数量、交期、允许合批的日期范围。"),

    dict(p="P0", id="SC-01", name="D+1 备货时钟与早班追产",
         current="目前只做了“日末库存对次日需求”的软罚，没有“当日早班必须追产”的硬兜底；也没有严格定义 24:00 是自然日还是班次日。",
         q="1) D+1 的“当天 24:00”按自然日 24:00，还是按最后一个班次结束（如次日 07:00）？\n2) “次日发货计划”从哪里来、什么时间锁定？临时加单算不算？\n3) 昨日欠产时，当日早班追产是否允许挤占其他订单？挤占谁？\n4) 早班追产是硬约束（不满足就不可行）还是 99% 高权重软罚？\n5) 如果早班追产也来不及，系统应报警还是自动改交期？",
         example="明日（10/13）需发 500 件，今日（10/12）24:00 库存只有 400 件，缺口 100 件。系统应在 10/12 晚班前补 100 件，还是允许 10/13 早班追产 100 件？如果 10/13 早班已经排了别的急单，是否允许把别的单往后挤？",
         options="A. 24:00=自然日 24:00，早班追产为硬约束；\nB. 24:00=最后一个班次结束，早班追产为 99% 软罚；\nC. 只做 D+1 软罚，不做早班追产。\n建议默认：B，先软后硬。",
         impact="不确认：D+1 只能做“尽量提前”的软约束，无法保证次日发货；早班追产也无法自动触发。",
         data="请提供：发货计划来源/锁定时间、早班时间边界、追产优先级规则。"),

    dict(p="P0", id="HC-08", name="左右件 L/R 共模联产",
         current="Parameters-FG 有“同时生产 Y/N”，但没有 L/R 料号配对表和腔数比例；系统现在不会强制 L 和 R 同步产出。",
         q="1) L/R 料号如何配对？请给出配对表（左件料号、右件料号、共用模具）。\n2) 腔数比例是否恒为 1:1？有没有 1:2、1:4 的模具？\n3) 如果 L 库存已满、R 还缺，是否允许只生产 R？（这会打破 1:1）\n4) 调机废品/试模废品如何在 L/R 之间分摊？\n5) 共模联产时，是否允许 L/R 分别下不同数量的工单？",
         example="模具 M02 每模出 L 1 件、R 1 件。客户只要 R 500 件，L 仓库还有 800 件。系统是开模 500 次（同时多出 500 件 L），还是允许只产 R？如果只产 R，模具是否允许？",
         options="A. 严格 1:1，L/R 必须同量；\nB. 允许按库存差异调整，但需记录 L 的额外库存；\nC. 只在同一订单内 1:1，不同订单可独立。\n建议默认：A，例外需业务确认。",
         impact="不确认：系统无法判断 L/R 是否必须成对，可能排出“只产一侧”的不可执行计划，或把 L 库存压爆。",
         data="L/R 配对表、腔数比例、允许单侧生产的条件。"),

    dict(p="P0", id="HC-11", name="MOQ 与跨班连续",
         current="任务已带 MOQ，但排产仍按 MPQ 整批；MOQ 是“订单量”还是“单次开机量”没有确认，尾数能否低于 MOQ 也没有规则。",
         q="1) MOQ 是“一张订单的总量下限”，还是“每次开机的最小批量”？\n2) MOQ 按产品、模具还是机台维护？\n3) 尾数/急单是否允许低于 MOQ？谁审批？\n4) 跨班连续生产时，中间允许停顿吗？停顿多久需要重新算一次开机？\n5) MOQ 与 MPQ 冲突时，谁优先？",
         example="MOQ=500，客户只要 300 件；是拒绝接单、凑到 500 件入库，还是允许 300 件特批？如果 300 件跨两个班生产，中间停 4 小时，算一次开机还是两次？",
         options="A. MOQ 是订单总量，尾数不允许低于；\nB. MOQ 是单次开机量，尾数可特批；\nC. 按产品/客户分级。\n建议默认：B + 特批。",
         impact="不确认：系统无法判断“300 件急单”能不能排，可能出现该拒的单排了、该特批的单被卡住。",
         data="各产品/客户的 MOQ 值、尾数特批规则。"),

    dict(p="P0", id="HC-14", name="装配防断线 / 超库豁免",
         current="库存策略有 soft/hard 上限，但没有“装配断线风险”的触发变量；无法判断什么时候允许半成品超 max stock。",
         q="1) 什么情况算“装配断线风险”？库存覆盖小于 2 小时？缺一个关键件？L/R 不配套？\n2) 触发后允许超 max stock 多少？+10%？+1 天用量？还是不设上限？\n3) 允许超多久？一个班次、一天，还是直到风险解除？\n4) 谁有权批准豁免？系统自动豁免还是计划员确认？\n5) 与 SC-06（半成品库存上限）冲突时，谁优先？",
         example="半成品 max stock=1,000，因 L/R 不配套，装配线 2 小时后可能停线；此时是否允许把库存做到 1,200？做完 1,200 后，是继续生产还是等装配消耗？",
         options="A. 风险阈值=库存覆盖<2h，允许超 20%，最长 1 天；\nB. 只对 L/R 共模允许无限超库；\nC. 不自动豁免，必须计划员确认。\n建议默认：A。",
         impact="不确认：系统会在“库存上限”和“防断线”之间二选一，可能为了不超库而让装配停线，或为了防断线把库存压爆。",
         data="断线风险定义、允许超库比例/时长、审批人。"),

    dict(p="P0", id="SC-10", name="三班负荷平滑",
         current="目前按“机时”做三班 L1 偏差罚，没有区分“机时/人数/工单数”，也没有定义 2 班制怎么映射。",
         q="1) 这里的 Load 指机时、人数×工时，还是工单数？\n2) WL 是 3 班、SY38 是 2 班，2 班制怎么比较“三班均衡”？\n3) 用方差、极差还是峰值？允许多大差异？\n4) 与 HC-15 人数红线冲突时，谁优先？\n5) 平滑只针对 FG，还是 FG+SF 都算？",
         example="一天总负荷 21 机时：早班 8h、中班 8h、晚班 5h，是否算不均衡？如果早班 7h、中班 7h、晚班 7h，是否理想？SY38 只有两班，是否要求两班各 12h？",
         options="A. Load=机时，目标三班差≤1h；\nB. Load=人数×工时，按人头平滑；\nC. 只做峰值限制，不做方差。\n建议默认：A。",
         impact="不确认：系统可能把“机时均衡”误当“人力均衡”，或对 2 班制作出错误的三班比较。",
         data="Load 口径、2 班制映射、允许差异。"),

    dict(p="P0", id="BS-04", name="逆向拉动 Pull",
         current="当前只是把完成偏好从“越早越好”改成“越接近交期越好”，不是独立逆向求解器；BOM 逆向拉动仍靠 C4 前置期。",
         q="1) Pull 的 Buffer 按客户、运输方式还是仓库分级？\n2) 允许 Push 和 Pull 混合吗？哪些订单必须 Pull、哪些可以 Push？\n3) 半成品有多个父件时，Pull 量如何分配？\n4) 缺料/产能不足时，Pull 是否降级为 Push？\n5) Pull 的锚点是“交期”还是“发货日-运输时间”？",
         example="客户周五 12:00 收货，运输 4h，仓库备货 2h；总装最晚周五 06:00 完工，还是周五 08:00？如果半成品缺料，是否允许先按 Push 排，等料到了再 Pull？",
         options="A. 严格 Pull，所有订单倒排；\nB. 正式单 Pull、预测单 Push；\nC. 只对战略客户 Pull。\n建议默认：B。",
         impact="不确认：系统只能做“晚开工偏好”，无法真正按交期倒排，也无法处理多父件分配。",
         data="Buffer 规则、Pull/Push 订单分类、缺料降级规则。"),

    dict(p="P0", id="BS-05", name="同模合批（SuperOrder）",
         current="已有 CampaignBatch 分组对象，但没有真正合并订单；合批视距、合并键、库存罚豁免范围未确认。",
         q="1) 合并键：同模具、同产品族、同客户，还是同模具+同零件？\n2) 视距：正式订单 30 天、预测 10 天、波动 3~5 天，是否照此执行？\n3) SuperOrder 是否允许跨交期、跨月？\n4) 合批提前完工的库存持有罚，豁免范围多大？\n5) 合批后订单号保留原号，还是生成新的超级工单号？",
         example="同模具 M01 未来 10 天有 3 单：100、150、80 件，交期分别 10/15、10/18、10/25。是否合并成 330 件，用 10/15 的交期生产？后两单的库存罚是否豁免？",
         options="A. 同模+同零件，视距 10 天；\nB. 同模即可，视距 30 天；\nC. 只合并同客户。\n建议默认：A，先不跨月。",
         impact="不确认：系统只能做软合批，无法真正减少换型；BS-06 的截断也没有“超级工单”可截。",
         data="合批键、视距、跨月规则、库存罚豁免范围。"),

    dict(p="P0", id="BS-06", name="交期裕度退避 / 截断",
         current="已有回退、截断建议、自动顺延框架，但“触发阈值、切分粒度、保护谁、顺延到哪”未确认；有延期路径尚未验收。",
         q="1) 触发条件是 Slack<0，还是 Slack 小于安全缓冲（如 4h）？\n2) 截断粒度按班次、整单、还是 MOQ 倍数？\n3) 优先保护哪些订单：战略客户、最早交期、利润最高？\n4) 顺延到下一个计划周期，还是顺延到具体日期？\n5) 截断后允许低于 MOQ 吗？\n6) 自动截断后是否需要计划员确认才下发？",
         example="模具 M01 合批 1,000 件导致急单 B 延期 6h；系统建议把 M01 截成 600 件（≥MOQ 500），剩余 400 件顺延到下周。这个 600/400 的切法由谁定？如果 600 仍导致 B 延期，是否继续截到 500？",
         options="A. 触发 Slack<0，按整单截断，保留≥MOQ；\nB. 触发 Slack<4h，按班次切分；\nC. 只建议，不自动执行。\n建议默认：A + 人工确认。",
         impact="不确认：自动截断可能切错订单、切到低于 MOQ，或把急单保护错。",
         data="触发阈值、保护订单规则、顺延周期、审批流程。"),

    dict(p="P0", id="SC-08", name="顺位前置填补",
         current="目前用“同线完成顺序倒置”的配对罚，未定义自然交期序位 `NaturalDeliveryIndex`，也没有平方序位。",
         q="1) 自然序位按交期、下单时间、客户优先级，还是三者组合？\n2) 允许“前拉”的最大订单数/天数是多少？\n3) 缺口填补必须“紧邻下一单”且连续吗？\n4) 用平方罚还是线性罚？\n5) 跨产线是否算同一序位？",
         example="周一晚班缺 1h，周二早班第一单是 B，周五有一张 30 件小单 C。是否只允许拉 B，不允许拉 C？如果 B 拉了 2h，算不算跳序？",
         options="A. 按交期排序，只允许拉紧邻下一单；\nB. 按交期排序，允许拉未来 2 天内的单；\nC. 按客户优先级+交期。\n建议默认：A。",
         impact="不确认：系统无法判断“拉哪一单算合理”，防跳单罚可能过严或过松。",
         data="自然序位定义、允许前拉窗口、平方/线性。"),

    dict(p="P1", id="SC-09", name="计划平稳度 Jitter",
         current="已有“完成班次偏移 + 产线切换”的软罚，但基准版本、时间粒度、权重和豁免范围未定。",
         q="1) Jitter 的基准是上一版计划、冻结基线，还是已下发计划？\n2) 时间偏移按小时还是班次？\n3) 产线变更罚是否应大于时间偏移？\n4) 哪些任务豁免：插单、缺料、试产、客户加急？\n5) 重排后允许的最大偏移是多少？超过是否必须人工确认？",
         example="一张工单原计划周三早班，重排后到周三晚班（偏移 8h），机台不变；另一张从周三早班改到周四早班（偏移 24h），机台也变了。两者罚分应该如何区分？",
         options="A. 基准=上一版计划，按班次偏移，机台变更罚更重；\nB. 基准=冻结基线，只罚产线变更；\nC. 只做提示，不罚分。\n建议默认：A。",
         impact="不确认：重排可能频繁翻盘，现场退料/换线混乱；或罚得太重导致该调整的不调整。",
         data="基准版本、时间粒度、权重、豁免清单。"),

    dict(p="P0", id="SC-02", name="95%~100% 利用率",
         current="支持 hard/soft、105% 可配，但业务未定 95% 是硬地板还是软目标，尾班/换型班是否豁免。",
         q="1) 95% 是硬约束（低于就不可行）还是软目标？\n2) 尾班、换型班、试产班是否豁免？\n3) 100% 上限是否允许 105%？哪些线/阶段允许？\n4) 分母是日历工时，还是扣 PM 后的工时？\n5) 利用率与交期冲突时谁优先？",
         example="某线某班只排了 7h（87.5%），但订单已经做完；是否允许这个班空着，还是必须凑到 95%？如果凑到 95% 会多生产库存，是否允许？",
         options="A. 95% 硬地板，尾班豁免；\nB. 95% 软目标，允许低于；\nC. 只对 FG 硬，SF 软。\n建议默认：A。",
         impact="不确认：系统会在“凑利用率”和“不超产”之间摇摆，可能出现为凑 95% 而多生产。",
         data="95% 硬/软、尾班豁免、105% 允许线。"),

    dict(p="P0", id="HC-12", name="时间围栏冻结",
         current="有 freeze_shifts/baseline 机制，但没有确认冻结窗口、冻结对象、特批流程。",
         q="1) 冻结窗口按小时、班次还是天？WL/SY38 是否不同？\n2) 冻结对象是已下发、已开工、还是已领料？\n3) 计划员特批如何留痕/回滚？\n4) 紧急插单/缺料能否突破冻结？\n5) 冻结期内的工单允许改数量吗？",
         example="今天 10/12 早班，未来 3 个班次冻结；一个客户急单要插到 10/12 晚班，是否允许？如果允许，原冻结工单顺延，谁批准？",
         options="A. 冻结 3 个班次，已开工工单锁定；\nB. 冻结 1 天，所有已下发锁定；\nC. 只冻结已领料工单。\n建议默认：A。",
         impact="不确认：系统无法判断哪些工单可以动，可能导致现场已开工的工单被重排。",
         data="冻结窗口、对象、特批流程。"),

    dict(p="P1", id="HC-01", name="模具/工装跨设备独占",
         current="已有模具独占约束，但 `available_qty` 未进入容量；换模/装卸是否占用模具时间未定。",
         q="1) 同一模具 `available_qty>1` 时，是否允许多机台并行？\n2) 换模、卸模、装模、搬运时间是否计入模具占用？\n3) FG/SF 共用同一模具时是否共享占用？\n4) 模具维修/保养期间是否按 0 可用？",
         example="模具 M01 有 2 套，机台 A/B 能否同时生产 M01？如果只有 1 套，A 在 08:00-12:00 用，B 最早 12:00 还是 13:00（含卸模 1h）？",
         options="A. available_qty>1 可并行，装卸计入占用；\nB. 只有 1 套，装卸不计；\nC. 按模具类型区分。\n建议默认：A。",
         impact="不确认：可能把只有 1 套的模具排到两台机台，或浪费多套模具的并行能力。",
         data="模具可用套数、装卸时间。"),

    dict(p="P1", id="HC-02", name="调机工容量",
         current="有站点级技术员数，但没有班次级人员日历；换模技术员需求来自 Setup-Matrix。",
         q="1) 调机工按全厂、车间、产线还是技能组共享？\n2) 换模跨班时人数如何分摊？\n3) 夜班/周末/节假日是否减员？\n4) 技术员不足时硬排队，还是允许加班/外协？",
         example="白班只有 2 名调机工，A/B 两台机同时换模，C 是否必须等？如果 C 等 2h，算不算产能损失？",
         options="A. 全厂共享，硬排队；\nB. 按车间共享，可加班；\nC. 按技能组。\n建议默认：A。",
         impact="不确认：可能同时排多个换模，现场无人可调。",
         data="调机工班次人数、技能组、加班规则。"),

    dict(p="P1", id="HC-03", name="PM 避让（含部分降载）",
         current="PM 窗口当前按“产能=0”封锁；capacity_rate 字段存在，但 50% 这类部分降载没有明确表达，系统只会按 0/100 处理。",
         q="1) PM 期间是否允许“降载运行”（如 50% 产能），还是必须完全停机？\n2) 如果允许 50%，是指“PM 窗口内只能排一半时间”，还是“整班 CT 变慢一倍”？\n3) 部分降载时，换型/首件还能做吗？\n4) PM 能否跨班、延期、拆分？谁批准？",
         example="周三 14:00-16:00 PM 2h，capacity_rate=50%。是这 2h 只排 1h 生产，还是周三全天只能排 4h？如果这 2h 还要换模，换模时间算不算在 50% 里？",
         options="A. PM 完全停机（capacity_rate 只允许 0/100）；\nB. 窗口内按 50% 时间可排，换型不占；\nC. 整班产能 50%，换型占。\n建议默认：A，部分降载后续再做。",
         impact="不确认：系统会把 50% 当成 0 或 100，PM 计划与产能不一致。",
         data="PM 窗口、capacity_rate、是否允许换型。"),

    dict(p="P1", id="HC-04", name="换模工时刚性扣减",
         current="换型时间取 Setup-Matrix，同模为 0；矩阵部分仍是 mock，无矩阵边默认 60 分钟。",
         q="1) Setup 是否包含调机、试模、首件检验、清洗？\n2) 换型能否跨班或与生产并行？\n3) 矩阵无 edge 时默认 60min 是否可接受？\n4) 换型是否占用 HC-02 技术员？",
         example="A 产品 10:00 完工，切 B 需换模 1.5h，B 最早 11:30 开工；如果换模跨午休，是否顺延到 13:00？",
         options="A. Setup 含试模/首件，不跨班；\nB. 只含换模，试模另算；\nC. 允许跨班。\n建议默认：A。",
         impact="不确认：换型时间可能少算，导致虚假满负荷。",
         data="真实 Setup-Matrix、试模/首件时间。"),

    dict(p="P1", id="HC-05", name="认证机台清单",
         current="按 Routing 的 certified 字段限制候选线；认证有效期、客户专属、吨位限制未建模。",
         q="1) 认证是否有有效期/客户专属？\n2) 备线是否必须同吨位/同工艺？\n3) 认证清单以 Routing 还是设备台账为准？\n4) 认证失效时允许临时特批吗？",
         example="250T 模具只能上 250T~350T 认证机台；如果 250T 机台认证 12 月到期，11 月还能排吗？",
         options="A. 按 Routing 认证，永久有效；\nB. 认证有有效期；\nC. 客户专属认证。\n建议默认：A。",
         impact="不确认：可能把大模具排到小机台，或认证过期仍排产。",
         data="认证清单/有效期。"),

    dict(p="P1", id="HC-06", name="BOM 齐套开工",
         current="按 ETA 日期+班次限制开工；没有 IQC 检验时长、分批到货、替代料规则。",
         q="1) 到货后是否必须 IQC 检验，检验多久？\n2) 分批到货按首批还是最后一批齐套？\n3) 安全库存/替代料能否代用？\n4) 缺料时允许部分开工吗？",
         example="外购件 A 10:00 到货，IQC 2h，11:30 半成品 B 到；总装最早 11:30 还是 12:00？如果 A 分两批到，首批 50% 能否先开工？",
         options="A. 全部到齐+IQC 后开工；\nB. 首批到货可部分开工；\nC. 替代料可代用。\n建议默认：A。",
         impact="不确认：可能排了“料没到/没检”的工单，现场停线。",
         data="IQC 时长、分批/替代料规则。"),

    dict(p="P1", id="HC-07", name="报废率投料补偿",
         current="apply_scrap 可选，默认关闭；报废率按零件维护，未区分工序/模具。",
         q="1) 报废率按零件、工序、模具还是班次维护？\n2) 报废后补投是否再次计报废？\n3) 取整到件还是到 MPQ？\n4) 试产/新模报废率是否不同？",
         example="需求 1,000 件，报废率 3%，投料 1,031 件；如果这 1,031 件又报废 3%，是否再补 32 件？",
         options="A. 单次补偿，补投不再计报废；\nB. 递归补偿；\nC. 按工序分别算。\n建议默认：A。",
         impact="不确认：投料量可能不足或过度，影响尾数欠料。",
         data="报废率口径/补投规则。"),

    dict(p="P1", id="HC-09", name="半成品前置期",
         current="C4 用“静置+首批转运”推导；Queue/Inspection/Move 未单独建模。",
         q="1) 前置期是否还包含 Queue/Inspection/Move？\n2) 静置从首批完工还是整批完工起算？\n3) 前后工序允许重叠比例？\n4) 多级 BOM 是否逐级累加？",
         example="SF 首批 100 件 10:00 做完，静置 6h，16:00 可转运；总装 16:00 能开工，还是 16:00+搬运 1h=17:00？",
         options="A. 静置+首批，不含 Queue；\nB. 再加 Inspection/Move；\nC. 按工序分别配置。\n建议默认：A。",
         impact="不确认：总装可能排得太早，半成品还没静置好。",
         data="静置起算点、Queue/Inspection/Move 时间。"),

    dict(p="P1", id="HC-10", name="转移批量与工序重叠",
         current="C4 按首批转运批量计算；容器/转运时间未细化。",
         q="1) 转运批量按零件/容器/包装维护？\n2) 首箱是否必须质检放行后才可总装？\n3) TransferTime 是否含搬运/等待？\n4) 后道可提前开工的最大提前量？",
         example="标准箱 100 件，首批 100 件 11:00 做完，搬运 30min，总装 11:30 还是 12:00 开工？",
         options="A. 首批 100+搬运 30min；\nB. 首箱质检后；\nC. 按容器实际批量。\n建议默认：A。",
         impact="不确认：后道可能提前开工等料。",
         data="容器批量、转运/质检时间。"),

    dict(p="P1", id="HC-13", name="试产/首批受控窗口",
         current="Trial-Windows 数据层存在；窗口是否独占产线、超窗能否特批未定。",
         q="1) 窗口按产品、产线、客户还是项目？\n2) 试产批量/最长时长？\n3) 窗口内是否独占产线？\n4) 超窗是否允许特批或顺延？",
         example="某新品试产窗口 10/15 09:00-12:00，是否允许其他量产单插进这 3h？如果试产延长到 13:00，量产单是否顺延？",
         options="A. 窗口独占；\nB. 允许插单；\nC. 只允许同族。\n建议默认：A。",
         impact="不确认：试产可能被量产挤掉，或试产占用过多产能。",
         data="试产窗口/批量/独占规则。"),

    dict(p="P1", id="HC-15", name="班次在岗人数红线",
         current="crew_required 已有人数（FG=3/SF=2），但没有班次在岗人数上限/共享池。",
         q="1) 人数按线、班次、技能组维护？\n2) 请假/借调/加班/实习生如何计入？\n3) 一人多机如何折算？\n4) 超编时停线还是允许加班？",
         example="某班只有 10 人，两条线各需 6 人，是否只能开一条？如果开两条，是否允许加班/借调？",
         options="A. 硬上限，超了停线；\nB. 允许加班；\nC. 共享池可调。\n建议默认：A。",
         impact="不确认：可能排出“没人开线”的计划。",
         data="班次人数表、共享/加班规则。"),

    dict(p="P1", id="SC-03", name="首选高效主产线",
         current="C2-b 可选线，但主线/备线效率口径（CT/TRP/OEE）未定。",
         q="1) 效率用 CT、TRP、OEE 还是综合成本？\n2) 主线/备线定义谁维护？\n3) 备线换型/质量损失是否计入？\n4) 是否允许跨车间？",
         example="产品 P 在主线 CT=10s、备线 CT=15s；是否只按 CT 差罚，还是也考虑备线换型多 30min？",
         options="A. 按 CT 差；\nB. 按 CT+换型；\nC. 按综合成本。\n建议默认：A。",
         impact="不确认：可能为了省换型把单排到低效备线。",
         data="主线/备线效率、换型/质量损失。"),

    dict(p="P1", id="SC-04", name="MPQ 包装模数",
         current="FG 按 MPQ 整批，尾批散箱规则未定；SF 只要求总量。",
         q="1) MPQ 按包装箱/料架/托盘，FG/SF 是否不同？\n2) 是否允许超产到整箱？\n3) 尾批散箱上限 1 箱还是 1 个？\n4) MPQ 与 MOQ 冲突谁优先？",
         example="MPQ=48，需求 500；排 10 箱 480 + 20 散件，还是 11 箱 528？",
         options="A. 允许 1 个散箱；\nB. 必须整箱；\nC. 按客户。\n建议默认：A。",
         impact="不确认：尾数可能多生产或少生产。",
         data="MPQ/尾箱规则。"),

    dict(p="P1", id="SC-05", name="SDST 换型优化",
         current="sequence_setup 可选；矩阵部分 mock，排序偏好未硬性化。",
         q="1) 矩阵来源/维护频率？\n2) 换型是否含清洗/试模/首件？\n3) 颜色/规格排序是硬规则还是偏好？\n4) 跨班继承是否允许中间插其他模具？",
         example="白→黑 30min、黑→白 120min；是否必须按白→灰→黑排序？如果交期要求先黑后白，是否允许逆序？",
         options="A. 严格排序；\nB. 偏好排序；\nC. 只按矩阵最短。\n建议默认：B。",
         impact="不确认：换型顺序可能不符合工艺，或交期被排序规则压死。",
         data="真实换型矩阵、排序规则。"),

    dict(p="P1", id="SC-06", name="半成品库存控制",
         current="有 Inventory-Policy soft/hard；月末窗口、target_days/max_days 实际值未定。",
         q="1) 月末窗口是最后 3 天还是 5 天？\n2) target_days 按零件/族？\n3) max_stock 单位件/天？\n4) 豁免的触发条件与最大超库量？（详见对话专项解释）",
         example="平时允许 5 天库存，月末最后 3 天压到 2 天；某半成品因 L/R 不配套需要多做 1 天，是否豁免？",
         options="A. 月末 3 天，target=2 天；\nB. 月末 5 天，target=3 天；\nC. 按族。\n建议默认：A。",
         impact="不确认：月末可能压库过猛导致断线，或该压没压。",
         data="target/max 天数、月末窗口、豁免规则。"),

    dict(p="P1", id="SC-07", name="成品库存控制",
         current="目标库存来自安全库存；未来 5 天/月末 2 天的动态上限未接。",
         q="1) 5 天是滚动自然日还是发货日？\n2) 月末最后几天？\n3) 是否绝对禁止跨月备货，例外是什么？\n4) 与 SC-01 D+1 如何叠加？",
         example="平时成品库存上限=未来 5 天发货量；月末最后 3 天收紧到 2 天；如果次月产能不足，是否允许提前生产？",
         options="A. 严格 5 天/2 天；\nB. 允许产能瓶颈例外；\nC. 只做软罚。\n建议默认：A。",
         impact="不确认：成品可能压库或跨月盲目备货。",
         data="5 天/2 天口径、跨月例外。"),

    dict(p="P1", id="BS-01", name="四档工作日历",
         current="按 4/7、5/7、6/7 阈值推荐；分母/节假日/升降档时点未定。",
         q="1) 分母用 5×24h 还是扣 PM/节假日？\n2) 阈值按周/月/滚动？\n3) 法定假日/检修如何计入？\n4) 升降档提前多久决策、谁批准？",
         example="周需求 100h，5×24=120h，负荷 83%，是否做五休二？如果下周有国庆，是否按 3 天产能算？",
         options="A. 周滚动，扣节假日；\nB. 月固定；\nC. 只按订单负荷。\n建议默认：A。",
         impact="不确认：可能推荐错误工作制，导致产能不足或过度加班。",
         data="日历/节假日/审批。"),

    dict(p="P1", id="BS-02", name="跨期平准/削峰填谷",
         current="lookahead 30 天、max_pull_forward_days 10 天可配；前置上限/库存/保质期未定。",
         q="1) 视距按订单类型 30/10/3-5 天？\n2) 前置上限受库存/保质期/客户同意限制？\n3) 前置后库存罚/交期如何重算？\n4) 需求波动时是否回退？",
         example="远期缺口 500，近期空闲 300；前置 300 还是 500？如果前置后成品库存超 5 天，是否仍允许？",
         options="A. 按 Cap_idle 前置；\nB. 受库存上限约束；\nC. 客户同意才前置。\n建议默认：A。",
         impact="不确认：可能过度前置，库存/资金占用上升。",
         data="视距/前拉上限/库存约束。"),

    dict(p="P1", id="BS-03", name="超载与交期平移建议",
         current="超载 400 并给建议；Buffer 按小时/天/客户未定。",
         q="1) Buffer 按小时、天还是客户分级？\n2) 新交期是否需商务确认？\n3) 多个超期订单优先级？\n4) 是否允许部分交付/替代料？",
         example="7×24 满载仍超 300 件，建议新交期=完工+2 天；这 2 天是固定 buffer，还是按客户？",
         options="A. 固定 2 天；\nB. 按客户；\nC. 按运输。\n建议默认：A。",
         impact="不确认：超载建议可能不被商务接受。",
         data="Buffer/审批/部分交付规则。"),
]

DATA_ROWS = [
    ("Setup-Matrix 真实换型矩阵", "HC-04 / SC-05", "线、from 模具、to 模具、分钟、技术员数",
     "每行一条；例：PGLINKAGE, MOLD_A, MOLD_B, 90, 2", "工艺/设备", "", "待提供"),
    ("班次人数表", "HC-02 / HC-15 / SC-10", "日期/班次、线、在岗人数、技能组、共享池",
     "每班每线一行；例：2026-11-03, 早班, PGLINKAGE, 6, 总装, 共享", "生产/HR", "", "待提供"),
    ("L/R 配对与腔数", "HC-08", "L 料号、R 料号、模具、腔数比例、允许单侧条件",
     "例：L001, R001, M02, 1:1, 不允许", "工艺/模具", "", "待提供"),
    ("MOQ 清单", "HC-11", "料号、客户、MOQ、单位、尾数豁免条件",
     "例：P001, CUST-A, 500, 件, 急单可特批", "计划/商务", "", "待提供"),
    ("模具可用套数", "HC-01", "模具编号、可用套数、装卸时间",
     "例：M01, 2, 30min", "模具/设备", "", "待提供"),
    ("Inventory-Policy 真实值", "SC-06 / SC-07", "料号、周期、target_days、max_days、safety_stock、max_stock",
     "例：P001, NORMAL, 2, 5, 100, 1000", "物流/财务", "", "待提供"),
    ("试产窗口", "HC-13", "产品、线、开始、结束、批量、是否独占",
     "例：NEW-01, PGLINKAGE, 2026-11-05 09:00, 12:00, 50, 是", "工艺/项目", "", "待提供"),
    ("PM 降载规则", "HC-03", "线、开始、结束、capacity_rate、是否允许换型",
     "例：PGLINKAGE, 2026-11-04 14:00, 16:00, 50%, 否", "设备", "", "待提供"),
    ("主/备线效率", "SC-03", "产品、线、CT、TRP、认证、换型损失",
     "例：P001, PGLINKAGE, 46, 0.71, 是, 30min", "工艺/IE", "", "待提供"),
    ("订单/预测分类", "BS-04 / BS-05", "订单号、类型（正式/预测）、客户、交期、允许合批视距",
     "例：ORD-01, 正式, CUST-A, 2026-11-10, 10 天", "计划/商务", "", "待提供"),
]


def style_header(ws, row, fill=NAVY):
    for col, (name, width) in enumerate(HEADERS, start=1):
        cell = ws.cell(row, col)
        cell.value = name
        cell.font = Font(name="微软雅黑", size=10, bold=True, color=WHITE)
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.row_dimensions[row].height = 36


def build_guide(wb):
    ws = wb.active
    ws.title = "说明"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 100
    ws.merge_cells("A1:B1")
    c = ws["A1"]
    c.value = "APS 业务规则确认清单（物流排产专家版）"
    c.font = Font(name="微软雅黑", size=16, bold=True, color=NAVY)
    c.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 34
    rows = [
        ("用途", "把 Phase B 中需要业务拍板/提供数据的规则集中列出，便于物流排产专家逐条确认；技术实现缺口不在本文件，见 aps_progress_dashboard.md。"),
        ("怎么用", "1) 先看优先级 P0（会阻塞核心算法/试运行）；2) 读“当前做法”和“问题”；3) 在“业务回复”列逐条回答；4) 填“确认人/日期”；5) 状态改为“已确认”。"),
        ("优先级", "P0=不确认就无法硬化/试运行；P1=影响准确性但可先用默认；P2=优化项。"),
        ("状态", "待确认 / 已确认 / 不适用。"),
        ("回答要求", "请用业务语言，不用写代码；能举例最好。若某问不适用，写“不适用”并说明原因。"),
        ("示例", "问题：尾数能否低于 MOQ？回复：急单可低于 MOQ，需计划经理批准，最低不低于 MOQ 的 60%。"),
        ("注意", "一个问题可以拆成多行回答；附件请写文件名/链接。不要改表头，便于汇总。"),
    ]
    r = 3
    for label, text in rows:
        ws.cell(r, 1, label).font = Font(name="微软雅黑", size=10, bold=True)
        ws.cell(r, 1).fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        ws.cell(r, 1).border = BORDER
        ws.cell(r, 2, text).font = Font(name="微软雅黑", size=10)
        ws.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(r, 2).border = BORDER
        ws.row_dimensions[r].height = 56
        r += 1
    ws.freeze_panes = "A3"


def build_main(wb):
    ws = wb.create_sheet("业务确认清单")
    ws.sheet_view.showGridLines = False
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=13)
    title = ws.cell(1, 1, "Phase B 业务规则确认清单（请逐条回复“业务回复”列）")
    title.font = Font(name="微软雅黑", size=15, bold=True, color=NAVY)
    title.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 32
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=13)
    note = ws.cell(2, 1, "P0=阻塞核心算法/试运行；P1=影响准确性；P2=优化项。技术缺口不在本表，见进度文档。")
    note.font = Font(name="微软雅黑", size=9, color=GREY)
    note.alignment = Alignment(vertical="center")
    style_header(ws, 3)
    r = 4
    for idx, item in enumerate(ROWS, start=1):
        values = [
            idx, item["p"], item["id"], item["name"], item["current"], item["q"],
            item["example"], item["options"], "", "", item["impact"], item["data"], "待确认",
        ]
        fill = P0_FILL if item["p"] == "P0" else (P1_FILL if item["p"] == "P1" else P2_FILL)
        for col, value in enumerate(values, start=1):
            cell = ws.cell(r, col, value)
            cell.font = Font(name="微软雅黑", size=9)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = BORDER
            if col in (1, 2, 3, 10, 13):
                cell.alignment = Alignment(wrap_text=True, vertical="top", horizontal="center")
            if col in (2, 13):
                cell.fill = PatternFill("solid", fgColor=fill)
        ws.row_dimensions[r].height = 170 if item["p"] == "P0" else 145
        r += 1
    ws.freeze_panes = "E4"
    ws.auto_filter.ref = f"A3:M{r - 1}"
    dv = DataValidation(type="list", formula1='"待确认,已确认,不适用"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"M4:M{r - 1}")
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def build_data(wb):
    ws = wb.create_sheet("数据提供清单")
    ws.sheet_view.showGridLines = False
    ws.merge_cells("A1:G1")
    c = ws["A1"]
    c.value = "配套数据提供清单（请按格式提供）"
    c.font = Font(name="微软雅黑", size=13, bold=True, color=NAVY)
    ws.row_dimensions[1].height = 28
    headers = ["序号", "数据项", "用途 / 规则", "需要字段", "格式 / 示例", "责任部门", "状态"]
    widths = [6, 26, 22, 46, 46, 16, 12]
    for col, (h, w) in enumerate(zip(headers, widths), start=1):
        cell = ws.cell(2, col, h)
        cell.font = Font(name="微软雅黑", size=9, bold=True, color=WHITE)
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.row_dimensions[2].height = 30
    for idx, row in enumerate(DATA_ROWS, start=1):
        values = [idx, row[0], row[1], row[2], row[3], row[4], row[-1]]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(2 + idx, col, value)
            cell.font = Font(name="微软雅黑", size=9)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = BORDER
        ws.row_dimensions[2 + idx].height = 42
    ws.freeze_panes = "A3"


def main():
    wb = Workbook()
    build_guide(wb)
    build_main(wb)
    build_data(wb)
    wb.save(PATH)
    print("saved", PATH, "rows", len(ROWS))


if __name__ == "__main__":
    main()
