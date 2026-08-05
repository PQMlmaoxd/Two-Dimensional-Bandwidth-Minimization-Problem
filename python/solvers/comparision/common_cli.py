from __future__ import annotations

import os
import sys
import time
from typing import Iterable, Optional, Type

from python.utils.mtx_parser import parse_mtx_graph


def parse_mtx_file(filename: str):
    """Parse a graph using the shared rows-as-vertices policy."""
    print(f"Reading MTX file: {os.path.basename(filename)}")
    try:
        graph = parse_mtx_graph(filename)
    except (OSError, ValueError) as exc:
        print(f"Error: Could not parse {filename}: {exc}")
        return None, None
    print(
        f"Loaded: {graph.n_vertices} vertices, {len(graph.edges)} edges "
        f"({graph.interpretation})"
    )
    return graph.n_vertices, graph.edges


def resolve_mtx_path(mtx_file: str) -> Optional[str]:
    if os.path.exists(mtx_file):
        return mtx_file

    search_paths = [
        mtx_file,
        os.path.join("mtx", mtx_file),
        os.path.join("mtx", "regular", mtx_file),
        os.path.join("mtx", "test", mtx_file),
        os.path.join("mtx", "test1", mtx_file),
        os.path.join("mtx", "bench", mtx_file),
        os.path.join("mtx", "large", mtx_file),
        os.path.join("mtx", "large1", mtx_file),
        os.path.join("mtx", "large2", mtx_file),
        os.path.join("mtx", "large3", mtx_file),
        os.path.join("mtx", "large4", mtx_file),
    ]

    for path in search_paths:
        if os.path.exists(path):
            return path
    return None


def run_solver_cli(
    solver_class: Type,
    variant_title: str,
    argv: Optional[Iterable[str]] = None,
):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        script_name = os.path.basename(sys.argv[0])
        print(f"Usage: python {script_name} [mtx_file] [solver] [verification_file]")
        return 2

    mtx_file = args[0]
    solver_type = args[1] if len(args) >= 2 else "cadical195"
    verification_file = args[2] if len(args) >= 3 else None

    print("=" * 80)
    print(variant_title)
    print("=" * 80)
    print(f"File: {mtx_file}")
    print(f"Solver: {solver_type.upper()}")
    if verification_file:
        print(f"Verification output: {verification_file}")

    resolved = resolve_mtx_path(mtx_file)
    if resolved is None:
        print(f"Error: File '{mtx_file}' not found")
        return 1

    n, edges = parse_mtx_file(resolved)
    if n is None or edges is None:
        print("Failed to parse MTX file")
        return 1

    solver = solver_class(n, solver_type)
    solver.set_graph_edges(edges)
    solver.create_position_variables()
    solver.create_distance_variables()

    if verification_file:
        solver.set_verification_output_file(verification_file)
        solver.set_verification_context(resolved, parse_mtx_graph(resolved))

    start_time = time.time()
    optimal_bandwidth = solver.solve_bandwidth_optimization()
    solve_time = time.time() - start_time

    print("\n" + "=" * 80)
    print("FINAL RESULTS")
    print("=" * 80)
    print(f"Optimal bandwidth: {optimal_bandwidth}")
    print(f"Total time: {solve_time:.2f}s")
    print("=" * 80)
    return 0
