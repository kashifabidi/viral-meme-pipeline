# Viral Video Pipeline
Automated viral video curation and editing pipeline.

## Setup (Windows PowerShell)
```powershell
cd C:\Projects\viral-pipeline
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Layout
- assets/sfx/ - sound effects
- assets/overlays/ - overlay graphics
- downloads/ - raw downloads (yt-dlp)
- output/ - finished edits

## Env
Copy `.env` values. **Only Google Drive is required** -- harvesting needs no
credentials at all:
- GOOGLE_DRIVE_FOLDER_ID=
- GOOGLE_SERVICE_ACCOUNT_JSON=service_account.json

## Usage
```powershell
python main.py --input downloads\test_clip.mp4   # single video (path or URL)
python main.py --harvest                          # auto-source viral clips
```

## Harvesting (zero auth)
`--harvest` pulls candidates from two credential-free sources via
`get_viral_candidates()`:

1. **Reddit public JSON** - GETs `/r/<sub>/top.json?t=all&limit=N` with a
   browser User-Agent (no OAuth/PRAW), keeping native video posts
   (`is_video` or `v.redd.it`) scoring >= 25,000.
   Reddit 403s public `.json` from many datacenter/cloud IPs. If blocked the
   harvester prints the reason and moves on instead of failing the run.
2. **YouTube search** - yt-dlp `ytsearchN:`, filtered to 5-30s clips with
   >= 1M views. Real search returns mostly long compilations, so if the
   strict window matches nothing the thresholds relax progressively
   (wider window, then lower view floor) and the run reports which tier hit.

Results are interleaved by rank rather than concatenated, so one dead source
can't bury the other. Downloads try the permalink, then the direct
`v.redd.it` fallback.

## Google Drive upload
The uploader supports two auth modes and picks whichever is configured:

| Mode | Works on | Storage used | When to use |
|---|---|---|---|
| **OAuth** (default) | Any Google account | Your own Google One / Drive quota | Personal accounts |
| Service account | Shared drives only | None (SA has no quota) | Google Workspace bots |

**Why OAuth is the default:** a service account has *no storage quota of its
own*. Google rejects its uploads with `403 storageQuotaExceeded` even into an
empty Drive, and personal accounts can't create shared drives — so a service
account has nowhere it can write. OAuth acts as *you* and uses your quota.

### One-time OAuth setup
1. In [Google Cloud Console](https://console.cloud.google.com/) create an
   OAuth client ID of type **Desktop app** (APIs & Services → Credentials).
2. Enable the **Google Drive API** for the project.
3. Put the client ID + secret in `.env`:
   ```
   GOOGLE_OAUTH_CLIENT_ID=....apps.googleusercontent.com
   GOOGLE_OAUTH_CLIENT_SECRET=....
   ```
4. Authorize once (opens a browser; consent with the account that owns the
   folder). The refresh token is stored in `google_oauth_token.json`:
   ```powershell
   python drive_uploader.py --auth
   ```
5. Verify:
   ```powershell
   python drive_uploader.py --check
   # -> OK (oauth): folder 'MemeBot_Exports' on My Drive is writable.
   ```

> The consent screen starts in "Testing" mode, so the refresh token expires
> after 7 days. To make it permanent, publish the app (or add your account as
> a test user) in the OAuth consent screen settings.

### If you have Google Workspace
Create a folder **inside a shared drive**, share that drive with the service
account email (`client_email` in `service_account.json`) as Contributor, and
put the folder ID in `GOOGLE_DRIVE_FOLDER_ID`. Shared-drive files are owned by
the drive, so no SA quota applies.

## Long-source handling
Harvested candidates are often multi-minute compilations. The pipeline keeps
the freeze on the *real* crash but stays Short-length:

1. `stage_climax` detects the loudest moment on the **full** source.
2. `stage_trim` windows sources longer than `TRIM_THRESHOLD` (60s) to
   `PRE_ROLL_SRC` (8s) before the climax + `POST_ROLL` (3s) after it, so
   Whisper only transcribes ~11s and the render stays fast.
3. The editor's `pre_roll` (6s) additionally caps Segment 1, so even if a
   long source slips through, the front of the edit never runs minutes long.

## Performance
Render cost scales with **output** pixels, not source pixels. `video_editor.py`
therefore edits at `RENDER_SCALE` and upscales to 1080x1920 with one cheap
ffmpeg pass (`bicubic` + `veryfast`). Measured on this machine, per frame:

| Edit res | Per frame | Full 14s run |
|---|---|---|
| 1080x1920 | 0.596 s | ~254 s |
| 810x1440 (default, 0.75) | ~0.42 s | ~194 s |
| 540x960 | 0.269 s | - |

- `RENDER_SCALE = 1.0` disables it (max quality, ~30% slower).
- `MAX_INPUT_WIDTH = 1280` caps the source decode width so 4K harvests don't
  inflate per-frame resize cost.

## Hands-free scheduling (Windows Task Scheduler)

`cron` does not exist on Windows, so `run_scheduled.bat` is the wrapper and
**Task Scheduler** is the timer. Two tasks are already registered on this
machine, running `--harvest` twice a day:

| Task | Schedule | Next run |
|---|---|---|
| `MemeBot-Harvest-AM` | Daily 10:00 | e.g. 14-10-2026 10:00 |
| `MemeBot-Harvest-PM` | Daily 18:00 | e.g. 14-10-2026 18:00 |

`run_scheduled.bat` `cd`s to the project (so it works from any CWD), verifies
the venv exists, rotates `bot.log` past 5 MB into `bot.log.old`, and writes
both stdout and stderr plus the exit code to `bot.log`.

### Inspect / run / remove
```powershell
schtasks /query /tn MemeBot-Harvest-AM /fo list     # next run time + status
schtasks /run    /tn MemeBot-Harvest-AM             # fire it right now
Get-Content bot.log -Tail 20                       # what happened
```

To re-register on another machine (note the quoting — the path has spaces):
```powershell
$u = whoami
schtasks /create /tn MemeBot-Harvest-AM /tr '"C:\Projects\viral-pipeline\run_scheduled.bat"' /sc daily /st 10:00 /ru "$u" /f
schtasks /create /tn MemeBot-Harvest-PM /tr '"C:\Projects\viral-pipeline\run_scheduled.bat"' /sc daily /st 18:00 /ru "$u" /f
```
Created without `/rp`, so the task runs only while you are logged on, needs no
stored password, and works interactively. To run when logged off, add
`/rl HIGHEST /rp <password>`.

> A scheduled run is only as reliable as the OAuth token. Publishing the OAuth
> app (see above) keeps the refresh token alive indefinitely; in "Testing" mode
> it dies after 7 days and every scheduled run then reports a Drive auth
> failure in `bot.log` while still producing the render.

## Tools
- Python 3.12.10
- FFmpeg 9.0.2 (via `winget install --id Gyan.FFmpeg -e`)

### FFmpeg discovery note
`winget` installs FFmpeg into `%LOCALAPPDATA%\Microsoft\WinGet\Packages\...`
and does **not** add it to PATH, so `ffmpeg -version` can work in one shell
while a fresh shell (and yt-dlp) still see "ffmpeg is not installed".
`audio_detector._resolve_ffmpeg_bin()` handles this by falling back to a
directory walk of the WinGet Packages dir; `viral_harvester._ffmpeg_location()`
reuses it to pass an explicit `ffmpeg_location` to yt-dlp so that
video/audio stream merging works. If you see merge errors, verify with:

```powershell
.\.venv\Scripts\python.exe -c "from viral_harvester import _ffmpeg_location as f; print(f()[1])"


## GitHub Actions scheduler (code already committed)

A workflow is checked into `.github/workflows/viral-meme-pipeline.yml`. It runs on the same daily schedule as the Windows Task Scheduler tasks and needs only GitHub Secrets — no local machine required.

## GitHub Actions secrets (one-time setup)

On the repo **Settings → Secrets and variables → Actions**, add these four secrets:

| Secret | Value | Notes |
|---|---|---|
| `GOOGLE_DRIVE_FOLDER_ID` | `1-z91SWD6NnhG9KlRN_laTrw60dXQHyxe` | 5 TB folder ID |
| `GOOGLE_OAUTH_CLIENT_ID` | `163592659861-...` | OAuth client ID (Cloud Console → Desktop app) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | `GOCSPX-....` | OAuth client secret |
| `GOOGLE_OAUTH_TOKEN` | base64 of `google_oauth_token.json` | You can create it on any machine with: `Get-Content google_oauth_token.json -Encoding Byte \| ConvertTo-Base64` |

The workflow decodes `GOOGLE_OAUTH_TOKEN` back into `google_oauth_token.json`, so the runner refreshes itself with zero browser involvement.

> Cron runs in UTC — adjust the schedule in `.github/workflows/viral-meme-pipeline.yml` for your local timezone.

## Overflow provisioning (your 5 TB Drive)

You mentioned your Drive has plenty of storage. The current workflow uses your own Google One quota via OAuth (the default), so renders are written to `GOOGLE_DRIVE_FOLDER_ID`. If you ever want the service account to write into a shared drive instead, that requires a Workspace shared drive — outside these credentials. The current setup is fully working and self-refreshing.
```
