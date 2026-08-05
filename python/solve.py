from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from python.solvers.entrypoints import METHODS, MethodEntrypoint, get_method, worker_path
from python.utils.host_grid import HostGrid, deterministic_placement
from python.utils.mtx_parser import ParsedMtxGraph, parse_mtx_graph
from python.utils.process_supervisor import ProcessResult, run_supervised
from python.utils.solution_verifier import verify_placement
from python.utils.verification_io import (
    SCHEMA,
    build_verification_document,
    sha256_file,
    write_json_atomic,
)


SUBMIT_ROOT = Path(__file__).resolve().parents[1]
TERMINAL_STATUSES = {
    "OPTIMAL_BY_UNSAT",
    "OPTIMAL_BY_BOUND",
    "TIMEOUT_FEASIBLE",
    "INTERRUPTED_FEASIBLE",
}


def _host(method: MethodEntrypoint, n_vertices: int) -> HostGrid:
    if method.grid_policy == "two-row":
        return HostGrid.two_row(n_vertices)
    if method.grid_policy == "n-by-n-square":
        return HostGrid(n_vertices, n_vertices, method.grid_policy)
    side = math.ceil(math.sqrt(n_vertices))
    return HostGrid(side, side, method.grid_policy)


def _positions(payload: Mapping[str, Any]) -> dict[int, tuple[int, int]]:
    raw = (payload.get("witness") or {}).get("positions") or {}
    if not isinstance(raw, dict):
        raise ValueError("checkpoint positions are not an object")
    try:
        return {
            int(vertex): (coordinate[0], coordinate[1])
            for vertex, coordinate in raw.items()
        }
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError("checkpoint positions are malformed") from exc


def validate_checkpoint(
    payload: Mapping[str, Any],
    *,
    graph: ParsedMtxGraph,
    input_sha256: str,
    method: MethodEntrypoint,
) -> tuple[dict[str, Any], int]:
    """Fail closed unless a worker checkpoint matches this exact invocation."""

    if payload.get("schema") != SCHEMA:
        raise ValueError("unsupported checkpoint schema")
    instance = payload.get("instance")
    worker_method = payload.get("method")
    bounds = payload.get("bounds")
    witness = payload.get("witness")
    if not all(isinstance(section, dict) for section in (instance, worker_method, bounds, witness)):
        raise ValueError("checkpoint is missing a required object")
    if instance.get("sha256") != input_sha256:
        raise ValueError("checkpoint input hash does not match")
    if worker_method.get("id") != method.method_id:
        raise ValueError("checkpoint method does not match")
    host = _host(method, graph.n_vertices)
    grid = worker_method.get("grid") or {}
    if (grid.get("rows"), grid.get("cols"), grid.get("policy")) != (
        host.rows,
        host.cols,
        host.policy,
    ):
        raise ValueError("checkpoint host does not match")
    constraint_k = witness.get("constraint_k")
    if not isinstance(constraint_k, int) or isinstance(constraint_k, bool):
        raise ValueError("checkpoint constraint K is not an integer")
    positions = _positions(payload)
    verification = verify_placement(
        graph.n_vertices, graph.edges, host, positions, tested_k=constraint_k
    )
    if not verification.valid or verification.bandwidth is None:
        raise ValueError(f"checkpoint witness is invalid: {verification.error}")
    if bounds.get("final_verified") != verification.bandwidth:
        raise ValueError("checkpoint bandwidth is not independently verified")
    if witness.get("actual_bandwidth") != verification.bandwidth:
        raise ValueError("checkpoint witness bandwidth is inconsistent")
    return copy.deepcopy(dict(payload)), verification.bandwidth


def _error_document(
    *,
    instance: Path,
    method: str,
    solver: str,
    error: str,
    result: ProcessResult | None = None,
) -> dict[str, object]:
    return {
        "schema": "2dbmp-run-error-v1",
        "instance": str(instance),
        "method": method,
        "solver": solver,
        "error": error,
        "execution": None
        if result is None
        else {
            "elapsed_s": result.elapsed_s,
            "returncode": result.returncode,
            "termination_signal": result.termination_signal,
            "timed_out": result.timed_out,
        },
    }


def _seed_checkpoint(
    checkpoint: Path,
    *,
    instance: Path,
    graph: ParsedMtxGraph,
    method: MethodEntrypoint,
    solver: str,
) -> None:
    """Provide a valid incumbent if a timeout happens before worker setup."""

    host = _host(method, graph.n_vertices)
    positions = deterministic_placement(list(range(1, graph.n_vertices + 1)), host)
    verification = verify_placement(graph.n_vertices, graph.edges, host, positions)
    if not verification.valid or verification.bandwidth is None:
        raise RuntimeError(f"cannot create seed witness: {verification.error}")
    write_json_atomic(
        checkpoint,
        build_verification_document(
            instance_file=str(instance),
            instance_sha256=sha256_file(instance),
            graph=graph,
            method_id=method.method_id,
            solver=solver,
            solver_script=method.script,
            host=host,
            status="RUNNING_FEASIBLE",
            termination="supervisor_seed",
            termination_k=None,
            elapsed_s=None,
            timeout_s=None,
            certified_lower=None,
            final_verified=verification.bandwidth,
            optimal=None,
            constraint_k=verification.bandwidth,
            positions=positions,
        ),
    )


def _finalize_document(
    checkpoint: Mapping[str, Any],
    *,
    bandwidth: int,
    result: ProcessResult,
    timeout_s: float | None,
) -> dict[str, Any]:
    document = copy.deepcopy(dict(checkpoint))
    outcome = document["outcome"]
    bounds = document["bounds"]
    if result.timed_out:
        status, termination, termination_k = "TIMEOUT_FEASIBLE", "wall_timeout", None
        bounds["optimal"] = None
    elif result.returncode == 0 and outcome.get("status") in {
        "OPTIMAL_BY_UNSAT",
        "OPTIMAL_BY_BOUND",
    }:
        status = outcome["status"]
        termination = outcome.get("termination")
        termination_k = outcome.get("termination_k")
        bounds["certified_lower"] = bandwidth
        bounds["optimal"] = bandwidth
    elif result.returncode != 0:
        status, termination, termination_k = "INTERRUPTED_FEASIBLE", "worker_exit", None
        bounds["optimal"] = None
    else:
        # A clean exit without a terminal worker record is a protocol failure,
        # not evidence of an optimal solution.
        status, termination, termination_k = "INTERRUPTED_FEASIBLE", "missing_terminal_record", None
        bounds["optimal"] = None
    outcome.update(
        {
            "status": status,
            "termination": termination,
            "termination_k": termination_k,
            "elapsed_s": result.elapsed_s,
            "timeout_s": timeout_s,
        }
    )
    bounds["final_verified"] = bandwidth
    document.setdefault("provenance", {})["supervisor"] = {
        "returncode": result.returncode,
        "termination_signal": result.termination_signal,
        "timed_out": result.timed_out,
    }
    return document


def run(
    *,
    instance: Path,
    method_key: str,
    solver: str,
    timeout_s: float | None,
    output: Path,
    sa_k: int | None = None,
    quiet: bool = False,
) -> dict[str, object]:
    """Run one registered worker and atomically publish one terminal result."""

    instance = instance.resolve()
    output = output.resolve()
    method = get_method(method_key)
    if not instance.is_file():
        result = _error_document(
            instance=instance, method=method_key, solver=solver, error="input file does not exist"
        )
        write_json_atomic(output, result)
        return result
    try:
        graph = parse_mtx_graph(instance)
        input_sha256 = sha256_file(instance)
        worker = worker_path(SUBMIT_ROOT, method)
        checkpoint = output.with_name(f".{output.name}.{uuid.uuid4().hex}.checkpoint.json")
        _seed_checkpoint(
            checkpoint,
            instance=instance,
            graph=graph,
            method=method,
            solver=solver,
        )
    except Exception as exc:
        result = _error_document(instance=instance, method=method_key, solver=solver, error=str(exc))
        write_json_atomic(output, result)
        return result

    command = [sys.executable, str(worker), str(instance), solver, str(checkpoint)]
    if method.key == "two-row":
        if sa_k is not None:
            command.extend(["--sa-k", str(sa_k)])
        if quiet:
            command.append("--quiet")
    stdout_path = output.with_suffix(output.suffix + ".stdout.log")
    stderr_path = output.with_suffix(output.suffix + ".stderr.log")
    try:
        process_result = run_supervised(
            command,
            cwd=SUBMIT_ROOT,
            timeout_s=timeout_s,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
        checkpoint_payload, bandwidth = validate_checkpoint(
            payload,
            graph=graph,
            input_sha256=input_sha256,
            method=method,
        )
        result = _finalize_document(
            checkpoint_payload,
            bandwidth=bandwidth,
            result=process_result,
            timeout_s=timeout_s,
        )
    except Exception as exc:
        result = _error_document(
            instance=instance,
            method=method_key,
            solver=solver,
            error=str(exc),
            result=locals().get("process_result"),
        )
    finally:
        checkpoint.unlink(missing_ok=True)
    write_json_atomic(output, result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a 2DBMP solver under the package supervisor")
    parser.add_argument("instance", type=Path)
    parser.add_argument("--method", choices=sorted(METHODS), default="square")
    parser.add_argument("--solver", default="cadical195")
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sa-k", type=int, default=None, help="two-row reference bound")
    parser.add_argument("--quiet", action="store_true", help="silence two-row worker progress")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout < 0:
        raise SystemExit("--timeout must be non-negative")
    payload = run(
        instance=args.instance,
        method_key=args.method,
        solver=args.solver,
        timeout_s=args.timeout or None,
        output=args.output,
        sa_k=args.sa_k,
        quiet=args.quiet,
    )
    print(json.dumps({"output": str(args.output), "schema": payload["schema"]}, sort_keys=True))
    return 0 if payload["schema"] == SCHEMA else 2


if __name__ == "__main__":
    raise SystemExit(main())
