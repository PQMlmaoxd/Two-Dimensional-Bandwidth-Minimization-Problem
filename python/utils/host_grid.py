from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HostGrid:
    """Checked rectangular host geometry for a graph embedding."""

    rows: int
    cols: int
    policy: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.rows, int)
            or isinstance(self.rows, bool)
            or not isinstance(self.cols, int)
            or isinstance(self.cols, bool)
        ):
            raise ValueError("Host dimensions must be integers")
        if self.rows <= 0 or self.cols <= 0:
            raise ValueError("Host dimensions must be positive")

    @property
    def capacity(self) -> int:
        return self.rows * self.cols

    @property
    def diameter(self) -> int:
        return self.rows + self.cols - 2

    def validate_capacity(self, n_vertices: int) -> None:
        if not isinstance(n_vertices, int) or isinstance(n_vertices, bool):
            raise ValueError("Vertex count must be an integer")
        if n_vertices < 0:
            raise ValueError("Vertex count cannot be negative")
        if self.capacity < n_vertices:
            raise ValueError(
                f"Host {self.rows}x{self.cols} has {self.capacity} cells for "
                f"{n_vertices} vertices"
            )

    @classmethod
    def two_row(cls, n_vertices: int) -> "HostGrid":
        if n_vertices <= 0:
            raise ValueError("The two-row host requires at least one vertex")
        host = cls(rows=2, cols=(n_vertices + 1) // 2, policy="two-row")
        host.validate_capacity(n_vertices)
        return host


def host_cells(host: HostGrid, snake: bool = False) -> list[tuple[int, int]]:
    """Return all host cells in deterministic row-major or snake order."""

    cells: list[tuple[int, int]] = []
    for row in range(1, host.rows + 1):
        columns = range(1, host.cols + 1)
        if snake and row % 2 == 0:
            columns = range(host.cols, 0, -1)
        cells.extend((row, col) for col in columns)
    return cells


def deterministic_placement(
    vertices: list[int],
    host: HostGrid,
    *,
    snake: bool = False,
) -> dict[int, tuple[int, int]]:
    """Place a deterministic vertex order injectively on the host."""

    host.validate_capacity(len(vertices))
    cells = host_cells(host, snake=snake)
    return {vertex: cells[index] for index, vertex in enumerate(vertices)}
