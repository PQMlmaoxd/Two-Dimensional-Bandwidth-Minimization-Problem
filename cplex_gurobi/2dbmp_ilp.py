"""ILP formulations and commercial solver backends for 2DBMP."""

import argparse
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from python.utils.mtx_parser import parse_mtx_file


CLI_EXIT_OK = 0
CLI_EXIT_SETUP_ERROR = 2
CLI_EXIT_SOLVER_ERROR = 3


def compute_grid_side(n, grid_mode="sqrt"):
    if grid_mode == "sqrt":
        return math.ceil(math.sqrt(n))
    if grid_mode == "nxn":
        return n
    raise ValueError(f"Unsupported grid mode '{grid_mode}'. Use 'sqrt' or 'nxn'.")


def compute_grid_dimensions(n, grid_mode="sqrt"):
    if grid_mode == "two-row":
        return 2, (n + 1) // 2
    side = compute_grid_side(n, grid_mode)
    return side, side


def forbidden_anchor_cells_rect(anchor, rows, cols, symmetry_breaking):
    if not symmetry_breaking:
        return set()
    row_limit = (rows + 1) // 2
    col_limit = (cols + 1) // 2
    return {
        (anchor, row, col)
        for row in range(1, rows + 1)
        for col in range(1, cols + 1)
        if row > row_limit or col > col_limit
    }


def print_run_header(graph_name, n, edge_count, grid_mode):
    rows, cols = compute_grid_dimensions(n, grid_mode)
    print(f"[INFO] Loaded graph from {graph_name}: n={n}, m={edge_count}")
    if grid_mode == "sqrt":
        print(f"[INFO] grid mode = sqrt, eta = {rows}, grid = {rows}x{cols} = {rows * cols} cells")
    else:
        print(f"[INFO] grid mode = {grid_mode}, grid = {rows}x{cols} = {rows * cols} cells")


def emit_ilp_result(beta, status, solve_time, actual_bandwidth=None):
    if beta is None:
        print(f"ILP_RESULT: status={status}")
    else:
        print(f"ILP_RESULT: beta={beta} status={status}")
    print(f"METRIC: ilp_solve_time_s={solve_time:.3f}")
    print(f"METRIC: ilp_status={status}")
    print(f"METRIC: ilp_best_obj={beta if beta is not None else -1}")
    if actual_bandwidth is not None:
        print(f"[INFO] Actual bandwidth from solution: {actual_bandwidth}")


def solution_bandwidth(edges, positions):
    return max(
        (abs(positions[u][0] - positions[v][0]) + abs(positions[u][1] - positions[v][1]) for u, v in edges),
        default=0,
    )


def _emit_setup_error(package_name):
    print(f"[ERROR] {package_name} not installed. Use: pip install {package_name}")
    emit_ilp_result(None, "SETUP_ERROR", 0.0)
    return {"exit_code": CLI_EXIT_SETUP_ERROR, "ilp_status": "SETUP_ERROR"}


def _emit_solver_error(message, ilp_status="SOLVER_ERROR"):
    print(f"[ERROR] {message}")
    emit_ilp_result(None, ilp_status, 0.0)
    return {"exit_code": CLI_EXIT_SOLVER_ERROR, "ilp_status": ilp_status}


def _existing_path(path_str):
    if not path_str:
        return None
    path = Path(path_str).expanduser()
    return path if path.exists() else None


def _detect_gurobi_license_file():
    env_license = _existing_path(os.environ.get("GRB_LICENSE_FILE"))
    if env_license:
        return env_license

    candidates = [
        REPO_ROOT / "gurobi.lic",
        REPO_ROOT / "cplex_gurobi" / "gurobi.lic",
        Path.cwd() / "gurobi.lic",
        Path.home() / "gurobi.lic",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _configure_gurobi_license():
    license_file = _detect_gurobi_license_file()
    if license_file and os.environ.get("GRB_LICENSE_FILE") != str(license_file):
        os.environ["GRB_LICENSE_FILE"] = str(license_file)
    return license_file


def _detect_cplex_binary():
    env_binary = _existing_path(os.environ.get("CPLEX_BINARY"))
    if env_binary:
        return env_binary

    candidates = [
        Path.home() / "Downloads/cplex/cplex/bin/x86-64_linux/cplex",
        Path.home() / "Downloads/CPLEX_Studio/cplex/bin/x86-64_linux/cplex",
        Path.home() / "Downloads/CPLEX_Studio2212/cplex/bin/x86-64_linux/cplex",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    cplex_on_path = shutil.which("cplex")
    return Path(cplex_on_path) if cplex_on_path else None


def _parse_cplex_cli_output(output_text):
    result = {}
    msg_lower = output_text.lower()

    if (
        "community edition" in msg_lower
        or "problem size limits exceeded" in msg_lower
        or "promotional version" in msg_lower
    ):
        for line in reversed(output_text.splitlines()):
            line = line.strip()
            if line:
                result["error_message"] = line
                break
        result["ilp_status"] = "LICENSE_ERROR"
        return result

    if "integer optimal solution" in msg_lower:
        result["ilp_status"] = "OPTIMAL"
    elif "time limit exceeded, integer feasible" in msg_lower:
        result["ilp_status"] = "TIMEOUT"
    elif "time limit exceeded, no integer solution" in msg_lower:
        result["ilp_status"] = "TIMEOUT"
    elif "infeasible" in msg_lower:
        result["ilp_status"] = "INFEASIBLE"

    obj_matches = re.findall(r"Objective\s*=\s*([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)", output_text)
    if obj_matches:
        beta_val = int(round(float(obj_matches[-1])))
        result["beta"] = beta_val
        result["ilp_best_obj"] = beta_val

    solve_matches = re.findall(
        r"Solution time =\s*([0-9]+(?:\.[0-9]+)?)\s+sec\.",
        output_text,
        re.IGNORECASE,
    )
    if solve_matches:
        result["ilp_solve_time_s"] = float(solve_matches[-1])

    positions = {}
    for vertex, row, col, value in re.findall(
        r"\ba_(\d+)_(\d+)_(\d+)\s*=\s*([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)",
        output_text,
        re.IGNORECASE,
    ):
        if float(value) > 0.5:
            positions[int(vertex)] = (int(row), int(col))
    if positions:
        result["positions"] = positions

    return result


def solve_cplex_cli(n, edges, time_limit=3600, symmetry_breaking=True, grid_mode="sqrt"):
    cplex_binary = _detect_cplex_binary()
    if not cplex_binary:
        return _emit_setup_error("cplex")

    with tempfile.TemporaryDirectory(prefix="2dbmp_cplex_") as tmpdir:
        lp_path = Path(tmpdir) / "model.lp"
        generate_lp_file(
            n,
            edges,
            str(lp_path),
            symmetry_breaking=symmetry_breaking,
            grid_mode=grid_mode,
        )

        cmd = [
            str(cplex_binary),
            "-c",
            f'read "{lp_path}"',
            f"set timelimit {time_limit}",
            "opt",
            "display solution objective",
            "display solution variables",
            "quit",
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        parsed = _parse_cplex_cli_output(proc.stdout or "")
        if parsed.get("error_message"):
            return _emit_solver_error(
                parsed["error_message"],
                ilp_status=parsed.get("ilp_status", "SOLVER_ERROR"),
            )

        beta_val = parsed.get("beta")
        ilp_status = parsed.get("ilp_status", "NO_SOLUTION")
        solve_time = float(parsed.get("ilp_solve_time_s", 0.0))

        if beta_val is not None and ilp_status in ("OPTIMAL", "TIMEOUT"):
            positions = parsed.get("positions", {})
            actual_bandwidth = solution_bandwidth(edges, positions) if len(positions) == n else None
            emit_ilp_result(beta_val, ilp_status, solve_time, actual_bandwidth)
            return beta_val

        emit_ilp_result(None, ilp_status, solve_time)
        return None


def read_graph_from_file(filepath):
    if filepath.lower().endswith(".mtx"):
        n_vertices, one_based_edges = parse_mtx_file(filepath)
        return n_vertices, [(u - 1, v - 1) for u, v in one_based_edges]

    edges = []
    n_header = None
    m_header = None
    is_mtx = filepath.lower().endswith('.mtx')
    header_read = False

    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('%'):
                continue
            parts = line.split()
            if not header_read:
                if len(parts) >= 2:
                    try:
                        n_header = int(parts[0])
                        if is_mtx and len(parts) >= 3:
                            m_header = int(parts[2])
                        else:
                            m_header = int(parts[1])
                        header_read = True
                        continue
                    except ValueError:
                        pass
                header_read = True
            if len(parts) >= 2:
                try:
                    u, v = int(parts[0]), int(parts[1])
                    if u != v:
                        edges.append((u, v))
                except ValueError:
                    continue

    if not edges:
        raise ValueError("No edges found in file.")

    all_nodes = set()
    for u, v in edges:
        all_nodes.add(u)
        all_nodes.add(v)

    min_node = min(all_nodes)
    if min_node == 1:
        edges = [(u - 1, v - 1) for u, v in edges]
        all_nodes = {x - 1 for x in all_nodes}
        if n_header is not None:
            pass

    n_from_edges = max(all_nodes) + 1
    if n_header is not None:
        n = max(n_header, n_from_edges)
    else:
        n = n_from_edges

    edge_set = set()
    for u, v in edges:
        if u > v:
            u, v = v, u
        edge_set.add((u, v))
    edges = sorted(edge_set)
    return n, edges


def make_example_graph():
    n = 6
    edges = [
        (0, 1), (0, 2), (1, 2), (1, 3), (1, 4),
        (2, 3), (2, 5), (3, 4), (3, 5), (4, 5)
    ]
    return n, edges


def select_anchor_vertex(n, edges):
    adj = defaultdict(list)
    for u, v in edges:
        adj[u].append(v)
        adj[v].append(u)

    degrees = [len(adj[v]) for v in range(n)]

    best = 0
    best_deg = degrees[0]
    best_nbr_sum = sum(degrees[u] for u in adj[0])

    for v in range(1, n):
        d = degrees[v]
        nbr_sum = sum(degrees[u] for u in adj[v])
        if d > best_deg or (d == best_deg and nbr_sum > best_nbr_sum):
            best = v
            best_deg = d
            best_nbr_sum = nbr_sum
    return best


def symmetry_boundary(eta):
    return (eta + 1) // 2


def forbidden_anchor_cells(anchor, eta, m_sym):
    forbidden = set()
    for r in range(1, eta + 1):
        for c in range(1, eta + 1):
            if r > m_sym or c > m_sym:
                forbidden.add((anchor, r, c))
    return forbidden


def _lp_expr(terms):
    parts = []
    for coef, var in terms:
        if coef == 0:
            continue
        if not parts:
            if coef == 1:
                parts.append(var)
            elif coef == -1:
                parts.append(f"- {var}")
            elif coef > 0:
                parts.append(f"{coef} {var}")
            else:
                parts.append(f"- {-coef} {var}")
        else:
            if coef == 1:
                parts.append(f"+ {var}")
            elif coef == -1:
                parts.append(f"- {var}")
            elif coef > 0:
                parts.append(f"+ {coef} {var}")
            else:
                parts.append(f"- {-coef} {var}")
    return " ".join(parts) if parts else "0"


def generate_lp_file(n, edges, filename="2dbmp.lp", symmetry_breaking=True, grid_mode="sqrt"):
    rows, cols = compute_grid_dimensions(n, grid_mode)
    m = len(edges)

    forbidden = set()
    anchor = None
    if symmetry_breaking and edges:
        anchor = select_anchor_vertex(n, edges)
        forbidden = forbidden_anchor_cells_rect(anchor, rows, cols, True)

    with open(filename, 'w') as f:
        f.write("\\ 2DBMP ILP Model\n")
        f.write(f"\\ n={n} vertices, m={m} edges\n")
        f.write(f"\\ grid mode = {grid_mode}\n")
        f.write(f"\\ grid size = {rows}x{cols} = {rows * cols} cells\n")
        if symmetry_breaking and anchor is not None:
            f.write(f"\\ Symmetry breaking: rectangular reflection anchor pinning\n")
        f.write(f"\\ Auxiliary vars dxp/dxm/dyp/dym are CONTINUOUS (stronger LP relaxation)\n\n")

        f.write("Minimize\n")
        f.write("  obj: beta\n\n")

        f.write("Subject To\n\n")

        for v in range(n):
            terms = [(1, f"a_{v}_{r}_{c}")
                      for r in range(1, rows + 1)
                      for c in range(1, cols + 1)
                     if (v, r, c) not in forbidden]
            f.write(f"  assign_v{v}: {_lp_expr(terms)} = 1\n")
        f.write("\n")

        for r in range(1, rows + 1):
            for c in range(1, cols + 1):
                terms = [(1, f"a_{v}_{r}_{c}")
                         for v in range(n)
                         if (v, r, c) not in forbidden]
                if not terms:
                    continue
                f.write(f"  cell_{r}_{c}: {_lp_expr(terms)} <= 1\n")
        f.write("\n")

        for idx, (u, v) in enumerate(edges):
            terms = []
            for r in range(1, rows + 1):
                for c in range(1, cols + 1):
                    if (u, r, c) not in forbidden:
                        terms.append((r, f"a_{u}_{r}_{c}"))
            for r in range(1, rows + 1):
                for c in range(1, cols + 1):
                    if (v, r, c) not in forbidden:
                        terms.append((-r, f"a_{v}_{r}_{c}"))
            terms.append((-1, f"dxp_{idx}"))
            terms.append((1, f"dxm_{idx}"))
            f.write(f"  dx_e{idx}: {_lp_expr(terms)} = 0\n")

            terms = []
            for r in range(1, rows + 1):
                for c in range(1, cols + 1):
                    if (u, r, c) not in forbidden:
                        terms.append((c, f"a_{u}_{r}_{c}"))
            for r in range(1, rows + 1):
                for c in range(1, cols + 1):
                    if (v, r, c) not in forbidden:
                        terms.append((-c, f"a_{v}_{r}_{c}"))
            terms.append((-1, f"dyp_{idx}"))
            terms.append((1, f"dym_{idx}"))
            f.write(f"  dy_e{idx}: {_lp_expr(terms)} = 0\n")

            f.write(f"  bnd_e{idx}: dxp_{idx} + dxm_{idx} + dyp_{idx} + dym_{idx} - beta <= 0\n")

        f.write("\n")

        f.write("Bounds\n")
        f.write(f"  0 <= beta <= {rows + cols - 2}\n")
        for idx in range(m):
            f.write(f"  0 <= dxp_{idx} <= {rows - 1}\n")
            f.write(f"  0 <= dxm_{idx} <= {rows - 1}\n")
            f.write(f"  0 <= dyp_{idx} <= {cols - 1}\n")
            f.write(f"  0 <= dym_{idx} <= {cols - 1}\n")
        f.write("\n")

        f.write("General\n")
        f.write("  beta\n\n")

        f.write("Binary\n")
        for v in range(n):
            row = " ".join(f"a_{v}_{r}_{c}"
                           for r in range(1, rows + 1)
                           for c in range(1, cols + 1)
                           if (v, r, c) not in forbidden)
            f.write(f"  {row}\n")
        f.write("\n")

        f.write("End\n")



def solve_gurobi(n, edges, time_limit=3600, symmetry_breaking=True, grid_mode="sqrt"):
    _configure_gurobi_license()
    try:
        import gurobipy as gp
        from gurobipy import GRB
    except ImportError:
        return _emit_setup_error("gurobipy")

    rows, cols = compute_grid_dimensions(n, grid_mode)
    anchor = None
    forbidden = set()
    if symmetry_breaking and edges:
        anchor = select_anchor_vertex(n, edges)
        forbidden = forbidden_anchor_cells_rect(anchor, rows, cols, True)

    try:
        model = gp.Model("2DBMP")
    except gp.GurobiError as exc:
        msg = str(exc)
        status = "LICENSE_ERROR" if "license" in msg.lower() else "SOLVER_ERROR"
        return _emit_solver_error(msg, ilp_status=status)
    model.Params.TimeLimit = time_limit
    model.Params.OutputFlag = 0

    a = {}
    for v in range(n):
        for r in range(1, rows + 1):
            for c in range(1, cols + 1):
                a[v, r, c] = model.addVar(vtype=GRB.BINARY, name=f"a_{v}_{r}_{c}")

    for (fv, fr, fc) in forbidden:
        a[fv, fr, fc].ub = 0

    beta = model.addVar(vtype=GRB.INTEGER, lb=0, ub=rows + cols - 2, name="beta")

    m = len(edges)
    dxp = {}; dxm = {}; dyp = {}; dym = {}
    for idx in range(m):
        dxp[idx] = model.addVar(lb=0, ub=rows - 1, vtype=GRB.CONTINUOUS, name=f"dxp_{idx}")
        dxm[idx] = model.addVar(lb=0, ub=rows - 1, vtype=GRB.CONTINUOUS, name=f"dxm_{idx}")
        dyp[idx] = model.addVar(lb=0, ub=cols - 1, vtype=GRB.CONTINUOUS, name=f"dyp_{idx}")
        dym[idx] = model.addVar(lb=0, ub=cols - 1, vtype=GRB.CONTINUOUS, name=f"dym_{idx}")

    model.update()

    model.setObjective(beta, GRB.MINIMIZE)

    for v in range(n):
        model.addConstr(
            gp.quicksum(a[v, r, c] for r in range(1, rows + 1) for c in range(1, cols + 1)) == 1,
            name=f"assign_{v}"
        )

    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            model.addConstr(
                gp.quicksum(a[v, r, c] for v in range(n)) <= 1,
                name=f"cell_{r}_{c}"
            )

    for idx, (u, v) in enumerate(edges):
        xu = gp.quicksum(r * a[u, r, c] for r in range(1, rows + 1) for c in range(1, cols + 1))
        xv = gp.quicksum(r * a[v, r, c] for r in range(1, rows + 1) for c in range(1, cols + 1))
        yu = gp.quicksum(c * a[u, r, c] for r in range(1, rows + 1) for c in range(1, cols + 1))
        yv = gp.quicksum(c * a[v, r, c] for r in range(1, rows + 1) for c in range(1, cols + 1))

        model.addConstr(xu - xv == dxp[idx] - dxm[idx], name=f"dx_{idx}")
        model.addConstr(yu - yv == dyp[idx] - dym[idx], name=f"dy_{idx}")
        model.addConstr(dxp[idx] + dxm[idx] + dyp[idx] + dym[idx] <= beta, name=f"bnd_{idx}")

    t_solve_start = time.time()
    try:
        model.optimize()
    except gp.GurobiError as exc:
        msg = str(exc)
        status = "LICENSE_ERROR" if "size-limited license" in msg.lower() else "SOLVER_ERROR"
        return _emit_solver_error(msg, ilp_status=status)
    ilp_solve_time = time.time() - t_solve_start

    if model.SolCount > 0:
        beta_val = int(round(beta.X))
        ilp_status = "OPTIMAL" if model.Status == GRB.OPTIMAL else "TIMEOUT"
        positions = [
            next((r, c) for r in range(1, rows + 1) for c in range(1, cols + 1) if a[v, r, c].X > 0.5)
            for v in range(n)
        ]
        emit_ilp_result(beta_val, ilp_status, ilp_solve_time, solution_bandwidth(edges, positions))
        return beta_val
    else:
        ilp_status = "INFEASIBLE" if model.Status == GRB.INFEASIBLE else "NO_SOLUTION"
        emit_ilp_result(None, ilp_status, ilp_solve_time)
        return None


def solve_cplex(n, edges, time_limit=3600, symmetry_breaking=True, grid_mode="sqrt"):
    cplex_binary = _detect_cplex_binary()
    if cplex_binary is not None:
        return solve_cplex_cli(
            n,
            edges,
            time_limit,
            symmetry_breaking=symmetry_breaking,
            grid_mode=grid_mode,
        )

    try:
        from docplex.mp.model import Model
    except ImportError:
        return _emit_setup_error("docplex")

    rows, cols = compute_grid_dimensions(n, grid_mode)
    anchor = None
    forbidden = set()
    if symmetry_breaking and edges:
        anchor = select_anchor_vertex(n, edges)
        forbidden = forbidden_anchor_cells_rect(anchor, rows, cols, True)

    mdl = Model(name="2DBMP")
    mdl.set_time_limit(time_limit)

    a = {}
    for v in range(n):
        for r in range(1, rows + 1):
            for c in range(1, cols + 1):
                if (v, r, c) in forbidden:
                    a[v, r, c] = mdl.continuous_var(lb=0, ub=0, name=f"a_{v}_{r}_{c}")
                else:
                    a[v, r, c] = mdl.binary_var(name=f"a_{v}_{r}_{c}")

    beta = mdl.integer_var(lb=0, ub=rows + cols - 2, name="beta")

    m = len(edges)
    dxp = {}; dxm = {}; dyp = {}; dym = {}
    for idx in range(m):
        dxp[idx] = mdl.continuous_var(lb=0, ub=rows - 1, name=f"dxp_{idx}")
        dxm[idx] = mdl.continuous_var(lb=0, ub=rows - 1, name=f"dxm_{idx}")
        dyp[idx] = mdl.continuous_var(lb=0, ub=cols - 1, name=f"dyp_{idx}")
        dym[idx] = mdl.continuous_var(lb=0, ub=cols - 1, name=f"dym_{idx}")

    mdl.minimize(beta)

    for v in range(n):
        mdl.add_constraint(
            mdl.sum(a[v, r, c] for r in range(1, rows + 1) for c in range(1, cols + 1)) == 1,
            ctname=f"assign_{v}"
        )

    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            mdl.add_constraint(
                mdl.sum(a[v, r, c] for v in range(n)) <= 1,
                ctname=f"cell_{r}_{c}"
            )

    for idx, (u, v) in enumerate(edges):
        xu = mdl.sum(r * a[u, r, c] for r in range(1, rows + 1) for c in range(1, cols + 1))
        xv = mdl.sum(r * a[v, r, c] for r in range(1, rows + 1) for c in range(1, cols + 1))
        yu = mdl.sum(c * a[u, r, c] for r in range(1, rows + 1) for c in range(1, cols + 1))
        yv = mdl.sum(c * a[v, r, c] for r in range(1, rows + 1) for c in range(1, cols + 1))

        mdl.add_constraint(xu - xv == dxp[idx] - dxm[idx], ctname=f"dx_{idx}")
        mdl.add_constraint(yu - yv == dyp[idx] - dym[idx], ctname=f"dy_{idx}")
        mdl.add_constraint(dxp[idx] + dxm[idx] + dyp[idx] + dym[idx] <= beta, ctname=f"bnd_{idx}")

    t_solve_start = time.time()
    try:
        sol = mdl.solve(log_output=False)
    except Exception as exc:
        msg = str(exc)
        msg_lower = msg.lower()
        status = (
            "LICENSE_ERROR"
            if "community edition" in msg_lower
            or "problem size limits exceeded" in msg_lower
            or "promotional version" in msg_lower
            else "SOLVER_ERROR"
        )
        return _emit_solver_error(msg, ilp_status=status)
    ilp_solve_time = time.time() - t_solve_start

    if sol:
        beta_val = int(round(sol.get_value(beta)))
        details = mdl.solve_details
        solve_status = details.status if details else "unknown"
        ilp_status = "OPTIMAL" if "optimal" in solve_status.lower() else "TIMEOUT"
        positions = [
            next((r, c) for r in range(1, rows + 1) for c in range(1, cols + 1) if sol.get_value(a[v, r, c]) > 0.5)
            for v in range(n)
        ]
        emit_ilp_result(beta_val, ilp_status, ilp_solve_time, solution_bandwidth(edges, positions))
        return beta_val
    else:
        emit_ilp_result(None, "NO_SOLUTION", ilp_solve_time)
        return None


def main():
    parser = argparse.ArgumentParser(
        description="2DBMP - Two-Dimensional Bandwidth Minimization Problem (ILP)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--graph", type=str, help="Path to graph file (edge list or .mtx)")
    parser.add_argument("--example", action="store_true",
                        help="Use the example graph from the paper (6 vertices)")
    parser.add_argument("--output", type=str, default=None, help="Output .lp file path")
    parser.add_argument("--solver", type=str, choices=["gurobi", "cplex", "lp"], default="lp",
                        help="Solver: 'gurobi', 'cplex', or 'lp' (write .lp file, default)")
    parser.add_argument("--grid-mode", type=str, choices=["sqrt", "nxn", "two-row"], default="sqrt",
                        help="Host: sqrt => ceil(sqrt(n)) square, nxn => n x n, two-row => 2 x ceil(n/2)")
    parser.add_argument("--timelimit", type=int, default=3600,
                        help="Time limit in seconds (default: 3600)")
    parser.add_argument("--no-symmetry", action="store_true",
                        help="Disable D4 symmetry breaking (for comparison only)")

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

    result = None
    if args.solver == "gurobi":
        result = solve_gurobi(
            n,
            edges,
            args.timelimit,
            symmetry_breaking=sym,
            grid_mode=args.grid_mode,
        )
    elif args.solver == "cplex":
        result = solve_cplex(
            n,
            edges,
            args.timelimit,
            symmetry_breaking=sym,
            grid_mode=args.grid_mode,
        )
    else:
        outfile = args.output if args.output else "2dbmp.lp"
        generate_lp_file(
            n,
            edges,
            outfile,
            symmetry_breaking=sym,
            grid_mode=args.grid_mode,
        )

    if isinstance(result, dict):
        sys.exit(result.get("exit_code", CLI_EXIT_OK))


if __name__ == "__main__":
    main()
