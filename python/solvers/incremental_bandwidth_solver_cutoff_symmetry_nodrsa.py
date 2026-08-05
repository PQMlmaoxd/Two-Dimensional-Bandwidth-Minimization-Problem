# incremental_bandwidth_solver_cutoff_symmetry_nodrsa.py
#
# Incremental exact SAT solver for the ceil(sqrt(n))-square 2DBMP host.
# It starts from max(DRSA, theoretical upper bound) and monotonically tightens
# the bandwidth threshold in one persistent SAT solver.

from pysat.formula import IDPool

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

from python.solvers.solver_factory import create_sat_solver
from python.encoders.distance_encoder_cutoff import encode_abs_distance_cutoff, calculate_theoretical_upper_bound
from python.encoders.position_constraints import encode_all_position_constraints, create_position_variables
from python.encoders.symmetry_breaking import encode_symmetry_breaking_constraints, calculate_symmetry_boundary, get_symmetry_stats, select_anchor_vertex
from python.utils.mtx_parser import parse_mtx_graph
from python.utils.host_grid import HostGrid
from python.utils.verification_io import (
    build_verification_document,
    sha256_file,
    write_json_atomic,
)
from python.utils.solution_verifier import verify_placement

try:
    from python.utils.drsa_upper_bound import compute_upper_bound_drsa
    HAS_DRSA = True
except ImportError:
    HAS_DRSA = False


class IncrementalBandwidthSolverCutoffSymmetry:
    """
    2D Bandwidth Minimization solver with dual optimization:

    1. UB Cutoff Optimization:
       - Uses theoretical upper bound to eliminate impossible distance assignments
       - Lightweight T variables: Only creates up to UB, not full range 1..n-1
       - Direct mutual exclusion: Efficient 2-literal clauses for distance > UB
       - O(n × UB) complexity instead of O(n²)

    2. Symmetry Breaking:
       - Square grids have D_4 symmetry (8 elements: 4 rotations × 2 reflections)
       - Force v_1 into first quadrant: x_1 ≤ ⌈n/2⌉, y_1 ≤ ⌈n/2⌉
       - Unit clauses added FIRST for maximum propagation benefit

    OPTIMAL CLAUSE ORDER:
    1. Symmetry Breaking (FIRST) - unit clauses propagate immediately
    2. Position Constraints - benefit from symmetry propagation
    3. Distance Constraints with UB Cutoff - most complex, added last
    """

    def __init__(self, n, solver_type='cadical195',
                 use_drsa: bool = True,
                 drsa_seed: int = 42,
                 drsa_time_limit: float = 10.0):
        self.n = n
        self.solver_type = solver_type
        self.vpool = IDPool()

        self.use_drsa = use_drsa and HAS_DRSA
        self.drsa_seed = drsa_seed
        self.drsa_time_limit = drsa_time_limit
        self.drsa_ub = None

        # Grid dimensions: 2DBMP places n vertices on a ceil(√n) × ceil(√n) square grid.
        # x, y coordinates are in {1..grid_side}, not {1..n}.
        self.grid_side = math.ceil(math.sqrt(n))
        if self.grid_side * self.grid_side == n:
            print(f"Grid: {self.grid_side}×{self.grid_side} (perfect square, bijection)")
        else:
            print(f"Grid: {self.grid_side}×{self.grid_side} (n={n} is not a perfect square; "
                  f"{self.grid_side*self.grid_side - n} empty positions, injection)")

        # Position variables
        self.X_vars = {}
        self.Y_vars = {}

        # Distance variables with cutoff
        self.Tx_vars = {}
        self.Ty_vars = {}

        self.edges = []
        self.last_model = None

        # Cutoff optimization
        self.theoretical_ub = calculate_theoretical_upper_bound(n)
        self.upper_bound_cutoff = self.theoretical_ub
        self.k_start = self.theoretical_ub

        # Incremental SAT state
        self.persistent_solver = None
        self.base_constraints_added = False
        self.current_k_constraints = set()

        # Symmetry breaking stats (computed using grid_side, NOT n_vertices)
        self.symmetry_stats = get_symmetry_stats(self.grid_side)

        self.anchor_vertex = None

        # Verification output file path (optional)
        self.verification_output_file = None
        self.verification_input_file = None
        self.verification_graph = None
        self._verification_document = None

        print(f"Created dual-optimized solver: n={n}, grid={self.grid_side}×{self.grid_side}, solver={solver_type}")
        print(f"Theoretical UB: {self.theoretical_ub}")
        print(f"Grid positions per axis: {self.grid_side} (n={n})")
        print(f"Symmetry breaking: v_1 restricted to {self.symmetry_stats['valid_positions']}/{self.symmetry_stats['total_positions']} grid positions")
        print(f"Mode: no-ME, UB=max(DRSA, theoretical), max warmup "
              f"(use_drsa={self.use_drsa}, has_drsa={HAS_DRSA})")
        print(f"Combined benefits: UB cutoff + ~{self.symmetry_stats['theoretical_speedup']}x symmetry speedup")

    def set_verification_output_file(self, filepath):
        """Set the file path for verification output"""
        self.verification_output_file = filepath
        print(f"Verification output will be saved to: {filepath}")

    def set_verification_context(self, input_file, parsed_graph):
        self.verification_input_file = Path(input_file).resolve()
        self.verification_graph = parsed_graph

    def set_graph_edges(self, edges):
        """Set graph edges and compute UB = max(DRSA, theoretical_ub).

        DRSA gives a feasible bandwidth; theoretical_ub is the proven
        formula bound. Taking the max ensures K_start ≥ DRSA so the
        warmup never starts below a known feasible solution (max-warmup
        philosophy of this variant). No pairwise ME is encoded.
        """
        self.edges = edges
        print(f"Graph has {len(edges)} edges")

        drsa_bw = None
        if self.use_drsa and edges:
            print(f"[nodrsa-maxub] Running DRSA (time_limit={self.drsa_time_limit}s)…")
            try:
                drsa_bw = compute_upper_bound_drsa(
                    self.n,
                    edges,
                    seed=self.drsa_seed,
                    time_limit=self.drsa_time_limit,
                    verbose=True,
                )
                self.drsa_ub = drsa_bw
                print(f"[nodrsa-maxub] DRSA bandwidth = {drsa_bw}")
            except Exception as exc:
                print(f"[nodrsa-maxub] DRSA failed ({exc}); falling back to theoretical_ub")

        formula_ub = self.theoretical_ub
        if drsa_bw is not None:
            ub = max(drsa_bw, formula_ub)
            print(f"[nodrsa-maxub] UB = max(DRSA={drsa_bw}, formula={formula_ub}) = {ub}")
        else:
            ub = formula_ub
            print(f"[nodrsa-maxub] UB = formula = {ub} (DRSA unavailable)")

        self.upper_bound_cutoff = ub
        self.k_start = ub
        print(f"[nodrsa-maxub] K_start = {self.k_start} (max warmup, no ME)")
        print(f"Each edge will use distance constraints up to UB={self.upper_bound_cutoff}")

    def create_position_variables(self):
        """Create position variables for vertices on a grid_side × grid_side grid"""
        self.X_vars, self.Y_vars = create_position_variables(self.n, self.vpool, self.grid_side)
        print(f"Created position variables: {self.n} vertices × 2 axes × {self.grid_side} positions = {2 * self.n * self.grid_side} variables")

    def create_distance_variables(self):
        """
        Create T variables for edge distances with UB cutoff optimization
        Only creates T variables up to theoretical UB (not n-1)
        """
        print(f"Creating distance variables with UB cutoff optimization...")

        for i, (u, v) in enumerate(self.edges):
            edge_id = f'edge_{u}_{v}'
            self.Tx_vars[edge_id] = {'prefix': f'Tx_{edge_id}', 'vars': {}}
            self.Ty_vars[edge_id] = {'prefix': f'Ty_{edge_id}', 'vars': {}}

        print(f"Prepared {len(self.edges)} edges for distance variable creation")
        print(f"Each edge will get T variables for distances 1 to {self.upper_bound_cutoff}")
        expected_t_vars = len(self.edges) * 2 * self.upper_bound_cutoff
        # Max possible 1D distance on grid_side × grid_side grid = grid_side - 1
        standard_t_vars = len(self.edges) * 2 * (self.grid_side - 1)
        reduction = standard_t_vars - expected_t_vars
        print(f"Expected T variables: {expected_t_vars}")
        if reduction > 0:
            print(f"Reduction vs full grid range: ~{reduction} fewer variables ({reduction/(standard_t_vars+1)*100:.1f}%)")

    def encode_symmetry_breaking(self):
        """
        Encode symmetry breaking constraints FIRST.

        These constraints are added before position and distance constraints.
        Unit clauses propagate immediately and simplify subsequent encoding.
        """
        print(f"Encoding symmetry breaking constraints (FIRST)...")
        encode_start_time = time.time()

        # Build adjacency list to select highest-degree anchor (BCP cascade benefit)
        adj = [[] for _ in range(self.n + 1)]
        for (u, v) in self.edges:
            adj[u].append(v)
            adj[v].append(u)
        anchor = select_anchor_vertex(adj)
        print(f"Anchor vertex: v{anchor} (degree {len(adj[anchor])})")

        self.anchor_vertex = anchor

        clauses_added = 0
        for clause in encode_symmetry_breaking_constraints(
            self.grid_side, self.X_vars, self.Y_vars,
            first_vertex=anchor, verbose=True
        ):
            self.persistent_solver.add_clause(clause)
            clauses_added += 1

        encode_time = time.time() - encode_start_time

        vars_after = self.persistent_solver.nof_vars()
        clauses_after = self.persistent_solver.nof_clauses()

        print(f"Symmetry breaking: {clauses_added} unit clauses added")
        print(f"Symmetry encode time: {encode_time:.6f}s")
        print(f"Solver stats after symmetry: vars={vars_after}, clauses={clauses_after}")

        print(f"METRIC: symmetry_encode_time_s={encode_time:.6f}")
        print(f"METRIC: symmetry_clauses={clauses_added}")
        print(f"METRIC: symmetry_solver_vars={vars_after}")
        print(f"METRIC: symmetry_solver_clauses={clauses_after}")

        return clauses_added

    def encode_position_constraints(self):
        """Position constraints: each vertex gets exactly one position on each axis"""
        print(f"Encoding position constraints...")
        encode_start_time = time.time()

        constraints = encode_all_position_constraints(self.n, self.X_vars, self.Y_vars, self.vpool)

        clauses_added = 0
        for clause in constraints:
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

    def encode_distance_constraints(self, current_k: int):
        """
        Encode distance constraints with K-specific UB cutoff optimization.

        Uses effective_ub = min(current_k, theoretical_ub) for maximum optimization.
        """
        print("Encoding distance constraints with K-specific UB cutoff...")

        effective_ub = min(current_k, self.upper_bound_cutoff)
        print(f"  K-specific UB: {effective_ub} (K={current_k}, cutoff={self.upper_bound_cutoff})")

        if effective_ub < self.upper_bound_cutoff:
            reduction = ((self.upper_bound_cutoff - effective_ub) / self.upper_bound_cutoff * 100)
            print(f"  Optimization: Using UB={effective_ub} ({reduction:.1f}% reduction)")

        t0 = time.time()

        for edge_id, (u, v) in zip([f'edge_{u}_{v}' for u, v in self.edges], self.edges):
            # X distance with K-specific UB
            tx_prefix = self.Tx_vars[edge_id]['prefix']
            tx_clauses, tx_vars = encode_abs_distance_cutoff(
                self.X_vars[u], self.X_vars[v],
                effective_ub, self.vpool, tx_prefix
            )
            self.Tx_vars[edge_id]['vars'] = tx_vars
            for c in tx_clauses:
                self.persistent_solver.add_clause(c)
            tx_clauses.clear()

            # Y distance with K-specific UB
            ty_prefix = self.Ty_vars[edge_id]['prefix']
            ty_clauses, ty_vars = encode_abs_distance_cutoff(
                self.Y_vars[u], self.Y_vars[v],
                effective_ub, self.vpool, ty_prefix
            )
            self.Ty_vars[edge_id]['vars'] = ty_vars
            for c in ty_clauses:
                self.persistent_solver.add_clause(c)
            ty_clauses.clear()

        enc_time = time.time() - t0
        vars_after = self.persistent_solver.nof_vars()
        cls_after = self.persistent_solver.nof_clauses()

        print(f"Distance encoding time: {enc_time:.3f}s")
        print(f"Solver stats after distance: vars={vars_after}, clauses={cls_after}")

        print(f"METRIC: distance_encode_time_s={enc_time:.6f}")
        print(f"METRIC: distance_solver_vars={vars_after}")
        print(f"METRIC: distance_solver_clauses={cls_after}")
        print(f"METRIC: effective_ub_used={effective_ub}")

        return enc_time

    def _create_solver(self):
        """Create SAT solver instance"""
        return create_sat_solver(
            self.solver_type,
            fallback="glucose42",
            warn_on_fallback=True,
        )

    def _initialize_persistent_solver(self, initial_k: int):
        """
        Initialize persistent solver with dual optimization:
        1. Symmetry Breaking FIRST (unit clauses for immediate propagation)
        2. Position Constraints (benefit from symmetry propagation)
        3. Distance Constraints with K-specific UB cutoff (most complex)
        """
        if self.persistent_solver is not None:
            print("Persistent solver already initialized")
            return

        effective_ub = min(initial_k, self.upper_bound_cutoff)

        print(f"\nInitializing dual-optimized solver...")
        print(f"Using {self.solver_type.upper()} with incremental interface")
        print(f"Grid: {self.grid_side}\u00d7{self.grid_side} ({self.n} vertices, bijection)")
        print(f"Initial K: {initial_k}")
        print(f"Theoretical UB: {self.theoretical_ub}")
        print(f"UB cutoff (theoretical): {self.upper_bound_cutoff}")
        print(f"Effective UB for encoding: {effective_ub}")
        print(f"Symmetry boundary m: {self.symmetry_stats['boundary_m']} (grid_side={self.grid_side})")
        print(f"Clause order: Symmetry → Position → Distance (optimal)")

        self.persistent_solver = self._create_solver()
        initialization_start_time = time.time()

        # STEP 1: Symmetry Breaking FIRST (Level 1: Grid D_4)
        print(f"\n  Step 1: Adding symmetry breaking constraints (FIRST)...")
        symmetry_clauses = self.encode_symmetry_breaking()
        print(f"    Level 1 Symmetry: {symmetry_clauses} unit clauses")

        # STEP 2: Position constraints
        print(f"\n  Step 2: Adding position constraints...")
        position_clauses = self.encode_position_constraints()
        print(f"    Position: {position_clauses} clauses")

        # STEP 3: Distance constraints with K-specific UB cutoff
        print(f"\n  Step 3: Adding distance constraints with K-specific UB cutoff...")
        _ = self.encode_distance_constraints(initial_k)

        total_initialization_time = time.time() - initialization_start_time
        print(f"\n  Total base constraints encoding time: {total_initialization_time:.3f}s")

        # Garbage collection
        gc.collect()
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass

        self.base_constraints_added = True

        print(f"Dual-optimized solver initialized:")
        print(f"  UB cutoff: distance > {effective_ub} eliminated")
        print(f"  Symmetry: v_1 restricted to first quadrant (~{self.symmetry_stats['theoretical_speedup']}x)")
        print(f"  Final stats: vars={self.persistent_solver.nof_vars()}, clauses={self.persistent_solver.nof_clauses()}")

    def encode_bandwidth_constraints_for_k(self, K):
        """Encode bandwidth <= K constraints with cutoff optimization"""
        if K in self.current_k_constraints:
            print(f"  K={K} constraints already added")
            return []

        effective_k = K

        new_clauses = []
        edges_processed = 0

        print(f"  Encoding bandwidth constraints for K={effective_k}...")
        encode_start_time = time.time()

        for edge_id in self.Tx_vars:
            tx_vars = self.Tx_vars[edge_id]['vars']
            ty_vars = self.Ty_vars[edge_id]['vars']
            edges_processed += 1

            # Tx <= K
            if (effective_k + 1) in tx_vars:
                new_clauses.append([-tx_vars[effective_k + 1]])

            # Ty <= K
            if (effective_k + 1) in ty_vars:
                new_clauses.append([-ty_vars[effective_k + 1]])

            # Sum constraint: Tx + Ty <= K
            # Clause ¬Tx[i] ∨ ¬Ty[K-i+1] bans Tx≥i ∧ Ty≥K-i+1 (i.e. Tx+Ty > K).
            # The symmetric clause ¬Ty[i] ∨ ¬Tx[K-i+1] is IDENTICAL by commutativity
            # of ∨: the second "direction" loop would just regenerate the same set of
            # clauses in reverse order — so only one direction is needed.
            for i in range(1, effective_k + 1):
                remaining = effective_k - i  # always >= 0 since i <= effective_k
                if i in tx_vars and (remaining + 1) in ty_vars:
                    new_clauses.append([-tx_vars[i], -ty_vars[remaining + 1]])

        self.current_k_constraints.add(K)

        encode_time = time.time() - encode_start_time
        print(f"  Generated {len(new_clauses)} bandwidth clauses for K={effective_k}")
        print(f"  Encode time: {encode_time:.3f}s")

        return new_clauses

    def _extract_positions_from_model(self, model):
        """Extract vertex positions from SAT solution"""
        posset = {lit for lit in model if lit > 0}

        positions = {}
        for v in range(1, self.n + 1):
            for pos in range(1, self.grid_side + 1):
                if self.X_vars[v][pos-1] in posset:
                    positions[f'X_{v}'] = pos
                    break
            for pos in range(1, self.grid_side + 1):
                if self.Y_vars[v][pos-1] in posset:
                    positions[f'Y_{v}'] = pos
                    break

        return positions

    def _calculate_bandwidth(self, positions):
        """Calculate bandwidth from vertex positions"""
        max_distance = 0
        edge_distances = []

        for u, v in self.edges:
            x_u = positions.get(f'X_{u}', 0)
            y_u = positions.get(f'Y_{u}', 0)
            x_v = positions.get(f'X_{v}', 0)
            y_v = positions.get(f'Y_{v}', 0)

            distance = abs(x_u - x_v) + abs(y_u - y_v)
            max_distance = max(max_distance, distance)
            edge_distances.append((u, v, distance))

        return max_distance, edge_distances

    def extract_actual_bandwidth(self, model):
        """Extract actual bandwidth from SAT solution"""
        positions = self._extract_positions_from_model(model)
        bandwidth, _ = self._calculate_bandwidth(positions)
        return bandwidth

    def export_solution_to_file(self, model, K, actual_bandwidth):
        """Atomically replace the artifact with the latest verified incumbent."""
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
            vertex: (positions[f'X_{vertex}'], positions[f'Y_{vertex}'])
            for vertex in range(1, self.n + 1)
        }
        host = HostGrid(self.grid_side, self.grid_side, "ceil-sqrt-square")
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
            method_id=Path(script).stem,
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
            print(f"  Verification exported to: {self.verification_output_file}")
        except Exception as e:
            print(f"  Warning: Could not write verification file: {e}")

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

    def solve_with_incremental_sat(self, upper_bound):
        """Main incremental SAT solving with dual optimization"""
        print(f"\nINCREMENTAL SAT + UB CUTOFF + SYMMETRY BREAKING")
        print(f"Strategy: Monotone strengthening with dual optimization")
        print(f"Theoretical UB: {self.theoretical_ub}")
        print(f"UB (T-var depth): {self.upper_bound_cutoff}")
        print(f"K_start (max warmup): {self.k_start}")
        print(f"Symmetry: v_1 in first quadrant (~{self.symmetry_stats['theoretical_speedup']}x)")
        print(f"Solver: {self.solver_type.upper()}")

        current_k = upper_bound

        # Initialize with dual optimization
        self._initialize_persistent_solver(current_k)

        optimal_k = None
        cumulative_solve_time = 0.0
        solver_stats = {
            'total_solves': 0,
            'sat_results': 0,
            'unsat_results': 0,
            'smart_jumps': 0,
            'clauses_added': 0
        }

        while current_k >= 1:
            print(f"\nTesting K = {current_k}")
            solver_stats['total_solves'] += 1

            bandwidth_clauses = self.encode_bandwidth_constraints_for_k(current_k)
            for clause in bandwidth_clauses:
                self.persistent_solver.add_clause(clause)

            solver_stats['clauses_added'] += len(bandwidth_clauses)
            bandwidth_clauses.clear()

            solve_start = time.time()
            result = self.persistent_solver.solve()
            solve_time = time.time() - solve_start
            cumulative_solve_time += solve_time

            print(f"  Solve time: {solve_time:.3f}s")

            if result:
                solver_stats['sat_results'] += 1
                print(f"SATISFIABLE: K = {current_k}")

                model = self.persistent_solver.get_model()
                self.last_model = model
                actual_bandwidth = self.extract_actual_bandwidth(model)

                print(f"  Actual bandwidth: {actual_bandwidth}")

                # Export verification output to file if configured
                self.export_solution_to_file(model, current_k, actual_bandwidth)

                print(f"METRIC: best_k_so_far={actual_bandwidth}")
                print(f"METRIC: sat_at_k={current_k}")
                print(f"METRIC: sat_solve_time_s={solve_time:.6f}")
                print(f"METRIC: cumulative_solve_time_s={cumulative_solve_time:.6f}")
                print(f"METRIC: total_solver_clauses={self.persistent_solver.nof_clauses()}")

                if actual_bandwidth < current_k:
                    print(f"SMART JUMP: {current_k} → {actual_bandwidth}")
                    optimal_k = actual_bandwidth
                    current_k = actual_bandwidth - 1
                    solver_stats['smart_jumps'] += 1
                else:
                    optimal_k = current_k
                    current_k -= 1
            else:
                solver_stats['unsat_results'] += 1
                print(f"UNSATISFIABLE: K = {current_k}")
                print(f"Optimal bandwidth: {optimal_k}")
                break

        if optimal_k is not None:
            if solver_stats['unsat_results']:
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

        print(f"\nDUAL-OPTIMIZED SOLVER COMPLETE")
        print(f"="*60)
        print(f"Final optimal bandwidth: {optimal_k}")
        print(f"Total solves: {solver_stats['total_solves']}")
        print(f"SAT: {solver_stats['sat_results']}, UNSAT: {solver_stats['unsat_results']}")
        print(f"Smart jumps: {solver_stats['smart_jumps']}")
        print(f"Benefits: UB cutoff + ~{self.symmetry_stats['theoretical_speedup']}x symmetry")
        print(f"="*60)

        return optimal_k

    def cleanup_solver(self):
        """Clean up persistent solver"""
        if self.persistent_solver is not None:
            print(f"Cleaning up solver...")
            self.persistent_solver.delete()
            self.persistent_solver = None
            self.base_constraints_added = False
            self.current_k_constraints.clear()

    def solve_bandwidth_optimization(self, start_k=None, end_k=None):
        """Main solve function with dual optimization"""
        if start_k is None:
            start_k = 1
        if end_k is None:
            end_k = self.k_start

        print(f"\n" + "="*80)
        print(f"2D BANDWIDTH OPTIMIZATION - no-ME, UB=max(DRSA, theoretical)")
        print(f"Graph: {self.n} nodes, {len(self.edges)} edges")
        print(f"Strategy: UB Cutoff + Symmetry Breaking + Monotone Strengthening")
        print(f"Theoretical UB: {self.theoretical_ub}")
        print(f"DRSA UB: {self.drsa_ub}")
        print(f"Active UB (max): {self.upper_bound_cutoff}")
        print(f"Symmetry reduction: ~{self.symmetry_stats['theoretical_speedup']}x")
        print(f"Testing range: K = {start_k} to {end_k}")
        print(f"="*80)

        try:
            optimal_k = self.solve_with_incremental_sat(end_k)
            return optimal_k
        finally:
            self.cleanup_solver()


def test_dual_optimized_solver():
    """Test the dual-optimized solver"""
    print("=== DUAL-OPTIMIZED SOLVER TESTS ===")

    # Triangle
    print(f"\n" + "="*50)
    print(f"Test 1: Triangle")
    print(f"="*50)

    n1 = 3
    edges1 = [(1, 2), (2, 3), (1, 3)]

    solver1 = IncrementalBandwidthSolverCutoffSymmetry(n1, 'glucose42')
    solver1.set_graph_edges(edges1)
    solver1.create_position_variables()
    solver1.create_distance_variables()

    optimal1 = solver1.solve_bandwidth_optimization(start_k=1, end_k=4)
    print(f"Triangle result: {optimal1}")

    # Path
    print(f"\n" + "="*50)
    print(f"Test 2: Path")
    print(f"="*50)

    n2 = 4
    edges2 = [(1, 2), (2, 3), (3, 4)]

    solver2 = IncrementalBandwidthSolverCutoffSymmetry(n2, 'cadical195')
    solver2.set_graph_edges(edges2)
    solver2.create_position_variables()
    solver2.create_distance_variables()

    optimal2 = solver2.solve_bandwidth_optimization(start_k=1, end_k=6)
    print(f"Path result: {optimal2}")

    # Cycle
    print(f"\n" + "="*50)
    print(f"Test 3: Cycle")
    print(f"="*50)

    n3 = 5
    edges3 = [(1, 2), (2, 3), (3, 4), (4, 5), (1, 5)]

    solver3 = IncrementalBandwidthSolverCutoffSymmetry(n3, 'glucose42')
    solver3.set_graph_edges(edges3)
    solver3.create_position_variables()
    solver3.create_distance_variables()

    optimal3 = solver3.solve_bandwidth_optimization(start_k=1, end_k=8)
    print(f"Cycle result: {optimal3}")

    print(f"\n" + "="*80)
    print(f"RESULTS SUMMARY (Dual Optimized: UB Cutoff + Symmetry)")
    print(f"="*80)
    print(f"Triangle (3 nodes): {optimal1}")
    print(f"Path (4 nodes): {optimal2}")
    print(f"Cycle (5 nodes): {optimal3}")
    print(f"="*80)


if __name__ == '__main__':
    import sys

    if len(sys.argv) >= 2:
        mtx_file = sys.argv[1]
        solver_type = sys.argv[2] if len(sys.argv) >= 3 else 'cadical195'
        verification_file = sys.argv[3] if len(sys.argv) >= 4 else None

        print("=" * 80)
        print("2D BANDWIDTH OPTIMIZATION - DUAL OPTIMIZED SOLVER")
        print("=" * 80)
        print(f"File: {mtx_file}")
        print(f"Solver: {solver_type.upper()}")
        if verification_file:
            print(f"Verification output: {verification_file}")

        # Search for file
        if not os.path.exists(mtx_file):
            search_paths = [mtx_file]

            found_file = None
            for path in search_paths:
                if os.path.exists(path):
                    found_file = path
                    break

            if found_file is None:
                print(f"Error: File '{mtx_file}' not found")
                sys.exit(1)

            mtx_file = found_file

        # Parse MTX file
        def _parse_mtx_file_legacy(filename):
            """
            Parse MTX file and return n, edges

            Handles MatrixMarket format:
            - Comments and metadata parsing
            - Self-loop removal
            - Undirected graph processing only
            - Error handling for malformed files
            """
            print(f"Reading MTX file: {os.path.basename(filename)}")

            try:
                with open(filename, 'r') as f:
                    lines = f.readlines()
            except FileNotFoundError:
                print(f"Error: File {filename} not found")
                return None, None

            header_found = False
            edges_set = set()
            n = 0

            for line_num, line in enumerate(lines, 1):
                line = line.strip()

                if not line:
                    continue

                # Handle comments and metadata
                if line.startswith('%'):
                    continue

                # Parse header (rows cols nnz)
                if not header_found:
                    try:
                        parts = line.split()
                        if len(parts) >= 3:
                            rows, cols, nnz = map(int, parts[:3])
                            n = max(rows, cols)  # Handle non-square matrices
                            print(f"Matrix: {rows}×{cols}, {nnz} entries")
                            print(f"Graph: undirected, unweighted (dataset standard)")
                            header_found = True
                            continue
                    except ValueError:
                        print(f"Warning: bad header at line {line_num}: {line}")
                        continue

                # Parse edges
                try:
                    parts = line.split()
                    if len(parts) >= 2:
                        u, v = int(parts[0]), int(parts[1])
                        # Ignore weights (parts[2]) - dataset is unweighted

                        if u == v:  # skip self-loops
                            continue

                        # Always convert to undirected edge (sorted tuple)
                        edge = tuple(sorted([u, v]))

                        if edge not in edges_set:
                            edges_set.add(edge)

                except (ValueError, IndexError):
                    print(f"Warning: bad edge at line {line_num}: {line}")
                    continue

            edges = list(edges_set)
            print(f"Loaded: {n} vertices, {len(edges)} edges")
            return n, edges

        try:
            parsed_graph = parse_mtx_graph(mtx_file)
            n, edges = parsed_graph.n_vertices, parsed_graph.edges
            print(
                f"Loaded: {n} vertices, {len(edges)} edges "
                f"({parsed_graph.interpretation})"
            )
        except ValueError as exc:
            print(f"Failed to parse MTX file: {exc}")
            n, edges = None, None
        if n is None or edges is None:
            print("Failed to parse MTX file")
            sys.exit(1)

        solver = IncrementalBandwidthSolverCutoffSymmetry(n, solver_type)
        solver.set_graph_edges(edges)
        solver.create_position_variables()
        solver.create_distance_variables()

        # Set verification output file if provided
        if verification_file:
            solver.set_verification_output_file(verification_file)
            solver.set_verification_context(mtx_file, parsed_graph)

        start_time = time.time()
        optimal_bandwidth = solver.solve_bandwidth_optimization()
        solve_time = time.time() - start_time

        print(f"\n" + "="*80)
        print(f"FINAL RESULTS")
        print(f"="*80)
        print(f"Optimal bandwidth: {optimal_bandwidth}")
        print(f"Total time: {solve_time:.2f}s")
        print(f"="*80)

    else:
        print("Usage: python incremental_bandwidth_solver_cutoff_symmetry_nodrsa.py [mtx_file] [solver] [verification_file]")
        print("\nRunning tests...")
        test_dual_optimized_solver()
