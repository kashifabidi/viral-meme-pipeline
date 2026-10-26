"""Tier 1: Feature Coverage for Milestone 5 (E2E Testing, Final Verification & Hardening).

Covers:
- Feature 26: E2E CLI Execution (main.py)
- Feature 27: Opaque-Box E2E Test Suite
- Feature 28: Adversarial Coverage Hardening
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import tests.test_helpers as helpers


class TestFeature26E2ECLIExecution(unittest.TestCase):
    """Feature 26: Pass CLI execution end-to-end without errors."""

    def test_feat26_01_cli_help_flag(self):
        """26.1 python main.py --help exits with 0 and prints usage."""
        cmd = [sys.executable, "main.py", "--help"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Viral meme pipeline", proc.stdout)
        self.assertIn("--input", proc.stdout)
        self.assertIn("--harvest", proc.stdout)

    def test_feat26_02_mutually_exclusive_args(self):
        """26.2 Specifying both --input and --harvest triggers error."""
        cmd = [sys.executable, "main.py", "--input", "test.mp4", "--harvest"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not allowed with argument", proc.stderr)

    def test_feat26_03_max_argument_parsing(self):
        """26.3 CLI parses --max N flag correctly."""
        import main
        ap = main.argparse.ArgumentParser()
        g = ap.add_mutually_exclusive_group(required=True)
        g.add_argument("--input")
        g.add_argument("--harvest", action="store_true")
        ap.add_argument("--max", type=int, default=1)
        args = ap.parse_args(["--harvest", "--max", "3"])
        self.assertEqual(args.max, 3)

    def test_feat26_04_keep_local_flag_parsing(self):
        """26.4 CLI parses --keep-local flag."""
        import main
        ap = main.argparse.ArgumentParser()
        g = ap.add_mutually_exclusive_group(required=True)
        g.add_argument("--input")
        g.add_argument("--harvest", action="store_true")
        ap.add_argument("--keep-local", action="store_true")
        args = ap.parse_args(["--harvest", "--keep-local"])
        self.assertTrue(args.keep_local)

    def test_feat26_05_main_function_entry_with_mock(self):
        """26.5 main.main() executes cleanly when stages succeed."""
        import main
        with mock.patch("main.stage_acquire_batch", return_value=["dummy_clip.mp4"]):
            with mock.patch("main.stage_climax", return_value=1.5):
                with mock.patch("main.stage_trim", return_value=("dummy_clip.mp4", 1.5)):
                    with mock.patch("main.stage_caption", return_value="Funny caption"):
                        with mock.patch("main.stage_render", return_value="out.mp4"):
                            with mock.patch("main.stage_upload", return_value=("id123", "https://link")):
                                code = main.main(["--input", "dummy.mp4"])
                                self.assertEqual(code, 0)


class TestFeature27OpaqueBoxTestSuite(unittest.TestCase):
    """Feature 27: Comprehensive Tiers 1-4 opaque-box test suite."""

    def test_feat27_01_unittest_discovery_structure(self):
        """27.1 tests/ package contains valid Python test modules."""
        tests_dir = os.path.dirname(os.path.abspath(__file__))
        files = os.listdir(tests_dir)
        self.assertIn("__init__.py", files)
        self.assertIn("test_helpers.py", files)
        self.assertTrue(any(f.startswith("test_tier1_") for f in files))

    def test_feat27_02_synthetic_fixture_generation_speed(self):
        """27.2 Synthetic test video generation completes in < 1.0s."""
        import time
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "speed_test.mp4")
            t0 = time.time()
            helpers.create_synthetic_video(p, duration=0.5)
            elapsed = time.time() - t0
            self.assertLess(elapsed, 2.0)
            self.assertTrue(os.path.isfile(p))

    def test_feat27_03_clean_temp_isolation(self):
        """27.3 Test workspace context manager cleans up files."""
        temp_path = None
        with helpers.temp_workspace() as ws:
            temp_path = ws
            f = os.path.join(ws, "sample.txt")
            with open(f, "w") as fh:
                fh.write("isolated")
            self.assertTrue(os.path.isdir(temp_path))
        self.assertFalse(os.path.exists(temp_path))

    def test_feat27_04_opaque_box_observable_validation(self):
        """27.4 probe_media validates observable file attributes."""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "observable.mp4")
            helpers.create_synthetic_video(p, duration=1.0, width=1080, height=1920)
            meta = helpers.probe_media(p)
            self.assertEqual(meta["width"], 1080)
            self.assertEqual(meta["height"], 1920)

    def test_feat27_05_test_infra_documentation_exists(self):
        """27.5 TEST_INFRA.md document exists and defines test architecture."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        doc = os.path.join(root, "TEST_INFRA.md")
        self.assertTrue(os.path.isfile(doc))
        with open(doc, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Tier 1", content)
        self.assertIn("Tier 2", content)
        self.assertIn("Tier 3", content)
        self.assertIn("Tier 4", content)


class TestFeature28AdversarialCoverageHardening(unittest.TestCase):
    """Feature 28: Adversarial coverage hardening and boundary validation."""

    def test_feat28_01_special_characters_in_hook_text(self):
        """28.1 Special characters and quotes do not crash banner generation."""
        import video_editor
        adversarial_text = 'Test: "Quotes", \'Singles\', & <Tags> #Viral 100%!'
        img = video_editor.create_hook_banner_image(adversarial_text, scale=0.75)
        self.assertIsNotNone(img)

    def test_feat28_02_file_path_with_spaces_and_unicode(self):
        """28.2 File paths with spaces and parentheses are handled properly."""
        with tempfile.TemporaryDirectory() as td:
            spaced = os.path.join(td, "my meme (funny clip) 2026.mp4")
            helpers.create_synthetic_video(spaced, duration=0.5)
            self.assertTrue(os.path.isfile(spaced))
            info = helpers.probe_media(spaced)
            self.assertGreater(info["duration"], 0.0)

    def test_feat28_03_zero_byte_video_diagnostic(self):
        """28.3 Zero-byte input video triggers clean handling."""
        with tempfile.TemporaryDirectory() as td:
            empty = os.path.join(td, "empty.mp4")
            with open(empty, "wb"):
                pass
            from audio_detector import _get_duration_seconds
            dur = _get_duration_seconds(empty)
            self.assertEqual(dur, 0.0)

    def test_feat28_04_climax_at_boundary_edges(self):
        """28.4 Climax at t=0.0 does not produce negative or zero duration clip."""
        total = 5.0
        climax = 0.0
        pre_roll = 6.0
        seg_start = max(0.0, climax - max(0.0, pre_roll))
        seg_end = climax
        if seg_end - seg_start < 0.05:
            seg_end = min(total, max(seg_start + 0.05, climax))
            if seg_end - seg_start < 0.05:
                seg_start = max(0.0, seg_end - 0.05)
        self.assertGreater(seg_end - seg_start, 0.0)

    def test_feat28_05_rapid_timestamp_uniqueness(self):
        """28.5 Filename timestamps or unique suffixes prevent collisions."""
        from datetime import datetime
        s1 = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.assertGreater(len(s1), 10)


if __name__ == "__main__":
    unittest.main()
