"""Run with:  python -m clipper.llm_check
Checks Ollama, then asks the model to pick highlights from a tiny sample transcript."""
import json
import sys

import config
from . import llm

SAMPLE = [
    "So last week I decided to cook dinner for my girlfriend's parents.",
    "I wanted to impress them, so I bought a huge fish.",
    "Turns out I had no idea how to cook a fish.",
    "I put it in the oven, and about ten minutes later the smoke alarm goes off.",
    "Her dad walks into the kitchen, looks at the oven, and just says, is that supposed to be on fire?",
    "I said, no, it's a house special.",
    "Anyway, we ordered pizza.",
    "The weather has been really nice lately.",
]

CATEGORIES = ["funny", "surprising", "emotional", "exciting", "informative"]

SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_id": {"type": "integer"},
                    "end_id": {"type": "integer"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "score": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["start_id", "end_id", "category", "score", "reason"],
            },
        }
    },
    "required": ["moments"],
}

SYSTEM = (
    "You find the most engaging moments in a video transcript for short-form clips. "
    "Each line is numbered. A moment is a range of consecutive lines that includes the "
    "setup AND the payoff, never just half of a joke or story. Score each moment from 1 "
    "(weak) to 10 (excellent). Only pick moments that are genuinely engaging. "
    "Answer only with JSON."
)


def main():
    print(f"Model: {config.LLM_MODEL}   Server: {config.OLLAMA_URL}\n")
    st = llm.status()
    print(st["message"])
    if not (st["running"] and st["installed"]):
        sys.exit(1)

    user = "Transcript:\n" + "\n".join(f"[{i}] {line}" for i, line in enumerate(SAMPLE))
    user += "\n\nList up to 2 of the best moments."
    print("\nAsking the model (the first call also loads it into the GPU)...")
    try:
        result, stats = llm.chat_json(SYSTEM, user, SCHEMA)
    except llm.LLMError as e:
        print("FAILED:", e)
        sys.exit(1)

    print("\nModel answer:")
    print(json.dumps(result, indent=2))
    print(f"\nTime: {stats['seconds']}s | prompt tokens: {stats['prompt_tokens']} | "
          f"output tokens: {stats['output_tokens']} | speed: {stats['tokens_per_sec']} tokens/s")
    llm.unload()
    print("Model unloaded from the GPU.")


if __name__ == "__main__":
    main()