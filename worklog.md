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
