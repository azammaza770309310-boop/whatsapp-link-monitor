"""Repository interfaces - contracts for data access."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.domain.entities import (
    Backup,
    Link,
    LinkCategory,
    LinkStatus,
    Submission,
    User,
    UserRole,
)


class ILinkRepository(ABC):
    """Repository for WhatsApp link entities."""

    @abstractmethod
    async def save(self, link: Link) -> Link:
        """Save a link. Returns the saved link with ID."""

    @abstractmethod
    async def get_by_id(self, link_id: int) -> Optional[Link]:
        """Get a link by ID."""

    @abstractmethod
    async def get_by_url(self, url: str) -> Optional[Link]:
        """Get a link by normalized URL."""

    @abstractmethod
    async def list(
        self,
        category: Optional[LinkCategory] = None,
        status: Optional[LinkStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Link]:
        """List links with optional filters."""

    @abstractmethod
    async def search(self, query: str, limit: int = 20) -> List[Link]:
        """Full-text search across link URL, title, description."""

    @abstractmethod
    async def update_status(self, link_id: int, status: LinkStatus) -> bool:
        """Update a link's status. Returns True if updated."""

    @abstractmethod
    async def delete(self, link_id: int) -> bool:
        """Delete a link by ID. Returns True if deleted."""

    @abstractmethod
    async def count(
        self,
        category: Optional[LinkCategory] = None,
        status: Optional[LinkStatus] = None,
    ) -> int:
        """Count links with optional filters."""

    @abstractmethod
    async def get_expired(self, limit: int = 100) -> List[Link]:
        """Get links that need re-validation."""

    @abstractmethod
    async def export_all(self) -> List[Link]:
        """Export all links for backup."""

    @abstractmethod
    async def import_links(self, links: List[Link]) -> int:
        """Import links. Returns count of newly inserted."""


class IUserRepository(ABC):
    """Repository for user entities."""

    @abstractmethod
    async def save(self, user: User) -> User:
        """Save a user."""

    @abstractmethod
    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """Get a user by Telegram ID."""

    @abstractmethod
    async def update_role(self, telegram_id: int, role: UserRole) -> bool:
        """Update a user's role."""

    @abstractmethod
    async def block(self, telegram_id: int) -> bool:
        """Block a user."""

    @abstractmethod
    async def unblock(self, telegram_id: int) -> bool:
        """Unblock a user."""

    @abstractmethod
    async def list_admins(self) -> List[User]:
        """List all admin users."""

    @abstractmethod
    async def count(self) -> int:
        """Count total users."""


class ISubmissionRepository(ABC):
    """Repository for submission records."""

    @abstractmethod
    async def save(self, submission: Submission) -> Submission:
        """Save a submission record."""

    @abstractmethod
    async def list_by_user(
        self, user_id: int, limit: int = 20
    ) -> List[Submission]:
        """List submissions by a user."""


class IBackupRepository(ABC):
    """Repository for backup metadata."""

    @abstractmethod
    async def save(self, backup: Backup) -> Backup:
        """Save backup metadata."""

    @abstractmethod
    async def list_recent(self, limit: int = 10) -> List[Backup]:
        """List recent backups."""

    @abstractmethod
    async def delete_old(self, retention_days: int) -> int:
        """Delete backups older than retention period. Returns count deleted."""
