from app.config import Settings
from app.retrieval import HybridRetriever, _focused_query, expand_query


def test_short_division_question_gets_neutral_structure_terms() -> None:
    expanded = expand_query("How many divisions are there?")
    assert "NBA basketball" in expanded
    assert "league organization conference division" in expanded
    assert "Atlantic" not in expanded
    assert "six" not in expanded.lower()


def test_team_synonym_expands_without_answer_facts() -> None:
    expanded = expand_query("List the franchises")
    assert "team franchise club roster" in expanded


def test_team_member_question_adds_roster_vocabulary() -> None:
    expanded = expand_query("How many members play in a team?")
    assert "team roster players lineup" in expanded


def test_entity_focused_query_uses_neutral_field_vocabulary() -> None:
    focused = _focused_query(
        "What jersey number, position, and school are listed for Payton Pritchard?",
        [],
    )
    assert "Payton Pritchard" in focused
    assert "roster player number uniform" in focused
    assert "player position role guard forward center" in focused
    assert "college school last attended" in focused


def test_complete_section_aggregation_selects_multiple_source_groups() -> None:
    chunks = [
        {
            "chunk_id": "magic-roster",
            "text": "Player Paolo Banchero Position Forward",
            "page_url": "https://www.nba.com/team/1/magic",
            "title": "Magic team profile and roster",
            "section": "ROSTER",
            "retrieved_at": "2026-01-01T00:00:00Z",
            "score": 0.8,
        },
        {
            "chunk_id": "blazers-roster",
            "text": "Player Damian Lillard Position Guard",
            "page_url": "https://www.nba.com/team/2/blazers",
            "title": "Blazers team profile and roster",
            "section": "ROSTER",
            "retrieved_at": "2026-01-01T00:00:00Z",
            "score": 0.78,
        },
    ]

    class FakeVector:
        def search_many(self, queries: list[str], limit: int) -> list[list[dict]]:
            return [[dict(item) for item in chunks] for _ in queries]

    class FakeDatabase:
        def search(
            self,
            query: str,
            limit: int,
            *,
            match_mode: str = "any",
        ) -> list[dict]:
            return [dict(item) for item in chunks]

        def section_chunks(
            self,
            page_url: str,
            section: str,
            *,
            limit: int = 8,
        ) -> list[dict]:
            return [
                dict(item)
                for item in chunks
                if item["page_url"] == page_url and item["section"] == section
            ]

        def adjacent_chunks(self, chunk_id: str) -> list[dict]:
            return []

    retriever = HybridRetriever(
        FakeVector(),  # type: ignore[arg-type]
        FakeDatabase(),  # type: ignore[arg-type]
        Settings(retrieval_top_k=3, multi_source_top_k=8),
    )

    results = retriever.search(
        "List members for at least 2 teams",
        ["team roster players"],
        result_limit=8,
        complete_sections=True,
        group_count=2,
    )

    assert {item["page_url"] for item in results} == {
        "https://www.nba.com/team/1/magic",
        "https://www.nba.com/team/2/blazers",
    }


def test_planned_facets_can_select_two_sections_from_the_same_page() -> None:
    records = {
        "background": {
            "chunk_id": "lakers-background",
            "text": "Arena Example Arena Head Coach Example Coach",
            "page_url": "https://www.nba.com/team/1/lakers",
            "title": "Lakers team profile and roster",
            "section": "BACKGROUND",
            "retrieved_at": "2026-01-01T00:00:00Z",
            "score": 0.9,
        },
        "records": {
            "chunk_id": "lakers-records",
            "text": "Total Points Example Player 12345",
            "page_url": "https://www.nba.com/team/1/lakers",
            "title": "Lakers team profile and roster",
            "section": "ALL TIME RECORDS",
            "retrieved_at": "2026-01-01T00:00:00Z",
            "score": 0.9,
        },
        "noise": {
            "chunk_id": "other-records",
            "text": "Total Points Other Player 999",
            "page_url": "https://www.nba.com/team/2/other",
            "title": "Other team profile and roster",
            "section": "ALL TIME RECORDS",
            "retrieved_at": "2026-01-01T00:00:00Z",
            "score": 0.95,
        },
    }

    class FakeVector:
        def search_many(self, queries: list[str], limit: int) -> list[list[dict]]:
            batches = []
            for query in queries:
                lowered = query.lower()
                if "arena head coach" in lowered:
                    batches.append([
                        dict(records["background"]),
                        dict(records["noise"]),
                    ])
                elif "all time points leader" in lowered:
                    batches.append([
                        dict(records["records"]),
                        dict(records["noise"]),
                    ])
                else:
                    batches.append([
                        dict(records["noise"]),
                        dict(records["records"]),
                        dict(records["background"]),
                    ])
            return batches

    class FakeDatabase:
        def title_aliases_for_query(self, query: str) -> list[str]:
            return []

        def search(
            self,
            query: str,
            limit: int,
            *,
            match_mode: str = "any",
        ) -> list[dict]:
            return []

        def adjacent_chunks(self, chunk_id: str) -> list[dict]:
            return []

    retriever = HybridRetriever(
        FakeVector(),  # type: ignore[arg-type]
        FakeDatabase(),  # type: ignore[arg-type]
        Settings(retrieval_top_k=3, fusion_top_k=12),
    )

    results = retriever.search(
        "Give the Lakers arena, head coach, all-time points leader, and his total.",
        ["Lakers arena head coach", "Lakers all time points leader total"],
        result_limit=3,
    )

    assert {item["chunk_id"] for item in results} >= {
        "lakers-background",
        "lakers-records",
    }


def test_each_planned_facet_keeps_its_own_best_real_chunk() -> None:
    profile = {
        "chunk_id": "player-profile",
        "text": "LAST ATTENDED Example University DRAFT 2010 R1 Pick 4",
        "page_url": "https://www.nba.com/player/1",
        "title": "Example Player",
        "section": "Example Player",
        "retrieved_at": "2026-01-01T00:00:00Z",
        "score": 0.9,
    }
    career = {
        "chunk_id": "player-career",
        "text": "Olympic gold medalist in two listed years.",
        "page_url": "https://www.nba.com/player/1/bio",
        "title": "Example Player",
        "section": "PROFESSIONAL CAREER",
        "retrieved_at": "2026-01-01T00:00:00Z",
        "score": 0.9,
    }
    distractor = {
        "chunk_id": "player-personal",
        "text": "Personal background with repeated college references.",
        "page_url": "https://www.nba.com/player/1/bio",
        "title": "Example Player",
        "section": "PERSONAL LIFE",
        "retrieved_at": "2026-01-01T00:00:00Z",
        "score": 0.99,
    }

    class FakeVector:
        def search_many(self, queries: list[str], limit: int) -> list[list[dict]]:
            batches = []
            for query in queries:
                normalized = query.lower()
                if normalized == "example player college draft position":
                    batches.append([dict(profile), dict(distractor)])
                elif normalized == "example player olympic gold medal years":
                    batches.append([dict(career), dict(distractor)])
                else:
                    batches.append([dict(distractor)])
            return batches

    class FakeDatabase:
        def title_aliases_for_query(self, query: str) -> list[str]:
            return []

        def search(
            self,
            query: str,
            limit: int,
            *,
            match_mode: str = "any",
        ) -> list[dict]:
            return []

        def search_with_required(
            self,
            query: str,
            required: str,
            limit: int,
        ) -> list[dict]:
            return [dict(distractor)]

        def adjacent_chunks(self, chunk_id: str) -> list[dict]:
            return []

    retriever = HybridRetriever(
        FakeVector(),  # type: ignore[arg-type]
        FakeDatabase(),  # type: ignore[arg-type]
        Settings(retrieval_top_k=3, fusion_top_k=12),
    )

    results = retriever.search(
        "Give Example Player's college, draft position, and Olympic gold-medal years.",
        [
            "Example Player college draft position",
            "Example Player Olympic gold medal years",
        ],
        result_limit=3,
    )

    assert {item["chunk_id"] for item in results} >= {
        "player-profile",
        "player-career",
    }


def test_index_derived_title_alias_anchors_acronym_query() -> None:
    correct = {
        "chunk_id": "towns-profile",
        "text": "LAST ATTENDED Example College DRAFT 2015 R1 Pick 1",
        "page_url": "https://www.nba.com/player/1/towns",
        "title": "Karl-Anthony Towns",
        "section": "Karl-Anthony Towns",
        "retrieved_at": "2026-01-01T00:00:00Z",
        "score": 0.9,
    }
    noise = {
        "chunk_id": "draft-about",
        "text": "General draft information",
        "page_url": "https://www.nba.com/news/draft",
        "title": "Draft information",
        "section": "Draft",
        "retrieved_at": "2026-01-01T00:00:00Z",
        "score": 0.95,
    }

    class FakeVector:
        def search_many(self, queries: list[str], limit: int) -> list[list[dict]]:
            return [
                [dict(correct), dict(noise)]
                if "karl-anthony towns" in query.lower()
                else [dict(noise)]
                for query in queries
            ]

    class FakeDatabase:
        def title_aliases_for_query(self, query: str) -> list[str]:
            return ["Karl-Anthony Towns"] if "KAT" in query else []

        def search(
            self,
            query: str,
            limit: int,
            *,
            match_mode: str = "any",
        ) -> list[dict]:
            return []

        def adjacent_chunks(self, chunk_id: str) -> list[dict]:
            return []

    retriever = HybridRetriever(
        FakeVector(),  # type: ignore[arg-type]
        FakeDatabase(),  # type: ignore[arg-type]
        Settings(retrieval_top_k=2, fusion_top_k=12),
    )

    results = retriever.search(
        "What college and draft slot are shown for KAT?",
        result_limit=2,
    )

    assert results[0]["chunk_id"] == "towns-profile"


def test_complete_page_section_does_not_prove_complete_population() -> None:
    standings_chunk = {
        "chunk_id": "standings-one-page",
        "text": "Example Team 50 wins 32 losses",
        "page_url": "https://www.nba.com/standings",
        "title": "NBA Standings",
        "section": "Standings",
        "retrieved_at": "2026-01-01T00:00:00Z",
        "score": 0.92,
    }

    class FakeDatabase:
        def section_chunks(
            self,
            page_url: str,
            section: str,
            limit: int | None = None,
        ) -> list[dict]:
            return [dict(standings_chunk)]

    retriever = HybridRetriever(
        vector_store=None,  # type: ignore[arg-type]
        database=FakeDatabase(),  # type: ignore[arg-type]
        config=Settings(),
    )

    bundle = retriever._finalize_atomic_bundle(
        {
            "task_id": "wins-leader",
            "question": "Which team won the most games?",
            "facet": "standings",
            "team_name": "",
            "operation": "argmax",
            "requires_complete_section": True,
            "requires_complete_population": True,
        },
        [],
        [dict(standings_chunk)],
    )

    assert bundle["evidence"]
    assert bundle["complete"] is False
    assert bundle["status"] == "partial"
    assert "population" in bundle["missing_reason"].lower()


def test_team_scoped_nested_schedule_page_is_not_discarded() -> None:
    schedule_chunk = {
        "chunk_id": "celtics-schedule",
        "text": "Boston Celtics schedule and game results.",
        "page_url": (
            "https://www.nba.com/team/1610612738/celtics/schedule"
        ),
        "title": "Boston Celtics Schedule",
        "section": "Schedule",
        "retrieved_at": "2026-01-01T00:00:00Z",
        "score": 0.91,
    }

    class FakeDatabase:
        def find_team_pages(
            self,
            team_query: str,
            limit: int | None = 3,
        ) -> list[dict]:
            return [{
                "team_name": "Boston Celtics",
                "team_id": "1610612738",
                "page_url": "https://www.nba.com/team/1610612738/celtics",
                "title": "Boston Celtics team profile",
                "slug": "celtics",
                "match_score": 500.0,
            }]

    retriever = HybridRetriever(
        vector_store=None,  # type: ignore[arg-type]
        database=FakeDatabase(),  # type: ignore[arg-type]
        config=Settings(),
    )

    bundle = retriever._finalize_atomic_bundle(
        {
            "task_id": "celtics-schedule",
            "question": "What is the Boston Celtics schedule?",
            "facet": "schedule",
            "team_name": "Boston Celtics",
            "requires_complete_section": False,
            "requires_complete_population": False,
        },
        [],
        [dict(schedule_chunk)],
    )

    assert bundle["status"] == "found"
    assert [item["chunk_id"] for item in bundle["evidence"]] == [
        "celtics-schedule"
    ]
