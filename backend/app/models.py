from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedColumn, mapped_column, relationship

from .config import settings
from .constants import (
    Channel,
    DocumentStatus,
    Priority,
    SenderRole,
    Sentiment,
    TicketStatus,
    UserRole,
    WorkspaceRole,
)

IS_POSTGRES = settings.database_url.startswith("postgresql")
JSONType = JSONB if IS_POSTGRES else JSON

if IS_POSTGRES:
    from pgvector.sqlalchemy import Vector
else:
    Vector = None  # type: ignore[assignment]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_column() -> MappedColumn:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def refreshed_column() -> MappedColumn:
    return mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


def embedding_type():
    if IS_POSTGRES and Vector is not None:
        return Vector(settings.embedding_dim)
    return JSON


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default=UserRole.AGENT.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = now_column()

    tickets: Mapped[list[Ticket]] = relationship(back_populates="assignee")
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    widget_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    channel_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    telegram_bot_token: Mapped[str] = mapped_column(String(255), default="")
    telegram_bot_username: Mapped[str] = mapped_column(String(120), default="")
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_status: Mapped[str] = mapped_column(String(32), default="off")
    telegram_bot_name: Mapped[str] = mapped_column(String(160), default="")
    telegram_error: Mapped[str] = mapped_column(String(255), default="")
    telegram_last_seen: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    plan: Mapped[str] = mapped_column(String(32), default="team")
    created_at: Mapped[datetime] = now_column()
    updated_at: Mapped[datetime] = refreshed_column()

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_membership"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), default=WorkspaceRole.AGENT.value)
    created_at: Mapped[datetime] = now_column()

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    subject: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default=TicketStatus.OPEN.value, index=True)
    priority: Mapped[str] = mapped_column(String(32), default=Priority.NORMAL.value)
    channel: Mapped[str] = mapped_column(String(32), default=Channel.WEB.value, index=True)
    customer_name: Mapped[str] = mapped_column(String(255), default="")
    customer_ref: Mapped[str] = mapped_column(String(255), default="", index=True)
    language: Mapped[str] = mapped_column(String(16), default="auto")
    sentiment: Mapped[str] = mapped_column(String(16), default=Sentiment.NEUTRAL.value)
    ai_handled: Mapped[bool] = mapped_column(Boolean, default=False)
    assignee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    first_response_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = now_column()
    updated_at: Mapped[datetime] = refreshed_column()

    assignee: Mapped[Optional[User]] = relationship(back_populates="tickets")
    messages: Mapped[list[Message]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="Message.created_at"
    )
    events: Mapped[list[TicketEvent]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="TicketEvent.created_at"
    )


Index("ix_tickets_channel_status", Ticket.channel, Ticket.status)
Index("ix_tickets_customer_ref_channel", Ticket.customer_ref, Ticket.channel)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    sender: Mapped[str] = mapped_column(String(32), default=SenderRole.CUSTOMER.value, index=True)
    channel: Mapped[str] = mapped_column(String(32), default=Channel.WEB.value)
    content: Mapped[str] = mapped_column(Text, default="")
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = now_column()

    ticket: Mapped[Ticket] = relationship(back_populates="messages")


Index("ix_messages_ticket_created", Message.ticket_id, Message.created_at)


class TicketEvent(Base):
    __tablename__ = "ticket_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(48))
    actor: Mapped[str] = mapped_column(String(64), default="system")
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = now_column()

    ticket: Mapped[Ticket] = relationship(back_populates="events")


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(255), default="manual")
    status: Mapped[str] = mapped_column(String(32), default=DocumentStatus.READY.value, index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = now_column()
    updated_at: Mapped[datetime] = refreshed_column()

    chunks: Mapped[list[KnowledgeChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="KnowledgeChunk.ordinal"
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[float]] = mapped_column(embedding_type())
    created_at: Mapped[datetime] = now_column()

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), default="")
    prefix: Mapped[str] = mapped_column(String(32), index=True, default="")
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scope: Mapped[str] = mapped_column(String(32), default="ingest")
    masked: Mapped[str] = mapped_column(String(64), default="")
    created_by: Mapped[str] = mapped_column(String(255), default="")
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = now_column()

    workspace: Mapped[Workspace] = relationship()


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ident: Mapped[str] = mapped_column(String(255), index=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    blocked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = refreshed_column()


class AuditEntry(Base):
    __tablename__ = "audit_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    ip: Mapped[str] = mapped_column(String(64), default="")
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = now_column()


__all__ = [
    "Base",
    "User",
    "Workspace",
    "Membership",
    "Ticket",
    "Message",
    "TicketEvent",
    "KnowledgeDocument",
    "KnowledgeChunk",
    "ApiKey",
    "LoginAttempt",
    "AuditEntry",
]
