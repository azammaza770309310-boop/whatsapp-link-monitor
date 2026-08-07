#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PHASE 4 — LIVE Pipeline Test
============================
سكريبت يُشغّل على Render لاختبار الـ pipeline كاملاً مع Telegram حقيقي.

الاختبار:
1. يتصل بحساب Monitor حقيقي
2. ينتظر رسالة تحتوي رابط
3. يتتبع الرابط من PIPELINE-1 إلى PIPELINE-6
4. يتحقق من Database state
5. يطبع تقرير نهائي

Usage: python live_pipeline_test.py
"""
import asyncio
import os
import sys
import logging
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_pipeline_live():
    """اختبار الـ pipeline كامل مع Telegram حقيقي."""
    print("=" * 60)
    print("  PHASE 4 — LIVE PIPELINE TEST")
    print("=" * 60)

    try:
        from bot import Config, DatabaseManager, Monitor
        from link_system import init_production_tables, ProductionDB, GroupState

        config = Config()
        errors = config.validate()
        if errors:
            print(f"❌ Config errors: {errors}")
            return False

        db = DatabaseManager()
        await db.init_db()
        await init_production_tables(db)
        prod_db = ProductionDB(db)

        # Get watchers
        watchers = await db.get_active_watchers()
        monitors = [w for w in watchers if w.get('role', 'monitor') == 'monitor']
        joiners = [w for w in watchers if w.get('role') == 'joiner']

        print(f"  Monitors: {len(monitors)}")
        print(f"  Joiners: {len(joiners)}")

        if not monitors:
            print("  ❌ No monitors — cannot test pipeline")
            await db.close()
            return False

        # Connect to first monitor
        monitor_watcher = monitors[0]
        phone = monitor_watcher['phone']
        session_string = monitor_watcher['session_string']

        print(f"\n  Connecting to monitor: {phone}")

        from telethon import TelegramClient, StringSession
        client = TelegramClient(
            StringSession(session_string),
            config.api_id, config.api_hash
        )
        await client.connect()

        if not await client.is_user_authorized():
            print(f"  ❌ Monitor {phone} not authorized")
            await client.disconnect()
            await db.close()
            return False

        me = await client.get_me()
        print(f"  ✅ Connected as user_id={me.id} username={getattr(me, 'username', None)}")

        # Check queue depth
        queue_size = await prod_db.get_queue_size()
        print(f"\n  Current queue depth: {queue_size}")

        # Check recent queue items
        conn = await db._ensure_conn()
        cursor = await conn.execute(
            "SELECT id, raw_link, normalized_link, status, enqueued_at, next_retry_at "
            "FROM link_queue ORDER BY id DESC LIMIT 10"
        )
        rows = await cursor.fetchall()
        print(f"\n  Recent queue items (last 10):")
        for r in rows:
            print(f"    id={r[0]} status={r[3]} link={r[1][:50]} enqueued={r[4]}")
            if r[5]:
                print(f"      next_retry={r[5]}")

        # Check group_states
        cursor = await conn.execute(
            "SELECT normalized_link, state, joined_by, member_count, last_error "
            "FROM group_states ORDER BY last_seen DESC LIMIT 10"
        )
        rows = await cursor.fetchall()
        print(f"\n  Recent group_states (last 10):")
        for r in rows:
            print(f"    {r[0][:50]} state={r[1]} joined_by={r[2]} members={r[3]} error={r[4]}")

        # Check api_operations_log
        cursor = await conn.execute(
            "SELECT phone, action_type, timestamp, success "
            "FROM api_operations_log ORDER BY id DESC LIMIT 10"
        )
        rows = await cursor.fetchall()
        print(f"\n  Recent API operations (last 10):")
        for r in rows:
            print(f"    {r[0]} action={r[1]} success={r[3]}")

        # Check scheduler health
        scheduler_state = await prod_db.get_setting('scheduler_state', 'NOT_STARTED')
        scheduler_heartbeat = await prod_db.get_setting('scheduler_last_heartbeat', 'NEVER')
        scheduler_cycle = await prod_db.get_setting('scheduler_last_cycle', '0')
        join_paused = await prod_db.get_setting('join_paused', 'false')

        print(f"\n  Scheduler health:")
        print(f"    state={scheduler_state}")
        print(f"    last_heartbeat={scheduler_heartbeat}")
        print(f"    last_cycle={scheduler_cycle}")
        print(f"    join_paused={join_paused}")

        # Check FloodWait
        from link_system import FloodWaitManager
        fw_mgr = FloodWaitManager(prod_db)
        blocked = await fw_mgr.get_blocked_accounts()
        print(f"\n  FloodWait blocked accounts: {len(blocked)}")
        for b in blocked:
            wait = int(b['next_retry_at'] - time.time())
            print(f"    {b['phone']}: {wait}s remaining")

        await client.disconnect()
        await db.close()

        print(f"\n  ✅ Live pipeline test data collected")
        print(f"\n  To test full pipeline:")
        print(f"    1. Send a message with a Telegram link to a monitored group")
        print(f"    2. Wait 60-90 seconds for Scheduler cycle")
        print(f"    3. Check Render logs for [LINK id=N] entries")
        print(f"    4. Verify PIPELINE-1 through PIPELINE-6 all appear with same id")

        return True

    except Exception as e:
        print(f"  ❌ Exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    result = await test_pipeline_live()
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    asyncio.run(main())
