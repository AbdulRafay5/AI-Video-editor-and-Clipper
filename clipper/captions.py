"""Caption building, editing, styling, ASS/SRT generation and frame previews."""
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import config
from . import video

# Fonts we can use: family name -> font files to copy from the Windows fonts folder.
FONT_FILES = {
    "Arial": ["arial.ttf", "arialbd.ttf"],
    "Impact": ["impact.ttf"],
    "Segoe UI": ["segoeui.ttf", "segoeuib.ttf"],
    "Verdana": ["verdana.ttf", "verdanab.ttf"],
    "Trebuchet MS": ["trebuc.ttf", "trebucbd.ttf"],
    "Tahoma": ["tahoma.ttf", "tahomabd.ttf"],
}

DEFAULT_STYLE = {
    "font": "Arial",
    "size_pct": 6.0,          # font size as % of video height
    "bold": True,
    "uppercase": False,
    "text_color": "#FFFFFF",
    "highlight_color": "#FFD400",
    "highlight_word": True,   # colour the word being spoken
    "outline_color": "#000000",
    "outline_pct": 0.5,       # outline thickness as % of video height
    "position": "bottom",     # top | middle | bottom
    "margin_pct": 14.0,       # distance from top/bottom edge, % of height (safe area)
    "line_chars": 24,         # max characters per line before breaking into 2 lines
}

SENTENCE_END = re.compile(r"[.!?\u2026]$")


# ---------- fonts and style ----------

def _fonts_dir():
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


def available_fonts():
    return [name for name, files in FONT_FILES.items() if (_fonts_dir() / files[0]).exists()]


def normalise_style(raw):
    """Validate a style dict from the browser; anything invalid falls back to a default."""
    raw = raw or {}
    s = dict(DEFAULT_STYLE)

    def num(key, lo, hi):
        try:
            s[key] = min(max(float(raw.get(key, s[key])), lo), hi)
        except (TypeError, ValueError):
            pass

    num("size_pct", 2, 15)
    num("outline_pct", 0, 2)
    num("margin_pct", 0, 40)
    num("line_chars", 8, 60)
    s["line_chars"] = int(s["line_chars"])
    for key in ("text_color", "highlight_color", "outline_color"):
        v = raw.get(key)
        if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            s[key] = v.upper()
    for key in ("bold", "uppercase", "highlight_word"):
        if key in raw:
            s[key] = bool(raw[key])
    if raw.get("position") in ("top", "middle", "bottom"):
        s["position"] = raw["position"]
    if raw.get("font") in FONT_FILES:
        s["font"] = raw["font"]
    return s


# ---------- building and editing captions ----------

def build_captions(segments, max_words=4, max_chars=36, max_gap=0.7, max_duration=3.0):
    """Group word timestamps into short caption chunks."""
    words = []
    for seg in segments:
        for w in seg.get("words", []):
            text = (w.get("word") or "").strip()
            if text and w.get("start") is not None and w.get("end") is not None:
                words.append({"start": round(float(w["start"]), 3),
                              "end": round(float(w["end"]), 3), "word": text})

    captions, cur = [], []

    def flush():
        if cur:
            captions.append({
                "id": len(captions) + 1,
                "start": cur[0]["start"],
                "end": cur[-1]["end"],
                "text": " ".join(x["word"] for x in cur),
                "words": [dict(x) for x in cur],
            })
            cur.clear()

    for w in words:
        if cur:
            too_long = len(" ".join(x["word"] for x in cur) + " " + w["word"]) > max_chars
            if (len(cur) >= max_words or w["start"] - cur[-1]["end"] > max_gap
                    or w["end"] - cur[0]["start"] > max_duration or too_long):
                flush()
        cur.append(w)
        if SENTENCE_END.search(w["word"]):
            flush()
    flush()
    return captions


def apply_edits(old, edits):
    """Apply text/time edits from the UI. Captions missing from `edits` are removed."""
    by_id = {c["id"]: c for c in old}
    seen, result = set(), []
    for e in edits:
        try:
            cid = int(e["id"])
            start = float(e["start"])
            end = float(e["end"])
            text = " ".join(str(e["text"]).split())
        except (KeyError, TypeError, ValueError):
            raise ValueError("Each caption needs an id, start, end and text.")
        if cid not in by_id:
            raise ValueError(f"Unknown caption {cid}.")
        if cid in seen:
            raise ValueError(f"Caption {cid} appears twice.")
        seen.add(cid)
        if not text:
            continue  # empty text = delete this caption
        if start < 0 or end <= start:
            raise ValueError(f"Caption {cid}: the end time must be after the start time.")

        o = by_id[cid]
        new_words = text.split(" ")
        if len(new_words) == len(o["words"]):
            times = [(w["start"], w["end"]) for w in o["words"]]
        else:  # word count changed: spread the words evenly across the old span
            step = (o["end"] - o["start"]) / len(new_words)
            times = [(o["start"] + i * step, o["start"] + (i + 1) * step) for i in range(len(new_words))]
        scale = (end - start) / max(o["end"] - o["start"], 1e-6)
        words = [{"start": round(start + (a - o["start"]) * scale, 3),
                  "end": round(start + (b - o["start"]) * scale, 3),
                  "word": nw} for (a, b), nw in zip(times, new_words)]
        result.append({"id": cid, "start": round(start, 3), "end": round(end, 3),
                       "text": text, "words": words})
    result.sort(key=lambda c: c["start"])
    return result


# ---------- layout helpers ----------

def _split_lines(words, line_chars):
    """Split a list of words into at most two balanced lines."""
    if len(words) < 2 or len(" ".join(words)) <= line_chars:
        return [words]
    best = None
    for i in range(1, len(words)):
        score = max(len(" ".join(words[:i])), len(" ".join(words[i:])))
        if best is None or score < best[0]:
            best = (score, i)
    return [words[:best[1]], words[best[1]:]]


def _hex_to_ass(hex_color):
    h = hex_color.lstrip("#")
    return (h[4:6] + h[2:4] + h[0:2]).upper()  # ASS uses BBGGRR


def _ass_time(t):
    cs = int(round(max(t, 0.0) * 100))
    return f"{cs // 360000}:{(cs % 360000) // 6000:02d}:{(cs % 6000) // 100:02d}.{cs % 100:02d}"


def _clean(word, upper):
    w = re.sub(r"[{}\\]", "", word)
    return w.upper() if upper else w


def _tag(color):
    return "{\\c&H" + color + "&}"


def _render_text(lines, highlight_index, base, hl):
    out, idx = [], 0
    for line in lines:
        toks = []
        for w in line:
            toks.append(_tag(hl) + w + _tag(base) if idx == highlight_index else w)
            idx += 1
        out.append(" ".join(toks))
    return "\\N".join(out)


# ---------- ASS / SRT output ----------

def build_ass(captions, style, width, height, clip_start=0.0, clip_end=None):
    """Return (ass_text, event_count) for the time range, shifted so clip_start becomes 0."""
    st = normalise_style(style)
    clip_end = float("inf") if clip_end is None else clip_end
    base, hl, outline = (_hex_to_ass(st["text_color"]), _hex_to_ass(st["highlight_color"]),
                         _hex_to_ass(st["outline_color"]))
    fontsize = round(height * st["size_pct"] / 100, 1)
    outline_px = round(height * st["outline_pct"] / 100, 1)
    margin_v = int(height * st["margin_pct"] / 100)
    margin_lr = int(width * 0.05)
    align = {"bottom": 2, "middle": 5, "top": 8}[st["position"]]
    bold = -1 if st["bold"] else 0

    lines_out = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{st['font']},{fontsize},&H00{base},&H00{base},&H00{outline},&H80000000,"
        f"{bold},0,0,0,100,100,0,0,1,{outline_px},0,{align},{margin_lr},{margin_lr},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    count = 0

    def emit(s, e, text):
        nonlocal count
        s2, e2 = max(s, clip_start) - clip_start, min(e, clip_end) - clip_start
        if e2 - s2 < 0.02:
            return
        lines_out.append(f"Dialogue: 0,{_ass_time(s2)},{_ass_time(e2)},Default,,0,0,0,,{text}")
        count += 1

    for c in captions:
        words = c.get("words") or []
        if not words or c["end"] <= clip_start or c["start"] >= clip_end:
            continue
        cleaned = [_clean(w["word"], st["uppercase"]) for w in words]
        lines = _split_lines(cleaned, st["line_chars"])
        if st["highlight_word"]:
            for i, w in enumerate(words):
                s = c["start"] if i == 0 else w["start"]
                e = c["end"] if i == len(words) - 1 else words[i + 1]["start"]
                emit(s, e, _render_text(lines, i, base, hl))
        else:
            emit(c["start"], c["end"], _render_text(lines, None, base, hl))

    return "\n".join(lines_out) + "\n", count


def _srt_time(t):
    ms = int(round(max(t, 0.0) * 1000))
    return f"{ms // 3600000:02d}:{(ms % 3600000) // 60000:02d}:{(ms % 60000) // 1000:02d},{ms % 1000:03d}"


def to_srt(captions, line_chars=24, start=0.0, end=None):
    blocks, n = [], 0
    for c in captions:
        if c["end"] <= start or (end is not None and c["start"] >= end):
            continue
        s = max(c["start"], start) - start
        e = (c["end"] if end is None else min(c["end"], end)) - start
        if e - s < 0.02:
            continue
        n += 1
        text = "\n".join(" ".join(line) for line in _split_lines(c["text"].split(), line_chars))
        blocks.append(f"{n}\n{_srt_time(s)} --> {_srt_time(e)}\n{text}\n")
    return "\n".join(blocks)


# ---------- FFmpeg helpers ----------

def prepare_workdir(work_dir, ass_text, font):
    """Create a folder with captions.ass and the font files, so FFmpeg can use plain
    relative paths (avoids Windows path-escaping problems in filter strings)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    fonts = work_dir / "fonts"
    fonts.mkdir(exist_ok=True)
    for fname in FONT_FILES.get(font, []):
        src = _fonts_dir() / fname
        if src.exists():
            shutil.copy2(src, fonts / fname)
    (work_dir / "captions.ass").write_text(ass_text, encoding="utf-8")


def render_preview(source, t, captions, style, width, height):
    """Render one frame at time t with captions drawn on it. Returns (png_bytes, event_count)."""
    st = normalise_style(style)
    work = config.WORK_DIR / f"preview_{uuid.uuid4().hex}"
    try:
        ass, count = build_ass(captions, st, width, height, clip_start=t, clip_end=t + 1.0)
        prepare_workdir(work, ass, st["font"])
        cmd = [video._tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{t:.3f}", "-i", str(Path(source).resolve()),
               "-vf", "subtitles=captions.ass:fontsdir=fonts",
               "-frames:v", "1", "preview.png"]
        try:
            r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=90, creationflags=video.NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise video.VideoError("Preview timed out.")
        out = work / "preview.png"
        if r.returncode != 0 or not out.exists():
            raise video.VideoError("Preview failed: " + (r.stderr or "").strip()[-300:])
        return out.read_bytes(), count
    finally:
        shutil.rmtree(work, ignore_errors=True)