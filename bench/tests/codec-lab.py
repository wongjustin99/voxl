#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=11"]
# ///
"""Exercise generated video, real encoders, metrics, and the comparison page."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

LAB = Path(__file__).resolve().parents[1] / "codec-lab.py"


class LabTest(unittest.TestCase):
    def test_quick_run_produces_complete_playable_comparisons(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            subprocess.run(
                [sys.executable, str(LAB), "--quick", "--output", str(root)], check=True
            )
            report = json.loads((root / "results.json").read_text())
            self.assertEqual(len(report["results"]), 6)
            for candidate in report["results"]:
                self.assertNotIn("error", candidate, candidate)
                self.assertEqual(candidate["frames"], 60)
                self.assertEqual(len(candidate["ssim_per_frame"]), 60)
                self.assertTrue(
                    (root / f"{candidate['name']}.review.mp4").stat().st_size > 0
                )
                self.assertGreater(candidate["max_serialization_ms_at_10mbps"], 0)
            page = (root / "index.html").read_text()
            self.assertIn("<svg", page)
            self.assertIn("source.review.mp4", page)
            self.assertTrue((root / "source.review.mp4").exists())
            # Never overwrite a completed experiment.
            repeat = subprocess.run(
                [sys.executable, str(LAB), "--quick", "--output", str(root)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(repeat.returncode, 0)
            self.assertIn("already exists", repeat.stderr)


if __name__ == "__main__":
    unittest.main()
