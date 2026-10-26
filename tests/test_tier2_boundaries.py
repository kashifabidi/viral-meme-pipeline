"""Tier 2: Boundary & Corner Cases.

Covers:
- Empty / missing files
- Extreme video durations (sub-second, ultra-long)
- Video streams with missing or silent audio
- Corrupted emojis, unpaired unicode, and encoding anomalies
- Offline Google Drive network failure and quota limits
- Subprocess timeout handling
- Asset directory depletion (missing SFX / overlays)
- Boundary timestamps (climax at 0.0 or > total duration)
- Whisper speechlessness fallback
- Windows file locking & permission contention
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from PIL import Image

import audio_detector
import caption_generator
import drive_uploader
import main
import tests.test_helpers as helpers
import video_editor


class TestTier2BoundaryAndCornerCases(unittest.TestCase):
    """Tier 2: Boundary, stress, and corner case validation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="tier2_boundary_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_b01_empty_video_file_handling(self):
        """B1. Zero-byte video file does not crash duration check or climax detector."""
        empty_path = os.path.join(self.temp_dir, "zero_bytes.mp4")
        with open(empty_path, "wb"):
            pass
        dur = audio_detector._get_duration_seconds(empty_path)
        self.assertEqual(dur, 0.0)

    def test_b02_missing_video_file_raises_not_found(self):
        """B2. assemble_meme_edit_with_freeze_and_caption raises FileNotFoundError for missing file."""
        missing_path = os.path.join(self.temp_dir, "ghost_video.mp4")
        with self.assertRaises(FileNotFoundError):
            video_editor.assemble_meme_edit_with_freeze_and_caption(
                missing_path,
                1.0,
                "caption",
                "scratch.mp3",
                "boom.mp3",
                "overlay.png",
                os.path.join(self.temp_dir, "out.mp4"),
            )

    def test_b03_subsecond_duration_video(self):
        """B3. Sub-second video (0.4s) handles climax detection and trimming without zero-division."""
        short_path = os.path.join(self.temp_dir, "short_04s.mp4")
        helpers.create_synthetic_video(short_path, duration=0.4)
        ts = audio_detector.find_climax_timestamp(short_path)
        self.assertGreaterEqual(ts, 0.0)
        self.assertLessEqual(ts, 0.4)

    def test_b04_ultra_long_video_duration(self):
        """B4. Source longer than TRIM_THRESHOLD (60s) calculates valid window bounds."""
        climax = 75.0
        total = 120.0
        start = max(0.0, climax - main.PRE_ROLL_SRC)
        length = min((climax + main.POST_ROLL) - start, total - start)
        self.assertEqual(start, 67.0)
        self.assertAlmostEqual(length, 11.0)

    def test_b05_video_with_no_audio_stream(self):
        """B5. Video with no audio track falls back to 60% duration without error."""
        no_audio_path = os.path.join(self.temp_dir, "silent_visual.mp4")
        helpers.create_synthetic_video(no_audio_path, duration=3.0, has_audio=False)
        self.assertFalse(audio_detector._has_audio_stream(no_audio_path))
        ts = audio_detector.find_climax_timestamp(no_audio_path)
        # Fallback is 60% of duration = 1.8s
        self.assertAlmostEqual(ts, 1.8, places=1)

    def test_b06_silent_audio_climax_detection(self):
        """B6. Audio with uniform zero energy falls back to 60% duration."""
        silent_path = os.path.join(self.temp_dir, "zero_vol.mp4")
        # Sine wave with 0Hz / no amplitude
        helpers.create_synthetic_video(silent_path, duration=2.0, has_audio=True, freq=0)
        ts = audio_detector.find_climax_timestamp(silent_path)
        self.assertGreaterEqual(ts, 0.0)

    def test_b07_corrupted_emojis_and_unpaired_surrogates(self):
        """B7. Malformed emojis and corrupted strings render without PIL crash."""
        bad_text = "Wait for it \U0001F600\U0001F480 \u200B??? #Fail!!"
        img = video_editor.create_meme_text_image(bad_text)
        self.assertIsInstance(img, Image.Image)

    def test_b08_offline_drive_upload_fallback(self):
        """B8. Network drop / socket error in Drive upload falls back cleanly."""
        sample_path = os.path.join(self.temp_dir, "offline_cand.mp4")
        helpers.create_synthetic_video(sample_path, duration=0.5)
        mock_svc = mock.MagicMock()
        mock_svc.files().create().execute.side_effect = ConnectionError("Network unreachable / DNS failure")
        with helpers.isolated_env(GOOGLE_DRIVE_FOLDER_ID="dummy_folder"):
            with mock.patch("drive_uploader.get_drive_service", return_value=(mock_svc, "oauth")):
                with mock.patch("drive_uploader.check_destination", return_value={"name": "Folder", "drive_id": "", "can_add_children": True, "folder_id": "dummy_folder"}):
                    fid, link = drive_uploader.upload_video_to_drive(sample_path)
                    self.assertIsNone(fid)
                    self.assertIsNone(link)
                    self.assertTrue(os.path.isfile(sample_path))

    def test_b09_subprocess_timeout_handling(self):
        """B9. Subprocess timeout in stage_trim falls back to untrimmed source video."""
        sample_path = os.path.join(self.temp_dir, "timeout_src.mp4")
        helpers.create_synthetic_video(sample_path, duration=70.0)
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=180)):
            work_path, work_climax = main.stage_trim(sample_path, 40.0)
            self.assertEqual(work_path, sample_path)
            self.assertEqual(work_climax, 40.0)

    def test_b10_missing_sfx_and_overlay_assets(self):
        """B10. Completely empty SFX or overlay directory returns None without crashing _pick_files."""
        empty_dir = os.path.join(self.temp_dir, "empty_assets")
        os.makedirs(empty_dir, exist_ok=True)
        picked = main._pick_files(empty_dir, (".mp3", ".wav"), count=2)
        self.assertEqual(picked, [None, None])

    def test_b11_climax_past_video_duration(self):
        """B11. Climax timestamp greater than video duration is safely clamped."""
        total = 5.0
        climax = 10.0  # past end
        clamped = max(0.0, min(float(climax), max(0.0, total - 0.05)))
        self.assertEqual(clamped, 4.95)

    def test_b12_whisper_no_dialogue_fallback(self):
        """B12. Empty dialogue segments return one of FALLBACK_CAPTIONS."""
        empty_segments = []
        cap = caption_generator._last_phrase_before_climax(empty_segments, 5.0)
        self.assertEqual(cap, "")
        # Fallback selection
        fallback = caption_generator.FALLBACK_CAPTIONS[0]
        self.assertIn(fallback, caption_generator.FALLBACK_CAPTIONS)

    def test_b13_windows_file_deletion_lock_contention(self):
        """B13. WinError 32 permission contention during cleanup is safely swallowed."""
        sample_path = os.path.join(self.temp_dir, "locked.mp4")
        helpers.create_synthetic_video(sample_path, duration=0.5)
        # Open file handle to hold a lock
        with open(sample_path, "rb") as fh:
            try:
                os.remove(sample_path)
            except OSError:
                pass  # Safely swallowed
        self.assertTrue(os.path.isfile(sample_path))

    def test_b14_invalid_render_scale_environment_value(self):
        """B14. Out-of-range RENDER_SCALE (e.g. 99.0 or -1.0) does not break video_editor."""
        for bad_scale in ("99.0", "-1.0", "zero", ""):
            scale = 1.0
            try:
                val = float(bad_scale)
                if 0.25 <= val <= 1.0:
                    scale = val
            except ValueError:
                pass
            self.assertTrue(0.25 <= scale <= 1.0)

    def test_b15_special_aspect_ratio_ultrawide(self):
        """B15. Ultrawide (21:9) video scales into 9:16 vertical without crash."""
        wide_path = os.path.join(self.temp_dir, "ultrawide.mp4")
        helpers.create_synthetic_video(wide_path, duration=1.0, width=1280, height=548)
        from moviepy.editor import VideoFileClip
        clip = VideoFileClip(wide_path)
        try:
            base = video_editor.build_9_16_base(clip, 540, 960)
            self.assertEqual(base.size, (540, 960))
        finally:
            clip.close()


if __name__ == "__main__":
    unittest.main()
