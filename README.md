# Clyde Desktop

A Claude-desktop-style app that runs entirely on your PC: local models via
Ollama, **smart routing** between them, **multimodal** chat (image
understanding), **image generation** via ComfyUI, and **deterministic
skills** for things models shouldn't guess at (math, dates, units).

## Install & run

```bash
cd ~/Documents/clyde-desktop
python3 -m venv --system-site-packages .venv   # system site pkgs for the GTK webview
.venv/bin/pip install -e .
./run.sh              # native desktop window
./run.sh --browser    # or as a local web app at http://localhost:8420
```

`clydesk.desktop` can be copied to `~/.local/share/applications/` to get a
launcher icon like a real desktop app (edit its `Exec=` line to point at
your clone first).

## How routing works

Every message is classified before any big model runs, cheapest check first:

| signal | route | handled by |
|---|---|---|
| image attached | `vision` | multimodal model (`qwen3.5:9b`) |
| pure arithmetic ("what's 23*(4+5)?") | `calc` | calculator skill — **no LLM at all** |
| "draw/generate/make an image of ..." | `image` | ComfyUI: chat model writes prompt tags, ComfyUI renders |
| everything else | `chat` / `code` | a small router model (`qwen3:4b`) classifies; code goes to `qwen3-coder:30b`, chat to `qwen3.5:9b` |

Each response shows a colored badge with the route and model that handled
it. Change the model per route in Settings (gear icon) — saved to
`~/.config/clydesk/config.json`.

## Multimodal

Attach an image with the image button; it routes to the vision model
automatically. `qwen3.5:9b` supports vision + tools + thinking (thinking is
rendered as dim italic text while it streams, and stripped from history).

## Skills (deterministic tools)

Drop a `.py` file in `skills/` and hit the reload button — see
**SKILLS.md** for the format and design rules. Ships with `calculator`
(sympy), `convert_units`, and `current_datetime`. The chat model gets all
skills as tools and is instructed to always use them over mental math.

## Image generation

Uses your existing ComfyUI install (auto-started on first image request)
with whatever checkpoint you set in the config (`comfy.checkpoint`). The
chat model converts your request into quality-prefixed prompt tags; results
are stored under `~/.local/share/clydesk/images` and rendered inline.

## Knowledge (RAG)

The book icon opens the Knowledge dialog: index any folder and the
`search_knowledge` skill answers questions from your own files (embedded
locally with `nomic-embed-text`; nothing leaves the PC).

## More built-in skills

`web_search` (DuckDuckGo via ddgs) and `fetch_page` give local models
current information; `remember` stores lasting facts about you that are
injected into every chat.

## Verified math proofs (Lean)

Ask any loaded model to *prove* something ("prove the sum of two even
numbers is even", "show that √2 is irrational") and Clyde doesn't just take
the model's word for it. The model works out the proof, formalizes it as
**Lean 4** code, and the `lean_check` skill compiles it against **Mathlib**.
The reply tells you whether **Lean** — a real theorem prover — confirmed it.
A proof only counts if Lean reports no errors *and* no `sorry`/`admit`
placeholders. If Lean rejects it, the model reads the error and retries.

This works with whatever chat model you have loaded (it's just a tool);
stronger models formalize more reliably. Proof-shaped questions are also
detected up front and given an extra nudge to use the verifier.

**Setup.** Needs Lean installed via [`elan`](https://lean-lang.org/install/)
and a Lake project with Mathlib. One-time:

```bash
curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- -y
export PATH="$HOME/.elan/bin:$PATH"
mkdir -p ~/.local/share/clydesk/lean && cd ~/.local/share/clydesk/lean
lake init clyde_lean math      # wires in Mathlib
lake exe cache get             # prebuilt Mathlib oleans (no long compile)
lake build
```

Paths and the compile timeout live under the `lean` key in the config; set
`"enabled": false` to turn the feature off. If Lean isn't set up, `lean_check`
returns a clear error and the model falls back to an informal proof.

## Voice input

The mic button records in the browser and transcribes locally with
faster-whisper (`whisper_model` in the config, default `small`; the model
downloads on first use). Falls back to CPU when CUDA libs are missing.

## Daily-driver UX

Stop button (or Esc) cancels generation mid-stream — including ComfyUI
runs. Chats auto-title themselves, Ctrl+K searches all chats, Ctrl+N starts
a new one, code can be copied per message, and whole chats export to
markdown. "Make it darker" right after a generated image refines it using
the previous tags instead of starting over.

## Storage

- Chats: SQLite at `~/.local/share/clydesk/chats.db` (typed message
  records: `text`, `user_image`, `image_gen`)
- Config: `~/.config/clydesk/config.json`
- Related projects: `~/Documents/clyde` (the CLI coding agent),
  `~/localLLM` (the original chat app this replaces)
