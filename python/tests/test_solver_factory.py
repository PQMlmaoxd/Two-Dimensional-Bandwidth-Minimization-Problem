from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from pysat.solvers import Cadical195, Glucose42

try:
    from pysat.solvers import Cadical300
except ImportError:  # PySAT < 1.9.dev3 remains usable for cadical195.
    Cadical300 = None

from python.solvers.solver_factory import CADICAL300_PARAMETERS, create_sat_solver


class SolverFactoryTests(unittest.TestCase):
    def test_default_remains_cadical195(self):
        solver = create_sat_solver()
        try:
            self.assertIsInstance(solver, Cadical195)
        finally:
            solver.delete()

    def test_all_supported_backends_solve_the_same_formulas(self):
        backends = [("cadical195", Cadical195), ("glucose42", Glucose42)]
        if Cadical300 is not None:
            backends.insert(1, ("cadical300", Cadical300))
        for solver_name, solver_class in backends:
            with self.subTest(solver=solver_name):
                solver = create_sat_solver(solver_name)
                try:
                    self.assertIsInstance(solver, solver_class)
                    solver.append_formula([[1, 2], [-1, 2]])
                    self.assertTrue(solver.solve())
                    solver.add_clause([-2])
                    self.assertFalse(solver.solve())
                finally:
                    solver.delete()

    def test_cadical300_reports_a_targeted_error_when_unavailable(self):
        if Cadical300 is not None:
            self.skipTest("cadical300 is available")
        with self.assertRaisesRegex(RuntimeError, "python-sat>=1.9.dev3"):
            create_sat_solver("cadical300")

    @unittest.skipIf(Cadical300 is None, "cadical300 is unavailable")
    def test_cadical300_uses_incremental_safe_parameters(self):
        with patch("pysat.solvers.Cadical300") as solver_class:
            solver = create_sat_solver("cadical300")

        solver_class.assert_called_once_with()
        solver.configure.assert_called_once_with(CADICAL300_PARAMETERS)

    def test_unknown_solver_policies_remain_available(self):
        with self.assertRaisesRegex(ValueError, "Unsupported solver"):
            create_sat_solver("unknown")

        output = io.StringIO()
        with redirect_stdout(output):
            solver = create_sat_solver(
                "unknown",
                fallback="glucose42",
                warn_on_fallback=True,
            )
        try:
            self.assertIsInstance(solver, Glucose42)
            self.assertIn("using glucose42", output.getvalue())
        finally:
            solver.delete()


if __name__ == "__main__":
    unittest.main()
