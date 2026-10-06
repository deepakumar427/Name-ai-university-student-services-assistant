"""ChromaDB wrapper: store chunks with metadata, search by meaning, fetch a clause by section.

Embeddings: Chroma's built-in ONNX version of sentence-transformers `all-MiniLM-L6-v2`
(one of the two models the guide allows). It needs no PyTorch, which keeps the install
and the Docker image small.
"""

import logging
from functools import lru_cache

import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

from app.config import settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client() -> chromadb.ClientAPI:
    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(settings.chroma_dir))


@lru_cache(maxsize=1)
def get_collection():
    return _client().get_or_create_collection(
        name=settings.collection_name,
        embedding_function=DefaultEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"},
    )


def reset_cache() -> None:
    """Used by tests that switch data directories."""
    get_collection.cache_clear()
    _client.cache_clear()


def add_chunks(chunks: list[dict]) -> None:
    """Each chunk: {id, text, metadata}. Metadata values must be str/int/float/bool."""
    if not chunks:
        return
    get_collection().add(
        ids=[c["id"] for c in chunks],
        documents=[c["text"] for c in chunks],
        metadatas=[c["metadata"] for c in chunks],
    )


def delete_document(doc_id: str) -> None:
    get_collection().delete(where={"doc_id": doc_id})


def search(query: str, top_k: int | None = None) -> list[dict]:
    """Return the most similar chunks with similarity = 1 - cosine distance."""
    collection = get_collection()
    count = collection.count()
    if count == 0:
        return []
    res = collection.query(query_texts=[query], n_results=min(top_k or settings.retrieval_top_k, count))
    hits = []
    for chunk_id, text, meta, dist in zip(
        res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]
    ):
        hits.append({"chunk_id": chunk_id, "text": text, "metadata": meta, "score": round(1 - dist, 4)})
    return hits


def get_section_chunks(doc_id: str, section: str) -> list[dict]:
    """Fetch the chunks of one clause, e.g. the clause a rule in rule_registry came from."""
    res = get_collection().get(where={"$and": [{"doc_id": doc_id}, {"section": section}]})
    return [
        {"chunk_id": cid, "text": text, "metadata": meta, "score": 1.0}
        for cid, text, meta in zip(res["ids"], res["documents"], res["metadatas"])
    ]


def count() -> int:
    return get_collection().count()


def ping() -> bool:
    try:
        get_collection().count()
        return True
    except Exception as exc:  # noqa: BLE001 - health check must never raise
        log.warning("Chroma health check failed: %s", exc)
        return False
