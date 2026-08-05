from __future__ import annotations

import os
import sys
import time


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from python.encoders.position_constraints import encode_vertex_position_constraints
from python.solvers.incremental_bandwidth_solver_cutoff_symmetry_nodrsa import (
    IncrementalBandwidthSolverCutoffSymmetry,
)
from python.solvers.comparision.common_cli import run_solver_cli


def encode_naive_position_uniqueness_constraints(n, X_vars, Y_vars):
    """Naive pairwise At-Most-One for each grid cell.

    For every cell (x, y) and vertex pair u < v, forbid both vertices from
    occupying the same cell: not(X_u_x and Y_u_y and X_v_x and Y_v_y).
    """
    first_vertex = next(iter(X_vars))
    grid_side = len(X_vars[first_vertex])
    print(f"Encoding naive pairwise position uniqueness for {grid_side}x{grid_side} grid ({n} vertices)...")

    clause_count = 0
    for x in range(grid_side):
        for y in range(grid_side):
            for u in range(1, n + 1):
                for v in range(u + 1, n + 1):
                    yield [-X_vars[u][x], -Y_vars[u][y], -X_vars[v][x], -Y_vars[v][y]]
                    clause_count += 1

    print(f"Generated {clause_count} naive At-Most-One clauses")


class IncrementalBandwidthSolverCutoffSymmetryNaiveAMO(IncrementalBandwidthSolverCutoffSymmetry):
    """Baseline cutoff+symmetry solver with naive grid occupancy AMO."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        print("[comparison/naive-amo] Grid occupancy uses pairwise At-Most-One")

    def encode_position_constraints(self):
        print("Encoding position constraints with naive grid occupancy AMO...")
        encode_start_time = time.time()

        clauses_added = 0

        for clause in encode_vertex_position_constraints(self.n, self.X_vars, self.Y_vars, self.vpool):
            self.persistent_solver.add_clause(clause)
            clauses_added += 1

        for clause in encode_naive_position_uniqueness_constraints(self.n, self.X_vars, self.Y_vars):
            self.persistent_solver.add_clause(clause)
            clauses_added += 1

        encode_time = time.time() - encode_start_time
        vars_after = self.persistent_solver.nof_vars()
        clauses_after = self.persistent_solver.nof_clauses()

        print(f"Generated {clauses_added} position constraint clauses")
        print(f"Position constraints encode time: {encode_time:.3f}s")
        print(f"Solver stats after position: vars={vars_after}, clauses={clauses_after}")

        print(f"METRIC: position_encode_time_s={encode_time:.6f}")
        print(f"METRIC: position_solver_vars={vars_after}")
        print(f"METRIC: position_solver_clauses={clauses_after}")

        return clauses_added


if __name__ == "__main__":
    sys.exit(run_solver_cli(
        IncrementalBandwidthSolverCutoffSymmetryNaiveAMO,
        "2D BANDWIDTH OPTIMIZATION - CUTOFF, SYMMETRY, NAIVE AMO",
    ))
