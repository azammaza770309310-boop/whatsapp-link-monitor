"""Domain entities - pure business objects with no I/O dependencies."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class LinkCategory(str, Enum):
    """WhatsApp link categories."""
    GROUP_INVITE = "group_invite"
    CHANNEL = "channel"
    DIRECT_CHAT = "direct_chat"
    MESSAGE_LINK = "message_link"
    API_SEND = "api_send"
    QR_CODE = "qr_code"
    SHORT_LINK = "short_link"
    OTHER = "other"


class LinkStatus(str, Enum):
    """Status of a stored link."""
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    INVALID = "invalid"
    UNVERIFIED = "unverified"


class UserRole(str, Enum):
    """User roles for permission management."""
    USER = "user"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


@dataclass
class Link:
    """A WhatsApp link entity."""
    id: Optional[int] = None
    url: str = ""
    normalized_url: str = ""
    category: LinkCategory = LinkCategory.OTHER
    status: LinkStatus = LinkStatus.UNVERIFIED
    title: Optional[str] = None
    description: Optional[str] = None
    submitted_by: Optional[int] = None
    submitted_by_name: Optional[str] = None
    source_group_id: Optional[int] = None
    source_group_name: Optional[str] = None
    content_hash: str = ""
    message_text: Optional[str] = None
    verified_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.normalized_url and self.url:
            self.normalized_url = self._normalize(self.url)
        if not self.content_hash and self.url:
            import hashlib
            self.content_hash = hashlib.md5(
                self.normalized_url.encode("utf-8")
            ).hexdigest()

    @staticmethod
    def _normalize(url: str) -> str:
        """Normalize a URL for deduplication."""
        url = url.lower().strip()
        if url.startswith("https://"):
            url = url[8:]
        elif url.startswith("http://"):
            url = url[7:]
        return url.rstrip("/")


@dataclass
class User:
    """A bot user entity."""
    id: Optional[int] = None
    telegram_id: int = 0
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: UserRole = UserRole.USER
    is_blocked: bool = False
    submissions_count: int = 0
    created_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None

    @property
    def is_admin(self) -> bool:
        return self.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)

    @property
    def is_super_admin(self) -> bool:
        return self.role == UserRole.SUPER_ADMIN


@dataclass
class Submission:
    """A link submission record."""
    id: Optional[int] = None
    link_id: int = 0
    user_id: int = 0
    submitted_at: Optional[datetime] = None
    source: str = "manual"  # manual, group_scan, import
    is_duplicate: bool = False


@dataclass
class Backup:
    """A database backup record."""
    id: Optional[int] = None
    file_path: str = ""
    file_size: int = 0
    link_count: int = 0
    user_count: int = 0
    created_at: Optional[datetime] = None
    created_by: str = "system"


@dataclass
class ValidationResult:
    """Result of validating a link."""
    link: str
    is_valid: bool
    status: LinkStatus
    reason: Optional[str] = None
    response_code: Optional[int] = None
    checked_at: datetime = field(default_factory=datetime.utcnow)
