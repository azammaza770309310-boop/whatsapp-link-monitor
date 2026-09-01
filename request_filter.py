#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
request_filter.py — Request Intent Engine v4.0 (AI-First)
============================================================
إعادة بناء جذرية وفق طلب المُشغّل (v4.0 rebuild): القرار الأساسي من
مصنّف AI (LLM) — وليس من الكلمات المفتاحية أو Hard Gates.

المعمارية (المراحل):
  المرحلة 1  text_normalizer.normalize — تنظيف أولي (إيموجي/روابط/توقيعات/
             تكرار) مع الاحتفاظ بالسياق + canonical للـhashing.
  المرحلة 2  تصنيف النية: فئات ACCEPT (tutoring_request,
             homework_execution_request) مقابل 8 فئات REJECT (إعلانات،
             عروض خدمات، مدح، ديني/عام، أسئلة غير طلب، توصيات، نقاش، أخرى).
  المرحلة 3  intent_classifier.IntentClassifier — نداء LLM (نفس مزوّدي
             AIAnalyzer: Groq/OpenAI-compat/Gemini-compat) بعقد JSON صارم:
             {decision, confidence, category, reason}.
             ACCEPT فقط إذا confidence >= 0.85 (REQUEST_FILTER_AI_THRESHOLD).
             فشل AI (لا مفاتيح/timeout/parse) = REJECT صارم — لا keyword
             fallback أبدًا.
  المرحلة 4  semantic_dedup.SemanticDeduper — منع التكرار الدلالي
             (exact + semantic-hash + Jaccard near-dup) خلال TTL قصير.
  المرحلة 5  filter_store.DecisionLogger — جدول filter_decisions يحفظ كل
             قرار (id, message_id, text_hash, decision, confidence,
             category, reason, timestamp + تشخيصات) — لماذا قُبل/رُفض.
  المرحلة 6  /api/filter_stats (bot.py) — آخر 100 قرار + إحصاءات.

دور الكلمات المفتاحية في v4.0: demoted إلى extract_signals() — تُنتج
إشارات (hints) تُمرَّر للـAI كاستدلال مساعد ضعيف فقط + تشخيصات تُسجَّل.
لا يوجد أي مسار يحوّل الكلمات إلى قرار قبول/رفض.

بوابات هيكلية (ليست تصنيفًا لغويًا): empty / relay-bot wrapper (نسخة
مكررة من بوت ناقل). dedup دلالي قبل نداء AI (توفير تكلفة).

الواجهة الأساسية: async analyze_request_v4(text, classifier, ...) →
RequestAnalysis (is_request = AI-ACCEPT + confidence ≥ threshold).

توافق خلفي: analyze_request (sync) أصبح signals-only بلا قرار — أي
استخدام له كقرار نهائي هو خطأ معماري (reason=v4_ai_required).
"""

import re
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Any, Optional

FILTER_VERSION = "v4.1.0"
FILTER_MODE = "ai_intent_classifier"


# ============================================================
# تطبيع النص (normalization) — للمقارنة فقط، لا للإرسال
#   - lowercase للإنجليزية
#   - توحيد الحروف العربية: أإآ→ا، ؤ→و، ئ→ي، ة→ه، ى→ي
#   - إزالة التطويل (kashida) والتشكيل (harakat)
#   - تطبيع المسافات
# ============================================================
_ARABIC_NORMALIZE_MAP = str.maketrans({
    'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ٱ': 'ا',
    'ؤ': 'و', 'ئ': 'ي', 'ة': 'ه', 'ى': 'ي',
    'ـ': '',  # tatweel/kashida
})
_ARABIC_DIACRITICS = re.compile(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]')


def normalize_text(text: str) -> str:
    """تطبيع النص للمقارنة فقط — لا يُستخدم للإرسال."""
    if not text:
        return ""
    t = text.lower()
    t = t.translate(_ARABIC_NORMALIZE_MAP)
    # [v3.0] إزالة ألف التنوين (اً) قبل بقية التشكيل — تنوين الفتح يضيف ألفًا
    # زائدة («شخصاً»→«شخص»)؛ بقية التنوين يُزيله _ARABIC_DIACRITICS.
    t = t.replace('اً', '').replace('اٌ', '').replace('اٍ', '')
    t = _ARABIC_DIACRITICS.sub('', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t
# NOTE: طيّ الحروف المضاعفة (المشرووع→مشروع) يتم per-token في _tokens
# بعد تقشير البوادئ، حتى لا يُطبّ «لل»→«ل» قبل أن يتعرف عليها تقشير البوادئ.


def _norm_pairs(phrases: List[str]) -> List[Tuple[str, str]]:
    """يبني أزواج (أصلي، مُطبّع) ويزيل التكرار والفارغ."""
    seen = set()
    out = []
    for p in phrases:
        n = normalize_text(p)
        if n and n not in seen:
            seen.add(n)
            out.append((p, n))
    return out


# ============================================================
# [1] PERSON_WORDS — كلمات شخص منفردة (تُطابق بـ\b + «ال»)
# وحدها لا تكفي — يجب أن تُقرن بفعل تنفيذ (Gate 3).
# ============================================================
# [v3.1.0] REMOVED "واحد" from PERSON_WORDS — too ambiguous: matches
# "في يوم واحد" (one as number, religious/figurative context) as well as
# "أبي واحد" (one as person). The person sense is captured explicitly via
# multi-token REQUESTER_PHRASES ("أبي واحد"/"محتاج واحد"/"أريد واحد"/"أبغى
# واحد") so removing the bare word doesn't break legitimate ACCEPTs.
PERSON_WORDS: List[str] = [
    "أحد", "احد", "حد",                 # someone (formal + Gulf dialect)
    "شخص",                              # person ("واحد" removed v3.1.0)
    "مدرس", "دكتور", "استاذ",          # teacher / doctor / professor
    "مختص", "متخصص", "خبير",          # specialist / expert
    "فاهم",                             # someone who understands
    "معيد", "طالب",                     # teaching assistant / student
]

# ============================================================
# [2] REQUESTER_PHRASES — عبارات طلب شخص (multi-token)
# هذه تحمل نية البحث عن شخص لتنفيذ عمل. وحدها مع خدمة يمكن أن تقبل
# (strong_requester + service) حتى بلا فعل تنفيذ صريح (Gate 3 option b).
# ============================================================
REQUESTER_PHRASES: List[str] = [
    # --- أبي/أبغى/محتاج/احتاج + شخص ---
    "أبي أحد", "ابي احد", "أبي حد", "ابي حد", "أبي شخص", "ابي شخص",
    "أبي واحد", "ابي واحد", "أبي مدرس", "ابي مدرس", "أبي دكتور", "ابي دكتور",
    "أبي مختص", "ابي مختص", "أبي متخصص", "ابي متخصص", "أبي خبير", "ابي خبير",
    "أبي فاهم", "ابي فاهم", "أبي استاذ", "ابي استاذ", "أبي معيد", "ابي معيد",
    # أبغى (Gulf)
    "أبغى أحد", "ابغى احد", "أبغى حد", "ابغى حد", "أبغى شخص", "ابغى شخص",
    "أبغى واحد", "ابغى واحد", "أبغى مدرس", "ابغى مدرس", "أبغى دكتور", "ابغى دكتور",
    "أبغى مختص", "ابغى مختص", "أبغى متخصص", "ابغى متخصص", "أبغى خبير", "ابغى خبير",
    # محتاج/احتاج
    "محتاج أحد", "محتاج احد", "محتاج حد", "محتاج شخص", "محتاج واحد",
    "محتاج مدرس", "محتاج دكتور", "محتاج مختص", "محتاج متخصص", "محتاج خبير",
    "محتاج فاهم", "محتاج استاذ",
    "احتاج أحد", "احتاج احد", "احتاج حد", "احتاج شخص", "احتاج واحد",
    "احتاج مدرس", "احتاج دكتور", "احتاج مختص", "احتاج متخصص", "احتاج خبير",
    "احتاج فاهم", "احتاج استاذ",
    # أريد
    "أريد أحد", "اريد احد", "أريد شخص", "اريد شخص", "أريد واحد", "اريد واحد",
    "أريد مدرس", "اريد مدرس",
    # --- من/مين يعرف/عنده/يقدر (seeking recommendation of a person) ---
    "من يعرف", "مين يعرف", "من عنده", "مين عنده", "من يقدر", "مين يقدر",
    "من يدلني", "مين يدلني", "من يرشح لي", "مين يرشح لي",
    # [v3.1.0] REMOVED "تعرفون أحد"/"تعرف أحد"/"تعرفي أحد" from here — these
    # are RECOMMENDATION-SEEKING (asking "do you know someone...") not
    # explicit service-execution requests. Moved to RECOMMENDATION_SEEKING.
    # The person+exec variants below ("أحد يحل"/"أحد يشرح") cover real
    # requests phrased as "do you know someone to do X for me".
    "أدور على أحد", "ادور على احد", "أدور على شخص", "ادور على شخص",
    "أبحث عن شخص", "ابحث عن شخص", "أبحث عن أحد", "ابحث عن احد",
    "أبحث عن مدرس", "ابحث عن مدرس", "أدور على مدرس", "ادور على مدرس",
    # --- من/مين + فعل تنفيذ (implicit person + execution) ---
    "من يسوي", "مين يسوي", "من يعمل", "مين يعمل", "من ينجز", "مين ينجز",
    "من يحل", "مين يحل", "من يكتب", "مين يكتب", "من يجهز", "مين يجهز",
    "من يرتب", "مين يرتب", "من يصمم", "مين يصمم", "من ينفذ", "مين ينفذ",
    "من يشتغل", "مين يشتغل", "من يستلم", "مين يستلم", "من يتولى", "مين يتولى",
    "من يكمل", "مين يكمل", "من يراجع", "مين يراجع", "من يساعد", "مين يساعد",
    "من يضبط", "مين يضبط", "من يخلص", "مين يخلص", "من يشرح", "مين يشرح",
    "من يدرس", "مين يدرس", "من يعلم", "مين يعلم",
    "من يسوي لي", "مين يسوي لي", "من يعمل لي", "مين يعمل لي",
    # --- أحد/شخص + فعل (person + execution in one clause) ---
    "أحد يساعدني", "احد يساعدني", "شخص يساعدني",
    # [v3.1.0] REMOVED "أحد يعرف"/"شخص يعرف" from here — these are
    # CONTACT-INFO-SEEKING (asking the group "does anyone know [specific
    # person] so they can direct me to him?" — NOT asking the person to
    # perform a service). Moved to CONTACT_INFO_SEEKING_PHRASES.
    "أحد عنده", "احد عنده", "شخص عنده",
    "أحد يقدر", "احد يقدر", "شخص يقدر",
    "أحد يستلم", "احد يستلم", "شخص يستلم",
    "أحد يشتغل", "احد يشتغل", "شخص يشتغل",
    "أحد ينجز", "احد ينجز", "شخص ينجز",
    "أحد يخلص", "احد يخلص", "شخص يخلص",
    "أحد يسوي", "احد يسوي", "شخص يسوي",
    "أحد يحل", "احد يحل", "شخص يحل",
    "أحد يكتب", "احد يكتب", "شخص يكتب",
    "أحد يجهز", "احد يجهز", "شخص يجهز",
    "أحد يرتب", "احد يرتب", "شخص يرتب",
    "أحد يصمم", "احد يصمم", "شخص يصمم",
    "أحد يراجع", "احد يراجع", "شخص يراجع",
    "أحد يشرح", "احد يشرح", "شخص يشرح",
    "أحد يشتغل عليه", "احد يشتغل عليه", "شخص يشتغل عليه",
]

# ============================================================
# [3] EXECUTION_VERBS — أفعال تنفيذ (third-person «ي» form)
# أفعال يفعلها الشخص المطلوب. لاحظ: صيغة «ي» (يـ) = third person
# (هو/هي يفعل) = execution signal. صيغة «أ» (أفعل) = first person
# (المستخدم نفسه) = provider signal أو self-action. صيغة «ن» (نفعل)
# = first person plural = provider signal.
# ============================================================
EXECUTION_VERBS: List[str] = [
    "يسوي", "يعمل", "ينجز", "يخلص", "يحل", "يكتب", "يجهز", "يرتب",
    "يصمم", "ينفذ", "يشتغل", "يستلم", "يتولى", "يكمل", "يراجع",
    "يساعد", "يضبط", "يشرح", "يعلم", "ينقح",
    "يرتبه", "يخلصه", "ينجزه", "يسويه", "يحله", "يكتبه", "يجهزه",
]

# [v3.2.0] REMOVED "يدرس" and "يذاكر" from EXECUTION_VERBS.
# «يدرس» in Saudi student dialect = "studies" (a student studying a subject),
# NOT "teaches". When combined with a person word in existential questions
# («فيه احد يدرس اداره ماليه؟» / «في احد موظف في ارامكو و يدرس؟») it is a
# PERSON-STATUS inquiry (finding peers), never a service-execution request.
# This single entry caused 4 of the 14 new production FPs (#8/#15/#16/#17).
# «يذاكر» (studies/memorizes) has the same problem — "ابي احد يذاكر معي" is a
# study-buddy (mutual activity), not a service executed FOR the asker, and per
# the golden rule defaults to REJECT. Real teaching-execution verbs are kept:
# «يشرح» (explains), «يعلم/يعلمني» (teaches me), "يدرسني" (teaches me —
# object-suffix form, still matched via the 'ني' suffix on the verb 'يعلم'-
# style; bare «يدرس» without object is ambiguous → REJECT per golden rule).

# أفعال تنفيذ هي نفسها خدمة أكاديمية (يشرح=تدريس، يراجع=مراجعة، يحل=حل)
# لو ظهرت مع requester بلا خدمة صريحة، تُغني عن خدمة (Gate 4).
EXEC_IMPLIES_SERVICE: Dict[str, str] = {
    "يشرح": "teaching",
    "يعلم": "teaching",
    "يراجع": "reviewing",
    "يحل": "solving",
}
# [v3.2.0] REMOVED "يدرس" (→"teaching") and "يذاكر" (→"studying") — see the
# EXECUTION_VERBS comment above: both are "studies" (person-status verbs) in
# Saudi student dialect. With them gone, «الي يدرس ادا 110 يعامني متى الميد»
# falls to the info-seeking gate (متى) instead of ACCEPTing via exec_implies.

# ============================================================
# [4] OWNERSHIP_NEED — ملكية/حاجة (المستخدم يملك العمل أو يحتاجه)
# [v3.1.0] REMOVED "علي" / "على" — too common as Arabic preposition
# ("on"/"over"): matches "على حسب" (depending on), "على غيرك" (other than
# you), "على ارض الواقع" (in real life). These are NOT ownership markers.
# The real ownership signals ("عندي"/"محتاج"/"أبي"/"أبغى"/"مطلوب مني") are
# kept. Possessive suffixes on service nouns ("واجبي"=my homework) are now
# detected separately via _has_service_with_possessive.
# ============================================================
OWNERSHIP_NEED: List[str] = [
    "عندي", "عندى", "مطلوب مني", "لازم اسلم", "لازم أسلم",
    "محتاج", "أحتاج", "احتاج", "أبي", "ابي", "أبغى", "ابغى", "أريد", "اريد",
    "مطلوب", "واجبني", "متطلب",
]

# ============================================================
# [5] OUTSOURCING_INDICATORS — إشارات تفويض (له/لي/عني/مضمون)
# تدل على أن المستخدم يُفوّض العمل لشخص آخر. تُمكّن Gate 3 option c
# (requester + service + outsourcing، بلا فعل تنفيذ صريح).
# ============================================================
OUTSOURCING_INDICATORS: List[str] = [
    "لي", "له", "لها", "عني", "بدلي", "بدالى", "ني",
    "مضمون", "موثوق", "ياتي", "يقدم", "يوصي",
    # [v3.1.0] Added "خصوصي" / "خصوصية" — "مدرس خصوصي" / "درس خصوصي" =
    # private tutor/lesson = strong service-execution indicator. Without this,
    # "أبي مدرس خصوصي للمادة" would fail Gate 3 (d) tightened check (no exec
    # verb + no service term). "خصوصي" is rarely used outside tutoring context.
    "خصوصي", "خصوصية",
]

# ============================================================
# [6] SERVICE_TERMS — خدمات أكاديمية (وحدها = REJECT دائمًا)
# لا تُقبل الرسالة لمجرد وجودها. يجب أن تُقرن بـ requester + execution.
# ============================================================
SERVICE_TERMS: List[str] = [
    # research
    "بحث", "بحت", "البحث", "بحوث", "بحث جامعي", "بحث علمي", "بحث تخرج", "مشروع بحثي",
    "مراجعة أدبيات", "مراجعه ادبيات", "systematic review", "literature review",
    "سيرش", "ريسيرش",
    # reports
    "تقرير", "التقرير", "تقارير", "تقرير جامعي", "تقرير تدريب",
    "تقرير ميداني", "تقرير تعاوني",
    # assignments
    "واجب", "الواجب", "واجبات", "سكليف", "اسكليف", "assignment", "تكليف",
    "التكليف", "تكاليف",
    # projects
    "مشروع", "المشروع", "مشاريع", "بروجكت", "project", "مشروع تخرج",
    "مشروع جامعي", "مشروع بحثي",
    # presentations
    "عرض", "العرض", "عروض", "عرض تقديمي", "برزنتيشن", "presentation",
    "بوربوينت", "PowerPoint", "powerpoint", "ppt", "PPT", "عرض بوربوينت",
    # Excel / data
    "Excel", "excel", "إكسل", "اكسل", "ملف اكسل", "جداول", "SPSS", "spss",
    "MATLAB", "matlab", "Python", "python", "برمجة", "كود", "تحليل بيانات",
    # mindmaps
    "خريطة مفاهيم", "خريطه مفاهيم", "خريطة ذهنية", "خريطه ذهنيه",
    "mind map", "mindmap", "concept map", "مخطط",
    # studying help (as a service a person provides)
    "مذاكرة", "شرح خصوصي", "تدقيق", "تنسيق بحث", "حل مسائل", "تنسيق",
    "تدريس", "تعلّم", "تعلم",
    # task/work (خدمة عامة — مهمة/شغل)
    "مهمة", "المهمة", "شغل", "الشغل", "الشغلات",
]

# خريطة الخدمة → تصنيف للتشخيص (service field)
SERVICE_CATEGORY: Dict[str, str] = {
    "research": "research", "report": "report", "assignment": "assignment",
    "project": "project", "presentation": "presentation", "excel": "excel",
    "programming": "programming", "mindmap": "mindmap", "studying": "studying",
    "teaching": "teaching", "reviewing": "reviewing", "solving": "solving",
}
_SERVICE_TERM_TO_CATEGORY: List[Tuple[str, str]] = [
    # (term substring in normalized text, category) — checked in order
    ("systematic review", "research"), ("literature review", "research"),
    ("ريسيرش", "research"), ("سيرش", "research"),
    ("مراجعة أدبيات", "research"), ("مراجعه ادبيات", "research"),
    ("مشروع بحثي", "research"), ("بحث تخرج", "research"),
    ("بحث علمي", "research"), ("بحث جامعي", "research"),
    ("بحوث", "research"), ("البحث", "research"), ("بحث", "research"),
    ("تقرير تعاوني", "report"), ("تقرير ميداني", "report"),
    ("تقرير تدريب", "report"), ("تقرير جامعي", "report"),
    ("التقارير", "report"), ("تقارير", "report"), ("التقرير", "report"),
    ("تقرير", "report"),
    ("اسكليف", "assignment"), ("سكليف", "assignment"),
    ("assignment", "assignment"), ("التكاليف", "assignment"),
    ("التكليف", "assignment"), ("تكاليف", "assignment"),
    ("تكليف", "assignment"), ("الواجبات", "assignment"),
    ("واجبات", "assignment"), ("الواجب", "assignment"),
    ("واجب", "assignment"),
    ("مشروع تخرج", "project"), ("مشروع جامعي", "project"),
    ("المشاريع", "project"), ("مشاريع", "project"),
    ("بروجكت", "project"), ("project", "project"),
    ("المشروع", "project"), ("مشروع", "project"),
    ("عرض بوربوينت", "presentation"), ("عرض تقديمي", "presentation"),
    ("بوربوينت", "presentation"), ("powerpoint", "presentation"),
    ("برزنتيشن", "presentation"), ("presentation", "presentation"),
    ("العرض", "presentation"), ("عرض", "presentation"),
    ("ملف اكسل", "excel"), ("الجداول", "excel"), ("جداول", "excel"),
    ("spss", "excel"), ("matlab", "excel"),
    ("تحليل بيانات", "programming"), ("برمجة", "programming"),
    ("كود", "programming"), ("python", "programming"),
    ("اكسل", "excel"), ("إكسل", "excel"), ("excel", "excel"),
    ("خريطة مفاهيم", "mindmap"), ("خريطه مفاهيم", "mindmap"),
    ("خريطة ذهنية", "mindmap"), ("خريطه ذهنيه", "mindmap"),
    ("concept map", "mindmap"), ("mindmap", "mindmap"),
    ("mind map", "mindmap"), ("مخطط", "mindmap"),
    ("تنسيق بحث", "studying"), ("حل مسائل", "studying"),
    ("تدقيق", "studying"), ("تنسيق", "studying"),
    ("شرح خصوصي", "studying"), ("مذاكرة", "studying"),
    ("المهمة", "task"), ("مهمة", "task"),
    ("الشغل", "task"), ("شغل", "task"), ("الشغلات", "task"),
]


def _classify_service(normalized: str, exec_signals: List[str]) -> Optional[str]:
    """يحدد تصنيف الخدمة للتشخيص (service field)."""
    for term, cat in _SERVICE_TERM_TO_CATEGORY:
        # طبّع المصطلح قبل المقارنة (ة→ه، ى→ي...) حتى يطابق النص المُطبّع
        if normalize_text(term) in normalized:
            return cat
    # fallback: exec verb that implies service
    for ev in exec_signals:
        n = normalize_text(ev)
        if n in EXEC_IMPLIES_SERVICE:
            return EXEC_IMPLIES_SERVICE[n]
    return None


# ============================================================
# [7] PROVIDER_INDICATORS — إشارات مقدم خدمة (first-person offer)
# صيغة المفرد المُقدّم: أسوي/أحل/أكتب. صيغة الجمع: نوفر/نقدم/نسوي.
# عبارات: متخصص في/لدينا/خدماتنا/للتواصل/للطلب.
# ============================================================
PROVIDER_INDICATORS: List[str] = [
    # first-person singular (weak unless +plural service noun)
    "أسوي", "اسوي", "أعمل", "اعمل", "أنجز", "انجز", "أحل", "احل",
    "أكتب", "اكتب", "أصمم", "اصمم", "أشرح", "اشرح", "أجهز", "اجهز",
    "أرتب", "ارتب", "أكمل", "اكمل", "أنفذ", "انفذ",
    # first-person plural (strong provider)
    "نوفر", "نقدم", "نقدم خدمات", "لدينا خدمات", "لدينا", "خدماتنا",
    "نخدمكم", "نخدم", "نسوي", "نعمل", "ننجز", "نساعدكم", "نساعدكم في",
    "نشتغل", "نرتب", "نصمم", "نحل", "نكتب", "نجهز", "نشرح", "نراجع",
    "مكتبنا", "فريقنا", "ننجز لك",
    # provider phrases (strong)
    "متخصص في", "متخصصون", "متخصصون في", "مختص في", "مختصون",
    "للتواصل لحل", "للتواصل", "تواصل معنا", "راسلنا", "تواصل خاص",
    "للطلب", "للحجز", "للاستفسار", "للاستفسارات", "للحجز والاستفسار",
    "مكتب", "مؤسسة", "منشة",
]

# ============================================================
# [8] ADVERTISEMENT_STRONG_SIGNALS — إشارات تسويقية
# ============================================================
ADVERTISEMENT_STRONG_SIGNALS: List[str] = [
    "أسعارنا", "بأسعار", "بأسعار مناسبة", "بأسعار تنافسية", "أفضل الأسعار",
    "أسعار ممتازة", "أسعار", "تنافسية", "تنافسي",
    "خصم", "خصومات", "تخفيض", "تخفيضات", "حسم", "عروض", "عرض خاص",
    "عرض محدود", "عرض لفترة محدودة", "استفد الآن", "احجز الآن",
    "اطلب الآن", "سارع",
    "فرصة", "فرصه", "محدودة", "العدد محدود", "أماكن محدودة", "مقاعد محدودة",
    "حجز", "احجز", "حجوزات", "حجز مسبق",
    "دفع", "الدفع", "دفع اونلاين", "الدفع اونلاين", "سداد", "السداد",
    "الدفع المسبق", "دفع مسبق",
    "ضمان", "ضمان استرجاع", "ضمان الجودة", "نتيجة مضمونة",
    "ضمان النتيجة", "خدمة مضمونة",
    "خبرة سنوات", "سنوات من الخبرة", "خبرة طويلة", "فريق متخصص",
    "كفاءة عالية", "جودة عالية", "عالية الجودة",
    "سرعة في التنفيذ", "تنفيذ سريع", "انجاز سريع", "انجاز في وقت قياسي",
    "سرية تامة", "خصوصية تامة",
    "توصيل سريع", "تسليم سريع", "تسليم فوري", "تسليم في نفس اليوم",
    "عملاء", "عملائنا", "عملاء سابقون", "عملاء راضون", "رضا العملاء",
    "تقييمات العملاء", "شهادات العملاء", "مراجعات العملاء",
    "نماذج أعمال", "معرض أعمال", "حافظة أعمال",
    "واتساب للأعمال", "رقم واتساب", "مراسلة عبر واتساب", "تواصل واتساب",
    "عرض خدمات", "طلب خدمة", "خدمات طلابية", "خدمات تعليمية",
    "خدمات اكاديمية", "خدمة اونلاين", "خدمة مدرسية", "خدمات",
    "project service", "study help", "promotion", "announcement",
    "اعلان", "اعلانات", "contact me", "whatsapp",
    "مجاني", "مجانا", "هديه", "هدية", "هدية مجانية",
    "الباقة", "باقات", "اشتراك", "تقييم 5", "تقييم خمس نجوم",
    "5 نجوم", "موصى به",
]

# plural service nouns — مع plural noun يدل على عرض تجاري (أسوي بحوث/أحل واجبات)
PLURAL_SERVICE_NOUNS: List[str] = [
    "بحوث", "تقارير", "واجبات", "مشاريع", "تكاليف", "عروض", "خدمات",
]

# ============================================================
# [9] INFO_SEEKING_PHRASES — إشارات طلب معلومات (REJECTION)
# ============================================================
INFO_SEEKING_PHRASES: List[str] = [
    "ما هو", "ما هي", "وش هو", "وش هي", "ايش هو", "ايش هي",
    # [v3.2.0] PRODUCTION FP #18: «عندي سؤال اتمنى اللي فاهم او عارف مايبخل
    # بالاجابه...» — "I have a question, hope whoever understands answers"
    # = classic info-seeking opener, never a service-execution request.
    "عندي سؤال", "عندى سؤال", "عندي استفسار", "عندى استفسار",
    "ما معنى", "ما معنى هذا", "وش يعني", "ايش يعني", "وش معنى",
    "ما المقصود", "وش المقصود", "ايش المقصود",
    "كيف اسوي", "كيف أسوي", "كيف أعمل", "كيف اعمل", "كيف احل", "كيف أحل",
    "كيف اكتب", "كيف أكتب", "كيف ابدا", "كيف أبدأ", "كيف ابداء",
    "وش طريقة", "وش الطريقة", "ايش طريقة", "ما الطريقة",
    "طريقة كتابة", "طريقة عمل", "طريقة حل", "طريقة إعداد", "طريقة اعداد",
    "لماذا", "ليش", "ليه", "متى", "كيف يتم", "كيف تتم",
    "معلومات عن", "تعريف", "مفهوم", "معنى", "ما هو تعريف", "وش هو تعريف",
    "ايش هو تعريف", "اعطيني تعريف", "اعطني تعريف", "ابغى تعريف",
    "ابي تعريف", "محتاج تعريف",
    "عرفني", "عرفني", "وضح لي", "وضحلي", "فهمني", "فهموني",
    "ابي شرح", "أبي شرح", "أبغى شرح", "ابغى شرح",   # شرح as noun (resource)
    "محتاج شرح", "ابي ملخص", "أبي ملخص", "أبغى ملخص", "ابغى ملخص",
    "ابي ملخصات", "أبي ملخصات", "أبغى ملخصات", "ابغى ملخصات",
    "ابي نماذج", "أبي نماذج", "أبغى نماذج", "ابغى نماذج",
    "ابي ملفات", "أبي ملفات", "أبغى ملفات", "ابغى ملفات",
    "ابي مذكرة", "أبي مذكرة", "أبغى مذكرة", "ابغى مذكرة",
    "ابي مذكرات", "أبي مذكرات", "أبغى مذكرات", "ابغى مذكرات",
    "ابي مصادر", "أبي مصادر", "أبغى مصادر", "ابغى مصادر",
    "ابي رابط", "أبي رابط", "أبغى رابط", "ابغى رابط",
    "ابي فيديو", "أبي فيديو", "أبغى فيديو", "ابغى فيديو",
    "ابي قناة", "أبي قناة", "أبغى قناة", "ابغى قناة",
]

# ============================================================
# [10] RESOURCE_SEEKING_PHRASES — إشارات البحث عن مصدر (REJECTION)
# ============================================================
RESOURCE_SEEKING_PHRASES: List[str] = [
    "وين القى", "وين ألقى", "وين القى", "وين القاه", "وين القا",
    "أين أجد", "أين القى", "أين ألقى", "وين احصل", "وين أحصل",
    "أين احصل", "وين اقدر القى", "وين أقدر القى",
    "وين في", "وين ألاقي", "وين القاه",
    # [v3.2.0] PRODUCTION FP #5: «ممكن تبعتولى كتب تشريح ١ و٢...» — asking
    # group members to SEND files (books/slides) = resource-seeking, not
    # service-execution. Dialect verb forms "send me (pl)".
    "تبعتولي", "تبعتولى", "تبعثولي", "تبعثولى",
    "تبعتوني", "تبعثوني", "ابعتولي", "ابعتولى", "ابعتوني",
    "رابط شرح", "قناة تشرح", "قناة تشرح المادة",
    "شرح مجاني", "نماذج اختبار", "نماذج اختبارات", "بنوك أسئلة",
    "بنك أسئلة", "ملخصات المادة", "مذكرات المادة", "ملفات المادة",
    "ملخصات", "مذكرات", "نماذج", "ملفات", "مصادر", "روابط",
    "مذكرة", "مذكرات", "مذكرة المادة",
    "يوتيوب", "فيديو", "فيديوهات", "قناة",
]

# ============================================================
# [11] RECOMMENDATION_SEEKING — إشارات طلب توصية (REJECTION)
# «افضل واحد يشرح» = يطلب توصية بأفضل شخص، لا يطلب شخصًا ينفذ له.
# ============================================================
# [v3.1.0] ADDED "تعرف/تعرفين/تعرفون أحد" — asking "do you know someone
# who teaches..." is recommendation-seeking, NOT explicit service-execution.
# These were previously in REQUESTER_PHRASES (causing FPs like «تعرفين أحد
# يشرح البرمجه كويس؟»). Moved here. Gate 2.5 still allows ACCEPT if the
# message also has ownership + service + exec (e.g., «تعرف أحد يحل واجبي»
# — has possessive "واجبي" → ownership detected → not pure recommendation).
RECOMMENDATION_SEEKING: List[str] = [
    "افضل", "أفضل", "الافضل", "الأفضل", "اقوى", "أقوى",
    "مين افضل", "من افضل", "مين أفضل", "من أفضل",
    "اقترح", "أقترح", "يقترح", "اقتراح", "اقتراحات",
    "يرشح", "رشح", "رشح لي", "ترشيح",
    "توصية", "يوصي", "وصي",
    "ينصح", "نصيحة", "نصح", "استشارة",
    "وش رائيكم", "وش رايكم", "ايش رائيكم", "ايش رايكم",
    "عطوني راي", "اعطوني راي",
    # [v3.1.0] "do you know someone who..." patterns (recommendation-seeking)
    "تعرف أحد", "تعرف احد", "تعرف شخص",
    "تعرفي أحد", "تعرفي احد", "تعرفي شخص",
    "تعرفين أحد", "تعرفين احد", "تعرفين شخص",
    "تعرفون أحد", "تعرفون احد", "تعرفون شخص",
    # asking "who teaches/studies" (recommendation of a person)
    "مين يشرح", "من يشرح",
    "مين يدرس", "من يدرس",  # asking "who studies/teaches" (person rec)
    # [v3.2.0] PRODUCTION FP #1: «في احد يشرح المفكر غير المثنى؟...» —
    # existential inquiry "is there anyone who explains X?" WITHOUT a
    # wanting phrase (ابي/ابغى/محتاج/ضروري) or beneficiary marker (لي).
    # Same classification as «تعرفين احد يشرح البرمجه كويس؟» (v3.1.0 FP-10):
    # asking about the EXISTENCE of a tutor = recommendation/inquiry, not a
    # clear request per the golden rule. Messages WITH a beneficiary marker
    # («في احد يشرح لي المفكر؟») still bypass Gate 2.5 via (beneficiary AND
    # exec) and stay ACCEPTED.
    "في احد يشرح", "فيه احد يشرح", "في حد يشرح", "فيه حد يشرح",
    "في أحد يشرح", "فيه أحد يشرح", "في حد يشرح", "فيه حد يشرح",
]

# ============================================================
# [11b] CONTACT_INFO_SEEKING_PHRASES — إشارات طلب معلومات تواصل (REJECTION)
# [v3.1.0] NEW. «ابي رقم الدكتوره» = يطلب معلومات تواصل (phone/email)،
# لا يطلب شخصًا ينفذ خدمة. كذلك «أحد يعرفه يدلني عليه» = يطلب توجيهه
# لشخص معيّن (Not service execution). Gate 1.5.
# ============================================================
CONTACT_INFO_SEEKING_PHRASES: List[str] = [
    # asking for phone number / email
    "ابي رقم", "أبي رقم", "ابغى رقم", "أبغى رقم", "محتاج رقم",
    "ابي ايميل", "أبي ايميل", "ابغى ايميل", "أبغى ايميل", "محتاج ايميل",
    "ابي رقم او ايميل", "أبي رقم أو ايميل",
    "ابي رقم الاستاذ", "ابي رقم المدرس", "ابي رقم المعيد",
    # contact info of a specific doctor/professor
    "رقم الدكتور", "رقم الدكتوره", "ايميل الدكتور", "ايميل الدكتوره",
    "رقم الاستاذ", "ايميل الاستاذ",
    "الايميل",  # asking about "the email" of someone
    # asking "do you know [specific person] to direct me to him"
    # [v3.1.0] NOTE: removed bare "أحد يعرف" / "شخص يعرف" — these can appear
    # in legitimate service-execution requests like «أحد يعرف شخص يسوي خريطة
    # مفاهيم؟» (asking for someone to make a mind map). The contact-info-
    # seeking signal is the COMBINATION with "يدلني عليه" (direct me to him).
    # Keep the specific patterns instead.
    "أحد يعرفه", "احد يعرفه", "شخص يعرفه",
    "أحد يعرفني", "احد يعرفني", "شخص يعرفني",
    "يدلني عليه", "يدلوني عليه", "يدلوني عليه",
    # asking "who has studied with [specific person]"
    "مين قد درس عند", "من قد درس عند",
    "مين درس عند", "من درس عند",
    # asking to be messaged privately for info (not service)
    "يجيني بالخاص محتاج اعرف", "يجيني بالخاص محتاج",
    "محتاج اعرف عن طريقة", "ابغى اعرف عن طريقة", "ابي اعرف عن طريقة",
    "محتاج اعرف عن", "ابغى اعرف عن", "ابي اعرف عن",
    "وش طريقة الدكتور", "وش طريقة الدكتوره",
    "وش طريقة المدرس",
    # how to communicate (not service)
    "اكلمه على ارض", "اكلمه على ارض الواقع",
    "ارسله للدكتور", "ارسلها للدكتور",
]

# ============================================================
# [11c] DECISION_SEEKING_PHRASES — إشارات طلب قرار/نصيحة قرارية (REJECTION)
# [v3.1.0] NEW. «اداوم ولا؟» / «يمديني اسويه ولا لازم اخلي احد يسويلي» =
# المستخدم يسأل المشورة على قرار (لا يلتزم بتنفيذ خدمة). Gate 1.5.
# ============================================================
DECISION_SEEKING_PHRASES: List[str] = [
    # attendance / decision questions
    "اداوم ولا", "اداوم او", "اداوم الجامعه",
    "اداوم",  # bare "should I attend" — but only if NO service exec pattern
    "اقدر ؟", "اقدر?", "اقدر ولا", "اقدر ولا؟",
    "اقدر اغير", "اقدر اغير كم",
    # "can I do it myself or outsource?" patterns
    "يمديني انا اسويه", "يمديني اسويه", "يمديني اسوي",
    "يمديني انا",  # "can I (myself)..." — decision pattern
    "انا اسويه ولا لازم", "اسويه ولا لازم اخلي",
    "ولا لازم اخلي", "لازم اخلي احد",
    # "should I do X or Y" patterns
    "ولا اكلمه", "ولا اكلم", "ولا وش العلم",
    "وش العلم", "وش اسوي", "اش سويت",
    # asking if professor name/room number is right (administrative question)
    "ما نزل اسم الدكتوره", "ما نزل اسم الدكتور",
    # changing professor questions
    "ابي اغير كم دكتور", "ابي اغير دكتور",
    # [v3.2.0] PRODUCTION FP #6: «ابي اغير الدكتور لو اروح القسم يغيرونها لي؟»
    # — changing a section professor via the department = administrative
    #   decision, not hiring a tutor. «ابي اغير ال» covers الدكتور/المدرس/
    #   الشعبة/المواد. FP #10: «هل لازم أروح لهم رفع تذكرة» — "must I go to
    #   them to file a ticket" = decision. FP #12: «فتنصحوني اشترك مع خصوصي
    #   ولا لا» — "do you advise me to subscribe to private tutoring or not"
    #   = advice-seeking (the asker explicitly says they already understand
    #   the material — they only need practice questions, not a tutor).
    "ابي اغير ال", "ابغى اغير ال", "ابغا اغير ال",
    "ابغى اغير", "ابغا اغير",
    "لو اروح", "لو أروح", "لو ارح",
    "لازم أروح", "لازم اروح", "هل لازم أروح", "هل لازم اروح",
    "لازم أرح", "لازم ارح",
    "تنصحوني", "تنصحني", "تنصحني", "تنصحون", "تنصحوني ب",
    "ولا لا", "ولا لا؟", "او لا", "أو لا",
]

# ============================================================
# [11d] PERSON_STATUS_SEEKING_PHRASES — إشارات طلب حالة شخص (REJECTION)
# [v3.1.0] NEW. «طالب يدرس بالتمريض احد يعرفه يدلني عليه» = يسأل عن
# شخص معيّن (هل هو طالب؟ ما حالته؟) لا يطلب خدمة تنفيذ. كذلك
# «احد يدرس او تخرج بماجستير طاقة متجددة؟» = يسأل عن خلفية أعضاء
# المجموعة (من يدرس/تخرج؟) لا يطلب خدمة. Gate 1.5.
# ============================================================
PERSON_STATUS_SEEKING_PHRASES: List[str] = [
    # asking about specific person's status (student/graduate)
    "طالب يدرس", "طالب يدرس بـ", "طالب يدرس في", "طالب يدرس بال",
    # asking if anyone in the group is a student/graduate of a program
    "يدرس او تخرج", "يدرس أو تخرج",
    "احد يدرس او تخرج", "احد يدرس أو تخرج",
    "يدرس او تخرج بـ", "يدرس أو تخرج بـ",
    "يدرس او تخرج بماجستير", "يدرس أو تخرج بماجستير",
    # "do you know anyone who studies X" patterns
    "مين يدرس", "من يدرس",  # overlaps with RECOMMENDATION_SEEKING — both fire
    # asking "how does the doctor work?" (info about person, not service)
    "طريقة الدكتور", "طريقة الدكتوره", "طريقة المدرس",
]

# ============================================================
# [12] LONG_INFORMATIONAL_CONTENT — مؤشرات محتوى معلوماتي طويل
# نص طويل + عناوين/تعريفات/تعداد → محتوى تعليمي منسوخ، لا طلب خدمة.
# ============================================================
LONG_INFORMATION_MARKERS: List[str] = [
    "اهدافه", "أهدافه", "اهميته", "أهميته", "اهمية", "أهمية",
    "يعد", "يعتبر", "يعتمد على", "تعتمد على", "يقوم على", "تقوم على",
    "مميزاته", "خصائصه", "انواعه", "أنواعه", "خطواته", "مكوناته",
    "عناصره", "مميزات", "خصائص",
    "هو نوع", "هي نوع", "هو عبارة", "هي عبارة",
    "هو عملية", "هي عملية", "هو اسلوب", "هي اسلوب",
    "تعريفه", "مفهومه", "مفهوم",
    "بشكل عام", "بصفه عام", "خلاصة",
]
_ENUMERATION_RE = re.compile(r'1\ufe0f\u20e3|2\ufe0f\u20e3|3\ufe0f\u20e3|\u2780|\u2781|\u2782|[1-9][\.\)]\s|[\u2022\-\u25CF\u25AA]\s')

LONG_CONTENT_CHARS = 250
LONG_CONTENT_LINES = 5


def _detect_long_informational(text: str, normalized: str) -> bool:
    """هل النص محتوى معلوماتي طويل (ليس طلب خدمة)؟"""
    if not text:
        return False
    long_by_chars = len(text) >= LONG_CONTENT_CHARS
    long_by_lines = len(text.splitlines()) >= LONG_CONTENT_LINES
    if not (long_by_chars or long_by_lines):
        return False
    has_marker = any(m in normalized for m in LONG_INFORMATION_MARKERS)
    has_enum = bool(_ENUMERATION_RE.search(text))
    return has_marker or has_enum


# ============================================================
# [12b] DELEGATION_VERBS — أفعال تفويض (first-person)
# أوكل/أفوّض/أسند: المستخدم يُفوّض المهمة لشخص آخر. مع person + ownership
# تكفي للقبول (الشخص المُفوّض إليه سينفذ).
# ============================================================
DELEGATION_VERBS: List[str] = [
    "اوكل", "أوكل", "افوض", "أفوض", "فوض", "فوّض", "فوّض",
    "انيط", "أنيط", "اسند", "أسند", "سند",
]
# [v3.2.0] REMOVED "وكل" and "أكل"/"اكل" from DELEGATION_VERBS.
# «وكل» false-matched the extremely common conjunction+quantifier «وكل»
# (= و+كل "and all") — production FP #14: «...وكل اوراق تمام» ("and all
# papers are complete") was treated as delegation + person → ACCEPT for a
# pure informational statement about a military institute. The real
# delegation forms (اوكل/أوكل) are kept.
# «أكل»/«اكل» false-matched «اخاف اكل حرمان» ("afraid of failing" — the
# verb "eat" in the student idiom "اكل حرمان" = get an F). Production FP
# #11: «احذف مادته؟ اخاف اكل حرمان» was accepted as delegation + person.

# ============================================================
# [12c] PROFESSIONAL_ROLES — أدوار مهنية (مدرس/دكتور/مختص)
# لو ظهرت مع ownership (أبي/محتاج) فإنها imply خدمة (تدريس/استشارة).
# ============================================================
PROFESSIONAL_ROLES: List[str] = [
    "مدرس", "دكتور", "مختص", "متخصص", "خبير", "استاذ", "معيد", "فاهم",
]

# ============================================================
# [12d] READY_MADE_INDICATORS — مؤشرات «جاهز/معد»
# لو ظهرت مع خدمة بلا فعل تنفيذ → طلب مصدر جاهز (REJECT resource).
# مثال: «من عنده ملف اكسل جاهز» = يبحث عن ملف جاهز، لا عن شخص يصنعه.
# ============================================================
READY_MADE_INDICATORS: List[str] = [
    "جاهز", "جاهزه", "معد", "مكتمل", "مكتمله",
    "مأنجز", "منجز", "مختصر", "ملخص", "منجزة", "منجزه",
]

# ============================================================
# [13] Contact signals (phone / url / @handle) — diagnostic only
# لا ترفض وحدها؛ تُستخدم لتعزيز provider detection.
# ============================================================
_PHONE_RE = re.compile(
    r'(\+966\d{8,9}|\+967\d{8,9}|\+968\d{8,9}|\+971\d{8,9}|\+20\d{8,9}|(?<!\d)05\d{8}(?!\d))'
)
_CONTACT_URL_RE = re.compile(r'(https?://|t\.me/|wa\.me/|telegram\.me/)', re.IGNORECASE)
_AT_HANDLE_RE = re.compile(r'(?<!\w)@[A-Za-z][A-Za-z0-9_]{3,}')
_DOTTED_WORD_RE = re.compile(r'[أ-ي][ـ]?\.[ـ]?[أ-ي]')
_MULTILINE_AD_THRESHOLD = 6


def _has_phone_number(text: str) -> bool:
    return bool(_PHONE_RE.search(text or ""))


def _has_contact_url(text: str) -> bool:
    return bool(_CONTACT_URL_RE.search(text or ""))


def _has_at_handle(text: str) -> bool:
    return bool(_AT_HANDLE_RE.search(text or ""))


def _has_dotted_word(text: str) -> bool:
    return bool(_DOTTED_WORD_RE.search(text or ""))


def _has_many_lines(text: str, threshold: int = _MULTILINE_AD_THRESHOLD) -> bool:
    if not text:
        return False
    try:
        return len(text.splitlines()) >= int(threshold)
    except Exception:
        return False


# ============================================================
# Pre-normalized pairs (build once at import)
# ============================================================
_PERSON_PAIRS = _norm_pairs(PERSON_WORDS)
_REQUESTER_PAIRS = _norm_pairs(REQUESTER_PHRASES)
_EXEC_PAIRS = _norm_pairs(EXECUTION_VERBS)
_OWNERSHIP_PAIRS = _norm_pairs(OWNERSHIP_NEED)
_SERVICE_PAIRS = _norm_pairs(SERVICE_TERMS)
_PROVIDER_PAIRS = _norm_pairs(PROVIDER_INDICATORS)
_AD_PAIRS = _norm_pairs(ADVERTISEMENT_STRONG_SIGNALS)
_PLURAL_NOUN_PAIRS = _norm_pairs(PLURAL_SERVICE_NOUNS)
_INFO_PAIRS = _norm_pairs(INFO_SEEKING_PHRASES)
_RESOURCE_PAIRS = _norm_pairs(RESOURCE_SEEKING_PHRASES)
_RECOMMEND_PAIRS = _norm_pairs(RECOMMENDATION_SEEKING)
_OUTSOURCE_PAIRS = _norm_pairs(OUTSOURCING_INDICATORS)
_DELEGATION_PAIRS = _norm_pairs(DELEGATION_VERBS)
_ROLE_PAIRS = _norm_pairs(PROFESSIONAL_ROLES)
_READY_MADE_PAIRS = _norm_pairs(READY_MADE_INDICATORS)
# [v3.1.0] New REJECTION gates (Gate 1.5)
_CONTACT_INFO_PAIRS = _norm_pairs(CONTACT_INFO_SEEKING_PHRASES)
_DECISION_PAIRS = _norm_pairs(DECISION_SEEKING_PHRASES)
_PERSON_STATUS_PAIRS = _norm_pairs(PERSON_STATUS_SEEKING_PHRASES)


def _has_service_with_possessive(normalized: str) -> bool:
    """[v3.1.0] NEW. يكتشف إذا كان أي token في النص اسم خدمة + لاحقة ملكية
    (my/our/his/her/their) — مثل «واجبي»/«مشروعي»/«تقريركم»/«بحوثهم».

    هذه إشارة ملكية قوية: المستخدم يملك الخدمة (الواجب/المشروع/التقرير)
    ويطلب ضمنيًا شخصًا لينفذها له. تُستخدم لتوسيع ownership signal
    بحيث يلتقط «تعرف أحد يحل واجبي» (لا يملك «ابي»/«محتاج» لكنه يملك
    «واجبي» = my homework) كطلب صريح.

    الأمان: نطابق فقط لو root (بعد نزع اللاحقة) ينتمي لـSERVICE_TERMS.
    لا نُرجع True لمجرد وجود كلمة بـ«ه» — الشروط:
      - token >= 4 حروف (مطابقة _strip_possessive_suffix)
      - root >= 3 حروف
      - root ∈ SERVICE_TERMS (بعد normalize)
    """
    if not normalized:
        return False
    toks = _tokens(normalized)
    if not toks:
        return False
    # set of normalized service terms for O(1) lookup
    service_norms = {n for _, n in _SERVICE_PAIRS}
    plural_norms = {n for _, n in _PLURAL_NOUN_PAIRS}  # بحوث/تقارير/etc
    for tok in toks:
        if not tok or len(tok) < 4:
            continue
        root = _strip_possessive_suffix(tok)
        if root is None:
            continue
        if root in service_norms or root in plural_norms:
            return True
    return False


_ARABIC_PREFIXES = [
    # compound prefixes (longest first to avoid partial strips)
    'وبال', 'وكال', 'ولل', 'فلل', 'بلل', 'كلل', 'وكلال', 'وبلال',
    # single-suffix compound
    'لل', 'بال', 'كال', 'فال', 'وال', 'ول', 'فل', 'بل', 'كل', 'وب', 'وك', 'وف',
    # definite article
    'ال',
]

# [v3.2.0] PROTECTED function words — prefix-stripping must NOT touch these.
# Stripping "ال" from "اللي"/"اللى" (relative pronouns) produced a FALSE
# outsource signal "لي"/"لى" (to-me) in nearly every message containing
# "اللي" — one of the most common Arabic function words. Same catastrophe
# with God-words: "الله"→strip "ال"→"له", "والله"→strip "وال"→"له",
# "بالله"→strip "بال"→"له" — all produced FALSE "له" (to-him) outsource
# signals. These words appear in a huge fraction of Saudi chat messages
# (والله/بالله/ان شاء الله/اللي...) and silently inflated acceptance via
# Gate 3 (c)/(d) outsource paths and the Gate 2.5 bypass.
_PROTECTED_WORDS = {
    'الله', 'بالله', 'والله', 'تالله', 'لله', 'للاله', 'واللهم', 'اللهم',
    'اللي', 'اللى', 'اللذي', 'اللتي', 'واللي', 'واللى', 'باللي', 'باللى',
}


def _strip_arabic_prefix(word: str) -> str:
    """يُزيل البوادئ العربية الشائعة (ال/لل/بال/كال/فال/وال/ولل...) حتى نُطابق
    الجذر. مثلاً «للمشاريع»→«مشاريع»، «وبالبحث»→«بحث»، «والاستاذ»→«استاذ».
    لا يُزيل لو كان الباقي قصيرًا (تفادي الإفراط).
    [v3.2.0] الكلمات المحمية (الله/والله/بالله/اللي/اللى...) لا تُلمس — تقشيرها
    كان يُنتج إشارات خاطئة («له»/«لي») من كلمات وظيفية شائعة جدًا."""
    if not word:
        return word
    if word in _PROTECTED_WORDS:
        return word
    changed = True
    # try stripping up to 2 prefix layers (e.g. «ولل» → «لل» → root)
    for _ in range(2):
        changed = False
        for p in _ARABIC_PREFIXES:
            if word.startswith(p) and len(word) - len(p) >= 2:
                word = word[len(p):]
                changed = True
                break
        if not changed:
            break
    return word


_TOKEN_SPLIT_RE = re.compile(r'[\s\.,،؟!؟؛;:\-—–()«»\[\]/\\]+')


_COLLAPSE_DOUBLE_RE = re.compile(r'([أ-ي])\1{2,}')


def _tokens(normalized: str) -> List[str]:
    """يُقسّم النص المُطبّع إلى tokens: تقشير البوادئ ثم طيّ الحروف المضاعفة
    المفرطة (3+→1). ترتيب التقشير قبل الطيّ ضروري حتى لا يُطبّ «لل»→«ل» فلا
    يُتعرّف عليها تقشير البوادئ («للمشاريع»→strip«لل»→«مشاريع»).
    نطوي 3+ فقط (لا 2) لأن العربية لها جزم شرعي بكلمات مثل «متخصص»/«ممتاز»
    تكتب بحرفين متطابقين — 2 طيّها يكسرها. لا توجد كلمة عربية صحيحة بثلاثة
    حروف متطابقة متتالية، فالطيّ 3+ آمن ويعالج الإفراط في الطباعة («مشروووع»).
    """
    if not normalized:
        return []
    raw = _TOKEN_SPLIT_RE.split(normalized)
    out = []
    for tok in raw:
        if not tok:
            continue
        tok = _strip_arabic_prefix(tok)
        tok = _COLLAPSE_DOUBLE_RE.sub(r'\1', tok)
        out.append(tok)
    return out


# ============================================================
# [v3.0.1] Possessive pronoun suffixes (clitic-on-noun forms)
# تُلصق بالأسماء لإظهار الملكية: مشروعي (my project)، واجبك (your)،
# تقريره (his)، مشروعها (her)، بحوثنا (our)، تكاليفكم (your-pl)،
# مشاريعهم (their). لو لم تُنزع فلن تُطابق الاسم الجذر في SERVICE_TERMS
# أو PERSON_WORDS أو PLURAL_SERVICE_NOUNS. هذا كان سبب رفض «محتاج شخص
# ينجز مشروعي» (REJECT no_academic_service) رغم أنه طلب تنفيذ صريح.
# الأمان: نُزيل أطول لاحقة مناسبة أولاً (longest-first)، فقط لو كان
# الجذر الناتج >= 3 حروف والـ token >= 4 حروف. لا نُزيل «ي» من كلمة قصيرة
# (مثل «تقرير» نفسها لا تنتهي بي). هذا آمن لأن المطابقة لاحقًا تتطلب
# تساويًا تامًا مع vocab entry — أي تطابق خاطئ محدود بقائمة vocab نفسها.
#
# [v3.1.0] REMOVED "ه" (his/her possessive) from the suffix list — too
# ambiguous with taa marbuta (the feminine marker ة which normalize_text
# converts to ه). "مدرسه" (school) was being stripped to "مدرس" (teacher),
# causing massive FPs via role detection. Same for "دكتوره" (the female
# doctor) → "دكتور". Multi-char suffixes (يهم/كم/نا/ها) are kept since they
# cannot be confused with taa marbuta. The rare legitimate "تقريره" (his
# report) case is acceptable loss vs the FPs fixed. "ي" (my) and "ك" (your)
# are kept — they are unambiguous single-letter possessives.
# ============================================================
_POSSESSIVE_SUFFIXES_LONGEST_FIRST = (
    'هما', 'كما', 'كلن',     # dual / your-pl-f
    'ينا', 'يها', 'يكم', 'يكن', 'يهم', 'يهن', 'يكما', 'يهما',  # ي+...
    'كم', 'هم', 'هن', 'نا', 'ها',   # your-pl-m / their-m / their-f / our / her
    'يك',               # ي+ك (rare in writing but appears)
    'ك', 'ي',            # your / my (single-letter). REMOVED 'ه' (v3.1.0)
)
# [v3.2.0] REMOVED 'يه' — the adjective ending يه/ية ("شخصيه"=personal,
# "جامعيه"=university-adj) was being stripped as if it were a possessive
# pronoun: «المقابله الشخصيه» → root «شخص» → FALSE person-word match.
# Production FP #14 (a pure informational statement about a military
# institute) was accepted partly because «الشخصيه» produced the person
# token «شخص». Real possessives never end a service noun with 'يه'
# ("واجبيه"/"مشروعيه" are not written forms) — safe removal.


def _strip_possessive_suffix(token: str) -> Optional[str]:
    """يُرجع الجذر بعد نزع لاحقة الملكية، أو None لو لا لاحقة آمنة.
    «مشروعي»→«مشروع»، «واجبنا»→«واجب»، «تقريركم»→«تقرير»، «بحوثهم»→«بحوث».
    """
    if not token or len(token) < 4:
        return None
    for suf in _POSSESSIVE_SUFFIXES_LONGEST_FIRST:
        if token.endswith(suf):
            root = token[:-len(suf)]
            if len(root) >= 3:
                return root
            break  # longest-match-found-but-too-short → don't try shorter
    return None


def _token_root_set(toks: List[str]) -> set:
    """يبني مجموعة (set) من كل الجذور الممكنة لكل token:
    الـ token نفسه + الـ root بعد نزع لاحقة الملكية (لو آمن).
    يُستخدم لمطابقة token-equality آمنة (no substring false-positives).

    [v3.2.0] الكلمات المحمية (اللي/اللى/الله/والله/بالله...) تُستبعد نهائيًا
    من الـroots. مجرد حمايتها من تقشير البادئة في _strip_arabic_prefix لم
    يكن كافيًا: فرع المطابقة ('ال'+norm) in roots في _match_pairs كان يبني
    «اللي» نصيًا من norm='لي' ويطابقها → إشارة outsource خاطئة رغم الحماية
    (production FP #9: «المحاضرات اللي عن بعد» أنتجت 'لي'). الاستبعاد الكامل
    يقطع المسارين معًا: norm مباشر و ('ال'+norm)."""
    out = set()
    for t in toks:
        if not t or t in _PROTECTED_WORDS:
            continue
        out.add(t)
        root = _strip_possessive_suffix(t)
        if root is not None and root not in _PROTECTED_WORDS:
            out.add(root)
    return out


def _match_pairs(pairs, normalized: str) -> List[str]:
    """يرجع قائمة العبارات الأصلية التي تطابقت.

    للعبارات أحادية المُفرَدة: نُقسّم النص إلى tokens ونُزيل البوادئ العربية
    (ال/لل/بال/كال...) ثم نُطابق الجذر. هذا يلتقط «للمشاريع»→«مشاريع»،
    «وبالبحث»→«بحث»، ويتجنّب مطابقة كلمة قصيرة داخل كلمة أخرى («مين» داخل
    «تأمين»، «من» داخل «المن»).

    [v3.0.1] نقبل أيضًا تطابق root form بعد نزع لاحقة الملكية:
    «مشروعي»→«مشروع»، «واجبكم»→«واجب». آمن لأن المطابقة تساويًا تامًا على
    الجذر، لا substring.

    للعبارات متعددة المُفرَدات: نُبقي substring (محدّدة بما يكفي).
    """
    out = []
    toks = None  # lazy
    roots = None  # lazy
    for orig, norm in pairs:
        if not norm:
            continue
        if ' ' in norm:
            if norm in normalized:
                out.append(orig)
        else:
            if toks is None:
                toks = _tokens(normalized)
                roots = _token_root_set(toks)
            # bare token OR ال-prefixed (already handled by strip)
            # OR possessive-stripped root form (مشروعي → مشروع)
            if norm in roots or ('ال' + norm) in roots:
                out.append(orig)
    return out


def _match_plural_noun(pairs, normalized: str) -> List[str]:
    """للأسماء الجمعية (بحوث/تقارير/خدمات) — matching via tokens
    (بعد تقشير البوادئ) + [v3.0.1] نزع لاحقة الملكية («بحوثنا»→«بحوث»)."""
    out = []
    toks = _tokens(normalized)
    roots = _token_root_set(toks)
    for orig, norm in pairs:
        if not norm:
            continue
        if norm in roots:
            out.append(orig)
    return out


# ضمائر المفعول المرفقة بالفعل (يساعدني/يحلّه/ينجزهم/يكتبها/...)
_EXEC_PRONOUN_SUFFIX = r'(?:ني|نه|كم|كن|هم|هن|هما|كما|نا|ها|ه|ك)?'

# [v3.2.0] نفس الضمائر كـ set — لمطابقة token-start الآمنة (انظر أدناه)
_EXEC_SUFFIX_SET = {
    '', 'ني', 'نه', 'كم', 'كن', 'هم', 'هن', 'هما', 'كما', 'نا', 'ها', 'ه', 'ك',
}


def _match_exec_verbs(pairs, normalized: str) -> List[str]:
    """يطابق أفعال التنفيذ مع السماح بضمائر المفعول المرفقة:
    «يساعدني»→يساعد، «يحله»→يحل، «ينجزهم»→ينجز، «يكتبها»→يكتب.

    [v3.2.0] REPLACED regex-anywhere matching with TOKEN-START matching.
    الـregex القديم (re.search على النص كاملًا) كان يطابق الفعل في وسط أي
    كلمة: «الدكتور **بيشرح** منها» (صيغة المستقبل ب+يشرح) طابقت «يشرح» →
    إشارة تنفيذ خاطئة → قبول رسالة طلب كتب/سلايدات (FP #5). الآن: نقسم
    النص tokens، نزيل «و» العطف من بداية الـtoken فقط («ويعلمني»→«يعلمني» ✓)،
    ثم نشترط أن يبدأ الـtoken بالفعل وأن يكون الباقي ضمير مفعول صالحًا (أو
    فارغًا):
      «يشرح» ✓  «يشرحها» ✓  «ويعلمني» ✓  «يسويه» ✓
      «بيشرح» ✗ (يبدأ بـ«ب»)  «المشرح» ✗  «تشرح» ✗
    المطابقة الجديدة subset صارم من القديمة — لا تضيف أي تطابق جديد،
    فقط تمنع المطابقات داخل الكلمات."""
    out = []
    toks = _tokens(normalized)
    # strip the waw-conjunction from token starts (ويعلمني → يعلمني)
    candidates = set()
    for t in toks:
        if not t:
            continue
        candidates.add(t)
        if t.startswith('و') and len(t) > 1:
            candidates.add(t[1:])
    for orig, norm in pairs:
        if not norm:
            continue
        for cand in candidates:
            if cand.startswith(norm):
                rest = cand[len(norm):]
                if rest in _EXEC_SUFFIX_SET:
                    out.append(orig)
                    break
    return out


# ============================================================
# RequestAnalysis — نتيجة تشخيصية كاملة + توافق خلفي مع bot.py
# ============================================================
# RequestAnalysis — نتيجة v4.0 (AI-first) + توافق خلفي مع bot.py
# ============================================================
@dataclass
class RequestAnalysis:
    """نتيجة تحليل v4.0. القرار من الـAI (أو رفض صارم عند فشله).

    v4 core:
      accepted/is_request = (AI decision == ACCEPT) AND (confidence >= threshold)
      أي فشل/غموض/غياب AI = REJECT. لا قرار من الكلمات المفتاحية أبدًا.
    legacy fields (matched_*, seeker/provider_confidence) تبقى كتشخيص
    إشارات فقط (تُستخدم في alert التنسيق والسجلات).
    """

    # --- v4 core decision ---
    accepted: bool = False
    confidence: float = 0.0          # ثقة الـAI (0..1)
    threshold: float = 0.85          # عتبة القبول المطبَّقة
    decision_path: str = ""          # ai | semantic_dedup | relay_wrapper | empty | no_classifier
    ai_ok: bool = False              # هل اكتمل نداء AI بنجاح
    ai_decision: str = ""            # ACCEPT | REJECT (خام من النموذج)
    ai_category: str = ""
    ai_reason: str = ""
    ai_model: str = ""
    ai_provider: str = ""
    ai_latency_ms: int = 0
    ai_error: str = ""
    dedup_kind: str = ""             # exact | semantic | near
    text_hash: str = ""

    # --- diagnostic signal lists (hints — ليست قرارًا) ---
    requester_signals: List[str] = field(default_factory=list)
    execution_signals: List[str] = field(default_factory=list)
    service_signals: List[str] = field(default_factory=list)
    provider_signals: List[str] = field(default_factory=list)
    ad_signals: List[str] = field(default_factory=list)
    rejection_signals: List[str] = field(default_factory=list)

    # --- contact / obfuscation diagnostics ---
    has_phone: bool = False
    has_contact_url: bool = False
    has_at_handle: bool = False
    has_dotted_word: bool = False
    has_many_lines: bool = False

    # --- legacy compat (bot.py logs + alert formatting) ---
    is_request: bool = False
    intent_type: str = "unclassified"
    service: Optional[str] = None
    reason: str = "unclassified"
    matched_intents: List[str] = field(default_factory=list)
    matched_services: List[str] = field(default_factory=list)
    matched_patterns: List[str] = field(default_factory=list)
    matched_indicators: List[str] = field(default_factory=list)
    seeker_confidence: int = 0
    provider_confidence: int = 0
    has_question_form: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """v4 + legacy diagnostic dict (logs + /api/filter_stats)."""
        extra_ad = []
        if self.has_dotted_word:
            extra_ad.append("(dotted_word_obfuscation)")
        if self.has_many_lines:
            extra_ad.append("(multi_line_six_plus)")
        return {
            # v4 core
            "version": FILTER_VERSION,
            "accepted": self.accepted,
            "confidence": self.confidence,
            "threshold": self.threshold,
            "decision_path": self.decision_path,
            "ai_ok": self.ai_ok,
            "ai_decision": self.ai_decision,
            "ai_category": self.ai_category,
            "ai_reason": self.ai_reason,
            "ai_model": self.ai_model,
            "ai_latency_ms": self.ai_latency_ms,
            "ai_error": self.ai_error,
            "dedup_kind": self.dedup_kind,
            "text_hash": self.text_hash,
            "reason": self.reason,
            "intent_type": self.intent_type,
            # signal diagnostics
            "requester_signals": self.requester_signals,
            "service_signals": self.service_signals,
            "execution_signals": self.execution_signals,
            "provider_signals": self.provider_signals,
            "rejection_signals": self.rejection_signals,
            # legacy (bot.py reads these)
            "is_request": self.is_request,
            "matched_intents": self.matched_intents,
            "matched_services": self.matched_services,
            "matched_patterns": self.matched_patterns,
            "matched_indicators": self.matched_indicators,
            "seeker_confidence": self.seeker_confidence,
            "provider_confidence": self.provider_confidence,
            "advertisement_matches": self.ad_signals + extra_ad,
            "has_question_form": self.has_question_form,
            "has_phone": self.has_phone,
            "has_contact_url": self.has_contact_url,
            "has_at_handle": self.has_at_handle,
            "has_dotted_word": self.has_dotted_word,
            "has_many_lines": self.has_many_lines,
        }


# ============================================================
# SignalReport — استخراج الإشارات (الكلمات المفتاحية «demoted»)
# ============================================================
@dataclass
class SignalReport:
    """إشارات لغوية مستخرجة آليًا — تُمرَّر للـAI كـhints مساعدة فقط.

    القاعدة الصريحة (طلب المُشغّل): الكلمات إشارة، ليست حكمًا. لا يوجد أي
    مسار في v4.0 يحوّل هذه الإشارات إلى قرار قبول/رفض.
    """

    person_signals: List[str] = field(default_factory=list)
    requester_signals: List[str] = field(default_factory=list)
    execution_signals: List[str] = field(default_factory=list)
    ownership_signals: List[str] = field(default_factory=list)
    service_signals: List[str] = field(default_factory=list)
    provider_signals: List[str] = field(default_factory=list)
    ad_signals: List[str] = field(default_factory=list)
    info_signals: List[str] = field(default_factory=list)
    resource_signals: List[str] = field(default_factory=list)
    recommend_signals: List[str] = field(default_factory=list)
    outsource_signals: List[str] = field(default_factory=list)
    delegation_signals: List[str] = field(default_factory=list)
    role_signals: List[str] = field(default_factory=list)
    ready_made_signals: List[str] = field(default_factory=list)
    contact_info_signals: List[str] = field(default_factory=list)
    decision_signals: List[str] = field(default_factory=list)
    person_status_signals: List[str] = field(default_factory=list)
    plural_noun_signals: List[str] = field(default_factory=list)

    has_phone: bool = False
    has_contact_url: bool = False
    has_at_handle: bool = False
    has_dotted_word: bool = False
    has_many_lines: bool = False
    has_question_form: bool = False

    normalized: str = ""

    def to_hints(self, max_per_list: int = 5) -> Dict[str, Any]:
        """hints مضغوطة للـAI prompt (bounded — لا نرسل قوائم ضخمة)."""
        def _b(lst: List[str]) -> List[str]:
            return lst[:max_per_list]
        return {
            "person_words": _b(self.person_signals),
            "requester_phrases": _b(self.requester_signals),
            "execution_verbs": _b(self.execution_signals),
            "academic_services": _b(self.service_signals),
            "provider_phrases": _b(self.provider_signals),
            "ad_phrases": _b(self.ad_signals),
            "rejection_hints": _b(
                self.info_signals + self.resource_signals
                + self.recommend_signals + self.contact_info_signals
                + self.decision_signals + self.person_status_signals),
            "contact": {
                "has_phone": self.has_phone,
                "has_contact_url": self.has_contact_url,
                "has_at_handle": self.has_at_handle,
                "many_lines": self.has_many_lines,
            },
        }


def extract_signals(text: str) -> SignalReport:
    """[v4.0] يستخرج كل الإشارات اللغوية من النص — بلا أي قرار.

    هذا هو محرّك الكلمات المفتاحية القديم (v3.2.0) بعد خفض رتبته:
    كان يقرر القبول/الرفض عبر Hard Gates؛ الآن يُنتج hints فقط
    تُمرَّر للـAI (المرحلة 2/3) وتُسجَّل للتشخيص (المرحلة 5).
    """
    rep = SignalReport()
    if not text or not text.strip():
        return rep

    normalized = normalize_text(text)
    rep.normalized = normalized

    rep.person_signals = _match_pairs(_PERSON_PAIRS, normalized)
    rep.requester_signals = _match_pairs(_REQUESTER_PAIRS, normalized)
    rep.execution_signals = _match_exec_verbs(_EXEC_PAIRS, normalized)
    rep.ownership_signals = _match_pairs(_OWNERSHIP_PAIRS, normalized)
    rep.service_signals = _match_pairs(_SERVICE_PAIRS, normalized)
    rep.provider_signals = _match_pairs(_PROVIDER_PAIRS, normalized)
    rep.ad_signals = _match_pairs(_AD_PAIRS, normalized)
    rep.info_signals = _match_pairs(_INFO_PAIRS, normalized)
    rep.resource_signals = _match_pairs(_RESOURCE_PAIRS, normalized)
    rep.recommend_signals = _match_pairs(_RECOMMEND_PAIRS, normalized)
    rep.outsource_signals = _match_pairs(_OUTSOURCE_PAIRS, normalized)
    rep.delegation_signals = _match_pairs(_DELEGATION_PAIRS, normalized)
    rep.role_signals = _match_pairs(_ROLE_PAIRS, normalized)
    rep.ready_made_signals = _match_pairs(_READY_MADE_PAIRS, normalized)
    rep.contact_info_signals = _match_pairs(_CONTACT_INFO_PAIRS, normalized)
    rep.decision_signals = _match_pairs(_DECISION_PAIRS, normalized)
    rep.person_status_signals = _match_pairs(_PERSON_STATUS_PAIRS, normalized)
    rep.plural_noun_signals = _match_plural_noun(_PLURAL_NOUN_PAIRS, normalized)

    rep.has_phone = _has_phone_number(text)
    rep.has_contact_url = _has_contact_url(text)
    rep.has_at_handle = _has_at_handle(text)
    rep.has_dotted_word = _has_dotted_word(text)
    rep.has_many_lines = _has_many_lines(text)
    rep.has_question_form = (
        '؟' in text or '?' in text
        or bool(rep.requester_signals) or bool(rep.person_signals)
    )
    return rep


# ============================================================
# [v4.0] Structural duplicate gates (ليست تصنيفًا دلاليًا)
# ============================================================
_RELAY_WRAPPER_KEYS = ("نص الرساله", "نص الرسالة")
_RELAY_WRAPPER_KEYS2 = ("رابط الرساله", "رابط الرسالة", "المرسل")


def _is_relay_wrapper(canonical: str, raw_text: str) -> bool:
    """[منقول من v3.2.0 Gate 0] كشف نسخ البوتات الناقلة (relay reposts).

    البوت الناقل يُعيد نشر الرسائل بصيغة غلاف ثابتة:
    «المرسل : ...\nالاسم : ...\nID ...\nنص الرساله : ...\nرابط الرساله : https://t.me/...»
    هذه نسخة مكررة من الأصل (الذي عُوِّل من مصدره) → REJECT هيكلي قبل
    نداء AI (يوفر التكلفة). هذا كشف بنية، ليس تصنيف نية بكلمات مفتاحية.
    """
    t = canonical or normalize_text(raw_text or "")
    if not t:
        return False
    has_body = any(k in t for k in _RELAY_WRAPPER_KEYS)
    has_meta = any(k in t for k in _RELAY_WRAPPER_KEYS2)
    return bool(has_body and has_meta)


def _provider_confidence_int(sig: SignalReport) -> int:
    """[legacy diagnostic] نقاط provider كتشخيص فقط (لا يقرر شيئًا في v4).
    نفس معادلة v3.2.0 (بما فيها bonus التشويش بالنقاط) — للتوافق التشخيصي."""
    prov = len(sig.provider_signals) * 4 + min(len(sig.ad_signals), 3) * 2
    if sig.has_at_handle:
        prov += 3
    if sig.has_phone and sig.has_at_handle:
        prov += 4
    if (sig.has_phone or sig.has_contact_url or sig.has_at_handle) and prov > 0:
        prov += 1
    if sig.has_dotted_word:
        prov += 2
    if sig.has_many_lines:
        prov += 1
    return prov


def _seeker_confidence_int(sig: SignalReport, accepted: bool, ai_conf: float) -> int:
    """[legacy diagnostic] نسبة طلب 0-100 للتشخيص فقط."""
    if accepted:
        return int(round(min(0.99, ai_conf) * 100))
    base = 0
    if sig.requester_signals:
        base += 40
    if sig.person_signals and sig.execution_signals:
        base += 20
    if sig.service_signals:
        base += 10
    return min(base, 70)


# ============================================================
# v4.0 Orchestrator — المراحل 1→2/3→4→5
# ============================================================
async def analyze_request_v4(
    text: str,
    classifier=None,
    *,
    chat_id: int = 0,
    msg_id: int = 0,
    source_phone: str = "",
    decision_logger=None,
    deduper=None,
    threshold: float = 0.85,
) -> RequestAnalysis:
    """يحلل الرسالة عبر Intent Classification Engine v4.0.

    المسار:
      المرحلة 1  text_normalizer.normalize → clean (للـAI) + canonical (للـhash)
      بوابات هيكلية: empty / relay-wrapper (نسخة بوت ناقل مكررة)
      المرحلة 4  semantic dedup pre-check → REJECT duplicate (يوفر نداء AI)
      المرحلة 2/3 AI classify (clean + hints من extract_signals) → JSON قرار
      العتبة      ACCEPT فقط إذا confidence >= threshold (default 0.85)
      المرحلة 4  register البصمة (كل رسالة صُنِّفت)
      المرحلة 5  log_decision (non-fatal)

    الفشل دائمًا REJECT: لا AI / timeout / parse error / low confidence =
    REJECT بأسباب صريحة (ai_unavailable / ai_error / low_confidence).
    لا يوجد أي مسار قرار بالكلمات المفتاحية.
    """
    from text_normalizer import normalize as _normalize_stage1
    from filter_store import text_hash_of

    res = RequestAnalysis(threshold=threshold)

    # ---- بوابة 0: نص فارغ ----
    if not text or not text.strip():
        res.reason = "empty"
        res.intent_type = "empty"
        res.decision_path = "empty"
        await _log_decision_safe(decision_logger, text or "", res, chat_id, msg_id, source_phone)
        return res

    # ---- المرحلة 1: تنظيف أولي ----
    nt = _normalize_stage1(text)
    if not nt.clean or not nt.clean.strip():
        res.reason = "empty_after_normalize"
        res.intent_type = "empty"
        res.decision_path = "empty"
        res.text_hash = text_hash_of(text)
        await _log_decision_safe(decision_logger, text, res, chat_id, msg_id, source_phone)
        return res
    res.text_hash = text_hash_of(text)

    # ---- بوابات هيكلية: relay-wrapper (نسخة بوت ناقل) ----
    if _is_relay_wrapper(nt.canonical, text):
        res.reason = "relay_bot_repost_duplicate"
        res.intent_type = "relay_repost"
        res.decision_path = "relay_wrapper"
        res.confidence = 0.01
        await _log_decision_safe(decision_logger, text, res, chat_id, msg_id, source_phone)
        return res

    # ---- المرحلة 4 (pre): semantic dedup ----
    if deduper is not None and nt.canonical:
        dup = deduper.check(nt.canonical)
        if dup.is_dup:
            res.reason = "semantic_duplicate"
            res.intent_type = "duplicate"
            res.decision_path = "semantic_dedup"
            res.dedup_kind = dup.kind
            res.confidence = 0.0
            await _log_decision_safe(
                decision_logger, text, res, chat_id, msg_id, source_phone,
                dedup_kind=dup.kind)
            return res

    # ---- استخراج الإشارات (hints — demoted keyword engine) ----
    sig = extract_signals(text)

    # ---- بوابات تشخيصية على الإشارات (fill legacy fields للتنبيهات) ----
    res.requester_signals = sig.requester_signals + sig.person_signals
    res.execution_signals = sig.execution_signals
    res.service_signals = sig.service_signals
    res.provider_signals = sig.provider_signals
    res.ad_signals = sig.ad_signals
    res.rejection_signals = (
        sig.info_signals + sig.resource_signals + sig.recommend_signals
        + sig.contact_info_signals + sig.decision_signals + sig.person_status_signals
    )
    res.matched_intents = res.requester_signals
    res.matched_services = sig.service_signals
    res.matched_patterns = sig.execution_signals
    res.matched_indicators = sig.ownership_signals
    res.has_phone = sig.has_phone
    res.has_contact_url = sig.has_contact_url
    res.has_at_handle = sig.has_at_handle
    res.has_dotted_word = sig.has_dotted_word
    res.has_many_lines = sig.has_many_lines
    res.has_question_form = sig.has_question_form
    res.provider_confidence = _provider_confidence_int(sig)
    res.service = _classify_service(sig.normalized, sig.execution_signals)

    # ---- المرحلة 2/3: AI Intent Classification (القرار الأساسي) ----
    if classifier is None:
        # لا مصنِّف (لا مفاتيح AI) → REJECT صارم. لا keyword fallback.
        res.reason = "ai_classifier_not_configured"
        res.intent_type = "ai_unavailable"
        res.decision_path = "no_classifier"
        res.ai_category = "ai_unavailable"
        res.ai_reason = "no AI providers configured (REQUEST_FILTER cannot accept without AI)"
        res.ai_error = "no AI providers configured (OPENAI_API_KEY / AI_KEY_2..8 all empty)"
        res.seeker_confidence = _seeker_confidence_int(sig, False, 0.0)
        await _log_decision_safe(decision_logger, text, res, chat_id, msg_id, source_phone,
                                 error_detail=res.ai_error)
        return res

    decision = await classifier.classify(nt.clean, hints=sig.to_hints())
    res.ai_ok = decision.ok
    res.ai_decision = decision.decision
    res.confidence = decision.confidence
    res.ai_category = decision.category
    res.ai_reason = decision.reason
    res.ai_model = decision.model
    res.ai_provider = decision.provider_name
    res.ai_latency_ms = decision.latency_ms
    res.ai_error = decision.error

    # ---- المرحلة 4 (register): كل رسالة صُنِّفت تُسجَّل بصمتها ----
    if deduper is not None and nt.canonical:
        deduper.register(nt.canonical)

    # ---- القرار + العتبة ----
    if not decision.ok:
        # فشل AI (timeout/error/parse/overloaded) → REJECT صارم بأسباب صريحة
        # [v4.1] error_detail: التفاصيل التقنية (http status + provider +
        # attempts/budget) تُخزَّن في filter_decisions — تشخيص بلا runtime logs.
        res.reason = decision.category or "ai_error"
        res.intent_type = decision.category or "ai_error"
        res.decision_path = "ai"
        res.seeker_confidence = _seeker_confidence_int(sig, False, 0.0)
        await _log_decision_safe(decision_logger, text, res, chat_id, msg_id, source_phone,
                                 error_detail=decision.error or "")
        return res

    accepted = (decision.decision == "ACCEPT") and (decision.confidence >= threshold)

    if accepted:
        res.accepted = True
        res.is_request = True
        res.decision_path = "ai"
        res.intent_type = decision.category
        res.reason = decision.reason
        res.seeker_confidence = _seeker_confidence_int(sig, True, decision.confidence)
    else:
        res.accepted = False
        res.is_request = False
        res.decision_path = "ai"
        res.intent_type = decision.category
        if decision.decision == "ACCEPT" and decision.confidence < threshold:
            res.reason = "low_confidence"
        else:
            res.reason = decision.reason
        res.seeker_confidence = _seeker_confidence_int(sig, False, decision.confidence)

    await _log_decision_safe(decision_logger, text, res, chat_id, msg_id, source_phone)
    return res


async def _log_decision_safe(decision_logger, text: str, res: "RequestAnalysis",
                             chat_id: int, msg_id: int, source_phone: str,
                             dedup_kind: str = "",
                             error_detail: str = "") -> None:
    """المرحلة 5: كتابة القرار في filter_decisions — non-fatal دائمًا.

    [v4.1] error_detail: تفاصيل فشل AI التقنية (http status + provider +
    attempts/budget) — تُقرأ من /api/filter_stats للتشخيص الجذري.
    """
    if decision_logger is None:
        return
    try:
        await decision_logger.log_decision(
            chat_id=chat_id,
            message_id=msg_id,
            raw_text=text or "",
            decision="ACCEPT" if res.accepted else "REJECT",
            confidence=res.confidence,
            category=(res.intent_type or res.reason or "")[:64],
            reason=(res.reason or "")[:200],
            model=(res.ai_model or "")[:64],
            latency_ms=res.ai_latency_ms,
            dedup_kind=dedup_kind or res.dedup_kind,
            source_phone=source_phone,
            error_detail=(error_detail or res.ai_error or "")[:250],
        )
    except Exception:
        # فشل التشخيص لا يكسر المسار أبدًا
        pass


# ============================================================
# Backward-compat sync wrappers — إشارات فقط، ليست قرارًا
# ============================================================
def analyze_request(text: str) -> RequestAnalysis:
    """[DEPRECATED in v4.0] الواجهة القديمة (تزامنية بلا AI).

    في v4.0 القرار الأساسي يصدر عن الـAI فقط (analyze_request_v4 async).
    هذه الدالة تُعيد تقرير الإشارات بلا قرار: is_request=False دائمًا
    وreason=v4_ai_required. محفوظة لتوافق الاستيرادات القديمة —
    أي استخدام لها كقرار نهائي هو خطأ معماري.
    """
    res = RequestAnalysis()
    if not text or not text.strip():
        res.reason = "v4_ai_required"
        res.intent_type = "empty"
        return res
    sig = extract_signals(text)
    res.requester_signals = sig.requester_signals + sig.person_signals
    res.execution_signals = sig.execution_signals
    res.service_signals = sig.service_signals
    res.provider_signals = sig.provider_signals
    res.ad_signals = sig.ad_signals
    res.rejection_signals = (
        sig.info_signals + sig.resource_signals + sig.recommend_signals
        + sig.contact_info_signals + sig.decision_signals + sig.person_status_signals
    )
    res.matched_intents = res.requester_signals
    res.matched_services = sig.service_signals
    res.matched_patterns = sig.execution_signals
    res.matched_indicators = sig.ownership_signals
    res.has_phone = sig.has_phone
    res.has_contact_url = sig.has_contact_url
    res.has_at_handle = sig.has_at_handle
    res.has_dotted_word = sig.has_dotted_word
    res.has_many_lines = sig.has_many_lines
    res.has_question_form = sig.has_question_form
    res.provider_confidence = _provider_confidence_int(sig)
    res.reason = "v4_ai_required"
    res.intent_type = "signals_only_no_decision"
    return res


def is_service_seeker(text: str) -> bool:
    """[v4.0 — signal helper] هل توجد إشارات طالب خدمة؟ (ليست قرارًا)."""
    return bool(analyze_request(text).requester_signals)


def is_service_provider(text: str) -> bool:
    """[v4.0 — signal helper] هل توجد إشارات مقدّم خدمة/إعلان؟ (ليست قرارًا)."""
    rep = analyze_request(text)
    return bool(rep.provider_signals or rep.ad_signals)


def is_request_message(text: str) -> Tuple[bool, Dict[str, Any]]:
    """[DEPRECATED in v4.0] compatibility wrapper — يُرجع دائمًا (False, dict).

    القرار الفعلي: await analyze_request_v4(text, classifier, ...).
    """
    res = analyze_request(text)
    return False, res.to_dict()
