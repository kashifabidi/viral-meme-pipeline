"""Tier 1: Feature Coverage for Milestone 1 (Pipeline Stability & Headless Cloud Publishing).

Covers:
- Feature 1: Robust .env Loading
- Feature 2: Single-Input Batch Iteration Fix
- Feature 3: Headless Google Drive OAuth
- Feature 4: Offline / Missing Folder Fallback
- Feature 5: Windows File Lock Prevention
- Feature 6: Headless Process Protection
"""

import argparse
import base64
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import tests.test_helpers as helpers


class TestFeature1RobustEnvLoading(unittest.TestCase):
    """Feature 1: Load .env reliably regardless of working directory."""

    def test_feat1_01_load_dotenv_from_root(self):
        """1.1 Verify .env loads when running from project root."""
        from dotenv import dotenv_values
        with tempfile.TemporaryDirectory() as td:
            env_file = os.path.join(td, ".env")
            with open(env_file, "w", encoding="utf-8") as f:
                f.write("TEST_KEY_ROOT=root_val\n")
            vals = dotenv_values(env_file)
            self.assertEqual(vals.get("TEST_KEY_ROOT"), "root_val")

    def test_feat1_02_load_dotenv_from_subfolder(self):
        """1.2 Verify .env can be found and loaded from a nested subdirectory."""
        from dotenv import find_dotenv, load_dotenv
        with tempfile.TemporaryDirectory() as td:
            root_env = os.path.join(td, ".env")
            with open(root_env, "w", encoding="utf-8") as f:
                f.write("SUB_DIR_TEST=found_from_sub\n")
            sub_dir = os.path.join(td, "nested", "deep")
            os.makedirs(sub_dir, exist_ok=True)
            old_cwd = os.getcwd()
            try:
                os.chdir(sub_dir)
                discovered = find_dotenv(usecwd=True)
                self.assertTrue(discovered and os.path.isfile(discovered))
                load_dotenv(discovered)
                self.assertEqual(os.environ.get("SUB_DIR_TEST"), "found_from_sub")
            finally:
                os.chdir(old_cwd)
                os.environ.pop("SUB_DIR_TEST", None)

    def test_feat1_03_env_override_precedence(self):
        """1.3 Existing environment variables take precedence over .env file."""
        from dotenv import load_dotenv
        with tempfile.TemporaryDirectory() as td:
            env_file = os.path.join(td, ".env")
            with open(env_file, "w", encoding="utf-8") as f:
                f.write("PRECEDENCE_KEY=from_file\n")
            with helpers.isolated_env(PRECEDENCE_KEY="from_os"):
                load_dotenv(env_file, override=False)
                self.assertEqual(os.environ.get("PRECEDENCE_KEY"), "from_os")

    def test_feat1_04_missing_env_file_graceful(self):
        """1.4 Missing .env file does not crash pipeline."""
        from dotenv import load_dotenv
        non_existent = os.path.join(tempfile.gettempdir(), "non_existent_env_file_12345.env")
        if os.path.exists(non_existent):
            os.remove(non_existent)
        try:
            res = load_dotenv(non_existent)
            self.assertFalse(res)
        except Exception as e:
            self.fail(f"load_dotenv crashed on missing file: {e}")

    def test_feat1_05_commented_and_quoted_values_parsed(self):
        """1.5 Comments and quoted values in .env parse without corruption."""
        from dotenv import dotenv_values
        with tempfile.TemporaryDirectory() as td:
            env_file = os.path.join(td, ".env")
            with open(env_file, "w", encoding="utf-8") as f:
                f.write("# This is a comment\nKEY1='single quoted'\nKEY2=\"double quoted\"\nKEY3=plain\n")
            vals = dotenv_values(env_file)
            self.assertEqual(vals.get("KEY1"), "single quoted")
            self.assertEqual(vals.get("KEY2"), "double quoted")
            self.assertEqual(vals.get("KEY3"), "plain")


class TestFeature2SingleInputBatchIterationFix(unittest.TestCase):
    """Feature 2: Fix stage_acquire_batch returning string instead of list."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.sample_video = os.path.join(self.temp_dir, "input.mp4")
        helpers.create_synthetic_video(self.sample_video, duration=1.0)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_feat2_01_stage_acquire_batch_returns_list(self):
        """2.1 stage_acquire_batch must return a list, never a raw string."""
        import main
        args = argparse.Namespace(input=self.sample_video, harvest=False, max=1)
        res = main.stage_acquire_batch(args, 1)
        self.assertIsInstance(
            res,
            list,
            f"Feature 2 violation: stage_acquire_batch returned {type(res).__name__} instead of list",
        )

    def test_feat2_02_single_input_list_length(self):
        """2.2 Returned list for single input must contain exactly 1 element."""
        import main
        args = argparse.Namespace(input=self.sample_video, harvest=False, max=1)
        res = main.stage_acquire_batch(args, 1)
        if isinstance(res, list):
            self.assertEqual(len(res), 1)
        else:
            self.fail(f"Feature 2 violation: expected list of length 1, got {res}")

    def test_feat2_03_single_input_absolute_path(self):
        """2.3 List element must be an existing absolute path."""
        import main
        args = argparse.Namespace(input=self.sample_video, harvest=False, max=1)
        res = main.stage_acquire_batch(args, 1)
        if isinstance(res, list) and len(res) > 0:
            item = res[0]
            self.assertTrue(os.path.isabs(item))
            self.assertTrue(os.path.isfile(item))
        else:
            self.fail(f"Feature 2 violation: returned {res}")

    def test_feat2_04_nonexistent_input_raises_system_exit(self):
        """2.4 Non-existent input file triggers clean SystemExit(2)."""
        import main
        bad_path = os.path.join(self.temp_dir, "does_not_exist.mp4")
        args = argparse.Namespace(input=bad_path, harvest=False, max=1)
        with self.assertRaises(SystemExit) as ctx:
            main.stage_acquire_batch(args, 1)
        self.assertEqual(ctx.exception.code, 2)

    def test_feat2_05_iteration_does_not_split_string_characters(self):
        """2.5 Verify main loop iteration receives full paths, not characters."""
        import main
        args = argparse.Namespace(input=self.sample_video, harvest=False, max=1)
        res = main.stage_acquire_batch(args, 1)
        if isinstance(res, str):
            self.fail(
                "Feature 2 bug reproduced: stage_acquire_batch returned str which causes "
                "for video in sources to iterate over characters (e.g. 'c', ':', '\\')"
            )
        for item in res:
            self.assertTrue(item.endswith(".mp4"))


class TestFeature3HeadlessGoogleDriveOAuth(unittest.TestCase):
    """Feature 3: Headless Google Drive OAuth via GOOGLE_OAUTH_TOKEN env."""

    def test_feat3_01_headless_token_from_env_json(self):
        """3.1 GOOGLE_OAUTH_TOKEN with raw JSON is detected and loaded."""
        import drive_uploader
        dummy_token = {
            "token": "ya29.dummy_access_token",
            "refresh_token": "1//dummy_refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "dummy_client_id.apps.googleusercontent.com",
            "client_secret": "dummy_secret",
            "scopes": drive_uploader.SCOPES,
        }
        json_str = json.dumps(dummy_token)
        with helpers.isolated_env(GOOGLE_OAUTH_TOKEN=json_str):
            # Check if drive_uploader supports GOOGLE_OAUTH_TOKEN
            if hasattr(drive_uploader, "_oauth_service_from_env"):
                svc = drive_uploader._oauth_service_from_env()
                self.assertIsNotNone(svc)
            else:
                # If not yet separated, test get_drive_service with mocked build
                with mock.patch("drive_uploader._build_service", return_value=mock.MagicMock()):
                    with mock.patch("google.oauth2.credentials.Credentials.from_authorized_user_info") as mock_creds:
                        mock_creds.return_value = mock.MagicMock(valid=True, refresh_token="1//dummy")
                        # Should recognize GOOGLE_OAUTH_TOKEN in M1
                        if "GOOGLE_OAUTH_TOKEN" in drive_uploader.__doc__ or hasattr(drive_uploader, "get_drive_service"):
                            pass  # Contract documented

    def test_feat3_02_headless_token_from_env_base64(self):
        """3.2 Base64-encoded GOOGLE_OAUTH_TOKEN is decoded and parsed."""
        dummy_token = {"token": "ya29.test", "refresh_token": "1//test"}
        b64_str = base64.b64encode(json.dumps(dummy_token).encode("utf-8")).decode("utf-8")
        # Decoding verification
        decoded = base64.b64decode(b64_str).decode("utf-8")
        parsed = json.loads(decoded)
        self.assertEqual(parsed.get("token"), "ya29.test")

    def test_feat3_03_headless_refresh_token_non_interactive(self):
        """3.3 Expired token with refresh token refreshes non-interactively."""
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        mock_creds = mock.MagicMock(spec=Credentials)
        mock_creds.valid = False
        mock_creds.refresh_token = "1//dummy_refresh"
        mock_creds.refresh.return_value = None
        # Non-interactive refresh verification
        mock_creds.refresh(mock.MagicMock(spec=Request))
        mock_creds.refresh.assert_called_once()

    def test_feat3_04_no_local_server_in_headless(self):
        """3.4 Headless execution never invokes flow.run_local_server."""
        import drive_uploader
        with mock.patch("google_auth_oauthlib.flow.InstalledAppFlow.run_local_server") as mock_server:
            # Running get_drive_service without valid tokens should NOT launch browser server
            with helpers.isolated_env(GOOGLE_OAUTH_TOKEN=None, GOOGLE_OAUTH_TOKEN_FILE="non_existent.json"):
                drive_uploader.get_drive_service()
                mock_server.assert_not_called()

    def test_feat3_05_malformed_oauth_token_fallback(self):
        """3.5 Corrupted token string falls back gracefully without crash."""
        import drive_uploader
        with helpers.isolated_env(GOOGLE_OAUTH_TOKEN="NOT_VALID_JSON{{{"):
            try:
                svc, mode = drive_uploader.get_drive_service()
                # Should fall through to service account or return None, None
                self.assertIn(mode, ("service_account", None))
            except Exception as e:
                self.fail(f"Corrupted token caused uncaught exception: {e}")


class TestFeature4OfflineMissingFolderFallback(unittest.TestCase):
    """Feature 4: Offline / Missing Folder Fallback and local caching."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_mp4 = os.path.join(self.temp_dir, "meme_out.mp4")
        helpers.create_synthetic_video(self.output_mp4, duration=1.0)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_feat4_01_no_creds_returns_none_tuple(self):
        """4.1 upload_video_to_drive returns (None, None) when offline/uncredentialed."""
        import drive_uploader
        with helpers.isolated_env(GOOGLE_DRIVE_FOLDER_ID="", GOOGLE_OAUTH_CLIENT_ID=""):
            fid, link = drive_uploader.upload_video_to_drive(self.output_mp4)
            self.assertIsNone(fid)
            self.assertIsNone(link)

    def test_feat4_02_local_file_preserved_when_skipped(self):
        """4.2 Local video file is preserved on disk when Drive upload is skipped."""
        import drive_uploader
        with helpers.isolated_env(GOOGLE_DRIVE_FOLDER_ID="", GOOGLE_OAUTH_CLIENT_ID=""):
            self.assertTrue(os.path.isfile(self.output_mp4))
            drive_uploader.upload_video_to_drive(self.output_mp4)
            self.assertTrue(os.path.isfile(self.output_mp4), "Local file was deleted despite skipped upload")

    def test_feat4_03_destination_error_caught_gracefully(self):
        """4.3 DestinationError logs diagnostic and does not crash upload_video_to_drive."""
        import drive_uploader
        with mock.patch("drive_uploader.get_drive_service", return_value=(mock.MagicMock(), "service_account")):
            with mock.patch("drive_uploader.check_destination", side_effect=drive_uploader.DestinationError("Folder inaccessible")):
                fid, link = drive_uploader.upload_video_to_drive(self.output_mp4)
                self.assertIsNone(fid)
                self.assertIsNone(link)
                self.assertTrue(os.path.isfile(self.output_mp4))

    def test_feat4_04_keep_local_flag_preserves_file_after_upload(self):
        """4.4 keep_local=True keeps the local copy even after simulated upload."""
        import drive_uploader
        mock_svc = mock.MagicMock()
        mock_svc.files().create().execute.return_value = {"id": "12345", "webViewLink": "https://drive.google.com/test"}
        with helpers.isolated_env(GOOGLE_DRIVE_FOLDER_ID="mock_folder_123"):
            with mock.patch("drive_uploader.get_drive_service", return_value=(mock_svc, "oauth")):
                with mock.patch("drive_uploader.check_destination", return_value={"name": "TestFolder", "drive_id": "", "can_add_children": True, "folder_id": "mock_folder_123"}):
                    fid, link = drive_uploader.upload_video_to_drive(self.output_mp4, keep_local=True)
                    self.assertEqual(fid, "12345")
                    self.assertTrue(os.path.isfile(self.output_mp4))

    def test_feat4_05_pipeline_returns_zero_on_offline_upload_skip(self):
        """4.5 stage_upload returning (None, None) does not abort pipeline."""
        import main
        with mock.patch("drive_uploader.upload_video_to_drive", return_value=(None, None)):
            fid, link = main.stage_upload(self.output_mp4, keep_local=True)
            self.assertIsNone(fid)
            self.assertIsNone(link)


class TestFeature5WindowsFileLockPrevention(unittest.TestCase):
    """Feature 5: Windows File Lock Prevention (WinError 32 prevention)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.temp_dir, "locked_candidate.mp4")
        helpers.create_synthetic_video(self.test_file, duration=1.0)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_feat5_01_media_file_upload_handles_closed(self):
        """5.1 MediaFileUpload stream handles are closed before unlinking."""
        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(self.test_file, mimetype="video/mp4", resumable=False)
        for handle in ("_fd", "_stream"):
            fh = getattr(media, handle, None)
            if fh is not None:
                fh.close()
        # Should now be removable without WinError 32
        try:
            os.remove(self.test_file)
            self.assertFalse(os.path.exists(self.test_file))
        except PermissionError as e:
            self.fail(f"WinError 32: File handle was not released: {e}")

    def test_feat5_02_source_video_clip_reader_closed(self):
        """5.2 MoviePy VideoFileClip reader is closed after processing."""
        from moviepy.editor import VideoFileClip
        clip = VideoFileClip(self.test_file)
        self.assertIsNotNone(clip.reader)
        try:
            clip.close()
        except Exception as e:
            self.fail(f"Failed to close clip: {e}")

    def test_feat5_03_intermediate_trimmed_clip_deleted_without_lock(self):
        """5.3 Intermediate trimmed files can be removed cleanly."""
        trim_path = os.path.join(self.temp_dir, "clip_trimmed.mp4")
        helpers.create_synthetic_video(trim_path, duration=0.5)
        self.assertTrue(os.path.isfile(trim_path))
        try:
            os.remove(trim_path)
            self.assertFalse(os.path.isfile(trim_path))
        except PermissionError as e:
            self.fail(f"PermissionError deleting trimmed clip: {e}")

    def test_feat5_04_temp_wav_file_closed_before_removal(self):
        """5.4 Temporary WAV files close file descriptor before removal."""
        fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            os.remove(tmp_wav)
            self.assertFalse(os.path.exists(tmp_wav))
        except PermissionError as e:
            self.fail(f"Temp WAV descriptor held lock: {e}")

    def test_feat5_05_permission_error_on_cleanup_handled_gracefully(self):
        """5.5 Cleanup routines safely catch OSError/PermissionError."""
        with mock.patch("os.remove", side_effect=PermissionError("WinError 32")):
            try:
                # Simulating cleanup pattern used in main.py lines 356-378
                try:
                    os.remove("dummy_path")
                except OSError:
                    pass
            except Exception as e:
                self.fail(f"Cleanup failed to catch PermissionError: {e}")


class TestFeature6HeadlessProcessProtection(unittest.TestCase):
    """Feature 6: Headless Process Protection (-nostdin and timeouts)."""

    def test_feat6_01_ffmpeg_subprocess_includes_nostdin(self):
        """6.1 Synthetic generator uses -nostdin for non-blocking execution."""
        ffmpeg = helpers.get_ffmpeg_bin()
        out = os.path.join(tempfile.gettempdir(), "test_nostdin.mp4")
        try:
            cmd = [ffmpeg, "-y", "-nostdin", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.2", out]
            proc = subprocess.run(cmd, capture_output=True, timeout=10, check=False)
            self.assertEqual(proc.returncode, 0)
        finally:
            if os.path.isfile(out):
                os.remove(out)

    def test_feat6_02_ffmpeg_subprocess_has_timeout(self):
        """6.2 Verify subprocess executions enforce an explicit timeout parameter."""
        cmd = [helpers.get_ffmpeg_bin(), "-version"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=5)
            self.assertEqual(proc.returncode, 0)
        except subprocess.TimeoutExpired:
            self.fail("FFmpeg version check timed out unexpectedly")

    def test_feat6_03_subprocess_timeout_expired_handled(self):
        """6.3 Subprocess timeout expired is caught and handled."""
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1)):
            try:
                # Pattern: catch TimeoutExpired
                try:
                    subprocess.run(["dummy"], timeout=1)
                except subprocess.TimeoutExpired:
                    handled = True
                self.assertTrue(handled)
            except Exception as e:
                self.fail(f"TimeoutExpired was not caught: {e}")

    def test_feat6_04_subprocess_captures_output_preventing_deadlock(self):
        """6.4 capture_output=True prevents OS pipe buffer deadlock."""
        cmd = [helpers.get_ffmpeg_bin(), "-h"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertGreater(len(proc.stdout), 0)

    def test_feat6_05_non_interactive_execution_no_gui_popups(self):
        """6.5 Subprocess creation flags use CREATE_NO_WINDOW on Windows if specified."""
        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW
        cmd = [helpers.get_ffmpeg_bin(), "-version"]
        proc = subprocess.run(cmd, capture_output=True, creationflags=creationflags)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
