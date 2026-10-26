"""Google Drive upload for finished meme edits (API v3).

Two auth modes, tried in order:

1. OAuth user credentials (DEFAULT, works on personal Google accounts).
   A one-time browser consent (`python drive_uploader.py --auth`) stores a
   refresh token in TOKEN_FILE. Uploads then act as YOU and draw on your own
   Google One / Drive storage -- this is the only option that works for a
   personal account, because a service account has no storage quota of its
   own and personal accounts cannot have shared drives.
   Headless / CI runs can inject the token directly via GOOGLE_OAUTH_TOKEN
   (as raw JSON or base64 JSON string) without needing a token file on disk.

2. Service account (fallback). Only usable when the destination folder lives
   on a SHARED DRIVE, where the drive itself owns the files. Personal "My
   Drive" targets are rejected up front with DestinationError.

Env (loaded from .env):
    GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET   (Desktop app client)
    GOOGLE_OAUTH_TOKEN                                    (optional headless token JSON/base64)
    GOOGLE_OAUTH_TOKEN_FILE=google_oauth_token.json       (optional path override)
    GOOGLE_SERVICE_ACCOUNT_JSON=service_account.json      (optional fallback)
    GOOGLE_DRIVE_FOLDER_ID=...                            (optional target folder; defaults to root)

upload_video_to_drive() uploads the rendered MP4 with a resumable
MediaFileUpload, deletes the local MP4 after success, and returns
(file_id, web_link). Missing credentials skip gracefully with
(None, None); network errors and rate limits retry with exponential backoff;
quota exceeded logs clearly and keeps the local file without crashing.
"""

import base64
import json
import os
from pathlib import Path
import socket
import ssl
import time
import urllib.error

from dotenv import load_dotenv

# Robust .env loading regardless of working directory
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)
load_dotenv()

# Full drive scope so the uploader can both read the destination folder's
# metadata (preflight) and write into it. drive.file is too narrow: it can't
# touch pre-existing folders like MemeBot_Exports.
SCOPES = ["https://www.googleapis.com/auth/drive"]

# Where the one-time OAuth consent stores its refresh token.
# Overridable via GOOGLE_OAUTH_TOKEN_FILE so CI (GitHub Actions etc.) can point
# at a token restored from a secret -- credentials never enter the repo.
TOKEN_ENV_VAR = "GOOGLE_OAUTH_TOKEN_FILE"
TOKEN_RAW_ENV_VAR = "GOOGLE_OAUTH_TOKEN"
DEFAULT_TOKEN_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "google_oauth_token.json"
)


class DestinationError(RuntimeError):
    """Raised when the configured Drive folder can't accept uploads."""


def token_path() -> str:
    """Resolve the OAuth token file, allowing an env override for CI."""
    override = os.environ.get(TOKEN_ENV_VAR, "").strip()
    return override or DEFAULT_TOKEN_FILE


def _build_service(creds):
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _oauth_client_config(client_id=None, client_secret=None):
    """Minimal "installed" client config for InstalledAppFlow.

    Reads from env when not passed explicitly. Returns None if unset so the
    caller can fall through to the service-account path.
    """
    cid = (client_id or os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")).strip()
    csec = (client_secret
            or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")).strip()
    if not cid or not csec:
        return None
    return {"installed": {"client_id": cid, "client_secret": csec,
                          "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                          "token_uri": "https://oauth2.googleapis.com/token",
                          "redirect_uris": ["http://localhost"]}}


def authorize(client_id=None, client_secret=None, token_file=None):
    """Run the one-time browser consent flow and persist the refresh token.

    Returns the authorized Credentials. Call this once, from a machine with a
    browser:
        python drive_uploader.py --auth
    """
    if token_file is None:
        token_file = token_path()
    config = _oauth_client_config(client_id, client_secret)
    if config is None:
        raise DestinationError(
            "GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET not set. "
            "Create an OAuth client of type 'Desktop app' in Google Cloud "
            "Console, then put both values in .env.")

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(config, scopes=SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    with open(token_file, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    print(f"[drive] OAuth authorized; token saved -> {token_file}")
    return creds


def _parse_oauth_token_data(raw_token: str | None) -> dict | None:
    """Parse OAuth token from raw JSON string, base64-encoded JSON, or raw access token.

    Supports:
      1. Plain JSON string containing refresh_token / token / client_id / client_secret
      2. Base64 / URL-safe Base64-encoded JSON string (with padding auto-fix)
      3. Raw Bearer access token string starting with 'ya29.'
      4. Strips outer quotes and whitespace
    """
    if not raw_token or not isinstance(raw_token, str):
        return None
    raw = raw_token.strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1].strip()
    if not raw:
        return None

    # 1. Direct JSON parse
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # 2. Base64 decode then JSON parse
    for b64fn in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            padded = raw + "=" * (-len(raw) % 4)
            decoded = b64fn(padded.encode("ascii")).decode("utf-8")
            data = json.loads(decoded)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    # 3. Direct access token string (e.g. ya29...)
    if raw.startswith("ya29."):
        return {"token": raw}

    return None


parse_oauth_token_payload = _parse_oauth_token_data


def _oauth_service(token_file=None):
    """Build a Drive service from GOOGLE_OAUTH_TOKEN env or stored OAuth token file.

    Priority:
    1. GOOGLE_OAUTH_TOKEN env variable (JSON or base64 JSON) for headless cloud/CI.
    2. Stored OAuth token file (token_file or token_path()).

    Refreshes an expired access token automatically using the refresh token.
    This is a plain HTTPS POST -- NO browser, loopback server, or interactive
    prompt -- which is exactly what makes unattended runs (CI, scheduled
    tasks) possible. The only interactive step is the one-time --auth.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    data = None
    save_file = None

    # 1. Check GOOGLE_OAUTH_TOKEN in environment first
    raw_env_token = os.environ.get(TOKEN_RAW_ENV_VAR, "").strip()
    if raw_env_token:
        data = _parse_oauth_token_data(raw_env_token)
        if not data:
            print(f"[drive] {TOKEN_RAW_ENV_VAR} provided in env is malformed; "
                  "falling back to token file.")

    # 2. Fall back to token file on disk
    if data is None:
        if token_file is None:
            token_file = token_path()
        if not os.path.isfile(token_file):
            return None
        try:
            with open(token_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            save_file = token_file
        except Exception as exc:
            print(f"[drive] failed reading token file {token_file}: {exc}")
            return None

    if not isinstance(data, dict):
        return None

    # Supplement client_id / client_secret from environment if missing in token dict
    if "client_id" not in data and os.environ.get("GOOGLE_OAUTH_CLIENT_ID"):
        data["client_id"] = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    if "client_secret" not in data and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"):
        data["client_secret"] = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    data.setdefault("token_uri", "https://oauth2.googleapis.com/token")

    try:
        if data.get("refresh_token") and data.get("client_id") and data.get("client_secret"):
            creds = Credentials.from_authorized_user_info(data, scopes=SCOPES)
        elif data.get("token"):
            creds = Credentials(token=data["token"], scopes=SCOPES)
        else:
            creds = Credentials.from_authorized_user_info(data, scopes=SCOPES)

        if creds.refresh_token:
            # Refresh proactively even when not flagged expired: keeps long
            # unattended runs from dying mid-pipeline on a stale access token.
            try:
                creds.refresh(Request())
                if save_file:
                    try:
                        with open(save_file, "w", encoding="utf-8") as fh:
                            fh.write(creds.to_json())
                    except OSError:
                        pass
            except Exception as ref_err:
                if not creds.valid:
                    print(f"[drive] OAuth token refresh failed ({ref_err}); re-run --auth or update token.")
                    return None

        if not creds.valid:
            return None
        return _build_service(creds)
    except Exception as exc:
        print(f"[drive] OAuth token unusable ({exc}); re-run --auth.")
        return None


def _service_account_service():
    """Build a service-account Drive service or None when not configured."""
    from google.oauth2 import service_account

    json_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON",
                               "service_account.json").strip()
    if not json_path or not os.path.isfile(json_path):
        return None
    try:
        creds = service_account.Credentials.from_service_account_file(
            json_path, scopes=SCOPES)
        return _build_service(creds)
    except Exception as exc:
        print(f"[drive] service-account auth failed: {exc}")
        return None


def get_drive_service():
    """Return (service, mode) -- OAuth first, then service account.

    mode is "oauth", "service_account", or (None, None) when neither works.
    """
    svc = _oauth_service()
    if svc is not None:
        return svc, "oauth"
    svc = _service_account_service()
    if svc is not None:
        return svc, "service_account"
    return None, None


def check_destination(service=None, folder_id: str = None,
                      mode: str = None) -> dict:
    """Verify the folder exists and is writable by the active auth mode.

    If folder_id is None, empty, or "root", verifies the user's My Drive root folder.
    Service accounts are additionally required to target a SHARED DRIVE,
    because they have no storage quota of their own. OAuth uploads act as the
    user, so personal "My Drive" folders (including root) are fine.

    Returns {shared, drive_id, name, can_add_children, mode, folder_id}.
    Raises DestinationError when the folder is unreachable or unusable.
    """
    if service is None:
        service, detected = get_drive_service()
        mode = mode or detected
    if service is None:
        raise DestinationError(
            "no Drive credentials configured. For a personal Google account, "
            "set GOOGLE_OAUTH_TOKEN in environment or set GOOGLE_OAUTH_CLIENT_ID/"
            "GOOGLE_OAUTH_CLIENT_SECRET in .env and run 'python drive_uploader.py --auth'.")

    raw_folder = (folder_id or os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")).strip()
    target_id = "root" if (not raw_folder or raw_folder.lower() == "root") else raw_folder

    from googleapiclient.errors import HttpError

    try:
        meta = service.files().get(
            fileId=target_id,
            fields="id,name,mimeType,driveId,capabilities",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        status = getattr(exc, "status_code", None) or (exc.resp.status if hasattr(exc, "resp") else "unknown")
        raise DestinationError(
            f"cannot read folder '{target_id}': HTTP {status} -- "
            "check the ID and make sure the active account has access") from exc
    except Exception as exc:
        raise DestinationError(
            f"cannot connect to Drive to verify folder '{target_id}': {exc}") from exc

    drive_id = meta.get("driveId")
    caps = meta.get("capabilities") or {}
    name = meta.get("name") or ("My Drive" if target_id == "root" else target_id)
    info = {
        "shared": bool(drive_id),
        "drive_id": drive_id or "",
        "name": name,
        "can_add_children": bool(caps.get("canAddChildren", True)),
        "mode": mode or "unknown",
        "folder_id": meta.get("id") or target_id,
    }

    # Only service accounts are quota-blocked on personal My Drive.
    if info["mode"] == "service_account" and not info["shared"]:
        raise DestinationError(
            f"folder '{info['name']}' is in personal My Drive, not a shared "
            "drive. A service account has no storage quota, so uploads there "
            "always fail with 403 storageQuotaExceeded. Either point "
            "GOOGLE_DRIVE_FOLDER_ID at a folder on a shared drive, or switch "
            "to OAuth (recommended for personal accounts).")
    if not info["can_add_children"]:
        raise DestinationError(
            f"the active account cannot add files to '{info['name']}' -- "
            "check its role/permissions.")
    return info


def _close_media_handle(media):
    """Safely close any open stream/file handle held by MediaFileUpload.

    Prevents Windows WinError 32 (file lock) when removing or moving local files.
    """
    if media is None:
        return
    for attr in ("_fd", "_stream"):
        handle = getattr(media, attr, None)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


def upload_video_to_drive(file_path: str,
                          folder_id: str | None = None,
                          custom_name: str | None = None,
                          keep_local: bool = False,
                          mime_type: str = "video/mp4") -> tuple[str | None, str | None]:
    """Upload MP4 to Google Drive folder or root, delete local copy, return id+link.

    Supports:
      - Headless OAuth via GOOGLE_OAUTH_TOKEN env or local token file.
      - Upload to My Drive root when folder_id is unset or "root".
      - Exponential backoff retry on transient network timeouts and rate limits (429, 5xx).
      - Graceful detection of quota exceeded (403 storageQuotaExceeded) without retries.
      - Safe Windows file handle release (prevents WinError 32).
      - Never crashes unattended pipeline runs; returns (None, None) on failure/skip.

    Returns:
        (file_id, web_link) on success, (None, None) when skipped/failed.
    """
    # Disambiguation if positional second argument was intended as custom_name:
    # e.g., if folder_id looks like a filename (contains extension/slash) and custom_name is None
    if folder_id and custom_name is None and ("." in folder_id or "/" in folder_id or "\\" in folder_id):
        custom_name = folder_id
        folder_id = None

    if not file_path or not os.path.isfile(file_path):
        print(f"[drive] file not found, skipping: {file_path}")
        return None, None

    abs_path = os.path.abspath(file_path)

    service, mode = get_drive_service()
    if service is None:
        print(f"[drive] credentials not configured -> skipping upload; "
              f"keeping local file {abs_path}")
        return None, None

    raw_folder = (folder_id or os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")).strip()
    target_folder = "root" if (not raw_folder or raw_folder.lower() == "root") else raw_folder

    # Preflight: fail fast with an actionable message if the destination
    # can't accept an upload for the active auth mode.
    try:
        dest = check_destination(service, target_folder, mode)
        print(f"[drive] using {mode} auth; destination '{dest['name']}' "
              f"({dest['drive_id'] or 'My Drive'}) -- OK")
    except DestinationError as exc:
        print(f"[drive] destination unusable: {exc}")
        print(f"[drive] -> skipping upload; keeping local file {abs_path}")
        return None, None
    except Exception as exc:
        print(f"[drive] destination check unexpected error: {exc}")
        print(f"[drive] -> skipping upload; keeping local file {abs_path}")
        return None, None

    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    name = custom_name or os.path.basename(file_path)
    body = {"name": name}
    if dest["folder_id"] != "root":
        body["parents"] = [dest["folder_id"]]

    MAX_RETRIES = 3
    INITIAL_BACKOFF = 2.0  # seconds

    file_id = None
    link = None

    for attempt in range(1, MAX_RETRIES + 1):
        media = None
        try:
            media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
            created = service.files().create(
                body=body,
                media_body=media,
                fields="id, webViewLink",
                supportsAllDrives=True,
            ).execute(num_retries=2)
            file_id = created.get("id")
            link = created.get("webViewLink")
            print(f"[drive] uploaded {name} -> {file_id}")
            break
        except HttpError as exc:
            status = getattr(exc, "status_code", None) or (exc.resp.status if hasattr(exc, "resp") else None)
            reason = ""
            try:
                err_content = json.loads(exc.content.decode("utf-8"))
                errors = err_content.get("error", {}).get("errors", [])
                if errors:
                    reason = errors[0].get("reason", "")
            except Exception:
                pass

            # Non-retryable quota errors
            if status == 403 and reason in ("storageQuotaExceeded", "quotaExceeded"):
                print(f"[drive] upload failed: storage quota exceeded ({reason}). "
                      f"Keeping local file: {abs_path}")
                return None, None

            # Retryable rate limits (429) or transient Google server errors (5xx)
            is_retryable = (status in (429, 500, 502, 503, 504) or
                            (status == 403 and reason in ("userRateLimitExceeded", "rateLimitExceeded")))
            if is_retryable and attempt < MAX_RETRIES:
                backoff = INITIAL_BACKOFF * (2 ** (attempt - 1))
                print(f"[drive] transient HTTP {status} ({reason or 'server error'}); "
                      f"retrying in {backoff:.1f}s (attempt {attempt}/{MAX_RETRIES})...")
                time.sleep(backoff)
                continue
            else:
                print(f"[drive] upload failed with HTTP {status} ({reason or exc}): "
                      f"keeping local file {abs_path}")
                return None, None

        except (socket.timeout, TimeoutError, ConnectionError, ssl.SSLError,
                urllib.error.URLError, OSError) as exc:
            if attempt < MAX_RETRIES:
                backoff = INITIAL_BACKOFF * (2 ** (attempt - 1))
                print(f"[drive] network timeout/error ({exc}); "
                      f"retrying in {backoff:.1f}s (attempt {attempt}/{MAX_RETRIES})...")
                time.sleep(backoff)
                continue
            else:
                print(f"[drive] upload failed after {MAX_RETRIES} attempts due to network error: {exc}. "
                      f"Keeping local file {abs_path}")
                return None, None

        except Exception as exc:
            print(f"[drive] unexpected upload error: {exc} -> keeping local file {abs_path}")
            return None, None

        finally:
            _close_media_handle(media)

    if not file_id:
        return None, None

    if not keep_local:
        try:
            os.remove(file_path)
            print(f"[drive] deleted local copy: {abs_path}")
        except OSError as exc:
            print(f"[drive] uploaded but local delete failed: {exc}")
    else:
        print(f"[drive] kept local copy (--keep-local): {abs_path}")
    return file_id, link


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python drive_uploader.py --auth")
        print("       python drive_uploader.py --check [folder_id]")
        print("       python drive_uploader.py <mp4> [custom_name]")
        raise SystemExit(1)

    cmd = sys.argv[1]

    if cmd == "--auth":
        # One-time browser consent; stores a refresh token for all later runs.
        try:
            authorize()
            print("Done. Verify with: python drive_uploader.py --check")
        except Exception as exc:
            print(f"Auth failed: {exc}")
            raise SystemExit(1)
        raise SystemExit(0)

    if cmd == "--check":
        # Preflight-only: validate credentials + destination without uploading.
        check_folder = sys.argv[2] if len(sys.argv) > 2 else None
        service, mode = get_drive_service()
        try:
            dest = check_destination(service, check_folder, mode)
            print(f"OK ({mode}): folder '{dest['name']}' on "
                  f"{dest['drive_id'] or 'My Drive'} is writable.")
        except DestinationError as exc:
            print(f"FAIL: {exc}")
            raise SystemExit(1)
        except Exception as exc:
            print(f"FAIL: {exc}")
            raise SystemExit(1)
        raise SystemExit(0)

    fid, link = upload_video_to_drive(
        sys.argv[1], custom_name=sys.argv[2] if len(sys.argv) > 2 else None)
    print(fid, link)
