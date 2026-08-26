#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Raw MTProto hook (UpdateNewMessage/UpdateNewChannelMessage) — Tests [PR-3]
=========================================================================
السيناريوهات:
  6. Raw update + NewMessage لنفس الرسالة → لا duplicate (LRB idempotent)
  12. chat_id normalization: PeerChannel → -100..., PeerChat → -..., PeerUser → uid
  + Raw hook resilience: malformed update / missing fields → no exception
"""
import asyncio
import os
import sys
import logging
import types
from pathlib import Path
from unittest.mock import AsyncMock

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
from link_system import ProductionDB  # noqa: E402
from source_registry import MessageClaim  # noqa: E402

RESULTS = []


def record(name, passed, detail=""):
    RESULTS.append({'name': name, 'passed': passed, 'detail': detail})
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if detail and not passed:
        print(f"         {detail}")


async def make_test_db():
    import aiosqlite, tempfile
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd); os.chmod(path, 0o644)
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("""CREATE TABLE IF NOT EXISTS link_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT, raw_link TEXT, normalized_link TEXT UNIQUE,
        link_type TEXT, username TEXT, invite_hash TEXT, msg_id INTEGER,
        group_name TEXT, sender_name TEXT, sender_contact TEXT, source_phone TEXT,
        message_text TEXT, message_link TEXT, status TEXT DEFAULT 'QUEUED',
        enqueued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, priority INTEGER DEFAULT 3,
        attempt_count INTEGER DEFAULT 0, next_retry_at TIMESTAMP, last_error TEXT,
        member_count INTEGER)""")
    await conn.execute("""CREATE TABLE IF NOT EXISTS message_journal (
        chat_id INTEGER, msg_id INTEGER, raw_text TEXT, source_phone TEXT,
        received_at REAL, chat_title TEXT, chat_username TEXT, chat_link_type TEXT,
        sender_id INTEGER, sender_name TEXT, state TEXT,
        processed_at REAL, rescued_at REAL, deleted_at REAL,
        attempt_count INTEGER DEFAULT 0, error TEXT,
        PRIMARY KEY (chat_id, msg_id))""")
    await conn.execute("""CREATE TABLE IF NOT EXISTS processed_messages (
        chat_id INTEGER, msg_id INTEGER, state TEXT, source TEXT, claimant_phone TEXT,
        claim_token TEXT, claimed_at TEXT, lease_until TEXT, attempt_count INTEGER DEFAULT 0,
        PRIMARY KEY (chat_id, msg_id))""")
    await conn.commit()
    db = types.SimpleNamespace(_ensure_conn=AsyncMock(return_value=conn), _lock=asyncio.Lock())
    db.check_link_exists = types.MethodType(bot.DatabaseManager.check_link_exists, db)
    return ProductionDB(db), path, conn


def make_fake_monitor(prod_db, channel_id=-1009999999):
    cfg = types.SimpleNamespace(
        journal_enabled=True, delete_miss_reconcile=False,
        journal_retention_s=86400, journal_no_text_retention_s=21600,
        channel_id=channel_id,
    )
    fm = types.SimpleNamespace(
        config=cfg, prod_db=prod_db,
        message_claim=MessageClaim(prod_db),
        _msg_cache={}, _msg_cache_lock=asyncio.Lock(),
        metrics=types.SimpleNamespace(
            record_skip=AsyncMock(), record_duplicate=AsyncMock(),
            record_link_capture=AsyncMock(), record_link_ring_hit=AsyncMock(),
            record_delete_miss=AsyncMock(), record_delete_rescued=AsyncMock(),
            record_reconcile_rescued=AsyncMock(), record_link_forwarded=AsyncMock(),
        ),
        _link_ring={}, _link_ring_lock=asyncio.Lock(),
        _link_ring_ttl=300, _link_ring_cap=20000,
        _link_ring_evicted=0, _link_ring_hits=0,
        user_clients={}, source_registry=None,
        _delete_miss_log_ts={}, _delete_miss_count={}, _no_text_count=0,
        _reconcile_inflight=set(), _chat_poll_failures={},
        _polling_state={}, _polling_lock=asyncio.Lock(), _active_polling_chats=[],
    )
    for method_name in (
        '_journal_enabled', '_journal_write', '_journal_set_state_safe',
        '_journal_mark_deleted_safe', '_record_delete_miss',
        '_rescue_enqueue_links', '_spawn_reconcile',
        '_reconcile_chat_after_delete_miss', '_journal_recovery',
        '_link_ring_put', '_link_ring_pop', '_link_ring_evict',
        '_on_user_message', '_on_message_deleted',
        '_normalized_to_link_data', '_rescue_link_only',
        '_on_raw_new_message',  # [PR-3] regular async method — bind as MethodType
    ):
        if hasattr(bot.Monitor, method_name):
            setattr(fm, method_name,
                    types.MethodType(getattr(bot.Monitor, method_name), fm))
    # [PR-3] staticmethod: assign as plain function (not bound) so
    # self._normalize_raw_chat_id(peer_id) calls func(peer_id) correctly.
    fm._normalize_raw_chat_id = bot.Monitor._normalize_raw_chat_id
    return fm


class FakeNewMessageEvent:
    def __init__(self, raw_text, chat_id, msg_id, sender_id=42):
        self.raw_text = raw_text; self.chat_id = chat_id
        self.id = msg_id; self.sender_id = sender_id
        self.chat = None; self.sender = None


class FakeMsg:
    """محاكاة telethon Message object للـRaw update."""
    def __init__(self, text, msg_id, peer_id):
        self.message = text; self.id = msg_id; self.peer_id = peer_id


class FakeRawUpdate:
    def __init__(self, text, msg_id, peer_id):
        self.message = FakeMsg(text, msg_id, peer_id)


def _peer_channel(cid):  # construct a PeerChannel-like
    from telethon.tl.types import PeerChannel
    return PeerChannel(channel_id=cid)


def _peer_chat(hid):
    from telethon.tl.types import PeerChat
    return PeerChat(chat_id=hid)


def _peer_user(uid):
    from telethon.tl.types import PeerUser
    return PeerUser(user_id=uid)


# =====================================================================
# 12. chat_id normalization — channel/supergroup/group/user IDs
# =====================================================================
async def test_12_chat_id_normalization():
    print("\n--- Test 12: chat_id normalization for all peer types ---")
    norm = bot.Monitor._normalize_raw_chat_id
    # PeerChannel (supergroup/channel) → -100{cid}
    r1 = norm(_peer_channel(1234567890))
    record("12: PeerChannel → -100{cid} (supergroup)",
           r1 == -1001234567890, f"got {r1}")
    # PeerChannel small
    r2 = norm(_peer_channel(7))
    record("12: PeerChannel small → -1007",
           r2 == -1007, f"got {r2}")
    # PeerChat (legacy small group) → -{hid}
    r3 = norm(_peer_chat(987654321))
    record("12: PeerChat → -{hid} (legacy group)",
           r3 == -987654321, f"got {r3}")
    # PeerUser (private chat) → uid
    r4 = norm(_peer_user(111222333))
    record("12: PeerUser → uid (private chat)",
           r4 == 111222333, f"got {r4}")
    # None → None
    record("12: None peer → None", norm(None) is None, f"got {norm(None)}")
    # Unknown type → None
    record("12: unknown peer → None (defensive)",
           norm(types.SimpleNamespace(foo='bar')) is None, "not None")


# =====================================================================
# 6. Raw update + NewMessage → no duplicate (LRB idempotent overwrite)
# =====================================================================
async def test_6_raw_plus_newmessage_no_dup():
    print("\n--- Test 6: Raw update + NewMessage same msg → no duplicate ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_fake_monitor(prod_db)
        chat = -1006006
        msg_id = 6106
        peer = _peer_channel(6006)  # channel_id 6006 → normalized chat -1006006

        # 1. Raw hook fires first (writes LRB)
        raw_upd = FakeRawUpdate("https://t.me/RawAndNew123", msg_id, peer)
        await bot.Monitor._on_raw_new_message(fm, raw_upd, '+999')
        ring_after_raw = dict(fm._link_ring)
        record("6: Raw hook wrote LRB",
               (-1006006, msg_id) in ring_after_raw, f"got {list(ring_after_raw)}")

        # 2. NewMessage fires for same msg (overwrites LRB + processes)
        ev = FakeNewMessageEvent("https://t.me/RawAndNew123", chat, msg_id)
        await bot.Monitor._on_user_message(fm, ev, '+999')

        # LRB should still have exactly 1 key (overwrite, not duplicate)
        keys = [k for k in fm._link_ring if k == (chat, msg_id)]
        record("6: LRB has exactly 1 key after Raw+NewMessage",
               len(keys) == 1, f"got {len(keys)} keys")

        # link_queue should have exactly 1 row (NewMessage enqueued; Raw only
        # wrote LRB, didn't enqueue)
        cur = await conn.execute("SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
                                 ('tg:user:rawandnew123',))
        cnt = (await cur.fetchone())[0]
        record("6: link_queue has exactly 1 row (no dup enqueue)",
               cnt == 1, f"got {cnt}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Raw hook resilience — malformed/missing fields never raise
# =====================================================================
async def test_raw_resilience():
    print("\n--- Test: Raw hook resilience (malformed updates never raise) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_fake_monitor(prod_db)
        # 1. update with no .message
        try:
            await bot.Monitor._on_raw_new_message(fm, types.SimpleNamespace(), '+999')
            record("RAW: update with no .message → no exception", True)
        except Exception as e:
            record("RAW: update with no .message → no exception", False, str(e))

        # 2. message with no text
        try:
            upd = FakeRawUpdate("", 1, _peer_channel(1))
            await bot.Monitor._on_raw_new_message(fm, upd, '+999')
            record("RAW: empty text → no exception", True)
        except Exception as e:
            record("RAW: empty text → no exception", False, str(e))

        # 3. message with unknown peer type
        try:
            upd = FakeRawUpdate("https://t.me/x", 2, types.SimpleNamespace(foo='bar'))
            await bot.Monitor._on_raw_new_message(fm, upd, '+999')
            record("RAW: unknown peer → no exception (silent skip)", True)
        except Exception as e:
            record("RAW: unknown peer → no exception", False, str(e))

        # 4. None update
        try:
            await bot.Monitor._on_raw_new_message(fm, None, '+999')
            record("RAW: None update → no exception", True)
        except Exception as e:
            record("RAW: None update → no exception", False, str(e))
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


async def main():
    print("=" * 70)
    print("Raw MTProto hook + chat_id normalization — Test Suite [PR-3]")
    print("=" * 70)
    await test_12_chat_id_normalization()
    await test_6_raw_plus_newmessage_no_dup()
    await test_raw_resilience()
    print("\n" + "=" * 70)
    passed = sum(1 for r in RESULTS if r['passed'])
    failed = sum(1 for r in RESULTS if not r['passed'])
    print(f"RESULTS: {passed}/{passed + failed} passed, {failed} failed")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
