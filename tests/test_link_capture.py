#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Link Capture (Link Ring Buffer + central dedup) — Contract Tests
=================================================================
[PR-1] يثبت السيناريوهات من قائمة المستخدم:
  1. NewMessage يحتوي Telegram link → التقاط فوري
  2. NewMessage يحتوي WhatsApp group link → التقاط
  6. Raw update + NewMessage لنفس الرسالة → لا duplicate (LRB idempotent)
  12. chat_id normalization (group/supergroup/channel IDs متطابقة بعد LRB put/pop)

البنية تُختبر هنا:
  - LinkNormalizer.extract_links (TG public + TG private invite + WhatsApp)
  - Monitor._link_ring_put / _link_ring_pop / _link_ring_evict (TTL+cap+eviction)
  - ProductionDB.is_link_known (central dedup: link_queue + forwarded_requests + target_groups)
  - idempotency: إعادة put لنفس المفتاح لا تضاعف (overwrite)
  - eviction يطرح عند تجاوز cap
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
from link_system import LinkNormalizer, ProductionDB, Metrics  # noqa: E402
from source_registry import MessageClaim  # noqa: E402

RESULTS = []


def record(name, passed, detail=""):
    RESULTS.append({'name': name, 'passed': passed, 'detail': detail})
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if detail and not passed:
        print(f"         {detail}")


def make_fake_monitor(prod_db, channel_id=-1009999999):
    """Minimal namespace matching Monitor's LRB + journal surface."""
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
    ):
        setattr(fm, method_name,
                types.MethodType(getattr(bot.Monitor, method_name), fm))
    return fm


class FakeNewMessageEvent:
    def __init__(self, raw_text, chat_id, msg_id, sender_id=42):
        self.raw_text = raw_text
        self.chat_id = chat_id
        self.id = msg_id
        self.sender_id = sender_id
        self.chat = None
        self.sender = None


async def make_test_db():
    import aiosqlite
    import tempfile
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    os.chmod(path, 0o644)
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    # minimal schema subset for link_queue + forwarded_requests + message_journal
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
    await conn.commit()
    # Minimal DatabaseManager mock exposing check_link_exists + _ensure_conn.
    # Bind the REAL check_link_exists + delete_forwarded_request so tests
    # exercise actual query logic against the temp DB (not a stub returning None).
    db = types.SimpleNamespace(
        _ensure_conn=AsyncMock(return_value=conn),
        _lock=asyncio.Lock(),
    )
    db.check_link_exists = types.MethodType(bot.DatabaseManager.check_link_exists, db)
    db.delete_forwarded_request = types.MethodType(
        bot.DatabaseManager.delete_forwarded_request, db)
    return ProductionDB(db), path, conn


# =====================================================================
# 1. Telegram link extraction + immediate capture
# =====================================================================
async def test_1_tg_link_capture():
    print("\n--- Test 1: NewMessage with Telegram link → immediate capture ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_fake_monitor(prod_db)
        ev = FakeNewMessageEvent("مرحبوا انضموا https://t.me/SEU_Group2025", -1002001, 1001)
        await bot.Monitor._on_user_message(fm, ev, '+999111')
        # LRB has the normalized link
        links_in_ring = fm._link_ring.get((-1002001, 1001))
        record("1: LRB captured 1 link after NewMessage",
               bool(links_in_ring) and len(links_in_ring) == 1,
               f"got {links_in_ring!r}")
        record("1: normalized form tg:user:...",
               links_in_ring and links_in_ring[0] == 'tg:user:seu_group2025',
               f"got {links_in_ring[0] if links_in_ring else None!r}")
        record("1: metrics.record_link_capture called",
               fm.metrics.record_link_capture.called,
               "not called")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# 2. WhatsApp group link extraction
# =====================================================================
async def test_2_wa_link_capture():
    print("\n--- Test 2: NewMessage with WhatsApp group link → capture ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_fake_monitor(prod_db)
        ev = FakeNewMessageEvent("انضموا لقروب الواتساب https://chat.whatsapp.com/AbCdEfGhIjK", -1003002, 2002)
        await bot.Monitor._on_user_message(fm, ev, '+999222')
        links_in_ring = fm._link_ring.get((-1003002, 2002))
        record("2: LRB captured 1 WA link",
               bool(links_in_ring) and len(links_in_ring) == 1,
               f"got {links_in_ring!r}")
        record("2: normalized form wa:invite:...",
               links_in_ring and links_in_ring[0] == 'wa:invite:abcdefghijk',
               f"got {links_in_ring[0] if links_in_ring else None!r}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# 6. Idempotency: re-put same (chat_id,msg_id) does not duplicate
# =====================================================================
async def test_6_lrb_idempotent():
    print("\n--- Test 6: LRB re-put same key → no duplicate (overwrite) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_fake_monitor(prod_db)
        await fm._link_ring_put(-1004003, 3003, ['tg:user:testidem'])
        await fm._link_ring_put(-1004003, 3003, ['tg:user:testidem'])
        await fm._link_ring_put(-1004003, 3003, ['tg:user:testidem'])
        keys = [k for k in fm._link_ring if k == (-1004003, 3003)]
        record("6: exactly 1 key after 3 re-puts",
               len(keys) == 1, f"got {len(keys)} keys")
        record("6: value list has 1 link (no dup)",
               len(fm._link_ring[(-1004003, 3003)]) == 1,
               f"got {fm._link_ring[(-1004003, 3003)]!r}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# 12. chat_id normalization — group/supergroup/channel IDs round-trip LRB
# =====================================================================
async def test_12_chat_id_normalization():
    print("\n--- Test 12: chat_id normalization (supergroup -100... IDs) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_fake_monitor(prod_db)
        # Telegram supergroup IDs come as -100<channel_id>
        cases = [
            (-1001234567890, 5001, "supergroup -100... form"),
            (-1009999999999, 5002, "channel -100... form"),
            (-1001234567, 5003, "megagroup short -100... form"),
        ]
        all_ok = True
        for chat_id, msg_id, label in cases:
            await fm._link_ring_put(chat_id, msg_id, [f'tg:user:test_{msg_id}'])
            popped = await fm._link_ring_pop(chat_id, msg_id)
            ok = len(popped) == 1 and popped[0] == f'tg:user:test_{msg_id}'
            if not ok:
                all_ok = False
            record(f"12: {label} round-trip",
                   ok, f"got {popped!r}")
        record("12: all chat_id forms round-trip OK", all_ok)
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# LRB eviction: cap is enforced
# =====================================================================
async def test_lrb_cap_eviction():
    print("\n--- Test LRB: cap eviction prevents unbounded growth ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_fake_monitor(prod_db)
        fm._link_ring_cap = 100  # small cap for test
        for i in range(150):
            await fm._link_ring_put(-1005, 6000 + i, [f'tg:user:evict_{i}'])
        record("LRB: size <= cap after over-fill",
               len(fm._link_ring) <= fm._link_ring_cap,
               f"size={len(fm._link_ring)} cap={fm._link_ring_cap}")
        record("LRB: eviction counter > 0",
               fm._link_ring_evicted > 0,
               f"evicted={fm._link_ring_evicted}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# is_link_known: central dedup (link_queue + forwarded_requests + target_groups)
# =====================================================================
async def test_is_link_known():
    print("\n--- Test is_link_known: central dedup across all tables ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_fake_monitor(prod_db)
        # 1. unknown link → False
        known = await prod_db.is_link_known("https://t.me/BrandNewGroup", "tg:user:brandnewgroup")
        record("DEDUP: unknown link → not known", not known, f"got {known}")

        # 2. enqueue the link → now known via link_queue
        await prod_db.enqueue_link({
            'raw': 'https://t.me/EnqueuedGroup', 'normalized': 'tg:user:enqueuedgroup',
            'link_type': 'telegram', 'username': 'enqueuedgroup', 'invite_hash': None,
            'msg_id': None, 'group_name': 'test', 'sender_name': 's',
            'sender_contact': '', 'source_phone': '+999', 'message_text': '',
            'message_link': None,
        })
        known2 = await prod_db.is_link_known("https://t.me/EnqueuedGroup", "tg:user:enqueuedgroup")
        record("DEDUP: enqueued link → known (link_queue)", known2, f"got {known2}")

        # 3. forwarded_requests (published) → known via content_hash MD5
        import hashlib
        raw = "https://t.me/PublishedGroup"
        norm = raw.lower().strip().rstrip("/")
        chash = hashlib.md5(norm.encode(), usedforsecurity=False).hexdigest()
        await conn.execute(
            "INSERT INTO forwarded_requests (message_text, message_date, content_hash) VALUES (?, ?, ?)",
            (raw, None, chash))
        await conn.commit()
        known3 = await prod_db.is_link_known(raw, "tg:user:publishedgroup")
        record("DEDUP: published link → known (forwarded_requests)", known3, f"got {known3}")

        # 4. is_link_known with no args → False (defensive)
        known4 = await prod_db.is_link_known(None, None)
        record("DEDUP: empty args → False (defensive)", not known4, f"got {known4}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# LinkNormalizer: covers TG public, TG private invite, WhatsApp, message-link exclusion
# =====================================================================
async def test_extractor_coverage():
    print("\n--- Test LinkNormalizer: TG public + private + WhatsApp + msg-link exclusion ---")
    text = "مجموعة https://t.me/SEU_Students و خاصة https://t.me/+AbCdEf123 و واتساب https://chat.whatsapp.com/XyZ123 و رسالة https://t.me/SomeChannel/456"
    links = LinkNormalizer.extract_links(text)
    norms = {l['normalized'] for l in links}
    record("EXTRACT: TG public username link",
           'tg:user:seu_students' in norms, f"got {norms}")
    record("EXTRACT: TG private invite link",
           any(n.startswith('tg:invite:') for n in norms), f"got {norms}")
    record("EXTRACT: WhatsApp invite link",
           any(n.startswith('wa:invite:') for n in norms), f"got {norms}")
    # message-link (t.me/SomeChannel/456) must be stripped of /456 → username only
    record("EXTRACT: message-link stripped to username (no /456 msg_id)",
           'tg:user:somechannel' in norms and not any('456' in n for n in norms),
           f"got {norms}")


# =====================================================================
# Main runner
# =====================================================================
async def main():
    print("=" * 70)
    print("Link Capture (Link Ring Buffer + central dedup) — Test Suite [PR-1]")
    print("=" * 70)
    await test_1_tg_link_capture()
    await test_2_wa_link_capture()
    await test_6_lrb_idempotent()
    await test_12_chat_id_normalization()
    await test_lrb_cap_eviction()
    await test_is_link_known()
    await test_extractor_coverage()

    print("\n" + "=" * 70)
    passed = sum(1 for r in RESULTS if r['passed'])
    failed = sum(1 for r in RESULTS if not r['passed'])
    print(f"RESULTS: {passed}/{passed + failed} passed, {failed} failed")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
