#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot Sender Filter Test
=====================
[user-request — BOT-FILTER] يثبت أن البوت لا يسحب أي رابط أو طلب من أي بوت كان.

السيناريوهات المُختبَرة:

  Test 1 — _sender_is_bot helper correctness:
    None → False | bot=True → True | bot=False → False | بلا سمة bot → False
    | يرفع استثناء → False (دفاعي). لا يكسر الالتقاط أبدًا.

  Test 2 — NewMessage: رسالة بوت → لا إرسال، لا LRB، لا cache:
    رسالة بوت فيها رابط + كلمة طلب → send_message لا يُستدعى أبدًا،
    LRB فارغ للرسالة، _msg_cache فارغ. المسار كامل يُتجاهل البوت.

  Test 3 — NewMessage: بوت → ينظّف LRB entry كتبه الـraw hook (سباق):
    لو الـraw hook كتب روابط البوت في LRB قبل NewMessage، الحارس في
    NewMessage ينظّفها (pop) قبل return. لا يُنقذ لاحقًا روابط البوت.

  Test 4 — NewMessage: مستخدم عادي → يستمر (الحارس لا يكسر المسار):
    رسالة مستخدم عادي (sender.bot=False) فيها رابط → LRB يحوي الرابط،
    مسار الطلبات يُشغّل. الحارس لا يحجب المستخدمين الحقيقيين.

  Test 5 — Static: الحارس في _on_user_message يسبق extract_links و _handle_request_path:
    ترتيب الكود: _sender_is_bot يُفحص BEFORE LinkNormalizer.extract_links
    و BEFORE _handle_request_path. لو ترتيب الكود انكسر، الاختبار يفشل.

  Test 6 — Static: _sender_is_bot مُشار إليه في _poll_one_chat:
    مسار polling scanner يحوي فحص البوت.

  Test 7 — Static: _sender_is_bot مُشار إليه في _reconcile_chat_after_delete_miss:
    مسار reconcile يحوي فحص البوت.

  Test 8 — Static: _sender_is_bot مُشار إليه في _on_message_deleted:
    مسار get_messages rescue داخل _on_message_deleted يحوي فحص البوت.

  Test 9 — Polling integration: بوت مُجاهل، مستخدم يُعالَج:
    _poll_one_chat يسحب [bot_msg, user_msg] → bot_msg لا يدخل _msg_cache،
    user_msg يدخله. يثبت أن polling لا يسحب من البوتات.

NO Telegram credentials في هذه البيئة — SIMULATION ONLY.
"""
import asyncio
import os
import sys
import types
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('BOT_TOKEN', '123:test')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')  # قناة الروابط القديمة
os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'testhash')
os.environ.setdefault('OWNER_ID', '12345')
os.environ.setdefault('SUPABASE_URL', '')
os.environ.setdefault('SUPABASE_KEY', '')
os.environ.setdefault('REQUESTS_TARGET_CHANNEL', '@dhkskwksjskwk')  # قناة الطلبات

import logging
logging.disable(logging.CRITICAL)

import bot  # noqa: E402
from link_system import ProductionDB, LinkNormalizer  # noqa: E402
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
    await conn.execute("""CREATE TABLE IF NOT EXISTS link_state (
        normalized_link TEXT PRIMARY KEY, state TEXT, raw_link TEXT, group_title TEXT,
        joined_by TEXT, member_count INTEGER, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        error TEXT)""")
    await conn.commit()
    db = types.SimpleNamespace(_ensure_conn=AsyncMock(return_value=conn), _lock=asyncio.Lock())
    db.check_link_exists = types.MethodType(bot.DatabaseManager.check_link_exists, db)
    db.delete_forwarded_request = types.MethodType(
        bot.DatabaseManager.delete_forwarded_request, db)
    return ProductionDB(db), path, conn


class FakeSender:
    """مُحاكي كيان مُرسِل Telethon. bot=True للبوتات."""
    def __init__(self, is_bot=False, first_name="User", username="user"):
        self.bot = is_bot
        self.first_name = first_name
        self.username = username


class RaisingBotAttr:
    """يحاكي sender يرفع استثناء عند قراءة .bot (دفاعي)."""
    @property
    def bot(self):
        raise RuntimeError("simulated attribute error")


class FakeNewMessageEvent:
    def __init__(self, raw_text, chat_id, msg_id, sender_id=42, chat=None, sender=None):
        self.raw_text = raw_text
        self.chat_id = chat_id
        self.id = msg_id
        self.sender_id = sender_id
        self.chat = chat
        self.sender = sender


def make_monitor(prod_db, channel_id=-1001234567890,
                 requests_target_channel='@dhkskwksjskwk'):
    """يبني namespace مُحاكى مع REAL bot.Monitor methods + bot_client مُحاكى."""
    cfg = types.SimpleNamespace(
        journal_enabled=False, delete_miss_reconcile=False,
        journal_retention_s=86400, journal_no_text_retention_s=21600,
        channel_id=channel_id, journal_recovery_enabled=False,
        requests_target_channel=requests_target_channel,
    )

    class SendMock:
        def __init__(self):
            self.calls = []
        async def __call__(self, *args, **kwargs):
            self.calls.append({
                'target': args[0] if args else kwargs.get('entity'),
                'alert': args[1] if len(args) > 1 else kwargs.get('message', ''),
                'kwargs': kwargs,
            })
        @property
        def called(self):
            return len(self.calls) > 0
        @property
        def call_count(self):
            return len(self.calls)
        def reset_mock(self):
            self.calls = []

    send_mock = SendMock()
    bot_client = MagicMock()
    bot_client.is_connected = MagicMock(return_value=True)
    bot_client.send_message = send_mock

    fm = types.SimpleNamespace(
        config=cfg, prod_db=prod_db,
        message_claim=None,  # نبسّط: لا claim في اختبارات البوت
        _msg_cache={}, _msg_cache_lock=asyncio.Lock(),
        metrics=types.SimpleNamespace(
            record_skip=AsyncMock(), record_duplicate=AsyncMock(),
            record_link_capture=AsyncMock(), record_link_ring_hit=AsyncMock(),
            record_delete_miss=AsyncMock(), record_delete_rescued=AsyncMock(),
            record_reconcile_rescued=AsyncMock(), record_link_forwarded=AsyncMock(),
        ),
        _link_ring={}, _link_ring_lock=asyncio.Lock(),
        _link_ring_ts={}, _link_ring_ttl=300, _link_ring_cap=20000,
        _link_ring_evicted=0, _link_ring_hits=0,
        user_clients={}, source_registry=None,
        _delete_miss_log_ts={}, _delete_miss_count={}, _no_text_count=0,
        _reconcile_inflight=set(), _chat_poll_failures={},
        _polling_state={}, _polling_lock=asyncio.Lock(), _active_polling_chats=[],
        bot_client=bot_client,
        floodwait_mgr=None,
    )
    # instance methods — bind with MethodType so self=fm
    for method_name in (
        '_journal_enabled', '_journal_write', '_journal_set_state_safe',
        '_journal_mark_deleted_safe', '_record_delete_miss',
        '_rescue_enqueue_links', '_spawn_reconcile',
        '_reconcile_chat_after_delete_miss', '_journal_recovery',
        '_link_ring_put', '_link_ring_pop', '_link_ring_evict',
        '_normalized_to_link_data', '_rescue_link_only',
        '_on_user_message', '_on_message_deleted', '_handle_request_path',
        '_poll_one_chat',
    ):
        setattr(fm, method_name,
                types.MethodType(getattr(bot.Monitor, method_name), fm))
    # staticmethods — assign raw function (NO MethodType binding) so
    # fm._sender_is_bot(x) calls func(x) with one arg, not func(fm, x).
    # types.MethodType on a staticmethod would bind fm as first arg → TypeError.
    fm._sender_is_bot = bot.Monitor._sender_is_bot
    fm._get_sender_name = bot.Monitor._get_sender_name
    fm._send_mock = send_mock
    return fm


# =========================================================================
# Test 1 — _sender_is_bot helper correctness
# =========================================================================
def test_helper_correctness():
    print("\n=== Test 1: _sender_is_bot helper correctness ===")
    cases = [
        ("None sender → False", None, False),
        ("bot=True → True", FakeSender(is_bot=True), True),
        ("bot=False → False", FakeSender(is_bot=False), False),
        ("no bot attr (channel-like) → False", types.SimpleNamespace(title="Grp"), False),
        ("raises on .bot → False (defensive)", RaisingBotAttr(), False),
    ]
    all_pass = True
    for name, sender, expected in cases:
        got = bot.Monitor._sender_is_bot(sender)
        ok = (got == expected)
        record(name, ok, f"expected={expected} got={got}")
        if not ok:
            all_pass = False
    return all_pass


# =========================================================================
# Test 2 — NewMessage: bot message → no send, no LRB, no cache
# =========================================================================
async def test_newmessage_bot_skipped():
    print("\n=== Test 2: NewMessage bot message → no send/LRB/cache ===")
    prod_db, dbpath, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        chat_id = -100555000111
        msg_id = 9001
        bot_sender = FakeSender(is_bot=True, first_name="PromoBot", username="promo_bot")
        # رسالة بوت فيها رابط + كلمة طلب — يجب أن تُتجاهل بالكامل
        raw = "مين يحل لي واجب؟ https://t.me/joinchat/BotLink1"
        ev = FakeNewMessageEvent(raw, chat_id, msg_id,
                                  sender_id=7770001, sender=bot_sender)
        await fm._on_user_message(ev, '966500000001')
        no_send = (fm._send_mock.call_count == 0)
        no_lrb = ((chat_id, msg_id) not in fm._link_ring)
        no_cache = ((chat_id, msg_id) not in fm._msg_cache)
        record("bot message → send_message NOT called", no_send,
               f"call_count={fm._send_mock.call_count}")
        record("bot message → no LRB entry", no_lrb,
               f"link_ring keys={list(fm._link_ring.keys())}")
        record("bot message → no _msg_cache entry", no_cache,
               f"cache keys={list(fm._msg_cache.keys())}")
        return no_send and no_lrb and no_cache
    finally:
        await conn.close()
        try: os.unlink(dbpath)
        except OSError: pass


# =========================================================================
# Test 3 — NewMessage: bot → purges pre-existing LRB entry (raw-hook race)
# =========================================================================
async def test_newmessage_bot_purges_lrb():
    print("\n=== Test 3: NewMessage bot → purges pre-existing LRB entry ===")
    prod_db, dbpath, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        chat_id = -100555000222
        msg_id = 9002
        # حاكِ أن الـraw hook كتب روابط البوت في LRB قبل NewMessage
        key = (chat_id, msg_id)
        fm._link_ring[key] = ['https://t.me/BotPurgedLink']
        fm._link_ring_ts[key] = 1234567890.0
        bot_sender = FakeSender(is_bot=True, first_name="SpamBot")
        raw = "تحميل مجاني https://t.me/joinchat/BotPurgedLink"
        ev = FakeNewMessageEvent(raw, chat_id, msg_id,
                                  sender_id=7770002, sender=bot_sender)
        await fm._on_user_message(ev, '966500000002')
        purged = (key not in fm._link_ring)
        purged_ts = (key not in fm._link_ring_ts)
        no_send = (fm._send_mock.call_count == 0)
        record("bot message → LRB entry purged", purged,
               f"link_ring keys after={list(fm._link_ring.keys())}")
        record("bot message → LRB timestamp purged", purged_ts,
               f"ts keys after={list(fm._link_ring_ts.keys())}")
        record("bot message → no send (purge path)", no_send,
               f"call_count={fm._send_mock.call_count}")
        return purged and purged_ts and no_send
    finally:
        await conn.close()
        try: os.unlink(dbpath)
        except OSError: pass


# =========================================================================
# Test 4 — NewMessage: normal user → continues (guard does not block users)
# =========================================================================
async def test_newmessage_user_continues():
    print("\n=== Test 4: NewMessage normal user → continues (guard transparent) ===")
    prod_db, dbpath, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        chat_id = -100555000333
        msg_id = 9003
        user_sender = FakeSender(is_bot=False, first_name="Ahmed", username="ahmed")
        # رسالة فيها رابط — يجب أن يُكتب في LRB (الحارس شفاف للمستخدمين)
        raw = "شوف هذي المجموعة https://t.me/SomeRealGroup"
        ev = FakeNewMessageEvent(raw, chat_id, msg_id,
                                  sender_id=5550001, sender=user_sender)
        await fm._on_user_message(ev, '966500000003')
        lrb_has = ((chat_id, msg_id) in fm._link_ring)
        record("user message → LRB entry written", lrb_has,
               f"link_ring keys={list(fm._link_ring.keys())}")
        return lrb_has
    finally:
        await conn.close()
        try: os.unlink(dbpath)
        except OSError: pass


# =========================================================================
# Test 5 — Static: guard in _on_user_message precedes extract_links + request path
# =========================================================================
def test_static_guard_ordering_in_on_user_message():
    print("\n=== Test 5: Static — guard precedes extract_links & _handle_request_path ===")
    src = inspect.getsource(bot.Monitor._on_user_message)
    has_guard = '_sender_is_bot' in src
    # ترتيب: guard (BOT-FILTER block) قبل استدعاء extract_links الفعلي
    # و BEFORE استدعاء _handle_request_path(event الفعلي (لا في التعليقات).
    idx_guard = src.find('_sender_is_bot')
    # extract_links call (real invocation, not comment)
    idx_extract = src.find('LinkNormalizer.extract_links')
    # _handle_request_path real call site — نبحث عن النداء الفعلي لا التعليق
    idx_req = src.find('await self._handle_request_path(event')
    order_ok = (idx_guard != -1 and idx_extract != -1 and idx_req != -1
                and idx_guard < idx_extract and idx_guard < idx_req)
    record("_sender_is_bot present in _on_user_message", has_guard)
    record("guard index < extract_links index", idx_guard < idx_extract,
           f"guard={idx_guard} extract={idx_extract}")
    record("guard index < _handle_request_path index", idx_guard < idx_req,
           f"guard={idx_guard} request={idx_req}")
    return has_guard and order_ok


# =========================================================================
# Test 6 — Static: _sender_is_bot referenced in _poll_one_chat
# =========================================================================
def test_static_polling_guard():
    print("\n=== Test 6: Static — _sender_is_bot in _poll_one_chat ===")
    src = inspect.getsource(bot.Monitor._poll_one_chat)
    has = '_sender_is_bot' in src
    record("_sender_is_bot present in _poll_one_chat", has)
    return has


# =========================================================================
# Test 7 — Static: _sender_is_bot referenced in _reconcile_chat_after_delete_miss
# =========================================================================
def test_static_reconcile_guard():
    print("\n=== Test 7: Static — _sender_is_bot in _reconcile_chat_after_delete_miss ===")
    src = inspect.getsource(bot.Monitor._reconcile_chat_after_delete_miss)
    has = '_sender_is_bot' in src
    record("_sender_is_bot present in _reconcile_chat_after_delete_miss", has)
    return has


# =========================================================================
# Test 8 — Static: _sender_is_bot referenced in _on_message_deleted (get_messages rescue)
# =========================================================================
def test_static_delete_rescue_guard():
    print("\n=== Test 8: Static — _sender_is_bot in _on_message_deleted ===")
    src = inspect.getsource(bot.Monitor._on_message_deleted)
    has = '_sender_is_bot' in src
    record("_sender_is_bot present in _on_message_deleted", has)
    return has


# =========================================================================
# Test 9 — Polling integration: bot ignored, user processed
# =========================================================================
async def test_polling_bot_ignored():
    print("\n=== Test 9: Polling — bot ignored, user processed ===")
    prod_db, dbpath, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        chat_id = -100555000444
        bot_msg_id = 9101
        user_msg_id = 9102
        # رسالتان: بوت (يُتجاهل) + مستخدم عادي بلا روابط (يُكتب في cache)
        bot_msg = MagicMock()
        bot_msg.id = bot_msg_id
        bot_msg.raw_text = "تحميل مجاني https://t.me/BotFromPolling"
        bot_msg.out = False
        bot_msg.sender = FakeSender(is_bot=True, first_name="PollBot")
        bot_msg.sender_id = 7770003

        user_msg = MagicMock()
        user_msg.id = user_msg_id
        user_msg.raw_text = "السلام عليكم كيف الحال"
        user_msg.out = False
        user_msg.sender = FakeSender(is_bot=False, first_name="Salem")
        user_msg.sender_id = 5550002
        user_msg.chat = None

        fake_client = MagicMock()
        fake_client.is_connected = MagicMock(return_value=True)
        async def fake_get_messages(chat_id, limit=3, min_id=0):
            return [bot_msg, user_msg]
        fake_client.get_messages = fake_get_messages
        fm.user_clients['966500000004'] = fake_client

        chat = {'chat_id': chat_id, 'chat_title': 'TestGroup', 'username': ''}
        await fm._poll_one_chat('966500000004', chat)

        bot_cached = ((chat_id, bot_msg_id) in fm._msg_cache)
        user_cached = ((chat_id, user_msg_id) in fm._msg_cache)
        record("polling bot message → NOT cached (skipped)", not bot_cached,
               f"cache keys={list(fm._msg_cache.keys())}")
        record("polling user message → cached (processed)", user_cached,
               f"cache keys={list(fm._msg_cache.keys())}")
        return (not bot_cached) and user_cached
    finally:
        await conn.close()
        try: os.unlink(dbpath)
        except OSError: pass


async def main():
    print("=" * 70)
    print("BOT FILTER TEST — لا يسحب أي رابط أو طلب من أي بوت كان")
    print("=" * 70)
    r1 = test_helper_correctness()
    r2 = await test_newmessage_bot_skipped()
    r3 = await test_newmessage_bot_purges_lrb()
    r4 = await test_newmessage_user_continues()
    r5 = test_static_guard_ordering_in_on_user_message()
    r6 = test_static_polling_guard()
    r7 = test_static_reconcile_guard()
    r8 = test_static_delete_rescue_guard()
    r9 = await test_polling_bot_ignored()
    print("\n" + "=" * 70)
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r['passed'])
    print(f"BOT-FILTER RESULTS: {passed}/{total} assertions passed")
    print(f"Test groups: T1={r1} T2={r2} T3={r3} T4={r4} T5={r5} "
          f"T6={r6} T7={r7} T8={r8} T9={r9}")
    print("=" * 70)
    ok = (r1 and r2 and r3 and r4 and r5 and r6 and r7 and r8 and r9
          and passed == total)
    return 0 if ok else 1


if __name__ == '__main__':
    rc = asyncio.run(main())
    sys.exit(rc)
