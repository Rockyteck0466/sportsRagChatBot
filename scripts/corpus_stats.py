import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402


def _size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> None:
    database_path = settings.data_dir / "courtside.sqlite"
    if not database_path.exists():
        raise SystemExit("No local corpus database exists. Run ingestion first.")
    with sqlite3.connect(database_path) as connection:
        pages = connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        raw_characters = connection.execute(
            "SELECT COALESCE(SUM(LENGTH(text)), 0) FROM pages"
        ).fetchone()[0]
        indexed_rows = connection.execute("SELECT text FROM chunks")
        indexed_characters = 0
        indexed_words = 0
        for (text,) in indexed_rows:
            indexed_characters += len(text)
            indexed_words += len(re.findall(r"\w+", text))

    markdown_files = (
        list(settings.markdown_dir.glob("*.md"))
        if settings.markdown_dir.exists()
        else []
    )
    # Four characters per token is a transparent planning estimate. Actual API
    # input usage varies with the selected evidence and model tokenizer.
    estimated_index_tokens = round(indexed_characters / 4)
    print(f"Indexed pages: {pages}")
    print(f"Indexed chunks: {chunks}")
    print(f"Saved Markdown files: {len(markdown_files)}")
    print(f"Raw scraped characters: {raw_characters:,}")
    print(f"Indexed words: {indexed_words:,}")
    print(f"Estimated indexed tokens: {estimated_index_tokens:,}")
    print(f"Markdown size: {_size(settings.markdown_dir) / 1_048_576:.2f} MiB")
    print(f"SQLite size: {_size(database_path) / 1_048_576:.2f} MiB")
    print(f"Chroma size: {_size(settings.chroma_dir) / 1_048_576:.2f} MiB")
    print(f"Total data size: {_size(settings.data_dir) / 1_048_576:.2f} MiB")


if __name__ == "__main__":
    main()
