import hashlib
import re

from .config import Settings


def chunk_pages(pages: list[dict], config: Settings) -> list[dict]:
    chunks: list[dict] = []
    size = config.chunk_words
    step = max(1, size - config.chunk_overlap_words)
    for page in pages:
        section = "Page content"
        section_words: list[str] = []
        sections: list[tuple[str, list[str]]] = []
        for line in page["text"].splitlines():
            heading_match = re.match(r"^#{1,4}\s+(.+)$", line)
            if heading_match:
                if section_words:
                    sections.append((section, section_words))
                section = heading_match.group(1)[:180]
                section_words = []
            else:
                section_words.extend(line.split())
        if section_words:
            sections.append((section, section_words))
        number = 0
        for heading, words in sections:
            for start in range(0, len(words), step):
                window = words[start : start + size]
                if len(window) < 35:
                    continue
                number += 1
                digest = hashlib.sha256(f"{page['url']}:{number}:{' '.join(window)}".encode()).hexdigest()[:12]
                slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")[:45] or "page"
                chunks.append({
                    "chunk_id": f"nba:web:{digest}:{slug}:{number:04d}",
                    "page_url": page["url"],
                    "title": page["title"],
                    "section": heading,
                    "retrieved_at": page["retrieved_at"],
                    "text": " ".join(window),
                })
                if start + size >= len(words):
                    break
    return chunks
