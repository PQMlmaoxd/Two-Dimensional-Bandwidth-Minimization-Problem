from __future__ import annotations

import ctypes
import gc
import os
import sys
import time


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from python.encoders.distance_encoder import encode_abs_distance_final
from python.solvers.incremental_bandwidth_solver_cutoff_symmetry_nodrsa import (
    IncrementalBandwidthSolverCutoffSymmetry,
)
from python.solvers.comparision.common_cli import run_solver_cli


class IncrementalBandwidthSolverStandardSymmetry(IncrementalBandwidthSolverCutoffSymmetry):
    """Symmetry solver with standard distance encoding and no UB cutoff."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_axis_distance = max(0, self.grid_side - 1)
        self.max_manhattan_ub = 2 * self.max_axis_distance
        self.upper_bound_cutoff = self.max_manhattan_ub
        self.k_start = self.max_manhattan_ub
        print("[comparison/standard] Distance encoding uses full T range 1..grid_side-1")
        print(f"[comparison/standard] maxUB = 2*(grid_side-1) = {self.max_manhattan_ub}")

    def set_graph_edges(self, edges):
        self.edges = edges
        self.drsa_ub = None
        self.upper_bound_cutoff = self.max_manhattan_ub
        self.k_start = self.max_manhattan_ub

        print(f"Graph has {len(edges)} edges")
        print(f"Standard encoding search UB = max Manhattan distance = {self.max_manhattan_ub}")
        print(f"Theoretical cutoff UB for baseline comparison: {self.theoretical_ub}")
        print("DRSA/cutoff UB is intentionally not used by this standard-encoding variant")

    def create_distance_variables(self):
        print("Creating standard distance variable containers...")
        for u, v in self.edges:
            edge_id = f"edge_{u}_{v}"
            self.Tx_vars[edge_id] = {"prefix": f"Tx_{edge_id}", "vars": {}}
            self.Ty_vars[edge_id] = {"prefix": f"Ty_{edge_id}", "vars": {}}

        expected_t_vars = len(self.edges) * 2 * self.max_axis_distance
        print(f"Prepared {len(self.edges)} edges for standard distance encoding")
        print(f"T variables per edge/axis: 1..{self.max_axis_distance}")
        print(f"Expected T variables: {expected_t_vars}")

    def encode_distance_constraints(self, current_k: int):
        print("Encoding standard distance constraints (no cutoff)...")
        t0 = time.time()

        for edge_id, (u, v) in zip([f"edge_{u}_{v}" for u, v in self.edges], self.edges):
            tx_prefix = self.Tx_vars[edge_id]["prefix"]
            tx_list, tx_clauses = encode_abs_distance_final(
                self.X_vars[u], self.X_vars[v], self.grid_side, self.vpool, tx_prefix
            )
            self.Tx_vars[edge_id]["vars"] = {
                d: var for d, var in enumerate(tx_list, start=1)
            }
            for clause in tx_clauses:
                self.persistent_solver.add_clause(clause)
            tx_clauses.clear()

            ty_prefix = self.Ty_vars[edge_id]["prefix"]
            ty_list, ty_clauses = encode_abs_distance_final(
                self.Y_vars[u], self.Y_vars[v], self.grid_side, self.vpool, ty_prefix
            )
            self.Ty_vars[edge_id]["vars"] = {
                d: var for d, var in enumerate(ty_list, start=1)
            }
            for clause in ty_clauses:
                self.persistent_solver.add_clause(clause)
            ty_clauses.clear()

        enc_time = time.time() - t0
        vars_after = self.persistent_solver.nof_vars()
        clauses_after = self.persistent_solver.nof_clauses()

        print(f"Distance encoding time: {enc_time:.3f}s")
        print(f"Solver stats after distance: vars={vars_after}, clauses={clauses_after}")

        print(f"METRIC: distance_encode_time_s={enc_time:.6f}")
        print(f"METRIC: distance_solver_vars={vars_after}")
        print(f"METRIC: distance_solver_clauses={clauses_after}")
        print(f"METRIC: effective_ub_used={self.max_axis_distance}")

        return enc_time

    def _initialize_persistent_solver(self, initial_k: int):
        if self.persistent_solver is not None:
            print("Persistent solver already initialized")
            return

        print("\nInitializing standard-encoding solver...")
        print(f"Using {self.solver_type.upper()} with incremental interface")
        print(f"Grid: {self.grid_side}x{self.grid_side} ({self.n} vertices)")
        print(f"Initial K: {initial_k}")
        print(f"Theoretical cutoff UB used by baseline: {self.theoretical_ub}")
        print(f"Standard search maxUB: {self.max_manhattan_ub}")
        print(f"Full 1D T range: 1..{self.max_axis_distance}")
        print(f"Symmetry boundary m: {self.symmetry_stats['boundary_m']} (grid_side={self.grid_side})")
        print("Clause order: Symmetry -> Position -> Standard Distance")

        self.persistent_solver = self._create_solver()
        initialization_start_time = time.time()

        print("\n  Step 1: Adding symmetry breaking constraints (FIRST)...")
        symmetry_clauses = self.encode_symmetry_breaking()
        print(f"    Level 1 Symmetry: {symmetry_clauses} unit clauses")

        print("\n  Step 2: Adding position constraints...")
        position_clauses = self.encode_position_constraints()
        print(f"    Position: {position_clauses} clauses")

        print("\n  Step 3: Adding standard distance constraints...")
        _ = self.encode_distance_constraints(initial_k)

        total_initialization_time = time.time() - initialization_start_time
        print(f"\n  Total base constraints encoding time: {total_initialization_time:.3f}s")

        gc.collect()
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass

        self.base_constraints_added = True

        print("Standard-encoding solver initialized:")
        print("  Distance cutoff: disabled")
        print(f"  Standard maxUB: {self.max_manhattan_ub}")
        print(f"  Symmetry: anchor restricted to first quadrant (~{self.symmetry_stats['theoretical_speedup']}x)")
        print(f"  Final stats: vars={self.persistent_solver.nof_vars()}, clauses={self.persistent_solver.nof_clauses()}")

    def solve_with_incremental_sat(self, upper_bound):
        print("\nINCREMENTAL SAT + STANDARD DISTANCE ENCODING + SYMMETRY BREAKING")
        print("Strategy: Monotone strengthening with full distance encoding")
        print(f"Theoretical cutoff UB used by baseline: {self.theoretical_ub}")
        print(f"Standard maxUB: {self.max_manhattan_ub}")
        print(f"Solver: {self.solver_type.upper()}")

        current_k = upper_bound
        self._initialize_persistent_solver(current_k)

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

            bandwidth_clauses = self.encode_bandwidth_constraints_for_k(current_k)
            for clause in bandwidth_clauses:
                self.persistent_solver.add_clause(clause)

            solver_stats["clauses_added"] += len(bandwidth_clauses)
            bandwidth_clauses.clear()

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
                break

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

        print("\nSTANDARD-ENCODING SOLVER COMPLETE")
        print("=" * 60)
        print(f"Final optimal bandwidth: {optimal_k}")
        print(f"Total solves: {solver_stats['total_solves']}")
        print(f"SAT: {solver_stats['sat_results']}, UNSAT: {solver_stats['unsat_results']}")
        print(f"Smart jumps: {solver_stats['smart_jumps']}")
        print("Benefits disabled for this comparison: UB cutoff")
        print("=" * 60)

        return optimal_k

    def solve_bandwidth_optimization(self, start_k=None, end_k=None):
        if start_k is None:
            start_k = 1
        if end_k is None:
            end_k = self.k_start

        print("\n" + "=" * 80)
        print("2D BANDWIDTH OPTIMIZATION - STANDARD ENCODING")
        print(f"Graph: {self.n} nodes, {len(self.edges)} edges")
        print("Strategy: Standard Distance + Symmetry Breaking + Monotone Strengthening")
        print(f"Theoretical cutoff UB used by baseline: {self.theoretical_ub}")
        print(f"Standard maxUB: {self.max_manhattan_ub}")
        print(f"Testing range: K = {start_k} to {end_k}")
        print("=" * 80)

        try:
            return self.solve_with_incremental_sat(end_k)
        finally:
            self.cleanup_solver()


if __name__ == "__main__":
    sys.exit(run_solver_cli(
        IncrementalBandwidthSolverStandardSymmetry,
        "2D BANDWIDTH OPTIMIZATION - STANDARD ENCODING, SYMMETRY",
    ))
