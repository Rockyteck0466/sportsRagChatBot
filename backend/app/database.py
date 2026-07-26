import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


# Team identities are retrieval/navigation aliases only. They help resolve a
# city, full team name, nickname, or abbreviation to a source page that is
# already present in the corpus. They are never returned as answer evidence.
_NBA_TEAM_IDENTITIES: dict[str, dict[str, Any]] = {
    "1610612737": {
        "name": "Atlanta Hawks",
        "city": "Atlanta",
        "nickname": "Hawks",
        "slug": "hawks",
        "aliases": ("ATL",),
    },
    "1610612738": {
        "name": "Boston Celtics",
        "city": "Boston",
        "nickname": "Celtics",
        "slug": "celtics",
        "aliases": ("BOS",),
    },
    "1610612739": {
        "name": "Cleveland Cavaliers",
        "city": "Cleveland",
        "nickname": "Cavaliers",
        "slug": "cavaliers",
        "aliases": ("Cavs", "CLE"),
    },
    "1610612740": {
        "name": "New Orleans Pelicans",
        "city": "New Orleans",
        "nickname": "Pelicans",
        "slug": "pelicans",
        "aliases": ("NOP",),
    },
    "1610612741": {
        "name": "Chicago Bulls",
        "city": "Chicago",
        "nickname": "Bulls",
        "slug": "bulls",
        "aliases": ("CHI",),
    },
    "1610612742": {
        "name": "Dallas Mavericks",
        "city": "Dallas",
        "nickname": "Mavericks",
        "slug": "mavericks",
        "aliases": ("Mavs", "DAL"),
    },
    "1610612743": {
        "name": "Denver Nuggets",
        "city": "Denver",
        "nickname": "Nuggets",
        "slug": "nuggets",
        "aliases": ("DEN",),
    },
    "1610612744": {
        "name": "Golden State Warriors",
        "city": "Golden State",
        "nickname": "Warriors",
        "slug": "warriors",
        "aliases": ("GSW",),
    },
    "1610612745": {
        "name": "Houston Rockets",
        "city": "Houston",
        "nickname": "Rockets",
        "slug": "rockets",
        "aliases": ("HOU",),
    },
    "1610612746": {
        "name": "LA Clippers",
        "city": "Los Angeles",
        "nickname": "Clippers",
        "slug": "clippers",
        "aliases": ("Los Angeles Clippers", "LAC"),
    },
    "1610612747": {
        "name": "Los Angeles Lakers",
        "city": "Los Angeles",
        "nickname": "Lakers",
        "slug": "lakers",
        "aliases": ("LAL",),
    },
    "1610612748": {
        "name": "Miami Heat",
        "city": "Miami",
        "nickname": "Heat",
        "slug": "heat",
        "aliases": ("MIA",),
    },
    "1610612749": {
        "name": "Milwaukee Bucks",
        "city": "Milwaukee",
        "nickname": "Bucks",
        "slug": "bucks",
        "aliases": ("MIL",),
    },
    "1610612750": {
        "name": "Minnesota Timberwolves",
        "city": "Minnesota",
        "nickname": "Timberwolves",
        "slug": "timberwolves",
        "aliases": ("Wolves", "MIN"),
    },
    "1610612751": {
        "name": "Brooklyn Nets",
        "city": "Brooklyn",
        "nickname": "Nets",
        "slug": "nets",
        "aliases": ("BKN",),
    },
    "1610612752": {
        "name": "New York Knicks",
        "city": "New York",
        "nickname": "Knicks",
        "slug": "knicks",
        "aliases": ("NYK",),
    },
    "1610612753": {
        "name": "Orlando Magic",
        "city": "Orlando",
        "nickname": "Magic",
        "slug": "magic",
        "aliases": ("ORL",),
    },
    "1610612754": {
        "name": "Indiana Pacers",
        "city": "Indiana",
        "nickname": "Pacers",
        "slug": "pacers",
        "aliases": ("IND",),
    },
    "1610612755": {
        "name": "Philadelphia 76ers",
        "city": "Philadelphia",
        "nickname": "76ers",
        "slug": "sixers",
        "aliases": ("Sixers", "Philadelphia Sixers", "PHI"),
    },
    "1610612756": {
        "name": "Phoenix Suns",
        "city": "Phoenix",
        "nickname": "Suns",
        "slug": "suns",
        "aliases": ("PHX",),
    },
    "1610612757": {
        "name": "Portland Trail Blazers",
        "city": "Portland",
        "nickname": "Trail Blazers",
        "slug": "blazers",
        "aliases": ("Blazers", "Portland Blazers", "POR"),
    },
    "1610612758": {
        "name": "Sacramento Kings",
        "city": "Sacramento",
        "nickname": "Kings",
        "slug": "kings",
        "aliases": ("SAC",),
    },
    "1610612759": {
        "name": "San Antonio Spurs",
        "city": "San Antonio",
        "nickname": "Spurs",
        "slug": "spurs",
        "aliases": ("SAS",),
    },
    "1610612760": {
        "name": "Oklahoma City Thunder",
        "city": "Oklahoma City",
        "nickname": "Thunder",
        "slug": "thunder",
        "aliases": ("OKC",),
    },
    "1610612761": {
        "name": "Toronto Raptors",
        "city": "Toronto",
        "nickname": "Raptors",
        "slug": "raptors",
        "aliases": ("TOR",),
    },
    "1610612762": {
        "name": "Utah Jazz",
        "city": "Utah",
        "nickname": "Jazz",
        "slug": "jazz",
        "aliases": ("UTA",),
    },
    "1610612763": {
        "name": "Memphis Grizzlies",
        "city": "Memphis",
        "nickname": "Grizzlies",
        "slug": "grizzlies",
        "aliases": ("MEM",),
    },
    "1610612764": {
        "name": "Washington Wizards",
        "city": "Washington",
        "nickname": "Wizards",
        "slug": "wizards",
        "aliases": ("WAS",),
    },
    "1610612765": {
        "name": "Detroit Pistons",
        "city": "Detroit",
        "nickname": "Pistons",
        "slug": "pistons",
        "aliases": ("DET",),
    },
    "1610612766": {
        "name": "Charlotte Hornets",
        "city": "Charlotte",
        "nickname": "Hornets",
        "slug": "hornets",
        "aliases": ("CHA",),
    },
}

_TEAM_QUERY_NOISE = {
    "and",
    "captain",
    "captains",
    "club",
    "clubs",
    "detail",
    "details",
    "find",
    "for",
    "franchise",
    "franchises",
    "get",
    "give",
    "list",
    "member",
    "members",
    "name",
    "names",
    "nba",
    "of",
    "player",
    "players",
    "roster",
    "show",
    "team",
    "teams",
    "the",
    "with",
}


def _normalize_lookup_text(value: str) -> str:
    """Normalize navigation labels without changing stored source content."""
    ascii_value = (
        unicodedata.normalize("NFKD", str(value))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()


def _team_url_parts(page_url: str) -> tuple[str, str] | None:
    """Return an NBA team id and optional profile slug for a direct team page."""
    parsed = urlparse(page_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if (parsed.hostname or "").lower() not in {"nba.com", "www.nba.com"}:
        return None
    match = re.fullmatch(
        r"/team/(?P<team_id>\d+)(?:/(?P<slug>[a-z0-9-]+))?/?",
        parsed.path,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group("team_id"), (match.group("slug") or "").lower()


def _fallback_team_name(title: str, slug: str) -> str:
    title_name = re.sub(
        r"\s+team\s+profile(?:\s+and\s+roster)?\s*$",
        "",
        str(title).strip(),
        flags=re.IGNORECASE,
    )
    if title_name and _normalize_lookup_text(title_name) not in {
        "nba",
        "page content",
        "upcoming games",
    }:
        return title_name
    return slug.replace("-", " ").title() if slug else "Indexed NBA team"


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

    def search_many(
        self,
        queries: list[str],
        limit: int,
        *,
        match_mode: str = "any",
    ) -> list[list[dict[str, Any]]]:
        """Run aligned FTS searches through one read connection."""
        if match_mode not in {"all", "any"}:
            raise ValueError("match_mode must be 'all' or 'any'.")
        operator = " AND " if match_mode == "all" else " OR "
        batches: list[list[dict[str, Any]]] = []
        with self.connect() as connection:
            for query in queries:
                tokens = self._query_tokens(query)
                if not tokens:
                    batches.append([])
                    continue
                expression = operator.join(f'"{token}"' for token in tokens)
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
                batches.append(results)
        return batches

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

    def team_pages(self, limit: int | None = 30) -> list[dict[str, Any]]:
        """Return one deterministic, source-backed profile page per NBA team.

        Duplicate URLs for a team id can be present after crawling both a short
        ``/team/<id>`` URL and its canonical slug URL. A page with an exact
        normalized ``ROSTER`` section is preferred, followed by the known
        canonical slug. Only the real ``chunks`` table is inspected; prepared
        questions are not evidence and do not participate in team discovery.
        """
        if limit is not None and limit <= 0:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT id,page_url,title,section
                   FROM chunks ORDER BY id"""
            ).fetchall()

        candidates: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            parsed = _team_url_parts(str(row["page_url"]))
            if not parsed:
                continue
            team_id, slug = parsed
            by_url = candidates.setdefault(team_id, {})
            candidate = by_url.setdefault(
                str(row["page_url"]),
                {
                    "page_url": str(row["page_url"]),
                    "title": str(row["title"]),
                    "slug": slug,
                    "first_chunk_id": int(row["id"]),
                    "normalized_sections": set(),
                },
            )
            candidate["normalized_sections"].add(
                _normalize_lookup_text(str(row["section"]))
            )

        selected: list[dict[str, Any]] = []
        for team_id, team_candidates in candidates.items():
            identity = _NBA_TEAM_IDENTITIES.get(team_id, {})
            canonical_slug = str(identity.get("slug", ""))

            def preference(candidate: dict[str, Any]) -> tuple[Any, ...]:
                has_roster = "roster" in candidate["normalized_sections"]
                canonical = bool(
                    canonical_slug and candidate["slug"] == canonical_slug
                )
                return (
                    has_roster and canonical,
                    has_roster,
                    canonical,
                    bool(candidate["slug"]),
                    -candidate["first_chunk_id"],
                    candidate["page_url"],
                )

            preferred = max(team_candidates.values(), key=preference)
            team_name = str(identity.get("name", "")).strip() or _fallback_team_name(
                preferred["title"],
                preferred["slug"],
            )
            selected.append({
                "team_name": team_name,
                "team_id": team_id,
                "page_url": preferred["page_url"],
                "title": preferred["title"],
                "slug": preferred["slug"],
                "has_roster": "roster" in preferred["normalized_sections"],
            })

        selected.sort(
            key=lambda item: (
                int(item["team_id"]) if str(item["team_id"]).isdigit() else 10**20,
                item["team_name"].lower(),
                item["page_url"],
            )
        )
        return selected if limit is None else selected[:limit]

    @staticmethod
    def _team_aliases(page: dict[str, Any]) -> set[str]:
        identity = _NBA_TEAM_IDENTITIES.get(str(page["team_id"]), {})
        aliases = {
            str(page["team_id"]),
            str(page["team_name"]),
            str(page["slug"]).replace("-", " "),
            _fallback_team_name(str(page["title"]), str(page["slug"])),
        }
        for field in ("name", "city", "nickname", "slug"):
            if identity.get(field):
                aliases.add(str(identity[field]))
        aliases.update(str(alias) for alias in identity.get("aliases", ()))
        return {
            normalized
            for alias in aliases
            if (normalized := _normalize_lookup_text(alias))
        }

    def find_team_pages(
        self,
        team_query: str,
        limit: int | None = 4,
    ) -> list[dict[str, Any]]:
        """Resolve city/full/nickname wording to indexed team source pages.

        This is deliberately a navigation lookup. Every returned record points
        to a page found in ``chunks``; the aliases themselves must not be used
        as factual answer evidence.
        """
        if limit is not None and limit <= 0:
            return []
        normalized_query = _normalize_lookup_text(team_query)
        query_tokens = [
            token
            for token in normalized_query.split()
            if token not in _TEAM_QUERY_NOISE
        ]
        if not query_tokens:
            return []
        focused_query = " ".join(query_tokens)
        focused_tokens = set(query_tokens)
        padded_query = f" {focused_query} "

        matches: list[dict[str, Any]] = []
        for page in self.team_pages(limit=None):
            best_score = 0.0
            for alias in self._team_aliases(page):
                alias_tokens = set(alias.split())
                if not alias_tokens:
                    continue
                if focused_query == alias:
                    score = 500.0 + len(alias_tokens)
                elif f" {alias} " in padded_query:
                    score = 400.0 + len(alias_tokens)
                elif f" {focused_query} " in f" {alias} ":
                    score = 350.0 + len(focused_tokens)
                else:
                    overlap = focused_tokens & alias_tokens
                    if not overlap:
                        continue
                    score = (
                        100.0 * len(overlap) / len(alias_tokens)
                        + 25.0 * len(overlap) / len(focused_tokens)
                    )
                best_score = max(best_score, score)
            if best_score <= 0:
                continue
            matches.append({**page, "match_score": best_score})

        matches.sort(
            key=lambda item: (
                -item["match_score"],
                int(item["team_id"])
                if str(item["team_id"]).isdigit()
                else 10**20,
                item["page_url"],
            )
        )
        return matches if limit is None else matches[:limit]

    def page_sections(self, page_url: str) -> list[dict[str, Any]]:
        """Describe the real, stored sections for a page in source order."""
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT chunk_id,section FROM chunks
                   WHERE page_url = ? ORDER BY id""",
                (page_url,),
            ).fetchall()

        sections: dict[str, dict[str, Any]] = {}
        for row in rows:
            normalized = _normalize_lookup_text(str(row["section"]))
            if normalized not in sections:
                sections[normalized] = {
                    "section": str(row["section"]),
                    "normalized_section": normalized,
                    "chunk_count": 0,
                    "first_chunk_id": str(row["chunk_id"]),
                }
            sections[normalized]["chunk_count"] += 1
        return list(sections.values())

    def team_section_chunks(
        self,
        team_query: str,
        section_query: str,
        *,
        page_limit: int = 1,
        chunk_limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch a complete exact-normalized section for each matched team page.

        With ``chunk_limit=None`` every source chunk in the section is returned.
        Results are ordered by matched page and original chunk insertion order.
        The lookup never reads ``chunk_questions`` or either question index.
        """
        if page_limit <= 0 or (chunk_limit is not None and chunk_limit <= 0):
            return []
        if not _normalize_lookup_text(section_query):
            return []

        results: list[dict[str, Any]] = []
        for page in self.find_team_pages(team_query, limit=page_limit):
            chunks = self.section_chunks(
                page["page_url"],
                section_query,
                limit=None,
            )
            if chunk_limit is not None:
                chunks = chunks[:chunk_limit]
            for chunk in chunks:
                results.append({
                    **chunk,
                    "team_name": page["team_name"],
                    "team_id": page["team_id"],
                })
        return results

    def team_directory_chunks(self) -> list[dict[str, Any]]:
        """Return only real source chunks from the indexed NBA ``/teams`` page."""
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM chunks ORDER BY id"""
            ).fetchall()
        return [
            dict(row)
            for row in rows
            if urlparse(str(row["page_url"])).path.rstrip("/").lower()
            == "/teams"
        ]

    def captain_candidate_chunks(
        self,
        team_query: str,
    ) -> list[dict[str, Any]]:
        """Exhaustively find source chunks that may name a captain for a team.

        This is a source lookup, not a prepared-question lookup. It scans every
        indexed chunk containing captain wording and retains only chunks on the
        resolved team page or chunks that explicitly mention a validated team
        alias.
        """
        [team_page] = self.find_team_pages(team_query, limit=1) or [None]
        if not team_page:
            return []
        canonical_team_url = str(team_page["page_url"])
        aliases = {
            alias
            for alias in self._team_aliases(team_page)
            if len(alias) >= 4 and not alias.isdigit()
        }
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM chunks
                   WHERE lower(text) LIKE '%captain%'
                      OR lower(section) LIKE '%captain%'
                   ORDER BY id"""
            ).fetchall()

        candidates: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if str(item["page_url"]) == canonical_team_url:
                candidates.append(item)
                continue
            searchable = _normalize_lookup_text(
                f"{item['title']} {item['section']} {item['text']}"
            )
            padded = f" {searchable} "
            if any(f" {alias} " in padded for alias in aliases):
                candidates.append(item)
        return candidates

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

    def search_chunk_questions_many(
        self,
        queries: list[str],
        limit: int,
    ) -> list[list[dict[str, Any]]]:
        """Run aligned retrieval-question FTS searches through one connection."""
        batches: list[list[dict[str, Any]]] = []
        with self.connect() as connection:
            for query in queries:
                tokens = self._query_tokens(query)
                if not tokens:
                    batches.append([])
                    continue
                expression = " OR ".join(f'"{token}"' for token in tokens)
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
                batches.append(results)
        return batches

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
        limit: int | None = 8,
    ) -> list[dict[str, Any]]:
        """Return an exact normalized section in original source order.

        The historical default of eight chunks remains unchanged. Callers that
        need a genuinely complete section can pass ``limit=None``.
        """
        if limit is not None and limit <= 0:
            return []
        normalized_section = _normalize_lookup_text(section)
        if not normalized_section:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM chunks
                   WHERE page_url = ? ORDER BY id""",
                (page_url,),
            ).fetchall()
        matching = [
            dict(row)
            for row in rows
            if _normalize_lookup_text(str(row["section"])) == normalized_section
        ]
        return matching if limit is None else matching[:limit]

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
