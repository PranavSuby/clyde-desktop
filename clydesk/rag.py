"""Local RAG: index folders of text files with nomic-embed-text, search by
cosine similarity. Sync httpx + sqlite so it also works from skill threads."""

import array
import os
import sqlite3

import httpx

from .config import DATA_DIR, load_config

RAG_DB = os.path.join(DATA_DIR, "rag.db")
EMBED_MODEL = "nomic-embed-text"
OLLAMA = load_config().get("ollama_base", "http://localhost:11434").rstrip("/")
CHUNK_CHARS = 1200
OVERLAP = 150
TEXT_EXTS = {".md", ".txt", ".rst", ".py", ".js", ".ts", ".json", ".yaml",
             ".yml", ".toml", ".sh", ".html", ".css", ".csv", ".ini", ".cfg"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    chunk TEXT NOT NULL,
    vector BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source);
"""


def _db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(RAG_DB)
    conn.executescript(SCHEMA)
    return conn


def _embed(texts: list[str]) -> list[list[float]]:
    resp = httpx.post(f"{OLLAMA}/api/embed",
                      json={"model": EMBED_MODEL, "input": texts},
                      timeout=120.0)
    resp.raise_for_status()
    return resp.json()["embeddings"]


def _chunk_text(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_CHARS
        # prefer to break at a paragraph/newline near the end
        if end < len(text):
            nl = text.rfind("\n", start + CHUNK_CHARS // 2, end)
            if nl > 0:
                end = nl
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        start = max(end - OVERLAP, start + 1)
    return chunks


def index_folder(folder: str, on_progress=None) -> dict:
    """(Re)index every text file under folder. Returns counts."""
    folder = os.path.realpath(os.path.expanduser(folder))
    files = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            if os.path.splitext(fname)[1].lower() in TEXT_EXTS:
                files.append(os.path.join(dirpath, fname))
    conn = _db()
    n_chunks = 0
    with conn:
        for i, fpath in enumerate(files):
            if on_progress:
                on_progress(f"{i + 1}/{len(files)} {os.path.basename(fpath)}")
            try:
                with open(fpath, "r", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            if not text.strip() or len(text) > 2_000_000:
                continue
            conn.execute("DELETE FROM chunks WHERE source=?", (fpath,))
            chunks = _chunk_text(text)
            for batch_start in range(0, len(chunks), 16):
                batch = chunks[batch_start:batch_start + 16]
                try:
                    vectors = _embed([f"search_document: {c}" for c in batch])
                except (httpx.HTTPError, KeyError) as e:
                    return {"error": f"embedding failed: {e}",
                            "files": i, "chunks": n_chunks}
                for chunk, vec in zip(batch, vectors, strict=False):
                    conn.execute(
                        "INSERT INTO chunks (source, chunk, vector) VALUES (?,?,?)",
                        (fpath, chunk, array.array("f", vec).tobytes()),
                    )
                    n_chunks += 1
    conn.close()
    return {"files": len(files), "chunks": n_chunks}


def stats() -> dict:
    conn = _db()
    n_chunks, n_sources = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT source) FROM chunks").fetchone()
    conn.close()
    return {"chunks": n_chunks, "sources": n_sources}


def clear_index():
    conn = _db()
    with conn:
        conn.execute("DELETE FROM chunks")
    conn.close()


def search(query: str, k: int = 5) -> list[dict]:
    """Top-k chunks by cosine similarity (embeddings are L2-normalized-ish;
    we normalize explicitly)."""
    try:
        import numpy as np
    except ImportError:
        np = None
    qvec = _embed([f"search_query: {query}"])[0]
    conn = _db()
    rows = conn.execute("SELECT source, chunk, vector FROM chunks").fetchall()
    conn.close()
    if not rows:
        return []
    if np is not None:
        mat = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32) \
            .reshape(len(rows), -1)
        q = np.asarray(qvec, dtype=np.float32)
        mat_n = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-8)
        q_n = q / (np.linalg.norm(q) + 1e-8)
        sims = mat_n @ q_n
        order = sims.argsort()[::-1][:k]
        return [{"source": rows[i][0], "chunk": rows[i][1],
                 "score": float(sims[i])} for i in order]
    # pure-python fallback
    import math
    qn = math.sqrt(sum(x * x for x in qvec)) + 1e-8
    scored = []
    for source, chunk, blob in rows:
        vec = array.array("f")
        vec.frombytes(blob)
        dot = sum(a * b for a, b in zip(qvec, vec, strict=False))
        vn = math.sqrt(sum(x * x for x in vec)) + 1e-8
        scored.append((dot / (qn * vn), source, chunk))
    scored.sort(reverse=True)
    return [{"source": s, "chunk": c, "score": sc} for sc, s, c in scored[:k]]
