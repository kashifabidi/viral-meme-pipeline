"""Google Drive upload for finished meme edits (API v3).

Two auth modes, tried in order:

1. OAuth user credentials (DEFAULT, works on personal Google accounts).
   A one-time browser consent (`python drive_uploader.py --auth`) stores a
   refresh token in TOKEN_FILE. Uploads then act as YOU and draw on your own
   Google One / Drive storage -- this is the only option that works for a
   personal account, because a service account has no storage quota of its
   own and personal accounts cannot have shared drives.

2. Service account (fallback). Only usable when the destination folder lives
   on a SHARED DRIVE, where the drive itself owns the files. Personal "My
   Drive" targets are rejected up front with DestinationError.

Env (loaded from .env):
    GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET   (Desktop app client)
    GOOGLE_SERVICE_ACCOUNT_JSON=service_account.json      (optional fallback)
    GOOGLE_DRIVE_FOLDER_ID=...                            (upload target)

upload_video_to_drive() uploads the rendered MP4 with a resumable
MediaFileUpload, deletes the local MP4 after success, and returns
(file_id, web_link). Missing credentials/folder skip gracefully with
(None, None); a bad destination raises DestinationError.
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()

# Full drive scope so the uploader can both read the destination folder's
# metadata (preflight) and write into it. drive.file is too narrow: it can't
# touch pre-existing folders like MemeBot_Exports.
SCOPES = ["https://www.googleapis.com/auth/drive"]

# Where the one-time OAuth consent stores its refresh token.
# Overridable via GOOGLE_OAUTH_TOKEN_FILE so CI (GitHub Actions etc.) can point
# at a token restored from a secret -- credentials never enter the repo.
TOKEN_ENV_VAR = "GOOGLE_OAUTH_TOKEN_FILE"
DEFAULT_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "google_oauth_token.json")


def token_path() -> str:
    """Resolve the OAuth token file, allowing an env override for CI."""
    override = os.environ.get(TOKEN_ENV_VAR, "").strip()
    return override or DEFAULT_TOKEN_FILE


class DestinationError(RuntimeError):
    """Raised when the configured Drive folder can't accept uploads."""


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


def _oauth_service(token_file=None):
    """Build a Drive service from the stored OAuth token, or None.

    Refreshes an expired access token automatically using the refresh token.
    This is a plain HTTPS POST -- NO browser, loopback server, or interactive
    prompt -- which is exactly what makes unattended runs (CI, scheduled
    tasks) possible. The only interactive step is the one-time --auth.
    """
    if token_file is None:
        token_file = token_path()
    if not os.path.isfile(token_file):
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    try:
        with open(token_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        creds = Credentials.from_authorized_user_info(data, scopes=SCOPES)
        if creds.refresh_token:
            # Refresh proactively even when not flagged expired: keeps long
            # unattended runs from dying mid-pipeline on a stale access token.
            creds.refresh(Request())
            with open(token_file, "w", encoding="utf-8") as fh:
                fh.write(creds.to_json())
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

    Service accounts are additionally required to target a SHARED DRIVE,
    because they have no storage quota of their own. OAuth uploads act as the
    user, so personal "My Drive" folders are fine.

    Returns {shared, drive_id, name, can_add_children, mode}.
    Raises DestinationError when the folder is unreachable or unusable.
    """
    if service is None:
        service, detected = get_drive_service()
        mode = mode or detected
    folder_id = (folder_id or os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
                 ).strip()
    if service is None:
        raise DestinationError(
            "no Drive credentials configured. For a personal Google account, "
            "create a Desktop OAuth client, set GOOGLE_OAUTH_CLIENT_ID/"
            "GOOGLE_OAUTH_CLIENT_SECRET in .env, then run "
            "'python drive_uploader.py --auth'.")
    if not folder_id:
        raise DestinationError("GOOGLE_DRIVE_FOLDER_ID is empty")

    from googleapiclient.errors import HttpError

    try:
        meta = service.files().get(
            fileId=folder_id,
            fields="id,name,mimeType,driveId,capabilities",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise DestinationError(
            f"cannot read folder {folder_id}: HTTP {exc.status_code} -- "
            "check the ID and make sure your account can see it") from exc

    drive_id = meta.get("driveId")
    caps = meta.get("capabilities") or {}
    info = {
        "shared": bool(drive_id),
        "drive_id": drive_id or "",
        "name": meta.get("name") or "",
        "can_add_children": bool(caps.get("canAddChildren")),
        "mode": mode or "unknown",
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


def upload_video_to_drive(file_path: str, custom_name: str = None, keep_local: bool = False):
    """Upload MP4 to the Drive folder, delete local copy, return id+link.

    Returns:
        (file_id, web_link) on success, (None, None) when skipped/failed.
    """
    if not file_path or not os.path.isfile(file_path):
        print(f"[drive] file not found, skipping: {file_path}")
        return None, None

    service, mode = get_drive_service()
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    if service is None or not folder_id:
        print("[drive] credentials/folder not set -> skipping upload; "
              f"keeping local file {file_path}")
        return None, None

    # Preflight: fail fast with an actionable message if the destination
    # can't accept an upload for the active auth mode.
    try:
        dest = check_destination(service, folder_id, mode)
        print(f"[drive] using {mode} auth; destination '{dest['name']}' "
              f"({dest['drive_id'] or 'My Drive'}) -- OK")
    except DestinationError as exc:
        print(f"[drive] destination unusable: {exc}")
        print(f"[drive] -> skipping upload; keeping local file {file_path}")
        return None, None

    from googleapiclient.http import MediaFileUpload

    name = custom_name or os.path.basename(file_path)
    media = MediaFileUpload(file_path, mimetype="video/mp4", resumable=True)
    try:
        created = service.files().create(
            body={"name": name, "parents": [folder_id]},
            media_body=media,
            fields="id, webViewLink",
            supportsAllDrives=True,
        ).execute()
        file_id = created.get("id")
        link = created.get("webViewLink")
        print(f"[drive] uploaded {name} -> {file_id}")
    except Exception as exc:
        print(f"[drive] upload failed: {exc} -> keeping local file")
        return None, None
    finally:
        # MediaFileUpload keeps an open BufferedReader on the source file
        # (media._fd). Windows refuses to delete a file with an open handle
        # (WinError 32), so release it before the os.remove below.
        for handle in ("_fd", "_stream"):
            fh = getattr(media, handle, None)
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass

    if not keep_local:
        try:
            os.remove(file_path)
            print(f"[drive] deleted local copy: {file_path}")
        except OSError as exc:
            print(f"[drive] uploaded but local delete failed: {exc}")
    else:
        print(f"[drive] kept local copy (--keep-local): {file_path}")
    return file_id, link


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python drive_uploader.py --auth")
        print("       python drive_uploader.py --check")
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
        service, mode = get_drive_service()
        try:
            dest = check_destination(service, None, mode)
            print(f"OK ({mode}): folder '{dest['name']}' on "
                  f"{dest['drive_id'] or 'My Drive'} is writable.")
        except DestinationError as exc:
            print(f"FAIL: {exc}")
            raise SystemExit(1)
        raise SystemExit(0)

    fid, link = upload_video_to_drive(
        sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(fid, link)
