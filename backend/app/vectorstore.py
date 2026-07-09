import os
import uuid
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, PayloadSchemaType
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
    for field_name in ("user_id", "session_id"):
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


def search(query_vector, user_id: str, top_k: int = 5):
    # user_id only — this is the deliberate change that makes documents
    # uploaded in ANY of a user's chats retrievable from EVERY chat, not
    # just the one they were originally uploaded in.
    qfilter = Filter(
        must=[
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
        ]
    )
    response = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=qfilter,
        limit=top_k,
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
    client.delete(collection_name=COLLECTION_NAME, points_selector=qfilter)
