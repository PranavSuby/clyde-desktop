"""ComfyUI text-to-image: prompt → tags (via LLM) → SDXL workflow."""

import asyncio
import json
import os
import random
import subprocess
import uuid

import httpx

from .config import IMAGES_DIR

TAGS_SYSTEM = """\
You convert image requests into Stable Diffusion (SDXL) prompt tags.
Respond with JSON only: {"count": <1-4>, "positive": "<comma-separated \
tags describing the image>", "negative": "<comma-separated tags to avoid>"}.
Keep positive tags concrete and visual (subject, style, lighting, setting).
Default count is 1 unless the user asks for more."""

TAGS_REFINE_SYSTEM = """\
You refine Stable Diffusion (SDXL) prompt tags based on user feedback.
You get the PREVIOUS tags and a change request. Respond with JSON only:
{"count": <1-4>, "positive": "<updated tags>", "negative": "<updated tags>"}.
Keep everything from the previous tags that the user did not ask to change."""

# Defaults; override per checkpoint via comfy.quality_prefix / comfy.negative
# in the config (e.g. score_* tags for Pony-family models).
QUALITY_PREFIX = "masterpiece, best quality, "
NEGATIVE_DEFAULT = ("low quality, worst quality, blurry, "
                    "deformed, bad anatomy, watermark, text, signature")


class ComfyError(Exception):
    pass


class Comfy:
    def __init__(self, cfg: dict):
        self.cfg = cfg["comfy"]
        self.base = self.cfg["base_url"].rstrip("/")
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
        self._proc = None

    async def is_running(self) -> bool:
        try:
            resp = await self.client.get(f"{self.base}/system_stats", timeout=3.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def ensure_running(self) -> bool:
        if await self.is_running():
            return True
        if not self.cfg.get("auto_start"):
            return False
        python = os.path.expanduser(self.cfg["python"])
        comfy_dir = os.path.expanduser(self.cfg["dir"])
        main = os.path.join(comfy_dir, "main.py")
        if not (os.path.exists(python) and os.path.exists(main)):
            return False
        self._proc = subprocess.Popen(
            [python, main], cwd=comfy_dir,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(60):  # ComfyUI takes a while to boot
            await asyncio.sleep(1.0)
            if await self.is_running():
                return True
        return False

    def build_workflow(self, positive: str, negative: str, count: int) -> dict:
        c = self.cfg
        return {
            "4": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": c["checkpoint"]}},
            "5": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": c["width"], "height": c["height"],
                             "batch_size": max(1, min(4, count))}},
            "6": {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": ["4", 1], "text": positive}},
            "7": {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": ["4", 1], "text": negative}},
            "3": {"class_type": "KSampler",
                  "inputs": {"model": ["4", 0], "positive": ["6", 0],
                             "negative": ["7", 0], "latent_image": ["5", 0],
                             "seed": random.randint(0, 2 ** 32 - 1),
                             "steps": c["steps"], "cfg": c["cfg"],
                             "sampler_name": "euler_ancestral",
                             "scheduler": "karras", "denoise": 1.0}},
            "8": {"class_type": "VAEDecode",
                  "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {"class_type": "SaveImage",
                  "inputs": {"filename_prefix": "clydesk", "images": ["8", 0]}},
        }

    async def interrupt(self):
        """Ask ComfyUI to stop the current generation (used by Stop)."""
        try:
            await self.client.post(f"{self.base}/interrupt", timeout=3.0)
        except httpx.HTTPError:
            pass

    async def generate(self, workflow: dict, on_status=None) -> list[str]:
        """Run the workflow; returns saved image filenames in IMAGES_DIR.

        Every network/parse failure surfaces as ComfyError so the UI can
        show it instead of a stuck "generating..." status."""
        try:
            resp = await self.client.post(
                f"{self.base}/prompt",
                json={"prompt": workflow, "client_id": uuid.uuid4().hex},
            )
            if resp.status_code != 200:
                raise ComfyError(f"ComfyUI rejected the workflow: {resp.text[:300]}")
            try:
                prompt_id = resp.json()["prompt_id"]
            except (ValueError, KeyError) as e:
                raise ComfyError(f"Unexpected ComfyUI response: {resp.text[:200]}") from e

            outputs = None
            for i in range(600):
                await asyncio.sleep(1.0)
                hist = await self.client.get(f"{self.base}/history/{prompt_id}")
                try:
                    data = hist.json().get(prompt_id)
                except ValueError:
                    data = None
                if data and data.get("outputs"):
                    outputs = data["outputs"]
                    break
                if on_status and i % 5 == 0:
                    on_status(f"generating... {i}s")
            if outputs is None:
                raise ComfyError("Timed out waiting for ComfyUI (10 min)")

            saved = []
            for node in outputs.values():
                for img in node.get("images", []):
                    raw = await self.client.get(
                        f"{self.base}/view",
                        params={"filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output")},
                    )
                    raw.raise_for_status()
                    name = f"{uuid.uuid4().hex}.png"
                    with open(os.path.join(IMAGES_DIR, name), "wb") as f:
                        f.write(raw.content)
                    saved.append(name)
            return saved
        except httpx.HTTPError as e:
            raise ComfyError(f"ComfyUI connection failed mid-generation: {e}") from e


def parse_tag_json(raw: str, quality_prefix: str = QUALITY_PREFIX,
                   negative_default: str = NEGATIVE_DEFAULT) -> dict:
    """Parse the tag-writer LLM's JSON, tolerating fences and bad output."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0] if "```" in text else text
    try:
        data = json.loads(text)
        positive = str(data.get("positive", "")).strip()
        negative = str(data.get("negative", "")).strip() or negative_default
        count = max(1, min(4, int(data.get("count", 1) or 1)))
    except (json.JSONDecodeError, ValueError, TypeError):
        positive, negative, count = raw.strip()[:400], negative_default, 1
    first_tag = quality_prefix.split(",")[0].strip()
    if not positive.startswith(first_tag):
        positive = quality_prefix + positive
    return {"positive": positive, "negative": negative, "count": count}
