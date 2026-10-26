"""Milestone 1 Adversarial Empirical Stress Test Suite.

Authored by Challenger 2 (teamwork_preview_challenger_m1_2).
Empirically challenges:
1. Windows NTFS file lock prevention & immediate post-assembly deletion without WinError 32.
2. Immediate file deletion under abnormal exit / exception during assembly.
3. Rapid multi-iteration assembly & deletion cycle stress test (leak detection).
4. Google Drive uploader offline/uncredentialed local caching and absolute path logging.
5. Headless token parsing adversarial variants (unpadded b64, urlsafe, quotes, ya29 bearer).
6. Non-interactive headless execution (no browser popup, -nostdin, timeouts on subprocesses).
7. Working directory independence (.env path anchoring from external CWD).
8. Batch fault tolerance on unhandled item exceptions.
"""

import argparse
import base64
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from PIL import Image

import tests.test_helpers as helpers


class TestWindowsFileLockAdversarial(unittest.TestCase):
    """Empirical challenge on Windows file lock prevention."""

    def setUp(self):
        self.workspace = tempfile.mkdtemp(prefix="challenger_lock_")
        self.raw_video = os.path.join(self.workspace, "input_raw.mp4")
        self.scratch_sfx = os.path.join(self.workspace, "scratch.wav")
        self.boom_sfx = os.path.join(self.workspace, "boom.wav")
        self.overlay_img = os.path.join(self.workspace, "overlay.png")
        self.output_video = os.path.join(self.workspace, "out_meme.mp4")

        # Generate lightweight media
        helpers.create_synthetic_video(self.raw_video, duration=2.5, width=480, height=360, fps=25)
        helpers.create_synthetic_audio(self.scratch_sfx, duration=1.0)
        helpers.create_synthetic_audio(self.boom_sfx, duration=1.0)

        # Create a 100x100 RGBA PNG overlay
        im = Image.new("RGBA", (100, 100), (255, 0, 0, 180))
        im.save(self.overlay_img)

    def tearDown(self):
        # Best effort cleanup
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_challenge_01_immediate_deletion_post_assembly(self):
        """Verify deleting input, sfx, overlay, and output immediately after assemble raises NO WinError 32."""
        import video_editor

        res = video_editor.assemble_meme_edit_with_freeze_and_caption(
            raw_video_path=self.raw_video,
            climax_timestamp=1.2,
            caption_text="Challenger 2 Empirical Lock Test",
            record_scratch_sfx=self.scratch_sfx,
            impact_sfx=self.boom_sfx,
            overlay_image_path=self.overlay_img,
            output_path=self.output_video,
            freeze_duration=0.5,
            pre_roll=2.0,
            credit_text="@test_creator",
            hook_text="CHALLENGER 2 HOOK TEST",
        )
        self.assertTrue(os.path.isfile(res), "Output file should exist")

        # IMMEDIATELY delete all involved files without any sleep/delay
        # If any MoviePy or FFmpeg handle remains open on Windows, os.remove will fail with WinError 32
        files_to_delete = [
            ("raw_video", self.raw_video),
            ("scratch_sfx", self.scratch_sfx),
            ("boom_sfx", self.boom_sfx),
            ("overlay_img", self.overlay_img),
            ("output_video", self.output_video),
        ]

        deleted = []
        for name, path in files_to_delete:
            try:
                os.remove(path)
                deleted.append(name)
            except PermissionError as pe:
                self.fail(f"CRITICAL REGRESSION: WinError 32 raised deleting {name} ({path}): {pe}")
            except Exception as e:
                self.fail(f"Unexpected error deleting {name} ({path}): {e}")

        self.assertEqual(len(deleted), len(files_to_delete))

    def test_challenge_02_immediate_deletion_on_assembly_exception(self):
        """Verify that if assembly raises an exception, clips_to_close in finally releases handles immediately."""
        import video_editor

        # Intentionally cause an error during assembly by patching build_9_16_base to raise
        with mock.patch("video_editor.build_9_16_base", side_effect=RuntimeError("Simulated mid-assembly failure")):
            with self.assertRaises(RuntimeError):
                video_editor.assemble_meme_edit_with_freeze_and_caption(
                    raw_video_path=self.raw_video,
                    climax_timestamp=1.0,
                    caption_text="Fail Test",
                    record_scratch_sfx=self.scratch_sfx,
                    impact_sfx=self.boom_sfx,
                    overlay_image_path=self.overlay_img,
                    output_path=self.output_video,
                )

        # Ensure raw_video can be removed immediately despite the crash mid-assembly
        try:
            os.remove(self.raw_video)
        except PermissionError as pe:
            self.fail(f"CRITICAL: Failed to release file handle on assembly exception: {pe}")

    def test_challenge_03_repeated_assembly_handle_exhaustion_stress(self):
        """Stress-test 3 consecutive assembly & immediate delete cycles to detect cumulative file handle leaks."""
        import video_editor

        for iteration in range(3):
            sub_raw = os.path.join(self.workspace, f"sub_raw_{iteration}.mp4")
            sub_out = os.path.join(self.workspace, f"sub_out_{iteration}.mp4")
            helpers.create_synthetic_video(sub_raw, duration=1.5, width=320, height=240, fps=24)

            res = video_editor.assemble_meme_edit_with_freeze_and_caption(
                raw_video_path=sub_raw,
                climax_timestamp=0.8,
                caption_text=f"Iteration {iteration}",
                record_scratch_sfx=self.scratch_sfx,
                impact_sfx=self.boom_sfx,
                overlay_image_path=self.overlay_img,
                output_path=sub_out,
                freeze_duration=0.3,
            )
            self.assertTrue(os.path.isfile(res))

            # Immediate unlinks
            try:
                os.remove(sub_raw)
                os.remove(sub_out)
            except PermissionError as pe:
                self.fail(f"Iteration {iteration} handle leak: {pe}")


class TestDriveUploaderOfflineCachingAndLogging(unittest.TestCase):
    """Empirical challenge on Drive uploader fallback, caching, and absolute path logging."""

    def setUp(self):
        self.workspace = tempfile.mkdtemp(prefix="challenger_drive_")
        self.sample_mp4 = os.path.join(self.workspace, "sample_render.mp4")
        helpers.create_synthetic_video(self.sample_mp4, duration=1.0)
        self.abs_path = os.path.abspath(self.sample_mp4)

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_challenge_04_offline_fallback_and_absolute_path_logging(self):
        """Verify upload_video_to_drive safely caches locally and logs exact absolute path when uncredentialed."""
        import drive_uploader

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            with helpers.isolated_env(
                GOOGLE_OAUTH_TOKEN=None,
                GOOGLE_OAUTH_CLIENT_ID=None,
                GOOGLE_OAUTH_CLIENT_SECRET=None,
                GOOGLE_SERVICE_ACCOUNT_JSON=None,
                GOOGLE_OAUTH_TOKEN_FILE=os.path.join(self.workspace, "nonexistent.json"),
            ):
                fid, link = drive_uploader.upload_video_to_drive(self.sample_mp4)

        out_log = captured.getvalue()

        # 1. Returned tuple must be (None, None)
        self.assertIsNone(fid, "File ID must be None when unconfigured")
        self.assertIsNone(link, "Link must be None when unconfigured")

        # 2. Local file must still exist (preserved)
        self.assertTrue(os.path.isfile(self.sample_mp4), "Local file was deleted during offline fallback")

        # 3. Output log must contain exact absolute path
        self.assertIn(self.abs_path, out_log, f"Log missing absolute path {self.abs_path}. Output: {out_log}")
        self.assertIn("credentials not configured -> skipping upload; keeping local file", out_log)

    def test_challenge_05_inaccessible_folder_fallback_and_absolute_path_logging(self):
        """Verify upload_video_to_drive preserves local file and logs absolute path on DestinationError."""
        import drive_uploader

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            with mock.patch("drive_uploader.get_drive_service", return_value=(mock.MagicMock(), "oauth")):
                with mock.patch("drive_uploader.check_destination", side_effect=drive_uploader.DestinationError("Folder inaccessible 404")):
                    fid, link = drive_uploader.upload_video_to_drive(self.sample_mp4, folder_id="invalid_folder_id")

        out_log = captured.getvalue()
        self.assertIsNone(fid)
        self.assertIsNone(link)
        self.assertTrue(os.path.isfile(self.sample_mp4))
        self.assertIn(self.abs_path, out_log)
        self.assertIn("destination unusable: Folder inaccessible 404", out_log)

    def test_challenge_06_successful_upload_releases_handle_and_deletes(self):
        """Verify successful upload closes media stream handle via _close_media_handle and unlinks cleanly."""
        import drive_uploader

        mock_svc = mock.MagicMock()
        mock_svc.files().create().execute.return_value = {
            "id": "drive_file_id_999",
            "webViewLink": "https://drive.google.com/file/d/999/view",
        }

        with mock.patch("drive_uploader.get_drive_service", return_value=(mock_svc, "oauth")):
            with mock.patch("drive_uploader.check_destination", return_value={"name": "TestDir", "drive_id": "", "can_add_children": True, "folder_id": "root"}):
                # keep_local = False -> should safely delete file without WinError 32
                fid, link = drive_uploader.upload_video_to_drive(self.sample_mp4, keep_local=False)
                self.assertEqual(fid, "drive_file_id_999")
                self.assertEqual(link, "https://drive.google.com/file/d/999/view")
                self.assertFalse(os.path.exists(self.sample_mp4), "Local file should be deleted on success when keep_local=False")

    def test_challenge_07_oauth_token_parser_adversarial_matrix(self):
        """Adversarial stress on _parse_oauth_token_data across edge encodings."""
        import drive_uploader

        base_dict = {"token": "ya29.test_token", "refresh_token": "1//refresh_token"}
        base_json = json.dumps(base_dict)

        # 1. Plain JSON string
        p1 = drive_uploader.parse_oauth_token_payload(base_json)
        self.assertEqual(p1.get("token"), "ya29.test_token")

        # 2. Standard Base64
        b64_std = base64.b64encode(base_json.encode("utf-8")).decode("utf-8")
        p2 = drive_uploader.parse_oauth_token_payload(b64_std)
        self.assertEqual(p2.get("token"), "ya29.test_token")

        # 3. Unpadded Base64 (missing '=' padding)
        b64_unpadded = b64_std.rstrip("=")
        p3 = drive_uploader.parse_oauth_token_payload(b64_unpadded)
        self.assertEqual(p3.get("token"), "ya29.test_token")

        # 4. URL-safe Base64
        b64_url = base64.urlsafe_b64encode(base_json.encode("utf-8")).decode("utf-8")
        p4 = drive_uploader.parse_oauth_token_payload(b64_url)
        self.assertEqual(p4.get("token"), "ya29.test_token")

        # 5. Wrapped in double or single outer quotes
        quoted = f'"{b64_std}"'
        p5 = drive_uploader.parse_oauth_token_payload(quoted)
        self.assertEqual(p5.get("token"), "ya29.test_token")

        # 6. Raw Bearer Token (ya29...)
        p6 = drive_uploader.parse_oauth_token_payload("ya29.direct_access_bearer")
        self.assertEqual(p6.get("token"), "ya29.direct_access_bearer")

        # 7. Complete garbage / corrupt string
        p7 = drive_uploader.parse_oauth_token_payload("%%%CORRUPT_NOT_BASE64_OR_JSON%%%")
        self.assertIsNone(p7)

        # 8. Empty or None
        self.assertIsNone(drive_uploader.parse_oauth_token_payload(""))
        self.assertIsNone(drive_uploader.parse_oauth_token_payload(None))


class TestHeadlessExecutionAndSubprocessSafety(unittest.TestCase):
    """Empirical challenge on headless execution and subprocess non-blocking safety."""

    def test_challenge_08_headless_never_invokes_browser(self):
        """Verify get_drive_service never calls flow.run_local_server in headless environments."""
        import drive_uploader

        with mock.patch("google_auth_oauthlib.flow.InstalledAppFlow.run_local_server") as mock_server:
            with helpers.isolated_env(
                GOOGLE_OAUTH_TOKEN=None,
                GOOGLE_OAUTH_TOKEN_FILE="nonexistent_token.json",
            ):
                svc, mode = drive_uploader.get_drive_service()
                mock_server.assert_not_called()
                self.assertIsNone(svc)

    def test_challenge_09_upscale_subprocess_has_nostdin_and_timeout(self):
        """Verify video_editor upscale subprocess has -nostdin and timeout configured."""
        import inspect
        import video_editor

        src = inspect.getsource(video_editor.assemble_meme_edit_with_freeze_and_caption)
        self.assertIn("-nostdin", src, "Subprocess call must include -nostdin to prevent hanging in headless runs")
        self.assertIn("timeout=", src, "Subprocess call must include timeout parameter")

    def test_challenge_10_cwd_independent_env_loading(self):
        """Verify drive_uploader and main load .env when invoked from external working directory."""
        with tempfile.TemporaryDirectory() as td:
            old_cwd = os.getcwd()
            try:
                os.chdir(td)
                # Verify that drive_uploader ENV_PATH is anchored to its file directory
                import drive_uploader
                self.assertTrue(os.path.isabs(str(drive_uploader.ENV_PATH)))
                self.assertTrue(os.path.isfile(str(drive_uploader.ENV_PATH)))
            finally:
                os.chdir(old_cwd)


class TestBatchIterationAndFaultTolerance(unittest.TestCase):
    """Empirical challenge on batch iteration and exception resilience in main.py."""

    def setUp(self):
        self.workspace = tempfile.mkdtemp(prefix="challenger_batch_")
        self.vid1 = os.path.join(self.workspace, "vid1.mp4")
        self.vid2 = os.path.join(self.workspace, "vid2.mp4")
        helpers.create_synthetic_video(self.vid1, duration=1.0)
        helpers.create_synthetic_video(self.vid2, duration=1.0)

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_challenge_11_single_input_batch_returns_list(self):
        """Verify stage_acquire_batch returns list of length 1 for single input."""
        import main
        args = argparse.Namespace(input=self.vid1, harvest=False, max=1)
        res = main.stage_acquire_batch(args, 1)
        self.assertIsInstance(res, list)
        self.assertEqual(len(res), 1)
        self.assertEqual(os.path.abspath(res[0]), os.path.abspath(self.vid1))

    def test_challenge_12_batch_loop_continues_on_item_exception(self):
        """Verify that an unhandled Exception on item 1 does not abort batch processing for item 2."""
        import main

        args = argparse.Namespace(input=None, harvest=True, max=2, keep_local=True)
        # Mock stage_acquire_batch to return [vid1, vid2]
        with mock.patch("main.stage_acquire_batch", return_value=[self.vid1, self.vid2]):
            call_counts = {"stage_climax": 0}

            def mock_climax(path):
                call_counts["stage_climax"] += 1
                if path == self.vid1:
                    raise RuntimeError("Synthetic failure on item 1")
                return 0.5

            with mock.patch("main.stage_climax", side_effect=mock_climax):
                with mock.patch("main.stage_trim", return_value=(self.vid2, 0.5)):
                    with mock.patch("main.stage_caption", return_value="Test caption"):
                        with mock.patch("main.stage_render", return_value=self.vid2):
                            with mock.patch("main.stage_upload", return_value=("mock_id", "mock_link")):
                                # Run main with synthetic batch
                                ret = main.main(["--harvest", "--max", "2"])
                                # Even though item 1 failed, item 2 was processed, so main returns 0 (partial success)
                                self.assertEqual(call_counts["stage_climax"], 2, "Item 2 was not reached after item 1 exception")
                                self.assertEqual(ret, 0, "Partial success with 1 ok item should return exit code 0")


if __name__ == "__main__":
    unittest.main()
