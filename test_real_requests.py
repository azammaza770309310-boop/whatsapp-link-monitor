#!/usr/bin/env python3
"""[TEST-REAL-REQUESTS] اختبار المصنّف الحقيقي (نفس مزوّدي وبرومبت الإنتاج)
على 18 طلبًا حقيقيًا جمعها المُشغّل من مجموعات المراقبة.

- نفس env vars الخاصة بالإنتاج (جُلبت من Render env-vars).
- نفس بارامترات IntentClassifier في bot.py:3139 (timeout 10s, attempts 2,
  budget 40s, pending 64, pool_wait 4s, retry_rounds 3).
- نفس مسار analyze_request_v4 الإنتاجي: threshold=0.85, admission_gate=True,
  chatter_guard=True. (بلا dedupers — كل نص فريد.)
- الإخراج: جدول قرار لكل طلب (ACCEPT/REJECT + الثقة + الفئة + السبب + المزوّد).
"""
import asyncio
import json
import os
import sys

# --- تحميل بيئة الإنتاج (مفاتيح AI) قبل استيراد المصنّف ---
with open('/home/z/wlm/.test_env.json') as f:
    _envs = json.load(f)
for k, v in _envs.items():
    if isinstance(v, str) and v:
        os.environ[k] = v

sys.path.insert(0, '/home/z/wlm')

from intent_classifier import IntentClassifier  # noqa: E402
from request_filter import analyze_request_v4   # noqa: E402

# --- الطلبات الحقيقية الـ18 من رسالة المُشغّل (2026-09-03) ---
REQUESTS = [
    "احد يحل كويزات وكذا؟",
    "ياجماعة تعرفون أحد يحل كويزات فصليه ثقه",
    "احد يعرف يسوي cv ؟",
    "تعرفون احد مضمون يسوي اعذار طبيه،؟",
    "ابغا افضل مكان يسوي سيره بناظ ats",
    "مين يسوي تقرير ؟؟",
    "ابي شخص يسوي لي بحث الذي فاهم يتواصل معي بليز",
    "مين الي يقدر يسوي لي جدول ؟",
    "السلام عليكم من يقدر يسوي سي في",
    "من يعرف احد يسوي عذر",
    "احد يعرف يسوي فيديو بالذكاء الاصطناعي؟؟",
    "سلام عليكم يارجال من عنده رقم واحد يسوي سيفيات؟",
    "احد يسوي سكليف ؟",
    "مين يعرف أحد بجامعة العيال يسوي الجداول ؟ يساعدني، الله يسعدكم",
    "احد يسوي عذر ؟",
    "ابي احد يسوي مشروع تخرج 👩🏻‍🎓",
    "ياااعالم ابي احد يسوي سكليف",
    "سلام عليكم ابي احد يسوي سكليف مضمون ينزل ف صحتي؟",
]

# التوقع المرجعي من منظور المُشغّل (طلب تفويض عمل بدلاً عن الطالب):
EXPECTED = {
    0: "ACCEPT",   # احد يحل كويزات — تنفيذ
    1: "ACCEPT",   # يحل كويزات فصليه — تنفيذ
    2: "ACCEPT?",  # يسوي cv — تفويض لكن غير أكاديمي صرف
    3: "ACCEPT?",  # اعذار طبيه — تفويض لكن غير أكاديمي
    4: "ACCEPT?",  # سيره ذاتيه ATS — تفويض لكن غير أكاديمي
    5: "ACCEPT",   # تقرير — أكاديمي
    6: "ACCEPT",   # بحث — أكاديمي
    7: "ACCEPT",   # جدول — خدمة طلابية
    8: "ACCEPT?",  # سي في — تفويض
    9: "ACCEPT?",  # عذر — تفويض
    10: "ACCEPT?", # فيديو AI — تفويض
    11: "ACCEPT?", # سيفيات — تفويض
    12: "ACCEPT",  # سكليف (واجبات) — أكاديمي
    13: "ACCEPT",  # جداول جامعة — خدمة طلابية
    14: "ACCEPT?", # عذر
    15: "ACCEPT",  # مشروع تخرج — أكاديمي
    16: "ACCEPT",  # سكليف
    17: "ACCEPT",  # سكليف
}


async def main():
    classifier = IntentClassifier(
        timeout_s=10.0,
        max_attempts=2,
        max_chars=1200,
        min_interval_s=1.05,
        retry_rounds=3,
        total_budget_s=40.0,
        max_pending=64,
        pool_wait_budget_s=4.0,
    )
    print(f"providers: {len(classifier.providers)} — "
          f"{[p['name'] for p in classifier.providers]}")
    print(f"prompt: SYSTEM_PROMPT v{len(__import__('request_filter').FILTER_VERSION and '')}"
          f" FILTER_VERSION={__import__('request_filter').FILTER_VERSION}")
    print("=" * 100)

    results = []
    for i, text in enumerate(REQUESTS, 1):
        # تباعد بين النداءات: المفاتيح مشتركة مع الإنتاج الحي — احترام pacing
        # (groq floor 4.5s / mistral 3.0s لكل مفتاح) + تجنّب 429.
        await asyncio.sleep(7)
        res = await analyze_request_v4(
            text, classifier,
            chat_id=900000000 + i, msg_id=i,
            threshold=0.85,
            admission_gate=True,
            chatter_guard=True,
            db_dedup=False,
        )
        verdict = "ACCEPT ✅" if res.is_request else "REJECT ❌"
        results.append((i, text, verdict, res))
        print(f"[{i:02d}] {verdict} | conf={res.confidence:.2f} | "
              f"cat={res.intent_type} | path={res.decision_path}")
        print(f"     reason: {res.reason}")
        print(f"     model: {res.ai_model or '-'} provider={res.ai_provider or '-'} "
              f"latency={res.ai_latency_ms}ms")
        print(f"     text: {text[:70]}")
        print("-" * 100)

    # ملخص
    accepted = [r for r in results if r[3].is_request]
    rejected = [r for r in results if not r[3].is_request]
    print()
    print("=" * 60)
    print(f"ACCEPT: {len(accepted)}/{len(results)}")
    for i, text, v, r in accepted:
        print(f"  ✅ [{i:02d}] {text[:60]} (conf={r.confidence:.2f})")
    print(f"REJECT: {len(rejected)}/{len(results)}")
    for i, text, v, r in rejected:
        print(f"  ❌ [{i:02d}] {text[:60]} → {r.intent_type} ({r.reason})")
    print("=" * 60)

    # JSON للتحليل اللاحق
    out = [{
        "i": i, "text": text, "decision": "ACCEPT" if r.is_request else "REJECT",
        "confidence": r.confidence, "category": r.intent_type,
        "reason": r.reason, "path": r.decision_path,
        "model": r.ai_model, "provider": r.ai_provider,
        "expected": EXPECTED.get(i - 1, "?"),
    } for i, text, v, r in results]
    with open('/home/z/wlm/test_real_requests_results.json', 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved: test_real_requests_results.json")


if __name__ == "__main__":
    asyncio.run(main())
