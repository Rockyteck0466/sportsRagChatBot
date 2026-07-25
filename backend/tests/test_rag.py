import asyncio
import re

from app.config import Settings
from app.rag import ANSWER_SCHEMA, QUERY_PLAN_SCHEMA, RagService, compact_evidence


def test_trailing_citation_label_can_be_normalized_inline() -> None:
    answer = "LeBron James chose Philadelphia. Citation: [C2]"
    normalized = re.sub(
        r"\.\s*(?:citation|source)s?:\s*((?:\[C\d+\][,;\s]*)+)(?=$|\n)",
        lambda match: f" {match.group(1).strip(' ,;')}.",
        answer,
        flags=re.IGNORECASE,
    )
    assert normalized == "LeBron James chose Philadelphia [C2]."


def test_compact_evidence_keeps_labels_and_removes_markdown_targets() -> None:
    evidence = "[Atlantic](https://www.nba.com/teams) ![NBA Logo](https://cdn.nba.com/logo.svg)"
    compact = compact_evidence(evidence)
    assert "Atlantic" in compact
    assert "https://" not in compact


def test_answer_schema_requires_all_structured_fields() -> None:
    assert set(ANSWER_SCHEMA["required"]) == {"answer", "citation_ids", "insufficient"}
    assert ANSWER_SCHEMA["additionalProperties"] is False


def test_query_plan_schema_cannot_return_an_answer_field() -> None:
    assert "answer" not in QUERY_PLAN_SCHEMA["properties"]
    assert QUERY_PLAN_SCHEMA["additionalProperties"] is False


def test_explicit_multi_group_count_is_detected() -> None:
    assert RagService._explicit_group_count(
        "NBA teams and their members for at least 2 teams"
    ) == 2
    assert RagService._explicit_group_count("List any three rosters") == 3


def test_plan_is_limited_to_queries_and_four_groups() -> None:
    plan = RagService._validated_plan(
        {
            "search_queries": [
                "team roster players",
                "team roster players",
                "NBA roster by team",
            ],
            "requires_multiple_sources": True,
            "requires_complete_sections": True,
            "requested_groups": 99,
        },
        "List teams and members",
    )
    assert plan["search_queries"] == ["team roster players", "NBA roster by team"]
    assert plan["requested_groups"] == 4
    assert plan["requires_complete_sections"] is True


def test_weak_or_complex_retrieval_uses_query_planner() -> None:
    service = RagService(
        retriever=None,  # type: ignore[arg-type]
        config=Settings(
            enable_query_planner=True,
            query_plan_min_score=0.48,
        ),
    )
    retrieved = [{
        "score": 0.7,
        "retrieval_sources": ["semantic_0", "keyword_original"],
    }]
    assert service._needs_query_plan("How many divisions are there?", retrieved) is False
    assert service._needs_query_plan(
        "List players for at least two teams", retrieved
    ) is True


def test_model_json_parser_rejects_empty_non_json() -> None:
    assert RagService._parse_generated('{"answer":"Six [C1].","citation_ids":["C1"],"insufficient":false}')[
        "citation_ids"
    ] == ["C1"]


def test_answer_prompt_treats_matched_question_as_non_evidence() -> None:
    class Retriever:
        def search(self, question: str, *args, **kwargs) -> list[dict]:
            return [{
                "chunk_id": "nba:web:test:0001",
                "page_url": "https://www.nba.com/news/about",
                "title": "About The NBA",
                "section": "Roster size",
                "retrieved_at": "2026-07-25T00:00:00Z",
                "text": "Each team may have up to 15 players on its roster.",
                "score": 0.8,
                "retrieval_sources": [
                    "semantic_0",
                    "keyword_original",
                    "question_semantic_0",
                ],
                "matched_terms": ["team"],
                "matched_expected_questions": [
                    "What is the maximum NBA roster size?"
                ],
            }]

    class RecordingService(RagService):
        prompts: tuple[str, str] | None = None

        async def _generate_openai(
            self,
            system_prompt: str,
            user_prompt: str,
        ) -> dict:
            self.prompts = (system_prompt, user_prompt)
            return {
                "answer": "A team may have up to 15 players. [C1]",
                "citation_ids": ["C1"],
                "insufficient": False,
            }

    service = RecordingService(
        Retriever(),  # type: ignore[arg-type]
        Settings(enable_query_planner=False, min_retrieval_score=0.25),
    )
    response = asyncio.run(service.answer("How large can a team be?"))

    assert response.refused is False
    assert service.prompts is not None
    system_prompt, user_prompt = service.prompts
    assert "Question: How large can a team be?" in user_prompt
    assert "Matched expected-question retrieval hints (NOT EVIDENCE)" in user_prompt
    assert "What is the maximum NBA roster size?" in user_prompt
    assert "Content: Each team may have up to 15 players on its roster." in user_prompt
    assert "Use only each block's Content field as factual evidence" in system_prompt
