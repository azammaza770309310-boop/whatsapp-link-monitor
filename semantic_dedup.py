#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
semantic_dedup.py — Request Intent Engine v4.0 / المرحلة 4: منع التكرار الدلالي
================================================================================
STAGE 4 of the v4.0 rebuild. Duplicate Intelligence بثلاث طبقات:

  1. exact hash    — MD5 للـcanonical النص كاملًا (نفس النص بعد التطبيع).
  2. semantic hash — MD5 لمجموعة الكلمات الدلالية (content tokens) مرتّبة
                     (بلا stopwords وبعد light stemming): يلتقط إعادة الترتيب
                     والتعديلات الطفيفة («أحد يشرح لي التفاضل» vs «يشرح أحد
                     لي التفاضل» = نفس الـhash).
  3. near-dup      — Jaccard similarity ≥ threshold على مجموعات الكلمات ضد
                     آخر N بصمة (يلتطف «نفس المعنى بصياغة مختلفة قليلًا»:
                     إضافة/حذف كلمة واحدة غالبًا ما تبقى فوق 0.8).

سلوك is_duplicate(text):
  - يُرجع DupResult(is_dup=True, kind=..., similarity=...) لو الرسالة مكررة
    خلال TTL (default 900s = 15 دقيقة «فترة قصيرة»).
  - المرة الأولى تُسجَّل عبر register() — كل رسالة صُنِّفت (ACCEPT أو REJECT)
    تُسجَّل: تكرار الإعلانات المكرّة يوفّر نداءات AI أيضًا.

Bound + TTL دائمًا (ذاكرة محدودة). Pure in-memory — لا DB.
القرار نهائيًا لا يعتمد على كلمات مفتاحية — هذا dedup هيكلي فقط.
"""

import hashlib
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, FrozenSet, Optional, Tuple

from text_normalizer import normalize as _tn_normalize


# ============================================================
# Stopwords عربية — تُستبعد من بصمة الكلمات الدلالية فقط
# ============================================================
_AR_STOPWORDS = frozenset({
    # حروف وشروط وأدوات — لا قيمة تمييزية دلالية
    'من', 'في', 'على', 'الى', 'عن', 'مع', 'هذا', 'هذه', 'ذلك', 'تلك',
    'التي', 'الذي', 'الذين', 'ان', 'او', 'ثم', 'لو', 'لا', 'ما', 'هل',
    'يا', 'بعد', 'قبل', 'كل', 'بعض', 'غير', 'نفس', 'هنا', 'هناك',
    'ايضا', 'جدا', 'بس', 'كمان', 'يكون', 'تكون', 'كان', 'كانت',
    'هو', 'هي', 'هم', 'انا', 'انت', 'انتم', 'نحن',
    'و', 'او', 'ثم', 'لكن', 'حتى', 'اذا', 'عند', 'عندما', 'بين', 'بدون',
    # توحيد اللهجات (canonical map) يُنتج هذه أيضًا
    'كيف', 'لماذا', 'ماذا', 'اين', 'الان', 'اريد', 'هكذا', 'ذلك',
})


def _light_strip(token: str) -> str:
    """Light stemming آمن للـhash: إزالة «ال» التعريف + سوابق و/ف/ب/ل/ك +
    لواحق الضمائر — بشرط بقاء جذر ≥ 3 حروف. لا يُستخدم للتصنيف، فقط للتقارب."""
    t = token
    if len(t) >= 4 and t.startswith('ال'):
        t = t[2:]
    # سابقة واحدة فقط (و/ف/ب/ل/ك) — «وبالبحوث» → «البحوث» → handled above? يُطبق قبل
    if len(t) >= 4 and t[0] in 'و ف ب ل ك' and not t.startswith('كل'):
        t = t[1:]
        if len(t) >= 4 and t.startswith('ال'):
            t = t[2:]
    # لاحقات الضمائر (الأطول أولًا)
    for suf in ('كم', 'هم', 'هن', 'ها', 'نا', 'ني', 'ه', 'ك', 'ي'):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            t = t[:-len(suf)]
            break
    return t


def content_tokens(canonical: str) -> FrozenSet[str]:
    """مجموعة الكلمات الدلالية من النص القياسي (بلا stopwords، بعد light stemming)."""
    if not canonical:
        return frozenset()
    toks = []
    for raw in canonical.split():
        if len(raw) < 2:
            continue
        t = _light_strip(raw)
        if len(t) < 2 or t in _AR_STOPWORDS:
            continue
        toks.append(t)
    return frozenset(toks)


@dataclass
class DupResult:
    """نتيجة فحص التكرار."""
    is_dup: bool = False
    kind: str = ""            # exact | semantic | near | ""
    similarity: float = 0.0   # 1.0 للـexact/semantic
    matched_hash: str = ""


@dataclass
class _Fingerprint:
    exact_hash: str
    semantic_hash: str
    tokens: FrozenSet[str]


def _md5(s: str) -> str:
    return hashlib.md5(s.encode('utf-8', 'replace')).hexdigest()


class SemanticDeduper:
    """Duplicate Intelligence — exact + semantic + near-dup (Jaccard)."""

    def __init__(self,
                 ttl_s: float = 900.0,
                 max_entries: int = 4000,
                 near_window: int = 600,
                 jaccard_threshold: float = 0.80):
        self.ttl = float(ttl_s)
        self.max_entries = int(max_entries)
        self.near_window = int(near_window)
        self.jaccard_threshold = float(jaccard_threshold)
        # exact → timestamp
        self._exact: Dict[str, float] = {}
        # semantic_hash → timestamp
        self._semantic: Dict[str, float] = {}
        # آخر N بصمات (للـJaccard scan)
        self._recent: Deque[Tuple[_Fingerprint, float]] = deque(maxlen=self.near_window)
        self._seen_count = 0
        self._dup_count = 0

    # --------------------------------------------------------
    # fingerprint
    # --------------------------------------------------------
    @staticmethod
    def fingerprint(canonical: str) -> _Fingerprint:
        exact = _md5(canonical[:600])
        toks = content_tokens(canonical)
        semantic = _md5(' '.join(sorted(toks)))
        return _Fingerprint(exact_hash=exact, semantic_hash=semantic, tokens=toks)

    # --------------------------------------------------------
    # internal: prune by TTL
    # --------------------------------------------------------
    def _prune(self, now: float) -> None:
        if len(self._exact) > self.max_entries:
            for k in [k for k, ts in self._exact.items() if ts < now - self.ttl]:
                self._exact.pop(k, None)
            if len(self._exact) > self.max_entries:
                for k in sorted(self._exact, key=lambda k: self._exact[k])[:len(self._exact) - self.max_entries]:
                    self._exact.pop(k, None)
        if len(self._semantic) > self.max_entries:
            for k in [k for k, ts in self._semantic.items() if ts < now - self.ttl]:
                self._semantic.pop(k, None)
            if len(self._semantic) > self.max_entries:
                for k in sorted(self._semantic, key=lambda k: self._semantic[k])[:len(self._semantic) - self.max_entries]:
                    self._semantic.pop(k, None)
        # recent: مسح الكسول للمنتهي (مسح كامل مكلف — يكفي فحص expiry عند المطابقة)
        if self._recent and self._recent[0][1] < now - self.ttl:
            keep = deque(maxlen=self.near_window)
            for fp, ts in self._recent:
                if ts >= now - self.ttl:
                    keep.append((fp, ts))
            self._recent = keep

    # --------------------------------------------------------
    # public API
    # --------------------------------------------------------
    def check(self, canonical: str, now: Optional[float] = None) -> DupResult:
        """يفحص فقط (بلا تسجيل). يُستخدم للفحص قبل AI."""
        now = time.time() if now is None else float(now)
        self._prune(now)
        fp = self.fingerprint(canonical)

        # 1) exact
        ts = self._exact.get(fp.exact_hash)
        if ts is not None and ts >= now - self.ttl:
            return DupResult(True, 'exact', 1.0, fp.exact_hash)

        # 2) semantic (نفس مجموعة الكلمات بأي ترتيب)
        ts = self._semantic.get(fp.semantic_hash)
        if ts is not None and ts >= now - self.ttl:
            return DupResult(True, 'semantic', 1.0, fp.semantic_hash)

        # 3) near-dup (Jaccard) — فقط لو فيه كلمات كافية للتمييز
        if len(fp.tokens) >= 3:
            for other, ots in self._recent:
                if ots < now - self.ttl:
                    continue
                if not other.tokens:
                    continue
                inter = len(fp.tokens & other.tokens)
                if inter == 0:
                    continue
                union = len(fp.tokens | other.tokens)
                sim = inter / union
                if sim >= self.jaccard_threshold:
                    return DupResult(True, 'near', round(sim, 3), other.semantic_hash)
        return DupResult(False, '', 0.0, '')

    def register(self, canonical: str, now: Optional[float] = None) -> None:
        """يسجّل البصمة (يُستدعى لكل رسالة صُنِّفت — ACCEPT وREJECT كليهما)."""
        now = time.time() if now is None else float(now)
        fp = self.fingerprint(canonical)
        self._exact[fp.exact_hash] = now
        self._semantic[fp.semantic_hash] = now
        self._recent.append((fp, now))
        self._seen_count += 1

    def is_duplicate(self, text: str, now: Optional[float] = None) -> DupResult:
        """فحص + تسجيل في خطوة واحدة (compatible مع نمط ContentDeduper القديم)."""
        now = time.time() if now is None else float(now)
        canonical = _tn_normalize(text).canonical
        if not canonical:
            return DupResult(False, '', 0.0, '')
        res = self.check(canonical, now)
        if res.is_dup:
            self._dup_count += 1
            # refresh تاريخ آخر ظهور
            self.register(canonical, now)
        else:
            self.register(canonical, now)
        return res

    def stats(self) -> dict:
        return {
            "ttl_s": self.ttl,
            "max_entries": self.max_entries,
            "near_window": self.near_window,
            "jaccard_threshold": self.jaccard_threshold,
            "exact_entries": len(self._exact),
            "semantic_entries": len(self._semantic),
            "recent_window": len(self._recent),
            "total_seen": self._seen_count,
            "total_duplicates": self._dup_count,
        }
