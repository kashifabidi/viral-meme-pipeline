"""AI Voiceover generation: dual-tier TTS (edge-tts neural + Windows SAPI5 PowerShell fallback).

Interface:
    clean_hook_text_for_speech(text: str) -> str
    synthesize_hook_voiceover(
        text: str,
        output_path: str | None = None,
        preferred_voice: str = "en-US-ChristopherNeural"
    ) -> tuple[str, float]
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time

try:
    import edge_tts
except ImportError:
    edge_tts = None

log = logging.getLogger("tts_generator")

DEFAULT_VOICE = "en-US-ChristopherNeural"
FALLBACK_VOICE = "en-US-GuyNeural"
KNOWN_VOICES = {
    "en-US-ChristopherNeural",
    "en-US-GuyNeural",
    "en-US-JennyNeural",
    "en-US-AriaNeural",
    "en-US-EricNeural",
    "en-US-AnaNeural",
    "en-GB-SoniaNeural",
    "en-GB-RyanNeural",
}

# Regex to strip emojis across standard unicode blocks
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"  # enclosed characters
    "\U0001F900-\U0001F9FF"  # supplemental symbols and pictographs
    "\U0001FA00-\U0001FA6F"  # chess symbols, extended-a
    "\U0001FA70-\U0001FAFF"  # symbols and pictographs extended-a
    "\U00002600-\U000026FF"  # misc symbols
    "]+",
    flags=re.UNICODE,
)


def _resolve_ffmpeg_bin(name: str = "ffmpeg") -> str:
    """Resolve ffmpeg / ffprobe binary."""
    try:
        from audio_detector import _resolve_ffmpeg_bin as _res
        return _res(name)
    except Exception:
        found = shutil.which(name)
        return found or name


def _get_audio_duration(file_path: str) -> float:
    """Get duration of audio file in seconds."""
    if not file_path or not os.path.isfile(file_path):
        return 0.0
    try:
        from audio_detector import _get_duration_seconds
        dur = _get_duration_seconds(file_path)
        if dur > 0:
            return round(dur, 2)
    except Exception:
        pass
    try:
        from moviepy.editor import AudioFileClip
        clip = AudioFileClip(file_path)
        dur = float(clip.duration or 0.0)
        clip.close()
        if dur > 0:
            return round(dur, 2)
    except Exception:
        pass
    return 2.5


def clean_hook_text_for_speech(text: str) -> str:
    """Normalizes hook text, strips emojis and trailing question marks/punctuation for crisp speech synthesis."""
    if not text or not str(text).strip():
        return "Wait for it"

    cleaned = str(text)
    # Remove emojis
    cleaned = EMOJI_PATTERN.sub("", cleaned)
    # Normalize corrupted '??' from encoding glitches
    cleaned = cleaned.replace("??", " ")
    # Collapse excess whitespace and newlines
    cleaned = " ".join(cleaned.split())
    # Strip trailing question marks and unnecessary punctuation for natural speech
    cleaned = cleaned.rstrip("?").strip()

    if not cleaned:
        return "Wait for it"
    return cleaned


def _run_async(coro):
    """Safely run async coroutine whether or not an event loop is already active."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro)).result(timeout=30)
    else:
        return asyncio.run(coro)


def _synthesize_edge_tts(
    text: str,
    voice: str = DEFAULT_VOICE,
    output_path: str | None = None,
) -> tuple[str, float]:
    """Primary Tier: edge-tts neural voice synthesis."""
    if not output_path:
        fd, output_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    target_voice = voice or DEFAULT_VOICE

    async def _save_audio(v: str):
        # Allow edge_tts module to be dynamically imported or mocked
        mod = edge_tts
        if mod is None:
            import edge_tts as mod
        communicate = mod.Communicate(text, v)
        await communicate.save(output_path)

    try:
        _run_async(_save_audio(target_voice))
    except Exception as exc:
        if target_voice != DEFAULT_VOICE:
            log.warning("Edge-TTS voice %s failed (%s); trying default %s", target_voice, exc, DEFAULT_VOICE)
            _run_async(_save_audio(DEFAULT_VOICE))
        else:
            raise RuntimeError(f"Edge-TTS synthesis failed: {exc}") from exc

    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("Edge-TTS produced an empty or missing audio file")

    dur = _get_audio_duration(output_path)
    return output_path, dur


def _synthesize_sapi5_powershell(
    text: str,
    output_path: str | None = None,
) -> tuple[str, float]:
    """Resilient Offline Tier: Windows System.Speech SAPI5 via PowerShell."""
    if not output_path:
        fd, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    is_mp3 = output_path.lower().endswith(".mp3")
    wav_path = os.path.abspath(output_path if not is_mp3 else os.path.splitext(output_path)[0] + "_sapi.wav")

    # Escape single quotes for PowerShell literal string
    escaped_text = text.replace("'", "''")
    escaped_wav = wav_path.replace("'", "''")

    ps_script = (
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$synth.SetOutputToWaveFile('{escaped_wav}'); "
        f"$synth.Speak('{escaped_text}'); "
        "$synth.Dispose();"
    )

    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0 or not os.path.isfile(wav_path) or os.path.getsize(wav_path) == 0:
        raise RuntimeError(f"SAPI5 PowerShell synthesis failed: {proc.stderr[:300]}")

    if is_mp3:
        ffmpeg_bin = _resolve_ffmpeg_bin("ffmpeg")
        proc_conv = subprocess.run(
            [ffmpeg_bin, "-y", "-nostdin", "-i", wav_path, "-c:a", "libmp3lame", "-b:a", "128k", output_path],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if os.path.isfile(wav_path):
            try:
                os.remove(wav_path)
            except OSError:
                pass
        if proc_conv.returncode != 0 or not os.path.isfile(output_path):
            raise RuntimeError(f"FFmpeg WAV to MP3 conversion failed: {proc_conv.stderr[:300]}")

    dur = _get_audio_duration(output_path)
    return output_path, dur


def synthesize_hook_voiceover(
    text: str,
    output_path: str | None = None,
    preferred_voice: str = DEFAULT_VOICE,
) -> tuple[str, float]:
    """
    Synthesizes spoken hook narration.
    Tier 1: edge-tts (neural voice).
    Tier 2: Windows System.Speech PowerShell fallback.
    Returns: (audio_file_path, duration_seconds)
    """
    cleaned = clean_hook_text_for_speech(text)

    # 1. Primary Tier: edge-tts
    try:
        return _synthesize_edge_tts(cleaned, voice=preferred_voice, output_path=output_path)
    except Exception as exc:
        log.warning("Edge-TTS voiceover failed (%s); falling back to SAPI5", exc)

    # 2. Resilient Offline Tier: Windows System.Speech SAPI5
    return _synthesize_sapi5_powershell(cleaned, output_path=output_path)
