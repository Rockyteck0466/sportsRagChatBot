import argparse
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

DEFAULT_URL = "https://official.nba.com/rule-no-3-players-substitutes-and-coaches/"


async def main(url: str) -> None:
    page = await NbaScraper(settings).fetch_page(url)
    chunks = chunk_pages([page], settings)
    if not chunks:
        raise SystemExit("The page produced no usable chunks.")
    database = Database(settings.data_dir / "courtside.sqlite")
    old_ids = database.chunk_ids_for_pages([page["url"]])
    collection = VectorStore(settings).upsert(chunks, delete_ids=old_ids)
    database.upsert_corpus([page], chunks)
    print(
        f"Incrementally indexed {page['url']} as {len(chunks)} chunks "
        f"in Chroma collection {collection}."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Incrementally index one approved NBA.com page.")
    parser.add_argument("url", nargs="?", default=DEFAULT_URL)
    arguments = parser.parse_args()
    asyncio.run(main(arguments.url))
