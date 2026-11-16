from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


SUBMIT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SUBMIT_ROOT.parent if SUBMIT_ROOT.name == "submit_src_code" else SUBMIT_ROOT
if str(SUBMIT_ROOT) not in sys.path:
    sys.path.insert(0, str(SUBMIT_ROOT))

from python.utils.host_grid import HostGrid
from python.utils.mtx_parser import ParsedMtxGraph, parse_mtx_graph
from python.utils.solution_verifier import normalize_edges, verify_placement
from python.utils.verification_io import write_json_atomic


SCHEMA = "2dbmp-verification-v1"
NO_WITNESS_SCHEMA = "2dbmp-no-witness-v1"
MANIFEST_SCHEMA = "2dbmp-verification-manifest-v1"
DEFAULT_SOURCE = SUBMIT_ROOT / "results" / "logs"
TERMINAL_STATUSES = {
    "OPTIMAL_BY_UNSAT",
    "OPTIMAL_BY_BOUND",
    "TIMEOUT_FEASIBLE",
    "INTERRUPTED_FEASIBLE",
}
NO_WITNESS_STATUS = "TIMEOUT_NO_WITNESS"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def _coordinate_graph(path: Path) -> ParsedMtxGraph:
    """Reproduce the graph semantics declared by historical artifacts."""

    metadata = parse_mtx_graph(path)
    edges: list[tuple[int, int]] = []
    header_seen = False
    with path.open("r", encoding="utf-8") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if not line or line.startswith("%"):
                continue
            parts = line.split()
            if not header_seen:
                header_seen = True
                continue
            edges.append((int(parts[0]), int(parts[1])))
    return ParsedMtxGraph(
        n_vertices=max(metadata.rows, metadata.cols),
        edges=normalize_edges(max(metadata.rows, metadata.cols), edges),
        rows=metadata.rows,
        cols=metadata.cols,
        interpretation="matrix-coordinate-as-edge-v1",
        declared_entries=metadata.declared_entries,
        field=metadata.field,
        symmetry=metadata.symmetry,
    )


def _graph_for_document(path: Path, interpretation: object) -> ParsedMtxGraph:
    if interpretation == "matrix-coordinate-as-edge-v1":
        return _coordinate_graph(path)
    return parse_mtx_graph(path)


def _require_int(value: Any, label: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _verification_path(payload: Mapping[str, Any], result_path: Path) -> Path:
    instance = payload.get("instance")
    if not isinstance(instance, dict) or not isinstance(instance.get("file"), str):
        raise ValueError(f"{result_path}: missing instance file")
    relative_path = Path(instance["file"])
    candidate = REPO_ROOT / relative_path
    if not candidate.is_file() and relative_path.parts[:1] == ("submit_src_code",):
        candidate = REPO_ROOT.joinpath(*relative_path.parts[1:])
    if not candidate.is_file():
        raise ValueError(f"{result_path}: input file does not exist")
    return candidate


def _verify_document(path: Path, payload: Mapping[str, Any]) -> str:
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"{path}: unsupported schema {payload.get('schema')!r}")
    instance = payload.get("instance")
    method = payload.get("method")
    outcome = payload.get("outcome")
    bounds = payload.get("bounds")
    witness = payload.get("witness")
    if not all(isinstance(value, dict) for value in (instance, method, outcome, bounds, witness)):
        raise ValueError(f"{path}: missing required object")
    mtx_path = _verification_path(payload, path)
    if instance.get("sha256") != _sha256(mtx_path):
        raise ValueError(f"{path}: input file/hash mismatch")
    graph = _graph_for_document(mtx_path, instance.get("graph_interpretation"))
    matrix = instance.get("matrix") or {}
    expected_graph = (
        graph.n_vertices,
        len(graph.edges),
        graph.interpretation,
        graph.rows,
        graph.cols,
        graph.declared_entries,
        graph.field,
        graph.symmetry,
    )
    declared_graph = (
        instance.get("vertices"),
        instance.get("graph_edges"),
        instance.get("graph_interpretation"),
        matrix.get("rows"),
        matrix.get("cols"),
        matrix.get("declared_entries"),
        matrix.get("field"),
        matrix.get("symmetry"),
    )
    if declared_graph != expected_graph:
        raise ValueError(f"{path}: graph metadata mismatch")
    grid = method.get("grid") or {}
    host = HostGrid(
        rows=_require_int(grid.get("rows"), f"{path}: grid.rows"),
        cols=_require_int(grid.get("cols"), f"{path}: grid.cols"),
        policy=str(grid.get("policy")),
    )
    raw_positions = witness.get("positions") or {}
    try:
        positions = {int(vertex): (coordinate[0], coordinate[1]) for vertex, coordinate in raw_positions.items()}
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError(f"{path}: malformed positions") from exc
    constraint_k = _require_int(witness.get("constraint_k"), f"{path}: constraint_k")
    verification = verify_placement(graph.n_vertices, graph.edges, host, positions, tested_k=constraint_k)
    if not verification.valid or verification.bandwidth is None:
        raise ValueError(f"{path}: invalid witness: {verification.error}")
    bandwidth = verification.bandwidth
    final_verified = _require_int(bounds.get("final_verified"), f"{path}: final_verified")
    witness_bandwidth = _require_int(witness.get("actual_bandwidth"), f"{path}: actual_bandwidth")
    if witness.get("valid") is not True or (bandwidth, final_verified, witness_bandwidth) != (bandwidth,) * 3:
        raise ValueError(f"{path}: recomputed bandwidth mismatch")
    status = outcome.get("status")
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"{path}: invalid status {status!r}")
    optimal = _require_int(bounds.get("optimal"), f"{path}: optimal", nullable=True)
    certified_lower = _require_int(bounds.get("certified_lower"), f"{path}: certified_lower", nullable=True)
    termination_k = _require_int(outcome.get("termination_k"), f"{path}: termination_k", nullable=True)
    if status.startswith("OPTIMAL_"):
        if optimal != bandwidth or certified_lower != bandwidth:
            raise ValueError(f"{path}: inconsistent optimal bounds")
        expected_k = bandwidth - 1 if status == "OPTIMAL_BY_UNSAT" else bandwidth
        if termination_k != expected_k:
            raise ValueError(f"{path}: inconsistent termination K")
    elif optimal is not None:
        raise ValueError(f"{path}: feasible incumbent cannot claim optimality")
    if status == "TIMEOUT_FEASIBLE" and termination_k is not None:
        raise ValueError(f"{path}: timeout termination K is not evidenced")
    return str(status)


def _verify_no_witness_document(path: Path, payload: Mapping[str, Any]) -> str:
    """Validate a timed-out run that never produced a feasible placement."""

    if payload.get("schema") != NO_WITNESS_SCHEMA:
        raise ValueError(f"{path}: unsupported schema {payload.get('schema')!r}")
    instance = payload.get("instance")
    method = payload.get("method")
    outcome = payload.get("outcome")
    bounds = payload.get("bounds")
    if not all(isinstance(value, dict) for value in (instance, method, outcome, bounds)):
        raise ValueError(f"{path}: missing required object")
    if payload.get("witness") is not None:
        raise ValueError(f"{path}: no-witness result must not contain a witness")
    mtx_path = _verification_path(payload, path)
    if instance.get("sha256") != _sha256(mtx_path):
        raise ValueError(f"{path}: input file/hash mismatch")
    graph = _graph_for_document(mtx_path, instance.get("graph_interpretation"))
    matrix = instance.get("matrix") or {}
    expected_graph = (
        graph.n_vertices, len(graph.edges), graph.interpretation, graph.rows, graph.cols,
        graph.declared_entries, graph.field, graph.symmetry,
    )
    declared_graph = (
        instance.get("vertices"), instance.get("graph_edges"), instance.get("graph_interpretation"),
        matrix.get("rows"), matrix.get("cols"), matrix.get("declared_entries"),
        matrix.get("field"), matrix.get("symmetry"),
    )
    if declared_graph != expected_graph:
        raise ValueError(f"{path}: graph metadata mismatch")
    grid = method.get("grid") or {}
    if (grid.get("rows"), grid.get("cols"), grid.get("policy")) != (
        2, math.ceil(graph.n_vertices / 2), "two-row",
    ):
        raise ValueError(f"{path}: no-witness host mismatch")
    if outcome.get("status") != NO_WITNESS_STATUS or outcome.get("termination") != "wall_timeout":
        raise ValueError(f"{path}: invalid no-witness outcome")
    if _require_int(outcome.get("termination_k"), f"{path}: termination_k", nullable=True) is not None:
        raise ValueError(f"{path}: no-witness termination K is not evidenced")
    if _require_int(bounds.get("final_verified"), f"{path}: final_verified", nullable=True) is not None:
        raise ValueError(f"{path}: no-witness result cannot have a final bandwidth")
    if _require_int(bounds.get("optimal"), f"{path}: optimal", nullable=True) is not None:
        raise ValueError(f"{path}: no-witness result cannot claim optimality")
    _require_int(bounds.get("initial_upper_bound"), f"{path}: initial_upper_bound")
    return NO_WITNESS_STATUS


def _manifest_artifacts(root: Path) -> list[str] | None:
    path = root / "manifest.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("artifacts")
    if payload.get("schema") != MANIFEST_SCHEMA or not isinstance(entries, list):
        raise ValueError(f"Invalid verification manifest: {path}")
    if not all(isinstance(entry, str) for entry in entries) or len(entries) != len(set(entries)):
        raise ValueError(f"Invalid verification manifest entries: {path}")
    return sorted(entries)


def _record_paths(root: Path) -> list[Path]:
    """Records directly in ``root`` and in its subfolders, except folders with their own manifest."""

    return sorted(
        path
        for path in [*root.glob("*.json"), *root.glob("*/*.json")]
        if path.name != "manifest.json"
        and (path.parent == root or not (path.parent / "manifest.json").is_file())
    )


def _verification_roots(root: Path) -> list[Path]:
    """``root`` and every folder below it that has its own manifest.json."""

    nested = sorted(path.parent for path in root.glob("**/manifest.json") if path.parent != root)
    return [root, *nested]


def _verify_records(root: Path) -> Counter[str]:
    files = _record_paths(root)
    actual = [path.relative_to(root).as_posix() for path in files]
    manifest = _manifest_artifacts(root)
    if manifest is not None and actual != manifest:
        raise ValueError("Verification artifacts do not match manifest")
    counts: Counter[str] = Counter()
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Top-level JSON must be an object: {path}")
        if payload.get("schema") == SCHEMA:
            counts[_verify_document(path, payload)] += 1
        elif payload.get("schema") == NO_WITNESS_SCHEMA:
            counts[_verify_no_witness_document(path, payload)] += 1
        else:
            raise ValueError(f"{path}: unsupported schema {payload.get('schema')!r}")
    return counts


def verify_tree(root: Path) -> Counter[str]:
    root = root.resolve()
    counts: Counter[str] = Counter()
    for tree in _verification_roots(root):
        counts.update(_verify_records(tree))
    if not sum(counts.values()):
        raise ValueError(f"No verification artifacts found under {root}")
    return counts


def write_manifest(root: Path) -> Path:
    root = root.resolve()
    artifacts = [path.relative_to(root).as_posix() for path in _record_paths(root)]
    if not artifacts:
        raise ValueError(f"No verification artifacts found under {root}")
    path = root / "manifest.json"
    write_json_atomic(path, {"schema": MANIFEST_SCHEMA, "artifacts": artifacts})
    return path


def _last_text_witness(path: Path) -> tuple[int, int, dict[int, tuple[int, int]]]:
    text = path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"VERIFICATION \[nxn-pairwise\]: K=(\d+), Actual Bandwidth=(\d+)", text))
    if not matches:
        raise ValueError(f"No n×n witness block in {path}")
    last = matches[-1]
    positions = {
        int(vertex): (int(row), int(col))
        for vertex, row, col in re.findall(r"^\s*v(\d+): \((\d+),(\d+)\)", text[last.start():], re.MULTILINE)
    }
    return int(last.group(1)), int(last.group(2)), positions


def _summary_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    return {Path(row["filename"]).stem: row for row in rows}


def convert_regular_nxn(source_root: Path, summary_csv: Path, output_root: Path) -> Counter[str]:
    """Normalize and independently validate the 43 historical Regular n×n witnesses."""

    artifacts = sorted(source_root.glob("*_verify.txt"))
    if len(artifacts) != 43:
        raise ValueError(f"Expected 43 Regular n×n witnesses, found {len(artifacts)}")
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"Output directory is not empty: {output_root}")
    rows = _summary_rows(summary_csv)
    if len(rows) != 43:
        raise ValueError(f"Expected 43 Regular n×n summary rows, found {len(rows)}")
    output_root.mkdir(parents=True)
    counts: Counter[str] = Counter()
    for artifact in artifacts:
        instance = artifact.name.removesuffix("__incremental_bandwidth_solver_cutoff_symmetry_nxn_verify.txt")
        row = rows.get(instance)
        if row is None:
            raise ValueError(f"No summary row for {instance}")
        input_path = SUBMIT_ROOT / "dataset" / "regular set" / f"{instance}.mtx"
        graph = parse_mtx_graph(input_path)
        host = HostGrid(graph.n_vertices, graph.n_vertices, "n-by-n-square")
        constraint_k, claimed_bandwidth, positions = _last_text_witness(artifact)
        verification = verify_placement(graph.n_vertices, graph.edges, host, positions, tested_k=constraint_k)
        if not verification.valid or verification.bandwidth != claimed_bandwidth:
            raise ValueError(f"Invalid historical witness {artifact}: {verification.error}")
        reported_k = int(row["K"])
        if row["status"] == "TO":
            status, termination, termination_k, lower, optimal = "TIMEOUT_FEASIBLE", "wall_timeout", None, int(row["LB"]), None
        elif claimed_bandwidth == 1:
            status, termination, termination_k, lower, optimal = "OPTIMAL_BY_BOUND", "minimum_positive_bandwidth", 1, 1, 1
        elif row["status"] == "SOLVED":
            status, termination, termination_k, lower, optimal = "OPTIMAL_BY_UNSAT", "unsat_below_verified_incumbent", claimed_bandwidth - 1, claimed_bandwidth, claimed_bandwidth
        else:
            raise ValueError(f"Unsupported status for {instance}: {row['status']!r}")
        document = {
            "schema": SCHEMA,
            "instance": {
                "file": _relative(input_path), "sha256": _sha256(input_path),
                "vertices": graph.n_vertices, "graph_edges": len(graph.edges),
                "graph_interpretation": graph.interpretation,
                "matrix": {"rows": graph.rows, "cols": graph.cols, "declared_entries": graph.declared_entries, "field": graph.field, "symmetry": graph.symmetry},
            },
            "method": {"id": "nxn-pairwise", "solver": row["solver"], "solver_script": "python/solvers/incremental_bandwidth_solver_cutoff_symmetry_nxn.py", "grid": {"rows": host.rows, "cols": host.cols, "policy": host.policy}},
            "outcome": {"status": status, "termination": termination, "termination_k": termination_k, "elapsed_s": float(row["elapsed_time"]), "timeout_s": int(row["timeout_limit"])},
            "bounds": {"certified_lower": lower, "final_verified": claimed_bandwidth, "optimal": optimal},
            "witness": {"valid": True, "constraint_k": constraint_k, "actual_bandwidth": claimed_bandwidth, "positions": {str(vertex): list(position) for vertex, position in sorted(positions.items())}},
            "provenance": {"legacy_artifact": _relative(artifact), "status_source": _relative(summary_csv), "source_reported_k": reported_k, "source_reported_k_verified": reported_k == claimed_bandwidth},
        }
        output = output_root / f"{instance}__incremental_bandwidth_solver_cutoff_symmetry_nxn_verify.json"
        write_json_atomic(output, document)
        counts[status] += 1
    return counts


def _two_row_text_witness(path: Path) -> tuple[int, int, dict[int, tuple[int, int]]]:
    text = path.read_text(encoding="utf-8")
    incumbent = re.search(r"^Current verified incumbent: (\d+)$", text, re.MULTILINE)
    lower = re.search(r"^Lower bound: (\d+)$", text, re.MULTILINE)
    table = re.search(
        r"--- Vertex labeling ---.*?^-+\s*$\n(?P<positions>.*?)^--- Edge Manhattan distances ---",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if incumbent is None or lower is None or table is None:
        raise ValueError(f"Malformed two-row witness: {path}")
    positions: dict[int, tuple[int, int]] = {}
    for vertex, row, column in re.findall(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s*$", table.group("positions"), re.MULTILINE):
        key = int(vertex)
        if key in positions:
            raise ValueError(f"Duplicate vertex in {path}: {key}")
        positions[key] = (int(row), int(column))
    return int(incumbent.group(1)), int(lower.group(1)), positions


def _text_witness_edges(path: Path) -> list[tuple[int, int]]:
    edge_section = path.read_text(encoding="utf-8").partition("--- Edge Manhattan distances ---")[2]
    raw_edges = [(int(u), int(v)) for u, v in re.findall(r"^\((\d+),(\d+)\)", edge_section, re.MULTILINE)]
    edges = normalize_edges(max((vertex for edge in raw_edges for vertex in edge), default=0), raw_edges)
    if not edges or len(edges) != len(raw_edges):
        raise ValueError(f"Malformed edge table in {path}")
    return edges


def _complete_bipartition(n_vertices: int, edges: Sequence[tuple[int, int]]) -> tuple[list[int], list[int]] | None:
    adjacency = {vertex: set() for vertex in range(1, n_vertices + 1)}
    for u, v in edges:
        adjacency[u].add(v)
        adjacency[v].add(u)
    colors: dict[int, int] = {1: 0}
    pending = [1]
    while pending:
        vertex = pending.pop()
        for neighbor in adjacency[vertex]:
            color = 1 - colors[vertex]
            if neighbor in colors:
                if colors[neighbor] != color:
                    return None
            else:
                colors[neighbor] = color
                pending.append(neighbor)
    if len(colors) != n_vertices:
        return None
    first = sorted(vertex for vertex, color in colors.items() if color == 0)
    second = sorted(vertex for vertex, color in colors.items() if color == 1)
    if len(edges) != len(first) * len(second):
        return None
    return first, second


def _remap_complete_bipartite_witness(
    source_positions: Mapping[int, tuple[int, int]], source_edges: Sequence[tuple[int, int]], graph: ParsedMtxGraph,
) -> tuple[dict[int, tuple[int, int]], dict[int, int]] | None:
    source_parts = _complete_bipartition(graph.n_vertices, source_edges)
    target_parts = _complete_bipartition(graph.n_vertices, graph.edges)
    if source_parts is None or target_parts is None:
        return None
    if tuple(map(len, source_parts)) == tuple(map(len, target_parts)):
        target_order = target_parts
    elif len(source_parts[0]) == len(target_parts[1]) and len(source_parts[1]) == len(target_parts[0]):
        target_order = (target_parts[1], target_parts[0])
    else:
        return None
    mapping = {
        source: target
        for source_part, target_part in zip(source_parts, target_order)
        for source, target in zip(source_part, target_part)
    }
    return {mapping[vertex]: position for vertex, position in source_positions.items()}, mapping


def convert_regular_two_row_lin_probe(source_root: Path, summary_csv: Path, output_root: Path) -> Counter[str]:
    """Normalize and independently validate the 43 historical Regular two-row probes."""

    artifacts = sorted(source_root.glob("*.txt"))
    if len(artifacts) != 43:
        raise ValueError(f"Expected 43 Regular two-row witnesses, found {len(artifacts)}")
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"Output directory is not empty: {output_root}")
    with summary_csv.open(newline="", encoding="utf-8") as stream:
        source_rows = list(csv.DictReader(stream))
    rows = _summary_rows(summary_csv)
    if len(source_rows) != 43 or len(rows) != 43:
        raise ValueError(f"Expected 43 unique Regular two-row summary rows in {summary_csv}")
    output_root.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    for artifact in artifacts:
        instance = artifact.stem
        row = rows.get(instance)
        if row is None:
            raise ValueError(f"No summary row for {instance}")
        input_path = SUBMIT_ROOT / "dataset" / "regular set" / row["filename"]
        graph = parse_mtx_graph(input_path)
        if (graph.n_vertices, len(graph.edges), _sha256(input_path)) != (
            int(row["n"]), int(row["E"]), row["instance_sha256"],
        ):
            raise ValueError(f"Source metadata does not match packaged input: {instance}")
        host = HostGrid.two_row(graph.n_vertices)
        claimed_bandwidth, text_lower, positions = _two_row_text_witness(artifact)
        if set(positions) != set(range(1, graph.n_vertices + 1)):
            raise ValueError(f"Incomplete vertex labeling in {artifact}")
        verification = verify_placement(graph.n_vertices, graph.edges, host, positions, tested_k=claimed_bandwidth)
        source_relabeling: dict[int, int] | None = None
        if not verification.valid:
            remapped = _remap_complete_bipartite_witness(positions, _text_witness_edges(artifact), graph)
            if remapped is not None:
                positions, source_relabeling = remapped
                verification = verify_placement(graph.n_vertices, graph.edges, host, positions, tested_k=claimed_bandwidth)
        if not verification.valid or verification.bandwidth != claimed_bandwidth:
            raise ValueError(f"Invalid historical witness {artifact}: {verification.error}")
        source_status = row["status"]
        if source_status == "TO":
            status, termination, termination_k, optimal = "TIMEOUT_FEASIBLE", "wall_timeout", None, None
        elif source_status == "SOLVED":
            status = row["outcome_status"]
            if status == "OPTIMAL_BY_BOUND":
                termination, termination_k = row["termination_reason"], claimed_bandwidth
            elif status == "OPTIMAL_BY_UNSAT":
                termination, termination_k = row["termination_reason"], claimed_bandwidth - 1
            else:
                raise ValueError(f"Unsupported solved outcome for {instance}: {status!r}")
            if int(row["K"]) != claimed_bandwidth:
                raise ValueError(f"Solved source K does not match witness for {instance}")
            optimal = claimed_bandwidth
        else:
            raise ValueError(f"Unsupported source status for {instance}: {source_status!r}")
        provenance: dict[str, Any] = {
            "source_witness": f"lin_probe_regular/verification/{artifact.name}",
            "summary": "lin_probe_regular/lin_probe_summary.csv",
            "source_status": source_status, "source_reported_k": int(row["K"]),
            "source_incumbent_k": int(row["incumbent_k"]), "source_lower_bound": int(row["LB"]),
            "source_termination_reason": row["termination_reason"], "lin_ub": int(row["lin_ub"]),
            "lin_multiplier": float(row["lin_multiplier"]), "requested_start_k": int(row["requested_start_k"]),
            "last_tested_k": int(row["last_tested_k"]),
        }
        if source_relabeling is not None:
            provenance["source_vertex_to_instance_vertex"] = {
                str(source): target for source, target in sorted(source_relabeling.items())
            }
        document = {
            "schema": SCHEMA,
            "instance": _instance_document(input_path, graph),
            "method": {
                "id": "two-row-lin-probe", "solver": row["solver"], "solver_script": row["solver_script"],
                "grid": {"rows": host.rows, "cols": host.cols, "policy": host.policy},
            },
            "outcome": {
                "status": status, "termination": termination, "termination_k": termination_k,
                "elapsed_s": float(row["elapsed_time"]), "timeout_s": int(row["timeout_limit"]),
            },
            "bounds": {
                "certified_lower": claimed_bandwidth if optimal is not None else text_lower,
                "initial_upper_bound": int(row["initial_ub"]), "final_verified": claimed_bandwidth, "optimal": optimal,
            },
            "witness": {
                "valid": True, "constraint_k": claimed_bandwidth, "actual_bandwidth": claimed_bandwidth,
                "positions": {str(vertex): list(position) for vertex, position in sorted(positions.items())},
            },
            "provenance": provenance,
        }
        output = output_root / f"{instance}__two_row_lin_probe_verify.json"
        write_json_atomic(output, document)
        counts[status] += 1
    return counts


def _source_candidates(source_roots: Sequence[Path]) -> dict[str, list[tuple[Path, Mapping[str, Any], int | None, Mapping[str, Any] | None, bool]]]:
    candidates: dict[str, list[tuple[Path, Mapping[str, Any], int | None, Mapping[str, Any] | None, bool]]] = {}
    for source_root in source_roots:
        for path in source_root.rglob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("schema") == SCHEMA:
                instance = payload.get("instance") or {}
                witness = payload.get("witness") or {}
                filename = Path(str(instance.get("file", ""))).name
                bandwidth = witness.get("actual_bandwidth")
                positions = witness.get("positions") if isinstance(witness.get("positions"), dict) else None
                optimal = (payload.get("outcome") or {}).get("status") in {"OPTIMAL_BY_UNSAT", "OPTIMAL_BY_BOUND"}
            else:
                filename = str(payload.get("filename", ""))
                bandwidth = payload.get("incumbent_k")
                positions = payload.get("incumbent_positions") if isinstance(payload.get("incumbent_positions"), dict) else None
                optimal = payload.get("optimal_k") == bandwidth and bandwidth is not None
            if not filename:
                continue
            if not isinstance(bandwidth, int) or isinstance(bandwidth, bool):
                bandwidth = None
            candidates.setdefault(filename, []).append((path, payload, bandwidth, positions, optimal))
    return candidates


def _instance_document(input_path: Path, graph: ParsedMtxGraph) -> dict[str, Any]:
    return {
        "file": _relative(input_path), "sha256": _sha256(input_path),
        "vertices": graph.n_vertices, "graph_edges": len(graph.edges),
        "graph_interpretation": graph.interpretation,
        "matrix": {
            "rows": graph.rows, "cols": graph.cols, "declared_entries": graph.declared_entries,
            "field": graph.field, "symmetry": graph.symmetry,
        },
    }


def _normalized_positions(positions: Mapping[str, Any]) -> dict[int, tuple[int, int]]:
    try:
        return {int(vertex): (int(coordinate[0]), int(coordinate[1])) for vertex, coordinate in positions.items()}
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError("source candidate positions are malformed") from exc


def convert_two_row_ub_bound(roster_csv: Path, source_roots: Sequence[Path], output_root: Path) -> Counter[str]:
    """Publish the requested two-row UB-bound roster with independently checked witnesses."""

    with roster_csv.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 93 or len({row.get("filename") for row in rows}) != 93:
        raise ValueError(f"Expected 93 unique roster rows in {roster_csv}")
    records = output_root / "records"
    if records.exists() and any(records.iterdir()):
        raise ValueError(f"Output records directory is not empty: {records}")
    records.mkdir(parents=True, exist_ok=True)
    candidates = _source_candidates(source_roots)
    counts: Counter[str] = Counter()
    for row in rows:
        filename = row["filename"]
        requested_k = int(row["K"])
        input_path = SUBMIT_ROOT / "dataset" / "Harwell Boeing set" / filename
        graph = parse_mtx_graph(input_path)
        if graph.n_vertices != int(row["n"]):
            raise ValueError(f"{filename}: roster order does not match the packaged instance")
        host = HostGrid.two_row(graph.n_vertices)
        available = candidates.get(filename, [])
        if requested_k < 0:
            source = next((item for item in available if item[2] is None and item[3] is None), None)
            if source is None:
                raise ValueError(f"{filename}: no timeout-without-witness source record")
            document: dict[str, Any] = {
                "schema": NO_WITNESS_SCHEMA,
                "instance": _instance_document(input_path, graph),
                "method": {
                    "id": "two-row-cutoff", "solver": row["solver"],
                    "solver_script": "python/solvers/incremental_bandwidth_solver_two_row.py",
                    "grid": {"rows": host.rows, "cols": host.cols, "policy": host.policy},
                },
                "outcome": {
                    "status": NO_WITNESS_STATUS, "termination": "wall_timeout", "termination_k": None,
                    "elapsed_s": float(row["solve_time"]), "timeout_s": int(row["timeout_limit"]),
                },
                "bounds": {"certified_lower": None, "initial_upper_bound": int(row["UB"]), "final_verified": None, "optimal": None},
                "witness": None,
                "provenance": {"source_artifact": _relative(source[0]), "roster": _relative(roster_csv)},
            }
        else:
            matching = [item for item in available if item[2] == requested_k and item[3] is not None]
            if not matching:
                raise ValueError(f"{filename}: no source witness with K={requested_k}")
            if row["status"] == "SOLVED":
                matching.sort(key=lambda item: (not item[4], str(item[0])))
                if not matching[0][4]:
                    raise ValueError(f"{filename}: solved roster row has no optimal source record")
            else:
                matching.sort(key=lambda item: str(item[0]))
            source = matching[0]
            positions = _normalized_positions(source[3])
            verification = verify_placement(graph.n_vertices, graph.edges, host, positions, tested_k=requested_k)
            if not verification.valid or verification.bandwidth != requested_k:
                raise ValueError(f"{filename}: invalid source witness: {verification.error}")
            if row["status"] == "SOLVED":
                status = "OPTIMAL_BY_BOUND" if requested_k == 1 or requested_k == int(row["UB"]) else "OPTIMAL_BY_UNSAT"
                termination = "minimum_positive_bandwidth" if status == "OPTIMAL_BY_BOUND" else "unsat_below_verified_incumbent"
                termination_k = requested_k if status == "OPTIMAL_BY_BOUND" else requested_k - 1
                lower = optimal = requested_k
            elif row["status"] == "TO":
                status, termination, termination_k, lower, optimal = "TIMEOUT_FEASIBLE", "wall_timeout", None, None, None
            else:
                raise ValueError(f"{filename}: unsupported roster status {row['status']!r}")
            document = {
                "schema": SCHEMA,
                "instance": _instance_document(input_path, graph),
                "method": {
                    "id": "two-row-cutoff", "solver": row["solver"],
                    "solver_script": "python/solvers/incremental_bandwidth_solver_two_row.py",
                    "grid": {"rows": host.rows, "cols": host.cols, "policy": host.policy},
                },
                "outcome": {"status": status, "termination": termination, "termination_k": termination_k, "elapsed_s": float(row["solve_time"]), "timeout_s": int(row["timeout_limit"])},
                "bounds": {"certified_lower": lower, "final_verified": requested_k, "optimal": optimal},
                "witness": {"valid": True, "constraint_k": requested_k, "actual_bandwidth": requested_k, "positions": {str(vertex): list(position) for vertex, position in sorted(positions.items())}},
                "provenance": {"source_artifact": _relative(source[0]), "roster": _relative(roster_csv)},
            }
        output = records / f"{Path(filename).stem}__two_row_ub_bound_verify.json"
        write_json_atomic(output, document)
        counts[document["outcome"]["status"]] += 1
    return counts


def _print_counts(label: str, counts: Counter[str]) -> None:
    print(f"{label}: {sum(counts.values())} artifacts")
    for status in sorted(counts):
        print(f"  {status}: {counts[status]}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and normalize compact 2DBMP result artifacts")
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--root", type=Path, default=DEFAULT_SOURCE)
    manifest = commands.add_parser("manifest")
    manifest.add_argument("--root", type=Path, default=DEFAULT_SOURCE)
    regular_nxn = commands.add_parser("convert-regular-nxn")
    regular_nxn.add_argument("--source", type=Path, required=True)
    regular_nxn.add_argument("--summary", type=Path, required=True)
    regular_nxn.add_argument("--output", type=Path, required=True)
    regular_two_row = commands.add_parser("convert-regular-two-row-lin-probe")
    regular_two_row.add_argument("--source", type=Path, required=True)
    regular_two_row.add_argument("--summary", type=Path, required=True)
    regular_two_row.add_argument("--output", type=Path, required=True)
    two_row_ub = commands.add_parser("convert-two-row-ub-bound")
    two_row_ub.add_argument("--roster", type=Path, required=True)
    two_row_ub.add_argument("--source", type=Path, nargs="+", required=True)
    two_row_ub.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "verify":
        _print_counts("Verified", verify_tree(args.root))
    elif args.command == "manifest":
        print(write_manifest(args.root))
    elif args.command == "convert-regular-nxn":
        _print_counts("Converted", convert_regular_nxn(args.source, args.summary, args.output))
    elif args.command == "convert-regular-two-row-lin-probe":
        _print_counts("Converted", convert_regular_two_row_lin_probe(args.source, args.summary, args.output))
    elif args.command == "convert-two-row-ub-bound":
        _print_counts("Converted", convert_two_row_ub_bound(args.roster, args.source, args.output))
    else:
        raise AssertionError(f"Unsupported command: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
