import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  retrieved_at TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  chunk_id TEXT UNIQUE NOT NULL,
  page_url TEXT NOT NULL,
  title TEXT NOT NULL,
  section TEXT NOT NULL,
  retrieved_at TEXT NOT NULL,
  text TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_id UNINDEXED, text, title, section, tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS ingestion_runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  seed_url TEXT NOT NULL,
  pages_indexed INTEGER DEFAULT 0,
  chunks_created INTEGER DEFAULT 0,
  errors_json TEXT DEFAULT '[]'
);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def replace_corpus(self, pages: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM chunks_fts")
            connection.execute("DELETE FROM chunks")
            connection.execute("DELETE FROM pages")
            connection.executemany(
                "INSERT INTO pages(url,title,retrieved_at,content_hash,text) VALUES(:url,:title,:retrieved_at,:content_hash,:text)",
                pages,
            )
            connection.executemany(
                """INSERT INTO chunks(chunk_id,page_url,title,section,retrieved_at,text)
                   VALUES(:chunk_id,:page_url,:title,:section,:retrieved_at,:text)""",
                chunks,
            )
            connection.executemany(
                "INSERT INTO chunks_fts(chunk_id,text,title,section) VALUES(:chunk_id,:text,:title,:section)",
                chunks,
            )

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        stopwords = {
            "a", "an", "and", "are", "as", "at", "be", "did", "do", "does", "for",
            "from", "how", "i", "in", "is", "it", "me", "of", "on", "or", "the",
            "this", "to", "was", "what", "when", "where", "which", "who", "why", "with",
        }
        tokens = [
            token.lower()
            for token in "".join(c if c.isalnum() else " " for c in query).split()
            if len(token) > 1 and token.lower() not in stopwords
        ]
        if not tokens:
            return []
        expression = " OR ".join(f'"{token}"' for token in tokens[:20])
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT c.*, bm25(chunks_fts, 0, 1, .4, .4) AS rank
                   FROM chunks_fts JOIN chunks c USING(chunk_id)
                   WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?""",
                (expression, limit),
            ).fetchall()
        results = []
        query_terms = set(tokens)
        for rank_position, row in enumerate(rows, 1):
            item = dict(row)
            item.pop("rank")
            searchable = f"{item['title']} {item['section']} {item['text']}".lower()
            matched = sum(1 for term in query_terms if term in searchable)
            coverage = matched / max(1, len(query_terms))
            item["score"] = (0.85 * coverage) + (0.15 / rank_position)
            results.append(item)
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def adjacent_chunks(self, chunk_id: str, radius: int = 1) -> list[dict[str, Any]]:
        """Return neighboring chunks from the same page to preserve split evidence."""
        with self.connect() as connection:
            anchor = connection.execute(
                "SELECT id, page_url FROM chunks WHERE chunk_id = ?", (chunk_id,)
            ).fetchone()
            if not anchor:
                return []
            rows = connection.execute(
                """SELECT * FROM chunks
                   WHERE page_url = ? AND id BETWEEN ? AND ?
                   ORDER BY id""",
                (anchor["page_url"], anchor["id"] - radius, anchor["id"] + radius),
            ).fetchall()
        return [dict(row) for row in rows if row["chunk_id"] != chunk_id]

    def status(self) -> dict[str, Any]:
        with self.connect() as connection:
            pages = connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
            chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            latest = connection.execute("SELECT MAX(retrieved_at) FROM pages").fetchone()[0]
        return {"indexed_pages": pages, "indexed_chunks": chunks, "last_ingested_at": latest}

    def record_run(self, seed_url: str, started_at: str, pages: int, chunks: int, errors: list[str]) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO ingestion_runs(started_at,completed_at,seed_url,pages_indexed,chunks_created,errors_json)
                   VALUES(?,datetime('now'),?,?,?,?)""",
                (started_at, seed_url, pages, chunks, json.dumps(errors)),
            )
