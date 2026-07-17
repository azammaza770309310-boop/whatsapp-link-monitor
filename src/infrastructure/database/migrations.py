"""Database migration system."""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import List

from src.core.exceptions import MigrationError
from src.core.logging import get_logger
from src.infrastructure.database.connection import Database

logger = get_logger(__name__)


class MigrationRunner:
    """Runs database migrations in order."""

    def __init__(self, database: Database, migrations_dir: str = "migrations") -> None:
        self._database = database
        self._migrations_dir = Path(migrations_dir)

    async def ensure_migrations_table(self) -> None:
        """Create migrations tracking table if not exists."""
        await self._database.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    async def get_applied(self) -> List[str]:
        """Get list of applied migration names."""
        rows = await self._database.fetchall(
            "SELECT name FROM _migrations ORDER BY name"
        )
        return [row[0] for row in rows]

    async def run(self) -> int:
        """Run all pending migrations. Returns count of applied."""
        await self.ensure_migrations_table()
        applied = await self.get_applied()

        if not self._migrations_dir.exists():
            logger.warning(
                "Migrations directory not found",
                extra={"extra_data": {"path": str(self._migrations_dir)}},
            )
            return 0

        # Discover migration modules
        migration_files = sorted(
            f.stem
            for f in self._migrations_dir.glob("*.py")
            if f.stem != "__init__" and not f.name.startswith("_")
        )

        count = 0
        for name in migration_files:
            if name in applied:
                continue

            logger.info(f"Applying migration: {name}")
            try:
                await self._apply_migration(name)
                await self._database.execute(
                    "INSERT INTO _migrations (name) VALUES (?)",
                    (name,),
                )
                count += 1
                logger.info(f"Migration applied: {name}")
            except Exception as e:
                raise MigrationError(f"Migration {name} failed: {e}") from e

        return count

    async def _apply_migration(self, name: str) -> None:
        """Apply a single migration."""
        # Import the migration module dynamically
        import sys
        if str(self._migrations_dir.parent) not in sys.path:
            sys.path.insert(0, str(self._migrations_dir.parent))

        module = importlib.import_module(f"migrations.{name}")
        if not hasattr(module, "upgrade"):
            raise MigrationError(
                f"Migration {name} missing upgrade() function"
            )
        await module.upgrade(self._database)
