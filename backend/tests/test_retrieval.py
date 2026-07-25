from app.retrieval import expand_query


def test_short_division_question_is_expanded_with_nba_structure() -> None:
    expanded = expand_query("How many divisions are there?")
    assert "NBA basketball" in expanded
    assert "Eastern Conference" in expanded
    assert "Atlantic Central Southeast Northwest Pacific Southwest" in expanded


def test_team_synonym_expands_without_llm() -> None:
    expanded = expand_query("List the franchises")
    assert "NBA teams franchises league roster all teams" in expanded
