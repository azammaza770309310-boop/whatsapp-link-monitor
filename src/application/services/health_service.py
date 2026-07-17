"""Health monitoring service."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, Optional

from src.core.logging import get_logger
from src.domain.repositories import ILinkRepository, IUserRepository

logger = get_logger(__name__)


class HealthService:
    """Service for health monitoring and metrics."""

    def __init__(
        self,
        link_repo: ILinkRepository,
        user_repo: IUserRepository,
    ) -> None:
        self._link_repo = link_repo
        self._user_repo = user_repo
        self._started_at = time.time()
        self._last_error: Optional[str] = None
        self._last_error_at: Optional[datetime] = None

    def record_error(self, error: str) -> None:
        """Record an error for health monitoring."""
        self._last_error = error
        self._last_error_at = datetime.utcnow()
        logger.error(
            "Error recorded",
            extra={"extra_data": {"error": error}},
        )

    async def get_health(self) -> Dict[str, Any]:
        """Get health status."""
        uptime = time.time() - self._started_at
        try:
            link_count = await self._link_repo.count()
            user_count = await self._user_repo.count()
            status = "ok"
        except Exception as e:
            link_count = 0
            user_count = 0
            status = "degraded"
            self.record_error(str(e))

        return {
            "status": status,
            "uptime_seconds": int(uptime),
            "link_count": link_count,
            "user_count": user_count,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at.isoformat()
            if self._last_error_at
            else None,
            "timestamp": datetime.utcnow().isoformat(),
        }
