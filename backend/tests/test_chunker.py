from app.chunker import chunk_pages
from app.config import Settings


def _page(text: str, *, url: str = "https://www.nba.com/news/about") -> dict:
    return {
        "url": url,
        "title": "NBA source",
        "retrieved_at": "2026-01-01T00:00:00Z",
        "text": text,
    }


def test_chunks_keep_provenance() -> None:
    settings = Settings(chunk_words=50, chunk_overlap_words=10)
    page = _page(
        "## Headlines\n" + "basketball information " * 80,
        url="https://www.nba.com/",
    )
    chunks = chunk_pages([page], settings)
    assert chunks
    assert chunks[0]["page_url"] == page["url"]
    assert chunks[0]["section"] == "Headlines"
    assert chunks[0]["chunk_id"].startswith("nba:web:")


def test_faq_question_becomes_section_and_short_answer_is_indexed() -> None:
    settings = Settings(
        chunk_words=50,
        chunk_overlap_words=10,
        min_chunk_words=6,
    )
    page = _page(
        """# About the NBA

**How many players are on an NBA roster?**

NBA rosters include 15 players, with 12 active for each game.

### Related

Unrelated recommendation content must be removed.
"""
    )

    chunks = chunk_pages([page], settings)

    assert len(chunks) == 1
    assert chunks[0]["section"] == "How many players are on an NBA roster?"
    assert chunks[0]["text"] == (
        "NBA rosters include 15 players, with 12 active for each game."
    )
    assert "recommendation" not in chunks[0]["text"]


def test_official_rule_section_becomes_section_and_short_fact_is_indexed() -> None:
    settings = Settings(
        chunk_words=50,
        chunk_overlap_words=10,
        min_chunk_words=6,
    )
    page = _page(
        """# NBA Official

* RULE NO. 1: Court Dimensions
* RULE NO. 2: Officials

# RULE NO 3: Players, Substitutes and Coaches

Section I\u00e2\u20ac\u201dTeam

Each team shall consist of five players.
""",
        url="https://official.nba.com/rule-no-3-players-substitutes-and-coaches/",
    )

    chunks = chunk_pages([page], settings)

    assert len(chunks) == 1
    assert chunks[0]["section"] == "Section I\u2014Team"
    assert chunks[0]["text"] == "Each team shall consist of five players."


def test_rule_section_recognizes_a_normal_unicode_em_dash() -> None:
    settings = Settings(
        chunk_words=50,
        chunk_overlap_words=10,
        min_chunk_words=6,
    )
    page = _page(
        """# RULE NO 3: Players, Substitutes and Coaches

Section I\u2014Team

Each team shall consist of five players.
""",
        url="https://official.nba.com/rule-no-3-players-substitutes-and-coaches/",
    )

    chunks = chunk_pages([page], settings)

    assert len(chunks) == 1
    assert chunks[0]["section"] == "Section I\u2014Team"
    assert chunks[0]["text"] == "Each team shall consist of five players."


def test_team_widget_title_is_replaced_with_url_entity_metadata() -> None:
    settings = Settings(chunk_words=50, chunk_overlap_words=10, min_chunk_words=6)
    page = _page(
        "# ROSTER\nPlayer | Position\nJayson Tatum | Forward",
        url="https://www.nba.com/team/1610612738/celtics/",
    )
    page["title"] = "Upcoming Games"

    chunks = chunk_pages([page], settings)

    assert chunks[0]["title"] == "Celtics team profile and roster"
    assert chunks[0]["page_url"] == "https://www.nba.com/team/1610612738/celtics"


def test_canonical_url_alias_is_indexed_once() -> None:
    settings = Settings(chunk_words=50, chunk_overlap_words=10, min_chunk_words=6)
    text = "# ROSTER\nPlayer | Position\nJayson Tatum | Forward"
    first = _page(text, url="https://www.nba.com/team/1610612738/celtics")
    second = _page(text, url="https://www.nba.com/team/1610612738/celtics/")

    chunks = chunk_pages([first, second], settings)

    assert len(chunks) == 1
