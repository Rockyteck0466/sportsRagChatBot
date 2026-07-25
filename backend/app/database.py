import json
import re
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
CREATE TABLE IF NOT EXISTS chunk_questions (
  id INTEGER PRIMARY KEY,
  question_id TEXT UNIQUE NOT NULL,
  chunk_id TEXT NOT NULL,
  question TEXT NOT NULL,
  kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunk_questions_chunk_id
  ON chunk_questions(chunk_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_questions_fts USING fts5(
  question_id UNINDEXED, chunk_id UNINDEXED, question, tokenize='porter unicode61'
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
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def replace_corpus(self, pages: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM chunk_questions_fts")
            connection.execute("DELETE FROM chunk_questions")
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

    def chunk_ids_for_pages(self, page_urls: list[str]) -> list[str]:
        if not page_urls:
            return []
        placeholders = ",".join("?" for _ in page_urls)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT chunk_id FROM chunks WHERE page_url IN ({placeholders})",
                page_urls,
            ).fetchall()
        return [row["chunk_id"] for row in rows]

    def upsert_corpus(self, pages: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> None:
        """Replace selected pages and their keyword chunks without clearing the corpus."""
        if not pages:
            return
        page_urls = [page["url"] for page in pages]
        placeholders = ",".join("?" for _ in page_urls)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"""DELETE FROM chunk_questions_fts
                    WHERE question_id IN (
                        SELECT q.question_id
                        FROM chunk_questions q
                        JOIN chunks c USING(chunk_id)
                        WHERE c.page_url IN ({placeholders})
                    )""",
                page_urls,
            )
            connection.execute(
                f"""DELETE FROM chunk_questions
                    WHERE chunk_id IN (
                        SELECT chunk_id FROM chunks WHERE page_url IN ({placeholders})
                    )""",
                page_urls,
            )
            connection.execute(
                f"""DELETE FROM chunks_fts
                    WHERE chunk_id IN (
                        SELECT chunk_id FROM chunks WHERE page_url IN ({placeholders})
                    )""",
                page_urls,
            )
            connection.execute(
                f"DELETE FROM chunks WHERE page_url IN ({placeholders})",
                page_urls,
            )
            connection.execute(
                f"DELETE FROM pages WHERE url IN ({placeholders})",
                page_urls,
            )
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

    @staticmethod
    def _query_tokens(query: str) -> list[str]:
        stopwords = {
            "a", "an", "and", "are", "as", "at", "be", "did", "do", "does", "for",
            "from", "how", "i", "in", "is", "it", "me", "of", "on", "or", "the",
            "this", "to", "was", "what", "when", "where", "which", "who", "why", "with",
        }
        tokens = [
            token.lower()
            for token in re.findall(r"[A-Za-z0-9]+", query)
            if len(token) > 1 and token.lower() not in stopwords
        ]
        return list(dict.fromkeys(tokens))[:20]

    def search(
        self,
        query: str,
        limit: int,
        *,
        match_mode: str = "any",
    ) -> list[dict[str, Any]]:
        """Search FTS with an exact-token lane or a broad recall lane.

        ``match_mode="all"`` rewards passages containing every salient query
        token. The broad ``"any"`` mode is kept as a lower-weight fallback by
        the hybrid retriever.
        """
        tokens = self._query_tokens(query)
        if not tokens:
            return []
        if match_mode not in {"all", "any"}:
            raise ValueError("match_mode must be 'all' or 'any'.")
        operator = " AND " if match_mode == "all" else " OR "
        expression = operator.join(f'"{token}"' for token in tokens)
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT c.*, bm25(chunks_fts, 0, 1.0, 2.5, 3.0) AS rank
                   FROM chunks_fts JOIN chunks c USING(chunk_id)
                   WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?""",
                (expression, limit),
            ).fetchall()
        results = []
        for rank_position, row in enumerate(rows, 1):
            item = dict(row)
            item["bm25_rank"] = float(item.pop("rank"))
            item["score"] = 1.0 / rank_position
            results.append(item)
        return results

    def search_with_required(
        self,
        query: str,
        required: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Search while requiring an entity and ranking optional intent terms."""
        required_tokens = self._query_tokens(required)
        if not required_tokens:
            return []
        required_stems = {token.lower() for token in required_tokens}
        optional_tokens = [
            token
            for token in self._query_tokens(query)
            if token.lower() not in required_stems
        ]
        required_expression = " AND ".join(
            f'"{token}"' for token in required_tokens
        )
        expression = f"({required_expression})"
        if optional_tokens:
            optional_expression = " OR ".join(
                f'"{token}"' for token in optional_tokens
            )
            expression += f" AND ({optional_expression})"
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT c.*, bm25(chunks_fts, 0, 1.0, 2.5, 3.0) AS rank
                   FROM chunks_fts JOIN chunks c USING(chunk_id)
                   WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?""",
                (expression, limit),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for rank_position, row in enumerate(rows, 1):
            item = dict(row)
            item["bm25_rank"] = float(item.pop("rank"))
            item["score"] = 1.0 / rank_position
            results.append(item)
        return results

    def all_pages(self) -> list[dict[str, Any]]:
        """Return the active raw pages so they can be reindexed without scraping."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT url,title,retrieved_at,content_hash,text FROM pages ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def all_chunks(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT chunk_id,page_url,title,section,retrieved_at,text
                   FROM chunks ORDER BY id"""
            ).fetchall()
        return [dict(row) for row in rows]

    def title_aliases_for_query(self, query: str, limit: int = 4) -> list[str]:
        """Resolve uppercase initials to titles that actually exist in the corpus.

        This is retrieval vocabulary only. For example, an acronym can navigate to
        a matching indexed player title, while the returned source chunk remains
        the only answer evidence.
        """
        acronyms = set(
            re.findall(
                r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,5}(?![A-Za-z0-9])",
                query,
            )
        )
        if not acronyms:
            return []
        ignored_title_words = {
            "about",
            "all",
            "and",
            "league",
            "profile",
            "roster",
            "schedule",
            "season",
            "team",
            "the",
        }
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT title FROM chunks ORDER BY title"
            ).fetchall()
        aliases: list[str] = []
        for row in rows:
            title = str(row["title"]).strip()
            words = [
                word
                for word in re.findall(r"[A-Za-z0-9]+", title)
                if word.lower() not in ignored_title_words
            ]
            if not 2 <= len(words) <= 5:
                continue
            initials = "".join(word[0].upper() for word in words)
            if initials not in acronyms or title in aliases:
                continue
            aliases.append(title)
            if len(aliases) >= limit:
                break
        return aliases

    def replace_chunk_questions(self, records: list[dict[str, Any]]) -> None:
        """Atomically replace retrieval-only questions mapped to real chunks."""
        with self.connect() as connection:
            valid_chunk_ids = {
                row[0] for row in connection.execute("SELECT chunk_id FROM chunks")
            }
            invalid = {
                record["chunk_id"]
                for record in records
                if record["chunk_id"] not in valid_chunk_ids
            }
            if invalid:
                raise ValueError("Synthetic questions referenced unknown source chunks.")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM chunk_questions_fts")
            connection.execute("DELETE FROM chunk_questions")
            connection.executemany(
                """INSERT INTO chunk_questions(question_id,chunk_id,question,kind)
                   VALUES(:question_id,:chunk_id,:question,:kind)""",
                records,
            )
            connection.executemany(
                """INSERT INTO chunk_questions_fts(question_id,chunk_id,question)
                   VALUES(:question_id,:chunk_id,:question)""",
                records,
            )

    def search_chunk_questions(
        self,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Resolve expected-question matches back to original evidence chunks."""
        tokens = self._query_tokens(query)
        if not tokens:
            return []
        expression = " OR ".join(f'"{token}"' for token in tokens)
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT c.*, q.question_id, q.question, q.kind,
                          bm25(chunk_questions_fts, 0, 0, 1.0) AS rank
                   FROM chunk_questions_fts
                   JOIN chunk_questions q USING(question_id)
                   JOIN chunks c USING(chunk_id)
                   WHERE chunk_questions_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (expression, limit * 3),
            ).fetchall()
        results: list[dict[str, Any]] = []
        seen_chunks: set[str] = set()
        for row in rows:
            item = dict(row)
            if item["chunk_id"] in seen_chunks:
                continue
            seen_chunks.add(item["chunk_id"])
            item["question_bm25_rank"] = float(item.pop("rank"))
            item["score"] = 1.0 / (len(results) + 1)
            results.append(item)
            if len(results) >= limit:
                break
        return results

    def chunks_by_ids(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM chunks
                    WHERE chunk_id IN ({placeholders})""",
                chunk_ids,
            ).fetchall()
        by_id = {row["chunk_id"]: dict(row) for row in rows}
        return [by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in by_id]

    def chunk_questions_by_ids(
        self,
        question_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not question_ids:
            return {}
        placeholders = ",".join("?" for _ in question_ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT question_id,chunk_id,question,kind
                    FROM chunk_questions
                    WHERE question_id IN ({placeholders})""",
                question_ids,
            ).fetchall()
        return {row["question_id"]: dict(row) for row in rows}

    def adjacent_chunks(self, chunk_id: str, radius: int = 1) -> list[dict[str, Any]]:
        """Return neighboring chunks from the same page to preserve split evidence."""
        with self.connect() as connection:
            anchor = connection.execute(
                "SELECT id, page_url, section FROM chunks WHERE chunk_id = ?", (chunk_id,)
            ).fetchone()
            if not anchor:
                return []
            rows = connection.execute(
                """SELECT * FROM chunks
                   WHERE page_url = ? AND section = ? AND id BETWEEN ? AND ?
                   ORDER BY id""",
                (
                    anchor["page_url"],
                    anchor["section"],
                    anchor["id"] - radius,
                    anchor["id"] + radius,
                ),
            ).fetchall()
        return [dict(row) for row in rows if row["chunk_id"] != chunk_id]

    def section_chunks(
        self,
        page_url: str,
        section: str,
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """Return a complete structured section for list/table aggregation."""
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM chunks
                   WHERE page_url = ? AND section = ?
                   ORDER BY id LIMIT ?""",
                (page_url, section, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def status(self) -> dict[str, Any]:
        with self.connect() as connection:
            pages = connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
            chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            questions = connection.execute(
                "SELECT COUNT(*) FROM chunk_questions"
            ).fetchone()[0]
            latest = connection.execute("SELECT MAX(retrieved_at) FROM pages").fetchone()[0]
        return {
            "indexed_pages": pages,
            "indexed_chunks": chunks,
            "indexed_questions": questions,
            "last_ingested_at": latest,
        }

    def record_run(self, seed_url: str, started_at: str, pages: int, chunks: int, errors: list[str]) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO ingestion_runs(started_at,completed_at,seed_url,pages_indexed,chunks_created,errors_json)
                   VALUES(?,datetime('now'),?,?,?,?)""",
                (started_at, seed_url, pages, chunks, json.dumps(errors)),
            )
