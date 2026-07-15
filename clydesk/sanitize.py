"""HTML sanitization for anything rendered into the webview.

Model output, stored messages, and (transitively) fetched web content are all
rendered with ui.markdown. NiceGUI sanitizes client-side via DOMPurify by
default, but we don't want to rely on that alone: the streaming set_content
path and any future sanitize=False would silently reopen XSS. Passing a
callable to ui.markdown(sanitize=...) makes NiceGUI run OUR pass over the
rendered HTML, server-side, before it ever reaches the browser DOM.

nh3 (Rust ammonia bindings) is an allowlist sanitizer: it keeps the tags
markdown produces (formatting, links, code, tables) and drops scripts, event
handlers, and javascript: URLs.
"""

import nh3

# Tags markdown2 can emit (with fenced-code-blocks + tables) plus common
# inline formatting. Anything not here is stripped.
_ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "code", "del", "em", "h1", "h2",
    "h3", "h4", "h5", "h6", "hr", "i", "img", "input", "li", "ol", "p", "pre",
    "s", "span", "strong", "sub", "sup", "table", "tbody", "td", "th", "thead",
    "tr", "ul", "kbd", "dl", "dt", "dd",
}

_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "code": {"class"},        # markdown adds language classes for highlighting
    "span": {"class"},
    "input": {"type", "checked", "disabled"},  # task-list checkboxes
    "td": {"align"},
    "th": {"align"},
}


def sanitize_html(html: str) -> str:
    """Strip scripts, event handlers, and dangerous URLs from rendered HTML.

    url_schemes limits href/src to safe schemes, so javascript:/data: payloads
    can't survive; link_rel adds noopener to outbound links."""
    return nh3.clean(
        html or "",
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
    )
