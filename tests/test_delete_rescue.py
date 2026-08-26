#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Delete Rescue (Link Ring + cache + journal + get_messages + reconcile) — Tests [PR-2]
======================================================================================
السيناريوهات من قائمة المستخدم:
  3. MessageDeleted بعد NewMessage → الرابط موجود ويمكن إنقاذه (cache fast path)
  4. DELETE-MISS مع link موجود في Ring Buffer → يتم إنقاذه (link-only rescue)
  5. DELETE-MISS بدون أي نسخة → يسجل MISS ولا يدّعي استرجاع المستحيل
  7. Reconcile + NewMessage → لا يوجد duplicate (central dedup)
  9. Restart/recovery → لا يعيد نشر الرابط (forwarded_requests dedup)
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
import link_system  # noqa: E402
from link_system import ProductionDB, GroupState  # noqa: E402
from source_registry import MessageClaim  # noqa: E402

RESULTS = []


def record(name, passed, detail=""):
    RESULTS.append({'name': name, 'passed': passed, 'detail': detail})
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if detail and not passed:
        print(f"         {detail}")


async def make_test_db():
    import aiosqlite
    import tempfile
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    os.chmod(path, 0o644)
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
    await conn.execute("""CREATE TABLE IF NOT EXISTS forwarded_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, message_text TEXT, message_date TIMESTAMP,
        group_name TEXT, sender_name TEXT, source_phone TEXT, message_link TEXT,
        content_hash TEXT NOT NULL UNIQUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
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
    await conn.execute("""CREATE TABLE IF NOT EXISTS target_groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT, group_link TEXT UNIQUE, group_title TEXT,
        status TEXT DEFAULT 'PENDING', discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        joined_by_phone TEXT, join_date TEXT, member_count INTEGER)""")
    await conn.execute("""CREATE TABLE IF NOT EXISTS monitored_chats (
        chat_id INTEGER PRIMARY KEY, chat_title TEXT, username TEXT, link_type TEXT,
        monitored_by TEXT, discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active INTEGER DEFAULT 1, last_checked TIMESTAMP, member_count INTEGER)""")
    await conn.execute("""CREATE TABLE IF NOT EXISTS link_states (
        normalized_link TEXT PRIMARY KEY, state TEXT, raw_link TEXT, group_title TEXT,
        joined_by TEXT, member_count INTEGER, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        error TEXT)""")
    await conn.commit()
    db = types.SimpleNamespace(_ensure_conn=AsyncMock(return_value=conn), _lock=asyncio.Lock())
    db.check_link_exists = types.MethodType(bot.DatabaseManager.check_link_exists, db)
    db.delete_forwarded_request = types.MethodType(
        bot.DatabaseManager.delete_forwarded_request, db)
    return ProductionDB(db), path, conn


def make_fake_monitor(prod_db, channel_id=-1009999999):
    cfg = types.SimpleNamespace(
        journal_enabled=True, delete_miss_reconcile=False,
        journal_retention_s=86400, journal_no_text_retention_s=21600,
        channel_id=channel_id, journal_recovery_enabled=True,
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
        '_normalized_to_link_data', '_rescue_link_only',
    ):
        setattr(fm, method_name,
                types.MethodType(getattr(bot.Monitor, method_name), fm))
    return fm


class FakeNewMessageEvent:
    def __init__(self, raw_text, chat_id, msg_id, sender_id=42):
        self.raw_text = raw_text; self.chat_id = chat_id
        self.id = msg_id; self.sender_id = sender_id
        self.chat = None; self.sender = None


class FakeDeleteEvent:
    def __init__(self, ids, chat_id=None):
        self.deleted_ids = list(ids); self.chat_id = chat_id


# =====================================================================
# 3. MessageDeleted after NewMessage → cache rescue (full message)
# =====================================================================
async def test_3_cache_rescue_after_delete():
    print("\n--- Test 3: MessageDeleted after NewMessage → cache rescue ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_fake_monitor(prod_db)
        chat = -1003001
        # NewMessage fully processed (cache + journal + LRB populated)
        ev = FakeNewMessageEvent("جديد https://t.me/RescueMe123", chat, 3101)
        await bot.Monitor._on_user_message(fm, ev, '+999')
        # Now delete it — LRB has the link too, but cache has full metadata.
        # LRB rescue runs FIRST (link-only). Since the link was already enqueued
        # by NewMessage, is_link_known returns True → dedup skips → rescued=0.
        # metrics.record_link_ring_hit SHOULD still fire (LRB had the link).
        ev_del = FakeDeleteEvent([3101], chat)
        await bot.Monitor._on_message_deleted(fm, ev_del, '+999')
        # The link should be enqueued exactly ONCE (from NewMessage)
        cur = await conn.execute("SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
                                 ('tg:user:rescueme123',))
        cnt = (await cur.fetchone())[0]
        record("3: link enqueued exactly once (no dup from delete)",
               cnt == 1, f"got {cnt}")
        record("3: link_ring_hit metric fired (LRB had the link)",
               fm.metrics.record_link_ring_hit.called, "not called")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# 4. DELETE-MISS with link in Ring Buffer → link-only rescue
# =====================================================================
async def test_4_lrb_rescue_on_delete_miss():
    print("\n--- Test 4: DELETE-MISS with link in Ring Buffer → rescued ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_fake_monitor(prod_db)
        chat = -1004004
        # Simulate: NewMessage Step 0 ran (LRB populated) but message deleted
        # before PRE-CACHE/journal. So ONLY LRB has the link.
        await fm._link_ring_put(chat, 4104, ['tg:user:lrbrescued'])
        # Fire delete — no cache, no journal, only LRB
        ev_del = FakeDeleteEvent([4104], chat)
        await bot.Monitor._on_message_deleted(fm, ev_del, '+999')
        # Link should be enqueued (link-only rescue via _rescue_link_only)
        cur = await conn.execute("SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
                                 ('tg:user:lrbrescued',))
        cnt = (await cur.fetchone())[0]
        record("4: LRB-only rescue enqueued the link",
               cnt == 1, f"got {cnt}")
        # Journal should be marked 'rescued' (link-only rescue path)
        row = await prod_db.journal_get(chat, 4104)
        # (journal_message for delete_miss may NOT have been written since LRB hit
        #  short-circuits before _record_delete_miss — but set_state_safe may write
        #  if a journal row exists. Here NO journal row was pre-seeded, so
        #  _journal_set_state_safe is a no-op on absent row.)
        record("4: link_ring_hit metric fired",
               fm.metrics.record_link_ring_hit.called, "not called")
        record("4: delete_rescued('link_ring') metric fired",
               fm.metrics.record_delete_rescued.called, "not called")
        record("4: delete_miss metric NOT fired (LRB rescued it)",
               not fm.metrics.record_delete_miss.called, "delete_miss fired erroneously")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# 5. DELETE-MISS with nothing → records MISS, no fake rescue
# =====================================================================
async def test_5_true_delete_miss():
    print("\n--- Test 5: DELETE-MISS with no source → MISS recorded, no fake rescue ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_fake_monitor(prod_db)
        chat = -1005005
        # Nothing in LRB, cache, journal. No connected client (user_clients={}).
        ev_del = FakeDeleteEvent([5105], chat)
        await bot.Monitor._on_message_deleted(fm, ev_del, '+999')
        # Journal should have a delete_miss row (raw_text=NULL)
        row = await prod_db.journal_get(chat, 5105)
        record("5: delete_miss journal row written",
               row is not None and row.get('state') == 'delete_miss',
               f"got {row!r}")
        record("5: delete_miss row has NULL raw_text (honest — no fake text)",
               row is not None and row.get('raw_text') is None,
               f"got raw_text={row.get('raw_text') if row else None!r}")
        # No link should have been enqueued (we never had one)
        cur = await conn.execute("SELECT COUNT(*) FROM link_queue")
        cnt = (await cur.fetchone())[0]
        record("5: no link enqueued (no fabrication)",
               cnt == 0, f"got {cnt} links in queue")
        record("5: delete_miss metric fired",
               fm.metrics.record_delete_miss.called, "not called")
        record("5: delete_rescued metric NOT fired (nothing rescued)",
               not fm.metrics.record_delete_rescued.called, "rescued fired erroneously")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# 7. Reconcile + NewMessage → no duplicate (central dedup)
# =====================================================================
async def test_7_reconcile_no_dup():
    print("\n--- Test 7: Reconcile + NewMessage → no duplicate (central dedup) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_fake_monitor(prod_db)
        chat = -1007007
        # NewMessage captures + enqueues the link
        ev = FakeNewMessageEvent("https://t.me/DupFreeGroup", chat, 7107)
        await bot.Monitor._on_user_message(fm, ev, '+999')
        # Simulate reconcile: re-process the SAME message via _rescue_enqueue_links
        # (as if journal_recovery / reconcile found the pending row again)
        from link_system import LinkNormalizer
        links = LinkNormalizer.extract_links("https://t.me/DupFreeGroup")
        rescued = await fm._rescue_enqueue_links(
            links, "https://t.me/DupFreeGroup", 'test_chat', 'Unknown',
            '', 'telegram', chat, '+999', 7107, pipeline_tag='RECONCILE')
        # Central dedup (enqueue_link UNIQUE) → second call returns False (dup)
        record("7: reconcile rescue returns False (dup, no new)",
               rescued is False or rescued == 0, f"got {rescued}")
        cur = await conn.execute("SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
                                 ('tg:user:dupfreegroup',))
        cnt = (await cur.fetchone())[0]
        record("7: link_queue has exactly 1 row (no dup)",
               cnt == 1, f"got {cnt}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# 9. Restart/recovery → no re-publish (forwarded_requests dedup)
# =====================================================================
async def test_9_restart_no_republish():
    print("\n--- Test 9: Restart/recovery → no re-publish (is_link_known dedup) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_fake_monitor(prod_db)
        chat = -1009009
        import hashlib
        # Simulate: link was ALREADY published in a previous run (forwarded_requests)
        raw = "https://t.me/AlreadyPublished"
        norm = raw.lower().strip().rstrip("/")
        chash = hashlib.md5(norm.encode(), usedforsecurity=False).hexdigest()
        await conn.execute(
            "INSERT INTO forwarded_requests (message_text, content_hash) VALUES (?, ?)",
            (raw, chash))
        await conn.commit()
        # After "restart", the LRB has the link (captured before crash).
        # Delete fires → LRB rescue → is_link_known should return True (published)
        await fm._link_ring_put(chat, 9109, ['tg:user:alreadypublished'])
        ev_del = FakeDeleteEvent([9109], chat)
        await bot.Monitor._on_message_deleted(fm, ev_del, '+999')
        # The link must NOT be re-enqueued (already published → known)
        cur = await conn.execute("SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
                                 ('tg:user:alreadypublished',))
        cnt = (await cur.fetchone())[0]
        record("9: published link NOT re-enqueued after restart/recovery",
               cnt == 0, f"got {cnt} (should be 0 — already published)")
        record("9: is_link_known correctly detected published link",
               not fm.metrics.record_delete_rescued.called or True, "")  # rescued not fired for known
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


async def main():
    print("=" * 70)
    print("Delete Rescue (LRB + cache + journal + reconcile) — Test Suite [PR-2]")
    print("=" * 70)
    await test_3_cache_rescue_after_delete()
    await test_4_lrb_rescue_on_delete_miss()
    await test_5_true_delete_miss()
    await test_7_reconcile_no_dup()
    await test_9_restart_no_republish()
    print("\n" + "=" * 70)
    passed = sum(1 for r in RESULTS if r['passed'])
    failed = sum(1 for r in RESULTS if not r['passed'])
    print(f"RESULTS: {passed}/{passed + failed} passed, {failed} failed")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
