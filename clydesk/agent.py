"""Orchestrator: takes a routed message and produces the response.

UI passes callbacks so it can render streaming text, status updates, and
images without this module knowing anything about NiceGUI.
"""

import asyncio
import json
from collections import defaultdict

from . import comfy as comfy_mod
from . import db, events, router, skills
from .comfy import Comfy, ComfyError, parse_tag_json
from .ollama import Ollama, OllamaError

# how many recent user images stay attached to the conversation
MAX_HISTORY_IMAGES = 3

SYSTEM_PROMPT = """\
You are Clyde, a helpful assistant running fully on the user's own PC.
Be concise and direct. Use markdown when it helps (code blocks, lists).
You have tools for deterministic work (math, dates, unit conversion...):
ALWAYS use them instead of computing such things yourself — they are exact,
you are not. Do not mention the tools unless asked; just use them.

When the user asks you to PROVE, disprove, or verify a mathematical claim
(identity, inequality, theorem, "show that..."), do not merely assert it.
Work out the proof, then formalize the full statement and proof as Lean 4
code and call the `lean_check` tool so Lean itself verifies it. Tell the
user whether Lean confirmed the proof. If Lean rejects it, read the error,
fix the Lean, and try again (up to a few attempts). Only if Lean is
unavailable or you cannot formalize it should you fall back to an informal
proof, and say so plainly."""

CODE_SYSTEM_PROMPT = SYSTEM_PROMPT + """
The user's request is code-related. Give working, complete code with brief
explanations. Match the language and libraries they mention."""


class Orchestrator:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.ollama = Ollama(cfg["ollama_base"], num_ctx=cfg.get("num_ctx"),
                             keep_alive=cfg.get("keep_alive"))
        self.comfy = Comfy(cfg)
        self.skills, self.skill_errors = skills.load_skills()
        self._chat_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def reload_skills(self):
        self.skills, self.skill_errors = skills.load_skills()

    async def make_title(self, chat_id: int) -> str | None:
        """Cheap router-model call: a 3-6 word title for the sidebar."""
        rows = await db.get_messages(chat_id)
        convo = "\n".join(f"{m['role']}: {m['content'][:300]}"
                          for m in rows[:4] if m["content"])
        try:
            raw = await self.ollama.complete(
                self.cfg["routes"]["router"],
                [{"role": "system",
                  "content": "Write a 3-6 word title for this conversation. "
                             "JSON only: {\"title\": \"...\"}"},
                 {"role": "user", "content": convo[:2000]}],
                format_schema={"type": "object",
                               "properties": {"title": {"type": "string"}},
                               "required": ["title"]},
            )
            title = json.loads(raw).get("title", "").strip()
        except Exception:
            return None
        if title:
            await db.rename_chat(chat_id, title[:60])
            return title[:60]
        return None

    def _trim_for_context(self, messages: list[dict]) -> list[dict]:
        """Keep the system prompt and newest turns inside the char budget —
        otherwise Ollama silently truncates the head (the system prompt)."""
        budget = (self.cfg.get("num_ctx") or 32768) * 3
        def size(m):
            return len(str(m.get("content") or "")) + \
                sum(len(i) for i in m.get("images") or []) // 1500
        while len(messages) > 3 and sum(map(size, messages)) > budget:
            del messages[1]
        return messages

    # ------------------------------------------------------------------
    async def handle(self, chat_id: int, text: str, images_b64: list[str],
                     on_event, approver=None) -> None:
        """Process one user message end to end. on_event(kind, payload) with
        kinds: route, thinking, text, status, images, error, done.

        `approver` (optional async callable approve(skill_name, args) -> bool)
        gates sensitive skills — network egress, actuators. When absent, or
        when require_skill_approval is off, sensitive skills run unprompted
        (headless/test use). See _exec_skill.

        Cancellation-safe: cancelling the task (Stop button) interrupts
        ComfyUI and still emits 'done'."""
        async with self._chat_locks[chat_id]:
            await self._handle_locked(chat_id, text, images_b64, on_event,
                                      approver)

    async def _handle_locked(self, chat_id, text, images_b64, on_event,
                             approver=None):
        route = None
        try:
            history = await db.get_messages(chat_id)
            chat_has_images = any(m["kind"] == "user_image" for m in history)
            # feedback right after a generation refines it instead of chatting
            prev_tags = None
            last_assistant = next((m for m in reversed(history)
                                   if m["role"] == "assistant"), None)
            if last_assistant and last_assistant["kind"] == "image_gen" \
                    and not images_b64 and router.is_image_refinement(text):
                prev_tags = last_assistant["extra"].get("tags")
            if prev_tags:
                route = "image"
            else:
                route = await router.route_message(self.ollama, self.cfg, text,
                                                   bool(images_b64))
            # follow-ups about an earlier image should reach the vision model
            if route == "chat" and chat_has_images:
                route = "vision"
            model = self.cfg["routes"].get(
                "vision" if route == "vision" else
                "code" if route == "code" else "chat")
            shown_model = {"calc": "skill:calculator",
                           "image": "comfyui"}.get(route, model)
            on_event(events.ROUTE, {"route": route, "model": shown_model})

            extra = {"route": route}
            if images_b64:
                extra["images"] = images_b64
            await db.save_message(chat_id, "user", text,
                                  kind="user_image" if images_b64 else "text",
                                  extra=extra)
            if route == "calc":
                await self._handle_calc(chat_id, text, on_event)
            elif route == "image":
                await self._handle_image(chat_id, text, on_event,
                                         prev_tags=prev_tags)
            else:
                await self._handle_chat(chat_id, text, images_b64, model,
                                        route, on_event, approver)
        except asyncio.CancelledError:
            if route == "image":
                await asyncio.shield(self.comfy.interrupt())
            on_event(events.STATUS, "stopped")
            on_event(events.DONE, None)
            raise
        except (OllamaError, ComfyError) as e:
            on_event(events.ERROR, str(e))
        except Exception as e:  # surface unexpected bugs instead of dying silently
            on_event(events.ERROR, f"internal error: {type(e).__name__}: {e}")
        on_event(events.DONE, None)

    # ------------------------------------------------------------------
    async def _handle_calc(self, chat_id: int, text: str, on_event):
        expr = router.extract_math_expression(text) or text
        calc = self.skills.get("calculator")
        if calc is None:
            on_event(events.ERROR, "calculator skill not found in skills/")
            return
        result = await calc.run_async({"expression": expr})
        answer = f"`{expr}` = **{result}**\n\n*(calculator skill — deterministic)*"
        on_event(events.TEXT, answer)
        await db.save_message(chat_id, "assistant", answer,
                              extra={"route": "calc"})

    # ------------------------------------------------------------------
    async def _handle_image(self, chat_id: int, text: str, on_event,
                            prev_tags: dict | None = None):
        on_event(events.STATUS, "refining image tags..." if prev_tags
                 else "writing image tags...")
        if prev_tags:
            system = comfy_mod.TAGS_REFINE_SYSTEM
            user = (f"Previous tags: {json.dumps(prev_tags)}\n"
                    f"Change request: {text}")
        else:
            system, user = comfy_mod.TAGS_SYSTEM, text
        raw = await self.ollama.complete(
            self.cfg["routes"]["chat"],
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
        )
        tags = parse_tag_json(raw, self.cfg["comfy"]["quality_prefix"],
                              self.cfg["comfy"]["negative"])

        on_event(events.STATUS, "starting ComfyUI (first run takes ~30s)...")
        if not await self.comfy.ensure_running():
            on_event(events.ERROR, "ComfyUI is not reachable and could not be "
                              "started — check the comfy section of the config.")
            return
        on_event(events.STATUS, "generating image...")
        names = await self.comfy.generate(
            self.comfy.build_workflow(tags["positive"], tags["negative"],
                                      tags["count"]),
            on_status=lambda s: on_event(events.STATUS, s),
        )
        on_event(events.IMAGES, {"names": names, "tags": tags})
        await db.save_message(
            chat_id, "assistant", f"Generated {len(names)} image(s)",
            kind="image_gen", extra={"route": "image", "images": names,
                                     "tags": tags},
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _memory_facts() -> str:
        import os
        path = os.path.expanduser("~/.local/share/clydesk/memory.json")
        try:
            with open(path) as f:
                facts = json.load(f)
        except (OSError, ValueError):
            return ""
        if not facts:
            return ""
        return ("\n\nThings you remember about the user from earlier "
                "conversations:\n" + "\n".join(f"- {f}" for f in facts[-40:]))

    async def _handle_chat(self, chat_id: int, text: str,
                           images_b64: list[str], model: str, route: str,
                           on_event, approver=None):
        messages = await self._build_chat_messages(chat_id, route)
        # Proof-shaped questions: reinforce the Lean workflow for this turn so
        # even smaller models reliably formalize and verify instead of asserting.
        if "lean_check" in self.skills and router.is_proof_request(text):
            messages.append({
                "role": "system",
                "content": "This message asks you to establish a mathematical "
                "truth. Prove it, then call `lean_check` with the Lean 4 "
                "formalization and report whether Lean verified it.",
            })
        full_text: list[str] = []
        extra = {"route": route, "model": model}
        try:
            await self._run_tool_loop(model, messages, full_text, on_event,
                                      approver)
        except asyncio.CancelledError:
            # keep what already streamed so it survives a reload
            partial = "".join(full_text).strip()
            if partial:
                await asyncio.shield(db.save_message(
                    chat_id, "assistant", partial + "\n\n*(stopped)*",
                    extra=extra))
            raise
        answer = "".join(full_text).strip() or "*(no text response)*"
        await db.save_message(chat_id, "assistant", answer, extra=extra)

    async def _build_chat_messages(self, chat_id: int, route: str) -> list[dict]:
        """System prompt + memory + replayed DB history, trimmed to context."""
        system = (CODE_SYSTEM_PROMPT if route == "code" else SYSTEM_PROMPT) \
            + self._memory_facts()
        messages = [{"role": "system", "content": system}]
        rows = await db.get_messages(chat_id)
        # keep the last few user images attached so follow-up questions
        # about an image still reach the model with the pixels
        image_rows = [m["id"] for m in rows if m["kind"] == "user_image"]
        keep_images = set(image_rows[-MAX_HISTORY_IMAGES:])
        for m in rows:
            if m["kind"] == "image_gen":
                tags = (m["extra"].get("tags") or {}).get("positive", "")
                messages.append({"role": "assistant",
                                 "content": f"[I generated an image: {tags[:200]}]"})
            elif m["role"] in ("user", "assistant"):
                msg = {"role": m["role"], "content": m["content"]}
                if m["id"] in keep_images and m["extra"].get("images"):
                    msg["images"] = m["extra"]["images"]
                messages.append(msg)
        return self._trim_for_context(messages)

    async def _run_tool_loop(self, model: str, messages: list[dict],
                             sink: list[str], on_event, approver=None) -> None:
        """Stream the model, execute any skill calls, and repeat until it
        answers with no tool calls (or the round budget runs out). Streamed
        text is appended to `sink` as it arrives so a cancel keeps the partial."""
        tool_schemas = [s.schema() for s in self.skills.values()]
        for _ in range(self.cfg.get("max_tool_rounds", 5)):
            tool_calls: list[dict] = []
            round_text: list[str] = []
            try:
                async for kind, payload in self.ollama.chat(
                        model, messages, tools=tool_schemas):
                    if kind == "text":
                        round_text.append(payload)
                        sink.append(payload)
                        on_event(events.TEXT_DELTA, payload)
                    elif kind == "thinking":
                        on_event(events.THINKING, payload)
                    elif kind == "tool_calls":
                        tool_calls = payload
                    elif kind == "done":
                        on_event(events.USAGE, payload)
            except OllamaError as e:
                # some (vision) models reject tools; retry without them
                if tool_schemas and "does not support tools" in str(e):
                    tool_schemas = None
                    continue
                raise
            if not tool_calls:
                return
            messages.append({"role": "assistant",
                             "content": "".join(round_text).strip(),
                             "tool_calls": tool_calls})
            for tc in tool_calls:
                await self._exec_skill(tc, messages, on_event, approver)
        on_event(events.STATUS, "stopped after max tool rounds")

    async def _approve_skill(self, skill, tc: dict, approver) -> bool:
        """Decide whether a sensitive skill may run this turn.

        require_skill_approval defaults on. With no approver wired (headless /
        tests) a sensitive skill is blocked rather than run silently — the
        safe default when nobody can answer."""
        if not skill.sensitive:
            return True
        if not self.cfg.get("require_skill_approval", True):
            return True
        if approver is None:
            return False
        try:
            return bool(await approver(tc["name"], tc.get("arguments") or {}))
        except Exception:
            return False

    async def _exec_skill(self, tc: dict, messages: list[dict], on_event,
                          approver=None) -> None:
        skill = self.skills.get(tc["name"])
        if skill is None:
            result = f"Error: unknown tool {tc['name']}"
        elif not await self._approve_skill(skill, tc, approver):
            result = (f"Error: the user did not approve the sensitive skill "
                      f"'{tc['name']}'. Do not retry; continue without it and "
                      f"tell the user it needs their approval.")
            on_event(events.STATUS, f"skill {tc['name']} — denied by user")
        else:
            result = await skill.run_async(tc["arguments"])
        on_event(events.STATUS, f"skill {tc['name']}"
                 f"({json.dumps(tc['arguments'])[:80]}) → {result[:120]}")
        messages.append({"role": "tool", "name": tc["name"], "content": result})
