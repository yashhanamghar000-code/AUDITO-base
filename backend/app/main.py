import traceback
import os
import json

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware

# App imports
from app.db import get_db, Base, engine
from app.models import User
from app.auth import get_current_user
from app.routes_auth import router as auth_router
from app.parser import MultiUserParser
from app.retriever import MultiUserRetriever
from app.workflow import build_workflow

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

HISTORY_DIR = "./chat_history"
os.makedirs(HISTORY_DIR, exist_ok=True)


@app.get("/")
async def health_check():
    return {
        "status": "Backend is active. Database and Workflow engines ready."
    }


@app.get("/api/chat/history/{user_id}/{session_id}")
async def get_chat_history(user_id: str, session_id: str):
    history_file = os.path.join(HISTORY_DIR, f"{user_id}_{session_id}.json")

    if os.path.exists(history_file):
        with open(history_file, "r") as f:
            return {"history": json.load(f)}

    return {"history": []}


# =====================================================
# UPLOAD API
# =====================================================

@app.post("/api/upload")
async def upload_document(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    try:
        print("\n==============================")
        print("UPLOAD REQUEST RECEIVED")
        print("==============================")
        print("User:", user_id)
        print("Session:", session_id)
        print("File:", file.filename)

        print("STEP 1 - Reading file")
        file_bytes = await file.read()
        print("File read complete")

        print("STEP 2 - Parsing document")
        final_chunks = MultiUserParser.parse_uploaded_stream(
            file_bytes=file_bytes,
            file_name=file.filename,
            user_id=user_id,
            session_id=session_id,
        )
        print("Parsing complete")

        if not final_chunks:
            raise HTTPException(
                status_code=422,
                detail="Extraction failed: No content could be mapped."
            )

        print(f"Chunks created: {len(final_chunks)}")

        print("STEP 3 - Saving to vector store")
        success = MultiUserRetriever.ingest_documents(
            final_chunks,
            user_id,
            session_id
        )

        print("Ingestion completed")

        if not success:
            raise HTTPException(
                status_code=500,
                detail="Database ingestion failed."
            )

        print("UPLOAD FINISHED\n")

        return {
            "status": "success",
            "total_chunks_indexed": len(final_chunks),
        }

    except HTTPException:
        raise
    except Exception as e:
        print("\nUPLOAD FAILED")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================
# CHAT API
# =====================================================

@app.post("/api/chat")
async def secure_chat(
    query: str = Form(...),
    session_id: str = Form(...),
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
            "response": ""
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

        history_file = os.path.join(
            HISTORY_DIR,
            f"{user_id}_{session_id}.json"
        )

        history = []

        if os.path.exists(history_file):
            with open(history_file, "r") as f:
                history = json.load(f)

        history.append({
            "sender": "user",
            "text": query
        })

        history.append({
            "sender": "bot",
            "text": response_text
        })

        with open(history_file, "w") as f:
            json.dump(history, f)

        print("CHAT SUCCESS\n")

        return {
            "status": "success",
            "response": response_text,
            "sub_queries_used": final_output.get(
                "sub_queries",
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
    current_user: User = Depends(get_current_user),
):
    user_id = str(current_user.id)
    MultiUserRetriever.clear_session(user_id, session_id)

    history_file = os.path.join(HISTORY_DIR, f"{user_id}_{session_id}.json")
    if os.path.exists(history_file):
        os.remove(history_file)

    return {"status": "success", "detail": f"Session {session_id} cleared for user {user_id}."}
