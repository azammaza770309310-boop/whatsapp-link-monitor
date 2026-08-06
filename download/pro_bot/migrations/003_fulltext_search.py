"""Add FTS for full-text search."""
from src.infrastructure.database.connection import Database


async def upgrade(db: Database) -> None:
    """Create full-text search virtual table."""
    await db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS links_fts
        USING fts5(
            url,
            title,
            description,
            content='links',
            content_rowid='id'
        )
    """)
    # Trigger to keep FTS in sync
    await db.execute("""
        CREATE TRIGGER IF NOT EXISTS links_ai AFTER INSERT ON links
        BEGIN
            INSERT INTO links_fts(rowid, url, title, description)
            VALUES (new.id, new.url, new.title, new.description);
        END
    """)
    await db.execute("""
        CREATE TRIGGER IF NOT EXISTS links_ad AFTER DELETE ON links
        BEGIN
            INSERT INTO links_fts(links_fts, rowid, url, title, description)
            VALUES ('delete', old.id, old.url, old.title, old.description);
        END
    """)
    await db.execute("""
        CREATE TRIGGER IF NOT EXISTS links_au AFTER UPDATE ON links
        BEGIN
            INSERT INTO links_fts(links_fts, rowid, url, title, description)
            VALUES ('delete', old.id, old.url, old.title, old.description);
            INSERT INTO links_fts(rowid, url, title, description)
            VALUES (new.id, new.url, new.title, new.description);
        END
    """)
