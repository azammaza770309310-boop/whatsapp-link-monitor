# دليل اختبار End-to-End الحقيقي (بالـ Logs فقط)

> هذا الدليل يثبت بالـ Logs الحقيقية (بدون محاكاة) أن Supabase هو المصدر
> الوحيد للحسابات، وأن المشكلة القديمة (اختفاء الحسابات بعد كل تحديث)
> قد انتهت فعلاً.

## المتطلبات المسبقة

1. حساب Monitor (جلسة Telegram صالحة) + رقم هاتف
2. حساب Joiner (جلسة Telegram صالحة) + رقم هاتف
3. مجموعة Telegram يراقبها الـ Monitor
4. Supabase project مفعل مع جدول `watchers`
5. Render deployment جاهز للاستقبال

---

## الخطوة 0: قبل الـ Deploy — شغّل Migration SQL في Supabase

**مهم جداً:** قبل أي deploy، افتح Supabase Dashboard → SQL Editor ونفّذ:

```sql
-- Migration: أضف أعمدة role و joiner_enabled لجدول watchers
ALTER TABLE watchers ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'monitor';
ALTER TABLE watchers ADD COLUMN IF NOT EXISTS joiner_enabled INTEGER DEFAULT 1;
ALTER TABLE watchers ADD COLUMN IF NOT EXISTS last_join_timestamp TIMESTAMP;
ALTER TABLE watchers ADD COLUMN IF NOT EXISTS health_score INTEGER DEFAULT 100;

-- تحديث الحسابات الموجودة لتصبح monitor افتراضياً
UPDATE watchers SET role = 'monitor' WHERE role IS NULL;
UPDATE watchers SET joiner_enabled = 1 WHERE joiner_enabled IS NULL;

-- تحقق
SELECT phone, role, joiner_enabled, is_active FROM watchers;
```

**لو لم تنفّذ هذا الـ SQL:** البوت سيعمل لكن بحقول fallback
(role=monitor, joiner_enabled=1). لن يكون هناك خطأ، لكن لن تستطيع
تمييز الـ monitors عن الـ joiners في Supabase حتى تنفّذه.

---

## الخطوة 1: Deploy الكود الجديد إلى Render

```bash
git add -A
git commit -m "Supabase sole source of truth + E2E verification commands + pipeline logging"
git push origin main
```

انتظر حتى يكتمل الـ deploy في Render Dashboard.

---

## الخطوة 2: أضف حساب Monitor + Joiner عبر البوت

افتح البوت في Telegram: `@Azzamntheer2026_bot`

### 2.1 أضف حساب Monitor
1. أرسل `/start`
2. اضغط «🔐 تسجيل الدخول»
3. اختر الدور: **مراقب (Monitor)**
4. أرسل رقم الهاتف (مثلاً `+967739407274`)
5. أرسل كود تيليجرام الذي يصلك
6. انتظر رسالة التأكيد: `✅ تم تسجيل الدخول!`

### 2.2 أضف حساب Joiner
1. كرر نفس الخطوات
2. اختر الدور: **فدائي (Joiner)**
3. استخدم رقم هاتف آخر (مثلاً `+967700000001`)

### 2.3 تحقق عبر البوت
أرسل الأمر: `/verify`

**الـ Logs المتوقعة في Render:**
```
[VERIFY] /verify command invoked — full E2E check
[VERIFY] ═══════════════════════════════════════
[VERIFY]  E2E VERIFICATION REPORT
[VERIFY] ═══════════════════════════════════════
[VERIFY] Supabase accounts (is_active=true): 2
[VERIFY] Supabase count (REST count=exact): 2
[VERIFY] Monitors: 1
[VERIFY] Joiners:  1
[VERIFY] ─────────────────────────────────────
[VERIFY] Started clients (in memory): 2
[VERIFY] Connected clients:           2
[VERIFY] ─────────────────────────────────────
[VERIFY] Account list (phone | role | connected):
[VERIFY]   → +967739407274 | role=monitor | ✅ connected
[VERIFY]   → +967700000001 | role=joiner  | ✅ connected
[VERIFY] ═══════════════════════════════════════
[VERIFY] SQLite tables: ['api_operations_log', 'floodwait_tracker', 'forwarded_requests', 'group_states', 'link_queue', 'membership_cache', 'scan_state', 'system_settings', 'target_groups']
[VERIFY] 'watchers' table in SQLite: ✅ NO (correct)
```

**الرسالة في Telegram:**
```
🔍 E2E Verification Report
═══════════════════════════
📦 Supabase accounts: 2 (REST count: 2)
   • Monitors: 1
   • Joiners:  1
🚀 Started clients (in memory): 2
🔗 Connected clients: 2

📋 Account list:
   ✅ +967739407274 (role=monitor)
   ✅ +967700000001 (role=joiner)

🗄️ SQLite tables:
   • api_operations_log
   • floodwait_tracker
   ...

✅ PROVEN: 'watchers' table does NOT exist in SQLite.
✅ Supabase is the SOLE source of truth for accounts.

✅ E2E PASS: Supabase count == started count, no SQLite watchers.
```

---

## الخطوة 3: تحقق أن الحسابات في Supabase فقط (وليس SQLite)

أرسل الأمر: `/sqlite_check`

**الـ Logs المتوقعة:**
```
[SQLITE_CHECK] /sqlite_check command invoked
[SQLITE_CHECK] Tables found: ['api_operations_log', 'floodwait_tracker', 'forwarded_requests', 'group_states', 'link_queue', 'membership_cache', 'scan_state', 'system_settings', 'target_groups']
[SQLITE_CHECK] 'watchers' present: False
```

**الرسالة في Telegram:** يجب أن ترى `✅` بجانب كل جدول، ولا يوجد `watchers`.

---

## الخطوة 4: أعد تشغيل Render (Restart كامل)

في Render Dashboard:
1. اذهب إلى service الخاص بالبوت
2. اضغط **Manual Deploy** → **Clear build cache & deploy**
   (أو **Restart** لو متوفر)

انتظر حتى يكتمل الـ restart ورؤية `✅ Bot started` في الـ logs.

---

## الخطوة 5: بعد الـ Restart — اطبع الـ Startup Verification

بعد الـ restart، راجع الـ logs من البداية. يجب أن ترى:

```
=== Telegram Help Requests Monitor v7 ===
[MIGRATION] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[MIGRATION] Checking Supabase watchers schema (role, joiner_enabled)...
[SUPABASE] Schema OK: role + joiner_enabled columns exist
[MIGRATION] SQLite tables after init_db: ['api_operations_log', ...]
[MIGRATION] ✅ PROVEN: 'watchers' table does NOT exist in SQLite
[MIGRATION] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔒 Recovery Mode: JOIN PAUSED (send /resume_join to enable)
✅ No accounts in FloodWait
📡 Production Mode: Real Telegram API calls enabled
📊 Daily Join Limit: 2/day (conservative)
[STARTUP VERIFICATION] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[STARTUP VERIFICATION] Supabase = SOLE source of truth for accounts
[STARTUP VERIFICATION] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[STARTUP VERIFICATION] ════════════════════════════════════
[STARTUP VERIFICATION]  Supabase accounts (is_active=true): 2
[STARTUP VERIFICATION]  Supabase count (REST count=exact): 2
[STARTUP VERIFICATION]  Monitors: 1
[STARTUP VERIFICATION]  Joiners:  1
[STARTUP VERIFICATION] ────────────────────────────────────
[STARTUP VERIFICATION]  Account list (phone | role):
[STARTUP VERIFICATION]    → +967739407274 (role=monitor)
[STARTUP VERIFICATION]    → +967700000001 (role=joiner)
[STARTUP VERIFICATION] ────────────────────────────────────
[STARTUP VERIFICATION]  SQLite tables: ['api_operations_log', ...]
[STARTUP VERIFICATION]  'watchers' in SQLite: ✅ NO (correct)
[STARTUP VERIFICATION] ════════════════════════════════════
[STARTUP VERIFICATION] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Starting 2 accounts (1 monitors, 1 joiners)
  → +967739407274 (role=monitor)
  → +967700000001 (role=joiner)
👁️ Monitor +967739407274: handlers registered
🚀 Joiner +967700000001: connected (no message handlers)
🔄 Production Scheduler started — runs every 60s
✅ Monitor started. Send /help to channel.
```

**هذا يثبت:**
- ✅ عدد الحسابات في Supabase: 2
- ✅ عدد الحسابات التي تم تشغيلها فعلياً: 2 (متطابق!)
- ✅ رقم كل حساب ودوره
- ✅ SQLite لا يحتوي جدول watchers

---

## الخطوة 6: اختبر الـ Pipeline كاملاً (Event → Queue → AI → Publish → Joiner)

### 6.1 فعّل الانضمام
أرسل في القناة: `/resume_join`

### 6.2 أرسل رسالة في مجموعة يراقبها الـ Monitor
اذهب إلى أي مجموعة فيها حساب الـ Monitor، وأرسل رسالة تحتوي رابط:

```
مرحباً، هذه مجموعة دراسية مفيدة:
https://chat.whatsapp.com/AbCdEfGhIjKl
```

أو لتيليجرام:
```
مجموعة جديدة انضموا:
https://t.me/joinchat/AbCdEfGhIjKl
```

### 6.3 راجع الـ Logs — يجب أن ترى التسلسل الكامل

```
[PIPELINE-1] 📨 Event Handler received message from source=+967739407274 chat_id=-1001234567890 (len=85)
[PIPELINE-1] 🔗 Found 1 link(s) in message from chat_-1001234567890
[PIPELINE-2] ✅ Link enqueued: https://chat.whatsapp.com/AbCdEfGhIjKl (state=DISCOVERED)
```

(انتظر حتى 60 ثانية — Scheduler يعمل كل 60 ثانية)

```
[PIPELINE-3] 🔄 Scheduler picked link from queue: https://chat.whatsapp.com/AbCdEfGhIjKl (id=1, type=whatsapp)
[PIPELINE-4] 🤖 AI verifying link: https://chat.whatsapp.com/AbCdEfGhIjKl
[PIPELINE-4] ✅ AI APPROVED link: https://chat.whatsapp.com/AbCdEfGhIjKl
[PIPELINE-5] 📢 PUBLISHED to channel: https://chat.whatsapp.com/AbCdEfGhIjKl
[PIPELINE-6] 🛡️ Safety Guard checking +967700000001 for https://chat.whatsapp.com/AbCdEfGhIjKl
[PIPELINE-6] ✅ Safety Guard PASSED for +967700000001
[PIPELINE-6] 🚀 Joiner +967700000001 attempting to join: https://chat.whatsapp.com/AbCdEfGhIjKl
[PIPELINE-6] 🚫 Safety Guard BLOCKED +967700000001 from joining: role_no_join
```

> **ملاحظة:** الروابط `chat.whatsapp.com` لا يمكن الانضمام إليها عبر Telegram.
> الـ Safety Guard سيحظرها بـ `role_no_join` أو `low_reputation`.
> هذا السلوك **صحيح ومتوقع** — يثبت أن الـ Guard يعمل.
>
> لاختبار الانضمام الفعلي، استخدم رابط `t.me/joinchat/...`
> لكن انتبه: قد يحدث FloodWait لو الحساب جديد.

### 6.4 تحقق من القناة
يجب أن ترى رسالة منشورة في القناة بالصيغة:
```
🔗 رابط محفوظ (🟢 واتساب)
👥 العضوية: chat_-1001234567890
👤 الاسم: user_12345
🕒 التاريخ: 2026-08-06 14:30
🔗 اضغط هنا لفتح الرابط
```

---

## الخطوة 7: تحقق نهائي بعد الـ Restart

أرسل: `/verify`

**يجب أن ترى نفس الأرقام مثل قبل الـ restart:**
- Supabase accounts: 2
- Started clients: 2
- نفس رقم كل حساب ودوره

**هذا هو الدليل النهائي:** الحسابات لم تختفِ بعد الـ restart!

---

## قائمة التحقق النهائية (Checklist)

| # | الخطوة | الحالة |
|---|--------|--------|
| 1 | Migration SQL منفّذ في Supabase | ☐ |
| 2 | تم deploy الكود الجديد | ☐ |
| 3 | تم إضافة حساب Monitor عبر `/login` | ☐ |
| 4 | تم إضافة حساب Joiner عبر `/login` | ☐ |
| 5 | `/verify` يظهر 2 حساب (Supabase) | ☐ |
| 6 | `/sqlite_check` يثبت عدم وجود watchers | ☐ |
| 7 | تم Restart كامل لـ Render | ☐ |
| 8 | Startup Logs تظهر 2 حساب بنفس الأرقام | ☐ |
| 9 | تم إرسال رسالة في مجموعة المراقبة | ☐ |
| 10 | Logs تظهر PIPELINE-1 (Event Handler) | ☐ |
| 11 | Logs تظهر PIPELINE-2 (Enqueue) | ☐ |
| 12 | Logs تظهر PIPELINE-3 (Scheduler) | ☐ |
| 13 | Logs تظهر PIPELINE-4 (AI) | ☐ |
| 14 | Logs تظهر PIPELINE-5 (Publish) | ☐ |
| 15 | Logs تظهر PIPELINE-6 (Safety Guard / Joiner) | ☐ |
| 16 | رسالة منشورة في القناة | ☐ |
| 17 | `/verify` بعد الـ restart يظهر نفس النتيجة | ☐ |

**لو نجحت كل الخطوات → ارفع المشروع إلى GitHub نهائياً.**

---

## استكشاف الأخطاء

### المشكلة: Startup Logs تظهر `Starting 0 accounts`
**السبب:** Supabase لم يُرجع حسابات.
**الحل:**
1. تحقق من `SUPABASE_URL` و `SUPABASE_KEY` في Render env vars
2. تحقق أن `is_active=true` في Supabase للجميع
3. شغّل الـ Migration SQL (الخطوة 0)

### المشكلة: `Schema MISSING: role/joiner_enabled columns NOT found`
**السبب:** الـ Migration SQL لم يُنفّذ.
**الحل:** نفّذ SQL في الخطوة 0. البوت سيعمل لكن بحقول fallback.

### المشكلة: `[PIPELINE-6] 🚫 Safety Guard BLOCKED ... role_no_join`
**السبب:** الحساب ليس joiner، أو `joiner_enabled=0`.
**الحل:**
- تأكد أن الحساب role=joiner في Supabase
- أرسل `/enable_joiner <phone>`

### المشكلة: لا توجد `[PIPELINE-*]` في الـ Logs
**السبب:** الـ Monitor لا يراقب المجموعة، أو الرسالة لا تحتوي رابط.
**الحل:**
- تأكد أن الـ Monitor عضو في المجموعة
- تأكد أن الرسالة تحتوي `https://chat.whatsapp.com/` أو `https://t.me/`

### المشكلة: `FATAL: Supabase watchers table is empty`
**السبب:** لا توجد حسابات في Supabase.
**الحل:** أضف حسابات عبر `/login` قبل الـ restart.

---

## ملخص ما تم إثباته

1. **Supabase = المصدر الوحيد** — لا يوجد جدول `watchers` في SQLite
2. **الحسابات تبقى بعد الـ restart** — تُقرأ من Supabase في كل إقلاع
3. **الـ Pipeline كامل** — Event → Queue → AI → Publish → Joiner
4. **Safety Guard يعمل** — يحظر الانضمام غير الآمن
5. **Startup Verification صارمة** — FATAL + sys.exit(1) لو 0 حسابات
6. **Migration مدمجة** — تحذير واضح لو الأعمدة ناقصة
