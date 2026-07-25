import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings


class VectorStoreUnavailable(RuntimeError):
    pass


class VectorStore:
    """Persistent Chroma store using local sentence-transformer embeddings."""

    def __init__(self, config: Settings):
        self.config = config
        self._model: Any = None
        self._client: Any = None
        self._collection: Any = None
        self._collection_name: str | None = None
        self._question_collection: Any = None
        self._question_collection_name: str | None = None

    def _dependencies(self) -> tuple[Any, Any]:
        try:
            import chromadb
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise VectorStoreUnavailable(
                "Install chromadb and sentence-transformers from backend/requirements.txt."
            ) from exc
        return chromadb, SentenceTransformer

    def _embedding_model(self) -> Any:
        if self._model is None:
            _, sentence_transformer = self._dependencies()
            self._model = sentence_transformer(self.config.embedding_model)
        return self._model

    def _persistent_client(self) -> Any:
        if self._client is None:
            chromadb, _ = self._dependencies()
            self.config.chroma_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.config.chroma_dir))
        return self._client

    def _active_collection(self) -> Any:
        name = self._active_name()
        if self._collection is None or self._collection_name != name:
            self._collection = self._persistent_client().get_collection(name)
            self._collection_name = name
        return self._collection

    def _active_question_collection(self) -> Any | None:
        if not self.state_path.exists():
            return None
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        name = state.get("question_collection")
        if not name:
            return None
        if (
            self._question_collection is None
            or self._question_collection_name != name
        ):
            self._question_collection = self._persistent_client().get_collection(name)
            self._question_collection_name = name
        return self._question_collection

    @staticmethod
    def _embedding_text(item: dict[str, Any]) -> str:
        return f"Title: {item['title']}\nSection: {item['section']}\n{item['text']}"

    @property
    def state_path(self) -> Path:
        return self.config.chroma_dir / "active.json"

    def _active_name(self) -> str:
        if not self.state_path.exists():
            raise VectorStoreUnavailable("No active Chroma index exists. Run ingestion first.")
        return json.loads(self.state_path.read_text(encoding="utf-8"))["collection"]

    def build(self, chunks: list[dict[str, Any]]) -> str:
        if not chunks:
            raise ValueError("At least one chunk is required.")
        client = self._persistent_client()
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        name = f"{self.config.vector_collection_prefix}_{timestamp}"
        collection = client.create_collection(name=name, metadata={"hnsw:space": "cosine"})
        model = self._embedding_model()
        batch_size = 64
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            embeddings = model.encode_document(
                [self._embedding_text(item) for item in batch],
                normalize_embeddings=True,
                show_progress_bar=False,
            ).tolist()
            collection.add(
                ids=[item["chunk_id"] for item in batch],
                documents=[item["text"] for item in batch],
                embeddings=embeddings,
                metadatas=[
                    {
                        "page_url": item["page_url"],
                        "title": item["title"],
                        "section": item["section"],
                        "retrieved_at": item["retrieved_at"],
                    }
                    for item in batch
                ],
            )
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"collection": name, "chunks": len(chunks), "embedding_model": self.config.embedding_model}),
            encoding="utf-8",
        )
        os.replace(temporary, self.state_path)
        self._collection = collection
        self._collection_name = name
        return name

    def upsert(self, chunks: list[dict[str, Any]], delete_ids: list[str] | None = None) -> str:
        """Update selected chunks in the active collection without rebuilding it."""
        if not chunks:
            raise ValueError("At least one chunk is required.")
        client = self._persistent_client()
        name = self._active_name()
        collection = client.get_collection(name)
        if delete_ids:
            collection.delete(ids=delete_ids)
        model = self._embedding_model()
        embeddings = model.encode_document(
            [self._embedding_text(item) for item in chunks],
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()
        collection.upsert(
            ids=[item["chunk_id"] for item in chunks],
            documents=[item["text"] for item in chunks],
            embeddings=embeddings,
            metadatas=[
                {
                    "page_url": item["page_url"],
                    "title": item["title"],
                    "section": item["section"],
                    "retrieved_at": item["retrieved_at"],
                }
                for item in chunks
            ],
        )
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "collection": name,
                    "chunks": collection.count(),
                    "embedding_model": self.config.embedding_model,
                }
            ),
            encoding="utf-8",
        )
        os.replace(temporary, self.state_path)
        self._collection = collection
        self._collection_name = name
        return name

    def build_question_index(self, records: list[dict[str, Any]]) -> str:
        """Build retrieval-only expected-question embeddings mapped to source chunks."""
        if not records:
            raise ValueError("At least one expected question is required.")
        client = self._persistent_client()
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        name = f"{self.config.vector_collection_prefix}_questions_{timestamp}"
        collection = client.create_collection(
            name=name,
            metadata={"hnsw:space": "cosine", "purpose": "retrieval_questions"},
        )
        model = self._embedding_model()
        batch_size = 64
        for start in range(0, len(records), batch_size):
            batch = records[start : start + batch_size]
            embeddings = model.encode_document(
                [record["question"] for record in batch],
                normalize_embeddings=True,
                show_progress_bar=False,
            ).tolist()
            collection.add(
                ids=[record["question_id"] for record in batch],
                embeddings=embeddings,
                metadatas=[
                    {
                        "chunk_id": record["chunk_id"],
                        "kind": record["kind"],
                    }
                    for record in batch
                ],
            )
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["question_collection"] = name
        state["question_records"] = len(records)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, self.state_path)
        self._question_collection = collection
        self._question_collection_name = name
        return name

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        return self.search_many([query], limit)[0]

    def search_many(
        self,
        queries: list[str],
        limit: int,
    ) -> list[list[dict[str, Any]]]:
        """Embed and search query variants in one batch to reduce latency."""
        if not queries:
            return []
        collection = self._active_collection()
        embeddings = self._embedding_model().encode_query(
            queries, normalize_embeddings=True, show_progress_bar=False
        ).tolist()
        result = collection.query(
            query_embeddings=embeddings,
            n_results=limit,
            include=["documents", "metadatas", "distances"],
        )
        batches: list[list[dict[str, Any]]] = []
        for ids, documents, metadatas, distances in zip(
            result["ids"],
            result["documents"],
            result["metadatas"],
            result["distances"],
        ):
            items: list[dict[str, Any]] = []
            for chunk_id, document, metadata, distance in zip(
                ids,
                documents,
                metadatas,
                distances,
            ):
                items.append({
                    "chunk_id": chunk_id,
                    "text": document,
                    "page_url": metadata["page_url"],
                    "title": metadata["title"],
                    "section": metadata["section"],
                    "retrieved_at": metadata["retrieved_at"],
                    "score": max(0.0, 1.0 - float(distance)),
                })
            batches.append(items)
        return batches

    def search_questions_many(
        self,
        queries: list[str],
        limit: int,
    ) -> list[list[dict[str, Any]]]:
        """Search synthetic questions; callers must hydrate original evidence."""
        if not queries:
            return []
        collection = self._active_question_collection()
        if collection is None:
            return [[] for _ in queries]
        embeddings = self._embedding_model().encode_query(
            queries,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()
        result = collection.query(
            query_embeddings=embeddings,
            n_results=min(limit * 3, collection.count()),
            include=["metadatas", "distances"],
        )
        batches: list[list[dict[str, Any]]] = []
        for ids, metadatas, distances in zip(
            result["ids"],
            result["metadatas"],
            result["distances"],
        ):
            batches.append([
                {
                    "question_id": question_id,
                    "chunk_id": metadata["chunk_id"],
                    "kind": metadata["kind"],
                    "score": max(0.0, 1.0 - float(distance)),
                }
                for question_id, metadata, distance in zip(
                    ids,
                    metadatas,
                    distances,
                )
            ])
        return batches

    def status(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "vector_db": "ChromaDB",
                "vector_collection": None,
                "vector_chunks": 0,
                "question_collection": None,
                "question_vectors": 0,
            }
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        return {
            "vector_db": "ChromaDB",
            "vector_collection": state["collection"],
            "vector_chunks": state["chunks"],
            "embedding_model": state["embedding_model"],
            "question_collection": state.get("question_collection"),
            "question_vectors": state.get("question_records", 0),
        }
