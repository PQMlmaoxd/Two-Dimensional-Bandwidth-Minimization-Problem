"""CP Optimizer backend for 2DBMP."""

import argparse
import importlib.util
import os
import sys
import time
from pathlib import Path


CLI_EXIT_OK = 0
CLI_EXIT_SETUP_ERROR = 2
CLI_EXIT_SOLVER_ERROR = 3


def _load_ilp_helpers():
    ilp_path = Path(__file__).resolve().parent / "2dbmp_ilp.py"
    spec = importlib.util.spec_from_file_location("two_dbmp_ilp_helpers", ilp_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ilp = _load_ilp_helpers()
compute_grid_dimensions = _ilp.compute_grid_dimensions
read_graph_from_file = _ilp.read_graph_from_file
make_example_graph = _ilp.make_example_graph
select_anchor_vertex = _ilp.select_anchor_vertex
print_run_header = _ilp.print_run_header


def _emit_setup_error(package_name):
    print(f"[ERROR] {package_name} not installed. Use: pip install {package_name}")
    _emit_cp_result(None, "SETUP_ERROR", 0.0)
    return {"exit_code": CLI_EXIT_SETUP_ERROR, "cp_status": "SETUP_ERROR"}


def _emit_solver_error(message, cp_status="SOLVER_ERROR"):
    print(f"[ERROR] {message}")
    _emit_cp_result(None, cp_status, 0.0)
    return {"exit_code": CLI_EXIT_SOLVER_ERROR, "cp_status": cp_status}


def _emit_cp_result(beta, status, solve_time, actual_bandwidth=None):
    if beta is None:
        print(f"CP_RESULT: status={status}")
    else:
        print(f"CP_RESULT: beta={beta} status={status}")
    print(f"METRIC: cp_solve_time_s={float(solve_time):.3f}")
    print(f"METRIC: cp_status={status}")
    print(f"METRIC: cp_best_obj={beta if beta is not None else -1}")
    if actual_bandwidth is not None:
        print(f"[INFO] Actual bandwidth from solution: {actual_bandwidth}")


def _existing_path(path_str):
    if not path_str:
        return None
    path = Path(path_str).expanduser()
    return path if path.exists() else None


def _detect_cpoptimizer_binary():
    env_binary = _existing_path(os.environ.get("CPOPTIMIZER_BINARY"))
    if env_binary:
        return env_binary

    candidates = [
        Path.home() / "Downloads/cplex/cpoptimizer/bin/x86-64_linux/cpoptimizer",
        Path.home() / "Downloads/CPLEX_Studio/cpoptimizer/bin/x86-64_linux/cpoptimizer",
        Path.home() / "Downloads/CPLEX_Studio2212/cpoptimizer/bin/x86-64_linux/cpoptimizer",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    from shutil import which
    cpoptimizer_on_path = which("cpoptimizer")
    return Path(cpoptimizer_on_path) if cpoptimizer_on_path else None


def _theoretical_upper_bound(rows, cols):
    return rows + cols - 2


def _normalize_status(result):
    solve_status = str(getattr(result, "solve_status", "UNKNOWN") or "UNKNOWN")
    infos = result.get_infos() if hasattr(result, "get_infos") else {}
    stop_cause = str(infos.get("SearchStopCause", ""))
    search_status = str(infos.get("SearchStatus", ""))

    if solve_status.lower() == "optimal":
        return "OPTIMAL"
    if solve_status.lower() == "infeasible":
        return "INFEASIBLE"
    if "timelimit" in stop_cause.lower() or "limit" in stop_cause.lower():
        return "TIMEOUT"
    if solve_status.lower() == "feasible" and search_status != "SearchCompleted":
        return "TIMEOUT"
    if solve_status.lower() == "feasible":
        return "FEASIBLE"
    return solve_status.upper()


def _get_info(result, name, default=""):
    if not hasattr(result, "get_infos"):
        return default
    return result.get_infos().get(name, default)


def _has_solution(result):
    try:
        return bool(result.is_solution())
    except Exception:
        try:
            result.get_objective_value()
            return True
        except Exception:
            return False


def _int_objective(result):
    try:
        return int(round(result.get_objective_value()))
    except Exception:
        return -1


def _edge_distances(edges, x_values, y_values):
    rows = []
    max_dist = 0
    for u, v in edges:
        dx = abs(x_values[u] - x_values[v])
        dy = abs(y_values[u] - y_values[v])
        dist = dx + dy
        max_dist = max(max_dist, dist)
        rows.append((u, v, dx, dy, dist))
    rows.sort(key=lambda r: (-r[4], r[0], r[1]))
    return max_dist, rows


def export_solution_to_file(output_path, n, edges, x_values, y_values, beta, rows, cols,
                             symmetry_breaking=True, anchor=None):
    row_limit = (rows + 1) // 2
    col_limit = (cols + 1) // 2
    ub = _theoretical_upper_bound(rows, cols)
    actual_bw, edge_rows = _edge_distances(edges, x_values, y_values)
    valid = actual_bw <= beta and beta <= ub

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write(f"VERIFICATION: K={beta}, Actual Bandwidth={actual_bw}\n")
        f.write("=" * 80 + "\n")
        f.write(f"Result: {'SAT' if valid else 'INVALID'}\n")
        f.write(f"Computed Bandwidth: {actual_bw}\n")
        f.write(f"Theoretical UB: {ub}\n")
        if symmetry_breaking and anchor is not None:
            f.write(f"Symmetry breaking: v_{anchor + 1} in reflection region "
                    f"(rows<={row_limit}, cols<={col_limit})\n")
        f.write(f"Number of vertices: {n}\n")
        f.write(f"Number of edges: {len(edges)}\n\n")

        f.write("--- Vertex Labeling (Position Assignment) ---\n")
        f.write(f"{'Vertex':<10} {'X':<10} {'Y':<10} {'(X,Y)':<15} Note\n")
        f.write("-" * 65 + "\n")
        for v in range(n):
            note = ""
            if symmetry_breaking and anchor == v:
                if x_values[v] <= row_limit and y_values[v] <= col_limit:
                    note = "anchor reflection region OK"
                else:
                    note = "anchor: VIOLATION!"
            f.write(
                f"{v + 1:<10} {x_values[v]:<10} {y_values[v]:<10} "
                f"{f'({x_values[v]},{y_values[v]})':<15} {note}\n"
            )

        f.write("\n--- Edge Distances (Manhattan Distance Calculation) ---\n")
        f.write(f"{'Edge':<12} {'Pos_u':<12} {'Pos_v':<12} {'|dx|':<6} {'|dy|':<6} {'d=|dx|+|dy|':<14} Status\n")
        f.write("-" * 70 + "\n")
        for u, v, dx, dy, dist in edge_rows:
            status = "EXCEEDED" if dist > beta else ("MAX" if dist == actual_bw else "OK")
            edge = f"({u + 1},{v + 1})"
            pos_u = f"({x_values[u]},{y_values[u]})"
            pos_v = f"({x_values[v]},{y_values[v]})"
            f.write(f"{edge:<12} {pos_u:<12} {pos_v:<12} {dx:<6} {dy:<6} {dist:<14} {status}\n")

        f.write("\n--- Summary ---\n")
        f.write(f"Maximum edge distance (bandwidth): {actual_bw}\n")
        f.write(f"Constraint K: {beta}\n")
        f.write(f"Theoretical UB: {ub}\n")
        if symmetry_breaking:
            f.write("Symmetry: reflection anchor pinning\n")
        f.write(f"Satisfies K constraint: {'Yes' if actual_bw <= beta else 'No'}\n")
        f.write(f"Within theoretical UB: {'Yes' if beta <= ub else 'No'}\n")
        f.write("=" * 80 + "\n")


def solve_cp(n, edges, timelimit=3600, symmetry_breaking=True, grid_mode="sqrt",
             output_path=None):
    try:
        from docplex.cp.model import CpoModel
    except ImportError:
        return _emit_setup_error("docplex")

    cpoptimizer_binary = _detect_cpoptimizer_binary()
    if not cpoptimizer_binary:
        return _emit_solver_error(
            "CP Optimizer executable not found. Set CPOPTIMIZER_BINARY or add cpoptimizer to PATH.",
            cp_status="SETUP_ERROR",
        )

    rows, cols = compute_grid_dimensions(n, grid_mode)
    ub = _theoretical_upper_bound(rows, cols)
    model = CpoModel()

    x = [model.integer_var(min=1, max=rows, name=f"x_{v}") for v in range(n)]
    y = [model.integer_var(min=1, max=cols, name=f"y_{v}") for v in range(n)]
    beta = model.integer_var(min=0, max=ub, name="beta")

    for u in range(n):
        for v in range(u + 1, n):
            model.add((x[u] != x[v]) | (y[u] != y[v]))

    for u, v in edges:
        model.add(beta >= model.abs(x[u] - x[v]) + model.abs(y[u] - y[v]))

    anchor = None
    if symmetry_breaking:
        anchor = select_anchor_vertex(n, edges)
        model.add(x[anchor] <= (rows + 1) // 2)
        model.add(y[anchor] <= (cols + 1) // 2)

    model.add(model.minimize(beta))

    t0 = time.time()
    try:
        result = model.solve(
            TimeLimit=timelimit,
            LogVerbosity="Quiet",
            execfile=str(cpoptimizer_binary),
        )
    except Exception as exc:
        return _emit_solver_error(str(exc))
    elapsed = time.time() - t0

    status = _normalize_status(result)
    beta_value = _int_objective(result) if _has_solution(result) else -1
    solve_time = _get_info(result, "SolveTime", elapsed)

    x_values = y_values = None
    if beta_value >= 0:
        x_values = [int(result.get_value(xv)) for xv in x]
        y_values = [int(result.get_value(yv)) for yv in y]
        actual_bw, _ = _edge_distances(edges, x_values, y_values)
        _emit_cp_result(beta_value, status, solve_time, actual_bw)
        if output_path:
            export_solution_to_file(
                output_path, n, edges, x_values, y_values, beta_value, rows, cols,
                symmetry_breaking=symmetry_breaking, anchor=anchor,
            )
    else:
        _emit_cp_result(None, status, solve_time)

    return {
        "exit_code": CLI_EXIT_OK,
        "cp_status": status,
        "beta": beta_value,
        "solve_time": solve_time,
        "x": x_values,
        "y": y_values,
    }


def main():
    parser = argparse.ArgumentParser(
        description="2DBMP - Two-Dimensional Bandwidth Minimization Problem (CP)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--graph", type=str, help="Path to graph file (edge list or .mtx)")
    parser.add_argument("--example", action="store_true",
                        help="Use the example graph from the paper (6 vertices)")
    parser.add_argument("--output", type=str, default=None,
                        help="Optional verification .txt output path")
    parser.add_argument("--grid-mode", type=str, choices=["sqrt", "nxn", "two-row"], default="sqrt",
                        help="Host: sqrt => ceil(sqrt(n)) square, nxn => n x n, two-row => 2 x ceil(n/2)")
    parser.add_argument("--timelimit", type=int, default=3600,
                        help="Time limit in seconds (default: 3600)")
    parser.add_argument("--no-symmetry", action="store_true",
                        help="Disable Q1 anchor symmetry breaking (for comparison only)")

    args = parser.parse_args()

    if args.example:
        n, edges = make_example_graph()
        graph_name = "example"
    elif args.graph:
        n, edges = read_graph_from_file(args.graph)
        graph_name = Path(args.graph).name
    else:
        parser.print_help()
        print("\n[ERROR] Provide --graph <file> or --example")
        sys.exit(1)

    sym = not args.no_symmetry
    print_run_header(graph_name, n, len(edges), args.grid_mode)

    result = solve_cp(
        n,
        edges,
        timelimit=args.timelimit,
        symmetry_breaking=sym,
        grid_mode=args.grid_mode,
        output_path=args.output,
    )
    if isinstance(result, dict):
        sys.exit(result.get("exit_code", CLI_EXIT_OK))


if __name__ == "__main__":
    main()
