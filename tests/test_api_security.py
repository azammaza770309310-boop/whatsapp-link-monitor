#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API Security (Dashboard fail-closed) — Tests [PR-7]
=====================================================
السيناريو 13: API بدون key → لا يسمح بالوصول غير المصرح به إلى dashboard/internal APIs.
+ مع key صحيح → يسمح.
+ key خاطئ → 401.
+ /health /ready /metrics مفتوحة دائماً (probes).
+ API_FAIL_OPEN=true → مسارات /api/* مفتوحة (transition mode).
"""
import asyncio
import os
import sys
import logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('BOT_TOKEN', '123:test')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')
os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'testhash')
os.environ.setdefault('OWNER_ID', '12345')
os.environ.setdefault('SUPABASE_URL', '')
os.environ.setdefault('SUPABASE_KEY', '')

# We need to control DASHBOARD_API_KEY + API_FAIL_OPEN per-test, so we manipulate
# os.environ directly and re-import the module functions fresh each time.
logging.disable(logging.CRITICAL)

import importlib
import bot

RESULTS = []


def record(name, passed, detail=""):
    RESULTS.append({'name': name, 'passed': passed, 'detail': detail})
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if detail and not passed:
        print(f"         {detail}")


class FakeRequest:
    def __init__(self, path, headers=None):
        self.path = path
        self.headers = headers or {}


class FakeHandler:
    """A fake handler that just records it was called."""
    def __init__(self):
        self.called = False
    async def __call__(self, request):
        self.called = True
        from aiohttp import web
        return web.json_response({"ok": True}, status=200)


async def _run_middleware(path, headers=None, key_env=None, fail_open_env=None):
    """Run dashboard_api_key_middleware with controlled env + return (status, called)."""
    # Save + set env
    saved_key = os.environ.get('DASHBOARD_API_KEY')
    saved_fo = os.environ.get('API_FAIL_OPEN')
    if key_env is None:
        os.environ.pop('DASHBOARD_API_KEY', None)
    else:
        os.environ['DASHBOARD_API_KEY'] = key_env
    if fail_open_env is None:
        os.environ.pop('API_FAIL_OPEN', None)
    else:
        os.environ['API_FAIL_OPEN'] = fail_open_env
    # Reset the one-time-warning latch so warnings re-emit deterministically
    bot._DASHBOARD_API_KEY_WARNED["open"] = False
    try:
        req = FakeRequest(path, headers=headers)
        handler = FakeHandler()
        resp = await bot.dashboard_api_key_middleware(req, handler)
        # resp may be a Response (rejected) or whatever handler returns
        status = getattr(resp, 'status', None)
        return status, handler.called
    finally:
        # restore
        if saved_key is None: os.environ.pop('DASHBOARD_API_KEY', None)
        else: os.environ['DASHBOARD_API_KEY'] = saved_key
        if saved_fo is None: os.environ.pop('API_FAIL_OPEN', None)
        else: os.environ['API_FAIL_OPEN'] = saved_fo


async def test_13_api_without_key_rejected():
    print("\n--- Test 13: /api/* without DASHBOARD_API_KEY → 401 (fail-closed) ---")
    # /api/stats without key, no API_FAIL_OPEN → 401
    status, called = await _run_middleware("/api/stats")
    record("13: /api/stats no key → 401",
           status == 401, f"got status={status}")
    record("13: handler NOT called on rejection",
           not called, "handler was called (security breach)")

    # /api/joined_groups without key → 401
    status2, _ = await _run_middleware("/api/joined_groups")
    record("13: /api/joined_groups no key → 401",
           status2 == 401, f"got status={status2}")

    # /api/deploy_check without key → 401
    status3, _ = await _run_middleware("/api/deploy_check")
    record("13: /api/deploy_check no key → 401",
           status3 == 401, f"got status={status3}")


async def test_api_with_correct_key_allowed():
    print("\n--- Test: /api/* with correct X-Api-Key → 200 (handler called) ---")
    status, called = await _run_middleware(
        "/api/stats",
        headers={"X-Api-Key": "secret123"},
        key_env="secret123")
    record("with correct key → 200 (not 401)",
           status != 401, f"got status={status}")
    record("with correct key → handler called",
           called, "handler not called")


async def test_api_with_wrong_key_rejected():
    print("\n--- Test: /api/* with wrong X-Api-Key → 401 ---")
    status, called = await _run_middleware(
        "/api/stats",
        headers={"X-Api-Key": "wrong"},
        key_env="secret123")
    record("with wrong key → 401",
           status == 401, f"got status={status}")
    record("with wrong key → handler NOT called",
           not called, "handler called (breach)")


async def test_health_endpoints_always_open():
    print("\n--- Test: /health /ready /metrics always open (no key needed) ---")
    for path in ("/health", "/ready", "/metrics"):
        status, called = await _run_middleware(path)
        record(f"{path} no key → handler called (open probe)",
               called, f"got status={status}, called={called}")


async def test_api_fail_open_escape_hatch():
    print("\n--- Test: API_FAIL_OPEN=true → /api/* open (transition mode) ---")
    status, called = await _run_middleware(
        "/api/stats", fail_open_env="true")
    record("API_FAIL_OPEN=true → handler called (open transition)",
           called, f"got status={status}, called={called}")


async def test_secrets_not_in_logs():
    print("\n--- Test 14: secrets do not appear in logs/source ---")
    # Verify the dashboard key itself is never logged (only the warning about it being unset)
    import io
    log_buf = io.StringIO()
    handler = logging.StreamHandler(log_buf)
    handler.setLevel(logging.WARNING)
    logger = logging.getLogger()
    saved_level = logger.level
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        os.environ['DASHBOARD_API_KEY'] = 'super-secret-key-12345'
        bot._DASHBOARD_API_KEY_WARNED["open"] = False
        bot._warn_dashboard_api_key_open_once()  # key IS set → no warning
        os.environ.pop('DASHBOARD_API_KEY', None)
        bot._DASHBOARD_API_KEY_WARNED["open"] = False
        bot._warn_dashboard_api_key_open_once()  # key unset → warning (no value)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(saved_level)
        os.environ.pop('DASHBOARD_API_KEY', None)
    log_text = log_buf.getvalue()
    record("14: secret key value NOT logged",
           'super-secret-key-12345' not in log_text,
           f"key leaked in log: {log_text!r}")


async def main():
    print("=" * 70)
    print("API Security (Dashboard fail-closed) — Test Suite [PR-7]")
    print("=" * 70)
    await test_13_api_without_key_rejected()
    await test_api_with_correct_key_allowed()
    await test_api_with_wrong_key_rejected()
    await test_health_endpoints_always_open()
    await test_api_fail_open_escape_hatch()
    await test_secrets_not_in_logs()
    print("\n" + "=" * 70)
    passed = sum(1 for r in RESULTS if r['passed'])
    failed = sum(1 for r in RESULTS if not r['passed'])
    print(f"RESULTS: {passed}/{passed + failed} passed, {failed} failed")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
