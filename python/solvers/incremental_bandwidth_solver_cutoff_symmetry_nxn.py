import sys
import os
import time
import gc
import ctypes
import math
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pysat.formula import IDPool
from python.utils.mtx_parser import parse_mtx_graph
from python.utils.host_grid import HostGrid
from python.utils.verification_io import (
    build_verification_document,
    sha256_file,
    write_json_atomic,
)
from python.utils.solution_verifier import verify_placement
from python.solvers.solver_factory import create_sat_solver
from python.encoders.distance_encoder_cutoff import encode_abs_distance_cutoff, calculate_theoretical_upper_bound
from python.encoders.position_constraints import encode_all_position_constraints, create_position_variables
from python.encoders.symmetry_breaking import (
    encode_symmetry_breaking_constraints,
    get_symmetry_stats,
    select_anchor_vertex,
)

try:
    from python.utils.drsa_upper_bound import compute_upper_bound_drsa
    HAS_DRSA = True
except ImportError:
    HAS_DRSA = False


class IncrementalBandwidthSolverNxN:
    """
    Incremental 2DBMP solver on an n×n grid (pairwise mutual exclusion).

    Identical logic to IncrementalBandwidthSolverCutoffSymmetry except
    grid_side = n (number of vertices) instead of ceil(sqrt(n)).

    Uses an n-by-n host and the cutoff distance encoding.
    """

    def __init__(
        self,
        n: int,
        solver_type: str = "cadical195",
        use_drsa: bool = True,
        drsa_seed: int = 42,
        drsa_time_limit: float = 10.0,
    ):
        self.n = n
        self.solver_type = solver_type
        self.vpool = IDPool()
        self.use_drsa = use_drsa and HAS_DRSA
        self.drsa_seed = drsa_seed
        self.drsa_time_limit = drsa_time_limit

        # ── KEY DIFFERENCE ──────────────────────────────────────────────────
        # n×n grid: each axis has n positions (not ceil(sqrt(n))).
        self.grid_side = n

        self.X_vars = {}
        self.Y_vars = {}
        self.Tx_vars = {}
        self.Ty_vars = {}
        self.edges = []
        self.last_model = None
        self.anchor_vertex = None

        self.theoretical_ub = calculate_theoretical_upper_bound(n)
        self.drsa_ub = None
        self.upper_bound_cutoff = self.theoretical_ub

        self.persistent_solver = None
        self.base_constraints_added = False
        self.current_k_constraints = set()

        # Symmetry stats computed using grid_side = n
        self.symmetry_stats = get_symmetry_stats(self.grid_side)

        self.verification_output_file = None
        self.verification_input_file = None
        self.verification_graph = None
        self._verification_document = None

        print(f"[nxn-pairwise] n={n}, grid={self.grid_side}×{self.grid_side} (n×n), solver={solver_type}")
        print(f"Theoretical UB: {self.theoretical_ub}")
        print(f"  Note: on n×n grid, max 1D dist = n-1={n-1}; δ(n)≈{self.theoretical_ub} << n-1 → UB cutoff very effective")

    def set_verification_output_file(self, filepath: str):
        self.verification_output_file = filepath

    def set_verification_context(self, input_file, parsed_graph):
        self.verification_input_file = Path(input_file).resolve()
        self.verification_graph = parsed_graph

    def set_graph_edges(self, edges):
        self.edges = edges
        self.upper_bound_cutoff = self.compute_upper_bound()
        print(f"Graph: {self.n} nodes, {len(edges)} edges")
        print(f"UB cutoff: {self.upper_bound_cutoff}")

    def compute_upper_bound(self) -> int:
        """UB = δ(n) (proven formula). No DRSA needed for n×n grid experiments."""
        formula_ub = self.theoretical_ub
        print(f"[nxn-pairwise] UB = δ(n) = {formula_ub} (formula, proven)")
        return formula_ub

    def create_position_variables(self):
        """Create X/Y position variables: X_v[0..n-1] = position 1..n."""
        self.X_vars, self.Y_vars = create_position_variables(
            self.n, self.vpool, self.grid_side  # grid_side = n
        )
        print(f"Position variables: {self.n} vertices × 2 axes × {self.grid_side} positions"
              f" = {2 * self.n * self.grid_side} vars")

    def create_distance_variables(self):
        print("Preparing edge distance variable containers...")
        for (u, v) in self.edges:
            edge_id = f"edge_{u}_{v}"
            self.Tx_vars[edge_id] = {"prefix": f"Tx_{edge_id}", "vars": {}}
            self.Ty_vars[edge_id] = {"prefix": f"Ty_{edge_id}", "vars": {}}
        # Max 1D distance on n-wide axis = n-1; T vars capped at min(UB, n-1)
        effective_t_range = min(self.upper_bound_cutoff, self.grid_side - 1)
        print(f"T_d range per edge/axis: 1..{effective_t_range}  (grid_side-1 = {self.grid_side-1})")

    def encode_symmetry_breaking(self):
        print("Encoding symmetry breaking (FIRST)...")
        adj = [[] for _ in range(self.n + 1)]
        for (u, v) in self.edges:
            adj[u].append(v)
            adj[v].append(u)
        anchor = select_anchor_vertex(adj)
        self.anchor_vertex = anchor
        print(f"  Anchor vertex: v{anchor} (degree {len(adj[anchor])})")

        clauses_added = 0
        for clause in encode_symmetry_breaking_constraints(
            self.grid_side, self.X_vars, self.Y_vars,
            first_vertex=anchor, verbose=True,
        ):
            self.persistent_solver.add_clause(clause)
            clauses_added += 1

        print(f"  Symmetry clauses: {clauses_added}")
        return clauses_added

    def encode_position_constraints(self):
        print("Encoding position constraints...")
        t0 = time.time()
        clauses_added = 0
        for clause in encode_all_position_constraints(
            self.n, self.X_vars, self.Y_vars, self.vpool
        ):
            self.persistent_solver.add_clause(clause)
            clauses_added += 1
        encode_time = time.time() - t0
        print(f"  Position clauses: {clauses_added}")
        print(f"METRIC: position_encode_time_s={encode_time:.6f}")
        print(f"METRIC: position_solver_vars={self.persistent_solver.nof_vars()}")
        print(f"METRIC: position_solver_clauses={self.persistent_solver.nof_clauses()}")
        return clauses_added

    def encode_distance_constraints(self, current_k: int):
        """Encode T-variable rules + pairwise mutual exclusion for all edges."""
        print("Encoding distance constraints (T-rules + pairwise ME)...")
        effective_ub = min(current_k, self.upper_bound_cutoff)
        # distance_encoder_cutoff internally caps at min(effective_ub, grid_side-1)
        print(f"  effective_ub={effective_ub}  (K={current_k}, cutoff={self.upper_bound_cutoff}, "
              f"grid_side-1={self.grid_side-1})")

        t0 = time.time()
        t_rule_clauses = 0
        me_clauses = 0

        for edge_id, (u, v) in zip(
            [f"edge_{u}_{v}" for u, v in self.edges], self.edges
        ):
            tx_prefix = self.Tx_vars[edge_id]["prefix"]
            tx_clauses, tx_vars = encode_abs_distance_cutoff(
                self.X_vars[u], self.X_vars[v], effective_ub, self.vpool, tx_prefix
            )
            self.Tx_vars[edge_id]["vars"] = tx_vars
            for c in tx_clauses:
                self.persistent_solver.add_clause(c)
            t_rule_clauses += len(tx_clauses)
            tx_clauses.clear()

            ty_prefix = self.Ty_vars[edge_id]["prefix"]
            ty_clauses, ty_vars = encode_abs_distance_cutoff(
                self.Y_vars[u], self.Y_vars[v], effective_ub, self.vpool, ty_prefix
            )
            self.Ty_vars[edge_id]["vars"] = ty_vars
            for c in ty_clauses:
                self.persistent_solver.add_clause(c)
            t_rule_clauses += len(ty_clauses)
            ty_clauses.clear()

        # Count ME clauses (pairwise, included inside encode_abs_distance_cutoff stage 7)
        # They are part of the t_rule_clauses total above; we log them separately for
        # comparison against the GX variant.
        elapsed = time.time() - t0
        print(f"  Distance clauses total (T-rules + pairwise ME): {t_rule_clauses}")
        print(f"  Distance encode time: {elapsed:.3f}s")
        print(f"METRIC: distance_encode_time_s={elapsed:.6f}")
        print(f"METRIC: distance_solver_vars={self.persistent_solver.nof_vars()}")
        print(f"METRIC: distance_solver_clauses={t_rule_clauses}")
        print(f"METRIC: effective_ub_used={effective_ub}")
        return elapsed

    def _create_solver(self):
        return create_sat_solver(
            self.solver_type,
            fallback="glucose42",
            warn_on_fallback=True,
        )

    def _initialize_persistent_solver(self, initial_k: int):
        if self.persistent_solver is not None:
            return
        effective_ub = min(initial_k, self.upper_bound_cutoff)
        print(f"\n[nxn-pairwise] Initializing solver  grid={self.grid_side}×{self.grid_side}")
        print(f"  initial_k={initial_k}, effective_ub={effective_ub}")

        self.persistent_solver = self._create_solver()
        t0 = time.time()

        print("\n  Step 1: Symmetry breaking")
        self.encode_symmetry_breaking()
        print("\n  Step 2: Position constraints")
        self.encode_position_constraints()
        print("\n  Step 3: Distance constraints")
        self.encode_distance_constraints(initial_k)

        self.base_constraints_added = True
        elapsed = time.time() - t0
        print(f"\n  Init done in {elapsed:.3f}s  "
              f"vars={self.persistent_solver.nof_vars()}, "
              f"clauses={self.persistent_solver.nof_clauses()}")

        gc.collect()
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass

    def encode_bandwidth_constraints_for_k(self, K: int):
        """Generate monotone bandwidth ≤ K constraints (added once per K)."""
        if K in self.current_k_constraints:
            return []

        effective_k = min(K, self.upper_bound_cutoff)
        new_clauses = []

        for edge_id in self.Tx_vars:
            tx_vars = self.Tx_vars[edge_id]["vars"]
            ty_vars = self.Ty_vars[edge_id]["vars"]

            # ¬T_x_{K+1}  →  1D X distance ≤ K
            if (effective_k + 1) in tx_vars:
                new_clauses.append([-tx_vars[effective_k + 1]])
            # ¬T_y_{K+1}  →  1D Y distance ≤ K
            if (effective_k + 1) in ty_vars:
                new_clauses.append([-ty_vars[effective_k + 1]])

            # Sum constraint: ¬Tx_≥i ∨ ¬Ty_≥(K−i+1)  prevents Tx+Ty > K
            # Covers all decompositions (a,b) with a+b = K+1, 1≤a≤K.
            for i in range(1, effective_k + 1):
                remaining = effective_k - i  # = K - i ≥ 0
                if i in tx_vars and (remaining + 1) in ty_vars:
                    new_clauses.append([-tx_vars[i], -ty_vars[remaining + 1]])

        self.current_k_constraints.add(K)
        return new_clauses

    def _extract_positions_from_model(self, model):
        posset = {lit for lit in model if lit > 0}
        positions = {}
        for v in range(1, self.n + 1):
            for pos in range(1, self.grid_side + 1):
                if self.X_vars[v][pos - 1] in posset:
                    positions[f"X_{v}"] = pos
                    break
            for pos in range(1, self.grid_side + 1):
                if self.Y_vars[v][pos - 1] in posset:
                    positions[f"Y_{v}"] = pos
                    break
        return positions

    def _calculate_bandwidth(self, positions):
        max_dist = 0
        edge_distances = []
        for u, v in self.edges:
            xu = positions.get(f"X_{u}", 0)
            yu = positions.get(f"Y_{u}", 0)
            xv = positions.get(f"X_{v}", 0)
            yv = positions.get(f"Y_{v}", 0)
            d = abs(xu - xv) + abs(yu - yv)
            max_dist = max(max_dist, d)
            edge_distances.append((u, v, d))
        return max_dist, edge_distances

    def extract_actual_bandwidth(self, model):
        positions = self._extract_positions_from_model(model)
        bw, _ = self._calculate_bandwidth(positions)
        return bw

    def export_solution_to_file(self, model, K, actual_bandwidth):
        if self.verification_output_file is None:
            return
        if self.verification_graph is None or self.verification_input_file is None:
            print("  Warning: verification context is not configured")
            return
        positions = self._extract_positions_from_model(model)
        bandwidth, _ = self._calculate_bandwidth(positions)
        if bandwidth != actual_bandwidth or bandwidth > K:
            raise RuntimeError(
                f"Refusing invalid verification witness: K={K}, "
                f"reported={actual_bandwidth}, recomputed={bandwidth}"
            )
        compact_positions = {
            vertex: (positions[f"X_{vertex}"], positions[f"Y_{vertex}"])
            for vertex in range(1, self.n + 1)
        }
        host = HostGrid(self.grid_side, self.grid_side, "n-by-n-square")
        verification = verify_placement(
            self.verification_graph.n_vertices,
            self.verification_graph.edges,
            host,
            compact_positions,
            tested_k=K,
        )
        if not verification.valid or verification.bandwidth != bandwidth:
            raise RuntimeError(
                "Refusing verification witness that does not match the input graph: "
                f"{verification.error or verification.bandwidth}"
            )
        script = Path(sys.argv[0]).name
        document = build_verification_document(
            instance_file=str(self.verification_input_file),
            instance_sha256=sha256_file(self.verification_input_file),
            graph=self.verification_graph,
            method_id="nxn-pairwise",
            solver=self.solver_type,
            solver_script=script,
            host=host,
            status="RUNNING_FEASIBLE",
            termination="in_progress",
            termination_k=None,
            elapsed_s=None,
            timeout_s=None,
            certified_lower=None,
            final_verified=bandwidth,
            optimal=None,
            constraint_k=K,
            positions=compact_positions,
        )
        try:
            write_json_atomic(Path(self.verification_output_file), document)
            self._verification_document = document
        except Exception as e:
            print(f"  Warning: could not write verification file: {e}")

    def _finalize_verification(self, *, status, termination, termination_k, elapsed_s):
        if self._verification_document is None or self.verification_output_file is None:
            return
        document = self._verification_document
        final_verified = document["bounds"]["final_verified"]
        document["outcome"].update({
            "status": status,
            "termination": termination,
            "termination_k": termination_k,
            "elapsed_s": elapsed_s,
        })
        document["bounds"].update({
            "certified_lower": final_verified,
            "optimal": final_verified,
        })
        write_json_atomic(Path(self.verification_output_file), document)

    def solve_with_incremental_sat(self, upper_bound: int):
        print(f"\n[nxn-pairwise] INCREMENTAL SAT  grid={self.grid_side}×{self.grid_side}")
        print(f"  UB={self.upper_bound_cutoff}, starting K={min(upper_bound, self.upper_bound_cutoff)}")

        current_k = min(upper_bound, self.upper_bound_cutoff)
        self._initialize_persistent_solver(current_k)

        optimal_k = None
        cumulative_time = 0.0

        while current_k >= 1:
            print(f"\nTesting K = {current_k}")

            bw_clauses = self.encode_bandwidth_constraints_for_k(current_k)
            for clause in bw_clauses:
                self.persistent_solver.add_clause(clause)

            t0 = time.time()
            sat = self.persistent_solver.solve()
            solve_dt = time.time() - t0
            cumulative_time += solve_dt
            print(f"  Solve time: {solve_dt:.6f}s")

            if sat:
                model = self.persistent_solver.get_model()
                self.last_model = model
                actual_bw = self.extract_actual_bandwidth(model)
                print(f"SATISFIABLE: K={current_k}, actual_bw={actual_bw}")
                self.export_solution_to_file(model, current_k, actual_bw)
                print(f"METRIC: best_k_so_far={actual_bw}")
                print(f"METRIC: sat_at_k={current_k}")
                print(f"METRIC: sat_solve_time_s={solve_dt:.6f}")
                print(f"METRIC: cumulative_solve_time_s={cumulative_time:.6f}")
                print(f"METRIC: total_solver_clauses={self.persistent_solver.nof_clauses()}")
                if actual_bw < current_k:
                    optimal_k = actual_bw
                    current_k = actual_bw - 1
                    print(f"SMART JUMP: → K={current_k}")
                else:
                    optimal_k = current_k
                    current_k -= 1
            else:
                print(f"UNSATISFIABLE at K={current_k}")
                break

        if optimal_k is not None:
            if current_k >= 1:
                self._finalize_verification(
                    status="OPTIMAL_BY_UNSAT",
                    termination="unsat_below_verified_incumbent",
                    termination_k=current_k,
                    elapsed_s=cumulative_time,
                )
            else:
                self._finalize_verification(
                    status="OPTIMAL_BY_BOUND",
                    termination="minimum_positive_bandwidth",
                    termination_k=optimal_k,
                    elapsed_s=cumulative_time,
                )

        print(f"\n[nxn-pairwise] Optimal bandwidth: {optimal_k}")
        return optimal_k

    def cleanup_solver(self):
        if self.persistent_solver is not None:
            self.persistent_solver.delete()
            self.persistent_solver = None
            self.base_constraints_added = False
            self.current_k_constraints.clear()

    def solve_bandwidth_optimization(self, start_k=None, end_k=None):
        if end_k is None:
            end_k = self.upper_bound_cutoff
        effective_end_k = min(end_k, self.upper_bound_cutoff)

        print("\n" + "=" * 80)
        print("2DBMP — N×N GRID, PAIRWISE ME (experiment)")
        print(f"  n={self.n} vertices on {self.grid_side}×{self.grid_side} grid")
        print(f"  UB={self.upper_bound_cutoff},  K range: 1..{effective_end_k}")
        print("=" * 80)
        try:
            return self.solve_with_incremental_sat(effective_end_k)
        finally:
            self.cleanup_solver()


# ── MTX parser (identical to m×m solver) ────────────────────────────────────

def _parse_mtx_file_legacy(filename: str):
    print(f"Reading MTX file: {os.path.basename(filename)}")
    try:
        with open(filename, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"Error: File {filename} not found")
        return None, None

    header_found = False
    edges_set = set()
    n = 0

    for line_num, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("%"):
            continue
        if not header_found:
            try:
                parts = line.split()
                if len(parts) >= 3:
                    rows, cols, _ = map(int, parts[:3])
                    n = max(rows, cols)
                    header_found = True
                    continue
            except ValueError:
                continue
        try:
            parts = line.split()
            if len(parts) >= 2:
                u, v = int(parts[0]), int(parts[1])
                if u != v:
                    edges_set.add(tuple(sorted([u, v])))
        except (ValueError, IndexError):
            continue

    edges = list(edges_set)
    print(f"Loaded: {n} vertices, {len(edges)} edges")
    return n, edges


def _resolve_path(mtx_file: str):
    if os.path.exists(mtx_file):
        return mtx_file
    return None


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        mtx_file = sys.argv[1]
        solver_type = sys.argv[2] if len(sys.argv) >= 3 else "cadical195"
        verification_file = sys.argv[3] if len(sys.argv) >= 4 else None

        resolved = _resolve_path(mtx_file)
        if resolved is None:
            print(f"Error: '{mtx_file}' not found")
            sys.exit(1)

        try:
            parsed_graph = parse_mtx_graph(resolved)
            n, edges = parsed_graph.n_vertices, parsed_graph.edges
            print(f"Loaded: {n} vertices, {len(edges)} edges ({parsed_graph.interpretation})")
        except ValueError as exc:
            print(f"Failed to parse MTX file: {exc}")
            n, edges = None, None
        if n is None:
            print("Failed to parse MTX file")
            sys.exit(1)

        solver = IncrementalBandwidthSolverNxN(n, solver_type)
        solver.set_graph_edges(edges)
        solver.create_position_variables()
        solver.create_distance_variables()

        if verification_file:
            solver.set_verification_output_file(verification_file)
            solver.set_verification_context(resolved, parsed_graph)

        t0 = time.time()
        best = solver.solve_bandwidth_optimization()
        elapsed = time.time() - t0

        print("\n" + "=" * 80)
        print("FINAL RESULTS [nxn-pairwise]")
        print("=" * 80)
        print(f"Optimal bandwidth: {best}")
        print(f"Total time: {elapsed:.2f}s")
        print("=" * 80)
    else:
        print("Usage: python incremental_bandwidth_solver_cutoff_symmetry_nxn.py "
              "[mtx_file] [solver] [verification_file]")
