from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def drsa_binary() -> Path:
    override = os.environ.get("DRSA_BINARY")
    if override:
        return Path(override)
    for name in ("build/drsa", "build/drsa.exe"):
        candidate = REPO_ROOT / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("build the DRSA binary first with make drsa")


class DrsaInputTests(unittest.TestCase):
    def bandwidth(self, text: str) -> int:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.mtx"
            path.write_text(text, encoding="utf-8")
            completed = subprocess.run(
                [str(drsa_binary()), str(path), "42", "0"],
                capture_output=True,
                text=True,
                check=True,
            )
        label, value = completed.stdout.split()
        self.assertEqual(label, "RESULT")
        return int(value)

    def test_zero_valued_entries_are_not_edges(self):
        # The graph is the path 1-2-3, with bandwidth 1. If the zero entry (1, 3)
        # counted, the graph would be a triangle, whose bandwidth is 2 on a 2x2 grid.
        text = (
            "%%MatrixMarket matrix coordinate real general\n"
            "3 3 3\n"
            "1 2 1.0\n"
            "2 3 1.0\n"
            "1 3 0.0\n"
        )
        self.assertEqual(self.bandwidth(text), 1)

    def test_pattern_entries_always_count(self):
        text = "%%MatrixMarket matrix coordinate pattern general\n3 3 2\n1 2\n2 3\n"
        self.assertEqual(self.bandwidth(text), 1)


if __name__ == "__main__":
    unittest.main()
