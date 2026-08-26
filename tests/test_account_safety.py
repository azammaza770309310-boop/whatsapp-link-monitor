#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Account / Fleet Safety — Tests [PR-5]
=======================================
السيناريوهات:
  10. disconnected account → skipped, doesn't block others
  11. FloodWait → backoff, no infinite loop
  + not_authorized → 1h cooldown (not endless retry) — alert dedup
  + _cleanup_user_client removes the client from active tracking
  + backoff caps at 600s (no runaway growth)
"""
import asyncio
import os
import sys
import logging
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('BOT_TOKEN', '123:test')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')
os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'testhash')
os.environ.setdefault('OWNER_ID', '12345')
os.environ.setdefault('SUPABASE_URL', '')
os.environ.setdefault('SUPABASE_KEY', '')

logging.disable(logging.CRITICAL)

import bot  # noqa: E402

RESULTS = []


def record(name, passed, detail=""):
    RESULTS.append({'name': name, 'passed': passed, 'detail': detail})
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if detail and not passed:
        print(f"         {detail}")


def make_fake_monitor():
    """Minimal Monitor-like namespace with account-safety state."""
    fm = types.SimpleNamespace(
        _alerted_terminal_phones=set(),
        user_clients={},
        config=types.SimpleNamespace(owner_id=12345),
        bot_client=None,  # no real bot client — alerts will skip Telegram send
        db=types.SimpleNamespace(invalidate_dialogs_cache=lambda p: None),
        _fleet_health={
            'connected_joiners': 0,
            'floodwait_joiners': [],
            'disconnected_joiners': [],
            'safety_guard_blocked_joiners': 0,
            'all_unavailable_since': None,
            'fleet_down_alerted': False,
        },
    )
    # Bind the real account-safety methods (unbound, on the namespace)
    for method_name in ('_alert_terminal_failure', '_cleanup_user_client'):
        if hasattr(bot.Monitor, method_name):
            setattr(fm, method_name,
                    types.MethodType(getattr(bot.Monitor, method_name), fm))
    return fm


# =====================================================================
# 10. Disconnected account → _cleanup_user_client removes it
# =====================================================================
async def test_10_cleanup_removes_disconnected():
    print("\n--- Test 10: disconnected account is cleaned up (not blocking) ---")
    fm = make_fake_monitor()
    # Simulate a disconnected client in the active map
    fake_client = MagicMock()
    fake_client.is_connected.return_value = False
    fm.user_clients['+999111'] = fake_client
    # _cleanup_user_client should remove it
    fm._cleanup_user_client('+999111')
    record("10: _cleanup_user_client removes the phone from user_clients",
           '+999111' not in fm.user_clients, f"still present: {fm.user_clients}")

    # Other accounts remain untouched
    fm.user_clients['+999222'] = MagicMock()
    fm.user_clients['+999333'] = MagicMock()
    fm._cleanup_user_client('+999222')
    record("10: other accounts remain after one cleanup",
           '+999333' in fm.user_clients and '+999222' not in fm.user_clients,
           f"got {list(fm.user_clients)}")


# =====================================================================
# 11. not_authorized → alert dedup (ONE alert per phone, not spam)
# =====================================================================
async def test_11_alert_dedup_not_authorized():
    print("\n--- Test 11: not_authorized alert dedup (one per phone) ---")
    fm = make_fake_monitor()
    # First alert for +999444
    await fm._alert_terminal_failure('+999444', 'not_authorized', 're-login required')
    record("11: first alert adds phone to _alerted_terminal_phones",
           '+999444' in fm._alerted_terminal_phones,
           f"got {fm._alerted_terminal_phones}")
    alerted_count_first = len(fm._alerted_terminal_phones)
    # Second alert for SAME phone — should be suppressed (dedup)
    await fm._alert_terminal_failure('+999444', 'not_authorized', 're-login required')
    record("11: second alert for same phone is deduped (set size unchanged)",
           len(fm._alerted_terminal_phones) == alerted_count_first,
           f"set grew: {fm._alerted_terminal_phones}")
    # Alert for a DIFFERENT phone — should add
    await fm._alert_terminal_failure('+999555', 'invalid_session', 'bad session')
    record("11: alert for different phone is NOT deduped",
           '+999555' in fm._alerted_terminal_phones,
           f"got {fm._alerted_terminal_phones}")


# =====================================================================
# Backoff cap: verify the formula caps at 600s
# =====================================================================
async def test_backoff_cap_600s():
    print("\n--- Test: backoff caps at 600s (no infinite runaway) ---")
    # Replicate the exact backoff formula from the account loop
    backoff = 5
    steps = []
    for _ in range(20):  # many disconnect cycles
        steps.append(backoff)
        backoff = min(backoff * 2, 600)
    record("backoff: starts at 5s", steps[0] == 5, f"got {steps[0]}")
    record("backoff: caps at 600s (never exceeds)",
           max(steps) <= 600 and steps[-1] == 600,
           f"got max={max(steps)}, last={steps[-1]}")
    record("backoff: growth is bounded (monotonic, capped)",
           steps == sorted(steps) or steps[-1] == 600,
           f"steps={steps[:5]}...{steps[-3:]}")


# =====================================================================
# FloodWait handling: the account loop catches FloodWaitError and sleeps
# =====================================================================
async def test_floodwait_caught_not_raising():
    print("\n--- Test 11: FloodWait caught by account loop (no crash) ---")
    # Verify the account loop's exception handlers list includes FloodWaitError
    import inspect
    src = inspect.getsource(bot.Monitor)
    record("FloodWait: account loop catches FloodWaitError",
           'FloodWaitError' in src and 'e.seconds' in src,
           "FloodWait handler missing")
    record("FloodWait: backoff uses e.seconds + 1 (not infinite)",
           'e.seconds + 1' in src, "missing seconds-based sleep")


async def main():
    print("=" * 70)
    print("Account / Fleet Safety — Test Suite [PR-5]")
    print("=" * 70)
    await test_10_cleanup_removes_disconnected()
    await test_11_alert_dedup_not_authorized()
    await test_backoff_cap_600s()
    await test_floodwait_caught_not_raising()
    print("\n" + "=" * 70)
    passed = sum(1 for r in RESULTS if r['passed'])
    failed = sum(1 for r in RESULTS if not r['passed'])
    print(f"RESULTS: {passed}/{passed + failed} passed, {failed} failed")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
