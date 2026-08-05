from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from python.utils.host_grid import HostGrid


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    bandwidth: int | None
    error: str | None = None


def normalize_edges(
    n_vertices: int,
    edges: Sequence[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Validate and canonicalize a simple undirected edge list."""

    normalized: set[tuple[int, int]] = set()
    for u, v in edges:
        if not 1 <= u <= n_vertices or not 1 <= v <= n_vertices:
            raise ValueError(f"Edge ({u}, {v}) has an endpoint outside 1..{n_vertices}")
        if u == v:
            continue
        normalized.add((min(u, v), max(u, v)))
    return sorted(normalized)


def compute_bandwidth(
    positions: Mapping[int, tuple[int, int]],
    edges: Sequence[tuple[int, int]],
) -> int:
    """Recompute the L1 bandwidth directly from a complete placement."""

    bandwidth = 0
    for u, v in edges:
        row_u, col_u = positions[u]
        row_v, col_v = positions[v]
        bandwidth = max(bandwidth, abs(row_u - row_v) + abs(col_u - col_v))
    return bandwidth


def verify_placement(
    n_vertices: int,
    edges: Sequence[tuple[int, int]],
    host: HostGrid,
    positions: Mapping[int, tuple[int, int]],
    *,
    tested_k: int | None = None,
    anchor_vertex: int | None = None,
    enforce_two_row_anchor: bool = False,
) -> VerificationResult:
    """Fail-closed semantic validation independent of the SAT distance CNF."""

    expected_vertices = set(range(1, n_vertices + 1))
    actual_vertices = set(positions)
    if actual_vertices != expected_vertices:
        missing = sorted(expected_vertices - actual_vertices)
        extra = sorted(actual_vertices - expected_vertices)
        return VerificationResult(False, None, f"vertex mismatch: missing={missing}, extra={extra}")

    occupied: set[tuple[int, int]] = set()
    for vertex in range(1, n_vertices + 1):
        coordinate = positions[vertex]
        if not isinstance(coordinate, tuple) or len(coordinate) != 2:
            return VerificationResult(False, None, f"invalid coordinate for vertex {vertex}")
        row, col = coordinate
        if (
            not isinstance(row, int)
            or isinstance(row, bool)
            or not isinstance(col, int)
            or isinstance(col, bool)
        ):
            return VerificationResult(
                False,
                None,
                f"vertex {vertex} has non-integer coordinates: {coordinate}",
            )
        if not 1 <= row <= host.rows or not 1 <= col <= host.cols:
            return VerificationResult(False, None, f"vertex {vertex} is outside the host: {coordinate}")
        if coordinate in occupied:
            return VerificationResult(False, None, f"duplicate occupied cell: {coordinate}")
        occupied.add(coordinate)

    expected_empty = host.capacity - n_vertices
    if host.capacity - len(occupied) != expected_empty:
        return VerificationResult(False, None, "occupied-cell count is inconsistent")

    if enforce_two_row_anchor:
        if anchor_vertex is None or anchor_vertex not in positions:
            return VerificationResult(False, None, "missing symmetry anchor")
        row, col = positions[anchor_vertex]
        if row != 1 or col > (host.cols + 1) // 2:
            return VerificationResult(False, None, "two-row anchor symmetry is violated")

    try:
        normalized_edges = normalize_edges(n_vertices, edges)
        bandwidth = compute_bandwidth(positions, normalized_edges)
    except (KeyError, ValueError) as exc:
        return VerificationResult(False, None, str(exc))

    if tested_k is not None and bandwidth > tested_k:
        return VerificationResult(
            False,
            bandwidth,
            f"recomputed bandwidth {bandwidth} exceeds tested K={tested_k}",
        )
    return VerificationResult(True, bandwidth)
