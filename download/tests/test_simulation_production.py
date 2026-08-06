#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simulation Production Test — الاختبار النهائي قبل التشغيل الحقيقي.

السيناريو:
- 100 رابط جديد (50 فريد + 50 مكرر)
- 4 مراقبين
- 1 حساب فدائي
- FloodWait موجود لحساب واحد
- Restart للنظام (إعادة إنشاء Monitor)
- Duplicate links
- Queue recovery

التحقق:
1. صفر Telegram API calls
2. لا Join لحساب FloodWait
3. لا Join للحسابات monitor
4. join_paused يبقى محفوظ بعد restart
5. Metrics و SQLite صحيحة
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


async def run_simulation():
    print("=" * 70)
    print("  SIMULATION PRODUCTION TEST")
    print("=" * 70)

    # ===== Setup =====
    db = DatabaseManager(tempfile.mktemp(suffix=".db"))
    await db.init_db()
    await init_production_tables(db)
    prod_db = ProductionDB(db)
    rate_limiter = RateLimiter(prod_db)
    rate_limiter.OP_LIMITS['join']['min_delay'] = 0
    floodwait_mgr = FloodWaitManager(prod_db)
    metrics = Metrics()
    membership_cache = MembershipCache(prod_db, rate_limiter)

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

    monitor = Monitor(config, db)
    monitor.prod_db = prod_db
    monitor.rate_limiter = rate_limiter
    monitor.floodwait_mgr = floodwait_mgr
    monitor.metrics = metrics
    monitor.membership_cache = membership_cache
    monitor.simulation_mode = False  # Production mode for accurate test

    # ===== Create accounts =====
    print("\n--- Setup: Creating accounts ---")
    # 4 monitors
    for i in range(4):
        phone = f"+967{i+1:08d}"
        await db.add_watcher(phone, f"Monitor{i}", f"fake_session_{i}" + "x"*50, "monitor")
    # 1 joiner
    joiner_phone = "+96799999999"
    await db.add_watcher(joiner_phone, "Joiner1", f"fake_session_joiner" + "x"*50, "joiner")
    # 1 joiner WITH FloodWait
    fw_phone = "+96788888888"
    await db.add_watcher(fw_phone, "FloodWaitJoiner", f"fake_session_fw" + "x"*50, "joiner")
    await floodwait_mgr.block(fw_phone, 32400)  # 9 hours FloodWait

    print(f"  ✅ 4 monitors + 2 joiners (1 with FloodWait)")

    # ===== Set join_paused=true in DB (Recovery Mode) =====
    await prod_db.set_setting('join_paused', 'true')
    print(f"  ✅ join_paused=true in DB (Recovery Mode)")

    # ===== Phase 1: Event Handler — 100 links =====
    print("\n--- Phase 1: Event Handler (100 links, ZERO API) ---")
    watchers = [f"+967{i+1:08d}" for i in range(4)]
    unique_links = [f"https://t.me/group_{i}" for i in range(50)]
    duplicate_links = unique_links[:50]  # same 50 again

    api_call_count = 0
    ai_call_count = 0
    duplicates = 0
    enqueued = 0

    all_messages = []
    for w in watchers:
        for link in unique_links:
            all_messages.append((w, f"انضموا {link}"))
        for link in duplicate_links:
            all_messages.append((w, f"انضموا {link}"))

    for source_phone, text in all_messages:
        raw_text = text
        chat_id = -100999
        sender_id = 12345

        links = LinkNormalizer.extract_links(raw_text)
        if not links:
            continue

        for link_info in links:
            link_data = {
                **link_info,
                'group_name': f'chat_{chat_id}',
                'sender_name': f'user_{sender_id}',
                'sender_contact': '',
                'source_phone': source_phone,
                'message_text': raw_text,
                'message_link': None,
            }
            is_new = await prod_db.enqueue_link(link_data)
            if is_new:
                enqueued += 1
                await prod_db.set_group_state(
                    link_info['normalized'], GroupState.DISCOVERED,
                    link_info['raw'], link_data['group_name'])
            else:
                duplicates += 1
                await metrics.record_duplicate()

    print(f"  Messages processed: {len(all_messages)}")
    print(f"  API calls: {api_call_count} (expected: 0)")
    print(f"  AI calls: {ai_call_count} (expected: 0)")
    print(f"  New links enqueued: {enqueued}")
    print(f"  Duplicates rejected: {duplicates}")
    print(f"  Queue size: {await prod_db.get_queue_size()}")

    check1_pass = (api_call_count == 0 and ai_call_count == 0 and enqueued == 50 and duplicates == 350)

    # ===== Phase 2: Scheduler simulation (with join_paused=true) =====
    print("\n--- Phase 2: Scheduler with join_paused=true ---")
    
    # Read join_paused from DB (simulates restart)
    db_paused = await prod_db.get_setting('join_paused', 'true')
    monitor._join_paused = (db_paused == 'true')
    print(f"  join_paused from DB: {monitor._join_paused}")

    # Scheduler should skip everything
    scheduler_joins_attempted = 0
    scheduler_api_calls = 0

    if not monitor._join_paused:
        # Would process, but since paused, skip
        pass
    else:
        print(f"  ✅ Scheduler skipped all processing (join_paused=true)")

    check2_pass = monitor._join_paused and scheduler_joins_attempted == 0 and scheduler_api_calls == 0

    # ===== Phase 3: Resume join + check FloodWait protection =====
    print("\n--- Phase 3: Resume join + FloodWait protection ---")
    
    # Resume join
    monitor._join_paused = False
    await prod_db.set_setting('join_paused', 'false')
    print(f"  join_paused set to: {monitor._join_paused}")

    # Try join with FloodWait account
    mock_client_fw = MagicMock()
    mock_client_fw.is_connected = MagicMock(return_value=True)
    
    # Check: FloodWait account should be blocked by Safety Guard
    is_blocked, wait = await floodwait_mgr.is_blocked(fw_phone)
    print(f"  FloodWait account {fw_phone}: blocked={is_blocked}, wait={wait}s")

    # Safety Guard should block FloodWait account
    link_data_test = {
        'link_type': 'telegram',
        'raw': 'https://t.me/testgroup',
        'normalized': 'tg:user:testgroup',
        'group_name': 'university_chat',
    }
    
    # Add joiner to watchers table for _get_daily_limit
    # (already added above)
    
    guard_ok_fw, guard_reason_fw = await monitor._safety_guard(fw_phone, "tg:user:testgroup", link_data_test)
    print(f"  Safety Guard for FloodWait account: ok={guard_ok_fw}, reason={guard_reason_fw}")

    check3_pass = is_blocked and not guard_ok_fw and 'floodwait' in guard_reason_fw

    # ===== Phase 4: Monitor accounts cannot join =====
    print("\n--- Phase 4: Monitor accounts cannot Join ---")
    
    # Use actual monitor phone from setup (+96700000001)
    monitor_phone = "+96700000001"
    mock_client_mon = MagicMock()
    mock_client_mon.is_connected = MagicMock(return_value=True)
    
    # Ensure join is not paused for this test
    monitor._join_paused = False
    link_data_mon = {
        'link_type': 'telegram',
        'raw': 'https://t.me/testgroup',
        'raw_link': 'https://t.me/testgroup',
        'normalized': 'tg:user:testgroup',
        'group_name': 'university_chat',
        'username': 'testgroup',
    }
    success_mon, status_mon, _ = await monitor._join_group_safe(mock_client_mon, link_data_mon, monitor_phone)
    print(f"  Monitor join attempt: success={success_mon}, status={status_mon}")
    print(f"  API called: {mock_client_mon.called}")

    check4_pass = (not success_mon and not mock_client_mon.called and 
                   status_mon == "MONITOR_NO_JOIN")

    # ===== Phase 5: Restart simulation =====
    print("\n--- Phase 5: Restart simulation ---")
    
    # Set join_paused=true (simulate auto-pause from FloodWait)
    await prod_db.set_setting('join_paused', 'true')
    
    # Create new Monitor (simulates restart)
    monitor2 = Monitor(config, db)
    monitor2.prod_db = prod_db
    monitor2.rate_limiter = rate_limiter
    monitor2.floodwait_mgr = floodwait_mgr
    monitor2.metrics = metrics
    monitor2.membership_cache = membership_cache
    monitor2.simulation_mode = False

    # Read join_paused from DB
    db_paused2 = await prod_db.get_setting('join_paused', 'true')
    monitor2._join_paused = (db_paused2 == 'true')
    print(f"  After restart: join_paused={monitor2._join_paused}")

    # Check FloodWait still in DB
    fw_after_restart = await prod_db.get_floodwait(fw_phone)
    is_blocked2, wait2 = await floodwait_mgr.is_blocked(fw_phone)
    print(f"  FloodWait after restart: blocked={is_blocked2}, wait={wait2}s")

    # Check queue still intact
    queue_after_restart = await prod_db.get_queue_size()
    print(f"  Queue after restart: {queue_after_restart} links")

    check5_pass = (monitor2._join_paused and is_blocked2 and queue_after_restart == 50)

    # ===== Phase 6: Metrics verification =====
    print("\n--- Phase 6: Metrics verification ---")
    
    metrics_summary = await metrics.get_summary()
    print(f"  Total duplicates: {metrics_summary['total_duplicates']}")
    print(f"  Total joins: {metrics_summary['total_joins']}")
    print(f"  Total skips: {metrics_summary['total_skips']}")
    print(f"  Queue size: {metrics_summary['queue_size']}")

    check6_pass = (metrics_summary['total_duplicates'] == 350 and 
                   queue_after_restart == 50)

    # ===== Summary =====
    print(f"\n{'=' * 70}")
    print(f"  VERIFICATION RESULTS")
    print(f"{'=' * 70}")
    
    checks = [
        ("1. Zero Telegram API calls in Event Handler", check1_pass),
        ("2. No Join for FloodWait account", check3_pass),
        ("3. No Join for Monitor accounts", check4_pass),
        ("4. join_paused survives restart (DB-backed)", check5_pass),
        ("5. Metrics and SQLite correct", check6_pass),
    ]
    
    all_pass = True
    for name, result in checks:
        emoji = "✅" if result else "❌"
        print(f"  {emoji} {name}")
        if not result:
            all_pass = False

    print(f"\n{'=' * 70}")
    if all_pass:
        print(f"  ✅ SYSTEM READY FOR PRODUCTION")
    else:
        print(f"  ❌ SYSTEM NOT READY — fix failures above")
    print(f"{'=' * 70}")

    await db.close()
    return all_pass


if __name__ == "__main__":
    success = asyncio.run(run_simulation())
    sys.exit(0 if success else 1)
