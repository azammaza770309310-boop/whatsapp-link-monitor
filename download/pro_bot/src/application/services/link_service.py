"""Link service - core use cases for link management."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import List, Optional, Tuple

from src.core.exceptions import ValidationError
from src.core.logging import get_logger
from src.domain.entities import Link, LinkCategory, LinkStatus, Submission
from src.domain.repositories import (
    ILinkRepository,
    ISubmissionRepository,
    IUserRepository,
)

logger = get_logger(__name__)

# Comprehensive WhatsApp link pattern
WHATSAPP_LINK_PATTERN = re.compile(
    r"""
    (?:https?://)?
    (?:
        chat\.whatsapp\.com
      | whatsapp\.com/channel
      | whatsapp\.com/contact
      | wa\.me
      | api\.whatsapp\.com
      | l\.whatsapp\.com
    )
    [^\s<>"'\)\]]*
    """,
    re.IGNORECASE | re.VERBOSE,
)


class LinkService:
    """Application service for link management."""

    def __init__(
        self,
        link_repo: ILinkRepository,
        user_repo: IUserRepository,
        submission_repo: ISubmissionRepository,
    ) -> None:
        self._link_repo = link_repo
        self._user_repo = user_repo
        self._submission_repo = submission_repo

    @staticmethod
    def extract_links(text: str) -> List[str]:
        """Extract all WhatsApp links from text."""
        if not text:
            return []
        matches = WHATSAPP_LINK_PATTERN.findall(text)
        seen = set()
        unique = []
        for link in matches:
            link = link.rstrip(".,;:!?)]}>\"'")
            normalized = Link._normalize(link)
            if normalized not in seen:
                seen.add(normalized)
                unique.append(link)
        return unique

    @staticmethod
    def categorize_link(url: str) -> LinkCategory:
        """Categorize a WhatsApp link by URL pattern."""
        lower = url.lower()
        if "chat.whatsapp.com" in lower:
            return LinkCategory.GROUP_INVITE
        if "/channel" in lower:
            return LinkCategory.CHANNEL
        if "/message" in lower:
            return LinkCategory.MESSAGE_LINK
        if "wa.me" in lower and "/message" not in lower:
            return LinkCategory.DIRECT_CHAT
        if "api.whatsapp.com/send" in lower:
            return LinkCategory.API_SEND
        if "api.whatsapp.com/q" in lower:
            return LinkCategory.QR_CODE
        if "l.whatsapp.com" in lower:
            return LinkCategory.SHORT_LINK
        return LinkCategory.OTHER

    async def submit_link(
        self,
        url: str,
        submitted_by: Optional[int] = None,
        submitted_by_name: Optional[str] = None,
        source_group_id: Optional[int] = None,
        source_group_name: Optional[str] = None,
        message_text: Optional[str] = None,
    ) -> Tuple[Link, bool]:
        """
        Submit a new link. Returns (link, is_duplicate).
        Raises ValidationError if URL is invalid.
        """
        # Validate
        if not url or not WHATSAPP_LINK_PATTERN.search(url):
            raise ValidationError(f"Invalid WhatsApp link: {url}")

        # Check for duplicate
        existing = await self._link_repo.get_by_url(url)
        if existing:
            # Record submission
            if submitted_by:
                submission = Submission(
                    link_id=existing.id or 0,
                    user_id=submitted_by,
                    source="manual" if not source_group_id else "group_scan",
                    is_duplicate=True,
                )
                await self._submission_repo.save(submission)
            logger.info(
                "Duplicate link submission",
                extra={"extra_data": {"url": url, "existing_id": existing.id}},
            )
            return existing, True

        # Create new link
        category = self.categorize_link(url)
        link = Link(
            url=url,
            normalized_url=Link._normalize(url),
            category=category,
            status=LinkStatus.UNVERIFIED,
            submitted_by=submitted_by,
            submitted_by_name=submitted_by_name,
            source_group_id=source_group_id,
            source_group_name=source_group_name,
            message_text=message_text,
        )
        saved = await self._link_repo.save(link)

        # Record submission
        if submitted_by:
            submission = Submission(
                link_id=saved.id or 0,
                user_id=submitted_by,
                source="manual" if not source_group_id else "group_scan",
                is_duplicate=False,
            )
            await self._submission_repo.save(submission)

        logger.info(
            "New link submitted",
            extra={
                "extra_data": {
                    "url": url,
                    "category": category.value,
                    "submitted_by": submitted_by,
                }
            },
        )
        return saved, False

    async def list_links(
        self,
        category: Optional[LinkCategory] = None,
        status: Optional[LinkStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Link]:
        return await self._link_repo.list(category, status, limit, offset)

    async def search_links(self, query: str, limit: int = 20) -> List[Link]:
        if not query or len(query) < 2:
            raise ValidationError("Search query must be at least 2 characters")
        return await self._link_repo.search(query, limit)

    async def delete_link(self, link_id: int) -> bool:
        return await self._link_repo.delete(link_id)

    async def get_stats(self) -> dict:
        """Get statistics summary."""
        total = await self._link_repo.count()
        active = await self._link_repo.count(status=LinkStatus.ACTIVE)
        expired = await self._link_repo.count(status=LinkStatus.EXPIRED)
        revoked = await self._link_repo.count(status=LinkStatus.REVOKED)
        unverified = await self._link_repo.count(status=LinkStatus.UNVERIFIED)
        user_count = await self._user_repo.count()

        # Count by category
        by_category = {}
        for cat in LinkCategory:
            count = await self._link_repo.count(category=cat)
            if count > 0:
                by_category[cat.value] = count

        return {
            "total_links": total,
            "active": active,
            "expired": expired,
            "revoked": revoked,
            "unverified": unverified,
            "total_users": user_count,
            "by_category": by_category,
        }

    async def export_all(self) -> List[Link]:
        """Export all links for backup."""
        return await self._link_repo.export_all()

    async def import_links(self, links: List[Link]) -> int:
        """Import links. Returns count of newly imported."""
        return await self._link_repo.import_links(links)
