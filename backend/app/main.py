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

        # Dispatch ONLY paths and minimal metadata elements over Redis broker networks
        task = process_document_task.delay(
            local_file_path,
            file.filename,
            user_id,
            session_id,
        )

        print(f"Enqueued Celery task: {task.id}\n")

        return {
            "status": "queued",
            "task_id": task.id,
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

        initial_state = {
            "query": query,
            "user_id": user_id,
            "session_id": session_id,
            "chat_history": [],
            "sub_queries": [],
            "retrieved_docs": [],
            "response": "",
            "follow_up_questions": [],
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