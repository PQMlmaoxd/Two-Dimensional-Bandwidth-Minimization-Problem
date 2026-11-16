from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from python.utils.verification_artifacts import _record_paths, _verification_roots


class VerificationTreeTests(unittest.TestCase):
    def test_folders_with_their_own_manifest_are_separate_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "top.json").write_text("{}", encoding="utf-8")
            (root / "plain").mkdir()
            (root / "plain" / "a.json").write_text("{}", encoding="utf-8")
            (root / "nested").mkdir()
            (root / "nested" / "manifest.json").write_text("{}", encoding="utf-8")
            (root / "nested" / "b.json").write_text("{}", encoding="utf-8")

            self.assertEqual(_verification_roots(root), [root, root / "nested"])
            self.assertEqual(
                _record_paths(root),
                sorted([root / "top.json", root / "plain" / "a.json"]),
            )
            self.assertEqual(_record_paths(root / "nested"), [root / "nested" / "b.json"])


if __name__ == "__main__":
    unittest.main()
