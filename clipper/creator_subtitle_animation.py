"""Creator Studio subtitle animation compatibility for libass.

Spring scale curve is ported from the MIT-licensed MoneyPrinterTurbo
app/services/video.py::_get_subtitle_spring_scale. We apply its sampled
curve using ASS \t transforms, rather than importing MoviePy's large renderer.
See clipper/mpt_compat/LICENSE for upstream copyright terms.
"""
import math

SPRING_SECONDS = 0.18
MIN_SCALE = 0.05
MAX_SCALE = 1.35
MODES = ("none", "pop_spring", "fade")


def spring_scale(seconds: float, duration: float = SPRING_SECONDS) -> float:
    """Upstream MoneyPrinterTurbo exponential, damped-cosine spring curve."""
    if duration <= 0 or seconds >= duration:
        return 1.0
    progress = max(0.0, min(seconds / duration, 1.0))
    scale = 1.0 - math.exp(-6.0 * progress) * math.cos(2.5 * math.pi * progress)
    return max(MIN_SCALE, min(scale, MAX_SCALE))


def ass_prefix(mode: str, cue_seconds: float) -> str:
    """ASS animation tags for one subtitle event, bounded by its duration.

    ASS \t interpolates the upstream spring at 60-fps-sized intervals; the
    same transform operates on both glyph fill and outline without alpha
    masks, avoiding MoviePy's black first-frame/mask mismatch.
    """
    if mode not in MODES:
        raise ValueError("Unknown subtitle animation.")
    if mode == "none" or cue_seconds <= 0.04:
        return ""
    if mode == "fade":
        return r"{\fad(" + str(min(110, int(cue_seconds * 350))) + r",0)}"
    duration = min(SPRING_SECONDS, max(0.04, cue_seconds * 0.85))
    total_ms = max(1, int(duration * 1000))
    keyframes = [0, 60, 120, 180, 240, total_ms]
    keyframes = sorted(set(min(total_ms, x) for x in keyframes))
    initial_scale = round(spring_scale(0, duration) * 100)
    tags = [rf"\fscx{initial_scale}\fscy{initial_scale}"]
    previous = 0
    for moment in keyframes[1:]:
        scale = round(spring_scale(moment / 1000, duration) * 100)
        if moment == total_ms:
            scale = 100
        tags.append(rf"\t({previous},{moment},\fscx{scale}\fscy{scale})")
        previous = moment
    return "{"+ "".join(tags) + "}"


def animate_ass(ass_text: str, mode: str) -> str:
    """Decorate Dialogue lines only; retain original ASS subtitle styling."""
    if mode == "none":
        return ass_text
    output = []
    for line in ass_text.splitlines(keepends=True):
        if not line.startswith("Dialogue: "):
            output.append(line)
            continue
        parts = line.split(",", 9)
        if len(parts) != 10:
            output.append(line)
            continue
        # ASS timestamps are H:MM:SS.cs.
        def time_seconds(value):
            hh, mm, ss = value.split(":")
            return int(hh)*3600 + int(mm)*60 + float(ss)
        try:
            duration = time_seconds(parts[2]) - time_seconds(parts[1])
        except (ValueError, IndexError):
            output.append(line)
            continue
        prefix = ass_prefix(mode, duration)
        output.append(",".join(parts[:9]) + "," + prefix + parts[9])
    return "".join(output)
