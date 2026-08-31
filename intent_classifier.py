#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
intent_classifier.py — Request Intent Engine v4.0 / المرحلتان 2+3: تصنيف النية بالـAI
================================================================================
STAGES 2+3 of the v4.0 rebuild. AI Intent Classification Engine — القرار الأساسي.

الفلسفة (طلب المُشغّل الصريح):
  - لا keyword matching كقرار نهائي. الـLLM هو المُصنِّف.
  - الكلمات المفتاحية (المرحلة extracts_signals في request_filter.py) تُمرَّر
    للنموذج كـ«إشارات لغوية مساعدة» فقط — noisy lexical hints، ليست حكمًا.
  - أي فشل AI (لا مفاتيح/timeout/parse error) → REJECT (ai_unavailable /
    ai_error) — لا يوجد أبدًا fallback إلى قرار الكلمات المفتاحية.

المزوّدون: نفس متغيرات AIAnalyzer (صفر إعداد جديد للمُشغّل):
  OPENAI_API_KEY / OPENAI_API_URL / AI_MODEL
  AI_KEY_2..8 / AI_URL_2..8 / AI_MODEL_2..8
  (Groq OpenAI-compat افتراضيًا؛ يعمل مع أي endpoint متوافق بما فيه Gemini
   OpenAI-compat عبر ضبط OPENAI_API_URL/AI_MODEL.)

العقد (JSON فقط):
  {"decision":"ACCEPT|REJECT","confidence":0.0-1.0,"category":"...","reason":"..."}

تطبيق العتبة (confidence >= 0.85) يحدث في المُنسِّق (request_filter.analyze_request_v4)
— هذا الملف يُعيد قرار الـAI كما هو (مقايَس ومُتحقَّق منه فقط).

الاختبارات: transport injection — constructor يستقبل transport=async callable
(provider, messages) -> (status, body). الإنتاج يستخدم aiohttp.
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
    "tutoring_request",            # يبحث عن مدرس/دكتور/معلم يدرّسه أو يشرح له
    "homework_execution_request",  # يطلب أحدًا يحل/ينجز/يسوي واجب/بحث/تقرير/مشروع
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
})

VALID_CATEGORIES = ACCEPT_CATEGORIES | REJECT_CATEGORIES

# فئات REJECT التي يُنتجها النظام نفسه (ليست من الـAI)
SYSTEM_REJECT_CATEGORIES = frozenset({
    "duplicate", "empty", "relay_repost", "ai_unavailable", "ai_error",
    "invalid_output", "low_confidence",
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
    error: str = ""                        # آخر خطأ (لو ok=False)

    @property
    def is_accept(self) -> bool:
        return self.decision == "ACCEPT"


# ============================================================
# Prompt — العقد الدلالي (المرحلة 2: تعريف النية)
# ============================================================
SYSTEM_PROMPT = """أنت مصنّف نوايا (Intent Classifier) صارم لقناة «طلبات مساعدة أكاديمية» تخدم طلاب جامعات الخليج. مهمتك: تحديد هل الرسالة «طلب مساعدة أكاديمية شخصي مباشر» يستحق النشر للطلاب، أم لا.

القاعدة الذهبية (لا تتنازل عنها أبدًا):
ACCEPT فقط إذا وُجد دليل واضح وصريح أن المرسل نفسه يطلب من شخص آخر أن ينفّذ/يحلّ/يشرح/يدرّس/يتولّى خدمة أكاديمية تخص المرسل شخصيًا.
أي غموض، أو شك، أو نقص الدليل = REJECT.

فئات ACCEPT المسموحة (لا شيء غيرها):
- "tutoring_request": المرسل يبحث عن مدرس/دكتور/معلم/أستاذ يدرّسه أو يشرح له مادة. أمثلة: «مين يعرف دكتور يشرح رياضيات؟»، «أبي مدرس خصوصي للمادة».
- "homework_execution_request": المرسل يطلب أحدًا يحل/ينجز/يسوي/يكتب له واجب أو بحث أو تقرير أو مشروع أو سؤالًا. أمثلة: «أحد يشرح لي تفاضل 1»، «احتاج شخص يحل معي السؤال»، «محتاج أحد يكتب بحثي».

فئات REJECT (كل ما ليس ACCEPT أعلاه):
- "advertisement": إعلان تجاري أو ترويج: تعلّم التداول واربح، بوت خصوصي، للتواصل واتساب، كورسات مدفوعة.
- "service_offer": المرسل يعرض خدمته أو يُحيل لجهة تقدم خدمة: «عندي دكتور يساعد في الرسائل والتكاليف»، «حل واجبات وبحوث».
- "praise_testimonial": مدح أو شكر أو تجربة شخصية مع منصة/مدرس: «شكراً منصة اكتمال جبت درجة عالية».
- "religious_general_content": محتوى ديني أو وعظي أو دعاء أو حكمة عامة: «حين يحبك الله يبدل وجه الحياة».
- "non_request_question": سؤال معلوماتي عن الدراسة لا يطلب تنفيذ خدمة: «كم نسبة الحرمان؟»، «هل الاختبار 5 أقسام؟».
- "recommendation_or_opinion": طلب رأي أو توصية عامة وليست طلب خدمة مباشرة: «مين أفضل مدرس؟».
- "general_discussion": نقاش عام أو فضفضة أو ملاحظة: «مدري ليه جابوا فلان»، «احس تفرق من أستاذ لأستاذ».
- "other": أي شيء آخر لا يناسب ما أعلاه.

قواعد تفصيلية حاسمة:
1. «مين يعرف مدرس رياضيات؟» = ACCEPT (tutoring_request) — يبحث عن مدرس يخدمه.
2. «مين أفضل مدرس؟» = REJECT (recommendation_or_opinion) — يستطلع آراء، لا يطلب خدمة.
3. الرسالة التي يعرض فيها المرسل خدمة = REJECT دائمًا حتى لو استعمل كلمات مثل يساعد/يشرح/يحل («عندي دكتور يساعد»).
4. المدح/الشكر على خدمة سابقة = REJECT (praise_testimonial) وليست طلبًا.
5. الإعلانات والمحتوى الديني والأسئلة المعلوماتية = REJECT دائمًا.
6. الإشارات اللغوية المرفقة (إن وُجدت) مستخرجة آليًا من قوائم كلمات مفتاحية قديمة — اعتبرها استدلالًا مساعدًا ضعيفًا فقط. القرار قرارك المستقل من فهم المعنى الكامل للرسالة. لو تعارضت الإشارات مع المعنى الواضح، اتبع المعنى.
7. الرسائل غير المفهومة أو المبتورة أو بلا معنى واضح = REJECT (other).

الناتج: JSON فقط، بلا أي نص إضافي، بهذا الشكل بالضبط:
{"decision":"ACCEPT أو REJECT","confidence":رقم من 0.0 إلى 1.0,"category":"إحدى الفئات أعلاه","reason":"سبب مختصر جدًا بالعربية"}

أمثلة:
- «أحد يشرح لي التفاضل» → {"decision":"ACCEPT","confidence":0.93,"category":"homework_execution_request","reason":"طالب شخصًا يشرح له مادة"}
- «شكراً اكتمال جبت درجة عالية» → {"decision":"REJECT","confidence":0.97,"category":"praise_testimonial","reason":"مدح منصة بعد تجربة"}
- «مين أفضل مدرس؟» → {"decision":"REJECT","confidence":0.88,"category":"recommendation_or_opinion","reason":"استطلاع رأي عام وليس طلب خدمة"}
- «مين يعرف دكتور يشرح رياضيات؟» → {"decision":"ACCEPT","confidence":0.9,"category":"tutoring_request","reason":"يبحث عن مدرس لمادة الرياضيات"}"""


# ============================================================
# JSON extraction / validation (متسامح مع fences وnoise)
# ============================================================
_FENCE_RE = re.compile(r'```(?:json)?\s*\n?(.*?)```', re.DOTALL)


def extract_json_text(text: str) -> str:
    """يستخرج نص الـJSON من رد النموذج (يتعامل مع ```json وnoise قبل/بعد)."""
    if not text:
        return ""
    t = text.strip()
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
# المصنِّف
# ============================================================
class IntentClassifier:
    """AI-first intent classifier. القرار الأساسي للمرحلتين 2+3.

    - classify(text, hints) → IntentDecision (raw AI decision؛ العتبة في المُنسِّق).
    - فشل كامل → IntentDecision(ok=False, REJECT, ai_error/ai_unavailable).
    - لا يستخدم الكلمات المفتاحية للقرار أبدًا — hints تُمرَّر للنموذج كسياق مساعد فقط.
    """

    def __init__(self,
                 providers: Optional[List[Dict[str, str]]] = None,
                 timeout_s: float = 10.0,
                 max_attempts: int = 2,
                 max_chars: int = 1200,
                 max_concurrent: int = 8,
                 transport: Optional[Callable] = None):
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
        self.enabled = bool(self.providers)
        # counters (تشخيص /api/filter_stats)
        self.counters = {
            "calls": 0, "accepts": 0, "rejects": 0, "errors": 0,
            "timeouts": 0, "parse_failures": 0, "rotations": 0,
            "total_latency_ms": 0,
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
    # prompt construction
    # --------------------------------------------------------
    @staticmethod
    def build_user_prompt(clean_text: str, hints: Optional[Dict[str, Any]]) -> str:
        parts = []
        if hints:
            parts.append("إشارات لغوية مستخرجة آليًا (استدلال مساعد فقط — القرار قرارك):")
            parts.append(json.dumps(hints, ensure_ascii=False))
            parts.append("")
        parts.append("الرسالة:")
        parts.append(f'"""{clean_text}"""')
        parts.append("")
        parts.append("صنّفها وأعد الـJSON فقط.")
        return "\n".join(parts)

    def _build_payload(self, clean_text: str, hints: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "model": "",  # يُملأ لكل provider
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self.build_user_prompt(clean_text, hints)},
            ],
            "temperature": 0.0,
            "max_tokens": 160,
            "stream": False,
        }

    # --------------------------------------------------------
    # main entry
    # --------------------------------------------------------
    async def classify(self, text: str, hints: Optional[Dict[str, Any]] = None) -> IntentDecision:
        """يصنّف الرسالة. لا يطبّق عتبة القبول — يُعيد قرار الـAI المقاس."""
        if not self.enabled:
            return IntentDecision(ok=False, decision="REJECT", confidence=0.0,
                                  category="ai_unavailable", reason="ai_unavailable",
                                  error="no providers configured")
        if not text or not text.strip():
            return IntentDecision(ok=False, decision="REJECT", confidence=0.0,
                                  category="empty", reason="empty", error="empty text")

        clean = text.strip()[: self.max_chars]
        payload = self._build_payload(clean, hints)
        attempts = min(self.max_attempts, len(self.providers))
        last_error = ""

        for _ in range(attempts):
            async with self._lock:
                provider = self.providers[self._current]
            payload["model"] = provider["model"]
            t0 = time.monotonic()
            self.counters["calls"] += 1
            try:
                async with self._sem:
                    status, body = await asyncio.wait_for(
                        self._call_transport(provider, payload),
                        timeout=self.timeout_s,
                    )
                latency = int((time.monotonic() - t0) * 1000)
                self.counters["total_latency_ms"] += latency
            except asyncio.TimeoutError:
                self.counters["timeouts"] += 1
                last_error = f"timeout after {self.timeout_s}s ({provider['name']})"
                self._rotate()
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.counters["errors"] += 1
                last_error = f"{type(e).__name__}: {e} ({provider['name']})"
                self._rotate()
                continue

            if status != 200:
                self.counters["errors"] += 1
                last_error = f"http {status} ({provider['name']})"
                self._rotate()
                continue

            content = self._extract_content(body)
            if content is None:
                self.counters["parse_failures"] += 1
                last_error = f"no content in response ({provider['name']})"
                self._rotate()
                continue

            decision = self._parse_decision(content, provider, latency)
            if decision is not None:
                if decision.decision == "ACCEPT":
                    self.counters["accepts"] += 1
                else:
                    self.counters["rejects"] += 1
                return decision
            last_error = f"invalid JSON output ({provider['name']})"
            self._rotate()
            continue

        # كل المحاولات فشلت → REJECT صارم (لا keyword fallback أبدًا)
        self.counters["errors"] += 1
        return IntentDecision(
            ok=False, decision="REJECT", confidence=0.0,
            category="ai_error", reason="ai_error",
            provider_name="", latency_ms=0,
            error=last_error or "all attempts failed",
        )

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
        return {
            "enabled": self.enabled,
            "providers": len(self.providers),
            "timeout_s": self.timeout_s,
            "max_attempts": self.max_attempts,
            "max_concurrent": self.max_concurrent,
            **dict(self.counters),
            "avg_latency_ms": round(self.counters["total_latency_ms"] / calls, 1),
        }

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
        self._session = None
