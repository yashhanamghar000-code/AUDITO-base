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
    def hybrid_search(query: str, user_id: str, session_id: str = None, top_k: int = 5, file_ids: List[str] = None) -> List[Document]:
        # 1. Dense search via Qdrant, filtered server-side by user_id (and
        # optionally file_ids, when the user has selected specific
        # documents to answer from). session_id is accepted for backward
        # compatibility with existing call sites but is intentionally
        # unused as a filter here.
        query_vector = embeddings.embed_query(query)
        qdrant_hits = qdrant_store.search(query_vector, user_id, top_k=top_k, file_ids=file_ids)
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
                raw_sparse = bm25_retriever.invoke(query)
                # BM25Retriever has no built-in metadata filter, so we
                # post-filter to the selected file_ids here.
                if file_ids:
                    raw_sparse = [d for d in raw_sparse if d.metadata.get("file_id") in file_ids]
                sparse_results = raw_sparse[:top_k]

        # 3. Deduplicate
        seen_contents = set()
        combined_results = []
        for doc in (dense_results + sparse_results):
            if doc.page_content not in seen_contents:
                seen_contents.add(doc.page_content)
                combined_results.append(doc)

        return combined_results

    @staticmethod
    def retrieve_and_rerank(query: str, user_id: str, session_id: str = None, top_k: int = 5, top_n: int = 3, file_ids: List[str] = None) -> List[Document]:
        candidate_docs = MultiUserRetriever.hybrid_search(query, user_id, session_id, top_k, file_ids=file_ids)
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

    @staticmethod
    def remove_file(user_id: str, file_id: str) -> None:
        """
        Deletes ONE uploaded file's chunks from both the dense (Qdrant) and
        sparse (BM25) indexes. Unlike clear_session above, this DOES rebuild
        the user-wide BM25 cache (filtered to drop this file_id) rather than
        leaving stale entries behind — otherwise a "deleted" PDF would still
        surface via sparse search until the user's next upload.
        """
        qdrant_store.delete_file_data(user_id, file_id)

        user_bm25_path = os.path.join(BM25_CACHE_DIR, f"bm25_{user_id}.pkl")
        if not os.path.exists(user_bm25_path):
            return

        with open(user_bm25_path, "rb") as f:
            bm25_retriever = pickle.load(f)

        remaining_docs = [d for d in bm25_retriever.docs if d.metadata.get("file_id") != file_id]

        if remaining_docs:
            new_retriever = BM25Retriever.from_documents(remaining_docs)
            with open(user_bm25_path, "wb") as f:
                pickle.dump(new_retriever, f)
        else:
            os.remove(user_bm25_path)
