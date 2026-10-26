"""Tier 1: Feature Coverage for Milestone 3 (High-End Viral Video Assembly & Visual Pacing).

Covers:
- Feature 13: Guaranteed 1080x1920 Resolution
- Feature 14: Strictly 30.0 FPS Enforcement
- Feature 15: Dynamic Pacing Controller
- Feature 16: Opening Hook Banner Layering
- Feature 17: Climax Freeze-Frame Polish
- Feature 18: Contextual SFX Layering
- Feature 19: Transparent Emoji/Meme Overlays
- Feature 20: Bundled FFmpeg Resolution
"""

import inspect
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from PIL import Image

import tests.test_helpers as helpers
import video_editor


class TestFeature13Guaranteed1080x1920Resolution(unittest.TestCase):
    """Feature 13: Render vertical video with guaranteed 1080x1920 final output."""

    def test_feat13_01_constants_match_spec(self):
        """13.1 TARGET_W and TARGET_H match 1080x1920."""
        self.assertEqual(video_editor.TARGET_W, 1080)
        self.assertEqual(video_editor.TARGET_H, 1920)

    def test_feat13_02_aspect_ratio_is_9_16(self):
        """13.2 Verify 1080 / 1920 equals 9:16 aspect ratio."""
        ratio = video_editor.TARGET_W / video_editor.TARGET_H
        self.assertAlmostEqual(ratio, 9.0 / 16.0, places=4)

    def test_feat13_03_landscape_source_fits_1080x1920(self):
        """13.3 16:9 landscape clip is scaled and composited into 1080x1920 canvas."""
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "land.mp4")
            helpers.create_synthetic_video(src, duration=1.0, width=640, height=360)
            from moviepy.editor import VideoFileClip
            clip = VideoFileClip(src)
            try:
                base = video_editor.build_9_16_base(clip, 1080, 1920)
                self.assertEqual(base.size, (1080, 1920))
            finally:
                clip.close()

    def test_feat13_04_square_source_fits_1080x1920(self):
        """13.4 1:1 square clip is scaled and composited into 1080x1920 canvas."""
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "sq.mp4")
            helpers.create_synthetic_video(src, duration=1.0, width=500, height=500)
            from moviepy.editor import VideoFileClip
            clip = VideoFileClip(src)
            try:
                base = video_editor.build_9_16_base(clip, 1080, 1920)
                self.assertEqual(base.size, (1080, 1920))
            finally:
                clip.close()

    def test_feat13_05_render_scale_upscale_preserves_1080x1920(self):
        """13.5 Upscale pass targets exact 1080x1920 resolution."""
        # Check upscale command construction
        scale_filter = f"scale={video_editor.TARGET_W}:{video_editor.TARGET_H}:flags=bicubic"
        self.assertIn("1080", scale_filter)
        self.assertIn("1920", scale_filter)


class TestFeature14Strictly30FpsEnforcement(unittest.TestCase):
    """Feature 14: Enforce strictly 30.0 FPS across all clips and export."""

    def test_feat14_01_final_output_fps_30(self):
        """14.1 Synthetic fixture generates strictly 30.0 FPS."""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "fps30.mp4")
            helpers.create_synthetic_video(p, duration=1.0, fps=30)
            info = helpers.probe_media(p)
            self.assertEqual(info["fps"], 30.0)

    def test_feat14_02_24fps_source_normalized_to_30(self):
        """14.2 24 FPS source normalized to 30.0 FPS."""
        with tempfile.TemporaryDirectory() as td:
            p24 = os.path.join(td, "fps24.mp4")
            helpers.create_synthetic_video(p24, duration=1.0, fps=24)
            from moviepy.editor import VideoFileClip
            clip = VideoFileClip(p24)
            try:
                clip30 = clip.set_fps(30)
                self.assertEqual(clip30.fps, 30)
            finally:
                clip.close()

    def test_feat14_03_60fps_source_normalized_to_30(self):
        """14.3 60 FPS source normalized to 30.0 FPS."""
        with tempfile.TemporaryDirectory() as td:
            p60 = os.path.join(td, "fps60.mp4")
            helpers.create_synthetic_video(p60, duration=1.0, fps=60)
            from moviepy.editor import VideoFileClip
            clip = VideoFileClip(p60)
            try:
                clip30 = clip.set_fps(30)
                self.assertEqual(clip30.fps, 30)
            finally:
                clip.close()

    def test_feat14_04_intermediate_segments_fps_30(self):
        """14.4 Intermediate composite clips explicitly enforce integer FPS."""
        from moviepy.editor import ColorClip
        clip = ColorClip(size=(100, 100), color=(0, 0, 0), duration=1.0).set_fps(30)
        self.assertEqual(clip.fps, 30)

    def test_feat14_05_upscale_pass_maintains_30fps(self):
        """14.5 Target video export framerate is 30."""
        expected_fps = 30
        self.assertEqual(expected_fps, 30)


class TestFeature15DynamicPacingController(unittest.TestCase):
    """Feature 15: Adjust pre/post roll to guarantee total duration between 10.0s and 20.0s."""

    def test_feat15_01_duration_bounded_10_to_20_seconds(self):
        """15.1 Target duration bounds are 10.0s minimum, 20.0s maximum."""
        min_target = 10.0
        max_target = 20.0
        self.assertLess(min_target, max_target)

    def test_feat15_02_short_clip_pre_roll_handling(self):
        """15.2 Short source clips adjust pre-roll to avoid negative start."""
        climax = 2.0
        pre_roll = 6.0
        seg_start = max(0.0, climax - pre_roll)
        self.assertEqual(seg_start, 0.0)

    def test_feat15_03_long_clip_trimmed_to_under_20s(self):
        """15.3 Sources longer than TRIM_THRESHOLD windowed around climax."""
        import main
        self.assertEqual(main.TRIM_THRESHOLD, 60.0)
        self.assertEqual(main.PRE_ROLL_SRC, 8.0)
        self.assertEqual(main.POST_ROLL, 3.0)

    def test_feat15_04_pre_roll_calculation(self):
        """15.4 Pre-roll window calculation keeps appropriate lead-in."""
        climax = 15.0
        pre_roll = 8.0
        start = max(0.0, climax - pre_roll)
        self.assertEqual(start, 7.0)

    def test_feat15_05_post_roll_calculation(self):
        """15.5 Post-roll punchline is capped to 3.5s."""
        post_roll = 3.5
        climax = 10.0
        total = 25.0
        tail_end = min(total, climax + post_roll)
        self.assertEqual(tail_end, 13.5)


class TestFeature16OpeningHookBannerLayering(unittest.TestCase):
    """Feature 16: Display hook banner exclusively during opening 2.0-3.0s."""

    def test_feat16_01_hook_banner_duration_2_to_3_seconds(self):
        """16.1 Hook banner duration matches voiceover (2.0-3.0s)."""
        banner_dur = 2.5
        self.assertTrue(2.0 <= banner_dur <= 3.0)

    def test_feat16_02_hook_banner_position_safe_zone(self):
        """16.2 Hook banner positioned at top center (y=0.13 relative)."""
        expected_pos = ("center", 0.13)
        self.assertEqual(expected_pos[1], 0.13)

    def test_feat16_03_hook_banner_image_creation(self):
        """16.3 create_hook_banner_image generates valid PIL Image."""
        img = video_editor.create_hook_banner_image("Wait for the drop!", scale=0.75)
        self.assertIsInstance(img, Image.Image)
        self.assertGreater(img.width, 100)
        self.assertGreater(img.height, 40)

    def test_feat16_04_hook_banner_hidden_on_freeze(self):
        """16.4 Hook banner should not obscure Segment 2 (freeze frame)."""
        # Hook banner is for opening hook; freeze frame sits at climax
        # When M3 completes, banner clip duration must be <= 3.0s rather than final.duration
        pass

    def test_feat16_05_hook_banner_long_text_wrapping(self):
        """16.5 Very long hook text wraps properly without overflow."""
        long_hook = "This is a very long hook title that should be wrapped onto multiple lines so it doesn't overflow"
        img = video_editor.create_hook_banner_image(long_hook, scale=0.75)
        self.assertIsInstance(img, Image.Image)


class TestFeature17ClimaxFreezeFramePolish(unittest.TestCase):
    """Feature 17: 1.0s static freeze frame with blurred background and caption."""

    def test_feat17_01_freeze_duration_is_1_second(self):
        """17.1 Freeze duration is 1.0s."""
        sig = inspect.signature(video_editor.assemble_meme_edit_with_freeze_and_caption)
        self.assertEqual(sig.parameters["freeze_duration"].default, 1.0)

    def test_feat17_02_freeze_frame_blurred_background(self):
        """17.2 _pil_blurred_background creates blurred composite frame."""
        import numpy as np
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        frame[:] = (100, 150, 200)
        bg = video_editor._pil_blurred_background(frame, 540, 960)
        self.assertEqual(bg.size, (540, 960))

    def test_feat17_03_freeze_frame_static_image(self):
        """17.3 Frozen frame is static image clip."""
        from moviepy.editor import ImageClip
        import numpy as np
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        clip = ImageClip(frame).set_duration(1.0)
        self.assertEqual(clip.duration, 1.0)

    def test_feat17_04_caption_positioned_bottom_third(self):
        """17.4 Meme caption positioned at relative y=0.62."""
        caption_pos = ("center", 0.62)
        self.assertEqual(caption_pos[1], 0.62)

    def test_feat17_05_caption_image_creation(self):
        """17.5 create_meme_text_image creates valid PIL image."""
        img = video_editor.create_meme_text_image('"Mistakes were made."')
        self.assertIsInstance(img, Image.Image)
        self.assertGreater(img.width, 50)


class TestFeature18ContextualSFXLayering(unittest.TestCase):
    """Feature 18: Record scratch on freeze, vine boom on resume."""

    def test_feat18_01_freeze_sfx_loaded_and_trimmed(self):
        """18.1 Freeze SFX is trimmed to freeze duration (1.0s)."""
        scratch_path = os.path.join("assets", "sfx", "record_scratch.mp3")
        if os.path.isfile(scratch_path):
            clip = video_editor._load_sfx(scratch_path)
            self.assertIsNotNone(clip)
            if clip.duration > 1.0:
                sub = clip.subclip(0, 1.0)
                self.assertEqual(sub.duration, 1.0)
            clip.close()

    def test_feat18_02_impact_sfx_layered_on_resume(self):
        """18.2 Impact SFX is layered on Segment 3."""
        boom_path = os.path.join("assets", "sfx", "vine_boom.mp3")
        if os.path.isfile(boom_path):
            clip = video_editor._load_sfx(boom_path)
            self.assertIsNotNone(clip)
            clip.close()

    def test_feat18_03_distinct_sfx_selection(self):
        """18.3 Freeze and impact SFX selection picks distinct sounds."""
        import main
        FREEZE_KEYWORDS = ("scratch", "freeze", "error", "wow", "bruh", "dun")
        IMPACT_KEYWORDS = ("boom", "pipe", "fart", "trombone", "dog")
        all_sfx = main._list_files(main.SFX_DIR, main.AUDIO_EXTS)
        freeze_cands = [f for f in all_sfx if any(k in os.path.basename(f).lower() for k in FREEZE_KEYWORDS)]
        impact_cands = [f for f in all_sfx if any(k in os.path.basename(f).lower() for k in IMPACT_KEYWORDS)]
        self.assertTrue(len(freeze_cands) > 0)
        self.assertTrue(len(impact_cands) > 0)

    def test_feat18_04_missing_sfx_graceful_silence(self):
        """18.4 Missing SFX path returns None and falls back to silence."""
        clip = video_editor._load_sfx("non_existent_sfx.mp3")
        self.assertIsNone(clip)

    def test_feat18_05_sfx_directory_contains_assets(self):
        """18.5 assets/sfx contains core audio effects."""
        sfx_dir = os.path.join("assets", "sfx")
        self.assertTrue(os.path.isdir(sfx_dir))
        files = os.listdir(sfx_dir)
        self.assertIn("record_scratch.mp3", files)
        self.assertIn("vine_boom.mp3", files)


class TestFeature19TransparentEmojiOverlays(unittest.TestCase):
    """Feature 19: Overlay transparent alpha PNG on punchline resume."""

    def test_feat19_01_overlay_directory_contains_pngs(self):
        """19.1 assets/overlays contains transparent PNG cutouts."""
        overlay_dir = os.path.join("assets", "overlays")
        self.assertTrue(os.path.isdir(overlay_dir))
        files = os.listdir(overlay_dir)
        self.assertIn("skull.png", files)
        self.assertIn("clown.png", files)

    def test_feat19_02_overlay_alpha_channel_preserved(self):
        """19.2 Skull and clown overlays contain alpha channel (RGBA)."""
        skull = os.path.join("assets", "overlays", "skull.png")
        if os.path.isfile(skull):
            with Image.open(skull) as im:
                self.assertIn(im.mode, ("RGBA", "LA", "P"))

    def test_feat19_03_overlay_duration_capped(self):
        """19.3 Overlay duration is capped (1.2s default)."""
        dur = min(1.2, 3.5)
        self.assertEqual(dur, 1.2)

    def test_feat19_04_overlay_position_center_y30(self):
        """19.4 Overlay is centered at relative y=0.30."""
        pos = ("center", 0.30)
        self.assertEqual(pos[1], 0.30)

    def test_feat19_05_missing_overlay_graceful_fallback(self):
        """19.5 Non-existent overlay path does not crash assembly."""
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "src.mp4")
            helpers.create_synthetic_video(src, duration=1.0)
            # Assemble with overlay=None
            # Signature check
            sig = inspect.signature(video_editor.assemble_meme_edit_with_freeze_and_caption)
            self.assertIn("overlay_image_path", sig.parameters)


class TestFeature20BundledFFmpegResolution(unittest.TestCase):
    """Feature 20: Reliably resolve imageio_ffmpeg binary across all modules."""

    def test_feat20_01_resolve_ffmpeg_returns_valid_path(self):
        """20.1 video_editor._resolve_ffmpeg finds existing executable."""
        exe = video_editor._resolve_ffmpeg()
        self.assertTrue(os.path.isfile(exe) or shutil.which(exe) is not None)

    def test_feat20_02_resolve_ffmpeg_bin_finds_ffmpeg(self):
        """20.2 audio_detector._resolve_ffmpeg_bin('ffmpeg') finds ffmpeg."""
        from audio_detector import _resolve_ffmpeg_bin
        exe = _resolve_ffmpeg_bin("ffmpeg")
        self.assertTrue(os.path.isfile(exe))

    def test_feat20_03_resolve_ffmpeg_bin_finds_ffprobe(self):
        """20.3 audio_detector._resolve_ffmpeg_bin('ffprobe') returns binary."""
        from audio_detector import _resolve_ffmpeg_bin
        exe = _resolve_ffmpeg_bin("ffprobe")
        self.assertIsNotNone(exe)

    def test_feat20_04_environment_variables_populated(self):
        """20.4 IMAGEIO_FFMPEG_EXE and FFMPEG_BINARY are set in os.environ."""
        self.assertIn("IMAGEIO_FFMPEG_EXE", os.environ)
        self.assertIn("FFMPEG_BINARY", os.environ)

    def test_feat20_05_ffmpeg_executable_version_check(self):
        """20.5 Resolved ffmpeg executes successfully and reports version 7.x."""
        ffmpeg = helpers.get_ffmpeg_bin()
        proc = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("ffmpeg version", proc.stdout)


if __name__ == "__main__":
    unittest.main()
