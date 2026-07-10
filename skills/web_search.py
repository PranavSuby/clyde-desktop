"""Web search via the ddgs library (DuckDuckGo backend, no API key)."""

SKILL = {
    "name": "web_search",
    "description": (
        "Search the web and return result titles, URLs, and snippets. Use "
        "for anything requiring current information: news, prices, recent "
        "releases, docs, or facts you are unsure about. Follow up with "
        "fetch_page on a promising URL for details."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
        },
        "required": ["query"],
    },
    "timeout": 25,
}


def run(args: dict) -> str:
    from ddgs import DDGS

    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: empty query"
    try:
        rows = list(DDGS().text(query, max_results=6))
    except Exception as e:
        return f"Error: search failed ({e}); try again in a moment"
    if not rows:
        return "No results found."
    out = []
    for i, r in enumerate(rows, 1):
        out.append(f"{i}. {r.get('title', '')}\n   {r.get('href', '')}\n"
                   f"   {r.get('body', '')[:250]}")
    return "\n".join(out)
