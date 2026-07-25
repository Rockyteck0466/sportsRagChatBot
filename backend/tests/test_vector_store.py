from app.config import Settings
from app.vector_store import VectorStore


def test_vector_status_is_empty_before_first_build(tmp_path) -> None:
    store = VectorStore(Settings(data_dir=tmp_path))
    assert store.status()["vector_db"] == "ChromaDB"
    assert store.status()["vector_chunks"] == 0
    assert store.status()["vector_collection"] is None
