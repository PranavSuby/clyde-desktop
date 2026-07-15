"""Configuration for Clyde Desktop: route → model mapping and paths.

The merge/save machinery is shared with the sibling `clyde` package
(already a dependency): deep merge over defaults, a clear error on broken
JSON, and crash-safe atomic saves.
"""

import json
import os

from clyde.config import ConfigError, atomic_write_json, deep_merge

CONFIG_DIR = os.path.expanduser("~/.config/clydesk")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
DATA_DIR = os.path.expanduser("~/.local/share/clydesk")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
DB_PATH = os.path.join(DATA_DIR, "chats.db")

DEFAULT_CONFIG = {
    "ollama_base": "http://localhost:11434",
    # Which model handles which kind of request. The router model should be
    # small and fast; it only classifies, it never answers.
    "routes": {
        "chat": "qwen3.5:9b",
        "code": "qwen3-coder:30b",
        "vision": "qwen3.5:9b",
        "router": "qwen3:4b",
    },
    "num_ctx": 32768,
    # keep the chat model loaded between messages (avoids cold reloads)
    "keep_alive": "30m",
    "auto_start_ollama": True,
    # Lean 4 proof checking (lean_check skill). The project dir holds a Lake
    # project with Mathlib as a dependency; the skill compiles model-written
    # proofs against it. Set enabled=false to turn the feature off.
    "lean": {
        "enabled": True,
        "project_dir": "~/.local/share/clydesk/lean",
        "elan_bin": "~/.elan/bin",
        "timeout": 90,
    },
    # ComfyUI text-to-image. Point dir/python at your ComfyUI install;
    # auto-start is skipped quietly if the paths don't exist.
    "comfy": {
        "base_url": "http://localhost:8188",
        "dir": "~/ComfyUI",
        "python": "~/ComfyUI/venv/bin/python",
        "checkpoint": "sd_xl_base_1.0.safetensors",
        # prompt-tag prefix / default negative — tune per checkpoint
        "quality_prefix": "masterpiece, best quality, ",
        "negative": ("low quality, worst quality, blurry, deformed, "
                     "bad anatomy, watermark, text, signature"),
        "auto_start": True,
        "steps": 28,
        "cfg": 7.0,
        "width": 1024,
        "height": 1024,
    },
    "max_tool_rounds": 5,
    # Require the user to confirm before a "sensitive" skill runs (network
    # egress, physical actuators). Defends against a model steered by injected
    # instructions in tool/web content firing those skills unprompted.
    "require_skill_approval": True,
}


def load_config() -> dict:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH) as f:
            user_cfg = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(
            f"{CONFIG_PATH} is not valid JSON (line {e.lineno}, col {e.colno}): "
            f"{e.msg}. Fix it or delete the file to regenerate defaults."
        ) from e
    return deep_merge(json.loads(json.dumps(DEFAULT_CONFIG)), user_cfg)


def save_config(cfg: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    atomic_write_json(CONFIG_PATH, cfg)
