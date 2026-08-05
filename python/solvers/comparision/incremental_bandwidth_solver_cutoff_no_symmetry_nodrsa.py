from __future__ import annotations

import ctypes
import gc
import os
import sys
import time


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from python.solvers.incremental_bandwidth_solver_cutoff_symmetry_nodrsa import (
    IncrementalBandwidthSolverCutoffSymmetry,
)
from python.solvers.comparision.common_cli import run_solver_cli


class IncrementalBandwidthSolverCutoffNoSymmetry(IncrementalBandwidthSolverCutoffSymmetry):
    """Baseline cutoff solver with only D4 symmetry breaking removed."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        print("[comparison/no-symmetry] D4 symmetry breaking disabled")

    def encode_symmetry_breaking(self):
        print("Symmetry breaking disabled for this comparison variant")
        print("METRIC: symmetry_encode_time_s=0.000000")
        print("METRIC: symmetry_clauses=0")
        return 0

    def _initialize_persistent_solver(self, initial_k: int):
        if self.persistent_solver is not None:
            print("Persistent solver already initialized")
            return

        effective_ub = min(initial_k, self.upper_bound_cutoff)

        print("\nInitializing cutoff solver WITHOUT symmetry breaking...")
        print(f"Using {self.solver_type.upper()} with incremental interface")
        print(f"Grid: {self.grid_side}x{self.grid_side} ({self.n} vertices)")
        print(f"Initial K: {initial_k}")
        print(f"Theoretical UB: {self.theoretical_ub}")
        print(f"UB cutoff: {self.upper_bound_cutoff}")
        print(f"Effective UB for encoding: {effective_ub}")
        print("Clause order: Position -> Distance")

        self.persistent_solver = self._create_solver()
        initialization_start_time = time.time()

        print("\n  Step 1: Adding position constraints...")
        position_clauses = self.encode_position_constraints()
        print(f"    Position: {position_clauses} clauses")

        print("\n  Step 2: Adding distance constraints with K-specific UB cutoff...")
        _ = self.encode_distance_constraints(initial_k)

        total_initialization_time = time.time() - initialization_start_time
        print(f"\n  Total base constraints encoding time: {total_initialization_time:.3f}s")

        gc.collect()
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass

        self.base_constraints_added = True

        print("Cutoff no-symmetry solver initialized:")
        print(f"  UB cutoff: distance > {effective_ub} eliminated")
        print("  Symmetry: disabled")
        print(f"  Final stats: vars={self.persistent_solver.nof_vars()}, clauses={self.persistent_solver.nof_clauses()}")


if __name__ == "__main__":
    sys.exit(run_solver_cli(
        IncrementalBandwidthSolverCutoffNoSymmetry,
        "2D BANDWIDTH OPTIMIZATION - CUTOFF, NO SYMMETRY",
    ))
