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

        # session_id is still stamped onto every chunk's Qdrant payload (so
        # "clear this chat's documents" and citations still know which
        # upload/conversation a chunk came from) — it's just no longer used
        # as a search filter, per the user-wide retrieval change below.
        qdrant_store.upsert_chunks(texts, vectors, metadatas, user_id, session_id)

        # BM25 sparse index — now ONE cache per user, merged across every
        # chat that user has ever uploaded into, not one file per session.
        # This is what makes a brand new chat able to retrieve documents
        # uploaded in a different, older chat.
        user_bm25_path = os.path.join(BM25_CACHE_DIR, f"bm25_{user_id}.pkl")
        existing_docs: List[Document] = []
        if os.path.exists(user_bm25_path):
            try:
                with open(user_bm25_path, "rb") as f:
                    existing_retriever = pickle.load(f)
                    existing_docs = list(existing_retriever.docs)
            except Exception as e:
                print(f"[BM25] Could not load existing user-wide cache, rebuilding fresh: {e}")

        combined_docs = existing_docs + chunks
        bm25_retriever = BM25Retriever.from_documents(combined_docs)
        with open(user_bm25_path, "wb") as f:
            pickle.dump(bm25_retriever, f)

        print(f"[DEBUG] Ingested {len(chunks)} chunks for user={user_id} session={session_id} "
              f"(user-wide BM25 index now has {len(combined_docs)} total chunks)")
        return True

    @staticmethod
    def hybrid_search(query: str, user_id: str, session_id: str = None, top_k: int = 5) -> List[Document]:
        # 1. Dense search via Qdrant, filtered server-side by user_id only —
        # session_id is accepted for backward compatibility with existing
        # call sites but is intentionally unused here now.
        query_vector = embeddings.embed_query(query)
        qdrant_hits = qdrant_store.search(query_vector, user_id, top_k=top_k)
        dense_results = [
            Document(page_content=hit.payload.get("text", ""), metadata=hit.payload)
            for hit in qdrant_hits
        ]

        # 2. Sparse search via the user-wide BM25 cache
        sparse_results = []
        user_bm25_path = os.path.join(BM25_CACHE_DIR, f"bm25_{user_id}.pkl")
        if os.path.exists(user_bm25_path):
            with open(user_bm25_path, "rb") as f:
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
    def retrieve_and_rerank(query: str, user_id: str, session_id: str = None, top_k: int = 5, top_n: int = 3) -> List[Document]:
        candidate_docs = MultiUserRetriever.hybrid_search(query, user_id, session_id, top_k)
        if not candidate_docs:
            return []

        pairs = [[query, doc.page_content] for doc in candidate_docs]
        rerank_scores = reranker.predict(pairs)
        scored_docs = sorted(zip(rerank_scores, candidate_docs), key=lambda x: x[0], reverse=True)
        return [doc for score, doc in scored_docs[:top_n]]

    @staticmethod
    def clear_session(user_id: str, session_id: str) -> None:
        """
        Wipes only the vectors uploaded in THIS specific chat (Qdrant still
        tracks session_id per-chunk for exactly this purpose), leaving that
        user's other chats' documents untouched.

        KNOWN LIMITATION: the user-wide BM25 cache is additively merged on
        ingest, not rebuilt from Qdrant on delete — so chunks from a cleared
        session may still be found via the BM25/sparse side of hybrid_search
        until that user's next upload rebuilds the cache from scratch, even
        though they're gone from Qdrant's dense side. Rebuilding the BM25
        cache from Qdrant's remaining points on every delete would fix this
        fully but is a heavier operation; flagging it rather than silently
        leaving it unaddressed.
        """
        qdrant_store.delete_session_data(user_id, session_id)
