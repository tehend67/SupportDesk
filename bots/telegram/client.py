from dataclasses import dataclass, field
from typing import Any, Optional

import httpx


@dataclass
class Reply:
    ticket_ref: str
    reply: str
    status: str
    escalated: bool
    ai_handled: bool
    sources: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Assignment:
    workspace_id: int
    name: str
    bot_token: str
    channel_key: str
    bot_username: str = ""


class HelpdeskClient:
    def __init__(self, base_url: str, workspace_key: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.workspace_key = workspace_key
        self.timeout = timeout

    async def send(
        self,
        content: str,
        customer_ref: str,
        customer_name: str = "",
        channel: str = "telegram",
        ticket_ref: Optional[str] = None,
        subject: str = "",
    ) -> Reply:
        payload = {
            "channel": channel,
            "content": content,
            "customer_ref": customer_ref,
            "customer_name": customer_name,
            "ticket_ref": ticket_ref,
            "subject": subject,
        }
        headers = {"X-Workspace-Key": self.workspace_key}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/ingest/message", headers=headers, json=payload
            )
            response.raise_for_status()
            data = response.json()
        return Reply(
            ticket_ref=data.get("public_id", ""),
            reply=data.get("reply", ""),
            status=data.get("status", ""),
            escalated=bool(data.get("escalated")),
            ai_handled=bool(data.get("ai_handled")),
            sources=data.get("sources", []),
        )


async def fetch_assignments(base_url: str, worker_key: str, timeout: float = 20.0) -> list[Assignment]:
    headers = {"X-Worker-Key": worker_key}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            f"{base_url.rstrip('/')}/api/channels/telegram/assignments", headers=headers
        )
        response.raise_for_status()
        rows = response.json()
    return [
        Assignment(
            workspace_id=row["workspace_id"],
            name=row.get("name", ""),
            bot_token=row["bot_token"],
            channel_key=row["channel_key"],
            bot_username=row.get("bot_username", ""),
        )
        for row in rows
    ]


async def send_heartbeat(
    base_url: str,
    worker_key: str,
    workspace_id: int,
    ok: bool,
    error: str = "",
    timeout: float = 10.0,
) -> None:
    """Сообщаем бэкенду, что бот жив (или упал) — комната видит статус в реальном времени."""
    headers = {"X-Worker-Key": worker_key}
    payload = {"workspace_id": workspace_id, "ok": ok, "error": error[:255]}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/api/channels/telegram/heartbeat",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
