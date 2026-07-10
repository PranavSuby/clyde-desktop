# Clyde Desktop — Improvement Plan

From a deep code review (independent reviewer pass, 2026-07-02) plus a gap
analysis against Claude Desktop. Ordered so each phase is shippable alone.

## Phase 0 — Bug & safety fixes (do first, ~1 day)

1. **Calculator eval escape (security, critical).** The `solve(...)` branch
   in `skills/calculator.py` uses raw `eval` with empty builtins — escapable
   via attribute chains (`().__class__.__base__.__subclasses__()...`) and
   reachable by the model as a tool call, so prompt injection in pasted text
   → code execution. Replace with `sympy.sympify`/`parse_expr` plus a
   whitelist of allowed function heads. Never raw `eval`.
2. **Event-loop freeze.** Skills run synchronously on the asyncio loop;
   "what is 9^9^9?" evaluates a 369-million-digit integer and freezes the
   entire app. Run skills in `asyncio.to_thread` with a hard timeout (~5s).
3. **Vision follow-ups are broken.** Attached images are stored in the DB
   but never re-sent on later turns, and routing only considers the current
   message — "what breed is it?" after an image answer goes to a blind
   model. Reattach recent images when rebuilding history and route to
   vision when the chat contains any image.
4. **Image-gen regex false positives.** "How do I draw a circle in
   matplotlib?", "make a picture of the architecture", "base image of this
   Dockerfile" all hard-route to ComfyUI (30s boot + wasted diffusion).
   Keep only unambiguous patterns as a hard route; feed the rest through
   the LLM router with the regex as a hint.
5. **Stop/cancel button.** No way to stop a generation or a 10-minute
   ComfyUI run. Abort the Ollama stream (close the response) and call
   ComfyUI `/interrupt`; Esc as shortcut.
6. **Streaming perf + scroll hijack.** Every token re-sends the full
   markdown and forces scroll-to-bottom (O(n²) traffic; can't read while
   generating). Throttle to ~10 Hz and only autoscroll when already at
   bottom.
7. **Chat switching during a stream** clears the elements being streamed
   into (exceptions per token). Block switching while busy, or detach the
   stream handlers safely.
8. **Enter handling.** Shift+Enter should insert a newline; today every
   Enter sends.
9. **ComfyUI error paths.** Mid-poll network errors and non-JSON responses
   escape `ComfyError` and vanish — the UI shows "generating..." forever.
   Wrap all comfy HTTP calls; add a catch-all in `Orchestrator.handle` that
   surfaces every unexpected exception as an error bubble.
10. **DB/concurrency holes.** Enable `PRAGMA foreign_keys` on every
    connection (orphaned messages after delete); per-chat send lock so two
    tabs can't interleave one conversation; clear pending images on chat
    switch; fix the route badge showing the chat model for calc/image
    routes; guard the calc route against date-like strings ("2025-06-30 -
    2025-07-02" currently becomes arithmetic).
11. **Startup resilience.** Page load awaits `ensure_running()` (up to
    15s blank page) and `list_models()` with a 600s timeout. Load the UI
    immediately with a status banner + retry button instead.

## Phase 1 — Daily-driver essentials (~2 days)

1. **Chat auto-titles** — one cheap router-model call after the first
   exchange (`db.rename_chat` already exists); sidebar findability.
2. **Image refinement route** — "make it darker" / "regenerate" after a
   generation should reuse the stored tags (already persisted in `extra`)
   instead of routing to chat; port the strong/weak refine logic from the
   old localLLM app.
3. **Model management UI** — pull models with progress bar, show
   loaded models/VRAM (`ollama ps`), set `keep_alive` so the chat model
   isn't cold-loaded every time.
4. **Copy + export** — copy button on code blocks; export chat to
   markdown. Code answers are the app's main output.
5. **Keyboard shortcuts + chat search** — Ctrl+N new chat, Esc cancel,
   Ctrl+K fuzzy search across chat titles/content.
6. **Context management** — full history is resent every turn and Ollama
   silently truncates the head (system prompt dies first). Trim/compact
   per chat using the ctx% signal that's already displayed.

## Phase 2 — Killer features (~week+, pick à la carte)

1. **RAG over local files** — `nomic-embed-text` is already pulled. Attach
   a folder → chunk → embed (sqlite + numpy is enough at this scale) →
   retrieval as a skill. "Chat with my documents" is the flagship local-AI
   feature.
2. **Web search skill** — local models have stale knowledge; DuckDuckGo/
   SearxNG search + page fetch as tools transforms answer quality.
3. **Memory** — a `remember` skill writing persistent user facts (name,
   stack, preferences) injected into the system prompt across chats.
4. **Voice input** — local Whisper (faster-whisper) hold-to-talk.
5. **Multi-tab hardening** — per-client refresh broadcasts for the
   sidebar, config hot-reload semantics across tabs.

## Cross-cutting

- `git init` + baseline commit.
- Share ThinkFilter/Ollama client with the clyde CLI instead of the
  current copy-paste.
- Handle non-PNG uploads properly (data URL mime by extension).
- pytest for router (fast paths + false-positive corpus), ThinkFilter,
  tag parsing, db round-trips, skills loader.
