import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.database import Database  # noqa: E402
from app.question_index import build_expected_question_index  # noqa: E402
from app.vector_store import VectorStore  # noqa: E402


async def main() -> None:
    """Generate expected questions once, then build retrieval-only indexes."""
    database = Database(settings.data_dir / "courtside.sqlite")
    result = await build_expected_question_index(
        database,
        VectorStore(settings),
        settings,
    )
    manifest_path = settings.data_dir / "question-index.json"
    temporary = manifest_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(result, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, manifest_path)
    print(
        f"Indexed {result['questions']} expected questions for "
        f"{result['source_chunks']} original evidence chunks in "
        f"{result['collection']}."
    )
    print(
        f"Generation batches with fallbacks: {len(result['errors'])}. "
        "Expected questions are retrieval hints only, never evidence."
    )


if __name__ == "__main__":
    asyncio.run(main())
