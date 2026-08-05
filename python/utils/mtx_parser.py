from __future__ import annotations

import os
from itertools import combinations
from dataclasses import dataclass
from pathlib import Path

from python.utils.solution_verifier import normalize_edges


@dataclass(frozen=True)
class ParsedMtxGraph:
    n_vertices: int
    edges: list[tuple[int, int]]
    rows: int
    cols: int
    interpretation: str
    declared_entries: int
    field: str
    symmetry: str


def _parse_legacy_edge_list(source: Path) -> ParsedMtxGraph:
    """Parse the repository's pre-Matrix-Market regular benchmark format.

    These files start with optional ``%`` comments followed by ``n n m`` and
    exactly ``m`` unweighted edge pairs.  Keeping this format explicit avoids
    silently weakening validation for actual Matrix Market inputs.
    """

    rows = cols = declared_entries = 0
    parsed_entries = 0
    header_seen = False
    raw_edges: list[tuple[int, int]] = []

    with source.open("r", encoding="utf-8", errors="strict") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line or line.startswith("%"):
                continue

            parts = line.split()
            if not header_seen:
                if len(parts) != 3:
                    raise ValueError("Invalid legacy edge-list size line")
                try:
                    rows, cols, declared_entries = map(int, parts)
                except ValueError as exc:
                    raise ValueError("Invalid legacy edge-list size line") from exc
                if rows <= 0 or cols <= 0 or declared_entries < 0:
                    raise ValueError("Legacy edge-list dimensions must be positive")
                if rows != cols:
                    raise ValueError(
                        "Legacy edge-list input must declare a square n n m header"
                    )
                header_seen = True
                continue

            if len(parts) != 2:
                raise ValueError(
                    f"Invalid legacy edge-list entry at line {line_number}"
                )
            try:
                u, v = int(parts[0]), int(parts[1])
            except ValueError as exc:
                raise ValueError(
                    f"Invalid legacy edge-list endpoint at line {line_number}"
                ) from exc
            if not 1 <= u <= rows or not 1 <= v <= rows:
                raise ValueError(
                    f"Legacy edge ({u}, {v}) is outside 1..{rows} at line {line_number}"
                )
            parsed_entries += 1
            raw_edges.append((u, v))

    if not header_seen:
        raise ValueError("Legacy edge-list file has no valid size line")
    if parsed_entries != declared_entries:
        raise ValueError(
            f"Legacy edge-list count mismatch: declared {declared_entries}, "
            f"parsed {parsed_entries}"
        )

    return ParsedMtxGraph(
        n_vertices=rows,
        edges=normalize_edges(rows, raw_edges),
        rows=rows,
        cols=cols,
        interpretation="legacy-edge-list-v1",
        declared_entries=declared_entries,
        field="pattern",
        symmetry="undirected-edge-list",
    )


def parse_mtx_graph(path: str | os.PathLike[str]) -> ParsedMtxGraph:
    """Parse a supported graph file as a simple graph with ``n = rows``.

    Square matrices are interpreted directly as adjacency matrices.  A
    rectangular matrix has rows as guest vertices and columns as features:
    two rows are adjacent exactly when they share a nonzero column.  This
    row-support projection keeps the first header dimension as the graph
    order and never treats an out-of-range column index as a vertex label.

    The repository's older regular benchmarks are not Matrix Market files:
    they are explicit square ``n n m`` edge lists preceded by ``%`` comments.
    They are accepted only through the separately labeled
    ``legacy-edge-list-v1`` compatibility format.
    """

    source = Path(path)
    with source.open("r", encoding="utf-8", errors="strict") as probe:
        first_content = next(
            (line.strip() for line in probe if line.strip()),
            "",
        )
    if not first_content.lower().startswith("%%matrixmarket"):
        return _parse_legacy_edge_list(source)

    n_vertices = 0
    rows = 0
    cols = 0
    matrix_field = ""
    symmetry = ""
    declared_entries = 0
    parsed_entries = 0
    banner_seen = False
    header_seen = False
    raw_edges: list[tuple[int, int]] = []
    column_rows: list[set[int]] = []

    with source.open("r", encoding="utf-8", errors="strict") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line:
                continue
            if line.lower().startswith("%%matrixmarket"):
                if banner_seen or header_seen:
                    raise ValueError(f"Duplicate Matrix Market banner at line {line_number}")
                banner = line.split()
                if (
                    len(banner) != 5
                    or banner[1].lower() != "matrix"
                    or banner[2].lower() != "coordinate"
                ):
                    raise ValueError("Expected a Matrix Market coordinate matrix")
                matrix_field = banner[3].lower()
                symmetry = banner[4].lower()
                if matrix_field not in {"pattern", "integer", "real", "complex"}:
                    raise ValueError(f"Unsupported Matrix Market field: {matrix_field}")
                if symmetry not in {
                    "general",
                    "symmetric",
                    "skew-symmetric",
                    "hermitian",
                }:
                    raise ValueError(f"Unsupported Matrix Market symmetry: {symmetry}")
                banner_seen = True
                continue
            if line.startswith("%"):
                continue

            parts = line.split()
            if not header_seen:
                if not banner_seen:
                    raise ValueError("Missing Matrix Market banner")
                if len(parts) != 3:
                    raise ValueError("Invalid Matrix Market size line")
                rows, cols, declared_entries = map(int, parts)
                if rows <= 0 or cols <= 0:
                    raise ValueError("Matrix Market dimensions must be positive")
                if declared_entries < 0:
                    raise ValueError("Matrix Market entry count cannot be negative")
                n_vertices = rows
                column_rows = [set() for _ in range(cols + 1)]
                header_seen = True
                continue

            expected_fields = 2 if matrix_field == "pattern" else 4 if matrix_field == "complex" else 3
            if len(parts) != expected_fields:
                raise ValueError(f"Invalid Matrix Market coordinate entry at line {line_number}")
            parsed_entries += 1
            try:
                u, v = int(parts[0]), int(parts[1])
            except ValueError as exc:
                raise ValueError(f"Invalid endpoint at line {line_number}") from exc
            if not 1 <= u <= rows or not 1 <= v <= cols:
                raise ValueError(
                    f"Matrix Market entry ({u}, {v}) is outside "
                    f"1..{rows} x 1..{cols}"
                )

            is_nonzero = True
            try:
                if matrix_field in {"integer", "real"}:
                    is_nonzero = float(parts[2]) != 0.0
                elif matrix_field == "complex":
                    is_nonzero = float(parts[2]) != 0.0 or float(parts[3]) != 0.0
            except ValueError as exc:
                raise ValueError(f"Invalid numeric value at line {line_number}") from exc
            if not is_nonzero:
                continue
            if rows == cols:
                if u != v:
                    raw_edges.append((u, v))
            else:
                column_rows[v].add(u)

    if not header_seen:
        raise ValueError("Matrix Market file has no valid size line")
    if parsed_entries != declared_entries:
        raise ValueError(
            f"Matrix Market entry count mismatch: declared {declared_entries}, "
            f"parsed {parsed_entries}"
        )
    if rows == cols:
        interpretation = "square-adjacency-v1"
        edges = normalize_edges(n_vertices, raw_edges)
    else:
        interpretation = "rectangular-row-support-v1"
        projected_edges: list[tuple[int, int]] = []
        for support in column_rows:
            projected_edges.extend(combinations(sorted(support), 2))
        edges = normalize_edges(n_vertices, projected_edges)

    return ParsedMtxGraph(
        n_vertices=n_vertices,
        edges=edges,
        rows=rows,
        cols=cols,
        interpretation=interpretation,
        declared_entries=declared_entries,
        field=matrix_field,
        symmetry=symmetry,
    )


def parse_mtx_file(path: str | os.PathLike[str]) -> tuple[int, list[tuple[int, int]]]:
    """Compatibility API returning only graph order and normalized edges."""

    graph = parse_mtx_graph(path)
    return graph.n_vertices, graph.edges
