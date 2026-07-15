"""Async Ollama client: streaming chat with tools, vision images, and
thinking-tag separation. Uses the native /api/chat endpoint so num_ctx works.
"""

import asyncio
import json
import shutil
import subprocess

import httpx
from clyde.ollama_wire import parse_tool_calls, parse_usage
from clyde.streaming import ThinkFilter


class OllamaError(Exception):
    pass


class Ollama:
    def __init__(self, base_url: str, num_ctx: int | None = None,
                 keep_alive: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive  # e.g. "30m": avoid cold model reloads
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))

    async def is_running(self) -> bool:
        try:
            resp = await self.client.get(self.base_url, timeout=2.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def ensure_running(self) -> bool:
        if await self.is_running():
            return True
        binary = shutil.which("ollama")
        if not binary:
            return False
        subprocess.Popen(
            [binary, "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(30):
            await asyncio.sleep(0.5)
            if await self.is_running():
                return True
        return False

    async def list_models(self) -> list[str]:
        resp = await self.client.get(f"{self.base_url}/api/tags")
        resp.raise_for_status()
        return sorted(m["name"] for m in resp.json().get("models", []))

    async def loaded_models(self) -> list[dict]:
        """Currently loaded models: name, VRAM share, expiry (ollama ps)."""
        resp = await self.client.get(f"{self.base_url}/api/ps", timeout=5.0)
        resp.raise_for_status()
        out = []
        for m in resp.json().get("models", []):
            size = m.get("size", 0)
            vram = m.get("size_vram", 0)
            out.append({
                "name": m.get("name", "?"),
                "size_gb": size / 1e9,
                "gpu_pct": round(100 * vram / size) if size else 0,
                "until": m.get("expires_at", ""),
            })
        return out

    async def pull(self, name: str):
        """Pull a model, yielding (status, percent|None) progress tuples."""
        async with self.client.stream(
            "POST", f"{self.base_url}/api/pull",
            json={"model": name, "stream": True}, timeout=None,
        ) as resp:
            if resp.status_code != 200:
                raise OllamaError(f"pull failed: HTTP {resp.status_code}")
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except ValueError:
                    continue
                if chunk.get("error"):
                    raise OllamaError(chunk["error"])
                total, done = chunk.get("total"), chunk.get("completed")
                pct = round(100 * done / total) if total and done else None
                yield chunk.get("status", ""), pct

    def _wire_messages(self, messages: list[dict]) -> list[dict]:
        """Internal format -> Ollama native. Images are base64 strings."""
        wire = []
        for m in messages:
            wm = {"role": m["role"], "content": m.get("content") or ""}
            if m.get("images"):
                wm["images"] = m["images"]
            if m.get("tool_calls"):
                wm["tool_calls"] = [
                    {"function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in m["tool_calls"]
                ]
            if m["role"] == "tool":
                wm["tool_name"] = m.get("name", "")
            wire.append(wm)
        return wire

    async def chat(self, model: str, messages: list[dict],
                   tools: list[dict] | None = None,
                   format_schema: dict | None = None):
        """Yields ('thinking'|'text', str), then optionally ('tool_calls', [..]),
        then ('done', usage_dict)."""
        payload = {
            "model": model,
            "messages": self._wire_messages(messages),
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        if format_schema:
            payload["format"] = format_schema
        if self.num_ctx:
            payload["options"] = {"num_ctx": self.num_ctx}
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive

        tool_calls = []
        think = ThinkFilter()
        usage = {}
        try:
            async with self.client.stream(
                "POST", f"{self.base_url}/api/chat", json=payload
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode(errors="replace")
                    raise OllamaError(f"HTTP {resp.status_code}: {body[:400]}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        continue  # tolerate malformed/truncated stream lines
                    if chunk.get("error"):
                        raise OllamaError(chunk["error"])
                    msg = chunk.get("message", {})
                    if msg.get("thinking"):
                        yield ("thinking", msg["thinking"])
                    if msg.get("content"):
                        for kind, text in think.feed(msg["content"]):
                            yield (kind, text)
                    tool_calls.extend(parse_tool_calls(msg))
                    if chunk.get("done"):
                        usage = parse_usage(chunk)
        except httpx.HTTPError as e:
            raise OllamaError(f"Cannot reach Ollama: {e}") from e
        for kind, text in think.flush():
            yield (kind, text)
        if tool_calls:
            yield ("tool_calls", tool_calls)
        yield ("done", usage)

    async def complete(self, model: str, messages: list[dict],
                       format_schema: dict | None = None) -> str:
        """Non-streaming completion; returns final text (thinking stripped)."""
        parts = []
        async for kind, payload in self.chat(model, messages,
                                             format_schema=format_schema):
            if kind == "text":
                parts.append(payload)
        return "".join(parts).strip()
