import hashlib
import re
from typing import Any

from .config import Settings
from .database import Database
from .vector_store import VectorStore

EXPANSIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bdivisions?\b", re.IGNORECASE),
        "NBA league structure divisions Eastern Conference Western Conference "
        "Atlantic Central Southeast Northwest Pacific Southwest",
    ),
    (
        re.compile(r"\bconferences?\b", re.IGNORECASE),
        "NBA league structure two conferences Eastern Conference Western Conference",
    ),
    (
        re.compile(r"\bteams?\b|\bfranchises?\b", re.IGNORECASE),
        "NBA teams franchises league roster all teams",
    ),
    (
        re.compile(r"\bpositions?\b|\broles?\b", re.IGNORECASE),
        "NBA basketball player positions point guard shooting guard small forward power forward center",
    ),
    (
        re.compile(r"\bscor(?:e|ing)\b|\bpoints?\b", re.IGNORECASE),
        "NBA basketball scoring points free throw field goal three point",
    ),
    (
        re.compile(r"\bfouls?\b|\bviolations?\b|\bpenalt(?:y|ies)\b", re.IGNORECASE),
        "NBA basketball rules fouls violations penalties",
    ),
)


def expand_query(question: str) -> str:
    """Add deterministic NBA context without asking an LLM or using outside facts."""
    normalized = " ".join(question.split())
    additions = [expansion for pattern, expansion in EXPANSIONS if pattern.search(normalized)]
    prefix = "NBA basketball"
    return " ".join(dict.fromkeys((prefix, normalized, *additions)))


class HybridRetriever:
    """Fuse semantic and FTS candidates, then rerank against the original question."""

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
        normalized = re.sub(r"\s+", " ", item["text"]).strip().lower()[:500]
        return hashlib.sha256(normalized.encode()).hexdigest()

    @staticmethod
    def _evidence_window(text: str, expanded_query: str, size: int = 320) -> str:
        """Select the passage window with the most query-term evidence."""
        ignored = {
            "a", "an", "and", "are", "basketball", "how", "in", "is", "league",
            "many", "nba", "of", "structure", "the", "there", "to", "what",
        }
        terms = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9]+", expanded_query)
            if len(token) > 2 and token.lower() not in ignored
        }
        words = text.split()
        if len(words) <= size:
            return text
        best_start = 0
        best_score = -1
        step = max(30, size // 3)
        for start in range(0, len(words), step):
            window = " ".join(words[start : start + size]).lower()
            score = sum(1 for term in terms if term in window)
            if score > best_score:
                best_score = score
                best_start = start
            if start + size >= len(words):
                break
        return " ".join(words[best_start : best_start + size])

    def _rrf(
        self,
        semantic: list[dict[str, Any]],
        keyword: list[dict[str, Any]],
        k: int = 60,
    ) -> list[dict[str, Any]]:
        fused: dict[str, dict[str, Any]] = {}
        seen_content: set[str] = set()
        for source_name, results in (("semantic", semantic), ("keyword", keyword)):
            for rank, item in enumerate(results, 1):
                chunk_id = item["chunk_id"]
                if chunk_id not in fused:
                    fused[chunk_id] = {**item, "rrf_score": 0.0, "retrieval_sources": []}
                fused[chunk_id]["rrf_score"] += 1.0 / (k + rank)
                fused[chunk_id]["retrieval_sources"].append(source_name)
                fused[chunk_id][f"{source_name}_score"] = item["score"]
        ordered = sorted(
            fused.values(),
            key=lambda item: (
                item["rrf_score"],
                len(item["retrieval_sources"]),
                max(item.get("semantic_score", 0), item.get("keyword_score", 0)),
            ),
            reverse=True,
        )
        unique: list[dict[str, Any]] = []
        for item in ordered:
            content_key = self._content_key(item)
            if content_key in seen_content:
                continue
            seen_content.add(content_key)
            unique.append(item)
            if len(unique) >= self.config.fusion_top_k:
                break
        return unique

    def search(self, question: str) -> list[dict[str, Any]]:
        expanded = expand_query(question)
        lexical_terms = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9]+", expanded)
            if len(token) > 3 and token.lower() not in {"basketball", "league", "structure"}
        }
        semantic = self.vector_store.search(expanded, self.config.semantic_top_k)
        keyword = self.database.search(expanded, self.config.keyword_top_k)
        fused = self._rrf(semantic, keyword)
        if not fused:
            return []
        for item in fused:
            item["evidence_text"] = self._evidence_window(item["text"], expanded)
            lowered = item["evidence_text"].lower()
            item["lexical_coverage"] = sum(term in lowered for term in lexical_terms)
            item["matched_terms"] = sorted(term for term in lexical_terms if term in lowered)
        if self.config.enable_reranker:
            logits = self._cross_encoder().predict(
                [(question, item["evidence_text"]) for item in fused],
                show_progress_bar=False,
            )
            for item, logit in zip(fused, logits):
                item["rerank_score"] = float(logit)
                item["score"] = max(
                    item.get("semantic_score", 0.0),
                    item.get("keyword_score", 0.0),
                )
            fused.sort(
                key=lambda item: (
                    item["lexical_coverage"],
                    len(item["retrieval_sources"]),
                    item["rrf_score"],
                    item["rerank_score"],
                ),
                reverse=True,
            )
        else:
            for item in fused:
                item["score"] = max(
                    item.get("semantic_score", 0.0),
                    item.get("keyword_score", 0.0),
                )
        max_coverage = max(item.get("lexical_coverage", 0) for item in fused)
        focused = [
            item
            for item in fused
            if item.get("lexical_coverage", 0) >= max(1, max_coverage - 1)
        ]
        selected = focused[: self.config.retrieval_top_k] or fused[: self.config.retrieval_top_k]
        selected_ids = {item["chunk_id"] for item in selected}
        expanded_selection = list(selected)
        for item in selected:
            for neighbor in self.database.adjacent_chunks(item["chunk_id"]):
                if neighbor["chunk_id"] in selected_ids:
                    continue
                neighbor["score"] = item["score"]
                neighbor["retrieval_sources"] = ["adjacent"]
                neighbor["matched_terms"] = sorted(
                    term for term in lexical_terms if term in neighbor["text"].lower()
                )
                expanded_selection.append(neighbor)
                selected_ids.add(neighbor["chunk_id"])
                if len(expanded_selection) >= self.config.retrieval_top_k:
                    break
            if len(expanded_selection) >= self.config.retrieval_top_k:
                break
        for item in expanded_selection:
            if "evidence_text" in item:
                item["text"] = item.pop("evidence_text")
        return expanded_selection
