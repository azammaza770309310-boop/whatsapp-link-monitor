#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
request_guard.py — طبقات حماية مسار الطلبات (Request Filter Guards)

ثلاث طبقات حماية مستقلة تُلتفّ حول قرار الفلتر (analyze_request) لمنع
فيضان القناة (الذي سبب مشكلة 15,000 رسالة سابقًا):

  1. RateLimiter        — حدّان: عالمي (default 20/min) + لكل مجموعة (5/min).
                          نافذة منزلقة 60 ثانية. يعدّ الـACCEPT فقط.
  2. CircuitBreaker     — لو ACCEPT تجاوز threshold (default 100/10min)
                          → يُفصّل تلقائيًا (cooldown default 600s) ويُسجّل:
                          🚨 [REQUEST-FILTER] CIRCUIT BREAKER ACTIVATED
                          لا يوقف البوت ولا مسار الروابط — مسار الطلبات فقط.
  3. ContentDeduper     — dedup ثانوي على hash النص المُطبّع (TTL 10min،
                          bounded). مستقل عن (chat_id,msg_id) dedup الأساسي.
                          لا يؤثر على مسار الروابط إطلاقًا.

كلها pure-Python، لا Telegram، لا async مطلوب (تُستدعى داخل asyncio
لكنها تزامنية وسريعة — micro-operations على deque/dict).

لا تُلمس مسار استخراج الروابط إطلاقًا.
"""

import time
import re
import hashlib
from collections import deque
from typing import Optional


# ============================================================
# [1] RateLimiter — حدّ عالمي + حدّ لكل مجموعة (نافذة منزلقة)
# ============================================================
class RateLimiter:
    """Sliding-window rate limiter — global + per-chat.

    try_acquire(chat_id) يفحص الحدين ويسجّل محاولة لو مسموحة. يعيد False
    لو أي من الحدين مُتجاوز. الـACCEPT فقط يستهلك token (يُستدعى بعد
    نجاح الفلتر). لا يحجب المسار عدا عن طريق return False.
    """

    def __init__(self, max_per_minute: int = 20,
                 max_per_chat_per_minute: int = 5, window_s: float = 60.0):
        self.max_global = max(1, int(max_per_minute))
        self.max_per_chat = max(1, int(max_per_chat_per_minute))
        self.window = float(window_s)
        self._global: "deque[float]" = deque()
        self._per_chat: "dict[int, deque[float]]" = {}

    def _prune(self, dq: "deque[float]", now: float) -> None:
        cutoff = now - self.window
        while dq and dq[0] <= cutoff:
            dq.popleft()

    def try_acquire(self, chat_id, now: Optional[float] = None) -> bool:
        """يفحص الحدين ويسجّل لو مسموحة. يعيد True لو مسموحة، False لو محجوبة."""
        now = float(now) if now is not None else time.time()
        self._prune(self._global, now)
        try:
            cid = int(chat_id)
        except (TypeError, ValueError):
            cid = 0
        dq = self._per_chat.get(cid)
        if dq is None:
            dq = deque()
            self._per_chat[cid] = dq
        self._prune(dq, now)
        if len(self._global) >= self.max_global:
            return False
        if len(dq) >= self.max_per_chat:
            return False
        self._global.append(now)
        dq.append(now)
        return True

    def stats(self, now: Optional[float] = None) -> dict:
        now = float(now) if now is not None else time.time()
        self._prune(self._global, now)
        return {
            "global_count": len(self._global),
            "global_limit": self.max_global,
            "chats_tracked": len(self._per_chat),
        }


# ============================================================
# [2] CircuitBreaker — إيقاف طوارئ عند فيضان الـACCEPT
# ============================================================
class CircuitBreaker:
    """يُفصّل تلقائيًا لو ACCEPT تجاوز threshold في نافذة زمنية.

    - record_accept() يضيف timestamp ويُفحص threshold.
    - is_tripped() يعيد True خلال cooldown (يُعاد ضبطه عند كل trip).
    - بعد cooldown → auto-reset (نافذة جديدة فارغة).
    - لا يوقف البوت ولا مسار الروابط — return True من is_tripped
      يعني فقط «مسار الطلبات يجب أن يتخطّى هذه الرسالة».
    """

    def __init__(self, threshold: int = 100, window_s: float = 600.0,
                 cooldown_s: float = 600.0):
        self.threshold = max(1, int(threshold))
        self.window = float(window_s)
        self.cooldown = float(cooldown_s)
        self._accepts: "deque[float]" = deque()
        self._tripped_until: float = 0.0
        self._tripped_count: int = 0

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        while self._accepts and self._accepts[0] <= cutoff:
            self._accepts.popleft()

    def is_tripped(self, now: Optional[float] = None) -> bool:
        now = float(now) if now is not None else time.time()
        if now >= self._tripped_until:
            # cooldown انتهى → auto-reset لو كان مفصولاً
            if self._tripped_until > 0:
                self._tripped_until = 0.0
                self._accepts.clear()
            return False
        return True

    def record_accept(self, now: Optional[float] = None) -> bool:
        """يسجّل ACCEPT ويفحص threshold. يعيد True لو أطلق الـtrip الآن."""
        now = float(now) if now is not None else time.time()
        if now < self._tripped_until:
            # ما زال مفصولاً — لا نسجّل أثناء cooldown
            return False
        self._prune(now)
        self._accepts.append(now)
        if len(self._accepts) >= self.threshold:
            self._tripped_until = now + self.cooldown
            self._tripped_count += 1
            self._accepts.clear()
            return True
        return False

    def reset(self) -> None:
        self._accepts.clear()
        self._tripped_until = 0.0

    def stats(self) -> dict:
        return {
            "tripped": self.is_tripped(),
            "tripped_count": self._tripped_count,
            "accepts_in_window": len(self._accepts),
            "threshold": self.threshold,
            "tripped_until": self._tripped_until,
        }


# ============================================================
# [3] ContentDeduper — dedup ثانوي على hash النص (TTL 10min)
# ============================================================
_AR_NORM_MAP = str.maketrans({
    'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ٱ': 'ا',
    'ؤ': 'و', 'ئ': 'ي', 'ة': 'ه', 'ى': 'ي', 'ـ': '',
})
_DIACRITICS = re.compile(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]')
_WS = re.compile(r'\s+')


class ContentDeduper:
    """dedup ثانوي على محتوى النص المُطبّع.

    - is_duplicate(text) يعيد True لو رأينا نفس النص (normalized hash) خلال
      TTL. يسجّل hash لو جديد. لا يحل محل (chat_id,msg_id) dedup الأساسي.
    - bounded: لو تجاوز max_entries يُ prune بالـTTL.
    - لا يؤثر على مسار الروابط — يُستدعى فقط من مسار الطلبات.
    """

    def __init__(self, ttl_s: float = 600.0, max_entries: int = 5000):
        self.ttl = float(ttl_s)
        self.max = int(max_entries)
        self._seen: "dict[str, float]" = {}

    @staticmethod
    def _normalize(text: str) -> str:
        if not text:
            return ""
        t = text.lower()
        t = t.translate(_AR_NORM_MAP)
        t = _DIACRITICS.sub('', t)
        t = _WS.sub(' ', t).strip()
        return t

    def _hash(self, text: str) -> str:
        n = self._normalize(text)
        # قص لتجنب hash ضخمة على رسائل طويلة (أول 500 حرف كافية للتمييز)
        return hashlib.md5(n[:500].encode('utf-8', 'replace')).hexdigest()

    def _prune(self, now: float) -> None:
        if len(self._seen) <= self.max:
            # prune خفيف: احذف المنتهية فقط لو قاربنا الحد
            if len(self._seen) > self.max * 0.9:
                cutoff = now - self.ttl
                expired = [k for k, ts in self._seen.items() if ts < cutoff]
                for k in expired:
                    del self._seen[k]
            return
        # تجاوزنا الحد → prune صارم
        cutoff = now - self.ttl
        expired = [k for k, ts in self._seen.items() if ts < cutoff]
        for k in expired:
            del self._seen[k]
        # لو ما زال فوق الحد، احذف الأقدم
        if len(self._seen) > self.max:
            for k, _ in sorted(self._seen.items(), key=lambda kv: kv[1])[:len(self._seen) - self.max]:
                self._seen.pop(k, None)

    def is_duplicate(self, text: str, now: Optional[float] = None) -> bool:
        now = float(now) if now is not None else time.time()
        self._prune(now)
        h = self._hash(text)
        if not h:
            return False
        if h in self._seen:
            ts = self._seen[h]
            # per-entry TTL: لو انتهت صلاحية الإدخال رغم عدم بلوغ السعة
            # (الـprune الكسول لا يفحص كل إدخال تحت 90% سعة) → عامل كجديد.
            if ts < now - self.ttl:
                self._seen[h] = now
                return False
            self._seen[h] = now  # refresh
            return True
        self._seen[h] = now
        return False

    def stats(self) -> dict:
        return {"entries": len(self._seen), "max": self.max, "ttl": self.ttl}
