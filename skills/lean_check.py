"""Verify a Lean 4 proof by compiling it against Mathlib.

This is the deterministic ground truth for math: the chat model writes a
formal statement + proof in Lean, Lean — not the model — decides whether
the proof is actually correct. A proof "passes" only if Lean reports no
errors AND the proof contains no `sorry`/`admit` placeholders.

The checker itself lives in the sibling `clyde` package (`clyde.lean`,
already a dependency); this skill only supplies clydesk's config (the
`lean` section: project_dir, elan_bin, timeout, enabled) and delegates.
The proof is compiled inside a Lake project (default
``~/.local/share/clydesk/lean``) that has Mathlib as a built dependency.
"""

from clyde import lean

SKILL = {
    "name": "lean_check",
    "description": lean.TOOL_SCHEMA["function"]["description"],
    "parameters": lean.TOOL_SCHEMA["function"]["parameters"],
    # Loading Mathlib oleans into a fresh `lean` process is slow (~15-40s),
    # so the ceiling is high. The subprocess is hard-killed at this deadline.
    "timeout": 100,
}

# Back-compat aliases (tests and older callers).
_uses_cheat = lean.uses_cheat
_find_lake = lean.find_lake


def _lean_conf() -> dict:
    """Clydesk's `lean` config section, tolerating a missing config file."""
    defaults = {
        "enabled": True,
        "project_dir": "~/.local/share/clydesk/lean",
        "elan_bin": "~/.elan/bin",
        "timeout": 90,
    }
    try:
        from clydesk import config
        defaults.update(config.load_config().get("lean") or {})
    except Exception:
        pass
    return defaults


def run(args: dict) -> str:
    return lean.run(args, conf=_lean_conf())
