"""Check a finished clip against a campaign's rules. It only claims what it can verify;
everything else is flagged for a human to look at."""
import difflib
import logging
import re

from . import tracking, video

log = logging.getLogger("clipper.compliance")

PASS, REVIEW, FAIL = "pass", "review", "fail"
LENGTH_TOLERANCE = 0.25          # seconds of slack on length limits
VISIBLE_PIXEL_FRACTION = 0.005   # a colour counts as visible in a frame if it covers 0.5% of it
HIGH_COVERAGE = 0.9
LOW_COVERAGE = 0.1

VERTICAL_PLATFORMS = re.compile(r"tiktok|reels|shorts|vertical|9:16", re.I)
FACE_WORDS = re.compile(
    r"\b(person|people|face|faces|host|presenter|speaker|man|woman|guy|girl|creator|influencer)\b", re.I)

# OpenCV HSV ranges: hue 0-179, saturation and value 0-255
COLOR_RANGES = {
    "red": [((0, 100, 80), (8, 255, 255)), ((172, 100, 80), (179, 255, 255))],
    "orange": [((9, 120, 100), (19, 255, 255))],
    "yellow": [((20, 100, 100), (35, 255, 255))],
    "green": [((36, 70, 60), (85, 255, 255))],
    "blue": [((90, 80, 60), (130, 255, 255))],
    "purple": [((131, 60, 50), (160, 255, 255))],
    "pink": [((161, 50, 140), (171, 255, 255))],
}


def _mmss(t):
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


# ---------- finding words in the clip ----------

def _tokens(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def _flatten(words):
    flat = []
    for t, w in words:
        for tok in _tokens(w):
            flat.append((t, tok))
    return flat


def find_phrase(flat, phrase):
    """Returns ('exact' | 'close' | None, time_in_clip)."""
    target = _tokens(phrase)
    if not target:
        return None, None
    n = len(target)
    toks = [x[1] for x in flat]
    for i in range(len(toks) - n + 1):
        if toks[i:i + n] == target:
            return "exact", flat[i][0]
    wanted = " ".join(target)
    best_ratio, best_time = 0.0, None
    for i in range(len(toks) - n + 1):
        ratio = difflib.SequenceMatcher(None, " ".join(toks[i:i + n]), wanted).ratio()
        if ratio > best_ratio:
            best_ratio, best_time = ratio, flat[i][0]
    if best_ratio >= 0.82:
        return "close", best_time
    return None, None


# ---------- looking at the picture ----------

def _color_of(text):
    for color in COLOR_RANGES:
        if re.search(rf"\b{color}\b", text, re.I):
            return color
    return None


def _analyse_frames(path, width, height, duration, colors, want_faces, cancel):
    """Count how many sampled frames of the finished clip contain each colour / a face."""
    cv2 = tracking._cv2()
    import numpy as np

    detector = None
    if want_faces:
        model = tracking.ensure_model()
        sw, sh = tracking._dims(width, height)
        detector = cv2.FaceDetectorYN.create(str(model), "", (sw, sh), tracking.SCORE_THRESHOLD, 0.3, 200)

    frames = face_frames = 0
    color_frames = {c: 0 for c in colors}
    for _, frame in tracking._sample_frames(path, 0.0, duration, width, height, cancel):
        frames += 1
        if colors:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            area = hsv.shape[0] * hsv.shape[1]
            for color in colors:
                mask = None
                for lo, hi in COLOR_RANGES[color]:
                    part = cv2.inRange(hsv, np.array(lo, dtype=np.uint8), np.array(hi, dtype=np.uint8))
                    mask = part if mask is None else cv2.bitwise_or(mask, part)
                if cv2.countNonZero(mask) / area >= VISIBLE_PIXEL_FRACTION:
                    color_frames[color] += 1
        if detector is not None:
            _, faces = detector.detect(frame)
            if faces is not None and any(
                    float(f[2]) >= tracking.MIN_FACE_FRACTION * frame.shape[1] for f in faces):
                face_frames += 1
    return {"frames": frames, "face_frames": face_frames, "color_frames": color_frames}


# ---------- the checklist ----------

def post_text(rules):
    parts = []
    if rules["required_caption_text"]:
        parts.append("\n".join(rules["required_caption_text"]))
    if rules["required_hashtags"]:
        parts.append(" ".join(rules["required_hashtags"]))
    return "\n\n".join(parts)


def failed_report(message):
    return {"overall": REVIEW, "post_text": "",
            "checks": [{"rule": "Automatic checks", "status": REVIEW, "detail": message}]}


def check_clip(rules, out_info, words, out_path, cancel):
    """rules: normalised campaign rules. out_info: probe() of the finished file.
    words: (seconds_into_clip, word) pairs. Returns the report dict."""
    checks = []

    def add(rule, status, detail):
        checks.append({"rule": rule, "status": status, "detail": detail})

    duration = out_info["duration"]
    flat = _flatten(words)

    # --- length ---
    mn, mx = rules["min_seconds"], rules["max_seconds"]
    if mn or mx:
        if mn and mx:
            label = f"Clip length {mn:g}-{mx:g} seconds"
        elif mn:
            label = f"Clip length at least {mn:g} seconds"
        else:
            label = f"Clip length at most {mx:g} seconds"
        if mn and duration < mn - LENGTH_TOLERANCE:
            add(label, FAIL, f"This clip is {duration:.1f}s, shorter than the minimum.")
        elif mx and duration > mx + LENGTH_TOLERANCE:
            add(label, FAIL, f"This clip is {duration:.1f}s, longer than the maximum.")
        else:
            add(label, PASS, f"This clip is {duration:.1f}s.")

    # --- vertical format ---
    if any(VERTICAL_PLATFORMS.search(p) for p in rules["platforms"]):
        w, h = out_info["width"], out_info["height"]
        vertical = h >= w * 1.7
        detail = f"The clip is {w}x{h}."
        if not vertical:
            detail += " These platforms need a vertical video: choose a vertical Output format and create the clip again."
        add("Vertical 9:16 format", PASS if vertical else FAIL, detail)

    # --- hashtags and post text (not part of the video) ---
    if rules["required_hashtags"] or rules["required_caption_text"]:
        add("Required hashtags and post text", REVIEW,
            "These belong in your post, not in the video. Copy the suggested post text below when you publish.")

    # --- spoken phrases ---
    for phrase in rules["required_spoken_phrases"]:
        label = f'Must say "{phrase}"'
        kind, t = find_phrase(flat, phrase)
        if kind == "exact":
            add(label, PASS, f"Found at {_mmss(t)} in the captions or transcript.")
        elif kind == "close":
            add(label, REVIEW, f"Something very similar is at {_mmss(t)}. Check what is actually said.")
        else:
            add(label, FAIL, "Not found in the captions or transcript for this clip. If it is said but was "
                             "misheard, fix it in the captions and check again.")

    # --- visuals (never marked as verified) ---
    plans, colors, want_faces = [], set(), False
    for visual in rules["required_visuals"]:
        color = _color_of(visual)
        if color:
            plans.append((visual, "color", color))
            colors.add(color)
        elif FACE_WORDS.search(visual):
            plans.append((visual, "face", None))
            want_faces = True
        else:
            plans.append((visual, "manual", None))

    stats, stats_error = None, None
    if colors or want_faces:
        try:
            stats = _analyse_frames(out_path, out_info["width"], out_info["height"],
                                    duration, sorted(colors), want_faces, cancel)
        except video.Cancelled:
            raise
        except video.VideoError as e:
            stats_error = str(e)
        except Exception:
            log.exception("Visual check failed")
            stats_error = "The visual check failed (see data/clipper.log)."

    for visual, kind, color in plans:
        label = f'Must be visible: "{visual}"'
        if kind == "manual":
            add(label, REVIEW, "The app can't check this automatically. Watch the clip to confirm.")
            continue
        if not stats or not stats["frames"]:
            add(label, REVIEW, (stats_error or "No frames could be read from the clip.") + " Check it by eye.")
            continue
        if kind == "color":
            count, what = stats["color_frames"][color], f"Something {color}"
        else:
            count, what = stats["face_frames"], "A face"
        share = count / stats["frames"]
        pct = round(share * 100)
        if share >= HIGH_COVERAGE:
            caveat = ("This is a colour check only: it can't confirm the object itself."
                      if kind == "color" else "It can't confirm who it is.")
            add(label, REVIEW, f"{what} is visible in {pct}% of the clip. {caveat}")
        elif share >= LOW_COVERAGE:
            add(label, REVIEW, f"{what} is visible in only {pct}% of the clip. "
                               "Check by eye that it stays in view when it matters.")
        else:
            add(label, FAIL, f"{what} is visible in only {pct}% of the clip, so the required element "
                             "is probably missing.")

    # --- banned content ---
    for item in rules["banned_content"]:
        label = f'Must not contain: "{item}"'
        kind, t = find_phrase(flat, item)
        if kind == "exact":
            add(label, FAIL, f'"{item}" is said at {_mmss(t)}.')
        elif kind == "close":
            add(label, REVIEW, f"Something similar is said at {_mmss(t)}. Check it.")
        else:
            add(label, REVIEW, "Not found in the speech or captions. What is on screen can't be checked "
                               "automatically, so watch for it.")

    # --- everything else ---
    for rule in rules["other_rules"]:
        add(rule, REVIEW, "The app can't check this one. Make sure the clip follows it.")

    if not checks:
        overall = "none"
    elif any(c["status"] == FAIL for c in checks):
        overall = FAIL
    elif any(c["status"] == REVIEW for c in checks):
        overall = REVIEW
    else:
        overall = PASS
    return {"overall": overall, "checks": checks, "post_text": post_text(rules)}