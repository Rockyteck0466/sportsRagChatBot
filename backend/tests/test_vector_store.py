import time
from concurrent.futures import ThreadPoolExecutor

from app.config import Settings
from app.vector_store import VectorStore


def test_embedding_model_prefers_cached_local_files(tmp_path) -> None:
    store = VectorStore(Settings(data_dir=tmp_path))
    calls: list[dict] = []
    model = object()

    def fake_sentence_transformer(name: str, **kwargs):
        calls.append({"name": name, **kwargs})
        return model

    store._dependencies = lambda: (  # type: ignore[method-assign]
        object,
        fake_sentence_transformer,
    )

    assert store._embedding_model() is model
    assert calls == [{
        "name": store.config.embedding_model,
        "local_files_only": True,
    }]


def test_embedding_model_downloads_when_cache_is_missing(tmp_path) -> None:
    store = VectorStore(Settings(data_dir=tmp_path))
    calls: list[dict] = []
    model = object()

    def fake_sentence_transformer(name: str, **kwargs):
        calls.append({"name": name, **kwargs})
        if kwargs.get("local_files_only"):
            raise OSError("not cached")
        return model

    store._dependencies = lambda: (  # type: ignore[method-assign]
        object,
        fake_sentence_transformer,
    )

    assert store._embedding_model() is model
    assert calls == [
        {
            "name": store.config.embedding_model,
            "local_files_only": True,
        },
        {"name": store.config.embedding_model},
    ]


def test_warm_loads_source_question_and_embedding_indexes(tmp_path) -> None:
    store = VectorStore(Settings(data_dir=tmp_path))
    calls: list[str] = []
    store._active_collection = (  # type: ignore[method-assign]
        lambda: calls.append("source")
    )
    store._active_question_collection = (  # type: ignore[method-assign]
        lambda: calls.append("questions")
    )
    store._embedding_model = (  # type: ignore[method-assign]
        lambda: calls.append("embedding")
    )

    store.warm()

    assert calls == ["source", "questions", "embedding"]


def test_vector_status_is_empty_before_first_build(tmp_path) -> None:
    store = VectorStore(Settings(data_dir=tmp_path))
    assert store.status()["vector_db"] == "ChromaDB"
    assert store.status()["vector_chunks"] == 0
    assert store.status()["vector_collection"] is None


def test_persistent_client_initialization_is_thread_safe(tmp_path) -> None:
    store = VectorStore(Settings(data_dir=tmp_path))
    created: list[object] = []

    class FakeChroma:
        @staticmethod
        def PersistentClient(*, path: str) -> object:
            time.sleep(0.01)
            client = object()
            created.append(client)
            return client

    store._dependencies = lambda: (FakeChroma, object)  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=8) as executor:
        clients = list(executor.map(lambda _item: store._persistent_client(), range(16)))

    assert len(created) == 1
    assert all(client is created[0] for client in clients)


def test_combined_source_and_question_search_embeds_queries_once(tmp_path) -> None:
    store = VectorStore(Settings(data_dir=tmp_path))

    class Encodings(list):
        def tolist(self) -> list[list[float]]:
            return list(self)

    class Model:
        calls = 0

        def encode_query(self, queries, **kwargs):
            self.calls += 1
            return Encodings([[1.0, 0.0] for _ in queries])

    class SourceCollection:
        def query(self, *, query_embeddings, n_results, include):
            count = len(query_embeddings)
            return {
                "ids": [["chunk-1"] for _ in range(count)],
                "documents": [["Original source text"] for _ in range(count)],
                "metadatas": [
                    [
                        {
                            "page_url": "https://www.nba.com/news/about",
                            "title": "About",
                            "section": "League",
                            "retrieved_at": "2026-07-25T00:00:00Z",
                        }
                    ]
                    for _ in range(count)
                ],
                "distances": [[0.1] for _ in range(count)],
            }

    class QuestionCollection:
        def count(self) -> int:
            return 10

        def query(self, *, query_embeddings, n_results, include):
            count = len(query_embeddings)
            return {
                "ids": [["question-1"] for _ in range(count)],
                "metadatas": [
                    [{"chunk_id": "chunk-1", "kind": "paraphrase"}]
                    for _ in range(count)
                ],
                "distances": [[0.05] for _ in range(count)],
            }

    model = Model()
    store._embedding_model = lambda: model  # type: ignore[method-assign]
    store._active_collection = lambda: SourceCollection()  # type: ignore[method-assign]
    store._active_question_collection = (  # type: ignore[method-assign]
        lambda: QuestionCollection()
    )

    source, questions = store.search_source_and_questions_many(
        ["first query", "second query"],
        3,
        3,
    )

    assert model.calls == 1
    assert len(source) == 2
    assert len(questions) == 2
    assert source[0][0]["chunk_id"] == "chunk-1"
    assert questions[0][0]["question_id"] == "question-1"
