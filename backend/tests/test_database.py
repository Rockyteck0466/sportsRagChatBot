from pathlib import Path

from app.database import Database


def test_batched_source_and_prepared_question_searches_stay_aligned(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "batch.sqlite")
    page = {
        "url": "https://www.nba.com/news/about",
        "title": "About the NBA",
        "retrieved_at": "2026-07-25T00:00:00Z",
        "content_hash": "about",
        "text": "The NBA has six divisions and thirty teams.",
    }
    chunk = {
        "chunk_id": "nba:web:about:league",
        "page_url": page["url"],
        "title": page["title"],
        "section": "League organization",
        "retrieved_at": page["retrieved_at"],
        "text": page["text"],
    }
    database.replace_corpus([page], [chunk])
    database.replace_chunk_questions(
        [
            {
                "question_id": "question:division-count",
                "chunk_id": chunk["chunk_id"],
                "question": "How many divisions does the NBA have?",
                "kind": "count",
            }
        ]
    )

    source_batches = database.search_many(
        ["NBA divisions", "unknown mascot"],
        3,
    )
    prepared_batches = database.search_chunk_questions_many(
        ["NBA division count", "unknown mascot"],
        3,
    )

    assert len(source_batches) == 2
    assert len(prepared_batches) == 2
    assert source_batches[0][0]["chunk_id"] == chunk["chunk_id"]
    assert source_batches[1] == []
    assert prepared_batches[0][0]["question_id"] == "question:division-count"
    assert prepared_batches[1] == []


def test_relevant_fts_result_clears_grounding_threshold(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.sqlite")
    page = {
        "url": "https://www.nba.com/news/example",
        "title": "LeBron James picks Philadelphia",
        "retrieved_at": "2026-07-25T00:00:00Z",
        "content_hash": "abc",
        "text": "LeBron James selected Philadelphia for his final NBA challenge.",
    }
    chunk = {
        "chunk_id": "nba:web:test:headline:0001",
        "page_url": page["url"],
        "title": page["title"],
        "section": "Free agency",
        "retrieved_at": page["retrieved_at"],
        "text": page["text"],
    }
    database.replace_corpus([page], [chunk])

    results = database.search("Which team did LeBron James choose?", 5)

    assert results
    assert results[0]["score"] >= 0.12
    assert results[0]["chunk_id"] == chunk["chunk_id"]


def test_upsert_corpus_adds_page_without_clearing_existing_page(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.sqlite")
    first_page = {
        "url": "https://www.nba.com/news/example",
        "title": "Existing page",
        "retrieved_at": "2026-07-25T00:00:00Z",
        "content_hash": "first",
        "text": "Existing indexed basketball information.",
    }
    first_chunk = {
        "chunk_id": "nba:web:first:page:0001",
        "page_url": first_page["url"],
        "title": first_page["title"],
        "section": "Page",
        "retrieved_at": first_page["retrieved_at"],
        "text": first_page["text"],
    }
    database.replace_corpus([first_page], [first_chunk])
    rule_page = {
        "url": "https://official.nba.com/rule-no-3/",
        "title": "Rule 3",
        "retrieved_at": "2026-07-25T01:00:00Z",
        "content_hash": "rule",
        "text": "Each team shall consist of five players.",
    }
    rule_chunk = {
        "chunk_id": "nba:web:rule:team:0001",
        "page_url": rule_page["url"],
        "title": rule_page["title"],
        "section": "Team",
        "retrieved_at": rule_page["retrieved_at"],
        "text": rule_page["text"],
    }

    database.upsert_corpus([rule_page], [rule_chunk])

    assert database.status()["indexed_pages"] == 2
    assert database.search("five players team", 5)[0]["chunk_id"] == rule_chunk["chunk_id"]


def test_replace_corpus_clears_stale_synthetic_questions(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.sqlite")
    old_page = {
        "url": "https://www.nba.com/team/1610612738/celtics",
        "title": "Boston Celtics",
        "retrieved_at": "2026-07-25T00:00:00Z",
        "content_hash": "old",
        "text": "The indexed Boston roster snapshot.",
    }
    old_chunk = {
        "chunk_id": "nba:web:old:roster:0001",
        "page_url": old_page["url"],
        "title": old_page["title"],
        "section": "Roster",
        "retrieved_at": old_page["retrieved_at"],
        "text": "Jayson Tatum and Derrick White are listed in this roster passage.",
    }
    database.replace_corpus([old_page], [old_chunk])
    database.replace_chunk_questions(
        [
            {
                "question_id": "question:old-roster:0001",
                "chunk_id": old_chunk["chunk_id"],
                "question": "Which Celtics players are listed in the roster?",
                "kind": "roster",
            }
        ]
    )

    assert database.status()["indexed_questions"] == 1

    new_page = {
        "url": "https://official.nba.com/rule-no-3/",
        "title": "Rule 3",
        "retrieved_at": "2026-07-25T01:00:00Z",
        "content_hash": "new",
        "text": "Each team shall consist of five players.",
    }
    new_chunk = {
        "chunk_id": "nba:web:new:team:0001",
        "page_url": new_page["url"],
        "title": new_page["title"],
        "section": "Team",
        "retrieved_at": new_page["retrieved_at"],
        "text": new_page["text"],
    }

    database.replace_corpus([new_page], [new_chunk])

    status = database.status()
    assert status["indexed_questions"] == 0
    assert database.search_chunk_questions("Celtics roster players", 5) == []
    assert database.chunks_by_ids([old_chunk["chunk_id"]]) == []
    assert database.chunks_by_ids([new_chunk["chunk_id"]])[0]["text"] == new_chunk["text"]


def test_title_acronym_alias_is_derived_from_indexed_titles(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.sqlite")
    page = {
        "url": "https://www.nba.com/player/example",
        "title": "Karl-Anthony Towns",
        "retrieved_at": "2026-07-25T00:00:00Z",
        "content_hash": "towns",
        "text": "An indexed NBA.com player profile.",
    }
    chunk = {
        "chunk_id": "nba:web:towns:profile:0001",
        "page_url": page["url"],
        "title": page["title"],
        "section": page["title"],
        "retrieved_at": page["retrieved_at"],
        "text": page["text"],
    }
    database.replace_corpus([page], [chunk])

    assert database.title_aliases_for_query("What college is shown for KAT?") == [
        "Karl-Anthony Towns"
    ]
    assert database.title_aliases_for_query("What college is shown?") == []


def test_required_entity_search_keeps_intent_on_the_named_source(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "test.sqlite")
    pages = [
        {
            "url": "https://www.nba.com/team/example",
            "title": "Example team profile and roster",
            "retrieved_at": "2026-07-25T00:00:00Z",
            "content_hash": "roster",
            "text": "Payton Pritchard 11 G Oregon",
        },
        {
            "url": "https://official.nba.com/rule/example",
            "title": "Uniform rule",
            "retrieved_at": "2026-07-25T00:00:00Z",
            "content_hash": "rule",
            "text": "Every player jersey must show a number.",
        },
    ]
    chunks = [
        {
            "chunk_id": "nba:web:example:roster:0001",
            "page_url": pages[0]["url"],
            "title": pages[0]["title"],
            "section": "ROSTER",
            "retrieved_at": pages[0]["retrieved_at"],
            "text": pages[0]["text"],
        },
        {
            "chunk_id": "nba:web:example:rule:0001",
            "page_url": pages[1]["url"],
            "title": pages[1]["title"],
            "section": "Uniforms",
            "retrieved_at": pages[1]["retrieved_at"],
            "text": pages[1]["text"],
        },
    ]
    database.replace_corpus(pages, chunks)

    results = database.search_with_required(
        "Payton Pritchard jersey number position school roster",
        "Payton Pritchard",
        5,
    )

    assert [item["chunk_id"] for item in results] == [
        "nba:web:example:roster:0001"
    ]


def test_team_pages_prefers_canonical_slug_page_with_roster(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "test.sqlite")
    retrieved_at = "2026-07-25T00:00:00Z"
    pages = [
        {
            "url": "https://www.nba.com/team/1610612738",
            "title": "Upcoming Games",
            "retrieved_at": retrieved_at,
            "content_hash": "celtics-short",
            "text": "A short-url Celtics roster.",
        },
        {
            "url": "https://www.nba.com/team/1610612738/celtics",
            "title": "Upcoming Games",
            "retrieved_at": retrieved_at,
            "content_hash": "celtics-canonical",
            "text": "The canonical Celtics profile and roster.",
        },
        {
            "url": "https://www.nba.com/team/1610612738/celtics/schedule",
            "title": "Celtics schedule",
            "retrieved_at": retrieved_at,
            "content_hash": "celtics-nested",
            "text": "A nested team schedule URL.",
        },
        {
            "url": "https://www.nba.com/team/1610612747/lakers",
            "title": "Lakers team profile and roster",
            "retrieved_at": retrieved_at,
            "content_hash": "lakers",
            "text": "The canonical Lakers profile and roster.",
        },
    ]
    chunks = [
        {
            "chunk_id": "celtics-short-roster",
            "page_url": pages[0]["url"],
            "title": "Celtics team profile and roster",
            "section": "ROSTER",
            "retrieved_at": retrieved_at,
            "text": "Short URL roster source.",
        },
        {
            "chunk_id": "celtics-canonical-coaches",
            "page_url": pages[1]["url"],
            "title": "Celtics team profile and roster",
            "section": "COACHING STAFF",
            "retrieved_at": retrieved_at,
            "text": "Canonical URL coaching source.",
        },
        {
            "chunk_id": "celtics-canonical-roster",
            "page_url": pages[1]["url"],
            "title": "Celtics team profile and roster",
            "section": "ROSTER",
            "retrieved_at": retrieved_at,
            "text": "Canonical URL roster source.",
        },
        {
            "chunk_id": "celtics-nested-roster",
            "page_url": pages[2]["url"],
            "title": "Celtics schedule",
            "section": "ROSTER",
            "retrieved_at": retrieved_at,
            "text": "Nested page noise.",
        },
        {
            "chunk_id": "lakers-roster",
            "page_url": pages[3]["url"],
            "title": pages[3]["title"],
            "section": "ROSTER",
            "retrieved_at": retrieved_at,
            "text": "Lakers roster source.",
        },
    ]
    database.replace_corpus(pages, chunks)

    teams = database.team_pages(limit=None)

    assert [team["team_id"] for team in teams] == [
        "1610612738",
        "1610612747",
    ]
    assert teams[0] == {
        "team_name": "Boston Celtics",
        "team_id": "1610612738",
        "page_url": "https://www.nba.com/team/1610612738/celtics",
        "title": "Celtics team profile and roster",
        "slug": "celtics",
        "has_roster": True,
    }


def test_find_team_pages_understands_city_full_name_and_nickname(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "test.sqlite")
    retrieved_at = "2026-07-25T00:00:00Z"
    identities = [
        ("1610612738", "celtics", "Celtics team profile and roster"),
        ("1610612746", "clippers", "Clippers team profile and roster"),
        ("1610612747", "lakers", "Lakers team profile and roster"),
    ]
    pages = [
        {
            "url": f"https://www.nba.com/team/{team_id}/{slug}",
            "title": title,
            "retrieved_at": retrieved_at,
            "content_hash": team_id,
            "text": f"Indexed {title}.",
        }
        for team_id, slug, title in identities
    ]
    chunks = [
        {
            "chunk_id": f"{slug}-roster",
            "page_url": page["url"],
            "title": title,
            "section": "ROSTER",
            "retrieved_at": retrieved_at,
            "text": f"Original {slug} roster source.",
        }
        for page, (_, slug, title) in zip(pages, identities)
    ]
    database.replace_corpus(pages, chunks)

    assert database.find_team_pages("Boston Celtics details", limit=1)[0][
        "team_id"
    ] == "1610612738"
    assert database.find_team_pages("show the Celtics players", limit=1)[0][
        "team_id"
    ] == "1610612738"
    assert {
        item["team_id"]
        for item in database.find_team_pages("Los Angeles teams", limit=None)
    } == {"1610612746", "1610612747"}
    assert database.find_team_pages("Seattle team", limit=None) == []


def test_complete_team_section_uses_only_original_chunks_in_source_order(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "test.sqlite")
    retrieved_at = "2026-07-25T00:00:00Z"
    page = {
        "url": "https://www.nba.com/team/1610612738/celtics",
        "title": "Celtics team profile and roster",
        "retrieved_at": retrieved_at,
        "content_hash": "celtics",
        "text": "A complete source page.",
    }
    chunks = [
        {
            "chunk_id": "z-roster-first",
            "page_url": page["url"],
            "title": page["title"],
            "section": "ROSTER",
            "retrieved_at": retrieved_at,
            "text": "First original roster source chunk.",
        },
        {
            "chunk_id": "coach-middle",
            "page_url": page["url"],
            "title": page["title"],
            "section": "Assistant Coach",
            "retrieved_at": retrieved_at,
            "text": "An assistant coach source chunk.",
        },
        {
            "chunk_id": "a-roster-last",
            "page_url": page["url"],
            "title": page["title"],
            "section": " Roster ",
            "retrieved_at": retrieved_at,
            "text": "Last original roster source chunk.",
        },
    ]
    database.replace_corpus([page], chunks)
    database.replace_chunk_questions(
        [
            {
                "question_id": "synthetic-captain",
                "chunk_id": "coach-middle",
                "question": "Who is the Celtics captain?",
                "kind": "team-leadership",
            }
        ]
    )

    sections = database.page_sections(page["url"])
    roster = next(
        item for item in sections if item["normalized_section"] == "roster"
    )
    results = database.team_section_chunks(
        "Boston",
        " roster ",
        chunk_limit=None,
    )

    assert roster["chunk_count"] == 2
    assert [item["chunk_id"] for item in results] == [
        "z-roster-first",
        "a-roster-last",
    ]
    assert all(item["team_name"] == "Boston Celtics" for item in results)
    assert all("question" not in item for item in results)
    assert database.section_chunks(
        page["url"],
        "ROSTER",
        limit=None,
    ) == [
        {key: value for key, value in item.items() if key not in {"team_name", "team_id"}}
        for item in results
    ]
    assert len(
        database.team_section_chunks(
            "Celtics",
            "roster",
            chunk_limit=1,
        )
    ) == 1


def test_team_directory_and_captain_candidates_are_source_scoped(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "test.sqlite")
    retrieved_at = "2026-07-25T00:00:00Z"
    pages = [
        {
            "url": "https://www.nba.com/teams",
            "title": "NBA Teams",
            "retrieved_at": retrieved_at,
            "content_hash": "teams",
            "text": "Boston Celtics and Los Angeles Lakers.",
        },
        {
            "url": "https://www.nba.com/team/1610612738/celtics",
            "title": "Celtics team profile and roster",
            "retrieved_at": retrieved_at,
            "content_hash": "celtics",
            "text": "Boston Celtics roster.",
        },
        {
            "url": "https://www.nba.com/news/celtics-captain",
            "title": "Boston Celtics update",
            "retrieved_at": retrieved_at,
            "content_hash": "captain",
            "text": "Boston Celtics named Example Player as team captain.",
        },
        {
            "url": "https://official.nba.com/rule-no-3",
            "title": "NBA Rule No. 3",
            "retrieved_at": retrieved_at,
            "content_hash": "rule",
            "text": "A team may designate a captain.",
        },
    ]
    chunks = [
        {
            "chunk_id": "teams-directory",
            "page_url": pages[0]["url"],
            "title": pages[0]["title"],
            "section": "All Teams",
            "retrieved_at": retrieved_at,
            "text": pages[0]["text"],
        },
        {
            "chunk_id": "celtics-roster",
            "page_url": pages[1]["url"],
            "title": pages[1]["title"],
            "section": "ROSTER",
            "retrieved_at": retrieved_at,
            "text": pages[1]["text"],
        },
        {
            "chunk_id": "celtics-captain-news",
            "page_url": pages[2]["url"],
            "title": pages[2]["title"],
            "section": "Team update",
            "retrieved_at": retrieved_at,
            "text": pages[2]["text"],
        },
        {
            "chunk_id": "generic-captain-rule",
            "page_url": pages[3]["url"],
            "title": pages[3]["title"],
            "section": "The Captain",
            "retrieved_at": retrieved_at,
            "text": pages[3]["text"],
        },
    ]
    database.replace_corpus(pages, chunks)

    assert [item["chunk_id"] for item in database.team_directory_chunks()] == [
        "teams-directory"
    ]
    assert [
        item["chunk_id"]
        for item in database.captain_candidate_chunks("Boston Celtics")
    ] == ["celtics-captain-news"]
    assert database.captain_candidate_chunks("Seattle") == []
