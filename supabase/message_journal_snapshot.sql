-- =====================================================================
-- message_journal_snapshot — Supabase durability mirror [PR-6]
-- =====================================================================
-- مرآة Supabase لجدول message_journal المحلي (SQLite WAL). تتيح استرجاع
-- الرسائل المعرّضة للحذف بعد إعادة تشغيل/انهيار النظام.
--
-- الكود (bot.py: _journal_snapshot_loop) يكتشف غياب الجدول تلقائياً
-- (404 / "does not exist") ويسجّل هذا الـSQL في اللوق كل ساعة، دون أن
-- يكسر الـmain pipeline. هذا الملف جاهز للتنفيذ اليدوي في Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS message_journal_snapshot (
    chat_id         BIGINT NOT NULL,
    msg_id          BIGINT NOT NULL,
    raw_text        TEXT,
    source_phone    TEXT,
    chat_title      TEXT,
    chat_username    TEXT,
    chat_link_type  TEXT,
    sender_id       BIGINT,
    sender_name     TEXT,
    state           TEXT NOT NULL,
    received_at     DOUBLE PRECISION,
    PRIMARY KEY (chat_id, msg_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_journal_snapshot_pk
    ON message_journal_snapshot (chat_id, msg_id);

CREATE INDEX IF NOT EXISTS idx_journal_snapshot_state
    ON message_journal_snapshot (state);

CREATE INDEX IF NOT EXISTS idx_journal_snapshot_received_at
    ON message_journal_snapshot (received_at);

-- =====================================================================
-- Row Level Security (موصى به للإنتاج)
-- =====================================================================
ALTER TABLE message_journal_snapshot ENABLE ROW LEVEL SECURITY;

-- service_role: وصول كامل (البوت يستخدم SERVICE_ROLE_KEY)
CREATE POLICY IF NOT EXISTS "service_role full access journal_snapshot"
    ON message_journal_snapshot
    FOR ALL
    TO service_role
    USING (true) WITH CHECK (true);

-- anon: ممنوع (لا وصول عام لبيانات تشغيلية حساسة)
CREATE POLICY IF NOT EXISTS "anon no access journal_snapshot"
    ON message_journal_snapshot
    FOR SELECT
    TO anon
    USING (false);
