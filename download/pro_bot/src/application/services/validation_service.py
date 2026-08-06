"""Link validation service - checks if links are still valid."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List, Optional

import aiohttp

from src.core.logging import get_logger
from src.domain.entities import Link, LinkStatus, ValidationResult
from src.domain.repositories import ILinkRepository

logger = get_logger(__name__)

# Markers indicating an expired/revoked link
EXPIRED_MARKERS = [
    "invite link revoked",
    "this group invite link has been revoked",
    "this invite link has expired",
    "link expired",
    "invalid invite link",
    "this group cannot be joined",
    "group has been changed",
    "the link is no longer valid",
    "this link has been revoked",
    "this community invite link has been revoked",
    "page not found",
    "invite link invalid",
]


class ValidationService:
    """Validates WhatsApp links via HTTP requests."""

    def __init__(
        self,
        link_repo: ILinkRepository,
        timeout: int = 10,
        max_concurrent: int = 5,
    ) -> None:
        self._link_repo = link_repo
        self._timeout = timeout
        self._max_concurrent = max_concurrent
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout),
                headers=self._headers,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def validate_link(self, url: str) -> ValidationResult:
        """Validate a single link."""
        # Skip non-checkable URLs (e.g., wa.me direct chats)
        lower = url.lower()
        if not any(
            x in lower
            for x in ("chat.whatsapp.com", "wa.me/message", "api.whatsapp.com/message")
        ):
            return ValidationResult(
                link=url,
                is_valid=True,
                status=LinkStatus.ACTIVE,
                reason="not_checkable",
            )

        normalized = url.strip()
        if not normalized.startswith("http"):
            normalized = "https://" + normalized

        async with self._semaphore:
            try:
                session = await self._get_session()
                async with session.get(
                    normalized, allow_redirects=True, ssl=False
                ) as resp:
                    if resp.status == 404:
                        return ValidationResult(
                            link=url,
                            is_valid=False,
                            status=LinkStatus.INVALID,
                            reason="http_404",
                            response_code=404,
                        )
                    if resp.status >= 500:
                        # Server error, don't change status
                        return ValidationResult(
                            link=url,
                            is_valid=True,
                            status=LinkStatus.ACTIVE,
                            reason=f"server_error_{resp.status}",
                            response_code=resp.status,
                        )
                    if resp.status == 200:
                        try:
                            text = await resp.text(errors="ignore")
                        except Exception:
                            return ValidationResult(
                                link=url,
                                is_valid=True,
                                status=LinkStatus.ACTIVE,
                                reason="no_body",
                                response_code=200,
                            )
                        text_lower = text.lower()
                        for marker in EXPIRED_MARKERS:
                            if marker in text_lower:
                                status = (
                                    LinkStatus.REVOKED
                                    if "revoked" in marker
                                    else LinkStatus.EXPIRED
                                )
                                return ValidationResult(
                                    link=url,
                                    is_valid=False,
                                    status=status,
                                    reason=f"marker:{marker[:30]}",
                                    response_code=200,
                                )
                        return ValidationResult(
                            link=url,
                            is_valid=True,
                            status=LinkStatus.ACTIVE,
                            reason="verified",
                            response_code=200,
                        )
                    return ValidationResult(
                        link=url,
                        is_valid=True,
                        status=LinkStatus.ACTIVE,
                        reason=f"http_{resp.status}",
                        response_code=resp.status,
                    )
            except asyncio.TimeoutError:
                return ValidationResult(
                    link=url,
                    is_valid=True,
                    status=LinkStatus.ACTIVE,
                    reason="timeout",
                )
            except aiohttp.ClientError as e:
                return ValidationResult(
                    link=url,
                    is_valid=True,
                    status=LinkStatus.ACTIVE,
                    reason=f"client_error:{type(e).__name__}",
                )
            except Exception as e:
                logger.error(f"Validation error for {url}: {e}")
                return ValidationResult(
                    link=url,
                    is_valid=True,
                    status=LinkStatus.ACTIVE,
                    reason=f"error:{type(e).__name__}",
                )

    async def validate_batch(self, links: List[Link]) -> List[ValidationResult]:
        """Validate multiple links concurrently."""
        tasks = [self.validate_link(link.url) for link in links]
        return await asyncio.gather(*tasks, return_exceptions=False)

    async def validate_expired(self, limit: int = 50) -> dict:
        """Re-validate links that haven't been checked recently."""
        links = await self._link_repo.get_expired(limit=limit)
        if not links:
            return {"checked": 0, "expired": 0, "revoked": 0, "still_valid": 0}

        results = await self.validate_batch(links)

        expired_count = 0
        revoked_count = 0
        still_valid = 0

        for link, result in zip(links, results):
            if not result.is_valid:
                await self._link_repo.update_status(link.id, result.status)
                if result.status == LinkStatus.EXPIRED:
                    expired_count += 1
                elif result.status == LinkStatus.REVOKED:
                    revoked_count += 1
            else:
                # Update verified_at timestamp
                await self._link_repo.update_status(link.id, LinkStatus.ACTIVE)
                still_valid += 1

        logger.info(
            "Validation batch complete",
            extra={
                "extra_data": {
                    "checked": len(links),
                    "expired": expired_count,
                    "revoked": revoked_count,
                    "still_valid": still_valid,
                }
            },
        )

        return {
            "checked": len(links),
            "expired": expired_count,
            "revoked": revoked_count,
            "still_valid": still_valid,
        }
