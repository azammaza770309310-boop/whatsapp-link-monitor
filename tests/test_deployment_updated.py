#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PHASE 3 — Updated Deployment & Safety Guard Tests
==================================================
يعيد بناء الاختبارات القديمة لتناسب الـ contracts الجديدة.

التغييرات:
- لا يفترض أن bot.py مطابق لـ monitor_v12.py (تم إصلاح bugs بعدها)
- يستورد من bot بدلاً من monitor_v12
- يختبر الـ contracts الجديدة:
  - _send() → (bool, Optional[int])
  - JOINED_VERIFIED بدلاً من JOINED
  - Supabase بدلاً من SQLite watchers
  - update_queue_status مرة واحدة
"""
import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Setup
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('BOT_TOKEN', '123:test')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')
os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'testhash')
os.environ.setdefault('OWNER_ID', '12345')
os.environ.setdefault('SUPABASE_URL', '')
os.environ.setdefault('SUPABASE_KEY', '')

import logging
logging.disable(logging.CRITICAL)

RESULTS = []

def record(name, passed, detail=""):
    RESULTS.append({'name': name, 'passed': passed, 'detail': detail})
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if detail and not passed:
        print(f"         {detail}")


async def test_bot_py_exists_and_imports():
    """bot.py must exist and import cleanly."""
    print("\n--- bot.py Deployment ---")
    try:
        bot_path = PROJECT_ROOT / "bot.py"
        record("DEPLOY-1: bot.py exists", bot_path.exists(),
               f"path={bot_path}")

        if 'bot' in sys.modules:
            del sys.modules['bot']
        import bot
        record("DEPLOY-2: bot.py imports cleanly", True, "")

        # Check required components
        required = ['Monitor', 'Config', 'DatabaseManager', 'AIAnalyzer',
                    'MessageFormatter', 'HelpRequestDetector', 'HistoryScanner',
                    'health_handler', 'ready_handler', 'metrics_handler',
                    'start_http_server', 'main']
        missing = [name for name in required if not hasattr(bot, name)]
        record("DEPLOY-3: All required components present", len(missing) == 0,
               f"missing={missing}" if missing else "")

    except Exception as e:
        record("bot.py deployment test", False, f"Exception: {e}")


async def test_bot_py_has_phase2_contracts():
    """bot.py must contain PHASE 2 contract fixes."""
    print("\n--- PHASE 2 Contracts in bot.py ---")
    try:
        source = (PROJECT_ROOT / "bot.py").read_text(encoding='utf-8')

        # _send returns tuple
        record("PHASE2-1: _send returns Tuple[bool, Optional[int]]",
               "Tuple[bool, Optional[int]]" in source,
               "return type annotation")

        # JOINED_VERIFIED exists
        record("PHASE2-2: JOINED_VERIFIED status exists",
               '"JOINED_VERIFIED"' in source,
               "verified join status")

        # JOIN_UNVERIFIED exists
        record("PHASE2-3: JOIN_UNVERIFIED status exists",
               '"JOIN_UNVERIFIED"' in source,
               "unverified join status")

        # Never returns plain "JOINED"
        record("PHASE2-4: Never returns plain 'JOINED'",
               'return True, "JOINED"' not in source,
               "no plain JOINED return")

        # _verify_membership method exists
        record("PHASE2-5: _verify_membership method exists",
               "async def _verify_membership" in source,
               "verification method")

        # GetParticipantRequest used
        record("PHASE2-6: GetParticipantRequest used for verification",
               "GetParticipantRequest" in source,
               "membership verification")

        # No double update_queue_status
        record("PHASE2-7: Single final update_queue_status",
               "final_status" in source and "next_retry" in source,
               "single update with variables")

        # Worker state tracking
        record("PHASE2-8: Worker state tracking",
               "worker_state" in source,
               "worker supervisor")

        # PUBLISHED_VERIFIED
        record("PHASE2-9: PUBLISHED_VERIFIED exists",
               "PUBLISHED_VERIFIED" in source,
               "verified publish")

        # PUBLISH_FAILED
        record("PHASE2-10: PUBLISH_FAILED exists",
               "PUBLISH_FAILED" in source,
               "publish failure tracking")

    except Exception as e:
        record("PHASE 2 contracts test", False, f"Exception: {e}")


async def test_bot_py_no_sqlite_watchers():
    """bot.py must NOT use SQLite watchers table."""
    print("\n--- No SQLite Watchers ---")
    try:
        source = (PROJECT_ROOT / "bot.py").read_text(encoding='utf-8')

        record("NOWATCH-1: No CREATE TABLE watchers",
               "CREATE TABLE IF NOT EXISTS watchers" not in source,
               "no watchers table creation")

        record("NOWATCH-2: No SELECT FROM watchers",
               "FROM watchers" not in source,
               "no SQLite watchers queries")

        record("NOWATCH-3: No INSERT INTO watchers",
               "INTO watchers" not in source,
               "no SQLite watchers insert")

        record("NOWATCH-4: No UPDATE watchers",
               "UPDATE watchers" not in source,
               "no SQLite watchers update")

    except Exception as e:
        record("No SQLite watchers test", False, f"Exception: {e}")


async def test_safety_guard_no_double_counting():
    """Safety Guard must not double count via acquire."""
    print("\n--- Safety Guard: No Double Counting ---")
    try:
        source = (PROJECT_ROOT / "bot.py").read_text(encoding='utf-8')

        # Scheduler uses check() not acquire()
        # NOTE: variable is `jphone` inside the joiner-selection loop (PUBLISH-INCIDENT-1
        # fix moved the check inside the loop), or `phone` in older code — accept both.
        record("SG-1: Scheduler uses rate_limiter.check()",
               ("rate_limiter.check(jphone, 'join')" in source
                or "rate_limiter.check(phone, 'join')" in source),
               "check instead of acquire in scheduler")

        # _join_group_safe uses acquire()
        record("SG-2: _join_group_safe uses rate_limiter.acquire()",
               "rate_limiter.acquire(phone, 'join_channel')" in source,
               "acquire in join")

        # record_success exists
        record("SG-3: record_success called after join",
               "record_success(phone, 'join_channel')" in source,
               "record after success")

        # check() method exists in link_system
        link_src = (PROJECT_ROOT / "link_system.py").read_text(encoding='utf-8')
        record("SG-4: RateLimiter.check() method exists",
               "async def check(" in link_src,
               "check method in link_system")

        record("SG-5: RateLimiter.record_success() exists",
               "async def record_success(" in link_src,
               "record_success method")

    except Exception as e:
        record("Safety guard double counting test", False, f"Exception: {e}")


async def test_safety_guard_verification():
    """Safety Guard + Join must verify membership."""
    print("\n--- Safety Guard: Verification ---")
    try:
        source = (PROJECT_ROOT / "bot.py").read_text(encoding='utf-8')

        # _verify_membership catches UserNotParticipantError
        record("SG-6: _verify_membership catches UserNotParticipantError",
               "UserNotParticipantError" in source,
               "handles not participant")

        # _verify_membership catches ChannelPrivateError
        record("SG-7: _verify_membership catches ChannelPrivateError",
               "ChannelPrivateError" in source,
               "handles private channel")

        # _verify_membership catches FloodWaitError
        record("SG-8: _verify_membership catches FloodWaitError",
               "FloodWaitError" in source,
               "handles floodwait in verification")

        # Timeout in verification
        record("SG-9: _verify_membership has timeout",
               "timeout=15" in source,
               "15s timeout for verification")

    except Exception as e:
        record("Safety guard verification test", False, f"Exception: {e}")


async def test_link_system_contracts():
    """link_system.py must have PHASE 2 contracts."""
    print("\n--- link_system.py Contracts ---")
    try:
        source = (PROJECT_ROOT / "link_system.py").read_text(encoding='utf-8')

        # RateLimiter.check exists
        record("LINK-1: RateLimiter.check() exists",
               "async def check(" in source,
               "check method")

        # RateLimiter.record_success exists
        record("LINK-2: RateLimiter.record_success() exists",
               "async def record_success(" in source,
               "record_success method")

        # acquire does NOT log to DB (only record_success does)
        # Check that log_operation is in record_success, not in acquire
        record("LINK-3: log_operation in record_success (not acquire)",
               "async def record_success" in source and
               "await self.db.log_operation(phone, 'join')" in source and
               "# === RESERVE ONLY — do NOT log_operation yet ===" in source,
               "log_operation only in record_success")

        # MembershipCache distinguishes User
        record("LINK-4: MembershipCache checks for User entity",
               "is_user" in source,
               "user entity detection")

        # User entity returns None (not True)
        record("LINK-5: User entity returns None",
               "return None  # Not applicable" in source,
               "None for users")

    except Exception as e:
        record("link_system contracts test", False, f"Exception: {e}")


async def test_html_injection_protection():
    """bot.py must block HTML injection."""
    print("\n--- HTML Injection Protection ---")
    try:
        if 'bot' in sys.modules:
            del sys.modules['bot']
        import bot
        from datetime import datetime

        malicious = '<script>alert(1)</script>'
        html = bot.MessageFormatter.format_link_message(
            group_name=malicious,
            sender_name="user",
            sender_contact="",
            message_date=datetime.now(),
            link="https://chat.whatsapp.com/ABC",
            message_text="text",
            source_phone="+966",
        )
        record("HTML-1: Script tag escaped", "<script>" not in html,
               "no raw script tag")
        record("HTML-2: Escaped version present", "&lt;script&gt;" in html,
               "escaped tag present")

    except Exception as e:
        record("HTML injection test", False, f"Exception: {e}")


async def test_database_dedup():
    """DatabaseManager must deduplicate correctly."""
    print("\n--- Database Dedup ---")
    try:
        if 'bot' in sys.modules:
            del sys.modules['bot']
        import bot
        from datetime import datetime
        from unittest.mock import AsyncMock, patch

        async def go():
            db = bot.DatabaseManager(tempfile.mktemp(suffix=".db"))
            await db.init_db()
            try:
                with patch.object(db, '_supabase_insert_link', new=AsyncMock()):
                    with patch.object(db, '_get_supabase_session', new=AsyncMock(return_value=None)):
                        r1 = await db.insert_request(
                            link="https://chat.whatsapp.com/DEDUP1",
                            message_date=datetime.now(),
                            group_name="g", sender_name="s", source_phone="+966",
                            link_type="whatsapp")
                        r2 = await db.insert_request(
                            link="https://chat.whatsapp.com/DEDUP1",
                            message_date=datetime.now(),
                            group_name="g", sender_name="s", source_phone="+966",
                            link_type="whatsapp")
                return r1, r2
            finally:
                await db.close()

        r1, r2 = await go()
        record("DEDUP-1: First insert succeeds", r1 == True,
               f"r1={r1}")
        record("DEDUP-2: Duplicate rejected", r2 == False,
               f"r2={r2}")

    except Exception as e:
        record("Database dedup test", False, f"Exception: {e}")


async def main():
    print("=" * 70)
    print("  PHASE 3 — UPDATED DEPLOYMENT & SAFETY GUARD TESTS")
    print("=" * 70)

    await test_bot_py_exists_and_imports()
    await test_bot_py_has_phase2_contracts()
    await test_bot_py_no_sqlite_watchers()
    await test_safety_guard_no_double_counting()
    await test_safety_guard_verification()
    await test_link_system_contracts()
    await test_html_injection_protection()
    await test_database_dedup()

    print("\n" + "=" * 70)
    passed = sum(1 for r in RESULTS if r['passed'])
    failed = sum(1 for r in RESULTS if not r['passed'])
    total = len(RESULTS)
    print(f"  RESULTS: {passed}/{total} passed, {failed} failed")
    print("=" * 70)

    if failed > 0:
        print("\n❌ FAILED TESTS:")
        for r in RESULTS:
            if not r['passed']:
                print(f"   ❌ {r['name']}: {r['detail']}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
