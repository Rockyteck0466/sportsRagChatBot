import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.chunker import chunk_pages  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Database  # noqa: E402
from app.vector_store import VectorStore  # noqa: E402


def main() -> None:
    """Rebuild cleaned SQLite/Chroma chunks from active pages without scraping."""
    database = Database(settings.data_dir / "courtside.sqlite")
    pages = database.all_pages()
    if not pages:
        raise SystemExit("No local pages exist. Run ingestion before reindexing.")
    chunks = chunk_pages(pages, settings)
    if not chunks:
        raise SystemExit("The local pages produced no usable cleaned chunks.")
    collection = VectorStore(settings).build(chunks)
    database.replace_corpus(pages, chunks)
    unique_sources = len({chunk["page_url"] for chunk in chunks})
    print(
        f"Reindexed {len(pages)} scraped pages into {len(chunks)} cleaned chunks "
        f"from {unique_sources} unique sources in Chroma collection {collection}."
    )
    print("No web requests were made and no ScraperAPI credits were used.")


if __name__ == "__main__":
    main()
