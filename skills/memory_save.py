"""Persistent memory: facts about the user that survive across chats."""

SKILL = {
    "name": "remember",
    "description": (
        "Save a lasting fact about the user (name, preferences, projects, "
        "setup). Use when the user shares something worth remembering across "
        "conversations, or explicitly says 'remember ...'. Keep facts short."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "fact": {"type": "string", "description": "One short, standalone fact"},
        },
        "required": ["fact"],
    },
    "timeout": 5,
}


def run(args: dict) -> str:
    import json
    import os

    fact = str(args.get("fact", "")).strip()
    if not fact:
        return "Error: empty fact"
    if len(fact) > 300:
        return "Error: keep facts under 300 chars"
    path = os.path.expanduser("~/.local/share/clydesk/memory.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    facts = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                facts = json.load(f)
        except (OSError, ValueError):
            facts = []
    if fact in facts:
        return "Already remembered."
    facts.append(fact)
    facts = facts[-100:]  # cap
    with open(path, "w") as f:
        json.dump(facts, f, indent=1)
    return f"Remembered ({len(facts)} facts stored)."
