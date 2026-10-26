"""Viral pipeline orchestrator: harvest/download -> climax -> caption
-> 9:16 meme edit -> Drive upload.

Usage:
    python main.py --input <path_or_url>   # test a single video
    python main.py --harvest                # scrape Reddit top hits
"""

import argparse
import logging
import os
from pathlib import Path
import random
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from datetime import datetime

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")

SFX_DIR = os.path.join("assets", "sfx")
OVERLAY_DIR = os.path.join("assets", "overlays")
OUTPUT_DIR = "output"

AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")

# Harvested candidates are often multi-minute compilations. The climax is
# detected on the FULL source (so it lands on the real crash), but Whisper and
# the editor only need a few seconds either side. Sources longer than
# TRIM_THRESHOLD get windowed around the climax -- this keeps captioning and
# rendering fast and yields a Short-length result instead of minutes of
# unrelated footage in front of the freeze.
TRIM_THRESHOLD = 60.0
PRE_ROLL_SRC = 8.0   # seconds of lead-in kept before the climax
POST_ROLL = 3.0      # seconds kept after the climax


def _safe_remove(file_path: str, retries: int = 5, delay: float = 0.2) -> bool:
    """Safely delete a file on Windows, retrying on transient WinError 32 file locks."""
    if not file_path or not os.path.isfile(file_path):
        return True
    import gc
    gc.collect()
    for attempt in range(retries):
        try:
            os.remove(file_path)
            return True
        except OSError as exc:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                log.warning("Could not remove %s: %s", file_path, exc)
                return False
    return False


def _list_files(folder, exts):
    """All files in folder matching exts, or []."""
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, f) for f in os.listdir(folder)
            if f.lower().endswith(exts)
            and os.path.isfile(os.path.join(folder, f))]


def _pick_files(folder, exts, count=1):
    """Pick up to `count` DISTINCT random files from folder.

    Distinct matters: scratch and boom are different sounds, so picking the
    same file twice would play a boom on the freeze-frame. Shuffle once and
    slice, so 2 candidates can never collide; with fewer files than count,
    some slots come back None and the caller warns.
    """
    cands = _list_files(folder, exts)
    random.shuffle(cands)
    picked = cands[:count]
    return picked + [None] * (count - len(picked))


def stage_acquire(args) -> str:
    """Stage A: local file passthrough, yt-dlp download, or harvest."""
    from viral_harvester import download_candidate, download_source_video

    if args.input:
        src = args.input
        if os.path.isfile(src):
            log.info("Using local file: %s", src)
            return os.path.abspath(src)
        if src.startswith("http"):
            log.info("Downloading URL: %s", src)
            try:
                path = download_source_video(src)
                log.info("Downloaded -> %s", path)
                return path
            except Exception as exc:
                log.error("STAGE acquire FAILED (download): %s", exc)
                raise SystemExit(2)
        log.error("STAGE acquire FAILED: not a file or URL: %s", src)
        raise SystemExit(2)

    # --harvest mode: no credentials needed (Reddit JSON + YouTube search).
    log.info("Harvesting viral candidates (Reddit JSON + YouTube)...")
    try:
        from viral_harvester import get_viral_candidates
        hits = get_viral_candidates()
    except Exception as exc:
        log.error("STAGE acquire FAILED (harvest): %s", exc)
        raise SystemExit(2)
    if not hits:
        log.error("STAGE acquire FAILED: no candidates from any source "
                  "(network/Reddit/YouTube all unavailable).")
        raise SystemExit(2)
    for cand in hits:
        log.info("Candidate: [%s/%s] %s", cand.get("source", "?"),
                 cand.get("score", 0), (cand.get("title") or "")[:60])
        try:
            path = download_candidate(cand)
            log.info("Downloaded -> %s", path)
            return path
        except Exception as exc:
            log.warning("Candidate failed (%s), trying next...", exc)
            continue
    log.error("STAGE acquire FAILED: all %d candidates failed to download.",
              len(hits))
    raise SystemExit(2)


def stage_acquire_batch(args, max_items: int):
    """Acquire up to `max_items` videos for one unattended run.

    --input yields exactly one item (as before). --harvest walks the ranked
    candidate list and keeps going until `max_items` downloads succeed, so a
    flaky source or an un-downloadable clip doesn't cost the whole run.
    """
    if args.input:
        single_path = stage_acquire(args)
        if isinstance(single_path, list):
            return single_path
        return [single_path] if single_path else []

    from viral_harvester import download_candidate, get_viral_candidates

    log.info("Harvesting viral candidates (Reddit JSON + YouTube)...")
    try:
        hits = get_viral_candidates()
    except Exception as exc:
        log.error("STAGE acquire FAILED (harvest): %s", exc)
        raise SystemExit(2)
    if not hits:
        log.error("STAGE acquire FAILED: no candidates from any source "
                  "(network/Reddit/YouTube all unavailable).")
        raise SystemExit(2)

    acquired = []
    for cand in hits:
        if len(acquired) >= max_items:
            break
        log.info("Candidate: [%s/%s] %s", cand.get("source", "?"),
                 cand.get("score", 0), (cand.get("title") or "")[:60])
        try:
            path = download_candidate(cand)
        except Exception as exc:
            log.warning("Candidate failed (%s), trying next...", exc)
            continue
        log.info("Downloaded -> %s", path)
        acquired.append(path)

    if not acquired:
        log.error("STAGE acquire FAILED: all %d candidates failed to download.",
                  len(hits))
        raise SystemExit(2)
    log.info("Acquired %d/%d requested video(s).", len(acquired), max_items)
    return acquired


def stage_climax(video_path: str) -> float:
    from audio_detector import _has_audio_stream, find_climax_timestamp

    try:
        if not _has_audio_stream(video_path):
            log.warning("No audio stream: using 60%% duration fallback.")
        ts = find_climax_timestamp(video_path)
        log.info("Climax timestamp: %.3fs", ts)
        return ts
    except Exception as exc:
        log.error("STAGE climax FAILED: %s", exc)
        raise SystemExit(3)


def stage_trim(video_path: str, climax: float):
    """Stage B2: window long sources around the climax.

    Harvested candidates are often multi-minute compilations. The freeze and
    caption must sit at the real climax (so we detect it on the FULL source),
    but Whisper + the editor only need a few seconds either side. Trimming
    here keeps captioning and rendering fast and yields a Short-length result
    instead of minutes of unrelated footage.

    Returns (working_path, working_climax). Falls back to the full source if
    duration is unavailable or the ffmpeg trim fails.
    """
    from audio_detector import _get_duration_seconds, _resolve_ffmpeg_bin

    total = _get_duration_seconds(video_path)
    if not total or total <= TRIM_THRESHOLD:
        return video_path, climax

    start = max(0.0, climax - PRE_ROLL_SRC)
    length = min((climax + POST_ROLL) - start, total - start)
    if length <= 0.1:
        return video_path, climax

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    trimmed = os.path.abspath(os.path.join("downloads", f"clip_{stamp}.mp4"))
    cmd = [
        _resolve_ffmpeg_bin("ffmpeg"), "-y", "-nostdin", "-ss", f"{start:.3f}",
        "-i", video_path, "-t", f"{length:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", trimmed,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    except Exception as exc:
        log.warning("Trim failed (%s); using full source.", exc)
        return video_path, climax

    if not os.path.isfile(trimmed):
        log.warning("Trim produced no file; using full source.")
        return video_path, climax

    meta_src = os.path.splitext(video_path)[0] + ".meta.json"
    meta_dst = os.path.splitext(trimmed)[0] + ".meta.json"
    if os.path.isfile(meta_src):
        try:
            import shutil
            shutil.copy2(meta_src, meta_dst)
        except OSError:
            pass

    log.info("Trimmed %.1fs source to %.1fs window (%.1fs..%.1fs) -> %s",
             total, length, start, start + length, os.path.basename(trimmed))
    return trimmed, climax - start


def stage_caption(video_path: str, climax: float) -> tuple[str, str]:
    from caption_generator import get_freeze_caption
    from video_editor import HOOK_TEMPLATES

    try:
        cap = get_freeze_caption(video_path, climax)
        log.info("Caption: %s", cap)
        hook = random.choice(HOOK_TEMPLATES) if HOOK_TEMPLATES else "Wait for it"
        log.info("Hook text: %s", hook)
        return cap, hook
    except Exception as exc:
        log.error("STAGE caption FAILED: %s", exc)
        raise SystemExit(4)


def stage_render(video_path, climax, caption, credit=None, hook_text=None, voiceover_path=None) -> str:
    from video_editor import assemble_meme_edit_with_freeze_and_caption

    meta_path = os.path.splitext(video_path)[0] + ".meta.json"
    meta = {}
    if os.path.isfile(meta_path):
        try:
            import json
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass

    credit = credit or meta.get("credit") or "Credit: Original Creator"
    if credit:
        log.info("Attribution: %s", credit)

    # Categorized SFX: suspense/pause sound on freeze, impact punch sound on resume crash
    FREEZE_KEYWORDS = ("scratch", "freeze", "error", "wow", "bruh", "dun")
    IMPACT_KEYWORDS = ("boom", "bonk", "pipe", "fart", "trombone", "dog")

    all_sfx = _list_files(SFX_DIR, AUDIO_EXTS)
    freeze_candidates = [f for f in all_sfx if any(k in os.path.basename(f).lower() for k in FREEZE_KEYWORDS)] or all_sfx
    impact_candidates = [f for f in all_sfx if any(k in os.path.basename(f).lower() for k in IMPACT_KEYWORDS)] or all_sfx

    scratch = random.choice(freeze_candidates) if freeze_candidates else None
    remaining_impact = [f for f in impact_candidates if f != scratch] or impact_candidates
    boom = random.choice(remaining_impact) if remaining_impact else None

    # Pick 1 random overlay cutout out of all available meme emojis/graphics
    (overlay,) = _pick_files(OVERLAY_DIR, (".png", ".jpg", ".jpeg", ".webp"), count=1)
    log.info("SFX scratch=%s boom=%s overlay=%s", scratch, boom, overlay)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.abspath(os.path.join(OUTPUT_DIR, f"meme_{stamp}.mp4"))
    try:
        assemble_meme_edit_with_freeze_and_caption(
            video_path, climax, caption, scratch, boom, overlay, out,
            credit_text=credit, hook_text=hook_text, voiceover_path=voiceover_path)
        log.info("Rendered -> %s", out)
        return out
    except Exception as exc:
        log.error("STAGE render FAILED: %s", exc)
        raise SystemExit(5)


def stage_upload(mp4_path: str, keep_local: bool = False):
    from drive_uploader import upload_video_to_drive

    try:
        fid, link = upload_video_to_drive(mp4_path, keep_local=keep_local)
        if fid:
            log.info("Drive upload OK: id=%s link=%s", fid, link)
        else:
            log.info("Drive upload skipped (no creds); kept %s", mp4_path)
        return fid, link
    except Exception as exc:
        log.error("STAGE upload FAILED: %s (local file kept)", exc)
        return None, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Viral meme pipeline")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--input", help="Local video path or direct video URL")
    g.add_argument("--harvest", action="store_true",
                   help="Scrape top viral hits and process them")
    ap.add_argument("--max", type=int, default=1, metavar="N",
                    help="Max videos to produce in this run (harvest mode "
                         "only; default 1). Use --max 3 for 3 per run.")
    ap.add_argument("--keep-local", action="store_true",
                    help="Keep the local render after a successful upload")
    args = ap.parse_args(argv)

    t0 = time.time()
    log.info("=== PIPELINE START (%s) ===",
             f"input={args.input}" if args.input
             else f"harvest max={args.max}")

    # (a) acquire -- a list so one scheduled run can make several videos.
    sources = stage_acquire_batch(args, max(1, args.max))

    ok, failed = 0, 0
    links = []
    for idx, video in enumerate(sources, start=1):
        if len(sources) > 1:
            log.info("--- item %d/%d: %s ---", idx, len(sources),
                     os.path.basename(video))
        work = video  # intermediate trim path; distinct from source when trimmed
        vo_path = None
        try:
            climax = stage_climax(video)              # (b) crash timestamp
            work, climax = stage_trim(video, climax)  # (b2) window long clips
            caption_res = stage_caption(work, climax) # (c) punchline caption & hook
            if isinstance(caption_res, (tuple, list)):
                caption = caption_res[0]
                hook_text = caption_res[1] if len(caption_res) > 1 else None
            else:
                caption = caption_res
                hook_text = None

            if not hook_text:
                from video_editor import HOOK_TEMPLATES
                hook_text = random.choice(HOOK_TEMPLATES) if HOOK_TEMPLATES else "Wait for it"

            try:
                import tts_generator
                vo_path, vo_dur = tts_generator.synthesize_hook_voiceover(hook_text)
                log.info("Voiceover synthesized (%.2fs) -> %s", vo_dur, vo_path)
            except Exception as vo_exc:
                log.warning("Voiceover synthesis failed (%s); continuing without VO", vo_exc)
                vo_path = None

            final = stage_render(work, climax, caption, hook_text=hook_text, voiceover_path=vo_path)  # (d+e) 9:16 edit
            fid, link = stage_upload(final,
                                     keep_local=args.keep_local)  # (f)
            if link:
                links.append(link)
            ok += 1
        except SystemExit as exc:
            # A stage already logged a clean diagnostic for this item.
            failed += 1
            log.error("item %d aborted (exit %s); continuing with next.",
                      idx, exc.code)
        except Exception as exc:
            failed += 1
            log.error("item %d aborted with unhandled error (%s); continuing with next.",
                      idx, exc)
        finally:
            if vo_path:
                _safe_remove(vo_path)
            # Remove the intermediate trimmed copy safely (the download stays for re-runs).
            if work != video:
                _safe_remove(work)
                _safe_remove(os.path.splitext(work)[0] + ".meta.json")
            # Downloads are only worth keeping for --input debugging; in harvest
            # mode they would accumulate until the disk fills.
            if args.harvest:
                _safe_remove(video)
                _safe_remove(os.path.splitext(video)[0] + ".meta.json")

    log.info("=== PIPELINE DONE in %.1fs: %d ok, %d failed ===",
             time.time() - t0, ok, failed)
    for link in links:
        log.info("  uploaded: %s", link)
    # Partial success is still success for a scheduled run: one dead clip
    # shouldn't mark the whole batch failed.
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
