import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .constants import Channel, Priority, SenderRole, UserRole, WorkspaceRole
from .db import SessionLocal
from .models import KnowledgeDocument, Membership, Ticket, User, Workspace
from .security import hash_password
from .services.knowledge import create_document
from .services.tickets import add_message, create_ticket, log_event

logger = logging.getLogger("helpdesk.seed")

DEMO_DOCUMENTS = [
    {
        "title": "Тарифы и оплата",
        "source": "pricing.md",
        "content": (
            "Базовый тариф «Старт» стоит 990 рублей в месяц и включает одного оператора, "
            "500 обращений и подключение одного канала. Тариф «Команда» — 3900 рублей в месяц, "
            "до 10 операторов и безлимит обращений. Тариф «Бизнес» — 9900 рублей в месяц: "
            "приоритетная поддержка, SLA 99.9% и SSO. Оплата принимается картой, "
            "по счёту для юридических лиц и через СБП. Подписка продлевается автоматически, "
            "отменить можно в любой момент в разделе «Оплата» личного кабинета."
        ),
    },
    {
        "title": "Доставка и сроки",
        "source": "delivery.md",
        "content": (
            "Доставка по Москве занимает 1 рабочий день, по России — от 2 до 5 рабочих дней "
            "в зависимости от региона. Стоимость доставки 350 рублей, для заказов от 5000 рублей — бесплатно. "
            "Курьер связывается с получателем за час до прибытия. Отследить заказ можно по трек-номеру, "
            "который приходит в SMS и на электронную почту после передачи в службу доставки."
        ),
    },
    {
        "title": "Возврат и гарантия",
        "source": "returns.md",
        "content": (
            "Вернуть товар можно в течение 14 дней с момента получения, если он не был в использовании "
            "и сохранена упаковка. Деньги возвращаются на исходный способ оплаты в течение 10 рабочих дней. "
            "Гарантия на оборудование составляет 12 месяцев. Для оформления возврата напишите в поддержку "
            "номер заказа и причину возврата, после чего мы пришлём бланк и адрес для отправки."
        ),
    },
    {
        "title": "Технические требования и восстановление доступа",
        "source": "tech.md",
        "content": (
            "Сервис работает в браузерах Chrome, Safari, Firefox и Edge последних двух версий. "
            "Мобильное приложение поддерживает iOS 15+ и Android 10+. "
            "Если забыли пароль, нажмите «Восстановить доступ» на странице входа: ссылка для сброса "
            "действует 30 минут и приходит на привязанную почту. При двухфакторной аутентификации "
            "понадобится код из приложения-аутентификатора. Если письмо не приходит, проверьте папку «Спам» "
            "и обратитесь в поддержку с указанием email аккаунта."
        ),
    },
]

DEMO_TICKETS = [
    {
        "subject": "Не приходит письмо для сброса пароля",
        "channel": Channel.TELEGRAM,
        "priority": Priority.HIGH,
        "customer_name": "Иван Петров",
        "customer_ref": "tg-1001",
        "messages": [
            "Здравствуйте, не приходит письмо для сброса пароля, уже час жду.",
            "Проверил папку спам, там тоже нет.",
        ],
    },
    {
        "subject": "Сроки доставки в Казань",
        "channel": Channel.WEB,
        "priority": Priority.NORMAL,
        "customer_name": "Мария Сидорова",
        "customer_ref": "web-demo-1",
        "messages": ["Здравствуйте! Сколько дней идёт доставка в Казань и сколько стоит?"],
    },
    {
        "subject": "Как отменить подписку",
        "channel": Channel.TELEGRAM,
        "priority": Priority.LOW,
        "customer_name": "Алексей",
        "customer_ref": "tg-1002",
        "messages": ["Как отменить подписку на тарифе Старт?"],
    },
]


async def ensure_demo_user(session: AsyncSession) -> User:
    email = settings.admin_email.lower().strip()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        user = User(
            email=email,
            full_name=settings.admin_name,
            hashed_password=hash_password(settings.admin_password),
            role=UserRole.ADMIN.value,
        )
        session.add(user)
        await session.flush()
        logger.info("created demo user %s", email)
    return user


async def ensure_demo_workspace(session: AsyncSession, owner: User) -> Workspace:
    slug = "acme-support"
    workspace = (
        await session.execute(select(Workspace).where(Workspace.slug == slug))
    ).scalar_one_or_none()
    if workspace is None:
        from .services.workspaces import create_workspace

        workspace = await create_workspace(session, owner, settings.demo_workspace_name, slug)
        logger.info("created demo workspace %s", slug)
    else:
        membership = (
            await session.execute(
                select(Membership).where(
                    Membership.workspace_id == workspace.id, Membership.user_id == owner.id
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            session.add(
                Membership(
                    workspace_id=workspace.id, user_id=owner.id, role=WorkspaceRole.OWNER.value
                )
            )
            await session.flush()
    return workspace


async def seed_documents(session: AsyncSession, workspace: Workspace) -> int:
    created = 0
    for document in DEMO_DOCUMENTS:
        found = (
            await session.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.workspace_id == workspace.id,
                    KnowledgeDocument.title == document["title"],
                )
            )
        ).scalar_one_or_none()
        if found is not None:
            continue
        await create_document(session, workspace.id, **document)
        created += 1
    return created


async def seed_tickets(session: AsyncSession, workspace: Workspace) -> int:
    created = 0
    for ticket_data in DEMO_TICKETS:
        found = (
            await session.execute(
                select(Ticket).where(
                    Ticket.workspace_id == workspace.id,
                    Ticket.customer_ref == ticket_data["customer_ref"],
                )
            )
        ).scalar_one_or_none()
        if found is not None:
            continue
        ticket = await create_ticket(
            session,
            workspace.id,
            subject=ticket_data["subject"],
            channel=ticket_data["channel"],
            priority=ticket_data["priority"],
            customer_name=ticket_data["customer_name"],
            customer_ref=ticket_data["customer_ref"],
        )
        for message in ticket_data["messages"]:
            await add_message(
                session, ticket, SenderRole.CUSTOMER, message, channel=ticket_data["channel"]
            )
        await log_event(session, ticket, "seeded", actor="seed")
        created += 1
    return created


async def run_seed() -> None:
    async with SessionLocal() as session:
        try:
            user = await ensure_demo_user(session)
            workspace = await ensure_demo_workspace(session, user)
            documents = await seed_documents(session, workspace)
            tickets = await seed_tickets(session, workspace)
            await session.commit()
            if documents or tickets:
                logger.info(
                    "seed complete: workspace=%s, %s documents, %s tickets",
                    workspace.slug,
                    documents,
                    tickets,
                )
        except Exception:
            await session.rollback()
            raise


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_seed())
