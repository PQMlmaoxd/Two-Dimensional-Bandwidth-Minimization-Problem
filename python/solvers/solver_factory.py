"""Create supported PySAT backends without changing existing defaults."""

from __future__ import annotations


DEFAULT_SOLVER = "cadical195"
SUPPORTED_SOLVERS = ("cadical195", "cadical300", "glucose42")

# The active solvers strengthen one persistent formula with additional K
# clauses. Keep BVA disabled until solver instances are synchronized with the
# owning IDPool; enable full incremental lazy backtracking for that workflow.
CADICAL300_PARAMETERS = {
    "factor": 0,
    "ilb": 2,
}


def create_sat_solver(
    solver_type: str = DEFAULT_SOLVER,
    *,
    fallback: str | None = None,
    warn_on_fallback: bool = False,
):
    """Create a solver, loading CaDiCaL 3.0 only when explicitly selected."""

    if solver_type == "cadical195":
        from pysat.solvers import Cadical195

        return Cadical195()
    if solver_type == "cadical300":
        try:
            from pysat.solvers import Cadical300
        except ImportError as exc:
            raise RuntimeError(
                "cadical300 requires python-sat>=1.9.dev3; "
                "cadical195 remains available with the current installation"
            ) from exc

        solver = Cadical300()
        solver.configure(CADICAL300_PARAMETERS)
        return solver
    if solver_type == "glucose42":
        from pysat.solvers import Glucose42

        return Glucose42()

    if fallback is None:
        supported = ", ".join(SUPPORTED_SOLVERS)
        raise ValueError(f"Unsupported solver '{solver_type}'; choose one of: {supported}")
    if fallback not in SUPPORTED_SOLVERS:
        raise ValueError(f"Unsupported fallback solver '{fallback}'")
    if warn_on_fallback:
        print(f"Unknown solver '{solver_type}', using {fallback}")
    return create_sat_solver(fallback)
