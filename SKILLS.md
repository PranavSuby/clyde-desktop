# Writing skills for Clyde Desktop

Skills are deterministic tools the model can call. The point: things like
math, dates, and unit conversions should be **computed, not guessed** — an
LLM doing arithmetic is a bug, not a feature. Any `.py` file you drop into
`skills/` becomes a tool the chat model can call.

Files matching `skills/local_*.py` are gitignored: put personal skills
(home automation, private APIs, machine-specific glue) there and they load
like any other skill without ever being committed.

## Anatomy of a skill

One file = one skill. It must define a `SKILL` dict and a `run` function:

```python
# skills/dice.py
SKILL = {
    "name": "roll_dice",
    "description": (
        "Roll dice and return the results. Use whenever the user asks to "
        "roll dice, flip a coin, or pick a random number."
    ),
    "parameters": {                      # JSON schema (OpenAI function format)
        "type": "object",
        "properties": {
            "sides": {"type": "integer", "description": "Sides per die (default 6)"},
            "count": {"type": "integer", "description": "Number of dice (default 1)"},
        },
        "required": [],
    },
}

def run(args: dict) -> str:
    import random
    sides = int(args.get("sides") or 6)
    count = int(args.get("count") or 1)
    rolls = [random.randint(1, sides) for _ in range(count)]
    return f"rolls: {rolls}, total: {sum(rolls)}"
```

Reload with the puzzle-piece button in the app header (no restart needed).

Optional `SKILL` fields: `"timeout": 5` (seconds — skills run in a forked
subprocess and are **hard-killed** at the deadline, so runaway computations
can't freeze the app) and `"in_process": true` (runs on a thread in the app
process instead — required for skills that need app state, like
`search_knowledge`; timeout is soft there).

## Rules that make skills work well

1. **The `description` decides everything.** The model reads it to choose
   when to call your skill. Say *when to use it* explicitly ("Use whenever
   the user asks ..."), not just what it does. If the model isn't calling
   your skill, improve the description first.
2. **Return a string** — it's inserted into the model's context verbatim.
   Keep it short and unambiguous; include units/labels so the model can't
   misread it.
3. **Never raise** for expected bad input — return `"Error: ..."` strings.
   The model will read the error and correct itself. (Uncaught exceptions
   are caught by the loader and returned as errors anyway.)
4. **Be fast and deterministic.** Skills run in the UI process. Anything
   slower than ~a second should be rethought (or made async-external).
   Randomness (like dice) is fine when randomness is the point.
5. **No model calls inside skills.** Skills are the deterministic half of
   the system; the model is the fuzzy half. Keep them separate.
6. **Imports inside `run`** keep app startup fast and let a broken import
   surface as a per-call error instead of killing the skill list.

## Built-in skills

| skill | what it does |
|---|---|
| `calculator` | exact math via sympy: arithmetic, `solve()`, `integrate()`, `diff()` |
| `convert_units` | length/mass/temp/speed/volume/data conversions |
| `current_datetime` | current date/time (models never know this) |
| `lean_check` | verifies a **proof** by compiling Lean 4 + Mathlib — real ground truth, not the model's word (see README) |

## How skills interact with routing

The router runs *before* the model sees your message; pure-arithmetic
messages go straight to `calculator` without any LLM at all. Everything
else reaches the chat/code model with all skills attached as tools, and the
system prompt tells it to prefer tools for anything a tool covers.
