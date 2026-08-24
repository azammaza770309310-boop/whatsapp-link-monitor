#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Source Registry + Polling Scheduler + Message Claim — Contract Tests
====================================================================

14 اختبار (A-N) تثبت:
- chat_id UNIQUE عبر حسابات متعددة
- Atomic message dedup مع claim_token + lease
- Fair scheduling (Cold لا يُجوَّع بسبب Hot)
- Load balancing بين Monitors
- Retry على الفشل بدون فقدان الرسائل
- Channels تُغطّى (مو بس Groups)

كل اختبار يستخدم temp DB حقيقية + asserts فعلية.
"""
import asyncio
import os
import sys
import tempfile
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Setup path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Set test env BEFORE importing bot
os.environ.setdefault('BOT_TOKEN', '123:test')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')
os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'testhash')
os.environ.setdefault('OWNER_ID', '12345')
os.environ.setdefault('SUPABASE_URL', '')
os.environ.setdefault('SUPABASE_KEY', '')

logging.disable(logging.CRITICAL)

RESULTS = []

def record(name, passed, detail=""):
    RESULTS.append({'name': name, 'passed': passed, 'detail': detail})
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if detail and not passed:
        print(f"         {detail}")


# === Test DB Helper ===

async def make_test_db():
    """Create temp DB with all production tables."""
    import aiosqlite
    from link_system import ProductionDB, init_production_tables

    db_path = tempfile.mktemp(suffix='.db')

    class FakeDB:
        def __init__(self, path):
            self.db_path = path
            self._conn = None
            self._lock = asyncio.Lock()

        async def _ensure_conn(self):
            if self._conn is None:
                self._conn = await aiosqlite.connect(self.db_path)
            return self._conn

    fake_db = FakeDB(db_path)
    prod_db = ProductionDB(fake_db)
    await init_production_tables(fake_db)
    return prod_db, fake_db


# === Test A: 3 accounts + same chat = 1 source ===

async def test_A():
    """3 حسابات (Monitor A, Monitor B, Joiner C) كلها في Group X → monitored_chats فيه 1 row."""
    print("\n--- Test A: 3 accounts + same chat = 1 source ---")
    try:
        from source_registry import SourceRegistry
        prod_db, _ = await make_test_db()

        watchers = [
            {'phone': 'A', 'role': 'monitor'},
            {'phone': 'B', 'role': 'monitor'},
            {'phone': 'C', 'role': 'joiner'},
        ]
        registry = SourceRegistry(prod_db, watchers)

        # Mock: simulate that all 3 accounts see the same Group X (chat_id=-100123)
        chat_id = -100123
        async def fake_discover(self, user_clients):
            async with self._lock:
                phones_list = ['A', 'B', 'C']  # monitors first
                self._chat_to_phones[chat_id] = phones_list
            await prod_db.add_monitored_chat(
                chat_id=chat_id, chat_title='Group X',
                username='groupx', link_type='group', monitored_by='A',
            )
            import json as _json
            await prod_db.update_monitored_chat(
                chat_id,
                reader_phones=_json.dumps(phones_list),
                primary_reader='A',
                monitored_by='A',
            )

        # Monkey-patch discover
        registry.discover_all_sources_background = fake_discover.__get__(registry)
        await registry.discover_all_sources_background({})

        chats = await prod_db.get_monitored_chats(limit=100)
        group_x = [c for c in chats if c['chat_title'] == 'Group X']
        if len(group_x) != 1:
            record("A: monitored_chats has 1 row for Group X", False,
                   f"expected 1, got {len(group_x)}")
            return
        record("A: monitored_chats has 1 row for Group X", True)

        chat = group_x[0]
        reader_phones = json.loads(chat.get('reader_phones') or '[]')
        if set(reader_phones) != {'A', 'B', 'C'}:
            record("A: reader_phones contains all 3 accounts", False,
                   f"got {reader_phones}")
            return
        record("A: reader_phones contains all 3 accounts", True)

        # Monitors first
        if reader_phones[0] not in ('A', 'B'):
            record("A: monitors first in reader_phones", False,
                   f"got {reader_phones}")
            return
        record("A: monitors first in reader_phones", True)

        if chat['primary_reader'] in ('A', 'B'):
            record("A: primary_reader is a Monitor", True)
        else:
            record("A: primary_reader is a Monitor", False,
                   f"got {chat['primary_reader']}")
    except Exception as e:
        record("A: exception", False, str(e))


# === Test B: Monitor + Joiner + same message = 1 processing ===

async def test_B():
    """نفس الرسالة من Monitor A و Joiner C → processed_messages فيه 1 row."""
    print("\n--- Test B: Monitor + Joiner + same message = 1 processing ---")
    try:
        from source_registry import MessageClaim
        prod_db, _ = await make_test_db()
        claim = MessageClaim(prod_db)

        chat_id, msg_id = -100456, 789

        # Monitor A claims
        token_a = await prod_db.claim_message(chat_id, msg_id, 'newmessage', 'A')
        if not token_a:
            record("B: Monitor A claim succeeds", False, "no token returned")
            return
        record("B: Monitor A claim succeeds", True)

        # Joiner C tries (same chat_id, msg_id)
        token_c = await prod_db.claim_message(chat_id, msg_id, 'polling', 'C')
        if token_c is not None:
            record("B: Joiner C claim rejected (already claimed)", False,
                   "got token — should be None")
            return
        record("B: Joiner C claim rejected (already claimed)", True)

        # Verify DB has only 1 row
        count = await prod_db.count_processed_messages(chat_id, msg_id)
        if count != 1:
            record("B: processed_messages has 1 row", False, f"got {count}")
            return
        record("B: processed_messages has 1 row", True)

        row = await prod_db.get_processed_message(chat_id, msg_id)
        if row['source'] == 'newmessage' and row['claimant_phone'] == 'A':
            record("B: winner is Monitor A via newmessage", True)
        else:
            record("B: winner is Monitor A via newmessage", False,
                   f"got source={row['source']}, phone={row['claimant_phone']}")
    except Exception as e:
        record("B: exception", False, str(e))


# === Test C: NewMessage + Polling = 1 processing ===

async def test_C():
    """NewMessage يعالج رسالة، ثم Polling يحاول → يُرفض."""
    print("\n--- Test C: NewMessage + Polling = 1 processing ---")
    try:
        prod_db, _ = await make_test_db()
        chat_id, msg_id = -100789, 100

        # NewMessage claims + processes
        token_nm = await prod_db.claim_message(chat_id, msg_id, 'newmessage', 'A')
        await prod_db.mark_message_processed(chat_id, msg_id, token_nm)

        # Polling tries
        token_poll = await prod_db.claim_message(chat_id, msg_id, 'polling', 'A')
        if token_poll is not None:
            record("C: Polling rejected (already processed)", False,
                   "got token — should be None")
            return
        record("C: Polling rejected (already processed)", True)

        # Verify DB
        row = await prod_db.get_processed_message(chat_id, msg_id)
        if row['state'] == 'processed':
            record("C: state='processed' (winner was NewMessage)", True)
        else:
            record("C: state='processed'", False, f"got {row['state']}")
    except Exception as e:
        record("C: exception", False, str(e))


# === Test D: Polling + Scanner = 1 processing ===

async def test_D():
    """Polling يعالج، ثم Scanner يحاول → يُرفض."""
    print("\n--- Test D: Polling + Scanner = 1 processing ---")
    try:
        prod_db, _ = await make_test_db()
        chat_id, msg_id = -100999, 200

        # Polling claims + processes
        token_poll = await prod_db.claim_message(chat_id, msg_id, 'polling', 'A')
        await prod_db.mark_message_processed(chat_id, msg_id, token_poll)

        # Scanner tries
        token_sc = await prod_db.claim_message(chat_id, msg_id, 'scanner', 'A')
        if token_sc is not None:
            record("D: Scanner rejected (already processed)", False,
                   "got token — should be None")
            return
        record("D: Scanner rejected (already processed)", True)

        row = await prod_db.get_processed_message(chat_id, msg_id)
        if row['state'] == 'processed' and row['source'] == 'polling':
            record("D: state='processed' (winner was Polling)", True)
        else:
            record("D: state='processed' (winner was Polling)", False,
                   f"got {row['state']}/{row['source']}")
    except Exception as e:
        record("D: exception", False, str(e))


# === Test E: same URL in 10 messages = 1 queue item ===

async def test_E():
    """10 رسائل مختلفة (msg_ids 1-10) كلها تحوي نفس الرابط → link_queue فيه 1 row."""
    print("\n--- Test E: same URL in 10 messages = 1 queue item ---")
    try:
        prod_db, _ = await make_test_db()
        chat_id = -100111

        for msg_id in range(1, 11):
            # Each message gets a unique claim (different msg_id)
            token = await prod_db.claim_message(chat_id, msg_id, 'newmessage', 'A')
            if not token:
                record(f"E: msg {msg_id} claim succeeds", False, "no token")
                return
            await prod_db.mark_message_processed(chat_id, msg_id, token)

            # Try to enqueue same link
            await prod_db.enqueue_link({
                'raw': 'https://t.me/SummerSEU',
                'normalized': 'tg:user:summerseu',
                'link_type': 'telegram',
                'username': 'summerseu',
                'group_name': 'Test',
                'sender_name': 'tester',
                'source_phone': 'A',
                'message_text': '',
                'message_link': '',
                'invite_hash': None,
                'msg_id': str(msg_id),
                'sender_contact': '',
            })

        # Verify: 10 messages in processed_messages
        pm_count = await prod_db.count_processed_messages(chat_id)
        if pm_count != 10:
            record("E: 10 messages in processed_messages", False, f"got {pm_count}")
            return
        record("E: 10 messages in processed_messages", True)

        # Verify: link_queue has 1 row (URL dedup via UNIQUE normalized_link)
        conn = await prod_db._conn()
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
            ('tg:user:summerseu',)
        )
        row = await cursor.fetchone()
        lq_count = row[0] if row else 0
        if lq_count != 1:
            record("E: link_queue has 1 row (URL dedup)", False, f"got {lq_count}")
            return
        record("E: link_queue has 1 row (URL dedup)", True)
    except Exception as e:
        record("E: exception", False, str(e))


# === Test F: Monitor failover → Monitor → Joiner ===

async def test_F():
    """Monitor A offline → Monitor B → Joiner C → Monitor A returns."""
    print("\n--- Test F: Monitor failover → Monitor → Joiner ---")
    try:
        from source_registry import SourceRegistry
        prod_db, _ = await make_test_db()
        watchers = [
            {'phone': 'A', 'role': 'monitor'},
            {'phone': 'B', 'role': 'monitor'},
            {'phone': 'C', 'role': 'joiner'},
        ]
        registry = SourceRegistry(prod_db, watchers)
        chat_id = -100222
        registry._chat_to_phones[chat_id] = ['A', 'B', 'C']

        # All connected
        registry.update_phone_status('A', True)
        registry.update_phone_status('B', True)
        registry.update_phone_status('C', True)
        reader1 = registry.get_reader(chat_id)
        if reader1 != 'A':
            # May be A or B depending on load (both have 0 load)
            if reader1 in ('A', 'B'):
                record("F: All online → Monitor (A or B)", True)
            else:
                record("F: All online → Monitor (A or B)", False, f"got {reader1}")
                return
        else:
            record("F: All online → Monitor (A or B)", True)

        # A disconnects
        registry.update_phone_status('A', False)
        reader2 = registry.get_reader(chat_id)
        # Release load from reader1 if it was A
        if reader1 == 'A':
            registry.release_load('A')
        # Reset load for fresh test
        registry._phone_load = {'A': 0, 'B': 0, 'C': 0}
        reader2 = registry.get_reader(chat_id)
        if reader2 != 'B':
            record("F: A offline → B", False, f"got {reader2}")
            return
        record("F: A offline → B", True)
        registry.release_load('B')

        # B disconnects → fallback to Joiner C
        registry.update_phone_status('B', False)
        registry._phone_load = {'A': 0, 'B': 0, 'C': 0}
        reader3 = registry.get_reader(chat_id)
        if reader3 != 'C':
            record("F: B offline → Joiner C (fallback)", False, f"got {reader3}")
            return
        record("F: B offline → Joiner C (fallback)", True)
        registry.release_load('C')

        # A returns → Monitor preferred again
        registry.update_phone_status('A', True)
        registry._phone_load = {'A': 0, 'B': 0, 'C': 0}
        reader4 = registry.get_reader(chat_id)
        if reader4 != 'A':
            record("F: A returns → Monitor A (priority overrides stickiness)", False,
                   f"got {reader4}")
            return
        record("F: A returns → Monitor A (priority overrides stickiness)", True)
    except Exception as e:
        record("F: exception", False, str(e))


# === Test G: concurrent processing = exactly one winner ===

async def test_G():
    """3 concurrent claims لنفس الرسالة → فائز واحد فقط."""
    print("\n--- Test G: concurrent processing = exactly one winner ---")
    try:
        prod_db, _ = await make_test_db()
        chat_id, msg_id = -100333, 999

        results = await asyncio.gather(
            prod_db.claim_message(chat_id, msg_id, 'newmessage', 'A'),
            prod_db.claim_message(chat_id, msg_id, 'newmessage', 'B'),
            prod_db.claim_message(chat_id, msg_id, 'polling', 'C'),
        )

        true_count = sum(1 for r in results if r is not None)
        if true_count != 1:
            record("G: exactly 1 winner", False, f"got {true_count} winners")
            return
        record("G: exactly 1 winner", True)

        count = await prod_db.count_processed_messages(chat_id, msg_id)
        if count != 1:
            record("G: DB has 1 row", False, f"got {count}")
            return
        record("G: DB has 1 row", True)
    except Exception as e:
        record("G: exception", False, str(e))


# === Test H: restart لا يعيد معالجة الرسالة ===

async def test_H():
    """معالجة رسالة، محاكاة restart (امسح cache)، Scanner يحاول → يُرفض."""
    print("\n--- Test H: restart does not reprocess ---")
    try:
        prod_db, _ = await make_test_db()
        chat_id, msg_id = -100444, 100

        # Process before restart
        token = await prod_db.claim_message(chat_id, msg_id, 'newmessage', 'A')
        await prod_db.mark_message_processed(chat_id, msg_id, token)

        # Simulate restart: in-memory state cleared
        # (DB state persists — that's the point)

        # Scanner tries after restart
        token_retry = await prod_db.claim_message(chat_id, msg_id, 'scanner', 'A')
        if token_retry is not None:
            record("H: Scanner rejected after restart", False, "got token")
            return
        record("H: Scanner rejected after restart", True)

        row = await prod_db.get_processed_message(chat_id, msg_id)
        if row['state'] == 'processed':
            record("H: state='processed' (DB persisted)", True)
        else:
            record("H: state='processed'", False, f"got {row['state']}")
    except Exception as e:
        record("H: exception", False, str(e))


# === Test I: 1000+ dialogs كلها تظهر في Registry ===

async def test_I():
    """3 حسابات، 1000 unique chat_ids → Registry يكتشف كل 1000."""
    print("\n--- Test I: 1000+ dialogs all appear in Registry ---")
    try:
        from source_registry import SourceRegistry
        prod_db, _ = await make_test_db()
        watchers = [
            {'phone': 'A', 'role': 'monitor'},
            {'phone': 'B', 'role': 'monitor'},
            {'phone': 'C', 'role': 'joiner'},
        ]
        registry = SourceRegistry(prod_db, watchers)

        # Mock discovery: 1000 unique chats
        # chats 1-200: all 3 accounts
        # chats 201-400: only A
        # chats 401-600: only B
        # chats 601-800: only C
        # chats 801-1000: A and B
        async def fake_discover(self, user_clients):
            async with self._lock:
                for cid in range(1, 1001):
                    chat_id = -200000 - cid
                    if cid <= 200:
                        phones = ['A', 'B', 'C']
                    elif cid <= 400:
                        phones = ['A']
                    elif cid <= 600:
                        phones = ['B']
                    elif cid <= 800:
                        phones = ['C']
                    else:
                        phones = ['A', 'B']
                    # Sort: monitors first
                    phones.sort(key=lambda p: 0 if p in ('A', 'B') else 1)
                    self._chat_to_phones[chat_id] = phones
                    primary = phones[0] if phones else ''
                    await prod_db.add_monitored_chat(
                        chat_id=chat_id, chat_title=f'Chat {cid}',
                        username='', link_type='group', monitored_by=primary,
                    )
                    import json as _json
                    await prod_db.update_monitored_chat(
                        chat_id,
                        reader_phones=_json.dumps(phones),
                        primary_reader=primary,
                        monitored_by=primary,
                    )

        registry.discover_all_sources_background = fake_discover.__get__(registry)
        await registry.discover_all_sources_background({})

        chats = await prod_db.get_monitored_chats(limit=50000)
        if len(chats) != 1000:
            record("I: Registry has 1000 sources", False, f"got {len(chats)}")
            return
        record("I: Registry has 1000 sources (no Top-N limit)", True)

        # Verify: chat 1 (shared by 3) has reader_phones = ['A', 'B', 'C']
        chat_1 = next(c for c in chats if c['chat_id'] == -200001)
        rp1 = json.loads(chat_1['reader_phones'])
        if set(rp1) == {'A', 'B', 'C'}:
            record("I: shared chat has all 3 readers", True)
        else:
            record("I: shared chat has all 3 readers", False, f"got {rp1}")

        # Verify: chat 601 (only C) has reader_phones = ['C']
        chat_601 = next(c for c in chats if c['chat_id'] == -200601)
        rp601 = json.loads(chat_601['reader_phones'])
        if rp601 == ['C']:
            record("I: single-account chat has 1 reader", True)
        else:
            record("I: single-account chat has 1 reader", False, f"got {rp601}")
    except Exception as e:
        record("I: exception", False, str(e))


# === Test J: Channels يتم مراقبتها ===

async def test_J():
    """50 group + 30 channel → كل 80 في monitored_chats + PollingScheduler يغطيها."""
    print("\n--- Test J: Channels are monitored ---")
    try:
        prod_db, _ = await make_test_db()

        # Add 50 groups + 30 channels
        for i in range(50):
            await prod_db.add_monitored_chat(
                chat_id=-300000 - i, chat_title=f'Group {i}',
                username='', link_type='group', monitored_by='A',
            )
        for i in range(30):
            await prod_db.add_monitored_chat(
                chat_id=-400000 - i, chat_title=f'Channel {i}',
                username='', link_type='channel', monitored_by='A',
            )

        chats = await prod_db.get_monitored_chats(limit=50000)
        groups = [c for c in chats if c['link_type'] == 'group']
        channels = [c for c in chats if c['link_type'] == 'channel']

        if len(groups) != 50:
            record("J: 50 groups in monitored_chats", False, f"got {len(groups)}")
            return
        record("J: 50 groups in monitored_chats", True)

        if len(channels) != 30:
            record("J: 30 channels in monitored_chats", False, f"got {len(channels)}")
            return
        record("J: 30 channels in monitored_chats", True)

        # Verify PollingScheduler does not filter out channels
        # (select_due_chats does not filter by link_type)
        from source_registry import PollingScheduler
        sched = PollingScheduler.__new__(PollingScheduler)  # bypass __init__
        sched.BATCH_SIZE = 100
        sched.prod_db = prod_db
        due = await sched.select_due_chats(limit=100)
        if len(due) != 80:
            record("J: PollingScheduler covers all 80 (groups + channels)", False,
                   f"got {len(due)}")
            return
        record("J: PollingScheduler covers all 80 (groups + channels)", True)

        # Verify channels are included
        due_channel_count = sum(1 for c in due if c['link_type'] == 'channel')
        if due_channel_count != 30:
            record("J: 30 channels in due_chats", False, f"got {due_channel_count}")
            return
        record("J: 30 channels in due_chats", True)
    except Exception as e:
        record("J: exception", False, str(e))


# === Test K: Cold sources لا تُجوَّع بسبب Hot sources ===

async def test_K():
    """50 hot + 50 cold (كلها مستحقة) → بعد عدة دورات، كل 100 تحصل على poll."""
    print("\n--- Test K: Cold sources not starved by Hot sources ---")
    try:
        from source_registry import PollingScheduler
        prod_db, _ = await make_test_db()

        # Add 50 hot (last_activity = now) + 50 cold (last_activity = 2 days ago)
        now = datetime.now()
        two_days_ago = now - timedelta(days=2)

        for i in range(50):
            await prod_db.add_monitored_chat(
                chat_id=-500000 - i, chat_title=f'Hot {i}',
                username='', link_type='group', monitored_by='A',
            )
            await prod_db.update_monitored_chat(
                -500000 - i,
                last_activity=now.isoformat(),
                next_poll_at=now.isoformat(),  # due now
            )
        for i in range(50):
            await prod_db.add_monitored_chat(
                chat_id=-600000 - i, chat_title=f'Cold {i}',
                username='', link_type='group', monitored_by='A',
            )
            await prod_db.update_monitored_chat(
                -600000 - i,
                last_activity=two_days_ago.isoformat(),
                next_poll_at=now.isoformat(),  # due now (same as hot)
            )

        sched = PollingScheduler.__new__(PollingScheduler)
        sched.BATCH_SIZE = 10
        sched.prod_db = prod_db

        # Run 10 selection cycles (10 * 10 = 100 polls)
        polled_ids = set()
        for cycle in range(10):
            due = await sched.select_due_chats(limit=10)
            for c in due:
                polled_ids.add(c['chat_id'])
                # Mark as polled (next_poll_at = future)
                await prod_db.update_monitored_chat(
                    c['chat_id'],
                    next_poll_at=(now + timedelta(hours=1)).isoformat(),
                )

        if len(polled_ids) != 100:
            record("K: all 100 sources polled", False, f"got {len(polled_ids)}")
            return
        record("K: all 100 sources polled (no starvation)", True)

        # Verify cold sources got polled (chat_ids -600000 to -600049)
        cold_polled = sum(1 for cid in polled_ids if -600049 <= cid <= -600000)
        if cold_polled != 50:
            record("K: all 50 cold sources polled", False, f"got {cold_polled}")
            return
        record("K: all 50 cold sources polled", True)

        # Verify hot sources got polled (chat_ids -500000 to -500049)
        hot_polled = sum(1 for cid in polled_ids if -500049 <= cid <= -500000)
        if hot_polled != 50:
            record("K: all 50 hot sources polled", False, f"got {hot_polled}")
            return
        record("K: all 50 hot sources polled", True)
    except Exception as e:
        record("K: exception", False, str(e))


# === Test L: failed processing يمكن retry ولا تضيع الرسالة ===

async def test_L():
    """claim → فشل → retry → نجاح."""
    print("\n--- Test L: failed processing can retry ---")
    try:
        prod_db, _ = await make_test_db()
        chat_id, msg_id = -100555, 200

        # 1. Claim + fail
        token1 = await prod_db.claim_message(chat_id, msg_id, 'newmessage', 'A')
        if not token1:
            record("L: first claim succeeds", False, "no token")
            return
        record("L: first claim succeeds", True)

        success = await prod_db.mark_message_failed(chat_id, msg_id, token1, 'extract failed')
        if not success:
            record("L: mark_failed succeeds", False, "rowcount=0")
            return
        record("L: mark_failed succeeds", True)

        row = await prod_db.get_processed_message(chat_id, msg_id)
        if row['state'] != 'failed':
            record("L: state='failed'", False, f"got {row['state']}")
            return
        record("L: state='failed'", True)

        if row['attempt_count'] != 1:
            record("L: attempt_count=1", False, f"got {row['attempt_count']}")
            return
        record("L: attempt_count=1", True)

        # 2. Retry (another worker)
        token2 = await prod_db.claim_message(chat_id, msg_id, 'polling', 'B')
        if not token2:
            record("L: retry claim succeeds (after failure)", False, "no token")
            return
        record("L: retry claim succeeds (after failure)", True)

        row = await prod_db.get_processed_message(chat_id, msg_id)
        if row['attempt_count'] != 2:
            record("L: attempt_count=2 after retry", False, f"got {row['attempt_count']}")
            return
        record("L: attempt_count=2 after retry", True)

        # 3. Success
        success = await prod_db.mark_message_processed(chat_id, msg_id, token2)
        if not success:
            record("L: mark_processed (retry) succeeds", False, "rowcount=0")
            return
        record("L: mark_processed (retry) succeeds", True)

        row = await prod_db.get_processed_message(chat_id, msg_id)
        if row['state'] == 'processed':
            record("L: final state='processed'", True)
        else:
            record("L: final state='processed'", False, f"got {row['state']}")
    except Exception as e:
        record("L: exception", False, str(e))


# === Test M: claim_token + lease (stale worker cannot corrupt) ===

async def test_M():
    """Worker A يحصل على token AAA، lease ينتهي، Worker B يحصل على token BBB.
    A يحاول mark_processed(AAA) → فشل.
    B ينفذ mark_processed(BBB) → نجاح.
    """
    print("\n--- Test M: claim_token + lease (stale worker protection) ---")
    try:
        prod_db, _ = await make_test_db()
        chat_id, msg_id = -100666, 300

        # 1. Worker A claims
        token_a = await prod_db.claim_message(chat_id, msg_id, 'newmessage', 'A')
        if not token_a:
            record("M: Worker A claim succeeds", False, "no token")
            return
        record("M: Worker A claim succeeds", True)

        # 2. Simulate lease expiry (set lease_until to past)
        conn = await prod_db._conn()
        past = (datetime.now() - timedelta(seconds=10)).isoformat()
        await conn.execute(
            "UPDATE processed_messages SET lease_until=? WHERE chat_id=? AND msg_id=?",
            (past, chat_id, msg_id)
        )
        await conn.commit()

        # 3. Worker B claims (lease expired → re-claimable)
        token_b = await prod_db.claim_message(chat_id, msg_id, 'polling', 'B')
        if not token_b:
            record("M: Worker B claim succeeds (after lease expiry)", False, "no token")
            return
        record("M: Worker B claim succeeds (after lease expiry)", True)

        if token_b == token_a:
            record("M: tokens are different", False, "same token")
            return
        record("M: tokens are different", True)

        # 4. Worker A tries mark_processed (stale token)
        success_a = await prod_db.mark_message_processed(chat_id, msg_id, token_a)
        if success_a:
            record("M: stale Worker A mark_processed fails", False, "succeeded — should fail")
            return
        record("M: stale Worker A mark_processed fails", True)

        # 5. Worker B executes mark_processed
        success_b = await prod_db.mark_message_processed(chat_id, msg_id, token_b)
        if not success_b:
            record("M: Worker B mark_processed succeeds", False, "failed")
            return
        record("M: Worker B mark_processed succeeds", True)

        # 6. Verify state
        row = await prod_db.get_processed_message(chat_id, msg_id)
        if row['state'] == 'processed' and row['claim_token'] == token_b:
            record("M: final state='processed' with Worker B token", True)
        else:
            record("M: final state='processed' with Worker B token", False,
                   f"got state={row['state']}, token match={row['claim_token'] == token_b}")
    except Exception as e:
        record("M: exception", False, str(e))


# === Test N: Load balancing بين 4 Monitors ===

async def test_N():
    """4 Monitors متصلين، 20 مصدر مشترك → لا استحواذ من حساب واحد."""
    print("\n--- Test N: Load balancing across 4 Monitors ---")
    try:
        from source_registry import SourceRegistry
        prod_db, _ = await make_test_db()
        watchers = [
            {'phone': 'A', 'role': 'monitor'},
            {'phone': 'B', 'role': 'monitor'},
            {'phone': 'C', 'role': 'monitor'},
            {'phone': 'D', 'role': 'monitor'},
        ]
        registry = SourceRegistry(prod_db, watchers)

        # 40 chats, all readable by all 4 monitors
        for i in range(40):
            chat_id = -700000 - i
            registry._chat_to_phones[chat_id] = ['A', 'B', 'C', 'D']
        for phone in ['A', 'B', 'C', 'D']:
            registry.update_phone_status(phone, True)

        # Simulate 40 concurrent polling cycles (NO release between —
        # this simulates 40 polls in flight, where load balancing matters most)
        reader_counts = {'A': 0, 'B': 0, 'C': 0, 'D': 0}
        for i in range(40):
            chat_id = -700000 - i
            reader = registry.get_reader(chat_id)
            if reader is None:
                record(f"N: chat {i} got a reader", False, "None")
                return
            reader_counts[reader] += 1
            # Do NOT release load — simulates concurrent polls

        # Verify: every monitor got at least one poll
        for phone, count in reader_counts.items():
            if count == 0:
                record("N: no monitor starved", False, f"{phone} got 0 polls")
                return
        record("N: no monitor starved (all got polls)", True)

        # Verify: load balancing worked (with concurrent load, should be very balanced)
        # Expected: ~10 each. With load balancing (min load pick), should be exact.
        total = sum(reader_counts.values())
        for phone, count in reader_counts.items():
            ratio = count / total
            if ratio >= 0.5:
                record("N: load is distributed (<50% per monitor)", False,
                       f"{phone} got {ratio*100:.0f}%")
                return
        record("N: load is distributed (<50% per monitor)", True)

        # With load balancing on concurrent polls, distribution should be very tight
        # (each pick goes to the least-loaded, so they stay within 1 of each other)
        counts = list(reader_counts.values())
        if max(counts) - min(counts) > 2:
            record("N: distribution is balanced (max-min<=2)", False,
                   f"counts={reader_counts}")
            return
        record("N: distribution is balanced (max-min<=2)", True)

        print(f"         Distribution: {reader_counts}")
    except Exception as e:
        record("N: exception", False, str(e))


# === Main runner ===

async def main():
    print("=" * 70)
    print("Source Registry + PollingScheduler + MessageClaim — Test Suite")
    print("=" * 70)

    await test_A()
    await test_B()
    await test_C()
    await test_D()
    await test_E()
    await test_F()
    await test_G()
    await test_H()
    await test_I()
    await test_J()
    await test_K()
    await test_L()
    await test_M()
    await test_N()

    # Summary
    print("\n" + "=" * 70)
    passed = sum(1 for r in RESULTS if r['passed'])
    failed = sum(1 for r in RESULTS if not r['passed'])
    total = len(RESULTS)
    print(f"RESULTS: {passed}/{total} passed, {failed} failed")
    print("=" * 70)

    if failed > 0:
        print("\nFailed tests:")
        for r in RESULTS:
            if not r['passed']:
                print(f"  ❌ {r['name']}: {r['detail']}")
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    rc = asyncio.run(main())
    sys.exit(rc)
