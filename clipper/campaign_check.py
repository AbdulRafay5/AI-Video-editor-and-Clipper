"""Run with:  python -m clipper.campaign_check "C:\\path\\to\\brief.pdf"
Reads a campaign document, asks the local AI model for its rules, and prints them."""
import sys
import time
from pathlib import Path

from . import campaigns, docs, llm

LABELS = {
    "platforms": "Platforms",
    "required_hashtags": "Required hashtags",
    "required_caption_text": "Required caption text",
    "required_spoken_phrases": "Must be said aloud",
    "required_visuals": "Must be visible",
    "banned_content": "Must not contain",
    "other_rules": "Other rules (manual check)",
}


def main():
    if len(sys.argv) < 2:
        print('Usage: python -m clipper.campaign_check "path\\to\\brief.pdf"   (PDF, .docx, .txt)')
        sys.exit(2)
    path = Path(" ".join(sys.argv[1:]).strip().strip('"'))
    if not path.is_file():
        print(f"File not found: {path}")
        sys.exit(1)

    try:
        text = docs.read_document(path.name, path.read_bytes())
    except docs.DocError as e:
        print("Could not read the document:", e)
        sys.exit(1)
    print(f"Read {len(text):,} characters ({len(text.split()):,} words) from {path.name}.")

    st = llm.status()
    print(st["message"])
    if not (st["running"] and st["installed"]):
        sys.exit(1)

    print("\nAsking the model to extract the rules...")
    started = time.time()
    try:
        rules, info = campaigns.extract_rules(
            text, on_progress=lambda frac, msg: print(f"  {msg}"))
    except llm.LLMError as e:
        print("FAILED:", e)
        sys.exit(1)
    finally:
        llm.unload()

    print("\n=== Extracted rules ===")
    print("Summary:", rules["summary"] or "(none)")
    print(f"Length: min {rules['min_seconds'] or '-'}s | max {rules['max_seconds'] or '-'}s | "
          f"preferred {rules['preferred_seconds'] or '-'}s")
    for key, label in LABELS.items():
        items = rules[key]
        print(f"{label}:" + (" (none)" if not items else ""))
        for item in items:
            print(f"   - {item}")

    print(f"\nDone in {time.time() - started:.1f}s. Parts read: {info['parts'] - info['failed_parts']}/{info['parts']}.")
    if info["truncated"]:
        print("Note: the document was long, so only the first part of it was read.")
    print("Check the list against the document: anything missing, wrong or invented?")


if __name__ == "__main__":
    main()