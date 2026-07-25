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

    @property
    def state_path(self) -> Path:
        return self.config.chroma_dir / "active.json"

    def _active_name(self) -> str:
        if not self.state_path.exists():
            raise VectorStoreUnavailable("No active Chroma index exists. Run ingestion first.")
        return json.loads(self.state_path.read_text(encoding="utf-8"))["collection"]

    def build(self, chunks: list[dict[str, Any]]) -> str:
        chromadb, _ = self._dependencies()
        self.config.chroma_dir.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(self.config.chroma_dir))
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        name = f"{self.config.vector_collection_prefix}_{timestamp}"
        collection = client.create_collection(name=name, metadata={"hnsw:space": "cosine"})
        model = self._embedding_model()
        batch_size = 64
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            embeddings = model.encode_document(
                [item["text"] for item in batch],
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
        return name

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        chromadb, _ = self._dependencies()
        client = chromadb.PersistentClient(path=str(self.config.chroma_dir))
        collection = client.get_collection(self._active_name())
        embedding = self._embedding_model().encode_query(
            [query], normalize_embeddings=True, show_progress_bar=False
        ).tolist()
        result = collection.query(
            query_embeddings=embedding,
            n_results=limit,
            include=["documents", "metadatas", "distances"],
        )
        items: list[dict[str, Any]] = []
        for chunk_id, document, metadata, distance in zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
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
        return items

    def status(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"vector_db": "ChromaDB", "vector_collection": None, "vector_chunks": 0}
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        return {
            "vector_db": "ChromaDB",
            "vector_collection": state["collection"],
            "vector_chunks": state["chunks"],
            "embedding_model": state["embedding_model"],
        }
