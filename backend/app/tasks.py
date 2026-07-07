import os
import traceback
from app.celery_app import celery_app
from app.parser import MultiUserParser
from app.retriever import MultiUserRetriever
from app.db import SessionLocal
from app import history


@celery_app.task(bind=True, name="app.tasks.process_document_task")
def process_document_task(self, file_path: str, file_name: str, user_id: str, session_id: str):
    try:
        self.update_state(state="PARSING", meta={"stage": "parsing_document"})

        # 1. Verify file exists on local scratch disk
        if not os.path.exists(file_path):
            return {
                "status": "failed",
                "detail": f"File not found on worker storage lane: {file_path}",
            }

        print(f"[Tasks] Handing off file path directly to parsing engine: {file_path}")

        # 2. Pass file_path directly down instead of loading raw bytes into RAM
        final_chunks = MultiUserParser.parse_uploaded_stream(
            file_path=file_path,  # Changed parameter from file_bytes to file_path
            file_name=file_name,
            user_id=user_id,
            session_id=session_id,
        )

        # 3. Safe disk clean up right after extraction is complete
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"[Tasks] Cleaned up temporary file: {file_path}")
        except Exception as cleanup_error:
            print(f"Warning: Failed to delete temporary file {file_path}: {cleanup_error}")

        if not final_chunks:
            _record_result(user_id, session_id, file_name, "failed")
            return {
                "status": "failed",
                "detail": "Extraction failed: no content could be mapped.",
            }

        # 4. Trigger Embedding and Indexing stage
        self.update_state(state="EMBEDDING", meta={"stage": "embedding_and_indexing"})
        success = MultiUserRetriever.ingest_documents(final_chunks, user_id, session_id)

        if not success:
            _record_result(user_id, session_id, file_name, "failed")
            return {"status": "failed", "detail": "Vector store ingestion failed."}

        # 5. Permanent record in Postgres — this is what makes the upload
        #    still show up in the user's history after a restart, instead
        #    of only existing as vectors in Qdrant with nothing pointing
        #    back at "this file belongs to this user's session".
        _record_result(user_id, session_id, file_name, "indexed", len(final_chunks))

        return {
            "status": "success",
            "total_chunks_indexed": len(final_chunks),
            "file_name": file_name,
        }

    except Exception as e:
        traceback.print_exc()
        # Fallback cleanup on unexpected crash
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass
        try:
            _record_result(user_id, session_id, file_name, "failed")
        except Exception:
            pass
        return {"status": "failed", "detail": str(e)}


def _record_result(user_id: str, session_id: str, file_name: str, status: str, total_chunks_indexed: int = 0) -> None:
    """
    The Celery worker is a separate process from FastAPI — there's no
    request-scoped `db: Session = Depends(get_db)` to reuse here, so we
    open and close a short-lived session directly against the same
    Postgres instance.
    """
    db = SessionLocal()
    try:
        history.record_uploaded_file(
            db,
            user_id=int(user_id),
            session_id=session_id,
            file_name=file_name,
            status=status,
            total_chunks_indexed=total_chunks_indexed,
        )
    finally:
        db.close()