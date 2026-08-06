"""SQLite implementation of IUserRepository."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from src.domain.entities import User, UserRole
from src.domain.repositories import IUserRepository
from src.infrastructure.database.connection import Database


class SqliteUserRepository(IUserRepository):
    """SQLite-based user repository."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def save(self, user: User) -> User:
        if user.id is not None:
            await self._db.execute(
                """
                UPDATE users SET
                    username = ?, first_name = ?, last_name = ?, role = ?,
                    is_blocked = ?, submissions_count = ?, last_active_at = ?
                WHERE telegram_id = ?
                """,
                (
                    user.username,
                    user.first_name,
                    user.last_name,
                    user.role.value,
                    1 if user.is_blocked else 0,
                    user.submissions_count,
                    datetime.utcnow().isoformat(),
                    user.telegram_id,
                ),
            )
            return user

        await self._db.execute(
            """
            INSERT INTO users (telegram_id, username, first_name, last_name, role, is_blocked, submissions_count, last_active_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user.telegram_id,
                user.username,
                user.first_name,
                user.last_name,
                user.role.value,
                1 if user.is_blocked else 0,
                user.submissions_count,
                datetime.utcnow().isoformat(),
            ),
        )
        row = await self._db.fetchone(
            "SELECT last_insert_rowid()"
        )
        user.id = row[0] if row else None
        return user

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        row = await self._db.fetchone(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,),
        )
        return self._row_to_user(row) if row else None

    async def update_role(self, telegram_id: int, role: UserRole) -> bool:
        await self._db.execute(
            "UPDATE users SET role = ? WHERE telegram_id = ?",
            (role.value, telegram_id),
        )
        return True

    async def block(self, telegram_id: int) -> bool:
        await self._db.execute(
            "UPDATE users SET is_blocked = 1 WHERE telegram_id = ?",
            (telegram_id,),
        )
        return True

    async def unblock(self, telegram_id: int) -> bool:
        await self._db.execute(
            "UPDATE users SET is_blocked = 0 WHERE telegram_id = ?",
            (telegram_id,),
        )
        return True

    async def list_admins(self) -> List[User]:
        rows = await self._db.fetchall(
            "SELECT * FROM users WHERE role IN ('admin', 'super_admin') AND is_blocked = 0"
        )
        return [self._row_to_user(row) for row in rows]

    async def count(self) -> int:
        row = await self._db.fetchone("SELECT COUNT(*) FROM users")
        return row[0] if row else 0

    @staticmethod
    def _row_to_user(row) -> User:
        return User(
            id=row[0],
            telegram_id=row[1],
            username=row[2],
            first_name=row[3],
            last_name=row[4],
            role=UserRole(row[5]) if row[5] else UserRole.USER,
            is_blocked=bool(row[6]),
            submissions_count=row[7],
            created_at=datetime.fromisoformat(row[8]) if row[8] else None,
            last_active_at=datetime.fromisoformat(row[9]) if row[9] else None,
        )
