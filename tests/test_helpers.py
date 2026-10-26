"""Shared test utilities, fixtures, and media generators for E2E tests."""

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, Optional, Tuple


def get_ffmpeg_bin() -> str:
    """Resolve ffmpeg binary reliably."""
    try:
        from audio_detector import _resolve_ffmpeg_bin
        return _resolve_ffmpeg_bin("ffmpeg")
    except Exception:
        found = shutil.which("ffmpeg")
        return found or "ffmpeg"


def get_ffprobe_bin() -> str:
    """Resolve ffprobe binary reliably."""
    try:
        from audio_detector import _resolve_ffmpeg_bin
        return _resolve_ffmpeg_bin("ffprobe")
    except Exception:
        found = shutil.which("ffprobe")
        return found or "ffprobe"


def create_synthetic_video(
    output_path: str,
    duration: float = 2.0,
    width: int = 640,
    height: int = 360,
    fps: int = 30,
    has_audio: bool = True,
    color: str = "blue",
    freq: int = 440,
) -> str:
    """Generate a lightweight synthetic MP4 video in milliseconds using ffmpeg."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    ffmpeg = get_ffmpeg_bin()

    cmd = [
        ffmpeg,
        "-y",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={width}x{height}:r={fps}:d={duration}",
    ]

    if has_audio:
        cmd.extend([
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:sample_rate=44100:duration={duration}",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
        ])
    else:
        cmd.append("-an")

    cmd.extend([
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-t",
        str(duration),
        output_path,
    ])

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not os.path.isfile(output_path):
        raise RuntimeError(f"FFmpeg synthetic video generation failed: {proc.stderr[:400]}")
    return os.path.abspath(output_path)


def create_synthetic_audio(
    output_path: str,
    duration: float = 1.0,
    sample_rate: int = 44100,
    freq: int = 440,
) -> str:
    """Generate a lightweight synthetic WAV or MP3 audio file."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    ffmpeg = get_ffmpeg_bin()

    codec = "libmp3lame" if output_path.lower().endswith(".mp3") else "pcm_s16le"
    cmd = [
        ffmpeg,
        "-y",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:sample_rate={sample_rate}:duration={duration}",
        "-c:a",
        codec,
        "-t",
        str(duration),
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not os.path.isfile(output_path):
        raise RuntimeError(f"FFmpeg audio generation failed: {proc.stderr[:400]}")
    return os.path.abspath(output_path)


def probe_media(file_path: str) -> Dict[str, Any]:
    """Inspect media file using ffmpeg -i and return metadata dictionary."""
    import re
    ffmpeg = get_ffmpeg_bin()
    cmd = [ffmpeg, "-nostdin", "-i", file_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    stderr = proc.stderr or ""

    duration = 0.0
    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
    if dur_match:
        h, m, s = dur_match.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)

    width, height = None, None
    dim_match = re.search(r",\s*(\d{2,5})x(\d{2,5})\b", stderr)
    if dim_match:
        width, height = int(dim_match.group(1)), int(dim_match.group(2))

    fps = 0.0
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", stderr)
    if fps_match:
        fps = float(fps_match.group(1))

    has_video = "Video:" in stderr
    has_audio = "Audio:" in stderr

    return {
        "width": width,
        "height": height,
        "fps": round(fps, 2),
        "duration": duration,
        "has_video": has_video,
        "has_audio": has_audio,
        "stderr": stderr,
    }


def create_sidecar_meta(
    video_path: str,
    credit: str = "Test Creator",
    title: str = "Test Video Title",
    source: str = "youtube",
    url: str = "https://example.com/video",
) -> str:
    """Create a sidecar .meta.json file matching the video."""
    meta_path = os.path.splitext(video_path)[0] + ".meta.json"
    data = {
        "credit": credit,
        "title": title,
        "source": source,
        "url": url,
        "timestamp": 1234567890.0,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return meta_path


@contextlib.contextmanager
def isolated_env(**kwargs):
    """Context manager to set environment variables temporarily."""
    old_env = {}
    for k, v in kwargs.items():
        old_env[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    try:
        yield
    finally:
        for k, old_val in old_env.items():
            if old_val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old_val


@contextlib.contextmanager
def temp_workspace():
    """Context manager providing a temporary working directory."""
    temp_dir = tempfile.mkdtemp(prefix="meme_test_")
    try:
        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
