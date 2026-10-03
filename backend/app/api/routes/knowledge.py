from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...models import Workspace
from ...schemas import (
    KnowledgeDocumentCreate,
    KnowledgeDocumentOut,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from ...services import knowledge as knowledge_service
from ..deps import get_workspace_context, require_role

router = APIRouter()


@router.get("/documents", response_model=list[KnowledgeDocumentOut])
async def list_documents(
    limit: int = Query(default=100, ge=1, le=500),
    context: tuple[Workspace, str] = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> list[KnowledgeDocumentOut]:
    workspace, _ = context
    documents = await knowledge_service.list_documents(session, workspace.id, limit=limit)
    return [KnowledgeDocumentOut.model_validate(document) for document in documents]


@router.post("/documents", response_model=KnowledgeDocumentOut, status_code=status.HTTP_201_CREATED)
async def create_document(
    payload: KnowledgeDocumentCreate,
    context: tuple[Workspace, str] = Depends(require_role("agent")),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeDocumentOut:
    workspace, _ = context
    document = await knowledge_service.create_document(
        session, workspace.id, title=payload.title, content=payload.content, source=payload.source
    )
    await session.commit()
    return KnowledgeDocumentOut.model_validate(document)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: int,
    context: tuple[Workspace, str] = Depends(require_role("agent")),
    session: AsyncSession = Depends(get_session),
) -> None:
    workspace, _ = context
    document = await knowledge_service.get_document(session, workspace.id, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")
    await knowledge_service.delete_document(session, document)
    await session.commit()
    return None


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search(
    payload: KnowledgeSearchRequest,
    context: tuple[Workspace, str] = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeSearchResponse:
    workspace, _ = context
    hits = await knowledge_service.search_knowledge(
        session, workspace.id, payload.query, top_k=payload.top_k, min_score=0.0
    )
    return KnowledgeSearchResponse(query=payload.query, hits=hits)
