from app.chunker import chunk_pages
from app.config import Settings


def test_chunks_keep_provenance() -> None:
    settings = Settings(chunk_words=50, chunk_overlap_words=10)
    page = {
        "url": "https://www.nba.com/",
        "title": "NBA",
        "retrieved_at": "2026-01-01T00:00:00Z",
        "text": "## Headlines\n" + "basketball information " * 80,
    }
    chunks = chunk_pages([page], settings)
    assert chunks
    assert chunks[0]["page_url"] == page["url"]
    assert chunks[0]["section"] == "Headlines"
    assert chunks[0]["chunk_id"].startswith("nba:web:")
