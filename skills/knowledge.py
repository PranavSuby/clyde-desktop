"""Search the user's indexed documents (RAG over local files)."""

SKILL = {
    "name": "search_knowledge",
    "description": (
        "Search the user's indexed local documents and notes. Use whenever "
        "the user asks about their own files, notes, projects, or anything "
        "phrased like 'my docs', 'my notes', 'according to my files', or a "
        "topic you'd expect their personal documents to cover."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to look for"},
        },
        "required": ["query"],
    },
    "timeout": 30,
    "in_process": True,  # shares the app's rag db + embedding client
}


def run(args: dict) -> str:
    from clydesk import rag

    if rag.stats()["chunks"] == 0:
        return ("No documents are indexed yet. Tell the user to open the "
                "Knowledge dialog (book icon) and index a folder first.")
    hits = rag.search(str(args.get("query", "")), k=5)
    if not hits:
        return "No matching passages found."
    out = []
    for h in hits:
        out.append(f"[{h['source']} · score {h['score']:.2f}]\n{h['chunk'][:800]}")
    return "\n\n".join(out)
