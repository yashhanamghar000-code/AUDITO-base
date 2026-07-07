import os
import traceback
from app.celery_app import celery_app
from app.parser import MultiUserParser
from app.retriever import MultiUserRetriever


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
            return {
                "status": "failed",
                "detail": "Extraction failed: no content could be mapped.",
            }

        # 4. Trigger Embedding and Indexing stage
        self.update_state(state="EMBEDDING", meta={"stage": "embedding_and_indexing"})
        success = MultiUserRetriever.ingest_documents(final_chunks, user_id, session_id)

        if not success:
            return {"status": "failed", "detail": "Vector store ingestion failed."}

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
        return {"status": "failed", "detail": str(e)}