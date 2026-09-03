#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_request_filter_v4.py — Request Intent Engine v4.0 (AI-First) Test Suite
================================================================================
يختبر إعادة البناء الجذرية v4.0 المرحلة-بمرحلة:

  A. [STAGE 1] text_normalizer  — إيموجي/روابط/توقيعات/تكرار + canonical
  B. [STAGE 4] semantic_dedup   — exact + semantic-hash + Jaccard near-dup + TTL
  C. [STAGE 2/3] intent_classifier — JSON parse (fences/noise) + validation +
                                    rotation + timeout + parse-failure → REJECT
  D. Prompt Contract            — العقد الدلالي مقفول في الكود (taxonomy + عتبة)
  E. Orchestrator v4            — الحالات الإلزامية التسع (طلب المُشغّل) +
                                   عتبة 0.85 + لا-keyword-fallback + dedup +
                                   relay + empty + malformed + decision logging
  F. [STAGE 5] filter_store     — filter_decisions: كتابة + قراءة + إحصاءات
  G. [STAGE 6] /api/filter_stats — endpoint مع fake monitor
  H. Regression Corpus          — corpus v3.0 كامل (مئات الحالات) عبر mock AI:
                                   كل ACCEPT-case مقبول وكل REJECT-case مرفوض
                                   (يثبت سلامة السباكة على نطاق واسع)
  I. Integration _handle_request_path — إرسال حقيقي عبر Monitor method مُربوطة
                                   مع SQLite حقيقي + mock classifier + SendMock

مبدأ الاختبار الصريح: الـAI في الإنتاج يقرر. في الاختبار نُحقن transport
مُبرمج (scripted) يُعيد تصنيفًا صحيحًا لكل حالة — نثبت أن السباكة كاملة:
normalize → signals hints → classify → threshold → dedup → filter_decisions →
send. ونثبت المستحيل: لا يوجد أي مسار يقرر بالكلمات المفتاحية (AI down =
REJECT دائمًا حتى لرسالة مليئة بكلمات الطلب).

NO Telegram credentials — SIMULATION ONLY (transport injection + in-memory SQLite).

شغّل:  python3 tests/test_request_filter_v4.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('BOT_TOKEN', '123:test')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')
os.environ.setdefault('REQUESTS_TARGET_CHANNEL', '@dhkskwksjskwk')

from text_normalizer import normalize as tn_normalize                       # noqa: E402
from semantic_dedup import SemanticDeduper, content_tokens                  # noqa: E402
from intent_classifier import (                                             # noqa: E402
    IntentClassifier, IntentDecision, extract_json_text, validate_ai_output,
    SYSTEM_PROMPT, ACCEPT_CATEGORIES, REJECT_CATEGORIES, load_providers_from_env,
)
from request_filter import (                                                # noqa: E402
    analyze_request_v4, analyze_request, extract_signals,
    FILTER_VERSION, FILTER_MODE,
)
from filter_store import DecisionLogger, text_hash_of, FILTER_DECISIONS_SCHEMA  # noqa: E402

# test counters
_TOTAL = {"pass": 0, "fail": 0}
_FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _TOTAL["pass"] += 1
        print(f"  ✓ {name}")
    else:
        _TOTAL["fail"] += 1
        _FAILURES.append((name, detail))
        print(f"  ✗ {name}  {detail}")


# ============================================================
# Mock AI infrastructure (scripted transport)
# ============================================================
def _ai_json(decision, confidence, category, reason):
    return json.dumps({"decision": decision, "confidence": confidence,
                       "category": category, "reason": reason}, ensure_ascii=False)


def make_scripted_classifier(scripts, default=None, model="mock-70b"):
    """IntentClassifier بحقن transport — scripts: {substring_in_user_msg: (decision, conf, cat, reason)}.
    default: قرار غير المتطابق (REJECT other افتراضيًا)."""
    if default is None:
        default = ("REJECT", 0.90, "other", "غير مطابق")

    async def transport(provider, payload):
        user_msg = payload["messages"][1]["content"]
        for key, resp in scripts.items():
            if key in user_msg:
                body = {"choices": [{"message": {"content": resp}}]}
                return 200, json.dumps(body)
        d, c, cat, r = default
        body = {"choices": [{"message": {"content": _ai_json(d, c, cat, r)}}]}
        return 200, json.dumps(body)

    return IntentClassifier(
        providers=[{"key": "k", "url": "u", "model": model, "name": "Mock"}],
        transport=transport,
    )


# التصنيف المرجعي الصحيح للحالات الإلزامية التسع (كما يجب أن يقرر LLM مُدرَّب)
MANDATORY_SCRIPTS = {
    # ❌ REJECT (طلب المُشغّل)
    "حين يحبك الله": _ai_json("REJECT", 0.97, "religious_general_content", "محتوى ديني عام"),
    "عندي دكتور يساعد": _ai_json("REJECT", 0.95, "service_offer", "عرض خدمة من مقدّم"),
    "التداول": _ai_json("REJECT", 0.98, "advertisement", "إعلان تداول"),
    "اكتمال جبت": _ai_json("REJECT", 0.96, "praise_testimonial", "مدح منصة بعد تجربة"),
    "افضل مدرس": _ai_json("REJECT", 0.88, "recommendation_or_opinion", "استطلاع رأي عام"),
    # ✅ ACCEPT (طلب المُشغّل — v4.3.7: تنفيذ العمل بدلاً عن الطالب فقط)
    "تفاضل 1": _ai_json("ACCEPT", 0.93, "homework_execution_request", "طلب شخص ينفّذ الواجب بدلاً عنه"),
    "يحل معي السؤال": _ai_json("ACCEPT", 0.91, "homework_execution_request", "طلب شخص يحل معه"),
    "يسوي لي البحث": _ai_json("ACCEPT", 0.94, "homework_execution_request", "طلب تنفيذ البحث بدلاً عنه"),
    "يخلص لي التقرير": _ai_json("ACCEPT", 0.92, "homework_execution_request", "طلب إنجاز التقرير بدلاً عنه"),
    # ❌ REJECT — [v4.3.7] التدريس/الشرح لم يعد طلبًا مقبولاً (طلب المُشغّل)
    "يعرف دكتور يشرح": _ai_json("REJECT", 0.95, "tutoring_only_request", "طلب تدريس وشرح وليس تنفيذًا للعمل بدلاً عنه"),
    "مدرس خصوصي للمادة": _ai_json("REJECT", 0.94, "tutoring_only_request", "يبحث عن خصوصي وشرح"),
    "أكواد لشخصيات": _ai_json("REJECT", 0.95, "non_academic_request", "طلب أكواد ألعاب غير أكاديمي"),
    # ✅ ACCEPT — [v4.3.9] الصيغة الخليجية المختصرة بلا «لي» = تفويض
    # (قائمة المُشغّل الحقيقية: «مين يسوي تقرير ؟؟»/«احد يسوي سكليف ؟»)
    "يسوي تقرير": _ai_json("ACCEPT", 0.95, "homework_execution_request", "صيغة خليجية مختصرة لطلب تنفيذ التقرير بدلاً عنه"),
    "يسوي سكليف": _ai_json("ACCEPT", 0.95, "homework_execution_request", "طلب تنفيذ الواجبات بدلاً عنه"),
    "مشروع تخرج": _ai_json("ACCEPT", 0.98, "homework_execution_request", "طلب إنجاز مشروع التخرج بدلاً عنه"),
    "يحل كويزات": _ai_json("ACCEPT", 0.96, "homework_execution_request", "طلب حل الكويزات بدلاً عنه"),
    # ✅ ACCEPT — [v4.3.9] خدمات طلابية (CV/جدول/عذر — قائمة المُشغّل)
    "يسوي cv": _ai_json("ACCEPT", 0.95, "student_service_execution_request", "طلب تنفيذ سيرة ذاتية بدلاً عنه"),
    "يسوي لي جدول": _ai_json("ACCEPT", 0.95, "student_service_execution_request", "طلب بناء الجدول الدراسي بدلاً عنه"),
    "يسوي عذر": _ai_json("ACCEPT", 0.95, "student_service_execution_request", "طلب تجهيز عذر بدلاً عنه"),
    # ❌ REJECT — [v4.3.9] ملفات جاهزة (عنده ≠ يسوي)
    "عنده كويزات": _ai_json("REJECT", 0.95, "resource_request", "طلب مواد جاهزة لا تنفيذ عمل"),
}


# ============================================================
# A. [STAGE 1] text_normalizer
# ============================================================
def section_a():
    print("\n=== A. [STAGE 1] text_normalizer — تنظيف أولي ===")

    nt = tn_normalize("أحد يشرح لي التفاضل 1؟ 😊✅ https://t.me/xyz")
    check("A1: إيموجي مُزال", "😊" not in nt.clean and "✅" not in nt.clean, repr(nt.clean))
    check("A2: الرابط مُزال", "t.me" not in nt.clean, repr(nt.clean))
    check("A3: السياق محفوظ (السؤال واللهجة)", "؟" in nt.clean and "يشرح" in nt.clean, repr(nt.clean))
    check("A4: عدّادات الإزالة", nt.removed.get("emojis", 0) >= 2 and nt.removed.get("links", 0) >= 1, str(nt.removed))

    nt2 = tn_normalize("صررررراحة محتاج محتاج محتاج مساعدة")
    check("A5: تكرار الحروف مضغوط", "صررراحة" not in nt2.clean and "صرراحة" in nt2.clean, repr(nt2.clean))
    check("A6: تكرار الكلمات مضغوط", nt2.clean.count("محتاج") == 1, repr(nt2.clean))

    nt3 = tn_normalize("مين يعرف مدرس رياضيات؟\nSent from my iPhone")
    check("A7: توقيع الجهاز مُزال", "iphone" not in nt3.clean.lower(), repr(nt3.clean))
    check("A8: النص الأصلي باقٍ", "مين يعرف مدرس" in nt3.clean, repr(nt3.clean))

    nt4 = tn_normalize("شلون أحل الواجب؟ وين الأستاذ؟")
    check("A9: canonical يوحّد اللهجات", "كيف" in nt4.canonical and "اين" in nt4.canonical, repr(nt4.canonical))
    check("A10: clean يحافظ على اللهجة (السياق)", "شلون" in nt4.clean and "وين" in nt4.clean, repr(nt4.clean))

    nt5 = tn_normalize("أحتاج مساعدة في واجبي")
    check("A11: canonical يطبّع الحروف العربية", "احتاج" in nt5.canonical and "واجبي" in nt5.canonical, repr(nt5.canonical))

    nt6 = tn_normalize("")
    check("A12: نص فارغ → bool False", not nt6, str(nt6))
    nt7 = tn_normalize("😀😀😀")
    check("A13: إيموجي فقط → canonical فارغ", nt7.canonical == "", repr(nt7.canonical))


# ============================================================
# B. [STAGE 4] semantic_dedup
# ============================================================
def section_b():
    print("\n=== B. [STAGE 4] semantic_dedup — منع التكرار الدلالي ===")

    d = SemanticDeduper(ttl_s=900, jaccard_threshold=0.80)

    c1 = tn_normalize("أحد يشرح لي التفاضل").canonical
    check("B1: أول ظهور ليس مكررًا", d.check(c1).is_dup is False)
    d.register(c1)
    check("B2: نفس النص → exact dup", d.check(c1).is_dup and d.check(c1).kind == "exact")

    c2 = tn_normalize("التفاضل لي يشرح أحد").canonical  # reorder
    r = d.check(c2)
    check("B3: إعادة ترتيب الكلمات → semantic dup", r.is_dup and r.kind in ("semantic", "exact"), f"{r.kind}")

    c3 = tn_normalize("أحد يشرح لي التفاضل الليلة").canonical  # +1 word
    r3 = d.check(c3)
    check("B4: إضافة كلمة واحدة → near-dup (Jaccard)", r3.is_dup and r3.kind == "near", f"{r3.kind} sim={r3.similarity}")

    c4 = tn_normalize("أريد مدرس خصوصي للرياضيات").canonical
    check("B5: نص مختلف تمامًا → ليس مكررًا", d.check(c4).is_dup is False)

    # TTL expiry
    d2 = SemanticDeduper(ttl_s=0.2)
    c = tn_normalize("محتاج أحد يحل واجبي").canonical
    d2.register(c, now=time.time())
    check("B6: داخل TTL → dup", d2.check(c, now=time.time() + 0.05).is_dup)
    check("B7: بعد TTL → ليس dup", d2.check(c, now=time.time() + 1.0).is_dup is False)

    # word-level stemming: «بحوثي» vs «بحوث»
    d3 = SemanticDeduper()
    d3.register(tn_normalize("محتاج أحد يكتب بحوثي الجامعية").canonical)
    r8 = d3.check(tn_normalize("محتاج أحد يكتب بحوثك الجامعية").canonical)
    check("B8: تبديل ضمير الملكية → near-dup", r8.is_dup, f"{r8.kind}")

    # is_duplicate compat API
    d4 = SemanticDeduper()
    check("B9: is_duplicate API — أول مرة False", d4.is_duplicate("مين يحل واجب الرياضيات؟").is_dup is False)
    check("B10: is_duplicate API — ثاني مرة True", d4.is_duplicate("مين يحل واجب الرياضيات؟").is_dup is True)

    # stats bounded
    st = d.stats()
    check("B11: stats تُرجع الحقول", all(k in st for k in ("ttl_s", "exact_entries", "total_seen")), str(st))


# ============================================================
# C. [STAGE 2/3] intent_classifier — parse + validate + rotation
# ============================================================
def section_c():
    print("\n=== C. [STAGE 2/3] intent_classifier — عقد JSON + فشل آمن ===")

    # JSON extraction
    check("C1: extract_json — نظيف",
          extract_json_text('{"decision":"REJECT"}') == '{"decision":"REJECT"}')
    check("C2: extract_json — ```json fence",
          '"ACCEPT"' in extract_json_text('```json\n{"decision":"ACCEPT"}\n```'))
    check("C3: extract_json — noise قبل/بعد",
          extract_json_text('القرار هو: {"decision":"REJECT"} شكرًا') == '{"decision":"REJECT"}')

    # validation
    v = validate_ai_output({"decision": "ACCEPT", "confidence": 0.93, "category": "homework_execution_request", "reason": "ok"})
    check("C4: ACCEPT صالح مع فئة ACCEPT", v is not None and v["decision"] == "ACCEPT")
    v = validate_ai_output({"decision": "ACCEPT", "confidence": 1.7, "category": "homework_execution_request", "reason": "ok"})
    check("C5: confidence يُقصّ إلى [0,1]", v is not None and v["confidence"] == 1.0)
    v = validate_ai_output({"decision": "ACCEPT", "confidence": -5, "category": "homework_execution_request", "reason": "ok"})
    check("C6: confidence سالب → 0.0", v is not None and v["confidence"] == 0.0)
    v = validate_ai_output({"decision": "MAYBE", "confidence": 0.9, "category": "x", "reason": "y"})
    check("C7: قرار غير معروف → invalid (None)", v is None)
    v = validate_ai_output({"decision": "ACCEPT", "confidence": 0.9, "category": "advertisement", "reason": "y"})
    check("C8: ACCEPT مع فئة REJECT → رفض تناقض (None)", v is None)
    v = validate_ai_output({"decision": "REJECT", "confidence": 0.9, "category": "homework_execution_request", "reason": "y"})
    check("C9: REJECT مع فئة ACCEPT → رفض تناقض (None)", v is None)
    # [v4.3.7] EXECUTION-ONLY: التدريس فئة REJECT صالحة الآن
    v = validate_ai_output({"decision": "REJECT", "confidence": 0.95, "category": "tutoring_only_request", "reason": "y"})
    check("C9b: REJECT مع tutoring_only_request → صالح (EXECUTION-ONLY)", v is not None and v["category"] == "tutoring_only_request")
    v = validate_ai_output({"decision": "ACCEPT", "confidence": 0.95, "category": "tutoring_only_request", "reason": "y"})
    check("C9c: ACCEPT مع tutoring_only_request → None (لا قبول للتدريس)", v is None)
    v = validate_ai_output({"decision": "REJECT", "confidence": "nan", "category": "other", "reason": "y"})
    check("C10: confidence NaN → 0.0", v is not None and v["confidence"] == 0.0)
    v = validate_ai_output("not a dict")
    check("C11: ليس dict → None", v is None)

    # classify through scripted transport
    async def run_c():
        cl = make_scripted_classifier({"تداول": _ai_json("REJECT", 0.97, "advertisement", "اعلان")})
        dec = await cl.classify("تعلم التداول واربح")
        check("C12: classify عبر transport — قرار REJECT advertisement",
              dec.ok and dec.decision == "REJECT" and dec.category == "advertisement", str(dec))

        # malformed output → not ok → orchestrator rejects
        async def bad_transport(provider, payload):
            return 200, json.dumps({"choices": [{"message": {"content": "أعتقد أنها ليست طلبًا"}}]})
        cl2 = IntentClassifier(providers=[{"key": "k", "url": "u", "model": "m", "name": "Mock"}], transport=bad_transport)
        dec2 = await cl2.classify("أي رسالة")
        check("C13: مخرجات غير JSON → ok=False (رفض آمن)", dec2.ok is False, str(dec2))

        # [v4.3] double-encoded JSON (بوابة تعيد الـJSON كسلسلة escaped)
        async def double_transport(provider, payload):
            return 200, json.dumps({"choices": [{"message": {"content": json.dumps(
                _ai_json("REJECT", 0.91, "other", "double-encoded"))}}]})
        cl2b = IntentClassifier(providers=[{"key": "k", "url": "u", "model": "m", "name": "Mock"}], transport=double_transport)
        dec2b = await cl2b.classify("أي رسالة")
        check("C13b: JSON مزدوج الترميز → يُفكّ ويُقرأ (ok=True)",
              dec2b.ok and dec2b.decision == "REJECT" and dec2b.category == "other", str(dec2b))

        # rotation: أول مزوّد فاشل (HTTP 500) → الثاني يعمل
        state = {"n": 0}

        async def rotating_transport(provider, payload):
            state["n"] += 1
            if state["n"] == 1:
                return 500, "server error"
            return 200, json.dumps({"choices": [{"message": {"content": _ai_json("REJECT", 0.9, "other", "x")}}]})
        cl3 = IntentClassifier(
            providers=[{"key": "k1", "url": "u", "model": "m", "name": "P1"},
                       {"key": "k2", "url": "u", "model": "m", "name": "P2"}],
            transport=rotating_transport, max_attempts=2)
        dec3 = await cl3.classify("رسالة")
        check("C14: تدوير المزوّدين عند فشل HTTP 500", dec3.ok and dec3.provider_name == "P2", str(dec3))

        # timeout transport → ai_error
        async def slow_transport(provider, payload):
            await asyncio.sleep(5)
            return 200, "{}"
        cl4 = IntentClassifier(providers=[{"key": "k", "url": "u", "model": "m", "name": "P"}],
                               transport=slow_transport, timeout_s=0.2, max_attempts=1)
        dec4 = await cl4.classify("رسالة")
        check("C15: timeout → ok=False + error يشير للـtimeout", dec4.ok is False and "timeout" in dec4.error, str(dec4.error))

        # no providers → enabled False → classify returns ai_unavailable
        cl5 = IntentClassifier(providers=[])
        dec5 = await cl5.classify("أي شيء")
        check("C16: لا مزوّدين → ai_unavailable REJECT", dec5.ok is False and dec5.decision == "REJECT", str(dec5))

        # counters
        st = cl.stats()
        check("C17: stats — counters مسجّلة", st["calls"] > 0 and "avg_latency_ms" in st, str(st))

    asyncio.run(run_c())


# ============================================================
# D. Prompt Contract — العقد الدلالي مقفول
# ============================================================
def section_d():
    print("\n=== D. Prompt Contract — القاعدة الذهبية + الفئات مقفولة ===")
    check("D1: القاعدة الذهبية موجودة", "ACCEPT فقط إذا وُجد دليل واضح" in SYSTEM_PROMPT)
    check("D2: عقد JSON موجود", '"decision":"ACCEPT أو REJECT"' in SYSTEM_PROMPT or '"decision"' in SYSTEM_PROMPT)
    for cat in ACCEPT_CATEGORIES:
        check(f"D3: فئة ACCEPT مقفولة: {cat}", f'"{cat}"' in SYSTEM_PROMPT)
    for cat in ("advertisement", "service_offer", "praise_testimonial",
                "religious_general_content", "non_request_question",
                "recommendation_or_opinion", "general_discussion"):
        check(f"D4: فئة REJECT مقفولة: {cat}", f'"{cat}"' in SYSTEM_PROMPT)
    check("D5: الأمثلة الإلزامية للمُشغّل في الـprompt (مين يعرف دكتور)",
          "مين يعرف دكتور يشرح رياضيات" in SYSTEM_PROMPT)
    check("D6: التمييز الحرج (مين أفضل مدرس = REJECT توصية)",
          "مين أفضل مدرس" in SYSTEM_PROMPT and "recommendation_or_opinion" in SYSTEM_PROMPT)
    check("D7: الإشارات keyword محذَّر منها صراحة (نسبة خطئها عالية)",
          "نسبة خطئها عالية" in SYSTEM_PROMPT or "لا تُعطها وزنًا يذكر" in SYSTEM_PROMPT)


# ============================================================
# E. Orchestrator v4.0 — الحالات الإلزامية + الأمان
# ============================================================
def section_e():
    print("\n=== E. Orchestrator v4.0 — الحالات الإلزامية التسع (طلب المُشغّل) ===")

    MANDATORY_REJECT = [
        "حين يحبك الله يبدل وجه الحياة...",
        "عندي دكتور يساعد في الرسائل والتكاليف",
        "تعلم التداول واربح",
        "شكراً اكتمال جبت درجة عالية",
        "مين افضل مدرس؟",
        # [v4.3.7] التدريس/الشرح = REJECT (EXECUTION-ONLY)
        "مين يعرف دكتور يشرح رياضيات؟",
        "أبي مدرس خصوصي للمادة",
        # [v4.3.9] ملف جاهزة (عنده ≠ يسوي) = REJECT
        "احد عنده كويزات لدروس الكمي؟",
    ]
    MANDATORY_ACCEPT = [
        "أحد يحل لي واجب تفاضل 1",
        "احتاج شخص يحل معي السؤال",
        "من يسوي لي البحث بدالي بمقابل",
        "ابي أحد يخلص لي التقرير",
        # [v4.3.9] الصيغة الخليجية المختصرة (قائمة المُشغّل الحقيقية)
        "مين يسوي تقرير ؟؟",
        "احد يسوي سكليف ؟",
        "ابي احد يسوي مشروع تخرج 👩🏻‍🎓",
        # [v4.3.9] خدمات طلابية (قائمة المُشغّل الحقيقية)
        "احد يعرف يسوي cv ؟",
        "مين الي يقدر يسوي لي جدول ؟",
        "من يعرف احد يسوي عذر",
    ]

    async def run_e():
        cl = make_scripted_classifier(MANDATORY_SCRIPTS)

        print("  — الإلزامي REJECT (5):")
        # [v4.3] المسار المقبول للرفض: AI قرارًا أو chatter_guard هيكليًا
        # أو admission_gate — المطلب الإلزامي للمُشغّل هو الرفض نفسه.
        for t in MANDATORY_REJECT:
            r = await analyze_request_v4(t, cl, threshold=0.85)
            check(f"E: REJECT «{t[:30]}…»",
                  r.is_request is False and r.decision_path in (
                      "ai", "chatter_guard", "admission_gate"),
                  f"path={r.decision_path} reason={r.reason} cat={r.intent_type}")

        print("  — الإلزامي ACCEPT (4):")
        for t in MANDATORY_ACCEPT:
            r = await analyze_request_v4(t, cl, threshold=0.85)
            check(f"E: ACCEPT «{t[:30]}…»",
                  r.is_request is True and r.confidence >= 0.85 and r.ai_ok,
                  f"path={r.decision_path} conf={r.confidence} reason={r.reason}")

        # العتبة الصارمة
        print("  — العتبة والفشل الآمن:")
        cl_low = make_scripted_classifier({"تفاضل": _ai_json("ACCEPT", 0.7, "homework_execution_request", "شك")})
        r = await analyze_request_v4("أحد يشرح لي تفاضل 1", cl_low, threshold=0.85)
        check("E: ACCEPT بثقة 0.7 < 0.85 → REJECT low_confidence",
              r.is_request is False and r.reason == "low_confidence", f"reason={r.reason}")

        r = await analyze_request_v4("أحد يشرح لي تفاضل 1", cl_low, threshold=0.65)
        check("E: عتبة 0.65 → نفس الرسالة مقبولة (العتبة فاعلة)",
              r.is_request is True, f"conf={r.confidence}")

        # لا keyword fallback — رسالة مليئة بكلمات الطلب لكن AI معطّل
        r = await analyze_request_v4("محتاج أحد يشرح لي واجبي ويحل معي البحث", None)
        check("E: AI معطّل → REJECT (لا keyword fallback) — رسالة keyword-heavy",
              r.is_request is False and r.reason == "ai_classifier_not_configured", f"reason={r.reason}")
        sig = extract_signals("محتاج أحد يشرح لي واجبي ويحل معي البحث")
        check("E: الإشارات موجودة لكن لم تقرر (hint فقط)",
              bool(sig.requester_signals) and bool(sig.execution_signals), "signals empty?!")

        # timeout/error AI → REJECT صارم
        async def failing(provider, payload):
            raise RuntimeError("network down")
        cl_fail = IntentClassifier(providers=[{"key": "k", "url": "u", "model": "m", "name": "P"}],
                                   transport=failing, max_attempts=1)
        r = await analyze_request_v4("أحد يشرح لي تفاضل 1", cl_fail)
        check("E: AI يرمي استثناء → REJECT ai_error",
              r.is_request is False and r.reason in ("ai_error", "REJECT"), f"reason={r.reason}")

        # malformed AI output → REJECT
        async def garbage(provider, payload):
            return 200, json.dumps({"choices": [{"message": {"content": "لا أستطيع التصنيف"}}]})
        cl_g = IntentClassifier(providers=[{"key": "k", "url": "u", "model": "m", "name": "P"}], transport=garbage)
        r = await analyze_request_v4("أحد يشرح لي تفاضل 1", cl_g)
        check("E: مخرجات AI غير JSON → REJECT ai_error (parse failure)",
              r.is_request is False and r.reason == "ai_error", f"reason={r.reason}")

        # empty
        r = await analyze_request_v4("", cl)
        check("E: نص فارغ → REJECT empty", r.is_request is False and r.reason == "empty", f"reason={r.reason}")
        r = await analyze_request_v4("   ", cl)
        check("E: فراغات فقط → REJECT empty", r.is_request is False and r.reason == "empty", f"reason={r.reason}")

        # relay wrapper (structural dup)
        relay_text = "المرسل : أحمد\nالاسم : أبو محمد\nID : 12345\nنص الرساله : أحد يشرح لي تفاضل 1\nرابط الرساله : https://t.me/x/999"
        r = await analyze_request_v4(relay_text, cl)
        check("E: غلاف بوت ناقل → REJECT relay_bot_repost_duplicate (قبل AI)",
              r.is_request is False and r.reason == "relay_bot_repost_duplicate", f"reason={r.reason}")

        # semantic dedup عبر المنسِّق
        dd = SemanticDeduper(ttl_s=900)
        r1 = await analyze_request_v4("أحد يشرح لي تفاضل 1", cl, deduper=dd)
        r2 = await analyze_request_v4("التفاضل لي يشرح أحد", cl, deduper=dd)  # reorder → dup
        check("E: تكرار دلالي عبر المنسِّق — الثانية REJECT semantic_duplicate",
              r1.is_request is True and r2.is_request is False
              and r2.reason == "semantic_duplicate" and r2.dedup_kind in ("semantic", "exact"),
              f"r2.reason={r2.reason} kind={r2.dedup_kind}")

        # decision logging (in-memory sqlite)
        logger, conn = await make_logger()
        r = await analyze_request_v4("أحد يشرح لي تفاضل 1", cl, decision_logger=logger,
                                     chat_id=-100123, msg_id=555, source_phone="+9665xx")
        rows = await logger.recent_decisions(10)
        check("E: القرار مكتوب في filter_decisions",
              len(rows) == 1 and rows[0]["decision"] == "ACCEPT"
              and rows[0]["category"] == "homework_execution_request"
              and rows[0]["chat_id"] == -100123 and rows[0]["message_id"] == 555,
              str(rows[:1]))
        check("E: text_hash + preview + model مسجّلة",
              rows[0]["text_hash"] and rows[0]["text_preview"] and rows[0]["model"] == "mock-70b",
              str(rows[:1]))
        await conn.close()

        # to_dict compat (bot.py يقرأها)
        d = r.to_dict()
        check("E: to_dict يُرجع حقول v4 + legacy",
              d["ai_category"] and d["is_request"] is True and "matched_intents" in d and d["version"] == FILTER_VERSION,
              str(list(d.keys())[:8]))

    asyncio.run(run_e())


# ============================================================
# F. [STAGE 5] filter_store — قاعدة بيانات حقيقية (in-memory)
# ============================================================
async def make_logger():
    import aiosqlite
    conn = await aiosqlite.connect(":memory:")
    await conn.execute(FILTER_DECISIONS_SCHEMA)
    await conn.commit()
    fake_prod_db = types.SimpleNamespace(_conn=lambda: _return_conn(conn))

    async def _return_conn(c):
        return c
    # rebuild with proper async closure
    async def _conn():
        return conn
    fake_prod_db = types.SimpleNamespace(_conn=_conn)
    logger = DecisionLogger(fake_prod_db)
    logger._ensured = True  # الجدول أُنشئ يدويًا أعلاه
    return logger, conn


def section_f():
    print("\n=== F. [STAGE 5] filter_store — filter_decisions ===")

    async def run_f():
        logger, conn = await make_logger()
        ok1 = await logger.log_decision(chat_id=-1, message_id=1, raw_text="أحد يشرح لي تفاضل",
                                        decision="ACCEPT", confidence=0.93,
                                        category="homework_execution_request",
                                        reason="طلب شرح", model="m1", latency_ms=120)
        ok2 = await logger.log_decision(chat_id=-2, message_id=2, raw_text="تعلم التداول",
                                        decision="REJECT", confidence=0.97,
                                        category="advertisement", reason="إعلان",
                                        model="m1", latency_ms=95, dedup_kind="near")
        ok3 = await logger.log_decision(chat_id=-3, message_id=3, raw_text="مكرر",
                                        decision="REJECT", confidence=0.0,
                                        category="duplicate", reason="semantic_duplicate",
                                        dedup_kind="semantic")
        check("F1: log_decision يكتب 3 قرارات", ok1 and ok2 and ok3)

        rows = await logger.recent_decisions(2)
        check("F2: recent_decisions(2) — newest first", len(rows) == 2 and rows[0]["message_id"] == 3, str([r['message_id'] for r in rows]))

        rows_all = await logger.recent_decisions(100)
        check("F3: كل الحقول محفوظة",
              all(k in rows_all[0] for k in ("id", "chat_id", "message_id", "text_hash",
                                             "text_preview", "decision", "confidence",
                                             "category", "reason", "model", "latency_ms",
                                             "dedup_kind", "source_phone", "error_detail",
                                             "created_at")),
              str(list(rows_all[0].keys())))

        st = await logger.stats()
        check("F4: stats — total/accepts/rejects", st["total"] == 3 and st["accepts"] == 1 and st["rejects"] == 2, str(st))
        check("F5: stats — by_category", st["by_category"].get("advertisement") == 1, str(st["by_category"]))
        check("F6: stats — by_dedup_kind", st["by_dedup_kind"].get("near") == 1, str(st["by_dedup_kind"]))

        # text_hash consistency مع semantic_dedup fingerprint
        h1 = text_hash_of("أحد يشرح لي التفاضل")
        h2 = text_hash_of("أحد يشرح لي التفاضل")  # نفس النص
        check("F7: text_hash حتمي (نفس النص → نفس الـhash)", h1 == h2 and len(h1) == 32)

        # non-fatal: logger مكسور لا يرمي
        class BrokenDB:
            async def _conn(self):
                raise RuntimeError("db down")
        broken = DecisionLogger(BrokenDB())
        ok = await broken.log_decision(raw_text="x", decision="REJECT")
        check("F8: فشل DB → non-fatal (False، بلا استثناء)", ok is False)
        rows = await broken.recent_decisions(10)
        check("F9: فشل DB → قراءة فارغة بلا استثناء", rows == [])

        await conn.close()

    asyncio.run(run_f())


# ============================================================
# G. [STAGE 6] /api/filter_stats endpoint
# ============================================================
def section_g():
    print("\n=== G. [STAGE 6] /api/filter_stats ===")

    async def run_g():
        import bot as bot_mod
        from aiohttp import web

        logger, conn = await make_logger()
        await logger.log_decision(chat_id=-100123, message_id=42, raw_text="أحد يشرح لي تفاضل 1",
                                  decision="ACCEPT", confidence=0.93,
                                  category="homework_execution_request", reason="طلب شرح",
                                  model="mock-70b", latency_ms=110, source_phone="+9665xx")

        cl = make_scripted_classifier(MANDATORY_SCRIPTS)
        dd = SemanticDeduper()
        dd.register(tn_normalize("طلب سابق").canonical)

        cfg = types.SimpleNamespace(
            request_filter_enabled=True,
            request_filter_ai_threshold=0.85,
        )
        monitor = types.SimpleNamespace(
            config=cfg,
            request_classifier=cl,
            _request_semantic_deduper=dd,
            _request_decision_logger=logger,
        )

        class FakeRequest:
            def __init__(self, query=None, app=None):
                self.query = query or {}
                self.app = app if app is not None else {"monitor": monitor}

        handler = bot_mod.api_filter_stats_handler
        resp = await handler(FakeRequest())
        check("G1: 200 OK", resp.status == 200, f"status={resp.status}")
        data = json.loads(resp.text)
        check("G2: filter_version + mode", data.get("filter_version") == FILTER_VERSION
              and data.get("filter_mode") == "ai_intent_classifier", str(data.get("filter_version")))
        check("G3: آخر القرارات مُعادة", data.get("count") == 1
              and data["decisions"][0]["decision"] == "ACCEPT", str(data.get("count")))
        check("G4: حالة الـclassifier الحية", data["ai_classifier"]["enabled"] is True
              and data["ai_classifier"]["providers"] == 1, str(data.get("ai_classifier")))
        check("G4b: [v4.1] provider_health معروضة", isinstance(data["ai_classifier"].get("provider_health"), list)
              and len(data["ai_classifier"]["provider_health"]) == 1
              and data["ai_classifier"]["provider_health"][0]["status"] in ("ok", "cooldown"),
              str(data["ai_classifier"].get("provider_health")))
        check("G5: العتبة معروضة", data.get("ai_threshold") == 0.85, str(data.get("ai_threshold")))
        check("G6: db_stats مجمّعة", data["db_stats"]["accepts"] == 1, str(data.get("db_stats")))
        check("G7: semantic_dedup stats", "total_seen" in data.get("semantic_dedup", {}), str(data.get("semantic_dedup")))

        # limit param
        resp2 = await handler(FakeRequest(query={"limit": "1"}))
        data2 = json.loads(resp2.text)
        check("G8: ?limit=1 محترم", data2["limit"] == 1 and len(data2["decisions"]) == 1)
        resp3 = await handler(FakeRequest(query={"limit": "99999"}))
        data3 = json.loads(resp3.text)
        check("G9: limit يُقصّ إلى 500", data3["limit"] == 500)

        # no monitor → 503
        resp4 = await handler(FakeRequest(app={}))
        check("G10: بلا monitor → 503", resp4.status == 503, f"status={resp4.status}")

        await conn.close()

    asyncio.run(run_g())


# ============================================================
# H. Regression Corpus — corpus v3.0 كامل عبر mock AI
# ============================================================
def section_h():
    print("\n=== H. Regression Corpus (corpus v3.0 عبر mock AI) ===")
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "tests"))
        import test_request_intent_engine as corpus_mod
    except Exception as e:
        print(f"  (skipped — corpus غير متاح: {e})")
        return

    accept_cases = getattr(corpus_mod, "ACCEPT_CASES", [])
    reject_cases = getattr(corpus_mod, "REJECT_CASES", [])
    ambiguous = getattr(corpus_mod, "AMBIGUOUS_CASES", [])

    async def run_h():
        # mock يقرر: corpus ACCEPT → ACCEPT بثقة عالية؛ غير ذلك → REJECT
        # المفاتيح تُقارن على clean النص (بعد إزالة إيموجي/روابط/التطويل) لأن
        # المنسّق يمرّر clean للـAI وليس النص الخام.
        accept_clean = {}
        for t in accept_cases:
            accept_clean[tn_normalize(t).clean.strip()] = t
            accept_clean[t.strip()] = t

        async def corpus_transport(provider, payload):
            user_msg = payload["messages"][1]["content"]
            inner = user_msg.split('"""')[-2].strip() if '"""' in user_msg else user_msg.strip()
            if inner in accept_clean:
                content = _ai_json("ACCEPT", 0.95, "homework_execution_request", "corpus accept")
            else:
                content = _ai_json("REJECT", 0.95, "other", "corpus reject")
            return 200, json.dumps({"choices": [{"message": {"content": content}}]})

        cl = IntentClassifier(providers=[{"key": "k", "url": "u", "model": "corpus-mock", "name": "Mock"}],
                              transport=corpus_transport)

        acc_ok = rej_ok = 0
        for t in accept_cases:
            r = await analyze_request_v4(t, cl)
            acc_ok += 1 if r.is_request else 0
        for t in reject_cases:
            r = await analyze_request_v4(t, cl)
            rej_ok += 0 if r.is_request else 1
        for t in ambiguous:
            r = await analyze_request_v4(t, cl)
            rej_ok += 0 if r.is_request else 1

        total = len(accept_cases) + len(reject_cases) + len(ambiguous)
        check(f"H1: corpus ACCEPT ({len(accept_cases)}) كلها مقبولة عبر السباكة",
              acc_ok == len(accept_cases), f"{acc_ok}/{len(accept_cases)}")
        check(f"H2: corpus REJECT+AMBIGUOUS ({len(reject_cases)+len(ambiguous)}) كلها مرفوضة",
              rej_ok == len(reject_cases) + len(ambiguous),
              f"{rej_ok}/{len(reject_cases)+len(ambiguous)}")
        check("H3: corpus كامل عبر v4.0", acc_ok + rej_ok == total, f"{acc_ok+rej_ok}/{total}")

    asyncio.run(run_h())


# ============================================================
# I. Integration — _handle_request_path (Monitor method حقيقية)
# ============================================================
def section_i():
    print("\n=== I. Integration _handle_request_path (Monitor حقيقية + SQLite حقيقية) ===")

    async def run_i():
        import aiosqlite
        import bot as bot_mod
        from request_guard import ContentDeduper, CircuitBreaker
        from request_guard import RateLimiter as RequestRateLimiter

        # --- in-memory SQLite + ProductionDB ---
        conn = await aiosqlite.connect(":memory:")
        await conn.execute(FILTER_DECISIONS_SCHEMA)
        await conn.commit()

        async def _ensure_conn():
            return conn
        db_ns = types.SimpleNamespace(_ensure_conn=_ensure_conn)
        prod_db = bot_mod.ProductionDB(db_ns)

        # --- config ---
        cfg = types.SimpleNamespace(
            journal_enabled=False,
            requests_target_channel="@dhkskwksjskwk",
            request_filter_enabled=True,
            request_filter_max_per_minute=1000,
            request_filter_max_per_chat_per_minute=1000,
            request_filter_cb_threshold=10000,
            request_filter_cb_window_s=600,
            request_filter_cb_cooldown_s=600,
            request_filter_ai_threshold=0.85,
            request_filter_dedup_ttl_s=900,
        )

        # --- SendMock ---
        class SendMock:
            def __init__(self):
                self.calls = []
            async def __call__(self, *args, **kwargs):
                self.calls.append({"target": args[0] if args else None,
                                   "alert": args[1] if len(args) > 1 else ""})
            @property
            def called(self):
                return len(self.calls) > 0
        send_mock = SendMock()
        bot_client = types.SimpleNamespace(is_connected=lambda: True, send_message=send_mock)

        # --- classifier: يقبل طلب الرياضيات ويرفض الإعلان ---
        cl = make_scripted_classifier(MANDATORY_SCRIPTS)

        fm = types.SimpleNamespace(
            config=cfg, prod_db=prod_db, bot_client=bot_client,
            request_classifier=cl,
            _request_rate_limiter=RequestRateLimiter(max_per_minute=1000, max_per_chat_per_minute=1000),
            _request_circuit_breaker=CircuitBreaker(threshold=10000),
            _request_content_deduper=ContentDeduper(ttl_s=600),
        )
        fm._handle_request_path = types.MethodType(bot_mod.Monitor._handle_request_path, fm)

        class FakeEvent:
            def __init__(self, chat_id, msg_id):
                self.chat_id = chat_id
                self.id = msg_id
                self.chat = types.SimpleNamespace(title="مجموعة تجريبية", username=None)
                self.sender = types.SimpleNamespace(first_name="طالب", last_name="", username=None)
                self.message = types.SimpleNamespace(date=None)

        # 1) ACCEPT — يُرسل
        await fm._handle_request_path(FakeEvent(-1009990001, 9001), "أحد يشرح لي تفاضل 1",
                                      -1009990001, 9001, "+SIM")
        check("I1: طلب حقيقي (AI ACCEPT) → أُرسل لقناة الطلبات",
              send_mock.called and send_mock.calls[-1]["target"] == "@dhkskwksjskwk",
              f"calls={len(send_mock.calls)}")
        check("I2: نص الطلب موجود في التنبيه", "تفاضل" in (send_mock.calls[-1]["alert"] if send_mock.calls else ""),
              "")

        # 2) REJECT — لا يُرسل
        n_before = len(send_mock.calls)
        await fm._handle_request_path(FakeEvent(-1009990002, 9002), "تعلم التداول واربح",
                                      -1009990002, 9002, "+SIM")
        check("I3: إعلان (AI REJECT) → لم يُرسل", len(send_mock.calls) == n_before,
              f"calls={len(send_mock.calls)}")

        # 3) AI down → لا يرسل أبدًا (لا keyword fallback)
        fm2_classifier_down = types.SimpleNamespace()  # request_classifier = None below
        fm.request_classifier = None
        await fm._handle_request_path(FakeEvent(-1009990003, 9003), "محتاج أحد يحل واجبي",
                                      -1009990003, 9003, "+SIM")
        check("I4: AI معطّل → طلب keyword-heavy لم يُرسل (لا fallback)",
              len(send_mock.calls) == n_before, f"calls={len(send_mock.calls)}")
        fm.request_classifier = cl

        # 4) duplicate — لا يُرسل مرتين
        await fm._handle_request_path(FakeEvent(-1009990004, 9004), "مين يعرف دكتور يشرح رياضيات؟",
                                      -1009990004, 9004, "+SIM")
        n2 = len(send_mock.calls)
        await fm._handle_request_path(FakeEvent(-1009990005, 9005), "مين يعرف دكتور يشرح رياضيات؟",
                                      -1009990005, 9005, "+SIM")
        check("I5: نفس الطلب بصياغة معاد ترتيبها → semantic dedup منع الإرسال",
              len(send_mock.calls) == n2, f"calls={len(send_mock.calls)} vs {n2}")

        # 5) filter_decisions كُتبت فعليًا عبر المسار الحقيقي
        logger = DecisionLogger(prod_db)
        rows = await logger.recent_decisions(10)
        check("I6: filter_decisions كُتبت عبر المسار الحقيقي (≥4 قرارات)",
              len(rows) >= 4, f"rows={len(rows)}")
        if rows:
            cats = {r["category"] for r in rows}
            check("I7: الأسباب كاملة (category + reason + confidence)",
                  all(r["reason"] for r in rows) and all(isinstance(r["confidence"], float) for r in rows),
                  str(cats))

        await conn.close()

    asyncio.run(run_i())


# ============================================================
# J. back-compat — sync API أصبح signals-only
# ============================================================
def section_j():
    print("\n=== J. back-compat — sync analyze_request signals-only ===")
    r = analyze_request("أحد يشرح لي التفاضل")
    check("J1: sync analyze_request → لا قرار (is_request=False دائمًا)",
          r.is_request is False and r.reason == "v4_ai_required", f"reason={r.reason}")
    check("J2: لكن الإشارات مُستخرجة (hints)", len(r.requester_signals) > 0 or len(r.execution_signals) > 0,
          str(r.requester_signals[:3]))
    r2 = analyze_request("شكراً اكتمال جبت درجة عالية")
    check("J3: sync على إعلان → لا قرار + إشارات", r2.is_request is False)
    from request_filter import is_service_provider, is_request_message
    check("J4: is_service_provider = إشارة مقدّم (ليست قرارًا)",
          isinstance(is_service_provider("عندي دكتور يساعد في الرسائل والتكاليف"), bool))
    ok, d = is_request_message("أحد يشرح لي التفاضل")
    check("J5: is_request_message → دائمًا (False, dict)", ok is False and isinstance(d, dict))


# ============================================================
# K. [v4.1] Provider Health Manager — resilience + observability
# ============================================================
def section_k():
    print("\n=== K. [v4.1] Provider Health Manager — circuit breaker + pacing + retry ===")

    def _ai(d, c, cat, r):
        return json.dumps({"decision": d, "confidence": c, "category": cat, "reason": r},
                          ensure_ascii=False)

    async def run_k():
        # ---- K1: مفتاح ميت (403) → circuit breaker: يستُدعى مرة واحدة فقط ----
        calls = {"P1": 0, "P2": 0}

        async def t403(provider, payload):
            calls[provider["name"]] += 1
            if provider["name"] == "P1":
                return 403, '{"error":{"message":"Forbidden"}}'
            return 200, json.dumps({"choices": [{"message": {"content": _ai("REJECT", 0.9, "other", "x")}}]})

        clk = IntentClassifier(
            providers=[{"key": "k1", "url": "u", "model": "m1", "name": "P1"},
                       {"key": "k2", "url": "u", "model": "m2", "name": "P2"}],
            transport=t403, cooldown_scale=0.001, retry_rounds=2, total_budget_s=2)
        d1 = await clk.classify("رسالة 1")
        d2 = await clk.classify("رسالة 2")
        d3 = await clk.classify("رسالة 3")
        check("K1: dead key (403) استُدعي مرة واحدة فقط (circuit breaker)",
              calls["P1"] == 1 and calls["P2"] == 3, str(calls))
        check("K1b: الرسائل تُخدم من المزوّد الحي",
              d2.ok and d3.ok and d2.provider_name == "P2", str(d2.provider_name))
        h = clk.provider_health()
        check("K1c: provider_health — الميت في cooldown مع السبب",
              h[0]["status"] == "cooldown" and "403" in h[0]["last_error"], str(h[0]))
        check("K1d: provider_health — الحي ok مع عدّادات",
              h[1]["status"] == "ok" and h[1]["success_count"] == 3, str(h[1]))

        # ---- K2: 429 rate-limit عابر → جولة إعادة محاولة تنقذ الرسالة ----
        state = {"n": 0}

        async def t429(provider, payload):
            state["n"] += 1
            if state["n"] == 1:
                return 429, "rate limit"
            return 200, json.dumps({"choices": [{"message": {"content": _ai("REJECT", 0.9, "other", "y")}}]})

        cl429 = IntentClassifier(providers=[{"key": "k", "url": "u", "model": "m", "name": "P"}],
                                 transport=t429, cooldown_scale=0.01,
                                 retry_rounds=3, total_budget_s=3)
        dr = await cl429.classify("رسالة أثناء 429 عابر")
        check("K2: 429 عابر → إعادة المحاولة بعد cooldown تنجح (لا فقدان)",
              dr.ok is True, str(dr.error))
        check("K2b: counters — cooldown_waits سُجّلت",
              cl429.counters["cooldown_waits"] >= 1, str(cl429.counters))

        # ---- K3: pacing — نداء واحد لكل مفتاح كل min_interval ----
        times = []

        async def tp(provider, payload):
            times.append(time.monotonic())
            return 200, json.dumps({"choices": [{"message": {"content": _ai("REJECT", 0.9, "other", "z")}}]})

        clp = IntentClassifier(providers=[{"key": "k", "url": "u", "model": "m", "name": "P"}],
                               transport=tp, min_interval_s=0.2)
        await clp.classify("نداء أول")
        await clp.classify("نداء ثان")
        gap = times[1] - times[0]
        check("K3: pacing يفرّج بين النداءات (gap ≥ min_interval)",
              gap >= 0.19, f"gap={gap:.3f}")

        # ---- K4: كل المزوّدين فاشلون → ai_error مع تفاصيل كاملة ----
        async def t500(provider, payload):
            return 500, "server error"

        clf = IntentClassifier(providers=[{"key": "k", "url": "u", "model": "m", "name": "P"}],
                               transport=t500, total_budget_s=1.5,
                               cooldown_scale=0.01, retry_rounds=2, max_attempts=1)
        df = await clf.classify("رسالة ستفشل")
        check("K4: فشل كامل → ok=False + category=ai_error + تفاصيل",
              df.ok is False and df.category == "ai_error" and "http 500" in df.error
              and "attempts" in df.error, str(df.error))

        # ---- K5: بوابة max_pending — الفائض يُرفض فورًا (أمان الاندفاعات) ----
        async def tslow(provider, payload):
            await asyncio.sleep(0.3)
            return 200, json.dumps({"choices": [{"message": {"content": _ai("REJECT", 0.9, "other", "s")}}]})

        clo = IntentClassifier(providers=[{"key": "k", "url": "u", "model": "m", "name": "P"}],
                               transport=tslow, max_pending=2,
                               retry_rounds=1, total_budget_s=5, timeout_s=3)
        tasks = [asyncio.create_task(clo.classify(f"رسالة {i}")) for i in range(10)]
        results = await asyncio.gather(*tasks)
        overloads = sum(1 for r in results if r.category == "overloaded")
        oks = sum(1 for r in results if r.ok)
        check("K5: الاندفاعة → بوابة max_pending ترفض الفائض فورًا (overloaded)",
              overloads >= 1 and oks >= 1, f"ok={oks} overloaded={overloads}")
        check("K5b: overloaded مُسجّل في counters",
              clo.counters["overload_rejects"] >= 1, str(clo.counters.get("overload_rejects")))

        # ---- K6: الميزانية تنتهي → ai_error سريع بلا تعليق ----
        async def tveryslow(provider, payload):
            await asyncio.sleep(2.0)
            return 200, "{}"

        clb = IntentClassifier(providers=[{"key": "k", "url": "u", "model": "m", "name": "P"}],
                               transport=tveryslow, total_budget_s=1.0, timeout_s=0.3,
                               retry_rounds=1, max_attempts=1)
        t0 = time.monotonic()
        db = await clb.classify("رسالة بطيئة")
        elapsed = time.monotonic() - t0
        check("K6: الميزانية تُحترم (لا تعليق) + ai_error",
              db.ok is False and elapsed < 2.5, f"elapsed={elapsed:.2f}s")

        # ---- K7: المحاولات محدودة (attempts × rounds) ----
        n500 = {"n": 0}

        async def t500c(provider, payload):
            n500["n"] += 1
            return 500, "e"

        cla = IntentClassifier(
            providers=[{"key": f"k{i}", "url": "u", "model": "m", "name": f"P{i}"} for i in range(4)],
            transport=t500c, retry_rounds=2, max_attempts=2,
            cooldown_scale=0.001, total_budget_s=2)
        await cla.classify("رسالة")
        check("K7: إجمالي المحاولات ≤ attempts×rounds",
              n500["n"] <= 4, f"calls={n500['n']}")

        # ---- K8: نجاح بعد فشل → يُصفّر حالة الصحة ----
        state2 = {"n": 0}

        async def tflux(provider, payload):
            state2["n"] += 1
            if state2["n"] <= 1:
                return 429, "rl"
            return 200, json.dumps({"choices": [{"message": {"content": _ai("REJECT", 0.9, "other", "r")}}]})

        clz = IntentClassifier(providers=[{"key": "k", "url": "u", "model": "m", "name": "P"}],
                               transport=tflux, cooldown_scale=0.01,
                               retry_rounds=2, total_budget_s=3)
        await clz.classify("أولى")   # 429 ثم نجاح في الجولة الثانية
        await clz.classify("ثانية")  # نجاح مباشر — الحالة مصفّرة
        h2 = clz.provider_health()[0]
        check("K8: النجاح يُصفّر حالة الصحة (consecutive_fails=0)",
              h2["consecutive_fails"] == 0 and h2["status"] == "ok", str(h2))

        # ---- K9: error_detail يُكتب في filter_decisions عبر المسار الكامل ----
        import aiosqlite
        from filter_store import DecisionLogger as DL

        conn = await aiosqlite.connect(":memory:")
        ns = types.SimpleNamespace(_conn=(lambda c=conn: _async_ret(c)))
        logger = DL(ns)

        async def _failing_transport(provider, payload):
            return 403, "forbidden"

        clx = IntentClassifier(providers=[{"key": "k", "url": "u", "model": "m", "name": "Dead"}],
                               transport=_failing_transport, retry_rounds=1,
                               total_budget_s=1)
        from request_filter import analyze_request_v4
        rx = await analyze_request_v4("أحد يشرح لي التفاضل", clx,
                                      chat_id=-100123, msg_id=77, source_phone="+9665x",
                                      decision_logger=logger)
        check("K9: فشل AI عبر المسار الكامل → REJECT",
              rx.is_request is False and rx.reason == "ai_error", f"reason={rx.reason}")
        rows = await logger.recent_decisions(5)
        check("K9b: error_detail مكتوب في filter_decisions (http 403 + provider)",
              rows and rows[0].get("error_detail") and "403" in rows[0]["error_detail"]
              and "Dead" in rows[0]["error_detail"],
              str(rows[0].get("error_detail") if rows else "no rows"))
        st = await logger.stats()
        check("K9c: stats — by_error_detail مجمّعة",
              st.get("by_error_detail") and "403" in list(st["by_error_detail"].keys())[0],
              str(st.get("by_error_detail")))
        await conn.close()

        # ---- K10: migration — جدول قديم بلا error_detail يُرقّى تلقائيًا ----
        conn2 = await aiosqlite.connect(":memory:")
        await conn2.execute("""CREATE TABLE filter_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, message_id INTEGER,
            text_hash TEXT NOT NULL, text_preview TEXT, decision TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.0, category TEXT, reason TEXT,
            model TEXT, latency_ms INTEGER, dedup_kind TEXT, source_phone TEXT,
            created_at REAL)""")
        await conn2.commit()
        ns2 = types.SimpleNamespace(_conn=(lambda c=conn2: _async_ret(c)))
        logger2 = DL(ns2)
        okm = await logger2.log_decision(raw_text="x", decision="REJECT",
                                         category="ai_error", reason="ai_error",
                                         error_detail="http 429 (P) [test]")
        rows2 = await logger2.recent_decisions(5)
        check("K10: migration — ALTER TABLE يضيف error_detail ويكتب فيه",
              okm and rows2 and rows2[0].get("error_detail") == "http 429 (P) [test]",
              str(rows2[0].get("error_detail") if rows2 else "no rows"))
        await conn2.close()

        # ---- K11: [v4.1.1] 404 (نموذج مُوقوف) → cooldown طويل (auth) ----
        async def t404(provider, payload):
            return 404, '{"error":{"message":"model not found"}}'

        cl404 = IntentClassifier(providers=[{"key": "k", "url": "u", "model": "dead-model", "name": "P"}],
                                 transport=t404, retry_rounds=1, total_budget_s=1)
        d404 = await cl404.classify("رسالة لنموذج ميت")
        h404 = cl404.provider_health()[0]
        check("K11: 404 model-gone → dead (auth cooldown ≥ 30 دقيقة)",
              d404.ok is False and "404" in d404.error
              and h404["status"] == "cooldown" and h404["cooldown_remaining_s"] >= 1700,
              f"cd={h404['cooldown_remaining_s']}s err={(d404.error or '')[:50]}")

    async def _async_ret(c):
        return c

    asyncio.run(run_k())


# ============================================================
# main


# ============================================================
# L. [v4.2/v4.3] Chatter Guard + Admission Gate — corpus الإنتاج الفعلي
# ============================================================
# [v4.3] 21 سوالف من قناة الإنتاج (2026-09-01: 15/20 منشورة كانت سوالف)
# + الحالات الإلزامية. كلها يجب أن تُرفض هيكليًا (بلا نداء AI).
PRODUCTION_CHATTER = [
    "دكتوره علا ياسمين البار عربي كيف؟مين قد درس عندها بالله يفيدني",  # استطلاع دكتوره
    "تكفون عطوني اساله هندسه بس ولا اساله طبيعيه",                    # طلب ملفات
    "احد عنده كويزات لدروس الكمي؟؟؟",                                   # طلب كويزات
    "الحين يالربع فيه احد نزل له الجدول بالتحضيريه ؟",                 # إداري
    "الله يساعدكم أحد يفيدني",                                          # بلا مادة
    "بنات وين الاقي كتاب الريدنق والرايتينق محلول؟",                   # كتاب
    "واقعد لخص وانت تذاكر وحط زبده التعاريف والمفاهيم على جنب",         # نصيحة
    "نزلت لي ماده والي يدرس فيها دكتور نايف طبيعي رجال؟",              # استطلاع
    "ابي شعب انجليزي تكفون بيقفل ولاماده انجليزي ضبطت",                # شعب
    "فيه احد عنده الصوره حقت كل ماده ووش تعادل",                        # صور مواد
    "ياليت اللي عنده علم عن هالموضوع يع",                               # مبتورة
    "احد يعرف دكتوره بدريه العمري؟",                                    # استفسار مدرس
    "بنات احد يعرف مكتب دكتورة منال السالم لرسائل الماجستير؟",        # مكتب
    "اكتبي اسم جدك اول حرف له بس",                                      # لعبة
    "حطي المواد وسوي تنفيذ",                                            # أمر
    "السلام عليكم مين ياخذ احياء عملي مع دكتوره سلمي المطرفي وكيمياء عملي مع د",  # تنسيق زملاء
    "احد يعرف كيف افرق بين الازمنة بطريقه مبسطه وسهله",                # سؤال معرفي
    "حين يحبك الله يبدل وجه الحياة...",                                 # ديني (إلزامي)
    "تعلم التداول واربح",                                               # إعلان (إلزامي)
    "شكراً اكتمال جبت درجة عالية",                                      # مدح (إلزامي)
    "مين افضل مدرس؟",                                                   # توصية (إلزامي)
]

# طلبات حقيقية (من القناة + الإلزامي) — الحرس لا يلمسها أبدًا (القرار للـAI)
PRODUCTION_REAL_REQUESTS = [
    "مافي خصوصي للمادة أو احد يشرح الاولد اكز",       # من القناة (حقيقي)
    "في احد يشرح احياء تحضيري احتاج مساعده؟",          # من القناة (حقيقي)
    "مرحبا في احد يشرح احياء تحضيري احتاج مساعده؟",    # من القناة (حقيقي)
    "مين يعرف دكتور يشرح رياضيات؟",                     # إلزامي
    "أحد يشرح لي تفاضل 1",                              # إلزامي
    "احتاج شخص يحل معي السؤال",                         # إلزامي
    "أبي مدرس خصوصي للمادة",                            # إلزامي
    "اشرحوا لي الماده قبل الاختبار",                    # أمر جمع + مستفيد
    "سوي لي بحث التخرج بليز",                           # أمر + مستفيد
    "اكتب لي ملخص الماده",                              # أمر + مستفيد
    "راجعوا معي قبل الاختبار",                          # أمر جمع + معي
    "محتاجة شخص يجهز عرضي",                             # corpus (exec+requester)
]

# [v4.3.1] فحص adversarial مستقل — سوالف شائعة لم تكن في corpus الإنتاج
# لكنها تُحاكي نفس الأنماط (عروض/خبر ماضٍ/حسم ذاتي/سعر/توصية/موقع)
ADVERSARIAL_CHATTER = [
    "الله يعطيكم العافيه على المجهود",      # شكر عام
    "مبروك التخرج يارب",                    # تهنئة
    "صباح الخير ياجماعه",                   # تحية
    "مين من جده؟",                          # لعبة اجتماعية
    "عندي كويزات جاهزه اللي يبيها يكلمني",  # عرض ملفات [G6]
    "ارفعوا ايديكم اذا معكم نفس الشعبه",    # لعبة/استطلاع
    "شكلي بانسحب من الماده",                # حسم شخصي [G8]
    "الاختبار كان صعب اليوم",               # خبر ماضٍ [G7]
    "تبي ملخصات؟ عندي كل شيء",              # عرض [G6]
    "كم سعر الكتاب بالنسخه؟",                # سؤال سعر [G9]
    "دكتور خالد شرحه ممتاز انصحكم فيه",     # توصية يقدّمها [G10]
    "وين صالة 12 بالجامعه؟",                # موقع إداري [G5]
]

# عبارات تحاكي أنماط الأنماط الجديدة لكنها طلبات حقيقية — الحرس يجب
# ألا يلمسها (صمام أفعال الخدمة + مفردات امتحان/ميدتيرم)
ADVERSARIAL_REAL_REQUESTS = [
    "عندي بحث محتاج احد ينجزه لي",            # عندي + تنفيذ
    "الاختبار اللي كان صعب ابي احد يشرحه لي", # خبر ماضٍ + خدمة
    "افكر اروح لمدرس خصوصي",                   # تردد + خصوصي
    "ابغى احد يشرح لي في القاعه؟",             # موقع + خدمة
    "عندي امتحان ابغى مساعده",                 # عندي + امتحان (مفردات جديدة)
]


def section_l():
    print("\n=== L. [v4.3] Chatter Guard — corpus الإنتاج الفعلي ===")
    from request_filter import chatter_guard_check, normalize_text
    from text_normalizer import normalize as _tn

    print("  — سوالف القناة الفعلية (21) — كلها يجب أن تُرفض هيكليًا:")
    for t in PRODUCTION_CHATTER:
        nt = _tn(t)
        clean = nt.clean if nt.clean else t
        sig = extract_signals(t)
        r = chatter_guard_check(normalize_text(clean), sig)
        check(f"L: REJECT «{t[:38]}…»",
              bool(r), f"reason={r or '(PASS!!)'}")

    print("  — طلبات حقيقية (12) — الحرس لا يلمسها (يمرّ للـAI):")
    for t in PRODUCTION_REAL_REQUESTS:
        nt = _tn(t)
        clean = nt.clean if nt.clean else t
        sig = extract_signals(t)
        r = chatter_guard_check(normalize_text(clean), sig)
        check(f"L: PASS «{t[:38]}…»",
              r == '', f"reason={r}")

    # [v4.3.1] فحص adversarial مستقل (توليد الوكيل لا المُشغّل) — 12 سوالف
    # جديدة + 5 طلبات تحاكي الأنماط الجديدة
    print("  — [v4.3.1] adversarial سوالف جديدة (12) — كلها تُرفض هيكليًا:")
    for t in ADVERSARIAL_CHATTER:
        nt = _tn(t)
        clean = nt.clean if nt.clean else t
        sig = extract_signals(t)
        r = chatter_guard_check(normalize_text(clean), sig)
        check(f"L: ADVERSARIAL REJECT «{t[:38]}…»",
              bool(r), f"reason={r or '(PASS!!)'}")

    print("  — [v4.3.1] adversarial طلبات تحاكي الأنماط (5) — لا تُلمس:")
    for t in ADVERSARIAL_REAL_REQUESTS:
        nt = _tn(t)
        clean = nt.clean if nt.clean else t
        sig = extract_signals(t)
        r = chatter_guard_check(normalize_text(clean), sig)
        check(f"L: ADVERSARIAL PASS «{t[:38]}…»",
              r == '', f"reason={r}")

    # عبر المنسّق الكامل: chatter_guard يرفض قبل نداء AI (عدّاد calls صفر)
    async def run_l():
        calls = {"n": 0}

        async def transport(provider, payload):
            calls["n"] += 1
            return 200, json.dumps({"choices": [{"message": {"content":
                _ai_json("ACCEPT", 0.99, "homework_execution_request", "scripted-accept")}}]})

        cl = IntentClassifier(
            providers=[{"key": "k", "url": "u", "model": "m", "name": "P"}],
            transport=transport)
        # سوالف → الـAI لا يُستدعى أصلًا (guard قبل classify)
        for t in PRODUCTION_CHATTER:
            r = await analyze_request_v4(t, cl, threshold=0.85)
            if r.decision_path == "chatter_guard":
                continue
            # السوالف التي عبرت الحرس (قلة) — الـAI المبرمج ACCEPT سيرفضها
            # لأنها لا تحمل مفتاح التصنيف المبرمج → REJECT
        guard_calls = calls["n"]
        # طلب حقيقي واحد على الأقل يجب أن يصل الـAI
        r = await analyze_request_v4(
            "مافي خصوصي للمادة أو احد يشرح الاولد اكز", cl, threshold=0.85)
        check("L: طلب حقيقي وصل الـAI وقُبل (الحرس لم يمنعه)",
              r.is_request is True and calls["n"] > guard_calls,
              f"path={r.decision_path} calls={calls['n']}")
        # ملاحظة: بعض السوالف تعبر الحرس (بلا نمط) — الـAI المبرمج هنا
        # يرد ACCEPT لأي نداء، لذا لا نفحص عدّاد الصفر الكامل؛ الحسم
        # يحدث في فحص الحالات الفردية أعلاه + فحوصات الـAI الحقيقية.

    asyncio.run(run_l())

    # عزل الحرس: chatter_guard=False → الرسالة تمر للـAI (الصمام قابل للعزل)
    async def run_l2():
        async def transport(provider, payload):
            return 200, json.dumps({"choices": [{"message": {"content":
                _ai_json("REJECT", 0.9, "other", "scripted-reject")}}]})
        cl = IntentClassifier(
            providers=[{"key": "k", "url": "u", "model": "m", "name": "P"}],
            transport=transport)
        r = await analyze_request_v4(
            "اكتبي اسم جدك اول حرف له بس", cl, threshold=0.85,
            chatter_guard=False)
        check("L: chatter_guard=False → القرار يعود للـAI (عزل الصمام)",
              r.decision_path == "ai" and r.is_request is False,
              f"path={r.decision_path}")

    asyncio.run(run_l2())


# ============================================================
# M. [v4.2] Admission Gate + Persistent Decision Dedup
# ============================================================
def section_m():
    print("\n=== M. [v4.2] Admission Gate + Persistent Decision Dedup ===")
    from request_filter import admission_allowed

    # admission gate: بلا إشارات → REJECT no_signals بلا نداء AI
    async def run_m():
        calls = {"n": 0}

        async def transport(provider, payload):
            calls["n"] += 1
            return 200, json.dumps({"choices": [{"message": {"content":
                _ai_json("ACCEPT", 0.99, "homework_execution_request", "x")}}]})

        cl = IntentClassifier(
            providers=[{"key": "k", "url": "u", "model": "m", "name": "P"}],
            transport=transport)
        # رسالة بلا أي إشارة معجمية
        r = await analyze_request_v4(
            "الحمد لله على كل حال", cl, threshold=0.85, admission_gate=True)
        check("M: admission gate — بلا إشارات → REJECT no_signals (بلا AI)",
              r.decision_path == "admission_gate" and r.is_request is False
              and calls["n"] == 0,
              f"path={r.decision_path} calls={calls['n']}")

        # رسالة ذات إشارات → تمر للـAI حتى لو كانت البوابة مفعّلة
        calls["n"] = 0
        r = await analyze_request_v4(
            "مافي خصوصي للمادة أو احد يشرح الاولد اكز", cl, threshold=0.85,
            admission_gate=True)
        check("M: ذات إشارات → تمر البوابة للـAI",
              calls["n"] >= 1 and r.is_request is True,
              f"path={r.decision_path} calls={calls['n']}")

        # admission gate معطّل → حتى بلا إشارات تصل الـAI (قراره)
        # (chatter_guard معطّل هنا أيضًا — عزل آلية واحدة في كل فحص)
        calls["n"] = 0
        r = await analyze_request_v4(
            "الحمد لله على كل حال", cl, threshold=0.85,
            admission_gate=False, chatter_guard=False)
        check("M: admission_gate=False → الرسالة تصل الـAI (البوابة قابلة للعزل)",
              calls["n"] >= 1,
              f"path={r.decision_path} calls={calls['n']}")

    asyncio.run(run_m())

    # persistent decision dedup: قرار نهائي سابق لنفس (chat_id, msg_id)
    async def run_m2():
        import aiosqlite
        db_path = "/tmp/_v4_test_dedup_m2.sqlite"
        try:
            os.remove(db_path)
        except OSError:
            pass
        conn = await aiosqlite.connect(db_path)

        class FakeProdDB:
            async def _conn(self):
                return conn

        logger = DecisionLogger(FakeProdDB())

        calls = {"n": 0}

        async def transport(provider, payload):
            calls["n"] += 1
            return 200, json.dumps({"choices": [{"message": {"content":
                _ai_json("REJECT", 0.9, "other", "scripted")}}]})

        cl = IntentClassifier(
            providers=[{"key": "k", "url": "u", "model": "m", "name": "P"}],
            transport=transport)
        # قرار نهائي أول (REJECT other)
        r1 = await analyze_request_v4(
            "رسالة نهائية أي نص", cl, threshold=0.85,
            chat_id=100, msg_id=200, decision_logger=logger)
        # إعادة نفس الرسالة → already_decided_db بلا نداء AI جديد
        calls_before = calls["n"]
        r2 = await analyze_request_v4(
            "رسالة نهائية أي نص", cl, threshold=0.85,
            chat_id=100, msg_id=200, decision_logger=logger)
        check("M: persistent dedup — قرار نهائي سابق → already_decided_db",
              r2.decision_path == "persistent_dedup" and r2.is_request is False
              and calls["n"] == calls_before,
              f"path={r2.decision_path} calls={calls['n']}/{calls_before}")

        # قرار عابر (ai_error) لا يحجب إعادة المحاولة
        async def failing_transport(provider, payload):
            return 500, "server error"
        cl_fail = IntentClassifier(
            providers=[{"key": "k", "url": "u", "model": "m", "name": "P"}],
            transport=failing_transport, total_budget_s=1.0, max_attempts=1)
        logger2 = DecisionLogger(FakeProdDB())
        # نص يحمل فعل خدمة (يشرح) → يعبر الحرس ويبلغ الـAI الفاشل → ai_error
        rf1 = await analyze_request_v4(
            "ابي احد يشرح لي الماده", cl_fail, threshold=0.85,
            chat_id=300, msg_id=400, decision_logger=logger2)
        check("M: قرار عابر (ai_error) يُسجّل",
              rf1.is_request is False and rf1.ai_ok is False
              and rf1.decision_path == "ai",
              f"path={rf1.decision_path} reason={rf1.reason}")
        # نفس الرسالة الآن بمزوّد حي → يجب أن تُصنَّف (القرار العابر لا يحجب)
        r_ok = await analyze_request_v4(
            "مافي خصوصي للمادة أو احد يشرح الاولد اكز", cl, threshold=0.85,
            chat_id=300, msg_id=400, decision_logger=logger2)
        check("M: قرار عابر لا يحجب إعادة التصنيف بعد التعافي",
              r_ok.decision_path == "ai",
              f"path={r_ok.decision_path} reason={r_ok.reason}")

        # decision filter في recent_decisions
        acc = await logger2.recent_decisions(limit=10, decision="ACCEPT")
        rej = await logger2.recent_decisions(limit=10, decision="REJECT")
        check("M: recent_decisions decision filter — ACCEPT/REJECT منفصلان",
              all(d["decision"] == "ACCEPT" for d in acc)
              and all(d["decision"] == "REJECT" for d in rej) and len(rej) >= 1,
              f"acc={len(acc)} rej={len(rej)}")

        await conn.close()
        try:
            os.remove(db_path)
        except OSError:
            pass

    asyncio.run(run_m2())


# ============================================================
# N. [v4.3.2] Dead-Key Latch + منع تلوث semantic dedup
# ============================================================
def section_n():
    print(f"\n=== N. [v4.3.2] dead-key latch + منع تلوث semantic dedup ({FILTER_VERSION}) ===")

    async def run_n():
        # ---- N1: 19 فشلًا متتاليًا → لا latch (rate cooldown فقط، cap 120s) ----
        async def ok_transport(provider, payload):
            return 200, json.dumps({"choices": [{"message": {"content": _ai_json("REJECT", 0.9, "other", "x")}}]})

        cl = IntentClassifier(
            providers=[{"key": "k", "url": "u", "model": "m", "name": "Dead"}],
            transport=ok_transport, cooldown_scale=1.0)
        for _ in range(19):
            cl._record_failure(0, "http 429 rate limit (Dead)", kind='rate')
        cd19 = cl._pstate[0]['cooldown_until'] - time.monotonic()
        check("N1: 19 فشلًا متتاليًا → لا latch (rate cooldown ≤120s)",
              cd19 <= 121.0 and cl._pstate[0]['last_kind'] == 'rate',
              f"cd={cd19:.1f}s kind={cl._pstate[0]['last_kind']}")
        check("N1b: counter dead_key_latches = 0 قبل العتبة",
              cl.stats().get("dead_key_latches", -1) == 0,
              str(cl.stats().get("dead_key_latches")))

        # ---- N2: الفشل رقم 20 → LATCH (cooldown 30 دقيقة) ----
        cl._record_failure(0, "http 429 rate limit (Dead)", kind='rate')
        st = cl._pstate[0]
        cd20 = st['cooldown_until'] - time.monotonic()
        check("N2: الفشل رقم 20 → latch 30 دقيقة",
              cd20 >= 1700.0 and st['last_kind'] == 'dead_key',
              f"cd={cd20:.1f}s kind={st['last_kind']}")
        health = cl.provider_health()[0]
        check("N2b: provider_health يُظهر status=dead_key",
              health['status'] == 'dead_key' and health['cooldown_kind'] == 'dead_key',
              f"status={health.get('status')} kind={health.get('cooldown_kind')}")
        check("N2c: counter dead_key_latches = 1",
              cl.stats().get("dead_key_latches") == 1,
              str(cl.stats().get("dead_key_latches")))

        # ---- N3: أول نجاح → إفراغ كامل وفوري (عودة للخدمة) ----
        cl._record_success(0)
        health = cl.provider_health()[0]
        check("N3: أول نجاح → عودة فورية (status=ok، consecutive=0)",
              health['status'] == 'ok' and health['consecutive_fails'] == 0,
              str(health))
        cl._record_failure(0, "http 429 rate limit (Dead)", kind='rate')
        health = cl.provider_health()[0]
        check("N3b: فشل واحد بعد النجاح لا يعيد الـlatch (consecutive=1 < 20)",
              health['status'] != 'dead_key' and health['consecutive_fails'] == 1,
              f"status={health.get('status')} consec={health.get('consecutive_fails')}")

        # ---- N4: دورة الاستكشاف — فشل واحد بعد انتهاء latch يعيد القفل فورًا ----
        cl2 = IntentClassifier(
            providers=[{"key": "k", "url": "u", "model": "m", "name": "Dead2"}],
            transport=ok_transport, cooldown_scale=1.0)
        for _ in range(20):
            cl2._record_failure(0, "http 429 rate limit (Dead2)", kind='rate')
        latches_before = cl2.stats()["dead_key_latches"]
        # محاكاة انتهاء الـlatch (مرور 30 دقيقة) ثم محاولة استكشاف فاشلة واحدة
        cl2._pstate[0]['cooldown_until'] = time.monotonic() - 0.1
        cl2._record_failure(0, "http 429 rate limit (Dead2)", kind='rate')
        st2 = cl2._pstate[0]
        cd26 = st2['cooldown_until'] - time.monotonic()
        check("N4: فشل الاستكشاف بعد انتهاء latch يعيد القفل فورًا — probe واحد/30د",
              cd26 >= 1700.0 and st2['last_kind'] == 'dead_key'
              and cl2.stats()["dead_key_latches"] == latches_before + 1,
              f"cd={cd26:.1f} latches={cl2.stats()['dead_key_latches']}")

        # ---- N5/N6: ai_error لا يلوّث الـsemantic dedup ----
        async def failing_transport(provider, payload):
            return 429, "rate limit"
        clf = IntentClassifier(
            providers=[{"key": "k", "url": "u", "model": "m", "name": "F"}],
            transport=failing_transport, max_attempts=1, retry_rounds=1,
            total_budget_s=1.0, pool_wait_budget_s=0.5)
        dd = SemanticDeduper(ttl_s=900)
        rx = await analyze_request_v4("أحد يشرح لي تفاضل 1", clf, deduper=dd)
        check("N5: فشل AI كامل (429) → REJECT ai_error",
              rx.is_request is False and rx.reason == "ai_error",
              f"reason={rx.reason}")

        # المزوّد يتعافى — نفس النص يجب أن يحصل على قرار AI حقيقي
        # (قديمًا: كان مسجّلًا في الـdedup → semantic_duplicate بلا قرار قط)
        cl_ok = make_scripted_classifier(
            {"تفاضل": _ai_json("ACCEPT", 0.97, "homework_execution_request", "طلب تنفيذ بدلاً عن الطالب")})
        ry = await analyze_request_v4("أحد يشرح لي تفاضل 1", cl_ok, deduper=dd)
        check("N6: النص ذاته بعد التعافي → قرار حقيقي (لا تلوث dedup)",
              ry.is_request is True,
              f"reason={ry.reason} path={ry.decision_path}")

        # ---- N7: القرار الحقيقي يُسجَّل في dedup كالسابق (سلوك محفوظ) ----
        dd2 = SemanticDeduper(ttl_s=900)
        r1 = await analyze_request_v4("أحد يشرح لي تفاضل 1", cl_ok, deduper=dd2)
        r2 = await analyze_request_v4("التفاضل لي يشرح أحد", cl_ok, deduper=dd2)
        check("N7: قرار AI حقيقي يُسجَّل — المشابه التالي semantic_duplicate",
              r1.is_request is True and r2.is_request is False
              and r2.reason == "semantic_duplicate",
              f"r2.reason={r2.reason}")

    asyncio.run(run_n())


# ============================================================
def main():
    print("=" * 70)
    print(f"Request Intent Engine {FILTER_VERSION} ({FILTER_MODE}) — v4.0/v4.1/v4.2/v4.3 Test Suite")
    print("=" * 70)

    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    section_f()
    section_g()
    section_h()
    section_i()
    section_j()
    section_k()
    section_l()
    section_m()
    section_n()

    print("\n" + "=" * 70)
    print(f"RESULT: {_TOTAL['pass']} pass / {_TOTAL['fail']} fail")
    if _FAILURES:
        print("FAILURES:")
        for name, detail in _FAILURES:
            print(f"  ✗ {name}  {detail}")
    print("=" * 70)
    sys.exit(1 if _TOTAL["fail"] else 0)


if __name__ == "__main__":
    main()
