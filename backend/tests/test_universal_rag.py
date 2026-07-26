import asyncio
from typing import Any

from app.config import Settings
from app.rag import RagService
from app.retrieval import HybridRetriever


def _source_chunk() -> dict[str, Any]:
    return {
        "chunk_id": "nba:web:about:divisions",
        "page_url": "https://www.nba.com/news/about",
        "title": "About the NBA",
        "section": "League organization",
        "retrieved_at": "2026-07-25T00:00:00Z",
        "text": "The NBA has two conferences and six divisions.",
        "score": 0.94,
        "retrieval_sources": ["semantic_0", "keyword_original"],
        "matched_terms": ["divisions"],
    }


def _universal_plan(question: str) -> dict[str, Any]:
    return {
        "intent": "Determine the number of NBA divisions.",
        "rewritten_question": "How many divisions does the NBA have?",
        "keywords": ["NBA", "divisions", "league organization"],
        "search_queries": [
            "NBA total number of divisions",
            "NBA league divisional structure",
        ],
        "subquestions": [
            {
                "task_id": "division-count",
                "question": "How many divisions does the NBA have?",
                "facet": "general",
                "team_name": "",
                "for_each_team": False,
                "requires_complete_section": False,
                "search_queries": [
                    "NBA total number of divisions",
                    "NBA league divisional structure",
                ],
                "keywords": ["NBA", "divisions"],
                "operation": "count",
                "metric": "divisions",
                "group_by": "",
                "time_scope": "",
                "competition_scope": "",
                "requires_complete_population": False,
            }
        ],
        "team_names": [],
        "ambiguities": [],
        "requires_clarification": False,
        "clarification_question": "",
        "requires_multiple_sources": False,
        "requires_complete_sections": False,
        "requested_groups": 1,
    }


def test_plan_validation_keeps_original_and_rejects_constraint_drift() -> None:
    question = "Which team did not lose the most games in 2025?"
    raw = {
        **_universal_plan(question),
        "intent": "Find the requested team.",
        "rewritten_question": "Which team lost the most games?",
        "search_queries": [
            "team with most losses",
            "team that did not lose most games in 2025",
        ],
        "subquestions": [
            {
                **_universal_plan(question)["subquestions"][0],
                "question": "Which team lost the most games?",
                "search_queries": [
                    "team with most losses",
                    "team that did not lose most games in 2025",
                ],
                "operation": "argmax",
                "metric": "losses",
                "group_by": "team",
            }
        ],
    }

    plan = RagService._validated_plan(raw, question)

    assert plan["original_question"] == question
    assert plan["rewritten_question"] == question
    assert plan["search_queries"] == [
        "team that did not lose most games in 2025"
    ]
    [task] = plan["subquestions"]
    assert task["question"] == question
    assert task["search_queries"] == [
        "team that did not lose most games in 2025"
    ]
    assert task["requires_complete_population"] is True
    assert task["requires_complete_section"] is True


def test_constraint_guard_preserves_numeric_words_and_quantifier_meaning() -> None:
    equivalent = [
        (
            "List at least ten NBA teams.",
            "Show a minimum of 10 NBA clubs.",
        ),
        (
            "Show every NBA team.",
            "List all NBA franchises.",
        ),
        (
            "Compare both teams.",
            "Compare the two teams.",
        ),
        (
            "Use the 2025-26 NBA season.",
            "Use the 2025/2026 NBA season.",
        ),
    ]
    for anchor, candidate in equivalent:
        assert RagService._preserves_query_constraints(anchor, candidate)

    drifted = [
        (
            "List at least ten NBA teams.",
            "List at most ten NBA teams.",
        ),
        (
            "List ten NBA teams.",
            "List nine NBA teams.",
        ),
        (
            "Show every NBA team.",
            "Show some NBA teams.",
        ),
        (
            "Use the 2025-26 NBA season.",
            "Use the 2024-25 NBA season.",
        ),
    ]
    for anchor, candidate in drifted:
        assert not RagService._preserves_query_constraints(anchor, candidate)


def test_constraint_guard_rejects_negation_and_extrema_inversions() -> None:
    assert not RagService._preserves_query_constraints(
        "Which team won the most games?",
        "Which team did not win the most games?",
    )
    assert not RagService._preserves_query_constraints(
        "Which team did not win the most games?",
        "Which team won the most games?",
    )
    assert not RagService._preserves_query_constraints(
        "Which team had the most wins?",
        "Which team had the fewest wins?",
    )
    assert not RagService._preserves_query_constraints(
        "Which team had the least losses?",
        "Which team had the highest losses?",
    )
    assert RagService._preserves_query_constraints(
        "Which team had the most wins?",
        "Which team recorded the highest wins?",
    )
    assert RagService._preserves_query_constraints(
        "Which team had the least losses?",
        "Which team recorded the fewest losses?",
    )


def test_constraint_guard_preserves_regular_season_and_playoff_scope() -> None:
    assert RagService._preserves_query_constraints(
        "Who led the 2025-26 playoffs in points?",
        "Who was the 2025-26 postseason leader in points?",
    )
    assert RagService._preserves_query_constraints(
        "Who led the 2025-26 regular season in points?",
        "Who was the 2025/2026 regular-season leader in points?",
    )
    assert not RagService._preserves_query_constraints(
        "Who led the 2025-26 regular season in points?",
        "Who led the 2025-26 playoffs in points?",
    )
    assert not RagService._preserves_query_constraints(
        "Who led the 2025-26 regular season in points?",
        "Who led the 2025-26 season in points?",
    )
    assert not RagService._preserves_query_constraints(
        "Who led the NBA in points?",
        "Who led the NBA playoffs in points?",
    )


def test_constraint_guard_rejects_planner_added_team_entities() -> None:
    assert not RagService._preserves_query_constraints(
        "Which NBA team won the most games?",
        "Did the Lakers win the most games?",
    )
    assert not RagService._preserves_query_constraints(
        "Which NBA team won the most games?",
        "Did LAL win the most games?",
    )
    assert not RagService._preserves_query_constraints(
        "Did the Lakers win the most games?",
        "Did the Celtics win the most games?",
    )
    assert RagService._preserves_query_constraints(
        "Did the Los Angeles Lakers win the most games?",
        "Did LAL record the highest number of wins?",
    )
    assert RagService._preserves_query_constraints(
        "Which Boston team had the most wins?",
        "Did the Celtics record the highest wins?",
    )


def test_multitask_decomposition_may_isolate_original_clauses() -> None:
    question = (
        "During the 2025-26 regular season, list the Lakers roster "
        "and explain the playoff qualification rule."
    )
    raw = _universal_plan(question)
    raw.update(
        {
            "intent": "Retrieve the requested roster and qualification rule.",
            "rewritten_question": (
                "For the 2025/2026 regular season, show the Lakers players "
                "and explain the postseason qualification rule."
            ),
            "search_queries": [
                (
                    "2025-26 regular season Lakers roster and playoff "
                    "qualification rule"
                )
            ],
            "requires_multiple_sources": True,
            "subquestions": [
                {
                    **raw["subquestions"][0],
                    "task_id": "lakers-roster",
                    "question": (
                        "Which players were on the Lakers roster in the "
                        "2025-26 regular season?"
                    ),
                    "facet": "roster",
                    "team_name": "Los Angeles Lakers",
                    "search_queries": [
                        "2025/2026 regular-season Lakers player roster"
                    ],
                    "operation": "list",
                },
                {
                    **raw["subquestions"][0],
                    "task_id": "playoff-rule",
                    "question": (
                        "How did playoff qualification work in 2025-26?"
                    ),
                    "facet": "rules",
                    "team_name": "",
                    "search_queries": [
                        "2025/2026 postseason qualification rules"
                    ],
                    "operation": "explain",
                },
            ],
        }
    )

    plan = RagService._validated_plan(raw, question)

    assert [task["task_id"] for task in plan["subquestions"]] == [
        "lakers-roster",
        "playoff-rule",
    ]
    assert plan["subquestions"][0]["question"].startswith(
        "Which players were on the Lakers roster"
    )
    assert plan["subquestions"][1]["question"] == (
        "How did playoff qualification work in 2025-26?"
    )
    assert plan["subquestions"][0]["search_queries"] == [
        "2025/2026 regular-season Lakers player roster"
    ]
    assert plan["subquestions"][1]["search_queries"] == [
        "2025/2026 postseason qualification rules"
    ]


def test_structured_scope_is_injected_into_atomic_retrieval_queries() -> None:
    question = (
        "Compare Lakers and Celtics wins in the 2025-26 regular season."
    )
    raw = _universal_plan(question)
    raw["subquestions"] = [
        {
            **raw["subquestions"][0],
            "task_id": team.lower(),
            "question": f"How many wins did the {team} have?",
            "team_name": team,
            "facet": "standings",
            "search_queries": [f"{team} win total"],
            "operation": "lookup",
            "metric": "wins",
            "time_scope": "2025-26",
            "competition_scope": "regular season",
        }
        for team in ("Lakers", "Celtics")
    ]

    plan = RagService._validated_plan(raw, question)

    for task in plan["subquestions"]:
        assert task["time_scope"] == "2025-26"
        assert task["competition_scope"] == "regular season"
        assert "2025-26 regular season" in task["search_queries"][0]


def test_every_simple_question_uses_planner_and_atomic_retrieval() -> None:
    chunk = _source_chunk()

    class Retriever:
        def __init__(self) -> None:
            self.atomic_tasks: list[dict[str, Any]] = []
            self.legacy_calls = 0

        def search(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            self.legacy_calls += 1
            return []

        def search_atomic_many(
            self,
            tasks: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            self.atomic_tasks = tasks
            return [
                {
                    **task,
                    "query_variants": [
                        task["question"],
                        *task.get("search_queries", []),
                    ],
                    "matched_questions": [
                        {
                            "question": "What is the NBA division count?",
                            "match_sources": [
                                "prepared_semantic",
                                "prepared_keyword",
                            ],
                        }
                    ],
                    "retrieval_channels": [
                        "semantic_0",
                        "keyword_original",
                        "question_semantic_0",
                        "question_keyword_0",
                    ],
                    "evidence": [dict(chunk)],
                    "complete": True,
                    "status": "found",
                }
                for task in tasks
            ]

    class Service(RagService):
        planner_calls = 0
        prompts: tuple[str, str] | None = None

        async def _plan_openai(
            self,
            question: str,
            retrieved: list[dict],
            team_catalog: list[dict[str, Any]] | None = None,
        ) -> dict:
            self.planner_calls += 1
            assert retrieved == []
            return _universal_plan(question)

        async def _generate_openai(
            self,
            system_prompt: str,
            user_prompt: str,
            *,
            max_output_tokens: int | None = None,
        ) -> dict:
            self.prompts = (system_prompt, user_prompt)
            return {
                "answer": "The NBA has six divisions [C1].",
                "citation_ids": ["C1"],
                "insufficient": False,
            }

    retriever = Retriever()
    service = Service(
        retriever,  # type: ignore[arg-type]
        Settings(enable_query_planner=True),
    )

    response = asyncio.run(service.answer("how many divisions are there?"))
    repeated = asyncio.run(service.answer("how many divisions are there?"))

    assert response.refused is False
    assert repeated.refused is False
    assert service.planner_calls == 1
    assert retriever.legacy_calls == 0
    assert len(retriever.atomic_tasks) == 1
    task = retriever.atomic_tasks[0]
    assert task["original_question"] == "how many divisions are there?"
    assert task["search_queries"] == [
        "NBA total number of divisions",
        "NBA league divisional structure",
    ]
    assert service.prompts is not None
    system_prompt, user_prompt = service.prompts
    assert "prepared-question matches are navigation aids only" in system_prompt
    assert "Original question:\nhow many divisions are there?" in user_prompt
    assert "Faithful rewritten intent" in user_prompt
    assert "What is the NBA division count?" in user_prompt
    assert "Content: The NBA has two conferences and six divisions." in user_prompt


def test_material_ambiguity_returns_clarification_without_retrieval() -> None:
    class Retriever:
        def search_atomic_many(self, tasks: list[dict[str, Any]]) -> list[dict]:
            raise AssertionError("retrieval must wait for material clarification")

    class Service(RagService):
        async def _plan_openai(
            self,
            question: str,
            retrieved: list[dict],
            team_catalog: list[dict[str, Any]] | None = None,
        ) -> dict:
            plan = _universal_plan(question)
            plan.update(
                {
                    "ambiguities": [
                        "2025 may mean calendar year 2025 or an NBA season."
                    ],
                    "requires_clarification": True,
                    "clarification_question": (
                        "Do you mean calendar year 2025, the 2024-25 season, "
                        "or the 2025-26 season?"
                    ),
                }
            )
            return plan

    service = Service(
        Retriever(),  # type: ignore[arg-type]
        Settings(enable_query_planner=True),
    )
    response = asyncio.run(
        service.answer("Which team won the most games in 2025?")
    )

    assert response.refused is False
    assert response.citations == []
    assert "calendar year 2025" in response.answer


def test_planner_cannot_force_clarification_for_ordinary_question() -> None:
    question = "List two NBA teams and the players on each team."
    raw = _universal_plan(question)
    raw.update(
        {
            "ambiguities": ["The user did not name the two teams."],
            "requires_clarification": True,
            "clarification_question": "Which two teams do you want?",
            "requested_groups": 2,
        }
    )

    plan = RagService._validated_plan(raw, question)

    assert plan["requires_clarification"] is False
    assert plan["clarification_question"] == ""


def test_material_year_scope_is_server_enforced_without_planner_flag() -> None:
    question = "Which team played the most games in 2025?"
    raw = _universal_plan(question)
    raw.update(
        {
            "requires_clarification": False,
            "clarification_question": "",
        }
    )

    plan = RagService._validated_plan(raw, question)

    assert plan["requires_clarification"] is True
    assert "calendar year" in plan["clarification_question"]


def test_explicit_season_or_calendar_year_does_not_ask_back() -> None:
    for question in (
        "Which team won the most games in the 2024-25 season?",
        "Which team won the most games during calendar year 2025?",
        "Which team played on January 5, 2025?",
    ):
        raw = _universal_plan(question)
        raw.update(
            {
                "requires_clarification": True,
                "clarification_question": "Please clarify the date.",
            }
        )

        plan = RagService._validated_plan(raw, question)

        assert plan["requires_clarification"] is False
        assert plan["clarification_question"] == ""


def test_every_ai_variant_hits_prepared_and_original_hybrid_lanes() -> None:
    chunk = _source_chunk()
    prepared_question = "What is the total number of NBA divisions?"

    class Vector:
        def __init__(self) -> None:
            self.prepared_queries: list[str] = []
            self.source_queries: list[str] = []

        def search_questions_many(
            self,
            queries: list[str],
            limit: int,
        ) -> list[list[dict[str, Any]]]:
            self.prepared_queries = list(queries)
            return [
                [
                    {
                        "question_id": "q:division-count",
                        "chunk_id": chunk["chunk_id"],
                        "score": 0.95,
                    }
                ]
                for _ in queries
            ]

        def search_many(
            self,
            queries: list[str],
            limit: int,
        ) -> list[list[dict[str, Any]]]:
            self.source_queries = list(queries)
            return [[dict(chunk)] for _ in queries]

    class Database:
        def __init__(self) -> None:
            self.source_keyword_queries: list[str] = []
            self.prepared_keyword_queries: list[str] = []

        def chunk_questions_by_ids(
            self,
            question_ids: list[str],
        ) -> dict[str, dict[str, Any]]:
            return {
                "q:division-count": {
                    "question_id": "q:division-count",
                    "chunk_id": chunk["chunk_id"],
                    "question": prepared_question,
                    "kind": "paraphrase",
                }
            }

        def search_chunk_questions(
            self,
            query: str,
            limit: int,
        ) -> list[dict[str, Any]]:
            self.prepared_keyword_queries.append(query)
            return [
                {
                    **chunk,
                    "question_id": "q:division-count",
                    "question": prepared_question,
                    "kind": "paraphrase",
                }
            ]

        def chunks_by_ids(
            self,
            chunk_ids: list[str],
        ) -> list[dict[str, Any]]:
            return [dict(chunk)] if chunk["chunk_id"] in chunk_ids else []

        def search(
            self,
            query: str,
            limit: int,
            *,
            match_mode: str = "any",
        ) -> list[dict[str, Any]]:
            self.source_keyword_queries.append(query)
            return [dict(chunk)]

    vector = Vector()
    database = Database()
    retriever = HybridRetriever(
        vector,  # type: ignore[arg-type]
        database,  # type: ignore[arg-type]
        Settings(
            semantic_top_k=5,
            keyword_top_k=5,
            composite_task_top_k=5,
            min_retrieval_score=0.1,
        ),
    )
    variants = [
        "NBA total number of divisions",
        "NBA league divisional structure",
    ]

    [bundle] = retriever.search_atomic_many(
        [
            {
                "task_id": "division-count",
                "question": "How many divisions does the NBA have?",
                "facet": "general",
                "team_name": "",
                "requires_complete_section": False,
                "search_queries": variants,
                "keywords": ["NBA", "divisions"],
            }
        ]
    )

    for query in variants:
        assert query in vector.prepared_queries
        assert query in vector.source_queries
        assert query in database.prepared_keyword_queries
        assert query in database.source_keyword_queries
    assert bundle["status"] == "found"
    assert bundle["evidence"][0]["text"] == chunk["text"]
    assert prepared_question not in bundle["evidence"][0]["text"]
    assert {
        "prepared_semantic",
        "prepared_keyword",
    }.issubset(set(bundle["matched_questions"][0]["match_sources"]))


def test_aggregate_coverage_is_marked_incomplete_without_complete_section() -> None:
    question = "Which team won the most games in 2025?"
    raw = _universal_plan(question)
    raw["subquestions"][0].update(
        {
            "question": question,
            "operation": "argmax",
            "metric": "wins",
            "group_by": "team",
            "time_scope": "2025",
            "requires_complete_population": True,
            "requires_complete_section": False,
        }
    )

    plan = RagService._validated_plan(raw, question)
    [task] = plan["subquestions"]

    assert task["operation"] == "argmax"
    assert task["requires_complete_population"] is True
    assert task["requires_complete_section"] is True


def test_explicit_source_count_does_not_require_population_enumeration() -> None:
    question = "How many divisions are there in the NBA?"
    raw = _universal_plan(question)
    raw["subquestions"][0].update(
        {
            "question": question,
            "operation": "count",
            "metric": "divisions",
            "requires_complete_population": True,
        }
    )

    plan = RagService._validated_plan(raw, question)
    [task] = plan["subquestions"]

    assert task["operation"] == "count"
    assert task["requires_complete_population"] is False


def test_identical_text_on_different_team_pages_keeps_both_sources() -> None:
    base = _source_chunk()
    first = {
        **base,
        "chunk_id": "team-one-roster",
        "page_url": "https://www.nba.com/team/1/one",
        "section": "ROSTER",
        "text": "Position Guard Height 6-4 Weight 205",
    }
    second = {
        **base,
        "chunk_id": "team-two-roster",
        "page_url": "https://www.nba.com/team/2/two",
        "section": "ROSTER",
        "text": first["text"],
    }
    retriever = HybridRetriever(
        vector_store=None,  # type: ignore[arg-type]
        database=None,  # type: ignore[arg-type]
        config=Settings(fusion_top_k=5),
    )

    fused = retriever._fuse(
        [("semantic_0", [first, second], 1.0)]
    )

    assert {item["chunk_id"] for item in fused} == {
        "team-one-roster",
        "team-two-roster",
    }


def test_weak_semantic_neighbor_is_not_treated_as_answer_evidence() -> None:
    weak = {**_source_chunk(), "score": 0.05}

    class Vector:
        def search_questions_many(self, queries, limit):
            return [[] for _ in queries]

        def search_many(self, queries, limit):
            return [[dict(weak)] for _ in queries]

    class Database:
        def chunk_questions_by_ids(self, question_ids):
            return {}

        def search_chunk_questions(self, query, limit):
            return []

        def chunks_by_ids(self, chunk_ids):
            return []

        def search(self, query, limit, *, match_mode="any"):
            return []

    retriever = HybridRetriever(
        Vector(),  # type: ignore[arg-type]
        Database(),  # type: ignore[arg-type]
        Settings(min_retrieval_score=0.25),
    )

    bundle = retriever.search_atomic(
        "What is the arena mascot's favorite food?",
    )

    assert bundle["status"] == "missing"
    assert bundle["evidence"] == []


def test_fallback_clarifies_ambiguous_year_for_result_aggregate() -> None:
    service = RagService(
        retriever=object(),  # type: ignore[arg-type]
        config=Settings(enable_query_planner=False),
    )

    plan = service._fallback_plan(
        "Which team won the most games in 2025?"
    )

    assert plan["requires_clarification"] is True
    assert "calendar year" in plan["clarification_question"]
    assert plan["subquestions"][0]["question"] == (
        "Which team won the most games in 2025?"
    )


def test_expanded_task_ids_remain_unique_after_team_fanout() -> None:
    service = RagService(
        retriever=object(),  # type: ignore[arg-type]
        config=Settings(),
    )
    plan = {
        "requested_groups": 2,
        "unresolved_team_names": [],
        "subquestions": [
            {
                "task_id": "roster",
                "question": "Which players are on the roster?",
                "facet": "roster",
                "team_name": "",
                "for_each_team": True,
                "requires_complete_section": True,
                "search_queries": [],
            },
            {
                "task_id": "roster-2",
                "question": "What other indexed fact is available?",
                "facet": "general",
                "team_name": "",
                "for_each_team": False,
                "requires_complete_section": False,
                "search_queries": [],
            },
        ],
    }

    tasks = service._expand_atomic_tasks(
        plan,
        ["Boston Celtics", "Los Angeles Lakers"],
    )
    task_ids = [task["task_id"] for task in tasks]

    assert len(task_ids) == len(set(task_ids))


def test_large_fanout_uses_bounded_round_robin_source_context() -> None:
    class Retriever:
        def search_atomic_many(
            self,
            tasks: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            bundles: list[dict[str, Any]] = []
            for task_index, task in enumerate(tasks):
                evidence = []
                for chunk_index in range(3):
                    evidence.append({
                        **_source_chunk(),
                        "chunk_id": f"task-{task_index}-chunk-{chunk_index}",
                        "title": f"Task {task_index} source {chunk_index}",
                        "text": (
                            f"Grounded fact for task {task_index}. "
                            + ("evidence " * 2_000)
                        ),
                    })
                bundles.append({
                    **task,
                    "query_variants": [task["question"]],
                    "matched_questions": [],
                    "retrieval_channels": ["semantic_0"],
                    "evidence": evidence,
                    "complete": True,
                    "status": "found",
                })
            return bundles

    class Service(RagService):
        captured_user_prompt = ""

        async def _generate_openai(
            self,
            system_prompt: str,
            user_prompt: str,
            *,
            max_output_tokens: int | None = None,
        ) -> dict:
            self.captured_user_prompt = user_prompt
            return {
                "answer": "A grounded partial result is available [C1].",
                "citation_ids": ["C1"],
                "insufficient": False,
            }

    raw_plan = _universal_plan("Summarize four indexed NBA facts.")
    raw_plan["subquestions"] = [
        {
            **raw_plan["subquestions"][0],
            "task_id": f"fact-{index}",
            "question": f"What is indexed fact {index}?",
            "operation": "lookup",
        }
        for index in range(4)
    ]
    plan = RagService._validated_plan(
        raw_plan,
        "Summarize four indexed NBA facts.",
    )
    service = Service(
        Retriever(),  # type: ignore[arg-type]
        Settings(
            enable_query_planner=False,
            openai_context_max_chars=8_000,
            openai_context_chunks_per_task=2,
        ),
    )

    response = asyncio.run(
        service._answer_composite(
            "Summarize four indexed NBA facts.",
            plan,
        )
    )

    assert response.refused is False
    source_context = service.captured_user_prompt.split(
        "NBA.com source context:\n",
        1,
    )[1].split(
        "\n\nProduce the requested structured answer",
        1,
    )[0]
    assert len(source_context) <= 8_000
    for task_index in range(4):
        assert f"Task {task_index} source 0" in source_context
