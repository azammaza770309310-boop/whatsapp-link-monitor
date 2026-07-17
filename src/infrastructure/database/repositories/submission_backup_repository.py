"""SQLite implementations for submission and backup repositories."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

from src.domain.entities import Backup, Submission
from src.domain.repositories import IBackupRepository, ISubmissionRepository
from src.infrastructure.database.connection import Database


class SqliteSubmissionRepository(ISubmissionRepository):
    def __init__(self, database: Database) -> None:
        self._db = database

    async def save(self, submission: Submission) -> Submission:
        await self._db.execute(
            """
            INSERT INTO submissions (link_id, user_id, source, is_duplicate)
            VALUES (?, ?, ?, ?)
            """,
            (
                submission.link_id,
                submission.user_id,
                submission.source,
                1 if submission.is_duplicate else 0,
            ),
        )
        row = await self._db.fetchone("SELECT last_insert_rowid()")
        submission.id = row[0] if row else None
        return submission

    async def list_by_user(
        self, user_id: int, limit: int = 20
    ) -> List[Submission]:
        rows = await self._db.fetchall(
            """
            SELECT * FROM submissions
            WHERE user_id = ?
            ORDER BY submitted_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [
            Submission(
                id=r[0],
                link_id=r[1],
                user_id=r[2],
                submitted_at=datetime.fromisoformat(r[3]) if r[3] else None,
                source=r[4],
                is_duplicate=bool(r[5]),
            )
            for r in rows
        ]


class SqliteBackupRepository(IBackupRepository):
    def __init__(self, database: Database) -> None:
        self._db = database

    async def save(self, backup: Backup) -> Backup:
        await self._db.execute(
            """
            INSERT INTO backups (file_path, file_size, link_count, user_count, created_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                backup.file_path,
                backup.file_size,
                backup.link_count,
                backup.user_count,
                backup.created_by,
            ),
        )
        row = await self._db.fetchone("SELECT last_insert_rowid()")
        backup.id = row[0] if row else None
        return backup

    async def list_recent(self, limit: int = 10) -> List[Backup]:
        rows = await self._db.fetchall(
            "SELECT * FROM backups ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [
            Backup(
                id=r[0],
                file_path=r[1],
                file_size=r[2],
                link_count=r[3],
                user_count=r[4],
                created_at=datetime.fromisoformat(r[5]) if r[5] else None,
                created_by=r[6],
            )
            for r in rows
        ]

    async def delete_old(self, retention_days: int) -> int:
        cutoff = (datetime.utcnow() - timedelta(days=retention_days)).isoformat()
        row = await self._db.fetchone(
            "SELECT COUNT(*) FROM backups WHERE created_at < ?",
            (cutoff,),
        )
        count = row[0] if row else 0
        await self._db.execute(
            "DELETE FROM backups WHERE created_at < ?",
            (cutoff,),
        )
        return count
