#!/usr/bin/env python3
"""Offline replay contract; never captures a display or connects to a server."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

BINARY = Path(
    os.environ.get("CODEC_REPLAY_BIN", "target/release/examples/codec-replay")
)


class ReplayTest(unittest.TestCase):
    def test_encodes_every_raw_frame_and_reports_keyframe_and_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.yuv"
            source.write_bytes(
                b"".join(
                    bytes([y]) * 1024 + bytes([128]) * 512 for y in (16, 80, 180, 235)
                )
            )
            out, stats = root / "out.h264", root / "stats.jsonl"
            subprocess.run(
                [
                    str(BINARY),
                    str(source),
                    "32",
                    "32",
                    "30",
                    "24",
                    "10000",
                    "ultrafast",
                    "1.25",
                    str(out),
                    str(stats),
                ],
                check=True,
                capture_output=True,
            )
            rows = [json.loads(line) for line in stats.read_text().splitlines()]
            self.assertEqual([row["frame"] for row in rows], list(range(4)))
            self.assertTrue(rows[0]["keyframe"])
            self.assertTrue(
                all(row["bytes"] > 0 and row["encode_us"] >= 0 for row in rows)
            )
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-count_frames",
                    "-show_entries",
                    "stream=nb_read_frames",
                    "-of",
                    "json",
                    str(out),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                json.loads(probe.stdout)["streams"][0]["nb_read_frames"], "4"
            )

    def test_accepts_streaming_input_before_a_whole_frame_has_arrived(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = subprocess.Popen(
                [
                    str(BINARY),
                    "/dev/stdin",
                    "32",
                    "32",
                    "30",
                    "24",
                    "10000",
                    "ultrafast",
                    "1.25",
                    str(root / "out.h264"),
                    str(root / "stats.jsonl"),
                ],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            process.stdin.write(b"\x10")
            process.stdin.flush()
            time.sleep(0.1)
            _, stderr = process.communicate(b"\x10" * 1023 + b"\x80" * 512)
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertEqual(len((root / "stats.jsonl").read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
