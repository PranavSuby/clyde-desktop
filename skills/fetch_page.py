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
    "sensitive": True,  # reaches outside this process — gate behind approval
}


def _is_private_host(host: str) -> bool:
    """True if the host resolves to a loopback/private/link-local address.

    Blocks SSRF: an injected instruction must not be able to make the app
    read internal services (Ollama :11434, ComfyUI :8188, cloud metadata,
    the LAN). Every resolved address is checked, not just the first, so a
    DNS entry mixing a public and an internal A record can't sneak through."""
    import ipaddress
    import socket

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # unresolvable → refuse rather than guess
    for info in infos:
        addr = info[4][0].split("%")[0]  # strip IPv6 zone id
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return True
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


def run(args: dict) -> str:
    import html as html_mod
    import re
    import urllib.parse
    import urllib.request

    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return "Error: only http(s) URLs are supported"
    host = urllib.parse.urlparse(url).hostname or ""
    if not host:
        return "Error: could not parse a host from the URL"
    if _is_private_host(host):
        return ("Error: refusing to fetch a private/loopback/internal address "
                "(SSRF guard). Only public web hosts are allowed.")
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) clydesk/0.1"})
    # nosec B310 — scheme is restricted to http/https above and the host is
    # SSRF-checked (private/loopback/link-local blocked), incl. after redirects.
    with urllib.request.urlopen(req, timeout=20) as resp:  # nosec B310
        # a redirect could point back at an internal host; re-check the final URL
        final_host = urllib.parse.urlparse(resp.geturl()).hostname or ""
        if _is_private_host(final_host):
            return ("Error: the URL redirected to a private/internal address "
                    "(SSRF guard).")
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
