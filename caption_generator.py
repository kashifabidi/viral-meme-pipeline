"""Freeze-frame caption generation via local OpenAI Whisper.

Transcribes audio up to the climax timestamp, extracts the last spoken
phrase before the crash, formats it with quotes, and falls back to a
witty comedic line when no dialogue is found.

Usage:
    from caption_generator import get_freeze_caption
    caption = get_freeze_caption("downloads/raw.mp4", climax_time=8.42)
"""

import os
import random
import shutil
import subprocess
import tempfile


def _ffmpeg_bin() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        "Microsoft", "WinGet", "Packages")
    try:
        for entry in os.listdir(base):
            if "Gyan.FFmpeg" in entry:
                for root, _d, files in os.walk(os.path.join(base, entry)):
                    if "ffmpeg.exe" in files:
                        return os.path.join(root, "ffmpeg.exe")
    except OSError:
        pass
    return "ffmpeg"


FFMPEG = _ffmpeg_bin()

FALLBACK_CAPTIONS = [
    '"It was at this moment..."',
    '"Calculated risk."',
    '"Mistakes were made."',
    '"That seemed like a good idea."',
    '"Nothing could possibly go wrong."',
    '"Confidence: 100%. Outcome: 0%."',
    '"Task failed successfully."',
    '"Physics has entered the chat."',
]


def _extract_audio_snippet(video_path: str, up_to_seconds: float) -> str:
    """Extract audio from 0..up_to_seconds to a temp WAV, return its path."""
    fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    proc = subprocess.run(
        [
            FFMPEG,
            "-y",
            "-v", "error",
            "-i", video_path,
            "-vn",
            "-ac", "1",
            "-ar", "16000",  # whisper-native sample rate
            "-t", str(max(0.1, up_to_seconds)),
            "-f", "wav",
            tmp_wav,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not os.path.isfile(tmp_wav):
        raise RuntimeError(
            f"FFmpeg snippet extraction failed: {proc.stderr.strip()[-500:]}"
        )
    return tmp_wav


def _last_phrase_before_climax(segments, climax_time: float,
                               lookback: float = 8.0) -> str:
    """Pick the last meaningful segment text ending at/before climax.

    Only considers segments ending within `lookback` seconds before the
    climax so stale intro dialogue is ignored.
    """
    candidates = []
    for seg in segments or []:
        text = (seg.get("text") or "").strip()
        end = float(seg.get("end", 0.0))
        if not text:
            continue
        if end <= climax_time + 0.25 and end >= climax_time - lookback:
            candidates.append((end, text))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0])
    phrase = candidates[-1][1].strip()
    # Keep captions short: last sentence if the model rambles.
    for sep in ("...", ". ", "! ", "? "):
        if sep in phrase:
            phrase = phrase.split(sep)[-1].strip().rstrip(".!?")
            break
    return phrase.strip().rstrip(".!?,;:")


def get_freeze_caption(audio_path: str, climax_time: float) -> str:
    """Transcribe audio up to climax and return a quoted freeze caption.

    Args:
        audio_path: Path to source video (or audio) file. Audio is
            extracted up to `climax_time` before transcription.
        climax_time: Climax timestamp in seconds (from audio_detector).

    Returns:
        Caption string wrapped in double quotes. Falls back to a random
        witty line when transcription yields no usable dialogue.
    """
    if not os.path.isfile(audio_path):
        return random.choice(FALLBACK_CAPTIONS)

    climax_time = max(0.0, float(climax_time))
    tmp_wav = None
    try:
        # If caller passed raw audio, use it directly when climax covers it.
        _, ext = os.path.splitext(audio_path.lower())
        if ext in (".wav", ".mp3", ".m4a", ".flac", ".ogg"):
            snippet = audio_path
        else:
            tmp_wav = _extract_audio_snippet(audio_path, climax_time)
            snippet = tmp_wav

        import whisper

        model = whisper.load_model("base", fp16=False)
        # fp16=False keeps CPU inference stable on Windows.
        result = model.transcribe(snippet, fp16=False, language="en")
        phrase = _last_phrase_before_climax(
            result.get("segments", []), climax_time
        )
        if not phrase:
            # Fall back to full-text tail if segments lack timing info.
            full = (result.get("text") or "").strip()
            if full:
                tail = full.split(".")[-1].strip().rstrip(".!?") or full
                phrase = " ".join(tail.split()[-14:])  # last ~14 words
        if phrase:
            return f'"{phrase}"'
        return random.choice(FALLBACK_CAPTIONS)
    except Exception:
        return random.choice(FALLBACK_CAPTIONS)
    finally:
        if tmp_wav:
            try:
                if os.path.isfile(tmp_wav):
                    os.remove(tmp_wav)
            except OSError:
                pass


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python caption_generator.py <audio_or_video> <climax_time>")
        raise SystemExit(1)
    print(get_freeze_caption(sys.argv[1], float(sys.argv[2])))
