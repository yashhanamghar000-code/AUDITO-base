import os
import pickle
from typing import List
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document

from app import config
from app import vectorstore as qdrant_store

BM25_CACHE_DIR = config.BM25_CACHE_DIR
os.makedirs(BM25_CACHE_DIR, exist_ok=True)

print("[System] Booting Embedding Model... (This might take a moment on first run)")
embeddings = HuggingFaceEmbeddings(model_name=config.EMBEDDING_MODEL)
reranker = config.reranker  # reuse the single instance already loaded in config.py
print("[System] Retrieval Engines Active.")


class MultiUserRetriever:

    @staticmethod
    def ingest_documents(chunks: List[Document], user_id: str, session_id: str) -> bool:
        if not chunks:
            return False

        texts = [doc.page_content for doc in chunks]
        metadatas = [doc.metadata for doc in chunks]
        vectors = embeddings.embed_documents(texts)

        qdrant_store.upsert_chunks(texts, vectors, metadatas, user_id, session_id)

        # BM25 sparse index — cached per user+session on disk
        bm25_retriever = BM25Retriever.from_documents(chunks)
        session_bm25_path = os.path.join(BM25_CACHE_DIR, f"bm25_{user_id}_{session_id}.pkl")
        with open(session_bm25_path, "wb") as f:
            pickle.dump(bm25_retriever, f)

        print(f"[DEBUG] Ingested {len(chunks)} chunks for user={user_id} session={session_id}")
        return True

    @staticmethod
    def hybrid_search(query: str, user_id: str, session_id: str, top_k: int = 5) -> List[Document]:
        # 1. Dense search via Qdrant, filtered server-side by user_id + session_id
        query_vector = embeddings.embed_query(query)
        qdrant_hits = qdrant_store.search(query_vector, user_id, session_id, top_k=top_k)
        dense_results = [
            Document(page_content=hit.payload.get("text", ""), metadata=hit.payload)
            for hit in qdrant_hits
        ]

        # 2. Sparse search via isolated BM25 cache
        sparse_results = []
        session_bm25_path = os.path.join(BM25_CACHE_DIR, f"bm25_{user_id}_{session_id}.pkl")
        if os.path.exists(session_bm25_path):
            with open(session_bm25_path, "rb") as f:
                bm25_retriever = pickle.load(f)
                sparse_results = bm25_retriever.invoke(query)[:top_k]

        # 3. Deduplicate
        seen_contents = set()
        combined_results = []
        for doc in (dense_results + sparse_results):
            if doc.page_content not in seen_contents:
                seen_contents.add(doc.page_content)
                combined_results.append(doc)

        return combined_results

    @staticmethod
    def retrieve_and_rerank(query: str, user_id: str, session_id: str, top_k: int = 5, top_n: int = 3) -> List[Document]:
        candidate_docs = MultiUserRetriever.hybrid_search(query, user_id, session_id, top_k)
        if not candidate_docs:
            return []

        pairs = [[query, doc.page_content] for doc in candidate_docs]
        rerank_scores = reranker.predict(pairs)
        scored_docs = sorted(zip(rerank_scores, candidate_docs), key=lambda x: x[0], reverse=True)
        return [doc for score, doc in scored_docs[:top_n]]

    @staticmethod
    def clear_session(user_id: str, session_id: str) -> None:
        """Wipes this user+session's vectors — useful for a 'delete document' or 'clear chat' feature."""
        qdrant_store.delete_session_data(user_id, session_id)
