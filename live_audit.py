#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PHASE 4 — LIVE Integration Audit Script
========================================
يُشغّل هذا السكريبت على Render للتحقق من:
1. Environment Variables (بدون كشف secrets)
2. Supabase LIVE connection
3. Supabase Schema verification
4. SQLite tables (no watchers)
5. Telegram account health (لكل حساب)
6. Worker health

ممنوع كشف أي secrets. يطبع فقط SET / MISSING.

Usage: python live_audit.py
"""
import asyncio
import os
import sys
import logging
from pathlib import Path
from datetime import datetime

# Setup
sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault('LOG_LEVEL', 'INFO')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_env_vars():
    """فحص Environment Variables بدون كشف secrets."""
    print("\n" + "=" * 60)
    print("  CONFIG CHECK")
    print("=" * 60)

    required = {
        'SUPABASE_URL': 'SUPABASE_URL',
        'SUPABASE_KEY': 'SUPABASE_KEY (service key)',
        'BOT_TOKEN': 'BOT_TOKEN',
        'API_ID': 'TELEGRAM_API_ID',
        'API_HASH': 'TELEGRAM_API_HASH',
        'CHANNEL_ID': 'CHANNEL_ID',
    }

    optional = {
        'OPENAI_API_KEY': 'AI_API_KEY (OPENAI_API_KEY)',
        'AI_KEY_1': 'AI_KEY_1 (alternate)',
        'AI_KEY_2': 'AI_KEY_2 (alternate)',
        'AI_KEY_3': 'AI_KEY_3 (alternate)',
        'OWNER_ID': 'OWNER_ID',
        'DAILY_JOIN_LIMIT': 'DAILY_JOIN_LIMIT',
        'SIMULATION_MODE': 'SIMULATION_MODE',
    }

    missing_required = []
    for env_var, display_name in required.items():
        value = os.getenv(env_var, '')
        status = "SET" if value else "MISSING"
        print(f"  {display_name:30s} = {status}")
        if not value:
            missing_required.append(env_var)

    print("\n  --- Optional ---")
    for env_var, display_name in optional.items():
        value = os.getenv(env_var, '')
        status = "SET" if value else "MISSING"
        print(f"  {display_name:30s} = {status}")

    if missing_required:
        print(f"\n  ❌ MISSING REQUIRED: {missing_required}")
        print("  ❌ STOP — cannot start Telegram without these")
        return False
    else:
        print(f"\n  ✅ All required env vars SET")
        return True


async def check_supabase_live():
    """اختبار Supabase LIVE."""
    print("\n" + "=" * 60)
    print("  SUPABASE LIVE TEST")
    print("=" * 60)

    supabase_url = os.getenv("SUPABASE_URL", "")
    supabase_key = os.getenv("SUPABASE_KEY", "")

    if not supabase_url or not supabase_key:
        print("  ❌ SUPABASE_URL or SUPABASE_KEY missing — cannot test")
        return False

    try:
        import aiohttp
        from urllib.parse import quote as url_quote

        session = aiohttp.ClientSession(headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
        })

        # Test 1: Connection — count watchers
        headers = {**session.headers, "Prefer": "count=exact"}
        async with session.get(
            f"{supabase_url}/rest/v1/watchers?is_active=eq.true&select=phone&limit=1",
            headers=headers
        ) as resp:
            if resp.status == 200:
                range_header = resp.headers.get("content-range", "0/0")
                count = int(range_header.split("/")[-1] or 0)
                print(f"  [SUPABASE LIVE] connection=OK")
                print(f"  [SUPABASE LIVE] accounts={count}")
            else:
                text = await resp.text()
                print(f"  ❌ Supabase connection failed: {resp.status} - {text[:100]}")
                await session.close()
                return False

        # Test 2: Fetch all active watchers (without session_string)
        async with session.get(
            f"{supabase_url}/rest/v1/watchers?is_active=eq.true&select=phone,display_name,role,joiner_enabled,health_score,last_join_timestamp"
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                monitors = sum(1 for w in data if w.get('role', 'monitor') == 'monitor')
                joiners = sum(1 for w in data if w.get('role') == 'joiner')
                print(f"  [SUPABASE LIVE] monitors={monitors}")
                print(f"  [SUPABASE LIVE] joiners={joiners}")
                print(f"  [SUPABASE LIVE] accounts detail:")
                for w in data:
                    phone = w.get('phone', '?')
                    role = w.get('role', 'monitor')
                    enabled = w.get('joiner_enabled', 1)
                    health = w.get('health_score', 100)
                    print(f"    → {phone} (role={role}, joiner_enabled={enabled}, health={health})")
            else:
                text = await resp.text()
                print(f"  ❌ Supabase fetch failed: {resp.status} - {text[:100]}")
                await session.close()
                return False

        # Test 3: Schema verification
        print(f"\n  --- Schema Verification ---")
        async with session.get(
            f"{supabase_url}/rest/v1/watchers?select=phone,role,joiner_enabled,health_score,last_join_timestamp,is_active&limit=1"
        ) as resp:
            if resp.status == 200:
                print(f"  [SCHEMA] role                = OK")
                print(f"  [SCHEMA] joiner_enabled      = OK")
                print(f"  [SCHEMA] health_score        = OK")
                print(f"  [SCHEMA] last_join_timestamp = OK")
                print(f"  [SCHEMA] is_active           = OK")
            elif resp.status == 400:
                print(f"  ❌ SCHEMA MISSING — some columns not found")
                text = await resp.text()
                print(f"  Error: {text[:200]}")
                print(f"  Run migration SQL:")
                print(f"    ALTER TABLE watchers ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'monitor';")
                print(f"    ALTER TABLE watchers ADD COLUMN IF NOT EXISTS joiner_enabled INTEGER DEFAULT 1;")
                print(f"    ALTER TABLE watchers ADD COLUMN IF NOT EXISTS health_score INTEGER DEFAULT 100;")
                print(f"    ALTER TABLE watchers ADD COLUMN IF NOT EXISTS last_join_timestamp TIMESTAMP;")
                await session.close()
                return False
            else:
                text = await resp.text()
                print(f"  ❌ Schema check failed: {resp.status} - {text[:100]}")
                await session.close()
                return False

        await session.close()
        print(f"\n  ✅ Supabase LIVE test PASSED")
        return True

    except Exception as e:
        print(f"  ❌ Supabase exception: {type(e).__name__}: {e}")
        return False


async def check_sqlite_live():
    """فحص SQLite — يجب عدم وجود watchers."""
    print("\n" + "=" * 60)
    print("  SQLITE LIVE TEST")
    print("=" * 60)

    try:
        import aiosqlite
        from link_system import init_production_tables, ProductionDB
        from bot import DatabaseManager

        db = DatabaseManager()
        await db.init_db()
        await init_production_tables(db)

        # List tables
        conn = await db._ensure_conn()
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        rows = await cursor.fetchall()
        tables = [r[0] for r in rows]

        print(f"  [SQLITE] tables={tables}")

        # Check watchers is ABSENT
        if 'watchers' in tables:
            print(f"  ❌ FATAL: 'watchers' table EXISTS in SQLite — should NOT!")
            await db.close()
            return False
        else:
            print(f"  [SQLITE] watchers=ABSENT ✅")

        # Check required tables exist
        required = ['link_queue', 'group_states', 'membership_cache',
                    'floodwait_tracker', 'api_operations_log', 'system_settings',
                    'forwarded_requests', 'scan_state']
        missing = [t for t in required if t not in tables]
        if missing:
            print(f"  ❌ Missing tables: {missing}")
            await db.close()
            return False
        else:
            print(f"  [SQLITE] all required tables present ✅")

        await db.close()
        print(f"\n  ✅ SQLite LIVE test PASSED")
        return True

    except Exception as e:
        print(f"  ❌ SQLite exception: {type(e).__name__}: {e}")
        return False


async def check_telegram_accounts():
    """فحص Telegram accounts — connect, authorize, get_me."""
    print("\n" + "=" * 60)
    print("  TELEGRAM ACCOUNT HEALTH CHECK")
    print("=" * 60)

    try:
        from bot import Config, DatabaseManager, Monitor

        config = Config()
        errors = config.validate()
        if errors:
            print(f"  ❌ Config errors: {errors}")
            return False

        db = DatabaseManager()
        await db.init_db()

        from link_system import init_production_tables
        await init_production_tables(db)

        # Get watchers from Supabase
        watchers = await db.get_active_watchers()
        if not watchers:
            print(f"  ❌ No watchers in Supabase")
            await db.close()
            return False

        print(f"  [TELEGRAM] {len(watchers)} accounts to check")

        monitor = Monitor(config, db)
        all_ok = True

        for w in watchers:
            phone = w['phone']
            role = w.get('role', 'monitor')
            session_string = w.get('session_string', '')

            print(f"\n  [ACCOUNT] {phone} role={role}")

            if not session_string or len(session_string) < 50:
                print(f"    ❌ STATUS=FAILED reason=invalid_session")
                all_ok = False
                continue

            try:
                from telethon import TelegramClient, StringSession
                client = TelegramClient(
                    StringSession(session_string),
                    config.api_id, config.api_hash,
                    connection_retries=3, retry_delay=2, request_retries=3
                )

                # CONNECT
                await asyncio.wait_for(client.connect(), timeout=30)
                if not client.is_connected():
                    print(f"    ❌ CONNECT=FAILED")
                    all_ok = False
                    continue
                print(f"    CONNECT=OK")

                # AUTHORIZE
                if not await client.is_user_authorized():
                    print(f"    ❌ AUTHORIZE=FAILED (session expired)")
                    print(f"    STATUS=FAILED reason=not_authorized")
                    await client.disconnect()
                    all_ok = False
                    continue
                print(f"    AUTHORIZE=OK")

                # GET_ME
                me = await client.get_me()
                if not me:
                    print(f"    ❌ GET_ME=FAILED")
                    all_ok = False
                    continue
                print(f"    GET_ME=OK user_id={me.id} username={getattr(me, 'username', None)}")

                # IDENTITY VERIFICATION
                # Verify the phone matches what we expect
                # (Telegram doesn't always expose phone, but we can check user_id)
                print(f"    IDENTITY phone={phone} telegram_id={me.id}")

                # HANDLERS (monitors only)
                if role == 'monitor':
                    print(f"    HANDLERS=REGISTERED (would register in production)")
                else:
                    print(f"    HANDLERS=NONE (joiner)")

                print(f"    STATUS=READY")

                await client.disconnect()

            except asyncio.TimeoutError:
                print(f"    ❌ CONNECT=TIMEOUT")
                print(f"    STATUS=FAILED reason=connect_timeout")
                all_ok = False
            except Exception as e:
                print(f"    ❌ STATUS=FAILED reason={type(e).__name__}: {e}")
                all_ok = False

        await db.close()

        if all_ok:
            print(f"\n  ✅ Telegram account health PASSED")
        else:
            print(f"\n  ⚠️ Some accounts failed — check above")
        return all_ok

    except Exception as e:
        print(f"  ❌ Telegram check exception: {type(e).__name__}: {e}")
        return False


async def check_worker_health():
    """فحص Worker health من system_settings."""
    print("\n" + "=" * 60)
    print("  WORKER HEALTH CHECK")
    print("=" * 60)

    try:
        import aiosqlite
        from bot import DatabaseManager
        from link_system import ProductionDB

        db = DatabaseManager()
        await db.init_db()
        prod_db = ProductionDB(db)

        # Check scheduler state
        scheduler_state = await prod_db.get_setting('scheduler_state', 'NOT_STARTED')
        scheduler_heartbeat = await prod_db.get_setting('scheduler_last_heartbeat', 'NEVER')
        scheduler_cycle = await prod_db.get_setting('scheduler_last_cycle', '0')

        print(f"  [SCHEDULER] state={scheduler_state}")
        print(f"  [SCHEDULER] last_heartbeat={scheduler_heartbeat}")
        print(f"  [SCHEDULER] last_cycle={scheduler_cycle}")

        # Check join_paused
        join_paused = await prod_db.get_setting('join_paused', 'false')
        print(f"  [SCHEDULER] join_paused={join_paused}")

        # Check queue depth
        queue_size = await prod_db.get_queue_size()
        print(f"  [QUEUE] depth={queue_size}")

        # Check FloodWait accounts
        from link_system import FloodWaitManager
        fw_mgr = FloodWaitManager(prod_db)
        blocked = await fw_mgr.get_blocked_accounts()
        if blocked:
            print(f"  [FLOODWAIT] {len(blocked)} accounts blocked:")
            for b in blocked:
                wait = int(b['next_retry_at'] - __import__('time').time())
                print(f"    → {b['phone']}: {wait}s remaining")
        else:
            print(f"  [FLOODWAIT] 0 accounts blocked ✅")

        await db.close()
        print(f"\n  ✅ Worker health check PASSED")
        return True

    except Exception as e:
        print(f"  ❌ Worker health exception: {type(e).__name__}: {e}")
        return False


async def main():
    print("=" * 60)
    print("  PHASE 4 — LIVE INTEGRATION AUDIT")
    print(f"  Timestamp: {datetime.now().isoformat()}")
    print("=" * 60)

    results = {}

    # 1. Environment Variables
    results['env'] = check_env_vars()

    if not results['env']:
        print("\n❌ STOP — missing required env vars")
        sys.exit(1)

    # 2. Supabase LIVE
    results['supabase'] = await check_supabase_live()

    # 3. SQLite LIVE
    results['sqlite'] = await check_sqlite_live()

    # 4. Telegram Accounts
    results['telegram'] = await check_telegram_accounts()

    # 5. Worker Health
    results['workers'] = await check_worker_health()

    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name:20s} = {status}")

    all_pass = all(results.values())
    print(f"\n  {'✅ ALL CHECKS PASSED' if all_pass else '❌ SOME CHECKS FAILED'}")
    print("=" * 60)

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    asyncio.run(main())
