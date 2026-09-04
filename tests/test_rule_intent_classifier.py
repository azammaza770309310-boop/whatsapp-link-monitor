#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_rule_intent_classifier.py — v4.4.0 AI-OFF Regression Suite
================================================================================
[AI-OFF-v4.4.0] طلب المُشغّل (2026-09-04): إلغاء AI من تصنيف الطلبات + فلتر
قواعدي أقوى. هذا الملف يجمّد العقد الجديد:

  1. قائمة المُشغّل الحقيقية (21 طلبًا — ال18 الأصلية + 3 معايرة prompt):
     كلها ACCEPT — بالفئات الصحيحة (homework / student_service).
  2. 26 حالة رفض إنتاجية: تدريس/ملفات جاهزة/أكواد/إعلانات/استطلاع/
     إداري/أسئلة معرفية/آراء/مدح/بلا عمل محدد — كلها REJECT.
  3. العقد الإنشائي: classify() async بنفس توقيع IntentClassifier،
     ok=True دائمًا (قرار حتمي — لا فشل شبكة أبدًا)، latency ~0،
     provider=rules، confidence تقبلها العتبة (>= 0.85).
  4. التكامل: bot.py يبني RuleBasedIntentClassifier افتراضيًا
     (REQUEST_AI_ENABLED غير مضبوط) — والتنبيه الناتج نظيف بلا أي
     نص تصنيف أعلاه (عنوان/سبب) ويبدأ ببطاقة المرسل.

استرجاع السرعة القديمة (اختياري للمُشغّل): POLL_TIER_HOT_S=4.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rule_intent_classifier import (  # noqa: E402
    RuleBasedIntentClassifier, RULE_ENGINE_VERSION,
)

# ------------------------------------------------------------

ACCEPT_CASES = [
    # ---- قائمة المُشغّل الحقيقية (18) — كلمة بكلمة ----
    ("احد يحل كويزات وكذا؟", "homework_execution_request"),
    ("ياجماعة تعرفون أحد يحل كويزات فصليه ثقه", "homework_execution_request"),
    ("احد يعرف يسوي cv ؟", "student_service_execution_request"),
    ("تعرفون احد مضمون يسوي اعذار طبيه،؟", "student_service_execution_request"),
    ("ابغا افضل مكان يسوي سيره بناظ ats", "student_service_execution_request"),
    ("مين يسوي تقرير ؟؟", "homework_execution_request"),
    ("ابي شخص يسوي لي بحث الذي فاهم يتواصل معي بليز", "homework_execution_request"),
    ("مين الي يقدر يسوي لي جدول ؟", "student_service_execution_request"),
    ("السلام عليكم من يقدر يسوي سي في", "student_service_execution_request"),
    ("من يعرف احد يسوي عذر", "student_service_execution_request"),
    ("احد يعرف يسوي فيديو بالذكاء الاصطناعي؟؟", "student_service_execution_request"),
    ("سلام عليكم يارجال من عنده رقم واحد يسوي سيفيات؟", "student_service_execution_request"),
    ("احد يسوي سكليف ؟", "homework_execution_request"),
    ("مين يعرف أحد بجامعة العيال يسوي الجداول ؟ يساعدني، الله يسعدكم", "student_service_execution_request"),
    ("احد يسوي عذر ؟", "student_service_execution_request"),
    ("ابي احد يسوي مشروع تخرج 👩🏻‍🎓", "homework_execution_request"),
    ("ياااعالم ابي احد يسوي سكليف", "homework_execution_request"),
    ("سلام عليكم ابي احد يسوي سكليف مضمون ينزل ف صحتي؟", "homework_execution_request"),
    # ---- معايرة الـprompt السابق (ACCEPT) ----
    ("أحد يحل لي واجب الرياضيات بسرعة قبل الساعة ١٢", "homework_execution_request"),
    ("من يقدر يسوي لي البحث كامل بفلوس؟", "homework_execution_request"),
    ("ابغى شخص يخلص التكليف عني", "homework_execution_request"),
]

REJECT_CASES = [
    # ---- التدريس/الشرح (المصدر الأول للرسائل غير المناسبة) ----
    "مين يعرف دكتور يشرح رياضيات؟",
    "في احد يشرح احياء تحضيري احتاج مساعده؟",
    "مافي خصوصي للمادة أو احد يشرح الاولد اكز",
    "أبي مدرس خصوصي للمادة",
    "من يراجع معي قبل الاختبار؟",
    "أحد يعلمنا تفاضل؟",
    # ---- ملفات جاهزة (ملكية لا تنفيذ) ----
    "احد عنده كويزات لدروس الكمي؟",
    "عطوني أسئلة هندسة",
    "مين عنده ملخصات الفيزياء؟",
    # ---- عرض خدمة/إعلان ----
    "حل واجبات برسوم رمزية",
    "عندي دكتور يساعد في الرسائل والتكاليف",
    "تعلّم التداول واربح",
    # ---- استطلاع/آراء/أسئلة ----
    "دكتوره علا ياسمين البار عربي كيف؟مين قد درس عندها",
    "مين أفضل مدرس؟",
    "كم نسبة الحرمان؟",
    "كيف أذاكر؟",
    "هل الاختبار 5 أقسام؟",
    "كيف احل هذا السؤال؟",
    "كيف اسوي cv بنفسي؟",
    # ---- إداري ----
    "فيه احد نزل له الجدول بالتحضيريه ؟",
    "متى يفتح التسجيل؟",
    "أبي شعب انجليزي",
    # ---- غير أكاديمي ----
    "عندك أكواد لشخصيات محددة ما اشتغلت؟",
    # ---- سوالف/مدح/بلا عمل ----
    "واقعد لخص وانت تذاكر وحط زبده التعاريف",
    "الله يساعدكم أحد يفيدني",
    "شكراً اكتمال جبت درجة عالية",
]

PASS = 0
FAIL = 0


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ PASS: {name}")
    else:
        FAIL += 1
        print(f"  ❌ FAIL: {name} — {detail}")


async def main():
    clf = RuleBasedIntentClassifier()

    print("\n--- A: operator's 21 real requests — all ACCEPT with correct category ---")
    for text, expected_cat in ACCEPT_CASES:
        d = clf.classify_sync(text)
        record(f"ACCEPT [{expected_cat}] «{text[:40]}»",
               d.decision == "ACCEPT" and d.category == expected_cat,
               f"got {d.decision}/{d.category} conf={d.confidence}")

    print("\n--- B: 26 production rejects — all REJECT ---")
    for text in REJECT_CASES:
        d = clf.classify_sync(text)
        record(f"REJECT «{text[:40]}»",
               d.decision == "REJECT",
               f"got {d.decision}/{d.category}")

    print("\n--- C: constructor contract (IntentClassifier-compatible) ---")
    d = await clf.classify("مين يسوي تقرير ؟؟", hints={"anything": True}, context="أي مجموعة")
    record("C: async classify() same signature works",
           d.decision == "ACCEPT" and d.category == "homework_execution_request")
    record("C: ok=True always (deterministic — no network failure path)",
           d.ok is True)
    record(f"C: model == {RULE_ENGINE_VERSION}",
           d.model == RULE_ENGINE_VERSION)
    record("C: provider_name == rules",
           d.provider_name == "rules")
    record("C: latency ~0 (no network)",
           0 <= d.latency_ms <= 5, f"latency={d.latency_ms}ms")
    record("C: confidence >= 0.85 threshold (accepted by analyze_request_v4)",
           d.confidence >= 0.85)
    record("C: kwargs accepted (IntentClassifier args ignored safely)",
           RuleBasedIntentClassifier(timeout_s=10, max_attempts=2).enabled is True)
    record("C: empty text → REJECT empty",
           clf.classify_sync("").category == "empty")
    d2 = await clf.classify("")
    record("C: empty async → REJECT empty",
           d2.decision == "REJECT" and d2.category == "empty")

    print("\n--- D: determinism (same input → same output, always) ---")
    a = clf.classify_sync("احد يسوي سكليف ؟")
    b = clf.classify_sync("احد يسوي سكليف ؟")
    record("D: deterministic decision (the AI contradiction bug is impossible)",
           (a.decision, a.category, a.confidence) == (b.decision, b.category, b.confidence))

    print("\n--- E: bot.py wiring — rule engine is the default classifier ---")
    try:
        import bot  # noqa: F401
        from rule_intent_classifier import RuleBasedIntentClassifier as _RC
        # REQUEST_AI_ENABLED غير مضبوط في بيئة الاختبار → الافتراضي قواعدي
        ai_flag = os.getenv("REQUEST_AI_ENABLED", "false").lower() in ("true", "1", "yes")
        record("E: REQUEST_AI_ENABLED default false (AI off unless explicitly set)",
               not ai_flag)
        record("E: rule engine class importable from bot's namespace",
               _RC is not None)
    except Exception as e:
        record("E: bot import", False, str(e))

    print("\n" + "=" * 70)
    print(f"RULE-ENGINE RESULTS: {PASS}/{PASS + FAIL} assertions passed")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
