"""The Clyde Desktop chat page (NiceGUI)."""

import asyncio
import base64
import os
import time

from nicegui import ui

from . import db, events
from .agent import Orchestrator
from .config import load_config, save_config
from .sanitize import sanitize_html


def md(content: str = "", **kwargs):
    """ui.markdown that sanitizes rendered HTML server-side.

    Everything shown here — model output, stored history, web content the
    model echoes — is untrusted; run our nh3 pass so an <img onerror>/<script>
    payload can't reach the webview DOM. Explicit, not relying on NiceGUI's
    client-side default."""
    return ui.markdown(content, sanitize=sanitize_html, **kwargs)

ROUTE_COLORS = {"chat": "primary", "code": "purple", "image": "pink",
                "vision": "teal", "calc": "green"}

_B64_MAGIC = {"/9j/": "jpeg", "iVBOR": "png", "R0lGOD": "gif", "UklGR": "webp"}


def data_url(b64: str) -> str:
    mime = next((m for magic, m in _B64_MAGIC.items()
                 if b64.startswith(magic)), "png")
    return f"data:image/{mime};base64,{b64}"

_cfg = load_config()
_orch = Orchestrator(_cfg)

# bumped on chat create/delete/rename so other tabs refresh their sidebars
_SIDEBAR_VERSION = [0]

_RECORDER_JS = """
<script>
window._clydeRec = null;
async function clydeStartRec() {
  const stream = await navigator.mediaDevices.getUserMedia({audio: true});
  const chunks = [];
  window._clydeRec = new MediaRecorder(stream);
  window._clydeRec.ondataavailable = e => chunks.push(e.data);
  window._clydeRec.onstop = () => {
    const blob = new Blob(chunks);
    const reader = new FileReader();
    reader.onloadend = () => {
      emitEvent('voice_audio', {data: reader.result.split(',')[1]});
    };
    reader.readAsDataURL(blob);
    stream.getTracks().forEach(t => t.stop());
    window._clydeRec = null;
  };
  window._clydeRec.start();
}
function clydeStopRec() { if (window._clydeRec) window._clydeRec.stop(); }
</script>
"""


def build():
    @ui.page("/")
    async def index():
        ui.dark_mode().enable()
        ui.add_body_html(_RECORDER_JS)
        ui.add_css("""
            .chat-col { max-width: 850px; margin: 0 auto; width: 100%; }
            a { color: #7aa2f7; }
        """)
        state = {"chat_id": None, "pending_images": [], "busy": False,
                 "task": None, "at_bottom": True}

        def clear_pending_images():
            state["pending_images"] = []
            preview_row.clear()

        # ------------------------------------------------------ sidebar
        with ui.left_drawer(value=True).classes("bg-neutral-900") as drawer:
            ui.button("+  New chat", on_click=lambda: new_chat()) \
                .props("flat align=left").classes("w-full")
            sidebar = ui.column().classes("w-full gap-0")

        seen_version = [_SIDEBAR_VERSION[0]]

        async def sidebar_changed():
            _SIDEBAR_VERSION[0] += 1
            seen_version[0] = _SIDEBAR_VERSION[0]
            await refresh_sidebar()

        async def _sync_sidebar():
            # other tabs bump the version; pick their changes up here
            if seen_version[0] != _SIDEBAR_VERSION[0]:
                seen_version[0] = _SIDEBAR_VERSION[0]
                await refresh_sidebar()

        ui.timer(3.0, _sync_sidebar)

        async def refresh_sidebar():
            sidebar.clear()
            with sidebar:
                for chat in await db.list_chats():
                    with ui.row().classes("w-full items-center no-wrap"):
                        ui.button(
                            chat["title"][:32],
                            on_click=lambda c=chat: load_chat(c["id"]),
                        ).props("flat no-caps align=left dense") \
                         .classes("grow text-left")
                        ui.button(
                            icon="delete",
                            on_click=lambda c=chat: remove_chat(c["id"]),
                        ).props("flat dense size=sm color=grey")

        def _blocked_while_busy() -> bool:
            if state["busy"]:
                ui.notify("A response is still streaming — press Stop first",
                          type="warning")
                return True
            return False

        async def remove_chat(chat_id: int):
            if _blocked_while_busy():
                return
            await db.delete_chat(chat_id)
            if state["chat_id"] == chat_id:
                await new_chat()
            await sidebar_changed()

        # ------------------------------------------------------ header
        with ui.header().classes("items-center bg-neutral-900"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat color=white")
            ui.label("Clyde").classes("text-lg font-bold")
            route_badge = ui.badge("ready").props("color=grey")
            ui.space()
            ui.button(icon="search", on_click=lambda: search_dialog.open()) \
                .props("flat color=white").tooltip("Search chats (Ctrl+K)")
            ui.button(icon="menu_book", on_click=lambda: knowledge_dialog.open()) \
                .props("flat color=white").tooltip("Knowledge (RAG over your files)")
            ui.button(icon="download", on_click=lambda: export_chat()) \
                .props("flat color=white").tooltip("Export chat as markdown")
            ui.button(icon="extension", on_click=lambda: skills_dialog.open()) \
                .props("flat color=white").tooltip("Skills")
            ui.button(icon="settings", on_click=lambda: settings_dialog.open()) \
                .props("flat color=white").tooltip("Routing & models")

        # ------------------------------------------- knowledge (RAG) dialog
        with ui.dialog() as knowledge_dialog, ui.card().classes("min-w-[480px]"):
            from . import rag
            ui.label("Knowledge — chat with your documents") \
                .classes("text-lg font-bold")
            kn_stats = ui.label("").classes("text-sm text-gray-500")
            folder_input = ui.input(
                placeholder="folder to index, e.g. ~/Documents/Books",
            ).classes("w-full")
            kn_progress = ui.label("").classes("text-xs text-gray-500")
            progress_state = {"text": "", "running": False}

            def refresh_kn_stats():
                s = rag.stats()
                kn_stats.set_text(
                    f"{s['chunks']} chunks from {s['sources']} files indexed "
                    f"(searchable via the search_knowledge skill)")

            knowledge_dialog.on("show", lambda: refresh_kn_stats())
            ui.timer(0.5, lambda: kn_progress.set_text(progress_state["text"])
                     if progress_state["running"] else None)

            async def do_index():
                folder = os.path.expanduser((folder_input.value or "").strip())
                if not os.path.isdir(folder):
                    ui.notify("not a folder", type="warning")
                    return
                progress_state["running"] = True
                try:
                    result = await asyncio.to_thread(
                        rag.index_folder, folder,
                        lambda s: progress_state.update(text=s))
                finally:
                    progress_state["running"] = False
                if "error" in result:
                    ui.notify(result["error"], type="negative")
                else:
                    ui.notify(f"indexed {result['chunks']} chunks "
                              f"from {result['files']} files")
                kn_progress.set_text("")
                refresh_kn_stats()

            async def do_clear():
                await asyncio.to_thread(rag.clear_index)
                refresh_kn_stats()

            with ui.row():
                ui.button("Index folder", on_click=do_index)
                ui.button("Clear index", on_click=do_clear).props("flat")

        # ------------------------------------------------------ dialogs
        with ui.dialog() as skills_dialog, ui.card().classes("min-w-[420px]"):
            ui.label("Skills (deterministic tools)").classes("text-lg font-bold")
            skills_list = ui.column().classes("gap-1")

            def refresh_skills_list():
                skills_list.clear()
                with skills_list:
                    for s in _orch.skills.values():
                        ui.label(f"• {s.name} — {s.description[:80]}") \
                            .classes("text-sm")
                    for err in _orch.skill_errors:
                        ui.label(f"⚠ {err}").classes("text-sm text-red-400")
                    ui.label("Add .py files to the skills/ folder — "
                             "see SKILLS.md").classes("text-xs text-gray-500")

            def reload_skills():
                _orch.reload_skills()
                refresh_skills_list()
                ui.notify(f"{len(_orch.skills)} skills loaded")

            refresh_skills_list()
            ui.button("Reload skills", on_click=reload_skills)

        with ui.dialog() as settings_dialog, ui.card().classes("min-w-[420px]"):
            ui.label("Routing").classes("text-lg font-bold")
            selects = {}
            for route_name in ("chat", "code", "vision", "router"):
                selects[route_name] = ui.select(
                    [_cfg["routes"].get(route_name)],
                    value=_cfg["routes"].get(route_name),
                    label=f"{route_name} model",
                ).classes("w-full")

            async def _populate_models():
                # fetched lazily so a hung daemon can't stall page render
                try:
                    models = await asyncio.wait_for(
                        _orch.ollama.list_models(), timeout=5)
                except Exception:
                    return
                for sel in selects.values():
                    sel.set_options(models, value=sel.value)

            settings_dialog.on("show", _populate_models)
            ctx_input = ui.number("num_ctx", value=_cfg.get("num_ctx", 32768))

            def save_settings():
                for k, sel in selects.items():
                    if sel.value:
                        _cfg["routes"][k] = sel.value
                _cfg["num_ctx"] = int(ctx_input.value or 32768)
                _orch.ollama.num_ctx = _cfg["num_ctx"]
                save_config(_cfg)
                settings_dialog.close()
                ui.notify("saved")

            ui.button("Save", on_click=save_settings)

            ui.separator()
            ui.label("Models").classes("text-lg font-bold")
            loaded_label = ui.label("").classes("text-xs text-gray-500")

            async def refresh_loaded():
                try:
                    loaded = await _orch.ollama.loaded_models()
                except Exception:
                    loaded_label.set_text("ollama not reachable")
                    return
                if not loaded:
                    loaded_label.set_text("no models loaded right now")
                else:
                    loaded_label.set_text(" · ".join(
                        f"{m['name']} ({m['size_gb']:.1f}GB, {m['gpu_pct']}% GPU)"
                        for m in loaded))

            settings_dialog.on("show", refresh_loaded)
            with ui.row().classes("w-full items-center"):
                pull_input = ui.input(placeholder="model to pull, e.g. qwen2.5vl:7b") \
                    .classes("grow")
                pull_progress = ui.linear_progress(value=0, show_value=False) \
                    .classes("w-full")
                pull_progress.visible = False

                async def do_pull():
                    name = (pull_input.value or "").strip()
                    if not name:
                        return
                    pull_progress.visible = True
                    try:
                        async for _status_text, pct in _orch.ollama.pull(name):
                            if pct is not None:
                                pull_progress.set_value(pct / 100)
                        ui.notify(f"pulled {name}")
                        await _populate_models()
                    except Exception as e:
                        ui.notify(f"pull failed: {e}", type="negative")
                    finally:
                        pull_progress.visible = False

                ui.button("Pull", on_click=do_pull)

        # ------------------------------------------------------ chat area
        scroll = ui.scroll_area(
            on_scroll=lambda e: state.update(
                at_bottom=e.vertical_percentage >= 0.95),
        ).classes("grow chat-col")
        with scroll:
            chat_area = ui.column().classes("w-full")

        def scroll_down(force: bool = False):
            # don't hijack the scrollbar while the user is reading upthread
            if force or state["at_bottom"]:
                scroll.scroll_to(percent=1.0)

        async def copy_text(content: str):
            ui.clipboard.write(content)
            ui.notify("copied", timeout=1000)

        def render_stored(m: dict):
            mine = m["role"] == "user"
            with chat_area, ui.chat_message(name="You" if mine else "Clyde",
                                            sent=mine).classes("w-full"):
                route = (m["extra"] or {}).get("route")
                if route and not mine:
                    with ui.row().classes("items-center gap-1"):
                        ui.badge(route).props(
                            f"color={ROUTE_COLORS.get(route, 'grey')}")
                        ui.button(icon="content_copy",
                                  on_click=lambda c=m["content"]: copy_text(c)) \
                            .props("flat dense size=xs color=grey") \
                            .tooltip("Copy message")
                if m["kind"] == "image_gen":
                    with ui.row():
                        for name in m["extra"].get("images", []):
                            ui.image(f"/images/{name}").classes("w-80 rounded")
                else:
                    if m["kind"] == "user_image":
                        with ui.row():
                            for b64 in m["extra"].get("images", []):
                                ui.image(data_url(b64)) \
                                    .classes("w-40 rounded")
                    if m["content"]:
                        md(m["content"])

        async def load_chat(chat_id: int):
            if _blocked_while_busy():
                return
            state["chat_id"] = chat_id
            clear_pending_images()
            chat_area.clear()
            for m in await db.get_messages(chat_id):
                render_stored(m)
            scroll_down(force=True)

        async def new_chat():
            if _blocked_while_busy():
                return
            state["chat_id"] = None
            clear_pending_images()
            chat_area.clear()
            route_badge.set_text("ready")

        # ------------------------------------------------------ input row
        with ui.footer().classes("bg-neutral-900"):
            with ui.column().classes("chat-col gap-1"):
                preview_row = ui.row().classes("gap-1")
                with ui.row().classes("w-full items-end no-wrap"):
                    upload = ui.upload(
                        auto_upload=True, multiple=True,
                        on_upload=lambda e: add_image(e),
                    ).props("accept=image/*").classes("hidden")
                    ui.button(icon="image", on_click=lambda:
                              upload.run_method("pickFiles")) \
                        .props("flat round").tooltip("Attach image (vision)")
                    mic_btn = ui.button(icon="mic", on_click=lambda: toggle_mic()) \
                        .props("flat round").tooltip("Voice input (local Whisper)")
                    box = ui.textarea(placeholder="Message Clyde...  (Shift+Enter for newline)") \
                        .props("autogrow outlined dense") \
                        .classes("grow").on("keydown.enter.exact.prevent",
                                            lambda: send())
                    send_btn = ui.button(icon="send", on_click=lambda: send()) \
                        .props("round")
                    stop_btn = ui.button(icon="stop", on_click=lambda: stop()) \
                        .props("round color=red")
                    stop_btn.visible = False

        def stop():
            task = state.get("task")
            if task and not task.done():
                task.cancel()

        # ---------------------------------------------------- voice input
        state["recording"] = False

        async def toggle_mic():
            if state["recording"]:
                await ui.run_javascript("clydeStopRec()")
                state["recording"] = False
                mic_btn.props("flat round color=default")
            else:
                state["recording"] = True
                mic_btn.props("flat round color=red")
                await ui.run_javascript("clydeStartRec()")

        async def on_voice_audio(e):
            import base64 as b64mod

            from . import voice
            mic_btn.props("flat round color=default")
            state["recording"] = False
            try:
                audio = b64mod.b64decode(e.args["data"])
                ui.notify("transcribing...", timeout=1500)
                text = await voice.transcribe(
                    audio, _cfg.get("whisper_model", "small"))
            except Exception as err:
                ui.notify(f"voice input failed: {err}", type="negative")
                return
            if text:
                box.value = ((box.value or "") + " " + text).strip()
            else:
                ui.notify("heard nothing", type="warning")

        ui.on("voice_audio", on_voice_audio)

        async def autotitle(chat_id: int):
            if await _orch.make_title(chat_id):
                await sidebar_changed()

        async def export_chat():
            if state["chat_id"] is None:
                ui.notify("No chat open")
                return
            rows = await db.get_messages(state["chat_id"])
            lines = []
            from .config import IMAGES_DIR
            for m in rows:
                who = "**You**" if m["role"] == "user" else "**Clyde**"
                if m["kind"] == "image_gen":
                    imgs = " ".join(f"![]({os.path.join(IMAGES_DIR, n)})"
                                    for n in m["extra"].get("images", []))
                    lines.append(f"{who}: {m['content']}\n\n{imgs}")
                else:
                    lines.append(f"{who}:\n\n{m['content']}")
            md = "\n\n---\n\n".join(lines)
            ui.download(md.encode(), f"clyde-chat-{state['chat_id']}.md")

        def on_key(e):
            if not e.action.keydown:
                return
            if e.key == "Escape" and state["busy"]:
                stop()
            elif e.modifiers.ctrl and e.key == "n":
                asyncio.create_task(new_chat())
            elif e.modifiers.ctrl and e.key == "k":
                search_dialog.open()

        ui.keyboard(on_key=on_key, ignore=[])

        # ------------------------------------------------ chat search (Ctrl+K)
        with ui.dialog() as search_dialog, ui.card().classes("min-w-[480px]"):
            ui.label("Search chats").classes("text-lg font-bold")
            search_input = ui.input(placeholder="title or message text...") \
                .classes("w-full").props("autofocus")
            search_results = ui.column().classes("w-full gap-0")

            async def do_search():
                q = (search_input.value or "").strip()
                search_results.clear()
                if len(q) < 2:
                    return
                hits = await db.search_chats(q)

                async def open_hit(cid: int):
                    search_dialog.close()
                    await load_chat(cid)

                with search_results:
                    if not hits:
                        ui.label("no matches").classes("text-sm text-gray-500")
                    for chat in hits:
                        ui.button(
                            chat["title"][:60],
                            on_click=lambda c=chat: open_hit(c["id"]),
                        ).props("flat no-caps align=left dense").classes("w-full")

            search_input.on("keydown.enter", do_search)
            ui.button("Search", on_click=do_search)

        def add_image(e):
            b64 = base64.b64encode(e.content.read()).decode()
            state["pending_images"].append(b64)
            with preview_row:
                ui.image(data_url(b64)).classes("w-16 rounded")
            ui.notify("image attached — will route to the vision model")

        # ------------------------------------------------------ send
        async def send():
            text = (box.value or "").strip()
            if not text or state["busy"]:
                return
            images = state["pending_images"]
            state["pending_images"] = []
            preview_row.clear()
            box.value = ""
            state["busy"] = True
            send_btn.visible = False
            stop_btn.visible = True

            if state["chat_id"] is None:
                state["chat_id"] = await db.create_chat(text[:40])
                await sidebar_changed()

            with chat_area, ui.chat_message(name="You", sent=True) \
                    .classes("w-full"):
                if images:
                    with ui.row():
                        for b64 in images:
                            ui.image(data_url(b64)) \
                                .classes("w-40 rounded")
                md(text)

            with chat_area, ui.chat_message(name="Clyde").classes("w-full"):
                badge = ui.badge("routing...").props("color=grey")
                status = ui.label("").classes("text-xs text-gray-500")
                thinking = md("").classes(
                    "text-xs text-gray-500 italic")
                body = md("")
                img_row = ui.row()
            scroll_down()

            acc = {"text": "", "think": "", "last_flush": 0.0}

            def flush_text(force: bool = False):
                # ~10 Hz: full-content markdown re-renders per token are O(n²)
                now = time.monotonic()
                if not force and now - acc["last_flush"] < 0.1:
                    return
                acc["last_flush"] = now
                body.set_content(acc["text"])
                scroll_down()

            def on_event(kind, payload):
                if kind == events.ROUTE:
                    badge.set_text(f"{payload['route']} · {payload['model']}")
                    badge.props(f"color={ROUTE_COLORS.get(payload['route'], 'grey')}")
                    route_badge.set_text(payload["route"])
                elif kind == events.STATUS:
                    status.set_text(str(payload))
                elif kind == events.THINKING:
                    acc["think"] += payload
                    now = time.monotonic()
                    if now - acc.get("last_think_flush", 0) >= 0.1:
                        acc["last_think_flush"] = now
                        thinking.set_content("*" + acc["think"][-500:] + "*")
                elif kind == events.TEXT_DELTA:
                    acc["text"] += payload
                    flush_text()
                elif kind == events.TEXT:
                    acc["text"] = payload
                    flush_text(force=True)
                elif kind == events.IMAGES:
                    status.set_text("")
                    with img_row:
                        for name in payload["names"]:
                            ui.image(f"/images/{name}").classes("w-80 rounded")
                    scroll_down()
                elif kind == events.ERROR:
                    status.set_text("")
                    with img_row:
                        ui.label(f"Error: {payload}").classes("text-red-400")
                elif kind == events.USAGE and payload.get("prompt_tokens"):
                    pct = ""
                    if _cfg.get("num_ctx"):
                        pct = f" · ctx {100 * payload['prompt_tokens'] / _cfg['num_ctx']:.0f}%"
                    status.set_text(
                        f"{payload['prompt_tokens']} prompt · "
                        f"{payload['completion_tokens']} gen{pct}")

            async def approve_skill(name: str, args: dict) -> bool:
                # A sensitive skill (network egress / actuator) wants to run.
                # Show what it would do and wait for the user's decision.
                arg_str = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
                with ui.dialog() as confirm, ui.card().classes("min-w-[420px]"):
                    ui.label("Allow this action?").classes("text-lg font-bold")
                    ui.label(f"The assistant wants to run the “{name}” skill.") \
                        .classes("text-sm")
                    if arg_str:
                        ui.label(arg_str).classes(
                            "text-xs text-gray-500 font-mono break-all")
                    ui.label("Only allow if you asked for something that needs "
                             "it — tool or web content can try to trigger this.") \
                        .classes("text-xs text-amber-500")
                    with ui.row().classes("w-full justify-end"):
                        ui.button("Deny", on_click=lambda: confirm.submit(False)) \
                            .props("flat color=grey")
                        ui.button("Allow", on_click=lambda: confirm.submit(True)) \
                            .props("color=primary")
                return bool(await confirm)

            is_first_exchange = len(await db.get_messages(state["chat_id"])) == 0
            try:
                state["task"] = asyncio.create_task(
                    _orch.handle(state["chat_id"], text, images, on_event,
                                 approver=approve_skill))
                await state["task"]
                if is_first_exchange:
                    asyncio.create_task(autotitle(state["chat_id"]))
            except asyncio.CancelledError:
                status.set_text("stopped")
            finally:
                state["task"] = None
                flush_text(force=True)
                thinking.set_content("")
                state["busy"] = False
                stop_btn.visible = False
                send_btn.visible = True
                scroll_down()

        # ------------------------------------------------------ startup
        await db.init_db()
        await refresh_sidebar()

        async def _check_backend():
            # off the critical path: page renders immediately, banner follows
            if not await _orch.ollama.ensure_running():
                ui.notify("Ollama is not running and could not be started — "
                          "chat will fail until it's up",
                          type="negative", timeout=0, close_button=True)

        asyncio.create_task(_check_backend())
