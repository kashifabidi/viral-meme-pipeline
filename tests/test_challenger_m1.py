"""Adversarial stress test suite for Milestone 1: Pipeline Stability & Headless Cloud Publishing.

Conducted by Challenger 1.
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import drive_uploader
import main


class TestTokenDeserializerStress(unittest.TestCase):
    """Adversarial stress tests for drive_uploader token deserializer."""

    def setUp(self):
        self.valid_payload = {
            "token": "ya29.sample_access_token",
            "refresh_token": "1//sample_refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "sample_client_id.apps.googleusercontent.com",
            "client_secret": "sample_secret",
            "scopes": ["https://www.googleapis.com/auth/drive.file"],
        }
        self.valid_json = json.dumps(self.valid_payload)

    def test_valid_json(self):
        res = drive_uploader._parse_oauth_token_data(self.valid_json)
        self.assertEqual(res, self.valid_payload)

    def test_standard_base64(self):
        b64 = base64.b64encode(self.valid_json.encode("utf-8")).decode("ascii")
        res = drive_uploader._parse_oauth_token_data(b64)
        self.assertEqual(res, self.valid_payload)

    def test_unpadded_standard_base64(self):
        b64 = base64.b64encode(self.valid_json.encode("utf-8")).decode("ascii").rstrip("=")
        res = drive_uploader._parse_oauth_token_data(b64)
        self.assertEqual(res, self.valid_payload)

    def test_urlsafe_base64(self):
        b64 = base64.urlsafe_b64encode(self.valid_json.encode("utf-8")).decode("ascii")
        res = drive_uploader._parse_oauth_token_data(b64)
        self.assertEqual(res, self.valid_payload)

    def test_unpadded_urlsafe_base64(self):
        b64 = base64.urlsafe_b64encode(self.valid_json.encode("utf-8")).decode("ascii").rstrip("=")
        res = drive_uploader._parse_oauth_token_data(b64)
        self.assertEqual(res, self.valid_payload)

    def test_quoted_base64(self):
        b64 = base64.b64encode(self.valid_json.encode("utf-8")).decode("ascii")
        quoted = f'"{b64}"'
        res = drive_uploader._parse_oauth_token_data(quoted)
        self.assertEqual(res, self.valid_payload)

    def test_quoted_json(self):
        res = drive_uploader._parse_oauth_token_data(f"'{self.valid_json}'")
        self.assertEqual(res, self.valid_payload)

    def test_bearer_token(self):
        bearer = "ya29.a0AfH6SMD_ad89172349182374918273"
        res = drive_uploader._parse_oauth_token_data(bearer)
        self.assertEqual(res, {"token": bearer})

    def test_corrupted_base64_invalid_chars(self):
        """Corrupted base64 with non-base64 chars should not crash; returns None."""
        corrupted = "!!@@##$$%%^^&&**(())"
        res = drive_uploader._parse_oauth_token_data(corrupted)
        self.assertIsNone(res)

    def test_corrupted_base64_non_json_content(self):
        """Valid base64 but decodes to non-JSON string; should not crash; returns None."""
        raw_text = "This is definitely not a JSON object!"
        b64 = base64.b64encode(raw_text.encode("utf-8")).decode("ascii")
        res = drive_uploader._parse_oauth_token_data(b64)
        self.assertIsNone(res)

    def test_corrupted_base64_json_primitive(self):
        """Valid base64 but decodes to a JSON scalar (int or string), not a dict; returns None."""
        raw_scalar = json.dumps("just_a_string")
        b64 = base64.b64encode(raw_scalar.encode("utf-8")).decode("ascii")
        res = drive_uploader._parse_oauth_token_data(b64)
        self.assertIsNone(res)

    def test_empty_and_whitespace_inputs(self):
        """Empty or whitespace input returns None."""
        self.assertIsNone(drive_uploader._parse_oauth_token_data(""))
        self.assertIsNone(drive_uploader._parse_oauth_token_data("   \n\t  "))
        self.assertIsNone(drive_uploader._parse_oauth_token_data(None))

    def test_truncated_base64(self):
        """Truncated base64 payload should not crash."""
        b64 = base64.b64encode(self.valid_json.encode("utf-8")).decode("ascii")[:15]
        res = drive_uploader._parse_oauth_token_data(b64)
        # Should gracefully return None or fallback without uncaught exceptions
        self.assertIsNone(res)


class TestBatchStabilityAdversarial(unittest.TestCase):
    """Stress test batch stability when an invalid file is passed along with a valid file."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="m1_stress_batch_")
        self.valid_mp4 = os.path.join(self.tmp_dir, "valid_sample.mp4")
        self.invalid_mp4 = os.path.join(self.tmp_dir, "invalid_nonexistent.mp4")
        self.corrupt_mp4 = os.path.join(self.tmp_dir, "corrupt_zero_byte.mp4")

        # Copy existing test video to simulate a valid file
        src_sample = os.path.join(PROJECT_ROOT, "downloads", "35KDnej1hlI.mp4")
        if os.path.isfile(src_sample):
            shutil.copy2(src_sample, self.valid_mp4)
        else:
            with open(self.valid_mp4, "wb") as f:
                f.write(b"fake_mp4_content")

        # Create zero-byte corrupt file
        with open(self.corrupt_mp4, "wb") as f:
            pass

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_batch_loop_isolation_invalid_then_valid(self):
        """Invalid file does not abort subsequent valid files in batch."""
        batch = [self.invalid_mp4, self.valid_mp4]

        # Mock stages so we don't render heavy MoviePy videos during unit test
        with mock.patch("main.stage_acquire_batch", return_value=batch):
            with mock.patch("main.stage_climax") as mock_climax:
                def side_effect_climax(path):
                    if path == self.invalid_mp4:
                        raise FileNotFoundError(f"File not found: {path}")
                    return 2.5
                mock_climax.side_effect = side_effect_climax

                with mock.patch("main.stage_trim", return_value=(self.valid_mp4, 2.5)):
                    with mock.patch("main.stage_caption", return_value="Test caption"):
                        with mock.patch("main.stage_render", return_value=os.path.join(self.tmp_dir, "out.mp4")):
                            with mock.patch("main.stage_upload", return_value=("id123", "http://drive/link")):
                                exit_code = main.main(["--input", self.valid_mp4])
                                # One failed, one succeeded: exit code must reflect partial success (0 if at least 1 ok, or non-zero if all fail)
                                self.assertEqual(exit_code, 0)

    def test_batch_loop_isolation_valid_then_invalid(self):
        """Valid file succeeds even if followed by a crash-inducing invalid file."""
        batch = [self.valid_mp4, self.invalid_mp4]

        with mock.patch("main.stage_acquire_batch", return_value=batch):
            with mock.patch("main.stage_climax") as mock_climax:
                def side_effect_climax(path):
                    if path == self.invalid_mp4:
                        raise SystemExit(3)
                    return 2.5
                mock_climax.side_effect = side_effect_climax

                with mock.patch("main.stage_trim", return_value=(self.valid_mp4, 2.5)):
                    with mock.patch("main.stage_caption", return_value="Test caption"):
                        with mock.patch("main.stage_render", return_value=os.path.join(self.tmp_dir, "out.mp4")):
                            with mock.patch("main.stage_upload", return_value=("id123", "http://drive/link")):
                                exit_code = main.main(["--input", self.valid_mp4])
                                self.assertEqual(exit_code, 0)

    def test_batch_all_failures_returns_code_1(self):
        """If all items in the batch fail, main() returns non-zero exit code 1."""
        batch = [self.invalid_mp4, self.corrupt_mp4]

        with mock.patch("main.stage_acquire_batch", return_value=batch):
            with mock.patch("main.stage_climax", side_effect=RuntimeError("Corrupt media stream")):
                exit_code = main.main(["--input", self.invalid_mp4])
                self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
