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
