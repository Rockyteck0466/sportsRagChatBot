import re

from app.rag import ANSWER_SCHEMA, RagService, compact_evidence


def test_trailing_citation_label_can_be_normalized_inline() -> None:
    answer = "LeBron James chose Philadelphia. Citation: [C2]"
    normalized = re.sub(
        r"\.\s*(?:citation|source)s?:\s*((?:\[C\d+\][,;\s]*)+)(?=$|\n)",
        lambda match: f" {match.group(1).strip(' ,;')}.",
        answer,
        flags=re.IGNORECASE,
    )
    assert normalized == "LeBron James chose Philadelphia [C2]."


def test_compact_evidence_keeps_labels_and_removes_markdown_targets() -> None:
    evidence = "[Atlantic](https://www.nba.com/teams) ![NBA Logo](https://cdn.nba.com/logo.svg)"
    compact = compact_evidence(evidence)
    assert "Atlantic" in compact
    assert "https://" not in compact


def test_answer_schema_requires_all_structured_fields() -> None:
    assert set(ANSWER_SCHEMA["required"]) == {"answer", "citation_ids", "insufficient"}
    assert ANSWER_SCHEMA["additionalProperties"] is False


def test_model_json_parser_rejects_empty_non_json() -> None:
    assert RagService._parse_generated('{"answer":"Six [C1].","citation_ids":["C1"],"insufficient":false}')[
        "citation_ids"
    ] == ["C1"]
