# Worklog — Supabase Sole Source of Truth + E2E Verification

---
Task ID: E2E-FINAL
Agent: main (Super Z)
Task: Make Supabase the SOLE source of truth for accounts, eliminate all SQLite watchers table references, add E2E verification commands, prepare for live E2E test with real logs.

Work Log:
- Audited `bot.py` (3967 lines) for all SQLite `watchers` table references.
- Found 7 critical bugs where code queried non-existent SQLite `watchers` table:
  - `/enable_joiner` (line ~2597): `UPDATE watchers SET joiner_enabled = 1`
  - `/disable_joiner` (line ~2610): `UPDATE watchers SET joiner_enabled = 0`
  - `/join_status` (line ~2625): `SELECT joiner_enabled, last_join_timestamp, health_score FROM watchers`
  - `_safety_guard` check #6 (line ~3131): `SELECT last_join_timestamp FROM watchers`
  - `_get_daily_limit` (line ~3151): `SELECT role FROM watchers`
  - `_join_group_safe` (line ~3220): `SELECT role FROM watchers`
  - `_join_group_safe` (line ~3227): `SELECT joiner_enabled FROM watchers`
- Added 5 new Supabase helper methods to DatabaseManager:
  - `_supabase_get_watcher(phone)` — fetch single watcher with all fields (role, joiner_enabled, stats)
  - `_supabase_update_watcher(phone, **fields)` — update fields via PATCH
  - `_supabase_count_watchers()` — count active watchers via REST count=exact
  - `_supabase_ensure_schema()` — migration check for role/joiner_enabled columns
  - `_sqlite_list_tables()` — list all SQLite tables (proves watchers is absent)
- Fixed all 7 SQLite watchers references to use Supabase instead.
- Added `/verify` command — prints full E2E report: Supabase count, started count, each account phone+role, SQLite table list, E2E PASS/FAIL verdict.
- Added `/sqlite_check` command — lists all SQLite tables and marks whether `watchers` exists.
- Updated `/help` to document new commands.
- Enhanced pipeline logging with 6 clear `[PIPELINE-N]` stage markers:
  - PIPELINE-1: Event Handler received message
  - PIPELINE-2: Link enqueued
  - PIPELINE-3: Scheduler picked link from queue
  - PIPELINE-4: AI verification (APPROVED/REJECTED)
  - PIPELINE-5: Published to channel
  - PIPELINE-6: Safety Guard + Joiner attempt
- Added Migration call on startup: `await db._supabase_ensure_schema()` logs schema status and SQL instructions if columns missing.
- Enhanced Startup Verification with detailed logging: Supabase count (2 methods), monitors/joiners split, full account list, SQLite table list, FATAL exit if `watchers` table found in SQLite.
- Created E2E proof guide (`e2e_test/E2E_PROOF_GUIDE.md`) with 7 steps + 17-item checklist.
- Created Supabase migration SQL (`e2e_test/migration_supabase.sql`).
- Created code verification script (`e2e_test/verify_code.py`) with 12 automated checks.
- Ran verification script: **12/12 checks PASSED**.

Stage Summary:
- `bot.py` (3967 lines) now has ZERO SQLite `watchers` table SQL references.
- Supabase is the verified sole source of truth for all account operations.
- `/verify` and `/sqlite_check` commands produce real logs proving the architecture.
- 6-stage `[PIPELINE-N]` logging enables tracing a link from message receipt to joiner attempt.
- Startup Verification logs the exact account count + phone + role + SQLite table list on every boot.
- FATAL exit (sys.exit(1)) if: Supabase unavailable, 0 accounts, or `watchers` table found in SQLite.
- Migration runs on every startup and warns with exact SQL if columns missing.
- Code is ready for deploy + live E2E test on Render.
- Files produced:
  - `/home/z/my-project/download/bot.py` (updated, 3967 lines)
  - `/home/z/my-project/download/e2e_test/E2E_PROOF_GUIDE.md`
  - `/home/z/my-project/download/e2e_test/migration_supabase.sql`
  - `/home/z/my-project/download/e2e_test/verify_code.py`

---
Task ID: AI-DASHBOARD-FINAL
Agent: main (Super Z)
Task: تحديث لوحة التحكم لعرض حقول AI الجديدة من Supabase + إعادة تفعيل AI في المجدول عبر مفتاح بيئي + إضافة أمر /ai_mode للتحكم.

Work Log:
- فحص `bot.py` (6217 سطر) و `page.tsx` (872 سطر بعد التحديث).
- وجد أن `/api/links` كان يجلب كل الأعمدة افتراضياً لكن بدون دعم فلترة AI — تم تحديثه.
- وجد أن `/api/stats` لم يكن يرجع أي إحصائيات AI (whatsapp_links=0, telegram_links=0 ثابتة).
- وجد أن المجدول `_joiner_worker` كان يتخطى AI دائماً (hardcoded batch mode) بدون إمكانية التبديل.
- تحديث `api_links_handler` في bot.py:
  - استخدام `select=` صريح لضمان إرجاع ai_approved, ai_description, ai_country, ai_is_ad.
  - دعم فلترة عبر query params: `?ai_approved=true`, `?ai_approved=false`, `?ai_is_ad=true`, `?link_type=whatsapp`.
  - إضافة logging للأخطاء.
- تحديث `api_stats_handler` في bot.py:
  - إضافة جلب إحصائيات AI من Supabase عبر `Prefer: count=exact` headers.
  - 4 عدّادات جديدة: ai_approved, ai_rejected, ai_ads, ai_pending.
  - إضافة `ai_batch_mode` (boolean) لعرض حالة وضع المعالجة المجمّعة.
  - إصلاح عداد whatsapp_links و telegram_links (كانت hardcoded=0).
- تحديث المجدول `_joiner_worker` في bot.py (PIPELINE-4):
  - إضافة `AI_BATCH_MODE` env var (افتراضي=true).
  - لو `AI_BATCH_MODE=false` AND `ai_analyzer.enabled` → ينفّذ AI فحص كامل.
  - يستخرج: should_save, description, country, is_advertisement.
  - لو AI رفض الرابط (should_save=false) → يحوّل الحالة BANNED ويتجاوزه بدون نشر.
  - تمرير بيانات AI إلى `insert_request` ليتم حفظها في Supabase.
  - لو `AI_BATCH_MODE=true` (افتراضي) → يتخطى AI تماماً (السلوك السابق).
- إضافة أمر `/ai_mode` جديد في bot.py:
  - `/ai_mode` → عرض الحالة الحالية + عدد مفاتيح AI.
  - `/ai_mode on` → تفعيل AI (تعطيل batch mode).
  - `/ai_mode off` → تعطيل AI (تفعيل batch mode — الافتراضي).
  - مُسجّل في `admin_commands` list ليُعالج من الرسائل الخاصة أيضاً.
  - مُوثّق في رسالة /help.
- تحديث `page.tsx` (الواجهة الأمامية):
  - إضافة حقول AI إلى `LinkItem` interface: ai_approved, ai_description, ai_country, ai_is_ad.
  - إضافة `AIStats` interface وحقنه داخل `Stats`.
  - تحديث `fetchStats` لقراءة `ai_stats` من `/api/stats`.
  - إضافة state جديد: `aiFilterLinks` (قائمة AI محمّلة عند الطلب) و `aiFilterLoading`.
  - إضافة `fetchAiLinks(mode)` لتحميل روابط AI مُصفّاة من `/api/links?ai_approved=true` إلخ.
  - إضافة `useEffect` لتشغيل fetchAiLinks عند التبديل لعرض AI + reset عند المغادرة.
  - تحديث `LinkCard` لعرض:
    * شارة "✅ AI موافق" (أخضر) لو ai_approved=true.
    * شارة "❌ AI مرفوض" (أحمر) لو ai_approved=false.
    * شارة "⏳ لم يُفحص" (رمادي) لو ai_approved=null.
    * شارة "⚠️ إعلان" (كهرماني) لو ai_is_ad=true.
    * صندوق وصف AI (أخضر) لو ai_description موجودة.
    * أولوية لـ ai_country على detectCountry المحلي.
  - إضافة "AI Stats Card" في الصفحة الرئيسية:
    * 4 أزرار قابلة للضغط: موافق عليه / مرفوض / إعلان / لم يُفحص.
    * badge يعرض حالة Batch Mode أو AI Active.
    * رسالة توعية تشرح كيفية إعادة تفعيل AI عبر `/ai_mode on`.
  - إضافة 3 views جديدة: 'ai_approved', 'ai_rejected', 'ai_ads' مع عناوين عربية مناسبة.
  - تحديث loading state ليتعامل مع aiFilterLoading.
- التحقق من صحة syntax: `python -c "import ast; ast.parse(open('bot.py').read())"` → OK.
- نسخ bot.py المحدّث إلى `/home/z/my-project/download/bot.py`.

Stage Summary:
- `/api/links` الآن يرجع كل حقول AI + يدعم الفلترة عبر query params.
- `/api/stats` الآن يرجع إحصائيات AI كاملة (ai_approved/rejected/ads/pending + ai_batch_mode).
- المجدول يحترم `AI_BATCH_MODE` env var — يمكن تفعيل/تعطيل AI بدون إعادة نشر الكود.
- أمر `/ai_mode` يسمح بالتبديل الديناميكي من تيليجرام بدون لمس Render.
- الواجهة الأمامية تعرض شارات AI + صندوق وصف + لوحة إحصائيات AI تفاعلية + 3 views للفلترة.
- الملفات المُنتجة:
  - `/home/z/my-project/bot.py` (6217 سطر)
  - `/home/z/my-project/download/bot.py` (نسخة mirror)
  - `/home/z/my-project/download/frontend/src/app/page.tsx` (872 سطر)
- الخطوات التالية للمستخدم:
  1. اضغط (commit) + ارفع (push) التغييرات إلى GitHub.
  2. Render سيعيد النشر تلقائياً.
  3. Vercel: ارفع (push) مجلد frontend أو أعد النشر.
  4. للتفعيل: أرسل `/ai_mode on` للبوت @Azzamntheer2026_bot.
  5. ستظهر شارات AI في لوحة التحكم خلال 30 ثانية (interval التحديث).

---
Task ID: STRENGTHEN-115911d
Agent: main (Super Z)
Task: العودة للنسخة 557aea1 (AI integration) + تقوية الكود بأربع تحسينات رئيسية.

Work Log:
- تم `git reset --hard 557aea1` للرجوع للنسخة الكاملة.
- إضافة `_detect_service_role_key()` في DatabaseManager:
  - يفك ترميز JWT payload (base64url)
  - يبحث عن `"role":"service_role"` لتأكيد المفتاح الصحيح
  - يسجل خطأ واضح لو anon key مكتشف
- إضافة endpoint جديد `/api/deploy_check`:
  - يفحص 10 متغيرات بيئية (masked للأمان)
  - يختبر Supabase live (200/401/404)
  - يعرض key_type (service_role vs anon)
  - يفحص SQLite tables (يكشف watchers table violation)
  - يعرض Telegram bot + user_clients + monitors/joiners count
  - يعرض queue size + total links + AI status
  - يرجع issues list + verdict (HEALTHY/ISSUES_FOUND)
- تقوية Frontend:
  - `fetchWithRetry()`: 3 محاولات + 10s timeout + 1.5s backoff
  - `DeployCheckBanner`: مكون يعرض تقرير النشر في أعلى الـ dashboard
    * Supabase status + key type
    * Telegram bot status
    * Monitors/Joiners count
    * AI mode + providers
    * Issues list بالعربي
  - `fetchCountryStats`: يحسب من allLinks محلياً (ما يحتاج Supabase مباشر)
  - إزالة كل استدعاءات Supabase المباشرة من الـ frontend
- إضافة `render.yaml` (Render Blueprint):
  - worker service (python runtime)
  - buildCommand: pip install -r requirements.txt
  - startCommand: python bot.py
  - healthCheckPath: /ready
  - PYTHON_VERSION: 3.11.9
  - كل env vars موثقة مع sync:false
- تم رفع التحديث لـ GitHub: `115911d` (force push).

Stage Summary:
- الكود الآن على `115911d` = نسخة 557aea1 + 4 تحسينات قوية.
- الفحص الذكي للمفتاح يكشف anon vs service_role تلقائياً.
- endpoint /api/deploy_check يعطي صورة كاملة للنشر.
- الـ dashboard صار resilient (retry + timeout + fallback).
- render.yaml يسمح بنشر موحد بضغطة زر.
- الملفات المُنتجة:
  - `/home/z/my-project/bot.py` (6413 سطر)
  - `/home/z/my-project/download/bot.py` (نسخة mirror)
  - `/home/z/my-project/download/frontend/src/app/page.tsx` (985 سطر)
  - `/home/z/my-project/render.yaml` (49 سطر)

---
Task ID: SECURITY-GULF-FILTER-13e5e9c
Agent: main (Super Z)
Task: إضافة فلتر صارم قبل الانضمام — يرفض مجموعات بيتكوين والجامعات العراقية وغير الخليجية.

Work Log:
- المشكلة: البوت الفدائي كان ينضم لمجموعات استثمار بيتكوين وجامعات عراقية.
- السبب الجذري: EducationalFilter كان يُطبَّق فقط في /cleanup_links، مو في الـ scheduler قبل الانضمام. AI كان في batch mode (متخطّى). ما كان في أي فلتر قبل استدعاء Join API.
- إضافة HARD_BLACKLIST (100+ كلمة):
  * كريبتو/بيتكوين/استثمار (bitcoin, btc, crypto, forex, airdrop, binance...)
  * مقامرة (casino, betting, lottery...)
  * جامعات عراقية (بغداد, البصرة, كربلاء, نجف, كوفة, المصلا...)
  * دول غير خليجية (مصر, الأردن, سوريا, لبنان, السودان, اليمن, المغرب, الجزائر, تونس, ليبيا, فلسطين)
  * محتوى للكبار، متابعات، متاجر
- إضافة GULF_WHITELIST (50+ كلمة):
  * جامعات سعودية (KSU, KAU, KFU, KFUPM, PNU, IAU, UQU, PSU...)
  * كويت (KU, AUM, AUK, GUST)
  * قطر (QU, HBKU, Carnegie, Georgetown)
  * بحرين (UOB, Ahlia, AMA)
  * إمارات (UAEU, Khalifa, Zayed, AUS, NYUAD)
- إضافة method should_join(text, username, link):
  1. رفض لو HARD_BLACKLIST تطابق
  2. قبول لو GULF_WHITELIST تطابق
  3. قبول لو is_educational يوافق
  4. رفض احتياطي (ما ينضم لمجهول)
- تطبيق في scheduler قبل "اختر حساب فدائي":
  * يستخرج username من t.me/username
  * ينفّذ should_join()
  * رفض → BANNED + skip + continue
  * قبول → يكمل PIPELINE-6
- إضافة /leave_bad_groups command:
  * /leave_bad_groups → معاينة (dry run)
  * /leave_bad_groups confirm → مغادرة فعلية
  * يفحص كل dialogs للحساب الفدائي
  * يغادر السيئة بفاصل 2 ثانية
- إصلاح syntax error: 'ha'il' → 'hail'
- رفع لـ GitHub: 13e5e9c

Stage Summary:
- البوت الحين يرفض تلقائياً أي رابط فيه:
  * bitcoin/btc/crypto/forex/binance/airdrop...
  * بغداد/البصرة/كربلاء/نجف/كوفة...
  * مصر/الأردن/سوريا/لبنان...
- يقبل فقط لو فيه إشارة خليجية واضحة (جامعة سعودية/كويتية/قطرية/بحرينية/إماراتية).
- /leave_bad_groups يمسح المجموعات السيئة اللي انضم لها سابقاً.
- الملفات: bot.py (6633 سطر), download/bot.py (mirror)

---
Task ID: SMART-FILTER-844ecbf
Agent: main (Super Z)
Task: تخفيف الفلتر ليقبل المجموعات الخليجية بدون ذكر اسم الجامعة صراحة.

Work Log:
- المشكلة: الفلتر السابق كان صارم جداً — يرفض أي مجموعة ما تذكر جامعة خليجية بالاسم.
  كثير من مجموعات الـ WhatsApp/Telegram الخليجية ما تذكر اسم الجامعة:
  "طلاب المستوى الأول"، "دفعة 1446"، "تجمع قسم كذا"
- إضافة ACADEMIC_CONTEXT (50+ كلمة):
  * مستويات: مستوى/level 1-8
  * ترامس: ترم/فصل دراسي/semester/fall/spring
  * دفعات: دفعة/1444-1448/cohort/batch
  * أنشطة: محاضرة/سكشن/واجب/اختبار/كويز/midterm/final
  * مواد: مادة/مواد/منهج/كتاب/ملخص/شرائح
  * أنظمة: تسجيل/add drop/معدل/gpa/credit/blackboard/moodle/tudris
  * أقسام: تخصص/قسم/شعبة/فرقة/major
  * طلاب: طلاب/طالبات/تجمع/طلابي
  * مستويات جامعية: بكالوريوس/ماجستير/دكتوراه
  * سعودي خاص: سنة تحضيرية/انتساب/تعليم عن بعد
- إضافة 3 طبقات قبول جديدة في should_join():
  1. مصدر الرسالة خليجي (gulf_source_group) — لو group_name المصدر فيه إشارة خليجية
  2. سياق أكاديمي (academic_context) — لو text/username فيه كلمات أكاديمية
  3. مصدر فيه سياق أكاديمي (academic_source_group)
- ترتيب الفحص الجديد:
  1. HARD_BLACKLIST → رفض (حتى لو المصدر خليجي — بيتكوين دائماً مرفوض)
  2. GULF_WHITELIST → قبول
  3. مصدر خليجي → قبول
  4. سياق أكاديمي → قبول
  5. مصدر أكاديمي → قبول
  6. is_educational عام → قبول
  7. احتياطي → رفض
- تمرير source_group_name و source_phone من scheduler للفلتر
- كتابة scripts/test_filter.py (13 اختبار):
  - 13/13 نجح ✅
  - حالة مهمة: "بيتكوين داخل مصدر خليجي" → رفض (blacklist يفوز دائماً)
- رفع لـ GitHub: 844ecbf

Stage Summary:
- الفلتر الحين ذكي: يقبل المجموعات الخليجية بدون ذكر اسم الجامعة.
- يرفض بيتكوين/عراقي/مصري حتى لو جاءت من مصدر خليجي.
- يقبل "طلاب المستوى الأول" و "دفعة 1446" لأنها سياق أكاديمي.
- الملفات: bot.py, download/bot.py, scripts/test_filter.py

---
Task ID: FILTER-V2-MERGE-92cd709
Agent: main (Super Z)
Task: دمج GulfFilter (DeepSeek) + EducationalFilter (الحالي) في كلاس واحد محسّن.

Work Log:
- مقارنة الكودين:
  * DeepSeek GulfFilter: تنظيم أفضل، regex أقوى، _find_* methods نظيفة
  * EducationalFilter الحالي: قوائم شاملة (100+ كلمة)، is_educational، is_likely_channel
- إنشاء scripts/gulf_filter_v2.py بالكلاس المدمج:
  * تبني هيكل DeepSeek (BLACKLIST_CRYPTO_INVEST, BLACKLIST_GAMBLING, ...)
  * الحفاظ على القوائم الشاملة من EducationalFilter
  * إضافة دعم WhatsApp links في _extract_username
  * كل methods ترجع Tuple[bool, str] بشكل متسق
- اختبارات: 24/24 نجح (مقارنة بـ 13 سابقاً):
  * بيتكوين بالعربي والإنجليزي → رفض
  * جامعات عراقية/مصرية/أردنية/لبنانية → رفض
  * WhatsApp links مع اسم خليجي → قبول
  * telegram.me/ (مش بس t.me/) → قبول
  * @username مباشر → قبول
  * blackboard، تحضيري، تجمع طلاب → قبول
- استبدال EducationalFilter في bot.py (17076 chars → 16391 chars):
  * EducationalFilter = GulfFilter (alias) — لا تغيير في الاستدعاءات
  * إصلاح 1 call site: is_gulf_target صارت ترجع tuple
- رفع لـ GitHub: 92cd709

Stage Summary:
- الكلاس المدمج يجمع أفضل ما في الكودين:
  * هيكل DeepSeek النظيف + قوائم EducationalFilter الشاملة
  * دعم WhatsApp + Telegram + @username
  * كل methods متسقة (Tuple[bool, str])
  * 24 اختبار ناجح
- الملفات: bot.py, download/bot.py, scripts/gulf_filter_v2.py, scripts/replace_filter.py

---
Task ID: PRIORITY-SCORER-6fd1acc
Agent: main (Super Z)
Task: إضافة نظام أولوية — البوت يعطي أولوية الانضمام للمجموعات الكبيرة (10K+ عضو).

Work Log:
- الطلب: البوت يركز على المجموعات ذات التجمع العالي (10,000+ عضو).
- إضافة أعمدة جديدة لـ link_queue في link_system.py:
  * member_count INTEGER — يخزن عدد الأعضاء
  * priority INTEGER DEFAULT 3 — 1=HIGH, 2=MEDIUM, 3=LOW
  * migration: ALTER TABLE ADD COLUMN للجداول القديمة
  * index على (priority, status) للفرز السريع
- تعديل get_queued_links():
  * ORDER BY priority ASC, member_count DESC NULLS LAST, enqueued_at ASC
  * يرجع member_count و priority في النتائج
- إضافة method جديدة update_link_priority():
  * >= 10,000 عضو → priority 1 (HIGH)
  * >= 1,000 عضو → priority 2 (MEDIUM)
  * < 1,000 أو غير معروف → priority 3 (LOW)
- إضافة method get_unscored_links() — للروابط بدون member_count
- إضافة مهمة _priority_scorer() الخلفية في bot.py:
  * تعمل كل 30 ثانية
  * تستخدم حساب المراقب (مو الفدائي)
  * تجلب member_count عبر get_entity + GetFullChannelRequest
  * تتعامل مع المجموعات الخاصة/المحذوفة
  * تسجّل: [SCORER] id @username: 12,345 members → priority=HIGH
- بدء المهمة في start() + إلغاؤها في stop()
- تحسين /queue command:
  * عرض توزيع الأولوية (HIGH/MEDIUM/LOW)
  * عرض member_count لكل رابط
  * رموز تعبيرية: 🔴 HIGH, 🟡 MEDIUM, ⚪ LOW
- رفع لـ GitHub: 6fd1acc

Stage Summary:
- الروابط الجديدة تدخل القائمة priority=3 (غير معروف)
- خلال 30 ثانية، الـ scorer يجلب member_count ويحدّث priority
- المجدول يختار الروابط بهذا الترتيب:
  1. HIGH (10K+ عضو) أولاً
  2. MEDIUM (1K+ عضو) ثانياً
  3. LOW (أقل من 1K أو غير معروف) أخيراً
- ضمن نفس الأولوية، الأكثر أعضاءً يُختار أولاً
- الملفات: bot.py, download/bot.py, link_system.py, download/link_system.py

---
Task ID: ANTI-DELETE-CACHE-f1a3e9c
Agent: main (Super Z)
Task: حل سحب الرابط قبل ما بوتات الحماية تحذفه + تأكيد هل مجموعة الرابط مراقبة.

Work Log:
- تحليل المشكلة:
  * المستخدم لاحظ أن بوتات حماية (مثل جبل/صقير) تحذف الرسائل قبل ما البوت يلحق يعالجها
  * الحل السابق (08cd8c1) كان يعيد ترتيب _on_user_message لكنه ما يقدر يلتقط الرسائل المحذوفة قبل المعالجة
  * سؤال ثاني: هل المجموعة المصدر للرابط من ضمن المجموعات المراقبة؟

- التحقق من البيانات الحالية على Render:
  * stats: 26,828 رابط في queue، 839 مجموعة مراقبة، 4 حسابات متصلة، 2 فدائي متصل
  * آخر 20 رابط: كل المصادر من ضمن monitored_chats ✅
  * مصادر شائعة: S_boot (8x), Bot (2x), طلبة جامعة زايد (2x), جامعة الملك خالد، CCIS/IMAMU
  * المشكلة: 3 banned (فضفضة، استشارات زوجية، low_member_count_258)

- الحل المُطبّق (3 طبقات حماية):
  1. PRE-CACHE: عند وصول أي رسالة (NewMessage event)، نخزنها كاملة في ذاكرة قبل أي معالجة
     - Key: (chat_id, msg_id) → {raw_text, source_phone, chat_title, chat_username, ...}
     - TTL: 120 ثانية
     - استخدام asyncio.Lock للحماية
  2. PROCESS: نستخرج الروابط ونضعها في queue فوراً (مثل السابق)
     - نعلّم الرسالة كـ processed=True بعد الإكمال
  3. RESCUE: عند حدث MessageDeleted:
     - نسحب الرسالة من cache (لو ما عُولجت بعد)
     - نعالجها كاملة (extract links + blacklist + enqueue)
     - نلوّج: [DELETE-HANDLER] 🚨⏰ RESCUED deleted msg_id=X

- التنفيذ:
  * إضافة self._msg_cache, self._msg_cache_lock, self._msg_cache_ttl في __init__
  * تعديل _register_user_handlers: إضافة MessageDeleted handler لكل user_client
  * إعادة كتابة _on_user_message:
    - PRE-CACHE أولاً (قبل extract_links)
    - استخدام event.chat بدون API calls
    - استخراج sender_name بدون API
    - تسجيل monitored_chat بدون API
  * إضافة _on_message_deleted: يلتقط الرسائل المحذوفة، يعالجها من cache
  * إضافة _msg_cache_cleanup: مهمة خلفية تنظف الرسائل القديمة كل 30 ثانية
  * بدء المهمة في start() + إلغاؤها في stop()

- API endpoint جديد /api/link_source_check:
  * params: ?message_link=, ?chat_id=, ?group_name=
  * returns: {is_monitored: bool, chat: {...}, total_monitored: N}
  * يساعد على فحص أي رابط ومن يعرف هل مصدره مراقَب

- تحديث Frontend (download/frontend/src/app/page.tsx):
  * إضافة isMonitored prop لـ LinkCard
  * إظهار Badge "👁️ مصدر مراقَب" (cyan) للروابط اللي مصدرها مجموعة مراقبة
  * إظهار Badge "⚪ مصدر غير مراقَب" (slate) للروابط اللي مصدرها غير مراقب
  * الفحص يحصل بـ: chat_title === group_name OR message_link.includes(/c/{chat_id_without_prefix}/)
  * في compact mode: نقطة خضراء ● بجانب اسم المجموعة (لو مراقَب)

- ملفات تم تعديلها:
  * bot.py (الحجم: 8109 سطر)
  * download/bot.py (نسخة متطابقة)
  * download/frontend/src/app/page.tsx

Stage Summary:
- 3 طبقات حماية ضد الحذف: PRE-CACHE + PROCESS + RESCUE
- حتى لو بوت حماية حذف الرسالة بسرعة، نقدر نسحبها من cache
- API جديد للتحقق من مصدر أي رابط
- Dashboard يُظهر حالة المراقبة بوضوح على كل رابط
- كل المصادر الحالية فعلاً في monitored_chats (تأكيد من البيانات الحالية)

---
Task ID: DURABILITY-MESSAGE-JOURNAL
Agent: main (Super Z)
Task: حل جذري لمشكلة فقدان الروابط بسبب الحذف السريع من بوتات الحماية — Message Journal durable + rescue ثلاثي المصادر + delete-miss forensics + تحسين تغطية polling.

Work Log:
- المشكلة (من التحقيق الجنائي لاختبار UQU_Medicine1 2026-08-25): الرسائل المحذوفة خلال ثوانٍ كانت تُفقد لأن:
  1) _msg_cache في الذاكرة فقط (TTL 120s) — يضيع عند إعادة التشغيل أو انتهاء المدة.
  2) Delete Handler يعتمد كليًا على الـ cache — لو NewMessage لم يصل أو انتهى TTL لا إنقاذ.
  3) لا يوجد أي دليل جنائي على وصول الأحداث (silent returns بلا logs).
  4) Polling لا يمكنه استرجاع رسائل حذفها Telegram نهائيًا (get_messages لا يعيد المحذوف).
- الحل المُنفّذ (عام لكل المجموعات):
  * message_journal (SQLite WAL): جدول جديد يُكتب فور وصول أي رسالة (raw_text كامل + metadata) قبل أي معالجة — INSERT OR IGNORE (chat_id, msg_id). يصمد بعد إعادة التشغيل وبعد انتهاء TTL الذاكرة.
  * _on_user_message: journal write-ahead لكل رسالة (pending/no_text/no_links/dup_claim/processed) — يوفر دليلًا جنائيًا دائمًا على وصول كل حدث.
  * _on_message_deleted (rewrite): إنقاذ ثلاثي المصادر — _msg_cache ← message_journal ← DELETE-MISS (تحذير WARNING مقيّد + صف delete_miss في journal + reconcile). mass-delete cap 50.
  * _reconcile_chat_after_delete_miss: بعد DELETE-MISS يسحب آخر 15 رسالة من الشات (عبر الحساب الذي رأى الحذف أو registry reader) لالتقاط أي رسائل أخوات فاتتها فجوة الأحداث.
  * _journal_recovery: مهمة إقلاع تعيد معالجة صفوف pending الأقدم من 120 ثانية (رسائل انهار النظام قبل اكتمال معالجتها) — MessageClaim يمنع التكرار.
  * PollingScheduler: BATCH_SIZE 10←25 + تزامن محدود (Semaphore 4) — تغطية 800+ مصدر أسرع ~4x مع بقاء RateLimiter (قفل لكل هاتف + min_delay) يسلسل عمليات نفس الحساب.
  * _poll_one_chat: إزالة reader فقد وصوله (channel private/forbidden/banned) من reader_phones للشات فقط + تأجيل الشات الميت ساعتين بعد 5 إخفاقات متتالية.
  * journal_cleanup كل ساعة (24h عام / 6h للصفوف الخفيفة no_text/delete_miss).
  * Config knobs (كلها default-on): MESSAGE_JOURNAL, JOURNAL_RETENTION_S, JOURNAL_NO_TEXT_RETENTION_S, DELETE_MISS_RECONCILE, JOURNAL_RECOVERY.
- الاختبارات: tests/test_message_journal.py جديد — 18 سيناريو / 66 assertion (إنقاذ بعد إعادة تشغيل محاكاة، delete-miss، استرجاع الانهيار، dedup متعدد الحسابات، chat_id=None، mass-delete cap، remove_reader...). كل الاختبارات القديمة تمر بلا تعديل: 103+96+35+3.
- ملاحظة مكتشفة أثناء الاختبار (pre-existing): GulfFilter.is_blacklisted يستخدم substring matching — أسماء حساسة تحتوي كلمات قصيرة في blacklist (مثل "bet" داخل "beta") تُرفض خطأً. لم تُغيَّر في هذا الـcommit (خارج النطاق).

Stage Summary:
- أي رابط يصل عبر NewMessage لأي حساب مراقب يُكتب الآن في journal فورًا — لا يمكن فقدانه بالحذف السريع حتى لو انتهى TTL الذاكرة أو أعيد تشغيل النظام.
- الحالة الوحيدة المتبقية غير القابلة للاسترجاع: رسالة حُذفت قبل أن يستلمها أي من حساباتنا على الإطلاق (فجوة تسليم كاملة) — أصبحت الآن مرئية (DELETE-MISS warning + صف journal) بدل أن تختفي بصمت، و reconcile يلتقط الرسائل الأخوات.
- الملفات: bot.py, link_system.py, source_registry.py, tests/test_message_journal.py, download/{bot,link_system,source_registry}.py

---
Task ID: 4a
Agent: ai-drainer-auditor
Task: Deep audit + harden `_ai_drainer_worker` in bot.py — bounded concurrency, rate-limited, lease-protected, idempotent, observable, restart-safe, graceful-shutdown-capable. Must NOT launch 26,475 jobs at once.

Work Log:
- Read worklog.md, AUDIT_FINDINGS.md (Task 8b N07 section), FINAL_REPORT.md.
- Located `_ai_drainer_worker` at bot.py:3825-3904 (pre-audit). Grep-verified all 7 markers: `_ai_drainer_worker`, `AI_DRAIN_ENABLED`, `AI_BATCH_MODE`, `[AI-DRAIN]`, `ai_pending`, `analyze_message`, `ai_analyzer`.
- Audited against the 17-point checklist. Found gaps in: concurrency bound (was sequential=bounded=1, but no semaphore for future-proofing), batch size (hardcoded 10), timeout (none — provider hang = forever-block), retry/backoff (no poison-row cap), lease protection (SELECT-then-PATCH race), stuck-job rotation (ORDER BY id ASC = poison row at head forever), observability (per-row only, no batch summary).
- Rewrote `_ai_drainer_worker` (bot.py:3817-4079) with the full guarantee matrix:
  * Bounded concurrency: `asyncio.Semaphore(AI_DRAIN_CONCURRENCY)` (default 3, env-configurable, set to 1 for sequential). Rows processed via `asyncio.gather(return_exceptions=True)` capped by the semaphore.
  * Batch size: `AI_DRAIN_BATCH_SIZE` env (default 10) — used in GET `&limit=N`.
  * Timeout: `asyncio.wait_for(analyze_message(...), timeout=AI_DRAIN_TIMEOUT_S)` (default 60s). On TimeoutError → WARNING + row stays ai_pending + fail_count incremented.
  * Retry cap: in-memory `self._ai_drainer_fail_count` dict — a row that fails (timeout/exception/None) 3× in this worker lifetime is skipped on subsequent cycles. Lost on restart (idempotent — row retried by next worker).
  * Lease protection: PATCH URL carries `&ai_approved=is.null` filter (no Supabase migration needed — the existing `ai_approved` column IS the lease flag). PATCH uses `Prefer: return=representation` header; empty body `[]` = 0 rows updated = race-lost (logged DEBUG + skipped, NOT counted as failure).
  * Stuck-job rotation: SELECT uses `&order=id.desc` (newest-first) so the head rotates as new rows arrive; combined with the retry cap, a chronically-failing poison row is skipped after 3 retries.
  * Observability: per-batch summary `[AI-DRAIN] batch=N processed=M failed=K skipped=L elapsed=Xs` at INFO.
  * Backlog starvation comment: documented that with AI_BATCH_MODE=true (default), the drainer is the SOLE AI consumer (no competition with the live pipeline); if AI_BATCH_MODE=false, set AI_DRAIN_CONCURRENCY=1.
- Verified (no change needed): worker death (#9 — supervisor already relaunches, bot.py:3774-3780), restart safety (#10 — in-memory counters lost, idempotent), graceful shutdown (#12 — stop() cancels _ai_drainer_task, CancelledError caught and breaks), provider failure (#13 — try/except wraps each row), 429 rate limit (#14 — per-cycle 60s backoff, not per-row), empty queue (#15 — 60s sleep), AI_DRAIN_ENABLED default false (#17 — opt-in confirmed).
- Updated render.yaml: added `AI_DRAIN_BATCH_SIZE` (value "10"), `AI_DRAIN_CONCURRENCY` (value "3"), `AI_DRAIN_TIMEOUT_S` (value "60"). Kept `AI_DRAIN_ENABLED` value "false" (NOT enabled in production — operator must opt-in after audit).
- Added 17 regression tests to tests/test_audit_regressions.py (Task 4a section, lines 1215-1827):
  1. `test_4a_ai_drainer_lease_filter_on_patch` — PATCH URL has `ai_approved=is.null` + `Prefer: return=representation` header.
  2. `test_4a_ai_drainer_batch_size_env` — `AI_DRAIN_BATCH_SIZE=25` → GET URL `limit=25`.
  3. `test_4a_ai_drainer_order_rotates_head` — GET URL has `order=id.desc`.
  4. `test_4a_ai_drainer_timeout_skips_row` — analyze blocks → wait_for times out → no PATCH, fail_count=1.
  5. `test_4a_ai_drainer_provider_failure_skips_row` — analyze raises → no PATCH, fail_count=1 (no infinite-loop).
  6. `test_4a_ai_drainer_none_result_skips_patch` — analyze returns None → no PATCH, fail_count=1.
  7. `test_4a_ai_drainer_429_cycle_backoff` — 429 on GET → no analyze, no PATCH (cycle-level backoff).
  8. `test_4a_ai_drainer_empty_queue_60s_sleep` — GET returns [] → no analyze, no PATCH.
  9. `test_4a_ai_drainer_poison_row_skipped_after_3_fails` — pre-seeded fail_count=3 → analyze NOT called (skipped).
  10. `test_4a_ai_drainer_race_loss_detected` — PATCH returns 200 with `[]` → skipped (not failed), no WARNING.
  11. `test_4a_ai_drainer_concurrency_semaphore_bounded` — concurrency=1 → max-in-flight=1 (serialized), all 5 PATCHes made.
  12. `test_4a_ai_drainer_concurrency_3_allows_parallel` — concurrency=3 → max-in-flight >1 and ≤3, all 6 PATCHes made.
  13. `test_4a_ai_drainer_batch_summary_log` — `[AI-DRAIN] batch=1 processed=1 ...` emitted at INFO.
  14. `test_4a_ai_drainer_fail_count_resets_on_success` — pre-seeded fail_count=2 → cleared on successful PATCH.
  15. `test_4a_ai_drainer_graceful_shutdown` — task.cancel() mid-cycle → CancelledError handled cleanly (no unhandled exception).
  16. `test_4a_ai_drainer_disabled_by_default` — AI_DRAIN_ENABLED unset → worker returns immediately (no GET/PATCH/analyze).
  17. `test_4a_ai_drainer_no_ai_configured_60s_sleep` — ai_analyzer.enabled=False → no GET, 60s sleep.
- Test infrastructure: `_make_drainer_fakes` (async helper building fake monitor + FakeSession), `_run_one_drainer_cycle` (patches asyncio.sleep, flips _running after N calls), `_REAL_SLEEP = asyncio.sleep` captured at module load for tests needing real sleeps.
- Test results:
  - tests/test_audit_regressions.py: 119/119 PASS (was 57 at committed baseline; +62 new = 17 Task 4a + 8 Task 3a snapshot-audit + 37 assertions from pre-existing 3a that now pass with the hardened bot.py).
  - tests/test_message_journal.py: 66/66 PASS (no regression).
  - tests/test_source_registry.py: 103/103 PASS (no regression).
  - tests/test_phase3_contracts.py: 96/96 PASS (no regression).
  - tests/test_deployment_updated.py: 35/35 PASS (no regression).
  - tests/test_extractor_comparison.py: 3/3 PASS (no regression).
  - TOTAL: 422/422 PASS.
- `git diff --check`: exit 0 (no whitespace errors).
- Secret scan on diff: no matches (ghp_/gho_/ghs_/github_pat_/bot_token/supabase_key/session_string patterns all clean).
- `git diff --stat`: bot.py +380/-114, render.yaml +24/0, tests/test_audit_regressions.py +1152/-4 (3 files, +1556/-118 total).

Stage Summary:
- `_ai_drainer_worker` is now bounded-concurrency (Semaphore 3), rate-limited (per-cycle 429 backoff), lease-protected (PATCH `ai_approved=is.null` filter + `return=representation` race-loss detection), idempotent (WHERE-filter prevents double-write; re-analyzing same text is safe), observable (per-batch summary), restart-safe (in-memory fail_count lost on restart — idempotent), graceful-shutdown-capable (CancelledError caught + breaks).
- No Supabase migration needed — the existing `ai_approved` column IS the lease flag.
- AI_DRAIN_ENABLED stays `false` in render.yaml. Operator can opt-in by setting it to `true` + providing OPENAI_API_KEY. With AI_BATCH_MODE=true (default), the drainer is the SOLE AI consumer (no competition with the live pipeline).
- Recommendation: AI_DRAIN_ENABLED=true is now SAFE to enable in production, CONDITIONAL on: (1) OPENAI_API_KEY set, (2) AI_BATCH_MODE=true (default — drainer is sole AI consumer), (3) operator monitors the first few `[AI-DRAIN] batch=...` logs for the expected ~20 rows/min drain rate. If AI_BATCH_MODE=false (live AI on hot path), set AI_DRAIN_CONCURRENCY=1 to serialize drainer calls behind the live path.

---
Task ID: 3a
Agent: supabase-snapshot-auditor
Task: Deep audit of the Supabase Journal Snapshot feature (added in commit 803822b / Task 8b). Verify 15 failure modes + the snapshot SELECT predicate; fix what's broken in-place; add regression tests.

Work Log:
- Read prior work: worklog.md (DURABILITY + 8a + 8b history), AUDIT_FINDINGS.md
  (8a/8b changelogs), FINAL_REPORT.md (status). Located snapshot code in
  bot.py (`_journal_snapshot_loop` ~line 4120, `_restore_journal_from_supabase`
  ~line 4234) and link_system.py (`journal_message` INSERT OR IGNORE ~line 1464,
  `journal_cleanup` ~line 1561).
- Audited all 15 failure modes + the ALSO section. Findings + fixes:

  SCENARIOS ALREADY CORRECT (no fix needed — documented with code evidence):
  • Point 1 (Atomicity): PostgREST batch POST is a single PostgreSQL
    transaction; one bad row aborts the whole batch. Caller retries same
    rows next cycle (idempotent via PK (chat_id, msg_id) upsert). bot.py
    handles non-200 with WARNING + continues. ✅ correct.
  • Point 4 (Duplicate restore idempotent): `_restore_journal_from_supabase`
    uses `journal_message` which is `INSERT OR IGNORE` (link_system.py:1464).
    Re-restore is a no-op for existing rows. ✅ correct (covered by existing
    test_persist_snapshot_restore_on_startup).
  • Point 5 (Concurrent snapshot + restore at startup): start() calls
    `await self._restore_journal_from_supabase()` (line ~8803) BEFORE
    `asyncio.create_task(self._journal_snapshot_loop())` (line ~8817). The
    snapshot loop also has `await asyncio.sleep(40)` settle delay. Restore
    completes before snapshot starts. A just-restored row re-snapshotted
    next cycle is harmless (PK upsert idempotent). ✅ correct.
  • Point 7 (Partial snapshot retry): No local "snapshotted" marker exists;
    on POST failure (caught by except), rows are NOT marked. Next cycle
    re-selects the same at-risk rows and re-POSTs. Upsert PK makes re-post
    idempotent. ✅ correct.
  • Point 11 (Corrupted snapshot row): `_restore_journal_from_supabase` has
    per-row try/except (bot.py:4295-4298). INSERT OR IGNORE silently skips
    constraint violations (NULL chat_id/msg_id); non-constraint errors
    (disk-full, locked DB) are caught by the per-row except. ✅ correct
    (verified by new test 3a-7).
  • Point 12 (Restart during snapshot): Snapshot only READS local SQLite
    (SELECT) and WRITES to Supabase (POST). No local writes. Kill mid-POST
    leaves local SQLite unaffected. ✅ correct.
  • Point 13 (Restart during restore): Restore uses `journal_message`
    (INSERT OR IGNORE) per row — atomic per row in SQLite. Killed mid-restore
    leaves some rows as pending; `_journal_recovery` picks them up. ✅ correct.
  • Point 14 (Restore BEFORE journal_recovery): start() at line ~8803 calls
    `_restore_journal_from_supabase()` BEFORE `_journal_recovery_task` is
    created at line ~8810. ✅ correct ordering.
  • Point 15 (Table-missing 404 handling): `_journal_snapshot_loop` catches
    404 / "relation does not exist" via rate-limited WARNING (max once/hour
    via `last_warn_ts`). Doesn't crash (caught by except). Doesn't spam
    (rate-limited). ✅ correct.

  ALSO section — 'failed' in snapshot predicate:
  • 'failed' is NEVER a journal state. The journal states set is: pending,
    processed, no_links, no_text, dup_claim, rescued, blacklisted, delete_miss
    (verified via grep of all `_journal_set_state_safe` call sites in bot.py).
    'failed' is only a `processed_messages` / link_queue claim state. The
    `state NOT IN ('pending','failed')` clause in journal_cleanup
    (link_system.py:1573) is purely defensive (harmless). So the snapshot
    predicate `state IN ('pending','no_text','delete_miss')` is CORRECT and
    complete — there is no 'failed' to add. Documented in the new design-notes
    comment block above `_journal_snapshot_loop` (bot.py:4094-4105).

  ISSUES FOUND + FIXED (5 code fixes in bot.py):

  Fix 1 (point 6 — concurrent snapshot guard):
    Issue: `_journal_snapshot_loop` had NO guard against two concurrent
    invocations. The supervisor's `done()` check (bot.py:3868) + create_task
    is a check-then-act race; if start() and _supervisor_loop both pass the
    check, two loops could POST simultaneously (double rate-limit pressure).
    Fix: Added `self._snapshot_running: bool = False` to Monitor.__init__
    (bot.py:2722) and a guard at the top of `_journal_snapshot_loop`
    (bot.py:4127-4131): if `_snapshot_running` is True, log INFO and return.
    Set True on entry, reset to False in a `finally` block (bot.py:4231-4232)
    so it can't get stuck True on exception.

  Fix 2 (point 2 — snapshot SELECT ordering):
    Issue: SELECT had `LIMIT 500` with NO `ORDER BY`. SQLite returns rows in
    unspecified order, so a 500-row LIMIT could repeatedly snapshot the same
    NEW rows while OLD at-risk rows (most likely to be lost on crash) never
    made it into the snapshot.
    Fix: Added `ORDER BY received_at ASC` before `LIMIT 500` (bot.py:4154)
    so the OLDEST at-risk rows are snapshotted first. Comment explains
    rationale (bot.py:4142-4147).

  Fix 3 (point 8 — Supabase POST/GET timeout):
    Issue: The shared `_get_supabase_session` (bot.py:882-890) creates
    `aiohttp.ClientSession(headers={...})` with NO `timeout=` arg. So the
    snapshot POST and restore GET could hang forever if Supabase stalls,
    blocking the snapshot loop / startup indefinitely.
    Fix: Added explicit `snap_timeout = aiohttp.ClientTimeout(total=15)`
    (bot.py:4173) passed to `session.post(..., timeout=snap_timeout)`
    (bot.py:4182). Added `restore_timeout = aiohttp.ClientTimeout(total=15)`
    (bot.py:4267) passed to `session.get(url, timeout=restore_timeout)`
    (bot.py:4268). Other Supabase callers (insert_link, etc.) are out of
    scope of this audit — only snapshot/restore were hardened.

  Fix 4 (point 9 — 429 rate-limit backoff):
    Issue: The snapshot loop had NO explicit 429 handling. A 429 fell into
    the generic non-200 `else` branch (WARNING + 30s sleep). On the free
    tier (0.03 req/s), a 429 storm could cascade.
    Fix: Added explicit `if resp.status == 429:` check (bot.py:4189-4194)
    BEFORE the generic non-200 handler: logs
    `[JOURNAL-SNAPSHOT] 429 rate-limited — backing off 60s` and sleeps 60s
    (mirrors `_ai_drainer_worker` pattern at bot.py:3929-3932).

  Fix 5 (point 10 — timeout/network/5xx isolation):
    Issue: The snapshot loop caught all errors via a single
    `except Exception as e:` (line ~4162). While this prevents a crash, it
    didn't distinguish asyncio.TimeoutError and aiohttp.ClientError for
    targeted logging.
    Fix: Split the except into three (bot.py:4221-4229):
      - `except asyncio.TimeoutError` → WARNING "[JOURNAL-SNAPSHOT] POST timed out (15s)"
      - `except aiohttp.ClientError` → WARNING "[JOURNAL-SNAPSHOT] network error"
      - `except Exception` → WARNING "[JOURNAL-SNAPSHOT] error"
    Same pattern added to `_restore_journal_from_supabase` (bot.py:4301-4309):
    TimeoutError → "restore timed out (15s) — skipping"; ClientError →
    "restore network error"; both return 0 (no partial restore).

  TEST INFRASTRUCTURE FIX (1 fix in tests/test_audit_regressions.py):
    Issue: `LogCapture` class saved the root logger's LEVEL but passed it
    to `logging.disable()` on exit. The root logger's default level is
    WARNING (30), so after the first LogCapture exited, `logging.disable(30)`
    was called — suppressing INFO logs globally. Any test using
    `lc._h.setLevel(logging.INFO)` to capture INFO (e.g. 4a-summary) got
    `summary_msgs=[]` because the root logger (still at WARNING) dropped
    INFO before it reached the handler.
    Fix: Rewrote `LogCapture.__enter__/__exit__` (test_audit_regressions.py:140-162)
    to save BOTH the global disable level (`logging.root.manager.disable`)
    AND the root logger's level. On enter: `logging.disable(NOTSET)` +
    `logging.getLogger().setLevel(INFO)` so INFO reaches the handler. On exit:
    restore both independently. This fixed the pre-existing 4a-summary
    failure and benefits any future test that captures INFO logs.

  REGRESSION TESTS ADDED (8 new test functions, 28 assertions):
    All in tests/test_audit_regressions.py, appended before the Main runner.
    Pattern: RESULTS list + record() + _OPEN_DBS/close_all_test_dbs teardown
    (matches existing file style).
    - test_3a_snapshot_concurrent_guard (4 assertions): _snapshot_running=True
      → 2nd invocation no-ops; clean cycle POSTs once + resets flag.
    - test_3a_snapshot_order_by_received_at (2): 700 pending rows → snapshot
      selects OLDEST 500 (msg_id 1000..1499), not arbitrary 500.
    - test_3a_snapshot_429_backoff (3): 429 → WARNING "429 rate-limited" +
      60s sleep + exactly 1 POST before backoff.
    - test_3a_snapshot_post_timeout_does_not_crash (3): POST raises
      asyncio.TimeoutError → caught + WARNING "timed out" + loop survives +
      _snapshot_running reset to False.
    - test_3a_snapshot_network_error_continues (2): POST raises
      aiohttp.ClientError → caught + WARNING "network error" + loop survives.
    - test_3a_restore_does_not_overwrite_terminal_local_state (4): local row
      state='processed'; snapshot returns same PK with state='pending';
      restore via INSERT OR IGNORE → local row STAYS 'processed' (no
      overwrite, no duplicate).
    - test_3a_restore_per_row_corruption_isolation (7): NULL chat_id row →
      INSERT OR IGNORE silently skips (NOT NULL held); 2 good rows restored.
      Sub-test B: mock journal_message to raise on row 2 → per-row except
      isolates; rows 1 and 3 still restored (restored=2).
    - test_3a_restore_get_timeout_returns_zero (3): GET raises
      asyncio.TimeoutError → returns 0 + WARNING "restore timed out" + no
      rows inserted.

  Also updated 2 existing FakeSession mocks in the PERSIST tests to accept
  `**kwargs` (forward-compatible with the new `timeout=` kwarg passed to
  session.post/session.get) and set `fm._snapshot_running = False` before
  invoking the snapshot loop (so the guard doesn't block the test cycle).

Test results (all 6 test files exit 0):
  - test_audit_regressions.py: 119/119 PASS (was 57/57 at 8b baseline; +62
    from 4a tests now working + 28 from new 3a assertions)
  - test_message_journal.py: 66/66 PASS (no regression)
  - test_source_registry.py: 103/103 PASS
  - test_phase3_contracts.py: 96/96 PASS
  - test_deployment_updated.py: 35/35 PASS
  - test_extractor_comparison.py: 3/3 PASS
  - TOTAL: 422/422 PASS
  - git diff --check: exit 0 (no whitespace errors)
  - secret scan: no new secrets introduced (no ghp_/bot_token/api_hash/
    supabase_key patterns in the diff)

Files changed:
  - bot.py (+380/-114): 5 snapshot/restore fixes + design-notes comments.
    NOTE: the +380/-114 also includes PRE-EXISTING uncommitted 4a AI-drainer
    enhancements (AI_DRAIN_BATCH_SIZE/CONCURRENCY/TIMEOUT_S env, fail_count
    dict, Prefer: return=representation, race-loss detection) from a prior
    session — those were already in the working tree at task start and were
    NOT touched by this audit.
  - tests/test_audit_regressions.py (+1152/-29): 8 new 3a test functions
    (28 assertions) + LogCapture infrastructure fix + 2 FakeSession mock
    updates + 4a test section (pre-existing from prior session, now all
    passing after LogCapture fix + pycache clear).

Scenarios that are external / cannot-be-fixed-in-code:
  1. PostgREST batch atomicity (point 1): the all-or-nothing transaction
     semantics are a property of PostgREST + PostgreSQL, not our code. We
     rely on it correctly (idempotent retry via PK upsert) but can't change
     it. ✅ no action needed.
  2. Stale-snapshot resurrection (point 3): the at-risk-only snapshot
     design means a stale 'pending' row in Supabase can be restored after
     local cleanup wipes the SQLite. This is mitigated by (a) INSERT OR
     IGNORE preserving any surviving local terminal state, and (b) the
     downstream UNIQUE(link) constraint on link_queue making journal_recovery
     re-processing idempotent (duplicate link insert is a no-op). A full
     fix would require UPSERT-ing terminal states to Supabase BEFORE local
     cleanup — that's a bigger design change (snapshot would no longer be
     at-risk-only) and is deliberately NOT made; documented in the
     design-notes comment block (bot.py:4094-4105).
  3. Render ephemeral-disk restart (B01): the snapshot mirror survives
     restart, but a real persistent disk at /data (DATA_DIR=/data) is still
     the recommended durability path. Operator action — not code-blockable.
  4. Supabase free-tier rate limit (0.03 req/s): the 30s cycle + 60s 429
     backoff stays within limits, but a sustained 429 storm would still
     throttle the snapshot. Operator can upgrade Supabase tier — not
     code-blockable.

---
Task ID: PASS-3 (8c)
Agent: main (Super Z) — post-audit deployment hardening
Task: Continue from 8a/8b state: AUDIT → REVIEW FIXES → TEST → SECURITY → GIT SYNC → PUSH → DEPLOY → VERIFY. Deep-audit Supabase snapshot + AI drainer + PollingScheduler (active_chats_count=0) + UQU_Medicine1 + workers + SQLite + API + secrets; commit + push + verify production.

Work Log:
- Verified git state: 3 local commits (2c20050/803822b/2fa72e7) ahead of origin/main (9077819). Working tree clean (only FINAL_REPORT.md untracked).
- Ran baseline tests: 360/360 PASS (confirmed prior session's work intact).
- Launched 4 parallel audit subagents on the actual code (not just AUDIT_FINDINGS):
  * Task 3a (Supabase snapshot): COMPLETED — 5 fixes (concurrent guard, ORDER BY, 15s timeout, 429→60s backoff, per-row isolation) + 8 regression tests.
  * Task 4a (AI drainer): COMPLETED — rewrite to 263 lines (Semaphore(3), wait_for(60s), lease filter ai_approved=is.null, order=id.desc, fail_count(3), batch summary) + 17 regression tests. Conditional-YES to enable.
  * Task 6a (PollingScheduler): FAILED (max turns) — re-done by main agent.
  * Task 9a+10a+5a+11a (workers+sqlite+api+secrets): FAILED (context deadline) — partial work left 9 undefined test calls in main() (corruption). Re-done by main agent.
- Fixed parallel-agent corruption: removed 9 undefined test calls from main(); restored runnable suite (119/119).
- Task 6a (active_chats_count=0 root cause — done by main):
  * ROOT CAUSE: /api/polling_status used `next_poll_at <= ?` but add_monitored_chat left next_poll_at NULL on insert → all 845 chats excluded → count=0. SourceRegistry.select_due_chats already used `(next_poll_at IS NULL OR next_poll_at <= ?)` so the SCHEDULER polled fine, but the STATUS endpoint didn't match.
  * FIX 1: /api/polling_status predicate → `(next_poll_at IS NULL OR next_poll_at <= ?)` (bot.py).
  * FIX 2: add_monitored_chat seeds next_poll_at=now() + last_activity=now() on insert (link_system.py).
  * FIX 3: one-time backfill UPDATE monitored_chats SET next_poll_at=COALESCE(next_poll_at, now) WHERE NULL (link_system.py) — converts the 845 existing NULL rows.
  * 4 regression tests (NULL-counted, seed-on-insert, backfill+idempotency, scheduler predicate).
- Task 9a (supervisor — done by main): confirmed _supervisor_loop watches all 9 critical tasks, _scheduler_relaunch_lock prevents double-relaunch, ai_drainer relaunch gated on AI_DRAIN_ENABLED. 3 source-level guards. (Partial 9a bot.py work from the failed agent was COHERENT and kept.)
- Task 10a (SQLite — done by main): confirmed WAL + busy_timeout=5000 + synchronous=NORMAL in _ensure_conn, _lock double-check (N10). 10×100 concurrent-writer test + 2 source guards.
- Task 5a (API security — done by main): confirmed secrets.compare_digest (constant-time), /health+/ready+/metrics exempt, unset=open backward-compat, _redact_phone masks. 4 tests.
- Task 11a (secrets scan — done by main): working tree 0 secrets; 4 local commits 0 secrets; git HISTORY has 3 real secrets (BOT_TOKEN + API_ID + API_HASH) in download/create_env_v6.py @ commits 68dbb53/66779fe/52427b0 → documented as B23 (external rotation + filter-repo purge). 1 source-file regression guard.
- UQU_Medicine1: only in a comment (bot.py:7762) + worklog investigation. NOT in force-include list. Recovery depends on watcher account membership (iter_dialogs). Code path sound (B04 load_from_db). → external limitation, documented.
- Deleted-message pipeline A-N: re-verified — test_message_journal.py test_A..test_N (18 scenarios) + audit_regressions B05/B08/N01/N02/N04/N05 cover all 14 paths. No new gap from Pass 3 fixes (they don't touch the rescue path).
- Tests: 448/448 PASS (was 360 → +88: 8 snapshot + 17 drainer + 4 polling + 3 supervisor + 2 sqlite + 4 api + 1 secrets + 53 from 3a/4a LogCapture fix surfacing previously-broken assertions). git diff --check exit 0.
- Commit: 1d99afc "AUDIT-FIX(8c): snapshot/drainer hardening + active_chats_count=0 root-cause fix + supervisor/sqlite/api/secrets regressions" (4th commit ahead of origin).
- Push attempt: FAILED (PUSH_RC=128). Exhaustive credential check: git config credential.helper empty, ~/.netrc absent, ~/.git-credentials absent, gh CLI not installed, ~/.ssh/ absent, GH_TOKEN/GITHUB_TOKEN/GH_PAT env unset, git credential fill → "could not read Username". Genuine external blocker — user must push.
- Production verified STILL on 9077819: /ready=ready(4 watchers, db ok); /api/polling_status active_chats_count=0 (6a fix not live); /api/stats 26834 links + 26475 ai_pending. All 22+ fixes remain local-only until push + Render auto-deploy.

Stage Summary:
- Code: 448/448 PASS, 22 original fixes + Pass-3 hardening (3a snapshot, 4a drainer, 6a active_chats_count=0 CRITICAL, 9a supervisor, 10a sqlite, 5a api, 11a secrets). Minimal + backward-compatible. No secrets in 4 local commits.
- 4 commits ahead of origin/main (9077819). Push blocked (no GitHub creds in env).
- 3 secrets in git history (B23) need external rotation + filter-repo purge.
- Verdict: READY WITH EXTERNAL ACTION REQUIRED (corrected code NOT deployed — push blocked).

---
Task ID: PUBLISH-INCIDENT-1
Agent: main (Super Z) — Production Publishing Incident investigation
Task: Focused investigation: why no LINK reaches PUBLISH SUCCESS in production. Trace link_queue → scheduler → PIPELINE-3 → PIPELINE-6 → joiner selection → connection check → join → publish. Render Logs evidence: [PIPELINE-1] Link found, [PIPELINE-2] Link enqueued, [PIPELINE-3] Scheduler picked link id=412, [PIPELINE-6] Selecting joiner, [SUPABASE] Loaded 6 watchers, [SCHED] +967735272360 not connected — skipping. Same LINK id=412 re-picked in cycle=1059 then cycle=1060. No JOIN or PUBLISH success after.

Work Log:
- Git state confirmed: HEAD=1d99afc, origin/main=9077819 (4 commits ahead). Push still blocked (no GitHub creds).
- Baseline tests: 448/448 PASS.
- Traced the pipeline in actual code (bot.py):
  * PIPELINE-1 (line ~4567): link found in event handler
  * PIPELINE-2 (line ~4621): link enqueued to link_queue
  * PIPELINE-3 (line ~7148): scheduler picks queued link
  * PIPELINE-4 (line ~7187): AI verdict (skipped in batch mode by default)
  * PIPELINE-5 (line ~7227): publish to channel via insert_request + _send
  * PIPELINE-6 (line ~7242): joiner selection + join attempt
- ROOT CAUSE IDENTIFIED: joiner selection loop (lines 7312-7333) checked only FloodWait + daily-limit. The connection check was OUTSIDE the loop (lines 7335-7342) and only tested the FIRST selected joiner. If that joiner was disconnected:
  * Scheduler did NOT try other joiners
  * Set link next_retry = now+2min, slept 60s
  * Re-picked the SAME link with the SAME disconnected joiner → infinite retry loop
  * No JOIN happened → no PUBLISH of join confirmation
- SAME ANTI-PATTERN in rate-limiter (7376-7382) and safety-guard (7386-7402): aborting the whole cycle instead of trying the next joiner.
- PIPELINE-5 (publish) analysis: on first cycle, state=DISCOVERED → insert_request runs. If link already in forwarded_requests (duplicate from a previous run), returns False → "Already published (duplicate)" → no new publish (CORRECT behavior — don't re-publish). On subsequent cycles, state=QUEUED → publish block skipped entirely. So "no new publish in channel" = link was already published before, OR _send failed (would log PUBLISH_FAILED + continue, skipping PIPELINE-6).
- LINK id=412 re-selection analysis: no `lease_until` or `retry_count` column in link_queue. `attempt_count` (incremented on every update_queue_status) serves as retry counter. `next_retry_at` is the throttle. When joiner not connected → next_retry=now+2min, attempt_count++. Link re-picked after next_retry passes. Same joiner re-selected (first eligible) → same skip → loop.
- FIX applied (PUBLISH-INCIDENT-1): Restructured joiner selection so connection check, rate-limiter, and safety-guard are ALL INSIDE the for-joiner loop. Each check `continue`s to the next joiner on failure (does NOT abort the cycle). Only if ALL joiners fail does the link get next_retry=+5min. Membership check moved BEFORE selection (it checks all joiners anyway). Added logging markers: [JOINER] selected/unavailable, [JOIN] started/success, [PUBLISH] started/success/failed, [RETRY] state/retry_count/next_retry. Added publish-block-skip debug log when state != DISCOVERED.
- Regression tests: 8 new source-level guards in test_audit_regressions.py (P1-1 through P1-8): connection-check-inside-loop, old-anti-pattern-gone, rate-limiter-inside-loop, safety-guard-inside-loop, no-cycle-abort-sleep-inside-loop, [JOINER]/[PUBLISH]/[JOIN] log markers present.
- Updated test_deployment_updated.py SG-1: accept `jphone` (new loop variable) in addition to `phone`.
- Tests: 456/456 PASS (was 448 → +8 new). git diff --check exit 0.

Stage Summary:
- Root cause: connection check outside joiner-selection loop → infinite retry on disconnected first joiner. Fixed by moving connection + rate-limiter + safety-guard INSIDE the loop.
- Code: 456/456 PASS, 1 focused fix + 8 regression tests. Minimal, backward-compatible, no business-logic change (same checks, same order — just tries next joiner instead of aborting).
- 5 commits ahead of origin/main (9077819). Push still blocked (no GitHub creds).
- Verdict: FIX COMPLETE LOCALLY — NOT deployed (push blocked). Production still has the infinite-retry bug until push + Render auto-deploy.

---
Task ID: DEPLOY-VERIFY-1
Agent: main (Super Z) — push + Render deploy + production verification
Task: User provided GitHub PAT. Push the 5 local commits (ending at 044defc, PUBLISH-INCIDENT-1 fix) to origin/main, wait for Render auto-deploy, then VERIFY in production that [JOIN] success + [PUBLISH] success actually occur (per user requirement: not solved until proven from logs/endpoints).

Work Log:
- Read worklog: confirmed HEAD=044defc, origin/main=9077819 (5 commits ahead), working tree clean. Previous push blocked (no creds).
- Ran full test suite via .venv-test/bin/python (custom asyncio runner, no pytest): 153+66+103+96+35+3 = 456/456 PASS. Matches worklog baseline.
- Set up GitHub credentials using inline credential helper (token passed via env var, not persisted in .git/config): `GH_PUSH_TOKEN=*** git -c credential.helper='!f() { echo "username=x-access-token"; echo "password=$GH_PUSH_TOKEN"; }; f' push origin main`.
- PUSH SUCCESS: `9077819..044defc main -> main`, exit 0. origin/main now = 044defc. Branch in sync.
- Established production baseline (on old SHA 9077819):
  * /ready: ready, bot_connected=true, db_connected=true, 4 watchers
  * /api/deploy_check: total_links=26834, pending_links=793, verdict=HEALTHY, python 3.14.3, 4 user clients connected (+967735451189, +967770309310, +967739407274, +967737990530)
  * /api/polling_status: active_chats_count=0 (6a fix NOT live)
  * /api/joiners_status: 3 joiners — +967735272360 (connected:false), +967739407274 (connected:true), +967737990530 (connected:true); total_joined_groups=0
  * /api/joined_groups: total_joined=0, pending_groups=793, active_joiners=3
  * /api/links?limit=1: latest id=77437 created at 2026-08-25T05:20:23 (~18h stale)
- Polled /api/polling_status every 55s watching for active_chats_count 0→>0 (deploy-landed signal):
  * poll#1 at 00:50:37: active_chats_count=836 → DEPLOY LANDED. (6a fix live = NULL next_poll_at chats now counted.)
- Re-confirmed deploy stable at 00:51:39: active_chats_count=236 (fluctuates as scheduler polls subsets), deploy_check timestamp fresh at 00:51:34 (1s before check).
- PRODUCTION PROOF (all gathered from live endpoints, no code changes):
  * pending_links: 793 → 0 (entire backlog drained by scheduler)
  * total_links: 26834 → 26835 → 26836 (+2 new links PUBLISHED to channel)
  * latest published link: id=77441 (t.me/kku_15, source +967770309310) created 2026-08-26T00:51:52; then id=77446 created 00:54:25 — PUBLISH pipeline running.
  * joined_groups: status="JOINED" for t.me/kku_15 by +967739407274 join_date 2026-08-26T00:51:58 — JOIN SUCCESS (the connected joiner was selected; the broken +967735272360 was skipped per fix).
  * +967739407274 daily_joins: 0 → 1; last_join_timestamp updated to 00:51:58.
  * total_joined_groups: 0 → 1 (the JOINED group).
  * Connected joiner +967739407274 processed 7 groups between 00:51:51 and 00:54:46 (1 JOINED + 6 ALREADY_MEMBER) — stable, continuous operation.
  * Broken joiner +967735272360: connected=false, daily_joins=0, last_join=2026-08-18 (stale) — permanently excluded, never selected. Matches user requirement: exclude disconnected accounts, don't reconnect, don't block scheduler.
- End-to-end chain PROVEN: watcher (+967770309310) found link t.me/kku_15 → enqueued → scheduler picked → PUBLISH to channel (id=77441 @ 00:51:52) → joiner selection skipped disconnected +967735272360 → selected connected +967739407274 → JOIN (@ 00:51:58, status=JOINED).
- ai_approved unchanged at 359 (AI batch-mode pipeline is separate from the publish/join pipeline; AI approval is not required for publish in batch mode — links publish regardless. Not a regression.)

Stage Summary:
- PUSH: SUCCESS. origin/main = 044defc (was 9077819). 5 commits pushed.
- RENDER DEPLOY: SUCCESS. Detected via active_chats_count 0→836 (6a fix live). Server fresh (deploy_check timestamp 1s lag).
- JOIN SUCCESS: PROVEN. status="JOINED" for t.me/kku_15 by connected joiner +967739407274 @ 00:51:58. daily_joins 0→1. total_joined_groups 0→1.
- PUBLISH SUCCESS: PROVEN. New links published to channel (id=77441 @ 00:51:52, id=77446 @ 00:54:25). total_links 26834→26836 (+2).
- BROKEN JOINER: Permanently excluded (+967735272360 connected:false, daily_joins:0, never selected). Matches user requirement.
- QUEUE: Drained 793→0. Scheduler now keeping up with live watcher output.
- VERDICT: FIX VERIFIED IN PRODUCTION. The PUBLISH-INCIDENT-1 root cause (connection check outside joiner-selection loop) is resolved — connected joiners are now selected and both JOIN and PUBLISH succeed end-to-end.

---
Task ID: 1-C
Agent: dedup-delete-investigator
Task: Investigation against user Requirements #4 (dedup) + #5 (capture-before-delete) — INVESTIGATION ONLY

Work Log:
- Read full worklog.md (821 lines) — paid special attention to Task DURABILITY-MESSAGE-JOURNAL (3a snapshot audit), PUBLISH-INCIDENT-1 (publish-path dedup), DEPLOY-VERIFY-1 (production ALREADY_MEMBER evidence).
- Audited normalization: link_system.py LinkNormalizer.extract_links (lines 48-141).
- Audited DB schemas: link_queue (link_system.py:687-710), group_states (725-738), membership_cache (741-747), processed_messages (834-847), message_journal (858-876); forwarded_requests (bot.py:1298-1307); monitored_chats (link_system.py:776-792).
- Audited enqueue path: link_system.ProductionDB.enqueue_link (link_system.py:899-958) — INSERT OR IGNORE + IntegrityError fallback.
- Audited claim path: link_system.ProductionDB.claim_message (link_system.py:1326-1405) — atomic INSERT OR IGNORE + CAS UPDATE on stale/failed.
- Audited journal path: link_system.ProductionDB.journal_message (link_system.py:1487-1502) — INSERT OR IGNORE with PK (chat_id, msg_id).
- Audited NewMessage handler: bot.py Monitor._on_user_message (bot.py:4402-4648) — pre-cache → journal_message → extract_links → claim → enqueue → mark_processed.
- Audited DeleteMessage handler: bot.py Monitor._on_message_deleted (bot.py:4649-4822) — triple-source rescue (cache → journal → DELETE-MISS).
- Audited reconcile: bot.py Monitor._reconcile_chat_after_delete_miss (bot.py:3582-3689) — fetches 15 sibling messages, NO fetch of the deleted message.
- Audited journal recovery: bot.py Monitor._journal_recovery (bot.py:3692-3746) — 60s recurring sweep of 'pending' rows >120s old.
- Audited Supabase snapshot: bot.py Monitor._journal_snapshot_loop (bot.py:4211-4323) + _restore_journal_from_supabase (bot.py:4325-4400) — at-risk-only predicate state IN ('pending','no_text','delete_miss').
- Audited scheduler pipeline: bot.py (lines 7135-7534) — PIPELINE-3..6, publish gated by state==DISCOVERED.
- Audited insert_request: bot.py DatabaseManager.insert_request (bot.py:1473-1536) — race-safe INSERT OR IGNORE then Supabase POST.
- Audited join path: bot.py Monitor._join_group_safe (bot.py:8693-8874) + _safety_guard (bot.py:8496-8553) + cross-joiner membership check (bot.py:7320-7346).
- Audited priority_scorer (bot.py:7583-7712) — fetches target group entity, NOT the source message.
- Cross-referenced tests/test_message_journal.py Test A..N (lines 178-786) — confirms expected dedup/rescue/no-double-enqueue behavior.

Stage Summary:
============================================================
REQUIREMENT #4 (DEDUPLICATION) — STRONG, WITH ONE NARROW EDGE CASE
============================================================

#4.1 — Link normalization: ✅ IMPLEMENTED (robust)
  Evidence: link_system.py:48-141 (LinkNormalizer.extract_links).
    - t.me/+abc123     → tg:invite:abc123  (line 97)
    - t.me/joinchat/abc123 → tg:invite:abc123  (lines 95-97) ✅ SAME canonical form
    - t.me/GroupName   → tg:user:groupname (line 102) — case-folded
    - t.me/GroupName/42072578 → tg:user:groupname (lines 85-92 strip /msg_id before normalizing)
    - t.me/GroupName/  → tg:user:groupname (regex `(?:/(\d+))?` doesn't match trailing slash alone)
  Stored in UNIQUE column: link_system.py:709 `UNIQUE(normalized_link)` on link_queue. ✅

#4.2 — DB UNIQUE constraints: ✅ IMPLEMENTED
  Evidence:
    - link_system.py:709 `UNIQUE(normalized_link)` on link_queue
    - link_system.py:726 `normalized_link TEXT PRIMARY KEY` on group_states
    - link_system.py:746 `PRIMARY KEY (phone, normalized_link)` on membership_cache
    - link_system.py:791 `UNIQUE(chat_id)` on monitored_chats
    - link_system.py:846 `PRIMARY KEY (chat_id, msg_id)` on processed_messages
    - link_system.py:875 `PRIMARY KEY (chat_id, msg_id)` on message_journal
    - bot.py:1306 `content_hash TEXT NOT NULL UNIQUE` on forwarded_requests
  INSERT OR IGNORE used uniformly: enqueue_link (link_system.py:914), journal_message (1493),
  claim_message (1354), add_monitored_chat (1082), insert_request (1514), restore (4373).
  Duplicate insert → IntegrityError fallback at link_system.py:948-958 (returns False, only for
  genuine duplicates; OperationalError/disk-full/etc. PROPAGATE so caller can retry — does NOT
  silently swallow as duplicate).

#4.3 — Cross-account dedup: ✅ IMPLEMENTED at 3 layers (all race-safe via SQLite PK)
  Evidence:
    1. journal_message INSERT OR IGNORE with PK(chat_id, msg_id) (link_system.py:1493) — first
       writer wins, second's INSERT OR IGNORE rowcount=0 silently skips.
    2. claim_message INSERT OR IGNORE + CAS UPDATE (link_system.py:1354, 1392-1401) — atomic
       winner-take-all via SQLite PK + lease_token. Loser returns None (line 1389) →
       bot.py:4513-4514 silently returns WITHOUT calling extract_links or enqueue_link.
    3. enqueue_link INSERT OR IGNORE with UNIQUE(normalized_link) (link_system.py:914) —
       final gate; both watchers' NewMessage handlers may call enqueue_link, but only the first
       INSERT succeeds; second's rowcount=0 → returns False → "Duplicate" log (bot.py:4624).
  Dedup gate location: AT ENQUEUE (NewMessage handler). The downstream publish/join paths
  inherit dedup because the same normalized_link is never re-enqueued. ✅

#4.4 — No re-enqueue after processing: ✅ IMPLEMENTED
  Evidence:
    - enqueue_link (link_system.py:914-947): INSERT OR IGNORE → if rowcount=0 (existing row),
      returns False UNLESS allow_requeue=True (default False). The re-queue path (lines 929-945)
      only fires if caller explicitly passes allow_requeue=True AND existing status IN
      ('DONE','REJECTED','FAILED'). Default call site (bot.py:4616 `_on_user_message`,
      bot.py:3553 `_rescue_enqueue_links`) does NOT pass allow_requeue → safe no-re-enqueue.
    - On publish: link_queue row status transitions DISCOVERED→QUEUED→DONE; DONE rows are NOT
      picked by `WHERE status='QUEUED'` (link_system.py:985). Same for JOINED/BANNED/FAILED.
    - The DELETE-rescue path (bot.py:4776 _rescue_enqueue_links → enqueue_link) reuses the same
      INSERT OR IGNORE — duplicate rescue attempt for an already-queued link returns False
      (logged "⏭️ Duplicate" at bot.py:3565). No double-enqueue possible.

#4.5 — No double-join: ✅ IMPLEMENTED (4-layer defense)
  Evidence:
    1. TOP-OF-CYCLE: bot.py:7154 `if state in (GroupState.JOINED, GroupState.ALREADY_MEMBER): …
       update_queue_status(link_id, 'DONE') → continue`. Skips JOIN entirely.
    2. CROSS-JOINER PRE-CHECK: bot.py:7320-7346 — iterates ALL joiners, calls
       membership_cache.check_membership(jphone, normalized, jclient). If ANY is_member=True →
       set_group_state(ALREADY_MEMBER) + mark DONE + skip.
    3. SAFETY GUARD CHECK #5: bot.py:8533-8537 — re-reads group_state, rejects with
       'already_attempted_{state}' if state in (JOINED, ALREADY_MEMBER).
    4. TELEGRAM API: bot.py:8763 + 8851 — UserAlreadyParticipantError → returns
       (False, "ALREADY_MEMBER", None); mapped at bot.py:7477-7481 to set_group_state(ALREADY_MEMBER)
       + final_status='DONE'.
  Membership check uses link_system.MembershipCache.check_membership (link_system.py:424-465)
  with hybrid Memory(1h) → DB(7d TTL) → API fallback. membership_cache PK (phone, normalized_link)
  at link_system.py:746 prevents duplicate per-(phone, link) rows; set_membership uses
  INSERT OR REPLACE (link_system.py:1192). Production proof of ALREADY_MEMBER path:
  worklog.md DEPLOY-VERIFY-1 (lines 806-810) — connected joiner processed 7 groups, 6 with
  status=ALREADY_MEMBER without re-attempting JOIN. ✅

#4.6 — No double-publish: ✅ IMPLEMENTED (PUBLISH-INCIDENT-1 confirmed)
  Evidence:
    - bot.py:7170 `if state == GroupState.DISCOVERED or state is None:` gate → insert_request
      runs ONLY on first cycle (state was DISCOVERED before line 7204 set it to QUEUED).
      Subsequent cycles hit `else` branch at bot.py:7238-7239: "state={state} — publish block
      skipped (already queued, no re-publish)".
    - insert_request (bot.py:1473-1536): race-safe INSERT OR IGNORE into forwarded_requests with
      UNIQUE(content_hash) → if rowcount=0 (duplicate) returns False → caller logs
      "Already published (duplicate)" (bot.py:7237) and skips the _send call.
    - The set_group_state(QUEUED) at bot.py:7204 happens BEFORE insert_request, so even if
      insert_request returns False for a NON-duplicate reason (SQLite error), the next cycle's
      state==QUEUED → publish skipped. (Caveat: see GAPS below.)
  NOTE (gap — see below): content_hash uses MD5 of `link.lower().strip().rstrip("/")` with
  fragment/query stripped (bot.py:1499-1507). For `t.me/+abc` vs `t.me/joinchat/abc`, this
  yields DIFFERENT content_hashes — BUT link_queue's UNIQUE(normalized_link) catches the
  duplicate at enqueue (both forms normalize to tg:invite:abc), so the second form never reaches
  insert_request. No realistic double-publish path observed in normal operation.

GAPS UNDER #4 (LOW SEVERITY, narrow edge cases):
  G4-A (rare): if a link_queue row is MANUALLY deleted (e.g. via /cleanup_links command at
    bot.py:6250) and the same group later re-appears via a DIFFERENT raw link form (e.g. the
    +abc form was deleted, the joinchat/abc form is posted), the new enqueue succeeds (no
    UNIQUE violation) and the new insert_request computes a DIFFERENT content_hash → forwarded
    to channel a second time. The two raw forms resolve to the same canonical normalized_link,
    but link_queue has no row to compare against (it was manually purged). NOT triggered by any
    automated path — requires operator running /cleanup_links. Mitigation: /cleanup_links only
    deletes rows matching `/+` or `joinchat` or `t.me/user/123` patterns (bot.py:6240-6243), so
    a private link deleted by /cleanup_links leaves no record of the normalized form either.

  G4-B (rare, partial): if insert_request (bot.py:1510-1524) raises a generic Exception (NOT
    IntegrityError — INSERT OR IGNORE handles those silently) e.g. sqlite3.OperationalError
    (locked DB / disk full), it returns False. The caller logs "Already published (duplicate)"
    (bot.py:7237) and continues to PIPELINE-6 — but the link was NEVER actually published.
    State is already QUEUED so the next cycle's publish block is skipped → publish NEVER
    happens. Severity: MEDIUM — link goes through JOIN but no channel publish. Operator would
    see the link missing from the channel. (Not a double-publish; an un-caught no-publish.)

============================================================
REQUIREMENT #5 (CAPTURE-BEFORE-DELETE) — STRONG, WITH ONE DOCUMENTED GAP
============================================================

#5.1 — Synchronous capture on NewMessage: ✅ IMPLEMENTED
  Evidence: bot.py Monitor._on_user_message (lines 4402-4648).
    Order of operations per NewMessage event:
      1. event.raw_text, event.chat_id, event.sender_id, event.id — read synchronously
         (bot.py:4413-4419). NO API call before this.
      2. PRE-CACHE in memory (bot.py:4462-4475) — microsecond, async-lock only.
      3. DURABLE JOURNAL WRITE (bot.py:4483-4486): `await self._journal_write(...)` →
         `journal_message` → INSERT OR IGNORE into message_journal with state='pending'.
         This is the FIRST await that could let a deletion race through, but it's a fast
         SQLite INSERT (~1-10ms), and it LEAVES THE RAW_TEXT PERSISTED before extract_links.
      4. extract_links (bot.py:4489) — regex, no API.
      5. claim_message (bot.py:4503) — atomic SQLite INSERT OR IGNORE + CAS.
      6. enqueue_link (bot.py:4616) — INSERT OR IGNORE into link_queue.
      7. mark_processed + journal_set_state('processed') (bot.py:4628-4629).
  Critical: the journal_message write at step 3 is SYNCHRONOUS in the event handler (not
  deferred to a background task) — see _journal_write wrapper (bot.py:3425-3459) which is
  `await`-ed directly. After step 3, the raw_text is persisted to BOTH in-memory cache AND
  durable SQLite journal. If the protection bot deletes the original Telegram message at
  any time after step 3, the link is recoverable from cache (120s) or journal (24h retention
  for terminal states, indefinite until cleanup for 'pending' state per N01 fix at
  link_system.py:1601-1602). ✅
  NO deferred/async pipeline that could lose the link if the message is deleted mid-flight.

#5.2 — Message journal rescue + DeleteMessage reconciliation: ✅ IMPLEMENTED
  Evidence:
    - NewMessage writes journal row at bot.py:4483-4486 IMMEDIATELY (state='pending') — see #5.1.
    - MessageDeleted event handler at bot.py:3314-3316 (registered for every user_client).
    - _on_message_deleted (bot.py:4649-4822): TRIPLE-SOURCE rescue:
        (a) _msg_cache (in-memory, TTL 120s) — bot.py:4685-4696
        (b) message_journal (durable SQLite, survives restart + TTL expiry) — bot.py:4698-4717
            Uses journal_get(chat_id, deleted_msg_id) for deterministic path (line 4709),
            falls back to journal_lookup_any(deleted_msg_id) only when chat_id is None (line 4711)
            — B09 fix prevents wrong-chat rescue.
        (c) DELETE-MISS path (bot.py:4719-4724): records a 'delete_miss' journal row + spawns
            _reconcile_chat_after_delete_miss for sibling-message recovery.
      When a cached/journaled message is found and NOT yet processed (state not in
      'processed/no_links/no_text/dup_claim/rescued' at bot.py:4727-4729), the handler:
        - extracts links (bot.py:4748)
        - claims via MessageClaim (bot.py:4756-4767 — loser returns None, sets 'dup_claim')
        - calls _rescue_enqueue_links (bot.py:4776) which uses the SAME enqueue_link path
          with UNIQUE(normalized_link) — duplicate rescue attempts are no-ops.
        - sets journal state 'rescued' (bot.py:4791-4793) or 'pending' on exception (4799-4801).
  Reconciliation RECOVERS the link (does NOT lose it). The deleted message itself is
  fetched from cache/journal, never re-fetched from Telegram. ✅
  Verified by tests/test_message_journal.py: Test I (delete rescue from journal with EMPTY
  cache — simulates restart), Test L (already-processed deleted → no double enqueue).

#5.3 — Independence from message persistence: ✅ IMPLEMENTED
  Evidence: Once a link is enqueued (link_queue row + group_states row), the scheduler pipeline
    (bot.py:7135-7534) operates on the link_queue row data ONLY:
      - PIPELINE-3 picks link by id from link_queue (link_system.py:980-988) — no message fetch.
      - PIPELINE-4 AI verify uses link_data.get('message_text') + group_name (bot.py:7179) — both
        already stored in link_queue (raw text snapshot from when NewMessage fired).
      - PIPELINE-5 publish uses raw_link + group_name + sender_name + message_text from
        link_data (bot.py:7207-7223) — no message fetch.
      - PIPELINE-6 membership check (bot.py:7329) calls check_membership(jphone, normalized,
        jclient) which queries the TARGET group's membership, NOT the source message
        (link_system.py:424-465).
      - _join_group_safe (bot.py:8693-8874) operates on link_data['link_type'] / username /
        invite_hash — no source-message fetch.
      - _priority_scorer (bot.py:7583-7712) fetches the TARGET group entity via
        monitor_client.get_entity(username) — NOT the source message.
  The ONLY path that fetches messages from a chat is _reconcile_chat_after_delete_miss
  (bot.py:3603 — `await client.get_messages(chat_id, limit=15)`), and it runs ONLY after a
  DELETE-MISS to recover SIBLING messages (other messages in the same chat that may have been
  missed). The DELETED message itself is NEVER fetched (Telegram doesn't return deleted msgs).
  ✅ Fully independent of whether the original Telegram message still exists.

#5.4 — Delete reconciliation safety: ✅ GOOD BEHAVIOR (option (b) — leave in queue)
  Evidence: bot.py:4649-4822 _on_message_deleted.
    - When the deleted message is NOT in cache and NOT in journal → DELETE-MISS forensics only
      (bot.py:4720-4724): records a 'delete_miss' journal row + spawns reconcile. Does NOT
      touch any existing link_queue row. ✅ Leave in queue to process (option b).
    - When the deleted message IS in cache/journal AND already processed → mark_deleted_safe
      only (bot.py:4731-4736): just stamps deleted_at timestamp on the journal row for forensics.
      Does NOT delete the link_queue row, does NOT change group_state, does NOT cancel
      processing. ✅ Leave in queue to process (option b).
    - When the deleted message IS in cache/journal AND NOT processed → RESCUE: extract links +
      enqueue (bot.py:4738-4793). The enqueue uses the SAME enqueue_link path with
      UNIQUE(normalized_link), so if the link was already enqueued by another path
      (NewMessage, polling, journal_recovery), the rescue's INSERT OR IGNORE returns False —
      no duplicate. The rescued link is then processed normally by the scheduler. ✅
    - The handler NEVER (a) marks link as cancelled/lost, NEVER (c) re-verifies message
      existence and drops if missing. It only enriches forensic metadata (deleted_at) and
      rescues unprocessed content. ✅ EXACTLY option (b) — the GOOD behavior the user wants.
  Verified by tests/test_message_journal.py Test L (lines 656-686): "already-processed message
  deleted → NO double enqueue; journal state stays 'processed'; deleted_at is set."

#5.5 — At-risk-only snapshot design + restart-loss gap: ⚠️ PARTIAL (one documented narrow gap)
  Evidence: bot.py:4211-4323 _journal_snapshot_loop, bot.py:4325-4400 _restore_journal_from_supabase.
    Snapshot SELECT predicate (bot.py:4239-4246):
      `WHERE state IN ('pending','no_text','delete_miss') ORDER BY received_at ASC LIMIT 500`
    Cycle: every 30s (bot.py:4321), with 40s startup settle delay (bot.py:4224).
    Restore at startup (bot.py:9005) runs SYNCHRONOUSLY before _journal_recovery task is
    created (line 9012) and before _journal_snapshot_loop task is created (line 9019).
    Snapshot POST is atomic per PostgREST batch (Task 3a point 1).
    On failure, rows are NOT marked (next cycle re-POSTs same rows; upsert PK idempotent).
  "At-risk-only" means: ONLY rows in transient/non-terminal states get snapshotted.
    - 'pending' = NewMessage fired but processing didn't complete (crash mid-flight, slow DB).
    - 'no_text' = NewMessage fired, message had no text (lightweight forensic; terminal state
      but still snapshotted because raw_text is NULL → cheap).
    - 'delete_miss' = NewMessage never fired, DeleteMessage did (no recovery possible for
      the deleted message itself; snapshot provides forensic evidence).
    Terminal states ('processed', 'rescued', 'no_links', 'dup_claim', 'blacklisted') are
    deliberately EXCLUDED — by then the link is already in link_queue with its own
    UNIQUE(normalized_link) constraint providing durability.
  Restart-loss gap (Task 3a Stage Summary, page 702):
    If Render free-tier restart wipes the ephemeral disk (local SQLite gone) BETWEEN:
      (1) NewMessage firing journal_message(state='pending') at bot.py:4483, AND
      (2) The next 30s snapshot cycle POSTing that row to Supabase,
    AND the original Telegram message was already deleted by a protection bot (so polling
    can't recover it via get_messages), THEN the link is LOST.
    Window size: 0–30s (snapshot cycle) + ~5ms (processing time in healthy operation).
    In healthy operation: ~5ms after NewMessage, state becomes 'processed' → snapshot
    INELIGIBLE (excluded by predicate) → the link's durability is provided by
    link_queue UNIQUE(normalized_link) on local SQLite (also wiped on ephemeral restart).
    In crash/slow-processing: state stays 'pending' → snapshot catches it on the next cycle.
    Documented in worklog.md Task 3a Stage Summary (page 702-705):
      "Render ephemeral-disk restart (B01): the snapshot mirror survives restart, but a real
       persistent disk at /data (DATA_DIR=/data) is still the recommended durability path.
       Operator action — not code-blockable."
  Could a link that's enqueued-but-not-yet-snapshotted be lost on a restart? YES, in the
  narrow window described. Probability: low (requires restart in the 30s window AND original
  message already deleted AND ephemeral disk wipe). The _restore_journal_from_supabase path
  DOES catch anything that was snapshotted before the restart; the gap is the unsnapshotted
  pending rows in the 0–30s window.
  Mitigation options (NOT in code — design tradeoff documented in bot.py:4185-4192 comment):
    (a) persistent disk at /data (DATA_DIR=/data) on Render — eliminates ephemeral-wipe risk.
    (b) more frequent snapshot cycle (e.g. 10s) — puts more pressure on Supabase free tier.
    (c) synchronous Supabase write-ahead on every NewMessage — adds latency to hot path.

GAPS UNDER #5:
  G5-A (documented, narrow, B01 in worklog): 0–30s window between NewMessage's journal_message
    write (bot.py:4483) and the next snapshot cycle (bot.py:4321). If Render free-tier restart
    wipes ephemeral disk in this window AND the protection bot has already deleted the original
    message, the link is LOST. Mitigation: persistent disk (operator action). All other capture-
    before-delete scenarios are covered by cache (120s) + journal (24h/indefinite pending) +
    Supabase snapshot (after first 30s cycle). This is the SOLE remaining path where a link could
    realistically be lost — and it's a deployment-config issue, not a code bug.

  G5-B (external, no code fix possible): "DELETE-MISS" scenario — protection bot deletes the
    message BEFORE any watcher account's NewMessage fires (full delivery gap). The link from
    THAT specific message is genuinely unrecoverable (Telegram doesn't return deleted messages
    via get_messages). The code makes this VISIBLE (delete_miss journal row + WARNING at
    bot.py:3485-3492) and attempts SIBLING recovery via reconcile (bot.py:3582-3689), but the
    deleted message itself is lost. Documented in worklog.md DURABILITY-MESSAGE-JOURNAL Stage
    Summary (page 438). Not a code defect — Telegram's API doesn't expose deleted content.

============================================================
OVERALL VERDICT
============================================================
- Requirement #4 (dedup): STRONG. Six-layer dedup chain (LinkNormalizer canonicalization →
  link_queue UNIQUE(normalized_link) → journal_message PK → claim_message PK+CAS →
  insert_request UNIQUE(content_hash) → membership_cache PK + state-machine gating).
  No realistic path for double-enqueue / double-publish / double-join in normal operation.
  Two narrow edge cases (G4-A manual cleanup + different link form; G4-B generic SQLite error
  misclassified as "Already published") — neither is a duplicate, neither silently loses a
  link in the dedup direction.

- Requirement #5 (capture-before-delete): STRONG. Three-layer capture (in-memory cache →
  durable SQLite journal → Supabase snapshot) with synchronous journal write on NewMessage
  BEFORE any await that could race deletion. DeleteMessage reconciliation uses GOOD option (b):
  leaves queued links alone, rescues unprocessed content from cache/journal, never re-verifies
  existence of the deleted message, never cancels a queued link.
  ONE documented narrow gap (G5-A): 0–30s snapshot window + ephemeral-disk restart + already-
  deleted original message. Mitigation is operator action (persistent disk at /data).

No code changes made (investigation only). All findings are evidence-cited with file:line.

---
Task ID: 1-D
Agent: join-publish-verify-investigator
Task: Investigation against user Requirements #6 (disconnected) + #7 (JOIN verify) + #8 (PUBLISH verify) — INVESTIGATION ONLY

Work Log:
- Read worklog.md PUBLISH-INCIDENT-1 (lines 748-780) + DEPLOY-VERIFY-1 (lines 782-821) sections to establish the fix baseline (commit 044defc, production verified status=JOINED for t.me/kku_15 by connected joiner +967739407274).
- Grep'd bot.py for [PIPELINE-N], [JOINER], [JOIN], [PUBLISH], [RETRY] log markers — all present, mapped each marker to line.
- Read PIPELINE-6 joiner-selection loop (bot.py:7309-7431) to verify connection/rate-limiter/safety-guard placement.
- Read `_join_group_safe` (bot.py:8693-8877) + `_verify_membership` (bot.py:8624-8691) to verify JOIN result states.
- Read PIPELINE-5 publish block (bot.py:7204-7239) + `_send` (bot.py:5248-5362) + `insert_request` (bot.py:1473-1536) to verify PUBLISH verification.
- Read `_run_user_client` (bot.py:6943-7032) to check for reconnection logic on disconnected accounts.
- Read `/api/joined_groups` handler (bot.py:9128-9183) and `set_group_state` (link_system.py:1159-1173) to verify DB persistence.
- Grep'd for `connect()`, `sign_in`, `is_connected`, `log_operation`, `record_success`, `record_join_success` to map the reconnection + metric paths.
- Confirmed `record_success` (link_system.py:349-361) IS called from bot.py:8790/8836/8841 — populates `api_operations_log` with action_type='join' (so `get_daily_join_count` works correctly).

Stage Summary:

REQUIREMENT #6 (Disconnected accounts permanently excluded):
- 6.1 is_connected() INSIDE joiner-selection loop + `continue` (NOT abort): ✅ IMPLEMENTED
  - bot.py:7386-7392: `jclient = self.user_clients.get(jphone); if not jclient or not jclient.is_connected(): logging.warning(...reason=not_connected); last_skip_reason=f'not_connected_{jphone}'; continue`
  - Inside `for joiner in joiners:` loop at bot.py:7362. Pre-loop membership check at bot.py:7326 also uses is_connected()+continue inside its own loop.
- 6.2 Reconnection logic on disconnected joiners: ⚠️ PARTIAL
  - Scheduler path: NO reconnection. bot.py:7387 just checks + continue. ✅
  - BUT `_run_user_client` (bot.py:6954-7032) IS a background reconnection loop for ALL user clients including joiners. On disconnect, `run_until_disconnected()` (7015) returns, loop catches exception, sleeps with backoff (5→10→...→600s), re-enters `while self._running:` and re-calls `await client.connect()` at bot.py:6985 + `is_user_authorized()` at bot.py:6988. Independent of scheduler (doesn't block) but DOES attempt reconnection.
  - Only `connect()` calls in bot.py: line 6985 (user_client lifecycle), line 5640 (interactive /login flow for new accounts — not relevant).
  - Production reality: +967735272360 stayed connected:false even with this loop running (session presumably invalid → `is_user_authorized()` fails → `_cleanup_user_client`+return at 6994). So broken sessions ARE permanently excluded in practice.
- 6.3 next_retry set only when NO joiner eligible: ✅ IMPLEMENTED
  - bot.py:7419-7431: `if not selected_joiner:` → log `[JOINER] no eligible joiner (last_reason=...)` + `[RETRY] state=QUEUED retry_count=+1 reason=no_joiner next_retry=+5min` → `update_queue_status(id, 'QUEUED', next_retry=+5min)` → `asyncio.sleep(30)` → continue.
  - Individual joiner failures (floodwait, daily_limit, not_connected, rate_limited, safety_guard) just `continue` to next joiner WITHOUT setting next_retry.
- 6.4 Disconnected joiner excluded at selection time vs skipped inside loop: ✅ IMPLEMENTED (skipped inside loop — either is acceptable per user)
  - `get_watchers_by_role("joiner")` (bot.py:7309, def bot.py:1326-1334) returns ALL active joiners from Supabase with NO is_connected() pre-filter. Disconnected joiners ARE in the `joiners` list, skipped at bot.py:7387 via `continue`.
- 6.5 rate_limiter and safety_guard INSIDE the loop: ✅ IMPLEMENTED
  - rate_limiter: bot.py:7395-7402 INSIDE loop; `allowed = await self.rate_limiter.check(jphone, 'join')` → if not: log+last_skip_reason+continue.
  - safety_guard: bot.py:7404-7412 INSIDE loop; `guard_ok, guard_reason = await self._safety_guard(jphone, normalized, link_data)` → if not: log+last_skip_reason+continue.
  - Fix comment at bot.py:7350-7358 explicitly documents the PUBLISH-INCIDENT-1 move-inside-loop.

REQUIREMENT #7 (JOIN result verification with clear states):
- 7.1 Status set to clear states after Join API: ✅ IMPLEMENTED
  - `_join_group_safe` (bot.py:8693) returns (success, status, member_count) with statuses: JOINED_VERIFIED (8837), JOIN_UNVERIFIED (8791/8846), ALREADY_MEMBER (8764/8852), FLOODWAIT (8768/8856), BANNED (8775/8863), PRIVATE (8771/8859), TIMEOUT (8762/8850), DISCONNECTED (8708), INVALID, RATE_LIMITED, IS_CHANNEL, SKIP, MONITOR_NO_JOIN, JOINER_DISABLED, PAUSED, SIMULATION, FAILED.
  - Mapped to GroupState (JOINED/ALREADY_MEMBER/FLOODWAIT/BANNED/FAILED/PRIVATE) at bot.py:7453-7557.
  - Production /api/joined_groups reads group_states WHERE state IN ('JOINED','ALREADY_MEMBER') (bot.py:9143-9145).
- 7.2 ALREADY_MEMBER distinguished from JOINED: ✅ IMPLEMENTED
  - Pre-check: `membership_cache.check_membership` for all connected joiners BEFORE selection (bot.py:7320-7346) → set_group_state(ALREADY_MEMBER) + DONE + continue (no Join API call).
  - Post-join: `UserAlreadyParticipantError` exception (bot.py:8763, 8851) → returns (False, "ALREADY_MEMBER", None) → GroupState.ALREADY_MEMBER at bot.py:7477-7481.
- 7.3 ChannelPrivateError/FloodWaitError/InviteHashExpiredError/UserNotParticipantError mapped distinctly: ✅ IMPLEMENTED
  - ChannelPrivateError → "PRIVATE" (bot.py:8771, 8859).
  - InviteHashExpiredError → "PRIVATE" (lumped with ChannelPrivate — same catch, reasonable since both mean broken/private link).
  - FloodWaitError → "FLOODWAIT" (bot.py:8768, 8856) — separate record_floodwait call.
  - UserNotParticipantError (in _verify_membership bot.py:8653) → returns (False, None) → caller degrades to "JOIN_UNVERIFIED".
  - PeerFloodError/UserBannedInChannelError → "BANNED" (bot.py:8775, 8863) — triggers auto-pause at bot.py:7502-7504.
  - Each maps to distinct GroupState at bot.py:7477-7557.
- 7.4 Status persisted to DB: ✅ IMPLEMENTED
  - bot.py:7561-7566: `set_group_state(normalized, state_to_set, raw_link, joined_by=phone if success else None, member_count=member_count if success else None, error=state_error)`.
  - bot.py:7570-7572: `update_queue_status(link_data['id'], final_status, next_retry=next_retry)`.
  - /api/joined_groups (bot.py:9128-9183) reads group_states → reflects real verified outcomes.
- 7.5 False success without verification: ⚠️ PARTIAL
  - JOIN_UNVERIFIED path (bot.py:7465-7475) marks `state_to_set = GroupState.JOINED` + calls `record_join_success` (7470) + `increment_joiner_stats(success=True)` (7471) even when GetParticipant verification failed. Sets `state_error='join_unverified'` (7468) as warning flag.
  - For PUBLIC links: verification IS attempted via `_verify_membership` (bot.py:8833 → 8624-8691). Degrades to JOIN_UNVERIFIED only if GetParticipant itself fails (UserNotParticipant 8653, Timeout 8661, ChannelPrivate 8668, FloodWait 8676, other Exception 8684).
  - For PRIVATE INVITE links (telegram_private): verification is NEVER attempted (bot.py:8782-8791, comment "we don't have entity easily"). Always returns JOIN_UNVERIFIED. So private-invite joins are "trusted API accept" with no independent verify.
  - Gap: `/api/joined_groups` may include JOINED entries that were never independently verified. The `last_error` column distinguishes 'join_unverified' from real verified joins — so the data IS distinguishable in DB, but the API response lumps both as state=JOINED.

REQUIREMENT #8 (PUBLISH actual verification, not just DB insert):
- 8.1 Publish verified by real Telegram Message.id (success path): ✅ IMPLEMENTED
  - bot.py:7207 `insert_request` (DB insert to forwarded_requests, bot.py:1514) + bot.py:7226 `_send` (actual Telegram send).
  - `_send` (bot.py:5248-5362): only returns (True, message_id) when `result and hasattr(result, 'id')` (bot.py:5292) — Telegram returned a real Message object after RPC ack. Otherwise retries with backoff, returns (False, None) on exhaustion.
  - `[PUBLISH] success message_id={msg_id}` (bot.py:7228) + `[PIPELINE-5] PUBLISHED_VERIFIED` (7229) logged ONLY after `_send` returns (True, msg_id).
- 8.2 _send failure handling — false publish possible?: ⚠️ PARTIAL (false "published" DB row possible)
  - bot.py:7230-7235 handles _send failure: logs `[PUBLISH] failed reason=send_failed` + `[PIPELINE-5] PUBLISH_FAILED — retry in 5 min` + `update_queue_status(id, 'QUEUED', next_retry=+5min)` + `continue` (skips PIPELINE-6).
  - BUT: `insert_request` at bot.py:7207 ran BEFORE `_send` and inserted a row into `forwarded_requests` (UNIQUE on content_hash). The row is NOT rolled back on _send failure → phantom row exists.
  - AND: `set_group_state(QUEUED)` at bot.py:7204 ran BEFORE publish attempt. On retry cycle (5 min later), state is QUEUED → bot.py:7170 `if state == GroupState.DISCOVERED or state is None:` is False → publish block SKIPPED at bot.py:7239 → publish NEVER re-attempted.
  - AND: if re-discovered later, `insert_request` returns False (duplicate via phantom row) → "Already published (duplicate)" at bot.py:7237 → publish block skipped again.
  - NET EFFECT: a failed `_send` leaves a phantom row in `forwarded_requests` (DB says published, channel has no message) AND the publish is never retried (state advanced to QUEUED + duplicate-row blocks future attempts). The link proceeds to PIPELINE-6 (join) without being published to the channel.
  - This IS a false-success path for PUBLISH from the DB perspective. NOT from the log perspective ([PUBLISH] failed IS logged).
- 8.3 Re-verification step (fetch channel message by id): ❌ MISSING
  - No `get_messages(message_id=msg_id)` call after `_send`. Searched bot.py for get_messages — only in scan/history/cleanup contexts (3603, 5026, 7936, 8317-8430), NOT after _send.
  - PUBLISHED_VERIFIED is based solely on Telethon's `send_message` return value (Message.id present = Telegram RPC acked).
  - Telethon's ack IS a strong signal (the Message object only comes back after Telegram accepts the send) — but no post-send re-fetch to confirm the message is actually persisted/delivered.
- 8.4 Log markers present + success-only-after-verify: ✅ IMPLEMENTED
  - All markers present: [PIPELINE-1] (4567), [PIPELINE-2] (4621), [PIPELINE-3] (7148), [PIPELINE-4] (7187-7202), [PIPELINE-5] (7229 success / 7232 failed / 7237 duplicate / 7239 skipped), [PIPELINE-6] (7242-7557), [JOINER] (7311/7369/7380/7389/7399/7409/7421/7435), [JOIN] (7443/7459 + many in _join_group_safe 8707-8876), [PUBLISH] (7225/7228/7231), [RETRY] (7424/7493).
  - Success markers logged ONLY AFTER verification:
    * [PUBLISH] success (7228) → after _send returns (True, msg_id).
    * [PIPELINE-5] PUBLISHED_VERIFIED (7229) → after _send returns msg_id.
    * [JOIN] success (7459) → after _join_group_safe returns (True, JOINED_VERIFIED|JOIN_UNVERIFIED, member_count).
    * [PIPELINE-6] JOINED_VERIFIED (7461) → after _verify_membership GetParticipantRequest success (8646-8652).
    * [PIPELINE-6] JOIN_UNVERIFIED (7473) → warning, after Telegram accepted API but verify failed.
  - Failure outcomes logged distinctly: [PUBLISH] failed, [PIPELINE-5] PUBLISH_FAILED, [JOINER] unavailable reason=X, [PIPELINE-6] ❌ TIMEOUT/FLOODWAIT/BANNED/PRIVATE/RATE_LIMITED/IS_CHANNEL/INVALID/DISCONNECTED.
- 8.5 State machine in link_queue reflecting REAL verified transitions: ⚠️ PARTIAL
  - link_queue.status (link_system.py:917 enqueue='QUEUED'; bot.py:7441 'PROCESSING'; bot.py:7570 'DONE' or 'QUEUED'+next_retry on retry).
  - group_states.state (12 states, link_system.py:148-161): UNKNOWN/DISCOVERED/QUEUED/JOINING/JOINED/FAILED/PRIVATE/INVALID/EXPIRED/FLOODWAIT/BANNED/ALREADY_MEMBER.
  - Observed transitions: DISCOVERED (enqueue, bot.py:4619) → QUEUED (pre-publish, bot.py:7204) → JOINING (pre-join, bot.py:7440) → JOINED/ALREADY_MEMBER/FLOODWAIT/BANNED/FAILED/PRIVATE (post-join-verify, bot.py:7453-7557).
  - Gap: PUBLISH has NO explicit state representation. PUBLISHED is implicit by row existence in `forwarded_requests` (a dedup table, link_system.py:1514, NOT a state table). No PUBLISHED value in GroupState enum.
  - JOIN state DOES reflect verified transitions for verified joins (set_group_state after _verify_membership GetParticipant). BUT JOIN_UNVERIFIED also sets state=JOINED (bot.py:7467) — so JOINED is not strictly "verified".

REAL GAPS FOUND — paths where false success could be recorded:
1. JOIN_UNVERIFIED → state=JOINED (bot.py:7467): A join where Telegram accepted the API call but GetParticipant verify failed appears as state=JOINED in /api/joined_groups. Distinguishable only via last_error='join_unverified' column. For private-invite links, NEVER verified.
2. PUBLISH phantom row (bot.py:7207 insert_request runs BEFORE bot.py:7226 _send): If _send fails, forwarded_requests has a row but no Telegram channel message exists. Retry cycle SKIPS publish (state=QUEUED, not DISCOVERED). Link proceeds to JOIN without being published. This is the most material gap — the DB shows the link as "published" but the channel has no message.
3. No PUBLISHED state in GroupState enum: publish verification is only at runtime via _send return value; no persistent state column to query "is this link really published?".
4. No post-send re-fetch: PUBLISHED_VERIFIED is based on Telethon's send_message return (strong signal but not a delivery confirmation fetch).

NO file modifications made — INVESTIGATION ONLY.

---
Task ID: 1-B
Agent: monitoring-linkfilter-investigator
Task: Investigation against user Requirements #2 (monitoring coverage) + #3 (link-type filtering) — INVESTIGATION ONLY

Work Log:
- Read worklog.md (821 lines) for project history: prior work on SourceRegistry
  (select_due_chats with `(next_poll_at IS NULL OR next_poll_at <= ?)`), monitored_chats
  table schema (should_monitor DEFAULT 1), watcher accounts (4 connected +967735451189
  +967770309310 +967739407274 +967737990530; +967735272360 broken), iter_dialogs
  used for discovery.
- Surveyed code: bot.py (10222 lines), link_system.py (1618 lines),
  source_registry.py (643 lines), render.yaml.
- Grep-verified all key markers: iter_dialogs, iter_messages, HistoryScanner,
  STARTUP_SCAN_DAYS, _sync_monitored_chats, _periodic_sync, _run_startup_scan,
  _chat_classifier, should_monitor, get_active_watchers, LinkNormalizer,
  GulfFilter, is_blacklisted, is_channel, is_megagroup, broadcast, megagroup,
  chat.whatsapp.com, wa.me, JoinChannelRequest, GetParticipantRequest.
- Confirmed via grep that `_run_startup_scan` (defined bot.py:7057) and
  `_chat_classifier` (defined bot.py:7809) are NEVER invoked via
  `asyncio.create_task(...)` anywhere in start() or other paths — both are
  dead code (only `_start_scan_all`/`_run_scan_for_watcher` for manual /scan_*
  commands are wired up).
- Confirmed via grep that `extract_whatsapp_telegram_links` (bot.py:187) and
  `is_target_university_message`/`TARGET_UNIVERSITIES` (bot.py:317/250) are
  legacy code, NOT called by the live pipeline (only mentioned in a comment
  at bot.py:2430/2441). The live pipeline uses `LinkNormalizer.extract_links`
  (link_system.py:48) for extraction and `GulfFilter.is_blacklisted`
  (bot.py:1659) for filtering.
- Verified `get_active_watchers()` (bot.py:1429) returns ALL Supabase
  `is_active=eq.true` rows (no role/connected filter); used by both
  `_sync_monitored_chats` and `start()` for dialog discovery.
- Traced the chat-type detection at three sites: `_on_user_message`
  (bot.py:4454-4459), `_sync_monitored_chats` (bot.py:7763-7768),
  `discover_all_sources_background` (source_registry.py:280-285) — all use
  `dialog.entity.broadcast` (or `chat_obj.broadcast`/`megagroup`) to label
  `link_type='channel'` vs `'group'`. Channels are REGISTERED, NOT excluded.
- Traced the channel-exclusion at JOIN time only: `_join_group_safe`
  (bot.py:8805-8821) resolves entity via `get_entity(username)` and returns
  "IS_CHANNEL" if `entity.broadcast` is True and `megagroup`/`gigagroup` are
  False; the scheduler (bot.py:7539-7544) marks such links BANNED+DONE.
- Verified the WhatsApp regex in `LinkNormalizer.WA_PATTERNS`
  (link_system.py:57-59) captures ONLY `chat.whatsapp.com/<code>` — it does
  NOT capture `wa.me/<phone>` (acceptable: those are 1-on-1 chat deep links,
  not group invites) nor `whatsapp.com/channel/<code>` (acceptable: those are
  WhatsApp broadcast channels, equivalent to Telegram channels).
- Verified Telegram regex `LinkNormalizer.TG_PATTERNS` (link_system.py:52-55)
  captures any `t.me/<identifier>` — it does NOT distinguish personal users,
  bots, groups, channels, or megagroups. False-positive risks (user/bot
  profiles, t.me/share, t.me/addstickers) ARE enqueued+published; they fail
  later at join time (JoinChannelRequest on a User raises → "FAILED").
- Cross-checked the deploy_check counts (bot.py:9901-9914): the production
  user task description's "monitors_count:3, joiners_count:3" = 6 active
  Supabase rows; the "4 user clients total" wording is inconsistent with
  3+3=6 — the 4 number refers to the *currently connected* user_clients,
  not the configured total.
- No code files modified. No tests run (investigation-only scope).

Stage Summary:

==============================================================
REQUIREMENT #2 — MONITORING COVERAGE
==============================================================

#2.1 — Discover ALL dialogs accessible to each watcher (iter_dialogs/get_dialogs)
  Status: ✅ IMPLEMENTED
  Evidence (3 sites, all use iter_dialogs on ALL connected watchers):
  - bot.py:8916  `asyncio.create_task(self._sync_monitored_chats())` — startup, after 15s settle.
  - bot.py:8918  `asyncio.create_task(self._periodic_sync())` — hourly recurrence.
  - bot.py:7734  `watchers = await self.db.get_active_watchers()` (returns ALL active Supabase rows — not filtered by role/connected).
  - bot.py:7735  comment: `# كل الحسابات (مراقبين + فدائيين) — الفدائيين عندهم مجموعات بعد` ("all accounts — monitors+joiners; joiners have groups too").
  - bot.py:7749  `async for dialog in client.iter_dialogs():` — iterates ALL accessible dialogs of EACH connected client.
  - source_registry.py:259-265 — `discover_all_sources_background` (one-shot at startup, bot.py:8927) also iter_dialogs on every connected user_client.
  Periodicity: ✅ HOURLY via `_periodic_sync` (bot.py:7795 `await asyncio.sleep(3600)` → calls `_sync_monitored_chats()`).
  All connected watcher AND joiner accounts: ✅ INCLUDED (loop iterates `get_active_watchers()` result, which is role-agnostic).
  Gap: ⚠️ Only CONNECTED clients are iterated (bot.py:7742-7744 skips `not client.is_connected()`). A temporarily-offline account's dialogs are NOT scanned that cycle; its previously-discovered `reader_phones` are preserved by the merge logic at source_registry.py:302-322 (offline accounts not removed).

#2.2 — Register newly-discovered suitable groups into monitored_chats automatically
  Status: ✅ IMPLEMENTED (registration); ⚠️ PARTIAL (suitability filter)
  Evidence:
  - bot.py:7770  `is_new = await self.prod_db.add_monitored_chat(chat_id=..., chat_title=..., username=..., link_type=..., monitored_by=phone)` — INSERT OR IGNORE on chat_id (UNIQUE).
  - link_system.py:776-792  `monitored_chats` schema: `should_monitor INTEGER DEFAULT 1` — every newly-registered chat is polled by default.
  - source_registry.py:534  PollingScheduler.select_due_chats: `WHERE should_monitor = 1 AND (next_poll_at IS NULL OR next_poll_at <= ?)` — polls ALL newly-registered chats immediately.
  Gap (suitability): ⚠️ The AI classifier `_chat_classifier` (bot.py:7809-7886) which would set `should_monitor=0` for non-educational chats IS DEFINED BUT NEVER STARTED — `rg -n "_chat_classifier\(" bot.py` returns ONLY the definition line, no `asyncio.create_task(self._chat_classifier())` call. So the should_monitor column stays at default 1 forever; the AI never prunes non-educational chats. The only filtering at chat level is the per-link blacklist (GulfFilter.is_blacklisted) applied at message-extraction time.

#2.3 — Proactively scan RECENT message history of monitored chats (iter_messages with limit/backfill)
  Status: ⚠️ PARTIAL — NO automatic periodic backfill; manual-only history scan.
  Evidence:
  - bot.py:2320-2633  `HistoryScanner` class: uses `iter_messages(dialog, reverse=False, limit=self.max_per_chat)` (bot.py:2407; default 500 from `HISTORY_MAX_PER_CHAT` env, bot.py:783) → DOES scan last 500 messages of every dialog with a `days_back` cutoff. Unified on `LinkNormalizer.extract_links` (bot.py:2431) + `GulfFilter.is_blacklisted` (bot.py:2452) + `MessageClaim` (bot.py:2420-2426) per worklog "HistoryScanner unified" refactor.
  - bot.py:760  `SCAN_COMMANDS = {"/scan_week": 7, "/scan_month": 30, "/scan_60": 60, "/scan_90": 90, "/scan_full": None}` — manual operator commands only.
  - bot.py:5921-5923  `elif cmd in SCAN_COMMANDS: days = SCAN_COMMANDS[cmd]; await self._start_scan_all(days, cmd)` — manual trigger via /scan_*.
  - bot.py:6892-6911  `_start_scan_all` filters `role == 'monitor'` only (line 6899) and creates `asyncio.create_task(self._run_scan_for_watcher(...))` per watcher. This is the ONLY way HistoryScanner runs.
  Gap (CRITICAL): bot.py:7057 `async def _run_startup_scan(self, watcher):` IS DEFINED BUT NEVER CALLED. `rg -n "_run_startup_scan" bot.py` returns only the definition line; there is NO `asyncio.create_task(self._run_startup_scan(...))` in `start()` or anywhere else. The `_startup_scan_done: Set[str]` set initialized at bot.py:2667 is never read. So even if STARTUP_SCAN_DAYS env var is set (bot.py:786-792 reads it; bot.py:10094-10095 only logs it), NO startup history scan fires automatically.
  Gap (PollingScheduler is delta-only): source_registry.py:554-633 `PollingScheduler.run()` calls `_poll_one_chat` → bot.py:5026 `messages = await client.get_messages(chat_id, limit=3, min_id=last_msg_id)` — this only fetches NEW messages since the last high-water mark; it does NOT backfill older messages. The 3-message limit × 25-chats-batch × 4-parallel (source_registry.py:493/498) is delta-polling, not history scanning.
  Gap (Reconcile is targeted, not periodic): bot.py:3582-3690 `_reconcile_chat_after_delete_miss` pulls `limit=15` (bot.py:3603) — but ONLY triggered by a MessageDeleted event for a message the bot never received (DELETE-MISS path). It's reactive forensics, not proactive discovery.
  Gap (Journal recovery is crash-rescue, not discovery): bot.py:3692-3710 `_journal_recovery` reprocesses pending journal rows older than 120s — only recovers messages the bot already saw but never finished processing. Does NOT discover new links from unprocessed historical messages.
  Net: the ONLY automatic capture paths are (a) NewMessage events and (b) PollingScheduler delta-polling. To find groups mentioned in OLDER messages of monitored chats (e.g. a link posted 3 days ago that the bot missed), the operator MUST run `/scan_week` manually. There is no cron / periodic background equivalent.

#2.4 — New group joined by a watcher AFTER initial dialog scan gets picked up
  Status: ✅ IMPLEMENTED (1-hour discovery latency)
  Evidence:
  - bot.py:7792-7807  `_periodic_sync` runs `await asyncio.sleep(3600)` then calls `_sync_monitored_chats()` which re-iterates `iter_dialogs` for every connected watcher (bot.py:7739-7749). A newly-joined group appears as a new dialog → `add_monitored_chat` (bot.py:7770) inserts it via INSERT OR IGNORE → should_monitor defaults to 1.
  - bot.py:7802-7805  After sync, `await self.source_registry.load_from_db()` refreshes the in-memory registry so PollingScheduler sees the new chat within the same cycle (B04 fix per worklog).
  Latency: up to 1 hour from join to first poll. Operator can force immediate discovery via /scan_* commands.

#2.5 — Are all 4 (per task description) / 6 (per monitors_count 3 + joiners_count 3) connected user clients used for monitoring?
  Status: ✅ ALL active accounts used for dialog discovery; ⚠️ NewMessage listening is MONITORS-ONLY.
  Evidence:
  - bot.py:8903-8910  `start()` calls `get_active_watchers()` and creates `asyncio.create_task(self._run_user_client(w))` for EVERY active Supabase watcher (monitors AND joiners — no role filter on client creation).
  - bot.py:7734-7749  `_sync_monitored_chats` iterates `get_active_watchers()` (ALL accounts, monitor+joiner+backup) and calls `iter_dialogs` on each connected client — so ALL accounts' groups are discovered.
  - source_registry.py:259-265  `discover_all_sources_background` iterates `user_clients.items()` (all accounts) for `iter_dialogs`.
  - source_registry.py:373-418  `get_reader(chat_id)` PREFERs monitors, FALLs BACK to joiners (line 404-416) for polling readers — so joiners' dialogs are READABLE for polling too.
  - bot.py:6997-6999  `if role == 'monitor': self._register_user_handlers(phone)` — ONLY MONITORS register NewMessage+MessageDeleted handlers. Joiners do NOT listen to live messages. (Worklog line 7006-7010 confirms: `role=joiner → handlers=none (joiner only)`.)
  Discrepancy with task description: production `/api/deploy_check` reports `monitors_count: 3` and `joiners_count: 3` (bot.py:9901-9914 counts role!=`joiner` as monitor, role==`joiner` as joiner — so 3+3=6 active Supabase rows). The task's "4 user clients total" wording matches the CONNECTED subset seen in `report["telegram"]["user_clients"]` dict (bot.py:9891-9898 — only currently-instantiated clients, e.g. 4 of 6 connected). Net: 6 accounts configured in Supabase; 4 currently connected; ALL 6 (and 4 connected) are used for dialog discovery. Only monitors (out of the 6) listen to NewMessage events; joiners serve as fallback polling readers + group joiners.

==============================================================
REQUIREMENT #3 — LINK-TYPE FILTERING
==============================================================

#3.1 — Distinguish Telegram groups vs channels (channels EXCLUDED)
  Status: ⚠️ PARTIAL — channel exclusion happens at JOIN time only, NOT at extraction/registration time.
  Evidence:
  - Regex captures ANY `t.me/<identifier>`: link_system.py:52-55 `TG_PATTERNS = [re.compile(r'(?:https?://)?t\.me/(\+[\w]+|joinchat/[\w]+|[\w]+)(?:/(\d+))?', re.I), re.compile(r'(?:https?://)?telegram\.me/(\+[\w]+|joinchat/[\w]+|[\w]+)(?:/(\d+))?', re.I)]`. link_type is set to `"telegram"` (line 103) for username-based links or `"telegram_private"` (line 98) for `+hash`/`joinchat/hash` — the regex does NOT pre-filter channels.
  - Channel LABEL is stored at dialog-discovery time (not excluded):
    - bot.py:7763-7768  `_sync_monitored_chats`: `if hasattr(dialog.entity, 'broadcast') and dialog.entity.broadcast: link_type = 'channel'`.
    - source_registry.py:280-285  same logic in `discover_all_sources_background`.
    - bot.py:4454-4459  same logic in `_on_user_message` for the SOURCE chat (where the link was posted).
  - Channels are MONITORED (polled) but NOT JOINED:
    - link_system.py:790  `should_monitor INTEGER DEFAULT 1` — every chat including channels is polled by PollingScheduler (source_registry.py:534 `WHERE should_monitor = 1`).
    - bot.py:8805-8821  `_join_group_safe` resolves `entity = await client.get_entity(username)` then checks: `entity.broadcast=True → is_channel=True`; `entity.megagroup=True → is_channel=False`; `entity.gigagroup=True → is_channel=False`; (not megagroup AND not broadcast) → not channel. If `is_channel`: returns `False, "IS_CHANNEL", None`.
    - bot.py:7539-7544  Scheduler: on "IS_CHANNEL" status → `state_to_set=BANNED`, `state_error='is_channel'`, `final_status='DONE'` (terminal — no retry). Logs `📢 Skipped channel (broadcast)`.
  - HistoryScanner CAN skip channel posts if `HISTORY_SKIP_CHANNEL_POSTS=true` (bot.py:2379-2382 `if d.is_channel: continue`), but render.yaml has NO `HISTORY_SKIP_CHANNEL_POSTS` env var set — defaults to `false` (bot.py:785). So even the manual scan path includes channels.
  Gap: channel LINKS (e.g. `t.me/SomeBroadcastChannel`) ARE captured, enqueued, and PUBLISHED to the operator's channel (PIPELINE-5, bot.py:7187-7237) — the system only excludes them at join time. If the user wants channels excluded from the PUBLISHED feed too, that filter doesn't exist; the operator must run `/cleanup_preview` (bot.py:8364 — `EducationalFilter.is_educational('', username)`) to retroactively delete non-educational messages from the channel.

#3.2 — WhatsApp group invites vs WhatsApp broadcast/communities
  Status: ✅ IMPLEMENTED (group invites captured); ⚠️ NO community-vs-group distinction.
  Evidence:
  - link_system.py:57-59  `WA_PATTERNS = [re.compile(r'(?:https?://)?chat\.whatsapp\.com/([\w]+)', re.I)]` — captures ONLY `chat.whatsapp.com/<inviteCode>`.
  - link_system.py:122-139  `extract_links` returns link_type=`'whatsapp'`, normalized=`'wa:invite:<code>'`.
  - bot.py:7635-7638  `_priority_scorer`: `if link_type == 'whatsapp' or 'chat.whatsapp.com' in raw_link: await self.prod_db.update_link_priority(link_id, 0); continue` — WhatsApp links are NOT joined (no member_count fetchable via Telegram), priority stays LOW/REJECT but they ARE enqueued + published.
  - bot.py:8871-8873  `_join_group_safe`: `else: return False, "SKIP", None` — WhatsApp links return "SKIP" status (no join attempted).
  - bot.py:7535-7537  Scheduler on "SKIP": `final_status='DONE'` (terminal) — WhatsApp links are publish-only, never joined.
  Gap: WhatsApp COMMUNITIES (which also use `chat.whatsapp.com/<code>` URL format) are captured as if they were group invites — there is NO way to distinguish community-vs-group from URL alone without a WhatsApp API call (which the system doesn't make). WhatsApp BROADCAST LISTS don't have public URLs so they're naturally not captured. `wa.me/<phone>` (1-on-1 chat deep links) are NOT captured — this is correct behavior (they're not group invites) but the legacy `WHATSAPP_LINK_PATTERN` (bot.py:73-87) DID include `wa.me` and `whatsapp.com/channel`; that stricter regex is in dead code (`extract_whatsapp_telegram_links`, bot.py:187, never called by the live pipeline per grep).

#3.3 — Gulf-student focus filter (Saudi/UAE/Kuwait/Qatar/Bahrain/Oman student groups; university names)
  Status: ⚠️ PARTIAL — Gulf whitelist + non-Gulf blacklist exist in GulfFilter, BUT the live pipeline only applies the BLACKLIST at message time; the full `should_join` filter (with whitelist priority) runs only at JOIN time AND has a fallback-accept that lets non-Gulf non-academic content through.
  Evidence:
  - bot.py:1659-2101  `class GulfFilter` — full filter logic.
  - bot.py:1791-1827  `GULF_WHITELIST` — covers KSA (الملك سعود/ksu, الملك عبدالعزيز/kau, الملك فيصل/kfu, الملك خالد/kku, الملك فهد/kfupm, الملك عبدالله/kaust, أم القرى/uqu, الطائف/taibahu/taif, القصيم/qassim, الإمام/imamu, تبوك, prince sattam/psau, الإمام عبدالرحمن/iau, جدة/uj, دار الحكمة, اليمامة, ابن رشد, pnu/norah, seu, majmaah, shaqra), Kuwait (الكويت/kuwait, ku, AUM, AUK, GUST, PAAET, الكندي), Qatar (قطر/qatar, qu, Carnegie, Georgetown, HBKU), Bahrain (البحرين/bahrain, Ahlia, AMA, UoB, المنامة), UAE (الإمارات/UAE, Khalifa, Zayed, Sharjah, UAEU, UOS, AUS, NYUAD, دبي, أبوظبي, الشارقة, etc.).
  - bot.py:1713-1737  `BLACKLIST_NON_GULF_COUNTRIES` — Egypt, Jordan, Syria, Lebanon, Sudan, Yemen, Morocco, Algeria, Tunisia, Libya, Palestine (all blacklisted).
  - bot.py:1699-1711  `BLACKLIST_IRAQI_UNIS` — Iraqi cities/universities blacklisted.
  - bot.py:1679-1692  `BLACKLIST_CRYPTO_INVEST`, 1694-1697 `BLACKLIST_GAMBLING`, 1739-1742 `BLACKLIST_ADULT`, 1744-1766 `BLACKLIST_SOCIAL`, 1768-1775 `BLACKLIST_SHOPS` — comprehensive content blacklists.
  - bot.py:1953-1968  `is_blacklisted` — checks combined text+link_username+link+source_group_name against HARD_BLACKLIST (substring match).
  - bot.py:1970-1978  `is_gulf_target` — checks against GULF_WHITELIST (substring match).
  - bot.py:2055-2098  `should_join` — full filter with whitelist priority. CRITICAL line 2097-2098: `# 6. احتياطي → قبول (البوت يراقب مجموعات تعليمية)` / `return True, f'fallback_accept_{edu_reason}'` — FALLBACK ACCEPT means everything not in HARD_BLACKLIST is accepted by default (even if not Gulf and not academic).
  - Live application sites (3 paths, all use is_blacklisted only — NOT should_join):
    - bot.py:4605-4613  `_on_user_message` (NewMessage handler): `is_bad, bad_reason = GulfFilter.is_blacklisted(...)` — BLACKLIST ONLY.
    - bot.py:5158-5166  `_poll_one_chat` (delta-polling): same `GulfFilter.is_blacklisted(...)` — BLACKLIST ONLY.
    - bot.py:2452-2466  HistoryScanner._scan_chat: same `GulfFilter.is_blacklisted(...)` — BLACKLIST ONLY.
  - bot.py:7262-7285  Scheduler: calls the FULL `EducationalFilter.should_join(...)` (note: EducationalFilter is an alias for GulfFilter, bot.py:2101) — applies whitelist priority + fallback-accept. So a NON-Gulf NON-blacklisted NON-academic link (e.g. an Omani student group — Oman is not in GULF_WHITELIST, not in BLACKLIST_NON_GULF_COUNTRIES either) reaches the `fallback_accept` at line 2098 and gets joined.
  Gap (Oman coverage): Oman / Sultan Qaboos University is NOT in GULF_WHITELIST (bot.py:1791-1827) — neither is it in BLACKLIST_NON_GULF_COUNTRIES. So Omani content falls through to `fallback_accept`. If the user wants strict Gulf-only filtering, Oman must be added to GULF_WHITELIST, OR the fallback line 2098 must be flipped from `return True` to `return False`.
  Gap (university name coverage check): the user asked about KAU, KSU, KKU, KFUPM, King Saud, Imam, Qassim, Taif, Tabuk, Bahrain, UAE, Kuwait Univ, Qatar Univ, Sultan Qaboos — verification:
    - KAU ✅ (bot.py:1794 `kau`)
    - KSU ✅ (bot.py:1794 `ksu`)
    - KKU ✅ (bot.py:1795 `kku`)
    - KFUPM ✅ (bot.py:1795 `kfupm`)
    - King Saud ✅ (bot.py:1794 `الملك سعود`)
    - Imam ✅ (bot.py:1801 `الإمام`, `imamu`)
    - Qassim ✅ (bot.py:1800 `القصيم`, `qassim`)
    - Taif ✅ (bot.py:1797 `الطائف`, `taibahu`; bot.py:1811 `taif`, `طيبة`)
    - Tabuk ✅ (bot.py:1799 `تبوك`)
    - Bahrain ✅ (bot.py:1821 `البحرين`, `bahrain`)
    - UAE ✅ (bot.py:1824 `الإمارات`, `UAE`)
    - Kuwait Univ ✅ (bot.py:1813 `الكويت`, `kuwait`)
    - Qatar Univ ✅ (bot.py:1817 `قطر`, `qatar`, `qu`, `qatar university`)
    - Sultan Qaboos ❌ NOT IN WHITELIST (Oman absent from both lists)
  - AI-based filtering (separate from GulfFilter): `AIAnalyzer` (bot.py:2666, instantiated at start) is invoked ONLY in the AI drainer (bot.py:9027, gated on `AI_DRAIN_ENABLED=false` per render.yaml:51) and on the live hot-path if `AI_BATCH_MODE=false` (render.yaml:38-39 sets it to `true` → AI skipped on hot path). So in current production config, AI does NOT filter links on the live path; the GulfFilter blacklist-only is the SOLE filter applied at message time.

#3.4 — Link regexes used; false-positive risks
  Status: ⚠️ PARTIAL — false-positive risks for `t.me/<username>` profile/bot/share links.
  Evidence (live pipeline uses LinkNormalizer regexes only):
  - link_system.py:52-55  `TG_PATTERNS`:
      `re.compile(r'(?:https?://)?t\.me/(\+[\w]+|joinchat/[\w]+|[\w]+)(?:/(\d+))?', re.I)`
      `re.compile(r'(?:https?://)?telegram\.me/(\+[\w]+|joinchat/[\w]+|[\w]+)(?:/(\d+))?', re.I)`
  - link_system.py:57-59  `WA_PATTERNS`:
      `re.compile(r'(?:https?://)?chat\.whatsapp\.com/([\w]+)', re.I)`
  - link_system.py:62-141  `extract_links` returns list of `{raw, normalized, link_type, username, invite_hash, msg_id}` dicts.
    - For `+hash`/`joinchat/hash` → `link_type='telegram_private'` (line 95-99).
    - For plain `username` → `link_type='telegram'` (line 100-104) — DOES NOT distinguish user/bot/group/channel.
    - For `chat.whatsapp.com/<code>` → `link_type='whatsapp'` (line 132-139).
    - Message-link `/123` suffix is stripped (line 85-92) so `t.me/SomeChannel/123` becomes `t.me/SomeChannel`.
  - Legacy regexes (DEAD CODE — never called by live pipeline, but show stricter filtering was historically considered):
    - bot.py:73-87  `WHATSAPP_LINK_PATTERN` includes `chat.whatsapp.com | whatsapp.com/channel | whatsapp.com/contact | wa.me | api.whatsapp.com | l.whatsapp.com`.
    - bot.py:89-99  `TELEGRAM_LINK_PATTERN` matches `t\.me | telegram\.me /...`.
    - bot.py:187-244  `extract_whatsapp_telegram_links` (DEAD) excludes `wa.me`, `?start=`, `?text=`, `t.me/username/123`, AND usernames inside `[@username](url)` Markdown AND links mentioned after "المرسل" / "ID المرسل" / "👤" markers (line 222-239). None of these safeguards are in `LinkNormalizer.extract_links`.
  False-positive risks (verified by tracing the live pipeline):
    - `t.me/<personal_user>` → captured (link_type='telegram'), enqueued, published to channel, then FAILS at join time (JoinChannelRequest on a User entity raises → "FAILED" status, bot.py:8867-8869).
    - `t.me/<bot_username>` → same fate (Bot entity has no `broadcast`/`megagroup`, `is_channel` stays False, JoinChannelRequest raises).
    - `t.me/share/url?url=...` → captures `share` as username → enqueued+published → fails at join.
    - `t.me/addstickers/<set>` → captures `addstickers` as username → same.
    - `t.me/proxy?server=...` → captures `proxy` as username → same.
    - `t.me/SomeChannel` (broadcast channel) → captured+published, REJECTED at join with "IS_CHANNEL" (bot.py:8819-8821, 7539-7544 → BANNED+DONE).
  Mitigations present:
    - bot.py:7660-7671  `_priority_scorer` calls `get_entity(username)`. If entity is a User (no `title`, no `participants_count`, not isinstance(Channel, Chat)), `member_count` stays 0 → priority stays LOW/REJECT (label only — link stays in queue).
    - link_system.py:492-502  `MembershipCache._api_check` distinguishes User (first_name + no megagroup + no broadcast + no gigagroup) → returns None (not cached, treated as "don't know") so the scheduler skips membership check and proceeds to joiner selection where the link ultimately fails.
  Net: false-positive links (user/bot/share/proxy) DO get enqueued and PUBLISHED to the operator's channel — they waste an API call on `get_entity` + a failed `JoinChannelRequest` before being marked FAILED. The user's dashboard would show these as published messages with no corresponding JOIN. The legacy `extract_whatsapp_telegram_links` (dead code) had smarter filtering — if the user wants to suppress these false positives at extraction time, that logic should be ported into `LinkNormalizer.extract_links`.

==============================================================
CRITICAL GAPS SUMMARY (priority-ordered for next pass)
==============================================================
1. ⚠️ HIGH — No automatic periodic history scan. `_run_startup_scan` (bot.py:7057) and `_chat_classifier` (bot.py:7809) are dead code (defined, never wired into start()). STARTUP_SCAN_DAYS env var has no effect beyond a log line at bot.py:10094. HistoryScanner runs ONLY via manual /scan_week /scan_month /scan_60 /scan_90 /scan_full commands. → Recommend: wire `_run_startup_scan` into `start()` (conditional on `config.startup_scan_days is not None`) and/or add a periodic background scan (e.g. daily `/scan_week` equivalent).
2. ⚠️ HIGH — PollingScheduler delta-only. `_poll_one_chat` (bot.py:5026) uses `get_messages(limit=3, min_id=last_msg_id)` — never backfills. Missed messages older than the high-water mark are never discovered via polling. → Mitigated by journal_recovery (crash-rescue) + reconcile (delete-miss forensics), but NEITHER discovers links in messages the bot never received at all.
3. ⚠️ MEDIUM — LinkNormalizer.extract_links (link_system.py:52-55) captures any `t.me/<username>` without distinguishing user/bot/group/channel. False-positive user/bot/share/proxy links ARE enqueued + PUBLISHED before failing at join time. → Recommend: port the smarter exclusion logic from the legacy `extract_whatsapp_telegram_links` (bot.py:222-239) into LinkNormalizer, OR add a pre-join entity-type check (`get_entity → if hasattr(first_name) and no broadcast/megagroup → mark BANNED at scorer time`).
4. ⚠️ MEDIUM — GulfFilter.should_join has a `fallback_accept` (bot.py:2097-2098) that lets non-Gulf non-blacklisted non-academic content through. Combined with OMAN not being in GULF_WHITELIST (bot.py:1791-1827), Omani student groups are NOT explicitly preferred. → Recommend: either add Oman + Sultan Qaboos to GULF_WHITELIST, OR flip the fallback to `return False` for stricter Gulf-only filtering.
5. ⚠️ LOW — `_chat_classifier` (bot.py:7809) AI-based chat pruning is dead code. `should_monitor` stays at default 1 for all chats forever, so PollingScheduler polls EVERY discovered chat including non-educational ones. → Recommend: wire `_chat_classifier` into start() if AI-based chat pruning is desired, OR accept the current behavior (poll-everything, filter at link-extraction time via blacklist).
6. ℹ️ INFO — Channels are MONITORED (polled + their links captured+published) but NOT JOINED. If the user wants channels EXCLUDED from the published feed too, a `t.me/<channel>` pre-filter at extraction time would be needed (currently relies on post-hoc /cleanup_preview).

---
Task ID: 1-A
Agent: security-investigator
Task: Security investigation against user Requirement #1 (INVESTIGATION ONLY, no code changes)

Work Log:
- Read worklog.md fully (821 lines). Confirmed prior security work: Task 5a (API auth, line 733) and Task 11a (secrets scan, line 734) both done by main agent; documented B23 open external action (3 secrets in git history need rotation + filter-repo purge).
- Verified git state: HEAD=044defc, origin/main in sync per worklog DEPLOY-VERIFY-1.
- Sub-item 1 (Secret/Token handling): grep across *.py for BOT_TOKEN/API_ID/API_HASH/SUPABASE_KEY/OPENAI_API_KEY/StringSession; read Config class (bot.py:768-811) + DatabaseManager (bot.py:837-859) + _create_user_client (bot.py:2761-2774) + login flow (bot.py:5630-5718). Verified all secrets via os.getenv; StringSession built from in-memory session_string param (loaded from Supabase), never hardcoded. Confirmed download/create_env_v6.py at HEAD = placeholders only.
- Sub-item 2 (Log redaction): grep for _redact + logging.*(.*phone|api_hash|bot_token|session_string|supabase_key). Found _redact_phone at bot.py:9976 used ONLY at bot.py:9896 (deploy_check). 50+ logging.* statements emit RAW phones (bot.py:1067,1083,3318,5209,5736,6907,7435,8755,...). bot_token logged as [:8]...[-4:] at bot.py:10090. No full api_hash/supabase_key/session_string logged anywhere (good).
- Sub-item 3 (API auth): read dashboard_api_key_middleware (bot.py:10007-10039), start_http_server (bot.py:10042-10071), deploy_check (bot.py:9776-9944), ready/metrics/health (bot.py:9123-9773), joiners_status (bot.py:9191-9294). Confirmed secrets.compare_digest (Task 5a/A1), only add_get routes (no POST/PUT/DELETE), /health+/ready+/metrics exempt. Found DASHBOARD_API_KEY is OPTIONAL (default OPEN) — joiners_status/joined_groups/monitored_chats/links return RAW phones when key unset (render.yaml:88-92 confirms default empty). Found Caddyfile:1-13 SSRF: ?XTransformPort=* → reverse_proxy localhost:{query.XTransformPort}.
- Sub-item 4 (Git secret leaks): git log --all -S 'BOT_TOKEN' -- download/ → 3 commits (68dbb53,66779fe,52427b0). git cat-file -t confirms all 3 still reachable. git show 68dbb53:download/create_env_v6.py → REAL secrets: API_ID=<api_id_REDACTED>, API_HASH=<api_hash_REDACTED>, BOT_TOKEN=<bot_token_REDACTED>, PHONE=+967770309310. Working tree scan: 0 hardcoded ghp_/sk-/eyJ-JWT/phone-as-secret patterns. Source-file regression guard at tests/test_audit_regressions.py:2820-2857 covers 8 secret patterns. Matches worklog Task 11a exactly; B23 purge NOT done (worklog line 745).
- Sub-item 5 (Session file security): .gitignore lines 65-66 exclude *.session, *_session_string*, .secrets/, accounts.env, .env*. git ls-files confirms 0 .session and 0 accounts.env tracked. SESSIONS_DIR="sessions" (bot.py:60) → ./sessions/bot.session, NOT under public/ or web-served. HTTP server (bot.py:10054-10065) has only add_get routes — no add_static/FileResponse/file serving. public/ contains only logo.svg+robots.txt (Next.js assets, not bot-served). StringSession for watchers loaded from Supabase (in-memory).
- Sub-item 6 (Command injection / unsafe eval): grep across *.py for os.system/subprocess/shell=True/eval/exec/pickle.loads/yaml.load. ZERO matches in bot.py, link_system.py, source_registry.py, live_audit.py. Only exec() calls are in scripts/test_requeue.py:37, scripts/test_filter.py:28, scripts/test_requeue_direct.py:256 (dev-only test harnesses, not invoked at runtime). live_audit.py:392 uses __import__('time') — safe.
- Sub-item 7 (Path traversal): grep for request.query/open/Path/FileResponse/add_static. All request.query.get calls (bot.py:9370-9372, 9529-9545) take SQL-filter values (message_link/chat_id/group_name/limit/offset/ai_approved), never file paths. All Path()/open() in bot.py use static constants (SESSIONS_DIR/DATA_DIR/LOGS_DIR/LOG_FILE). No FileResponse, no add_static, no open(request.X) anywhere.

Stage Summary:

- Sub-item 1 — Secret/Token handling: ✅ IMPLEMENTED
  * bot.py:771-773  `self.api_id = int(os.getenv("API_ID","0")); self.api_hash = os.getenv("API_HASH",""); self.bot_token = os.getenv("BOT_TOKEN","")`
  * bot.py:843-844  `self.supabase_url = os.getenv("SUPABASE_URL",""); self.supabase_key = os.getenv("SUPABASE_KEY","")`
  * bot.py:429       `key1 = os.getenv("OPENAI_API_KEY","")`
  * bot.py:2761-2774 `_create_user_client(session_string, phone)` builds `TelegramClient(StringSession(session_string), ...)` — session_string is a parameter, sourced from Supabase `watchers` table at runtime (bot.py:6951 `session_string = watcher['session_string']`), never hardcoded
  * bot.py:2756      bot's own SQLite session at `os.path.join(SESSIONS_DIR,"bot")` (SESSIONS_DIR="sessions", bot.py:60)
  * download/create_env_v6.py at HEAD = placeholders only (`API_ID=YOUR_API_ID_HERE`, etc.)

- Sub-item 2 — Log redaction: ⚠️ PARTIAL
  * bot.py:9976       `def _redact_phone(phone): ... return f"{s[:4]}{'•' * (len(s) - 6)}{s[-2:]}"`
  * bot.py:9896       ONLY use of _redact_phone — in /api/deploy_check. NOT applied to log statements.
  * 50+ logging.* calls emit RAW phone numbers, e.g. bot.py:7435 `logging.info(f"[LINK id={link_id}] [JOINER] selected account={phone}")`, bot.py:8755 `logging.info(f"[JOIN] API request started: IMPORT_INVITE phone={phone} ...")`, bot.py:5736 `logging.info(f"[LOGIN] New {role} registered: {phone} ({display_name})")`
  * bot.py:10090      `logging.info(f"Bot token: {config.bot_token[:8]}...{config.bot_token[-4:]} (loaded, len={len(config.bot_token)})")` — leaks first 8 + last 4 chars (reveals bot's numeric ID + last 4 of secret portion)
  * api_hash, supabase_key, session_string: NEVER logged in full anywhere ✅
  * GAP / RISK: If Render log drain / log aggregation / the previous hack vector re-occurs, all watcher/joiner phone numbers leak in clear text. The `_redact_phone` helper exists but is not wired into the logger. Recommend a `logging.Formatter` that masks `\+\d{7,15}` patterns or wrapping phone vars with `_redact_phone()` at every log call site.

- Sub-item 3 — API/control-panel auth: ⚠️ PARTIAL
  * ✅ bot.py:10031   `ok = bool(provided) and secrets.compare_digest(str(provided), str(key))` (Task 5a/A1 constant-time)
  * ✅ bot.py:10018-10019 /health /ready /metrics explicitly exempt from gating (probes must stay open)
  * ✅ bot.py:10054-10065  ONLY `app.router.add_get(...)` — ZERO `add_post/add_put/add_delete/add_route`. No HTTP mutation endpoints exist; the only control plane is Telegram commands. So no endpoint can be used to send a join, publish, restart, or write data.
  * ✅ bot.py:9829   `masked = val[:4] + "..." + val[-2:] if len(val) > 8 else "***"` — env var masking in /api/deploy_check is complete (no full secret leaks; matches worklog "API_HASH set (1bb7...95)")
  * ✅ bot.py:9896   `report["telegram"]["user_clients"][_redact_phone(phone)] = {...}` — phones masked in /api/deploy_check
  * ⚠️ bot.py:9961   `_get_dashboard_api_key() → os.environ.get("DASHBOARD_API_KEY") or None` — DEFAULT IS OPEN. render.yaml:88-92 confirms `DASHBOARD_API_KEY: sync: false` (operator must set explicitly). When unset (current default), all /api/* endpoints are publicly readable.
  * ⚠️ bot.py:9217, 9240, 9272  /api/joiners_status + /api/joined_groups return RAW phones: `'phone': jphone`, `'joined_by_phone': r[4] or ''`. The deploy_check docstring (bot.py:9790-9797) explicitly acknowledges this and defers to operator-set DASHBOARD_API_KEY. Currently unset → public PII leak (production deploy_check output in worklog line 794 already lists 4 full phones to the internet).
  * ⚠️ bot.py:9834   `report["supabase"]["url"] = db.supabase_url` — leaks the Supabase project URL (minor; identifies the project but is not a credential)
  * ⚠️ Caddyfile:1-13  `@transform_port_query { query XTransformPort=* } handle @transform_port_query { reverse_proxy localhost:{query.XTransformPort} }` — SSRF / arbitrary-localhost-port-scan vector via the frontend Caddy (NOT the bot's HTTP server, but in the same repo). An attacker hitting the Caddy on port 81 with `?XTransformPort=NNNN` gets proxied to any localhost port (postgres 5432, redis 6379, ssh 22, the bot's API 10000, etc.). Recommend restricting to a hardcoded allowlist or removing.

- Sub-item 4 — Git secret leaks: ⚠️ PARTIAL (matches worklog Task 11a / B23 — open external action)
  * ✅ Working tree: 0 hardcoded secrets. `download/create_env_v6.py` at HEAD = `API_ID=YOUR_API_ID_HERE / API_HASH=YOUR_API_HASH_HERE / BOT_TOKEN=YOUR_BOT_TOKEN_HERE`
  * ✅ Working tree scan: 0 `ghp_[A-Za-z0-9]{36}`, 0 `sk-proj-`/`sk-` OpenAI keys, 0 `eyJ<jwt>` literals in *.py/*.ts/*.tsx/*.json/*.yaml
  * ✅ tests/test_audit_regressions.py:2820-2857 `test_11a_no_real_secrets_in_source_files` — regression guard scanning bot.py/link_system.py/source_registry.py/render.yaml against 8 secret patterns (ghp_, github_pat_, sk-proj-, sk-, BOT_TOKEN=, API_HASH=, session_string=, eyJ JWT)
  * ❌ Git HISTORY: commits 68dbb53, 66779fe, 52427b0 STILL REACHABLE (`git cat-file -t` → commit). `git show 68dbb53:download/create_env_v6.py` exposes:
      API_ID=<api_id_REDACTED>
      API_HASH=<api_hash_REDACTED>
      BOT_TOKEN=<bot_token_REDACTED>
      PHONE=+967770309310
  * RISK: Anyone with read access to origin/main can run `git show 68dbb53:download/create_env_v6.py` and retrieve 4 live credentials. The BOT_TOKEN is the bot's actual identity, the API_HASH/API_ID are the userbot's Telegram app credentials (allow impersonation if combined with a session string leak), and the PHONE is the operator's personal number. These are STILL ACTIVE in production (worklog line 794 shows them in /api/deploy_check output, masked but matching the leaked prefix `1bb7...95`). The worklog Task 11a documented this as B23 ("external rotation + filter-repo purge") — NOT yet done (worklog line 745). REQUIRED ACTION: (1) rotate all 3 credentials at Telegram (my.telegram.org + @BotFather), (2) `git filter-repo --replace-text` to purge the 3 commits from history, (3) force-push and re-deploy. Until rotated, the secrets must be considered compromised.

- Sub-item 5 — Session file security: ✅ IMPLEMENTED
  * .gitignore lines 36 + 65-66: `*.session`, `*_session_string*`, `.secrets/`, `accounts.env`, `.env*`, `*.pem`
  * `git ls-files | grep -E '\.(session|env)$'` → 0 matches. `git check-ignore -v accounts.env` → matched at .gitignore:68
  * bot.py:60  `SESSIONS_DIR = "sessions"` → bot's SQLite session at ./sessions/bot.session — NOT under public/ or any web-served path
  * bot.py:10054-10065  HTTP server has ONLY `add_get` routes — no `add_static`, no `FileResponse`, no static file serving. public/ contains only logo.svg + robots.txt (Next.js assets, not served by bot)
  * Watcher/joiner clients use `StringSession(...)` (in-memory, loaded from Supabase watchers table at bot.py:6951) — no per-watcher .session files written

- Sub-item 6 — Command injection / unsafe eval: ✅ IMPLEMENTED (clean)
  * bot.py, link_system.py, source_registry.py, live_audit.py: ZERO `os.system()`, ZERO `subprocess`, ZERO `eval()`, ZERO `exec()`, ZERO `shell=True`, ZERO `pickle.loads`, ZERO `yaml.load`
  * Only `exec()` matches in repo: scripts/test_requeue.py:37, scripts/test_filter.py:28, scripts/test_requeue_direct.py:256 — dev-only test harnesses, NOT imported by bot at runtime
  * live_audit.py:392 uses `__import__('time')` — safe (no user-controlled input)
  * link_system.py:53-58 + bot.py:73,89,329,1641 use `re.compile` — safe regex, not eval

- Sub-item 7 — Path traversal: ✅ NOT APPLICABLE / NO RISK
  * bot.py:9370-9372  `/api/link_source_check` reads `message_link`, `chat_id`, `group_name` — used as SQL filter values for `monitor.prod_db.get_monitored_chats()`, NOT file paths
  * bot.py:9529-9545  `/api/links` reads `limit`, `offset`, `ai_approved`, `ai_is_ad`, `link_type` — SQL filter values, NOT file paths
  * bot.py:816, 818, 1248, 10097-10099  All `Path()/open()` use STATIC constants (LOGS_DIR, LOG_FILE, SESSIONS_DIR, DATA_DIR, DB_FILE)
  * bot.py:10054-10065  No `add_static`/`FileResponse`/`sendfile` — HTTP server serves only JSON from DB queries, never reads files from disk based on request input

Real gaps found:
- GAP-1 (HIGH): 3 live secrets in git history (68dbb53/66779fe/52427b0) — see Sub-item 4. ROTATION + FILTER-REPO PURGE REQUIRED (external operator action; worklog Task 11a/B23 ack'd but not done).
- GAP-2 (MEDIUM): DASHBOARD_API_KEY defaults to OPEN → /api/joiners_status/joined_groups/monitored_chats/links leak full watcher phones to the public internet (production already exposes 4 phones per worklog line 794). Set DASHBOARD_API_KEY in Render env to gate.
- GAP-3 (MEDIUM): 50+ log statements emit raw phone numbers. If logs leak (Render log drain, third-party aggregator, repeat of the previous hack vector), all watcher/joiner phones leak. Wire _redact_phone into the logging Formatter or wrap every phone var at log call sites.
- GAP-4 (LOW): bot_token logged as first 8 + last 4 chars at startup (bot.py:10090). Acceptable for operator confirmation (bot ID is semi-public) but the last 4 of the secret portion IS a minor leak — recommend masking to just `len=N` or `[:4]...`.
- GAP-5 (LOW): SUPABASE_URL returned raw in /api/deploy_check (bot.py:9834). Not a credential, but identifies the Supabase project — recommend masking.
- GAP-6 (LOW): Caddyfile:1-13 `?XTransformPort=*` reverse_proxy to arbitrary localhost port — SSRF/local-port-scan vector in the frontend's Caddy (not the bot's HTTP server). Restrict to a hardcoded allowlist or remove the feature.
- GAP-7 (LOW): Operator's personal phone `+967770309310` hardcoded as the example value in 12+ user-facing bot reply strings (bot.py:5606,5624,3283,5521,5951,5964,5984...). Reveals the operator's number to anyone DMing the bot. Replace with `+<country_code>XXXXXXXXX` placeholder.

---
Task ID: REQAUDIT-1
Agent: main (Super Z) — focused requirements audit + targeted fixes
Task: User demanded a focused code examination against 8 explicit requirements (security, monitoring coverage, link-type filtering, dedup, capture-before-delete, disconnected accounts, JOIN verify, PUBLISH verify). "No random audit or random changes" — fix only REAL problems, test, push, verify production.

Work Log:
- Launched 4 parallel investigation subagents (Task IDs 1-A/B/C/D) against the actual code. Each produced a structured IMPLEMENTED/PARTIAL/MISSING report with file:line evidence (appended to worklog).
- Synthesized findings into a per-requirement gap table. Real gaps identified:
  * Req-1: 50+ log statements emit raw phones; bot_token logged first8+last4; /api/joiners_status + /api/joined_groups leak raw phones when DASHBOARD_API_KEY unset; Supabase URL raw in deploy_check.
  * Req-8: PUBLISH false-success — insert_request (DB) runs BEFORE _send (actual send); on _send failure a PHANTOM row remains in forwarded_requests (DB says published, channel empty) + state=QUEUED blocks retry → link never published yet proceeds to JOIN.
  * Req-2: _run_startup_scan + _chat_classifier are dead code → STARTUP_SCAN_DAYS has no effect → no automatic history scan (only waits for new messages).
  * Req-3: channels (broadcast) are published to feed THEN rejected at join — should be excluded BEFORE publish; false-positive user/bot links also published.
  * Req-4/#5/#6/#7: already STRONG / verified (dedup 6-layer; capture-before-delete triple-source + good reconciliation; disconnected excluded per PUBLISH-INCIDENT-1; JOIN states clear).
- FIX-A (Req-1 logging): added _RedactingFilter (regex redaction of +<phone>, <bot_token>, ghp_<pat>, eyJ<jwt>) installed on BOTH file + stdout handlers in setup_logging — defence-in-depth so a missed call site cannot leak.
- FIX-B (Req-1 API): _api_should_show_full_pii() helper returns False when DASHBOARD_API_KEY unset → /api/joiners_status + /api/joined_groups + banned_groups now mask phones via _redact_phone() in open-dashboard mode (public scrape can no longer enumerate phones). Authenticated callers still see full.
- FIX-B cont.: bot_token startup log reduced to len=N only (was first8...last4). Supabase URL host masked in /api/deploy_check.
- FIX-C (Req-8 PUBLISH): added DatabaseManager.delete_forwarded_request(link) — computes content_hash the same way insert_request does, DELETEs the phantom row. On _send() failure: delete phantom row + reset group_state to DISCOVERED + retry in 2 min → next cycle re-attempts the full publish (state DISCOVERED re-enters publish block; no duplicate row blocking).
- FIX-D (Req-2 monitoring): wired _run_startup_scan into start() — when config.startup_scan_days is not None, schedules a HistoryScanner per connected monitor. Previously dead code. Operator enables via STARTUP_SCAN_DAYS env.
- FIX-E (Req-3 link-type): pre-publish channel/user exclusion at PIPELINE-5 — for public telegram username links, resolve entity (15s timeout, best-effort non-blocking); if broadcast channel or User/Bot, mark BANNED + skip publish+join entirely (channels never reach the published feed). Scorer also marks channels/users BANNED (catches them on the scorer cycle for future scheduler picks).
- Tests: +10 regression guards in tests/test_audit_regressions.py (Req1-1..5, Req8-1..2, Req2-1, Req3-1..2). Behavioral tests use FAKE runtime-constructed credential-shaped strings (no real token committed — the test file is itself scanned by test_11a).
- All 466/466 tests PASS (was 456 → +10). git diff --check clean. 0 real secrets in diff (worklog BOT_TOKEN/API_HASH/API_ID quoted as investigation evidence were redacted to <*_REDACTED> placeholders).
- Commit + push + Render auto-deploy + production verification to follow.

Stage Summary:
- 4 real code fixes (security logging/API masking, PUBLISH phantom-row rollback, startup-scan wiring, pre-publish channel exclusion) + 10 regression tests. Minimal, backward-compatible, no business-logic change to the working pipeline.
- Documented NOT fixed (operator action required): (a) 3 live secrets still in git history (68dbb53/66779fe/52427b0) — needs Telegram rotation + git-filter-repo purge; (b) DASHBOARD_API_KEY unset in production → phones now masked-by-default (operator sets key to see full); (c) STARTUP_SCAN_DAYS unset in render.yaml → startup scan wired but not yet active (operator sets value to enable); (d) GulfFilter fallback_accept + Oman-not-in-whitelist — deferred (flipping fallback to reject risks breaking production; documented as recommendation).
- 1 commit ahead of origin/main (after this). Push + Render verify pending.

---
Task ID: PHASE-0 (Inspection + Baseline)
Agent: main (Super Z) — link capture hardening (capture-before-delete)

Task: User demanded phased implementation of link-capture/protection improvements on the EXISTING wlm codebase (no rewrite). 17 explicit requirements covering: link-only fast capture (LRB), Raw MTProto hook, DELETE-MISS rescue ordering, central dedup, adaptive polling, fleet/account safety, monitoring coverage, student-group filtering, SECURITY (priority), Supabase journal snapshot, do-not-break-working-features, 14 test scenarios, observability, phased PR strategy, git/deploy rules, production-evidence report.

Work Log:
- git: clean working tree on `main` at commit bbc66d4 (FLEET resilience). No uncommitted changes.
- Baseline tests run with .venv-test: test_message_journal 66/66, test_audit_regressions 188/188, test_phase3_contracts 96/96, test_source_registry 103/103, test_deployment_updated 35/35, test_extractor_comparison 3/3 → 491/491 PASS (baseline locked).
- Read actual code (no assumptions): _on_user_message L4879 (pre-cache _msg_cache shared TTL120s + journal + extract + claim + enqueue), _on_message_deleted L5126 (cache→journal→DELETE-MISS→reconcile), _rescue_enqueue_links L3619, enqueue_link L904 (UNIQUE normalized_link = existing central dedup), insert_request L1518 (forwarded_requests dedup for publish), PUBLISH pipeline L7862 (insert_request→_send→phantom-rollback), joiner selection L8034 (iterates ALL joiners, skips unavailable with continue — good pattern already), dashboard security L10849 (DASHBOARD_API_KEY optional, FAIL-OPEN when unset = the production issue), PollingScheduler/SourceRegistry/MessageClaim in source_registry.py (additive), journal methods L1492-1577 (durable WAL).
- Key finding: _msg_cache IS shared across all phones in-process (single Bot instance attribute) → DELETE-MISS truly means NO phone received NewMessage (Telegram deleted before delivery).
- Key finding: .gitignore is Next.js default — does NOT ignore .env / *.session / __pycache__ / *.db. SECURITY GAP for future commits (existing repo already has them gitignored-elsewhere or not committed, but must fix before adding any new local files).
- Key finding: dedup IS central (enqueue_link UNIQUE + insert_request forwarded_requests). is_link_known helper does NOT exist yet — needed for fast LRB-forward pre-check.
- Test harness style confirmed: asyncio.run(main()), record(name,passed,detail), env stub before import, FAKE credentials only.

Stage Summary:
- Baseline locked: 491/491 PASS. Code map complete. No assumptions carried from prior analysis — all verified against live source.
- Ready for PR-1 (Link Ring Buffer + is_link_known dedup + reorder _on_user_message extract-before-metadata).
- SECURITY note for later: .gitignore wrong framework; will fix in PR-7. Dashboard fail-open; will harden in PR-7 (fail-closed-by-default + API_FAIL_OPEN escape hatch, flagged as potential frontend-break to user).

---
Task ID: PR-1 (Link-only fast capture + central dedup)
Agent: main (Super Z)

Task: Add Link Ring Buffer (LRB) + is_link_known central dedup + reorder _on_user_message to extract links BEFORE metadata. Preserve all working features.

Work Log:
- Monitor.__init__: added _link_ring (Dict), _link_ring_lock, _link_ring_ttl=300, _link_ring_cap=20000, _link_ring_evicted/_link_ring_hits counters (bot.py L2807-2818).
- Added _link_ring_put / _link_ring_pop / _link_ring_evict (bot.py L3606-3662): pure-memory, no API, cap-eviction (drops oldest 10% when near cap), defensive try/except so LRB failure never breaks capture.
- Reordered _on_user_message (L4947): NEW Step 0 = extract_links + _link_ring_put + metrics.record_link_capture + [LINK-CAPTURE] log, BEFORE PRE-CACHE metadata + journal_write. Design principle: observability failures (metrics/logging) wrapped in try/except — never break capture path. Reuses `links` var in later steps (no double regex except defensive re-extract).
- Metrics class (link_system.py): added record_link_capture, record_link_ring_hit, record_delete_miss, record_delete_rescued, record_reconcile_rescued, record_link_forwarded + new counters (link_capture_total, link_ring_hits, delete_miss_total, delete_rescued_total, reconcile_rescued_total, link_forwarded_total, floodwait_total, high_risk_chats, tight_poll_active).
- ProductionDB.is_link_known(raw, normalized) (link_system.py L946): central dedup — checks link_queue (any status, via normalized tg:.. form) + forwarded_requests (via MD5 content_hash, reusing db.check_link_exists) + target_groups. Defensive (returns False on error = allows attempt).
- Regression fix: test_message_journal make_fake_monitor mock lacked record_link_capture/_link_ring_* → AttributeError propagated to outer except, skipping journal_write (test R failed). Fixed by (a) wrapping metrics call in try/except in production code (correct design: observability must not break capture), (b) adding new metrics + LRB attrs to test mock.
- New tests/test_link_capture.py (21 tests): TG capture, WA capture, LRB idempotency (re-put no dup), chat_id normalization round-trip (supergroup -100... forms), LRB cap eviction, is_link_known across link_queue + forwarded_requests + empty-args defensive, LinkNormalizer coverage (public + private invite + WhatsApp + msg-link strip).

Stage Summary:
- PR-1 GREEN: 21/21 new tests pass + 491/491 baseline unchanged → 512/512 total.
- LRB now stores normalized links at the earliest possible point (after raw_text, before any metadata). DELETE-MISS rescue (PR-2) can consult LRB next.
- is_link_known gives a single pre-publish dedup gate for the upcoming Link-Only Forward path.
- No business logic touched: JOIN/PUBLISH/Fleet/Scheduler untouched. No secrets in diff.

---
Task ID: PR-2 (Delete-MISS rescue via Link Ring + unified link-only enqueue)
Agent: main (Super Z)

Task: Implement ordered DELETE-MISS rescue (LRB→cache→journal→get_messages→reconcile) + unified link-only enqueue path (no separate forward path → central dedup prevents dup publish).

Work Log:
- Added _normalized_to_link_data(normalized, ...) (bot.py L5220): reconstructs minimal link_data from tg:user:x / tg:invite:h / wa:invite:h — needed because LRB stores only normalized strings, not full metadata.
- Added _rescue_link_only(chat_id, msg_id, source_phone, normalized_links) (bot.py L5266): unified path — reconstruct → is_link_known (central dedup) → add_monitored_chat → enqueue_link → set_group_state DISCOVERED. Logs [LINK-DELETED-RESCUE]. Returns new-link count.
- Modified _on_message_deleted (bot.py L5311+): inserted LRB as rescue source #1 (before cache). On LRB hit: run _rescue_link_only, record metrics (link_ring_hit + delete_rescued('link_ring')), mark journal 'rescued'/'dup_claim', pop cache for cleanup, continue (skip full rescue — link saved, no need for metadata per user's "link > message" principle). On LRB miss → cache (source #2) → journal (source #3) → get_messages best-effort (source #4, NEW) → DELETE-MISS (source #5: honest, no fabrication).
- Added get_messages best-effort block (source #4): tries the phone that saw the delete, else any connected client. Rare hit rate but cheap (Telegram sometimes delays deletion in index 1-2s). Wrapped in try/except.
- _record_delete_miss: added metrics.record_delete_miss() call (defensive) for delete_miss_total KPI.
- Regression fix: test_audit_regressions make_fake_monitor lacked new metrics methods + LRB attrs + _link_ring_* binding → AttributeError in _on_message_deleted skipped all rescue (B09/N04 failed). Fixed mock (added metrics methods, LRB attrs, bound _link_ring_put/pop/evict + _normalized_to_link_data + _rescue_link_only).
- New tests/test_delete_rescue.py (15 tests): #3 cache-rescue-after-delete (no dup, link_ring_hit fires), #4 LRB-only-rescue-on-DELETE-MISS (enqueues, metrics fire, no delete_miss), #5 true-DELETE-MISS (journal delete_miss row + NULL raw_text + no fabrication + metrics), #7 reconcile+NewMessage no-dup (central dedup), #9 restart/recovery no-republish (forwarded_requests dedup).

Stage Summary:
- PR-2 GREEN: 15/15 new + 491+21 baseline = 527/527 total PASS.
- Delete rescue now honors user's ordered priority (LRB first) + "link is more important than message" principle (link-only rescue via unified enqueue, no separate forward path).
- Central dedup (is_link_known + enqueue_link UNIQUE + forwarded_requests content_hash) proven to prevent dup across NewMessage/Delete/reconcile/restart.
- DELETE-MISS is honest: records miss + NULL raw_text, never fabricates a link. get_messages is a best-effort bonus.
- No business logic touched: JOIN/PUBLISH/Fleet/Scheduler untouched.

---
Task ID: PR-3 (Raw MTProto hook + chat_id normalization)
Agent: main (Super Z)

Task: Add supplementary Raw update hook (UpdateNewMessage/UpdateNewChannelMessage) + chat_id normalization. Never replace NewMessage; never break event loop.

Work Log:
- Added _normalize_raw_chat_id(peer_id) staticmethod (bot.py L3464): PeerChannel → -100{cid}, PeerChat → -{hid}, PeerUser → uid, None/unknown → None. Verified formula matches Telethon event.chat_id (-100 prefix for supergroups/channels).
- Added _on_raw_new_message(update, source_phone) (bot.py L3491): extracts text + msg_id + peer_id from raw MTProto update, normalizes chat_id, extracts links (regex), writes LRB ONLY (no enqueue, no metrics — supplementary layer; NewMessage does full processing + metrics). Entire body in try/except that swallows ALL exceptions (Raw hook must never break event loop).
- Modified _register_user_handlers (bot.py L3426): registers events.Raw(types=(UpdateNewMessage, UpdateNewChannelMessage)) BEFORE NewMessage. Registration wrapped in try/except — if Raw registration fails, NewMessage+MessageDeleted still active (graceful degradation).
- New tests/test_raw_hook.py (13 tests): #12 chat_id normalization (PeerChannel→-100cid, PeerChat→-hid, PeerUser→uid, None→None, unknown→None), #6 Raw+NewMessage same msg → no dup (LRB idempotent overwrite + link_queue exactly 1 row), Raw resilience (no .message / empty text / unknown peer / None update → no exception).
- Test-mock fix: staticmethod _normalize_raw_chat_id must be assigned as plain function (not MethodType) on the SimpleNamespace mock — MethodType binding would inject `self` into a static function expecting 1 arg → TypeError. Documented in mock.

Stage Summary:
- PR-3 GREEN: 13/13 new + 527 baseline = 540/540 total PASS.
- Raw hook is a true supplementary layer: writes LRB only, fully fault-tolerant, never replaces NewMessage.
- chat_id normalization proven correct for all Telegram peer types (matches NewMessage's event.chat_id).

---
Task ID: PR-7 (Security hardening — Dashboard fail-closed + .gitignore + secrets audit)
Agent: main (Super Z)

Task: User flagged SECURITY as top priority. Log shows "DASHBOARD_API_KEY is UNSET — /api/* endpoints are OPEN". Must fix in production. No secrets in source/logs. Report historical secrets (no auto-purge).

Work Log:
- Secrets audit (live source scan): NO real secrets in tracked *.py. Only a FAKE token shape in tests ("1234567890:" + "a"*35, explicitly commented as fake). No ghp_/JWT tokens in source.
- Git history scan: commits 68dbb53 and 66779fe contain REAL secrets in download/create_env_v6.py (API_ID, API_HASH, BOT_TOKEN with real values). Commit 52427b0 is a revert with placeholders. → REPORTED, NOT PURGED (per user instruction #10 "no force-push/purge without approval"). Recommendation: rotate via BotFather + my.telegram.org, THEN git-filter-repo purge + force-push.
- Tracked files: db/custom.db is a Next.js sample (tables User/Post), NOT bot production DB — non-sensitive but bad practice to track. Added `*.db` to .gitignore for future safety (file stays tracked, removing needs approval).
- DASHBOARD API hardening (bot.py L11182-11316): changed from FAIL-OPEN to FAIL-CLOSED by default. When DASHBOARD_API_KEY is UNSET: /api/* now returns 401 (was: open). Added API_FAIL_OPEN env (default "false") as the ONLY escape hatch for a controlled transition (then masked PII, never raw phones). Health endpoints (/health, /ready, /metrics) stay open (probes). _warn_dashboard_api_key_open_once updated to log the new fail-closed posture. _api_should_show_full_pii unchanged (still requires key).
- New tests/test_api_security.py (13 tests): #13 no-key→401 for /api/stats /api/joined_groups /api/deploy_check; correct-key→200; wrong-key→401; /health /ready /metrics always open; API_FAIL_OPEN=true→open transition; #14 secret key value never logged.
- Regression fix: test_audit_regressions B06 + 5a-3 asserted OLD open behavior → updated to assert NEW fail-closed (+ API_FAIL_OPEN escape). Net +2 assertions (188→190).

Stage Summary:
- PR-7 GREEN: 13/13 new + 540 baseline = 555/555 total PASS (audit_regressions 188→190).
- Dashboard now SECURE-BY-DEFAULT: production /api/* can no longer leak phones/links to the open internet. Operator MUST set DASHBOARD_API_KEY (env) + frontend X-Api-Key; API_FAIL_OPEN=true for temporary transition.
- ⚠️ POTENTIAL BREAKING CHANGE (flagged): if the production frontend dashboard sends no X-Api-Key header, it will now get 401 until the operator sets DASHBOARD_API_KEY. This is the intended security fix per user requirement #10. Mitigation: operator sets DASHBOARD_API_KEY env in Render, OR temporarily API_FAIL_OPEN=true.
- Historical secrets (68dbb53/66779fe) reported — NOT touched (needs user approval + rotation first).

---
Task ID: FINAL (Production verification + observability export)
Agent: main (Super Z)

Task: Commit all PRs, push, verify Render deploy + production health, export new metrics to /metrics.

Work Log:
- Commit 35ff78d (FEAT LINK-CAPTURE-STRONG) pushed to main: PR-1/2/3/5/6/7 + 577/577 tests.
- Commit d81b682 (OBSERVABILITY) pushed: wired Metrics singleton into /metrics Prometheus endpoint (link_capture_total, link_ring_hits, delete_miss_total, delete_rescued_total, reconcile_rescued_total, duplicate_links_skipped, link_forwarded_total, floodwait_total, connected_joiners, disconnected_accounts, high_risk_chats, tight_poll_active).
- Production verification (https://whatsapp-userbot-yzm7.onrender.com):
  * /health → 200 ✅ Bot is running
  * /ready → status: ready, bot_connected: true, db_connected: true, active_watchers: 4
  * /api/stats (no key) → 401 ✅ (PR-7 fail-closed LIVE)
  * /api/joined_groups (no key) → 401 ✅
  * /api/pending_approvals (no key) → 401 ✅
  * /metrics → new counters LIVE with real data:
      link_capture_total = 3 (LRB captured 3 links via NewMessage Step 0 — proves reorder works)
      delete_miss_total = 7 (honest DELETE-MISS recording — matches user's original log)
      link_ring_hits = 0, delete_rescued_total = 0 (no deletes-in-window rescued yet)
      duplicate_links_skipped = 0
- ⚠️ Fleet: connected_joiners = 0, all_joiners_unavailable = true — JOIN blocked. This is the SAME operational issue the user's log showed. NOT a code regression — operator must clear FloodWait / re-authorize joiner sessions.

Stage Summary:
- All 7 PRs delivered + tests green (577/577) + production LIVE + verified.
- DEFERRED (honest): PR-4 adaptive tight-polling (velocity tracker + 2s poll for high-risk chats). Existing polling worker (5s baseline) still runs. high_risk_chats/tight_poll_active gauges export 0 until PR-4 lands.
- CANNOT verify end-to-end: JOIN/PUBLISH (all joiners unavailable — operator action). PUBLISH pipeline code untouched.
- Provided for operator: supabase/message_journal_snapshot.sql (run in Supabase SQL Editor; snapshot worker proven fault-tolerant without the table).
- Historical secrets (commits 68dbb53/66779fe in download/create_env_v6.py) REPORTED — NOT purged per user instruction. Recommend: rotate BOT_TOKEN/API_ID/API_HASH first, then git-filter-repo purge + force-push.
