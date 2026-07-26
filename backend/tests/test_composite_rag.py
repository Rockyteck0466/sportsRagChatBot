"""Regression contract for composite/jumbled RAG requests.

Assumed application contract:

* ``QUERY_PLAN_SCHEMA`` and ``RagService._validated_plan`` retain atomic
  ``subquestions`` with ``task_id``, ``question``, ``facet``, ``team_name``,
  and ``requires_complete_section``, plus ``team_names`` and 1..30 groups.
* Composite orchestration may use any internal helper, but every team/facet is
  submitted as an isolated operation through the retriever's public search
  boundary. Prepared questions are routing hints and source chunks are evidence.
* The final generation prompt includes a per-team/facet coverage ledger and
  generates a supported partial answer when another facet has no source evidence.
"""

import asyncio
import re
from typing import Any

from app.config import Settings
from app.rag import QUERY_PLAN_SCHEMA, RagService
from app.retrieval import HybridRetriever, _contains_named_captain_evidence


def _chunk(
    team: str,
    suffix: str,
    section: str,
    text: str,
    *,
    score: float = 0.9,
) -> dict[str, Any]:
    slug = re.sub(r"[^a-z0-9]+", "-", team.lower()).strip("-")
    return {
        "chunk_id": f"nba:web:{slug}:{suffix}",
        "page_url": f"https://www.nba.com/team/{slug}",
        "title": f"{team} team profile and roster",
        "section": section,
        "retrieved_at": "2026-07-25T00:00:00Z",
        "text": text,
        "score": score,
        "retrieval_sources": ["semantic_0", "keyword_original"],
        "matched_terms": ["team"],
    }


def _atomic_plan(team_names: list[str]) -> dict[str, Any]:
    subquestions: list[dict[str, Any]] = []
    for index, team_name in enumerate(team_names, 1):
        subquestions.extend(
            [
                {
                    "task_id": f"team-{index}-roster",
                    "question": f"What is the complete {team_name} roster?",
                    "facet": "roster",
                    "team_name": team_name,
                    "requires_complete_section": True,
                },
                {
                    "task_id": f"team-{index}-captain",
                    "question": f"Who is the named captain of {team_name}?",
                    "facet": "captain",
                    "team_name": team_name,
                    "requires_complete_section": False,
                },
            ]
        )
    return {
        "intent": "List the requested teams with their players and named captains.",
        "keywords": ["NBA teams", "roster", "players", "captain"],
        "search_queries": ["NBA teams directory"],
        "requires_multiple_sources": True,
        "requires_complete_sections": True,
        "requested_groups": len(team_names),
        "team_names": team_names,
        "subquestions": subquestions,
    }


def _generic_team_plan(requested_groups: int) -> dict[str, Any]:
    """Model output for an unspecified set of teams; the server must fan it out."""
    return {
        "intent": "List selected NBA teams with players and named captains.",
        "keywords": ["NBA teams", "roster", "players", "captain"],
        "search_queries": ["NBA teams directory"],
        "requires_multiple_sources": True,
        "requires_complete_sections": True,
        "requested_groups": requested_groups,
        "team_names": [],
        "subquestions": [
            {
                "task_id": "each-team-roster",
                "question": "What is the complete roster for {team_name}?",
                "facet": "roster",
                "team_name": "",
                "requires_complete_section": True,
            },
            {
                "task_id": "each-team-captain",
                "question": "Who is the named captain of {team_name}?",
                "facet": "captain",
                "team_name": "",
                "requires_complete_section": False,
            },
        ],
    }


def test_list_of_ten_teams_is_detected_and_not_capped_at_four() -> None:
    assert RagService._explicit_group_count(
        "Give me a list of 10 NBA teams, their players, and captain names."
    ) == 10
    assert RagService._explicit_group_count(
        "List ten clubs with their full squads and leaders."
    ) == 10


def test_group_count_is_safely_bounded_to_all_thirty_nba_teams() -> None:
    assert RagService._explicit_group_count("List 30 NBA team rosters.") == 30
    assert RagService._explicit_group_count("List 200 NBA team rosters.") == 30


def test_query_plan_schema_supports_atomic_subquestions_and_team_names() -> None:
    properties = QUERY_PLAN_SCHEMA["properties"]
    assert "team_names" in properties
    assert "subquestions" in properties

    subquestion_schema = properties["subquestions"]["items"]
    assert set(subquestion_schema["required"]) == {
        "task_id",
        "question",
        "facet",
        "team_name",
        "for_each_team",
        "requires_complete_section",
        "search_queries",
        "keywords",
        "operation",
        "metric",
        "group_by",
        "time_scope",
        "competition_scope",
        "requires_complete_population",
    }
    assert subquestion_schema["additionalProperties"] is False


def test_validated_plan_retains_ten_groups_and_atomic_tasks() -> None:
    team_names = [f"Team {index}" for index in range(1, 11)]
    raw_plan = _atomic_plan(team_names)

    plan = RagService._validated_plan(
        raw_plan,
        "List 10 NBA teams with each complete roster and named captain.",
    )

    assert plan["requested_groups"] == 10
    assert plan["team_names"] == team_names
    assert len(plan["subquestions"]) == 20
    assert {
        (task["team_name"], task["facet"])
        for task in plan["subquestions"]
    } == {
        (team_name, facet)
        for team_name in team_names
        for facet in ("roster", "captain")
    }
    assert all(task["question"] for task in plan["subquestions"])


def test_duplicate_planner_task_ids_are_made_unique() -> None:
    raw_plan = _atomic_plan(["Team One"])
    raw_plan["subquestions"][1]["task_id"] = raw_plan["subquestions"][0]["task_id"]

    plan = RagService._validated_plan(raw_plan, "Team One roster and captain")

    task_ids = [task["task_id"] for task in plan["subquestions"]]
    assert len(task_ids) == len(set(task_ids))


def test_unresolved_explicit_team_is_not_replaced_from_catalog() -> None:
    class TeamDatabase:
        def team_pages(self, limit: int | None = 30) -> list[dict[str, Any]]:
            records = [
                {
                    "team_name": "Atlanta Hawks",
                    "team_id": "1",
                    "page_url": "https://www.nba.com/team/1/hawks",
                },
                {
                    "team_name": "Boston Celtics",
                    "team_id": "2",
                    "page_url": "https://www.nba.com/team/2/celtics",
                },
            ]
            return records if limit is None else records[:limit]

        def find_team_pages(
            self,
            query: str,
            limit: int | None = 4,
        ) -> list[dict[str, Any]]:
            matches = []
            if "boston" in query.lower() or "celtics" in query.lower():
                matches.append({
                    "team_name": "Boston Celtics",
                    "team_id": "2",
                    "page_url": "https://www.nba.com/team/2/celtics",
                    "match_score": 500.0,
                })
            return matches if limit is None else matches[:limit]

    class Retriever:
        database = TeamDatabase()

    service = RagService(
        Retriever(),  # type: ignore[arg-type]
        Settings(max_composite_groups=30),
    )
    plan = _generic_team_plan(2)
    plan["team_names"] = ["Seattle", "Boston Celtics"]
    validated = service._validated_plan(
        plan,
        "Show Seattle and Boston Celtics rosters and captains.",
    )

    selected = service._resolve_selected_teams(
        validated,
        "Show Seattle and Boston Celtics rosters and captains.",
    )
    tasks = service._expand_atomic_tasks(validated, selected)

    assert selected == ["Boston Celtics"]
    assert validated["unresolved_team_names"] == ["Seattle"]
    assert "Atlanta Hawks" not in {
        task.get("team_name") for task in tasks
    }
    assert "Seattle" in {task.get("team_name") for task in tasks}


def test_complete_section_retrieval_can_fan_out_to_ten_team_rosters() -> None:
    anchors = [
        _chunk(
            f"Team {index}",
            "roster-1",
            "ROSTER",
            f"Team {index} Player {index}A Position Guard",
        )
        for index in range(1, 11)
    ]
    complete_sections = {
        anchor["page_url"]: [
            dict(anchor),
            _chunk(
                f"Team {index}",
                "roster-2",
                "ROSTER",
                f"Team {index} Player {index}B Position Forward",
            ),
        ]
        for index, anchor in enumerate(anchors, 1)
    }

    class FakeVector:
        def search_many(
            self,
            queries: list[str],
            limit: int,
        ) -> list[list[dict[str, Any]]]:
            return [[dict(item) for item in anchors] for _ in queries]

    class FakeDatabase:
        def title_aliases_for_query(self, query: str) -> list[str]:
            return []

        def search(
            self,
            query: str,
            limit: int,
            *,
            match_mode: str = "any",
        ) -> list[dict[str, Any]]:
            return [dict(item) for item in anchors]

        def section_chunks(
            self,
            page_url: str,
            section: str,
            *,
            limit: int = 8,
        ) -> list[dict[str, Any]]:
            return [
                dict(item)
                for item in complete_sections[page_url]
                if item["section"] == section
            ]

        def adjacent_chunks(self, chunk_id: str) -> list[dict[str, Any]]:
            return []

    retriever = HybridRetriever(
        FakeVector(),  # type: ignore[arg-type]
        FakeDatabase(),  # type: ignore[arg-type]
        Settings(
            retrieval_top_k=3,
            multi_source_top_k=40,
            fusion_top_k=40,
            semantic_top_k=20,
            keyword_top_k=20,
        ),
    )

    results = retriever.search(
        "List 10 NBA teams and all players on each team.",
        ["NBA team complete roster players"],
        result_limit=40,
        complete_sections=True,
        group_count=10,
    )

    assert len({item["page_url"] for item in results}) == 10
    assert len(results) == 20
    assert all("complete_section" in item["retrieval_sources"] for item in results)


def test_hybrid_search_combines_semantic_keyword_and_prepared_question_lanes() -> None:
    evidence = _chunk(
        "Example Team",
        "roster",
        "ROSTER",
        "Original NBA.com evidence lists Player Alpha as a guard.",
    )
    prepared_question = "Which athletes are included in the Example Team squad?"

    class FakeVector:
        def search_many(
            self,
            queries: list[str],
            limit: int,
        ) -> list[list[dict[str, Any]]]:
            return [[dict(evidence)] for _ in queries]

        def search_questions_many(
            self,
            queries: list[str],
            limit: int,
        ) -> list[list[dict[str, Any]]]:
            return [
                [
                    {
                        "question_id": "question:example-roster",
                        "chunk_id": evidence["chunk_id"],
                        "kind": "paraphrase",
                        "score": 0.94,
                    }
                ]
                for _ in queries
            ]

    class FakeDatabase:
        def title_aliases_for_query(self, query: str) -> list[str]:
            return []

        def search(
            self,
            query: str,
            limit: int,
            *,
            match_mode: str = "any",
        ) -> list[dict[str, Any]]:
            return [dict(evidence)]

        def search_chunk_questions(
            self,
            query: str,
            limit: int,
        ) -> list[dict[str, Any]]:
            return [
                {
                    **evidence,
                    "question_id": "question:example-roster",
                    "question": prepared_question,
                    "kind": "paraphrase",
                }
            ]

        def chunk_questions_by_ids(
            self,
            question_ids: list[str],
        ) -> dict[str, dict[str, Any]]:
            return {
                "question:example-roster": {
                    "question_id": "question:example-roster",
                    "chunk_id": evidence["chunk_id"],
                    "question": prepared_question,
                    "kind": "paraphrase",
                }
            }

        def chunks_by_ids(
            self,
            chunk_ids: list[str],
        ) -> list[dict[str, Any]]:
            return [dict(evidence)] if evidence["chunk_id"] in chunk_ids else []

        def adjacent_chunks(self, chunk_id: str) -> list[dict[str, Any]]:
            return []

    retriever = HybridRetriever(
        FakeVector(),  # type: ignore[arg-type]
        FakeDatabase(),  # type: ignore[arg-type]
        Settings(
            retrieval_top_k=3,
            semantic_top_k=3,
            keyword_top_k=3,
        ),
    )

    [result] = retriever.search(
        "Who is in the Example Team lineup?",
        result_limit=1,
    )

    sources = set(result["retrieval_sources"])
    assert any(source.startswith("semantic_") for source in sources)
    assert any(source.startswith("keyword_") for source in sources)
    assert any(source.startswith("question_semantic_") for source in sources)
    assert any(source.startswith("question_keyword_") for source in sources)
    assert result["matched_expected_questions"] == [prepared_question]
    assert "Original NBA.com evidence" in result["text"]
    assert prepared_question not in result["text"]


def test_generic_captain_rule_is_not_named_team_captain_evidence() -> None:
    generic_rule = {
        **_chunk(
            "NBA",
            "rule-3",
            "Rule No. 3 - Players, Substitutes and Coaches",
            "A team may designate a captain and co-captain for a game.",
        ),
        "page_url": "https://official.nba.com/rule-no-3-players-substitutes-and-coaches/",
        "title": "NBA Official Rule No. 3",
    }

    class FakeVector:
        def search_many(
            self,
            queries: list[str],
            limit: int,
        ) -> list[list[dict[str, Any]]]:
            return [[dict(generic_rule)] for _ in queries]

        def search_questions_many(
            self,
            queries: list[str],
            limit: int,
        ) -> list[list[dict[str, Any]]]:
            return [[] for _ in queries]

    class FakeDatabase:
        def title_aliases_for_query(self, query: str) -> list[str]:
            return []

        def search(
            self,
            query: str,
            limit: int,
            *,
            match_mode: str = "any",
        ) -> list[dict[str, Any]]:
            return [dict(generic_rule)]

        def search_chunk_questions(
            self,
            query: str,
            limit: int,
        ) -> list[dict[str, Any]]:
            return []

        def chunk_questions_by_ids(
            self,
            question_ids: list[str],
        ) -> dict[str, dict[str, Any]]:
            return {}

        def chunks_by_ids(
            self,
            chunk_ids: list[str],
        ) -> list[dict[str, Any]]:
            return []

        def find_team_pages(
            self,
            team_query: str,
            limit: int = 4,
        ) -> list[dict[str, Any]]:
            return [
                {
                    "team_name": "Example Team",
                    "team_id": "1",
                    "page_url": "https://www.nba.com/team/1/example-team",
                    "title": "Example Team team profile and roster",
                    "slug": "example-team",
                    "has_roster": True,
                    "match_score": 1.0,
                }
            ]

        def team_section_chunks(
            self,
            team_query: str,
            section_query: str,
            *,
            page_limit: int = 1,
            chunk_limit: int | None = None,
        ) -> list[dict[str, Any]]:
            return []

        def adjacent_chunks(self, chunk_id: str) -> list[dict[str, Any]]:
            return []

    retriever = HybridRetriever(
        FakeVector(),  # type: ignore[arg-type]
        FakeDatabase(),  # type: ignore[arg-type]
        Settings(retrieval_top_k=3, semantic_top_k=3, keyword_top_k=3),
    )

    result = retriever.search_atomic(
        "Who is the named captain of Example Team?",
        facet="captain",
        team_name="Example Team",
    )

    assert result["complete"] is False
    assert result["evidence"] == []


def test_named_captain_relation_is_required() -> None:
    assert not _contains_named_captain_evidence(
        "A team may designate a captain and co-captain for a game."
    )
    assert _contains_named_captain_evidence(
        "Boston named Example Player as the team captain."
    )


def test_composite_answer_fans_out_atomic_tasks_and_keeps_partial_rosters() -> None:
    team_names = [f"Team {index}" for index in range(1, 11)]
    directory = {
        **_chunk(
            "NBA",
            "directory",
            "TEAMS",
            "The indexed teams directory contains ten team profile links.",
        ),
        "page_url": "https://www.nba.com/teams",
        "title": "NBA Teams",
    }
    roster_chunks = {
        team_name: {
            **_chunk(
                team_name,
                "roster",
                "ROSTER",
                f"{team_name} Player A and {team_name} Player B are listed in the roster.",
            ),
            "matched_expected_questions": [
                f"Which players appear on the {team_name} roster?"
            ],
        }
        for team_name in team_names
    }

    class TeamCatalogDatabase:
        def team_pages(self, limit: int | None = 30) -> list[dict[str, Any]]:
            records = [
                {
                    "team_name": team_name,
                    "team_id": str(index),
                    "page_url": roster_chunks[team_name]["page_url"],
                    "title": roster_chunks[team_name]["title"],
                    "slug": f"team-{index}",
                    "has_roster": True,
                }
                for index, team_name in enumerate(team_names, 1)
            ]
            return records if limit is None else records[:limit]

    class RecordingRetriever:
        def __init__(self) -> None:
            self.calls: list[tuple[str, list[str], dict[str, Any]]] = []
            self.atomic_calls: list[dict[str, Any]] = []
            self.database = TeamCatalogDatabase()

        def search(
            self,
            question: str,
            extra_queries: list[str] | None = None,
            **kwargs: Any,
        ) -> list[dict[str, Any]]:
            extras = list(extra_queries or [])
            self.calls.append((question, extras, dict(kwargs)))
            if len(self.calls) == 1:
                return [dict(directory)]

            operation = " ".join(extras) if extras else question
            operation_lower = operation.lower()
            if "captain" in operation_lower:
                return []
            for team_name in team_names:
                if (
                    team_name.lower() in operation_lower
                    and re.search(r"\b(?:roster|players?|squad)\b", operation_lower)
                ):
                    return [dict(roster_chunks[team_name])]
            return [dict(directory)]

        def search_atomic(
            self,
            question: str,
            *,
            facet: str = "general",
            team_name: str = "",
            result_limit: int | None = None,
            requires_complete_section: bool = False,
        ) -> dict[str, Any]:
            self.atomic_calls.append(
                {
                    "question": question,
                    "facet": facet,
                    "team_name": team_name,
                    "result_limit": result_limit,
                    "requires_complete_section": requires_complete_section,
                }
            )
            evidence = (
                [dict(roster_chunks[team_name])]
                if facet == "roster" and team_name in roster_chunks
                else []
            )
            matched_questions = (
                [
                    {
                        "question_id": f"question:{team_name}:roster",
                        "chunk_id": roster_chunks[team_name]["chunk_id"],
                        "question": (
                            f"Which players appear on the {team_name} roster?"
                        ),
                        "kind": "paraphrase",
                        "score": 0.9,
                        "match_sources": [
                            "prepared_semantic",
                            "prepared_keyword",
                        ],
                    }
                ]
                if team_name in roster_chunks
                else []
            )
            return {
                "question": question,
                "facet": facet,
                "team_name": team_name,
                "matched_questions": matched_questions,
                "evidence": evidence,
                "complete": bool(evidence),
            }

    class RecordingService(RagService):
        prompts: tuple[str, str] | None = None

        async def _plan_openai(
            self,
            question: str,
            retrieved: list[dict],
            team_catalog: list[dict[str, Any]] | None = None,
        ) -> dict:
            return _generic_team_plan(10)

        async def _generate_openai(
            self,
            system_prompt: str,
            user_prompt: str,
            *,
            max_output_tokens: int | None = None,
        ) -> dict:
            self.prompts = (system_prompt, user_prompt)
            citation_ids = re.findall(r"^\[(C\d+)\]$", user_prompt, re.MULTILINE)
            citation_id = citation_ids[0]
            return {
                "answer": (
                    "The indexed roster details are available, while no named "
                    f"captain was found in the retrieved evidence [{citation_id}]."
                ),
                "citation_ids": [citation_id],
                "insufficient": False,
            }

    retriever = RecordingRetriever()
    service = RecordingService(
        retriever,  # type: ignore[arg-type]
        Settings(
            enable_query_planner=True,
            query_plan_min_score=0.99,
            min_retrieval_score=0.25,
            multi_source_top_k=40,
        ),
    )

    response = asyncio.run(
        service.answer(
            "Give me a list of 10 NBA teams with each team's players and captain."
        )
    )

    assert len(retriever.atomic_calls) == 21
    assert any(
        call["facet"] == "team_list" and call["team_name"] == ""
        for call in retriever.atomic_calls
    )
    assert {
        (call["team_name"], call["facet"])
        for call in retriever.atomic_calls
        if call["facet"] in {"roster", "captain"}
    } == {
        (team_name, facet)
        for team_name in team_names
        for facet in ("roster", "captain")
    }
    assert all(
        call["requires_complete_section"]
        for call in retriever.atomic_calls
        if call["facet"] == "roster"
    )
    assert all(
        "{team_name}" not in call["question"]
        for call in retriever.atomic_calls
    )

    assert response.refused is False
    assert service.prompts is not None
    system_prompt, user_prompt = service.prompts
    assert "coverage" in user_prompt.lower()
    assert all(team_name in user_prompt for team_name in team_names)
    assert re.search(r"Team 1.*roster.*(?:found|available)", user_prompt, re.IGNORECASE)
    assert re.search(r"Team 1.*captain.*(?:missing|not found)", user_prompt, re.IGNORECASE)
    assert "Matched expected-question retrieval hints (NOT EVIDENCE)" in user_prompt
    assert re.search(
        r"Use only each (?:supplied )?block's Content field as factual evidence",
        system_prompt,
    )
