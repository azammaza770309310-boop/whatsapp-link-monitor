"""Backup background job."""
from __future__ import annotations

from src.application.services.backup_service import BackupService
from src.core.logging import get_logger

logger = get_logger(__name__)


class BackupJob:
    """Periodic job to create database backups."""

    def __init__(
        self,
        backup_service: BackupService,
        retention_days: int = 30,
    ) -> None:
        self._backup_service = backup_service
        self._retention_days = retention_days

    async def run(self) -> None:
        """Run one backup cycle."""
        try:
            backup = await self._backup_service.create_backup()
            if backup:
                # Cleanup old backups
                deleted = await self._backup_service.cleanup_old(
                    self._retention_days
                )
                logger.info(
                    "Backup job complete",
                    extra={
                        "extra_data": {
                            "backup_id": backup.id,
                            "old_deleted": deleted,
                        }
                    },
                )
        except Exception as e:
            logger.error(f"Backup job error: {e}", exc_info=True)
