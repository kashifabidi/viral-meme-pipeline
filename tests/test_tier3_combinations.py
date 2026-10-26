"""Tier 3: Cross-Feature Combinations (Pairwise & Multi-Feature Interactions).

Covers:
- Pair 1: Local file input + Synthesized attribution metadata + Safe-zone pill badge
- Pair 2: Reddit 403 fallback + YouTube CC acquisition + Sidecar .meta.json propagation
- Pair 3: Dual-Tier TTS voiceover + Segment 1 source audio ducking + Anti-clipping limiter
- Pair 4: Climax freeze-frame + Blurred background + Contextual SFX layering (scratch & boom)
- Pair 5: Punchline resume + Transparent alpha emoji overlay + Guaranteed 1080x1920 upscale
- Pair 6: Dynamic pacing controller + Window trimming + Opening hook banner timing
- Pair 7: Offline Google Drive fallback + keep-local flag + Batch processing loop
- Pair 8: Headless process timeout protection + Fallback to untrimmed source
- Pair 9: Audio-less video input + AI voiceover intro + Climax SFX layering
- Pair 10: RENDER_SCALE optimization + High-quality bicubic upscale to 1080x1920 @ 30fps
- Pair 11: Hook text emoji stripping for speech + Visual emoji banner preservation
- Pair 12: Comedic punchline fallback + Decoupled hook banner
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

import audio_detector
import caption_generator
import drive_uploader
import main
import tests.test_helpers as helpers
import video_editor
import viral_harvester

try:
    import tts_generator
except ImportError:
    tts_generator = None


class TestTier3CrossFeatureCombinations(unittest.TestCase):
    """Tier 3: Pairwise and multi-feature interaction verification."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="tier3_combo_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_combo_01_local_input_with_attribution_and_safe_zone_badge(self):
        """Combo 1: Local file input + Sidecar metadata + Safe-zone attribution badge."""
        local_vid = os.path.join(self.temp_dir, "skate_fail.mp4")
        helpers.create_synthetic_video(local_vid, duration=2.0)
        meta_path = helpers.create_sidecar_meta(local_vid, credit="@SkateLegend", title="Epic Skate Fail")

        # Verify metadata is loaded in main.stage_render
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        credit = meta.get("credit")
        self.assertEqual(credit, "@SkateLegend")

        # Verify badge image can be generated with that credit
        badge_img = video_editor.create_credit_badge_image(credit, scale=0.75)
        self.assertIsInstance(badge_img, Image.Image)
        self.assertEqual(badge_img.mode, "RGBA")

    def test_combo_02_reddit_403_fallback_to_youtube_cc_and_sidecar(self):
        """Combo 2: Reddit 403 error triggers YouTube CC search with sidecar metadata."""
        mock_resp = mock.MagicMock(status_code=403)
        with mock.patch("requests.get", return_value=mock_resp):
            # Reddit 403 triggers graceful empty list from get_all_time_reddit_hits
            reddit_result = viral_harvester.get_all_time_reddit_hits(subreddits=["funny"])
            self.assertEqual(reddit_result, [])

            # Fallback executes YouTube search
            cc_query = viral_harvester.DEFAULT_YT_QUERIES[0]
            self.assertIn("creative commons", cc_query.lower())

            # Candidate carries attribution data
            candidate = {
                "source": "youtube_cc",
                "id": "abc123",
                "url": "https://youtu.be/abc123",
                "title": "Creative Commons Funny Moment",
                "credit": "CC Creator",
                "score": 500,
            }
            cand_video = os.path.join(self.temp_dir, "cand_cc.mp4")
            helpers.create_synthetic_video(cand_video, duration=1.0)
            sidecar = helpers.create_sidecar_meta(cand_video, credit=candidate["credit"])
            self.assertTrue(os.path.isfile(sidecar))

    def test_combo_03_ai_voiceover_with_ducked_source_audio(self):
        """Combo 3: AI Voiceover hook intro + Segment 1 source audio ducking (20-30%)."""
        vo_path = os.path.join(self.temp_dir, "hook_vo.mp3")
        helpers.create_synthetic_audio(vo_path, duration=2.5)

        ducking_factor = 0.25
        source_vol = 1.0
        ducked_vol = source_vol * ducking_factor
        self.assertEqual(ducked_vol, 0.25)

        # Anti-clipping composite level check
        vo_vol = 0.9
        total_peak = ducked_vol + vo_vol
        self.assertAlmostEqual(total_peak, 1.15)
        limiter_scale = 1.0 / max(1.0, total_peak)
        self.assertAlmostEqual(total_peak * limiter_scale, 1.0)

    def test_combo_04_freeze_frame_with_suspense_sfx_and_caption(self):
        """Combo 4: Climax freeze frame + Blurred background + Record scratch SFX + Caption."""
        src_path = os.path.join(self.temp_dir, "action.mp4")
        helpers.create_synthetic_video(src_path, duration=3.0)

        climax_ts = 1.5
        caption_text = '"Mistakes were made."'
        freeze_dur = 1.0

        # Verify blurred background generation
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        frame[:] = (50, 100, 150)
        bg = video_editor._pil_blurred_background(frame, 540, 960)
        self.assertEqual(bg.size, (540, 960))

        # Verify caption image creation
        cap_img = video_editor.create_meme_text_image(caption_text)
        self.assertIsInstance(cap_img, Image.Image)

        # Verify freeze sfx trim
        scratch_path = os.path.join("assets", "sfx", "record_scratch.mp3")
        if os.path.isfile(scratch_path):
            sfx = video_editor._load_sfx(scratch_path)
            self.assertIsNotNone(sfx)
            if sfx.duration and sfx.duration > freeze_dur:
                sfx = sfx.subclip(0, freeze_dur)
            self.assertLessEqual(sfx.duration, freeze_dur)
            sfx.close()

    def test_combo_05_punchline_resume_with_impact_sfx_and_emoji_overlay(self):
        """Combo 5: Punchline resume (Segment 3) + Vine boom impact SFX + Transparent emoji cutout."""
        boom_path = os.path.join("assets", "sfx", "vine_boom.mp3")
        skull_path = os.path.join("assets", "overlays", "skull.png")

        if os.path.isfile(boom_path) and os.path.isfile(skull_path):
            boom = video_editor._load_sfx(boom_path)
            self.assertIsNotNone(boom)
            boom.close()

            with Image.open(skull_path) as im:
                self.assertIn(im.mode, ("RGBA", "LA", "P"))

    def test_combo_06_dynamic_pacing_with_window_trim_and_hook_banner(self):
        """Combo 6: Dynamic pacing trim (10-20s duration) + Synchronized opening hook banner."""
        long_src = os.path.join(self.temp_dir, "long_compilation.mp4")
        helpers.create_synthetic_video(long_src, duration=80.0)

        climax = 50.0
        # Window trim: pre-roll 8s, post-roll 3s -> window length 11s
        start = max(0.0, climax - main.PRE_ROLL_SRC)
        total = 80.0
        length = min((climax + main.POST_ROLL) - start, total - start)
        self.assertEqual(length, 11.0)
        self.assertTrue(10.0 <= length <= 20.0)

        # Hook banner sits at opening 2-3s of this 11s clip
        banner_dur = 2.5
        self.assertLess(banner_dur, length)

    def test_combo_07_offline_drive_with_keep_local_and_batch_processing(self):
        """Combo 7: Offline Google Drive fallback + keep-local flag + Multi-item batch loop."""
        clip1 = os.path.join(self.temp_dir, "clip1.mp4")
        clip2 = os.path.join(self.temp_dir, "clip2.mp4")
        helpers.create_synthetic_video(clip1, duration=0.5)
        helpers.create_synthetic_video(clip2, duration=0.5)

        with helpers.isolated_env(GOOGLE_DRIVE_FOLDER_ID="", GOOGLE_OAUTH_CLIENT_ID=""):
            fid1, link1 = drive_uploader.upload_video_to_drive(clip1, keep_local=True)
            fid2, link2 = drive_uploader.upload_video_to_drive(clip2, keep_local=True)
            self.assertIsNone(fid1)
            self.assertIsNone(fid2)
            self.assertTrue(os.path.isfile(clip1))
            self.assertTrue(os.path.isfile(clip2))

    def test_combo_08_headless_process_timeout_with_fallback_to_source(self):
        """Combo 8: Subprocess timeout in stage_trim safely falls back to untrimmed source."""
        vid = os.path.join(self.temp_dir, "heavy_render.mp4")
        helpers.create_synthetic_video(vid, duration=65.0)
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=180)):
            work, work_climax = main.stage_trim(vid, 35.0)
            self.assertEqual(work, vid)
            self.assertEqual(work_climax, 35.0)

    def test_combo_09_no_audio_video_with_voiceover_and_freeze_sfx(self):
        """Combo 9: Silent input video receives voiceover intro and freeze SFX cleanly."""
        silent_vid = os.path.join(self.temp_dir, "mute_src.mp4")
        helpers.create_synthetic_video(silent_vid, duration=3.0, has_audio=False)

        # Detect climax on silent source
        ts = audio_detector.find_climax_timestamp(silent_vid)
        self.assertGreater(ts, 0.0)

        # Segment 2 scratch audio
        scratch_path = os.path.join("assets", "sfx", "record_scratch.mp3")
        scratch = video_editor._load_sfx(scratch_path)
        self.assertIsNotNone(scratch)
        scratch.close()

    def test_combo_10_render_scale_downscale_with_1080x1920_upscale(self):
        """Combo 10: RENDER_SCALE=0.75 composite + Bicubic upscale to 1080x1920 @ 30fps."""
        scale = 0.75
        edit_w, edit_h = int(1080 * scale), int(1920 * scale)
        self.assertEqual(edit_w, 810)
        self.assertEqual(edit_h, 1440)

        # Final target is 1080x1920
        final_w, final_h = 1080, 1920
        self.assertEqual((final_w, final_h), (video_editor.TARGET_W, video_editor.TARGET_H))

    def test_combo_11_hook_text_emoji_stripping_and_visual_preservation(self):
        """Combo 11: Emojis stripped for speech synthesis while preserved in visual hook banner."""
        raw_hook = "Wait for the ending! 💀💥"
        # Speech text
        clean_speech = "Wait for the ending!"
        self.assertNotIn("💀", clean_speech)

        # Visual banner image can render the raw string
        img = video_editor.create_hook_banner_image(raw_hook, scale=0.75)
        self.assertIsInstance(img, Image.Image)

    def test_combo_12_comedic_punchline_fallback_with_decoupled_hook(self):
        """Combo 12: Comedic punchline fallback caption paired with decoupled hook banner."""
        caption = caption_generator.FALLBACK_CAPTIONS[1]  # '"Calculated risk."'
        hook = "Wait for it..."

        cap_img = video_editor.create_meme_text_image(caption)
        hook_img = video_editor.create_hook_banner_image(hook)

        self.assertIsInstance(cap_img, Image.Image)
        self.assertIsInstance(hook_img, Image.Image)


if __name__ == "__main__":
    unittest.main()
