from fastapi import APIRouter, Depends

from .deps import require_csrf
from .routes import (
    analytics,
    auth,
    channels,
    health,
    ingest,
    knowledge,
    tickets,
    workspaces,
    ws,
)

csrf = [Depends(require_csrf)]

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(workspaces.router, prefix="/workspaces", tags=["workspaces"], dependencies=csrf)
api_router.include_router(tickets.router, prefix="/tickets", tags=["tickets"], dependencies=csrf)
api_router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"], dependencies=csrf)
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
api_router.include_router(channels.router, prefix="/channels", tags=["channels"], dependencies=csrf)
api_router.include_router(ws.router, prefix="/ws", tags=["websocket"])
