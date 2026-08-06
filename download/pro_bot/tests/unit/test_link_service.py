"""Tests for LinkService."""
import pytest

from src.application.services.link_service import LinkService
from src.domain.entities import Link, LinkCategory, LinkStatus, User, UserRole
from src.domain.repositories import (
    IBackupRepository,
    ILinkRepository,
    ISubmissionRepository,
    IUserRepository,
)


# In-memory repository implementations for testing
class InMemoryLinkRepository(ILinkRepository):
    def __init__(self):
        self._links = []
        self._next_id = 1

    async def save(self, link):
        if link.id is None:
            link.id = self._next_id
            self._next_id += 1
            self._links.append(link)
        else:
            for i, l in enumerate(self._links):
                if l.id == link.id:
                    self._links[i] = link
                    break
        return link

    async def get_by_id(self, link_id):
        for l in self._links:
            if l.id == link_id:
                return l
        return None

    async def get_by_url(self, url):
        normalized = Link._normalize(url)
        for l in self._links:
            if l.normalized_url == normalized:
                return l
        return None

    async def list(self, category=None, status=None, limit=50, offset=0):
        result = self._links
        if category:
            result = [l for l in result if l.category == category]
        if status:
            result = [l for l in result if l.status == status]
        return result[offset:offset + limit]

    async def search(self, query, limit=20):
        query_lower = query.lower()
        return [
            l for l in self._links
            if query_lower in l.url.lower() or (l.title and query_lower in l.title.lower())
        ][:limit]

    async def update_status(self, link_id, status):
        for l in self._links:
            if l.id == link_id:
                l.status = status
                return True
        return False

    async def delete(self, link_id):
        self._links = [l for l in self._links if l.id != link_id]
        return True

    async def count(self, category=None, status=None):
        result = self._links
        if category:
            result = [l for l in result if l.category == category]
        if status:
            result = [l for l in result if l.status == status]
        return len(result)

    async def get_expired(self, limit=100):
        return [l for l in self._links if l.status == LinkStatus.ACTIVE][:limit]

    async def export_all(self):
        return list(self._links)

    async def import_links(self, links):
        count = 0
        for link in links:
            existing = await self.get_by_url(link.url)
            if not existing:
                await self.save(link)
                count += 1
        return count


class InMemoryUserRepository(IUserRepository):
    def __init__(self):
        self._users = []

    async def save(self, user):
        if user.id is None:
            user.id = len(self._users) + 1
        self._users.append(user)
        return user

    async def get_by_telegram_id(self, telegram_id):
        for u in self._users:
            if u.telegram_id == telegram_id:
                return u
        return None

    async def update_role(self, telegram_id, role):
        for u in self._users:
            if u.telegram_id == telegram_id:
                u.role = role
                return True
        return False

    async def block(self, telegram_id):
        return True

    async def unblock(self, telegram_id):
        return True

    async def list_admins(self):
        return [u for u in self._users if u.is_admin]

    async def count(self):
        return len(self._users)


class InMemorySubmissionRepository(ISubmissionRepository):
    def __init__(self):
        self._submissions = []

    async def save(self, submission):
        submission.id = len(self._submissions) + 1
        self._submissions.append(submission)
        return submission

    async def list_by_user(self, user_id, limit=20):
        return [s for s in self._submissions if s.user_id == user_id][:limit]


@pytest.fixture
def link_service():
    link_repo = InMemoryLinkRepository()
    user_repo = InMemoryUserRepository()
    submission_repo = InMemorySubmissionRepository()
    return LinkService(link_repo, user_repo, submission_repo)


@pytest.mark.asyncio
async def test_extract_links_finds_whatsapp_urls(link_service):
    text = "انضموا هنا: https://chat.whatsapp.com/ABC123xyz"
    links = link_service.extract_links(text)
    assert len(links) == 1
    assert "chat.whatsapp.com" in links[0]


@pytest.mark.asyncio
async def test_extract_links_finds_multiple(link_service):
    text = (
        "https://chat.whatsapp.com/ABC123 "
        "https://wa.me/967777777 "
        "https://whatsapp.com/channel/0029Xyz"
    )
    links = link_service.extract_links(text)
    assert len(links) == 3


@pytest.mark.asyncio
async def test_extract_links_ignores_non_whatsapp(link_service):
    text = "https://google.com https://facebook.com"
    links = link_service.extract_links(text)
    assert len(links) == 0


@pytest.mark.asyncio
async def test_categorize_link_group_invite(link_service):
    cat = link_service.categorize_link("https://chat.whatsapp.com/ABC123")
    assert cat == LinkCategory.GROUP_INVITE


@pytest.mark.asyncio
async def test_categorize_link_channel(link_service):
    cat = link_service.categorize_link("https://whatsapp.com/channel/0029Xyz")
    assert cat == LinkCategory.CHANNEL


@pytest.mark.asyncio
async def test_categorize_link_direct_chat(link_service):
    cat = link_service.categorize_link("https://wa.me/967777777")
    assert cat == LinkCategory.DIRECT_CHAT


@pytest.mark.asyncio
async def test_submit_link_new(link_service):
    link, is_dup = await link_service.submit_link(
        "https://chat.whatsapp.com/ABC123",
        submitted_by_name="TestUser",
    )
    assert not is_dup
    assert link.id is not None
    assert link.category == LinkCategory.GROUP_INVITE


@pytest.mark.asyncio
async def test_submit_link_duplicate(link_service):
    url = "https://chat.whatsapp.com/ABC123"
    await link_service.submit_link(url)
    link, is_dup = await link_service.submit_link(url)
    assert is_dup


@pytest.mark.asyncio
async def test_submit_link_invalid_raises(link_service):
    from src.core.exceptions import ValidationError
    with pytest.raises(ValidationError):
        await link_service.submit_link("https://google.com")


@pytest.mark.asyncio
async def test_get_stats(link_service):
    await link_service.submit_link("https://chat.whatsapp.com/ABC")
    await link_service.submit_link("https://wa.me/967777")
    stats = await link_service.get_stats()
    assert stats["total_links"] == 2
    assert stats["total_users"] == 0
    assert "group_invite" in stats["by_category"]
    assert "direct_chat" in stats["by_category"]


@pytest.mark.asyncio
async def test_search_links(link_service):
    await link_service.submit_link("https://chat.whatsapp.com/ABC123")
    results = await link_service.search_links("ABC123")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_search_links_too_short(link_service):
    from src.core.exceptions import ValidationError
    with pytest.raises(ValidationError):
        await link_service.search_links("A")
