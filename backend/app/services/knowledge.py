from typing import Any, Optional, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.chunking import chunk_text, estimate_tokens
from ..ai.llm import get_llm_client
from ..ai.retrieval import cosine_similarity
from ..config import settings
from ..constants import DocumentStatus
from ..db import is_postgres
from ..models import KnowledgeChunk, KnowledgeDocument

SQLITE_CANDIDATE_LIMIT = 2000


async def create_document(
    session: AsyncSession,
    workspace_id: int,
    title: str,
    content: str,
    source: str = "manual",
) -> KnowledgeDocument:
    document = KnowledgeDocument(
        workspace_id=workspace_id,
        title=title.strip()[:255] or "Документ",
        source=source.strip()[:255] or "manual",
        status=DocumentStatus.PROCESSING.value,
    )
    session.add(document)
    await session.flush()

    chunks = chunk_text(content)
    try:
        if not chunks:
            raise ValueError("Документ пустой после нормализации")
        embeddings = await get_llm_client().embed(chunks)
        for ordinal, (text, vector) in enumerate(zip(chunks, embeddings)):
            session.add(
                KnowledgeChunk(
                    document_id=document.id,
                    ordinal=ordinal,
                    content=text,
                    tokens=estimate_tokens(text),
                    embedding=vector,
                )
            )
        document.chunk_count = len(chunks)
        document.status = DocumentStatus.READY.value
    except Exception as exc:
        document.status = DocumentStatus.FAILED.value
        document.error = str(exc)[:500]
        document.chunk_count = 0
    await session.flush()
    return document


OPERATOR_SOURCE = "operator-answer"


def _format_qa(question: str, answer: str) -> str:
    """Приводит пару вопрос-ответ к тому же виду, что и обычный документ."""
    return (
        f"Вопрос клиента: {question.strip()}\n\n"
        f"Ответ оператора: {answer.strip()}"
    )


def _title_from_question(question: str) -> str:
    cleaned = " ".join(question.strip().split())
    return (f"Ответ оператора: {cleaned[:180]}" if cleaned else "Ответ оператора")


async def find_similar_operator_answer(
    session: AsyncSession, workspace_id: int, question: str, threshold: float = 0.55
) -> tuple[Optional[KnowledgeDocument], float]:
    """Ищет ранее выученный ответ на тот же вопрос, чтобы не плодить дубли.

    Сравниваем вопрос с заголовком документа, а заголовок хранит ровно вопрос
    клиента: вектор всего документа размыт вопросом и ответом вместе, и
    одинаковые вопросы не узнавали бы друг друга.

    Смотрим только на документы, выученные из ответов операторов: обычные
    статьи про ту же тему — это другое знание, их не затираем.
    """
    rows = (
        await session.execute(
            select(KnowledgeDocument.id, KnowledgeDocument.title).where(
                KnowledgeDocument.workspace_id == workspace_id,
                KnowledgeDocument.status == DocumentStatus.READY.value,
                KnowledgeDocument.source.like(f"{OPERATOR_SOURCE}%"),
            )
        )
    ).all()
    if not rows:
        return None, 0.0

    client = get_llm_client()
    query_vector = await client.embed_one(question)
    titles = [title for _, title in rows]
    title_vectors = await client.embed(titles)

    best_score, best_document_id = 0.0, None
    for (document_id, _), vector in zip(rows, title_vectors):
        score = cosine_similarity(query_vector, vector)
        if score > best_score:
            best_score, best_document_id = score, document_id

    if best_document_id is None or best_score < threshold:
        return None, float(best_score)
    document = await session.get(KnowledgeDocument, best_document_id)
    if document is None:
        return None, float(best_score)
    return document, float(best_score)


async def learn_from_operator_reply(
    session: AsyncSession,
    workspace_id: int,
    question: str,
    answer: str,
) -> dict[str, Any]:
    """Учит базу знаний на живой паре «вопрос клиента → ответ оператора».

    Новый ответ по той же теме обновляет уже выученный документ, а не создаёт
    второй почти такой же: так база остаётся компактной и актуальной.
    """
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question or not answer:
        return {"learned": False, "reason": "пустой вопрос или ответ"}

    existing, score = await find_similar_operator_answer(session, workspace_id, question)
    if existing is not None:
        # Старые чанки удаляем явным запросом: relationship ленивый,
        # и обращение к нему отсюда роняет сессию.
        await session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.document_id == existing.id)
        )
        content = _format_qa(question, answer)
        chunks = chunk_text(content)
        embeddings = await get_llm_client().embed(chunks)
        for ordinal, (text, vector) in enumerate(zip(chunks, embeddings)):
            session.add(
                KnowledgeChunk(
                    document_id=existing.id,
                    ordinal=ordinal,
                    content=text,
                    tokens=estimate_tokens(text),
                    embedding=vector,
                )
            )
        existing.chunk_count = len(chunks)
        existing.title = _title_from_question(question)
        existing.status = DocumentStatus.READY.value
        existing.error = ""
        await session.flush()
        return {
            "learned": True,
            "created": False,
            "document_id": existing.id,
            "title": existing.title,
            "similarity": round(score, 3),
        }

    document = await create_document(
        session,
        workspace_id,
        _title_from_question(question),
        _format_qa(question, answer),
        source=OPERATOR_SOURCE,
    )
    return {
        "learned": document.status == DocumentStatus.READY.value,
        "created": True,
        "document_id": document.id,
        "title": document.title,
        "similarity": round(score, 3),
    }


async def list_documents(
    session: AsyncSession, workspace_id: int, limit: int = 100
) -> Sequence[KnowledgeDocument]:
    stmt = (
        select(KnowledgeDocument)
        .where(KnowledgeDocument.workspace_id == workspace_id)
        .order_by(KnowledgeDocument.created_at.desc())
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


async def get_document(
    session: AsyncSession, workspace_id: int, document_id: int
) -> Optional[KnowledgeDocument]:
    document = await session.get(KnowledgeDocument, document_id)
    if document is None or document.workspace_id != workspace_id:
        return None
    return document


async def delete_document(session: AsyncSession, document: KnowledgeDocument) -> None:
    await session.delete(document)
    await session.flush()


async def search_knowledge(
    session: AsyncSession,
    workspace_id: int,
    query: str,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
) -> list[dict[str, Any]]:
    limit = max(1, top_k or settings.retrieval_top_k)
    threshold = settings.retrieval_min_score if min_score is None else min_score
    query_vector = await get_llm_client().embed_one(query)

    stmt = (
        select(KnowledgeChunk, KnowledgeDocument.title)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(
            KnowledgeDocument.workspace_id == workspace_id,
            KnowledgeDocument.status == DocumentStatus.READY.value,
        )
    )

    if is_postgres():
        ordered = stmt.order_by(KnowledgeChunk.embedding.cosine_distance(query_vector)).limit(limit)
        rows = (await session.execute(ordered)).all()
    else:
        candidates = (await session.execute(stmt.limit(SQLITE_CANDIDATE_LIMIT))).all()
        scored = []
        for chunk, title in candidates:
            embedding = list(chunk.embedding) if chunk.embedding is not None else []
            scored.append((cosine_similarity(query_vector, embedding), chunk, title))
        scored.sort(key=lambda item: item[0], reverse=True)
        rows = [(chunk, title) for _, chunk, title in scored[:limit]]

    hits: list[dict[str, Any]] = []
    for chunk, title in rows:
        embedding = list(chunk.embedding) if chunk.embedding is not None else []
        score = cosine_similarity(query_vector, embedding)
        hits.append(
            {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "document_title": title,
                "content": chunk.content,
                "score": round(float(score), 4),
            }
        )
    filtered = [hit for hit in hits if hit["score"] >= threshold]
    return filtered or hits[:1]


async def knowledge_stats(session: AsyncSession, workspace_id: int) -> tuple[int, int]:
    documents = (
        await session.execute(
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(KnowledgeDocument.workspace_id == workspace_id)
        )
    ).scalar_one()
    chunks = (
        await session.execute(
            select(func.count())
            .select_from(KnowledgeChunk)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(KnowledgeDocument.workspace_id == workspace_id)
        )
    ).scalar_one()
    return int(documents or 0), int(chunks or 0)
