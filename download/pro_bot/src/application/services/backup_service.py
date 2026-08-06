"""Backup service for database export/import."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from src.core.exceptions import BackupError
from src.core.logging import get_logger
from src.domain.entities import Backup, Link
from src.domain.repositories import IBackupRepository, ILinkRepository, IUserRepository

logger = get_logger(__name__)


class BackupService:
    """Service for creating and restoring backups."""

    def __init__(
        self,
        link_repo: ILinkRepository,
        user_repo: IUserRepository,
        backup_repo: IBackupRepository,
        backup_dir: str = "data/backups",
    ) -> None:
        self._link_repo = link_repo
        self._user_repo = user_repo
        self._backup_repo = backup_repo
        self._backup_dir = Path(backup_dir)

    async def create_backup(self, created_by: str = "system") -> Optional[Backup]:
        """Create a JSON backup of all links."""
        try:
            self._backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            file_path = self._backup_dir / f"backup_{timestamp}.json"

            links = await self._link_repo.export_all()
            user_count = await self._user_repo.count()

            data = {
                "version": 1,
                "created_at": datetime.utcnow().isoformat(),
                "created_by": created_by,
                "link_count": len(links),
                "user_count": user_count,
                "links": [
                    {
                        "url": l.url,
                        "category": l.category.value,
                        "status": l.status.value,
                        "title": l.title,
                        "description": l.description,
                        "submitted_by_name": l.submitted_by_name,
                        "source_group_name": l.source_group_name,
                        "message_text": l.message_text,
                        "created_at": l.created_at.isoformat() if l.created_at else None,
                    }
                    for l in links
                ],
            }

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            file_size = file_path.stat().st_size

            backup = Backup(
                file_path=str(file_path),
                file_size=file_size,
                link_count=len(links),
                user_count=user_count,
                created_by=created_by,
            )
            saved = await self._backup_repo.save(backup)

            logger.info(
                "Backup created",
                extra={
                    "extra_data": {
                        "path": str(file_path),
                        "size": file_size,
                        "links": len(links),
                    }
                },
            )
            return saved

        except Exception as e:
            logger.error(f"Backup failed: {e}")
            raise BackupError(f"Backup failed: {e}") from e

    async def restore_backup(self, file_path: str) -> int:
        """Restore links from a backup file. Returns count imported."""
        try:
            path = Path(file_path)
            if not path.exists():
                raise BackupError(f"Backup file not found: {file_path}")

            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            from src.domain.entities import LinkCategory, LinkStatus

            links: List[Link] = []
            for item in data.get("links", []):
                try:
                    link = Link(
                        url=item["url"],
                        category=LinkCategory(item.get("category", "other")),
                        status=LinkStatus(item.get("status", "unverified")),
                        title=item.get("title"),
                        description=item.get("description"),
                        submitted_by_name=item.get("submitted_by_name"),
                        source_group_name=item.get("source_group_name"),
                        message_text=item.get("message_text"),
                    )
                    links.append(link)
                except (KeyError, ValueError) as e:
                    logger.warning(f"Skipping invalid link entry: {e}")
                    continue

            count = await self._link_repo.import_links(links)
            logger.info(
                "Backup restored",
                extra={
                    "extra_data": {
                        "file": file_path,
                        "imported": count,
                        "total_in_file": len(data.get("links", [])),
                    }
                },
            )
            return count

        except json.JSONDecodeError as e:
            raise BackupError(f"Invalid backup file: {e}") from e
        except Exception as e:
            raise BackupError(f"Restore failed: {e}") from e

    async def cleanup_old(self, retention_days: int = 30) -> int:
        """Delete old backup records. Returns count deleted."""
        deleted = await self._backup_repo.delete_old(retention_days)
        if deleted > 0:
            logger.info(
                "Old backups cleaned up",
                extra={"extra_data": {"deleted": deleted, "retention_days": retention_days}},
            )
        return deleted

    async def list_recent(self, limit: int = 10) -> List[Backup]:
        """List recent backups."""
        return await self._backup_repo.list_recent(limit)
