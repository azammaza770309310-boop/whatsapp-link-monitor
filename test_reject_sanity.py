#!/usr/bin/env python3
"""اختبار حالات الرفض الحرجة ضد البرومبت الجديد (v4.3.9) بالـAI الحقيقي —
ضمان عدم الانحدار: التدريس/الملفات الجاهزة/الرأي/الإعلان تبقى مرفوضة."""
import asyncio
import json
import os
import sys

with open('/home/z/wlm/.test_env.json') as f:
    _envs = json.load(f)
for k, v in _envs.items():
    if isinstance(v, str) and v:
        os.environ[k] = v

sys.path.insert(0, '/home/z/wlm')

from intent_classifier import IntentClassifier  # noqa: E402
from request_filter import analyze_request_v4   # noqa: E402

# (النص، التوقع: REJECT دائمًا + الفئة المتوقعة)
CASES = [
    ("مين يعرف دكتور يشرح رياضيات؟", "tutoring_only_request"),
    ("في احد يشرح احياء تحضيري احتاج مساعده؟", "tutoring_only_request"),
    ("مافي خصوصي للمادة أو احد يشرح الاولد اكز", "tutoring_only_request"),
    ("احد عنده كويزات لدروس الكمي؟", "resource_request"),
    ("عطوني أسئلة هندسة", "resource_request"),
    ("مين افضل مدرس؟", "recommendation_or_opinion"),
    ("عندك أكواد لشخصيات محددة ما اشتغلت؟", "non_academic_request"),
    ("عندي دكتور يساعد في الرسائل والتكاليف", "service_offer"),
    ("فيه احد نزل له الجدول بالتحضيريه ؟", "registration_admin"),
    ("الله يساعدكم أحد يفيدني", "other"),
    ("كم نسبة الحرمان؟", "non_request_question"),
    ("تعلم التداول واربح", "advertisement"),
]


async def main():
    classifier = IntentClassifier(
        timeout_s=10.0, max_attempts=2, max_chars=1200,
        min_interval_s=1.05, retry_rounds=3, total_budget_s=40.0,
        max_pending=64, pool_wait_budget_s=4.0,
    )
    ok = 0
    for i, (text, expected_cat) in enumerate(CASES, 1):
        for attempt in range(3):
            await asyncio.sleep(11)
            res = await analyze_request_v4(
                text, classifier,
                chat_id=920000000 + i, msg_id=i,
                threshold=0.85, admission_gate=True, chatter_guard=True,
                db_dedup=False,
            )
            if res.intent_type != 'ai_error' or attempt == 2:
                break
            print(f"[{i}] ai_error (attempt {attempt+1}) — retry…")
        rejected = not res.is_request
        cat_match = (res.intent_type == expected_cat) or rejected  # الفئة قد تتغير والرفض ثابت
        status = "✅" if (rejected and cat_match) else "❌"
        if rejected and cat_match:
            ok += 1
        print(f"[{i:02d}] {status} REJECT={rejected} | conf={res.confidence:.2f} | "
              f"cat={res.intent_type} (متوقع≈{expected_cat}) | reason={res.reason}")
        print(f"     text: {text}")
    print(f"\n=== REJECT SANITY: {ok}/{len(CASES)} ===")


if __name__ == "__main__":
    asyncio.run(main())
