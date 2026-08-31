#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_request_filter_v4.py — LIVE verification for Request Intent Engine v4.0
================================================================================
يُشغَّل في بيئة فيها مفاتيح AI حقيقية (Render/local .env) للتحقق المباشر من
سلوك المصنّف الفعلي (النموذج الحقيقي — ليس الـmock) على:

  1. الحالات الإلزامية التسع (طلب المُشغّل حرفيًا):
     ❌ REJECT: حين يحبك الله… / عندي دكتور يساعد… / تعلم التداول واربح /
               شكراً اكتمال جبت درجة عالية / مين افضل مدرس؟
     ✅ ACCEPT: أحد يشرح لي تفاضل 1 / احتاج شخص يحل معي السؤال /
               مين يعرف دكتور يشرح رياضيات؟ / أبي مدرس خصوصي للمادة
  2. عيّنة من corpus الانحدار (كل فئة).

يقرأ نفس متغيرات AIAnalyzer (OPENAI_API_KEY / AI_URL_i / AI_MODEL_i / AI_KEY_2..8).
بلا مفاتيح → يُطبع SKIP واضح (لا يدّعي نجاحًا).

شغّل:  python3 verify_request_filter_v4.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from intent_classifier import IntentClassifier, load_providers_from_env
from request_filter import analyze_request_v4, FILTER_VERSION, FILTER_MODE

MANDATORY = [
    # (text, expected_accept, label)
    ("حين يحبك الله يبدل وجه الحياة...", False, "ديني/عام"),
    ("عندي دكتور يساعد في الرسائل والتكاليف", False, "عرض خدمات"),
    ("تعلم التداول واربح", False, "إعلان"),
    ("شكراً اكتمال جبت درجة عالية", False, "مدح/تجربة"),
    ("مين افضل مدرس؟", False, "توصية/رأي"),
    ("أحد يشرح لي تفاضل 1", True, "طلب شرح"),
    ("احتاج شخص يحل معي السؤال", True, "طلب حل"),
    ("مين يعرف دكتور يشرح رياضيات؟", True, "بحث عن مدرس"),
    ("أبي مدرس خصوصي للمادة", True, "مدرس خصوصي"),
]

EXTRA = [
    ("مين يسوي لي بحث تخرج؟", True, "corpus ACCEPT"),
    ("كم نسبة الحرمان؟", False, "سؤال معلوماتي"),
    ("بوت خصوصي للتواصل واتساب", False, "إعلان"),
    ("محتاج أحد ينجز مشروعي", True, "corpus ACCEPT"),
    ("مدري ليه جابوا فلان", False, "نقاش عام"),
]


async def main():
    print("=" * 72)
    print(f"REQUEST-FILTER {FILTER_VERSION} ({FILTER_MODE}) — LIVE verification")
    print("=" * 72)

    providers = load_providers_from_env()
    if not providers:
        print("SKIP: لا مفاتيح AI في البيئة (OPENAI_API_KEY / AI_KEY_2..8).")
        print("      هذا الفحص يحتاج النموذج الحقيقي — شغّله على Render أو مع .env")
        return 0

    print(f"providers: {len(providers)} | model: {providers[0].get('model')}")
    print()
    cl = IntentClassifier(providers=providers, timeout_s=15, max_attempts=2)

    passed = failed = 0
    for text, expect, label in MANDATORY + EXTRA:
        r = await analyze_request_v4(text, cl, threshold=0.85)
        got = r.is_request
        ok = (got == expect)
        mark = "✅" if ok else "❌"
        passed += ok
        failed += (not ok)
        print(f"  {mark} [{'ACCEPT' if got else 'REJECT'}] conf={r.confidence:.2f} "
              f"cat={r.intent_type} reason={r.reason} model={r.ai_model} "
              f"({r.ai_latency_ms}ms) | {label} | {text[:40]}")

    print("\n" + "=" * 72)
    print(f"LIVE RESULT: {passed}/{passed + failed} correct "
          f"({passed / max(1, passed + failed) * 100:.0f}%)")
    print(f"stats: {cl.stats()}")
    print("=" * 72)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
