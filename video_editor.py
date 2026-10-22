"""9:16 meme edit assembly: freeze-frame + Whisper caption + SFX + overlay.

Pipeline:
  Segment 1: video up to climax.
  Segment 2: 1s freeze-frame of the climax frame + record-scratch SFX
             + Whisper caption overlaid (PIL meme text, no ImageMagick).
  Segment 3: video resumes with impact SFX (vine boom) + image cutout
             overlay (skull/clown) over the crash.
"""

import os
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# Compat: moviepy 1.0.3 references PIL.Image.ANTIALIAS, removed in Pillow 10+.
# Alias it to LANCZOS so resize() works with modern Pillow.
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS  # type: ignore[attr-defined]


def _resolve_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if os.path.isfile(exe):
            d = os.path.dirname(exe)
            std = os.path.join(d, "ffmpeg.exe")
            if os.path.isfile(std):
                return std
            return exe
    except Exception:
        pass
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        "Microsoft", "WinGet", "Packages")
    try:
        for entry in os.listdir(base):
            if "Gyan.FFmpeg" in entry:
                cand = os.path.join(base, entry)
                for root, _d, files in os.walk(cand):
                    if "ffmpeg.exe" in files:
                        return os.path.join(root, "ffmpeg.exe")
    except OSError:
        pass
    return "ffmpeg"


_resolved_ffmpeg = _resolve_ffmpeg()
if os.path.isfile(_resolved_ffmpeg):
    _ffmpeg_dir = os.path.dirname(_resolved_ffmpeg)
    if _ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
os.environ["IMAGEIO_FFMPEG_EXE"] = _resolved_ffmpeg
os.environ["FFMPEG_BINARY"] = _resolved_ffmpeg

import numpy as np
from moviepy.editor import (
    AudioFileClip,
    ColorClip,
    VideoClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    concatenate_videoclips,
)

TARGET_W, TARGET_H = 1080, 1920  # 9:16 vertical

# Perf: wide sources are pre-scaled to this width right after load.
# Keeps decode + fg-resize work bounded regardless of harvest resolution
# (4K Reddit clips would otherwise dominate per-frame resize cost).
MAX_INPUT_WIDTH = 1280

# Perf (measured on this machine, 3s clip, 1080x1920 vs 540x960):
#   1080x1920 -> 0.596 s/frame
#    540x960  -> 0.269 s/frame  (~2.2x faster)
# Render cost scales with OUTPUT pixels, not source pixels, so the real
# win is editing at RENDER_SCALE and letting ffmpeg upscale to 1080x1920
# afterwards. 0.75 is a compromise: ~1.6x faster, 810x1440 upscaled is
# still clean on a phone. Set to 1.0 to disable (full-quality, slow).
RENDER_SCALE = 0.75

# Env override so slower runners (CI containers, scheduled cloud jobs) can drop
# to 0.5 for ~2.2x faster rendering without editing code. Invalid/out-of-range
# values fall back to the default above.
try:
    _scale_env = os.environ.get("RENDER_SCALE", "").strip()
    if _scale_env:
        _scale_val = float(_scale_env)
        if 0.25 <= _scale_val <= 1.0:
            RENDER_SCALE = _scale_val
        else:
            print(f"[editor] RENDER_SCALE={_scale_env} out of range "
                  f"(0.25-1.0); using {RENDER_SCALE}")
except ValueError:
    print(f"[editor] RENDER_SCALE={os.environ.get('RENDER_SCALE')!r} is not a "
          f"number; using {RENDER_SCALE}")


def _resolve_ffmpeg() -> str:
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


# Make moviepy's ffmpeg calls work even when PATH lacks the WinGet shim.
os.environ["IMAGEIO_FFMPEG_EXE"] = _resolve_ffmpeg()
try:
    from moviepy.config import change_settings
    change_settings({"FFMPEG_BINARY": os.environ["IMAGEIO_FFMPEG_EXE"]})
except Exception:
    pass


def _load_sfx(path: str):
    if path and os.path.isfile(path):
        try:
            return AudioFileClip(path)
        except Exception:
            return None
    return None


def _pil_blurred_background(frame: np.ndarray,
                            target_w: int = TARGET_W,
                            target_h: int = TARGET_H) -> Image.Image:
    """Build a blurred cover background of target_w x target_h from a frame."""
    src = Image.fromarray(frame).convert("RGB")
    sw, sh = src.size
    scale = max(target_w / sw, target_h / sh)
    cover = src.resize((int(sw * scale) + 1, int(sh * scale) + 1),
                       Image.BILINEAR)
    left = (cover.width - target_w) // 2
    top = (cover.height - target_h) // 2
    cover = cover.crop((left, top, left + target_w, top + target_h))
    cover = cover.filter(ImageFilter.GaussianBlur(radius=25))
    dark = Image.new("RGB", cover.size, (0, 0, 0))
    return Image.blend(cover, dark, 0.35)


def create_meme_text_image(
    text: str,
    fontsize: int = 84,
    max_width: int = 940,
    padding: int = 24,
    text_fill=(255, 235, 0),
    stroke_fill=(0, 0, 0),
    stroke_width: int = 4,
) -> Image.Image:
    """Render high-contrast stroke-outlined meme text with PIL.

    Avoids MoviePy TextClip / ImageMagick entirely. Word-wraps to fit.
    Yellow text with black outline for classic meme look.
    """
    text = (text or "").strip() or '"..."'
    font = None
    for candidate in ("arialbd.ttf", "arial.ttf", "impact.ttf",
                      "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(candidate, fontsize)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    probe = Image.new("RGB", (10, 10))
    drawer = ImageDraw.Draw(probe)

    def _w(t):
        box = drawer.textbbox((0, 0), t, font=font,
                              stroke_width=stroke_width)
        return box[2] - box[0]

    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if _w(trial) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    heights, widths = [], []
    for line in lines:
        box = drawer.textbbox((0, 0), line, font=font,
                              stroke_width=stroke_width)
        widths.append(box[2] - box[0])
        heights.append(box[3] - box[1])
    line_gap = 12
    block_w = max(widths) + padding * 2
    block_h = sum(heights) + line_gap * (len(lines) - 1) + padding * 2

    img = Image.new("RGBA", (block_w, block_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    y = padding
    for line, lh, lw in zip(lines, heights, widths):
        x = (block_w - lw) // 2
        draw.text((x, y), line, font=font, fill=text_fill + (255,),
                  stroke_width=stroke_width, stroke_fill=stroke_fill + (255,))
        y += lh + line_gap
    return img


HOOK_TEMPLATES = [
    "WAIT FOR IT... ??",
    "BRO REALLY THOUGHT ??",
    "HE REGRETTED THIS IMMEDIATELY ??",
    "NO WAY THIS JUST HAPPENED ??",
    "NEVER LET THEM KNOW YOUR NEXT MOVE ??",
    "TOP 10 MOMENTS BEFORE DISASTER ??",
    "TASK FAILED SUCCESSFULLY ??",
    "WATCH TILL THE END ??",
    "INSTANT REGRET ??",
    "CALCULATED RISK ??",
    "IT KEEPS GETTING WORSE ??",
    "WHO DID THIS ????",
    "PHYSICS HAS LEFT THE CHAT ??",
]


def create_hook_banner_image(
    text: str,
    base_fontsize: int = 40,
    max_width: int = 960,
    scale: float = 1.0,
) -> Image.Image:
    """Render an eye-catching viral hook banner (e.g. 'WAIT FOR IT... ??')."""
    text = (text or "").strip()
    fontsize = max(20, int(round(base_fontsize * scale)))
    font = None
    for candidate in ("impact.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "arial.ttf"):
        try:
            font = ImageFont.truetype(candidate, fontsize)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    probe = Image.new("RGBA", (10, 10))
    drawer = ImageDraw.Draw(probe)
    stroke_w = max(1, int(round(2 * scale)))
    bbox = drawer.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    pad_x = max(16, int(round(26 * scale)))
    pad_y = max(8, int(round(12 * scale)))
    w = min(int(round(max_width * scale)), text_w + pad_x * 2)
    h = text_h + pad_y * 2

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(6, int(round(12 * scale)))
    border_w = max(1, int(round(2 * scale)))
    draw.rounded_rectangle(
        [0, 0, w - 1, h - 1],
        radius=radius,
        fill=(15, 15, 15, 215),
        outline=(255, 235, 0, 200),
        width=border_w,
    )
    tx = (w - text_w) // 2
    ty = (h - text_h) // 2 - bbox[1] // 2
    draw.text(
        (tx, ty), text, font=font, fill=(255, 255, 255, 255),
        stroke_width=stroke_w, stroke_fill=(0, 0, 0, 255)
    )
    return img


def create_credit_badge_image(
    credit_text: str,
    base_fontsize: int = 30,
    max_width: int = 900,
    scale: float = 1.0,
) -> Image.Image:
    """Render a clean, semi-transparent attribution pill badge (e.g. 'Credit: @creator')."""
    credit = (credit_text or "").strip()
    if len(credit) > 55:
        credit = credit[:52] + "..."

    fontsize = max(18, int(round(base_fontsize * scale)))
    font = None
    for candidate in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(candidate, fontsize)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    probe = Image.new("RGBA", (10, 10))
    drawer = ImageDraw.Draw(probe)
    bbox = drawer.textbbox((0, 0), credit, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    pad_x = max(12, int(round(22 * scale)))
    pad_y = max(6, int(round(10 * scale)))
    w = min(int(round(max_width * scale)), text_w + pad_x * 2)
    h = text_h + pad_y * 2

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Subtle translucent dark pill with light border
    radius = h // 2
    draw.rounded_rectangle(
        [0, 0, w - 1, h - 1],
        radius=radius,
        fill=(0, 0, 0, 160),
        outline=(255, 255, 255, 60),
        width=max(1, int(round(1 * scale))),
    )

    # Centered text in clear, high-legibility white
    tx = (w - text_w) // 2
    ty = (h - text_h) // 2 - bbox[1] // 2
    draw.text((tx, ty), credit, font=font, fill=(255, 255, 255, 240))
    return img


def build_9_16_base(clip, target_w: int = TARGET_W,
                    target_h: int = TARGET_H) -> CompositeVideoClip:
    """Center wide footage onto a blurred vertical 9:16 background.

    Keeps full audio of `clip`. Foreground is scaled to fit target_w wide
    while preserving aspect ratio, then centered vertically.
    """
    bg_frame = _pil_blurred_background(clip.get_frame(0), target_w, target_h)
    bg = (ImageClip(np.array(bg_frame))
          .set_duration(clip.duration)
          .set_fps(clip.fps or 30))
    fg_w = target_w
    fg_h = int(round(clip.h * (target_w / clip.w)))
    if fg_h > target_h:
        fg_h = target_h
        fg_w = int(round(clip.w * (target_h / clip.h)))
    fg = clip.resize(newsize=(fg_w, fg_h)).set_position("center")
    return CompositeVideoClip([bg, fg], size=(target_w, target_h))


def assemble_meme_edit_with_freeze_and_caption(
    raw_video_path: str,
    climax_timestamp: float,
    caption_text: str,
    record_scratch_sfx: str,
    impact_sfx: str,
    overlay_image_path: str,
    output_path: str,
    freeze_duration: float = 1.0,
    pre_roll: float = 6.0,
    credit_text: str = None,
    hook_text: str = None,
) -> str:
    """Assemble the full meme edit and export to output_path.

    pre_roll caps how many seconds of source play before the climax. Without
    it, a long source (e.g. a 20-min compilation whose loudest crash sits at
    900s) would render 900s of unrelated footage in front of the freeze --
    useless as a Short. The freeze + caption always sit at the climax.
    """
    if not os.path.isfile(raw_video_path):
        raise FileNotFoundError(f"Raw video not found: {raw_video_path}")
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    src = VideoFileClip(raw_video_path)
    # Pre-scale oversized wide sources (e.g. 1440p/4K harvests) to 720p-wide
    # before any editing: cuts per-frame PIL blur + encode cost ~3x.
    # Portrait clips (w < h) skip this -- they are already phone-shaped.
    try:
        if src.w > MAX_INPUT_WIDTH and src.w >= src.h:
            src = src.resize(width=MAX_INPUT_WIDTH)
    except Exception:
        pass
    fps = src.fps or 30
    total = src.duration or 0.0
    climax = max(0.0, min(float(climax_timestamp), max(0.0, total - 0.05)))

    # Edit at RENDER_SCALE then upscale (see module header for the numbers):
    # render cost scales with OUTPUT pixels, so building at 810x1440 and
    # letting ffmpeg upscale is ~1.6x faster than editing at 1080x1920.
    # Dimensions are passed explicitly -- no global mutation.
    _scale = float(globals().get("RENDER_SCALE", 1.0) or 1.0)
    if not (0 < _scale < 1.0):
        _scale = 1.0
    _tw, _th = int(TARGET_W * _scale), int(TARGET_H * _scale)

    # Segment 1: pre-roll leading into the climax, capped at pre_roll seconds
    # so long sources still produce a Short-sized edit.
    seg_start = max(0.0, climax - max(0.0, pre_roll))
    seg_end = climax
    if seg_end - seg_start < 0.05:
        # Climax sits near the source start: nudge forward so the clip is
        # never zero-length.
        seg_end = min(total, max(seg_start + 0.05, climax))
        if seg_end - seg_start < 0.05:
            seg_start = max(0.0, seg_end - 0.05)
    seg1_raw = src.subclip(seg_start, seg_end)
    seg1 = build_9_16_base(seg1_raw, _tw, _th).set_fps(fps)

    # Segment 2: freeze-frame + scratch SFX + caption.
    freeze_t = min(climax, max(0.0, total - 1e-3))
    freeze_frame = src.get_frame(freeze_t)
    bg_img = _pil_blurred_background(freeze_frame, _tw, _th)
    bg_clip = (ImageClip(np.array(bg_img))
               .set_duration(freeze_duration)
               .set_fps(fps))
    sw, sh = src.w, src.h
    fg_w = _tw
    fg_h = int(round(sh * (_tw / sw)))
    if fg_h > _th:
        fg_h = _th
        fg_w = int(round(sw * (_th / sh)))
    frozen_fg = (ImageClip(freeze_frame)
                 .resize(newsize=(fg_w, fg_h))
                 .set_duration(freeze_duration)
                 .set_position("center"))
    # Caption + overlay are sized in absolute px, so scale them to match
    # the edit resolution (font 84 / width 940 / overlay 520 are the
    # 1080-wide reference values).
    caption_img = create_meme_text_image(
        caption_text or '"..."',
        fontsize=max(28, int(round(84 * _scale))),
        max_width=max(320, int(round(940 * _scale))),
    )
    caption_clip = (ImageClip(np.array(caption_img))
                    .set_duration(freeze_duration)
                    .set_position(("center", 0.62), relative=True))
    seg2 = CompositeVideoClip([bg_clip, frozen_fg, caption_clip],
                              size=(_tw, _th)).set_fps(fps)
    scratch = _load_sfx(record_scratch_sfx)
    if scratch is not None:
        if scratch.duration and scratch.duration > freeze_duration:
            scratch = scratch.subclip(0, freeze_duration)
        seg2 = seg2.set_audio(scratch)
    else:
        seg2 = seg2.without_audio()

    # Segment 3: resume with impact SFX + overlay cutout (capped to 3.5s punchline).
    post_roll = 3.5
    if climax < total - 0.05:
        tail_end = min(total, climax + post_roll)
        tail_raw = src.subclip(climax, tail_end)
    else:
        tail_raw = src.subclip(max(0.0, total - 0.5))
    seg3_base = build_9_16_base(tail_raw, _tw, _th)
    overlays = []
    if overlay_image_path and os.path.isfile(overlay_image_path):
        try:
            ov_dur = min(1.2, tail_raw.duration or 1.2)
            ov = (ImageClip(overlay_image_path)
                  .set_duration(ov_dur)
                  .resize(width=max(160, int(520 * _scale)))
                  .set_position(("center", 0.30), relative=True))
            if ov.duration and ov.duration > 0.3:
                ov = ov.crossfadein(0.12)
            overlays.append(ov)
        except Exception:
            pass
    if overlays:
        seg3 = CompositeVideoClip([seg3_base] + overlays,
                                  size=(_tw, _th)).set_fps(fps)
    else:
        seg3 = seg3_base.set_fps(fps)
    boom = _load_sfx(impact_sfx)
    if boom is not None:
        base_audio = seg3.audio
        tracks = [boom.set_start(0.0)]
        if base_audio is not None:
            tracks.append(base_audio)
        seg3 = seg3.set_audio(CompositeAudioClip(tracks))

    # Concatenate + export. fps passed explicitly as int keyword:
    # moviepy 1.0.3's use_clip_fps_by_default only picks up fps from
    # kwargs (positional fps is ignored -> None -> writer crash).
    final = concatenate_videoclips([seg1, seg2, seg3], method="compose")
    final.fps = int(fps)

    import random

    extra_layers = [final]

    # 1. Attribution watermark (credit pill badge)
    if credit_text and credit_text.strip():
        try:
            badge_img = create_credit_badge_image(credit_text.strip(), scale=_scale)
            badge_clip = (ImageClip(np.array(badge_img))
                          .set_duration(final.duration)
                          .set_position(("center", 0.06), relative=True))
            extra_layers.append(badge_clip)
        except Exception as exc:
            print(f"[editor] credit watermark failed ({exc}); continuing without badge")

    # 2. Viral Hook Header Banner (Top retention hook)
    if hook_text is None and HOOK_TEMPLATES:
        hook_text = random.choice(HOOK_TEMPLATES)
    if hook_text and hook_text.strip():
        try:
            hook_img = create_hook_banner_image(hook_text.strip(), scale=_scale)
            hook_clip = (ImageClip(np.array(hook_img))
                         .set_duration(final.duration)
                         .set_position(("center", 0.13), relative=True))
            extra_layers.append(hook_clip)
        except Exception as exc:
            print(f"[editor] hook banner failed ({exc}); skipping")

# Progress bar removed for maximum MoviePy encoding speed.

    if len(extra_layers) > 1:
        final = CompositeVideoClip(extra_layers, size=(_tw, _th))
        final.fps = int(fps)

    # Encode at edit res, then upscale to 1080x1920 in one cheap pass.
    edit_path = (os.path.splitext(output_path)[0] + "_edit.mp4"
                 if _scale != 1.0 else output_path)
    final.write_videofile(edit_path, codec="libx264", audio_codec="aac",
                          fps=int(fps), preset="veryfast", threads=4,
                          logger=None)
    if edit_path != output_path:
        # Output dims are even, so no odd-height chroma padding needed.
        try:
            # imageio only exports IMAGEIO_FFMPEG_EXE to child processes when
            # moviepy itself spawned ffmpeg; a plain import leaves it unset, so
            # an unguarded os.environ[...] raises KeyError here. Resolve the
            # binary ourselves (also handles FFmpeg being off PATH).
            ffmpeg_bin = os.environ.get("IMAGEIO_FFMPEG_EXE") or ""
            if not ffmpeg_bin:
                try:
                    from audio_detector import _resolve_ffmpeg_bin
                    ffmpeg_bin = _resolve_ffmpeg_bin("ffmpeg")
                except Exception:
                    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
            subprocess.run(
                [ffmpeg_bin, "-y", "-i", edit_path,
                 "-vf", f"scale={TARGET_W}:{TARGET_H}:flags=bicubic",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-pix_fmt", "yuv420p", "-c:a", "aac",
                 "-movflags", "+faststart", output_path],
                check=True, capture_output=True, text=True,
            )
        except Exception as exc:
            # Never destroy the rendered edit: it is already a valid video,
            # just at edit res. Keep it as the deliverable instead.
            print(f"[video_editor] upscale failed ({exc}); "
                  f"keeping edit-res render {edit_path}")
            if not os.path.isfile(output_path):
                shutil.move(edit_path, output_path)
            return output_path
        finally:
            if os.path.isfile(edit_path):
                os.remove(edit_path)
    try:
        src.reader.close()
    except Exception:
        pass
    return output_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 8:
        print("Usage: python video_editor.py <raw> <climax> <caption> "
              "<scratch> <boom> <overlay> <output>")
        raise SystemExit(1)
    assemble_meme_edit_with_freeze_and_caption(
        sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4],
        sys.argv[5], sys.argv[6], sys.argv[7])
