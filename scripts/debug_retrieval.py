import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.database import Database  # noqa: E402
from app.retrieval import HybridRetriever  # noqa: E402
from app.vector_store import VectorStore  # noqa: E402


def _printable(value: str) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    return value.encode(encoding, errors="replace").decode(encoding)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--complete-sections", action="store_true")
    parser.add_argument("--groups", type=int, default=1)
    args = parser.parse_args()

    retriever = HybridRetriever(
        VectorStore(settings),
        Database(settings.data_dir / "courtside.sqlite"),
        settings,
    )
    results = retriever.search(
        args.question,
        args.query,
        result_limit=args.limit,
        complete_sections=args.complete_sections,
        group_count=args.groups,
    )
    for index, item in enumerate(results, 1):
        print(
            f"{index}. score={item['score']:.3f} "
            f"title={item['title']!r} section={item['section']!r}"
        )
        print(f"   url={item['page_url']}")
        print(f"   lanes={','.join(item.get('retrieval_sources', []))}")
        print(f"   matched={','.join(item.get('matched_terms', []))}")
        if item.get("matched_expected_questions"):
            print(f"   expected={item['matched_expected_questions'][:2]}")
        print(f"   text={_printable(item['text'][:280])}")


if __name__ == "__main__":
    main()
