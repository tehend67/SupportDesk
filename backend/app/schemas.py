from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .constants import Channel, Priority, SenderRole, TicketStatus, UserRole


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(default="", max_length=255)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(ORMModel):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool = True


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    slug: Optional[str] = Field(default=None, max_length=80)


class WorkspaceOut(ORMModel):
    id: int
    name: str
    slug: str
    plan: str
    role: str = "agent"
    members: int = 0
    open_tickets: int = 0
    created_at: datetime


class WorkspaceDetail(ORMModel):
    id: int
    name: str
    slug: str
    plan: str
    role: str = "agent"
    widget_key: str
    channel_key: str
    telegram_enabled: bool
    telegram_bot_username: str = ""
    telegram_bot_name: str = ""
    telegram_status: str = "off"
    telegram_deep_link: str = ""
    telegram_error: str = ""
    telegram_last_seen: Optional[datetime] = None
    widget_snippet: str = ""
    widget_url: str = ""
    api_base_url: str = ""
    created_at: datetime


class MemberOut(ORMModel):
    id: int
    user_id: int
    email: str = ""
    full_name: str = ""
    role: str
    created_at: datetime


class MemberCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    role: str = "agent"
    full_name: str = Field(default="", max_length=255)
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)


class MemberUpdate(BaseModel):
    role: str


class TelegramConnectRequest(BaseModel):
    bot_token: str = Field(default="", max_length=255)
    enabled: bool = True


class ApiKeyCreate(BaseModel):
    name: str = Field(default="Ключ", max_length=120)
    scope: str = "ingest"


class ApiKeyOut(BaseModel):
    id: int
    name: str
    prefix: str
    masked: str
    scope: str
    created_by: str = ""
    revoked: bool = False
    last_used_at: Optional[datetime] = None
    created_at: datetime


class ApiKeyCreated(ApiKeyOut):
    key: str


class AuditOut(BaseModel):
    id: int
    action: str
    actor: str
    ip: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class TelegramStatusOut(BaseModel):
    enabled: bool = False
    status: str = "off"
    bot_username: str = ""
    bot_name: str = ""
    error: str = ""
    deep_link: str = ""
    last_seen: Optional[datetime] = None


class TelegramHeartbeat(BaseModel):
    workspace_id: int
    ok: bool = True
    error: str = Field(default="", max_length=255)


class TicketEventOut(ORMModel):
    id: int
    type: str
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MessageOut(ORMModel):
    id: int
    ticket_id: int
    sender: str
    channel: str
    content: str
    tokens: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class TicketOut(ORMModel):
    id: int
    public_id: str
    subject: str
    status: str
    priority: str
    channel: str
    customer_name: str
    customer_ref: str
    sentiment: str
    ai_handled: bool
    assignee_id: Optional[int] = None
    first_response_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class TicketDetail(TicketOut):
    messages: list[MessageOut] = Field(default_factory=list)
    events: list[TicketEventOut] = Field(default_factory=list)


class TicketPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[TicketOut]


class TicketCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    channel: Channel = Channel.WEB
    priority: Priority = Priority.NORMAL
    customer_name: str = ""
    customer_ref: str = ""
    message: str = ""
    assignee_id: Optional[int] = None


class TicketUpdate(BaseModel):
    status: Optional[TicketStatus] = None
    priority: Optional[Priority] = None
    assignee_id: Optional[int] = None
    subject: Optional[str] = Field(default=None, max_length=255)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)
    sender: SenderRole = SenderRole.AGENT


class IngestRequest(BaseModel):
    channel: Channel = Channel.API
    content: str = Field(min_length=1)
    customer_ref: str = Field(min_length=1, max_length=255)
    customer_name: str = ""
    ticket_ref: Optional[str] = None
    subject: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeHit(BaseModel):
    chunk_id: int
    document_id: int
    document_title: str
    content: str
    score: float


class IngestResponse(BaseModel):
    ticket_id: int
    public_id: str
    status: str
    reply: str
    sender: str
    escalated: bool
    ai_handled: bool
    sources: list[KnowledgeHit] = Field(default_factory=list)
    message_id: Optional[int] = None
    created_at: Optional[datetime] = None


class KnowledgeDocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    source: str = "manual"


class KnowledgeDocumentOut(ORMModel):
    id: int
    title: str
    source: str
    status: str
    error: str = ""
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: Optional[int] = Field(default=None, ge=1, le=20)


class KnowledgeSearchResponse(BaseModel):
    query: str
    hits: list[KnowledgeHit]


class StatusCount(BaseModel):
    key: str
    count: int


class TimeseriesPoint(BaseModel):
    day: str
    created: int
    resolved: int


class AnalyticsOverview(BaseModel):
    total_tickets: int
    open_tickets: int
    resolved_tickets: int
    escalated_tickets: int
    ai_handled_share: float
    messages_total: int
    documents_total: int
    chunks_total: int
    avg_first_response_minutes: float
    avg_resolution_hours: float
    by_status: list[StatusCount]
    by_channel: list[StatusCount]
    by_priority: list[StatusCount]


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    llm_mode: str
    environment: str
