-- =====================================================================
-- message_journal_snapshot — Supabase durability mirror [PR-6]
-- =====================================================================
-- مرآة Supabase لجدول message_journal المحلي (SQLite WAL). تتيح استرجاع
-- الرسائل المعرّضة للحذف بعد إعادة تشغيل/انهيار النظام.
--
-- الكود (bot.py: _journal_snapshot_loop) يكتشف غياب الجدول تلقائياً
-- (404 / "does not exist") ويسجّل هذا الـSQL في اللوق كل ساعة، دون أن
-- يكسر الـmain pipeline. هذا الملف جاهز للتنفيذ اليدوي في Supabase SQL Editor.
--
-- ملاحظة: PostgreSQL لا يدعم IF NOT EXISTS مع CREATE POLICY (خطأ syntax).
-- النمط الصحيح للـidempotency: DROP POLICY IF EXISTS ثم CREATE POLICY.

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

-- ملاحظة: PRIMARY KEY (chat_id, msg_id) ينشئ تلقائياً unique index على هذه
-- الأعمدة، لذا لا حاجة لـCREATE UNIQUE INDEX إضافي بنفس الأعمدة (تجنباً
-- لـduplicate index بلا فائدة).

CREATE INDEX IF NOT EXISTS idx_journal_snapshot_state
    ON message_journal_snapshot (state);

CREATE INDEX IF NOT EXISTS idx_journal_snapshot_received_at
    ON message_journal_snapshot (received_at);

-- =====================================================================
-- Row Level Security (موصى به للإنتاج)
-- =====================================================================
ALTER TABLE message_journal_snapshot ENABLE ROW LEVEL SECURITY;

-- service_role: وصول كامل (البوت يستخدم SERVICE_ROLE_KEY)
DROP POLICY IF EXISTS "service_role full access journal_snapshot"
    ON message_journal_snapshot;

CREATE POLICY "service_role full access journal_snapshot"
    ON message_journal_snapshot
    FOR ALL
    TO service_role
    USING (true) WITH CHECK (true);

-- anon: ممنوع (لا وصول عام لبيانات تشغيلية حساسة)
DROP POLICY IF EXISTS "anon no access journal_snapshot"
    ON message_journal_snapshot;

CREATE POLICY "anon no access journal_snapshot"
    ON message_journal_snapshot
    FOR SELECT
    TO anon
    USING (false);
