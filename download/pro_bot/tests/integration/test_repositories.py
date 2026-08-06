"""Integration tests for SQLite repositories."""
import os
import tempfile

import pytest

from src.core.config import DatabaseConfig
from src.domain.entities import Link, LinkCategory, LinkStatus, User, UserRole
from src.infrastructure.database.connection import Database
from src.infrastructure.database.migrations import MigrationRunner
from src.infrastructure.database.repositories.link_repository import SqliteLinkRepository
from src.infrastructure.database.repositories.user_repository import SqliteUserRepository


@pytest.fixture
async def database():
    """Create a temporary database with migrations applied."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        config = DatabaseConfig(path=db_path)
        db = Database(config)
        await db.connect()

        # Run migrations
        runner = MigrationRunner(db, migrations_dir="migrations")
        await runner.run()

        yield db

        await db.disconnect()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


@pytest.mark.asyncio
async def test_link_repository_save_and_get(database):
    repo = SqliteLinkRepository(database)
    link = Link(
        url="https://chat.whatsapp.com/ABC123",
        normalized_url="chat.whatsapp.com/abc123",
        category=LinkCategory.GROUP_INVITE,
        status=LinkStatus.UNVERIFIED,
        content_hash="abc123",
    )
    saved = await repo.save(link)
    assert saved.id is not None

    fetched = await repo.get_by_id(saved.id)
    assert fetched is not None
    assert fetched.url == link.url
    assert fetched.category == LinkCategory.GROUP_INVITE


@pytest.mark.asyncio
async def test_link_repository_get_by_url(database):
    repo = SqliteLinkRepository(database)
    link = Link(
        url="https://chat.whatsapp.com/Test123",
        category=LinkCategory.GROUP_INVITE,
        content_hash="test123",
    )
    await repo.save(link)

    fetched = await repo.get_by_url("https://chat.whatsapp.com/Test123")
    assert fetched is not None
    assert fetched.url == link.url


@pytest.mark.asyncio
async def test_link_repository_duplicate_url(database):
    repo = SqliteLinkRepository(database)
    link1 = Link(
        url="https://chat.whatsapp.com/Dup123",
        category=LinkCategory.GROUP_INVITE,
        content_hash="dup123",
    )
    await repo.save(link1)

    link2 = Link(
        url="https://chat.whatsapp.com/Dup123",
        category=LinkCategory.GROUP_INVITE,
        content_hash="dup123",
    )
    with pytest.raises(Exception):
        await repo.save(link2)


@pytest.mark.asyncio
async def test_link_repository_count(database):
    repo = SqliteLinkRepository(database)
    assert await repo.count() == 0

    await repo.save(Link(
        url="https://chat.whatsapp.com/A1",
        category=LinkCategory.GROUP_INVITE,
        content_hash="a1",
    ))
    await repo.save(Link(
        url="https://wa.me/123",
        category=LinkCategory.DIRECT_CHAT,
        content_hash="a2",
    ))

    assert await repo.count() == 2
    assert await repo.count(category=LinkCategory.GROUP_INVITE) == 1
    assert await repo.count(category=LinkCategory.DIRECT_CHAT) == 1


@pytest.mark.asyncio
async def test_link_repository_update_status(database):
    repo = SqliteLinkRepository(database)
    link = Link(
        url="https://chat.whatsapp.com/Status1",
        category=LinkCategory.GROUP_INVITE,
        content_hash="status1",
    )
    saved = await repo.save(link)

    await repo.update_status(saved.id, LinkStatus.EXPIRED)
    fetched = await repo.get_by_id(saved.id)
    assert fetched.status == LinkStatus.EXPIRED


@pytest.mark.asyncio
async def test_user_repository_save_and_get(database):
    repo = SqliteUserRepository(database)
    user = User(
        telegram_id=123456789,
        username="testuser",
        first_name="Test",
        role=UserRole.USER,
    )
    saved = await repo.save(user)
    assert saved.id is not None

    fetched = await repo.get_by_telegram_id(123456789)
    assert fetched is not None
    assert fetched.username == "testuser"
    assert fetched.role == UserRole.USER


@pytest.mark.asyncio
async def test_user_repository_update_role(database):
    repo = SqliteUserRepository(database)
    user = User(telegram_id=111, username="user1", first_name="U1")
    await repo.save(user)

    await repo.update_role(111, UserRole.ADMIN)
    fetched = await repo.get_by_telegram_id(111)
    assert fetched.role == UserRole.ADMIN
    assert fetched.is_admin is True


@pytest.mark.asyncio
async def test_user_repository_list_admins(database):
    repo = SqliteUserRepository(database)
    await repo.save(User(telegram_id=1, username="u1", first_name="U1", role=UserRole.USER))
    await repo.save(User(telegram_id=2, username="u2", first_name="U2", role=UserRole.ADMIN))
    await repo.save(User(telegram_id=3, username="u3", first_name="U3", role=UserRole.SUPER_ADMIN))

    admins = await repo.list_admins()
    assert len(admins) == 2
