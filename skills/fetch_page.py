"""Fetch a web page and return its readable text."""

SKILL = {
    "name": "fetch_page",
    "description": (
        "Download a web page and return its text content (tags stripped). "
        "Use after web_search to read a promising result, or when the user "
        "gives you a URL."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The http(s) URL to fetch"},
        },
        "required": ["url"],
    },
    "timeout": 25,
}


def run(args: dict) -> str:
    import html as html_mod
    import re
    import urllib.request

    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return "Error: only http(s) URLs are supported"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) clydesk/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if "html" not in ctype and "text" not in ctype and "json" not in ctype:
            return f"Error: unsupported content type {ctype}"
        raw = resp.read(1_500_000).decode("utf-8", errors="replace")

    # crude readability: drop script/style/nav, then strip tags
    raw = re.sub(r"<(script|style|nav|header|footer|svg)[\s\S]*?</\1>", " ", raw,
                 flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    if len(text) > 6000:
        text = text[:6000] + "\n... (page truncated)"
    return text or "(no text content)"
