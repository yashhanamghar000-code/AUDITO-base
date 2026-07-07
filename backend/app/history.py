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


def delete_conversation(db: Session, user_id: int, session_id: str) -> None:
    conv = db.query(Conversation).filter(
        Conversation.session_id == session_id, Conversation.user_id == user_id
    ).first()
    if conv:
        db.delete(conv)  # cascades to messages + files
        db.commit()