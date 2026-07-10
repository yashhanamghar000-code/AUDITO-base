import traceback
import os
import uuid

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

# App imports
from app.db import get_db, Base, engine
from app.models import User
from app.auth import get_current_user
from app.routes_auth import router as auth_router
from app.parser import MultiUserParser
from app.retriever import MultiUserRetriever
from app.workflow import build_workflow
from app.tasks import process_document_task
from app.celery_app import celery_app
from celery.result import AsyncResult
from app import history

app = FastAPI(title="AUDITO AI Multiuser RAG Engine")

# Database and Auth setup
Base.metadata.create_all(bind=engine)
app.include_router(auth_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

print("[System] Compiling Multi-Tenant LangGraph Workflow Architecture...")
rag_agent_executor = build_workflow()
print("[System] LangGraph Workflow Compiled successfully.")

TEMP_UPLOAD_DIR = "./temp_uploads"
os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)  # Create local scratch storage lane


@app.get("/")
async def health_check():
    return {
        "status": "Backend is active. Database and Workflow engines ready."
    }


# =====================================================
# CONVERSATIONS — lets the frontend rebuild its sidebar after a fresh
# login / restart from Postgres, instead of only from whatever session_ids
# happen to still be sitting in the current browser tab.
# =====================================================

@app.get("/api/conversations")
async def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return {"conversations": history.list_conversations(db, current_user.id)}


@app.get("/api/conversations/{session_id}/files")
async def list_conversation_files(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return {"files": history.list_files_for_conversation(db, current_user.id, session_id)}


@app.get("/api/chat/history/{user_id}/{session_id}")
async def get_chat_history(
    user_id: str,
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # A logged-in user can only ever read their own history, regardless of
    # what user_id shows up in the URL.
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to view this history.")

    return {"history": history.get_history(db, current_user.id, session_id)}


# =====================================================
# UPLOAD API
# =====================================================

@app.post("/api/upload")
async def upload_document(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Stages incoming payloads to local temp disk before issuing references
    down into Celery. Preserves Redis transport pipelines from hitting
    Upstash cloud size constraints.
    """
    user_id = str(current_user.id)
    try:
        print("\n==============================")
        print("UPLOAD REQUEST RECEIVED")
        print("==============================")
        print("User:", user_id)
        print("Session:", session_id)
        print("File:", file.filename)

        # Generate a distinct local filename string to avoid cross-user collisions
        unique_prefix = uuid.uuid4().hex
        safe_filename = f"{unique_prefix}_{file.filename}"
        local_file_path = os.path.join(TEMP_UPLOAD_DIR, safe_filename)

        # Stream file parts directly down onto server drive storage
        file_has_content = False
        with open(local_file_path, "wb") as buffer:
            while chunk := await file.read(65536):  # Read in 64KB chunks
                file_has_content = True
                buffer.write(chunk)

        if not file_has_content:
            if os.path.exists(local_file_path):
                os.remove(local_file_path)
            raise HTTPException(status_code=422, detail="Uploaded file is empty.")

        # Create the Postgres row UP FRONT (status="processing") so we have
        # a stable file_id before the worker even starts. This id gets
        # stamped onto every chunk this file produces, which is what lets
        # a specific PDF be deleted or selected-for-search later — even if
        # two uploads share the exact same filename (e.g. two Tata PDFs).
        file_record = history.create_pending_file(db, current_user.id, session_id, file.filename)
        file_id = str(file_record.id)

        # Dispatch ONLY paths and minimal metadata elements over Redis broker networks
        task = process_document_task.delay(
            local_file_path,
            file.filename,
            user_id,
            session_id,
            file_id,
        )

        print(f"Enqueued Celery task: {task.id} (file_id={file_id})\n")

        return {
            "status": "queued",
            "task_id": task.id,
            "file_id": file_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        print("\nUPLOAD FAILED")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/upload/status/{task_id}")
async def upload_status(task_id: str, current_user: User = Depends(get_current_user)):
    """Poll this from the frontend to drive the 'parsing stages' UI and know when a document is ready to query."""
    result = AsyncResult(task_id, app=celery_app)

    if result.state == "PENDING":
        return {"state": "PENDING", "detail": "Task not found or not yet started."}

    if result.state in ("PARSING", "EMBEDDING"):
        return {"state": result.state, "detail": (result.info or {}).get("stage")}

    if result.state == "SUCCESS":
        payload = result.result or {}
        return {"state": "SUCCESS", **payload}

    if result.state == "FAILURE":
        return {"state": "FAILURE", "detail": str(result.info)}

    return {"state": result.state}


# =====================================================
# CHAT API
# =====================================================

@app.post("/api/chat")
async def secure_chat(
    query: str = Form(...),
    session_id: str = Form(...),
    file_ids: str = Form(None),  # optional comma-separated UploadedFile ids from the sidebar checkboxes; omit/empty = search across all of this user's files
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    try:
        print("\n==============================")
        print("CHAT REQUEST RECEIVED")
        print("==============================")
        print("User:", user_id)
        print("Session:", session_id)
        print("Question:", query)

        selected_file_ids = [f.strip() for f in file_ids.split(",") if f.strip()] if file_ids else []
        if selected_file_ids:
            print("Restricted to file_ids:", selected_file_ids)

        initial_state = {
            "query": query,
            "user_id": user_id,
            "session_id": session_id,
            "selected_file_ids": selected_file_ids,
            "chat_history": [],
            "sub_queries": [],
            "retrieved_docs": [],
            "response": "",
            "follow_up_questions": [],
            "citations": [],
        }

        print("STEP 1 - Running LangGraph")

        final_output = rag_agent_executor.invoke(initial_state)

        print("LangGraph Finished")

        response_text = final_output.get(
            "response",
            "No answer generated."
        )

        print("Generated Response:")
        print(response_text[:300])

        # Persisted to Postgres — survives `docker compose down/up` and
        # logins from a different browser/device, unlike the old JSON file
        # keyed only by whatever session_id this browser tab still had.
        history.save_chat_turn(db, current_user.id, session_id, query, response_text)

        print("CHAT SUCCESS\n")

        return {
            "status": "success",
            "response": response_text,
            "sub_queries_used": final_output.get(
                "sub_queries",
                []
            ),
            "follow_up_questions": final_output.get(
                "follow_up_questions",
                []
            ),
            "citations": final_output.get(
                "citations",
                []
            ),
        }

    except Exception as e:
        print("\nCHAT FAILED")
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=f"LLM Orchestration Error: {str(e)}"
        )


# =====================================================
# SESSION CLEAR API
# =====================================================

@app.delete("/api/session/{session_id}")
async def clear_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    MultiUserRetriever.clear_session(user_id, session_id)
    history.delete_conversation(db, current_user.id, session_id)

    return {"status": "success", "detail": f"Session {session_id} cleared for user {user_id}."}


# =====================================================
# SINGLE DOCUMENT DELETE API
# =====================================================
# Lets a user remove ONE uploaded file (e.g. one of two Tata PDFs) without
# clearing the whole chat/session. Distinct from /api/session/{id} above,
# which wipes an entire conversation's documents + chat history at once.

@app.delete("/api/documents/{file_id}")
async def delete_document(
    file_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)

    try:
        file_id_int = int(file_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid file id.")

    # Ownership check up front — a user can never delete a file that isn't
    # theirs, even by guessing an id. This only reads/verifies, it doesn't
    # delete anything yet.
    owned_file = history.get_file(db, current_user.id, file_id_int)
    if not owned_file:
        raise HTTPException(status_code=404, detail="File not found.")

    # BUG FIX: this used to delete the Postgres row FIRST and wipe the
    # Qdrant/BM25 vectors AFTER. If the vector cleanup step ever failed
    # (timeout, a concurrent upload holding the BM25 cache file, a worker
    # restart, etc.), the Postgres row was already gone — the file
    # disappeared from the UI with no way to ever select or delete it
    # again, while its chunks stayed orphaned in Qdrant/BM25 forever and
    # kept surfacing in every future answer. ("I deleted it but the answer
    # still uses it.")
    #
    # Fixed order: wipe the actual vector data FIRST (and let it raise if
    # it fails), and only delete the Postgres row — which is what makes
    # the file vanish from the UI — once that has actually succeeded.
    try:
        MultiUserRetriever.remove_file(user_id, file_id)
    except Exception as e:
        print(f"[Delete] Failed to purge vectors for file_id={file_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Could not fully remove this file's data. Please try again.",
        )

    deleted = history.delete_file_record(db, current_user.id, file_id_int)
    if not deleted:
        raise HTTPException(status_code=404, detail="File not found.")

    return {
        "status": "success",
        "detail": f"'{deleted['file_name']}' removed.",
        "file_id": file_id,
        "session_id": deleted["session_id"],
    }