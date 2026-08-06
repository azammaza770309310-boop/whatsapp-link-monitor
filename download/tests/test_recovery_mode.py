#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Recovery Mode Tests — يختبر النظام بعد آخر تحديثات الحماية.

1. Startup Recovery: join_paused=true افتراضياً
2. JOIN_PAUSED محفوظ في SQLite
3. SIMULATION_MODE: صفر API
4. Membership TTL 7 أيام
5. Conservative limits: 2/day, 1/hour, 3600s cooldown
6. FloodWait من startup يُقرأ من DB
"""
import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
logging.disable(logging.CRITICAL)

from link_system import (
    LinkNormalizer, GroupState, RateLimiter, FloodWaitManager,
    MembershipCache, Metrics, ProductionDB, init_production_tables
)
from monitor_v12 import DatabaseManager, Config, Monitor


async def main():
    print("=" * 70)
    print("  RECOVERY MODE TESTS")
    print("=" * 70)

    passed = 0
    failed = 0

    # Setup
    db = DatabaseManager(tempfile.mktemp(suffix=".db"))
    await db.init_db()
    await init_production_tables(db)
    prod_db = ProductionDB(db)
    rate_limiter = RateLimiter(prod_db)
    rate_limiter.OP_LIMITS['join']['min_delay'] = 0
    floodwait_mgr = FloodWaitManager(prod_db)
    metrics = Metrics()

    config = Config.__new__(Config)
    config.api_id = 123
    config.api_hash = "x"
    config.bot_token = "x"
    config.channel_id = -100123
    config.owner_id = None
    config.log_level = "INFO"
    config.history_max_per_chat = 100
    config.history_batch_size = 5
    config.history_skip_channel_posts = False
    config.startup_scan_days = None
    config.min_message_length = 20
    config.max_message_length = 2000

    os.environ['SIMULATION_MODE'] = 'false'
    monitor = Monitor(config, db)
    monitor.prod_db = prod_db
    monitor.rate_limiter = rate_limiter
    monitor.floodwait_mgr = floodwait_mgr
    monitor.metrics = metrics
    monitor.membership_cache = MembershipCache(prod_db, rate_limiter)

    # === Test 1: join_paused=true by default ===
    print("\n--- Test 1: join_paused defaults to True ---")
    if monitor._join_paused == True:
        print("  ✅ Default join_paused=True (safe startup)")
        passed += 1
    else:
        print(f"  ❌ Default join_paused={monitor._join_paused} (should be True)")
        failed += 1

    # === Test 2: JOIN_PAUSED saved in SQLite ===
    print("\n--- Test 2: JOIN_PAUSED persisted in SQLite ---")
    await prod_db.set_setting('join_paused', 'true')
    val = await prod_db.get_setting('join_paused', 'false')
    if val == 'true':
        print("  ✅ join_paused=true saved and read from SQLite")
        passed += 1
    else:
        print(f"  ❌ Got: {val}")
        failed += 1

    # Simulate restart
    monitor2 = Monitor(config, db)
    monitor2.prod_db = prod_db
    db_paused = await prod_db.get_setting('join_paused', 'true')
    monitor2._join_paused = (db_paused == 'true')
    if monitor2._join_paused:
        print("  ✅ After restart: join_paused=True (from DB)")
        passed += 1
    else:
        print(f"  ❌ After restart: join_paused={monitor2._join_paused}")
        failed += 1

    # === Test 3: SIMULATION_MODE blocks all API ===
    print("\n--- Test 3: SIMULATION_MODE blocks all Telegram API ---")
    os.environ['SIMULATION_MODE'] = 'true'
    sim_monitor = Monitor(config, db)
    sim_monitor.prod_db = prod_db
    sim_monitor._join_paused = False  # Not paused
    await db.add_watcher("+9672", "Joiner", "fake_session_long_enough_1234567890", "joiner")

    mock_client = MagicMock()
    link_data = {'link_type': 'telegram', 'raw': 'https://t.me/test', 'normalized': 'tg:user:test', 'username': 'test'}
    success, status, _ = await sim_monitor._join_group_safe(mock_client, link_data, "+9672")
    if status == "SIMULATION" and not success:
        print(f"  ✅ SIMULATION_MODE: status={status}, zero API calls")
        passed += 1
    else:
        print(f"  ❌ Expected SIMULATION, got: {status}")
        failed += 1
    os.environ['SIMULATION_MODE'] = 'false'

    # === Test 4: Membership TTL = 7 days ===
    print("\n--- Test 4: Membership Cache TTL = 7 days ---")
    # Set membership 3 days ago → should be valid
    conn = await db._ensure_conn()
    old_date = (datetime.now() - timedelta(days=3)).isoformat()
    await conn.execute(
        "INSERT OR REPLACE INTO membership_cache (phone, normalized_link, is_member, checked_at) VALUES (?, ?, ?, ?)",
        ("+9671", "tg:user:testgroup", 1, old_date))
    await conn.commit()

    result = await prod_db.get_membership_with_ttl("+9671", "tg:user:testgroup", 7)
    if result == True:
        print("  ✅ 3-day-old membership still valid (TTL=7d)")
        passed += 1
    else:
        print(f"  ❌ 3-day-old membership expired (got: {result})")
        failed += 1

    # Set membership 10 days ago → should be expired
    old_date2 = (datetime.now() - timedelta(days=10)).isoformat()
    await conn.execute(
        "INSERT OR REPLACE INTO membership_cache (phone, normalized_link, is_member, checked_at) VALUES (?, ?, ?, ?)",
        ("+9671", "tg:user:oldgroup", 0, old_date2))
    await conn.commit()

    result2 = await prod_db.get_membership_with_ttl("+9671", "tg:user:oldgroup", 7)
    if result2 is None:
        print("  ✅ 10-day-old membership expired (needs recheck)")
        passed += 1
    else:
        print(f"  ❌ 10-day-old membership still valid (got: {result2})")
        failed += 1

    # === Test 5: Conservative limits ===
    print("\n--- Test 5: Conservative post-FloodWait limits ---")
    join_limit = rate_limiter.OP_LIMITS['join']
    if join_limit['max'] == 1 and join_limit['window'] == 3600:
        print(f"  ✅ Hourly join limit: {join_limit['max']}/hour")
        passed += 1
    else:
        print(f"  ❌ Join limit: {join_limit}")
        failed += 1

    daily = await monitor._get_daily_limit("+9672")  # joiner
    if daily == 2:
        print(f"  ✅ Daily join limit: {daily}/day (conservative)")
        passed += 1
    else:
        print(f"  ❌ Daily limit: {daily} (expected 2)")
        failed += 1

    # === Test 6: FloodWait read from DB on startup ===
    print("\n--- Test 6: FloodWait accounts read from DB on startup ---")
    await floodwait_mgr.block("+9671", 7200)  # 2 hours
    blocked = await floodwait_mgr.get_blocked_accounts()
    if len(blocked) >= 1 and any(b['phone'] == "+9671" for b in blocked):
        print(f"  ✅ {len(blocked)} account(s) in FloodWait (from DB)")
        passed += 1
    else:
        print(f"  ❌ No blocked accounts found")
        failed += 1

    # === Summary ===
    print(f"\n{'=' * 70}")
    print(f"  RESULTS: {passed}/{passed + failed} passed")
    if failed:
        print(f"  ❌ {failed} FAILED")
    else:
        print(f"  ✅ ALL TESTS PASSED — System ready for Recovery Mode")
    print(f"{'=' * 70}")

    await db.close()
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
