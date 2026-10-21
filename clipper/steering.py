"""Apply campaign rules to the AI highlight settings. Campaign rules win over the screen's settings."""


def _join(items, limit=5):
    return "; ".join(items[:limit])


def apply_to_settings(settings, rules, name):
    s = dict(settings)
    mn, mx, pref = rules["min_seconds"], rules["max_seconds"], rules["preferred_seconds"]
    applied = []

    if mn or mx:
        lo = mn if mn else max(5.0, min(s["min_len"], mx - 1))
        hi = mx if mx else max(s["max_len"], lo + 1)
        if hi <= lo:
            lo = max(3.0, hi - 2)
        s.update(mode="variable", min_len=lo, max_len=hi)
        applied.append(f"length {lo:.0f}-{hi:.0f}s")
    elif pref:
        s.update(mode="fixed", duration=pref)
        applied.append(f"length about {pref:.0f}s")

    hints = []
    if rules["summary"]:
        hints.append(f"Campaign goal: {rules['summary']}")
    if pref and (mn or mx):
        hints.append(f"The ideal length is about {pref:.0f} seconds.")
    if rules["required_spoken_phrases"]:
        hints.append("Prefer moments where someone says: " + _join(rules["required_spoken_phrases"]) + ".")
    if rules["required_visuals"]:
        hints.append("Prefer moments that show: " + _join(rules["required_visuals"]) + ".")
    if rules["banned_content"]:
        hints.append("Avoid moments containing: " + _join(rules["banned_content"]) + ".")
    if rules["required_spoken_phrases"] or rules["required_visuals"] or rules["banned_content"]:
        applied.append("requirements")

    focus = " ".join(hints + ([s["focus"]] if s.get("focus") else []))
    s["focus"] = focus[:900]
    s["campaign_note"] = f'Campaign "{name}" applied: ' + (", ".join(applied) or "goal only") + "."
    return s