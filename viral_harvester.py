"""Viral video ingestion: zero-auth Reddit JSON + YouTube search harvest.

No API keys or credentials required for either source.

Usage:
    from viral_harvester import (get_viral_candidates, download_source_video)
    hits = get_viral_candidates()
    path = download_source_video(hits[0]["url"])

Sources:
    1. Reddit public .json endpoints (browser User-Agent, no OAuth).
    2. YouTube via yt-dlp's built-in "ytsearchN:" query support.
"""

import os
import shutil
import time

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_SUBREDDITS = [
    "funny",
    "Unexpected",
    "ContagiousLaughter",
    "maybemaybemaybe",
    "Whatcouldgowrong",
    "therewasanattempt",
    "AnimalsBeingDerps",
    "holdmybeer",
    "IdiotsInCars",
    "facepalm",
]
DEFAULT_YT_QUERIES = [
    # Creative Commons / Open reuse funny clips (attribution-friendly)
    "funny fails creative commons",
    "funny animal fails creative commons",
    "funny accidents moments creative commons",
    "unexpected funny fails creative commons",
    "try not to laugh creative commons",
    "meme clips creative commons",
    # Viral funny & comedy shorts
    "funny fail shorts",
    "instant regret funny shorts",
    "perfectly cut screams funny",
    "instant karma funny fails",
    "animals being derps funny shorts",
]

# Reddit returns 403 on public .json for many datacenter/cloud IPs (and to
# generic clients). A browser UA helps but is not a guarantee. When a request
# is blocked we say so explicitly rather than implying the subreddit is empty.
REDDIT_BLOCKED_HINT = (
    "Reddit returned 403 (blocked). Public .json is IP-sensitive -- retry "
    "from a residential connection or VPN, or rely on --harvest's YouTube "
    "source which needs no credentials.")

# Reddit rate-limits/403s generic clients (python-requests, curl, empty) on
# public .json endpoints. A browser-like UA with a contact string reads as a
# normal visitor and keeps harvesting working without an API key.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")
_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}
_REDDIT_BASE = "https://www.reddit.com/r"


def _http_get_json(url: str, params=None, retries: int = 3, timeout: int = 20):
    """GET a JSON document, retrying transient failures. Raises on hard fail.

    403/404 are terminal (retrying won't help); 429/5xx/network errors back
    off before the next attempt.
    """
    last_err = "unknown error"
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=_HEADERS,
                                timeout=timeout)
        except requests.RequestException as exc:
            last_err = str(exc)
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"{url} returned non-JSON body: {exc}") from exc
        last_err = f"HTTP {resp.status_code}"
        if resp.status_code in (403, 404):
            break
        if resp.status_code == 429:  # rate limited: wait longer
            time.sleep(3.0 * (attempt + 1))
            continue
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after {retries} tries: {last_err}")


def _normalize_reddit_hit(data: dict, subreddit: str):
    """Turn a raw Reddit post payload into a hit dict, or None if unusable."""
    permalink = data.get("permalink") or ""
    if not permalink:
        return None

    # Native Reddit video: is_video flag, or a v.redd.it media block.
    media = data.get("media") or data.get("secure_media") or {}
    reddit_video = (media.get("reddit_video") or {}) if isinstance(media, dict) else {}
    fallback_url = reddit_video.get("fallback_url") if reddit_video else None

    is_native = bool(data.get("is_video")) or "v.redd.it" in (fallback_url or "")
    if not is_native:
        return None

    score = int(data.get("score") or 0)
    author = (data.get("author") or "").strip()
    if author and author.lower() not in ("[deleted]", "[removed]"):
        credit = f"Credit: u/{author} (r/{subreddit})"
    else:
        credit = f"Credit: r/{subreddit}"

    return {
        "id": data.get("id") or "",
        "source": "reddit",
        "subreddit": subreddit,
        "author": author,
        "title": (data.get("title") or "").strip(),
        "score": score,
        "url": f"https://www.reddit.com{permalink}",
        "fallback_url": fallback_url or "",
        "permalink": f"https://reddit.com{permalink}",
        "duration": 0.0,
        "views": score,
        "license": "Reddit Community",
        "credit": credit,
    }



def get_all_time_reddit_hits(
    subreddits=None,
    min_score: int = 25000,
    limit: int = 20,
) -> list:
    """Fetch all-time top viral *native video* posts via public JSON.

    No credentials needed -- reads the same .json endpoints the website uses.

    Args:
        subreddits: Subreddit names to scan (defaults to the fail trio).
        min_score: Minimum post score to qualify as a hit.
        limit: Max top posts to scan per subreddit.

    Returns:
        List of hit dicts sorted by score desc. Subreddits that fail are
        skipped; an all-fail run returns [].
    """
    subs = subreddits or DEFAULT_SUBREDDITS
    hits = []
    for name in subs:
        url = f"{_REDDIT_BASE}/{name}/top.json"
        try:
            payload = _http_get_json(url, params={"t": "all", "limit": limit})
        except Exception as exc:
            print(f"[harvester] r/{name} failed: {exc}")
            if "403" in str(exc):
                print(f"[harvester] {REDDIT_BLOCKED_HINT}")
                break  # IP-level block: don't burn retries on every sub
            continue

        children = ((payload or {}).get("data") or {}).get("children") or []
        kept = 0
        for child in children:
            data = child.get("data") or {}
            if int(data.get("score") or 0) < min_score:
                continue
            hit = _normalize_reddit_hit(data, name)
            if hit is None:
                continue
            hits.append(hit)
            kept += 1
        print(f"[harvester] r/{name}: {kept} video hit(s) >= {min_score}")
        # Stay polite to Reddit's unauthenticated rate limits.
        time.sleep(1.5)

    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits


def get_youtube_hits(
    queries=None,
    min_views: int = 1000000,
    min_duration: int = 5,
    max_duration: int = 30,
    limit: int = 25,
    relax: bool = True,
) -> list:
    """Search YouTube for proven short hits via yt-dlp (no API key needed).

    Args:
        queries: Search strings (defaults to the fail/shorts presets).
        min_views: Minimum view count.
        min_duration / max_duration: Inclusive clip length window, seconds.
        limit: Results fetched per query.
        relax: If True and the strict window yields nothing, progressively
            widen the duration window and lower the view floor. Real YouTube
            search returns far more long compilations than true Shorts, so a
            strict-only pass frequently matches zero clips.

    Returns:
        List of hit dicts sorted by views desc. Failed queries are skipped.
    """
    from yt_dlp import YoutubeDL

    # Tier 1 is the requested threshold; later tiers only run if earlier ones
    # matched nothing.
    tiers = [(min_views, min_duration, max_duration)]
    if relax:
        # Short + massively popular almost never co-occur in relevance-ranked
        # search: the popular results are long compilations and the short
        # ones are tiny uploads (measured: best 5-30s clip had 57 views). So
        # widen duration first, then finally accept a proven popular longer
        # clip -- the editor's pre_roll trims it to the crash window.
        tiers += [
            (min_views, min_duration, 60),
            (max(100_000, min_views // 10), min_duration, 60),
            (max(50_000, min_views // 20), 3, 90),
            (max(1_000_000, min_views), 30, 720),  # proven popular, <=12 min
        ]

    # Fetch every query ONCE, then apply tiers to the cached pool. Re-running
    # yt-dlp per tier would mean ~4x as many network round-trips for the same
    # results -- tiers are just different filters over the same search data.
    search_terms = queries or DEFAULT_YT_QUERIES
    # extract_flat=True is ~50x faster (1s vs 60s+ per query) and STILL
    # returns duration + view_count on ytsearch results -- verified locally.
    # ignoreerrors=True stops one unavailable video from aborting the whole
    # result page.
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": True,
        "ignoreerrors": True,
        "socket_timeout": 20,
    }

    pool = []
    with YoutubeDL(opts) as ydl:
        for term in search_terms:
            try:
                info = ydl.extract_info(f"ytsearch{limit}:{term}",
                                        download=False)
            except Exception as exc:
                print(f"[harvester] YouTube '{term}' failed: {exc}")
                continue
            got = [e for e in (info or {}).get("entries") or [] if e]
            print(f"[harvester] YouTube '{term}': {len(got)} results")
            pool.extend(got)

    if not pool:
        print("[harvester] YouTube: no results at all")
        return []

    with_duration = [e for e in pool if e.get("duration")]
    print(f"[harvester] YouTube: {len(pool)} total results, "
          f"{len(with_duration)} with duration data")

    for tier_views, tier_min_d, tier_max_d in tiers:
        hits = []
        for entry in with_duration:
            duration = entry.get("duration") or 0
            views = entry.get("view_count") or 0
            if not (tier_min_d <= duration <= tier_max_d):
                continue
            if views < tier_views:
                continue
            webpage_url = (
                entry.get("url") or entry.get("webpage_url")
                or (f"https://www.youtube.com/watch?v={entry['id']}"
                    if entry.get("id") else ""))
            if not webpage_url:
                continue
            uploader = (entry.get("uploader") or entry.get("channel") or "").strip()
            license_type = (entry.get("license") or "").strip()
            title = (entry.get("title") or "").strip()
            is_cc = (
                "creative commons" in license_type.lower()
                or "creative commons" in title.lower()
                or "cc-by" in title.lower()
                or "cc by" in title.lower()
            )
            if uploader:
                credit = f"Credit: @{uploader} (CC-BY)" if is_cc else f"Credit: @{uploader}"
            else:
                credit = "Credit: Original Creator"

            hits.append({
                "id": entry.get("id") or "",
                "source": "youtube",
                "subreddit": "",
                "uploader": uploader,
                "license": license_type or ("Creative Commons" if is_cc else "Standard"),
                "credit": credit,
                "title": title,
                "score": int(views),
                "url": webpage_url,
                "fallback_url": "",
                "permalink": webpage_url,
                "duration": float(duration),
                "views": int(views),
            })

        print(f"[harvester] YouTube: {len(hits)} hit(s) at "
              f"{tier_min_d}-{tier_max_d}s / >= {tier_views:,} views")
        if hits:
            if tier_views != min_views or tier_min_d != min_duration:
                print(f"[harvester] YouTube: strict window matched nothing; "
                      f"relaxed to {tier_min_d}-{tier_max_d}s / "
                      f"{tier_views:,} views")
            hits.sort(key=lambda h: h["views"], reverse=True)
            return hits

    print("[harvester] YouTube: all thresholds exhausted, 0 hits")
    return []


def get_viral_candidates(subreddits=None, queries=None) -> list:
    """Harvest both sources and merge into one ranked candidate list.

    Reddit scores and YouTube view counts are different scales, so the two
    ranked lists are interleaved rather than concatenated -- that stops one
    dead source from silently burying the other's results. Returns [] only if
    every source failed.
    """
    reddit = get_all_time_reddit_hits(subreddits=subreddits)
    youtube = get_youtube_hits(queries=queries)

    candidates = []
    for reddit_hit, yt_hit in zip(reddit, youtube):
        candidates.extend((reddit_hit, yt_hit))
    # Whichever source had more entries appends its tail.
    candidates.extend(reddit[len(youtube):])
    candidates.extend(youtube[len(reddit):])
    return candidates


def download_candidate(hit: dict, output_folder: str = "downloads") -> str:
    """Download a harvested hit, trying its primary URL then any fallback."""
    last_err = None
    for url in (hit.get("url") or "", hit.get("fallback_url") or ""):
        if not url:
            continue
        try:
            return download_source_video(url, output_folder, hit_metadata=hit)
        except Exception as exc:
            last_err = exc
            print(f"[harvester] download failed for {url}: {exc}")
    raise RuntimeError(
        f"all URLs failed for '{hit.get('title')}': {last_err}")


def _ffmpeg_location():
    """Absolute path to ffmpeg's directory, for yt-dlp's ffmpeg_location.

    yt-dlp shells out to ffmpeg to mux separate video/audio streams, and it
    can only find ffmpeg via PATH. Winget installs FFmpeg into its Packages
    dir without always adding it to PATH, so a fresh shell sees "ffmpeg is
    not installed" even though it is. audio_detector already solves this for
    subprocess calls -- reuse it and hand yt-dlp the explicit directory.
    """
    try:
        from audio_detector import _resolve_ffmpeg_bin
        ffmpeg_path = _resolve_ffmpeg_bin("ffmpeg")
    except Exception:
        ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"
    # yt-dlp wants the directory containing the binaries.
    if os.path.isfile(ffmpeg_path):
        return os.path.dirname(ffmpeg_path), ffmpeg_path
    return None, ffmpeg_path


def download_source_video(url: str, output_folder: str = "downloads", hit_metadata: dict = None) -> str:
    """Download highest-quality single MP4 via yt-dlp, return local path.

    Reddit/YouTube serve video-only and audio-only streams, so bv*+ba/b pairs
    and merges them (needs ffmpeg); plain progressive MP4s fall back to the
    best single file.

    Raises FileNotFoundError / RuntimeError on failure.
    """
    if not url:
        raise ValueError("download_source_video: empty url")
    os.makedirs(output_folder, exist_ok=True)

    from yt_dlp import YoutubeDL

    ffmpeg_dir, ffmpeg_bin = _ffmpeg_location()
    if not os.path.isfile(ffmpeg_bin):
        print(f"[harvester] WARNING: ffmpeg not found at {ffmpeg_bin!r}; "
              "sources needing audio/video merging may fail. Install with: "
              "winget install --id Gyan.FFmpeg -e")
    template = os.path.join(output_folder, "%(id)s.%(ext)s")
    opts = {
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            raise RuntimeError(f"yt-dlp returned no info for {url}")
        # For playlists/chapters take the first real entry.
        if "entries" in info and info["entries"]:
            info = next((e for e in info["entries"] if e), info)
        path = ydl.prepare_filename(info)
        # merge may change ext to mp4.
        if not os.path.isfile(path):
            alt = os.path.splitext(path)[0] + ".mp4"
            path = alt if os.path.isfile(alt) else path
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Download finished but file missing: {path}")

        meta_path = os.path.splitext(path)[0] + ".meta.json"
        uploader = ((hit_metadata or {}).get("uploader")
                    or (hit_metadata or {}).get("author")
                    or info.get("uploader")
                    or info.get("channel") or "")
        lic = (hit_metadata or {}).get("license") or info.get("license") or ""
        is_cc = "creative commons" in str(lic).lower() or "cc" in (info.get("title") or "").lower()
        if (hit_metadata or {}).get("credit"):
            credit = hit_metadata["credit"]
        elif uploader:
            credit = f"Credit: @{uploader} (CC-BY)" if is_cc else f"Credit: @{uploader}"
        else:
            credit = "Credit: Original Creator"

        meta = {
            "id": info.get("id") or (hit_metadata or {}).get("id") or "",
            "title": info.get("title") or (hit_metadata or {}).get("title") or "",
            "uploader": uploader,
            "license": lic or ("Creative Commons" if is_cc else "Standard"),
            "credit": credit,
            "url": url,
            "source": (hit_metadata or {}).get("source") or ("youtube" if "youtube" in url or "youtu.be" in url else "web"),
        }
        try:
            import json
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
        except Exception:
            pass

        return os.path.abspath(path)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1].startswith("http"):
        print(download_source_video(sys.argv[1]))
    else:
        for hit in get_viral_candidates():
            print(hit["source"], hit["score"], hit["views"],
                  f"{hit['duration']:.1f}s", hit["title"][:60], hit["url"])
