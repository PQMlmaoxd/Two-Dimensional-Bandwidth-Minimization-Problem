from __future__ import annotations

import io
import itertools
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from python.encoders.distance_encoder_cutoff import calculate_theoretical_upper_bound
from python.solvers.incremental_bandwidth_solver_two_row import (
    IncrementalTwoRowBandwidthSolver,
    OPTIMAL_BY_BOUND,
    OPTIMAL_BY_UNSAT,
)
from python.utils.encoding_metrics import (
    cutoff_clause_count,
    seq_amo_aux,
    seq_amo_clauses,
    seq_exactly_one_clauses,
    threshold_clauses_per_edge,
)
from python.utils.host_grid import HostGrid
from python.utils.mtx_parser import parse_mtx_file, parse_mtx_graph
from python.utils.solution_verifier import verify_placement


def complete_graph(n: int) -> list[tuple[int, int]]:
    return [(u, v) for u in range(1, n + 1) for v in range(u + 1, n + 1)]


def brute_force_optimum(n: int, edges: list[tuple[int, int]]) -> int:
    if not edges:
        return 0
    host = HostGrid.two_row(n)
    cells = [(row, col) for row in range(1, 3) for col in range(1, host.cols + 1)]
    best = host.diameter
    for chosen in itertools.permutations(cells, n):
        positions = {vertex: chosen[vertex - 1] for vertex in range(1, n + 1)}
        bandwidth = max(
            abs(positions[u][0] - positions[v][0])
            + abs(positions[u][1] - positions[v][1])
            for u, v in edges
        )
        best = min(best, bandwidth)
        if best == 1:
            break
    return best


def solve_quiet(
    n: int,
    edges: list[tuple[int, int]],
    sa_k=None,
    solver_type: str = "cadical195",
):
    with redirect_stdout(io.StringIO()):
        return IncrementalTwoRowBandwidthSolver(
            n,
            edges,
            solver_type=solver_type,
            sa_k=sa_k,
            emit_metrics=False,
            verbose=False,
        ).solve()


class HostAndVerifierTests(unittest.TestCase):
    def test_two_row_shape_and_capacity(self):
        for n in range(1, 21):
            host = HostGrid.two_row(n)
            self.assertEqual(host.rows, 2)
            self.assertEqual(host.cols, (n + 1) // 2)
            self.assertGreaterEqual(host.capacity, n)
            self.assertEqual(host.capacity - n, n % 2)
            self.assertEqual(host.diameter, host.cols)

    def test_verifier_rejects_malformed_witnesses(self):
        host = HostGrid.two_row(3)
        edges = [(1, 2), (2, 3)]
        self.assertFalse(verify_placement(3, edges, host, {1: (1, 1)}).valid)
        duplicate = {1: (1, 1), 2: (1, 1), 3: (2, 2)}
        self.assertFalse(verify_placement(3, edges, host, duplicate).valid)
        outside = {1: (1, 1), 2: (1, 2), 3: (3, 1)}
        self.assertFalse(verify_placement(3, edges, host, outside).valid)
        fractional = {1: (1.5, 1), 2: (1, 2), 3: (2, 2)}
        self.assertFalse(verify_placement(3, edges, host, fractional).valid)
        boolean = {1: (True, 1), 2: (1, 2), 3: (2, 2)}
        self.assertFalse(verify_placement(3, edges, host, boolean).valid)

    def test_mtx_parser_uses_rows_for_rectangular_row_support(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            rectangular = temp / "rectangular.mtx"
            rectangular.write_text(
                "%%MatrixMarket matrix coordinate pattern general\n"
                "3 2 4\n"
                "1 1\n"
                "2 1\n"
                "2 2\n"
                "3 2\n",
                encoding="utf-8",
            )
            self.assertEqual(parse_mtx_file(rectangular), (3, [(1, 2), (2, 3)]))

            truncated = temp / "truncated.mtx"
            truncated.write_text(
                "%%MatrixMarket matrix coordinate pattern symmetric\n"
                "3 3 2\n"
                "1 2\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "entry count mismatch"):
                parse_mtx_file(truncated)

            numeric = temp / "numeric.mtx"
            numeric.write_text(
                "%%MatrixMarket matrix coordinate real symmetric\n"
                "3 3 2\n"
                "1 2 0.0\n"
                "2 3 4.5\n",
                encoding="utf-8",
            )
            self.assertEqual(parse_mtx_file(numeric), (3, [(2, 3)]))

    def test_legacy_regular_edge_list_is_explicitly_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / "cycle4.mtx"
            legacy.write_text(
                "% legacy regular benchmark\n"
                "4 4 4\n"
                "1 2\n"
                "2 3\n"
                "3 4\n"
                "4 1\n",
                encoding="utf-8",
            )
            graph = parse_mtx_graph(legacy)
            self.assertEqual(graph.interpretation, "legacy-edge-list-v1")
            self.assertEqual(graph.n_vertices, 4)
            self.assertEqual(graph.edges, [(1, 2), (1, 4), (2, 3), (3, 4)])

            rectangular = Path(directory) / "invalid_legacy.mtx"
            rectangular.write_text("3 2 1\n1 1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "square n n m"):
                parse_mtx_graph(rectangular)


class CountFormulaTests(unittest.TestCase):
    def test_small_seqcounter_counts(self):
        self.assertEqual([seq_amo_aux(i) for i in range(1, 5)], [0, 0, 2, 3])
        self.assertEqual([seq_amo_clauses(i) for i in range(1, 5)], [0, 1, 5, 8])
        self.assertEqual([seq_exactly_one_clauses(i) for i in range(1, 5)], [1, 2, 6, 9])

    def test_distance_count_examples(self):
        self.assertEqual(cutoff_clause_count(4, 1), 16)
        self.assertEqual(cutoff_clause_count(4, 2), 23)
        self.assertEqual(threshold_clauses_per_edge(2, 4, 1), 2)

    def test_generated_cutoff_counts_are_exact(self):
        edges = [(1, 2), (2, 3), (3, 4), (4, 5)]
        with redirect_stdout(io.StringIO()):
            solver = IncrementalTwoRowBandwidthSolver(
                5, edges, emit_metrics=False, verbose=False
            )
            solver._build_formula()
            self.assertGreater(solver.vpool.top, 0)
            self.assertGreater(solver.generated_base_clauses, 0)
            position_event = next(
                event
                for event in solver.metrics.events
                if event["phase"] == "build_position"
            )
            distance_event = next(
                event
                for event in solver.metrics.events
                if event["phase"] == "build_distance"
            )
            position_vars = sum(
                position_event[key]
                for key in (
                    "coordinate_exact_vars",
                    "coordinate_seq_aux_vars",
                    "cell_indicator_vars",
                    "cell_seq_aux_vars",
                )
            )
            self.assertEqual(
                position_vars,
                solver.vpool.top - distance_event["distance_threshold_vars"],
            )
            distance_clauses = sum(
                distance_event[key]
                for key in (
                    "distance_activation_clauses",
                    "distance_deactivation_clauses",
                    "distance_base_clauses",
                    "distance_monotonicity_clauses",
                    "distance_axis_exclusion_clauses",
                )
            )
            self.assertEqual(
                distance_clauses,
                distance_event["generated_clauses_added"],
            )
            self.assertGreaterEqual(
                distance_event["distance_axis_exclusion_clauses"],
                0,
            )
            solver._reset_formula()


class ExactSolverTests(unittest.TestCase):
    def test_cadical195_and_cadical300_result_parity(self):
        edges = complete_graph(5)
        expected = solve_quiet(5, edges, solver_type="cadical195")
        candidate = solve_quiet(5, edges, solver_type="cadical300")
        self.assertEqual(candidate.optimal_k, expected.optimal_k)
        self.assertEqual(candidate.outcome, expected.outcome)
        self.assertTrue(candidate.witness_valid)

    def test_critical_optima(self):
        fixtures = [
            (1, [], 0),
            (2, [(1, 2)], 1),
            (4, [(1, 2), (2, 3), (3, 4)], 1),
            (4, complete_graph(4), 2),
            (5, complete_graph(5), 3),
            (6, [(u, v) for u in range(1, 4) for v in range(4, 7)], 2),
            (6, complete_graph(6), 3),
            (8, complete_graph(8), 4),
        ]
        for n, edges, expected in fixtures:
            with self.subTest(n=n):
                result = solve_quiet(n, edges)
                self.assertEqual(result.optimal_k, expected)
                self.assertIn(result.outcome, {OPTIMAL_BY_BOUND, OPTIMAL_BY_UNSAT})
                self.assertTrue(result.witness_valid)

    def test_lin_start_expands_to_verified_two_row_ub(self):
        self.assertEqual(calculate_theoretical_upper_bound(5), 2)
        result = solve_quiet(5, complete_graph(5))
        self.assertEqual(result.initial_ub, 3)
        self.assertEqual(result.initial_ub_source, "lin-delta=2, multiplier=1.5")
        self.assertEqual(result.optimal_k, 3)

    def test_lin_multiplier_is_raised_until_it_meets_the_lower_bound(self):
        solver = IncrementalTwoRowBandwidthSolver(
            10,
            complete_graph(10),
            lin_multiplier=1.0,
            emit_metrics=False,
            verbose=False,
        )
        self.assertEqual(solver.lower_bound, 5)
        self.assertEqual(solver.lin_ub, 4)
        self.assertEqual(solver.lin_multiplier, 1.5)
        self.assertEqual(solver.requested_ub, 5)

    def test_sa_value_requires_a_sat_witness(self):
        result = solve_quiet(5, complete_graph(5), sa_k=2)
        self.assertEqual(result.sa_probe_status, "REFERENCE_BELOW_LOWER_BOUND")
        self.assertEqual(result.optimal_k, 3)

    def test_unsat_sa_probe_rebuilds_from_safe_ub(self):
        edges = [(u, v) for u in range(1, 4) for v in range(4, 8)]
        result = solve_quiet(7, edges, sa_k=2)
        self.assertEqual(result.sa_probe_status, "UNSAT_REFERENCE_INCONSISTENCY")
        self.assertEqual(result.optimal_k, 3)

    def test_all_labeled_graphs_through_four_vertices(self):
        for n in range(1, 5):
            possible_edges = complete_graph(n)
            for mask in range(1 << len(possible_edges)):
                edges = [edge for bit, edge in enumerate(possible_edges) if mask & (1 << bit)]
                expected = brute_force_optimum(n, edges)
                result = solve_quiet(n, edges)
                self.assertEqual(result.optimal_k, expected, (n, mask))

    @unittest.skipUnless(os.environ.get("EXHAUSTIVE_2XZ") == "1", "nightly exhaustive gate")
    def test_all_labeled_graphs_on_five_vertices(self):
        n = 5
        possible_edges = complete_graph(n)
        for mask in range(1 << len(possible_edges)):
            edges = [edge for bit, edge in enumerate(possible_edges) if mask & (1 << bit)]
            expected = brute_force_optimum(n, edges)
            result = solve_quiet(n, edges)
            self.assertEqual(result.optimal_k, expected, mask)


if __name__ == "__main__":
    unittest.main()
