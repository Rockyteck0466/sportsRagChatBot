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
