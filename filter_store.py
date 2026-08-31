#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
filter_store.py — Request Intent Engine v4.0 / المرحلة 5: سجل التشخيص
================================================================================
STAGE 5 of the v4.0 rebuild. جدول filter_decisions يحفظ كل قرار (ACCEPT وREJECT)
مع السبب الكامل — حتى نستطيع الإجابة على: «لماذا قُبِلت؟» و«لماذا رُفضت؟».

المخطط (يُنشأ أيضًا من link_system.init_production_tables — idempotent):
    filter_decisions(
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id       INTEGER,          -- مجموعة المصدر
        message_id    INTEGER,          -- رسالة المصدر
        text_hash     TEXT NOT NULL,    -- MD5 للنص القياسي (canonical)
        text_preview  TEXT,             -- أول 200 حرف (تشخيص)
        decision      TEXT NOT NULL,    -- ACCEPT | REJECT
        confidence    REAL DEFAULT 0,   -- ثقة الـAI (0..1)
        category      TEXT,             -- فئة التصنيف
        reason        TEXT,             -- سبب الرفض/القبول
        model         TEXT,             -- النموذج المستخدم
        latency_ms    INTEGER,          -- زمن نداء AI
        dedup_kind    TEXT,             -- exact|semantic|near (لو رُفض للتكرار)
        source_phone  TEXT,             -- الحساب الذي استلم الرسالة
        created_at    REAL              -- epoch seconds
    )

كل العمليات non-fatal: فشل الكتابة لا يكسر مسار الطلبات أبدًا (يُسجَّل debug فقط).
DecisionLogger يُغلف ProductionDB (مثل باقي أنظمة link_system).
"""

import hashlib
import logging
import time
from typing import Any, Dict, List, Optional

from text_normalizer import normalize as _tn_normalize


FILTER_DECISIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS filter_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    message_id INTEGER,
    text_hash TEXT NOT NULL,
    text_preview TEXT,
    decision TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    category TEXT,
    reason TEXT,
    model TEXT,
    latency_ms INTEGER,
    dedup_kind TEXT,
    source_phone TEXT,
    created_at REAL
)
"""

_FILTER_DECISIONS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_fd_created ON filter_decisions (created_at)",
    "CREATE INDEX IF NOT EXISTS idx_fd_decision ON filter_decisions (decision)",
    "CREATE INDEX IF NOT EXISTS idx_fd_text_hash ON filter_decisions (text_hash)",
)


def text_hash_of(raw_text: str) -> str:
    """MD5 للنص القياسي (canonical) — نفس البصمة التي يستعملها semantic_dedup."""
    canonical = _tn_normalize(raw_text or '').canonical
    return hashlib.md5(canonical.encode('utf-8', 'replace')).hexdigest()


class DecisionLogger:
    """يحفظ قرارات الفلتر في filter_decisions ويقرأها للتشخيص/الـAPI.

    كل الطرق non-fatal: أي استثناء → log debug + return False/[] (المسار لا يتأثر).
    """

    def __init__(self, prod_db):
        self.prod_db = prod_db
        self._ensured = False

    async def _conn(self):
        return await self.prod_db._conn()

    async def ensure_table(self) -> bool:
        """إنشاء الجدول لو لم يوجد (idempotent — link_system ينشئه أيضًا عند الإقلاع)."""
        if self._ensured:
            return True
        try:
            conn = await self._conn()
            await conn.execute(FILTER_DECISIONS_SCHEMA)
            for idx in _FILTER_DECISIONS_INDEXES:
                await conn.execute(idx)
            await conn.commit()
            self._ensured = True
            return True
        except Exception as e:
            logging.debug(f"[FILTER-STORE] ensure_table failed (non-fatal): {e}")
            return False

    async def log_decision(self,
                           *,
                           chat_id: int = 0,
                           message_id: int = 0,
                           raw_text: str = "",
                           decision: str = "REJECT",
                           confidence: float = 0.0,
                           category: str = "",
                           reason: str = "",
                           model: str = "",
                           latency_ms: int = 0,
                           dedup_kind: str = "",
                           source_phone: str = "") -> bool:
        """يحفظ قرارًا واحدًا. non-fatal — يُرجع False عند الفشل."""
        try:
            if not self._ensured:
                await self.ensure_table()
            conn = await self._conn()
            await conn.execute(
                """INSERT INTO filter_decisions
                   (chat_id, message_id, text_hash, text_preview, decision,
                    confidence, category, reason, model, latency_ms,
                    dedup_kind, source_phone, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _to_int(chat_id), _to_int(message_id),
                    text_hash_of(raw_text),
                    (raw_text or '')[:200],
                    str(decision or 'REJECT')[:16],
                    float(confidence or 0.0),
                    str(category or '')[:64],
                    str(reason or '')[:200],
                    str(model or '')[:64],
                    _to_int(latency_ms),
                    str(dedup_kind or '')[:16],
                    str(source_phone or '')[:32],
                    time.time(),
                ),
            )
            await conn.commit()
            return True
        except Exception as e:
            logging.debug(f"[FILTER-STORE] log_decision failed (non-fatal): {e}")
            return False

    async def recent_decisions(self, limit: int = 100) -> List[Dict[str, Any]]:
        """آخر N قرار (للـ/api/filter_stats). newest first."""
        try:
            if not self._ensured:
                await self.ensure_table()
            limit = max(1, min(int(limit), 500))
            conn = await self._conn()
            cursor = await conn.execute(
                """SELECT id, chat_id, message_id, text_hash, text_preview,
                          decision, confidence, category, reason, model,
                          latency_ms, dedup_kind, source_phone, created_at
                   FROM filter_decisions
                   ORDER BY id DESC
                   LIMIT ?""",
                (limit,),
            )
            rows = await cursor.fetchall()
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, r)) for r in rows]
        except Exception as e:
            logging.debug(f"[FILTER-STORE] recent_decisions failed (non-fatal): {e}")
            return []

    async def stats(self) -> Dict[str, Any]:
        """إحصاءات مجمّعة (للـ/api/filter_stats)."""
        out: Dict[str, Any] = {
            "total": 0, "accepts": 0, "rejects": 0,
            "avg_confidence": 0.0, "avg_latency_ms": 0.0,
            "by_category": {}, "by_reason": {}, "by_dedup_kind": {},
        }
        try:
            if not self._ensured:
                await self.ensure_table()
            conn = await self._conn()

            cursor = await conn.execute(
                """SELECT COUNT(*),
                          SUM(CASE WHEN decision='ACCEPT' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN decision='REJECT' THEN 1 ELSE 0 END),
                          AVG(confidence), AVG(latency_ms)
                   FROM filter_decisions""")
            row = await cursor.fetchone()
            if row and row[0]:
                out["total"] = row[0]
                out["accepts"] = row[1] or 0
                out["rejects"] = row[2] or 0
                out["avg_confidence"] = round(row[3] or 0.0, 3)
                out["avg_latency_ms"] = round(row[4] or 0.0, 1)

            cursor = await conn.execute(
                """SELECT category, COUNT(*) FROM filter_decisions
                   WHERE category IS NOT NULL AND category != ''
                   GROUP BY category ORDER BY COUNT(*) DESC LIMIT 25""")
            out["by_category"] = {r[0]: r[1] for r in await cursor.fetchall()}

            cursor = await conn.execute(
                """SELECT reason, COUNT(*) FROM filter_decisions
                   WHERE reason IS NOT NULL AND reason != ''
                   GROUP BY reason ORDER BY COUNT(*) DESC LIMIT 25""")
            out["by_reason"] = {r[0]: r[1] for r in await cursor.fetchall()}

            cursor = await conn.execute(
                """SELECT dedup_kind, COUNT(*) FROM filter_decisions
                   WHERE dedup_kind IS NOT NULL AND dedup_kind != ''
                   GROUP BY dedup_kind ORDER BY COUNT(*) DESC LIMIT 10""")
            out["by_dedup_kind"] = {r[0]: r[1] for r in await cursor.fetchall()}
        except Exception as e:
            logging.debug(f"[FILTER-STORE] stats failed (non-fatal): {e}")
        return out


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0
