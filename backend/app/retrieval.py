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
        return hashlib.sha256(normalized.encode()).hexdigest()

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
            requested_groups = max(1, min(group_count, 4))
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
                return selected[: max(limit, min(16, len(selected)))]

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
