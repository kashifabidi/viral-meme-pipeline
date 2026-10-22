"""Audio climax detection for viral video pipeline.

Uses FFmpeg (via subprocess) to extract audio to a temporary WAV file,
then uses librosa to compute the RMS energy envelope and locate the
loudest sound/crash spike inside a search window.

Usage:
    from audio_detector import find_climax_timestamp
    ts = find_climax_timestamp("downloads/raw.mp4")
"""

import os
import shutil
import subprocess
import tempfile

try:
    import librosa
except ImportError:
    librosa = None
import numpy as np


def _resolve_ffmpeg_bin(name: str) -> str:
    """Resolve ffmpeg/ffprobe absolute path (WinGet shim or imageio-ffmpeg)."""
    found = shutil.which(name)
    if found:
        return found
    # Fallback: imageio_ffmpeg binary
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if os.path.isfile(exe):
            d = os.path.dirname(exe)
            std = os.path.join(d, f"{name}.exe")
            if os.path.isfile(std):
                return std
            scripts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(d))), "Scripts")
            script_exe = os.path.join(scripts_dir, f"{name}.exe")
            if os.path.isfile(script_exe):
                return script_exe
            return exe
    except Exception:
        pass
    # Fallback: Gyan.FFmpeg WinGet package layout.
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        "Microsoft", "WinGet", "Packages")
    try:
        for entry in os.listdir(base):
            if "Gyan.FFmpeg" in entry:
                cand = os.path.join(base, entry)
                for root, _dirs, files in os.walk(cand):
                    if f"{name}.exe" in files:
                        return os.path.join(root, f"{name}.exe")
    except OSError:
        pass
    return name  # last resort: rely on PATH


FFMPEG = _resolve_ffmpeg_bin("ffmpeg")
FFPROBE = _resolve_ffmpeg_bin("ffprobe")


def _get_duration_seconds(video_path: str) -> float:
    """Return media duration in seconds via ffprobe or ffmpeg, 0.0 on failure."""
    try:
        out = subprocess.run(
            [
                FFPROBE,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        val = float(out.stdout.strip())
        if val > 0:
            return val
    except Exception:
        pass

    # Fallback via FFMPEG -i output
    try:
        out = subprocess.run(
            [FFMPEG, "-i", video_path],
            capture_output=True,
            text=True,
            check=False,
        )
        import re
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", out.stderr)
        if match:
            h, m, s = match.groups()
            return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        pass
    return 0.0


def _has_audio_stream(video_path: str) -> bool:
    """True if video has an audio stream (via ffprobe or ffmpeg)."""
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, check=False,
        )
        if out.stdout.strip():
            return True
    except Exception:
        pass
    try:
        out = subprocess.run(
            [FFMPEG, "-i", video_path],
            capture_output=True, text=True, check=False,
        )
        return "Audio:" in out.stderr
    except Exception:
        return False


def find_climax_timestamp(
    video_path: str,
    search_window: tuple = (0.35, 0.95),
    lead_time_offset: float = 0.08,
) -> float:
    """Detect the loudest sound/crash spike in a video.

    Args:
        video_path: Path to the source video file.
        search_window: (start_frac, end_frac) fractions of total duration
            to search inside, so intros/outros are ignored.
        lead_time_offset: Seconds to step BACK from the detected peak so
            the freeze-frame lands just before the crash (better comedic
            timing).

    Returns:
        Timestamp in seconds of the climax (peak minus lead offset,
        clamped to >= 0.0). For silent videos (no audio stream) falls
        back to 60% of duration so the meme flow still works.

    Raises:
        FileNotFoundError: If video_path does not exist.
        RuntimeError: If FFmpeg audio extraction fails.
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    if not _has_audio_stream(video_path):
        dur = _get_duration_seconds(video_path)
        fallback = round(dur * 0.6, 3) if dur > 0 else 0.0
        return fallback

    start_frac, end_frac = search_window
    if not (0.0 <= start_frac < end_frac <= 1.0):
        raise ValueError(
            f"search_window must satisfy 0 <= start < end <= 1, got {search_window}"
        )

    # 1. Extract audio to a temporary mono WAV via FFmpeg.
    fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        proc = subprocess.run(
            [
                FFMPEG,
                "-y",
                "-v", "error",
                "-i", video_path,
                "-vn",
                "-ac", "1",          # mono: simpler energy envelope
                "-ar", "22050",      # librosa-friendly sample rate
                "-f", "wav",
                tmp_wav,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not os.path.isfile(tmp_wav):
            raise RuntimeError(
                f"FFmpeg audio extraction failed for {video_path}: "
                f"{proc.stderr.strip()[-500:]}"
            )

        # 2. Load + compute RMS energy envelope with librosa.
        # NOTE: soundfile read at native rate (FFmpeg already output
        # 22050 Hz mono) avoids librosa.load's soxr resample path, which
        # needs the VC++ runtime DLL on some Windows boxes.
        import soundfile as sf
        y, sr = sf.read(tmp_wav, dtype="float32", always_2d=False)
        y = np.asarray(y, dtype=np.float32).ravel()
        if y.size == 0:
            return 0.0

        hop_length = 512
        rms = None
        if librosa is not None:
            try:
                rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
            except Exception:
                rms = None
        if rms is None:
            # Fallback: pure-numpy RMS envelope (no numba/llvmlite needed).
            # Pads tail so framing matches librosa's center=True default.
            n = y.shape[0]
            n_frames = 1 + n // hop_length
            padded = np.pad(y, (0, n_frames * hop_length - n))
            frames = padded[: n_frames * hop_length].reshape(n_frames,
                                                             hop_length)
            rms = np.sqrt(np.mean(frames ** 2, axis=1))
        if rms.size == 0:
            return 0.0

        # Frame -> time in seconds (plain arithmetic; avoids numba-backed
        # librosa.frames_to_time on machines missing the VC++ runtime).
        frame_times = np.arange(rms.size, dtype=np.float64) * hop_length / float(sr)

        # 3. Restrict to search window fractions of duration.
        duration = _get_duration_seconds(video_path)
        if duration <= 0:
            duration = float(frame_times[-1]) if frame_times.size else 0.0
        if duration <= 0:
            return 0.0

        win_start = duration * start_frac
        win_end = duration * end_frac
        mask = (frame_times >= win_start) & (frame_times <= win_end)
        if not np.any(mask):
            mask = np.ones_like(frame_times, dtype=bool)

        windowed_rms = rms.copy()
        windowed_rms[~mask] = -1.0  # exclude everything outside window

        # 4. Loudest spike = argmax of RMS inside window.
        peak_idx = int(np.argmax(windowed_rms))
        peak_time = float(frame_times[peak_idx])

        # 5. Step back slightly so the freeze lands just before impact.
        climax = max(0.0, peak_time - lead_time_offset)
        return round(climax, 3)
    finally:
        try:
            if os.path.isfile(tmp_wav):
                os.remove(tmp_wav)
        except OSError:
            pass


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python audio_detector.py <video_path>")
        raise SystemExit(1)
    print(find_climax_timestamp(sys.argv[1]))
