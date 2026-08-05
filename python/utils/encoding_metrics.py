from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, TextIO


def seq_amo_aux(size: int) -> int:
    return 0 if size <= 2 else size - 1


def seq_amo_clauses(size: int) -> int:
    if size <= 1:
        return 0
    if size == 2:
        return 1
    return 3 * size - 4


def seq_exactly_one_clauses(size: int) -> int:
    if size <= 0:
        raise ValueError("Exactly-one requires at least one literal")
    return 1 + seq_amo_clauses(size)


def cutoff_clause_count(axis_size: int, cutoff: int) -> int:
    effective = min(max(cutoff, 0), axis_size - 1)
    if effective == 0:
        return axis_size * (axis_size - 1)
    return axis_size**2 + 2 * (effective - 1) * axis_size - (effective - 1) ** 2


def t_rule_clause_count(axis_size: int, cutoff: int) -> int:
    effective = min(max(cutoff, 0), axis_size - 1)
    if effective == 0:
        return 0
    return (4 * effective - 1) * axis_size - 2 * effective**2 + effective - 1


def gx_me_clause_count(axis_size: int, cutoff: int) -> int:
    effective = min(max(cutoff, 0), axis_size - 1)
    return 4 * max(axis_size - effective - 1, 0)


def threshold_clauses_per_edge(k: int, x_cutoff: int, y_cutoff: int) -> int:
    axis_units = int(k + 1 <= x_cutoff) + int(k + 1 <= y_cutoff)
    lower = max(1, k + 1 - y_cutoff)
    upper = min(k, x_cutoff)
    couplings = max(0, upper - lower + 1)
    return axis_units + couplings


@dataclass
class MetricRecorder:
    """Emit complete, flushed JSON events and retain them for tests/callers."""

    run_id: str
    stream: TextIO | None = None
    emit_stdout: bool = True
    schema: int = 1
    events: list[dict[str, Any]] = field(default_factory=list)
    _seq: int = 0
    _started_at: float = field(default_factory=time.monotonic)

    def emit(self, phase: str, **values: Any) -> dict[str, Any]:
        self._seq += 1
        event = {
            "schema": self.schema,
            "run_id": self.run_id,
            "seq": self._seq,
            "phase": phase,
            "elapsed_wall_s": round(time.monotonic() - self._started_at, 9),
            **values,
        }
        self.events.append(event)
        line = "METRIC_JSON: " + json.dumps(event, sort_keys=True, separators=(",", ":"))
        if self.emit_stdout:
            print(line, flush=True)
        if self.stream is not None:
            self.stream.write(line + os.linesep)
            self.stream.flush()
        return event
