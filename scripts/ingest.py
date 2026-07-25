import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.chunker import chunk_pages  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Database  # noqa: E402
from app.scraper import NbaScraper  # noqa: E402
from app.vector_store import VectorStore  # noqa: E402


async def main() -> None:
    pages, errors = await NbaScraper(settings).crawl(settings.nba_seed_url, settings.crawl_max_pages)
    chunks = chunk_pages(pages, settings)
    if not chunks:
        raise SystemExit("No usable NBA.com content was indexed.")
    collection = VectorStore(settings).build(chunks)
    Database(settings.data_dir / "courtside.sqlite").replace_corpus(pages, chunks)
    print(
        f"Stored {len(pages)} Markdown pages and indexed {len(chunks)} chunks "
        f"in Chroma collection {collection}; skipped {len(errors)}."
    )


if __name__ == "__main__":
    asyncio.run(main())
