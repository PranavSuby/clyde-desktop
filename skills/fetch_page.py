"""Fetch a web page and return its readable text."""

import urllib.request as _urllib_request  # module level: _NoRedirect subclasses it

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
    DNS entry mixing a public and an internal A record can't sneak through.

    Residual: this resolves the name independently of the socket that urllib
    later opens, so a low-TTL attacker-controlled domain could rebind between
    the two lookups (DNS rebinding). Closing that fully means pinning the
    connection to the vetted IP; out of scope for this local single-user app."""
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


class _NoRedirect(_urllib_request.HTTPRedirectHandler):
    """Refuse to auto-follow redirects: urlopen raises HTTPError(30x) instead,
    so each hop's host is re-vetted BEFORE its socket is opened — rather than
    urllib silently chasing a redirect to an internal address."""

    def redirect_request(self, *a, **kw):
        return None


def run(args: dict) -> str:
    import html as html_mod
    import re
    import urllib.error
    import urllib.parse
    import urllib.request

    url = str(args.get("url", "")).strip()
    opener = urllib.request.build_opener(_NoRedirect)
    resp = None
    for _ in range(5):  # bounded redirect chain
        if not url.startswith(("http://", "https://")):
            return "Error: only http(s) URLs are supported"
        host = urllib.parse.urlparse(url).hostname or ""
        if not host:
            return "Error: could not parse a host from the URL"
        if _is_private_host(host):
            return ("Error: refusing to fetch a private/loopback/internal "
                    "address (SSRF guard). Only public web hosts are allowed.")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) clydesk/0.1"})
        try:
            # nosec B310 — scheme restricted to http/https and host SSRF-checked
            # above; redirects are re-vetted per hop, not auto-followed.
            resp = opener.open(req, timeout=20)  # nosec B310
            break
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                continue
            return f"Error: HTTP {e.code} {e.reason}"
    else:
        return "Error: too many redirects"

    with resp:
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
