from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from python.solve import run
from python.solvers.entrypoints import METHODS, worker_path
from python.utils.process_supervisor import run_supervised


class EntrypointTests(unittest.TestCase):
    def test_all_packaged_methods_have_workers(self):
        self.assertEqual(len(METHODS), 7)
        submit_root = Path(__file__).resolve().parents[2]
        for method in METHODS.values():
            self.assertTrue(worker_path(submit_root, method).is_file(), method.key)


class ProcessSupervisorTests(unittest.TestCase):
    def test_timeout_terminates_child_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_supervised(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=root,
                timeout_s=0.05,
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
                terminate_grace_s=0.01,
            )
            self.assertTrue(result.timed_out)
            self.assertLess(result.elapsed_s, 2.0)

    def test_completed_child_retains_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stdout, stderr = root / "stdout.log", root / "stderr.log"
            result = run_supervised(
                [sys.executable, "-c", "print('finished')"],
                cwd=root,
                timeout_s=1.0,
                stdout_path=stdout,
                stderr_path=stderr,
            )
            self.assertFalse(result.timed_out)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(stdout.read_text(encoding="utf-8"), "finished\n")


class PublicResultTests(unittest.TestCase):
    def test_missing_input_produces_error_document(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            payload = run(
                instance=Path(directory) / "missing.mtx",
                method_key="square",
                solver="cadical195",
                timeout_s=1.0,
                output=output,
            )
            self.assertEqual(payload["schema"], "2dbmp-run-error-v1")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["schema"], "2dbmp-run-error-v1")


if __name__ == "__main__":
    unittest.main()
