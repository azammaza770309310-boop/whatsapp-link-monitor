"""Add indexes for performance."""
from src.infrastructure.database.connection import Database


async def upgrade(db: Database) -> None:
    """Create performance indexes."""
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_links_normalized_url "
        "ON links(normalized_url)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_links_category ON links(category)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_links_status ON links(status)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_links_content_hash ON links(content_hash)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_links_submitted_by ON links(submitted_by)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_links_created_at ON links(created_at)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_submissions_user_id ON submissions(user_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_submissions_link_id ON submissions(link_id)"
    )
