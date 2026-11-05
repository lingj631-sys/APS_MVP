from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field as dc_field
from typing import Any

from ortools.sat.python import cp_model

from aps.calendar.geometry import MS_PER_HOUR
from aps.dataset import DataSet, LineGeometry, RouteOption, Task

__all__ = ["StructureContext"]


@dataclass
class StructureContext:
    dataset: DataSet
    model: cp_model.CpModel
    horizon_days: int
    task_ids: list[str]
    tasks_by_id: dict[str, Task]

    assign: dict[tuple[str, str], cp_model.IntVar] = dc_field(default_factory=dict)
    x: dict[tuple[str, str, int], cp_model.IntVar] = dc_field(default_factory=dict)
    x_by_task: dict[str, list[cp_model.IntVar]] = dc_field(default_factory=lambda: defaultdict(list))
    x_by_task_line: dict[tuple[str, str], list[tuple[int, cp_model.IntVar]]] = dc_field(default_factory=lambda: defaultdict(list))

    active: dict[tuple[str, str, int], cp_model.IntVar] = dc_field(default_factory=dict)
    started: dict[tuple[str, str, int], cp_model.IntVar] = dc_field(default_factory=dict)
    cum: dict[tuple[str, str, int], cp_model.IntVar] = dc_field(default_factory=dict)
    end_slot: dict[str, cp_model.IntVar] = dc_field(default_factory=dict)
    late_shifts: dict[str, cp_model.IntVar] = dc_field(default_factory=dict)
    load_ms: dict[tuple[str, int], cp_model.IntVar] = dc_field(default_factory=dict)
    is_open: dict[tuple[str, int], cp_model.IntVar] = dc_field(default_factory=dict)
    is_tail: dict[tuple[str, int], cp_model.IntVar] = dc_field(default_factory=dict)
    block_start: dict[tuple[str, str, int], cp_model.IntVar] = dc_field(default_factory=dict)
    under_util: dict[tuple[str, int], cp_model.IntVar] = dc_field(default_factory=dict)
    assumptions: dict[str, cp_model.IntVar] = dc_field(default_factory=dict)
    assumption_names: dict[int, str] = dc_field(default_factory=dict)

    @classmethod
    def build(cls, dataset: DataSet) -> "StructureContext":
        model = cp_model.CpModel()
        horizon_days = dataset.horizon.days

        task_ids: list[str] = []
        tasks_by_id: dict[str, Task] = {}
        for idx, task in enumerate(dataset.tasks):
            t_id = f"{task.stage}_{task.pn}_{task.due_date.isoformat()}_{idx}"
            task_ids.append(t_id)
            tasks_by_id[t_id] = task

        ctx = cls(
            dataset=dataset,
            model=model,
            horizon_days=horizon_days,
            task_ids=task_ids,
            tasks_by_id=tasks_by_id,
        )

        ctx._create_variables()
        return ctx

    def _create_variables(self) -> None:
        model = self.model
        dataset = self.dataset
        lines = {l.line_code: l for l in dataset.lines}

        # 1. 产线班次基础变量 (load_ms, is_open, is_tail)
        for line in dataset.lines:
            line_slots = dataset.slot_count(line.line_code)
            cap_ms = int(round(line.shift_hours * MS_PER_HOUR))
            max_load = int(cap_ms * 1.5)
            for s in range(line_slots):
                self.load_ms[(line.line_code, s)] = model.NewIntVar(
                    0, max_load, f"load.{line.line_code}.{s}"
                )
                self.is_open[(line.line_code, s)] = model.NewBoolVar(
                    f"open.{line.line_code}.{s}"
                )
                self.is_tail[(line.line_code, s)] = model.NewBoolVar(
                    f"tail.{line.line_code}.{s}"
                )

        # 2. 任务级与产线分配变量
        max_total_slots = max((dataset.slot_count(l.line_code) for l in dataset.lines), default=30)
        for t_id in self.task_ids:
            task = self.tasks_by_id[t_id]
            self.end_slot[t_id] = model.NewIntVar(0, max_total_slots - 1, f"end.{t_id}")
            self.late_shifts[t_id] = model.NewIntVar(0, max_total_slots, f"late.{t_id}")

            cert_lines = dataset.certified_lines(task.pn)
            if not cert_lines:
                cert_lines = tuple(
                    r.line_code for r in dataset.routes if r.pn == task.pn and r.line_code in lines
                )

            for l_code in cert_lines:
                line = lines[l_code]
                route = dataset.route(task.pn, l_code)
                per_shift_cap = route.per_shift_cap if route and route.per_shift_cap > 0 else task.net_requirement
                upper_bound = min(task.net_requirement, per_shift_cap)

                assign_var = model.NewBoolVar(f"assign.{t_id}.{l_code}")
                self.assign[(t_id, l_code)] = assign_var

                line_slots = dataset.slot_count(l_code)
                for s in range(line_slots):
                    x_var = model.NewIntVar(0, upper_bound, f"x.{t_id}.{l_code}.{s}")
                    act_var = model.NewBoolVar(f"act.{t_id}.{l_code}.{s}")
                    start_var = model.NewBoolVar(f"start.{t_id}.{l_code}.{s}")
                    block_var = model.NewBoolVar(f"block.{t_id}.{l_code}.{s}")
                    cum_var = model.NewIntVar(0, task.net_requirement, f"cum.{t_id}.{l_code}.{s}")

                    self.x[(t_id, l_code, s)] = x_var
                    self.x_by_task[t_id].append(x_var)
                    self.x_by_task_line[(t_id, l_code)].append((s, x_var))

                    self.active[(t_id, l_code, s)] = act_var
                    self.started[(t_id, l_code, s)] = start_var
                    self.block_start[(t_id, l_code, s)] = block_var
                    self.cum[(t_id, l_code, s)] = cum_var

                    model.Add(x_var <= upper_bound * act_var)
                    model.Add(x_var >= act_var)
                    model.Add(act_var <= assign_var)

                    if s == 0:
                        model.Add(start_var >= act_var)
                        model.Add(cum_var == x_var)
                    else:
                        prev_act = self.active[(t_id, l_code, s - 1)]
                        model.Add(start_var >= act_var - prev_act)
                        prev_cum = self.cum[(t_id, l_code, s - 1)]
                        model.Add(cum_var == prev_cum + x_var)

                    model.Add(block_var >= start_var)
