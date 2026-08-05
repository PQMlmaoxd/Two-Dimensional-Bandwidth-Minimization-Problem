from __future__ import annotations

import argparse
import json
import math
import sys
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from pysat.formula import IDPool

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from python.solvers.solver_factory import create_sat_solver
from python.encoders.distance_encoder_cutoff import (
    calculate_theoretical_upper_bound,
    encode_abs_distance_cutoff,
)
from python.encoders.position_constraints import (
    create_position_variables,
    encode_all_position_constraints,
)
from python.encoders.symmetry_breaking import (
    encode_rectangular_anchor_symmetry,
    select_anchor_vertex,
)
from python.utils.encoding_metrics import (
    MetricRecorder,
    cutoff_clause_count,
    seq_amo_aux,
    seq_amo_clauses,
    seq_exactly_one_clauses,
    threshold_clauses_per_edge,
)
from python.utils.host_grid import HostGrid, deterministic_placement
from python.utils.mtx_parser import ParsedMtxGraph, parse_mtx_graph
from python.utils.solution_verifier import (
    VerificationResult,
    normalize_edges,
    verify_placement,
)
from python.utils.verification_io import (
    build_verification_document,
    sha256_file,
    write_json_atomic,
)


OPTIMAL_BY_UNSAT = "OPTIMAL_BY_UNSAT"
OPTIMAL_BY_BOUND = "OPTIMAL_BY_BOUND"
FEASIBLE = "FEASIBLE"
UNKNOWN = "UNKNOWN"
INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass
class TwoRowResult:
    outcome: str
    optimal_k: int | None
    incumbent_k: int | None
    lower_bound: int
    certified_lower_bound: int
    positions: dict[int, tuple[int, int]] | None
    optimality_basis: str | None
    termination_reason: str
    witness_valid: bool
    sa_k: int | None
    sa_probe_status: str
    initial_ub: int
    initial_ub_source: str
    generated_vars_total: int
    generated_base_clauses: int
    generated_threshold_clauses: int
    formula_build_wall_s: float
    cumulative_solve_wall_s: float


def _build_adjacency(n_vertices: int, edges: Sequence[tuple[int, int]]) -> list[list[int]]:
    adjacency = [[] for _ in range(n_vertices + 1)]
    for u, v in edges:
        adjacency[u].append(v)
        adjacency[v].append(u)
    for neighbors in adjacency:
        neighbors.sort()
    return adjacency


def _component_lower_bound(n_vertices: int, adjacency: Sequence[Sequence[int]]) -> int:
    """Use the exact two-row anticode diameter inside each component."""

    unseen = set(range(1, n_vertices + 1))
    lower_bound = 0
    while unseen:
        root = min(unseen)
        queue = deque([root])
        component: list[int] = []
        unseen.remove(root)
        while queue:
            vertex = queue.popleft()
            component.append(vertex)
            for neighbor in adjacency[vertex]:
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    queue.append(neighbor)
        if len(component) <= 1:
            continue

        diameter = 0
        component_set = set(component)
        for source in component:
            distances = {source: 0}
            bfs = deque([source])
            while bfs:
                vertex = bfs.popleft()
                for neighbor in adjacency[vertex]:
                    if neighbor in component_set and neighbor not in distances:
                        distances[neighbor] = distances[vertex] + 1
                        bfs.append(neighbor)
            diameter = max(diameter, max(distances.values()))
        occupied_diameter = (len(component) + 1) // 2
        lower_bound = max(lower_bound, (occupied_diameter + diameter - 1) // diameter)
    return lower_bound


def _strip_degree_lower_bound(max_degree: int, cols: int) -> int:
    if max_degree <= 0:
        return 0
    for bandwidth in range(1, cols + 1):
        reachable_neighbors = min(cols, 2 * bandwidth + 1) - 1
        reachable_neighbors += min(cols, 2 * bandwidth - 1)
        if max_degree <= reachable_neighbors:
            return bandwidth
    return cols


class IncrementalTwoRowBandwidthSolver:
    """Exact incremental solver for the fixed 2 x ceil(n/2) L1 host."""

    def __init__(
        self,
        n_vertices: int,
        edges: Sequence[tuple[int, int]],
        *,
        solver_type: str = "cadical195",
        sa_k: int | None = None,
        instance_sha256: str | None = None,
        matrix_rows: int | None = None,
        matrix_cols: int | None = None,
        graph_interpretation: str | None = None,
        matrix_declared_entries: int | None = None,
        matrix_field: str = "pattern",
        matrix_symmetry: str = "general",
        instance_file: str | None = None,
        verification_output_file: str | None = None,
        lin_multiplier: float = 1.0,
        emit_metrics: bool = True,
        verbose: bool = True,
    ) -> None:
        self.n = n_vertices
        self.edges = normalize_edges(n_vertices, edges)
        self.host = HostGrid.two_row(n_vertices)
        self.solver_type = solver_type
        self.sa_k = sa_k
        self.instance_sha256 = instance_sha256
        self.matrix_rows = matrix_rows if matrix_rows is not None else n_vertices
        self.matrix_cols = matrix_cols if matrix_cols is not None else n_vertices
        self.graph_interpretation = graph_interpretation or "square-adjacency-v1"
        self.matrix_declared_entries = (
            matrix_declared_entries if matrix_declared_entries is not None else len(self.edges)
        )
        self.matrix_field = matrix_field
        self.matrix_symmetry = matrix_symmetry
        self.instance_file = instance_file
        self.verification_output_file = verification_output_file
        self.verbose = verbose
        if lin_multiplier < 1 or not math.isclose(lin_multiplier * 2, round(lin_multiplier * 2)):
            raise ValueError("lin_multiplier must be a positive multiple of 0.5")
        self.lin_multiplier = lin_multiplier

        run_id = f"two-row-cutoff-{uuid.uuid4().hex[:12]}"
        self.metrics = MetricRecorder(run_id=run_id, emit_stdout=emit_metrics)
        self.vpool = IDPool()
        self.solver = None
        self.X_vars: dict[int, list[int]] = {}
        self.Y_vars: dict[int, list[int]] = {}
        self.Tx_vars: dict[tuple[int, int], dict[int, int]] = {}
        self.Ty_vars: dict[tuple[int, int], dict[int, int]] = {}
        self.thresholds_added: set[int] = set()
        self.anchor_vertex = 1
        self.generated_base_clauses = 0
        self.generated_threshold_clauses = 0
        self.formula_build_wall_s = 0.0
        self.cumulative_solve_wall_s = 0.0
        self._last_verified_positions: dict[int, tuple[int, int]] = {}

        self.adjacency = _build_adjacency(self.n, self.edges)
        self.lower_bound = self._calculate_lower_bound()
        self.lin_ub = calculate_theoretical_upper_bound(self.n)
        self.requested_ub, self.lin_multiplier = self._scaled_lin_start()
        self.incumbent_positions: dict[int, tuple[int, int]] | None = None
        self.incumbent_k: int | None = None
        self.initial_ub = self.requested_ub
        self.initial_ub_source = "lin-scaled-pending"
        self.x_cutoff = min(self.initial_ub, self.host.cols - 1)
        self.y_cutoff = min(self.initial_ub, self.host.rows - 1)

        self.metrics.emit(
            "graph",
            n=self.n,
            m=len(self.edges),
            matrix_rows=self.matrix_rows,
            matrix_cols=self.matrix_cols,
            graph_interpretation=self.graph_interpretation,
            host_policy=self.host.policy,
            host_rows=self.host.rows,
            host_cols=self.host.cols,
            host_capacity=self.host.capacity,
            empty_cells=self.host.capacity - self.n,
            encoding="cutoff",
            solver=self.solver_type,
            instance_sha256=self.instance_sha256,
        )
        self.metrics.emit(
            "ub",
            initial_ub=self.initial_ub,
            initial_ub_source=self.initial_ub_source,
            requested_ub=self.requested_ub,
            ub_witness_valid=False,
            lower_bound=self.lower_bound,
            x_cutoff=self.x_cutoff,
            y_cutoff=self.y_cutoff,
            lin_delta=self.lin_ub,
            lin_multiplier=self.lin_multiplier,
            lin_delta_used=True,
            drsa_used=False,
            sa_k=self.sa_k,
        )

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def _create_solver(self):
        return create_sat_solver(self.solver_type)

    def _calculate_lower_bound(self) -> int:
        if not self.edges:
            return 0
        max_degree = max(len(self.adjacency[v]) for v in range(1, self.n + 1))
        return max(
            1,
            _strip_degree_lower_bound(max_degree, self.host.cols),
            _component_lower_bound(self.n, self.adjacency),
        )

    def _scaled_lin_start(self) -> tuple[int, float]:
        if self.lin_ub == 0:
            return 0, self.lin_multiplier
        half_steps = round(self.lin_multiplier * 2)
        while True:
            requested = math.ceil(half_steps * self.lin_ub / 2)
            if requested >= self.lower_bound:
                return min(requested, self.host.diameter), half_steps / 2
            half_steps += 1

    def _establish_initial_incumbent(self) -> None:
        candidate = self.requested_ub
        while True:
            self.x_cutoff = min(candidate, self.host.cols - 1)
            self.y_cutoff = min(candidate, self.host.rows - 1)
            self._build_formula()
            sat, verification, _ = self._solve_k(candidate)
            if sat:
                self.initial_ub = candidate
                self.initial_ub_source = (
                    f"lin-delta={self.lin_ub}, multiplier={self.lin_multiplier:g}"
                )
                self._record_incumbent(verification)
                self.metrics.emit(
                    "initial_witness",
                    initial_ub=self.initial_ub,
                    initial_ub_source=self.initial_ub_source,
                    requested_ub=self.requested_ub,
                    ub_witness_valid=True,
                )
                return
            if candidate >= self.host.diameter:
                raise RuntimeError("No feasible placement exists at the host diameter")
            candidate += 1
            self.metrics.emit("lin_start_expand", rejected_k=candidate - 1, next_k=candidate)
            self._reset_formula()

    def _seed_edgeless_incumbent(self) -> None:
        positions = deterministic_placement(list(range(1, self.n + 1)), self.host)
        verification = verify_placement(
            self.n,
            self.edges,
            self.host,
            positions,
            tested_k=0,
        )
        if not verification.valid:
            raise RuntimeError(f"Edgeless witness failed verification: {verification.error}")
        self._last_verified_positions = positions
        self.initial_ub = 0
        self.initial_ub_source = "edgeless"
        self._record_incumbent(verification)

    def _add_clauses(self, clauses: Iterable[list[int]]) -> int:
        count = 0
        for clause in clauses:
            self.solver.add_clause(clause)
            count += 1
        return count

    def _position_expected_counts(self) -> tuple[int, int]:
        rows, cols, n = self.host.rows, self.host.cols, self.n
        cell_variables = n * rows * cols + rows * cols * seq_amo_aux(n)
        cell_clauses = rows * cols * (3 * n + seq_amo_clauses(n))
        variables = n * (rows + cols)
        variables += n * (seq_amo_aux(rows) + seq_amo_aux(cols)) + cell_variables
        clauses = n * (
            seq_exactly_one_clauses(rows) + seq_exactly_one_clauses(cols)
        ) + cell_clauses
        return variables, clauses

    def _position_count_breakdown(self) -> dict[str, int]:
        rows, cols, n = self.host.rows, self.host.cols, self.n
        cells = rows * cols
        return {
            "coordinate_exact_vars": n * (rows + cols),
            "coordinate_seq_aux_vars": n * (seq_amo_aux(rows) + seq_amo_aux(cols)),
            "cell_indicator_vars": n * cells,
            "cell_seq_aux_vars": cells * seq_amo_aux(n),
            "position_axis_clauses": n
            * (
                seq_exactly_one_clauses(rows)
                + seq_exactly_one_clauses(cols)
            ),
            "cell_link_clauses": 3 * n * cells,
            "cell_amo_clauses": cells * seq_amo_clauses(n),
        }

    @staticmethod
    def _t_rule_breakdown(axis_size: int, cutoff: int) -> dict[str, int]:
        if cutoff <= 0:
            return {
                "activation": 0,
                "deactivation": 0,
                "base": 0,
                "monotonicity": 0,
            }
        return {
            "activation": 2 * cutoff * axis_size - cutoff * (cutoff + 1),
            "deactivation": 2 * (cutoff - 1) * axis_size - cutoff * (cutoff - 1),
            "base": axis_size,
            "monotonicity": cutoff - 1,
        }

    def _build_formula(self) -> None:
        if self.solver is not None:
            return
        started = time.monotonic()
        self.vpool = IDPool()
        self.thresholds_added.clear()
        self.Tx_vars.clear()
        self.Ty_vars.clear()
        self.solver = self._create_solver()

        self.X_vars, self.Y_vars = create_position_variables(
            self.n,
            self.vpool,
            rows=self.host.rows,
            cols=self.host.cols,
        )
        primary_vars = self.vpool.top
        self.metrics.emit("allocate_primary", generated_vars_added=primary_vars)

        self.anchor_vertex = select_anchor_vertex(self.adjacency)
        symmetry_clauses = self._add_clauses(
            encode_rectangular_anchor_symmetry(
                self.host.rows,
                self.host.cols,
                self.X_vars,
                self.Y_vars,
                self.anchor_vertex,
            )
        )
        expected_symmetry = 1 + self.host.cols // 2
        if symmetry_clauses != expected_symmetry:
            raise RuntimeError(
                f"Symmetry count mismatch: generated {symmetry_clauses}, expected {expected_symmetry}"
            )
        self.metrics.emit(
            "build_symmetry",
            anchor_vertex=self.anchor_vertex,
            generated_clauses_added=symmetry_clauses,
        )

        position_clauses = self._add_clauses(
            encode_all_position_constraints(
                self.n,
                self.X_vars,
                self.Y_vars,
                self.vpool,
            )
        )
        expected_position_vars, expected_position_clauses = self._position_expected_counts()
        position_breakdown = self._position_count_breakdown()
        if self.vpool.top != expected_position_vars:
            raise RuntimeError(
                f"Position variable mismatch: allocated {self.vpool.top}, "
                f"expected {expected_position_vars}"
            )
        if position_clauses != expected_position_clauses:
            raise RuntimeError(
                f"Position clause mismatch: generated {position_clauses}, "
                f"expected {expected_position_clauses}"
            )
        self.metrics.emit(
            "build_position",
            **position_breakdown,
            generated_vars_cumulative=self.vpool.top,
            generated_clauses_added=position_clauses,
            generated_clauses_cumulative=symmetry_clauses + position_clauses,
        )

        distance_started_vars = self.vpool.top
        distance_clauses = 0
        for edge in self.edges:
            u, v = edge
            x_clauses, tx = encode_abs_distance_cutoff(
                self.X_vars[u], self.X_vars[v], self.x_cutoff, self.vpool, f"Tx_{u}_{v}"
            )
            y_clauses, ty = encode_abs_distance_cutoff(
                self.Y_vars[u], self.Y_vars[v], self.y_cutoff, self.vpool, f"Ty_{u}_{v}"
            )
            distance_clauses += self._add_clauses(x_clauses)
            distance_clauses += self._add_clauses(y_clauses)
            self.Tx_vars[edge] = tx
            self.Ty_vars[edge] = ty

        expected_distance_vars = len(self.edges) * (self.x_cutoff + self.y_cutoff)
        actual_distance_vars = self.vpool.top - distance_started_vars
        if actual_distance_vars != expected_distance_vars:
            raise RuntimeError(
                f"Distance variable mismatch: allocated {actual_distance_vars}, "
                f"expected {expected_distance_vars}"
            )
        expected_distance = len(self.edges) * (
            cutoff_clause_count(self.host.cols, self.x_cutoff)
            + cutoff_clause_count(self.host.rows, self.y_cutoff)
        )
        if distance_clauses != expected_distance:
            raise RuntimeError(
                f"Cutoff distance clause mismatch: {distance_clauses}/{expected_distance}"
            )

        self.generated_base_clauses = symmetry_clauses + position_clauses + distance_clauses
        x_t_breakdown = self._t_rule_breakdown(self.host.cols, self.x_cutoff)
        y_t_breakdown = self._t_rule_breakdown(self.host.rows, self.y_cutoff)
        edge_count = len(self.edges)
        t_breakdown = {
            key: edge_count * (x_t_breakdown[key] + y_t_breakdown[key])
            for key in x_t_breakdown
        }
        axis_exclusion_clauses = distance_clauses - sum(t_breakdown.values())
        self.formula_build_wall_s += time.monotonic() - started
        self.metrics.emit(
            "build_distance",
            distance_threshold_vars=actual_distance_vars,
            distance_activation_clauses=t_breakdown["activation"],
            distance_deactivation_clauses=t_breakdown["deactivation"],
            distance_base_clauses=t_breakdown["base"],
            distance_monotonicity_clauses=t_breakdown["monotonicity"],
            distance_axis_exclusion_clauses=axis_exclusion_clauses,
            generated_vars_cumulative=self.vpool.top,
            generated_clauses_added=distance_clauses,
            generated_base_clauses=self.generated_base_clauses,
            formula_build_wall_s=round(self.formula_build_wall_s, 9),
            solver_reported_vars=self.solver.nof_vars(),
            solver_reported_clauses=self.solver.nof_clauses(),
        )

    def _reset_formula(self) -> None:
        if self.solver is not None:
            self.solver.delete()
        self.solver = None
        self.X_vars = {}
        self.Y_vars = {}
        self.Tx_vars = {}
        self.Ty_vars = {}
        self.thresholds_added.clear()
        self.generated_base_clauses = 0
        self.generated_threshold_clauses = 0

    def _threshold_clauses(self, k: int) -> list[list[int]]:
        if k < 0:
            raise ValueError("K cannot be negative")
        if k in self.thresholds_added:
            return []
        clauses: list[list[int]] = []
        for edge in self.edges:
            tx = self.Tx_vars[edge]
            ty = self.Ty_vars[edge]
            if k + 1 in tx:
                clauses.append([-tx[k + 1]])
            if k + 1 in ty:
                clauses.append([-ty[k + 1]])
            lower = max(1, k + 1 - self.y_cutoff)
            upper = min(k, self.x_cutoff)
            for x_distance in range(lower, upper + 1):
                y_distance = k - x_distance + 1
                if x_distance in tx and y_distance in ty:
                    clauses.append([-tx[x_distance], -ty[y_distance]])
        expected = len(self.edges) * threshold_clauses_per_edge(
            k, self.x_cutoff, self.y_cutoff
        )
        if len(clauses) != expected:
            raise RuntimeError(f"Threshold clause mismatch at K={k}: {len(clauses)}/{expected}")
        self.thresholds_added.add(k)
        return clauses

    def _decode_and_verify(self, model: Sequence[int], tested_k: int) -> VerificationResult:
        positive = {literal for literal in model if literal > 0}
        positions: dict[int, tuple[int, int]] = {}
        for vertex in range(1, self.n + 1):
            columns = [index + 1 for index, var in enumerate(self.X_vars[vertex]) if var in positive]
            rows = [index + 1 for index, var in enumerate(self.Y_vars[vertex]) if var in positive]
            if len(columns) != 1 or len(rows) != 1:
                return VerificationResult(
                    False,
                    None,
                    f"vertex {vertex} decoded to rows={rows}, columns={columns}",
                )
            positions[vertex] = (rows[0], columns[0])
        verification = verify_placement(
            self.n,
            self.edges,
            self.host,
            positions,
            tested_k=tested_k,
            anchor_vertex=self.anchor_vertex,
            enforce_two_row_anchor=True,
        )
        if verification.valid:
            self._last_verified_positions = positions
        return verification

    def _solve_k(self, k: int) -> tuple[bool, VerificationResult | None, float]:
        clauses = self._threshold_clauses(k)
        self._add_clauses(clauses)
        self.generated_threshold_clauses += len(clauses)
        axis_units_per_edge = int(k + 1 <= self.x_cutoff) + int(
            k + 1 <= self.y_cutoff
        )
        manhattan_per_edge = threshold_clauses_per_edge(
            k, self.x_cutoff, self.y_cutoff
        ) - axis_units_per_edge
        self.metrics.emit(
            "build_threshold",
            k=k,
            threshold_axis_unit_clauses=len(self.edges) * axis_units_per_edge,
            threshold_manhattan_clauses=len(self.edges) * manhattan_per_edge,
            generated_clauses_added=len(clauses),
            generated_threshold_clauses_cumulative=self.generated_threshold_clauses,
        )
        started = time.monotonic()
        sat = self.solver.solve()
        elapsed = time.monotonic() - started
        self.cumulative_solve_wall_s += elapsed
        verification = None
        if sat:
            model = self.solver.get_model()
            verification = self._decode_and_verify(model, k)
            if not verification.valid:
                raise RuntimeError(f"SAT model failed verification at K={k}: {verification.error}")
        self.metrics.emit(
            "solve",
            k=k,
            solver_result="SAT" if sat else "UNSAT",
            solve_wall_s=round(elapsed, 9),
            cumulative_solve_wall_s=round(self.cumulative_solve_wall_s, 9),
            witness_valid=verification.valid if verification else None,
            actual_bandwidth=verification.bandwidth if verification else None,
            solver_reported_vars=self.solver.nof_vars(),
            solver_reported_clauses=self.solver.nof_clauses(),
        )
        return sat, verification, elapsed

    def _record_incumbent(self, verification: VerificationResult) -> None:
        if not verification.valid or verification.bandwidth is None:
            raise RuntimeError("Cannot accept an invalid incumbent")
        self.incumbent_k = verification.bandwidth
        self.incumbent_positions = dict(self._last_verified_positions)
        self._write_incumbent_checkpoint()
        print(f"METRIC: best_k_so_far={self.incumbent_k}", flush=True)
        self.metrics.emit(
            "verify_witness",
            witness_valid=True,
            incumbent_k=self.incumbent_k,
        )

    def _write_incumbent_checkpoint(self) -> None:
        """Persist the latest verified witness for the external supervisor."""

        if not self.verification_output_file or self.incumbent_positions is None:
            return
        verification = verify_placement(
            self.n,
            self.edges,
            self.host,
            self.incumbent_positions,
            tested_k=self.incumbent_k,
        )
        if not verification.valid:
            raise RuntimeError(f"Incumbent checkpoint failed verification: {verification.error}")
        path = Path(self.verification_output_file)
        payload = self._verification_document(
            status="RUNNING_FEASIBLE",
            termination="in_progress",
            termination_k=None,
            certified_lower=self.lower_bound,
            optimal=None,
        )
        write_json_atomic(path, payload)

    def _write_witness(self, result: TwoRowResult) -> None:
        if not self.verification_output_file:
            return
        path = Path(self.verification_output_file)
        termination_k = None
        if result.incumbent_k is not None:
            termination_k = (
                result.incumbent_k - 1
                if result.outcome == OPTIMAL_BY_UNSAT
                else result.incumbent_k
            )
        payload = self._verification_document(
            status=result.outcome,
            termination=result.termination_reason,
            termination_k=termination_k,
            certified_lower=result.certified_lower_bound,
            optimal=result.optimal_k,
        )
        write_json_atomic(path, payload)

    def _verification_document(
        self,
        *,
        status: str,
        termination: str,
        termination_k: int | None,
        certified_lower: int,
        optimal: int | None,
    ) -> dict[str, object]:
        if self.incumbent_k is None or self.incumbent_positions is None:
            raise RuntimeError("Cannot write a verification artifact without an incumbent")
        graph = ParsedMtxGraph(
            n_vertices=self.n,
            edges=self.edges,
            rows=self.matrix_rows,
            cols=self.matrix_cols,
            interpretation=self.graph_interpretation,
            declared_entries=self.matrix_declared_entries,
            field=self.matrix_field,
            symmetry=self.matrix_symmetry,
        )
        return build_verification_document(
            instance_file=self.instance_file,
            instance_sha256=self.instance_sha256,
            graph=graph,
            method_id="two-row-cutoff",
            solver=self.solver_type,
            solver_script=Path(__file__).name,
            host=self.host,
            status=status,
            termination=termination,
            termination_k=termination_k,
            elapsed_s=self.cumulative_solve_wall_s,
            timeout_s=None,
            certified_lower=certified_lower,
            final_verified=self.incumbent_k,
            optimal=optimal,
            constraint_k=self.incumbent_k,
            positions=self.incumbent_positions,
        )

    def _finish(self, outcome: str, basis: str | None, reason: str) -> TwoRowResult:
        optimal_k = self.incumbent_k if outcome.startswith("OPTIMAL_") else None
        certified_lower_bound = (
            self.incumbent_k if optimal_k is not None else self.lower_bound
        )
        result = TwoRowResult(
            outcome=outcome,
            optimal_k=optimal_k,
            incumbent_k=self.incumbent_k,
            lower_bound=self.lower_bound,
            certified_lower_bound=certified_lower_bound,
            positions=dict(self.incumbent_positions) if self.incumbent_positions else None,
            optimality_basis=basis,
            termination_reason=reason,
            witness_valid=self.incumbent_positions is not None,
            sa_k=self.sa_k,
            sa_probe_status=getattr(self, "sa_probe_status", "NOT_REQUESTED"),
            initial_ub=self.initial_ub,
            initial_ub_source=self.initial_ub_source,
            generated_vars_total=self.vpool.top,
            generated_base_clauses=self.generated_base_clauses,
            generated_threshold_clauses=self.generated_threshold_clauses,
            formula_build_wall_s=self.formula_build_wall_s,
            cumulative_solve_wall_s=self.cumulative_solve_wall_s,
        )
        self.metrics.emit(
            "final",
            **{key: value for key, value in asdict(result).items() if key != "positions"},
        )
        print(f"FINAL_OUTCOME: {outcome}", flush=True)
        if optimal_k is not None:
            print(f"Optimal bandwidth: {optimal_k}", flush=True)
        elif self.incumbent_k is not None:
            print(f"Feasible bandwidth: {self.incumbent_k}", flush=True)
        self._write_witness(result)
        return result

    def solve(self) -> TwoRowResult:
        self._log(
            f"Two-row exact SAT: n={self.n}, m={len(self.edges)}, "
            f"host=2x{self.host.cols}, encoding=cutoff"
        )
        try:
            if self.edges:
                self._establish_initial_incumbent()
            else:
                self._seed_edgeless_incumbent()

            self._log(
                f"Verified initial UB={self.initial_ub} ({self.initial_ub_source}), "
                f"LB={self.lower_bound}"
            )
            if self.sa_k is None:
                self.sa_probe_status = "NOT_REQUESTED"
            elif self.sa_k < self.lower_bound:
                self.sa_probe_status = "REFERENCE_BELOW_LOWER_BOUND"
            elif self.sa_k >= self.incumbent_k:
                self.sa_probe_status = "SKIPPED_NOT_BETTER_THAN_UB"
            else:
                self.sa_probe_status = "PENDING"

            if not self.edges:
                if self.sa_probe_status == "NOT_REQUESTED":
                    self.sa_probe_status = "NOT_NEEDED"
                return self._finish(
                    OPTIMAL_BY_BOUND,
                    "edgeless graph",
                    "lower_bound_matches_incumbent",
                )
            if self.incumbent_k == self.lower_bound:
                if self.sa_probe_status == "PENDING":
                    self.sa_probe_status = "SKIPPED_BOUND_TIGHT"
                return self._finish(
                    OPTIMAL_BY_BOUND,
                    "verified UB equals lower bound",
                    "bound_tight",
                )

            if self.sa_probe_status == "PENDING":
                sat, verification, _ = self._solve_k(self.sa_k)
                if sat:
                    self.sa_probe_status = "SAT_WITNESS_VERIFIED"
                    self._record_incumbent(verification)
                else:
                    self.sa_probe_status = "UNSAT_REFERENCE_INCONSISTENCY"
                    self.metrics.emit(
                        "sa_probe",
                        sa_k=self.sa_k,
                        status=self.sa_probe_status,
                    )
                    self._reset_formula()
                    self._build_formula()

            while self.incumbent_k > self.lower_bound:
                target_k = self.incumbent_k - 1
                sat, verification, _ = self._solve_k(target_k)
                if not sat:
                    return self._finish(
                        OPTIMAL_BY_UNSAT,
                        f"UNSAT at K={target_k}",
                        "unsat_below_verified_incumbent",
                    )
                self._record_incumbent(verification)

            return self._finish(
                OPTIMAL_BY_BOUND,
                "verified UB equals lower bound",
                "lower_bound_matches_incumbent",
            )
        finally:
            if self.solver is not None:
                self.solver.delete()
                self.solver = None


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Exact SAT solver on a 2 x ceil(n/2) host")
    parser.add_argument("mtx_file")
    parser.add_argument("solver", nargs="?", default="cadical195")
    parser.add_argument("verification_file", nargs="?", default=None)
    parser.add_argument("--sa-k", type=int, default=None)
    parser.add_argument("--lin-multiplier", type=float, default=1.0)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    mtx_path = Path(args.mtx_file).resolve()
    try:
        parsed_graph = parse_mtx_graph(mtx_path)
        n_vertices, edges = parsed_graph.n_vertices, parsed_graph.edges
        instance_sha256 = sha256_file(mtx_path)
        print(f"INSTANCE_SHA256: {instance_sha256}", flush=True)
        solver = IncrementalTwoRowBandwidthSolver(
            n_vertices,
            edges,
            solver_type=args.solver,
            sa_k=args.sa_k,
            instance_sha256=instance_sha256,
            matrix_rows=parsed_graph.rows,
            matrix_cols=parsed_graph.cols,
            graph_interpretation=parsed_graph.interpretation,
            matrix_declared_entries=parsed_graph.declared_entries,
            matrix_field=parsed_graph.field,
            matrix_symmetry=parsed_graph.symmetry,
            instance_file=str(mtx_path),
            verification_output_file=args.verification_file,
            lin_multiplier=args.lin_multiplier,
            verbose=not args.quiet,
        )
        solver.solve()
        return 0
    except Exception as exc:
        error_event = {
            "schema": 1,
            "run_id": f"two-row-error-{uuid.uuid4().hex[:12]}",
            "seq": 1,
            "phase": "final",
            "outcome": INTERNAL_ERROR,
            "termination_reason": "exception",
            "witness_valid": False,
            "error": str(exc),
        }
        print(f"METRIC_JSON: {json.dumps(error_event, sort_keys=True)}", flush=True)
        print(f"FINAL_OUTCOME: {INTERNAL_ERROR}", flush=True)
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
