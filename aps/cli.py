from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from collections.abc import Sequence

from aps.calendar.pattern import CalendarMode
from aps.data.importer import import_workbook
from aps.data.issues import IssueLog
from aps.data.sqlite import SqliteRepository
from aps.data.template_v3 import DEMAND_DAYS, DEMAND_START, write_v3_template
from aps.dataset import DataSet
from aps.export.excel import export_production_plan
from aps.model.context import StructureContext
from aps.model.plan_row import extract_line_shifts, extract_plan_rows
from aps.model.report import build_binding_reports
from aps.model.solver import solve_schedule
from aps.schema.registry import REGISTRY, EntitySpec, get_entity

__all__ = [
    "DEFAULT_DB",
    "DEFAULT_SITE",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_VALIDATION",
    "build_parser",
    "main",
]

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_USAGE = 2

DEFAULT_DB = os.path.join("data", "aps.db")
DEFAULT_SITE = "WL"


def _has_site(spec: EntitySpec) -> bool:
    return any(field.name == "site_code" for field in spec.fields)


def _filters(spec: EntitySpec, site: str) -> dict[str, str]:
    return {"site_code": site} if _has_site(spec) else {}


def _require_db(path: str) -> int | None:
    if os.path.exists(path):
        return None
    print(f"数据库不存在：{path}（先运行 import）", file=sys.stderr)
    return EXIT_USAGE


def _cmd_template(args: argparse.Namespace) -> int:
    write_v3_template(args.out, args.site, args.demand_start, args.demand_days)
    print(f"已生成 V3 空白模板：{args.out}")
    print(f"需求日期轴：{args.demand_start.isoformat()} 起 {args.demand_days} 天")
    return EXIT_OK


def _cmd_import(args: argparse.Namespace) -> int:
    try:
        result = import_workbook(args.workbook, args.db, args.site)
    except OSError as exc:
        print(f"无法读取工作簿 {args.workbook}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(result.summary(args.limit))
    return EXIT_OK if result.wrote else EXIT_VALIDATION


def _cmd_verify_template(args: argparse.Namespace) -> int:
    write_v3_template(args.out, args.site, args.demand_start, args.demand_days)
    print(f"已生成 V3 空白模板：{args.out}")
    with tempfile.TemporaryDirectory() as tmp:
        result = import_workbook(args.out, os.path.join(tmp, "verify.db"), args.site)
    print(result.summary(args.limit))
    if result.wrote:
        print("V3 模板自校验通过：结构门零 error，登记表与适配器一致。")
    return EXIT_OK if result.wrote else EXIT_VALIDATION


def _cmd_show(args: argparse.Namespace) -> int:
    blocked = _require_db(args.db)
    if blocked is not None:
        return blocked
    repo = SqliteRepository(args.db)
    try:
        if args.entity is None:
            total = 0
            for name in sorted(REGISTRY):
                count = repo.count(name, **_filters(REGISTRY[name], args.site))
                total += count
                print(f"{name:24s} {count:6d}")
            print(f"共 {len(REGISTRY)} 个实体，{total} 行（站点 {args.site}）")
            return EXIT_OK
        try:
            spec = get_entity(args.entity)
        except KeyError:
            print(f"登记表无实体 {args.entity!r}；可用：{', '.join(sorted(REGISTRY))}",
                  file=sys.stderr)
            return EXIT_USAGE
        rows = repo.list_by(args.entity, **_filters(spec, args.site))[:args.limit]
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return EXIT_OK
    finally:
        repo.close()


def _cmd_dataset(args: argparse.Namespace) -> int:
    blocked = _require_db(args.db)
    if blocked is not None:
        return blocked
    try:
        mode = CalendarMode(args.calendar_mode)
    except ValueError:
        print(f"未知工作制 {args.calendar_mode!r}；可用："
              f"{', '.join(sorted(m.value for m in CalendarMode))}", file=sys.stderr)
        return EXIT_USAGE
    repo = SqliteRepository(args.db)
    try:
        log = IssueLog()
        dataset = DataSet.load(repo, args.site, args.start, args.days,
                               calendar_mode=mode.value, log=log)
    finally:
        repo.close()
    horizon = dataset.horizon
    print(f"站点 {horizon.site}  窗口 {horizon.start_date.isoformat()} ~ "
          f"{horizon.end_date.isoformat()}（{horizon.days} 天）  工作制 {dataset.mode.value}")
    print(f"产线 {len(dataset.lines)}  槽位 {len(dataset.slots)}  "
          f"开放槽位 {sum(len(dataset.open_slots(line.line_code)) for line in dataset.lines)}")
    print(f"任务 {len(dataset.tasks)}  路线 {len(dataset.routes)}  BOM {len(dataset.bom)}")
    print(f"degraded 字段 {len(dataset.degraded_fields)}："
          f"{', '.join(sorted(dataset.degraded_fields)) or '（无）'}")
    for issue in log.issues:
        print(f"[{issue.severity.value.upper()}] {issue.location()} {issue.message}")
    return EXIT_OK


def _cmd_solve(args: argparse.Namespace) -> int:
    if args.workbook and (not os.path.exists(args.db) or args.reload):
        try:
            res = import_workbook(args.workbook, args.db, args.site)
        except OSError as exc:
            print(f"无法读取工作簿 {args.workbook}: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if not res.wrote:
            print(res.summary())
            return EXIT_VALIDATION
        print(f"数据源已更新入库：{args.workbook} → {args.db}")

    blocked = _require_db(args.db)
    if blocked is not None:
        return blocked

    try:
        mode = CalendarMode(args.calendar_mode)
    except ValueError:
        print(f"未知工作制 {args.calendar_mode!r}；可用："
              f"{', '.join(sorted(m.value for m in CalendarMode))}", file=sys.stderr)
        return EXIT_USAGE

    repo = SqliteRepository(args.db)
    try:
        log = IssueLog()
        dataset = DataSet.load(repo, args.site, args.start, args.days,
                               calendar_mode=mode.value, log=log)
    finally:
        repo.close()

    print(f"正在构建 CP-SAT 求解模型（窗口 {args.start.isoformat()} 起 {args.days} 天，任务数 {len(dataset.tasks)}）...")
    ctx = StructureContext.build(dataset)

    print(f"开始执行四层字典序多目标优化（求解时限 {args.timeout} 秒）...")
    solver, solve_summary = solve_schedule(ctx, time_limit_seconds=float(args.timeout))

    if solver is None:
        print(f"\n[ERROR] 求解失败：模型不可行 (INFEASIBLE)（耗时 {solve_summary.wall_time_seconds:.2f}s）", file=sys.stderr)
        print("不可行最小冲突诊断建议：", file=sys.stderr)
        for diag in solve_summary.diagnostics:
            print(f"  • {diag}", file=sys.stderr)
        return EXIT_VALIDATION

    plan_rows = extract_plan_rows(ctx, solver)
    shift_summaries = extract_line_shifts(ctx, solver)
    binding_reports = build_binding_reports(ctx)

    out_file = args.out or f"Production_Plan_{args.site}.xlsx"
    export_production_plan(
        plan_rows=plan_rows,
        shift_summaries=shift_summaries,
        solve_summary=solve_summary,
        binding_reports=binding_reports,
        tasks=dataset.tasks,
        out_path=out_file,
    )

    print(f"\n求解成功：{solve_summary.status}（耗时 {solve_summary.wall_time_seconds:.2f} 秒）")
    print(f"P1 客户交付延误班次: {solve_summary.p1_lateness_shifts}")
    print(f"P2 95%利用率工时缺口: {solve_summary.p2_utilization_gap_ms / 3600000.0:.2f} 小时")
    print(f"P3 任务换型连续块数: {solve_summary.p3_changeover_blocks}")
    print(f"P4 完工槽位偏序和:   {solve_summary.p4_wip_units}")
    print(f"实排生产明细行数:   {len(plan_rows)} 行")
    print(f"排产总表已成功导出: {out_file}")
    return EXIT_OK


def _cmd_web(args: argparse.Namespace) -> int:
    import uvicorn
    from aps.web.server import create_app
    app = create_app()
    display_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    print(f"\n=======================================================")
    print(f"  APS 2.0 智能排产与决策看板服务已启动")
    print(f"  本地浏览器访问: http://{display_host}:{args.port}")
    print(f"  备用访问链接:   http://localhost:{args.port}")
    print(f"=======================================================\n")
    uvicorn.run(app, host=args.host, port=args.port)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aps", description="APS 排产系统（Excel → SQLite → DataSet → CP-SAT Solver → Excel）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    template = sub.add_parser("template", help="生成空白 V3 数据源模板")
    template.add_argument("--out", required=True, help="输出 .xlsx 路径")
    template.add_argument("--site", default=DEFAULT_SITE)
    template.add_argument("--demand-start", type=dt.date.fromisoformat, default=DEMAND_START,
                          help="需求日期轴首日（yyyy-mm-dd）")
    template.add_argument("--demand-days", type=int, default=DEMAND_DAYS)
    template.set_defaults(handler=_cmd_template)

    verify = sub.add_parser(
        "verify-template",
        help="生成 V3 模板并导入一次性临时库，验证登记表与适配器一致（不碰 --db）",
    )
    verify.add_argument("--out", required=True)
    verify.add_argument("--site", default=DEFAULT_SITE)
    verify.add_argument("--demand-start", type=dt.date.fromisoformat, default=DEMAND_START)
    verify.add_argument("--demand-days", type=int, default=DEMAND_DAYS)
    verify.add_argument("--limit", type=int, default=40)
    verify.set_defaults(handler=_cmd_verify_template)

    imp = sub.add_parser("import", help="导入 Excel 工作簿（整站快照替换）")
    imp.add_argument("--workbook", required=True)
    imp.add_argument("--db", default=DEFAULT_DB)
    imp.add_argument("--site", default=DEFAULT_SITE)
    imp.add_argument("--limit", type=int, default=40, help="报告中每级最多显示多少条")
    imp.set_defaults(handler=_cmd_import)

    show = sub.add_parser("show", help="查看库中数据")
    show.add_argument("--db", default=DEFAULT_DB)
    show.add_argument("--site", default=DEFAULT_SITE)
    show.add_argument("--entity", default=None, help="实体名；缺省则打印每实体行数")
    show.add_argument("--limit", type=int, default=20)
    show.set_defaults(handler=_cmd_show)

    dataset = sub.add_parser("dataset", help="从库中装载 DataSet 并打印冻结后的派生量")
    dataset.add_argument("--db", default=DEFAULT_DB)
    dataset.add_argument("--site", default=DEFAULT_SITE)
    dataset.add_argument("--start", required=True, type=dt.date.fromisoformat)
    dataset.add_argument("--days", required=True, type=int)
    dataset.add_argument("--calendar-mode", default="CONTINUOUS")
    dataset.set_defaults(handler=_cmd_dataset)

    solve = sub.add_parser("solve", help="执行 CP-SAT 四层字典序排产并导出 Excel")
    solve.add_argument("--workbook", default=None, help="可选：输入 Excel 工作簿路径；若指定且库不存在则自动入库")
    solve.add_argument("--db", default=DEFAULT_DB, help="SQLite 数据库路径")
    solve.add_argument("--site", default=DEFAULT_SITE, help="站点代码")
    solve.add_argument("--start", required=True, type=dt.date.fromisoformat, help="排产起始日期（YYYY-MM-DD）")
    solve.add_argument("--days", required=True, type=int, help="排产周期天数")
    solve.add_argument("--out", default=None, help="导出 Excel 路径（缺省为 Production_Plan_{site}.xlsx）")
    solve.add_argument("--timeout", type=float, default=60.0, help="求解超时时限（秒）")
    solve.add_argument("--calendar-mode", default="CONTINUOUS", help="四档工作制模式")
    solve.add_argument("--reload", action="store_true", help="强制从 --workbook 重新导入覆盖数据库")
    solve.set_defaults(handler=_cmd_solve)

    web = sub.add_parser("web", help="启动 APS 2.0 Web 决策工作台")
    web.add_argument("--host", default="127.0.0.1", help="监听地址（缺省 127.0.0.1）")
    web.add_argument("--port", type=int, default=8000, help="监听端口（缺省 8000）")
    web.set_defaults(handler=_cmd_web)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
