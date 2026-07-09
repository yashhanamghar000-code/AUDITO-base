"""
Shared read/write helpers for conversation/chat/upload history, backed by
the Postgres you already have running — not a second SQLite file, which
would just split your data across two unsynced databases.

Used from two places that don't share a request lifecycle:
- FastAPI routes in main.py (get a `db: Session` via the `get_db` dependency)
- The Celery worker task in tasks.py (opens its own short-lived session
  with `SessionLocal()`, since a worker process has no FastAPI request to
  hang a dependency off of)
"""
from sqlalchemy.orm import Session
from app.models import Conversation, ChatMessage, UploadedFile


def get_or_create_conversation(db: Session, user_id: int, session_id: str, title_hint: str = "New Chat") -> Conversation:
    conv = db.query(Conversation).filter(Conversation.session_id == session_id).first()
    if conv:
        if conv.user_id != user_id:
            raise PermissionError("session_id belongs to a different user")
        return conv

    conv = Conversation(session_id=session_id, user_id=user_id, title=title_hint[:60])
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def save_chat_turn(db: Session, user_id: int, session_id: str, query: str, response: str) -> None:
    conv = get_or_create_conversation(db, user_id, session_id, title_hint=query)
    db.add(ChatMessage(conversation_id=conv.id, user_id=user_id, role="user", message=query))
    db.add(ChatMessage(conversation_id=conv.id, user_id=user_id, role="assistant", message=response))
    db.commit()


def get_history(db: Session, user_id: int, session_id: str) -> list[dict]:
    conv = db.query(Conversation).filter(
        Conversation.session_id == session_id, Conversation.user_id == user_id
    ).first()
    if not conv:
        return []
    return [
        {"sender": "user" if m.role == "user" else "bot", "text": m.message}
        for m in sorted(conv.messages, key=lambda m: m.created_at)
    ]


def list_conversations(db: Session, user_id: int) -> list[dict]:
    """Powers the frontend's sidebar rebuild after login/restart."""
    convs = (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .all()
    )
    return [
        {
            "session_id": c.session_id,
            "title": c.title,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in convs
    ]


def list_files_for_conversation(db: Session, user_id: int, session_id: str) -> list[dict]:
    """
    Powers the document sidebar rebuild after a restart — previously only
    chat messages reappeared after login, uploaded documents did not,
    because nothing exposed the UploadedFile rows this table already had.
    """
    conv = db.query(Conversation).filter(
        Conversation.session_id == session_id, Conversation.user_id == user_id
    ).first()
    if not conv:
        return []
    return [
        {
            "id": str(f.id),
            "name": f.file_name,
            "status": f.status,
            "total_chunks_indexed": f.total_chunks_indexed,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in sorted(conv.files, key=lambda f: f.created_at)
    ]


def record_uploaded_file(db: Session, user_id: int, session_id: str, file_name: str, status: str, total_chunks_indexed: int = 0) -> None:
    conv = get_or_create_conversation(db, user_id, session_id, title_hint=file_name)
    db.add(UploadedFile(
        conversation_id=conv.id,
        user_id=user_id,
        file_name=file_name,
        status=status,
        total_chunks_indexed=total_chunks_indexed,
    ))
    db.commit()


def create_pending_file(db: Session, user_id: int, session_id: str, file_name: str) -> UploadedFile:
    """
    Creates the UploadedFile row BEFORE Celery processes it (previously this
    only happened after processing finished). This gives us a stable
    Postgres id up front — that id is what gets stamped onto every chunk
    this file produces (as `file_id` in Qdrant/BM25 metadata), which is what
    lets a user delete or select ONE specific PDF later, even if two PDFs
    share the exact same filename (e.g. two "annual_report.pdf" uploads).
    """
    conv = get_or_create_conversation(db, user_id, session_id, title_hint=file_name)
    f = UploadedFile(
        conversation_id=conv.id,
        user_id=user_id,
        file_name=file_name,
        status="processing",
        total_chunks_indexed=0,
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


def update_file_status(db: Session, file_id: int, status: str, total_chunks_indexed: int = 0) -> None:
    """Called by the Celery worker (tasks.py) once parsing/embedding finishes."""
    f = db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
    if f:
        f.status = status
        f.total_chunks_indexed = total_chunks_indexed
        db.commit()


def get_file(db: Session, user_id: int, file_id: int) -> UploadedFile | None:
    """Ownership-checked lookup — a user can only ever fetch their own files."""
    return (
        db.query(UploadedFile)
        .join(Conversation, UploadedFile.conversation_id == Conversation.id)
        .filter(UploadedFile.id == file_id, Conversation.user_id == user_id)
        .first()
    )


def delete_file_record(db: Session, user_id: int, file_id: int) -> dict | None:
    """Deletes the Postgres row for one file. Returns {file_name, session_id}
    (captured before deletion) so the caller can still use them after commit,
    or None if the file didn't exist or didn't belong to this user."""
    f = get_file(db, user_id, file_id)
    if not f:
        return None
    info = {"file_name": f.file_name, "session_id": f.conversation.session_id}
    db.delete(f)
    db.commit()
    return info


def delete_conversation(db: Session, user_id: int, session_id: str) -> None:
    conv = db.query(Conversation).filter(
        Conversation.session_id == session_id, Conversation.user_id == user_id
    ).first()
    if conv:
        db.delete(conv)  # cascades to messages + files
        db.commit()