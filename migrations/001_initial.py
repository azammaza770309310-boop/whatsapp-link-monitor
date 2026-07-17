"""Initial schema migration."""
from src.infrastructure.database.connection import Database


async def upgrade(db: Database) -> None:
    """Create initial tables."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            normalized_url TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL DEFAULT 'other',
            status TEXT NOT NULL DEFAULT 'unverified',
            title TEXT,
            description TEXT,
            submitted_by INTEGER,
            submitted_by_name TEXT,
            source_group_id INTEGER,
            source_group_name TEXT,
            content_hash TEXT NOT NULL,
            message_text TEXT,
            verified_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL UNIQUE,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            is_blocked INTEGER NOT NULL DEFAULT 0,
            submissions_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active_at TIMESTAMP
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source TEXT NOT NULL DEFAULT 'manual',
            is_duplicate INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (link_id) REFERENCES links(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS backups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            file_size INTEGER NOT NULL DEFAULT 0,
            link_count INTEGER NOT NULL DEFAULT 0,
            user_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT NOT NULL DEFAULT 'system'
        )
    """)
