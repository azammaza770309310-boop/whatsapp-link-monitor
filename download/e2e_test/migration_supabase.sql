-- ============================================================
-- Migration: إضافة أعمدة role و joiner_enabled لجدول watchers
-- ============================================================
-- شغّل هذا الـ SQL في Supabase Dashboard → SQL Editor
-- قبل عمل deploy للبوت.
-- ============================================================

-- 1. أضف عمود role (monitor | joiner | backup)
ALTER TABLE watchers ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'monitor';

-- 2. أضف عمود joiner_enabled (1 = مفعّل، 0 = متوقف)
ALTER TABLE watchers ADD COLUMN IF NOT EXISTS joiner_enabled INTEGER DEFAULT 1;

-- 3. أضف عمود last_join_timestamp (لتتبع آخر انضمام)
ALTER TABLE watchers ADD COLUMN IF NOT EXISTS last_join_timestamp TIMESTAMP;

-- 4. أضف عمود health_score (0-100، صحة الحساب)
ALTER TABLE watchers ADD COLUMN IF NOT EXISTS health_score INTEGER DEFAULT 100;

-- 5. حدّث الحسابات الموجودة بالقيم الافتراضية
UPDATE watchers SET role = 'monitor' WHERE role IS NULL;
UPDATE watchers SET joiner_enabled = 1 WHERE joiner_enabled IS NULL;
UPDATE watchers SET health_score = 100 WHERE health_score IS NULL;

-- 6. تحقق من النتيجة
SELECT phone, display_name, role, joiner_enabled, is_active, health_score
FROM watchers
ORDER BY phone;

-- ============================================================
-- ملاحظات:
-- - لو ما عندك جدول watchers أصلاً، أنشئه أولاً:
-- ============================================================
-- CREATE TABLE IF NOT EXISTS watchers (
--     phone TEXT PRIMARY KEY,
--     display_name TEXT,
--     session_string TEXT NOT NULL,
--     is_active BOOLEAN DEFAULT true,
--     role TEXT DEFAULT 'monitor',
--     joiner_enabled INTEGER DEFAULT 1,
--     last_join_timestamp TIMESTAMP,
--     health_score INTEGER DEFAULT 100,
--     created_at TIMESTAMP DEFAULT NOW()
-- );
-- ============================================================

-- بعد تنفيذ هذا الـ SQL، الـ Bot سيعمل بشكل صحيح ويميز
-- بين حسابات Monitor (تراقب فقط) و Joiner (تنضم للمجموعات).
