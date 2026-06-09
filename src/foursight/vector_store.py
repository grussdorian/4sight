from __future__ import annotations
from .models import Grounding


class FakeVector:
    def add(self, doc_id: str, text: str, metadata: dict | None = None) -> None: pass
    def query(self, text: str, k: int = 3) -> list[Grounding]: return []
    def query_texts(self, text: str, k: int = 3) -> list[str]: return []


class ChromaVectorStore:
    def __init__(self, collection: str = "corporate") -> None:
        import chromadb
        self._col = chromadb.EphemeralClient().get_or_create_collection(collection)

    def add(self, doc_id: str, text: str, metadata: dict | None = None) -> None:
        self._col.add(ids=[doc_id], documents=[text], metadatas=[metadata])

    def query(self, text: str, k: int = 3) -> list[Grounding]:
        res = self._col.query(query_texts=[text], n_results=k)
        ids = res.get("ids", [[]])[0]
        dists = res.get("distances", [[0.0] * len(ids)])[0]
        return [Grounding(doc=i, chunk=0, score=float(1.0 - d)) for i, d in zip(ids, dists)]

    def query_texts(self, text: str, k: int = 3) -> list[str]:
        res = self._col.query(query_texts=[text], n_results=k)
        docs = res.get("documents", [[]])[0]
        return [d for d in docs if d]
