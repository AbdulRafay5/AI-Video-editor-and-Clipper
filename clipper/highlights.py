"""Highlight detection: transcript -> sentences -> LLM-picked moments ->
sentence-aligned, length-fitted, de-duplicated clip suggestions."""
import logging
import math
import re

from . import llm, video

log = logging.getLogger("clipper.highlights")

CATEGORY_HELP = {
    "funny": "jokes, punchlines, funny stories or reactions",
    "surprising": "twists, reveals, shocking or unexpected statements",
    "emotional": "heartfelt, sad, touching or vulnerable moments",
    "exciting": "high-energy, tense, hype or action moments",
    "informative": "clear, valuable tips, facts or explanations",
}

SENTENCE_END = re.compile(r"[.!?\u2026][\"')\]]*$")
LAUGH = ("haha", "lol", "lmao", "[laughter]", "(laughter)", "laughs", "laughing")
HOOKS = ("oh my god", "no way", "wait", "what the", "can't believe", "cannot believe", "crazy",
         "insane", "never", "secret", "the truth", "turns out", "i was wrong", "shocked",
         "unbelievable", "here's the thing", "biggest", "worst", "best")

PRE_ROLL = 0.25          # seconds of lead-in before the first word
TAIL = 0.5               # seconds kept after the last word
CHUNK_WORDS = 1100       # transcript words per model request
CHUNK_OVERLAP_WORDS = 150


# ---------- settings ----------

def normalise_settings(raw):
    raw = raw or {}

    def num(key, default, lo, hi):
        try:
            v = float(raw.get(key, default))
        except (TypeError, ValueError):
            raise ValueError(f"'{key}' must be a number.")
        return min(max(v, lo), hi)

    s = {
        "mode": "variable" if raw.get("mode") == "variable" else "fixed",
        "duration": num("duration", 30, 5, 300),
        "min_len": num("min_len", 15, 5, 300),
        "max_len": num("max_len", 90, 10, 600),
        "count": int(num("count", 5, 1, 20)),
    }
    if s["max_len"] <= s["min_len"]:
        raise ValueError("Maximum length must be longer than the minimum length.")
    cats = raw.get("categories")
    if cats is None:
        cats = list(CATEGORY_HELP)
    s["categories"] = [c for c in CATEGORY_HELP if c in cats]
    if not s["categories"]:
        raise ValueError("Pick at least one highlight category.")
    s["focus"] = str(raw.get("focus") or "").strip()[:500]
    return s


# ---------- transcript -> sentences -> chunks ----------

def build_sentences(segments, max_gap=0.8, max_words=45):
    words = []
    for seg in segments:
        for w in seg.get("words", []):
            text = (w.get("word") or "").strip()
            if text and w.get("start") is not None and w.get("end") is not None:
                words.append((float(w["start"]), float(w["end"]), text))

    sentences, cur = [], []

    def flush():
        if cur:
            sentences.append({"id": len(sentences), "start": cur[0][0], "end": cur[-1][1],
                              "text": " ".join(x[2] for x in cur)})
            cur.clear()

    for i, (s, e, t) in enumerate(words):
        cur.append((s, e, t))
        gap = words[i + 1][0] - e if i + 1 < len(words) else None
        if SENTENCE_END.search(t) or (gap is not None and gap > max_gap) or len(cur) >= max_words:
            flush()
    flush()
    return sentences


def make_chunks(sentences):
    """Return (first_index, last_index) ranges of about CHUNK_WORDS words, with overlap."""
    chunks, i, n = [], 0, len(sentences)
    while i < n:
        j, w = i, 0
        while j < n and (w < CHUNK_WORDS or j == i):
            w += len(sentences[j]["text"].split())
            j += 1
        chunks.append((i, j - 1))
        if j >= n:
            break
        k, ow = j - 1, 0
        while k > i and ow < CHUNK_OVERLAP_WORDS:
            ow += len(sentences[k]["text"].split())
            k -= 1
        i = max(k + 1, i + 1)
    return chunks


# ---------- asking the model ----------

def _mmss(t):
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def _system_prompt(settings):
    cats = "\n".join(f"- {c}: {CATEGORY_HELP[c]}" for c in settings["categories"])
    if settings["mode"] == "fixed":
        length = f"Moments should usually run about {int(settings['duration'])} seconds."
    else:
        length = (f"Moments should run between {int(settings['min_len'])} and "
                  f"{int(settings['max_len'])} seconds.")
    text = (
        "You are a short-form video editor choosing the best moments from a transcript for vertical "
        "clips (TikTok, Reels, Shorts). Each transcript line looks like [line number | time] text.\n\n"
        "Rules:\n"
        "- A moment is a range of consecutive lines, start_id to end_id.\n"
        "- It must start at the beginning of a thought (never mid-sentence) and include the setup AND "
        "the payoff (punchline, reveal, reaction or conclusion). Never cut off before the payoff.\n"
        f"- {length} Use the times to judge the length.\n"
        "- Skip greetings, filler, sponsor reads, repetition and small talk.\n"
        "- Moments must not overlap each other.\n"
        "- Score from 1 to 10. Be strict: 5 is average and 8 or more is rare. "
        "If nothing is good, return an empty list.\n"
        "- The reason is one short sentence.\n\n"
        f"Allowed categories:\n{cats}\n"
    )
    if settings["focus"]:
        text += f"\nSpecial instructions from the editor (follow these first): {settings['focus']}\n"
    return text + "\nAnswer only with JSON."


def _schema(categories):
    return {
        "type": "object",
        "properties": {"moments": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "start_id": {"type": "integer"},
                "end_id": {"type": "integer"},
                "category": {"type": "string", "enum": categories},
                "score": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["start_id", "end_id", "category", "score", "reason"],
        }}},
        "required": ["moments"],
    }


def _ask_chunk(S, a, b, n, settings):
    lines = "\n".join(f"[{S[i]['id']}|{_mmss(S[i]['start'])}] {S[i]['text']}" for i in range(a, b + 1))
    user = f"Transcript:\n{lines}\n\nPick up to {n} moments."
    result, stats = llm.chat_json(_system_prompt(settings), user, _schema(settings["categories"]))
    log.info("Chunk %s-%s: %s moments, %ss, %s tok/s", a, b,
             len(result.get("moments", [])), stats["seconds"], stats["tokens_per_sec"])

    out = []
    for m in result.get("moments", []):
        try:
            s, e, score = int(m["start_id"]), int(m["end_id"]), int(m["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if s > e:
            s, e = e, s
        s, e = max(s, a), min(e, b)
        cat = m.get("category")
        if e < s or cat not in settings["categories"]:
            continue
        text = " ".join(S[i]["text"] for i in range(s, e + 1))
        if len(text.split()) < 8:
            continue
        out.append({"s": s, "e": e, "category": cat, "llm_score": min(max(score, 1), 10),
                    "reason": str(m.get("reason") or "")[:300]})
    return out


# ---------- timing, fitting, scoring ----------

def _window_times(S, s, e, video_duration):
    """Start/end seconds for sentences s..e, with a little lead-in and tail,
    never starting or ending inside a neighbouring sentence's words."""
    start = S[s]["start"] - PRE_ROLL
    if s > 0:
        start = max(start, S[s - 1]["end"] + 0.02)
    start = max(0.0, start)
    end = S[e]["end"] + TAIL
    if e + 1 < len(S):
        end = min(end, S[e + 1]["start"] - 0.02)
    end = max(min(end, video_duration), S[e]["end"])
    return round(start, 2), round(end, 2)


def fit_window(S, s, e, settings, video_duration):
    """Adjust whole sentences so the clip fits the length setting. Never cuts mid-sentence."""
    if settings["mode"] == "fixed":
        target = settings["duration"]
        slack = max(3.0, 0.15 * target)
        lo, hi = max(3.0, target - slack), target + slack
    else:
        target = None
        lo, hi = settings["min_len"], settings["max_len"]

    def length(a, b):
        t0, t1 = _window_times(S, a, b, video_duration)
        return t1 - t0

    d = natural = length(s, e)
    note = None

    if d < lo:  # too short: add neighbouring sentences (context before, reaction after)
        flip = 0
        while d < lo:
            options = []
            if s > 0 and S[s]["start"] - S[s - 1]["end"] <= 2.5 and length(s - 1, e) <= hi:
                options.append((s - 1, e))
            if e + 1 < len(S) and S[e + 1]["start"] - S[e]["end"] <= 2.5 and length(s, e + 1) <= hi:
                options.append((s, e + 1))
            if not options:
                break
            s, e = options[flip % len(options)]
            flip += 1
            d = length(s, e)
        if d > natural + 0.5:
            note = f"Extended from {natural:.0f}s to {d:.0f}s to include surrounding context."
    elif d > hi:  # too long: drop lead-in sentences, keep the payoff
        s2, d2 = s, d
        while d2 > hi and s2 < e:
            s2 += 1
            d2 = length(s2, e)
        if d2 <= hi and d2 >= lo * 0.75:
            s, d = s2, d2
            note = f"Trimmed the lead-in from {natural:.0f}s to {d:.0f}s to fit your length limit."

    if d < lo * 0.9 or d > hi * 1.05:
        want = f"{target:.0f}s target" if target else f"{lo:.0f}-{hi:.0f}s range"
        extra = (f"It needs about {d:.0f}s to keep the setup and payoff, which is outside your {want}. "
                 f"Suggested duration: {d:.0f}s.")
        note = f"{note} {extra}" if note else extra

    t0, t1 = _window_times(S, s, e, video_duration)
    if t1 - t0 < 3.0:
        return None
    return {"s": s, "e": e, "start": t0, "end": t1, "duration": round(t1 - t0, 2), "note": note}


def heuristic_score(text):
    """Small nudge (0-1) from simple signals: exclamations, questions, laughter, hook phrases."""
    t = text.lower()
    score = min(t.count("!"), 3) * 0.12
    score += min(t.count("?"), 3) * 0.06
    score += min(sum(t.count(w) for w in LAUGH), 3) * 0.15
    score += min(sum(1 for w in HOOKS if w in t), 3) * 0.1
    return min(score, 1.0)


def pick_top(cands, count, overlap_limit=0.3):
    """Highest scores first; skip anything that mostly overlaps one already chosen."""
    chosen = []
    for c in sorted(cands, key=lambda c: c["score"], reverse=True):
        clash = False
        for k in chosen:
            overlap = min(c["end_time"], k["end_time"]) - max(c["start_time"], k["start_time"])
            if overlap > 0 and overlap / min(c["duration"], k["duration"]) > overlap_limit:
                clash = True
                break
        if not clash:
            chosen.append(c)
        if len(chosen) >= count:
            break
    return chosen


# ---------- the whole pipeline ----------

def analyze(sentences, video_duration, settings, cancel, on_progress):
    """Returns {'highlights': [...], 'chunks': n, 'failed_chunks': n}."""
    if not sentences:
        raise video.VideoError("The transcript has no sentences to analyse.")
    chunks = make_chunks(sentences)
    per_chunk = max(2, min(5, math.ceil(settings["count"] * 2 / len(chunks))))

    raw, failures, last_err = [], 0, None
    for ci, (a, b) in enumerate(chunks):
        if cancel.is_set():
            raise video.Cancelled()
        on_progress(ci / len(chunks) * 0.95, f"Analysing part {ci + 1} of {len(chunks)}")
        try:
            raw.extend(_ask_chunk(sentences, a, b, per_chunk, settings))
        except llm.LLMError as e:
            failures += 1
            last_err = e
            log.warning("Chunk %s failed: %s", ci + 1, e)
    if failures == len(chunks):
        raise last_err

    on_progress(0.97, "Fitting clip lengths and removing duplicates")
    cands = []
    for m in raw:
        fit = fit_window(sentences, m["s"], m["e"], settings, video_duration)
        if not fit:
            continue
        text = " ".join(sentences[i]["text"] for i in range(fit["s"], fit["e"] + 1))
        final = 0.8 * (m["llm_score"] / 10) + 0.2 * heuristic_score(text)
        cands.append({
            "start_time": fit["start"], "end_time": fit["end"], "duration": fit["duration"],
            "category": m["category"], "score": int(round(final * 100)),
            "llm_score": m["llm_score"], "reason": m["reason"], "note": fit["note"],
        })
    return {"highlights": pick_top(cands, settings["count"]),
            "chunks": len(chunks), "failed_chunks": failures}