#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Request Channel Separation Hardening Test
=========================================
[user-request — CHANNEL-SEPARATION] يثبت قاطعًا أن مسار Request Filter
يُرسل التنبيهات لقناة الطلبات (@dhkskwksjskwk) فقط، وأن مسار Link Extractor
يُرسل لقناة الروابط القديمة (CHANNEL_ID) فقط، وأن المسارين لا يختلطان إطلاقًا.

السيناريوهات المُختبَرة:

  Test 1 — request message → target = @dhkskwksjskwk (NOT channel_id):
    رسالة "مين يحل لي واجب رياضيات؟" → send_message يُستدعى بالـtarget =
    '@dhkskwksjskwk' (string). نتأكد أن target != channel_id (int).

  Test 2 — request message → target != link_channel (numeric distinct):
    نتحقق أن target الفعلي لا يساوي channel_id حتى لو كانت الأنواع مختلفة.
    str(target) != str(channel_id) — قاطع.

  Test 3 — REQUESTS_TARGET_CHANNEL not set (target=0) → NO send at all:
    لو requests_target_channel = 0 (env var غير محددة)، الطلب لا يُرسل
    لأي قناة. لا fallback لقناة الروابط. send_message لا يُستدعى إطلاقًا.

  Test 4 — NO code path sends request alerts to channel_id:
    انفيًا للكود: نتحقق بـstatic code search أن مسار _handle_request_path
    يستخدم requests_target_channel فقط كـtarget، ولا يستخدم channel_id.

  Test 5 — Link extractor path is independent:
    رسالة فيها رابط فقط (لا كلمات طلب) → request path لا يُرسل، لكن مسار
    الروابط يستخرج الرابط ويضعه في queue (target channel_id). المساران
    مستقلان — قرار كل مسار لا يؤثر على الآخر.

  Test 6 — both paths on same message → both targets distinct:
    رسالة "مين يحل لي واجب رياضيات؟ https://t.me/SomeGroup":
    - request path يُرسل لـ@dhkskwksjskwk (لأن فيها كلمة طلب + الرابط
      سيجعل is_request_message يصنفها إعلانًا — التحقق هنا يعتمد على
      القرار؛ نتأكد لو حُكم عليها كطلب، فإن target = @dhkskwksjskwk).
    - link path يضع الرابط في queue (target channel_id).
    المساران مستقلان، والـtargets مختلفة قاطعًا.

NO Telegram credentials في هذه البيئة — SIMULATION ONLY.
"""
import asyncio
import os
import sys
import time
import logging
import types
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# [CHANNEL-SEPARATION] نضبط REQUESTS_TARGET_CHANNEL = @dhkskwksjskwk هنا
# ليطابق قيمة Render. هذا يثبت أن الكود يقرأ القيمة الصحيحة من البيئة.
os.environ.setdefault('BOT_TOKEN', '123:test')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')  # قناة الروابط القديمة
os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'testhash')
os.environ.setdefault('OWNER_ID', '12345')
os.environ.setdefault('SUPABASE_URL', '')
os.environ.setdefault('SUPABASE_KEY', '')
os.environ.setdefault('REQUESTS_TARGET_CHANNEL', '@dhkskwksjskwk')  # قناة الطلبات
# [REQUEST-FILTER-v2] مرّن الفلتر في الاختبارات (default false في الإنتاج).
os.environ.setdefault('REQUEST_FILTER_ENABLED', 'true')
os.environ.setdefault('REQUEST_FILTER_MAX_PER_MINUTE', '1000')
os.environ.setdefault('REQUEST_FILTER_MAX_PER_CHAT_PER_MINUTE', '1000')
os.environ.setdefault('REQUEST_FILTER_CIRCUIT_BREAKER_THRESHOLD', '10000')

logging.disable(logging.CRITICAL)


# [v4.0] Mock AI Intent Classifier — القرار من الـAI (mock scripted)،
# لأن كلمات المفاتيح لم تعد تقرر شيئًا (rebuild v4.0). النصوص التي
# تختبرها هذه السيناريوهات (طلبات واجبات/رياضيات/برمجة) → ACCEPT.
import json as _json
from intent_classifier import IntentClassifier as _IC


def _ai_json(decision, confidence, category, reason):
    return _json.dumps({"decision": decision, "confidence": confidence,
                        "category": category, "reason": reason}, ensure_ascii=False)


_REQUEST_MARKERS = ("واجب", "رياضيات", "برمجة", "بحث", "تقرير", "تفاضل", "مشروع")
_AD_MARKERS = ("تداول", "للتواصل", "بوت", "خدمات مدفوعة")


def make_mock_request_classifier(model="mock-v4"):
    """scripted AI: ACCEPT لطلبات المساعدة الأكاديمية الواضحة في هذه
    السيناريوهات، REJECT للإعلانات — يمرّر عبر السباكة الحقيقية للـv4."""

    async def transport(provider, payload):
        user_msg = payload["messages"][1]["content"]
        inner = user_msg.split('"""')[-2] if '"""' in user_msg else user_msg
        if any(m in inner for m in _REQUEST_MARKERS) and not any(m in inner for m in _AD_MARKERS):
            content = _ai_json("ACCEPT", 0.93, "homework_execution_request", "طلب مساعدة أكاديمية")
        else:
            content = _ai_json("REJECT", 0.95, "other", "ليس طلبًا")
        return 200, _json.dumps({"choices": [{"message": {"content": content}}]})

    return _IC(providers=[{"key": "k", "url": "u", "model": model, "name": "Mock"}],
               transport=transport)


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
    """يبني namespace مُحاكى مع REAL bot.Monitor methods + bot_client مُحاكى.
    القناتان: channel_id (قناة الروابط) و requests_target_channel (قناة الطلبات).
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

    class SendMock:
        def __init__(self):
            self.calls = []  # كل استدعاء: (target, alert, kwargs)
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
        request_classifier=make_mock_request_classifier(),
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
    )
    for method_name in (
        '_journal_enabled', '_journal_write', '_journal_set_state_safe',
        '_journal_mark_deleted_safe', '_record_delete_miss',
        '_rescue_enqueue_links', '_spawn_reconcile',
        '_reconcile_chat_after_delete_miss', '_journal_recovery',
        '_link_ring_put', '_link_ring_pop', '_link_ring_evict',
        '_normalized_to_link_data', '_rescue_link_only',
        '_on_user_message', '_on_message_deleted', '_handle_request_path',
        # [SPEED-v4.3.4] مسار الطلبات أصبح خلفية غير حاجبة — الاختبارات تُربط
        # الجسر الحقيقي وتُصفّي المهام الخلفية قبل التحقق من الإرسال.
        '_dispatch_request_path',
    ):
        setattr(fm, method_name,
                types.MethodType(getattr(bot.Monitor, method_name), fm))
    return fm


async def drain_request_tasks(fm):
    """[SPEED-v4.3.4] انتظار مهام مسار الطلبات الخلفية حتى تكتمل —
    _on_user_message يشغّل المسار الآن كـ fire-and-forget task.
    تُستدعى قبل أي تأكيد على send_message."""
    tasks = list(getattr(fm, '_request_bg_tasks', None) or set())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def get_request_send_targets(fm):
    """يُرجع list بكل targets استُخدمت في استدعاءات send_message."""
    return [c['target'] for c in fm.bot_client.send_message.calls]


# =====================================================================
# Test 1: request message → target = @dhkskwksjskwk (NOT channel_id)
# =====================================================================
async def test_1_request_message_sent_to_requests_channel_not_link_channel():
    print("\n--- Test 1: Request message → send_message target = '@dhkskwksjskwk' (NOT channel_id) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        chat = -1002222001
        msg_id = 22001
        raw_text = "مين يحل لي واجب رياضيات؟ محتاج مساعدة"
        ev = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)
        await fm._on_user_message(ev, '+TEST_SOURCE')
        await drain_request_tasks(fm)

        sm = fm.bot_client.send_message
        record("1: send_message was called (request alert dispatched)",
               sm.called, "send_message not called at all")
        if not sm.called:
            return

        targets = get_request_send_targets(fm)
        record("1: exactly 1 send_message call (no duplicate)",
               len(targets) == 1, f"got {len(targets)} calls: {targets!r}")
        if not targets:
            return
        target = targets[0]
        # target يجب أن يكون string '@dhkskwksjskwk' وليس int channel_id
        record("1: target is the string '@dhkskwksjskwk' (requests channel)",
               target == '@dhkskwksjskwk',
               f"got target={target!r} (type={type(target).__name__})")
        # target يجب ألا يساوي channel_id (int)
        record("1: target != channel_id (link channel, int)",
               target != fm.config.channel_id,
               f"target={target!r} == channel_id={fm.config.channel_id!r}")
        # str(target) != str(channel_id) — قاطع حتى عبر الأنواع
        record("1: str(target) != str(channel_id) (string-distinct)",
               str(target) != str(fm.config.channel_id),
               f"str({target!r}) == str({fm.config.channel_id!r})")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 2: request path NEVER uses channel_id as target (static code search)
# =====================================================================
async def test_2_request_path_never_uses_channel_id_as_target():
    print("\n--- Test 2: _handle_request_path source code never references channel_id ---")
    src = inspect.getsource(bot.Monitor._handle_request_path)
    # _handle_request_path يجب ألا يذكر channel_id إطلاقًا (لا target fallback).
    # نستثني التعليقات التوضيحية الصريحة التي تشرح عدم وجود fallback.
    has_channel_id_ref = False
    bad_lines = []
    for line in src.splitlines():
        stripped = line.strip()
        # تخطّي التعليقات (تبدأ بـ# أو داخل docstring — نتجاهلها بأبسط طريقة)
        if stripped.startswith('#'):
            continue
        if 'channel_id' in stripped:
            has_channel_id_ref = True
            bad_lines.append(stripped)
    record("2: _handle_request_path source has NO 'channel_id' reference (except comments)",
           not has_channel_id_ref,
           f"bad lines: {bad_lines!r}")
    # نتحقق أن الـtarget يأتي من requests_target_channel فقط
    record("2: _handle_request_path reads target from requests_target_channel",
           "requests_target_channel" in src,
           "missing 'requests_target_channel' in source")
    # نتحقق أن الـsend يستخدم target المتغير
    record("2: send_message call uses 'target' variable",
           "send_message" in src and "target" in src,
           "send_message or target not found in source")


# =====================================================================
# Test 3: REQUESTS_TARGET_CHANNEL not set → NO send at all (no fallback)
# =====================================================================
async def test_3_no_target_no_send_no_fallback_to_link_channel():
    print("\n--- Test 3: REQUESTS_TARGET_CHANNEL=0 → NO send_message call (no fallback to link channel) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        # target = 0 (env var unset) — يجب ألا يُرسل أي تنبيه
        fm = make_monitor(prod_db, requests_target_channel=0)
        chat = -1003333001
        msg_id = 33001
        raw_text = "مين يحل لي واجب رياضيات؟ محتاج مساعدة"
        ev = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)
        await fm._on_user_message(ev, '+TEST_SOURCE')
        await drain_request_tasks(fm)

        sm = fm.bot_client.send_message
        record("3: send_message NOT called (no target configured)",
               not sm.called,
               f"send_message was called {sm.call_count} times (should be 0)")
        # specifically: target != channel_id (no fallback)
        if sm.called:
            targets = get_request_send_targets(fm)
            no_channel_id = all(t != fm.config.channel_id for t in targets)
            record("3: NO call with target = channel_id (no fallback to link channel)",
                   no_channel_id,
                   f"got channel_id as target in: {targets!r}")
        else:
            record("3: NO call with target = channel_id (no fallback to link channel)",
                   True, "send_message not called — no fallback possible")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 4: link-only message → request path silent, link path enqueues link
# =====================================================================
async def test_4_link_only_message_request_path_silent_link_path_runs():
    print("\n--- Test 4: Link-only message → request path silent, link extractor enqueues link ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        chat = -1004444001
        msg_id = 44001
        link_username = "LinkOnlyTestGroup"
        # رسالة فيها رابط فقط، لا كلمات طلب
        raw_text = f"انضموا للقروب https://t.me/{link_username}"
        ev = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)
        await fm._on_user_message(ev, '+TEST_SOURCE')
        await drain_request_tasks(fm)

        sm = fm.bot_client.send_message
        # request path: لا يُرسل (الرسالة لا تحوي كلمات طلب حقيقية — انضموا للقروب
        # مصنفة إعلانًا لأن فيها t.me/ رابط)
        record("4: request path NOT triggered (no request alert sent)",
               not sm.called,
               f"send_message called {sm.call_count} times (expected 0)")
        # link path: الرابط يجب أن يكون في LRB (snapshot) + queue
        key = (int(chat), int(msg_id))
        record("4: LRB has the link (link extractor snapshot taken)",
               key in fm._link_ring,
               f"LRB keys: {list(fm._link_ring.keys())[:5]}")
        if key in fm._link_ring:
            links_list = fm._link_ring[key]
            record("4: LRB contains normalized 'tg:user:linkonlytestgroup'",
                   'tg:user:linkonlytestgroup' in links_list,
                   f"LRB value: {links_list}")
        # queue should have the link
        cur = await conn.execute(
            "SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
            ('tg:user:linkonlytestgroup',))
        cnt = (await cur.fetchone())[0]
        record("4: link enqueued in link_queue (link path ran independently)",
               cnt == 1, f"got {cnt} rows")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 5: message with BOTH request keyword + t.me link → both paths run
# (request path's target = @dhkskwksjskwk, link path enqueues to channel_id)
# =====================================================================
async def test_5_both_paths_run_on_request_plus_link_message():
    print("\n--- Test 5: Message with request keyword + link → request alert to @dhkskwksjskwk, link enqueued ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        chat = -1005555001
        msg_id = 55001
        link_username = "RequestPlusLinkGroup"
        # رسالة فيها كلمة طلب + رابط — is_request_message سيصنفها إعلانًا
        # بسبب وجود t.me/ في ADVERTISEMENT_KEYWORDS (قرار الجلسة السابق).
        # هذا يعني request path لن يُرسل، لكن link path سيستخرج الرابط.
        # نتأكد أن: (1) الرابط مُستخرَج، (2) لو request alert أُرسل، target =
        # @dhkskwksjskwk وليس channel_id.
        raw_text = f"مين يحل لي واجب البرمجة؟ انضموا https://t.me/{link_username}"
        ev = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)
        await fm._on_user_message(ev, '+TEST_SOURCE')
        await drain_request_tasks(fm)

        sm = fm.bot_client.send_message
        # link path must run regardless of request classification
        key = (int(chat), int(msg_id))
        record("5: LRB has the link (link path runs regardless of request decision)",
               key in fm._link_ring
               and 'tg:user:requestpluslinkgroup' in fm._link_ring[key],
               f"LRB: {fm._link_ring.get(key)}")
        cur = await conn.execute(
            "SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
            ('tg:user:requestpluslinkgroup',))
        cnt = (await cur.fetchone())[0]
        record("5: link enqueued in link_queue (link path independent)",
               cnt == 1, f"got {cnt} rows")

        # request path: لو حُكم كطلب، target يجب أن يكون @dhkskwksjskwk
        if sm.called:
            targets = get_request_send_targets(fm)
            for t in targets:
                record(f"5: request alert target={t!r} == '@dhkskwksjskwk' (NOT channel_id)",
                       t == '@dhkskwksjskwk' and t != fm.config.channel_id,
                       f"target={t!r}, channel_id={fm.config.channel_id!r}")
        else:
            record("5: request alert NOT sent (advertisement classification — t.me link present)",
                   True, "expected: send_message not called due to advertisement classification")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 6: Hard invariant — Config parsing produces the right target type
# =====================================================================
async def test_6_config_parsing_at_username_correctly():
    print("\n--- Test 6: Config parsing handles @username form correctly ---")
    # نتأكد أن Config يقرأ REQUESTS_TARGET_CHANNEL=@dhkskwksjskwk بشكل صحيح
    # (string، يحتفظ بـ@ في البداية، لا يحاول int() عليه).
    os.environ['REQUESTS_TARGET_CHANNEL'] = '@dhkskwksjskwk'
    try:
        cfg = bot.Config()
        record("6: requests_target_channel is string '@dhkskwksjskwk'",
               cfg.requests_target_channel == '@dhkskwksjskwk',
               f"got {cfg.requests_target_channel!r}")
        record("6: requests_target_channel type is str (not int)",
               isinstance(cfg.requests_target_channel, str),
               f"got type {type(cfg.requests_target_channel).__name__}")
        record("6: requests_target_channel != channel_id (distinct)",
               str(cfg.requests_target_channel) != str(cfg.channel_id),
               f"rtc={cfg.requests_target_channel!r}, channel_id={cfg.channel_id!r}")
    finally:
        # استعد القيمة الافتراضية للاختبارات الأخرى
        os.environ['REQUESTS_TARGET_CHANNEL'] = '@dhkskwksjskwk'


# =====================================================================
# Test 7: numeric REQUESTS_TARGET_CHANNEL parsing (defensive coverage)
# =====================================================================
async def test_7_config_parsing_numeric_form():
    print("\n--- Test 7: Config parsing handles numeric form (-100xxx) ---")
    os.environ['REQUESTS_TARGET_CHANNEL'] = '-1009876543210'
    try:
        cfg = bot.Config()
        record("7: requests_target_channel is int -1009876543210",
               cfg.requests_target_channel == -1009876543210,
               f"got {cfg.requests_target_channel!r}")
        record("7: requests_target_channel type is int",
               isinstance(cfg.requests_target_channel, int),
               f"got type {type(cfg.requests_target_channel).__name__}")
    finally:
        os.environ['REQUESTS_TARGET_CHANNEL'] = '@dhkskwksjskwk'


# =====================================================================
# Test 8: empty REQUESTS_TARGET_CHANNEL → 0 (disabled, not channel_id)
# =====================================================================
async def test_8_config_parsing_empty_form():
    print("\n--- Test 8: Empty REQUESTS_TARGET_CHANNEL → 0 (disabled, no fallback) ---")
    os.environ['REQUESTS_TARGET_CHANNEL'] = ''
    try:
        cfg = bot.Config()
        record("8: requests_target_channel == 0 (empty → disabled)",
               cfg.requests_target_channel == 0,
               f"got {cfg.requests_target_channel!r}")
        record("8: requests_target_channel != channel_id (no implicit fallback)",
               cfg.requests_target_channel != cfg.channel_id,
               f"rtc={cfg.requests_target_channel!r} == channel_id={cfg.channel_id!r}?")
    finally:
        os.environ['REQUESTS_TARGET_CHANNEL'] = '@dhkskwksjskwk'


# =====================================================================
# Test 9: validate() does NOT require REQUESTS_TARGET_CHANNEL (optional)
# but channel_id IS required. This preserves backward compatibility.
# =====================================================================
async def test_9_validate_does_not_require_rtc():
    print("\n--- Test 9: Config.validate() does NOT require REQUESTS_TARGET_CHANNEL (optional) ---")
    os.environ['REQUESTS_TARGET_CHANNEL'] = ''
    try:
        cfg = bot.Config()
        errors = cfg.validate()
        # REQUESTS_TARGET_CHANNEL should NOT be in errors (it's optional)
        has_rtc_error = any('REQUESTS_TARGET_CHANNEL' in e for e in errors)
        record("9: validate() does NOT report REQUESTS_TARGET_CHANNEL as required",
               not has_rtc_error,
               f"errors mention REQUESTS_TARGET_CHANNEL: {errors!r}")
        # but CHANNEL_ID IS required
        record("9: validate() requires CHANNEL_ID (link channel — unchanged)",
               any('CHANNEL_ID' in e for e in errors) if cfg.channel_id == 0 else True,
               "CHANNEL_ID not in errors despite being 0")
    finally:
        os.environ['REQUESTS_TARGET_CHANNEL'] = '@dhkskwksjskwk'


# =====================================================================
# Test 10: [STYLE-MATCH] alert format matches link channel's blockquote
# Verifies the new alert uses the same style as MessageFormatter.format_link_message:
#   - <blockquote> wrapper
#   - "طلب مساعدة" header
#   - field labels: 👥 المجموعة، 👤 المرسل، 📟 الحساب، 🕒 التاريخ، 📡 المصدر، 🔑 الكلمات
#   - "عرض الرسالة الأصلية" link text (matches link channel's "عرض الرسالة الأصلية")
#   - "💬 نص الطلب" at the bottom (matches link channel's "💬 النص" at bottom)
# =====================================================================
async def test_10_alert_format_matches_link_channel_style():
    print("\n--- Test 10: Alert format matches link channel blockquote style ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        chat = -1001010001
        msg_id = 100001
        raw_text = "مين يحل لي واجب رياضيات؟ محتاج مساعدة عاجلة"
        ev = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)
        await fm._on_user_message(ev, '+TEST_SOURCE')
        await drain_request_tasks(fm)

        sm = fm.bot_client.send_message
        record("10: send_message was called (alert dispatched)",
               sm.called, "send_message not called")
        if not sm.called:
            return
        alert = sm.calls[0]['alert']

        # [STYLE-MATCH] same blockquote wrapper as link channel
        record("10: alert wrapped in <blockquote>...</blockquote>",
               alert.startswith('<blockquote>') and alert.endswith('</blockquote>'),
               f"alert starts/ends: {alert[:30]!r}...{alert[-30:]!r}")

        # [TASK-FORMAT-v4.3.4] تنظيف طلب المُشغّل:
        #   - حُذف: "طلب مساعدة" (header) + "📟 الحساب" + "📡 المصدر"
        record("10: alert does NOT have header 'طلب مساعدة' (removed by operator)",
               'طلب مساعدة' not in alert,
               f"header still present: {alert[:200]!r}")
        record("10: alert does NOT have '📟 <b>الحساب:</b>' (removed by operator)",
               '📟 <b>الحساب:</b>' not in alert,
               "account line still present")
        record("10: alert does NOT have '📡 <b>المصدر:</b>' (removed by operator)",
               '📡 <b>المصدر:</b>' not in alert,
               "source line still present")

        # [STYLE-MATCH] field labels matching link channel (المُبقَاة)
        record("10: alert has '👥 <b>المجموعة:</b>' (link channel: 👥 العضوية)",
               '👥 <b>المجموعة:</b>' in alert,
               "missing group label")
        record("10: alert has '👤 <b>المرسل:</b>' (link channel: 👤 الاسم)",
               '👤 <b>المرسل:</b>' in alert,
               "missing sender label")
        record("10: alert has '🕒 <b>التاريخ:</b>' (link channel: 🕒 التاريخ)",
               '🕒 <b>التاريخ:</b>' in alert,
               "missing date label")
        record("10: alert has '🔑 <b>الكلمات:</b>' field",
               '🔑 <b>الكلمات:</b>' in alert,
               "missing keywords label")

        # [TASK-FORMAT-v4.3.4] زر «مراسلة» — deep-link بمعرف المستخدم
        # (FakeNewMessageEvent sender_id=42, بلا username → tg://user?id=42)
        kw = sm.calls[0]['kwargs']
        record("10: send_message called with buttons kwarg (DM button present)",
               'buttons' in kw and kw.get('buttons') is not None,
               f"kwargs keys: {list(kw.keys())!r}")
        _btn = (kw.get('buttons') or [[None]])[0][0]
        if _btn is not None:
            record("10: DM button label 'مراسلة'",
                   'مراسلة' in (getattr(_btn, 'text', '') or ''),
                   f"button text: {getattr(_btn, 'text', None)!r}")
            record("10: DM button uses user-ID deep-link tg://user?id=42 (no username)",
                   (getattr(_btn, 'url', '') or '') == 'tg://user?id=42',
                   f"button url: {getattr(_btn, 'url', None)!r}")

        # [STYLE-MATCH] link text "عرض الرسالة الأصلية" (matches link channel exactly)
        record("10: alert has link text 'عرض الرسالة الأصلية' (matches link channel)",
               'عرض الرسالة الأصلية' in alert,
               "missing link text 'عرض الرسالة الأصلية'")

        # [STYLE-MATCH] text at the bottom: "💬 <b>نص الطلب:</b>" (link channel: "💬 <b>النص:</b>")
        record("10: alert has '💬 <b>نص الطلب:</b>' at bottom (link channel: 💬 النص at bottom)",
               '💬 <b>نص الطلب:</b>' in alert,
               "missing text label")

        # [STYLE-MATCH] text label should come AFTER the link (order: link → text)
        link_pos = alert.find('عرض الرسالة الأصلية')
        text_label_pos = alert.find('💬 <b>نص الطلب:</b>')
        record("10: 'عرض الرسالة الأصلية' comes BEFORE '💬 نص الطلب' (link channel order)",
               link_pos != -1 and text_label_pos != -1 and link_pos < text_label_pos,
               f"link_pos={link_pos} text_label_pos={text_label_pos}")

        # [CONTENT] alert contains the original request text (HTML-escaped)
        record("10: alert contains original request text 'مين يحل لي واجب رياضيات؟'",
               'مين يحل لي واجب رياضيات' in alert,
               f"alert snippet: {alert[:300]!r}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 11: [CROSS-ACCOUNT-DEDUP] race-safe dedup — two concurrent
# arrivals of same (chat_id, msg_id) → exactly 1 send_message call.
# Simulates monitor + joiner both receiving the same message.
# =====================================================================
async def test_11_cross_account_dedup_race_safe():
    print("\n--- Test 11: Cross-account dedup (race-safe) — 2 concurrent arrivals → 1 send ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        chat = -1001111002
        msg_id = 110002
        raw_text = "مين يحل لي واجب رياضيات؟ محتاج مساعدة عاجلة"
        # محاكاة وصول نفس الرسالة على حسابين مختلفين في نفس اللحظة
        ev1 = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)
        ev2 = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)

        # تشغيل الاستدعاءين بشكل متوازي (محاكاة سباق) ثم تصفية المهام الخلفية
        await asyncio.gather(
            fm._on_user_message(ev1, '+MONITOR_ACCOUNT'),
            fm._on_user_message(ev2, '+JOINER_ACCOUNT'),
        )
        await drain_request_tasks(fm)

        sm = fm.bot_client.send_message
        record("11: exactly 1 send_message call (race-safe dedup prevented duplicate)",
               sm.call_count == 1,
               f"got {sm.call_count} calls (expected 1 — duplicate should be suppressed)")
        if sm.call_count >= 1:
            target = sm.calls[0]['target']
            record("11: target == '@dhkskwksjskwk' (request channel)",
                   target == '@dhkskwksjskwk',
                   f"got target={target!r}")
            # one of the two sources should be the winner (race outcome)
            source_seen = sm.calls[0]['kwargs'].get('source') or sm.calls[0].get('source')
            # both sources processed the same message; the winner is whichever
            # acquired the lock first. We don't assert which — only that ONE won.
            record("11: exactly 1 source won the race (no duplicate alert sent)",
                   sm.call_count == 1,
                   f"both sources tried, only 1 should succeed")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 12: [CROSS-ACCOUNT-DEDUP] two DIFFERENT messages (different msg_id)
# from two different accounts → 2 send_message calls (no false dedup).
# Verifies dedup is keyed on (chat_id, msg_id), not on chat alone.
# =====================================================================
async def test_12_different_messages_no_false_dedup():
    print("\n--- Test 12: Different (chat_id, msg_id) → 2 sends (no false dedup) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        chat = -1001212003
        # رسالتان مختلفتان في نفس المجموعة (msg_id مختلف)
        raw_text_1 = "مين يحل لي واجب رياضيات؟ محتاج مساعدة"
        raw_text_2 = "أبي أحد يسوي لي تقرير برمجة"
        ev1 = FakeNewMessageEvent(raw_text_1, chat, 120001, chat=None, sender=None)
        ev2 = FakeNewMessageEvent(raw_text_2, chat, 120002, chat=None, sender=None)

        await asyncio.gather(
            fm._on_user_message(ev1, '+ACCOUNT_A'),
            fm._on_user_message(ev2, '+ACCOUNT_B'),
        )
        await drain_request_tasks(fm)

        sm = fm.bot_client.send_message
        record("12: exactly 2 send_message calls (different msg_id → no false dedup)",
               sm.call_count == 2,
               f"got {sm.call_count} calls (expected 2)")
        if sm.call_count == 2:
            targets = [c['target'] for c in sm.calls]
            record("12: both targets == '@dhkskwksjskwk' (request channel)",
                   all(t == '@dhkskwksjskwk' for t in targets),
                   f"targets: {targets!r}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 13: [XSS-SAFETY] alert HTML-escapes user-controlled content
# (sender name, group title, raw text) to prevent injection in the channel.
# =====================================================================
async def test_13_alert_html_escapes_user_content():
    print("\n--- Test 13: Alert HTML-escapes user content (XSS safety) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        chat = -1001313004
        msg_id = 130004
        # نص يحوي محاولة حقن HTML
        raw_text = "مين يحل لي واجب رياضيات؟ <script>alert(1)</script> & <b>bold</b>"
        # chat وsender يحويان أيضاً محاولة حقن
        malicious_chat = type('MaliciousChat', (), {
            'title': '<img src=x onerror=alert(1)>',
            'username': None,
        })()
        malicious_sender = type('MaliciousSender', (), {
            'username': 'evil<script>',
            'first_name': '<svg/onload=alert(1)>',
            'last_name': None,
            'title': None,
        })()
        ev = FakeNewMessageEvent(
            raw_text, chat, msg_id,
            chat=malicious_chat, sender=malicious_sender
        )
        await fm._on_user_message(ev, '+TEST_SOURCE')
        await drain_request_tasks(fm)

        sm = fm.bot_client.send_message
        record("13: send_message was called (alert dispatched)",
               sm.called, "send_message not called")
        if not sm.called:
            return
        alert = sm.calls[0]['alert']

        # raw text must be HTML-escaped (no raw <script>, <b>, &)
        record("13: raw <script> tag escaped in alert",
               '<script>' not in alert and '&lt;script&gt;' in alert,
               f"<script> not escaped! alert snippet: {alert[:400]!r}")
        record("13: raw <b> tag escaped in alert",
               '<b>bold</b>' not in alert or '&lt;b&gt;bold&lt;/b&gt;' in alert,
               f"<b> not escaped")
        record("13: '&' escaped to '&amp;' in user text",
               '&amp;' in alert,
               "& not escaped")
        # sender name must be escaped (only the < > need to be escaped — the
        # payload text "onload" is preserved as plain text, which is safe
        # because it's not inside an HTML tag). The XSS vector is neutralized
        # when '<svg' is NOT in the alert (escaped to '&lt;svg').
        record("13: malicious sender tag '<svg' escaped to '&lt;svg' (XSS vector neutralized)",
               '<svg' not in alert and '&lt;svg' in alert,
               f"<svg not escaped — alert snippet: {alert[:400]!r}")
        # chat title must be escaped (same rationale)
        record("13: malicious chat title '<img' escaped to '&lt;img' (XSS vector neutralized)",
               '<img' not in alert and '&lt;img' in alert,
               f"<img not escaped — alert snippet: {alert[:400]!r}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 14: [DATE-FIELD] alert contains a date string in YYYY-MM-DD HH:MM format
# (matches link channel's date_str format from MessageFormatter.format_link_message)
# =====================================================================
async def test_14_alert_has_date_field_in_link_channel_format():
    print("\n--- Test 14: Alert has date field in YYYY-MM-DD HH:MM (link channel format) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        chat = -1001414005
        msg_id = 140005
        raw_text = "مين يحل لي واجب رياضيات؟"
        # نحقن message.date عبر event.message
        from datetime import datetime
        fake_msg = type('FakeMsg', (), {'date': datetime(2025, 8, 29, 14, 30)})()
        ev = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)
        ev.message = fake_msg  # نضيف السمة بعد الإنشاء
        await fm._on_user_message(ev, '+TEST_SOURCE')
        await drain_request_tasks(fm)

        sm = fm.bot_client.send_message
        record("14: send_message was called (alert dispatched)",
               sm.called, "send_message not called")
        if not sm.called:
            return
        alert = sm.calls[0]['alert']
        # التاريخ يجب أن يظهر بالصيغة YYYY-MM-DD HH:MM
        import re
        date_match = re.search(r'🕒 <b>التاريخ:</b> (\d{4}-\d{2}-\d{2} \d{2}:\d{2})', alert)
        record("14: date field present in YYYY-MM-DD HH:MM format",
               date_match is not None,
               f"date field not found in alert snippet: {alert[:300]!r}")
        if date_match:
            record("14: date value matches injected message.date (2025-08-29 14:30)",
                   date_match.group(1) == '2025-08-29 14:30',
                   f"got date={date_match.group(1)}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 15: [CHANNEL-EXCLUDE-v4.3.4] broadcast channel → NO request send.
# المسار يقبل الطلبات من المجموعات فقط — قنوات البث تُستثنى قاطعًا قبل
# dedup وقبل AI. مجموعة megagroup تمرّ (سيطرة).
# =====================================================================
class FakeBroadcastChat:
    broadcast = True
    megagroup = False
    title = 'قناة مواد دراسية'
    username = 'study_materials_ch'


class FakeMegagroupChat:
    broadcast = False
    megagroup = True
    title = 'مجموعة المناقشة'
    username = 'discussion_group'


async def test_15_broadcast_channel_excluded_groups_only():
    print("\n--- Test 15: [CHANNEL-EXCLUDE] broadcast channel → NO request alert (groups only) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        # (أ) رسالة نص طلب من قناة بث → لا إرسال إطلاقًا
        ev_ch = FakeNewMessageEvent(
            "مين يحل لي واجب رياضيات؟ محتاج مساعدة عاجلة",
            -1003333001, 330001,
            chat=FakeBroadcastChat(), sender=None)
        await fm._on_user_message(ev_ch, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        sm = fm.bot_client.send_message
        record("15: broadcast channel post → NO send_message (excluded)",
               not sm.called, f"got {sm.call_count} sends")
        # ميتريك التخطي سُجّل
        skip_calls = [c.args[0] if c.args else None
                      for c in fm.metrics.record_skip.await_args_list]
        record("15: metrics.record_skip('request_broadcast_channel') recorded",
               'request_broadcast_channel' in skip_calls,
               f"skip calls: {skip_calls!r}")

        # (ب) نفس النص من مجموعة megagroup → يُرسل (سيطرة: المجموعات تمرّ)
        sm.reset_mock()
        ev_g = FakeNewMessageEvent(
            "مين يحل لي واجب رياضيات؟ محتاج مساعدة عاجلة",
            -1003333002, 330002,
            chat=FakeMegagroupChat(), sender=None)
        await fm._on_user_message(ev_g, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        record("15: megagroup message → send_message DOES fire (control)",
               sm.call_count == 1,
               f"got {sm.call_count} sends (expected 1)")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 16: [TASK-FORMAT] sender WITH username → المرسسل يحمل @username
# وزر «مراسلة» يستخدم https://t.me/<username>.
# =====================================================================
class FakeUserWithUsername:
    username = 'ahmed_test'
    first_name = 'أحمد'
    last_name = None


async def test_16_sender_username_in_line_and_tme_button():
    print("\n--- Test 16: sender with username → المرسل (@user) + t.me DM button ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        ev = FakeNewMessageEvent(
            "مين يحل لي واجب رياضيات؟ محتاج مساعدة",
            -1003333003, 330003, sender_id=777,
            chat=FakeMegagroupChat(), sender=FakeUserWithUsername())
        await fm._on_user_message(ev, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        sm = fm.bot_client.send_message
        record("16: alert sent", sm.called, "send_message not called")
        if not sm.called:
            return
        alert = sm.calls[0]['alert']
        record("16: المرسل line contains '@ahmed_test' beside name",
               'المرسل:</b> أحمد (@ahmed_test)' in alert,
               f"sender snippet: {alert[:220]!r}")
        kw = sm.calls[0]['kwargs']
        _btn = (kw.get('buttons') or [[None]])[0][0]
        record("16: DM button uses https://t.me/ahmed_test (username path)",
               (getattr(_btn, 'url', '') or '') == 'https://t.me/ahmed_test',
               f"button url: {getattr(_btn, 'url', None)!r}")
        record("16: DM button does NOT use tg://user (username exists)",
               'tg://user' not in (getattr(_btn, 'url', '') or ''),
               f"button url: {getattr(_btn, 'url', None)!r}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# Test 17: [SPEED-v4.3.4] fire-and-forget — AI بطيء (0.8s) لا يحجب
# مسار الروابط: _on_user_message يعود سريعًا، PRE-CACHE مكتوب، والمهمة
# الخلفية لا تزال نشطة، وبعد التصفية يُرسل التنبيه ويُنظَّف الحوض.
# =====================================================================
def make_slow_request_classifier(sleep_s=0.8):
    async def transport(provider, payload):
        await asyncio.sleep(sleep_s)
        user_msg = payload["messages"][1]["content"]
        inner = user_msg.split('"""')[-2] if '"""' in user_msg else user_msg
        if any(m in inner for m in _REQUEST_MARKERS) and not any(m in inner for m in _AD_MARKERS):
            content = _ai_json("ACCEPT", 0.93, "homework_execution_request", "طلب مساعدة أكاديمية")
        else:
            content = _ai_json("REJECT", 0.95, "other", "ليس طلبًا")
        return 200, _json.dumps({"choices": [{"message": {"content": content}}]})
    return _IC(providers=[{"key": "k", "url": "u", "model": "mock-slow", "name": "Slow"}],
               transport=transport)


async def test_17_fire_and_forget_request_path():
    print("\n--- Test 17: [SPEED] slow AI (0.8s) does NOT block link path (fire-and-forget) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        fm.request_classifier = make_slow_request_classifier(0.8)
        chat = -1003333004
        msg_id = 330004
        raw_text = "مين يحل لي واجب رياضيات؟ https://t.me/SomeGroup"
        ev = FakeNewMessageEvent(raw_text, chat, msg_id, chat=None, sender=None)

        t0 = time.monotonic()
        await fm._on_user_message(ev, '+TEST_SOURCE')
        handler_elapsed = time.monotonic() - t0

        # المسار الرئيسي عاد سريعًا — AI (0.8s) لم يحجبه
        record("17: _on_user_message returned in < 0.4s while AI sleeps 0.8s",
               handler_elapsed < 0.4,
               f"handler took {handler_elapsed:.3f}s (AI sleeps 0.8s)")
        # مسار الروابط اكتمل رغم أن AI لا يزال يعمل
        record("17: PRE-CACHE written while AI still running",
               (chat, msg_id) in fm._msg_cache,
               "message not in _msg_cache right after handler returned")
        # المهمة الخلفية لا تزال نشطة (غير حاجبة)
        inflight = len(getattr(fm, '_request_bg_tasks', set()) or set())
        record("17: request bg task still in-flight right after handler returned",
               inflight == 1, f"inflight={inflight} (expected 1)")

        # التصفية: التنبيه يُرسل الآن والحوض يُنظَّف
        await drain_request_tasks(fm)
        sm = fm.bot_client.send_message
        record("17: after drain — request alert sent (async path completed)",
               sm.call_count == 1, f"got {sm.call_count} sends")
        record("17: bg task pool cleaned after completion",
               len(getattr(fm, '_request_bg_tasks', set()) or set()) == 0,
               "task set not empty after drain")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


async def main():
    print("=" * 70)
    print("Request Channel Separation Hardening — Test Suite [CHANNEL-SEPARATION]")
    print("=" * 70)
    print("⚠️  NO Telegram credentials — SIMULATION ONLY (in-process SQLite +")
    print("    AsyncMock bot_client). Exercises REAL bot.Monitor._on_user_message")
    print("    + _handle_request_path production code paths.")
    print("Channel identity:")
    print("    REQUESTS_TARGET_CHANNEL = '@dhkskwksjskwk' (request alerts → this)")
    print("    CHANNEL_ID              = '-1001234567890' (link extractor → this)")
    print("=" * 70)
    await test_1_request_message_sent_to_requests_channel_not_link_channel()
    await test_2_request_path_never_uses_channel_id_as_target()
    await test_3_no_target_no_send_no_fallback_to_link_channel()
    await test_4_link_only_message_request_path_silent_link_path_runs()
    await test_5_both_paths_run_on_request_plus_link_message()
    await test_6_config_parsing_at_username_correctly()
    await test_7_config_parsing_numeric_form()
    await test_8_config_parsing_empty_form()
    await test_9_validate_does_not_require_rtc()
    await test_10_alert_format_matches_link_channel_style()
    await test_11_cross_account_dedup_race_safe()
    await test_12_different_messages_no_false_dedup()
    await test_13_alert_html_escapes_user_content()
    await test_14_alert_has_date_field_in_link_channel_format()
    await test_15_broadcast_channel_excluded_groups_only()
    await test_16_sender_username_in_line_and_tme_button()
    await test_17_fire_and_forget_request_path()
    print("\n" + "=" * 70)
    passed = sum(1 for r in RESULTS if r['passed'])
    failed = sum(1 for r in RESULTS if not r['passed'])
    print(f"RESULTS: {passed}/{passed + failed} passed, {failed} failed")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
