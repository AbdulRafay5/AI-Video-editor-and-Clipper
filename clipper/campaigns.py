"""Campaign rules: pull them out of a brief with the local AI model and keep them tidy."""
import logging
import re

from . import llm

log = logging.getLogger("clipper.campaigns")

LIST_FIELDS = {
    "platforms": "platforms or formats the clips are for (for example TikTok, Instagram Reels, YouTube Shorts)",
    "required_hashtags": "hashtags and @mentions that must be used, exactly as written",
    "required_caption_text": "text that must appear in the post caption or description (disclosures, links, wording)",
    "required_spoken_phrases": "words or phrases that must be said aloud in the clip",
    "required_visuals": "people, objects or on-screen elements that must appear in the video",
    "banned_content": "things the clips must NOT contain",
    "other_rules": "any other requirement that fits nowhere else (tone, music, deadlines, payout conditions)",
}
NUMBER_FIELDS = ("min_seconds", "max_seconds", "preferred_seconds")

PART_WORDS = 1200   # words of the document sent to the model at once
MAX_PARTS = 15      # a longer document is cut off after this many parts

SYSTEM = (
    "You read a campaign brief for a short-form video clipping campaign and extract its rules.\n"
    "Rules for you:\n"
    "- Report only what the document explicitly says. Never invent or assume rules.\n"
    "- Use 0 for a number the document doesn't give, and an empty list when it says nothing.\n"
    "- Numbers are in seconds (convert minutes to seconds).\n"
    "- Copy hashtags, @mentions and required wording exactly as written.\n"
    "- summary: one or two sentences describing the campaign.\n"
    "- min_seconds / max_seconds: the shortest and longest allowed clip. "
    "preferred_seconds: the ideal clip length.\n"
    + "\n".join(f"- {name}: {desc}." for name, desc in LIST_FIELDS.items())
    + "\nAnswer only with JSON."
)


def _schema():
    props = {"summary": {"type": "string"}}
    for f in NUMBER_FIELDS:
        props[f] = {"type": "number"}
    for f in LIST_FIELDS:
        props[f] = {"type": "array", "items": {"type": "string"}}
    return {"type": "object", "properties": props, "required": list(props)}


# ---------- tidying ----------

def _clean_list(values, hashtags=False):
    if isinstance(values, str):
        values = values.splitlines()
    out, seen = [], set()
    for v in values or []:
        s = " ".join(str(v).split())[:300]
        if not s:
            continue
        if hashtags:
            tags = [t for t in re.split(r"[\s,;]+", s) if t[:1] in ("#", "@")]
            if tags:
                items = tags
            elif " " not in s:
                items = ["#" + s]   # a bare word like "ad" becomes "#ad"
            else:
                items = [s]
        else:
            items = [s]
        for item in items:
            key = item.lower()
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out[:50]


def normalise_rules(raw):
    """Validate a rules dict (from the AI or from the editing form)."""
    raw = raw or {}
    out = {"summary": " ".join(str(raw.get("summary") or "").split())[:1000]}
    for f in NUMBER_FIELDS:
        try:
            v = float(raw.get(f) or 0)
        except (TypeError, ValueError):
            raise ValueError(f"'{f}' must be a number.")
        out[f] = round(min(max(v, 0.0), 3600.0), 1)
    if out["min_seconds"] and out["max_seconds"] and out["min_seconds"] > out["max_seconds"]:
        raise ValueError("The minimum length can't be longer than the maximum length.")
    for f in LIST_FIELDS:
        out[f] = _clean_list(raw.get(f), hashtags=(f == "required_hashtags"))
    return out


EMPTY_RULES = normalise_rules({})


# ---------- reading a long document in parts ----------

def split_text(text, max_words=PART_WORDS):
    lines = []
    for line in text.splitlines():
        words = line.split()
        if len(words) > max_words:      # one giant paragraph: slice it
            lines += [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]
        else:
            lines.append(line)
    parts, cur, count = [], [], 0
    for line in lines:
        n = len(line.split())
        if cur and count + n > max_words:
            parts.append("\n".join(cur))
            cur, count = [], 0
        cur.append(line)
        count += n
    if cur:
        parts.append("\n".join(cur))
    return [p for p in parts if p.strip()]


def _merge(partials):
    merged = {"summary": "", "preferred_seconds": 0}
    mins, maxs = [], []
    for f in LIST_FIELDS:
        merged[f] = []
    for p in partials:
        if not merged["summary"] and p.get("summary"):
            merged["summary"] = p["summary"]
        if not merged["preferred_seconds"] and p.get("preferred_seconds"):
            merged["preferred_seconds"] = p["preferred_seconds"]
        if p.get("min_seconds"):
            mins.append(p["min_seconds"])
        if p.get("max_seconds"):
            maxs.append(p["max_seconds"])
        for f in LIST_FIELDS:
            merged[f] += p.get(f) or []
    # If parts disagree, keep the strictest limits.
    merged["min_seconds"] = max(mins) if mins else 0
    merged["max_seconds"] = min(maxs) if maxs else 0
    if merged["min_seconds"] and merged["max_seconds"] and merged["min_seconds"] > merged["max_seconds"]:
        merged["other_rules"].append("The document gives conflicting length limits. Check them by hand.")
        merged["min_seconds"] = merged["max_seconds"] = 0
    return merged


def extract_rules(text, on_progress=None):
    """Ask the local model to read the document. Returns (rules, info).
    Raises llm.LLMError if no part could be read."""
    parts = split_text(text)
    truncated = len(parts) > MAX_PARTS
    parts = parts[:MAX_PARTS]

    partials, failed, last_err = [], 0, None
    for i, part in enumerate(parts):
        if on_progress:
            on_progress(i / len(parts), f"Reading part {i + 1} of {len(parts)}")
        try:
            result, stats = llm.chat_json(
                SYSTEM, f"Campaign document (part {i + 1} of {len(parts)}):\n\n{part}", _schema())
        except llm.LLMError as e:
            failed += 1
            last_err = e
            log.warning("Part %s failed: %s", i + 1, e)
            continue
        log.info("Part %s read in %ss", i + 1, stats["seconds"])
        partials.append(result)
    if not partials:
        raise last_err
    rules = normalise_rules(_merge(partials))
    return rules, {"parts": len(parts), "failed_parts": failed, "truncated": truncated}