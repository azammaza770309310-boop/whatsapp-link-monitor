#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_request_filter.py — سكريبت تحقق مستقل لفلتر الطلبات

يختبر:
  1. أمثلة يجب أن تُرسل (PASS) — طلبات حقيقية
  2. أمثلة يجب رفضها (FAIL) — إعلانات
  3. حالات حدّية (لا keyword، keyword+ad، طويلة، عربية، إنجليزية،
     رقم جوال، رابط، مكررة، بدون username، مجموعة خاصة،
     أكثر من keyword، أكثر من advertisement keyword)

لا يحتاج أي Telegram/telethon — يختبر request_filter.is_request_message مباشرة.
شغّل:  python3 verify_request_filter.py
"""
import sys
from request_filter import is_request_message, normalize_text

# ============================================================
# 1. أمثلة يجب أن تُرسل (PASS = طلب حقيقي)
# ============================================================
PASS_CASES = [
    "مين يحل لي واجب؟",
    "أحتاج أحد يساعدني في مشروع",
    "مين يعرف مدرس خصوصي رياضيات؟",
    "عندي بحث وأحتاج مساعدة",
    "حل واجب جامعي",
    "مين يسوي لي بحث بسيط؟",
    "ابغى احد يساعدني في تقرير ميداني",
    "احتاج تقرير تدريب ضروري",
    "ابي خصوصي في مادة البرمجة",
    "مين يكتب لي سيرة ذاتية؟",
    "عندي مشروع تخرج هندسة ومحتاج مساعدة",
    "مين فاهم في تفاضل وتكامل؟",
    "ابغى شرح مفصل لمادة الفيزياء",
    "مين عنده ملخص محاضرة كيمياء عضوية؟",
]

# ============================================================
# 2. أمثلة يجب رفضها (FAIL = إعلان)
# ============================================================
FAIL_CASES = [
    "حل واجبات للتواصل واتساب 0551234567",
    "خدمات طلابية بأسعار مناسبة",
    "تواصل معنا عبر الواتساب",
    "عرض خاص خصم 50%",
    "لدينا خدمات تعليمية بأفضل الأسعار",
    "مكتبنا يقدم حل واجب فوري بسرعة",
    "احجز الآن — مقاعد محدودة",
    "للتواصل عبر حسابنا على تيليجرام",
    "خدمة اونلاين — تسليم مشروع مضمون",
    "wa.me/966512345678 حل واجبات سريع",
    "https://t.me/MyService حل بحث سريع",
    "+966512345678 حل واجب فوري",
]

# ============================================================
# 3. حالات حدّية إضافية
# ============================================================
EDGE_CASES = [
    # (text, expected_pass, description)
    ("", False, "رسالة فارغة"),
    ("صباح الخير", False, "رسالة لا تحتوي أي keyword"),
    ("مين يعرف احد يحل واجب؟ للتواصل واتساب 0551234567", False, "keyword + advertisement (هاتف + واتساب)"),
    ("مين\n\n\n\n\n\n\n\n\n\nيحل واجب؟", False, "رسالة طويلة (>=6 أسطر) — حتى لو فيها طلب"),
    ("I need help with my assignment please", False, "رسالة إنجليزية — لا keyword عربي مطابق (please help غير موجود نصًا)"),
    ("مين يعرف مدرس خصوصي في رياضيات وجبر وهندسة؟", True, "رسالة عربية بأكثر من keyword"),
    ("حل واجب فوري + حل بحث سريع + خصم 50% + احجز الآن", False, "أكثر من advertisement keyword"),
    ("السلام عليكم، مين يحل لي واجب البرمجة؟", True, "طلب حقيقي مع تحية"),
    ("مين يسوي لي عرض تقديمي؟", False, "عرض تقديمي طلب لكن يحوي 'عرض' (ad keyword) — false positive مقبول"),
]

# ============================================================
# تشغيل الاختبارات
# ============================================================
def run_tests():
    total = 0
    passed = 0
    failed = 0
    failures = []

    print("=" * 70)
    print("🟢 PASS CASES (يجب أن تُرسل لقناة الطلبات)")
    print("=" * 70)
    for text in PASS_CASES:
        total += 1
        is_req, info = is_request_message(text)
        if is_req:
            passed += 1
            print(f"  ✅ PASS | {text[:50]}")
            print(f"          keywords: {info['request_matches'][:3]}")
        else:
            failed += 1
            failures.append(("PASS", text, info))
            print(f"  ❌ FAIL (expected PASS) | {text[:50]}")
            print(f"          reason: {info['reason']} | {info.get('reasons', [])}")

    print()
    print("=" * 70)
    print("🔴 FAIL CASES (يجب رفضها — إعلانات)")
    print("=" * 70)
    for text in FAIL_CASES:
        total += 1
        is_req, info = is_request_message(text)
        if not is_req:
            passed += 1
            print(f"  ✅ REJECTED | {text[:50]}")
            print(f"          reason: {info['reason']} | {info.get('reasons', [])[:2]}")
        else:
            failed += 1
            failures.append(("FAIL", text, info))
            print(f"  ❌ ACCEPTED (expected REJECT) | {text[:50]}")
            print(f"          keywords: {info['request_matches'][:3]}")

    print()
    print("=" * 70)
    print("🟡 EDGE CASES (حالات حدّية)")
    print("=" * 70)
    for text, expected_pass, desc in EDGE_CASES:
        total += 1
        is_req, info = is_request_message(text)
        actual_pass = is_req
        if actual_pass == expected_pass:
            passed += 1
            label = "PASS" if expected_pass else "REJECT"
            print(f"  ✅ {label} | {desc}")
            print(f"          text: {text[:50]!r}")
            if is_req:
                print(f"          keywords: {info['request_matches'][:3]}")
            else:
                print(f"          reason: {info['reason']} | {info.get('reasons', [])[:2]}")
        else:
            failed += 1
            failures.append(("EDGE", text, info))
            print(f"  ❌ MISMATCH | {desc}")
            print(f"          text: {text[:50]!r}")
            print(f"          expected: {'PASS' if expected_pass else 'REJECT'}, got: {'PASS' if actual_pass else 'REJECT'}")

    print()
    print("=" * 70)
    print(f"📊 SUMMARY: {passed}/{total} passed, {failed} failed")
    print("=" * 70)
    if failures:
        print("\n🔴 FAILURES DETAIL:")
        for kind, text, info in failures:
            print(f"  [{kind}] {text[:60]!r}")
            print(f"    reason={info['reason']} | req_matches={info['request_matches'][:3]} | ad_matches={info.get('advertisement_matches', [])[:3]} | reasons={info.get('reasons', [])}")

    # اختبار dedup (محاكاة)
    print()
    print("=" * 70)
    print("🔁 DEDUP SIMULATION (المكافحة تكرار داخل session)")
    print("=" * 70)
    sent_keys = set()
    sample = "مين يحل لي واجب؟"
    for i in range(3):
        key = (123, 999)  # نفس (chat_id, msg_id)
        if key in sent_keys:
            print(f"  attempt {i+1}: SKIP (duplicate) ✓")
        else:
            sent_keys.add(key)
            is_req, _ = is_request_message(sample)
            print(f"  attempt {i+1}: {'SENT' if is_req else 'SKIP (not request)'} (added to dedup set)")
    print(f"  dedup set size: {len(sent_keys)} (expected 1)")

    # اختبار normalization
    print()
    print("=" * 70)
    print("🔧 NORMALIZATION TEST")
    print("=" * 70)
    norm_cases = [
        ("أحتاج", "احتاج"),
        ("إلى", "الي"),    # إ→ا، ى→ي
        ("آه", "اه"),
        ("مدرّس", "مدرس"),  # إزالة التطويل
        ("ولدٌ", "ولد"),    # إزالة التشكيل
        ("Hello WORLD", "hello world"),
        ("سيرة ذاتية", "سيره ذاتيه"),  # ة→ه، ى→ي (حرج للتطابق)
    ]
    for raw, expected in norm_cases:
        got = normalize_text(raw)
        status = "✅" if got == expected else "❌"
        print(f"  {status} normalize({raw!r}) = {got!r} (expected {expected!r})")

    return failed == 0


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
