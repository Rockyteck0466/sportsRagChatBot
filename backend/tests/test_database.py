from pathlib import Path

from app.database import Database


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
