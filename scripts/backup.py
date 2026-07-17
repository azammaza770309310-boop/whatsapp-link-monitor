#!/usr/bin/env python3
"""Create a manual backup."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import load_config
from src.core.logging import setup_logging, get_logger
from src.infrastructure.database.connection import Database
from src.infrastructure.database.migrations import MigrationRunner
from src.infrastructure.database.repositories.link_repository import SqliteLinkRepository
from src.infrastructure.database.repositories.submission_backup_repository import (
    SqliteBackupRepository,
)
from src.infrastructure.database.repositories.user_repository import SqliteUserRepository
from src.application.services.backup_service import BackupService

logger = get_logger(__name__)


async def main():
    setup_logging("INFO", "text")
    config = load_config()
    db = Database(config.database)
    await db.connect()
    runner = MigrationRunner(db, migrations_dir="migrations")
    await runner.run()

    link_repo = SqliteLinkRepository(db)
    user_repo = SqliteUserRepository(db)
    backup_repo = SqliteBackupRepository(db)
    backup_service = BackupService(link_repo, user_repo, backup_repo)

    backup = await backup_service.create_backup(created_by="manual")
    if backup:
        print(f"✅ Backup created: {backup.file_path}")
        print(f"   Size: {backup.file_size} bytes")
        print(f"   Links: {backup.link_count}")
    else:
        print("❌ Backup failed")
    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
