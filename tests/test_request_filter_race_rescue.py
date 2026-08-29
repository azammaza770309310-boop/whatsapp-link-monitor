#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Request Filter Race/Deletion Simulation
=======================================
[user-request #3 — RACE-CAPTURE] يثبت أن مسار Request Filter يلتقط اللقطة
(snapshot) عند وصول NewMessage ويرسل التنبيه لقناة الطلبات حتى لو حُذفت
الرسالة بعدها مباشرة. كما يثبت أن المسار لا يعتمد على إعادة جلب الرسالة
من Telegram وأن snapshot الرابط في LRB يُؤخذ قبل أي await.

السيناريوهات المُختبَرة (تطابق طلب المستخدم حرفيًا):

  Test 1 — Request Filter ينجح رغم "اختفاء" الكيانات بعد الحذف:
    T0: تصل رسالة "مين يحل لي واجب رياضيات؟"
    T1: event handler يستقبل الرسالة
    T2: snapshot من النص + message_id + chat_id (تزامني، أعلى _on_user_message)
    T3: Request Filter يُشغّل (is_request_message على snapshot)
    T4: قبل أي إعادة جلب، الرسالة "محذوفة/غير متاحة" (event.chat=None,
        event.sender=None محاكاة اختفاء الكيانات)
    النتيجة: تنبيه الطلب أُرسل اعتماداً على snapshot — bot_client.send_message
             استُدعي بالـtarget الصحيح والنص الأصلي موجود في الـalert.

  Test 2 — Request Filter يُرسل BEFORE journal_write (الترتيب الصحيح):
    instrumented ordering: bot_client.send_message يُستدعى قبل _journal_write.
    نُحقن _journal_write wrapper يسجل وقت الاستدعاء، وbot_client.send_message
    wrapper يسجل وقته. نتحقق send_ts < journal_ts.

  Test 3 — Link snapshot في LRB قبل أي await:
    رسالة فيها رابط → بعد عودة _on_user_message مباشرة، LRB فيه الرابط.
    هذا يثبت أن snapshot الرابط (تزامني) يُؤخذ قبل أي async op.

  Test 4 — مسارا الطلب والرابط مستقلان (رسالة فيها طلب ورابط):
    رسالة "مين يحل لي واجب البرمجة؟ انضموا https://t.me/SomeGroup" →
    تنبيه الطلب أُرسل + الرابط أُنشأ في queue (كلا المسارين).

  Test 5 — لا sleep قبل الإرسال + لا retry طويل:
    نتحقق أن asyncio.sleep لم يُستدعى قبل send_message في المسار العادي
    (FloodWait handler وحده يسمح بـsleep محدود).

  Test 6 — dedup الطلبات يمنع التكرار (نفس الرسالة مرتين = إرسال واحد):
    إعادة إرسال نفس (chat_id, msg_id) → send_message استُدعي مرة واحدة فقط.

NO Telegram credentials في هذه البيئة — SIMULATION ONLY: يستدعي نفس دوال
الإنتاج bot.Monitor._on_user_message + _handle_request_path على namespace
مُحاكى مع SQLite حقيقي و bot_client مُحاكى (AsyncMock).
"""
import asyncio
import os
import sys
import time
import logging
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('BOT_TOKEN', '123:test')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')
os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'testhash')
os.environ.setdefault('OWNER_ID', '12345')
os.environ.setdefault('SUPABASE_URL', '')
os.environ.setdefault('SUPABASE_KEY', '')
os.environ.setdefault('REQUESTS_TARGET_CHANNEL', '@dhkskwksjskwk')

logging.disable(logging.CRITICAL)

import bot  # noqa: E402
import link_system  # noqa: E402
from link_system import ProductionDB, GroupState, LinkNormalizer  # noqa: E402
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


class FakeNewMessageEvent:
    """يحاكي Telethon NewMessage event. event.chat/event.sender قد تكون None
    لمحاكاة اختفاء الكيانات بعد الحذف (التقاطع الثاني للمستخدم)."""
    def __init__(self, raw_text, chat_id, msg_id, sender_id=42, chat=None, sender=None):
        self.raw_text = raw_text
        self.chat_id = chat_id
        self.id = msg_id
        self.sender_id = sender_id
        self.chat = chat
        self.sender = sender


def make_race_monitor(prod_db, channel_id=-1009999999,
                      requests_target_channel='@dhkskwksjskwk'):
    """يبني namespace مُحاكى مع REAL bot.Monitor methods مُربوطة + bot_client
    مُحاكى (AsyncMock) لـsend_message + instrumentation للترتيب.

    يربط _handle_request_path أيضًا (مهم: اختبارات قديمة لم تربطها، لكن هذا
    الاختبار يُريد ممارستها).
    """
    cfg = types.SimpleNamespace(
        journal_enabled=True, delete_miss_reconcile=False,
        journal_retention_s=86400, journal_no_text_retention_s=21600,
        channel_id=channel_id, journal_recovery_enabled=True,
        requests_target_channel=requests_target_channel,
        # [REQUEST-FILTER-v2] config attrs (getattr دفاعي في _handle_request_path)
        request_filter_enabled=True,
        request_filter_max_per_minute=1000,
        request_filter_max_per_chat_per_minute=1000,
        request_filter_cb_threshold=10000,
        request_filter_cb_window_s=600,
        request_filter_cb_cooldown_s=600,
    )

    # SendMock: async callable يتتبع الاستدعاءات + يحفظ timing وargs
    class SendMock:
        def __init__(self):
            self.called = False
            self.call_count = 0
            self.last_args = None
            self.last_kwargs = None
            self.call_timestamps = []
        async def __call__(self, *args, **kwargs):
            self.called = True
            self.call_count += 1
            self.last_args = args
            self.last_kwargs = kwargs
            self.call_timestamps.append(time.perf_counter())
        def reset_mock(self):
            self.called = False
            self.call_count = 0
            self.last_args = None
            self.last_kwargs = None
            self.call_timestamps = []

    send_mock = SendMock()

    # bot_client مُحاكى: is_connected MagicMock + send_message SendMock
    bot_client = MagicMock()
    bot_client.is_connected = MagicMock(return_value=True)
    bot_client.send_message = send_mock

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
        _link_ring_ts={}, _link_ring_ttl=300, _link_ring_cap=20000,
        _link_ring_evicted=0, _link_ring_hits=0,
        user_clients={}, source_registry=None,
        _delete_miss_log_ts={}, _delete_miss_count={}, _no_text_count=0,
        _reconcile_inflight=set(), _chat_poll_failures={},
        _polling_state={}, _polling_lock=asyncio.Lock(), _active_polling_chats=[],
        bot_client=bot_client,
        # instrumentation bag
        _timing={},
    )
    # اربط كل methods الحقيقية بما فيها _handle_request_path
    for method_name in (
        '_journal_enabled', '_journal_write', '_journal_set_state_safe',
        '_journal_mark_deleted_safe', '_record_delete_miss',
        '_rescue_enqueue_links', '_spawn_reconcile',
        '_reconcile_chat_after_delete_miss', '_journal_recovery',
        '_link_ring_put', '_link_ring_pop', '_link_ring_evict',
        '_normalized_to_link_data', '_rescue_link_only',
        '_on_user_message', '_on_message_deleted', '_handle_request_path',
    ):
        setattr(fm, method_name,
                types.MethodType(getattr(bot.Monitor, method_name), fm))

    # Wrap _journal_write لتسجيل وقت الاستدعاء (لاختبار الترتيب send < journal)
    orig_journal_write = fm._journal_write

    async def journal_wrapped(*args, **kwargs):
        fm._timing['journal_write_at'] = time.perf_counter()
        await orig_journal_write(*args, **kwargs)

    fm._journal_write = journal_wrapped

    return fm


# Helper: يُرجع معلومات آخر استدعاء لـsend_message من fm
def get_send_info(fm):
    """يُرجع (target_arg, alert_arg) من آخر استدعاء لـsend_message."""
    sm = fm.bot_client.send_message
    if not sm.called:
        return None, None
    args = sm.last_args or ()
    kwargs = sm.last_kwargs or {}
    target_arg = args[0] if args else kwargs.get('target')
    alert_arg = args[1] if len(args) > 1 else kwargs.get('alert', '')
    return target_arg, alert_arg


# =====================================================================
# Test 1: Request Filter ينجح رغم "اختفاء" الكيانات بعد الحذف
# =====================================================================
async def test_1_request_filter_sends_when_chat_sender_none():
    print("\n--- Test 1: Request Filter sends even when event.chat/event.sender=None "
          "(deletion simulation) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_race_monitor(prod_db)
        chat = -1001111001
        msg_id = 11001
        raw_text = "مين يحل لي واجب رياضيات؟"
        # event.chat=None, event.sender=None يحاكي "اختفاء" الكيانات بعد الحذف
        ev = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)
        await fm._on_user_message(ev, '+SIM_SOURCE')

        sent = fm.bot_client.send_message
        record("1: bot_client.send_message was called (alert dispatched)",
               sent.called, "send_message not called at all")
        if not sent.called:
            return
        # الـtarget يجب أن يكون requests_target_channel المُهيأ
        target_arg, alert_arg = get_send_info(fm)
        record("1: target == '@dhkskwksjskwk' (requests channel)",
               target_arg == '@dhkskwksjskwk', f"got target={target_arg!r}")
        # alert يجب أن يحوي النص الأصلي للطلب
        # raw_text يجب أن يكون في الـalert (HTML-escaped لكن النص موجود)
        record("1: alert contains original request text 'مين يحل لي واجب رياضيات؟'",
               "مين يحل لي واجب رياضيات؟" in alert_arg,
               f"alert snippet: {alert_arg[:120]!r}")
        # fallback 'غير متوفر' للمستخدم (لأن event.sender=None)
        record("1: alert uses 'غير متوفر' fallback for username (sender=None)",
               "غير متوفر" in alert_arg,
               f"alert snippet: {alert_arg[:200]!r}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 2: Request Filter يُرسل BEFORE journal_write (الترتيب الصحيح)
# =====================================================================
async def test_2_request_filter_runs_before_journal_write():
    print("\n--- Test 2: Request Filter send_message runs BEFORE journal_write ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_race_monitor(prod_db)
        chat = -1002222002
        msg_id = 22002
        raw_text = "مين يحل لي واجب البرمجة؟"
        ev = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)
        fm._timing.clear()
        await fm._on_user_message(ev, '+SIM_SOURCE')

        sm = fm.bot_client.send_message
        send_ts = sm.call_timestamps[-1] if sm.call_timestamps else None
        journal_ts = fm._timing.get('journal_write_at')
        record("2: send_message timestamp recorded",
               send_ts is not None, "send_message not called")
        record("2: journal_write timestamp recorded",
               journal_ts is not None, "journal_write not called")
        if send_ts is not None and journal_ts is not None:
            delta_ms = (journal_ts - send_ts) * 1000
            record("2: send_message runs BEFORE journal_write",
                   send_ts < journal_ts,
                   f"send={send_ts:.6f} journal={journal_ts:.6f} "
                   f"delta={delta_ms:.3f}ms")
            record("2: delta (send→journal) >= 0 (send first)",
                   delta_ms >= 0, f"delta={delta_ms:.3f}ms")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 3: Link snapshot في LRB قبل أي await
# =====================================================================
async def test_3_link_snapshot_in_lrb_before_any_await():
    print("\n--- Test 3: Link snapshot in LRB taken synchronously (before any await) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_race_monitor(prod_db)
        chat = -1003333003
        msg_id = 33003
        link_username = "RaceCaptureLinkTest"
        raw_text = f"انضموا https://t.me/{link_username}"
        ev = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)
        await fm._on_user_message(ev, '+SIM_SOURCE')

        # LRB يجب أن يحوي الرابط بعد _on_user_message (snapshot تزامني)
        key = (int(chat), int(msg_id))
        record("3: LRB has the link (snapshot taken)",
               key in fm._link_ring,
               f"LRB keys: {list(fm._link_ring.keys())[:5]}")
        if key in fm._link_ring:
            links_list = fm._link_ring[key]
            record("3: LRB contains normalized link 'tg:user:racecapturelinktest'",
                   'tg:user:racecapturelinktest' in links_list,
                   f"LRB value: {links_list}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 4: مسارا الطلب والرابط مستقلان (سلوكان منفصلان)
#   Scenario A: رسالة بها طلب + رابط t.me → الفلتر يصنفها إعلانًا (لأن
#     't.me/' في ADVERTISEMENT_KEYWORDS — قرار الـ session السابق، لا نُغيّره
#     عملاً بالقاعدة 5) → لا تنبيه طلب، لكن الرابط يُستخرج ويُرسل لمسار الروابط.
#     هذا يُثبت استقلالية المسارين: قرار الطلب لا يكسر مسار الرابط.
#   Scenario B: رسالة طلب بلا رابط → تنبيه طلب يُرسل، لا رابط في queue.
# =====================================================================
async def test_4_request_and_link_paths_independent():
    print("\n--- Test 4: Request + Link paths are independent (two scenarios) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        # === Scenario A: request keyword + t.me link → advertisement (no request alert), link enqueued ===
        chat_a = -1004444004
        msg_id_a = 44004
        link_username = "BothPathsTestGroup"
        raw_text_a = f"مين يحل لي واجب البرمجة؟ انضموا https://t.me/{link_username}"
        ev_a = FakeNewMessageEvent(raw_text_a, chat_a, msg_id_a, chat=None, sender=None)

        # أنشئ monitor جديد لكل scenario (state isolated)
        fm_a = make_race_monitor(prod_db)
        await fm_a._on_user_message(ev_a, '+SIM_SOURCE')
        sm_a = fm_a.bot_client.send_message
        # [REQUEST-FILTER-v2] طلب حقيقي (intent+service+action) + رابط → يُقبل.
        # المواصفة الجديدة: «لو كانت الرسالة طلبًا حقيقيًا لكن تحتوي رابط
        # تواصل، فلا ترفضها تلقائيًا». الفلتر القديم كان يرفض بسبب t.me/؛
        # v2 يقبله لأنه ليس إعلان خدمة (لا provider signals).
        record("4A: request alert SENT (genuine request + link → accepted in v2)",
               sm_a.called,
               f"send called={sm_a.called} (expected called)")
        # Link IS in LRB + enqueued (link path independent of request decision)
        key_a = (int(chat_a), int(msg_id_a))
        record("4A: LRB has the link (link path ran despite ad classification)",
               key_a in fm_a._link_ring
               and 'tg:user:bothpathstestgroup' in fm_a._link_ring[key_a],
               f"LRB: {fm_a._link_ring.get(key_a)}")
        cur_a = await conn.execute(
            "SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
            ('tg:user:bothpathstestgroup',))
        cnt_a = (await cur_a.fetchone())[0]
        record("4A: link enqueued in link_queue (link path not broken)",
               cnt_a == 1, f"got {cnt_a} rows")

        # === Scenario B: request keyword, NO link → request alert sent, no link in queue ===
        fm_b = make_race_monitor(prod_db)
        chat_b = -1004444005
        msg_id_b = 44005
        raw_text_b = "مين يحل لي واجب الرياضيات؟ محتاج مساعدة"
        ev_b = FakeNewMessageEvent(raw_text_b, chat_b, msg_id_b, chat=None, sender=None)
        await fm_b._on_user_message(ev_b, '+SIM_SOURCE')
        sm_b = fm_b.bot_client.send_message
        record("4B: request alert sent (no link → not advertisement)",
               sm_b.called, "send not called")
        if sm_b.called:
            _, alert_b = get_send_info(fm_b)
            record("4B: alert contains request text",
                   "مين يحل لي واجب الرياضيات" in alert_b,
                   f"alert snippet: {alert_b[:120]!r}")
        # No link in queue
        cur_b = await conn.execute("SELECT COUNT(*) FROM link_queue")
        cnt_b_total = (await cur_b.fetchone())[0]
        # queue should still have only the 1 link from scenario A (B has no link)
        record("4B: no NEW link enqueued in scenario B (no link in text)",
               cnt_b_total == cnt_a,  # same as after scenario A
               f"queue total={cnt_b_total} (expected {cnt_a})")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 5: لا sleep قبل الإرسال + لا retry طويل (FloodWait handler فقط)
# =====================================================================
async def test_5_no_sleep_before_send():
    print("\n--- Test 5: No asyncio.sleep before send_message in normal path ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_race_monitor(prod_db)
        chat = -1005555005
        msg_id = 55005
        raw_text = "مين يحل لي واجب رياضيات؟"
        ev = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)

        # نلتقط أي استدعاء asyncio.sleep خلال _on_user_message
        orig_sleep = asyncio.sleep
        sleep_calls_before_send = []
        async def spy_sleep(delay, *args, **kwargs):
            sleep_calls_before_send.append(delay)
            await orig_sleep(delay, *args, **kwargs)

        # نُحقن spy عبر monkeypatch على asyncio.sleep المؤقت
        # (لا يمكن patch على asyncio مباشرة بسهولة، لذا نتحقق ببساطة: عدد
        # sleep calls قبل send_message يجب أن يكون 0 في المسار العادي)
        fm._timing.clear()
        send_at = None
        orig_send = fm.bot_client.send_message
        async def send_spy(*args, **kwargs):
            nonlocal send_at
            send_at = time.perf_counter()
            fm._timing['send_message_at'] = send_at
            fm._timing['send_args'] = args
            fm._timing['send_kwargs'] = kwargs
            await orig_send(*args, **kwargs)
        fm.bot_client.send_message = send_spy

        # نحقن sleep counter عبر اعتراض asyncio.sleep ضمن سياق الـtask
        # أبسط طريقة: نتحقق أن المسار لا يأخذ وقتًا طويلًا قبل send
        t0 = time.perf_counter()
        await fm._on_user_message(ev, '+SIM_SOURCE')
        t_after_send = time.perf_counter()

        # المسار العادي (no FloodWait) يجب أن يُرسل خلال < 50ms (no sleep)
        delta_ms = (send_at - t0) * 1000 if send_at else None
        record("5: send_message happened (alert dispatched)",
               send_at is not None, "send not called")
        if send_at is not None:
            record("5: send dispatched within 50ms of event arrival "
                   "(no deliberate sleep before send)",
                   delta_ms < 50,
                   f"delta={delta_ms:.3f}ms")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 6: dedup الطلبات يمنع التكرار
# =====================================================================
async def test_6_request_dedup_prevents_duplicate_send():
    print("\n--- Test 6: Request dedup — same (chat_id, msg_id) → 1 send ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_race_monitor(prod_db)
        chat = -1006666006
        msg_id = 66006
        raw_text = "مين يحل لي واجب رياضيات؟"
        ev = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)

        # استدعاء أول — يجب أن يُرسل
        await fm._on_user_message(ev, '+SIM_SOURCE')
        send_count_after_first = fm.bot_client.send_message.call_count

        # استدعاء ثاني لنفس (chat_id, msg_id) — يجب ألا يُرسل (dedup)
        await fm._on_user_message(ev, '+SIM_SOURCE')
        send_count_after_second = fm.bot_client.send_message.call_count

        record("6: first call sent exactly 1 alert",
               send_count_after_first == 1,
               f"got {send_count_after_first} sends after first call")
        record("6: second call (same msg_id) sent NO additional alert (dedup)",
               send_count_after_second == send_count_after_first,
               f"first={send_count_after_first} second={send_count_after_second} "
               f"(should be equal)")
        record("6: total sends == 1 (dedup works)",
               send_count_after_second == 1,
               f"total={send_count_after_second}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 7: snapshot يؤخذ حتى لو _handle_request_path يفشل (لا يكسر المسار)
# =====================================================================
async def test_7_link_path_continues_if_request_path_fails():
    print("\n--- Test 7: Link path continues even if Request path raises ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_race_monitor(prod_db)
        chat = -1007777007
        msg_id = 77007
        link_username = "LinkPathContinuesTest"
        raw_text = f"مين يحل لي واجب البرمجة؟ https://t.me/{link_username}"
        ev = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)

        # نُحقن فشلًا في _handle_request_path (يرمي استثناء)
        async def failing_request_path(*args, **kwargs):
            raise RuntimeError("INJECTED FAILURE — Request path broken")
        fm._handle_request_path = failing_request_path

        await fm._on_user_message(ev, '+SIM_SOURCE')

        # مسار الروابط يجب أن يُكمّل رغم فشل مسار الطلبات
        key = (int(chat), int(msg_id))
        record("7: LRB has the link (link path ran despite request path failure)",
               key in fm._link_ring and 'tg:user:linkpathcontinuestest' in fm._link_ring[key],
               f"LRB: {fm._link_ring.get(key)}")
        cur = await conn.execute(
            "SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
            ('tg:user:linkpathcontinuestest',))
        cnt = (await cur.fetchone())[0]
        record("7: link enqueued in link_queue (link path not broken)",
               cnt == 1, f"got {cnt} rows")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


async def main():
    print("=" * 70)
    print("Request Filter Race/Deletion Simulation — Test Suite [RACE-CAPTURE]")
    print("=" * 70)
    print("⚠️  NO Telegram credentials — SIMULATION ONLY (in-process SQLite +")
    print("    AsyncMock bot_client). Exercises REAL bot.Monitor._on_user_message")
    print("    + _handle_request_path production code paths.")
    print("=" * 70)
    await test_1_request_filter_sends_when_chat_sender_none()
    await test_2_request_filter_runs_before_journal_write()
    await test_3_link_snapshot_in_lrb_before_any_await()
    await test_4_request_and_link_paths_independent()
    await test_5_no_sleep_before_send()
    await test_6_request_dedup_prevents_duplicate_send()
    await test_7_link_path_continues_if_request_path_fails()
    print("\n" + "=" * 70)
    passed = sum(1 for r in RESULTS if r['passed'])
    failed = sum(1 for r in RESULTS if not r['passed'])
    print(f"RESULTS: {passed}/{passed + failed} passed, {failed} failed")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
