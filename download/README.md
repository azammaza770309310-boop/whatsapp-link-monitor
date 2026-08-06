# WhatsApp & Telegram Link Monitor — Production Edition

بوت تيليجرام احترافي متعدد المستخدمين لسحب روابط واتساب وتيليجرام من المجموعات
الجامعية (السعودية، الكويت، قطر، البحرين، الإمارات).

## ملف التشغيل

**الملف الذي يشغّله Render هو `bot.py`** (موجود في جذر المشروع).

هذا الملف يحتوي على كل الإصلاحات الأمنية وأداء وقاعدة البيانات من التدقيق الشامل.
ملف `monitor_v12.py` هو نسخة مطابقة لـ `bot.py` (لأغراض التطوير والاختبار).

## النشر على Render

### Service Type
**Background Worker** (ليس Web Service)

### Build Command
```
pip install -r requirements.txt
```

### Start Command
```
python bot.py
```

> ⚠️ **مهم**: لو Start Command لديك شيء آخر (مثل `python monitor_v6.py`)،
> غيّره إلى `python bot.py`. كل الإصلاحات موجودة في `bot.py` فقط.

### Health Check (اختياري لكن موصى به)
- **Health Check URL**: `https://<your-app>.onrender.com/health`
- أو استخدم `/ready` للـ readiness probe (يتحقق من DB + Telegram)

### نقاط النهاية (Endpoints)

| Endpoint | الوظيفة |
|----------|---------|
| `/` | Liveness (200 إذا العملية حية) |
| `/health` | Liveness probe — دائماً 200 |
| `/ready` | Readiness probe — 200 فقط إذا DB + Bot متصلان، 503 خلاف ذلك |
| `/metrics` | Prometheus metrics (6 gauges للمراقبة) |

## متغيرات البيئة المطلوبة

انسخ قيم `accounts.env.example` إلى Environment Variables في Render:

| المتغير | مطلوب | الوصف |
|---------|-------|-------|
| `API_ID` | ✅ | من https://my.telegram.org |
| `API_HASH` | ✅ | من https://my.telegram.org |
| `BOT_TOKEN` | ✅ | من @BotFather |
| `CHANNEL_ID` | ✅ | رقم القناة (يبدأ بـ -100) |
| `OWNER_ID` | اختياري | معرفك الرقمي من @userinfobot (للأوامر الإدارية) |
| `SUPABASE_URL` | موصى به | رابط Supabase للتخزين الدائم |
| `SUPABASE_KEY` | موصى به | مفتاح Supabase |
| `OPENAI_API_KEY` | اختياري | مفتاح Groq للذكاء الاصطناعي |
| `STARTUP_SCAN_DAYS` | اختياري | عدد أيام المسح عند البدء (أو None) |

## التشغيل المحلي

```bash
# 1. انسخ accounts.env.example إلى accounts.env واملأ القيم
cp accounts.env.example accounts.env

# 2. ثبت المتطلبات
pip install -r requirements.txt

# 3. شغّل البوت
python bot.py
```

## الاختبارات

```bash
# جميع الاختبارات (167+ اختبار)
python -m unittest discover tests

# اختبارات التحقق من النشر (مهمة قبل كل deploy)
python -m unittest tests.test_bot_py_deployment -v

# اختبارات الكوارث (Chaos Engineering)
python -m unittest tests.test_chaos_engineering -v
```

## المميزات

- ✅ **متعدد المستخدمين**: كل مستخدم يضيف حسابه كـ "مُراقب" عبر `/login`
- ✅ **ذكاء اصطناعي**: تحليل الرسائل بـ Groq/Gemini/OpenRouter (مع multi-key fallback)
- ✅ **فلترة ذكية**: استبعاد الإعلانات والروابط غير التعليمية
- ✅ **مسح تاريخي**: سحب الروابط من آخر N يوم عند الطلب
- ✅ **فحص العضوية**: توصية الانضمام فقط للمراقبين غير المشتركين
- ✅ **Deduplication**: منع تكرار الروابط (atomic INSERT OR IGNORE)
- ✅ **Supabase**: تخزين دائم + fallback تلقائي لـ SQLite عند الانقطاع
- ✅ **مراقبة**: `/health`, `/ready`, `/metrics` endpoints
- ✅ **استرداد الكوارث**: DB corruption recovery, auto-reconnect, retry caps
- ✅ **أمان**: HTML injection protection, URL validation, fail-closed auth

## البنية

```
bot.py                    ← ملف التشغيل الرئيسي (ما يشغّله Render)
monitor_v12.py            ← نسخة مطابقة (للتطوير)
accounts.env.example      ← قالب المتغيرات البيئية
requirements.txt          ← Python dependencies
tests/                    ← 182 اختبار (وحدة + تكامل + كوارث + أمن)
frontend/                 ← Next.js dashboard
pro_backend/              ← FastAPI proxy (اختياري)
```

## الأوامر

### أوامر القناة (تُرسل في القناة المشتركة)
- `/help` — دليل الاستخدام
- `/status` — حالة البوت
- `/watchers` — قائمة المراقبين
- `/scan_week` — مسح آخر أسبوع
- `/scan_month` — مسح آخر شهر
- `/scan_60` — مسح آخر 60 يوم
- `/scan_90` — مسح آخر 90 يوم
- `/scan_full` — مسح كامل
- `/scan_stop` — إيقاف المسح
- `/reset_scan` — إعادة تعيين سجل المسح

### أوامر الدردشة الخاصة (تُرسل للبوت)
- `/start` — البدء وعرض القائمة
- `/login` — تسجيل الدخول بحسابك
- `/status` — حالتك
- `/cancel` — إلغاء عملية التسجيل

## الأمان

- 🔒 `OWNER_ID` يقيّد أوامر القناة لمالك واحد
- 🔒 HTML injection blocked في كل رسائل البوت
- 🔒 URL validation — فقط روابط http(s) في `href`
- 🔒 Callback data size capped (256 bytes)
- 🔒 Login rate-limited (60s cooldown بين طلبات الكود)
- 🔒 Login sessions expire (10-min TTL)
- 🔒 Database corruption auto-recovery
- 🔒 `busy_timeout=5s` (لا تجميد 30 ثانية)
- 🔒 Retry caps on all network operations (max 120s total)

## الترخيص

استخدام شخصي/تعليمي.
