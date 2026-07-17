"""Rate limiting service using sliding window."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from src.core.exceptions import RateLimitExceededError
from src.core.logging import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """Sliding window rate limiter per user."""

    def __init__(self, per_minute: int = 10, burst: int = 20) -> None:
        self._per_minute = per_minute
        self._burst = burst
        self._requests: Dict[int, Deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, user_id: int) -> bool:
        """Check if user is within rate limit. Raises if exceeded."""
        async with self._lock:
            now = time.time()
            window_start = now - 60.0
            requests = self._requests[user_id]

            # Remove old entries
            while requests and requests[0] < window_start:
                requests.popleft()

            if len(requests) >= self._burst:
                logger.warning(
                    "Rate limit exceeded (burst)",
                    extra={
                        "extra_data": {
                            "user_id": user_id,
                            "limit": self._burst,
                        }
                    },
                )
                raise RateLimitExceededError(user_id, self._burst)

            if len(requests) >= self._per_minute:
                logger.warning(
                    "Rate limit exceeded (per minute)",
                    extra={
                        "extra_data": {
                            "user_id": user_id,
                            "limit": self._per_minute,
                        }
                    },
                )
                raise RateLimitExceededError(user_id, self._per_minute)

            requests.append(now)
            return True

    async def reset(self, user_id: int) -> None:
        """Reset rate limit for a user."""
        async with self._lock:
            self._requests.pop(user_id, None)
