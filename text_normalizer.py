#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
text_normalizer.py — Request Intent Engine v4.0 / المرحلة 1: تنظيف أولي للنص
================================================================================
STAGE 1 of the v4.0 rebuild. يُنتج طبقتين من النص لكل رسالة:

  1. clean     — النص المُنظَّف للتحليل بالـAI:
                 - إزالة الإيموجي (والرموز التجميلية/variation selectors/ZWJ)
                 - إزالة الروابط (http/https/www/t.me/wa.me/chat.whatsapp.com)
                 - إزالة التوقيعات الهيكلية (Sent from iPhone/Android، فواصل
                   التوقيع «—» المتكررة، سطر الترويسة «转发/Forwarded»)
                 - ضبط تكرار الحروف («صرررراحة» → «صراحة») وتكرار الكلمات
                 - *مع الاحتفاظ بالسياق كاملًا*: اللهجة، الترقيم، الأسئلة،
                   أرقام الهواتف، @handles — كلها تبقى (الـAI يحتاجها كإشارات).

  2. canonical — النص القياسي للـhashing (المرحلة 4 — منع التكرار):
                 - كل ما في clean +
                 - تطبيع الحروف العربية (أ/إ/آ→ا، ة→ه، ى→ي، ؤ→و، ئ→ي)
                 - إزالة التشكيل والتطويل
                 - توحيد اللهجات (خريطة آمنة: شلون→كيف، ليش→لماذا، مين→من ...)
                 - إزالة كل ما ليس حرفًا/رقمًا/مسافة
                 - ضغط المسافات

مبادئ:
  - الاحتفاظ بالسياق (طلب المستخدم الصريح): clean لا يمس اللهجة ولا الترقيم.
  - لا حكم تصنيف هنا إطلاقًا — تنظيف فقط. القرار للمرحلة 2/3 (AI classifier).
  - كل عمليات الإزالة تُعاد عدّها في diagnostics (removed dict) لتسجيلها في
    filter_decisions (المرحلة 5).

Standalone module: no Telegram / no DB / no network. Pure functions.
"""

import re
from dataclasses import dataclass, field
from typing import Dict


# ============================================================
# طبقات النص
# ============================================================
@dataclass
class NormalizedText:
    """نتيجة المرحلة 1 — طبقتا نص + عدّادات ما أُزيل."""
    original: str = ""
    clean: str = ""        # للـAI — سياق محفوظ، إيموجي/روابط/توقيعات مُزالة
    canonical: str = ""    # للـhashing/dedup — مطبّع بالكامل + لهجات موحّدة
    removed: Dict[str, int] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.clean and self.clean.strip())


# ============================================================
# [1] الإيموجي والرموز التجميلية
# نطاقات محافظة — لا تمس الحروف العربية ولا presentation forms
# (FB50–FDFF / FE70–FEFF خارج كل النطاقات أدناه).
# ============================================================
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001F0FF"    # misc symbols (mahjong, cards)
    "\U0001F300-\U0001FAFF"    # pictographs, transport, flags, skin tones, supplemental
    "\U00002600-\U000027BF"    # misc symbols & dingbats (✅ ❌ ☀ ⚡ ...)
    "\U00002B00-\U00002B5F"    # stars, arrows-decorative
    "\U0001F1E6-\U0001F1FF"    # regional indicators (flags) — redundant safety
    "\U0000FE00-\U0000FE0F"    # variation selectors
    "\U0000200D"               # ZWJ (joined emoji sequences)
    "]+"
)

# ============================================================
# [2] الروابط
# ============================================================
_URL_RE = re.compile(
    r'(?:https?://|www\.)\S+'
    r'|t\.me/\S+'
    r'|telegram\.me/\S+'
    r'|wa\.me/\S+'
    r'|chat\.whatsapp\.com/\S+'
    r'|bit\.ly/\S+'
    r'|tinyurl\.com/\S+',
    re.IGNORECASE,
)

# ============================================================
# [3] التوقيعات الهيكلية (device footers + separators)
# محافظ جدًا: أنماط لا تظهر في نص طلب طبيعي أبدًا.
# ============================================================
_SIGNATURE_RES = [
    # device footers (WhatsApp/Telegram cross-posting)
    re.compile(r'^\s*(?:[-–—]*\s*)?(?:sent\s+from|أُرسلت\s+من)\s+.*?(?:iphone|android|ios|phone|phone|نقال|جوال)\s*\.?\s*$', re.IGNORECASE),
    # «دعم بواسطة بوت» / bot watermarks
    re.compile(r'^\s*(?:[-–—]*\s*)?(?:powered\s+by|دعم\s+بواسطة|بواسطة\s+بوت)\s+\S.*$', re.IGNORECASE),
    # سطر فاصل توقيع متكرر (— أو -- أو ***) وحده في السطر (مرة واحدة كحد أقصى)
    re.compile(r'^\s*(?:-{2,}|—{2,}|\*{3,}|_{3,}|={3,})\s*$'),
]

# ============================================================
# [4] تكرار الحروف والكلمات
# ============================================================
_CHAR_RUN_RE = re.compile(r'(.)\1{2,}')          # 3+ نفس الحرف → حرفان (clean)
_CHAR_RUN_CANON_RE = re.compile(r'(.)\1{1,}')    # 2+ نفس الحرف → حرف واحد (canonical)
_WORD_RUN_RE = re.compile(r'(\S+)(\s+\1\b)+')    # كلمة تتكرر متتالية → مرة واحدة

# ============================================================
# [5] التطبيع العربي (canonical فقط)
# ============================================================
_ARABIC_NORMALIZE_MAP = str.maketrans({
    'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ٱ': 'ا',
    'ؤ': 'و', 'ئ': 'ي', 'ة': 'ه', 'ى': 'ي',
    'ـ': '',  # tatweel/kashida
})
_ARABIC_DIACRITICS = re.compile(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]')
_NON_WORD_CANON_RE = re.compile(r'[^\w\s]')
_WS_CANON_RE = re.compile(r'\s+')

# ============================================================
# [6] توحيد اللهجات (canonical فقط) — خريطة آمنة ومحافظة
# كلمات شائعة جدًا بمعنى واحد، لا تُنتج التباسًا عند التبديل.
# clean لا يُمس (الاحتفاظ بالسياق — اللهجة إشارة مهمة للـAI).
# ============================================================
_DIALECT_MAP = {
    'شلون': 'كيف',
    'ليش': 'لماذا',
    'شنو': 'ماذا', 'شو': 'ماذا', 'ايش': 'ماذا', 'وش': 'ماذا',
    'وين': 'اين',
    'الحين': 'الان', 'هالحين': 'الان',
    'ابي': 'اريد', 'ابغى': 'اريد', 'ابى': 'اريد', 'بغيت': 'اريد', 'ودي': 'اريد',
    'ابغي': 'اريد',
    'مين': 'من',
    'كذا': 'هكذا',
    'هذاك': 'ذلك',
}
_DIALECT_RE = re.compile(r'\b(' + '|'.join(
    re.escape(k) for k in sorted(_DIALECT_MAP, key=len, reverse=True)
) + r')\b')


def _strip_signature_lines(text: str, removed: Dict[str, int]) -> str:
    """يُزيل أسطر التوقيع الهيكلية (footers/fواصل) — من الآخر للأمان."""
    if not text:
        return text
    lines = text.splitlines()
    changed = False
    # device footer: آخر سطرين فقط (التوقيع دائمًا في النهاية)
    for idx in range(len(lines) - 1, max(len(lines) - 3, -1), -1):
        ln = lines[idx] if idx >= 0 else ''
        if not ln.strip():
            continue
        matched = False
        for pat in _SIGNATURE_RES:
            if pat.search(ln):
                matched = True
                break
        if matched:
            lines[idx] = ''
            changed = True
            removed['signatures'] = removed.get('signatures', 0) + 1
        # فاصل توقيع واحد فقط — لا نمسح قائمة كاملة من الفواصل
        if 'signatures' in removed and removed['signatures'] >= 2:
            break
    if not changed:
        return text
    return '\n'.join(lines)


def _collapse_char_runs(s: str, removed: Dict[str, int]) -> str:
    """«صرررراحة» → «صرراحة» (keep 2) — يمنع حروف مطولة تضخم الحجم."""
    if not s:
        return s

    def _sub(m):
        removed['char_runs'] = removed.get('char_runs', 0) + 1
        return m.group(1) * 2

    return _CHAR_RUN_RE.sub(_sub, s)


def _collapse_word_runs(s: str, removed: Dict[str, int]) -> str:
    """«محتاج محتاج محتاج مساعدة» → «محتاج مساعدة»."""
    if not s:
        return s

    def _sub(m):
        removed['word_runs'] = removed.get('word_runs', 0) + 1
        return m.group(1)

    return _WORD_RUN_RE.sub(_sub, s)


def _remove_emoji(s: str, removed: Dict[str, int]) -> str:
    if not s:
        return s

    def _sub(m):
        removed['emojis'] = removed.get('emojis', 0) + len(m.group(0))
        return ' '

    return _EMOJI_RE.sub(_sub, s)


def _remove_links(s: str, removed: Dict[str, int]) -> str:
    if not s:
        return s

    def _sub(m):
        removed['links'] = removed.get('links', 0) + 1
        return ' '

    return _URL_RE.sub(_sub, s)


def _canonical_arabic(s: str) -> str:
    """تطبيع عربي كامل + إزالة تشكيل + إزالة غير الكلمات + توحيد لهجات."""
    if not s:
        return s
    t = s.translate(_ARABIC_NORMALIZE_MAP)
    t = _ARABIC_DIACRITICS.sub('', t)
    # توحيد اللهجات (word-boundary، الأطول أولًا)
    t = _DIALECT_RE.sub(lambda m: _DIALECT_MAP[m.group(1)], t)
    # إزالة كل ما ليس حرفًا/رقمًا/مسافة (ترقيم → مسافة)
    t = _NON_WORD_CANON_RE.sub(' ', t)
    # ضغط الحروف المتكررة إلى حرف واحد (مطبّع للتقارب الدلالي)
    t = _CHAR_RUN_CANON_RE.sub(r'\1', t)
    t = _WS_CANON_RE.sub(' ', t).strip()
    return t


def normalize(text: str) -> NormalizedText:
    """المرحلة 1 — يُنتج clean (للـAI) + canonical (للـhash)."""
    removed: Dict[str, int] = {}
    original = text or ''

    if not original.strip():
        return NormalizedText(original=original, clean='', canonical='', removed=removed)

    # --- بناء clean ---
    clean = _remove_emoji(original, removed)
    clean = _remove_links(clean, removed)
    clean = _strip_signature_lines(clean, removed)
    clean = _collapse_char_runs(clean, removed)
    clean = _collapse_word_runs(clean, removed)
    # ضغط مسافات فقط (الأسطر محفوظة — تعدد الأسطر إشارة سياق للـAI)
    clean = re.sub(r'[ \t]+', ' ', clean)
    clean = re.sub(r' ?\n ?', '\n', clean).strip('\n').strip()

    # --- بناء canonical (على clean — لا نعيد إزالة ما أُزيل) ---
    canonical = _canonical_arabic(clean)

    return NormalizedText(
        original=original,
        clean=clean,
        canonical=canonical,
        removed=removed,
    )
