import hashlib
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

from .config import Settings
from .database import Database
from .vector_store import VectorStore

STOPWORDS = {
    "a", "about", "all", "an", "and", "any", "are", "as", "at", "be",
    "can", "did", "do", "does", "for", "from", "give", "how", "i", "in",
    "is", "it", "least", "list", "many", "me", "of", "on", "or", "please",
    "show", "shown", "snapshot", "some", "tell", "than", "that", "the",
    "their", "there", "these", "this", "to", "was", "what", "when", "where",
    "which", "who", "why", "with", "listed",
}

# These are retrieval vocabulary aliases, not answer-bearing facts. They improve
# recall while leaving every factual claim to the indexed evidence.
EXPANSIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:members?|players?|athletes?)\b.*\bteams?\b|"
            r"\bteams?\b.*\b(?:members?|players?|athletes?)\b",
            re.IGNORECASE,
        ),
        "team roster players lineup",
    ),
    (
        re.compile(r"\bdivisions?\b|\bconferences?\b", re.IGNORECASE),
        "league organization conference division",
    ),
    (
        re.compile(r"\bteams?\b|\bfranchises?\b|\bclubs?\b", re.IGNORECASE),
        "team franchise club roster",
    ),
    (
        re.compile(r"\bpositions?\b|\broles?\b", re.IGNORECASE),
        "player position role guard forward center",
    ),
    (
        re.compile(r"\bscor(?:e|ing)\b|\bpoints?\b", re.IGNORECASE),
        "scoring points free throw field goal",
    ),
    (
        re.compile(r"\bfouls?\b|\bviolations?\b|\bpenalt(?:y|ies)\b", re.IGNORECASE),
        "basketball rules foul violation penalty",
    ),
    (
        re.compile(
            r"\bjerseys?\b|\buniform numbers?\b",
            re.IGNORECASE,
        ),
        "roster player number uniform",
    ),
    (
        re.compile(r"\bcollege\b|\bschools?\b|\blast attended\b", re.IGNORECASE),
        "college school last attended",
    ),
    (
        re.compile(
            r"\bdraft (?:slot|position|selection|pick)\b|"
            r"\bselected\b.*\bdraft\b",
            re.IGNORECASE,
        ),
        "draft pick selection",
    ),
    (
        re.compile(r"\bhome floor\b|\baren[ae]\b|\bvenue\b", re.IGNORECASE),
        "arena venue background",
    ),
    (
        re.compile(r"\bbench boss\b|\bhead coach\b", re.IGNORECASE),
        "coach head coach",
    ),
    (
        re.compile(r"\bhalfway line\b|\bmidcourt\b", re.IGNORECASE),
        "midcourt line",
    ),
    (
        re.compile(
            r"\bhobb(?:y|ies)\b|\brecreational\b|\bpersonal interests?\b",
            re.IGNORECASE,
        ),
        "personal life interests activities",
    ),
    (
        re.compile(
            r"\bretired numbers?\b|"
            r"\bjerseys?\b.*\bhonou?rs?\b|"
            r"\bhonou?rs?\b.*\bjerseys?\b|"
            r"\binduct(?:ed|ion)\b",
            re.IGNORECASE,
        ),
        "retired numbers year induction",
    ),
)


def expand_query(question: str) -> str:
    """Add neutral NBA search vocabulary without adding any answer facts."""
    normalized = " ".join(question.split())
    additions = [expansion for pattern, expansion in EXPANSIONS if pattern.search(normalized)]
    return " ".join(dict.fromkeys(("NBA basketball", normalized, *additions)))


ENTITY_LEADING_WORDS = {
    "compare",
    "do",
    "does",
    "explain",
    "give",
    "how",
    "if",
    "is",
    "list",
    "show",
    "tell",
    "verify",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
}

ENTITY_IGNORED_WORDS = {
    "nba",
    "rule",
    "section",
    "oct",
    "nov",
    "dec",
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
}


def _capitalized_entities(question: str) -> list[str]:
    """Extract user-supplied names without inventing an entity or answer fact."""
    normalized = question.replace("’", "'")
    matches = re.findall(
        r"(?<![A-Za-z0-9])"
        r"(?:[A-Z][A-Za-z0-9'’-]*\.?)"
        r"(?:\s+(?:[A-Z][A-Za-z0-9'’-]*\.?)){0,4}",
        normalized,
    )
    entities: list[str] = []
    for match in matches:
        words = match.strip().split()
        while words and words[0].strip(".'").lower() in ENTITY_LEADING_WORDS:
            words.pop(0)
        while words and words[-1].strip(".'").lower() in {"jr", "sr"}:
            # Keep suffixes only when a name precedes them.
            if len(words) > 1:
                break
            words.pop()
        if not words:
            continue
        entity = " ".join(words).strip()
        normalized_entity = entity.strip(".'").lower()
        if normalized_entity in ENTITY_IGNORED_WORDS:
            continue
        if len(words) == 1 and len(normalized_entity) < 3:
            continue
        if entity.lower() not in {item.lower() for item in entities}:
            entities.append(entity)
    return entities[:5]


def _focused_query(question: str, aliases: list[str]) -> str:
    """Build a concise entity-and-intent query for noisy natural questions."""
    entities = _capitalized_entities(question)
    for alias in aliases:
        if alias.lower() not in {item.lower() for item in entities}:
            entities.append(alias)
    if not entities:
        return ""
    entity_stems = {
        _stem(token)
        for entity in entities
        for token in re.findall(r"[A-Za-z0-9]+", entity.lower())
    }
    intent_tokens = [
        token
        for token in _salient_tokens(question)
        if _stem(token) not in entity_stems
    ]
    additions = [
        expansion
        for pattern, expansion in EXPANSIONS
        if pattern.search(question)
    ]
    return " ".join(
        dict.fromkeys((*entities, *intent_tokens, *additions))
    )


def _stem(token: str) -> str:
    token = token.lower()
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    for suffix in ("ing", "ers", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _salient_tokens(value: str) -> list[str]:
    tokens = [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", value)
        if len(token) > 1
        and not token.isdigit()
        and token.lower() not in STOPWORDS
    ]
    return list(dict.fromkeys(tokens))


def _canonical_page(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", "")
    )


def _team_path_id(url: str) -> str:
    """Return the team id from direct or nested NBA team URLs."""
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in {"nba.com", "www.nba.com"}:
        return ""
    match = re.match(
        r"^/team/(?P<team_id>\d+)(?:/|$)",
        parsed.path,
        flags=re.IGNORECASE,
    )
    return match.group("team_id") if match else ""


def _normalized_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


FACET_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "roster": ("roster", "players", "team roster"),
    "captain": ("captain", "team captain", "captains"),
    "team_list": ("teams", "nba teams", "team list"),
}


def _contains_named_captain_evidence(text: str) -> bool:
    """Require an explicit person-to-captain relation, not a bare rule mention."""
    visible = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    name = (
        r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+"
        r"(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){1,4}"
    )
    patterns = (
        rf"\b(?:team\s+)?(?:co-)?captains?\s*(?:is|are|:|-)\s*{name}\b",
        rf"\b{name}\s+(?:is|was|serves?|served|has\s+been|was\s+named)"
        rf"\s+(?:as\s+)?(?:the\s+)?(?:team\s+)?(?:co-)?captain\b",
        rf"\bnamed\s+{name}\s+(?:as\s+)?(?:the\s+)?"
        rf"(?:team\s+)?(?:co-)?captain\b",
    )
    return any(re.search(pattern, visible) for pattern in patterns)


class HybridRetriever:
    """Retrieve through separate semantic/lexical lanes and fuse ranks."""

    def __init__(self, vector_store: VectorStore, database: Database, config: Settings):
        self.vector_store = vector_store
        self.database = database
        self.config = config
        self._reranker: Any = None

    def _cross_encoder(self) -> Any:
        if self._reranker is None:
            from sentence_transformers import CrossEncoder

            self._reranker = CrossEncoder(self.config.reranker_model)
        return self._reranker

    @staticmethod
    def _content_key(item: dict[str, Any]) -> str:
        normalized = re.sub(r"\s+", " ", item["text"]).strip().lower()
        identity = "||".join(
            (
                _canonical_page(str(item.get("page_url", ""))),
                _normalized_label(str(item.get("section", ""))),
                normalized,
            )
        )
        return hashlib.sha256(identity.encode()).hexdigest()

    @staticmethod
    def _evidence_window(
        text: str,
        query_terms: set[str],
        size: int = 320,
    ) -> str:
        """Select the passage window with the strongest token-boundary evidence."""
        words = text.split()
        if len(words) <= size:
            return text
        best_start = 0
        best_score = -1
        step = max(30, size // 3)
        for start in range(0, len(words), step):
            window = " ".join(words[start : start + size])
            window_stems = {
                _stem(token)
                for token in re.findall(r"[A-Za-z0-9]+", window.lower())
            }
            score = len(query_terms & window_stems)
            if score > best_score:
                best_score = score
                best_start = start
            if start + size >= len(words):
                break
        return " ".join(words[best_start : best_start + size])

    def _fuse(
        self,
        lanes: list[tuple[str, list[dict[str, Any]], float]],
        *,
        k: int = 60,
    ) -> list[dict[str, Any]]:
        fused: dict[str, dict[str, Any]] = {}
        for lane_name, results, lane_weight in lanes:
            for rank, item in enumerate(results, 1):
                chunk_id = item["chunk_id"]
                if chunk_id not in fused:
                    fused[chunk_id] = {
                        **item,
                        "rrf_score": 0.0,
                        "retrieval_sources": [],
                        "variant_rrf_scores": {},
                        "required_variant_rrf_scores": {},
                    }
                candidate = fused[chunk_id]
                contribution = lane_weight / (k + rank)
                candidate["rrf_score"] += contribution
                candidate["retrieval_sources"].append(lane_name)
                variant_index = self._lane_variant_index(lane_name)
                if variant_index is not None:
                    candidate["variant_rrf_scores"][variant_index] = (
                        candidate["variant_rrf_scores"].get(variant_index, 0.0)
                        + contribution
                    )
                    if lane_name.startswith("keyword_required_"):
                        candidate["required_variant_rrf_scores"][variant_index] = (
                            candidate["required_variant_rrf_scores"].get(
                                variant_index,
                                0.0,
                            )
                            + contribution
                        )
                matched_question = item.get(
                    "matched_expected_question",
                    item.get("question"),
                )
                if matched_question:
                    candidate.setdefault("matched_expected_questions", [])
                    if (
                        matched_question
                        not in candidate["matched_expected_questions"]
                    ):
                        candidate["matched_expected_questions"].append(
                            str(matched_question)
                        )
                if lane_name.startswith("semantic"):
                    candidate["semantic_score"] = max(
                        candidate.get("semantic_score", 0.0),
                        float(item.get("score", 0.0)),
                    )
                if lane_name.startswith("keyword"):
                    candidate["keyword_score"] = max(
                        candidate.get("keyword_score", 0.0),
                        float(item.get("score", 0.0)),
                    )
                if lane_name.startswith("question_semantic"):
                    candidate["question_score"] = max(
                        candidate.get("question_score", 0.0),
                        float(item.get("score", 0.0)),
                    )
                if lane_name.startswith("question_keyword"):
                    candidate["question_keyword_score"] = max(
                        candidate.get("question_keyword_score", 0.0),
                        float(item.get("score", 0.0)),
                    )
        ordered = sorted(
            fused.values(),
            key=lambda item: (
                item["rrf_score"],
                len(set(item["retrieval_sources"])),
                item.get("semantic_score", 0.0),
            ),
            reverse=True,
        )
        unique: list[dict[str, Any]] = []
        seen_content: set[str] = set()
        for item in ordered:
            content_key = self._content_key(item)
            if content_key in seen_content:
                continue
            seen_content.add(content_key)
            unique.append(item)
            if len(unique) >= self.config.fusion_top_k * 3:
                break
        return unique

    @staticmethod
    def _lane_variant_index(lane_name: str) -> int | None:
        if lane_name in {
            "keyword_exact",
            "keyword_original",
            "question_keyword_original",
        }:
            return 0
        required_match = re.search(r"_v(\d+)_", lane_name)
        if required_match:
            return int(required_match.group(1))
        match = re.search(r"_(\d+)$", lane_name)
        return int(match.group(1)) if match else None

    def _query_variants(
        self,
        question: str,
        extra_queries: list[str] | None,
    ) -> tuple[list[str], set[int], dict[int, list[str]]]:
        variants = [" ".join(question.split())]
        anchor_indices: set[int] = set()
        required_entities: dict[int, list[str]] = {}
        expanded = expand_query(question)
        if expanded.lower() != variants[0].lower():
            variants.append(expanded)

        title_alias_search = getattr(
            self.database,
            "title_aliases_for_query",
            None,
        )
        title_aliases = (
            title_alias_search(question)
            if callable(title_alias_search)
            else []
        )
        focused = _focused_query(question, title_aliases)
        if (
            focused
            and focused.lower() not in {item.lower() for item in variants}
        ):
            variants.append(focused)
            focused_index = len(variants) - 1
            anchor_indices.add(focused_index)
            supplied_entities = _capitalized_entities(question)
            if title_aliases:
                supplied_entities = [
                    entity
                    for entity in supplied_entities
                    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,5}", entity)
                ]
                supplied_entities.extend(title_aliases)
            entity_groups = list(dict.fromkeys(supplied_entities))
            if len(entity_groups) > 1:
                entity_groups.insert(0, " ".join(entity_groups))
            required_entities[focused_index] = entity_groups[:5]

        for query in extra_queries or []:
            normalized = " ".join(str(query).split())[:180]
            if normalized and normalized.lower() not in {item.lower() for item in variants}:
                variants.append(normalized)
                variant_index = len(variants) - 1
                anchor_indices.add(variant_index)
            if len(variants) >= 8:
                break
        return variants, anchor_indices, required_entities

    def match_prepared_questions(
        self,
        question: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Match one atomic query against retrieval-only question aliases.

        Both the semantic question collection and the SQLite FTS question index
        participate. Returned question text is routing metadata only; callers
        must hydrate and cite the mapped source chunks as evidence.
        """
        bounded_limit = max(1, min(limit, 12))
        matches: dict[str, dict[str, Any]] = {}

        question_search = getattr(
            self.vector_store,
            "search_questions_many",
            None,
        )
        if callable(question_search):
            semantic_batches = question_search([question], bounded_limit)
            semantic_results = semantic_batches[0] if semantic_batches else []
            records = self.database.chunk_questions_by_ids(
                [item["question_id"] for item in semantic_results]
            )
            for rank, item in enumerate(semantic_results, 1):
                record = records.get(item["question_id"])
                if not record:
                    continue
                matches[item["question_id"]] = {
                    **record,
                    "score": float(item.get("score", 0.0)),
                    "rrf_score": 1.0 / (60 + rank),
                    "match_sources": ["prepared_semantic"],
                }

        keyword_search = getattr(
            self.database,
            "search_chunk_questions",
            None,
        )
        if callable(keyword_search):
            for rank, item in enumerate(
                keyword_search(question, bounded_limit),
                1,
            ):
                question_id = item["question_id"]
                candidate = matches.setdefault(
                    question_id,
                    {
                        "question_id": question_id,
                        "chunk_id": item["chunk_id"],
                        "question": item["question"],
                        "kind": item["kind"],
                        "score": 0.0,
                        "rrf_score": 0.0,
                        "match_sources": [],
                    },
                )
                candidate["score"] = max(
                    float(candidate.get("score", 0.0)),
                    float(item.get("score", 0.0)),
                )
                candidate["rrf_score"] += 1.0 / (60 + rank)
                if "prepared_keyword" not in candidate["match_sources"]:
                    candidate["match_sources"].append("prepared_keyword")

        ordered = sorted(
            matches.values(),
            key=lambda item: (
                item.get("rrf_score", 0.0),
                len(item.get("match_sources", [])),
                item.get("score", 0.0),
            ),
            reverse=True,
        )
        return ordered[:bounded_limit]

    def match_prepared_questions_many(
        self,
        questions: list[str],
        limit: int = 3,
        semantic_batches: list[list[dict[str, Any]]] | None = None,
    ) -> list[list[dict[str, Any]]]:
        """Batch semantic alias matching while retaining an FTS lane per task."""
        if not questions:
            return []
        bounded_limit = max(1, min(limit, 12))
        question_search = getattr(
            self.vector_store,
            "search_questions_many",
            None,
        )
        if semantic_batches is None:
            semantic_batches = (
                question_search(questions, bounded_limit)
                if callable(question_search)
                else [[] for _ in questions]
            )
        if len(semantic_batches) != len(questions):
            raise ValueError(
                "Prepared-question semantic batches must align with queries."
            )
        all_question_ids = list(
            dict.fromkeys(
                item["question_id"]
                for batch in semantic_batches
                for item in batch
            )
        )
        records = self.database.chunk_questions_by_ids(all_question_ids)
        keyword_search = getattr(
            self.database,
            "search_chunk_questions",
            None,
        )
        keyword_search_many = getattr(
            self.database,
            "search_chunk_questions_many",
            None,
        )
        keyword_batches = (
            keyword_search_many(questions, bounded_limit)
            if callable(keyword_search_many)
            else [
                (
                    keyword_search(question, bounded_limit)
                    if callable(keyword_search)
                    else []
                )
                for question in questions
            ]
        )

        batches: list[list[dict[str, Any]]] = []
        for semantic_results, keyword_results in zip(
            semantic_batches,
            keyword_batches,
        ):
            matches: dict[str, dict[str, Any]] = {}
            for rank, item in enumerate(semantic_results, 1):
                record = records.get(item["question_id"])
                if not record:
                    continue
                matches[item["question_id"]] = {
                    **record,
                    "score": float(item.get("score", 0.0)),
                    "rrf_score": 1.0 / (60 + rank),
                    "match_sources": ["prepared_semantic"],
                }
            for rank, item in enumerate(keyword_results, 1):
                question_id = item["question_id"]
                candidate = matches.setdefault(
                    question_id,
                    {
                        "question_id": question_id,
                        "chunk_id": item["chunk_id"],
                        "question": item["question"],
                        "kind": item["kind"],
                        "score": 0.0,
                        "rrf_score": 0.0,
                        "match_sources": [],
                    },
                )
                candidate["score"] = max(
                    float(candidate.get("score", 0.0)),
                    float(item.get("score", 0.0)),
                )
                candidate["rrf_score"] += 1.0 / (60 + rank)
                if "prepared_keyword" not in candidate["match_sources"]:
                    candidate["match_sources"].append("prepared_keyword")
            ordered = sorted(
                matches.values(),
                key=lambda item: (
                    item.get("rrf_score", 0.0),
                    len(item.get("match_sources", [])),
                    item.get("score", 0.0),
                ),
                reverse=True,
            )
            batches.append(ordered[:bounded_limit])
        return batches

    @staticmethod
    def _atomic_terms(question: str) -> list[str]:
        return _salient_tokens(question)

    def _decorate_complete_section(
        self,
        chunks: list[dict[str, Any]],
        routed_results: list[dict[str, Any]],
        prepared_matches: list[dict[str, Any]],
        question: str,
    ) -> list[dict[str, Any]]:
        if not chunks:
            return []
        best_score = max(
            (float(item.get("score", 0.0)) for item in routed_results),
            default=0.0,
        )
        by_chunk = {
            item["chunk_id"]: item
            for item in routed_results
        }
        by_page_section = {
            (_canonical_page(item["page_url"]), _normalized_label(item["section"])): item
            for item in routed_results
        }
        aggregate_sources = list(
            dict.fromkeys(
                source
                for item in routed_results
                for source in item.get("retrieval_sources", [])
            )
        )
        route_hints = list(
            dict.fromkeys(
                str(item["question"])
                for item in prepared_matches
                if item.get("question")
            )
        )[:5]
        terms = self._atomic_terms(question)
        decorated: list[dict[str, Any]] = []
        for chunk in chunks:
            routed = by_chunk.get(chunk["chunk_id"]) or by_page_section.get(
                (
                    _canonical_page(chunk["page_url"]),
                    _normalized_label(chunk["section"]),
                )
            )
            sources = list(
                dict.fromkeys(
                    (
                        *(
                            (routed or {}).get("retrieval_sources", [])
                            or aggregate_sources
                        ),
                        "complete_team_section",
                    )
                )
            )
            hints = list(
                dict.fromkeys(
                    (
                        *((routed or {}).get("matched_expected_questions", [])),
                        *route_hints,
                    )
                )
            )[:5]
            searchable = (
                f"{chunk['title']} {chunk['section']} {chunk['text']}"
            ).lower()
            decorated.append({
                **chunk,
                "score": float((routed or {}).get("score", best_score)),
                "retrieval_sources": sources,
                "matched_terms": [
                    term for term in terms if _stem(term) in searchable
                ],
                "matched_expected_questions": hints,
            })
        return decorated

    def _rank_atomic_lanes(
        self,
        question: str,
        variants: list[str],
        lanes: list[tuple[str, list[dict[str, Any]], float]],
        limit: int,
    ) -> list[dict[str, Any]]:
        fused = self._fuse(lanes)
        if not fused:
            return []
        original_tokens = _salient_tokens(question)
        original_stems = {_stem(token) for token in original_tokens}
        variant_stems = {
            _stem(token)
            for variant in variants[1:]
            for token in _salient_tokens(variant)
        }
        max_rrf = max(item["rrf_score"] for item in fused)
        for item in fused:
            searchable = f"{item['title']} {item['section']} {item['text']}"
            searchable_stems = {
                _stem(token)
                for token in re.findall(r"[A-Za-z0-9]+", searchable.lower())
            }
            matched_original = original_stems & searchable_stems
            matched_variants = variant_stems & searchable_stems
            original_coverage = len(matched_original) / max(1, len(original_stems))
            variant_coverage = len(matched_variants) / max(1, len(variant_stems))
            sources = set(item["retrieval_sources"])
            cross_channel = bool(
                any(source.startswith("semantic_") for source in sources)
                and any(source.startswith("keyword_") for source in sources)
            )
            item["original_coverage"] = original_coverage
            item["matched_terms"] = sorted(
                token for token in original_tokens if _stem(token) in matched_original
            )
            item["score"] = max(
                0.0,
                (0.33 * item.get("semantic_score", 0.0))
                + (0.15 * item.get("question_score", 0.0))
                + (0.08 * item.get("keyword_score", 0.0))
                + (0.05 * item.get("question_keyword_score", 0.0))
                + (0.18 * original_coverage)
                + (0.08 * variant_coverage)
                + (0.08 if cross_channel else 0.0)
                + (0.05 * (item["rrf_score"] / max_rrf)),
            )
            item["evidence_text"] = self._evidence_window(
                item["text"],
                original_stems | variant_stems,
            )

        if self.config.enable_reranker:
            logits = self._cross_encoder().predict(
                [(question, item["evidence_text"]) for item in fused],
                show_progress_bar=False,
            )
            for item, logit in zip(fused, logits):
                item["rerank_score"] = float(logit)
            fused.sort(
                key=lambda item: (item["rerank_score"], item["score"]),
                reverse=True,
            )
        else:
            fused.sort(
                key=lambda item: (item["score"], item["rrf_score"]),
                reverse=True,
            )

        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        page_counts: dict[str, int] = {}
        for page_limit in (1, 2):
            for item in fused:
                if item["chunk_id"] in selected_ids:
                    continue
                page_key = _canonical_page(item["page_url"])
                if page_counts.get(page_key, 0) >= page_limit:
                    continue
                selected.append(item)
                selected_ids.add(item["chunk_id"])
                page_counts[page_key] = page_counts.get(page_key, 0) + 1
                if len(selected) >= limit:
                    break
            if len(selected) >= limit:
                break
        for item in selected:
            if "evidence_text" in item:
                item["text"] = item.pop("evidence_text")
        return selected

    def _finalize_atomic_bundle(
        self,
        task: dict[str, Any],
        prepared_matches: list[dict[str, Any]],
        routed_results: list[dict[str, Any]],
        retrieval_channels: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_question = " ".join(str(task["question"]).split())
        normalized_facet = (
            _normalized_label(str(task.get("facet", "general"))).replace(" ", "_")
            or "general"
        )
        team_name = " ".join(str(task.get("team_name", "")).split())
        requires_complete_section = bool(
            task.get("requires_complete_section")
        )
        requires_complete_population = bool(
            task.get("requires_complete_population")
        )
        evidence = routed_results

        if normalized_facet == "team_list" and requires_complete_section:
            directory_lookup = getattr(
                self.database,
                "team_directory_chunks",
                None,
            )
            directory_chunks = (
                directory_lookup()
                if callable(directory_lookup)
                else []
            )
            if directory_chunks:
                evidence = self._decorate_complete_section(
                    directory_chunks,
                    routed_results,
                    prepared_matches,
                    normalized_question,
                )
            else:
                evidence = []

        team_pages: list[dict[str, Any]] = []
        find_team_pages = getattr(self.database, "find_team_pages", None)
        if team_name and callable(find_team_pages):
            team_pages = find_team_pages(team_name, limit=3)
        team_page_urls = {
            _canonical_page(item["page_url"])
            for item in team_pages
            if item.get("page_url")
        }
        team_ids = {
            str(item.get("team_id", "")).strip()
            for item in team_pages
            if str(item.get("team_id", "")).strip()
        }
        team_slugs = {
            _normalized_label(str(item.get("slug", "")))
            for item in team_pages
            if _normalized_label(str(item.get("slug", "")))
        }
        team_names = {
            _normalized_label(str(item.get("team_name", "")))
            for item in team_pages
            if _normalized_label(str(item.get("team_name", "")))
        }

        section_lookup = getattr(
            self.database,
            "team_section_chunks",
            None,
        )
        expanded_complete_section = False
        if team_name and not team_pages:
            # Team-specific evidence must fail closed when a planner entity
            # cannot be resolved to an indexed source page.
            evidence = []
        if (
            team_name
            and team_pages
            and callable(section_lookup)
            and normalized_facet in FACET_SECTION_ALIASES
        ):
            complete_chunks: list[dict[str, Any]] = []
            for section_alias in FACET_SECTION_ALIASES[normalized_facet]:
                complete_chunks = section_lookup(
                    team_name,
                    section_alias,
                    page_limit=1,
                    chunk_limit=None,
                )
                if complete_chunks:
                    break
            if complete_chunks:
                evidence = self._decorate_complete_section(
                    complete_chunks,
                    routed_results,
                    prepared_matches,
                    normalized_question,
                )
                expanded_complete_section = True
            elif normalized_facet == "captain":
                captain_lookup = getattr(
                    self.database,
                    "captain_candidate_chunks",
                    None,
                )
                captain_candidates = (
                    captain_lookup(team_name)
                    if callable(captain_lookup)
                    else []
                )
                named_candidates = [
                    item
                    for item in captain_candidates
                    if _contains_named_captain_evidence(
                        f"{item['section']}\n{item['text']}"
                    )
                ]
                evidence = self._decorate_complete_section(
                    named_candidates,
                    routed_results,
                    prepared_matches,
                    normalized_question,
                )
            elif requires_complete_section:
                evidence = []

        if team_page_urls and normalized_facet not in {"roster", "captain"}:
            team_specific = [
                item
                for item in evidence
                if (
                    _canonical_page(item["page_url"]) in team_page_urls
                    or _team_path_id(item["page_url"]) in team_ids
                    or any(
                        f" {slug} "
                        in (
                            f" {_normalized_label(urlparse(item['page_url']).path)} "
                        )
                        for slug in team_slugs
                    )
                    or any(
                        f" {name} "
                        in (
                            " "
                            + _normalized_label(
                                " ".join(
                                    (
                                        str(item.get("title", "")),
                                        str(item.get("section", "")),
                                        str(item.get("text", "")),
                                    )
                                )
                            )
                            + " "
                        )
                        for name in team_names
                    )
                )
            ]
            if team_specific or normalized_facet == "captain":
                evidence = team_specific

        if (
            requires_complete_section
            and evidence
            and not expanded_complete_section
            and normalized_facet != "team_list"
        ):
            section_chunks = getattr(self.database, "section_chunks", None)
            if callable(section_chunks):
                anchor = evidence[0]
                try:
                    complete_chunks = section_chunks(
                        anchor["page_url"],
                        anchor["section"],
                        limit=None,
                    )
                except TypeError:
                    complete_chunks = section_chunks(
                        anchor["page_url"],
                        anchor["section"],
                        limit=64,
                    )
                if complete_chunks:
                    evidence = self._decorate_complete_section(
                        complete_chunks,
                        routed_results,
                        prepared_matches,
                        normalized_question,
                    )
                    expanded_complete_section = True

        # A complete page section is not the same as a complete league-wide
        # population. Exact counts, rankings and extrema must stay partial
        # until a deterministic population/metric verifier explicitly marks
        # the task as covered.
        population_coverage_verified = bool(
            task.get("population_coverage_verified")
        )
        is_complete = (
            bool(evidence)
            and (
                not requires_complete_section
                or expanded_complete_section
                or normalized_facet == "team_list"
            )
            and (
                not requires_complete_population
                or population_coverage_verified
            )
        )
        if not evidence:
            status = "missing"
            missing_reason = "No qualified original source chunks were retrieved."
        elif requires_complete_population and not is_complete:
            status = "partial"
            missing_reason = (
                "Complete population and metric coverage was not verified."
            )
        elif requires_complete_section and not is_complete:
            status = "partial"
            missing_reason = "A complete source section was not available."
        elif is_complete and requires_complete_section:
            status = "complete"
            missing_reason = ""
        else:
            status = "found"
            missing_reason = ""
        return {
            **task,
            "question": normalized_question,
            "facet": normalized_facet,
            "team_name": team_name,
            "matched_questions": prepared_matches,
            "retrieval_channels": list(retrieval_channels or []),
            "evidence": evidence,
            "complete": is_complete,
            "status": status,
            "evidence_count": len(evidence),
            "missing_reason": missing_reason,
        }

    def search_atomic_many(
        self,
        tasks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Retrieve every atomic task and its rewrites through all four lanes.

        Planner rewrites first match the prepared-question semantic/keyword
        indexes. Those aliases only route back to original chunks. The same
        rewrites also search the original source semantic/keyword indexes.
        """
        if not tasks:
            return []
        normalized_tasks = [
            {
                **task,
                "question": " ".join(str(task["question"]).split()),
                "facet": (
                    _normalized_label(str(task.get("facet", "general")))
                    .replace(" ", "_")
                    or "general"
                ),
                "team_name": " ".join(
                    str(task.get("team_name", "")).split()
                ),
                "search_queries": [
                    " ".join(str(item).split())[:240]
                    for item in (
                        task.get("search_queries", [])
                        if isinstance(task.get("search_queries", []), list)
                        else []
                    )
                    if " ".join(str(item).split())
                ][:4],
            }
            for task in tasks
        ]

        base_variants_by_task: list[list[str]] = []
        flattened_base_variants: list[str] = []
        base_variant_ranges: list[tuple[int, int]] = []
        for task in normalized_tasks:
            variants = [task["question"]]
            for query in task["search_queries"]:
                if query.lower() not in {item.lower() for item in variants}:
                    variants.append(query)
            expanded = expand_query(task["question"])
            if expanded.lower() not in {item.lower() for item in variants}:
                variants.append(expanded)
            if int(task.get("atomic_task_count", 1)) == 1:
                original_question = " ".join(
                    str(task.get("original_question", "")).split()
                )[:300]
                if (
                    original_question
                    and original_question.lower()
                    not in {item.lower() for item in variants}
                ):
                    variants.append(original_question)
            keyword_query = " ".join(
                str(item).strip()
                for item in task.get("keywords", [])[:8]
                if str(item).strip()
            )[:240]
            if (
                keyword_query
                and keyword_query.lower()
                not in {item.lower() for item in variants}
            ):
                variants.append(keyword_query)
            variants = variants[:6]
            start = len(flattened_base_variants)
            flattened_base_variants.extend(variants)
            base_variant_ranges.append(
                (start, len(flattened_base_variants))
            )
            base_variants_by_task.append(variants)

        combined_vector_search = getattr(
            self.vector_store,
            "search_source_and_questions_many",
            None,
        )
        base_source_semantic_flat: list[list[dict[str, Any]]] | None = None
        prepared_semantic_flat: list[list[dict[str, Any]]] | None = None
        if callable(combined_vector_search):
            (
                base_source_semantic_flat,
                prepared_semantic_flat,
            ) = combined_vector_search(
                flattened_base_variants,
                self.config.semantic_top_k,
                3,
            )
        prepared_variant_batches = self.match_prepared_questions_many(
            flattened_base_variants,
            limit=3,
            semantic_batches=prepared_semantic_flat,
        )

        prepared_batches: list[list[dict[str, Any]]] = []
        for start, end in base_variant_ranges:
            merged: dict[str, dict[str, Any]] = {}
            for variant_index, matches in enumerate(
                prepared_variant_batches[start:end]
            ):
                variant_weight = 1.0 if variant_index == 0 else 0.82
                query_variant = flattened_base_variants[
                    start + variant_index
                ]
                for rank, match in enumerate(matches, 1):
                    question_id = str(match["question_id"])
                    candidate = merged.setdefault(
                        question_id,
                        {
                            **match,
                            "rrf_score": 0.0,
                            "match_sources": [],
                            "matched_query_variants": [],
                        },
                    )
                    candidate["score"] = max(
                        float(candidate.get("score", 0.0)),
                        float(match.get("score", 0.0)),
                    )
                    candidate["rrf_score"] += (
                        variant_weight / (60 + rank)
                    )
                    candidate["match_sources"] = list(
                        dict.fromkeys(
                            (
                                *candidate.get("match_sources", []),
                                *match.get("match_sources", []),
                            )
                        )
                    )
                    if (
                        query_variant
                        not in candidate["matched_query_variants"]
                    ):
                        candidate["matched_query_variants"].append(
                            query_variant
                        )
            prepared_batches.append(
                sorted(
                    merged.values(),
                    key=lambda item: (
                        item.get("rrf_score", 0.0),
                        len(item.get("match_sources", [])),
                        item.get("score", 0.0),
                    ),
                    reverse=True,
                )[:5]
            )

        source_variants_by_task: list[list[str]] = []
        flattened_variants: list[str] = []
        variant_ranges: list[tuple[int, int]] = []
        prepared_alias_starts: list[int] = []
        for base_variants, prepared_matches in zip(
            base_variants_by_task,
            prepared_batches,
        ):
            variants = list(base_variants)
            prepared_alias_starts.append(len(variants))
            for match in prepared_matches[:2]:
                route_query = " ".join(str(match["question"]).split())[:300]
                if (
                    route_query
                    and route_query.lower()
                    not in {item.lower() for item in variants}
                ):
                    variants.append(route_query)
            start = len(flattened_variants)
            flattened_variants.extend(variants)
            variant_ranges.append((start, len(flattened_variants)))
            source_variants_by_task.append(variants)

        if base_source_semantic_flat is None:
            semantic_flat = self.vector_store.search_many(
                flattened_variants,
                self.config.semantic_top_k,
            )
        else:
            flattened_aliases: list[str] = []
            alias_ranges: list[tuple[int, int]] = []
            for base_variants, source_variants in zip(
                base_variants_by_task,
                source_variants_by_task,
            ):
                start = len(flattened_aliases)
                flattened_aliases.extend(
                    source_variants[len(base_variants):]
                )
                alias_ranges.append((start, len(flattened_aliases)))
            alias_semantic_flat = (
                self.vector_store.search_many(
                    flattened_aliases,
                    self.config.semantic_top_k,
                )
                if flattened_aliases
                else []
            )
            semantic_flat = []
            for (
                (base_start, base_end),
                (alias_start, alias_end),
            ) in zip(base_variant_ranges, alias_ranges):
                semantic_flat.extend(
                    base_source_semantic_flat[base_start:base_end]
                )
                semantic_flat.extend(
                    alias_semantic_flat[alias_start:alias_end]
                )
        prepared_chunk_ids = list(
            dict.fromkeys(
                match["chunk_id"]
                for batch in prepared_batches
                for match in batch
            )
        )
        prepared_chunks = {
            item["chunk_id"]: item
            for item in self.database.chunks_by_ids(prepared_chunk_ids)
        }
        source_keyword_many = getattr(self.database, "search_many", None)
        if callable(source_keyword_many):
            exact_keyword_batches = source_keyword_many(
                [task["question"] for task in normalized_tasks],
                self.config.keyword_top_k,
                match_mode="all",
            )
            broad_keyword_flat = source_keyword_many(
                flattened_variants,
                self.config.keyword_top_k,
                match_mode="any",
            )
        else:
            exact_keyword_batches = [
                self.database.search(
                    task["question"],
                    self.config.keyword_top_k,
                    match_mode="all",
                )
                for task in normalized_tasks
            ]
            broad_keyword_flat = [
                self.database.search(
                    query,
                    self.config.keyword_top_k,
                    match_mode="any",
                )
                for query in flattened_variants
            ]

        results: list[dict[str, Any]] = []
        for task_index, task in enumerate(normalized_tasks):
            variants = source_variants_by_task[task_index]
            base_variants = base_variants_by_task[task_index]
            prepared_alias_start = prepared_alias_starts[task_index]
            start, end = variant_ranges[task_index]
            semantic_batches = semantic_flat[start:end]
            prepared_matches = prepared_batches[task_index]
            lanes: list[tuple[str, list[dict[str, Any]], float]] = []
            for variant_index, semantic_results in enumerate(semantic_batches):
                weight = (
                    1.0
                    if variant_index == 0
                    else 0.84
                    if variant_index < prepared_alias_start
                    else 0.72
                )
                lanes.append(
                    (
                        f"semantic_{variant_index}",
                        semantic_results,
                        weight,
                    )
                )

            for match_index, match in enumerate(prepared_matches):
                source_chunk = prepared_chunks.get(match["chunk_id"])
                if not source_chunk:
                    continue
                resolved = [{
                    **source_chunk,
                    "score": float(match.get("score", 0.0)),
                    "matched_expected_question": match["question"],
                }]
                match_sources = set(match.get("match_sources", []))
                if "prepared_semantic" in match_sources:
                    lanes.append(
                        (
                            f"question_semantic_{match_index}",
                            resolved,
                            0.82,
                        )
                    )
                if "prepared_keyword" in match_sources:
                    lanes.append(
                        (
                            f"question_keyword_{match_index}",
                            resolved,
                            0.72,
                        )
                    )

            exact = exact_keyword_batches[task_index]
            if exact:
                lanes.append(("keyword_exact", exact, 1.25))
            broad_batches = broad_keyword_flat[start:end]
            broad = broad_batches[0] if broad_batches else []
            if broad:
                lanes.append(("keyword_original", broad, 1.0))
            for variant_index, keyword_results in enumerate(
                broad_batches[1:],
                1,
            ):
                if keyword_results:
                    lanes.append(
                        (
                            f"keyword_variant_{variant_index}",
                            keyword_results,
                            (
                                0.78
                                if variant_index < prepared_alias_start
                                else 0.62
                            ),
                        )
                    )

            routed_results = self._rank_atomic_lanes(
                task["question"],
                variants,
                lanes,
                max(
                    1,
                    int(
                        task.get(
                            "result_limit",
                            self.config.composite_task_top_k,
                        )
                    ),
                ),
            )
            qualified_results = [
                item
                for item in routed_results
                if float(item.get("score", 0.0))
                >= self.config.min_retrieval_score
            ]
            results.append(
                self._finalize_atomic_bundle(
                    {
                        **task,
                        "query_variants": base_variants,
                    },
                    prepared_matches,
                    qualified_results,
                    [
                        lane_name
                        for lane_name, lane_results, _weight in lanes
                        if lane_results
                    ],
                )
            )
        return results

    def search_atomic(
        self,
        question: str,
        *,
        facet: str = "general",
        team_name: str = "",
        result_limit: int | None = None,
        requires_complete_section: bool = False,
        search_queries: list[str] | None = None,
    ) -> dict[str, Any]:
        """Retrieve one decomposed task through all hybrid routing lanes."""
        return self.search_atomic_many([{
            "question": question,
            "facet": facet,
            "team_name": team_name,
            "search_queries": list(search_queries or []),
            "result_limit": (
                result_limit or self.config.retrieval_top_k
            ),
            "requires_complete_section": requires_complete_section,
        }])[0]

    def search(
        self,
        question: str,
        extra_queries: list[str] | None = None,
        *,
        result_limit: int | None = None,
        complete_sections: bool = False,
        group_count: int = 1,
    ) -> list[dict[str, Any]]:
        variants, anchor_indices, required_entities = self._query_variants(
            question,
            extra_queries,
        )
        semantic_batches = self.vector_store.search_many(
            variants,
            self.config.semantic_top_k,
        )
        lanes: list[tuple[str, list[dict[str, Any]], float]] = []
        for index, results in enumerate(semantic_batches):
            weight = 1.0 if index == 0 else 0.78 if index == 1 else 0.9
            lanes.append((f"semantic_{index}", results, weight))

        question_search = getattr(self.vector_store, "search_questions_many", None)
        if callable(question_search):
            question_batches = question_search(
                variants,
                self.config.semantic_top_k,
            )
            for index, question_results in enumerate(question_batches):
                question_records = self.database.chunk_questions_by_ids(
                    [item["question_id"] for item in question_results]
                )
                ordered_ids = list(
                    dict.fromkeys(item["chunk_id"] for item in question_results)
                )
                hydrated = {
                    item["chunk_id"]: item
                    for item in self.database.chunks_by_ids(ordered_ids)
                }
                resolved: list[dict[str, Any]] = []
                seen_chunks: set[str] = set()
                for question_result in question_results:
                    chunk_id = question_result["chunk_id"]
                    question_record = question_records.get(
                        question_result["question_id"]
                    )
                    if (
                        chunk_id in seen_chunks
                        or chunk_id not in hydrated
                        or not question_record
                    ):
                        continue
                    seen_chunks.add(chunk_id)
                    resolved.append({
                        **hydrated[chunk_id],
                        "score": question_result["score"],
                        "matched_expected_question": question_record["question"],
                    })
                if resolved:
                    weight = 0.82 if index == 0 else 0.65
                    lanes.append((f"question_semantic_{index}", resolved, weight))

        exact = self.database.search(
            question,
            self.config.keyword_top_k,
            match_mode="all",
        )
        if exact:
            lanes.append(("keyword_exact", exact, 1.25))
        broad = self.database.search(
            question,
            self.config.keyword_top_k,
            match_mode="any",
        )
        if broad:
            lanes.append(("keyword_original", broad, 1.0))
        question_keyword_search = getattr(
            self.database,
            "search_chunk_questions",
            None,
        )
        if callable(question_keyword_search):
            question_keyword = question_keyword_search(
                question,
                self.config.keyword_top_k,
            )
            if question_keyword:
                lanes.append(("question_keyword_original", question_keyword, 0.82))
        for index, query in enumerate(variants[1:], 1):
            planned = self.database.search(
                query,
                self.config.keyword_top_k,
                match_mode="any",
            )
            if planned:
                weight = 0.6 if index == 1 else 0.72
                lanes.append((f"keyword_variant_{index}", planned, weight))
            if callable(question_keyword_search):
                question_planned = question_keyword_search(
                    query,
                    self.config.keyword_top_k,
                )
                if question_planned:
                    lanes.append(
                        (f"question_keyword_variant_{index}", question_planned, 0.55)
                    )
            required_search = getattr(
                self.database,
                "search_with_required",
                None,
            )
            if callable(required_search):
                for entity_index, required in enumerate(
                    required_entities.get(index, [])
                ):
                    entity_results = required_search(
                        query,
                        required,
                        self.config.keyword_top_k,
                    )
                    if entity_results:
                        lanes.append(
                            (
                                f"keyword_required_v{index}_e{entity_index}",
                                entity_results,
                                1.4,
                            )
                        )

        fused = self._fuse(lanes)
        if not fused:
            return []

        original_tokens = _salient_tokens(question)
        original_stems = {_stem(token) for token in original_tokens}
        variant_stems = {
            _stem(token)
            for variant in variants[1:]
            for token in _salient_tokens(variant)
        }
        query_stems = original_stems | variant_stems
        max_rrf = max(item["rrf_score"] for item in fused)
        for item in fused:
            searchable = f"{item['title']} {item['section']} {item['text']}"
            searchable_stems = {
                _stem(token)
                for token in re.findall(r"[A-Za-z0-9]+", searchable.lower())
            }
            matched_original = original_stems & searchable_stems
            matched_variants = variant_stems & searchable_stems
            original_coverage = len(matched_original) / max(1, len(original_stems))
            variant_coverage = len(matched_variants) / max(1, len(variant_stems))
            sources = set(item["retrieval_sources"])
            cross_channel = bool(
                any(source.startswith("semantic_") for source in sources)
                and any(source.startswith("keyword_") for source in sources)
            )
            item["original_coverage"] = original_coverage
            item["matched_terms"] = sorted(
                token for token in original_tokens if _stem(token) in matched_original
            )
            item["score"] = max(
                0.0,
                (0.38 * item.get("semantic_score", 0.0))
                + (0.18 * item.get("question_score", 0.0))
                + (0.18 * original_coverage)
                + (0.08 * variant_coverage)
                + (0.10 if cross_channel else 0.0)
                + (0.08 * (item["rrf_score"] / max_rrf)),
            )
            item["evidence_text"] = self._evidence_window(
                item["text"],
                original_stems | variant_stems,
            )

        if self.config.enable_reranker:
            logits = self._cross_encoder().predict(
                [(question, item["evidence_text"]) for item in fused],
                show_progress_bar=False,
            )
            for item, logit in zip(fused, logits):
                item["rerank_score"] = float(logit)
            fused.sort(
                key=lambda item: (item["rerank_score"], item["score"]),
                reverse=True,
            )
        else:
            fused.sort(key=lambda item: (item["score"], item["rrf_score"]), reverse=True)

        limit = max(1, result_limit or self.config.retrieval_top_k)
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()

        if complete_sections:
            requested_groups = max(
                1,
                min(group_count, self.config.max_composite_groups),
            )
            section_groups: dict[str, list[dict[str, Any]]] = {}
            for item in fused:
                section_key = re.sub(
                    r"[^a-z0-9]+",
                    " ",
                    item["section"].lower(),
                ).strip()
                section_groups.setdefault(section_key, []).append(item)

            eligible_groups: list[tuple[float, list[dict[str, Any]]]] = []
            for section_key, items in section_groups.items():
                unique_pages: list[dict[str, Any]] = []
                seen_pages: set[str] = set()
                for item in items:
                    page_key = _canonical_page(item["page_url"])
                    if page_key in seen_pages:
                        continue
                    seen_pages.add(page_key)
                    unique_pages.append(item)
                if len(unique_pages) < requested_groups:
                    continue
                section_stems = {
                    _stem(token)
                    for token in re.findall(r"[A-Za-z0-9]+", section_key)
                }
                section_match = len(section_stems & query_stems)
                cluster_score = (
                    sum(item["score"] for item in unique_pages[:requested_groups])
                    + (0.20 * section_match)
                )
                eligible_groups.append((cluster_score, unique_pages))

            if eligible_groups:
                eligible_groups.sort(key=lambda entry: entry[0], reverse=True)
                anchors = eligible_groups[0][1][:requested_groups]
            else:
                anchors = []
                seen_pages = set()
                for item in fused:
                    page_key = _canonical_page(item["page_url"])
                    if page_key in seen_pages:
                        continue
                    seen_pages.add(page_key)
                    anchors.append(item)
                    if len(anchors) >= requested_groups:
                        break
            for anchor in anchors:
                section_items = self.database.section_chunks(
                    anchor["page_url"],
                    anchor["section"],
                    limit=8,
                )
                if not section_items:
                    section_items = [anchor]
                for item in section_items:
                    if item["chunk_id"] in selected_ids:
                        continue
                    item["score"] = anchor["score"]
                    item["retrieval_sources"] = list(
                        dict.fromkeys((*anchor["retrieval_sources"], "complete_section"))
                    )
                    item["matched_terms"] = list(anchor["matched_terms"])
                    if anchor.get("matched_expected_questions"):
                        item["matched_expected_questions"] = list(
                            anchor["matched_expected_questions"]
                        )
                    selected.append(item)
                    selected_ids.add(item["chunk_id"])
            if selected:
                complete_limit = max(limit, requested_groups * 8)
                return selected[:complete_limit]

        page_counts: dict[str, int] = {}
        # Preserve the best real source hit for each focused/planned query.
        # This prevents one broad facet from crowding out another facet of a
        # combined question. The aliases and planned questions remain retrieval
        # labels only; every selected item is still an original source chunk.
        for variant_index in sorted(anchor_indices):
            candidates = [
                item
                for item in fused
                if item.get("variant_rrf_scores", {}).get(variant_index, 0.0) > 0
            ]
            if not candidates:
                continue
            anchor = max(
                candidates,
                key=lambda item: (
                    bool(
                        item.get("required_variant_rrf_scores", {}).get(
                            variant_index,
                            0.0,
                        )
                    ),
                    item.get("required_variant_rrf_scores", {}).get(
                        variant_index,
                        0.0,
                    ),
                    item["variant_rrf_scores"][variant_index],
                    item["score"],
                    item["rrf_score"],
                ),
            )
            if anchor["chunk_id"] in selected_ids:
                continue
            selected.append(anchor)
            selected_ids.add(anchor["chunk_id"])
            page_key = _canonical_page(anchor["page_url"])
            page_counts[page_key] = page_counts.get(page_key, 0) + 1
            if len(selected) >= limit:
                break

        for page_limit in (1, 2):
            for item in fused:
                if item["chunk_id"] in selected_ids:
                    continue
                page_key = _canonical_page(item["page_url"])
                if page_counts.get(page_key, 0) >= page_limit:
                    continue
                selected.append(item)
                selected_ids.add(item["chunk_id"])
                page_counts[page_key] = page_counts.get(page_key, 0) + 1
                if len(selected) >= limit:
                    break
            if len(selected) >= limit:
                break

        selected.sort(
            key=lambda item: (item["score"], item["rrf_score"]),
            reverse=True,
        )
        expanded_selection = list(selected)
        max_context_chunks = min(12, limit + max(1, limit // 2))
        for item in selected:
            for neighbor in self.database.adjacent_chunks(item["chunk_id"]):
                if neighbor["chunk_id"] in selected_ids:
                    continue
                neighbor_searchable = (
                    f"{neighbor['title']} {neighbor['section']} {neighbor['text']}"
                )
                neighbor_stems = {
                    _stem(token)
                    for token in re.findall(r"[A-Za-z0-9]+", neighbor_searchable.lower())
                }
                if not (query_stems & neighbor_stems):
                    continue
                neighbor["score"] = item["score"]
                neighbor["retrieval_sources"] = ["adjacent"]
                neighbor["matched_terms"] = sorted(
                    token
                    for token in original_tokens
                    if _stem(token) in neighbor_stems
                )
                if item.get("matched_expected_questions"):
                    neighbor["matched_expected_questions"] = list(
                        item["matched_expected_questions"]
                    )
                expanded_selection.append(neighbor)
                selected_ids.add(neighbor["chunk_id"])
                if len(expanded_selection) >= max_context_chunks:
                    break
            if len(expanded_selection) >= max_context_chunks:
                break

        for item in expanded_selection:
            if "evidence_text" in item:
                item["text"] = item.pop("evidence_text")
        return expanded_selection
