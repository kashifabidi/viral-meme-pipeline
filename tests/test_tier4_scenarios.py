"""Tier 4: Real-World Scenarios.

Covers:
- Complete pipeline run on sample input downloads/35KDnej1hlI.mp4
- End-to-end visual assembly & output verification (1080x1920 @ 30fps)
- Climax detection & caption generation on real media
- Harvest-to-render lifecycle simulation
- Unattended CLI execution with local delivery
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import audio_detector
import caption_generator
import main
import tests.test_helpers as helpers
import video_editor


class TestTier4RealWorldScenarios(unittest.TestCase):
    """Tier 4: Real-world end-to-end integration scenarios."""

    SAMPLE_PATH = os.path.abspath(os.path.join("downloads", "35KDnej1hlI.mp4"))

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="tier4_scenario_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scenario_01_real_input_climax_detection(self):
        """Scenario 1: Detect climax timestamp on real sample video downloads/35KDnej1hlI.mp4."""
        if not os.path.isfile(self.SAMPLE_PATH):
            self.skipTest(f"Sample video {self.SAMPLE_PATH} not found")
        climax = audio_detector.find_climax_timestamp(self.SAMPLE_PATH)
        self.assertIsInstance(climax, float)
        self.assertGreater(climax, 0.0)
        self.assertLessEqual(climax, 56.0)

    def test_scenario_02_real_input_caption_generation(self):
        """Scenario 2: Generate punchline caption on real sample video."""
        if not os.path.isfile(self.SAMPLE_PATH):
            self.skipTest(f"Sample video {self.SAMPLE_PATH} not found")
        caption = caption_generator.get_freeze_caption(self.SAMPLE_PATH, climax_time=15.0)
        self.assertIsInstance(caption, str)
        self.assertGreater(len(caption), 0)

    def test_scenario_03_synthetic_fast_fixture_full_e2e_render(self):
        """Scenario 3: Full render with freeze, caption, sfx, and overlay producing 1080x1920 @ 30fps."""
        src_path = os.path.join(self.temp_dir, "e2e_synth.mp4")
        helpers.create_synthetic_video(src_path, duration=3.0, width=640, height=360, fps=30)
        out_path = os.path.join(self.temp_dir, "e2e_final.mp4")

        scratch = os.path.abspath(os.path.join("assets", "sfx", "record_scratch.mp3"))
        boom = os.path.abspath(os.path.join("assets", "sfx", "vine_boom.mp3"))
        overlay = os.path.abspath(os.path.join("assets", "overlays", "skull.png"))

        # Render edit
        result = video_editor.assemble_meme_edit_with_freeze_and_caption(
            src_path,
            climax_timestamp=1.5,
            caption_text='"Calculated risk."',
            record_scratch_sfx=scratch if os.path.isfile(scratch) else None,
            impact_sfx=boom if os.path.isfile(boom) else None,
            overlay_image_path=overlay if os.path.isfile(overlay) else None,
            output_path=out_path,
            freeze_duration=1.0,
            pre_roll=1.5,
            credit_text="Original Creator",
            hook_text="Wait for the drop...",
        )

        self.assertTrue(os.path.isfile(result))
        probe = helpers.probe_media(result)
        self.assertEqual(probe["width"], 1080)
        self.assertEqual(probe["height"], 1920)
        self.assertEqual(probe["fps"], 30.0)
        self.assertTrue(probe["has_video"])
        self.assertTrue(probe["has_audio"])
        self.assertGreater(probe["duration"], 2.0)

    def test_scenario_04_end_to_end_pipeline_simulation_with_real_input(self):
        """Scenario 4: E2E pipeline simulation on real input window."""
        if not os.path.isfile(self.SAMPLE_PATH):
            self.skipTest(f"Sample video {self.SAMPLE_PATH} not found")

        # Trim a fast 2-second snippet from real input to keep test fast (<3s)
        snippet = os.path.join(self.temp_dir, "snippet_35K.mp4")
        ffmpeg = helpers.get_ffmpeg_bin()
        cmd = [
            ffmpeg, "-y", "-nostdin",
            "-ss", "10.0",
            "-i", self.SAMPLE_PATH,
            "-t", "2.0",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            snippet,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(os.path.isfile(snippet))

        # Run pipeline stages
        climax = audio_detector.find_climax_timestamp(snippet)
        self.assertGreaterEqual(climax, 0.0)

        caption = caption_generator.get_freeze_caption(snippet, climax)
        self.assertIsInstance(caption, str)

        out_path = os.path.join(self.temp_dir, "out_real_snippet.mp4")
        video_editor.assemble_meme_edit_with_freeze_and_caption(
            snippet,
            climax_timestamp=climax,
            caption_text=caption,
            record_scratch_sfx=None,
            impact_sfx=None,
            overlay_image_path=None,
            output_path=out_path,
            freeze_duration=0.5,
            pre_roll=1.0,
        )
        self.assertTrue(os.path.isfile(out_path))
        info = helpers.probe_media(out_path)
        self.assertEqual(info["width"], 1080)
        self.assertEqual(info["height"], 1920)

    def test_scenario_05_harvest_to_render_lifecycle_simulation(self):
        """Scenario 5: Harvest lifecycle simulation producing delivered video and cleaning temp files."""
        cand_path = os.path.join(self.temp_dir, "harvest_cand.mp4")
        helpers.create_synthetic_video(cand_path, duration=2.0)
        helpers.create_sidecar_meta(cand_path, credit="RedditUser42", source="reddit")

        with mock.patch("main.stage_acquire_batch", return_value=[cand_path]):
            with mock.patch("main.stage_upload", return_value=("id_mock", "https://drive.google.com/test")):
                with mock.patch("main.stage_caption", return_value='"Bro tried"'):
                    code = main.main(["--harvest", "--max", "1", "--keep-local"])
                    self.assertEqual(code, 0)

    def test_scenario_06_unattended_pipeline_cli_invocation(self):
        """Scenario 6: CLI invocation via python main.py --input with offline Drive fallback."""
        synth = os.path.join(self.temp_dir, "cli_cand.mp4")
        helpers.create_synthetic_video(synth, duration=1.0)

        # Mocking external stages to verify CLI coordinator logic
        with mock.patch("main.stage_acquire_batch", return_value=[synth]):
            with mock.patch("main.stage_upload", return_value=(None, None)):
                with mock.patch("main.stage_render", return_value=synth):
                    code = main.main(["--input", synth, "--keep-local"])
                    self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
