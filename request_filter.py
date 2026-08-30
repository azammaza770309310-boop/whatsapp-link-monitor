#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
request_filter.py — Request Intent Engine v3.0 (Hard-Gated)
============================================================
إعادة بناء جذرية. ليس Keyword Filter، بل Intent Engine بمراحل قرار
واضحة (Hard Gates). يحل مشكلة الـ Keyword Filter القديم الذي كان يقبل
الرسائل لمجرد احتوائها على كلمات مثل «بحث»/«مشروع»/«تقرير»/«شرح»،
مما سبب آلاف الرسائل الخاطئة.

الهدف الحقيقي: التقاط الأشخاص الذين يطلبون من شخص آخر تنفيذ خدمة
أكاديمية لهم أو مساعدتهم مباشرة في إنجاز عمل أكاديمي.
لا يكفي وجود كلمة أكاديمية. كلمة خدمة منفردة = REJECT دائمًا.

Hard Gates (بالتسلسل — لا تراكم نقاط للوصول للقبول):
  GATE 1  provider/advertisement؟                  → REJECT provider_ad
  GATE 2  info/resource/long-content WITHOUT
          person+execution؟                          → REJECT (information_request
                                                       | resource_seeking
                                                       | long_informational_content)
  GATE 3  شخص مطلوب + علاقة تنفيذ؟                  → continue (else REJECT no_person_executor)
  GATE 4  خدمة أكاديمية (أو فعل implies خدمة)؟      → continue (else REJECT no_academic_service)
  GATE 5  long informational override                → REJECT long_informational_content
  GATE 6                                          → ACCEPT (service_execution_request
                                                       | person_for_academic_help)

النتيجة التشخيصية RequestAnalysis.to_dict():
  {
    "accepted": bool,
    "confidence": float 0-1,
    "intent_type": str,
    "service": str|None,
    "requester_signals": [...],
    "service_signals": [...],
    "execution_signals": [...],
    "rejection_signals": [...],
    "reason": str,
    # legacy compat (bot.py)
    "is_request": bool,
    "matched_intents": [...], "matched_services": [...], "matched_patterns": [...],
    "seeker_confidence": int, "provider_confidence": int,
  }

مبادئ صارمة:
  - SERVICE وحده = REJECT دائمًا («بحث»، «عندي بحث»، «البحث صعب»).
  - لا تراكم كلمات للقبول (لا «شرح+اختبار+بحث = 9 نقاط»). Hard Gates أولاً.
  - default = REJECT عند الغموض.
  - info/resource/recommendation = REJECTION signals (إلا مع person+execution).
  - المسار مستقل: Link Extractor → CHANNEL_ID لا يُلمس. Capture-First محفوظ.
"""

import re
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Any, Optional

FILTER_VERSION = "v3.0.1"
FILTER_MODE = "intent_engine_hard_gates"


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
PERSON_WORDS: List[str] = [
    "أحد", "احد", "حد",                 # someone (formal + Gulf dialect)
    "شخص", "واحد",                      # person / one
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
    "تعرفون أحد", "تعرفون احد", "تعرفون شخص", "تعرف أحد", "تعرف احد",
    "تعرف شخص", "تعرفي أحد", "تعرفي شخص",
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
    "أحد يعرف", "احد يعرف", "شخص يعرف",
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
    "يساعد", "يضبط", "يشرح", "يدرس", "يعلم", "يذاكر", "ينقح",
    "يرتبه", "يخلصه", "ينجزه", "يسويه", "يحله", "يكتبه", "يجهزه",
]

# أفعال تنفيذ هي نفسها خدمة أكاديمية (يشرح=تدريس، يراجع=مراجعة، يحل=حل)
# لو ظهرت مع requester بلا خدمة صريحة، تُغني عن خدمة (Gate 4).
EXEC_IMPLIES_SERVICE: Dict[str, str] = {
    "يشرح": "teaching",
    "يدرس": "teaching",
    "يعلم": "teaching",
    "يراجع": "reviewing",
    "يحل": "solving",
    "يذاكر": "studying",
}

# ============================================================
# [4] OWNERSHIP_NEED — ملكية/حاجة (المستخدم يملك العمل أو يحتاجه)
# ============================================================
OWNERSHIP_NEED: List[str] = [
    "عندي", "عندى", "علي", "على", "مطلوب مني", "لازم اسلم", "لازم أسلم",
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
RECOMMENDATION_SEEKING: List[str] = [
    "افضل", "أفضل", "الافضل", "الأفضل", "اقوى", "أقوى",
    "مين افضل", "من افضل", "مين أفضل", "من أفضل",
    "اقترح", "أقترح", "يقترح", "اقتراح", "اقتراحات",
    "يرشح", "رشح", "رشح لي", "ترشيح",
    "توصية", "يوصي", "وصي",
    "ينصح", "نصيحة", "نصح", "استشارة",
    "وش رائيكم", "وش رايكم", "ايش رائيكم", "ايش رايكم",
    "عطوني راي", "اعطوني راي",
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
    "انيط", "أنيط", "اسند", "أسند", "سند", "وكل", "أكل", "اكل",
]

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


_ARABIC_PREFIXES = [
    # compound prefixes (longest first to avoid partial strips)
    'وبال', 'وكال', 'ولل', 'فلل', 'بلل', 'كلل', 'وكلال', 'وبلال',
    # single-suffix compound
    'لل', 'بال', 'كال', 'فال', 'وال', 'ول', 'فل', 'بل', 'كل', 'وب', 'وك', 'وف',
    # definite article
    'ال',
]


def _strip_arabic_prefix(word: str) -> str:
    """يُزيل البوادئ العربية الشائعة (ال/لل/بال/كال/فال/وال/ولل...) حتى نُطابق
    الجذر. مثلاً «للمشاريع»→«مشاريع»، «وبالبحث»→«بحث»، «والاستاذ»→«استاذ».
    لا يُزيل لو كان الباقي قصيرًا (تفادي الإفراط)."""
    if not word:
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
# ============================================================
_POSSESSIVE_SUFFIXES_LONGEST_FIRST = (
    'هما', 'كما', 'كلن',     # dual / your-pl-f
    'ينا', 'يها', 'يكم', 'يكن', 'يهم', 'يهن', 'يكما', 'يهما',  # ي+...
    'كم', 'هم', 'هن', 'نا', 'ها',   # your-pl-m / their-m / their-f / our / her
    'يه', 'يك',               # ي+ه / ي+ك (rare in writing but appears)
    'ه', 'ك', 'ي',            # his / your / my (single-letter — riskiest, hence last)
)


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
    يُستخدم لمطابقة token-equality آمنة (no substring false-positives)."""
    out = set()
    for t in toks:
        if not t:
            continue
        out.add(t)
        root = _strip_possessive_suffix(t)
        if root is not None:
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


def _match_exec_verbs(pairs, normalized: str) -> List[str]:
    """يطابق أفعال التنفيذ مع السماح بضمائر المفعول المرفقة:
    «يساعدني»→يساعد، «يحله»→يحل، «ينجزهم»→ينجز، «يكتبها»→يكتب.
    نستخدم regex (لا token-equality) لأن الضمائر تُلصق بالفعل بلا مسافة."""
    out = []
    for orig, norm in pairs:
        if not norm:
            continue
        pat = re.escape(norm) + _EXEC_PRONOUN_SUFFIX
        if re.search(pat, normalized):
            out.append(orig)
    return out


# ============================================================
# RequestAnalysis — نتيجة تشخيصية كاملة + توافق خلفي مع bot.py
# ============================================================
@dataclass
class RequestAnalysis:
    """نتيجة تحليل Intent Engine. تشخيصية كاملة + متوافقة خلفيًا."""

    # --- Core decision (new) ---
    accepted: bool = False
    confidence: float = 0.0
    intent_type: str = "low_confidence"
    service: Optional[str] = None
    reason: str = "low_confidence"

    # --- Diagnostic signal lists ---
    requester_signals: List[str] = field(default_factory=list)
    execution_signals: List[str] = field(default_factory=list)
    service_signals: List[str] = field(default_factory=list)
    provider_signals: List[str] = field(default_factory=list)
    ad_signals: List[str] = field(default_factory=list)
    rejection_signals: List[str] = field(default_factory=list)

    # --- Contact / obfuscation diagnostics ---
    has_phone: bool = False
    has_contact_url: bool = False
    has_at_handle: bool = False
    has_dotted_word: bool = False
    has_many_lines: bool = False

    # --- Legacy compat (populated for bot.py logs) ---
    is_request: bool = False
    matched_intents: List[str] = field(default_factory=list)
    matched_services: List[str] = field(default_factory=list)
    matched_patterns: List[str] = field(default_factory=list)
    matched_indicators: List[str] = field(default_factory=list)
    seeker_confidence: int = 0
    provider_confidence: int = 0
    has_question_form: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Compatibility + diagnostic dict."""
        # backward-compat: advertisement_matches includes ad_signals + tags
        extra_ad = []
        if self.has_dotted_word:
            extra_ad.append("(dotted_word_obfuscation)")
        if self.has_many_lines:
            extra_ad.append("(multi_line_six_plus)")
        ad_matches = self.ad_signals + extra_ad
        return {
            # new diagnostic
            "accepted": self.accepted,
            "confidence": self.confidence,
            "intent_type": self.intent_type,
            "service": self.service,
            "requester_signals": self.requester_signals,
            "service_signals": self.service_signals,
            "execution_signals": self.execution_signals,
            "rejection_signals": self.rejection_signals,
            "reason": self.reason,
            # legacy (bot.py reads these)
            "is_request": self.is_request,
            "matched_intents": self.matched_intents,
            "matched_services": self.matched_services,
            "matched_patterns": self.matched_patterns,
            "matched_indicators": self.matched_indicators,
            "seeker_confidence": self.seeker_confidence,
            "provider_confidence": self.provider_confidence,
            "provider_signals": self.provider_signals,
            "advertisement_matches": ad_matches,
            "has_question_form": self.has_question_form,
            "has_phone": self.has_phone,
            "has_contact_url": self.has_contact_url,
            "has_at_handle": self.has_at_handle,
            "has_dotted_word": self.has_dotted_word,
            "has_many_lines": self.has_many_lines,
        }


# ============================================================
# Hard-Gated Intent Engine
# ============================================================
def analyze_request(text: str) -> RequestAnalysis:
    """يحلل الرسالة عبر Hard Gates ويرجع RequestAnalysis تشخيصية كاملة.

    قرار القبول (accepted=True) يتطلب:
      - NOT provider/ad (Gate 1)
      - NOT info/resource/long-content WITHOUT person+execution (Gate 2)
      - NOT recommendation WITHOUT strong execution (Gate 2.5)
      - person + execution relationship (Gate 3)
      - academic service OR exec-implies-service (Gate 4)
      - NOT long informational override (Gate 5)
    أي فشل في Gate = REJECT. default = REJECT عند الغموض.
    """
    res = RequestAnalysis()

    if not text or not text.strip():
        res.reason = "empty"
        res.intent_type = "empty"
        return res

    normalized = normalize_text(text)
    if not normalized:
        res.reason = "empty_after_normalize"
        res.intent_type = "empty"
        return res

    # ===== Signal detection =====
    person_tokens = _match_pairs(_PERSON_PAIRS, normalized)
    requester_phrases = _match_pairs(_REQUESTER_PAIRS, normalized)
    exec_verbs = _match_exec_verbs(_EXEC_PAIRS, normalized)
    ownership = _match_pairs(_OWNERSHIP_PAIRS, normalized)
    services = _match_pairs(_SERVICE_PAIRS, normalized)
    provider_sigs = _match_pairs(_PROVIDER_PAIRS, normalized)
    ad_sigs = _match_pairs(_AD_PAIRS, normalized)
    plural_nouns = _match_plural_noun(_PLURAL_NOUN_PAIRS, normalized)
    info_sigs = _match_pairs(_INFO_PAIRS, normalized)
    resource_sigs = _match_pairs(_RESOURCE_PAIRS, normalized)
    recommend_sigs = _match_pairs(_RECOMMEND_PAIRS, normalized)
    outsource_sigs = _match_pairs(_OUTSOURCE_PAIRS, normalized)
    delegation_verbs = _match_pairs(_DELEGATION_PAIRS, normalized)
    role_tokens = _match_pairs(_ROLE_PAIRS, normalized)
    ready_made = _match_pairs(_READY_MADE_PAIRS, normalized)

    # requester = explicit person phrase OR (person word + execution in same msg)
    # OR (person word + delegation verb — «أبي أوكل أحد»)
    has_requester = bool(requester_phrases) or (
        bool(person_tokens) and bool(exec_verbs)
    ) or (bool(person_tokens) and bool(delegation_verbs))
    has_execution = bool(exec_verbs) or bool(delegation_verbs)
    # professional role + ownership → implies service (teaching/consulting)
    role_implies_service = bool(role_tokens) and bool(ownership)

    # contact / obfuscation
    res.has_phone = _has_phone_number(text)
    res.has_contact_url = _has_contact_url(text)
    res.has_at_handle = _has_at_handle(text)
    res.has_dotted_word = _has_dotted_word(text)
    res.has_many_lines = _has_many_lines(text)

    # populate diagnostic lists
    res.requester_signals = requester_phrases + person_tokens
    res.execution_signals = exec_verbs
    res.service_signals = services
    res.provider_signals = provider_sigs
    res.ad_signals = ad_sigs
    res.rejection_signals = (
        info_sigs + resource_sigs + recommend_sigs
    )
    # legacy
    res.matched_intents = res.requester_signals
    res.matched_services = services
    res.matched_patterns = exec_verbs
    res.matched_indicators = ownership

    # has_question_form (legacy)
    res.has_question_form = (
        '؟' in text or '?' in text
        or has_requester
    )

    # provider_confidence (legacy int scale)
    prov_int = len(provider_sigs) * 4 + min(len(ad_sigs), 3) * 2
    if res.has_at_handle:
        prov_int += 3
    if res.has_phone and res.has_at_handle:
        prov_int += 4
    if (res.has_phone or res.has_contact_url or res.has_at_handle) and prov_int > 0:
        prov_int += 1
    if res.has_dotted_word:
        prov_int += 2
    if res.has_many_lines:
        prov_int += 1
    res.provider_confidence = prov_int

    # ===== GATE 1: provider / advertisement =====
    # Strong provider = first-person plural OR provider phrase OR ad signal
    # Weak provider plural = first-person singular verb + plural service noun
    #   (أسوي بحوث / أحل واجبات = commercial offering)
    has_strong_provider = bool(
        [p for p in provider_sigs if normalize_text(p) in {
            normalize_text(x) for x in (
                "نوفر", "نقدم", "نقدم خدمات", "لدينا خدمات", "لدينا",
                "خدماتنا", "نخدمكم", "نخدم", "نسوي", "نعمل", "ننجز",
                "نساعدكم", "نساعدكم في", "نشتغل", "نرتب", "نصمم", "نحل",
                "نكتب", "نجهز", "نشرح", "نراجع", "مكتبنا", "فريقنا",
                "ننجز لك", "متخصص في", "متخصصون", "متخصصون في",
                "مختص في", "مختصون", "للتواصل لحل", "للتواصل",
                "تواصل معنا", "راسلنا", "تواصل خاص", "للطلب", "للحجز",
                "للاستفسار", "للاستفسارات", "للحجز والاستفسار",
                "مكتب", "مؤسسة", "منشة",
            )
        }]
    )
    has_ad = bool(ad_sigs)
    has_provider_weak_plural = bool(plural_nouns) and any(
        normalize_text(p) in {
            normalize_text(x) for x in (
                "أسوي", "اسوي", "أعمل", "اعمل", "أنجز", "انجز",
                "أحل", "احل", "أكتب", "اكتب", "أصمم", "اصمم",
                "أشرح", "اشرح", "أجهز", "اجهز", "أرتب", "ارتب",
                "أكمل", "اكمل", "أنفذ", "انفذ",
            )
        }
        for p in provider_sigs
    )

    if has_strong_provider or has_ad or has_provider_weak_plural:
        res.accepted = False
        res.is_request = False
        res.intent_type = "provider_ad"
        res.reason = "provider_detected"
        res.confidence = 0.02
        res.seeker_confidence = 0
        res.service = _classify_service(normalized, exec_verbs)
        return res

    # ===== GATE 2: info / resource / long-content WITHOUT person+execution =====
    # [v3.0] READY_MADE_INDICATORS (جاهز/معد) + service + no exec → resource seeking
    long_info = _detect_long_informational(text, normalized)
    has_person_exec = has_requester and has_execution
    ready_made_resource = bool(ready_made) and bool(services) and not has_execution

    if (info_sigs or resource_sigs or long_info or ready_made_resource) and not has_person_exec:
        res.accepted = False
        res.is_request = False
        if long_info:
            res.intent_type = "long_informational_content"
            res.reason = "long_informational_content_no_person_executor"
            res.confidence = 0.04
        elif ready_made_resource:
            res.intent_type = "resource_seeking"
            res.reason = "ready_made_resource_not_service_execution"
            res.confidence = 0.05
        elif info_sigs:
            res.intent_type = "information_request"
            res.reason = "asking_for_information_not_service_execution"
            res.confidence = 0.06
        else:
            res.intent_type = "resource_seeking"
            res.reason = "resource_seeking_not_service_execution"
            res.confidence = 0.05
        res.seeker_confidence = 0
        res.service = _classify_service(normalized, exec_verbs)
        return res

    # ===== GATE 2.5: recommendation WITHOUT strong execution =====
    # «افضل واحد يشرح الماده» = يطلب توصية، لا يطلب شخصًا ينفذ له.
    # نرفض إلا لو وجد ownership + service + exec (طلب تنفيذ قوي).
    if recommend_sigs and not (ownership and services and exec_verbs):
        res.accepted = False
        res.is_request = False
        res.intent_type = "recommendation_seeking"
        res.reason = "recommendation_or_tips_request_not_service_execution"
        res.confidence = 0.08
        res.seeker_confidence = 1
        res.service = _classify_service(normalized, exec_verbs)
        return res

    # ===== GATE 3: person + execution relationship =====
    # (a) requester AND exec verb
    # (b) strong requester phrase AND service AND (outsource OR role)
    #     — «من عنده شخص مضمون للمشاريع» (مضمون=outsource) / «أبي مدرس خصوصي» (مدرس=role)
    # (c) requester AND service AND outsourcing indicator (e.g. «له/لي/عني»)
    # (d) professional role + ownership  (e.g. «أبي مدرس خصوصي» — الدور implies تنفيذ)
    # (e) delegation verb + person + ownership  (e.g. «أبي أوكل أحد بالمهمة»)
    # note: option (b) tightened in v3.0 — «أبي حد بحث» (no outsource/role) → REJECT.
    has_person_exec_relationship = (
        (has_requester and has_execution)
        or (bool(requester_phrases) and bool(services)
            and (bool(outsource_sigs) or bool(role_tokens)))
        or (has_requester and bool(services) and bool(outsource_sigs))
        or (bool(role_tokens) and bool(ownership))
        or (bool(delegation_verbs) and bool(person_tokens) and bool(ownership))
    )
    if not has_person_exec_relationship:
        res.accepted = False
        res.is_request = False
        # distinguish reason
        if not services and not has_requester and not has_execution:
            res.intent_type = "casual_talk"
            res.reason = "no_academic_intent"
            res.confidence = 0.01
        elif services and not has_requester and not has_execution:
            res.intent_type = "service_mention_only"
            res.reason = "service_word_without_person_or_executor"
            res.confidence = 0.03
        else:
            res.intent_type = "low_confidence"
            res.reason = "no_person_executor_relationship"
            res.confidence = 0.05
        res.seeker_confidence = 1
        res.service = _classify_service(normalized, exec_verbs)
        return res

    # ===== GATE 4: academic service (or exec/delegation/role implies service) =====
    exec_implies = any(
        normalize_text(ev) in EXEC_IMPLIES_SERVICE for ev in exec_verbs
    ) or bool(delegation_verbs) or role_implies_service
    if not services and not exec_implies:
        res.accepted = False
        res.is_request = False
        res.intent_type = "low_confidence"
        res.reason = "no_academic_service"
        res.confidence = 0.10
        res.seeker_confidence = 2
        res.service = None
        return res

    # ===== GATE 5: long informational override =====
    # حتى لو فحصنا person+exec+service، لو النص طويل معلوماتي وليس
    # طلبًا قصيرًا صريحًا → REJECT.
    if long_info and not (ownership and services and has_execution):
        res.accepted = False
        res.is_request = False
        res.intent_type = "long_informational_content"
        res.reason = "long_informational_content_override"
        res.confidence = 0.07
        res.seeker_confidence = 2
        res.service = _classify_service(normalized, exec_verbs)
        return res

    # ===== GATE 6: ACCEPT =====
    res.service = _classify_service(normalized, exec_verbs)
    # confidence scoring (0-1)
    conf = 0.45
    if has_requester:
        conf += 0.15
    if has_execution:
        conf += 0.15
    if services:
        conf += 0.15
    if ownership:
        conf += 0.05
    if len(services) >= 2:
        conf += 0.03
    if exec_implies and services:
        conf += 0.05
    if has_person_exec and services and ownership:
        conf += 0.07  # explicit execution request bonus
    conf = min(conf, 0.99)

    # intent_type
    if services and has_execution and has_requester:
        res.intent_type = "service_execution_request"
        res.reason = "explicit_request_for_person_to_execute_service"
    elif exec_implies and has_requester:
        res.intent_type = "person_for_academic_help"
        res.reason = "request_for_person_to_provide_academic_help"
    else:
        res.intent_type = "service_execution_request"
        res.reason = "service_execution_request"

    res.accepted = True
    res.is_request = True
    res.confidence = round(conf, 2)
    res.seeker_confidence = int(round(conf * 100))
    return res


# ============================================================
# Legacy wrappers (backward compat — bot.py & tests)
# ============================================================
def is_service_seeker(text: str) -> bool:
    """هل الرسالة طالب خدمة؟ قرار analyze_request."""
    return analyze_request(text).is_request


def is_service_provider(text: str) -> bool:
    """هل الرسالة مقدم خدمة؟ (provider/ad detected)."""
    return analyze_request(text).intent_type == "provider_ad"


def is_request_message(text: str) -> Tuple[bool, Dict[str, Any]]:
    """Compatibility wrapper للواجهة القديمة. يُرجع (is_request, info_dict)."""
    res = analyze_request(text)
    return res.is_request, res.to_dict()
