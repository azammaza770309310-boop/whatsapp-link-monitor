#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rule_intent_classifier.py — محرّك نوايا قواعدي حتمي (بديل AI) v4.4.0
================================================================================
[AI-OFF-v4.4.0] طلب المُشغّل (2026-09-04): إلغاء الذكاء الاصطناعي من تصنيف
الطلبات وإرجاع الفلتر — «النتيجة أقوى من الذكاء الاصطناعي».

لماذا أقوى من الـAI على هذا النطاق تحديدًا:
  1. صفر نداءات شبكة → صفر 429/مفاتيح ميتة/overloaded/50s قرار — كل عيوب
     الإنتاج الموثقة (RC-A..RC-C) تختفي من الجذر.
  2. زمن قرار ~0ms (كان 440-780ms أفضل حالة، 50s أسوأ حالة).
  3. حتمية كاملة: نفس الرسالة = نفس القرار دائمًا — الـAI كان يقبل
     «ياااعالم ابي احد يسوي سكليف» ويرفض «احد يسوي سكليف ؟» (تناقض
     موثق في تشخيص v4.3.9). القواعد لا تتناقض أبدًا.
  4. كل قاعدة معايرة على أمثلة إنتاج فعلية (قائمة المُشغّل ال18 الحقيقية
     + 20+ حالة رفض من قناة الإنتاج) — نفس عينات معايرة الـprompt
     السابق، لكنها مطبَّقة حرفيًا لا «مفهومة» بنموذج متقلب.
  5. صفر تكلفة باندودث صاعد (كانت كل نداءات AI egress على Render —
     أحد مسببات استهلاك الـ5GB).

الواجهة: مطابقة تمامًا لـ IntentClassifier.classify() — يُستبدل المُصنِّف
في bot.py بلا أي تغيير في analyze_request_v4 أو البوابات أو الاختبارات
الموجودة (transport injection tests تستهدف IntentClassifier نفسه — لم يُمس).

المعمارية — شلال قرارات حتمي (الترتيب مقصود ويحاكي عقد الـprompt السابق):
  1. تبييض/توحيد النص (همزات/تاء مربوطة/تشكيل/تطويل)
  2. REJECT service_offer/advertisement — المرسل يعرض خدمة (نقدم/برسوم/...)
  3. REJECT tutoring_only_request — فعل تعليم (يشرح/خصوصي/مدرس/...)
  4. REJECT non_academic_request — أكواد ألعاب/كوبونات/تفعيلات
  5. ACCEPT (شرط ثلاثي): فعل تنفيذ (يـ صيغة: يسوي/يحل/يكتب/...)
     + (طالب: احد/مين/من/اللي | رغبة: ابي/ابغا/اريد/محتاج)
     + (مفعول: سكليف/بحث/كويز/cv/جدول/عذر | مفعولية: لي/بدالي/رقم احد)
     → الفئة من المفعول (أكاديمي/خدمة طلابية)
  6. REJECT resource_request — ملكية جاهزة بلا فعل تنفيذ
     (عنده كويزات ≠ احد يحل كويزات)
  7. REJECT registration_admin — الشعب/التسجيل/نزول الجدول
  8. REJECT teacher_review_inquiry — استطلاع دكاترة
  9. REJECT non_request_question / recommendation_or_opinion
 10. REJECT other — بلا عمل محدد (الصيغة المبتورة الحقيقية)

ملاحظة: الصيغة الخليجية المختصرة «مين يسوي X؟» ليست «مبتورة» — قاعدة
الخطوة 5 تقبلها بـ«لي» مفهومة من السياق (قاعدة v4.3.9 حرفيًا).

إعادة تمكين الـAI (لو طُلب مستقبلًا): REQUEST_AI_ENABLED=true في بيئة
Render — يرجع IntentClassifier كما كان بلا أي تعديل كود.
"""

import re
import time
from typing import Any, Dict, Optional

from intent_classifier import IntentDecision

__all__ = ["RuleBasedIntentClassifier", "RULE_ENGINE_VERSION"]

RULE_ENGINE_VERSION = "rule-v4.4.0"


# ============================================================
# توحيد النص العربي (فك التشكيل/التطويل + توحيد الحروف)
# ============================================================
_DIACRITICS_RE = re.compile(r'[\u064B-\u065F\u0670\u0640]')  # تشكيل + تطويل

_ARABIC_NORMALIZE = {
    'أ': 'ا', 'إ': 'ا', 'آ': 'ا',   # همزات الألف
    'ى': 'ي', 'ئ': 'ي',
    'ؤ': 'و',
    'ة': 'ه',                       # تاء مربوطة (سيره = سيرة)
    'ٱ': 'ا',
}


def normalize_ar(text: str) -> str:
    """توحيد النص: بلا تشكيل/تطويل، همزات→ا، ى→ي، ة→ه، صغير."""
    if not text:
        return ""
    t = _DIACRITICS_RE.sub('', text)
    for src, dst in _ARABIC_NORMALIZE.items():
        t = t.replace(src, dst)
    # الإنجليزي صغير (CV → cv)
    return t.lower()


def _has_word(text: str, word: str) -> bool:
    """هل الكلمة موجودة ككلمة مستقلة (word boundary)؟

    \b في Python3 يعمل مع العربية (الحروف العربية \\w افتراضًا) —
    «لي» لا تلتقط «اللي» و«ليش»، و«من» لا تلتقط «منه»."""
    return re.search(r'\b' + re.escape(word) + r'\b', text) is not None


def _has_any(text: str, patterns) -> Optional[str]:
    """أول نمط متطابق من القائمة (كل عنصر: كلمة أو regex)."""
    for p in patterns:
        if p.startswith('~'):  # '~' prefix = regex خام
            if re.search(p[1:], text):
                return p
        elif _has_word(text, p):
            return p
    return None


def _has_sub(text: str, words) -> Optional[str]:
    """أول كلمة موجودة كـsubstring (للأفعال المتصلة: يسويه/يحلها)."""
    for w in words:
        if w in text:
            return w
    return None


# ============================================================
# المعجم — معاير على أمثلة الإنتاج وقائمة المُشغّل ال18
# ============================================================

# أفعال التنفيذ (صيغة الغائب يـ) — تلتقط المتصل: يسوي/يسويه/يسويها
EXEC_VERBS = (
    'يسوي', 'يسوون',        # do/make (خليجي)
    'يحل',                    # solve — يلتقط يحله/يحلها/يحلون/يحليها
    'يكتب', 'يكتبو',
    'يخلص', 'يخلس',         # finish/complete
    'ينجز',
    'يعمل', 'يعملو',
    'يرتب',
    'يصمم',
    'يبني',
    'يسلم', 'يسلمها',
    'ينفذ',
    'يعدل', 'يصحح',          # تعديل/تصحيح نيابة عن المرسل
    'يلخص', 'يترجم',         # تلخيص/ترجمة نيابة عن المرسل
)

# طالب الخدمة (استفهام/مجهول) + رغبة المتكلم
SEEKER_WORDS = (
    'احد', 'احدي', 'حدا', 'احدي',
    'مين', 'من', 'مينو', 'منو',
    'الي', 'اللي', 'الي ',
    'شخص', 'واحد', 'حد',
)
WANT_WORDS = (
    'ابي', 'اب', 'ابغي', 'ابغا', 'ابغي',
    'اريد', 'اريد', 'ودي', 'بغيت', 'بغي',
    'محتاج', 'احتاج', 'حتاج',
)

# مفعول أكاديمي (homework_execution_request)
HOMEWORK_NOUNS = (
    'واجب', 'واجبات', 'واجبه', 'واجباتي',
    'سكليف', 'سكاليف', 'تكليف', 'تكاليف', 'تكليفات',
    'بحث', 'بحوث', 'بحثي', 'البحوث',
    'تقرير', 'تقارير', 'التقرير',
    'مشروع', 'المشروع', 'مشاريع',
    'كويز', 'كويزات', 'الكويز', 'الكويزات',
    'اختبار', 'اختبارات', 'الاختبار',
    'اسايمنت', 'اساينمنت', 'اساينمنت', 'assignment', 'assignments',
    'هومورك', 'homework',
    'سليد', 'سلايد', 'سلايدات', 'slides', 'slide',
    'عرض', 'العرض', 'بوربوينت', 'برزنتيشن', 'برزنت', 'presentation',
    'اوراق', 'ورقة عمل', 'ورقه',
    'حلول', 'الحلول',
)

# مفعول خدمة طلابية (student_service_execution_request)
SERVICE_NOUNS = (
    'سيرة', 'سيره', 'سيرتي', 'السيرة', 'السيره',
    'سيفيات', 'سيفيه', 'سي في', 'cv', 'c.v',
    'ats', 'a.t.s',
    'جدول', 'جداول', 'الجدول', 'الجداول', 'جدولي',
    'عذر', 'اعذار', 'العذر', 'الاعذار', 'اعذاري',
    'فيديو', 'فيديوهات', 'فيداو', 'الفيديو',
    'مونتاج', 'تصميم', 'تصاميم', 'التصميم',
    'ذكاء اصطناعي', 'بوستر', 'بروشور', 'لوقو', 'logo',
)

# مفعولية/تفويض صريح — «لي» ككلمة مستقلة (لا «اللي»/«ليش»)
DELEGATION_MARKERS = (
    'لي', 'ليه', 'بدالي', 'بدلا', 'عني', 'نيابة',
    'معي',   # «يتواصل معي» / «اللي فاهم يتواصل معي»
    'رقم',   # «من عنده رقم واحد يسوي سيفيات» — طلب الوصول لمنفّذ
)

# أفعال تعليم → REJECT tutoring_only_request
TUTORING_MARKERS = (
    'يشرح', 'يشرحو', 'يشرحوها', 'شرحو', 'يشرحها',
    'خصوصي', 'خصوصيه', 'الخصوصي',
    'مدرس', 'مدرسه', 'المدرس', 'مدرسين',
    'معلم', 'معلمه', 'المعلم',
    'يعلم', 'يعلمنا', 'يعلمهم',
    'يراجع', 'مراجعه', 'مراجعة',
    'تدريس', 'تدريسي', 'دروس', 'درس خصوصي',
    'محفظ', 'محفظه',
    '~\\bيدرس\\b',
)

# عرض خدمة/إعلان → REJECT service_offer / advertisement
PROVIDER_MARKERS = (
    'نقدم', 'نوفر', 'نقدمها', 'نقدمه',
    'خدماتنا', 'خدمتنا',
    'لدينا', 'لديكم',
    'برسوم', 'رسوم', 'باسعار', 'اسعار', 'أسعار', 'رمزيه', 'رمزية',
    'مكتب', 'مركز يقدم', 'شركه تقدم', 'شركة تقدم',
    'عندي دكتور', 'عندنا دكتور', 'عندي مدرس', 'عندنا مدرس',
    'عندي شخص يساعد', 'عندنا شخص يساعد',
    'نسوي', 'نسويها', 'نسويهم',       # نحن نسوي = نحن نقدّم
    'اسوي لكم', 'اسويلكم', 'نحل لكم', 'نحللكم',
    'للتواصل واتساب', 'واتساب للتواصل', 'رقم واتساب',
    'اربح', 'تداول', 'استثمار', 'اكتتابات', 'بيتكوين',
    'كورسات مدفوعه', 'كورسات مدفوعة',
)

# ألعاب/أكواد → REJECT non_academic_request
NON_ACADEMIC_MARKERS = (
    'اكواد', 'اكوادك', 'كود', 'كوبون', 'كوبونات', 'كوبونات',
    'تفعيل', 'تفعيلات', 'جواهر', 'شخصيات',
    'ببجي', 'فورت', 'فري فاير', 'ونس', 'اوتار',
    'نت فلكس', 'نتفلكس', 'سبوتيفاي',
)

# ملكية جاهزة → REJECT resource_request (فقط بلا فعل تنفيذ)
RESOURCE_OWNERSHIP = (
    'عنده', 'عندي', 'عندك', 'عندهم', 'عندنا',
    'عطوني', 'عطونا', 'عطيك', 'يعطينا', 'عطني',
    'يوزع', 'يوزعون', 'ينزل لنا', 'ينزلها',
)
RESOURCE_NOUNS = (
    'كويزات', 'كويز', 'ملفات', 'ملف', 'ملخصات', 'ملخص', 'ملخصاتهم',
    'اسئله', 'اسيله', 'اسئلة', 'اسئله',   # أسئلة بعد التوحيد: اسيله
    'كتب', 'الكتب', 'كتاب',
    'مواد', 'المواد', 'سلايدات', 'سلايد', 'داتا', 'ملازم',
    'حلول جاهزه', 'حلول جاهزة', 'شيتات', 'شيت',
)

# إدارة/تسجيل → REJECT registration_admin
ADMIN_MARKERS = (
    'التسجيل', 'تسجيل', 'يسجل', 'تسجل',
    'شعبه', 'شعبة', 'شعبتي', 'شعب', 'الشعب',
    r'~نزل[\w\s]{0,20}الجدول',   # «نزل له الجدول بالتحضيريه»
    r'~الجدول[\w\s]{0,20}نزل',
    'نزول الجدول',
    'يفتح', 'يفتحون', 'الفصل يفتح',
    'تحويل', 'تحويلي', 'معامل تحويل',
    'السحب', 'سحب',
)

# استطلاع دكاترة → REJECT teacher_review_inquiry
TEACHER_REVIEW_MARKERS = (
    'قد درس', 'درس عنده', 'درس عندها', 'درست عنده', 'درست عندها',
    'درس معه', 'درس معها', 'تعامل معه', 'تعامل معها',
    r'~دكتور\w*.{0,25}كيف',   # «دكتوره علا كيف»
    r'~كيف.{0,25}دكتور',
    'وش رايكم فيه', 'وش رايكم فيها', 'وش رايك فيه',
)

# رأي/توصية → REJECT recommendation_or_opinion
OPINION_MARKERS = (
    'افضل مدرس', 'احسن مدرس', 'افضل دكتور', 'احسن دكتور', 'افضل معلم',
    'افضل طريقه', 'احسن طريقه', 'افضل طريقه',
    'تنصحون', 'تنصحني', 'تنصح', 'نصيحه', 'نصيحة',
    'رايكم', 'رأيكم', 'رايك', 'رأيك',
)

# سؤال معرفي/طريقة → REJECT non_request_question
METHOD_QUESTION_MARKERS = (
    'كيف اذاكر', 'كيف ادرس', 'كيف احل', 'كيف اسوي', 'كيف افهم',
    'وش الطريقه', 'وش طريقه', 'ايش الطريقه',
    'كم نسبه', 'كم درجه', 'كم ساعه', 'كم قسم',
    'هل الاختبار', 'هل الامتحان',
)

# مدح/تجربة → REJECT praise_testimonial
PRAISE_MARKERS = (
    'شكرا', 'مشكور', 'جبت', 'درجه عاليه',
    'ما شاء الله', 'ماشاء الله', 'انصح بالمنصه', 'انصح بالمنصة',
)

# سوالف عامة → other
GENERAL_CHATTER = (
    'شكرا', 'مشكور', 'جزاكم الله', 'جزاك الله',
    'صباح الخير', 'مساء الخير', 'السلام عليكم', 'وعليكم',
    'هههه', '😂', '😭',
)


# ============================================================
# المصنّف القواعدي
# ============================================================
class RuleBasedIntentClassifier:
    """مصنّف نوايا حتمي — نفس عقد IntentClassifier.classify().

    يُعيد IntentDecision(ok=True, ...) فورًا — لا شبكة، لا انتظار،
    لا عتبة غامضة: القواعد تنتج الثقة مباشرة (0.9+ للقبول الحاسم).
    """

    def __init__(self, **kwargs):
        # kwargs تُقبَل وتُتجاهل — توافق مع توقيع IntentClassifier
        # (timeout_s/ max_attempts/ ... تُهمل: لا معنى لها هنا).
        self.enabled = True
        self.version = RULE_ENGINE_VERSION
        # [STATS-CONTRACT-v4.4.1] عدّادات حية — عقد stats() الذي تقرأه
        # /api/filter_stats (كان خاصًا بـIntentClassifier؛ إغفاله عند
        # الاستبدال كسر اللوحة بـ500: 'RuleBasedIntentClassifier' object
        # has no attribute 'stats'). تُحدَّث في classify() (المسار الإنتاجي).
        self.counters: Dict[str, int] = {
            "calls": 0, "accepts": 0, "rejects": 0,
            "errors": 0, "timeouts": 0, "parse_failures": 0,
            "rotations": 0, "total_latency_ms": 0,
        }
        self._by_category: Dict[str, int] = {}

    # --------------------------------------------------------
    # الواجهة الرئيسية (async — نفس توقيع IntentClassifier)
    # --------------------------------------------------------
    async def classify(self, text: str, hints: Optional[Dict[str, Any]] = None,
                       context: str = "") -> IntentDecision:
        """تصنيف فوري حتمي. hints/context تُهمل عمدًا (راجع قاعدة 8
        في الـprompt السابق: الإشارات المعجمية نسبة خطئها عالية —
        القرار من بنية الرسالة نفسها)."""
        t0 = time.monotonic()
        decision = self.classify_sync(text)
        decision.latency_ms = int((time.monotonic() - t0) * 1000)
        # [STATS-CONTRACT-v4.4.1] عدّادات حية — تقرأها /api/filter_stats
        self.counters["calls"] += 1
        self.counters["total_latency_ms"] += decision.latency_ms
        if decision.ok:
            if decision.decision == "ACCEPT":
                self.counters["accepts"] += 1
            else:
                self.counters["rejects"] += 1
        else:
            self.counters["errors"] += 1
        _cat = decision.category or "unknown"
        self._by_category[_cat] = self._by_category.get(_cat, 0) + 1
        return decision

    # --------------------------------------------------------
    # المنطق الحتمي (متزامن — للاختبارات المباشرة)
    # --------------------------------------------------------
    def classify_sync(self, text: str) -> IntentDecision:
        if not text or not text.strip():
            return IntentDecision(
                ok=True, decision="REJECT", confidence=0.0,
                category="empty", reason="نص فارغ",
                model=RULE_ENGINE_VERSION, provider_name="rules")

        t = normalize_ar(text)
        # إزالة الإيموجي الشائع كضجيج (الرموز خارج \\w العربية)
        t_words_only = re.sub(r'[^\w\s]', ' ', t)
        t = re.sub(r'\s+', ' ', t).strip()
        t_words_only = re.sub(r'\s+', ' ', t_words_only).strip()

        def _dec(decision, conf, cat, reason):
            return IntentDecision(
                ok=True, decision=decision, confidence=conf,
                category=cat, reason=reason,
                model=RULE_ENGINE_VERSION, provider_name="rules")

        # ---- 1) REJECT: المرسل يعرض خدمة (مقدم، لا طالب) ----
        m = _has_sub(t, PROVIDER_MARKERS)
        if m:
            return _dec("REJECT", 0.96, "service_offer",
                        f"المرسل يعرض خدمة ({m}) — ليس طالبًا")

        # ---- 2) REJECT: ملكية جاهزة بلا فعل تنفيذ ----
        # (قبل التدريس: «احد عنده كويزات لدروس الكمي» = resource لا tutoring
        #  — الفئة الصحيحة حسب عقد التصنيف. «من عنده رقم احد يسوي» فيه فعل
        #  تنفيذ → يتجاوز لخطوة القبول.)
        _own = _has_sub(t, RESOURCE_OWNERSHIP)
        _res = _has_sub(t, RESOURCE_NOUNS)
        _exec_v = _has_sub(t, EXEC_VERBS)
        if _own and _res and not _exec_v:
            return _dec("REJECT", 0.95, "resource_request",
                        "طلب ملفات/مواد جاهزة — لا تنفيذ عمل")

        # ---- 2) REJECT: رأي/توصية (قبل التدريس: «أفضل مدرس» رأي لا تدريس) ----
        m = _has_any(t, OPINION_MARKERS)
        if m:
            return _dec("REJECT", 0.92, "recommendation_or_opinion",
                        "طلب رأي/توصية عامة")

        # ---- 3) REJECT: تدريس/شرح (فعل تعليم لا تنفيذ) ----
        m = _has_any(t, TUTORING_MARKERS)
        if m:
            return _dec("REJECT", 0.95, "tutoring_only_request",
                        f"طلب تعليم/شرح ({m}) — الطالب ينفذ بنفسه")

        # ---- 4) REJECT: أكواد ألعاب/كوبونات/تفعيلات ----
        m = _has_sub(t, NON_ACADEMIC_MARKERS)
        if m:
            return _dec("REJECT", 0.95, "non_academic_request",
                        f"طلب غير أكاديمي ({m})")

        # ---- 5) ACCEPT الحاسم: فعل تنفيذ + طالب/رغبة + مفعول ----
        exec_v = _exec_v
        if exec_v:
            seeker = _has_any(t, SEEKER_WORDS)
            want = _has_any(t, WANT_WORDS)
            hw_noun = _has_sub(t, HOMEWORK_NOUNS)
            svc_noun = _has_sub(t, SERVICE_NOUNS)
            # مفعولية: «لي» مستقلة أو بدالي/رقم احد (تتحقق على الكلمات فقط
            # حتى لا تلتقط «اللي» و«ليش»)
            deleg = _has_word(t_words_only, 'لي') or \
                _has_any(t, ('بدالي', 'بدلا', 'نيابة', 'عني', 'رقم', 'معي'))

            if (seeker or want) and (hw_noun or svc_noun or deleg):
                if hw_noun:
                    return _dec("ACCEPT", 0.96, "homework_execution_request",
                                "طلب تنفيذ عمل أكاديمي بدلاً عن المرسل")
                if svc_noun:
                    return _dec("ACCEPT", 0.95, "student_service_execution_request",
                                "طلب تنفيذ خدمة طلابية بدلاً عن المرسل")
                # مفعولية بلا اسم مفعول («من عنده رقم احد يسويها؟»)
                return _dec("ACCEPT", 0.93, "student_service_execution_request",
                            "طلب الوصول لمن ينفّذ العمل بدلاً عن المرسل")

        # ---- 6) REJECT: إداري/تسجيل/شعب ----
        m = _has_any(t, ADMIN_MARKERS)
        if m:
            return _dec("REJECT", 0.93, "registration_admin",
                        f"سؤال إداري ({m}) — ليس طلب تنفيذ")

        # ---- 7) REJECT: استطلاع دكاترة ----
        m = _has_any(t, TEACHER_REVIEW_MARKERS)
        if m:
            return _dec("REJECT", 0.93, "teacher_review_inquiry",
                        "استطلاع رأي/تجربة مع مدرّس")

        # ---- 8) REJECT: سؤال معرفي/طريقة ----
        m = _has_any(t, METHOD_QUESTION_MARKERS)
        if m:
            return _dec("REJECT", 0.92, "non_request_question",
                        "سؤال معلوماتي/طلبي معرفة — لا طلب تنفيذ")

        # ---- 9) REJECT: مدح/تجربة ----
        m = _has_sub(t, PRAISE_MARKERS)
        if m:
            return _dec("REJECT", 0.94, "praise_testimonial",
                        "مدح/شكر/تجربة شخصية — ليس طلبًا")

        # ---- 10) REJECT: بلا عمل محدد ----
        return _dec("REJECT", 0.85, "other",
                    "بلا دليل صريح على طلب تنفيذ بدلاً عن المرسل")

    # --------------------------------------------------------
    # توافق أسماء مستخدمة في اللوحات (provider_health وما شابه)
    # --------------------------------------------------------
    def provider_health(self):
        return [{
            "provider": "rules",
            "status": "healthy",
            "version": RULE_ENGINE_VERSION,
            "note": "deterministic — no network calls",
        }]

    # --------------------------------------------------------
    # [STATS-CONTRACT-v4.4.1] تكملة عقد IntentClassifier الذي يستدعيه
    # bot.py: stats() تقرأها /api/filter_stats (سطر classifier.stats())،
    # وclose() يستدعيه إيقاف bot.py (aiohttp session للـAI — هنا لا
    # موارد فلا-op، لكن الدالة موجودة كي لا يُبتلع AttributeError
    # عند الإيقاف). أي استبدال مستقبلي للمصنّف يجب أن يحقق العقد نفسه —
    # يفرضه tests/test_rule_intent_classifier.py قسم F.
    # --------------------------------------------------------
    def stats(self) -> dict:
        calls = max(1, self.counters["calls"])
        return {
            "enabled": self.enabled,
            "engine": RULE_ENGINE_VERSION,
            "providers": 1,               # المحرك الحتمي نفسه (طول provider_health)
            "timeout_s": 0,               # لا شبكة — لا مهلة
            "max_attempts": 1,            # قرار واحد حتمي — لا إعادة محاولة
            "max_concurrent": 0,          # لا I/O — لا معنى له
            "retry_rounds": 1,
            "total_budget_s": 0,
            "min_interval_s": 0,
            "max_pending": 0,
            "pool_wait_budget_s": 0,
            # knobs محايدة (لا معنى لها بلا شبكة — تُعرض أصفارًا):
            "cooldown_waits": 0,
            "pace_waits": 0,
            "budget_exhausted": 0,
            "overload_rejects": 0,
            "health_probes": 0,
            "pool_dead_fasts": 0,
            "busy_skips": 0,
            "aimd_grow": 0,
            "aimd_shrink": 0,
            "dead_key_latches": 0,
            **dict(self.counters),
            "avg_latency_ms": round(self.counters["total_latency_ms"] / calls, 1),
            "by_category": dict(self._by_category),
        }

    async def close(self) -> None:
        """no-op — المحرك لا يملك موارد (عقد IntentClassifier.close)."""
        return None
