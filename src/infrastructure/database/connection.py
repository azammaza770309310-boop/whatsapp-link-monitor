"""SQLite database connection management."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import aiosqlite

from src.core.config import DatabaseConfig
from src.core.exceptions import DatabaseError
from src.core.logging import get_logger

logger = get_logger(__name__)


class Database:
    """Async SQLite database wrapper."""

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Establish database connection."""
        if self._conn is not None:
            return

        try:
            Path(self._config.path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(
                self._config.path,
                timeout=30.0,
            )
            await self._conn.execute(
                f"PRAGMA journal_mode={self._config.journal_mode}"
            )
            await self._conn.execute(
                f"PRAGMA busy_timeout={self._config.busy_timeout}"
            )
            await self._conn.execute(
                f"PRAGMA synchronous={self._config.synchronous}"
            )
            await self._conn.commit()
            logger.info(
                "Database connected",
                extra={"extra_data": {"path": self._config.path}},
            )
        except Exception as e:
            raise DatabaseError(f"Failed to connect to database: {e}") from e

    async def disconnect(self) -> None:
        """Close database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("Database disconnected")

    @property
    def connection(self) -> aiosqlite.Connection:
        """Get the current connection. Must be connected first."""
        if self._conn is None:
            raise DatabaseError("Database not connected")
        return self._conn

    async def execute(self, sql: str, params: tuple = ()) -> None:
        """Execute a write statement."""
        async with self._lock:
            try:
                await self._conn.execute(sql, params)
                await self._conn.commit()
            except Exception as e:
                raise DatabaseError(f"Execute failed: {e}") from e

    async def executemany(self, sql: str, params_list) -> None:
        """Execute multiple statements."""
        async with self._lock:
            try:
                await self._conn.executemany(sql, params_list)
                await self._conn.commit()
            except Exception as e:
                raise DatabaseError(f"Executemany failed: {e}") from e

    async def fetchone(self, sql: str, params: tuple = ()):
        """Fetch a single row."""
        try:
            cursor = await self._conn.execute(sql, params)
            return await cursor.fetchone()
        except Exception as e:
            raise DatabaseError(f"Fetchone failed: {e}") from e

    async def fetchall(self, sql: str, params: tuple = ()):
        """Fetch all rows."""
        try:
            cursor = await self._conn.execute(sql, params)
            return await cursor.fetchall()
        except Exception as e:
            raise DatabaseError(f"Fetchall failed: {e}") from e
