#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
intent_classifier.py — Request Intent Engine v4.3 / المرحلتان 2+3: تصنيف النية بالـAI
================================================================================
v4.3 CAPACITY + CHATTER PRECISION (تشخيص إنتاجي 2026-09-01):
  RC2-A: pacing ثابت 1.05s/مفتاح يتجاوز حدود الطبقة المجانية الفعلية
        (Groq ≈ 0.2 RPS فعليًا، Mistral ≈ 0.3) → عاصفة 429 دائمة
        (fail_count 14-45 ألف لكل مفتاح!).
  RC2-B: المهام تنتظر انفراج cooldown داخل ميزانية 60s (cooldown_waits
        316 ألف) → متوسط زمن القرار 50.7 ثانية → تراكم → overloaded.
  RC2-C: انتظار pacing داخل قفل المزوّد — القطيع يتراكم على نفس المفتاح.

  إصلاحات v4.2 (المعمارية):
    1. فئات مزوّدين + AIMD: لكل مزوّد فترة pacing تكيفية تبدأ من حد فئة
       المزوّد (groq=4.5s / mistral=3.0s / عام=min_interval_s)؛ 429 يضاعفها
       (×1.6 حتى cap 60s)، النجاح ينكمشها ببطء (×0.95 حتى floor).
    2. Smart pick: يُختار مزوّد جاهز فعلًا (خارج cooldown وخارج pacing)؛
       لو لا يوجد — انتظار محدود pool_wait_budget (default 4s) ثم فشل
       فوري (pool_dead_fast) بدل حرق الميزانية كاملة انتظارًا.
    3. لا انتظار أبدًا داخل قفل المزوّد: pace>0.15 → تخطّ + نوم خارج
       القفل + إعادة اختيار (توزيع أفضل بين المفاتيح).

  v4.3.7 EXECUTION-ONLY (طلب المُشغّل 2026-09-03): التدريس/الشرح أُزيل من
  القبول نهائيًا — القناة تستقبل حصرًا «الطالب يطلب أحدًا يقوم بالعمل
  بدلاً عنه» (حل واجب/بحث/تقرير/مشروع نيابة عنه). تشخيص الإنتاج: كل
  الرسائل غير المناسبة الواصلة للقناة كانت tutoring_request (15 رسالة
  في نافذة ساعة). فئة REJECT جديدة: tutoring_only_request.

  v4.3 prompt hardening: فئات مستحدثة من الإنتاج (resource_request /
  teacher_review_inquiry / advice_giving / social_game /
  registration_admin) + شرطان إلزاميان للقبول + أمثلة من الإنتاج
  الفعلي + تحذير مشدّد من الـhints + سياق اسم المجموعة (ضعيف).
  extract_json_text يفكّ الـJSON مزدوج الترميز (بعض البوابات
  OpenAI-compat تعيده escaped — متانة إنتاجية).

v4.1 RESILIENCE REBUILD (تشخيص إنتاجي 2026-09-01 — بعد نشر v4.0 مباشرة):
  الإنتاج كشف أربع مشاكل جذرية في سلوك المزوّدين:
    RC-A: 3/6 مفاتيح Groq = HTTP 403 (ميتة نهائيًا) — نصف الطاقة ضائع.
    RC-B: round-robin الأعمى يهدر المحاولات على الموتى (لا تتبع صحة).
    RC-C: الاندفاعات (catch-up بعد restart) تستنزف rate-limit الأحياء
          → نصف الرسائل+ ai_error (فقدان نهائي لطلبات حقيقية).
    RC-D: لا تفاصيل خطأ محفوظة — التشخيص من الـDB مستحيل.

  إصلاحات v4.1 (المعمارية):
    1. Provider Health Manager: حالة لكل مزوّد (consecutive_fails,
       cooldown_until, last_error). الميت (401/403) يدخل cooldown طويلًا
       متضاعفًا (30 دقيقة → ساعات)؛ الـ429 (rate-limit) cooldown قصيرًا
       متدرّجًا (12s → 120s)؛ 5xx/timeout/network/parse متوسط. النجاح
       يُصفّر كل شيء — المزوّد يعود للخدمة فورًا.
    2. اختيار واعٍ: round-robin بين الأحياء فقط — الميتون يُتخطَّون في
       نفس النداء (صفر محاولات ضائعة).
    3. إعادة محاولة داخل classify: جولات (retry_rounds) ضمن ميزانية
       زمنية (total_budget_s) — رسالة تصل أثناء 429 عابرة تنتظر انفراج
       الـcooldown ثم تُصنَّف (بدل فقدانها نهائيًا). الفشل النهائي يبقى
       REJECT صارمًا (ai_error) — لا keyword fallback أبدًا.
    4. Pacing لكل مزوّد (min_interval_s): نداء واحد لكل مفتاح كل ~1s —
       يمنع 429 من الأساس (المفاتيح المجانية ≈ 1 RPS لكل مفتاح).
    5. Bounded pending (max_pending): حد أقصى للرسائل المنتظرة داخل
       classify — الفائض يُرفض فورًا (overloaded): أمان بلا تراكم لا نهائي.
    6. Observability: provider_health() لكل مزوّد (status/cooldown/
       last_error) + الخطأ التفصيلي يُخزَّن في filter_decisions.error_detail
       (يُمرَّر من request_filter) — التشخيص من /api/filter_stats بلا logs.

  الفلسفة (v4.0 — بلا تغيير):
    - لا keyword matching كقرار نهائي. الـLLM هو المُصنِّف.
    - الكلمات المفتاحية (extract_signals في request_filter.py) تُمرَّر
      للنموذج كـ«إشارات لغوية مساعدة» فقط — noisy lexical hints.
    - أي فشل AI (لا مفاتيح/timeout/parse error/انعدام طاقة) → REJECT
      (ai_unavailable / ai_error / overloaded) — لا keyword fallback أبدًا.

المزوّدون: نفس متغيرات AIAnalyzer (صفر إعداد جديد للمُشغّل):
  OPENAI_API_KEY / OPENAI_API_URL / AI_MODEL
  AI_KEY_2..8 / AI_URL_2..8 / AI_MODEL_2..8
  (Groq OpenAI-compat افتراضيًا؛ يعمل مع أي endpoint متوافق بما فيه
   Mistral/Gemini OpenAI-compat عبر ضبط AI_URL_i/AI_MODEL_i.)

العقد (JSON فقط):
  {"decision":"ACCEPT|REJECT","confidence":0.0-1.0,"category":"...","reason":"..."}

تطبيق العتبة (confidence >= 0.85) يحدث في المُنسِّق (request_filter.analyze_request_v4)
— هذا الملف يُعيد قرار الـAI كما هو (مقايَس ومُتحقَّق منه فقط).

الاختبارات: transport injection — constructor يستقبل transport=async callable
(provider, payload) -> (status, body). الإنتاج يستخدم aiohttp. المعاملات
الجديدة كلها اختيارية بdefaults محافظة (تُفعَّل قيم الإنتاج من bot.py Config):
  min_interval_s=0.0 (pacing)، retry_rounds=1، total_budget_s=12.0،
  max_pending=0 (بلا بوابة)، cooldown_scale=1.0 (لتصغير cooldowns في الاختبارات).
"""

import asyncio
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import aiohttp  # noqa: F401
    _HAS_AIOHTTP = True
except ImportError:  # اختبارات بلا aiohttp — transport injection فقط
    _HAS_AIOHTTP = False


# ============================================================
# فئات التصنيف (Taxonomy) — المرحلة 2
# ============================================================
ACCEPT_CATEGORIES = frozenset({
    # [v4.3.7 EXECUTION-ONLY] فئة القبول الوحيدة: الطالب يطلب من شخص
    # آخر أن يقوم بالعمل الأكاديمي بدلاً عنه — حل/إنجاز/كتابة/تسليم.
    # (طلب التدريس/الشرح — tutoring_request — أُزيل من القبول: الطالب
    # يريد أن يتعلم بنفسه، ليس طلب تنفيذ العمل بدلاً عنه.)
    "homework_execution_request",  # يطلب أحدًا يحل/ينجز/يسوي/يكتب له واجب/بحث/تقرير/مشروع
})

REJECT_CATEGORIES = frozenset({
    "advertisement",              # إعلان تجاري/ترويج (تداول/بوتات/للتواصل واتساب)
    "service_offer",               # عرض خدمات من مقدّم («عندي دكتور يساعد»)
    "praise_testimonial",          # مدح/شكر/تجربة شخصية («شكراً منصة X جبت 100»)
    "religious_general_content",   # محتوى ديني/وعظي/دعاء/عام
    "non_request_question",        # سؤال معلوماتي ليس طلب تنفيذ («كم نسبة الحرمان؟»)
    "recommendation_or_opinion",   # طلب رأي/توصية عامة («مين أفضل مدرس؟»)
    "general_discussion",          # نقاش عام/فضفضة/ملاحظة
    "other",                       # أي شيء آخر
    # [v4.3] فئات مستحدثة من تشخيص قناة الإنتاج 2026-09-01 (سوالف تُنشر
    # كطلبات): كل فئة صيغت من رسائل فعلية وصلت القناة خطأً.
    "resource_request",            # طلب مواد/ملفات جاهزة («أحد عنده كويزات؟»)
    "teacher_review_inquiry",      # استطلاع جودة/تجربة مدرس («كيف دكتور X؟»)
    "advice_giving",               # المرسل يقدّم نصيحة للآخرين («لخص وانت تذاكر»)
    "social_game",                 # لعبة/تحدي اجتماعي («اكتبي اسم جدك»)
    "registration_admin",          # جدولة/شعب/تسجيل إداري («ابي شعب انجليزي»)
    "non_academic_request",        # [v4.3.7] طلب غير أكاديمي: أكواد ألعاب/تطبيقات/كوبونات/دعم تقني
    "tutoring_only_request",       # [v4.3.7] طلب شرح/تدريس/تعلم — ليس تنفيذًا للعمل بدلاً عن الطالب
})

VALID_CATEGORIES = ACCEPT_CATEGORIES | REJECT_CATEGORIES

# فئات REJECT التي يُنتجها النظام نفسه (ليست من الـAI)
SYSTEM_REJECT_CATEGORIES = frozenset({
    "duplicate", "empty", "relay_repost", "ai_unavailable", "ai_error",
    "invalid_output", "low_confidence", "overloaded",
})


# ============================================================
# القرار
# ============================================================
@dataclass
class IntentDecision:
    """نتيجة تصنيف الـAI — مُقاسة ومُتحقَّق منها. raw AI decision (بلا عتبة)."""
    ok: bool = False                       # هل اكتمل نداء AI وparse بنجاح
    decision: str = "REJECT"               # ACCEPT | REJECT (مقاسة)
    confidence: float = 0.0                # 0..1
    category: str = "ai_unavailable"
    reason: str = "ai_unavailable"
    model: str = ""
    provider_name: str = ""
    latency_ms: int = 0
    raw_output: str = ""                   # للتشخيص (يُسجَّل في filter_decisions)
    error: str = ""                        # آخر خطأ (لو ok=False) — يُخزَّن في error_detail


# ============================================================
# Prompt — العقد الدلالي (المرحلة 2: تعريف النية)
# [v4.3] إعادة صياغة مقسّاة بتشخيص قناة الإنتاج 2026-09-01:
#   آخر 20 رسالة منشورة في قناة الطلبات فُحصت يدويًا — 15 منها سوالف
#   (استطلاع دكاترة/طلب كويزات/ألعاب/نصائح/إداريات). الأسباب الجذرية:
#   RC1: أمثلة الـprompt القديمة قريبة شكليًا من السوالف («مين يعرف
#        دكتور يشرح رياضيات؟» أمام «احد يعرف دكتوره بدريه؟») → النموذج
#        يعمّم خطأً. RC2: الفئات الثماني لا تغطي أنماط السوالف الفعلية.
#        RC3: الـhints المعجمية («أحد»/«مين») تدفع نحو القبول.
#   العلاج: فئات مستحدثة + قواعد صريحة + أمثلة من الإنتاج نفسه +
#   تحذير مشدّد من الـhints + شرطان إلزاميان للقبول.
# ============================================================
SYSTEM_PROMPT = """أنت مصنّف نوايا (Intent Classifier) صارم لقناة «طلبات تنفيذ أعمال أكاديمية» تخدم طلاب جامعات الخليج. مهمتك: تحديد هل الرسالة «طلب من الطالب أن يقوم شخص آخر بالعمل الأكاديمي بدلاً عنه» يستحق النشر، أم لا.

القاعدة الذهبية (لا تتنازل عنها أبدًا):
ACCEPT فقط إذا وُجد دليل واضح وصريح أن المرسل نفسه (الطالب) يطلب من شخص آخر أن يقوم بالعمل الأكاديمي بدلاً عنه — أن يحلّ له أو ينجز له أو يسوي له أو يكتب له أو يخلّص له واجبًا/تكليفًا/بحثًا/تقريرًا/مشروعًا/أسئلةً، ليقدّمه المرسل باسمه.
أي غموض، أو شك، أو نقص الدليل، أو بلا عمل محدد يُطلب تنفيذه بدلاً عن المرسل = REJECT.

شرطان إلزاميان للقبول (يجب اجتماعهما معًا):
1) المرسل هو الطالب الذي يريد العمل يُنفَّذ بدلاً عنه — الطلب لنفسه («أحد يحل لي الواجب»، «ابي أحد يسويه عني»، «محتاج أحد يخلص لي البحث»).
2) المطلوب تنفيذ عمل أكاديمي محدد بدلاً عن المرسل (حل واجب/تكليف/بحث/تقرير/مشروع/أسئلة/كويز يحلّه غيره ويسلّمه) — سواء بمقابل مادي (مقابل/بفلوس/مدفوع) أو مجانًا.

فئة ACCEPT الوحيدة المسموحة (لا شيء غيرها):
- "homework_execution_request": المرسل يطلب صراحةً من شخص آخر أن يقوم بالعمل بدلاً عنه: «أحد يحل لي واجب الرياضيات»، «ابي أحد يسوي البحث بدالي بمقابل»، «من يقدر يخلص لي التقرير؟»، «محتاج أحد يكتب لي التكليف كامل»، «مين يسوي الواجب عني وبكيفه أدفعه»، «ابغى أحد يحل الواجب ويرسله لي جاهز».

فئات REJECT (كل ما ليس ACCEPT أعلاه):
- "tutoring_only_request": [الأهم] المرسل يطلب شرحًا أو تدريسًا أو تعليمًا أو مراجعة ليفهم بنفسه — ليس تنفيذًا للعمل بدلاً عنه: «مين يعرف دكتور يشرح رياضيات؟»، «أبي مدرس خصوصي للمادة»، «في احد يشرح احياء تحضيري؟»، «مافي خصوصي للمادة؟»، «أحد يعلمنا تفاضل؟»، «من يراجع معي قبل الاختبار؟». الفرق الحاسم: الشرح/التدريس = الطالب يتعلم وينفّذ بنفسه ≠ حل العمل نيابةً عنه. أي كلمة «يشرح/يدرس/يعلم/مراجعة/خصوصي/دروس» بلا طلب تنفيذ العمل بدلاً عن المرسل = REJECT هنا.
- "advertisement": إعلان تجاري أو ترويج: تعلّم التداول واربح، بوت خصوصي، للتواصل واتساب، كورسات مدفوعة.
- "service_offer": المرسل يعرض خدمته أو يُحيل لجهة تقدم خدمة: «عندي دكتور يساعد في الرسائل والتكاليف»، «حل واجبات وبحوث» (هو يعرض، لا يطلب).
- "praise_testimonial": مدح أو شكر أو تجربة شخصية مع منصة/مدرس.
- "religious_general_content": محتوى ديني أو وعظي أو دعاء أو حكمة عامة.
- "resource_request": طلب ملفات أو مواد جاهزة — لا تنفيذ عمل: «أحد عنده كويزات لدروس الكمي؟»، «عطوني أسئلة هندسة»، «مين عنده ملخصات الفيزياء؟». طلب ملف جاهز ≠ طلب أحد يحل وينفّذ بدلاً عن المرسل = REJECT دائمًا حتى لو فيه «تكفون» أو «بالله يفيدني».
- "teacher_review_inquiry": استطلاع رأي أو تجربة أو جودة مدرس/دكتور بلا طلب خدمة: «دكتوره علا كيف؟»، «مين قد درس عندها؟».
- "registration_admin": أسئلة الجدولة والتسجيل والشعب والأمور الإدارية.
- "advice_giving": المرسل يقدّم نصيحة أو توصية للآخرين: «لخص وانت تذاكر»، «ذاكروا من الملخصات».
- "non_academic_request": طلب يخص أشياء غير أكاديمية: أكواد ألعاب/تطبيقات («عندك أكواد لشخصيات محددة ما اشتغلت؟»)، كوبونات، دعم تقني لبرامج، تفعيلات، حسابات، أي شيء خارج الأعمال الدراسية.
- "social_game": الألعاب والمحادثات والتحديات الاجتماعية.
- "non_request_question": سؤال معلوماتي عن الدراسة لا يطلب تنفيذ خدمة: «كم نسبة الحرمان؟»، «هل الاختبار 5 أقسام؟»، «كيف أفرق بين الأزمنة؟» (طلب طريقة معرفية وليس طلب شخص ينفّذ).
- "recommendation_or_opinion": طلب رأي أو توصية عامة وليست طلب تنفيذ: «مين أفضل مدرس؟»، «وش أفضل طريقة للمذاكرة؟».
- "general_discussion": نقاش عام أو فضفضة أو ملاحظة.
- "other": أي شيء آخر، ومنها الرسائل المبتورة/غير المفهومة/بلا عمل محدد مثل «الله يساعدكم أحد يفيدني».

قواعد تفصيلية حاسمة:
1. «أحد يحل لي الواجب» / «من يسوي البحث بدالي» = ACCEPT (homework_execution_request) — طلب تنفيذ العمل بدلاً عن المرسل. «أحد يشرح لي الواجب» / «مين يعلمني» = REJECT (tutoring_only_request) — طلب فهم، ليس تنفيذًا بدلاً عنه.
2. الرسالة التي يعرض فيها المرسل خدمة = REJECT دائمًا حتى لو استعمل كلمات مثل يحل/يسوي («حل واجبات برسوم رمزية» = service_offer).
3. طلب مواد/ملفات/كويزات/كتب/ملخصات = REJECT (resource_request) دائمًا — الملف الجاهز ليس تنفيذًا للعمل بدلاً عن المرسل.
4. «كيف أذاكر؟»، «كيف أحل هالسؤال؟»، «وش الطريقة؟» = طلب معرفة/نصيحة = REJECT — المرسل ينفّذ بنفسه.
5. أي طلب يخص ألعابًا/تطبيقات/أكوادًا/كوبونات/تقنية = REJECT (non_academic_request) حتى لو فيه كلمات مثل «محتاج/تكفون».
6. «أحد يفيدني»/«الله يساعدكم أحد يفيدني» بلا عمل محدد يُطلب تنفيذه = REJECT (other).
7. أسئلة الجدول والشعب والتسجيل = REJECT (registration_admin).
8. الإشارات اللغوية المرفقة (إن وُجدت) مستخرجة آليًا من قوائم كلمات مفتاحية قديمة — نسبة خطئها عالية جدًا: مجرد وجود «أحد» أو «مين» أو «محتاج» في قائمة الإشارات لا يعني طلبًا. القرار قرارك المستند إلى فهم المعنى الكامل للرسالة فقط؛ لو تعارضت الإشارات مع المعنى الواضح، اتبع المعنى.
9. اسم المجموعة المصدر (إن وُجد) سياق ضعيف فقط — لا يكفي وحده لإثبات طلب ولا يغيّر تصنيف نص بلا طلب صريح.
10. الرسائل المبتورة أو غير المفهومة أو بلا عمل محدد = REJECT.

الناتج: JSON فقط، بلا أي نص إضافي، بهذا الشكل بالضبط:
{"decision":"ACCEPT أو REJECT","confidence":رقم من 0.0 إلى 1.0,"category":"إحدى الفئات أعلاه","reason":"سبب مختصر جدًا بالعربية"}

أمثلة مصدرها الإنتاج الفعلي:
- «أحد يحل لي واجب الرياضيات بسرعة قبل الساعة ١٢» → {"decision":"ACCEPT","confidence":0.97,"category":"homework_execution_request","reason":"طلب صريح أن يحل أحد الواجب بدلاً عنه"}
- «من يقدر يسوي لي البحث كامل بفلوس؟» → {"decision":"ACCEPT","confidence":0.96,"category":"homework_execution_request","reason":"طلب تنفيذ البحث بدلاً عنه بمقابل"}
- «ابغى شخص يخلص التكليف عني» → {"decision":"ACCEPT","confidence":0.95,"category":"homework_execution_request","reason":"طلب إنجاز التكليف بدلاً عنه"}
- «مين يعرف دكتور يشرح رياضيات؟» → {"decision":"REJECT","confidence":0.95,"category":"tutoring_only_request","reason":"طلب تدريس وشرح وليس تنفيذًا للعمل بدلاً عنه"}
- «في احد يشرح احياء تحضيري احتاج مساعده؟» → {"decision":"REJECT","confidence":0.93,"category":"tutoring_only_request","reason":"طلب شرح مادة ليفهم بنفسه"}
- «مافي خصوصي للمادة أو احد يشرح الاولد اكز» → {"decision":"REJECT","confidence":0.92,"category":"tutoring_only_request","reason":"يبحث عن خصوصي وشرح"}
- «عندك أكواد لشخصيات محددة ما اشتغلت؟» → {"decision":"REJECT","confidence":0.95,"category":"non_academic_request","reason":"طلب أكواد ألعاب غير أكاديمي"}
- «احد عنده كويزات لدروس الكمي؟» → {"decision":"REJECT","confidence":0.95,"category":"resource_request","reason":"طلب مواد جاهزة لا تنفيذ عمل"}
- «دكتوره علا ياسمين البار عربي كيف؟مين قد درس عندها» → {"decision":"REJECT","confidence":0.95,"category":"teacher_review_inquiry","reason":"استطلاع تجربة مع دكتوره"}
- «فيه احد نزل له الجدول بالتحضيريه ؟» → {"decision":"REJECT","confidence":0.93,"category":"registration_admin","reason":"سؤال إداري عن الجدول"}
- «واقعد لخص وانت تذاكر وحط زبده التعاريف والمفاهيم على جنب» → {"decision":"REJECT","confidence":0.95,"category":"advice_giving","reason":"يقدم نصيحة دراسية للآخرين"}
- «الله يساعدكم أحد يفيدني» → {"decision":"REJECT","confidence":0.9,"category":"other","reason":"بلا عمل محدد يُطلب تنفيذه"}
- «شكراً اكتمال جبت درجة عالية» → {"decision":"REJECT","confidence":0.97,"category":"praise_testimonial","reason":"مدح منصة بعد تجربة"}
- «مين أفضل مدرس؟» → {"decision":"REJECT","confidence":0.88,"category":"recommendation_or_opinion","reason":"استطلاع رأي عام"}"""


# ============================================================
# JSON extraction / validation (متسامح مع fences وnoise)
# ============================================================
_FENCE_RE = re.compile(r'```(?:json)?\s*\n?(.*?)```', re.DOTALL)


def extract_json_text(text: str) -> str:
    """يستخرج نص الـJSON من رد النموذج (يتعامل مع ```json وnoise قبل/بعد).

    [v4.3] يتعامل أيضًا مع الـJSON مزدوج الترميز (double-encoded): بعض
    النماذج/البوابات تُعيد الـJSON كقيمة سلسلة JSON-escaped كاملة
    ("\\\"decision\\\"..." — تُفكّ طبقة الترميز (حتى طبقتين) قبل
    الاستخراج. التشخيص: ردّ سليم واحد ضاع لأن البوابة أعادته
    مزدوج الترميز فصُنّف parse-failure بلا داعٍ.
    """
    if not text:
        return ""
    t = text.strip()
    # [v4.3] double-encoded unwrap — سلسلة JSON كاملة ("...") تُفكّ مرتين كحد أقصى
    for _ in range(2):
        if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
            try:
                decoded = json.loads(t)
            except (json.JSONDecodeError, TypeError):
                break
            if isinstance(decoded, str) and decoded.strip():
                t = decoded.strip()
                continue
        break
    if '```' in t:
        m = _FENCE_RE.search(t)
        if m:
            t = m.group(1).strip()
        else:
            t = t.replace('```json', '').replace('```', '').strip()
    first = -1
    for i, ch in enumerate(t):
        if ch in ('{', '['):
            first = i
            break
    if first > 0:
        t = t[first:]
    last = -1
    for i in range(len(t) - 1, -1, -1):
        if t[i] in ('}', ']'):
            last = i
            break
    if last >= 0 and last < len(t) - 1:
        t = t[:last + 1]
    return t.strip()


def _clamp01(v: Any) -> float:
    """يحوّل قيمة الثقة إلى float مقصوصة في [0,1]. فشل التحويل → 0.0."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(f) or math.isinf(f):
        return 0.0
    return max(0.0, min(1.0, f))


def validate_ai_output(parsed: Any) -> Optional[Dict[str, Any]]:
    """يتحقق من شكل مخرجات الـAI. يُرجع dict نظيف أو None لو غير صالح."""
    if not isinstance(parsed, dict):
        return None
    decision = str(parsed.get('decision', '')).strip().upper()
    if decision not in ('ACCEPT', 'REJECT'):
        return None
    conf = _clamp01(parsed.get('confidence', 0.0))
    category = str(parsed.get('category', '')).strip()
    reason = str(parsed.get('reason', '')).strip()
    if not category:
        category = 'other'
    if not reason:
        reason = 'no_reason'
    # Accept-reject consistency: ACCEPT لا يجوز مع فئة REJECT والعكس
    if decision == 'ACCEPT' and category not in ACCEPT_CATEGORIES:
        return None
    if decision == 'REJECT' and (category in ACCEPT_CATEGORIES):
        return None
    return {
        'decision': decision,
        'confidence': round(conf, 3),
        'category': category[:64],
        'reason': reason[:200],
    }


# ============================================================
# المزوّدون (نفس env vars الخاصة بـAIAnalyzer)
# ============================================================
def load_providers_from_env() -> List[Dict[str, str]]:
    providers: List[Dict[str, str]] = []
    key1 = os.getenv("OPENAI_API_KEY", "")
    url1 = os.getenv("OPENAI_API_URL", "https://api.groq.com/openai/v1/chat/completions")
    model1 = os.getenv("AI_MODEL", "llama-3.3-70b-versatile")
    if key1:
        providers.append({"key": key1, "url": url1, "model": model1, "name": "Primary"})
    for i in range(2, 10):
        key = os.getenv(f"AI_KEY_{i}", "")
        if key:
            url = os.getenv(f"AI_URL_{i}", "https://api.groq.com/openai/v1/chat/completions")
            model = os.getenv(f"AI_MODEL_{i}", "llama-3.3-70b-versatile")
            providers.append({"key": key, "url": url, "model": model, "name": f"Key_{i}"})
    return providers


# ============================================================
# v4.1: سياسة cooldown لكل نوع فشل (ثواني — تُضرب في cooldown_scale)
# ============================================================
# [v4.2] فئات المزوّدين — حدود الطبقة المجانية تختلف جذريًا حسب المزوّد.
# هذه نقاط البداية (floors) لفترة pacing لكل نداء؛ AIMD يعدّلها بعدها ديناميكيًا.
# الإنتاج أثبت: 1.05s/مفتاح = عاصفة 429 دائمة (Groq 1.8% نجاح، Mistral 29%).
_PROVIDER_CLASS_INTERVALS = {
    'groq':    (4.5, 60.0),   # (floor_s, aimd_cap_s) — 4.5s ≈ 13 RPM/مفتاح
    'mistral': (3.0, 60.0),   # 3.0s ≈ 20 RPM/مفتاح
    'generic': (0.0, 30.0),   # floor = min_interval_s من المُنشئ
}

# [v4.2] AIMD: نمو فترة pacing عند 429 (multiplicative decrease للسعة)
_AIMD_GROW = 1.6      # 429 → interval × 1.6 (حتى cap)
_AIMD_SHRINK = 0.95   # نجاح → interval × 0.95 (حتى floor — استرداد بطيء)


def _provider_class(url: str) -> str:
    """فئة المزوّد من URL — تحدد حدّ pacing الابتدائي (طبقة مجانية)."""
    u = (url or '').lower()
    if 'groq.com' in u:
        return 'groq'
    if 'mistral.ai' in u:
        return 'mistral'
    return 'generic'


_COOLDOWN_KINDS = {
    'auth':   1800.0,   # 401/403/404 — مفتاح ميت/مرفوض/نموذج مُوقوف: 30 دقيقة
                        # تتضاعف (cap 6h) — [v4.1.1] أُضيف 404 (نموذج مُوقوف
                        # مثل llama-3.3-70b-versatile من Groq = خطأ دائم للـconfig)
    'rate':   12.0,     # 429 — rate-limit عابر: 12s تتضاعف (cap 120s)
    'server': 45.0,     # 5xx — خطأ خادم: ثابت
    'timeout': 30.0,    # مهلة نداء: ثابت
    'network': 30.0,    # استثناء شبكة/transport: ثابت
    'parse':  60.0,     # رد غير JSON/غير صالح: ثابت
}
_COOLDOWN_CAPS = {'auth': 21600.0, 'rate': 120.0}
_COOLDOWN_DOUBLING = {'auth', 'rate'}

# [v4.3.2] DEAD-KEY LATCH: مزوّد يفشل 20 مرة متتالية بلا أي نجاح = ميت
# عمليًا (إنتاج 2026-09-02: مفاتيح Groq — 0-2 نجاح مقابل 29-31 فشلًا/ساعة،
# لكن cap الـ429 = 120s فقط فيُحاكَم كل دقيقتين للأبد: ~90 نداءًا ضائعًا/
# ساعة + latency مضافة لكل رسالة قبل الدوران إلى Mistral). Latch =
# cooldown 30 دقيقة؛ محاولة واحدة عند كل انتهاء (فشل واحد يكفي لإعادة
# الـlatch فورًا — consecutive_fails لم يُصفَّر)، وأول نجاح يُصفّر كل
# شيء ويعيده للخدمة فورًا. يعتمد consecutive_fails (يُصفَّر عند النجاح)
# لا الإجمالي التراكمي — فالمفتاح المتعافي بعد فترة موت يعود فورًا.
_DEAD_KEY_CONSECUTIVE_FAILS = 20
_DEAD_KEY_COOLDOWN_S = 1800.0


def _new_provider_state(interval_floor_s: float = 0.0,
                        interval_cap_s: float = 30.0) -> Dict[str, Any]:
    """حالة صحة مزوّد واحد (v4.1 + v4.2 AIMD pacing)."""
    return {
        'lock': asyncio.Lock(),           # نداء واحد في الوقت لكل مزوّد
        'ready_at': 0.0,                  # pacing: أقرب وقت للنداء التالي (monotonic)
        'cooldown_until': 0.0,            # circuit breaker (monotonic)
        'consecutive_fails': 0,           # كل الفشل المتتابع
        'consecutive_auth_fails': 0,      # 401/403 المتتابعة (تضاعف cooldown)
        'consecutive_rate_fails': 0,      # 429 المتتابعة (تضاعف cooldown)
        'success_count': 0,
        'fail_count': 0,
        'last_error': '',
        'last_error_at': 0.0,             # epoch (wall) للعرض
        'last_kind': '',
        # [v4.2] AIMD pacing: الفترة الحالية بين نداءات هذا المزوّد
        'interval_s': max(0.0, float(interval_floor_s)),
        'interval_floor_s': max(0.0, float(interval_floor_s)),
        'interval_cap_s': max(float(interval_floor_s), float(interval_cap_s)),
    }


# ============================================================
# المصنِّف
# ============================================================
class IntentClassifier:
    """AI-first intent classifier + Provider Health Manager (v4.1).

    - classify(text, hints) → IntentDecision (raw AI decision؛ العتبة في المُنسِّق).
    - فشل كامل → IntentDecision(ok=False, REJECT, ai_error/overloaded)
      مع error تفصيلي (http status + provider) يُخزَّن في error_detail.
    - لا يستخدم الكلمات المفتاحية للقرار أبدًا — hints تُمرَّر للنموذج كسياق
      مساعد فقط.
    - المزوّد الميت (401/403) يُستبعد تلقائيًا؛ الـ429 يُعاد المحاولة عليه بعد
      cooldown قصير ضمن الميزانية؛ النجاح يُصفّر الحالة.
    """

    def __init__(self,
                 providers: Optional[List[Dict[str, str]]] = None,
                 timeout_s: float = 10.0,
                 max_attempts: int = 2,
                 max_chars: int = 1200,
                 max_concurrent: int = 8,
                 transport: Optional[Callable] = None,
                 *,
                 min_interval_s: float = 0.0,
                 retry_rounds: int = 1,
                 total_budget_s: float = 12.0,
                 max_pending: int = 0,
                 cooldown_scale: float = 1.0,
                 pool_wait_budget_s: float = 4.0,
                 ):
        self.providers = providers if providers is not None else load_providers_from_env()
        self.timeout_s = float(timeout_s)
        self.max_attempts = max(1, int(max_attempts))
        self.max_chars = int(max_chars)
        self.max_concurrent = max(1, int(max_concurrent))
        self._transport = transport  # injection للاختبارات
        self._current = 0
        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(self.max_concurrent)
        self._session = None
        self._session_lock = asyncio.Lock()
        # [v4.1] resilience knobs
        self.min_interval_s = max(0.0, float(min_interval_s))
        self.retry_rounds = max(1, int(retry_rounds))
        self.total_budget_s = max(0.5, float(total_budget_s))
        self.cooldown_scale = max(0.0, float(cooldown_scale))
        self._pending_sem = (asyncio.Semaphore(max(1, int(max_pending)))
                             if int(max_pending) > 0 else None)
        self._max_pending_value = int(max_pending)
        self._pending_wait_s = 2.0
        # [v4.2] فشل سريع عند موت كل المزوّدين — لا حرق للميزانية انتظارًا
        self.pool_wait_budget_s = max(0.0, float(pool_wait_budget_s))
        # [v4.1 + v4.2] per-provider health state (فترة pacing حسب فئة المزوّد)
        self._pstate: List[Dict[str, Any]] = []
        for p in self.providers:
            pcls = _provider_class(p.get('url', ''))
            floor, cap = _PROVIDER_CLASS_INTERVALS.get(pcls, (0.0, 30.0))
            floor = max(floor, self.min_interval_s)  # المُشغّل يرفع الحد فقط
            self._pstate.append(_new_provider_state(floor, cap))
        self.enabled = bool(self.providers)
        # counters (تشخيص /api/filter_stats)
        self.counters = {
            "calls": 0, "accepts": 0, "rejects": 0, "errors": 0,
            "timeouts": 0, "parse_failures": 0, "rotations": 0,
            "total_latency_ms": 0,
            # v4.1:
            "cooldown_waits": 0,      # مرات الانتظار لانفراج cooldown
            "pace_waits": 0,          # مرات الانتظار pacing (min_interval)
            "budget_exhausted": 0,    # رسائل انتهت ميزانيتها قبل القرار
            "overload_rejects": 0,    # رسائل رُفضت فورًا (max_pending ممتلئ)
            "health_probes": 0,       # محاولات على مزوّد خرج من cooldown
            # v4.2:
            "pool_dead_fasts": 0,     # فشل فوري: كل المزوّدين ميتان (pool-wait انتهى)
            "busy_skips": 0,          # تخطّي مزوّد مشغول (pace>0 داخل القفل) — بلا نداء
            "aimd_grow": 0,          # مرات نمو فترة pacing (بعد 429)
            "aimd_shrink": 0,        # مرات انكماش فترة pacing (بعد نجاح)
            # v4.3.2:
            "dead_key_latches": 0,   # مرات قفل مفتاح ميت (فشل متتالٍ بلا نجاح)
        }

    # --------------------------------------------------------
    # transport: real (aiohttp) or injected
    # --------------------------------------------------------
    async def _get_session(self):
        if self._session is not None and not self._session.closed:
            return self._session
        async with self._session_lock:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=self.timeout_s + 5, connect=10)
                self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _http_call(self, provider: Dict[str, str], payload: Dict[str, Any]) -> Tuple[int, str]:
        """النداء الحقيقي (OpenAI-compatible chat completions)."""
        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {provider['key']}",
            "Content-Type": "application/json",
        }
        async with session.post(provider["url"], json=payload, headers=headers) as resp:
            body = await resp.text()
            return resp.status, body

    async def _call_transport(self, provider: Dict[str, str], payload: Dict[str, Any]) -> Tuple[int, str]:
        if self._transport is not None:
            return await self._transport(provider, payload)
        if not _HAS_AIOHTTP:
            return 0, "aiohttp not installed"
        return await self._http_call(provider, payload)

    def _rotate(self) -> None:
        self.counters["rotations"] += 1
        self._current = (self._current + 1) % len(self.providers)

    # --------------------------------------------------------
    # [v4.1] Provider Health Manager
    # --------------------------------------------------------
    def _pick_provider_locked(self) -> Optional[int]:
        """أول مزوّد جاهز فعلًا (خارج cooldown وخارج pacing) — يقدّم الدوران بعده.

        يستدعى تحت self._lock. [v4.2]: الجاهزية تشمل ready_at (pacing) —
        المهام لا تتراكم على قفل مزوّد لم يحِن وقته بعد؛ مَن ليس جاهزًا
        يُتخطّى لصالح زميل جاهز (توزيع أفضل بين المفاتيح). الميت (403)
        لا يستهلك أي محاولة بعد اكتشافه مرة واحدة.
        """
        n = len(self.providers)
        now = time.monotonic()
        for i in range(n):
            idx = (self._current + i) % n
            st = self._pstate[idx]
            if now >= st['cooldown_until'] and now >= st['ready_at']:
                # probe-count لو خرج لتوه من cooldown (تشخيص)
                if st['cooldown_until'] > 0:
                    self.counters["health_probes"] += 1
                self._current = (idx + 1) % n
                return idx
        return None

    def _soonest_usable_locked(self) -> float:
        """أقرب وقت يصبح فيه أي مزوّد قابلًا للاستخدام (cooldown أو pacing).

        يستدعى تحت self._lock. لا مزوّدين/لا شيء قادم → الآن.
        """
        now = time.monotonic()
        soonest = now
        for st in self._pstate:
            usable = max(st['cooldown_until'], st['ready_at'])
            if usable > now:
                if soonest == now or usable < soonest:
                    soonest = usable
        return soonest

    def _record_failure(self, idx: int, error_str: str, kind: str) -> None:
        """يسجّل فشلًا ويضبط cooldown حسب نوع الخطأ (متضاعف للـauth/rate).

        [v4.2] AIMD: فشل rate (429) يطيل فترة pacing للمزوّد (×1.6 حتى
        cap) — تقارب تدريجي مع الحد الحقيقي للمفتاح بدل عاصفة 429 دائمة.
        """
        st = self._pstate[idx]
        st['fail_count'] += 1
        st['consecutive_fails'] += 1
        st['last_error'] = (error_str or '')[:150]
        st['last_error_at'] = time.time()
        st['last_kind'] = kind
        base = _COOLDOWN_KINDS.get(kind, 30.0)
        if kind in _COOLDOWN_DOUBLING:
            key = 'consecutive_auth_fails' if kind == 'auth' else 'consecutive_rate_fails'
            n = st[key]
            st[key] = n + 1
            base = base * (2 ** n)
            base = min(base, _COOLDOWN_CAPS.get(kind, base))
        cooldown = base * self.cooldown_scale
        st['cooldown_until'] = max(
            st['cooldown_until'], time.monotonic() + cooldown)
        if kind == 'rate':
            # [v4.2] AIMD grow — 429 دليل أن الفترة الحالية أقصر من حدّ المفتاح
            new_iv = min(st['interval_s'] * _AIMD_GROW, st['interval_cap_s'])
            if new_iv > st['interval_s']:
                self.counters["aimd_grow"] += 1
            st['interval_s'] = new_iv
        # [v4.3.2] DEAD-KEY LATCH — فشل متتالٍ بلا نجاح يكافئ مفتاحًا ميتًا:
        # cooldown طويل (30 دقيقة) بدل دورة cap-120s الأبدية. فشل واحد بعد
        # انتهاء الـlatch يُعيده فورًا (consecutive_fails ما زال ≥20) — محاولة
        # استكشاف واحدة كل 30 دقيقة فقط. أول نجاح (_record_success) يُصفّر
        # consecutive_fails فيخرج المفتاح من الـlatch نهائيًا.
        if st['consecutive_fails'] >= _DEAD_KEY_CONSECUTIVE_FAILS:
            st['last_kind'] = 'dead_key'
            st['cooldown_until'] = max(
                st['cooldown_until'],
                time.monotonic() + _DEAD_KEY_COOLDOWN_S * self.cooldown_scale)
            self.counters["dead_key_latches"] += 1
        self._rotate()

    def _record_success(self, idx: int) -> None:
        """النجاح يُصفّر كل حالات الفشل ويرفع cooldown فورًا.

        [v4.2] AIMD shrink: النجاح المستمر يسترد الفترة ببطء (×0.95 حتى
        floor) — استرداد حذر بعد التوسّع.
        """
        st = self._pstate[idx]
        st['success_count'] += 1
        st['consecutive_fails'] = 0
        st['consecutive_auth_fails'] = 0
        st['consecutive_rate_fails'] = 0
        st['cooldown_until'] = 0.0
        st['last_kind'] = ''
        new_iv = max(st['interval_s'] * _AIMD_SHRINK, st['interval_floor_s'])
        if new_iv < st['interval_s']:
            self.counters["aimd_shrink"] += 1
        st['interval_s'] = new_iv

    def provider_health(self) -> List[Dict[str, Any]]:
        """حالة كل مزوّد (للـ/api/filter_stats) — تشخيص بلا runtime logs."""
        now = time.monotonic()
        out: List[Dict[str, Any]] = []
        for i, p in enumerate(self.providers):
            st = self._pstate[i]
            cd = max(0.0, st['cooldown_until'] - now)
            # [v4.3.2] حالة dead_key تظهر صراحةً (تشخيص فوري للمفاتيح الميتة)
            latched = cd > 0 and st.get('last_kind') == 'dead_key'
            out.append({
                'name': p.get('name', ''),
                'model': p.get('model', ''),
                'status': 'dead_key' if latched else ('cooldown' if cd > 0 else 'ok'),
                'cooldown_remaining_s': round(cd, 1),
                'cooldown_kind': st.get('last_kind', '') if cd > 0 else '',
                'consecutive_fails': st['consecutive_fails'],
                'last_error': st['last_error'],
                'last_error_at': st['last_error_at'] or None,
                'success_count': st['success_count'],
                'fail_count': st['fail_count'],
                # [v4.2] AIMD pacing diagnostics:
                'interval_s': round(st['interval_s'], 2),
                'ready_in_s': round(max(0.0, st['ready_at'] - now), 2),
            })
        return out

    # --------------------------------------------------------
    # prompt construction
    # --------------------------------------------------------
    @staticmethod
    def build_user_prompt(clean_text: str, hints: Optional[Dict[str, Any]],
                          context: str = "") -> str:
        """[v4.3] بناء user prompt — hints مع تحذير مشدّد + سياق المجموعة.

        تشخيص الإنتاج: الـhints المعجمية («أحد»/«مين»/«يعلم») كانت تدفع
        النماذج الضعيفة نحو ACCEPT على السوالف — أغلب رسائل المجموعات
        تحتوي هذه الكلمات. التحذير الصريح + الطلب بالاستناد للمعنى فقط
        يقلّل هذا التلوث. اسم المجموعة يُمرّر كسياق ضعيف صريح التوصيف.
        """
        parts = []
        if context:
            parts.append(f"المجموعة المصدر (سياق ضعيف فقط — لا يكفي وحده): {context[:120]}")
            parts.append("")
        if hints:
            parts.append(
                "إشارات معجمية آلية (مطابقات كلمات مفتاحية قديمة — نسبة"
                " خطئها عالية جدًا على سوالف المجموعات؛ لا تُعطها وزنًا يذكر"
                " — القرار من فهم المعنى فقط):"
            )
            parts.append(json.dumps(hints, ensure_ascii=False))
            parts.append("")
        parts.append("الرسالة:")
        parts.append(f'"""{clean_text}"""')
        parts.append("")
        parts.append("صنّفها وأعد الـJSON فقط.")
        return "\n".join(parts)

    def _build_payload(self, clean_text: str, hints: Optional[Dict[str, Any]],
                       context: str = "") -> Dict[str, Any]:
        return {
            "model": "",  # يُملأ لكل provider
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self.build_user_prompt(clean_text, hints, context)},
            ],
            "temperature": 0.0,
            # 400: يسمح بنماذج reasoning (gpt-oss تفكّر قبل النص — 160 كان
            # يخنقها)؛ الناتج الفعلي ~40 tokens (JSON صارم + temperature 0).
            "max_tokens": 400,
            "stream": False,
        }

    # --------------------------------------------------------
    # main entry
    # --------------------------------------------------------
    async def classify(self, text: str, hints: Optional[Dict[str, Any]] = None,
                       context: str = "") -> IntentDecision:
        """يصنّف الرسالة. لا يطبّق عتبة القبول — يُعيد قرار الـAI المقاس.

        v4.1: يعيد المحاولة داخل الجولات ضمن ميزانية زمنية؛ يتخطّى المزوّدين
        في cooldown؛ pacing لكل مفتاح؛ بوابة max_pending للاندفاعات.
        v4.2: AIMD pacing + fail-fast pool-wait + لا نوم داخل قفل المزوّد.
        v4.3: context = اسم المجموعة المصدر (سياق ضعيف يُمرّر للـprompt).
        الفشل النهائي = REJECT صارم مع error تفصيلي — لا keyword fallback.
        """
        if not self.enabled:
            return IntentDecision(ok=False, decision="REJECT", confidence=0.0,
                                  category="ai_unavailable", reason="ai_unavailable",
                                  error="no providers configured")
        if not text or not text.strip():
            return IntentDecision(ok=False, decision="REJECT", confidence=0.0,
                                  category="empty", reason="empty", error="empty text")

        clean = text.strip()[: self.max_chars]
        payload = self._build_payload(clean, hints, context)
        attempts_per_round = max(1, min(self.max_attempts, len(self.providers)))
        max_total_attempts = attempts_per_round * self.retry_rounds
        deadline = time.monotonic() + self.total_budget_s
        last_error = ""
        total_attempts = 0
        # [v4.2] ميزانية انتظار مخصّصة لموت المجمّع كله — لو انتهت فشل فوري
        # بدل حرق كامل الميزانية انتظارًا (كان يصنع زمن قرار 50s+ وتراكمًا).
        pool_waited = 0.0

        # [v4.1] بوابة الاندفاعة: عدد محدود ينتظر داخل classify — الفائض
        # يُرفض فورًا (أمان: REJECT وليس تراكمًا لا نهائيًا للمهام).
        acquired = False
        if self._pending_sem is not None:
            try:
                await asyncio.wait_for(self._pending_sem.acquire(),
                                       timeout=self._pending_wait_s)
                acquired = True
            except asyncio.TimeoutError:
                self.counters["overload_rejects"] += 1
                return IntentDecision(
                    ok=False, decision="REJECT", confidence=0.0,
                    category="overloaded", reason="overloaded",
                    error=(f"overloaded: too many concurrent classifications "
                           f"(max_pending={self._max_pending_value}) — "
                           f"safe REJECT, no keyword fallback"),
                )
        try:
            while True:
                now = time.monotonic()
                if total_attempts >= max_total_attempts:
                    break
                remaining = deadline - now
                if remaining <= 0:
                    if total_attempts > 0:
                        self.counters["budget_exhausted"] += 1
                    break

                # [v4.2] اختيار مزوّد جاهز فعلًا (خارج cooldown وخارج pacing)
                wait_s = 0.0
                async with self._lock:
                    idx = self._pick_provider_locked()
                    if idx is None:
                        wait_s = self._soonest_usable_locked() - time.monotonic()
                if idx is None:
                    # كل المزوّدين غير متاحين (cooldown أو pacing) —
                    # [v4.2] fail-fast: انتظار محدود pool_wait_budget فقط.
                    if pool_waited >= self.pool_wait_budget_s:
                        self.counters["pool_dead_fasts"] += 1
                        last_error = (f"all providers unavailable "
                                      f"(cooldown/pacing) — fail-fast after "
                                      f"{pool_waited:.1f}s pool-wait "
                                      f"(budget {self.total_budget_s}s)")
                        break
                    wait_s = min(wait_s + 0.05, 2.5,
                                 max(0.05, deadline - time.monotonic()))
                    pool_waited += max(0.05, wait_s)
                    self.counters["cooldown_waits"] += 1
                    await asyncio.sleep(max(0.05, wait_s))
                    continue

                provider = self.providers[idx]
                pstate = self._pstate[idx]
                payload["model"] = provider["model"]
                t0 = time.monotonic()
                status, body = -1, ""
                skipped_busy = False
                pace = 0.0
                try:
                    async with pstate['lock']:
                        # [v4.2] لا ننام داخل قفل المزوّد أبدًا: لو صار مشغولًا
                        # (pace>0 — التقطه مهمام أخرى قبلنا) نحرّر القفل ونتخطّى
                        # بلا نداء — إعادة الاختيار توزّعنا على مفتاح آخر.
                        pace = pstate['ready_at'] - time.monotonic()
                        if pace > 0.15:
                            skipped_busy = True
                        else:
                            if pace > 0:
                                self.counters["pace_waits"] += 1
                            self.counters["calls"] += 1
                            total_attempts += 1
                            status, body = await asyncio.wait_for(
                                self._call_transport(provider, payload),
                                timeout=self.timeout_s,
                            )
                            # [v4.2] AIMD: الفترة الحالية لهذا المزوّد (ليست
                            # min_interval الثابت) — تتكيّف مع 429/النجاح.
                            pstate['ready_at'] = (time.monotonic()
                                                  + pstate['interval_s'])
                except asyncio.TimeoutError:
                    self.counters["timeouts"] += 1
                    last_error = f"timeout after {self.timeout_s}s ({provider['name']})"
                    self._record_failure(idx, last_error, kind='timeout')
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.counters["errors"] += 1
                    last_error = f"{type(e).__name__}: {e} ({provider['name']})"
                    self._record_failure(idx, last_error, kind='network')
                    continue
                if skipped_busy:
                    self.counters["busy_skips"] += 1
                    await asyncio.sleep(min(
                        max(0.05, pace), 2.0,
                        max(0.0, deadline - time.monotonic())))
                    continue  # لا تُحسب محاولة — أعِد الاختيار
                latency = int((time.monotonic() - t0) * 1000)
                self.counters["total_latency_ms"] += latency

                if status == 429:
                    self.counters["errors"] += 1
                    last_error = f"http 429 rate limit ({provider['name']})"
                    self._record_failure(idx, last_error, kind='rate')
                    continue
                if status in (401, 403):
                    self.counters["errors"] += 1
                    last_error = f"http {status} auth/dead key ({provider['name']})"
                    self._record_failure(idx, last_error, kind='auth')
                    continue
                if status == 404:
                    # [v4.1.1] 404 = endpoint/نموذج غير موجود (مُوقوف من المزوّد —
                    # llama-3.3-70b-versatile أُوقف من Groq) — خطأ دائم لهذا
                    # الـconfig: cooldown طويل متضاعف (30 دقيقة+) بدل 30s.
                    self.counters["errors"] += 1
                    last_error = f"http 404 model/endpoint gone ({provider['name']})"
                    self._record_failure(idx, last_error, kind='auth')
                    continue
                if status != 200:
                    self.counters["errors"] += 1
                    last_error = f"http {status} ({provider['name']})"
                    kind = 'server' if status >= 500 else 'network'
                    self._record_failure(idx, last_error, kind=kind)
                    continue

                content = self._extract_content(body)
                if content is None:
                    self.counters["parse_failures"] += 1
                    last_error = f"no content in response ({provider['name']})"
                    self._record_failure(idx, last_error, kind='parse')
                    continue

                decision = self._parse_decision(content, provider, latency)
                if decision is not None:
                    self._record_success(idx)
                    if decision.decision == "ACCEPT":
                        self.counters["accepts"] += 1
                    else:
                        self.counters["rejects"] += 1
                    return decision

                last_error = f"invalid JSON output ({provider['name']})"
                self._record_failure(idx, last_error, kind='parse')
                continue

            # كل المحاولات/الميزانية فشلت → REJECT صارم (لا keyword fallback أبدًا)
            self.counters["errors"] += 1
            if not last_error:
                last_error = (f"all attempts failed "
                              f"({total_attempts}/{max_total_attempts} attempts, "
                              f"budget {self.total_budget_s}s)")
            else:
                last_error = (f"{last_error} "
                              f"[attempts {total_attempts}/{max_total_attempts}, "
                              f"budget {self.total_budget_s}s]")
            return IntentDecision(
                ok=False, decision="REJECT", confidence=0.0,
                category="ai_error", reason="ai_error",
                provider_name="", latency_ms=0,
                error=last_error,
            )
        finally:
            if acquired:
                self._pending_sem.release()

    # --------------------------------------------------------
    # response parsing
    # --------------------------------------------------------
    @staticmethod
    def _extract_content(body: str) -> Optional[str]:
        """يستخرج message.content من رد OpenAI-compatible."""
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            # بعض المزوّدين يضعون النص في choices[0].text
            text_alt = choices[0].get("text")
            if isinstance(text_alt, str) and text_alt.strip():
                return text_alt
            return None
        return content

    def _parse_decision(self, content: str, provider: Dict[str, str], latency_ms: int) -> Optional[IntentDecision]:
        js = extract_json_text(content)
        if not js:
            self.counters["parse_failures"] += 1
            return None
        try:
            parsed = json.loads(js)
        except (json.JSONDecodeError, TypeError):
            self.counters["parse_failures"] += 1
            return None
        clean = validate_ai_output(parsed)
        if clean is None:
            self.counters["parse_failures"] += 1
            return None
        return IntentDecision(
            ok=True,
            decision=clean["decision"],
            confidence=clean["confidence"],
            category=clean["category"],
            reason=clean["reason"],
            model=provider.get("model", ""),
            provider_name=provider.get("name", ""),
            latency_ms=latency_ms,
            raw_output=content[:500],
        )

    # --------------------------------------------------------
    # stats / cleanup
    # --------------------------------------------------------
    def stats(self) -> dict:
        calls = max(1, self.counters["calls"])
        out = {
            "enabled": self.enabled,
            "providers": len(self.providers),
            "timeout_s": self.timeout_s,
            "max_attempts": self.max_attempts,
            "max_concurrent": self.max_concurrent,
            # v4.1 knobs:
            "retry_rounds": self.retry_rounds,
            "total_budget_s": self.total_budget_s,
            "min_interval_s": self.min_interval_s,
            "max_pending": self._max_pending_value,
            # v4.2 knobs:
            "pool_wait_budget_s": self.pool_wait_budget_s,
            **dict(self.counters),
            "avg_latency_ms": round(self.counters["total_latency_ms"] / calls, 1),
        }
        return out

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
        self._session = None
