from pathlib import Path

from app.database import Database


def _page() -> dict:
    return {
        "url": "https://www.nba.com/news/synthetic-question-test",
        "title": "NBA facts",
        "retrieved_at": "2026-07-25T00:00:00Z",
        "content_hash": "question-index",
        "text": "Indexed NBA facts used to test question-to-chunk mappings.",
    }


def _chunk(page: dict, suffix: str, section: str, text: str) -> dict:
    return {
        "chunk_id": f"nba:web:test:{suffix}:0001",
        "page_url": page["url"],
        "title": page["title"],
        "section": section,
        "retrieved_at": page["retrieved_at"],
        "text": text,
    }


def test_synthetic_question_search_maps_to_original_evidence_chunks(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.sqlite")
    page = _page()
    players_chunk = _chunk(
        page,
        "players",
        "Players",
        "Each team shall consist of five players on the court.",
    )
    divisions_chunk = _chunk(
        page,
        "divisions",
        "League organization",
        "The NBA is organized into six divisions.",
    )
    database.replace_corpus([page], [players_chunk, divisions_chunk])
    database.replace_chunk_questions(
        [
            {
                "question_id": "question:players:0001",
                "chunk_id": players_chunk["chunk_id"],
                "question": "How many players from each team can be on the court?",
                "kind": "paraphrase",
            },
            {
                "question_id": "question:divisions:0001",
                "chunk_id": divisions_chunk["chunk_id"],
                "question": "How many divisions organize the NBA?",
                "kind": "paraphrase",
            },
        ]
    )

    matches = database.search_chunk_questions(
        "How many players can a team have on the court?",
        limit=5,
    )

    assert matches
    assert matches[0]["question_id"] == "question:players:0001"
    assert matches[0]["chunk_id"] == players_chunk["chunk_id"]
    assert matches[0]["question"] == (
        "How many players from each team can be on the court?"
    )
    assert matches[0]["kind"] == "paraphrase"

    evidence = database.chunks_by_ids([matches[0]["chunk_id"]])
    assert len(evidence) == 1
    assert evidence[0]["chunk_id"] == players_chunk["chunk_id"]
    assert evidence[0]["page_url"] == players_chunk["page_url"]
    assert evidence[0]["section"] == players_chunk["section"]
    assert evidence[0]["text"] == players_chunk["text"]
    assert matches[0]["question"] not in evidence[0]["text"]
    assert database.status()["indexed_questions"] == 2


def test_replace_chunk_questions_replaces_the_complete_question_index(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "test.sqlite")
    page = _page()
    players_chunk = _chunk(
        page,
        "players",
        "Players",
        "Each team shall consist of five players on the court.",
    )
    divisions_chunk = _chunk(
        page,
        "divisions",
        "League organization",
        "The NBA is organized into six divisions.",
    )
    database.replace_corpus([page], [players_chunk, divisions_chunk])
    database.replace_chunk_questions(
        [
            {
                "question_id": "question:stale:0001",
                "chunk_id": players_chunk["chunk_id"],
                "question": "How many players are allowed on the court?",
                "kind": "paraphrase",
            }
        ]
    )

    database.replace_chunk_questions(
        [
            {
                "question_id": "question:current:0001",
                "chunk_id": divisions_chunk["chunk_id"],
                "question": "How many divisions are in the NBA?",
                "kind": "paraphrase",
            }
        ]
    )

    assert database.status()["indexed_questions"] == 1
    assert database.search_chunk_questions("players allowed on court", 5) == []
    current = database.search_chunk_questions("divisions in the NBA", 5)
    assert current
    assert current[0]["question_id"] == "question:current:0001"
    assert current[0]["chunk_id"] == divisions_chunk["chunk_id"]


def test_chunks_by_ids_returns_only_existing_original_chunks(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.sqlite")
    page = _page()
    first_chunk = _chunk(page, "first", "First", "First original evidence.")
    second_chunk = _chunk(page, "second", "Second", "Second original evidence.")
    database.replace_corpus([page], [first_chunk, second_chunk])

    chunks = database.chunks_by_ids(
        [
            second_chunk["chunk_id"],
            "nba:web:missing:chunk:9999",
            first_chunk["chunk_id"],
        ]
    )

    by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    assert set(by_id) == {first_chunk["chunk_id"], second_chunk["chunk_id"]}
    assert by_id[first_chunk["chunk_id"]]["text"] == first_chunk["text"]
    assert by_id[second_chunk["chunk_id"]]["text"] == second_chunk["text"]


def test_question_records_cannot_reference_an_unknown_chunk(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.sqlite")
    page = _page()
    source_chunk = _chunk(page, "source", "Source", "Original indexed evidence.")
    database.replace_corpus([page], [source_chunk])

    try:
        database.replace_chunk_questions(
            [
                {
                    "question_id": "question:orphan:0001",
                    "chunk_id": "nba:web:missing:chunk:9999",
                    "question": "Can this question point to missing evidence?",
                    "kind": "paraphrase",
                }
            ]
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Synthetic questions must map to an existing source chunk.")

    assert database.status()["indexed_questions"] == 0
