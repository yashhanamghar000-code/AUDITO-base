import os
import uuid
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, MatchAny, PayloadSchemaType, SearchParams
)
from qdrant_client.http.exceptions import UnexpectedResponse
from dotenv import load_dotenv

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "audito_documents")

EMBEDDING_DIM = 384  # matches BAAI/bge-small-en-v1.5

client = QdrantClient(
    url=os.getenv("QDRANT_URL"), 
    api_key=os.getenv("QDRANT_API_KEY"),
    timeout=60.0  # Raises the read threshold from 5 seconds to 60 seconds
)


def ensure_collection():
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
    _ensure_payload_indexes()


def _ensure_payload_indexes():
    """
    Newer Qdrant server versions require an explicit payload index on any
    field used in a filter (e.g. user_id, session_id) — otherwise filtered
    search fails with 'Index required but not found'. This is idempotent:
    re-creating an existing index is a no-op on Qdrant's side, and we
    swallow the error if the client raises on a duplicate anyway.
    """
    for field_name in ("user_id", "session_id", "file_id"):
        try:
            client.create_payload_index(
                collection_name=COLLECTION_NAME,
                field_name=field_name,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except UnexpectedResponse:
            pass  # index already exists
        except Exception as e:
            print(f"[Qdrant] Warning: could not ensure index on '{field_name}': {e}")


ensure_collection()


def upsert_chunks(texts, vectors, metadatas, user_id: str, session_id: str, batch_size: int = 100):
    points = []
    for i, (text, vector, meta) in enumerate(zip(texts, vectors, metadatas)):
        payload = {"text": text, "user_id": user_id, "session_id": session_id, **meta}
        points.append(
            PointStruct(
                id=str(uuid.uuid4()),  # Qdrant requires UUID or int point IDs
                vector=vector,
                payload=payload,
            )
        )
    # Sent in batches, not one request for the whole file — a 971-chunk
    # upsert as a single call is exactly what timed out earlier on a large
    # report. Batches of 100 stay comfortably within the client's timeout.
    for i in range(0, len(points), batch_size):
        client.upsert(collection_name=COLLECTION_NAME, points=points[i:i + batch_size])


def search(query_vector, user_id: str, top_k: int = 5, file_ids: list[str] | None = None):
    # user_id is always required. file_ids is optional — pass a list of
    # UploadedFile ids (as strings) to restrict this search to only those
    # documents (Sam picking 2 of her 5 uploaded PDFs before asking).
    must = [FieldCondition(key="user_id", match=MatchValue(value=user_id))]
    if file_ids:
        must.append(FieldCondition(key="file_id", match=MatchAny(any=[str(f) for f in file_ids])))
    qfilter = Filter(must=must)

    # BUG FIX: Qdrant's default vector search is APPROXIMATE (HNSW). With no
    # filter (checkbox unticked -> search every file), the candidate pool is
    # large enough that the right chunks are found anyway. But once a
    # file_id filter narrows the pool (checkbox ticked -> restrict to 1-2
    # files), HNSW's graph walk can finish before it ever reaches a node
    # that satisfies the filter, silently returning few/zero points even
    # though the data is indexed correctly. This is exactly the "ticking
    # the box makes the answer worse" symptom.
    # Forcing exact=True does a real filtered scan instead of the
    # approximate graph walk, so filtered results are as reliable as
    # unfiltered ones. This only kicks in when a filter is actually
    # applied, so unfiltered (all-files) search keeps its normal ANN speed.
    search_params = SearchParams(exact=True) if file_ids else None

    response = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=qfilter,
        limit=top_k,
        search_params=search_params,
    )
    return response.points


def delete_session_data(user_id: str, session_id: str):
    """'Clear' operation — wipes only this user+session's vectors, not the whole collection."""
    qfilter = Filter(
        must=[
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key="session_id", match=MatchValue(value=session_id)),
        ]
    )
    # wait=True so this call doesn't return until Qdrant has actually applied
    # the delete — without it, a caller that immediately re-searches (or
    # deletes the Postgres row right after) can race ahead of the deletion.
    client.delete(collection_name=COLLECTION_NAME, points_selector=qfilter, wait=True)


def delete_file_data(user_id: str, file_id: str):
    """Wipes only the vectors belonging to ONE uploaded file (e.g. one of
    Sam's two Tata PDFs), leaving her other files in this session/chat intact."""
    qfilter = Filter(
        must=[
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key="file_id", match=MatchValue(value=file_id)),
        ]
    )
    # wait=True — see delete_session_data. This one matters even more: the
    # caller (MultiUserRetriever.remove_file) must know these vectors are
    # actually gone before the Postgres file record is deleted, or a
    # deleted file's chunks can be left orphaned in Qdrant forever with no
    # UI record left to ever delete them again.
    client.delete(collection_name=COLLECTION_NAME, points_selector=qfilter, wait=True)