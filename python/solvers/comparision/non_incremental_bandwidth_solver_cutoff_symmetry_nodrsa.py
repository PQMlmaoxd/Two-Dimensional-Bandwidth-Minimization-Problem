from __future__ import annotations

import os
import sys
import time

from pysat.formula import IDPool


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from python.solvers.incremental_bandwidth_solver_cutoff_symmetry_nodrsa import (
    IncrementalBandwidthSolverCutoffSymmetry,
)
from python.solvers.comparision.common_cli import run_solver_cli


class NonIncrementalBandwidthSolverCutoffSymmetry(IncrementalBandwidthSolverCutoffSymmetry):
    """Non-incremental counterpart of the cutoff+symmetry baseline solver.

    The SAT formula for each tested K is rebuilt from scratch. The base encoding
    is kept the same as the incremental baseline: symmetry first, optimized
    position constraints, and cutoff distance encoding at upper_bound_cutoff.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        print("[comparison/non-incremental] Fresh SAT solver per K")

    def _reset_formula_state(self):
        self.vpool = IDPool()
        self.X_vars = {}
        self.Y_vars = {}
        self.Tx_vars = {}
        self.Ty_vars = {}
        self.current_k_constraints.clear()
        self.persistent_solver = self._create_solver()
        self.base_constraints_added = False
        self.anchor_vertex = None

    def _build_solver_for_k(self, K: int):
        print(f"\nBuilding fresh solver for K={K}...")
        build_start = time.time()

        self._reset_formula_state()
        self.create_position_variables()
        self.create_distance_variables()

        print("  Step 1: Adding symmetry breaking constraints (FIRST)...")
        symmetry_clauses = self.encode_symmetry_breaking()
        print(f"    Symmetry: {symmetry_clauses} unit clauses")

        print("  Step 2: Adding position constraints...")
        position_clauses = self.encode_position_constraints()
        print(f"    Position: {position_clauses} clauses")

        print("  Step 3: Adding cutoff distance constraints...")
        # Match the incremental baseline: distance is encoded once at K_start/UB,
        # then each tested K only adds bandwidth constraints.
        _ = self.encode_distance_constraints(self.upper_bound_cutoff)

        print(f"  Step 4: Adding bandwidth constraints for K={K}...")
        bandwidth_clauses = self.encode_bandwidth_constraints_for_k(K)
        for clause in bandwidth_clauses:
            self.persistent_solver.add_clause(clause)

        build_time = time.time() - build_start
        print(f"  Fresh solver build time: {build_time:.3f}s")
        print(f"  Fresh solver stats: vars={self.persistent_solver.nof_vars()}, clauses={self.persistent_solver.nof_clauses()}")
        print(f"METRIC: total_solver_clauses={self.persistent_solver.nof_clauses()}")
        return len(bandwidth_clauses), build_time

    def solve_with_non_incremental_sat(self, upper_bound: int):
        print("\nNON-INCREMENTAL SAT + UB CUTOFF + SYMMETRY BREAKING")
        print("Strategy: Fresh solver for every tested K")
        print(f"Theoretical UB: {self.theoretical_ub}")
        print(f"UB (T-var depth): {self.upper_bound_cutoff}")
        print(f"K_start (max warmup): {self.k_start}")
        print(f"Symmetry: anchor in first quadrant (~{self.symmetry_stats['theoretical_speedup']}x)")
        print(f"Solver: {self.solver_type.upper()}")

        current_k = upper_bound
        optimal_k = None
        cumulative_solve_time = 0.0
        solver_stats = {
            "total_solves": 0,
            "sat_results": 0,
            "unsat_results": 0,
            "smart_jumps": 0,
            "clauses_added": 0,
        }

        while current_k >= 1:
            print(f"\nTesting K = {current_k}")
            solver_stats["total_solves"] += 1

            bandwidth_clause_count, _ = self._build_solver_for_k(current_k)
            solver_stats["clauses_added"] += bandwidth_clause_count

            solve_start = time.time()
            result = self.persistent_solver.solve()
            solve_time = time.time() - solve_start
            cumulative_solve_time += solve_time

            print(f"  Solve time: {solve_time:.3f}s")

            if result:
                solver_stats["sat_results"] += 1
                print(f"SATISFIABLE: K = {current_k}")

                model = self.persistent_solver.get_model()
                self.last_model = model
                actual_bandwidth = self.extract_actual_bandwidth(model)

                print(f"  Actual bandwidth: {actual_bandwidth}")
                self.export_solution_to_file(model, current_k, actual_bandwidth)

                print(f"METRIC: best_k_so_far={actual_bandwidth}")
                print(f"METRIC: sat_at_k={current_k}")
                print(f"METRIC: sat_solve_time_s={solve_time:.6f}")
                print(f"METRIC: cumulative_solve_time_s={cumulative_solve_time:.6f}")
                print(f"METRIC: total_solver_clauses={self.persistent_solver.nof_clauses()}")

                if actual_bandwidth < current_k:
                    print(f"SMART JUMP: {current_k} -> {actual_bandwidth}")
                    optimal_k = actual_bandwidth
                    current_k = actual_bandwidth - 1
                    solver_stats["smart_jumps"] += 1
                else:
                    optimal_k = current_k
                    current_k -= 1
            else:
                solver_stats["unsat_results"] += 1
                print(f"UNSATISFIABLE: K = {current_k}")
                print(f"Optimal bandwidth: {optimal_k}")
                self.cleanup_solver()
                break

            self.cleanup_solver()

        if optimal_k is not None:
            if solver_stats["unsat_results"]:
                self._finalize_verification(
                    status="OPTIMAL_BY_UNSAT",
                    termination="unsat_below_verified_incumbent",
                    termination_k=current_k,
                    elapsed_s=cumulative_solve_time,
                )
            else:
                self._finalize_verification(
                    status="OPTIMAL_BY_BOUND",
                    termination="minimum_positive_bandwidth",
                    termination_k=optimal_k,
                    elapsed_s=cumulative_solve_time,
                )

        print("\nNON-INCREMENTAL SOLVER COMPLETE")
        print("=" * 60)
        print(f"Final optimal bandwidth: {optimal_k}")
        print(f"Total solves: {solver_stats['total_solves']}")
        print(f"SAT: {solver_stats['sat_results']}, UNSAT: {solver_stats['unsat_results']}")
        print(f"Smart jumps: {solver_stats['smart_jumps']}")
        print(f"Fresh solver rebuilds: {solver_stats['total_solves']}")
        print("=" * 60)

        return optimal_k

    def solve_bandwidth_optimization(self, start_k=None, end_k=None):
        if start_k is None:
            start_k = 1
        if end_k is None:
            end_k = self.k_start

        print("\n" + "=" * 80)
        print("2D BANDWIDTH OPTIMIZATION - NON-INCREMENTAL CUTOFF + SYMMETRY")
        print(f"Graph: {self.n} nodes, {len(self.edges)} edges")
        print("Strategy: Rebuild solver for each K")
        print(f"Theoretical UB: {self.theoretical_ub}")
        print(f"DRSA UB: {self.drsa_ub}")
        print(f"Active UB (max): {self.upper_bound_cutoff}")
        print(f"Symmetry reduction: ~{self.symmetry_stats['theoretical_speedup']}x")
        print(f"Testing range: K = {start_k} to {end_k}")
        print("=" * 80)

        try:
            return self.solve_with_non_incremental_sat(end_k)
        finally:
            self.cleanup_solver()


if __name__ == "__main__":
    sys.exit(run_solver_cli(
        NonIncrementalBandwidthSolverCutoffSymmetry,
        "2D BANDWIDTH OPTIMIZATION - NON-INCREMENTAL CUTOFF + SYMMETRY",
    ))
