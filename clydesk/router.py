"""Smart routing: decide which backend/model should handle a message.

Order of decision (cheapest first):
1. Attached image        -> vision model
2. Pure math expression  -> calculator skill, no LLM at all
3. Obvious image-gen ask -> ComfyUI pipeline (regex)
4. Everything else       -> a small router LLM classifies chat vs code vs image
"""

import re

ROUTES = ("chat", "code", "image", "vision", "calc")

# Hard image-gen route: an explicit generation verb followed by an image
# noun ("generate an image of...", "draw me a picture of..."). Bare verbs
# like "draw"/"illustrate" alone are NOT enough — "how do I draw a circle
# in matplotlib" is a code question. Ambiguous cases fall through to the
# LLM router.
_IMAGE_RE = re.compile(
    r"\b(draw|sketch|paint|generate|make|create|render|produce)\b"
    r"(\s+me)?(\s+an?|\s+some|\s+\d+)?\s+"
    r"(image|images|picture|pictures|photo|photos|drawing|drawings|"
    r"illustration|illustrations|wallpaper|wallpapers)s?\b",
    re.IGNORECASE,
)
# ...unless the message smells like a programming/diagram question
_IMAGE_VETO_RE = re.compile(
    r"\b(matplotlib|css|html|canvas|svg|code|function|python|javascript|"
    r"library|api|docker|dockerfile|diagram|architecture|chart|plot)\b",
    re.IGNORECASE,
)

# A message that is basically just arithmetic: "what is 23*(4+5)?", "2^10 =?"
_MATH_STRIP_RE = re.compile(
    r"^(what\s+is|what's|whats|calculate|compute|evaluate|solve|how much is)\b",
    re.IGNORECASE,
)
_MATH_EXPR_RE = re.compile(r"^[\d\s\.\+\-\*/\^\(\)%,]+$")

_ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["chat", "code", "image"]},
    },
    "required": ["route"],
}

_ROUTER_SYSTEM = (
    "Classify the user's message into exactly one route:\n"
    "- \"image\": they want a picture/image/photo/art GENERATED or edited.\n"
    "- \"code\": they want code written, debugged, reviewed, or explained; or "
    "asked a technical programming question.\n"
    "- \"chat\": everything else (questions, writing, conversation, advice).\n"
    "Respond with JSON only."
)


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}")

# "make it darker", "regenerate", "more realistic", "without the hat"...
# Only meaningful right after an image generation.
_REFINE_RE = re.compile(
    r"\b(regenerate|redo|try again|another one|instead|more|less|darker|"
    r"lighter|brighter|bigger|smaller|closer|wider|make (it|them|the)|"
    r"change|add|remove|without|different|same but)\b",
    re.IGNORECASE,
)


def is_image_refinement(text: str) -> bool:
    """Does this look like feedback on the image that was just generated?"""
    return len(text) < 250 and bool(_REFINE_RE.search(text))


# A request to establish a mathematical truth — the cue to formalize and
# verify with Lean rather than just asserting an answer. "Strong" cues are
# unambiguously mathematical and fire on their own; "weak" cues (prove/show)
# also occur non-mathematically ("prove yourself") so they need a math noun.
_PROOF_STRONG_RE = re.compile(
    r"\b(disprove|theorem|lemma|for all|for every|for any|there exists|"
    r"q\.?e\.?d\.?|by induction)\b",
    re.IGNORECASE,
)
_PROOF_WEAK_RE = re.compile(
    r"\b(prove|proof|verify|show that|demonstrate that|is it true that)\b",
    re.IGNORECASE,
)
# Trailing boundary omitted on purpose so plurals/derivatives match
# ("primes", "infinitely", "divisible").
_MATHY_RE = re.compile(
    r"\b(prime|even|odd|integer|rational|irrational|divisi|divides|"
    r"inequalit|identity|congruen|modulo|natural number|real number|"
    r"sum|product|square|cube|sqrt|infinite|converg|continuous|"
    r"gcd|lcm|factorial|fibonacci|induction|equation|derivative|"
    r"\bset\b|group|matrix|polynomial)",
    re.IGNORECASE,
)


def is_proof_request(text: str) -> bool:
    """Does the message ask us to prove/verify a mathematical statement?
    Used to nudge the model toward formalizing and checking it in Lean."""
    if len(text) > 2000:
        return False
    if _PROOF_STRONG_RE.search(text):
        return True
    return bool(_PROOF_WEAK_RE.search(text)) and bool(_MATHY_RE.search(text))


def extract_math_expression(text: str) -> str | None:
    """If the message is essentially a pure arithmetic question, return the
    expression, else None."""
    t = text.strip().rstrip("?=").strip()
    t = _MATH_STRIP_RE.sub("", t).strip()
    if not t or len(t) > 120:
        return None
    if _DATE_RE.search(t):  # "2025-06-30 - 2025-07-02" is not subtraction
        return None
    if _MATH_EXPR_RE.match(t) and any(c.isdigit() for c in t) \
            and any(op in t for op in "+-*/^%"):
        return t.replace(",", "")
    return None


def quick_route(text: str, has_image: bool) -> str | None:
    """Deterministic fast paths; returns None when the LLM router is needed."""
    if has_image:
        return "vision"
    if extract_math_expression(text):
        return "calc"
    if _IMAGE_RE.search(text) and not _IMAGE_VETO_RE.search(text):
        return "image"
    return None


async def llm_route(ollama, router_model: str, text: str) -> str:
    """Classify with the small router model; fall back to chat on any issue."""
    import json
    try:
        raw = await ollama.complete(
            router_model,
            [{"role": "system", "content": _ROUTER_SYSTEM},
             {"role": "user", "content": text[:2000]}],
            format_schema=_ROUTER_SCHEMA,
        )
        route = json.loads(raw).get("route", "chat")
        return route if route in ("chat", "code", "image") else "chat"
    except Exception:
        return "chat"


async def route_message(ollama, cfg: dict, text: str, has_image: bool) -> str:
    r = quick_route(text, has_image)
    if r:
        return r
    return await llm_route(ollama, cfg["routes"]["router"], text)
