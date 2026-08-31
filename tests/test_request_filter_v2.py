#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Request Filter v2 — Comprehensive Test Suite
============================================
يختبر الفلتر المحافظ الجديد + طبقات الحماية (Kill Switch + RateLimiter +
CircuitBreaker + ContentDeduper) + استقلالية المسارين + capture-first.

الأقسام:
  A. request_filter.analyze_request — ACCEPT/REJECT/critical/provider/seeker+contact
  B. Arabic normalization robustness
  C. request_guard.RateLimiter — global + per-chat + sliding window
  D. request_guard.CircuitBreaker — trip + cooldown + auto-recover
  E. request_guard.ContentDeduper — dup + TTL + bounded + independence
  F. _handle_request_path integration:
     - kill switch (enabled=false → no send; link path unaffected)
     - kill switch on (enabled=true → real request sent to @dhkskwksjskwk)
     - non-request → no send
     - provider → no send
     - rate limit (per-chat) blocks flood
     - circuit breaker blocks after threshold
     - content dedup blocks repeated text
     - channel separation (request target ≠ link channel_id)
     - capture-first snapshot (raw_text in alert, no re-fetch)
     - independence: request path error doesn't crash caller

NO Telegram credentials — SIMULATION ONLY.
"""
import asyncio
import os
import sys
import time
import types
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('BOT_TOKEN', '123:test')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')  # قناة الروابط
os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'testhash')
os.environ.setdefault('OWNER_ID', '12345')
os.environ.setdefault('SUPABASE_URL', '')
os.environ.setdefault('SUPABASE_KEY', '')
os.environ.setdefault('REQUESTS_TARGET_CHANNEL', '@dhkskwksjskwk')
os.environ.setdefault('REQUEST_FILTER_ENABLED', 'true')  # tests default ON

import logging
logging.disable(logging.CRITICAL)

import bot  # noqa: E402
from request_filter import (  # noqa: E402
    analyze_request, is_service_seeker, is_service_provider,
    normalize_text, FILTER_VERSION, analyze_request_v4,
    _has_dotted_word, _has_many_lines,
)
from intent_classifier import IntentClassifier  # noqa: E402
from text_normalizer import normalize as tn_normalize  # noqa: E402
import json as _json  # noqa: E402


def _ai_json(decision, confidence, category, reason):
    return _json.dumps({"decision": decision, "confidence": confidence,
                        "category": category, "reason": reason}, ensure_ascii=False)


def make_scripted_v4_classifier(accept_texts, default_reject=("REJECT", 0.93, "other", "ليس طلبًا")):
    """[v4.0] transport مُبرمَج: القرار المرجعي للنصوص المعروفة (كما يجب أن
    يقرر الـLLM). يُستخدم لإثبات أن السباكة كاملة تُطبّق قرار الـAI."""
    scripts = {}
    for t in accept_texts:
        scripts[tn_normalize(t).clean.strip()] = _ai_json(
            "ACCEPT", 0.93, "homework_execution_request", "طلب مساعدة أكاديمية")
    default = _ai_json(*default_reject)

    async def transport(provider, payload):
        user_msg = payload["messages"][1]["content"]
        inner = user_msg.split('"""')[-2].strip() if '"""' in user_msg else user_msg.strip()
        if inner in scripts:
            content = scripts[inner]
        else:
            content = default
        return 200, _json.dumps({"choices": [{"message": {"content": content}}]})

    return IntentClassifier(providers=[{"key": "k", "url": "u", "model": "mock-v4", "name": "Mock"}],
                             transport=transport)
from request_guard import RateLimiter, CircuitBreaker, ContentDeduper  # noqa: E402

RESULTS = []


def record(name, passed, detail=""):
    RESULTS.append({'name': name, 'passed': passed, 'detail': detail})
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if detail and not passed:
        print(f"         {detail}")


# =========================================================================
# A. analyze_request — ACCEPT / REJECT / provider / seeker+contact
# =========================================================================
async def test_section_A():
    print("\n=== A. analyze_request_v4 (AI-first) ACCEPT/REJECT ===")
    # v4.0: القرار من الـAI — نُبرمج transport بالتصنيف المرجعي (كما يقرر
    # LLM مُدرَّب) ونتحقق أن السباكة تُطبّق القرار بالعتبة والرفض الصارم.
    ACCEPT = [
        "من يسوي لي بحث؟", "أبي أحد يحل لي واجب",
        "مين يعرف أحد يسوي بوربوينت؟", "عندي مشروع تخرج وأحتاج أحد ينجزه",
        "من يساعدني في واجب Excel؟", "أحتاج شخص يكتب لي تقرير جامعي",
        "من يجهز لي عرض تقديمي؟", "أحد يعرف شخص يسوي خريطة مفاهيم؟",
        "من يشرح لي المادة ويحل معي أسئلة المقرر؟",
        "محتاج أحد يساعدني في مشروع جامعي",
    ]
    REJECT = [
        "عندي مشروع", "عندي واجب", "بحثي صعب", "هذا البحث ممتاز",
        "خلصت الواجب", "مشروعي جاهز", "أحتاج مساعدة", "ممكن أحد يساعدني",
        "Excel برنامج ممتاز", "عندي عرض بكرة", "اختباري الأسبوع القادم",
        "بحث", "مشروع",  # critical single words
    ]
    REJECT_AD = [
        "نوفر حل واجبات بأسعار ممتازة للتواصل واتساب",
        "نقدم خدمات بحوث وتقارير ومشاريع",
        "خصم على جميع خدماتنا",
        "متخصصون في إعداد البحوث والمشاريع",
        "أسوي واجبات وسكليفات تواصل خاص",
        "لدينا خدمات PowerPoint وExcel",
    ]
    SEEKER_CONTACT = [
        "أبي أحد يحل لي واجب، هذا رقمي 0551234567",
        "من يسوي لي بحث — تواصل معي t.me/ahmad",
    ]
    PROVIDER = ["أسوي بحوث وتقارير", "أحل واجبات", "نوفر مشاريع تخرج"]

    cl = make_scripted_v4_classifier(ACCEPT + SEEKER_CONTACT)

    async def _ok(texts, want_accept):
        for t in texts:
            r = await analyze_request_v4(t, cl)
            if r.is_request != want_accept:
                return False
        return True

    a_ok = await _ok(ACCEPT, True)
    r_ok = await _ok(REJECT, False)
    ad_ok = await _ok(REJECT_AD, False)
    sc_ok = await _ok(SEEKER_CONTACT, True)
    pv_ok = await _ok(PROVIDER, False)

    # AI down → strict reject (no keyword fallback) على نص keyword-heavy
    nf = not (await analyze_request_v4("أبي أحد يحل لي واجب", None)).is_request
    # low confidence → reject
    cl_low = make_scripted_v4_classifier([], default_reject=("ACCEPT", 0.7, "homework_execution_request", "شك"))
    lc = not (await analyze_request_v4("من يسوي لي بحث؟", cl_low)).is_request
    # critical single words
    crit = ["عندي مشروع", "عندي واجب", "أحتاج مساعدة", "بحث", "مشروع"]
    c_ok = await _ok(crit, False)

    record("ACCEPT (10 real requests)", a_ok)
    record("REJECT (13 general/single-word)", r_ok)
    record("REJECT-AD (6 provider/ads)", ad_ok)
    record("SEEKER+phone/url accepted (2)", sc_ok)
    record("PROVIDER rejected (3)", pv_ok)
    record("v4: AI down → strict REJECT (no keyword fallback)", nf)
    record("v4: ACCEPT conf 0.7 < 0.85 → REJECT low_confidence", lc)
    record("CRITICAL: «عندي مشروع»/«بحث»/«مشروع» alone REJECT", c_ok)
    # seeker/provider helpers consistency (signals in v4 — ليست قرارًا)
    seeker_msg = "من يسوي لي بحث؟"
    provider_msg = "نوفر حل واجبات بأسعار"
    se = is_service_seeker(seeker_msg)                       # True (signals)
    se_not_pv = not is_service_provider(seeker_msg)          # True
    pv = is_service_provider(provider_msg)                   # True (signals)
    pv_not_se = not is_service_seeker(provider_msg)          # True
    record("seeker/provider signal distinction (v4: hints)", se and se_not_pv and pv and pv_not_se)
    return (a_ok and r_ok and ad_ok and sc_ok and pv_ok and c_ok
            and se and se_not_pv and pv and pv_not_se and nf and lc)


# =========================================================================
# B. Arabic normalization robustness
# =========================================================================
async def test_section_B():
    print("\n=== B. Arabic normalization ===")
    # أبي/ابي (أ→ا) should normalize equal
    n1 = normalize_text("أبي أحد يحل لي واجب")
    n2 = normalize_text("ابي احد يحل لي واجب")
    record("أبي أحد ≈ ابي احد (أ→ا)", n1 == n2)
    # ة→ه, ى→ي
    record("خريطة→خريطه (ة→ه)", normalize_text("خريطة") == "خريطه")
    record("سيرة→سيره (ة→ه)", normalize_text("سيرة") == "سيره")
    # diacritics stripped
    record("diacritics stripped", normalize_text("مَنْ يَسْوِي") == "من يسوي")
    # tatweel stripped
    record("tatweel stripped", normalize_text("منـــ يسوي") == "من يسوي")
    # lowercase english
    record("Excel≈excel (lowercase)", normalize_text("Excel") == "excel")
    # both forms of intent accept — عبر v4 pipeline (mock AI مُبرمَج بالتطبيع)
    cl_b = make_scripted_v4_classifier([
        "أبي أحد يسوي لي بحث", "ابي احد يسوي لي بحث",
    ])

    async def run_b():
        r1 = (await analyze_request_v4("أبي أحد يسوي لي بحث", cl_b)).is_request
        r2 = (await analyze_request_v4("ابي احد يسوي لي بحث", cl_b)).is_request
        return r1 and r2

    record("both أبي/ابي forms ACCEPT (v4)", await run_b())
    return all(r['passed'] for r in RESULTS[-7:])


# =========================================================================
# C. RateLimiter — global + per-chat + sliding window
# =========================================================================
def test_section_C():
    print("\n=== C. RateLimiter ===")
    # per-chat limit 2, global 5
    rl = RateLimiter(max_per_minute=5, max_per_chat_per_minute=2, window_s=60)
    t0 = 1000.0
    # chat A: 2 allowed, 3rd blocked (per-chat)
    a1 = rl.try_acquire(1001, now=t0)
    a2 = rl.try_acquire(1001, now=t0 + 1)
    a3 = rl.try_acquire(1001, now=t0 + 2)  # blocked per-chat
    record("per-chat: 1st+2nd allowed", a1 and a2)
    record("per-chat: 3rd blocked", not a3)
    # chat B: different chat, allowed (independent)
    b1 = rl.try_acquire(2002, now=t0 + 3)
    record("different chat allowed (independent)", b1)
    # sliding window: after 60s, chat A can send again
    a4 = rl.try_acquire(1001, now=t0 + 61)
    record("sliding window: allowed after 60s", a4)
    # global limit: fill up global to 5, 6th blocked
    rl2 = RateLimiter(max_per_minute=3, max_per_chat_per_minute=10, window_s=60)
    g = [rl2.try_acquire(c, now=2000.0) for c in [10, 20, 30]]
    g4 = rl2.try_acquire(40, now=2000.5)  # global=3 reached
    record("global limit blocks 4th distinct chat", all(g) and not g4)
    # stats
    s = rl.stats(now=t0 + 5)
    record("stats returns dict", isinstance(s, dict) and 'global_count' in s)
    return all(r['passed'] for r in RESULTS[-7:])


# =========================================================================
# D. CircuitBreaker — trip + cooldown + auto-recover
# =========================================================================
def test_section_D():
    print("\n=== D. CircuitBreaker ===")
    cb = CircuitBreaker(threshold=3, window_s=100, cooldown_s=50)
    t0 = 5000.0
    # not tripped initially
    record("not tripped initially", not cb.is_tripped(now=t0))
    # 3 accepts → trips on 3rd
    cb.record_accept(now=t0)
    cb.record_accept(now=t0 + 1)
    tripped = cb.record_accept(now=t0 + 2)  # 3rd → trip
    record("trips on threshold (3rd accept)", tripped)
    record("is_tripped after trip", cb.is_tripped(now=t0 + 3))
    # during cooldown, record_accept returns False (no new trip)
    record("record_accept during cooldown = False",
           not cb.record_accept(now=t0 + 4))
    # after cooldown → auto-recover
    record("auto-recover after cooldown", not cb.is_tripped(now=t0 + 60))
    # stats
    s = cb.stats()
    record("stats returns dict", isinstance(s, dict) and 'tripped_count' in s)
    # reset
    cb2 = CircuitBreaker(threshold=2, window_s=100, cooldown_s=50)
    cb2.record_accept(now=6000.0)
    cb2.record_accept(now=6000.5)
    cb2.reset()
    record("reset clears trip", not cb2.is_tripped(now=6001.0))
    return all(r['passed'] for r in RESULTS[-7:])


# =========================================================================
# E. ContentDeduper — dup + TTL + bounded + independence
# =========================================================================
def test_section_E():
    print("\n=== E. ContentDeduper ===")
    cd = ContentDeduper(ttl_s=60, max_entries=10)
    t0 = 7000.0
    # first → not dup
    d1 = cd.is_duplicate("من يسوي لي بحث", now=t0)
    # second same → dup
    d2 = cd.is_duplicate("من يسوي لي بحث", now=t0 + 1)
    record("first occurrence not dup", not d1)
    record("second occurrence dup", d2)
    # different text → not dup
    d3 = cd.is_duplicate("أبي أحد يحل واجب", now=t0 + 2)
    record("different text not dup", not d3)
    # normalization: أبي ≈ ابي → dup
    d4 = cd.is_duplicate("ابي احد يحل واجب", now=t0 + 3)
    record("normalized variant = dup (أ≈ا)", d4)
    # after TTL → not dup anymore
    d5 = cd.is_duplicate("من يسوي لي بحث", now=t0 + 70)
    record("after TTL not dup", not d5)
    # bounded: overflow doesn't crash
    cd2 = ContentDeduper(ttl_s=60, max_entries=3)
    for i in range(20):
        cd2.is_duplicate(f"unique text {i}", now=t0 + i)
    record("bounded: overflow handled (no crash)", True)
    # independence: dedup has no link-path state
    record("dedup has no link_queue attr",
           not hasattr(cd, 'link_queue'))
    return all(r['passed'] for r in RESULTS[-7:])


# =========================================================================
# G. mbot.py ported heuristics — dotted-word obfuscation + multi-line ad
# =========================================================================
async def test_section_G():
    print("\n=== G. mbot.py ported heuristics ===")
    # ---- _has_dotted_word helper ----
    record("dotted: ت.قرير detected", _has_dotted_word("ت.قرير"))
    record("dotted: و.اجب detected", _has_dotted_word("و.اجب"))
    record("dotted: تـ.قرير (kashilda+dot) detected",
           _has_dotted_word("تـ.قرير"))
    record("dotted: تـ.ـقرير (both sides) detected",
           _has_dotted_word("تـ.ـقرير"))
    record("dotted: تقرير (no dots) NOT detected",
           not _has_dotted_word("تقرير"))
    record("dotted: empty string NOT detected",
           not _has_dotted_word(""))
    record("dotted: english only NOT detected",
           not _has_dotted_word("hello.world"))
    record("dotted: full normal Arabic sentence NOT detected",
           not _has_dotted_word("من يسوي لي بحث جامعي"))

    # ---- _has_many_lines helper ----
    six = "خط1\nخط2\nخط3\nخط4\nخط5\nخط6"
    five = "خط1\nخط2\nخط3\nخط4\nخط5"
    record("multi-line: 6 lines detected", _has_many_lines(six))
    record("multi-line: 5 lines NOT detected", not _has_many_lines(five))
    record("multi-line: empty NOT detected", not _has_many_lines(""))
    record("multi-line: custom threshold 3",
           _has_many_lines("a\nb\nc", threshold=3))
    record("multi-line: None-safe (no raise)",
           not _has_many_lines(None))

    # ---- v4.0: الإشارات التشخيصية (لا قرار من الكلمات) ----
    # Case 1: ad with dotted words + provider indicators → provider SIGNALS
    # تُستخرج (hints للـAI + تشخيص) — القرار نفسه من الـAI فقط.
    r1 = analyze_request("مكتبنا يقدم خدمات طلابية ت.قرير و.اجب باسعار مناسبة")
    record("dotted ad: v4 no keyword decision (is_request=False)",
           not r1.is_request)
    record("dotted ad: has_dotted_word=True (signal)",
           r1.has_dotted_word is True)
    record("dotted ad: provider_confidence >= 6 (signal)",
           r1.provider_confidence >= 6)

    # Case 2: same ad WITHOUT dotted words → provider signals still detected
    r2 = analyze_request("مكتبنا يقدم خدمات طلابية تقرير وواجب باسعار مناسبة")
    record("ad no-dots: v4 no keyword decision (is_request=False)",
           not r2.is_request)
    record("ad no-dots: has_dotted_word=False",
           r2.has_dotted_word is False)
    # dotted version should have HIGHER provider_confidence than non-dotted
    record("dotted boosts provider conf",
           r1.provider_confidence > r2.provider_confidence)

    # ---- analyze_request: multi-line boosts provider confidence ----
    # Multi-line ad with provider words
    multi_ad = (
        "مكتب خدمات طلابية متخصص\n"
        "نقدم بحوث ومشاريع تخرج\n"
        "اسعارنا مناسبة جدا\n"
        "تواصل واتساب للتواصل\n"
        "خدمات اكاديمية شاملة\n"
        "احجز الآن قبل نفاد المقاعد"
    )
    r3 = analyze_request(multi_ad)
    record("multi-line ad: v4 no keyword decision (is_request=False)",
           not r3.is_request)
    record("multi-line ad: has_many_lines=True (signal)",
           r3.has_many_lines is True)
    record("multi-line ad: provider_confidence >= 6",
           r3.provider_confidence >= 6)

    # ---- NO false positive on multi-line genuine request ----
    # A long, structured genuine request should still be ACCEPTED
    # (multi-line is a weak signal +1, not a verdict)
    genuine_multi = (
        "السلام عليكم\n"
        "محتاج مساعدة من اخواني\n"
        "ابي احد يسوي لي بحث\n"
        "البحث جامعي تخرج\n"
        "عندي مشروع تخرج قريب\n"
        "تكفون تساعدوني"
    )
    r4 = analyze_request(genuine_multi)
    # v4: القرار عبر الـAI — نمرّر النص نفسه عبر pipeline بمصنّف مُبرمَج
    # (قرار الـLLM الصحيح: طلب حقيقي) ونتأكد أن الإشارات متعددة الأسطر
    # (weak hint) لا تمنع القبول.
    cl_g = make_scripted_v4_classifier([genuine_multi])

    record("multi-line genuine: still ACCEPT (v4 pipeline)",
           (await analyze_request_v4(genuine_multi, cl_g)).is_request,
           f"signals: provider={r4.provider_confidence}")
    record("multi-line genuine: has_many_lines=True (signal)",
           r4.has_many_lines is True)
    record("multi-line genuine: provider_confidence < 6 (signal)",
           r4.provider_confidence < 6)

    # ---- to_dict exposes new fields ----
    r5 = analyze_request("ت.قرير و.اجب")
    d = r5.to_dict()
    record("to_dict: has_dotted_word in dict",
           d.get("has_dotted_word") is True)
    record("to_dict: advertisement_matches includes dotted_word tag",
           "(dotted_word_obfuscation)" in d.get("advertisement_matches", []))

    r6 = analyze_request("a\nb\nc\nd\ne\nf")
    d6 = r6.to_dict()
    record("to_dict: has_many_lines in dict",
           d6.get("has_many_lines") is True)
    record("to_dict: advertisement_matches includes multi_line tag",
           "(multi_line_six_plus)" in d6.get("advertisement_matches", []))

    return all(r['passed'] for r in RESULTS[-25:])


# =========================================================================
# F. _handle_request_path integration
# =========================================================================
class FakeSender:
    def __init__(self, is_bot=False, first_name="Ahmad", username="ahmad"):
        self.bot = is_bot
        self.first_name = first_name
        self.username = username


class FakeDate:
    def __init__(self, s):
        import datetime
        self._d = datetime.datetime(2025, 1, 1, 12, 0)
    def strftime(self, fmt):
        return self._d.strftime(fmt)


class FakeMessage:
    def __init__(self, msg_id, sender=None, date=None):
        self.id = msg_id
        self.sender = sender
        self.date = date or FakeDate(msg_id)


class FakeChat:
    def __init__(self, title="TestGroup", username="testgroup"):
        self.title = title
        self.username = username


class FakeEvent:
    def __init__(self, raw_text, chat_id, msg_id, sender=None, chat=None, date=None):
        self.raw_text = raw_text
        self.chat_id = chat_id
        self.id = msg_id
        self.sender = sender
        self.chat = chat
        self.sender_id = getattr(sender, 'id', 42) if sender else 0
        self.message = FakeMessage(msg_id, sender, date)


class SendMock:
    def __init__(self):
        self.calls = []
    async def __call__(self, *a, **kw):
        self.calls.append({'target': a[0] if a else kw.get('entity'),
                           'alert': a[1] if len(a) > 1 else kw.get('message', ''),
                           'kwargs': kw})
    @property
    def call_count(self):
        return len(self.calls)
    @property
    def targets(self):
        return [c['target'] for c in self.calls]
    def reset(self):
        self.calls = []


def make_fm(prod_db=None, channel_id=-1001234567890,
            requests_target_channel='@dhkskwksjskwk',
            request_filter_enabled=True,
            max_per_minute=20, max_per_chat=5,
            cb_threshold=100, cb_window=600, cb_cooldown=600):
    cfg = types.SimpleNamespace(
        journal_enabled=False, delete_miss_reconcile=False,
        journal_retention_s=86400, journal_no_text_retention_s=21600,
        channel_id=channel_id, journal_recovery_enabled=False,
        requests_target_channel=requests_target_channel,
        request_filter_enabled=request_filter_enabled,
        request_filter_max_per_minute=max_per_minute,
        request_filter_max_per_chat_per_minute=max_per_chat,
        request_filter_cb_threshold=cb_threshold,
        request_filter_cb_window_s=cb_window,
        request_filter_cb_cooldown_s=cb_cooldown,
    )
    send_mock = SendMock()
    bot_client = MagicMock()
    bot_client.is_connected = MagicMock(return_value=True)
    bot_client.send_message = send_mock

    # [v4.0] مصنّف AI مُحاكى: نصوص هذه السيناريوهات المعروفة تقرر كما يجب
    # أن يقرر LLM صحيح (طلبات واجب/بحث → ACCEPT؛ عروض/عام → REJECT).
    _REQUEST_PAT = ("يسوي لي", "يحل لي واجب", "أبي أحد", "يساعدني في", "يعرف أحد")

    async def _v4_transport(provider, payload):
        user_msg = payload["messages"][1]["content"]
        inner = user_msg.split('"""')[-2] if '"""' in user_msg else user_msg
        if any(p in inner for p in _REQUEST_PAT):
            content = _ai_json("ACCEPT", 0.93, "homework_execution_request", "طلب مساعدة")
        else:
            content = _ai_json("REJECT", 0.95, "other", "ليس طلبًا")
        return 200, _json.dumps({"choices": [{"message": {"content": content}}]})

    request_classifier = IntentClassifier(
        providers=[{"key": "k", "url": "u", "model": "mock-v4", "name": "Mock"}],
        transport=_v4_transport)

    fm = types.SimpleNamespace(
        config=cfg, prod_db=prod_db, message_claim=None,
        request_classifier=request_classifier,
        _msg_cache={}, _msg_cache_lock=asyncio.Lock(),
        metrics=types.SimpleNamespace(
            record_skip=AsyncMock(), record_duplicate=AsyncMock(),
            record_link_capture=AsyncMock(), record_link_ring_hit=AsyncMock(),
            record_delete_miss=AsyncMock(), record_delete_rescued=AsyncMock(),
            record_reconcile_rescued=AsyncMock(), record_link_forwarded=AsyncMock(),
        ),
        _link_ring={}, _link_ring_lock=asyncio.Lock(),
        _link_ring_ts={}, _link_ring_ttl=300, _link_ring_cap=20000,
        _link_ring_evicted=0, _link_ring_hits=0,
        user_clients={}, source_registry=None,
        _delete_miss_log_ts={}, _delete_miss_count={}, _no_text_count=0,
        _reconcile_inflight=set(), _chat_poll_failures={},
        _polling_state={}, _polling_lock=asyncio.Lock(), _active_polling_chats=[],
        bot_client=bot_client, floodwait_mgr=None,
    )
    for m in ('_journal_enabled', '_journal_write', '_journal_set_state_safe',
              '_journal_mark_deleted_safe', '_record_delete_miss',
              '_rescue_enqueue_links', '_spawn_reconcile',
              '_reconcile_chat_after_delete_miss', '_journal_recovery',
              '_link_ring_put', '_link_ring_pop', '_link_ring_evict',
              '_normalized_to_link_data', '_rescue_link_only',
              '_on_user_message', '_on_message_deleted', '_handle_request_path',
              '_poll_one_chat'):
        setattr(fm, m, types.MethodType(getattr(bot.Monitor, m), fm))
    # staticmethods — plain function attrs (no MethodType binding)
    fm._sender_is_bot = bot.Monitor._sender_is_bot
    fm._get_sender_name = bot.Monitor._get_sender_name
    fm._send_mock = send_mock
    return fm


async def test_section_F():
    print("\n=== F. _handle_request_path integration ===")
    results = []

    # F1. Kill switch OFF → no send (link path unaffected is structural)
    fm = make_fm(request_filter_enabled=False)
    ev = FakeEvent("من يسوي لي بحث؟", -100555000111, 9001,
                   sender=FakeSender(), chat=FakeChat())
    await fm._handle_request_path(ev, ev.raw_text, ev.chat_id, ev.id, '966500000001')
    results.append(("kill switch OFF → no send", fm._send_mock.call_count == 0))

    # F2. Kill switch ON → real request sent to @dhkskwksjskwk
    fm = make_fm(request_filter_enabled=True)
    ev = FakeEvent("من يسوي لي بحث؟", -100555000111, 9002,
                   sender=FakeSender(first_name="Salem"), chat=FakeChat())
    await fm._handle_request_path(ev, ev.raw_text, ev.chat_id, ev.id, '966500000002')
    sent = (fm._send_mock.call_count == 1
            and fm._send_mock.targets[0] == '@dhkskwksjskwk')
    results.append(("kill switch ON → send to @dhkskwksjskwk", sent))

    # F3. Non-request → no send
    fm = make_fm()
    ev = FakeEvent("عندي مشروع", -100555000111, 9003,
                   sender=FakeSender(), chat=FakeChat())
    await fm._handle_request_path(ev, ev.raw_text, ev.chat_id, ev.id, '9665')
    results.append(("«عندي مشروع» → no send", fm._send_mock.call_count == 0))

    # F4. Provider → no send
    fm = make_fm()
    ev = FakeEvent("نوفر حل واجبات بأسعار ممتازة", -100555000111, 9004,
                   sender=FakeSender(), chat=FakeChat())
    await fm._handle_request_path(ev, ev.raw_text, ev.chat_id, ev.id, '9665')
    results.append(("provider → no send", fm._send_mock.call_count == 0))

    # F5. Channel separation: target = @dhkskwksjskwk (str) ≠ channel_id (int)
    fm = make_fm()
    ev = FakeEvent("أبي أحد يحل لي واجب", -100555000111, 9005,
                   sender=FakeSender(), chat=FakeChat())
    await fm._handle_request_path(ev, ev.raw_text, ev.chat_id, ev.id, '9665')
    if fm._send_mock.call_count == 1:
        tgt = fm._send_mock.targets[0]
        sep = (tgt == '@dhkskwksjskwk' and tgt != fm.config.channel_id)
    else:
        sep = False
    results.append(("channel separation (target=str @dhkskwksjskwk ≠ channel_id)",
                    sep))

    # F6. Capture-first: alert contains the raw_text (snapshot, no re-fetch)
    fm = make_fm()
    raw = "من يسوي لي بحث تخرج؟"
    ev = FakeEvent(raw, -100555000111, 9006,
                   sender=FakeSender(first_name="Noor"), chat=FakeChat())
    await fm._handle_request_path(ev, raw, ev.chat_id, ev.id, '9665')
    has_text = (fm._send_mock.call_count == 1
                and raw in fm._send_mock.calls[0]['alert'])
    results.append(("capture-first: raw_text in alert (snapshot)", has_text))

    # F7. Rate limit (per-chat): max_per_chat=2 → 3rd blocked
    fm = make_fm(max_per_chat=2, max_per_minute=100)
    chat = -100555000222
    # [v4] نصوص مختلفة دلاليًا — حتى يمنع الـrate limiter (وليس semantic dedup)
    f7_texts = ["من يسوي لي بحث تخرج؟", "أبي أحد يحل لي واجب الرياضيات",
                "من يجهز لي عرض تقديمي؟"]
    for i, txt in enumerate(f7_texts):
        ev = FakeEvent(txt, chat, 9100 + i,
                       sender=FakeSender(first_name=f"U{i}"), chat=FakeChat())
        await fm._handle_request_path(ev, ev.raw_text, chat, ev.id, '9665')
    results.append(("per-chat rate limit: 3rd blocked (2 sent)",
                    fm._send_mock.call_count == 2))

    # F8. Circuit breaker: threshold=2 → 3rd blocked, link path unaffected
    fm = make_fm(cb_threshold=2, cb_cooldown=600, max_per_minute=100, max_per_chat=100)
    # [v4] نصوص مختلفة دلاليًا — حتى يفصل الـcircuit breaker (وليس semantic dedup)
    f8_texts = ["أبي أحد يحل واجب الفيزياء", "أبي أحد ينجز مشروع التخرج",
                "أبي أحد يشرح لي الإحصاء"]
    for i, txt in enumerate(f8_texts):
        ev = FakeEvent(txt, -100555000333, 9200 + i,
                       sender=FakeSender(first_name=f"U{i}"), chat=FakeChat())
        await fm._handle_request_path(ev, ev.raw_text, ev.chat_id, ev.id, '9665')
    results.append(("circuit breaker: 3rd blocked (2 sent)", fm._send_mock.call_count == 2))
    # breaker stats tripped
    results.append(("circuit breaker tripped flag",
                    fm._request_circuit_breaker.stats()['tripped']))

    # F9. Content dedup: same text from 2 different chats → 1 sent
    fm = make_fm()
    raw = "من يسوي لي بحث تخرج"
    ev1 = FakeEvent(raw, -100555000444, 9300,
                    sender=FakeSender(first_name="A"), chat=FakeChat())
    ev2 = FakeEvent(raw, -100555000555, 9301,
                    sender=FakeSender(first_name="B"), chat=FakeChat())
    await fm._handle_request_path(ev1, raw, ev1.chat_id, ev1.id, '9665')
    await fm._handle_request_path(ev2, raw, ev2.chat_id, ev2.id, '9665')
    results.append(("content dedup: same text 2 chats → 1 sent",
                    fm._send_mock.call_count == 1))

    # F10. (chat_id,msg_id) cross-account dedup: same msg → 1 sent
    fm = make_fm()
    raw = "من يسوي لي بحث"
    chat = -100555000666
    ev1 = FakeEvent(raw, chat, 9400, sender=FakeSender(first_name="A"), chat=FakeChat())
    ev2 = FakeEvent(raw, chat, 9400, sender=FakeSender(first_name="B"), chat=FakeChat())
    await fm._handle_request_path(ev1, raw, chat, 9400, '9665')
    await fm._handle_request_path(ev2, raw, chat, 9400, '9665')
    results.append(("(chat_id,msg_id) dedup → 1 sent",
                    fm._send_mock.call_count == 1))

    # F11. Independence: _handle_request_path failure doesn't raise to caller
    # (simulate by passing None event — should be caught internally)
    fm = make_fm()
    raised = False
    try:
        await fm._handle_request_path(None, "", 0, 0, '9665')
    except Exception:
        raised = True
    results.append(("request path robustness (None event no raise)", not raised))

    # F12. Link path unaffected: _on_user_message still extracts links even
    # when request filter rejects. We verify via _on_user_message writing LRB.
    fm = make_fm(request_filter_enabled=False)  # filter disabled
    raw = "عندي مشروع https://t.me/SomeGroup"  # not a request BUT has link
    ev = FakeEvent(raw, -100555000777, 9500,
                   sender=FakeSender(first_name="Z"), chat=FakeChat())
    await fm._on_user_message(ev, '9665')
    link_captured = ((ev.chat_id, ev.id) in fm._link_ring)
    no_req_send = (fm._send_mock.call_count == 0)
    results.append(("link path captures link even when request filter disabled",
                    link_captured and no_req_send))

    # F13. Static: bot_client.send_message NOT called for any link-channel target
    # (request alerts never go to channel_id)
    fm = make_fm()
    ev = FakeEvent("من يسوي لي بحث؟", -100555000888, 9600,
                   sender=FakeSender(), chat=FakeChat())
    await fm._handle_request_path(ev, ev.raw_text, ev.chat_id, ev.id, '9665')
    no_link_channel = (fm.config.channel_id not in fm._send_mock.targets)
    results.append(("request never sent to channel_id (link channel)", no_link_channel))

    # F14. Seeker with phone → sent (not rejected by phone)
    fm = make_fm()
    ev = FakeEvent("أبي أحد يحل لي واجب، رقمي 0551234567", -100555000999, 9700,
                   sender=FakeSender(first_name="K"), chat=FakeChat())
    await fm._handle_request_path(ev, ev.raw_text, ev.chat_id, ev.id, '9665')
    results.append(("seeker with phone → sent", fm._send_mock.call_count == 1))

    for name, passed in results:
        record(name, passed)
    return all(p for _, p in results)


async def main():
    print("=" * 72)
    print(f"REQUEST FILTER {FILTER_VERSION} — Comprehensive Test Suite")
    print("=" * 72)
    a = await test_section_A()
    b = await test_section_B()
    c = test_section_C()
    d = test_section_D()
    e = test_section_E()
    g = await test_section_G()
    f = await test_section_F()
    print("\n" + "=" * 72)
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r['passed'])
    print(f"REQUEST-FILTER-v2 RESULTS: {passed}/{total} assertions passed")
    print(f"Sections: A={a} B={b} C={c} D={d} E={e} G={g} F={f}")
    print("=" * 72)
    return 0 if (a and b and c and d and e and g and f and passed == total) else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
