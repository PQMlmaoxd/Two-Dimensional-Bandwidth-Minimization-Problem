from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MethodEntrypoint:
    """Immutable worker metadata used by the public supervisor."""

    key: str
    script: str
    method_id: str
    grid_policy: str


METHODS: dict[str, MethodEntrypoint] = {
    "square": MethodEntrypoint(
        "square",
        "python/solvers/incremental_bandwidth_solver_cutoff_symmetry_nodrsa.py",
        "incremental_bandwidth_solver_cutoff_symmetry_nodrsa",
        "ceil-sqrt-square",
    ),
    "square-non-incremental": MethodEntrypoint(
        "square-non-incremental",
        "python/solvers/comparision/non_incremental_bandwidth_solver_cutoff_symmetry_nodrsa.py",
        "non_incremental_bandwidth_solver_cutoff_symmetry_nodrsa",
        "ceil-sqrt-square",
    ),
    "square-no-symmetry": MethodEntrypoint(
        "square-no-symmetry",
        "python/solvers/comparision/incremental_bandwidth_solver_cutoff_no_symmetry_nodrsa.py",
        "incremental_bandwidth_solver_cutoff_no_symmetry_nodrsa",
        "ceil-sqrt-square",
    ),
    "square-naive-amo": MethodEntrypoint(
        "square-naive-amo",
        "python/solvers/comparision/incremental_bandwidth_solver_cutoff_symmetry_naive_amo_nodrsa.py",
        "incremental_bandwidth_solver_cutoff_symmetry_naive_amo_nodrsa",
        "ceil-sqrt-square",
    ),
    "square-standard": MethodEntrypoint(
        "square-standard",
        "python/solvers/comparision/incremental_bandwidth_solver_standard_symmetry_nodrsa.py",
        "incremental_bandwidth_solver_standard_symmetry_nodrsa",
        "ceil-sqrt-square",
    ),
    "nxn": MethodEntrypoint(
        "nxn",
        "python/solvers/incremental_bandwidth_solver_cutoff_symmetry_nxn.py",
        "nxn-pairwise",
        "n-by-n-square",
    ),
    "two-row": MethodEntrypoint(
        "two-row",
        "python/solvers/incremental_bandwidth_solver_two_row.py",
        "two-row-cutoff",
        "two-row",
    ),
}


def get_method(key: str) -> MethodEntrypoint:
    try:
        return METHODS[key]
    except KeyError as exc:
        choices = ", ".join(sorted(METHODS))
        raise ValueError(f"unknown method {key!r}; choose one of: {choices}") from exc


def worker_path(submit_root: Path, method: MethodEntrypoint) -> Path:
    path = submit_root / method.script
    if not path.is_file():
        raise FileNotFoundError(f"worker script is missing: {path}")
    return path
