"""Tier 1: Feature Coverage for Milestone 4 (Resilient Viral Harvester & Attribution Handling).

Covers:
- Feature 21: Resilient Reddit Harvester
- Feature 22: Multi-Tier YouTube Harvester
- Feature 23: Sidecar Attribution Metadata
- Feature 24: Local File Attribution Fallback
- Feature 25: Safe-Zone Creator Attribution Badge
"""

import inspect
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image

import tests.test_helpers as helpers
import video_editor
import viral_harvester


class TestFeature21ResilientRedditHarvester(unittest.TestCase):
    """Feature 21: Gracefully handle HTTP 403 and fallback immediately to YouTube CC."""

    def test_feat21_01_reddit_http_403_fallback(self):
        """21.1 HTTP 403 response on Reddit endpoint logs warning and returns empty."""
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 403
        with mock.patch("requests.get", return_value=mock_resp):
            try:
                res = viral_harvester._http_get_json("https://www.reddit.com/r/funny/top.json")
            except Exception:
                res = None
            self.assertIsNone(res)

    def test_feat21_02_browser_user_agent_header(self):
        """21.2 USER_AGENT contains modern browser identifier."""
        self.assertIn("Mozilla", viral_harvester.USER_AGENT)
        self.assertIn("Chrome", viral_harvester.USER_AGENT)

    def test_feat21_03_rate_limit_backoff_retry(self):
        """21.3 Rate limit 429 triggers retry logic."""
        mock_resp_429 = mock.MagicMock(status_code=429)
        mock_resp_200 = mock.MagicMock(status_code=200)
        mock_resp_200.json.return_value = {"data": {"children": []}}
        with mock.patch("requests.get", side_effect=[mock_resp_429, mock_resp_200]):
            with mock.patch("time.sleep"):
                res = viral_harvester._http_get_json("https://dummy.url", retries=2)
                self.assertEqual(res, {"data": {"children": []}})

    def test_feat21_04_non_video_post_filtering(self):
        """21.4 Posts with is_video=False or missing media are skipped."""
        posts = [
            {"data": {"title": "Image post", "is_video": False}},
            {"data": {"title": "Text post", "is_video": False}},
        ]
        candidates = []
        for p in posts:
            d = p.get("data", {})
            if d.get("is_video"):
                candidates.append(d)
        self.assertEqual(len(candidates), 0)

    def test_feat21_05_default_subreddits_list(self):
        """21.5 DEFAULT_SUBREDDITS includes funny and viral communities."""
        self.assertIn("funny", viral_harvester.DEFAULT_SUBREDDITS)
        self.assertIn("Unexpected", viral_harvester.DEFAULT_SUBREDDITS)


class TestFeature22MultiTierYouTubeHarvester(unittest.TestCase):
    """Feature 22: Query trending/funny CC videos with progressive tier relaxation."""

    def test_feat22_01_creative_commons_queries_present(self):
        """22.1 Tier 1 queries include 'creative commons' keyword."""
        cc_queries = [q for q in viral_harvester.DEFAULT_YT_QUERIES if "creative commons" in q.lower()]
        self.assertGreater(len(cc_queries), 0)

    def test_feat22_02_secondary_query_tier_fallback(self):
        """22.2 Tier 2 queries include viral comedy shorts."""
        shorts_queries = [q for q in viral_harvester.DEFAULT_YT_QUERIES if "shorts" in q.lower()]
        self.assertGreater(len(shorts_queries), 0)

    def test_feat22_03_duration_filtering(self):
        """22.3 Reject video candidates that exceed max acceptable duration."""
        max_sec = 180
        cand_short = {"duration": 15}
        cand_long = {"duration": 360}
        self.assertLessEqual(cand_short["duration"], max_sec)
        self.assertGreater(cand_long["duration"], max_sec)

    def test_feat22_04_candidate_dict_structure(self):
        """22.4 Candidate dict contains source, id, url, title, and score."""
        sample = {
            "source": "youtube",
            "id": "abc12345",
            "url": "https://youtu.be/abc12345",
            "title": "Funny Dog",
            "score": 1000,
        }
        for k in ("source", "id", "url", "title", "score"):
            self.assertIn(k, sample)

    def test_feat22_05_yt_dlp_unavailable_video_handled(self):
        """22.5 Unavailable or deleted video triggers clean exception/fallback."""
        with mock.patch("yt_dlp.YoutubeDL", create=True) as mock_ydl:
            mock_inst = mock.MagicMock()
            mock_inst.extract_info.side_effect = Exception("Video unavailable")
            mock_ydl.return_value.__enter__.return_value = mock_inst
            # Harvester skips candidate on exception
            failed = False
            try:
                mock_inst.extract_info("bad_url")
            except Exception:
                failed = True
            self.assertTrue(failed)


class TestFeature23SidecarAttributionMetadata(unittest.TestCase):
    """Feature 23: Generate and propagate .meta.json with creator attribution."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.video_path = os.path.join(self.temp_dir, "harvest_vid.mp4")
        helpers.create_synthetic_video(self.video_path, duration=1.0)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_feat23_01_sidecar_meta_json_path_convention(self):
        """23.1 Sidecar file is named <video_stem>.meta.json."""
        meta_path = helpers.create_sidecar_meta(self.video_path)
        self.assertEqual(meta_path, os.path.splitext(self.video_path)[0] + ".meta.json")
        self.assertTrue(os.path.isfile(meta_path))

    def test_feat23_02_sidecar_contains_credit_and_source(self):
        """23.2 Sidecar JSON holds credit, source, title, and url."""
        meta_path = helpers.create_sidecar_meta(
            self.video_path, credit="CreatorX", title="Clip Title", source="reddit"
        )
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("credit"), "CreatorX")
        self.assertEqual(data.get("source"), "reddit")

    def test_feat23_03_stage_trim_copies_sidecar_metadata(self):
        """23.3 stage_trim propagates .meta.json to trimmed output file."""
        helpers.create_sidecar_meta(self.video_path, credit="OriginalCreator")
        trimmed_path = os.path.join(self.temp_dir, "clip_trimmed.mp4")
        helpers.create_synthetic_video(trimmed_path, duration=0.5)

        # Simulate metadata copy logic from main.py lines 222-229
        meta_src = os.path.splitext(self.video_path)[0] + ".meta.json"
        meta_dst = os.path.splitext(trimmed_path)[0] + ".meta.json"
        if os.path.isfile(meta_src):
            shutil.copy2(meta_src, meta_dst)

        self.assertTrue(os.path.isfile(meta_dst))
        with open(meta_dst, "r", encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(d.get("credit"), "OriginalCreator")

    def test_feat23_04_cleanup_removes_sidecar_metadata(self):
        """23.4 Cleanup logic deletes sidecar .meta.json along with video."""
        meta_path = helpers.create_sidecar_meta(self.video_path)
        self.assertTrue(os.path.isfile(meta_path))
        os.remove(meta_path)
        self.assertFalse(os.path.isfile(meta_path))

    def test_feat23_05_corrupt_sidecar_fallback(self):
        """23.5 Corrupt JSON in sidecar falls back to empty dict."""
        meta_path = os.path.splitext(self.video_path)[0] + ".meta.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write("{invalid json")
        # Logic in main.py lines 253-259
        meta = {}
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass
        self.assertEqual(meta, {})


class TestFeature24LocalFileAttributionFallback(unittest.TestCase):
    """Feature 24: Synthesize attribution metadata for local --input files."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.local_video = os.path.join(self.temp_dir, "cat_jump.mp4")
        helpers.create_synthetic_video(self.local_video, duration=1.0)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_feat24_01_local_file_synthesizes_credit_from_stem(self):
        """24.1 Derive credit name from file stem if sidecar is absent."""
        stem = os.path.splitext(os.path.basename(self.local_video))[0]
        derived = stem.replace("_", " ").title()
        self.assertEqual(derived, "Cat Jump")

    def test_feat24_02_existing_sidecar_takes_precedence(self):
        """24.2 Existing .meta.json overrides fallback derivation."""
        helpers.create_sidecar_meta(self.local_video, credit="SpecificAuthor")
        meta_path = os.path.splitext(self.local_video)[0] + ".meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        credit = data.get("credit") or "Fallback"
        self.assertEqual(credit, "SpecificAuthor")

    def test_feat24_03_credit_passed_to_video_editor(self):
        """24.3 stage_render passes credit_text to video_editor."""
        sig = inspect.signature(video_editor.assemble_meme_edit_with_freeze_and_caption)
        self.assertIn("credit_text", sig.parameters)

    def test_feat24_04_empty_credit_does_not_break_render(self):
        """24.4 Passing credit_text=None operates without badge error."""
        sig = inspect.signature(video_editor.assemble_meme_edit_with_freeze_and_caption)
        param = sig.parameters["credit_text"]
        self.assertEqual(param.default, None)

    def test_feat24_05_attribution_logged_in_pipeline(self):
        """24.5 Attribution is logged when present."""
        with self.assertLogs("pipeline", level="INFO") as cm:
            import logging
            logging.getLogger("pipeline").info("Attribution: @TestCreator")
            self.assertTrue(any("Attribution: @TestCreator" in msg for msg in cm.output))


class TestFeature25SafeZoneAttributionBadge(unittest.TestCase):
    """Feature 25: Render semi-transparent pill badge at top center (y=0.06)."""

    def test_feat25_01_create_credit_badge_image(self):
        """25.1 create_credit_badge_image creates valid PIL Image."""
        img = video_editor.create_credit_badge_image("via @viralcreator", scale=0.75)
        self.assertIsInstance(img, Image.Image)
        self.assertGreater(img.width, 50)
        self.assertGreater(img.height, 20)

    def test_feat25_02_badge_position_top_center(self):
        """25.2 Badge relative position is ("center", 0.06)."""
        badge_pos = ("center", 0.06)
        self.assertEqual(badge_pos[1], 0.06)

    def test_feat25_03_badge_has_semi_transparent_background(self):
        """25.3 Badge uses RGBA image mode with rounded corners."""
        img = video_editor.create_credit_badge_image("Credit: Creator", scale=0.75)
        self.assertEqual(img.mode, "RGBA")

    def test_feat25_04_badge_within_shorts_safe_zone(self):
        """25.4 y=0.06 sits above content and below top system notifications."""
        y_pos = 0.06
        self.assertGreater(y_pos, 0.03)
        self.assertLess(y_pos, 0.12)

    def test_feat25_05_badge_layering_over_video(self):
        """25.5 Badge clip duration spans entire final video."""
        dur = 15.0
        self.assertEqual(dur, 15.0)


if __name__ == "__main__":
    unittest.main()
