from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


# Path resolution

_SCRIPT_DIR = Path(__file__).parent.resolve()
_DEFAULT_DRSA = _SCRIPT_DIR.parents[1] / "build" / "drsa"


def _find_drsa(drsa_path: Optional[str]) -> str:
    """Return path to the drsa binary, raising RuntimeError if not found."""
    if drsa_path is not None:
        p = Path(drsa_path)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
        raise RuntimeError(f"Specified drsa binary not found or not executable: {drsa_path}")

    environment_path = os.environ.get("DRSA_BINARY")
    if environment_path:
        return _find_drsa(environment_path)
    if _DEFAULT_DRSA.is_file() and os.access(_DEFAULT_DRSA, os.X_OK):
        return str(_DEFAULT_DRSA)

    raise RuntimeError(
        f"DRSA binary not found at {_DEFAULT_DRSA}.\n"
        "Build it with:  make drsa"
    )


# MTX writer (temporary file)

def _write_temp_mtx(n: int, edges: list[tuple[int, int]]) -> str:
    """Write a minimal MatrixMarket file and return its path."""
    fd, path = tempfile.mkstemp(prefix="drsa_", suffix=".mtx")
    with os.fdopen(fd, "w") as f:
        f.write("%%MatrixMarket matrix coordinate pattern symmetric\n")
        f.write(f"{n} {n} {len(edges)}\n")
        for u, v in edges:
            # Write 1-based
            f.write(f"{u} {v}\n")
    return path


# Core function

def compute_upper_bound_drsa(
    n: int,
    edges: list[tuple[int, int]],
    *,
    seed: int = 42,
    time_limit: float = 60.0,
    drsa_path: Optional[str] = None,
    mtx_file: Optional[str] = None,
    keep_mtx: bool = False,
    verbose: bool = False,
) -> int:
    """
    Run DRSA to obtain a heuristic upper bound for the 2D bandwidth of the
    given graph.

    Parameters
    ----------
    n          : number of vertices (1-based vertex IDs expected in `edges`).
    edges      : list of (u, v) pairs — 1-based, undirected, no self-loops.
    seed       : RNG seed forwarded to the drsa binary.
    time_limit : wall-clock budget in seconds.  DRSA will stop here and
                 return the best bandwidth found so far.  0 = run to
                 completion of the full cooling schedule (can be slow).
    drsa_path  : explicit path to the `drsa` binary.  Defaults to
                  ``build/drsa`` in the artifact root, or `DRSA_BINARY`.
    mtx_file   : if supplied, write the MTX here instead of a temp file.
    keep_mtx   : preserve the MTX file after the call (ignored when
                 `mtx_file` is given — caller's file is never deleted).
    verbose    : print DRSA stderr log to sys.stderr.

    Returns
    -------
    int — best bandwidth found (usable as upper bound for the SAT encoder).

    Raises
    ------
    RuntimeError  if the binary is missing or returns an unexpected exit code.
    """
    binary = _find_drsa(drsa_path)

    # Write MTX
    tmp_path: Optional[str] = None
    if mtx_file is not None:
        _write_mtx_to(n, edges, mtx_file)
        use_path = mtx_file
    else:
        use_path = _write_temp_mtx(n, edges)
        tmp_path = use_path

    try:
        cmd = [binary, use_path, str(seed), str(time_limit)]
        if verbose:
            print(f"[drsa_ub] Running: {' '.join(cmd)}", file=sys.stderr)

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        if verbose and proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)

        # Parse "RESULT <int>" from stdout
        result: Optional[int] = None
        for line in proc.stdout.splitlines():
            if line.startswith("RESULT "):
                try:
                    result = int(line.split()[1])
                except (IndexError, ValueError):
                    pass

        if result is None:
            raise RuntimeError(
                f"DRSA binary did not produce a RESULT line.\n"
                f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
            )

        return result

    finally:
        if tmp_path is not None and not keep_mtx:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _write_mtx_to(n: int, edges: list[tuple[int, int]], path: str) -> None:
    with open(path, "w") as f:
        f.write("%%MatrixMarket matrix coordinate pattern symmetric\n")
        f.write(f"{n} {n} {len(edges)}\n")
        for u, v in edges:
            f.write(f"{u} {v}\n")


# Standalone CLI

def _parse_mtx(filename: str):
    """Parse a MatrixMarket file → (n, edges_1based)."""
    with open(filename) as f:
        lines = f.readlines()
    edges_set: set[tuple[int,int]] = set()
    n = 0
    header_found = False
    for line in lines:
        line = line.strip()
        if not line or line.startswith("%"):
            continue
        parts = line.split()
        if not header_found:
            try:
                n = int(parts[0])
                header_found = True
            except (ValueError, IndexError):
                pass
            continue
        try:
            u, v = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            continue
        if u == v or u < 1 or v < 1 or u > n or v > n:
            continue
        edges_set.add((min(u, v), max(u, v)))
    return n, list(edges_set)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python drsa_upper_bound.py <mtx_file> [seed] [time_limit_s]")
        sys.exit(1)

    mtx  = sys.argv[1]
    seed = int(sys.argv[2]) if len(sys.argv) >= 3 else 42
    tlim = float(sys.argv[3]) if len(sys.argv) >= 4 else 60.0

    n, edges = _parse_mtx(mtx)
    print(f"Graph: n={n}  m={len(edges)}")

    ub = compute_upper_bound_drsa(n, edges, seed=seed, time_limit=tlim, verbose=True)
    print(f"DRSA upper bound: {ub}")
