"""SQLite implementation of ILinkRepository."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from src.domain.entities import Link, LinkCategory, LinkStatus
from src.domain.repositories import ILinkRepository
from src.infrastructure.database.connection import Database


class SqliteLinkRepository(ILinkRepository):
    """SQLite-based link repository."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def save(self, link: Link) -> Link:
        """Insert or update a link."""
        if link.id is not None:
            return await self._update(link)

        # Insert new
        await self._db.execute(
            """
            INSERT INTO links (
                url, normalized_url, category, status, title, description,
                submitted_by, submitted_by_name, source_group_id, source_group_name,
                content_hash, message_text, verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                link.url,
                link.normalized_url,
                link.category.value,
                link.status.value,
                link.title,
                link.description,
                link.submitted_by,
                link.submitted_by_name,
                link.source_group_id,
                link.source_group_name,
                link.content_hash,
                link.message_text,
                link.verified_at.isoformat() if link.verified_at else None,
            ),
        )
        row = await self._db.fetchone(
            "SELECT last_insert_rowid()"
        )
        link.id = row[0] if row else None
        return link

    async def _update(self, link: Link) -> Link:
        """Update existing link."""
        await self._db.execute(
            """
            UPDATE links SET
                url = ?, normalized_url = ?, category = ?, status = ?,
                title = ?, description = ?, message_text = ?, verified_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                link.url,
                link.normalized_url,
                link.category.value,
                link.status.value,
                link.title,
                link.description,
                link.message_text,
                link.verified_at.isoformat() if link.verified_at else None,
                datetime.utcnow().isoformat(),
                link.id,
            ),
        )
        return link

    async def get_by_id(self, link_id: int) -> Optional[Link]:
        row = await self._db.fetchone(
            "SELECT * FROM links WHERE id = ?",
            (link_id,),
        )
        return self._row_to_link(row) if row else None

    async def get_by_url(self, url: str) -> Optional[Link]:
        normalized = Link._normalize(url)
        row = await self._db.fetchone(
            "SELECT * FROM links WHERE normalized_url = ?",
            (normalized,),
        )
        return self._row_to_link(row) if row else None

    async def list(
        self,
        category: Optional[LinkCategory] = None,
        status: Optional[LinkStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Link]:
        query = "SELECT * FROM links WHERE 1=1"
        params = []
        if category:
            query += " AND category = ?"
            params.append(category.value)
        if status:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = await self._db.fetchall(query, tuple(params))
        return [self._row_to_link(row) for row in rows]

    async def search(self, query: str, limit: int = 20) -> List[Link]:
        # Try FTS first
        try:
            rows = await self._db.fetchall(
                """
                SELECT l.* FROM links l
                JOIN links_fts f ON l.id = f.rowid
                WHERE links_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            )
            if rows:
                return [self._row_to_link(row) for row in rows]
        except Exception:
            pass

        # Fallback to LIKE
        rows = await self._db.fetchall(
            """
            SELECT * FROM links
            WHERE url LIKE ? OR title LIKE ? OR description LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (f"%{query}%", f"%{query}%", f"%{query}%", limit),
        )
        return [self._row_to_link(row) for row in rows]

    async def update_status(self, link_id: int, status: LinkStatus) -> bool:
        await self._db.execute(
            "UPDATE links SET status = ?, verified_at = ?, updated_at = ? WHERE id = ?",
            (
                status.value,
                datetime.utcnow().isoformat(),
                datetime.utcnow().isoformat(),
                link_id,
            ),
        )
        return True

    async def delete(self, link_id: int) -> bool:
        await self._db.execute("DELETE FROM links WHERE id = ?", (link_id,))
        return True

    async def count(
        self,
        category: Optional[LinkCategory] = None,
        status: Optional[LinkStatus] = None,
    ) -> int:
        query = "SELECT COUNT(*) FROM links WHERE 1=1"
        params = []
        if category:
            query += " AND category = ?"
            params.append(category.value)
        if status:
            query += " AND status = ?"
            params.append(status.value)
        row = await self._db.fetchone(query, tuple(params))
        return row[0] if row else 0

    async def get_expired(self, limit: int = 100) -> List[Link]:
        """Get links that haven't been verified recently."""
        rows = await self._db.fetchall(
            """
            SELECT * FROM links
            WHERE status = 'active'
            AND (verified_at IS NULL OR verified_at < datetime('now', '-1 day'))
            ORDER BY verified_at ASC NULLS FIRST
            LIMIT ?
            """,
            (limit,),
        )
        return [self._row_to_link(row) for row in rows]

    async def export_all(self) -> List[Link]:
        rows = await self._db.fetchall("SELECT * FROM links")
        return [self._row_to_link(row) for row in rows]

    async def import_links(self, links: List[Link]) -> int:
        count = 0
        for link in links:
            existing = await self.get_by_url(link.url)
            if existing:
                continue
            await self.save(link)
            count += 1
        return count

    @staticmethod
    def _row_to_link(row) -> Link:
        return Link(
            id=row[0],
            url=row[1],
            normalized_url=row[2],
            category=LinkCategory(row[3]) if row[3] else LinkCategory.OTHER,
            status=LinkStatus(row[4]) if row[4] else LinkStatus.UNVERIFIED,
            title=row[5],
            description=row[6],
            submitted_by=row[7],
            submitted_by_name=row[8],
            source_group_id=row[9],
            source_group_name=row[10],
            content_hash=row[11],
            message_text=row[12],
            verified_at=datetime.fromisoformat(row[13]) if row[13] else None,
            created_at=datetime.fromisoformat(row[14]) if row[14] else None,
            updated_at=datetime.fromisoformat(row[15]) if row[15] else None,
        )
