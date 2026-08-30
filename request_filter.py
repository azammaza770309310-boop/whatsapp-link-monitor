#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
request_filter.py — Request Filter v2 (conservative Intent + Academic Service)

FLTER v2 المحافظ — يحلّ مشكلة الـ15,000 رسالة الكاذبة التي سببها الفلتر
القديم (REQUEST_KEYWORDS بـ300 كلمة + مطابقة substring ساذجة على كلمات
منفردة مثل «مشروع» / «واجب» / «بحث» / «عرض» / «اختبار» / «مساعدة»).

المبدأ الجوهري: لا يكفي وجود كلمة أكاديمية وحدها، ولا وجود طلب عام وحده.
يجب أن تبحث الرسالة عن:
  (REQUEST_INTENT  AND  ACADEMIC_SERVICE)
  OR
  (ACTION + ACADEMIC_SERVICE مع صيغة سؤال/طلب)
  OR
  (STRONG_PATTERN مركبة كاملة)

ويُستبعد مقدم الخدمة (PROVIDER) صراحةً: أسوي بحوث / نوفر مشاريع / خدمات
بأسعار — هذه إعلانات لا طلبات.

التطبيع (normalize_text) للمقارنة فقط — الإرسال يستخدم raw_text الأصلي.

لا يلمس مسار استخراج الروابط إطلاقًا. هذا الفلتر مستقل تمامًا ويُستدعى من
_handle_request_path في bot.py فقط.

نقاط التصميم:
  - لا قائمة 300 كلمة منفردة. كل قائمة هنا إما عبارة طلب (multi-word) أو
    خدمة أكاديمية محددة أو فعل إنجاز أو عبارة مركبة كاملة.
  - الخدمة الأكاديمية وحدها (+3) لا تكفي أبدًا للقبول.
  - مؤشر الطلب العام (من/مين/أبي) وحده (+1) لا يكفي أبدًا.
  - القبول يتطلب تركيبة واضحة (intent+service أو action+service+سؤال أو
    strong pattern). خلاف ذلك → REJECT.
  - confidence تشخيصي للـlogging، والقرار rule-based محافظ.
"""

import re
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Any

FILTER_VERSION = "v2.1"
FILTER_MODE = "conservative_intent_service"

# ============================================================
# تطبيع النص (normalization) — للمقارنة فقط، لا للإرسال
#   - lowercase للإنجليزية
#   - توحيد الحروف العربية: أإآٱ→ا، ؤ→و، ئ→ي، ة→ه، ى→ي
#   - إزالة التطويل (kashida) والتشكيل (harakat)
#   - تطبيع المسافات
# ============================================================
_ARABIC_NORMALIZE_MAP = str.maketrans({
    'أ': 'a', 'إ': 'a', 'آ': 'a', 'ٱ': 'a',  # placeholder, swapped below
})
# Use direct mapping (avoid 'a' placeholder confusion)
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
    t = _ARABIC_DIACRITICS.sub('', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


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
# [1] REQUEST_INTENT_PHRASES — عبارات طلب شخص (+3 لكل تطابق، cap 2)
# عبارات متعددة الكلمات تبحث عن شخص يسوي/يحل/يساعد. ليست كلمات منفردة.
# (القائمة تجمع طلبات المستخدم في الأقسام 3 و 4 من المواصفة.)
# ============================================================
REQUEST_INTENT_PHRASES: List[str] = [
    # أبي أحد / أحتاج شخص
    "ابي احد", "أبي أحد", "ابي شخص", "أبي شخص",
    "محتاج أحد", "محتاج شخص", "احتاج احد", "احتاج شخص",
    "أحتاج أحد", "أحتاج شخص", "أحتاج شخص متخصص", "محتاج شخص متخصص",
    # من/مين يسوي/يحل لي
    "من يسوي لي", "مين يسوي لي", "من يسوي", "مين يسوي",
    "من يحل لي", "مين يحل لي", "من يحل", "مين يحل",
    # من/مين يعرف أحد/شخص (ليست «من يعرف» المجرّدة — تلك سؤال عام، تُترك indicator فقط)
    "من يعرف أحد", "مين يعرف أحد", "من يعرف شخص", "مين يعرف شخص",
    "تعرفون أحد", "تعرفون شخص",
    # أحد يساعدني / من يقدر يساعدني
    "أحد يساعدني", "احد يساعدني", "من يقدر يساعدني", "مين يقدر يساعدني",
    "من يقدر", "مين يقدر", "من يساعدني", "مين يساعدني",
    # طلبات شخص متخصص/فاهم/خبرة
    "أبي أحد فاهم", "ابي احد فاهم", "أحد عنده خبرة", "احد عنده خبره",
    "من عنده شخص", "مين عنده شخص", "أحد ينجز لي", "احد ينجز لي",
    # أدور/أبحث عن شخص
    "أدور على أحد", "ادور على احد", "ابحث عن شخص", "أبحث عن شخص",
    "من يدلني", "مين يدلني", "من يرشح لي", "مين يرشح لي", "وين ألقى",
    # ممكن/ياليت/لو سمحتوا أحد
    "ممكن أحد", "ياليت أحد", "لو سمحتوا أحد",
    # --- seeking-person compounds (من قسم 4 — كلمات عامة للبحث عن شخص) ---
    # المواصفة: هذه القائمة وحدها غير كافية — تُوضع هنا كـintent (+3) وتحتاج
    # خدمة أكاديمية أو action+سؤال أو strong pattern للقبول. لا auto-accept.
    "ابي احد يسوي لي", "أبي أحد يساعدني", "من يعرف يسوي", "تعرفون أحد يسوي",
    "أحد يعرف شخص يسوي", "من عنده شخص يسوي", "أبي شخص ينجز لي",
    "محتاج أحد يساعدني", "احتاج شخص يسوي لي", "مين يعرف أحد",
    "من يقدر يسوي لي", "أحد عنده خبرة", "أبي أحد فاهم",
    "من يعرف شخص متخصص", "أحد ينجز لي", "مين يقدر يساعدني",
    "أبي شخص يسوي المشروع", "من عنده خبرة بالمشاريع",
]

# ============================================================
# [2] SEEKING_INDICATORS — مؤشرات طلب ضعيفة (+1 لكل تطابق، cap 3)
# كلمات/عبارات قصيرة تدل على بحث عن شخص، لكنها وحدها غير كافية أبدًا.
# تساهم في has_question_form (صيغة سؤال/طلب).
# ============================================================
SEEKING_INDICATORS: List[str] = [
    "من", "مين", "أبي", "ابي", "أحتاج", "احتاج", "محتاج", "تعرفون",
    "أحد يعرف", "احد يعرف", "أحد عنده", "احد عنده",
    "ممكن أحد", "ياليت أحد", "لو سمحتوا أحد",
    "من يدلني", "مين يدلني", "من يرشح لي", "مين يرشح لي",
    "أدور على أحد", "ادور على احد", "ابحث عن شخص", "أبحث عن شخص",
    "وين ألقى", "ابغى احد", "أبغى أحد",
]

# ============================================================
# [3] ACADEMIC_SERVICES — خدمات أكاديمية (+3 لكل تطابق، cap 2)
# وحدها لا تكفي للقبول. يجب أن تُقرن بـintent أو action+سؤال أو strong pattern.
# (القائمة الموسعة من القسم 5 في المواصفة.)
# ============================================================
ACADEMIC_SERVICES: List[str] = [
    # بحوث
    "بحث", "البحث", "بحث جامعي", "بحث علمي", "بحث تخرج", "مشروع بحثي",
    "بحوث",
    # تقارير
    "تقرير", "تقرير جامعي", "تقرير تدريب", "تقرير ميداني", "تقرير تعاوني",
    "تقارير",
    # واجبات
    "واجب", "الواجب", "واجب جامعي", "واجبات",
    # سكليف / تكليف
    "سكليف", "اسكليف", "assignment", "تكليف", "تكاليف",
    # مشاريع
    "مشروع", "المشروع", "مشاريع", "بروجكت", "project", "مشروع جامعي",
    "مشروع تخرج",
    # عروض / بوربوينت
    "عرض", "عرض تقديمي", "برزنتيشن", "presentation", "بوربوينت",
    "PowerPoint", "powerpoint", "ppt", "PPT",
    # Excel
    "Excel", "excel", "إكسل", "اكسل", "جداول", "ملف اكسل",
    # خرائط
    "خريطة مفاهيم", "خريطة ذهنية", "mind map", "mindmap", "concept map",
    "مخطط",
    # كويز / اختبارات / ميد
    "كويز", "quiz", "اختبار", "اختبارات", "ميد", "ميدترم", "midterm",
    # مذاكرة / شرح
    "مذاكرة", "شرح", "شرح المادة", "أسئلة المقرر", "حل مسائل",
]

# ============================================================
# [4] ACTION_VERBS — أفعال إنجاز (+4 عند وجود فعل + خدمة معًا)
# action+service = فعل إنجاز موجود AND خدمة أكاديمية موجودة.
# (القسم 3 من المواصفة: يسوي بحث / يكتب بحث / يحل واجب ...)
# ============================================================
ACTION_VERBS: List[str] = [
    "يسوي", "يكتب", "يحل", "ينجز", "يجهز", "يصمم", "يشرح", "يراجع",
    "يساعد", "يحضّر", "يحضر",
]

# ============================================================
# [5] STRONG_PATTERNS — عبارات مركبة قوية كاملة (+5 لكل تطابق)
# تطابق مباشر = قبول عالي الثقة (HIGH). (القسم 4 من المواصفة.)
# هذه عبارات تجمع طلب شخص + خدمة أكاديمية صريحة في عبارة واحدة.
# عبارات البحث عن شخص وحدها (دون خدمة) نُقلت لـREQUEST_INTENT_PHRASES
# لأن المواصفة تقول: «لا تعتبر هذه القائمة وحدها كافية».
# ============================================================
STRONG_PATTERNS: List[str] = [
    # --- بحوث وتقارير ---
    "من يسوي لي بحث", "أبي أحد يسوي بحث", "أحد يسوي بحث", "من يعرف يسوي بحث",
    "تعرفون أحد يسوي بحث", "أبي أحد يكتب بحث", "من يسوي تقرير",
    "أبي أحد يسوي تقرير", "أحد يسوي تقرير ميداني", "من يعرف يسوي تقرير ميداني",
    "أبي تقرير جامعي", "من يسوي بحث تخرج", "أحد يسوي مشروع بحثي",
    "من يجهز لي البحث", "أحد يساعدني في البحث", "من يسوي لي تقرير تدريب",
    "أحد يسوي تقرير تعاوني", "من يسوي تقرير تدريب ميداني",
    # --- واجبات وتكليفات ---
    "من يحل لي واجب", "أبي أحد يحل الواجب", "أحد يحل لي الواجب",
    "من يعرف يحل واجب", "أحد يساعدني في واجب", "أحد يسوي لي سكليف",
    "من يسوي سكليف", "من يعرف يسوي سكليف", "أبي أحد يجهز سكليف",
    "من يحل التكليف", "أحد يسوي التكليف", "من يساعدني في التكليف",
    "عندي واجب جامعي وأحتاج مساعدة", "أحد ينجز لي واجب جامعي",
    # --- مشاريع ---
    "من يسوي لي مشروع", "أحد يسوي لي مشروع", "من يسوي البروجكت",
    "أحد يسوي بروجكت", "من يعرف يسوي مشروع", "أبي أحد ينجز المشروع",
    "أحد يساعدني في المشروع", "من يجهز مشروع جامعي",
    "عندي مشروع تخرج وأحتاج أحد", "من يسوي مشروع تخرج",
    "أحد يشتغل مشاريع جامعية",
    # --- عروض وبوربوينت ---
    "أحد يسوي لي عرض", "من يسوي لي عرض تقديمي", "من يعرف يسوي برزنتيشن",
    "أحد يسوي برزنتيشن", "أبي أحد يسوي بوربوينت", "من يسوي بوربوينت",
    "أحد يجهز عرض بوربوينت", "من يصمم عرض تقديمي", "أحد يصمم برزنتيشن",
    "أبي عرض جامعي وأحتاج أحد", "من يجهز برزنتيشن جامعي",
    "أحد يسوي PowerPoint", "من يعرف يسوي PowerPoint", "أبي أحد يصمم لي عرض",
    # --- Excel والبرامج ---
    "أحد يحل إكسل", "من يحل Excel", "أحد يسوي لي إكسل", "حل واجب Excel",
    "أحد يحل واجب إكسل", "من يسوي جداول إكسل", "أحد يساعدني في Excel",
    "من يعرف يحل مسائل Excel", "أحد يسوي مشروع Excel", "من يجهز ملف Excel",
    "حل واجبات برامج", "أحد يساعدني في البرامج",
    # --- خرائط ومخططات ---
    "من يسوي خريطة مفاهيم", "أحد يسوي خريطة مفاهيم",
    "من يسوي خريطة ذهنية", "أحد يسوي خريطة ذهنية",
    "أبي خريطة مفاهيم وأحتاج أحد", "أبي خريطة ذهنية وأحتاج أحد",
    "من يعرف يسوي Mind Map", "أحد يصمم Mind Map", "من يسوي Concept Map",
    "أحد يجهز خريطة للمادة", "من يسوي مخطط للمقرر",
    # --- اختبارات وكويزات ومذاكرة ---
    "أحد يحل لي ميد", "من يحل الميد", "أحد يساعدني في الميد",
    "من يحل كويز", "أحد يحل كويز", "من يحل اختبار", "أحد يساعدني في الاختبار",
    "من يساعدني في أسئلة المقرر", "أحد يساعدني في المذاكرة",
    "من يشرح لي المادة", "أحد يراجع معي",
]

# ============================================================
# [6] PROVIDER_INDICATORS — إشارات مقدم خدمة (+4 لكل تطابق)
# أول شخص مفرد/جمع يعرض خدمة: أسوي / أحل / نوفر / نقدم / لدينا / متخصص.
# هذه ليست طلبًا — هذه إعلان عرض خدمة.
# ============================================================
PROVIDER_INDICATORS: List[str] = [
    "أسوي", "اسوي", "أحل", "احل", "أكتب", "اكتب", "أنجز", "انجز",
    "أصمم", "اصمم", "أشرح", "اشرح", "أجهز", "اجهز",
    "نوفر", "نقدم", "نقدم خدمات", "لدينا خدمات", "لدينا", "خدماتنا",
    "نخدمكم", "نخدم", "متخصص في", "متخصصون", "متخصصون في",
    "مختص في", "مختصون", "للتواصل لحل", "للتواصل", "تواصل معنا", "راسلنا",
    "تواصل خاص", "للطلب", "للحجز",
    # [v2.1] صيغ مُقدّم خدمة بصيغة الجمع (ننجز/نساعدكم) ومكتب/فريق — إشارات تجارية واضحة
    "ننجز", "ننجز لك", "نساعدكم", "نساعدكم في", "مكتبنا", "فريقنا",
    "للاستفسار", "للاستفسارات", "للحجز والاستفسار",
]

# ============================================================
# [7] ADVERTISEMENT_STRONG_SIGNALS — إشارات إعلانية تسويقية (+2 لكل، cap 3)
# كلمات تسويق: أسعار/خصومات/عروض/ضمان/عملاء. تُغذّي provider_confidence.
# NOTE: رقم الهاتف/الرابط وحده لا يُسبب الرفض — يجب أن يُقرن بإشارة عرض.
# ============================================================
ADVERTISEMENT_STRONG_SIGNALS: List[str] = [
    "أسعارنا", "بأسعار", "بأسعار مناسبة", "أفضل الأسعار", "أسعار ممتازة",
    "خصم", "خصومات", "تخفيض", "تخفيضات", "حسم", "عروض", "عرض خاص",
    "عرض محدود", "عرض لفترة محدودة", "استفد الآن", "احجز الآن",
    "اطلب الآن", "سارع",
    "فرصة", "فرصه", "محدودة", "العدد محدود", "أماكن محدودة", "مقاعد محدودة",
    "حجز", "احجز", "حجوزات", "حجز مسبق",
    "دفع", "الدفع", "دفع اونلاين", "الدفع اونلاين", "سداد", "السداد",
    "الدفع المسبق", "دفع مسبق",
    "ضمان", "ضمان استرجاع", "ضمان الجودة", "مضمون", "نتيجة مضمونة",
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
    # [v2.1] إشارات تسويقية إضافية شائعة في إعلانات الخدمات الأكاديمية
    "تنافسية", "بأسعار تنافسية", "تنافسي", "مجاني", "مجانا",
    "هديه", "هدية", "هدية مجانية", "الباقة", "باقات", "اشتراك",
    "تقييم 5", "تقييم خمس نجوم", "5 نجوم", "موصى به",
]

# نسخ مُطبّعة مسبقًا (أزواج أصلي/مُطبّع) للمقارنة السريعة
_INTENT_PAIRS = _norm_pairs(REQUEST_INTENT_PHRASES)
_INDICATOR_PAIRS = _norm_pairs(SEEKING_INDICATORS)
_SERVICE_PAIRS = _norm_pairs(ACADEMIC_SERVICES)
_ACTION_PAIRS = _norm_pairs(ACTION_VERBS)
_PATTERN_PAIRS = _norm_pairs(STRONG_PATTERNS)
_PROVIDER_PAIRS = _norm_pairs(PROVIDER_INDICATORS)
_AD_PAIRS = _norm_pairs(ADVERTISEMENT_STRONG_SIGNALS)

# ============================================================
# أرقام الجوال وروابط التواصل — إشارات إضافية (إضافية فقط، ليست حكمًا نهائيًا)
# رقم/رابط وحده لا يرفض. يُضاف +1 لـprovider_confidence لو وُجد مع إشارة عرض.
# ============================================================
_PHONE_RE = re.compile(
    # [v2.1] إصلاح: \b لا يعمل بين حرف عربي (\w) ورقم — كان يفوّت أرقامًا ملتصقة
    # بأحرف عربية مثل «رقمي0540916687» أو «للتواصل0540916687». استبدلنا \b
    # بـ lookaround رقمي: (?<!\d) قبل و (?!\d) بعد، لضمان التقاط الرقم
    # الكامل (وليس جزءًا من رقم أطول) مع السماح بتقدمه/تأخره بحرف عربي.
    r'(\+966\d{8,9}|\+967\d{8,9}|\+968\d{8,9}|\+971\d{8,9}|\+20\d{8,9}|(?<!\d)05\d{8}(?!\d))'
)
_CONTACT_URL_RE = re.compile(r'(https?://|t\.me/|wa\.me/|telegram\.me/)', re.IGNORECASE)


def _has_phone_number(text: str) -> bool:
    return bool(_PHONE_RE.search(text))


def _has_contact_url(text: str) -> bool:
    return bool(_CONTACT_URL_RE.search(text))


# ============================================================
# [MBOT-PORT] إشارات إعلانية إضافية من مرجع mbot.py القديم
# ------------------------------------------------------------
# هاتان الإشارتان وحدهما لا تكفيان للرفض، لكنهما تُضيفان لـprovider_confidence
# لتعزيز كشف الإعلانات المُتعمّدة التسلل. مأخوذتان من is_advertiser_message
# في mbot.py القديم الذي رفعه المُشغّل كمرجع.
# ============================================================

# [1] كلمات عربية مُجزّأة بنقاط متقطعة — تستخدم للالتفاف على الفلاتر
#     مثل "ت.قرير" / "تـ.قرير" / "و.اجب" — إشارة إعلانية قوية (+2).
#     regex: حرف عربي + (optional non-word) + literal "." + (optional non-word) + حرف عربي
# [أ-ي] + optional tatweel (ـ U+0640) + literal "." + optional tatweel + [أ-ي].
# NO \W (no space): a normal sentence period is followed by a space, which must
# NOT match (it caused false positives like «التأمين. وثيقة» being flagged as a
# dotted-word obfuscation). Only intra-word dots (ت.قرير / تـ.قرير / تـ.ـقرير)
# match — which is the actual evasion pattern.
_DOTTED_WORD_RE = re.compile(r'[أ-ي][ـ]?\.[ـ]?[أ-ي]')


def _has_dotted_word(text: str) -> bool:
    """يكشف كلمات عربية مُجزّأة بنقاط (تعمّد التلصص على الفلاتر)."""
    if not text:
        return False
    return bool(_DOTTED_WORD_RE.search(text))


# [3] Telegram @handle — إشارة مُقدّم خدمة قوية (+3).
# وجود @handle لاتيني (4+ chars بعد @) في رسالة جماعية إشارة تجارية شبه
# أكيدة: المساعدة نادرًا ما تترك @handle؛ مُقدّم الخدمة يتركه للتواصل التجاري.
# (?<!\w) يمنع مطابقة user@domain (email). يبدأ بحرف لاتيني ثم 3+ لاتيني/رقم/_.
_AT_HANDLE_RE = re.compile(r'(?<!\w)@[A-Za-z][A-Za-z0-9_]{3,}')


def _has_at_handle(text: str) -> bool:
    """يكشف @handle تيليجرام (لاتيني، 4+ chars بعد @) — إشارة تجارية قوية."""
    if not text:
        return False
    return bool(_AT_HANDLE_RE.search(text))


# [2] رسالة متعددة الأسطر (≥6 أسطر) — إشارة إعلانية ضعيفة (+1).
#     ملاحظة: ليست حكمًا نهائيًا — طلب مشروع طويل قد يكون متعدد الأسطر.
#     لذلك تُضاف كإشارة ضعيفة فقط، لا ترفض وحدها أبدًا.
_MULTILINE_AD_THRESHOLD = 6


def _has_many_lines(text: str, threshold: int = _MULTILINE_AD_THRESHOLD) -> bool:
    """يكشف رسائل متعددة الأسطر (≥6 افتراضيًا) — إشارة تسويقية ضعيفة."""
    if not text:
        return False
    try:
        return len(text.splitlines()) >= int(threshold)
    except Exception:
        return False


# ============================================================
# thresholds — محافظة لتقليل false positives
# ============================================================
PROVIDER_THRESHOLD = 6   # provider_confidence >= 6 → REJECT (provider)
INTENT_CAP = 2           # max intent matches counted (×3)
SERVICE_CAP = 2          # max service matches counted (×3)
ACTION_CAP = 2           # max action matches counted (×4)
INDICATOR_CAP = 3        # max indicator matches counted (×1)
AD_CAP = 3               # max ad-signal matches counted (×2)
PATTERN_BONUS = 5        # per strong pattern match
HIGH_CONFIDENCE = 9
MEDIUM_CONFIDENCE = 6


@dataclass
class RequestAnalysis:
    """نتيجة تحليل رسالة واحدة. تشخيصية للـlogging وقرار is_request."""
    is_request: bool = False
    confidence: int = 0
    reason: str = "low_confidence"
    matched_intents: List[str] = field(default_factory=list)
    matched_services: List[str] = field(default_factory=list)
    matched_actions: List[str] = field(default_factory=list)
    matched_patterns: List[str] = field(default_factory=list)
    matched_indicators: List[str] = field(default_factory=list)
    provider_signals: List[str] = field(default_factory=list)
    ad_signals: List[str] = field(default_factory=list)
    seeker_confidence: int = 0
    provider_confidence: int = 0
    has_question_form: bool = False
    has_phone: bool = False
    has_contact_url: bool = False
    has_at_handle: bool = False  # Telegram @handle — إشارة مُقدّم خدمة
    # [MBOT-PORT] إشارات إعلانية إضافية من mbot.py
    has_dotted_word: bool = False       # كلمات عربية مُجزّأة بنقاط (obfuscation)
    has_many_lines: bool = False        # ≥6 أسطر (weak marketing signal)

    def to_dict(self) -> Dict[str, Any]:
        """Compatibility dict للواجهة القديمة (is_request_message)."""
        # request_matches للعرض: intents + services + patterns (أبرزها)
        matches = (self.matched_intents + self.matched_services
                   + self.matched_patterns)
        # إشارات إعلانية إضافية تُعرض في advertisement_matches للتشخيص
        extra_ad = []
        if self.has_dotted_word:
            extra_ad.append("(dotted_word_obfuscation)")
        if self.has_many_lines:
            extra_ad.append("(multi_line_six_plus)")
        return {
            "reason": self.reason,
            "request_matches": matches,
            "advertisement_matches": (self.provider_signals + self.ad_signals + extra_ad),
            "reasons": [self.reason] if self.reason else [],
            "confidence": self.confidence,
            "seeker_confidence": self.seeker_confidence,
            "provider_confidence": self.provider_confidence,
            "matched_intents": self.matched_intents,
            "matched_services": self.matched_services,
            "matched_patterns": self.matched_patterns,
            "matched_actions": self.matched_actions,
            "provider_signals": self.provider_signals,
            "has_question_form": self.has_question_form,
            "has_dotted_word": self.has_dotted_word,
            "has_many_lines": self.has_many_lines,
        }


def _match_pairs(pairs, normalized: str) -> List[str]:
    """يرجع قائمة العبارات الأصلية التي تطابقت.

    للعبارات أحادية المُفرَدة (لا مسافة فيها) نستخدم word-boundary (\\b) حتى
    لا نُطابق كلمة قصيرة داخل كلمة أخرى: «مين» داخل «تأمين»، «من» داخل
    «المن»/«كلمن»، «أبي» داخل «أبيض». هذا كان سبب false-positive لمحتوى
    تجاري (تقرير طبي) مرّ كطلب لأن «مين» طُابقت داخل «تأمين» فظُنّ سؤالاً.

    استثناء مهم: نسمح بسبق «ال» التعريف (أي «ال» عند حدّ كلمة) لأن العربية
    تُلحق الأسماء بـ«ال» عادةً («التقرير» = the report = نفس الكلمة). فبدون
    هذا الاستثناء، «تقرير» لن يُطابق «التقرير»، ما يُكسر طلبات حقيقية مثل
    «أحتاج مساعدة في التقرير الجامعي». الـ«ال» نفسها لا تنقذ «مين» داخل
    «تأمين» لأن «تأمين» لا يحتوي «المين» (لا «ل» بعد الألف).

    للعبارات متعددة المُفرَدات نُبقي substring (محدّدة بما يكفي).
    """
    out = []
    for orig, norm in pairs:
        if not norm:
            continue
        if ' ' in norm:
            # multi-token phrase — substring is specific enough
            if norm in normalized:
                out.append(orig)
        else:
            # single token — word boundary, OR prefixed with the «ال» definite
            # article (itself at a word boundary). \b works for Arabic in
            # Python 3 re by default (\w includes Unicode letters).
            pat_bare = r'\b' + re.escape(norm) + r'\b'
            pat_undef = r'\bال' + re.escape(norm) + r'\b'
            if re.search(pat_bare, normalized) or re.search(pat_undef, normalized):
                out.append(orig)
    return out


def analyze_request(text: str) -> RequestAnalysis:
    """يحلل الرسالة ويُرجع RequestAnalysis تشخيصية.

    قرار القبول (is_request=True) يتطلب إحدى:
      A) matched_intents AND matched_services  (intent + academic service)
      B) matched_actions AND has_question_form (action+service + سؤال/طلب)
      C) matched_patterns (strong compound phrase)

    ويُرفض صراحةً لو provider_confidence >= PROVIDER_THRESHOLD (مقدم خدمة).
    خلاف ذلك → REJECT (confidence منخفض).
    """
    res = RequestAnalysis()

    if not text or not text.strip():
        res.reason = "empty"
        return res

    normalized = normalize_text(text)
    if not normalized:
        res.reason = "empty_after_normalize"
        return res

    # --- مطابقات ---
    res.matched_intents = _match_pairs(_INTENT_PAIRS, normalized)
    res.matched_indicators = _match_pairs(_INDICATOR_PAIRS, normalized)
    res.matched_services = _match_pairs(_SERVICE_PAIRS, normalized)
    res.matched_patterns = _match_pairs(_PATTERN_PAIRS, normalized)
    res.provider_signals = _match_pairs(_PROVIDER_PAIRS, normalized)
    res.ad_signals = _match_pairs(_AD_PAIRS, normalized)

    # action+service: فعل إنجاز موجود AND خدمة موجودة
    matched_action_verbs = _match_pairs(_ACTION_PAIRS, normalized)
    res.matched_actions = []
    if matched_action_verbs and res.matched_services:
        for av in matched_action_verbs:
            res.matched_actions.append(f"{av}+service")
            if len(res.matched_actions) >= ACTION_CAP:
                break

    # إشارات هاتف/رابط/@handle
    res.has_phone = _has_phone_number(text)
    res.has_contact_url = _has_contact_url(text)
    res.has_at_handle = _has_at_handle(text)

    # [MBOT-PORT] إشارات إعلانية إضافية من mbot.py
    # (1) كلمات عربية مُجزّأة بنقاط — تعمّد التلصص على الفلاتر
    # (2) رسالة متعددة الأسطر (≥6) — إشارة تسويقية ضعيفة
    res.has_dotted_word = _has_dotted_word(text)
    res.has_many_lines = _has_many_lines(text)

    # صيغة سؤال/طلب: علامة استفهام OR intent phrase OR seeking indicator
    res.has_question_form = (
        '؟' in text or '?' in text
        or bool(res.matched_intents) or bool(res.matched_indicators)
    )

    # --- حساب الثقة ---
    seeker = (
        min(len(res.matched_intents), INTENT_CAP) * 3
        + min(len(res.matched_indicators), INDICATOR_CAP) * 1
        + min(len(res.matched_services), SERVICE_CAP) * 3
        + min(len(res.matched_actions), ACTION_CAP) * 4
        + len(res.matched_patterns) * PATTERN_BONUS
    )
    res.seeker_confidence = seeker

    provider = (
        len(res.provider_signals) * 4
        + min(len(res.ad_signals), AD_CAP) * 2
    )
    # [MBOT-PORT] إشارات إعلانية إضافية: dotted word = +2 (قوية)، multi-line = +1 (ضعيفة)
    # وحدهما لا يكفيان للرفض (PROVIDER_THRESHOLD=6)، لكنهما يعززان الكشف لو
    # رافقتا إشارة عرض أخرى. dotted word قوية لأن العربية لا تستخدم النقاط
    # بين الحروف عادةً — هذا التلصص إعلاني شبه أكيد.
    if res.has_dotted_word:
        provider += 2
    if res.has_many_lines:
        provider += 1
    # @handle تيليجرام — إشارة تجارية قوية (+3). المساعد نادرًا ما يترك @handle؛
    # مُقدّم الخدمة يتركه للتواصل التجاري. وحدها (+3) لا تكفي للرفض (threshold 6)
    # لكنها تعزّز الكشف بقوة.
    # [v2.1] تقوية تركيبة هاتف+@handle من +2 إلى +4: المُعلن الذي يترك BOTH رقم
    # جوال AND @handle في رسالة جامعية شبه أكيد تجاريًا — لا يكاد يوجد طالب
    # مشروع يُرفق الاثنين معًا. الـ+2 القديمة كانت تضع أرضية provider عند 6
    # (3+2+1) بالضبط على عتبة الرفض — هشة. الآن الأرضية 8 (3+4+1) فوق العتبة
    # بمسافة آمنة. SEEKER_WITH_CONTACT الحقيقية لا تحوي @handle (فقط رقم)،
    # فلا تتأثر بهذا التعزيز.
    if res.has_at_handle:
        provider += 3
    if res.has_phone and res.has_at_handle:
        provider += 4
    # هاتف/رابط/@handle وحدها لا تكفي، لكنها تُضيف +1 لو وُجدت مع إشارة عرض أخرى
    # (provider>0 بعد الإشارات أعلاه).
    if (res.has_phone or res.has_contact_url or res.has_at_handle) and provider > 0:
        provider += 1
    res.provider_confidence = provider

    # --- القرار ---
    # 1) مقدم خدمة → رفض صريح (حتى لو بدا طلبًا)
    if res.provider_confidence >= PROVIDER_THRESHOLD:
        res.is_request = False
        res.reason = "provider_detected"
        res.confidence = seeker
        return res

    # 2) strong pattern → قبول عالي
    if res.matched_patterns:
        res.is_request = True
        res.reason = "strong_pattern"
        res.confidence = max(HIGH_CONFIDENCE, seeker)
        return res

    # 3) intent + service → قبول متوسط-عالي
    if res.matched_intents and res.matched_services:
        res.is_request = True
        res.reason = "intent_plus_service"
        res.confidence = max(MEDIUM_CONFIDENCE, seeker)
        return res

    # 4) action + service + صيغة سؤال/طلب → قبول متوسط
    if res.matched_actions and res.has_question_form:
        res.is_request = True
        res.reason = "action_plus_service"
        res.confidence = max(MEDIUM_CONFIDENCE, seeker)
        return res

    # 5) رفض — تمييز السبب للتشخيص
    res.is_request = False
    if not res.matched_services and not res.matched_intents and not res.matched_actions:
        res.reason = "no_academic_signal"
    elif res.matched_services and not res.matched_intents and not res.matched_actions:
        res.reason = "service_without_intent"  # «عندي مشروع» / «بحثي صعب»
    elif res.matched_intents and not res.matched_services:
        res.reason = "intent_without_service"  # «ممكن أحد يساعدني»
    else:
        res.reason = "low_confidence"
    res.confidence = seeker
    return res


def is_service_seeker(text: str) -> bool:
    """هل الرسالة طالب خدمة (seeker)؟ قرار analyze_request المعتمد."""
    return analyze_request(text).is_request


def is_service_provider(text: str) -> bool:
    """هل الرسالة مقدم خدمة (provider)؟ provider_confidence >= threshold."""
    return analyze_request(text).provider_confidence >= PROVIDER_THRESHOLD


def is_request_message(text: str) -> Tuple[bool, Dict[str, Any]]:
    """Compatibility wrapper للواجهة القديمة. يُرجع (is_request, info_dict).
    يستخدم نفس منطق analyze_request — لا فلتر قديم يعمل بالتوازي."""
    res = analyze_request(text)
    return res.is_request, res.to_dict()
