import hashlib
import re
from urllib.parse import urlparse, urlunparse

from .config import Settings
from .content_cleaner import clean_markdown

GENERIC_TITLES = {
    "",
    "nba",
    "nba source",
    "page content",
    "upcoming games",
}


def canonical_source_url(raw_url: str) -> str:
    """Collapse NBA URL aliases into one stable source identity."""
    parsed = urlparse(raw_url)
    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", "")
    )


def source_title(page: dict, canonical_url: str) -> str:
    """Derive entity metadata when NBA's first H1 is only a widget title."""
    current = " ".join(str(page.get("title", "")).split()).strip()
    if current.lower() not in GENERIC_TITLES:
        return current[:300]
    path = urlparse(canonical_url).path
    team_match = re.search(r"/team/\d+/([^/]+)$", path, flags=re.IGNORECASE)
    if team_match:
        team = team_match.group(1).replace("-", " ").title()
        return f"{team} team profile and roster"
    player_match = re.search(r"/player/\d+/([^/]+)", path, flags=re.IGNORECASE)
    if player_match:
        player = player_match.group(1).replace("-", " ").title()
        return f"{player} player profile"
    if path.rstrip("/") == "/players":
        return "NBA League Roster"
    if path.rstrip("/") == "/teams":
        return "NBA Teams"
    return current or canonical_url


def chunk_pages(pages: list[dict], config: Settings) -> list[dict]:
    chunks: list[dict] = []
    size = config.chunk_words
    step = max(1, size - config.chunk_overlap_words)
    seen_urls: set[str] = set()
    seen_pages: set[str] = set()

    for page in pages:
        canonical_url = canonical_source_url(page["url"])
        if canonical_url in seen_urls:
            continue
        cleaned_markdown = clean_markdown(page["text"])
        normalized_page = re.sub(r"\s+", " ", cleaned_markdown).strip().lower()
        if not normalized_page:
            continue
        page_hash = hashlib.sha256(normalized_page.encode()).hexdigest()
        if page_hash in seen_pages:
            continue
        seen_urls.add(canonical_url)
        seen_pages.add(page_hash)
        title = source_title(page, canonical_url)

        section = "Page content"
        section_words: list[str] = []
        sections: list[tuple[str, list[str]]] = []
        for line in cleaned_markdown.splitlines():
            heading_match = re.match(r"^#{1,4}\s+(.+)$", line)
            question_match = re.match(r"^\s*\*\*(.+\?)\*\*\s*$", line)
            rule_section_match = re.match(
                r"^\s*(Section\s+[IVXLC]+(?:\u2014|\u2013|-).+?)\s*$",
                line,
                re.IGNORECASE,
            )
            if heading_match or question_match or rule_section_match:
                if section_words:
                    sections.append((section, section_words))
                next_section = (
                    heading_match.group(1)
                    if heading_match
                    else question_match.group(1)
                    if question_match
                    else rule_section_match.group(1)
                )
                section = next_section[:180]
                section_words = []
            else:
                section_words.extend(line.split())
        if section_words:
            sections.append((section, section_words))

        number = 0
        for heading, words in sections:
            for start in range(0, len(words), step):
                window = words[start : start + size]
                if len(window) < config.min_chunk_words:
                    continue
                number += 1
                digest = hashlib.sha256(
                    f"{canonical_url}:{number}:{' '.join(window)}".encode()
                ).hexdigest()[:12]
                slug = (
                    re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")[:45]
                    or "page"
                )
                chunks.append({
                    "chunk_id": f"nba:web:{digest}:{slug}:{number:04d}",
                    "page_url": canonical_url,
                    "title": title,
                    "section": heading,
                    "retrieved_at": page["retrieved_at"],
                    "text": " ".join(window),
                })
                if start + size >= len(words):
                    break
    return chunks
