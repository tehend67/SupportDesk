from enum import Enum


class Channel(str, Enum):
    WEB = "web"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    EMAIL = "email"
    API = "api"


class TicketStatus(str, Enum):
    OPEN = "open"
    PENDING = "pending"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED = "closed"


OPEN_STATUSES = (TicketStatus.OPEN.value, TicketStatus.PENDING.value, TicketStatus.ESCALATED.value)
CLOSED_STATUSES = (TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value)


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class SenderRole(str, Enum):
    CUSTOMER = "customer"
    ASSISTANT = "assistant"
    AGENT = "agent"
    SYSTEM = "system"


class Sentiment(str, Enum):
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    POSITIVE = "positive"


class EventType(str, Enum):
    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    PRIORITY_CHANGED = "priority_changed"
    ASSIGNED = "assigned"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    AI_REPLIED = "ai_replied"
    DOCUMENT_ADDED = "document_added"
    KNOWLEDGE_LEARNED = "knowledge_learned"
    NOTE = "note"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class UserRole(str, Enum):
    ADMIN = "admin"
    AGENT = "agent"
    VIEWER = "viewer"


class WorkspaceRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    AGENT = "agent"


class TelegramStatus(str, Enum):
    """Жизненный цикл подключения Telegram-бота к комнате."""

    OFF = "off"           # токена нет
    CONNECTING = "connecting"  # токен принят, воркер ещё не поднял бота
    ONLINE = "online"      # бот работает
    INVALID = "invalid"    # токен не прошёл проверку Telegram
    ERROR = "error"        # воркер не смог запустить бота


TELEGRAM_ACTIVE_STATUSES = (TelegramStatus.CONNECTING.value, TelegramStatus.ONLINE.value)


ROLE_ORDER = {WorkspaceRole.AGENT.value: 1, WorkspaceRole.ADMIN.value: 2, WorkspaceRole.OWNER.value: 3}


def role_at_least(role: str, minimum: str) -> bool:
    return ROLE_ORDER.get(role, 0) >= ROLE_ORDER.get(minimum, 0)


SYSTEM_SENDER = SenderRole.SYSTEM.value
CUSTOMER_SENDER = SenderRole.CUSTOMER.value
ASSISTANT_SENDER = SenderRole.ASSISTANT.value
AGENT_SENDER = SenderRole.AGENT.value
