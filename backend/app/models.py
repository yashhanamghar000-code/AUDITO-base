from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")


class Conversation(Base):
    """
    One row per chat thread, keyed by the same session_id the frontend
    generates client-side. This table is what makes a user's chat/upload
    history reappear after `docker compose down` + `up`, or a login from
    a different browser — previously nothing tracked which session_ids
    belonged to which user, so the JSON history files existed on disk but
    there was no way to look them back up.
    """
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, default="New Chat")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="conversations")
    messages = relationship("ChatMessage", back_populates="conversation", cascade="all, delete-orphan")
    files = relationship("UploadedFile", back_populates="conversation", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String, nullable=False)  # "user" | "assistant"
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    conversation = relationship("Conversation", back_populates="messages")


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    file_name = Column(String, nullable=False)
    status = Column(String, default="processing")  # processing | indexed | failed
    total_chunks_indexed = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    conversation = relationship("Conversation", back_populates="files")