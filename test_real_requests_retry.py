#!/usr/bin/env python3
"""إعادة اختبار الطلبات التي فشلت بـai_error (اصطدام معدل) بتباعد أطول."""
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

REQUESTS = [
    "ياجماعة تعرفون أحد يحل كويزات فصليه ثقه",
    "احد يعرف يسوي cv ؟",
    "تعرفون احد مضمون يسوي اعذار طبيه،؟",
    "سلام عليكم يارجال من عنده رقم واحد يسوي سيفيات؟",
    "مين يعرف أحد بجامعة العيال يسوي الجداول ؟ يساعدني، الله يسعدكم",
    "احد يسوي عذر ؟",
]


async def main():
    classifier = IntentClassifier(
        timeout_s=10.0, max_attempts=2, max_chars=1200,
        min_interval_s=1.05, retry_rounds=3, total_budget_s=40.0,
        max_pending=64, pool_wait_budget_s=4.0,
    )
    for i, text in enumerate(REQUESTS, 1):
        for attempt in range(3):  # إعادة محاولة عند ai_error العابر
            await asyncio.sleep(12)
            res = await analyze_request_v4(
                text, classifier,
                chat_id=910000000 + i, msg_id=i,
                threshold=0.85, admission_gate=True, chatter_guard=True,
                db_dedup=False,
            )
            if res.intent_type != 'ai_error' or attempt == 2:
                verdict = "ACCEPT ✅" if res.is_request else "REJECT ❌"
                print(f"[{i}] {verdict} | conf={res.confidence:.2f} | "
                      f"cat={res.intent_type} | reason={res.reason}")
                print(f"    text: {text}")
                break
            print(f"[{i}] ai_error (attempt {attempt+1}) — retrying…")


if __name__ == "__main__":
    asyncio.run(main())
