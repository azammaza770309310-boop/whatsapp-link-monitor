#!/usr/bin/env python3
"""Run database migrations manually."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import load_config
from src.core.logging import setup_logging, get_logger
from src.infrastructure.database.connection import Database
from src.infrastructure.database.migrations import MigrationRunner

logger = get_logger(__name__)


async def main():
    setup_logging("INFO", "text")
    config = load_config()
    db = Database(config.database)
    await db.connect()
    runner = MigrationRunner(db, migrations_dir="migrations")
    count = await runner.run()
    print(f"✅ Applied {count} migrations")
    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
