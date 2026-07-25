import re

HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
HTML_TAG = re.compile(r"<[^>]+>")
GENERIC_H1 = {
    "nba",
    "nba com",
    "nba official",
    "news archive",
}
STOP_HEADINGS = {
    "related",
    "latest",
    "nba organization",
    "nba social impact",
    "across the league",
    "shop",
    "subscriptions",
}


def _visible_text(value: str) -> str:
    value = IMAGE.sub(r"\1", value)
    value = LINK.sub(r"\1", value)
    value = HTML_TAG.sub(" ", value)
    value = re.sub(r"[*_`#]+", " ", value)
    return " ".join(value.split()).strip()


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _visible_text(value).lower()).strip()


def _mojibake_score(value: str) -> int:
    markers = ("Ã", "Â", "Ä", "Å", "â€", "â€™", "â€“", "ðŸ", "\ufffd")
    return sum(value.count(marker) for marker in markers) + sum(
        "\u0080" <= character <= "\u009f" for character in value
    )


def _repair_fragment(value: str) -> str:
    before = _mojibake_score(value)
    if before == 0:
        return value
    candidates = [value]
    for encoding in ("cp1252", "latin1"):
        try:
            candidates.append(value.encode(encoding).decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return min(
        candidates,
        key=lambda candidate: (_mojibake_score(candidate), len(candidate)),
    )


def repair_mojibake(text: str) -> str:
    """Repair mixed UTF-8/Windows-1252 mojibake without changing valid names."""
    if _mojibake_score(text) == 0:
        return text
    repaired_lines: list[str] = []
    suspicious_token = re.compile(
        r"\S*(?:Ã|Â|Ä|Å|â|ð|\ufffd|[\u0080-\u009f])\S*"
    )
    for line in text.splitlines(keepends=True):
        repaired_line = _repair_fragment(line)
        if _mojibake_score(repaired_line) >= _mojibake_score(line):
            repaired_line = suspicious_token.sub(
                lambda match: _repair_fragment(match.group(0)),
                line,
            )
        repaired_lines.append(repaired_line)
    return "".join(repaired_lines)


def clean_markdown(markdown: str) -> str:
    """Extract likely page content and remove token-heavy Markdown boilerplate."""
    markdown = repair_mojibake(markdown)
    lines = markdown.splitlines()
    if lines and lines[0].strip() == "---":
        try:
            closing = next(
                index
                for index in range(1, len(lines))
                if lines[index].strip() == "---"
            )
            lines = lines[closing + 1 :]
        except StopIteration:
            pass

    start = 0
    for index, line in enumerate(lines):
        match = HEADING.match(line.strip())
        if not match or len(match.group(1)) != 1:
            continue
        heading = _normalized(match.group(2))
        if heading in GENERIC_H1 or not heading:
            continue
        start = index
        break

    content: list[str] = []
    blank_pending = False
    for line in lines[start:]:
        stripped = line.strip()
        heading_match = HEADING.match(stripped)
        if (
            content
            and heading_match
            and _normalized(heading_match.group(2)) in STOP_HEADINGS
        ):
            break
        visible = _visible_text(stripped)
        if content and visible.lower() in {
            "nba organization",
            "nba social impact",
            "shop",
            "subscriptions",
        }:
            break
        if content and re.match(
            r"^\*\s+\[?today'?s officials",
            stripped,
            flags=re.IGNORECASE,
        ):
            break
        if not stripped:
            blank_pending = bool(content)
            continue
        if stripped.lower().startswith(
            ("navigation toggle", "toggle navigation", "skip to content")
        ):
            continue

        cleaned = IMAGE.sub(r"\1", stripped)
        cleaned = LINK.sub(r"\1", cleaned)
        cleaned = HTML_TAG.sub(" ", cleaned)
        cleaned = re.sub(r"https?://\S+", "", cleaned)
        cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
        if not cleaned or cleaned in {"*", "* * *"}:
            continue
        if blank_pending:
            content.append("")
            blank_pending = False
        content.append(cleaned)

    return "\n".join(content).strip()
