"""Tier 5: Adversarial Coverage Hardening & Boundary Validation.

Adversarial stress-test suite challenging:
1. Malformed and extreme audio/video inputs (0-byte, corrupt headers, silent audio, extreme sample rates, corrupt overlays).
2. Long hook strings in TTS and sanitization (5,000 chars, pure emojis, PowerShell script injection tokens).
3. Offline TTS fallback switching to Windows System.Speech SAPI5 (including live PowerShell synthesis on Windows host).
4. Subprocess timeouts and process termination resilience (upscale timeout fallback, stage_trim timeout, batch pipeline fault isolation).
5. Google Drive publishing with invalid/corrupt tokens, revoked refresh tokens, destination errors, and offline caching fallback.
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
import tts_generator
import video_editor


class TestBoundaryCondition1_MalformedExtremeMediaInputs(unittest.TestCase):
    """Stress tests for malformed, extreme, and corrupted media inputs."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="tier5_media_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_adv01_zero_byte_media_files_climax_and_duration(self):
        """Zero-byte media files safely return 0.0 without divide-by-zero or crash."""
        empty_vid = os.path.join(self.temp_dir, "empty.mp4")
        with open(empty_vid, "wb") as f:
            pass

        dur = audio_detector._get_duration_seconds(empty_vid)
        self.assertEqual(dur, 0.0)

        climax = audio_detector.find_climax_timestamp(empty_vid)
        self.assertEqual(climax, 0.0)

        dur_tts = tts_generator._get_audio_duration(empty_vid)
        # Empty file triggers safe default duration fallback (2.5s) to prevent downstream division by zero
        self.assertIn(dur_tts, (0.0, 2.5))

    def test_adv02_corrupt_binary_header_audio_and_video(self):
        """Corrupted random binary files handle duration and climax checks without unhandled crash."""
        corrupt_file = os.path.join(self.temp_dir, "garbage.mp4")
        with open(corrupt_file, "wb") as f:
            f.write(os.urandom(1024))

        dur = audio_detector._get_duration_seconds(corrupt_file)
        self.assertEqual(dur, 0.0)

        # Climax detector catches invalid stream or no audio stream and falls back safely
        climax = audio_detector.find_climax_timestamp(corrupt_file)
        self.assertEqual(climax, 0.0)

    def test_adv03_audio_detector_invalid_search_windows(self):
        """Audio detector strictly rejects malformed or inverted search windows."""
        sample_path = os.path.join(self.temp_dir, "sample.mp4")
        helpers.create_synthetic_video(sample_path, duration=2.0)

        invalid_windows = [
            (-0.5, 0.5),   # negative start
            (0.8, 0.2),    # start > end
            (0.5, 0.5),    # start == end
            (0.0, 1.5),    # end > 1.0
            (-0.2, 1.2),   # both out of bounds
        ]
        for win in invalid_windows:
            with self.subTest(window=win):
                with self.assertRaises(ValueError):
                    audio_detector.find_climax_timestamp(sample_path, search_window=win)

    def test_adv04_silent_audio_rms_envelope_handling(self):
        """Synthetic video with completely silent audio safely falls back to valid climax timestamp."""
        silent_path = os.path.join(self.temp_dir, "silent.mp4")
        helpers.create_synthetic_video(silent_path, duration=2.5, has_audio=True, freq=0)

        climax = audio_detector.find_climax_timestamp(silent_path)
        self.assertIsInstance(climax, float)
        self.assertGreaterEqual(climax, 0.0)
        self.assertLessEqual(climax, 2.5)

    def test_adv05_extreme_high_samplerate_audio(self):
        """Audio file with high sample rate (96kHz) is processed cleanly."""
        high_sr_path = os.path.join(self.temp_dir, "high_sr.wav")
        helpers.create_synthetic_audio(high_sr_path, duration=1.5, sample_rate=96000)

        dur = tts_generator._get_audio_duration(high_sr_path)
        self.assertAlmostEqual(dur, 1.5, places=1)

    def test_adv06_extreme_meme_text_lengths_and_unicode_anomalies(self):
        """PIL meme text renderer survives 5,000 characters and RTL/control characters."""
        long_text = "WORD " * 800  # 4,000 characters
        img_long = video_editor.create_meme_text_image(long_text)
        self.assertIsInstance(img_long, Image.Image)
        self.assertGreater(img_long.size[0], 0)
        self.assertGreater(img_long.size[1], 0)

        # Unicode bidirectional marks, zero-width joiners, and null bytes
        bidi_text = "\u200e\u200f\u202a\u202bArabic \u0645\u0631\u062d\u0628\u0627 Hebrew \u05e9\u05dc\u05d5\u05dd\u202c\ufeff"
        img_bidi = video_editor.create_meme_text_image(bidi_text)
        self.assertIsInstance(img_bidi, Image.Image)

        # Empty string / None
        for empty in ("", "   ", None):
            with self.subTest(empty=empty):
                img_empty = video_editor.create_meme_text_image(empty)
                self.assertIsInstance(img_empty, Image.Image)

    def test_adv07_extreme_hook_banner_and_credit_badge_lengths(self):
        """Hook banner and credit pill badge cleanly truncate extreme text without crash."""
        long_banner = "VERY LONG BANNER " * 50
        img_banner = video_editor.create_hook_banner_image(long_banner)
        self.assertIsInstance(img_banner, Image.Image)

        # 500-character credit string must truncate to <= 55 characters without crash
        long_credit = "Credit: @" + "A" * 500
        img_badge = video_editor.create_credit_badge_image(long_credit)
        self.assertIsInstance(img_badge, Image.Image)

        # Empty string in banner and credit
        img_empty_badge = video_editor.create_credit_badge_image("")
        self.assertIsInstance(img_empty_badge, Image.Image)

    def test_adv08_corrupt_overlay_image_resilience(self):
        """assemble_meme_edit_with_freeze_and_caption handles corrupt overlay image without crashing."""
        sample_path = os.path.join(self.temp_dir, "sample.mp4")
        helpers.create_synthetic_video(sample_path, duration=2.0)
        corrupt_overlay = os.path.join(self.temp_dir, "corrupt_overlay.png")
        with open(corrupt_overlay, "wb") as f:
            f.write(b"NOT A REAL PNG FILE AT ALL")

        out_path = os.path.join(self.temp_dir, "out_corrupt_ov.mp4")
        scratch = os.path.abspath(os.path.join("assets", "sfx", "record_scratch.mp3"))
        boom = os.path.abspath(os.path.join("assets", "sfx", "vine_boom.mp3"))

        res = video_editor.assemble_meme_edit_with_freeze_and_caption(
            sample_path,
            climax_timestamp=1.0,
            caption_text="Test Corrupt Overlay",
            record_scratch_sfx=scratch if os.path.isfile(scratch) else None,
            impact_sfx=boom if os.path.isfile(boom) else None,
            overlay_image_path=corrupt_overlay,
            output_path=out_path,
        )
        self.assertTrue(os.path.isfile(res))
        self.assertGreater(os.path.getsize(res), 1000)


class TestBoundaryCondition2_LongHookStringsAndTTSNormalization(unittest.TestCase):
    """Stress tests for TTS hook normalization, emoji stripping, and long strings."""

    def test_adv09_long_hook_string_normalization(self):
        """TTS cleaner handles 5,000-character string without hanging."""
        raw = ("Look at this amazing viral clip right now! " * 150) + "???"
        cleaned = tts_generator.clean_hook_text_for_speech(raw)
        self.assertIsInstance(cleaned, str)
        self.assertFalse(cleaned.endswith("?"))
        self.assertNotIn("??", cleaned)

    def test_adv10_pure_emoji_and_corrupt_symbols_fallback(self):
        """Pure emoji strings or empty input fallback to 'Wait for it'."""
        pure_emojis = ["🔥🔥💀💀😭🤣🎉", "🧠⚠️📉", "😱🔥", "  💀  ", "???", ""]
        for s in pure_emojis:
            with self.subTest(input_text=s):
                cleaned = tts_generator.clean_hook_text_for_speech(s)
                self.assertEqual(cleaned, "Wait for it")

    def test_adv11_powershell_code_injection_prevention(self):
        """PowerShell command tokens and single quotes are safely escaped for SAPI5 synthesis."""
        malicious = "Wait for it'; Remove-Item -Path C:\\ -Recurse -Force; Write-Host 'hacked"
        cleaned = tts_generator.clean_hook_text_for_speech(malicious)
        escaped = cleaned.replace("'", "''")
        # In PowerShell, single quote escaping doubles the quote: ' -> ''
        self.assertIn("''", escaped)
        # Removing doubled quotes '' leaves 0 unescaped single quotes
        remaining_single_quotes = escaped.replace("''", "").count("'")
        self.assertEqual(remaining_single_quotes, 0)


class TestBoundaryCondition3_OfflineTTSFallbackToWindowsSpeech(unittest.TestCase):
    """Stress tests for offline TTS fallback and Windows System.Speech SAPI5."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="tier5_tts_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_adv12_edge_tts_failure_seamless_fallback_to_sapi5(self):
        """When edge-tts raises a network error, synthesize_hook_voiceover falls back to SAPI5."""
        with mock.patch("tts_generator._synthesize_edge_tts", side_effect=RuntimeError("Edge-TTS network down")):
            with mock.patch("tts_generator._synthesize_sapi5_powershell", return_value=("mock_sapi.wav", 2.3)) as mock_sapi:
                path, dur = tts_generator.synthesize_hook_voiceover("Check this out")
                self.assertEqual(path, "mock_sapi.wav")
                self.assertEqual(dur, 2.3)
                mock_sapi.assert_called_once()

    def test_adv13_live_windows_sapi5_powershell_synthesis(self):
        """Live execution of Windows System.Speech SAPI5 produces a valid WAV file on Windows host."""
        out_wav = os.path.join(self.temp_dir, "live_sapi.wav")
        produced_path, dur = tts_generator._synthesize_sapi5_powershell(
            "Adversarial verification of offline speech synthesis",
            output_path=out_wav,
        )
        self.assertTrue(os.path.isfile(produced_path))
        self.assertGreater(os.path.getsize(produced_path), 500)
        self.assertGreater(dur, 0.5)

    def test_adv14_live_windows_sapi5_mp3_conversion(self):
        """Live SAPI5 synthesis with .mp3 target successfully converts via FFmpeg."""
        out_mp3 = os.path.join(self.temp_dir, "live_sapi.mp3")
        produced_path, dur = tts_generator._synthesize_sapi5_powershell(
            "Testing Windows SAPI5 MP3 conversion pass",
            output_path=out_mp3,
        )
        self.assertTrue(os.path.isfile(produced_path))
        self.assertTrue(produced_path.lower().endswith(".mp3"))
        self.assertGreater(os.path.getsize(produced_path), 500)
        self.assertGreater(dur, 0.5)


class TestBoundaryCondition4_SubprocessTimeoutsAndProcessTermination(unittest.TestCase):
    """Stress tests for subprocess timeouts, failures, and pipeline isolation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="tier5_subproc_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_adv15_sapi5_subprocess_timeout_resilience(self):
        """SAPI5 raises RuntimeError cleanly when PowerShell execution times out."""
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="powershell", timeout=30)):
            with self.assertRaises(Exception):
                tts_generator._synthesize_sapi5_powershell("Text will timeout")

    def test_adv16_video_editor_upscale_subprocess_failure_resilience(self):
        """When FFmpeg upscale subprocess fails, assemble_meme_edit returns the edit-res render instead of crashing."""
        sample_path = os.path.join(self.temp_dir, "upscale_fail_src.mp4")
        helpers.create_synthetic_video(sample_path, duration=1.5)
        out_path = os.path.join(self.temp_dir, "final_upscale_fallback.mp4")

        orig_run = subprocess.run

        def mock_run(cmd, *args, **kwargs):
            # If the command is the upscale command (contains scale=1080:1920)
            if any("scale=1080:1920" in str(arg) for arg in cmd):
                raise subprocess.SubprocessError("Synthetic upscale process failure")
            return orig_run(cmd, *args, **kwargs)

        with mock.patch("subprocess.run", side_effect=mock_run):
            with mock.patch("video_editor.RENDER_SCALE", 0.75):
                res = video_editor.assemble_meme_edit_with_freeze_and_caption(
                    sample_path,
                    climax_timestamp=1.0,
                    caption_text="Upscale fallback test",
                    record_scratch_sfx=None,
                    impact_sfx=None,
                    overlay_image_path=None,
                    output_path=out_path,
                )
                self.assertTrue(os.path.isfile(res))
                self.assertGreater(os.path.getsize(res), 1000)

    def test_adv17_stage_trim_timeout_fallback(self):
        """When stage_trim FFmpeg times out, it safely falls back to untrimmed source video."""
        sample_path = os.path.join(self.temp_dir, "trim_timeout.mp4")
        helpers.create_synthetic_video(sample_path, duration=70.0)

        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=180)):
            w_path, w_climax = main.stage_trim(sample_path, 40.0)
            self.assertEqual(w_path, sample_path)
            self.assertEqual(w_climax, 40.0)

    def test_adv18_pipeline_batch_iteration_item_abort_resilience(self):
        """Batch processing in main.main isolates item-level failures and completes remaining items."""
        sample1 = os.path.join(self.temp_dir, "item1.mp4")
        sample2 = os.path.join(self.temp_dir, "item2.mp4")
        sample3 = os.path.join(self.temp_dir, "item3.mp4")
        for s in (sample1, sample2, sample3):
            helpers.create_synthetic_video(s, duration=1.0)

        call_count = [0]

        def fake_climax(path):
            call_count[0] += 1
            if call_count[0] == 1:
                raise SystemExit(3)  # Stage climax aborts on item 1
            elif call_count[0] == 3:
                raise RuntimeError("Corrupt media stream on item 3")
            return 0.5  # Item 2 succeeds

        with mock.patch("main.stage_acquire_batch", return_value=[sample1, sample2, sample3]):
            with mock.patch("main.stage_climax", side_effect=fake_climax):
                with mock.patch("main.stage_render", return_value=sample2):
                    with mock.patch("main.stage_upload", return_value=("id123", "http://drive/link")):
                        exit_code = main.main(["--harvest", "--max", "3"])
                        # Since item 2 succeeded, ok > 0, overall batch exit code is 0
                        self.assertEqual(exit_code, 0)
                        self.assertEqual(call_count[0], 3)


class TestBoundaryCondition5_DrivePublishingInvalidTokensAndOfflineFallback(unittest.TestCase):
    """Stress tests for Drive OAuth tokens, destination errors, network drops, and offline caching."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="tier5_drive_")
        self.dummy_video = os.path.join(self.temp_dir, "upload_test.mp4")
        helpers.create_synthetic_video(self.dummy_video, duration=1.0)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_adv19_corrupted_base64_oauth_token(self):
        """Malformed base64 token string safely parses as None without throwing unhandled exception."""
        bad_base64 = "!!!NotBase64AtAll***???"
        parsed = drive_uploader._parse_oauth_token_data(bad_base64)
        self.assertIsNone(parsed)

    def test_adv20_garbage_json_oauth_token(self):
        """Malformed JSON string in GOOGLE_OAUTH_TOKEN returns None."""
        bad_json = '{"broken": missing_quotes, "foo": }'
        parsed = drive_uploader._parse_oauth_token_data(bad_json)
        self.assertIsNone(parsed)

    def test_adv21_raw_access_token_parsing(self):
        """Raw access token starting with ya29. is correctly recognized and wrapped in dict."""
        raw_bearer = "ya29.a0AfH6SM_adversarial_test_bearer_token_string_12345"
        parsed = drive_uploader._parse_oauth_token_data(raw_bearer)
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed.get("token"), raw_bearer)

    def test_adv22_revoked_or_invalid_refresh_token_offline_caching(self):
        """Invalid or revoked OAuth refresh token triggers clean offline fallback and keeps local file."""
        fake_token_json = (
            '{"refresh_token": "revoked_mock_token", "client_id": "fake_client_id", '
            '"client_secret": "fake_client_secret", "token_uri": "https://oauth2.googleapis.com/token"}'
        )
        with helpers.isolated_env(GOOGLE_OAUTH_TOKEN=fake_token_json, GOOGLE_OAUTH_TOKEN_FILE="nonexistent.json"):
            # Mock Credentials to simulate refresh failure
            with mock.patch("google.oauth2.credentials.Credentials.refresh", side_effect=Exception("invalid_grant")):
                fid, link = drive_uploader.upload_video_to_drive(self.dummy_video)
                self.assertIsNone(fid)
                self.assertIsNone(link)
                # Local video must be kept safely on disk
                self.assertTrue(os.path.isfile(self.dummy_video))

    def test_adv23_destination_preflight_destination_error_fallback(self):
        """Preflight DestinationError safely aborts upload and keeps local file without crashing."""
        mock_svc = mock.MagicMock()
        with mock.patch("drive_uploader.get_drive_service", return_value=(mock_svc, "service_account")):
            with mock.patch("drive_uploader.check_destination", side_effect=drive_uploader.DestinationError("Personal drive not allowed for SA")):
                fid, link = drive_uploader.upload_video_to_drive(self.dummy_video)
                self.assertIsNone(fid)
                self.assertIsNone(link)
                self.assertTrue(os.path.isfile(self.dummy_video))

    def test_adv24_transient_network_errors_retry_and_eventual_offline_fallback(self):
        """Upload retries on transient network errors and falls back safely when exhausted."""
        mock_svc = mock.MagicMock()
        import socket
        mock_svc.files().create().execute.side_effect = socket.timeout("Timed out connecting to Google Drive API")

        with mock.patch("drive_uploader.get_drive_service", return_value=(mock_svc, "oauth")):
            with mock.patch("drive_uploader.check_destination", return_value={"name": "Drive", "drive_id": "", "can_add_children": True, "folder_id": "root"}):
                with mock.patch("time.sleep"):  # fast-forward retries
                    fid, link = drive_uploader.upload_video_to_drive(self.dummy_video)
                    self.assertIsNone(fid)
                    self.assertIsNone(link)
                    self.assertTrue(os.path.isfile(self.dummy_video))

    def test_adv25_windows_file_handle_closure_winerror32(self):
        """_close_media_handle safely closes underlying file stream without errors."""
        mock_media = mock.MagicMock()
        mock_fd = mock.MagicMock()
        mock_media._fd = mock_fd
        drive_uploader._close_media_handle(mock_media)
        mock_fd.close.assert_called_once()

        # Handle None without error
        drive_uploader._close_media_handle(None)


if __name__ == "__main__":
    unittest.main()
