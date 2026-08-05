from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Mapping

from python.utils.host_grid import HostGrid
from python.utils.mtx_parser import ParsedMtxGraph


SCHEMA = "2dbmp-verification-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Persist the directory entry as well as the JSON contents where the
        # platform exposes directory file descriptors.
        try:
            directory_fd = os.open(path.parent, os.O_DIRECTORY)
        except (AttributeError, OSError):
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def build_verification_document(
    *,
    instance_file: str | None,
    instance_sha256: str | None,
    graph: ParsedMtxGraph,
    method_id: str,
    solver: str,
    solver_script: str,
    host: HostGrid,
    status: str,
    termination: str,
    termination_k: int | None,
    elapsed_s: float | None,
    timeout_s: int | None,
    certified_lower: int | None,
    final_verified: int,
    optimal: int | None,
    constraint_k: int,
    positions: Mapping[int, tuple[int, int]],
) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "instance": {
            "file": instance_file,
            "sha256": instance_sha256,
            "vertices": graph.n_vertices,
            "graph_edges": len(graph.edges),
            "graph_interpretation": graph.interpretation,
            "matrix": {
                "rows": graph.rows,
                "cols": graph.cols,
                "declared_entries": graph.declared_entries,
                "field": graph.field,
                "symmetry": graph.symmetry,
            },
        },
        "method": {
            "id": method_id,
            "solver": solver,
            "solver_script": solver_script,
            "grid": {
                "rows": host.rows,
                "cols": host.cols,
                "policy": host.policy,
            },
        },
        "outcome": {
            "status": status,
            "termination": termination,
            "termination_k": termination_k,
            "elapsed_s": elapsed_s,
            "timeout_s": timeout_s,
        },
        "bounds": {
            "certified_lower": certified_lower,
            "final_verified": final_verified,
            "optimal": optimal,
        },
        "witness": {
            "valid": True,
            "constraint_k": constraint_k,
            "actual_bandwidth": final_verified,
            "positions": {
                str(vertex): list(position)
                for vertex, position in sorted(positions.items())
            },
        },
        "provenance": {"generator": "solver-runtime"},
    }
