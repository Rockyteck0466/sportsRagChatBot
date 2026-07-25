import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.chunker import chunk_pages  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Database  # noqa: E402
from app.question_index import build_expected_question_index  # noqa: E402
from app.scraper import NbaScraper  # noqa: E402
from app.vector_store import VectorStore  # noqa: E402


async def main() -> None:
    pages, errors = await NbaScraper(settings).crawl(settings.nba_seed_url, settings.crawl_max_pages)
    chunks = chunk_pages(pages, settings)
    if not chunks:
        raise SystemExit("No usable NBA.com content was indexed.")
    vector_store = VectorStore(settings)
    collection = vector_store.build(chunks)
    database = Database(settings.data_dir / "courtside.sqlite")
    database.replace_corpus(pages, chunks)
    print(
        f"Stored {len(pages)} Markdown pages and indexed {len(chunks)} chunks "
        f"in Chroma collection {collection}; skipped {len(errors)}."
    )
    try:
        question_result = await build_expected_question_index(
            database,
            vector_store,
            settings,
        )
        print(
            f"Prepared {question_result['questions']} retrieval-only expected "
            f"questions for {question_result['source_chunks']} source chunks."
        )
    except Exception as exc:
        print(
            "The source index is usable, but expected-question generation failed: "
            f"{type(exc).__name__}. Run scripts\\build_question_index.py to retry.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    asyncio.run(main())
