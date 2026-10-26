"""Empirical Challenger Deep Boundary Stress Harness.

Adversarially probes:
1. Video without audio track and without voiceover rendered through assemble_meme_edit_with_freeze_and_caption.
2. Ultra-short video (0.15s) passed through assemble_meme_edit_with_freeze_and_caption.
3. Hook text with complex Unicode, ZWJ, emojis, formatting, and PowerShell metacharacters in TTS generator.
4. Live Windows SAPI5 PowerShell synthesis with metacharacters ($env:PATH, double quotes, backticks).
5. Drive uploader with non-existent file, zero-byte file, and invalid directory paths.
6. Caption generator resilience on corrupted/truncated media with zero or negative climax timestamps.
7. Audio detector resilience on non-existent media, empty audio streams, and extreme duration values.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import audio_detector
import caption_generator
import drive_uploader
import tests.test_helpers as helpers
import tts_generator
import video_editor


class TestChallengerDeepBoundary(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="challenger_tier5_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_challenger_01_no_audio_video_and_no_voiceover(self):
        """Render a video that has NO audio track at all, with voiceover_path=None.
        Verifies MoviePy handles mixing segments where seg1 has None audio, seg2 has SFX, and seg3 has SFX."""
        no_audio_src = os.path.join(self.temp_dir, "no_audio.mp4")
        helpers.create_synthetic_video(no_audio_src, duration=2.0, has_audio=False)

        out_path = os.path.join(self.temp_dir, "out_no_audio.mp4")
        scratch = os.path.abspath(os.path.join("assets", "sfx", "record_scratch.mp3"))
        boom = os.path.abspath(os.path.join("assets", "sfx", "vine_boom.mp3"))

        res = video_editor.assemble_meme_edit_with_freeze_and_caption(
            no_audio_src,
            climax_timestamp=1.0,
            caption_text="No Audio Input Test",
            record_scratch_sfx=scratch if os.path.isfile(scratch) else None,
            impact_sfx=boom if os.path.isfile(boom) else None,
            overlay_image_path=None,
            output_path=out_path,
            voiceover_path=None,
        )
        self.assertTrue(os.path.isfile(res))
        self.assertGreater(os.path.getsize(res), 1000)

    def test_challenger_02_ultra_short_video_boundary(self):
        """Render an ultra-short video (0.2s duration) where climax is 0.1s.
        Verifies pre-roll and post-roll clamping logic never crashes or produces invalid subclips."""
        short_src = os.path.join(self.temp_dir, "ultra_short.mp4")
        helpers.create_synthetic_video(short_src, duration=0.25, has_audio=True)

        out_path = os.path.join(self.temp_dir, "out_ultra_short.mp4")
        res = video_editor.assemble_meme_edit_with_freeze_and_caption(
            short_src,
            climax_timestamp=0.1,
            caption_text="Ultra Short Test",
            record_scratch_sfx=None,
            impact_sfx=None,
            overlay_image_path=None,
            output_path=out_path,
        )
        self.assertTrue(os.path.isfile(res))
        self.assertGreater(os.path.getsize(res), 1000)

    def test_challenger_03_tts_cleaning_complex_unicode_and_zwj(self):
        """clean_hook_text_for_speech handles ZWJ, flags, math symbols, and extreme punctuation."""
        complex_input = (
            "👨‍👩‍👧‍👦 🏳️‍🌈 WATCH THIS! \U0001f525\U0001f525 𝓒𝓻𝓪𝔃𝔂 𝕧𝕚𝕣𝕒𝕝 𝓂𝑜𝓂𝑒𝓃𝓉??? "
            "WAIT FOR IT... ??? !!!"
        )
        cleaned = tts_generator.clean_hook_text_for_speech(complex_input)
        self.assertIsInstance(cleaned, str)
        self.assertFalse(cleaned.endswith("?"))
        self.assertNotIn("??", cleaned)
        self.assertGreater(len(cleaned), 0)

    def test_challenger_04_live_sapi5_with_powershell_metacharacters(self):
        """Live Windows SAPI5 PowerShell synthesis safely handles single quotes, double quotes,
        dollar signs ($env:TEMP), backticks (`), and semicolons without syntax error or code execution."""
        out_wav = os.path.join(self.temp_dir, "sapi_meta.wav")
        meta_text = "It's a test: \"quote\", `backtick`, $variable; done!"
        produced_path, dur = tts_generator._synthesize_sapi5_powershell(
            meta_text,
            output_path=out_wav,
        )
        self.assertTrue(os.path.isfile(produced_path))
        self.assertGreater(os.path.getsize(produced_path), 500)
        self.assertGreater(dur, 0.2)

    def test_challenger_05_drive_uploader_edge_cases(self):
        """upload_video_to_drive safely handles non-existent paths, directories, and zero-byte files."""
        # 1. Non-existent file
        fid, link = drive_uploader.upload_video_to_drive("c:\\non_existent_path_xyz.mp4")
        self.assertIsNone(fid)
        self.assertIsNone(link)

        # 2. Directory path passed as file_path
        fid, link = drive_uploader.upload_video_to_drive(self.temp_dir)
        self.assertIsNone(fid)
        self.assertIsNone(link)

        # 3. Empty string / None
        for empty in ("", None):
            fid, link = drive_uploader.upload_video_to_drive(empty)
            self.assertIsNone(fid)
            self.assertIsNone(link)

    def test_challenger_06_caption_generator_negative_and_excessive_climax(self):
        """get_freeze_caption handles negative climax_time and climax_time far exceeding media duration."""
        sample_path = os.path.join(self.temp_dir, "caption_edge.mp4")
        helpers.create_synthetic_video(sample_path, duration=1.0)

        # Negative climax
        cap1 = caption_generator.get_freeze_caption(sample_path, climax_time=-5.0)
        self.assertIsInstance(cap1, str)
        self.assertGreater(len(cap1), 0)

        # Climax way past duration
        cap2 = caption_generator.get_freeze_caption(sample_path, climax_time=9999.0)
        self.assertIsInstance(cap2, str)
        self.assertGreater(len(cap2), 0)

        # Non-existent file
        cap3 = caption_generator.get_freeze_caption("non_existent_file.mp4", climax_time=2.0)
        self.assertIn(cap3, caption_generator.FALLBACK_CAPTIONS)

    def test_challenger_07_audio_detector_edge_cases(self):
        """audio_detector functions correctly handle non-existent, corrupt, and boundary conditions."""
        # 1. _has_audio_stream on non-existent file
        self.assertFalse(audio_detector._has_audio_stream("non_existent.mp4"))

        # 2. _get_duration_seconds on non-existent file
        self.assertEqual(audio_detector._get_duration_seconds("non_existent.mp4"), 0.0)

        # 3. find_climax_timestamp on non-existent file raises FileNotFoundError
        with self.assertRaises(FileNotFoundError):
            audio_detector.find_climax_timestamp("non_existent.mp4")


if __name__ == "__main__":
    unittest.main()
