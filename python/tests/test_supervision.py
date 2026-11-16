from __future__ import annotations

import json
import sys
import tempfile
import time
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


GRANDCHILD_SCRIPT = """
import pathlib
import sys
import time

beat = pathlib.Path(sys.argv[1])
while True:
    with beat.open("a", encoding="utf-8") as handle:
        handle.write("x")
    time.sleep(0.05)
"""

PARENT_SCRIPT = """
import subprocess
import sys
import time

subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])
time.sleep(60)
"""


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

    def test_timeout_terminates_grandchildren(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "grandchild.py").write_text(GRANDCHILD_SCRIPT, encoding="utf-8")
            (root / "parent.py").write_text(PARENT_SCRIPT, encoding="utf-8")
            beat = root / "beat.txt"
            result = run_supervised(
                [sys.executable, str(root / "parent.py"), str(root / "grandchild.py"), str(beat)],
                cwd=root,
                timeout_s=2.0,
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
                terminate_grace_s=0.01,
            )
            self.assertTrue(result.timed_out)
            self.assertTrue(beat.is_file())
            size = beat.stat().st_size
            time.sleep(0.5)
            self.assertEqual(beat.stat().st_size, size)

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

    def test_workers_print_unicode_under_any_console_code_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_supervised(
                [sys.executable, "-c", "print('\\u2192')"],
                cwd=root,
                timeout_s=30.0,
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual((root / "stdout.log").read_text(encoding="utf-8"), "→\n")


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
