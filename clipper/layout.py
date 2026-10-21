"""Vertical (9:16) layouts built from FFmpeg filters, plus a one-frame preview."""
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

import config
from . import captions as cap
from . import tracking, video

LAYOUTS = {
    "original": "Original shape",
    "blur": "Vertical, blurred background",
    "crop": "Vertical, crop to fill",
    "follow": "Vertical, auto-follow faces",
    "fit": "Vertical, fit with black bars",
}

CAPTION_FILTER = "subtitles=captions.ass:fontsdir=fonts"


def out_size():
    return config.VERTICAL_WIDTH, config.VERTICAL_HEIGHT


def normalise(layout, crop_pos):
    layout = layout if layout in LAYOUTS else "original"
    try:
        pos = float(crop_pos)
    except (TypeError, ValueError):
        pos = 0.5
    return layout, min(max(pos, 0.0), 1.0)


def frame_size(layout, src_w, src_h):
    """Size of the final frame, which is what captions are laid out against."""
    return (src_w, src_h) if layout == "original" else out_size()


def _even(n):
    return n - (n % 2)


def _x_expression(points):
    """FFmpeg expression for the crop's left edge over time (straight lines between points)."""
    expr = str(points[-1][1])
    for (t0, x0), (t1, x1) in reversed(list(zip(points, points[1:]))):
        seg = str(x0) if x0 == x1 else f"{x0}+({x1 - x0})*(t-{t0:.3f})/({t1 - t0:.3f})"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return expr


def layout_filter(layout, src_w, src_h, crop_pos, plan=None):
    """FFmpeg filter text that turns the source into the chosen layout (None = leave as is)."""
    if layout == "original":
        return None
    W, H = out_size()

    if layout in ("crop", "follow"):
        target = W / H
        if src_w / src_h > target:      # wider than 9:16: trim the sides
            cw, ch = _even(int(src_h * target)), _even(src_h)
            if layout == "follow" and plan:
                x_part = f"x='{_x_expression(plan['points'])}':y=0"
            else:
                x_part = f"x={_even(int((src_w - cw) * crop_pos))}:y=0"
        else:                           # taller than 9:16: trim top and bottom
            cw, ch = _even(src_w), _even(int(src_w / target))
            x_part = f"x=0:y={_even(int((src_h - ch) * crop_pos))}"
        return f"crop={cw}:{ch}:{x_part},scale={W}:{H}:flags=lanczos,setsar=1"

    if layout == "fit":
        return (f"scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")

    # blur: the video sits in the middle over a blurred, enlarged copy of itself
    bw, bh = W // 4, H // 4
    return (f"split=2[bg][fg];"
            f"[bg]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"boxblur=8:2,scale={W}:{H}[b];"
            f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2[f];"
            f"[b][f]overlay=(W-w)/2:(H-h)/2,setsar=1")


def build_vf(layout, src_w, src_h, crop_pos, has_captions, plan=None):
    parts = []
    lf = layout_filter(layout, src_w, src_h, crop_pos, plan)
    if lf:
        parts.append(lf)
    if has_captions:
        parts.append(CAPTION_FILTER)
    return ",".join(parts) or None


def adapt_caption_style(style, layout):
    """A vertical frame is 1920 tall but only 1080 wide, so scale text to the width
    and keep captions inside the areas that TikTok/Reels/Shorts buttons don't cover."""
    st = dict(cap.normalise_style(style))
    if layout == "original":
        return st
    W, H = out_size()
    k = W / H
    st["size_pct"] = st["size_pct"] * k
    st["outline_pct"] = st["outline_pct"] * k
    if st["position"] == "bottom":
        st["margin_pct"] = max(st["margin_pct"], 20.0)
    elif st["position"] == "top":
        st["margin_pct"] = max(st["margin_pct"], 12.0)
    return st


def render_preview(source, t, layout, crop_pos, caps, style):
    """One frame at time t, in the chosen layout, optionally with captions.
    Returns (png_bytes, caption_event_count, note_or_None)."""
    layout, crop_pos = normalise(layout, crop_pos)
    info = video.probe(source)
    sw, sh = info["width"], info["height"]

    plan, note = None, None
    if layout == "follow":
        try:
            found = tracking.plan_follow(source, t, min(t + 2.0, info["duration"]),
                                         sw, sh, threading.Event())
        except video.VideoError as e:
            found, note = None, f"Face tracking unavailable ({e}); showing a centred crop."
        if found:
            plan = {"points": [(0.0, found["points"][0][1])]}   # where the camera is at time t
            note = "Following faces: " + found["summary"]
        elif not note:
            note = "No faces found here, so this shows a centred crop."

    work = config.WORK_DIR / f"layout_{uuid.uuid4().hex}"
    try:
        work.mkdir(parents=True, exist_ok=True)
        count = 0
        if caps:
            st = adapt_caption_style(style, layout)
            fw, fh = frame_size(layout, sw, sh)
            ass, count = cap.build_ass(caps, st, fw, fh, clip_start=t, clip_end=t + 1.0)
            if count:
                cap.prepare_workdir(work, ass, st["font"])
        vf = build_vf(layout, sw, sh, crop_pos, count > 0, plan)

        cmd = [video._tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{t:.3f}", "-i", str(Path(source).resolve())]
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-frames:v", "1", "preview.png"]
        try:
            r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=90, creationflags=video.NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise video.VideoError("Preview timed out.")
        out = work / "preview.png"
        if r.returncode != 0 or not out.exists():
            raise video.VideoError("Preview failed: " + (r.stderr or "").strip()[-300:])
        return out.read_bytes(), count, note
    finally:
        shutil.rmtree(work, ignore_errors=True)