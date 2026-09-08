#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sender Block + Repeat Guard Test Suite [v4.4.10]
=================================================
[أمر المُشغّل 2026-09-08] «وش هذ التكرار وابيك تحضر هذول المستخدمين
حتى ولو كان الطلب الذي يرسله حقيقي» — يثبت:

  (أ) حظر المرسلين (BLOCKED_SENDER_USERNAMES):
    - Test 1: مرسل محظور (كيان مخبأ) + طلب حقيقي → لا إرسال (فحص مبكر)
    - Test 2: مرسل محظور حُلّ عبر API (get_sender) → لا إرسال (فحص حاسم)
    - Test 3: الحظر غير حساس لحالة الأحرف (ENG_MUTASEM)
    - Test 4: يوزرنيمات بادئة @ / محارف غريبة → معقّمة ثم مطابقة
    - Test 5: المستخدمون الأربعة المحظورون جميعًا + نصوص طلبات حقيقية → صفر إرسال
    - Test 6: مرسل غير محظور + طلب حقيقي → يُرسل عاديًا (لا حظر زائد)

  (ب) كبح تكرار الطلب لكل مُرسل (SENDER-REPEAT 12h):
    - Test 7: نفس المرسل + نفس النص (msg_id مختلف، content-dedup معطّل
      عمدًا لعزل المسار) → تنبيه واحد فقط + reason=sender_repeat
    - Test 8: نفس المرسل + نص مختلف كليًا → التنبيهان يُرسلان
    - Test 9: مرسلان مختلفان + نفس النص → التنبيهان يُرسلان (الحرس
      لكل مُرسل — لا يحرم طالبًا ثانيًا بنفس النص)
    - Test 10: مرسل بلا username (sender_id فقط) + نفس النص مرتين → واحد
    - Test 11: نفس النص بسرعة (بلا تعطيل content-dedup) → واحد (خط
      الدفاع الأول العالمي كما هو — لا انحدار)

  (ج) مساعدات وثوابت (static):
    - Test 12: القائمة تحوي المستخدمين الأربعة بالنص الحرفي
    - Test 13: _sender_username_normalized — تعقيم/حد أدنى 4 محارف
    - Test 14: _sender_repeat_signature — ترتيب الكلمات/التشكيل لا يغير
      البصمة؛ كلمات مختلفة → بصمة مختلفة

NO Telegram credentials — SIMULATION ONLY. نفس حزمة channel-separation
المجمّدة: in-process SQLite + AsyncMock bot_client + REAL Monitor methods.
"""
import asyncio
import os
import sys
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
os.environ.setdefault('REQUEST_FILTER_ENABLED', 'true')
os.environ.setdefault('REQUEST_FILTER_MAX_PER_MINUTE', '1000')
os.environ.setdefault('REQUEST_FILTER_MAX_PER_CHAT_PER_MINUTE', '1000')
os.environ.setdefault('REQUEST_FILTER_CIRCUIT_BREAKER_THRESHOLD', '10000')

logging.disable(logging.CRITICAL)

import json as _json
from intent_classifier import IntentClassifier as _IC

_REQUEST_MARKERS = ("واجب", "رياضيات", "برمجة", "بحث", "تقرير", "تفاضل", "مشروع")
_AD_MARKERS = ("تداول", "للتواصل", "بوت", "خدمات مدفوعة")


def _ai_json(decision, confidence, category, reason):
    return _json.dumps({"decision": decision, "confidence": confidence,
                        "category": category, "reason": reason}, ensure_ascii=False)


def make_mock_request_classifier(model="mock-v4"):
    async def transport(provider, payload):
        user_msg = payload["messages"][1]["content"]
        inner = user_msg.split('"""')[-2] if '"""' in user_msg else user_msg
        if any(m in inner for m in _REQUEST_MARKERS) and not any(m in inner for m in _AD_MARKERS):
            content = _ai_json("ACCEPT", 0.93, "homework_execution_request", "طلب واجب صريح")
        else:
            content = _ai_json("REJECT", 0.95, "other", "ليس طلبًا")
        return 200, _json.dumps({"choices": [{"message": {"content": content}}]})
    return _IC(providers=[{"key": "k", "url": "u", "model": model, "name": "Mock"}],
               transport=transport)


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
    await conn.execute("""CREATE TABLE IF NOT EXISTS monitored_chats (
        chat_id INTEGER PRIMARY KEY, chat_title TEXT, username TEXT, link_type TEXT,
        monitored_by TEXT, discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active INTEGER DEFAULT 1, last_checked TIMESTAMP, member_count INTEGER)""")
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


class FakeMegagroupChat:
    title = 'جامعة حفرالباطن | استفسارات'
    username = 'hafralbatin_ask'
    broadcast = False
    megagroup = True


class FakeUser:
    """كيان مُرسِل Telethon مُحاكى."""
    def __init__(self, username='normal_user', first_name='طالب'):
        self.username = username
        self.first_name = first_name
        self.last_name = None


class FakeEventApiResolve(FakeNewMessageEvent):
    """event تزامني sender=None لكن get_sender() (API) يعيد كيانًا كاملاً."""
    def __init__(self, raw_text, chat_id, msg_id, sender_id, resolved_user):
        super().__init__(raw_text, chat_id, msg_id, sender_id=sender_id,
                         chat=FakeMegagroupChat(), sender=None)
        self._resolved = resolved_user

    async def get_sender(self):
        return self._resolved


def make_monitor(prod_db, channel_id=-1001234567890,
                 requests_target_channel='@dhkskwksjskwk'):
    cfg = types.SimpleNamespace(
        journal_enabled=True, delete_miss_reconcile=False,
        journal_retention_s=86400, journal_no_text_retention_s=21600,
        channel_id=channel_id, journal_recovery_enabled=True,
        requests_target_channel=requests_target_channel,
        request_filter_enabled=True,
        request_filter_max_per_minute=1000,
        request_filter_max_per_chat_per_minute=1000,
        request_filter_cb_threshold=10000,
        request_filter_cb_window_s=600,
        request_filter_cb_cooldown_s=600,
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
        '_dispatch_request_path',
        '_register_request_alert', '_mark_request_alert_deleted',
        '_mention_bridge_seed', '_mention_bridge_relay',
        '_relay_and_register_request_alert',
    ):
        setattr(fm, method_name,
                types.MethodType(getattr(bot.Monitor, method_name), fm))
    return fm


async def drain_request_tasks(fm):
    tasks = list(getattr(fm, '_request_bg_tasks', None) or set())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def stub_off_content_dedup(fm):
    """يعطّل خط content-dedup العالمي (10 دقائق) لعزل مسار sender-repeat:
    الإنتاج يرى التكرار بعد 17-34 دقيقة — خارج نافذة content-dedup."""
    fm._request_content_deduper = types.SimpleNamespace(
        is_duplicate=lambda t: False)


def stub_off_semantic_dedup(fm):
    """يعطّل خط semantic-dedup العالمي (15 دقيقة) — نفس الغرض: محاكاة
    فجوة الإنتاج الحقيقية (17-34 دقيقة) حيث تكرار نفس الشخص يمرّ من
    كل خطوط الدفاع العالمية القصيرة ويصده حارس المُرسل 12 ساعة فقط."""
    class _NoDup:
        is_dup = False
        kind = ''
    class _NoSemanticDeduper:
        def check(self, canonical, now=None):
            return _NoDup()
        def register(self, canonical, now=None):
            pass
    fm._request_semantic_deduper = _NoSemanticDeduper()


# نص طلب حقيقي (يقبله المصنّف) — نفس نص البلاغ الإنتاجي
_GENUINE_TEXT = "احتاج شخص يسوي بحث جامعي"
_GENUINE_TEXT_2 = "مين يحل لي واجب رياضيات محتاج مساعدة"


# =====================================================================
# (أ) الحظر
# =====================================================================
async def test_1_blocked_sender_sync_entity_no_send():
    print("\n--- Test 1: blocked sender (cached entity) + genuine request → NO send ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        ev = FakeNewMessageEvent(
            _GENUINE_TEXT, -1004444001, 440001, sender_id=111,
            chat=FakeMegagroupChat(), sender=FakeUser(username='eng_mutasem'))
        await fm._on_user_message(ev, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        sm = fm.bot_client.send_message
        record("1: blocked @eng_mutasem (sync entity) — zero sends",
               sm.call_count == 0, f"got {sm.call_count} sends")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


async def test_2_blocked_sender_api_resolve_no_send():
    print("\n--- Test 2: blocked sender resolved via API (get_sender) → NO send ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        ev = FakeEventApiResolve(
            _GENUINE_TEXT_2, -1004444002, 440002, sender_id=222,
            resolved_user=FakeUser(username='han0f0', first_name='هانوف'))
        await fm._on_user_message(ev, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        sm = fm.bot_client.send_message
        record("2: blocked @han0f0 (API-resolved, sync=None) — zero sends",
               sm.call_count == 0, f"got {sm.call_count} sends")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


async def test_3_block_case_insensitive():
    print("\n--- Test 3: blocklist matching is case-insensitive ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        ev = FakeNewMessageEvent(
            _GENUINE_TEXT, -1004444003, 440003, sender_id=333,
            chat=FakeMegagroupChat(), sender=FakeUser(username='ENG_MUTASEM'))
        await fm._on_user_message(ev, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        sm = fm.bot_client.send_message
        record("3: 'ENG_MUTASEM' (uppercase) — zero sends",
               sm.call_count == 0, f"got {sm.call_count} sends")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


async def test_4_username_sanitized_before_match():
    print("\n--- Test 4: username with @ prefix / stray chars → sanitized then matched ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        # كيان يحمل يوزرنيم بصيغة @farmah5 (بعض العملاء يعيدونها هكذا)
        u = FakeUser(username='@farmah5', first_name='فرح')
        ev = FakeNewMessageEvent(
            _GENUINE_TEXT, -1004444004, 440004, sender_id=444,
            chat=FakeMegagroupChat(), sender=u)
        await fm._on_user_message(ev, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        sm = fm.bot_client.send_message
        record("4: '@farmah5' (with @) sanitized → blocked, zero sends",
               sm.call_count == 0, f"got {sm.call_count} sends")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


async def test_5_all_four_blocked_usernames_genuine_requests():
    print("\n--- Test 5: ALL FOUR blocked usernames + genuine texts → zero sends ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        blocked = ['eng_mutasem', 'han0f0', 'farmah5', 'x3badix']
        total_sends = 0
        for i, un in enumerate(blocked):
            ev = FakeNewMessageEvent(
                _GENUINE_TEXT, -1004444010 + i, 440010 + i, sender_id=600 + i,
                chat=FakeMegagroupChat(), sender=FakeUser(username=un))
            await fm._on_user_message(ev, '+TEST_SOURCE')
            await drain_request_tasks(fm)
        total_sends = fm.bot_client.send_message.call_count
        record("5: 4 blocked senders × genuine request → 0 sends total",
               total_sends == 0, f"got {total_sends} sends")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


async def test_6_unblocked_sender_still_passes():
    print("\n--- Test 6: unblocked sender + genuine request → sent (no over-blocking) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        ev = FakeNewMessageEvent(
            _GENUINE_TEXT, -1004444006, 440006, sender_id=666,
            chat=FakeMegagroupChat(), sender=FakeUser(username='sara_ux'))
        await fm._on_user_message(ev, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        sm = fm.bot_client.send_message
        record("6: unblocked @sara_ux — exactly 1 send",
               sm.call_count == 1, f"got {sm.call_count} sends")
        if sm.call_count == 1:
            record("6: alert carries (@sara_ux) in sender line",
                   '(@sara_ux)' in sm.calls[0]['alert'],
                   f"sender snippet: {sm.calls[0]['alert'][:200]!r}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# (ب) كبح التكرار لكل مُرسل
# =====================================================================
async def test_7_same_sender_same_text_one_alert():
    print("\n--- Test 7: SAME sender re-sends SAME text → ONE alert (sender_repeat) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        stub_off_content_dedup(fm)   # فجوة إنتاج: >10 دقائق
        stub_off_semantic_dedup(fm)  # فجوة إنتاج: >15 دقيقة
        chat = -1004444007
        ev1 = FakeNewMessageEvent(
            _GENUINE_TEXT, chat, 440071, sender_id=777,
            chat=FakeMegagroupChat(), sender=FakeUser(username='normal_user'))
        ev2 = FakeNewMessageEvent(
            _GENUINE_TEXT, chat, 440072, sender_id=777,
            chat=FakeMegagroupChat(), sender=FakeUser(username='normal_user'))
        await fm._on_user_message(ev1, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        await fm._on_user_message(ev2, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        sm = fm.bot_client.send_message
        record("7: same sender × same text (2 msg_ids) → exactly 1 send",
               sm.call_count == 1, f"got {sm.call_count} sends")
        reg = getattr(fm, '_sender_repeat_reg', None) or {}
        bucket = reg.get('normal_user')
        record("7: repeat registry holds the signature for sender",
               isinstance(bucket, dict) and len(bucket) == 1,
               f"reg: {reg!r}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


async def test_8_same_sender_different_text_both_pass():
    print("\n--- Test 8: same sender, DIFFERENT request → both alerts pass ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        stub_off_content_dedup(fm)
        stub_off_semantic_dedup(fm)
        chat = -1004444008
        ev1 = FakeNewMessageEvent(
            _GENUINE_TEXT, chat, 440081, sender_id=888,
            chat=FakeMegagroupChat(), sender=FakeUser(username='multi_need'))
        ev2 = FakeNewMessageEvent(
            "ابغى احد يسوي لي تقرير تفاضل كامل", chat, 440082, sender_id=888,
            chat=FakeMegagroupChat(), sender=FakeUser(username='multi_need'))
        await fm._on_user_message(ev1, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        await fm._on_user_message(ev2, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        sm = fm.bot_client.send_message
        record("8: same sender × two DIFFERENT texts → 2 sends",
               sm.call_count == 2, f"got {sm.call_count} sends")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


async def test_9_different_senders_same_text_both_pass():
    print("\n--- Test 9: TWO different senders, same text → both alerts (per-sender scope) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        stub_off_content_dedup(fm)
        stub_off_semantic_dedup(fm)
        chat = -1004444009
        ev1 = FakeNewMessageEvent(
            _GENUINE_TEXT, chat, 440091, sender_id=991,
            chat=FakeMegagroupChat(), sender=FakeUser(username='student_a'))
        ev2 = FakeNewMessageEvent(
            _GENUINE_TEXT, chat, 440092, sender_id=992,
            chat=FakeMegagroupChat(), sender=FakeUser(username='student_b'))
        await fm._on_user_message(ev1, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        await fm._on_user_message(ev2, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        sm = fm.bot_client.send_message
        record("9: two senders × same text → 2 sends (guard is per-sender)",
               sm.call_count == 2, f"got {sm.call_count} sends")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


async def test_10_repeat_by_sender_id_without_username():
    print("\n--- Test 10: username-less sender (id-keyed) repeats → one alert ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)
        stub_off_content_dedup(fm)
        stub_off_semantic_dedup(fm)
        chat = -1004444010
        # sender=None (لا username في أي طبقة) → المفتاح id:1234
        ev1 = FakeNewMessageEvent(
            _GENUINE_TEXT_2, chat, 440101, sender_id=1234,
            chat=FakeMegagroupChat(), sender=None)
        ev2 = FakeNewMessageEvent(
            _GENUINE_TEXT_2, chat, 440102, sender_id=1234,
            chat=FakeMegagroupChat(), sender=None)
        await fm._on_user_message(ev1, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        await fm._on_user_message(ev2, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        sm = fm.bot_client.send_message
        record("10: username-less repeat (id:1234) → exactly 1 send",
               sm.call_count == 1, f"got {sm.call_count} sends")
        reg = getattr(fm, '_sender_repeat_reg', None) or {}
        record("10: registry keyed by id:1234",
               'id:1234' in reg, f"reg keys: {list(reg.keys())!r}")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


async def test_11_rapid_identical_text_content_dedup_intact():
    print("\n--- Test 11: rapid identical text (content-dedup active) → 1 send (no regression) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_monitor(prod_db)  # بلا stub — خط الدفاع العالمي الأول يعمل
        chat = -1004444011
        ev1 = FakeNewMessageEvent(
            _GENUINE_TEXT, chat, 440111, sender_id=1357,
            chat=FakeMegagroupChat(), sender=FakeUser(username='rapid_user'))
        ev2 = FakeNewMessageEvent(
            _GENUINE_TEXT, chat, 440112, sender_id=1357,
            chat=FakeMegagroupChat(), sender=FakeUser(username='rapid_user'))
        await fm._on_user_message(ev1, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        await fm._on_user_message(ev2, '+TEST_SOURCE')
        await drain_request_tasks(fm)
        sm = fm.bot_client.send_message
        record("11: rapid identical re-send → 1 send (first-line dedup intact)",
               sm.call_count == 1, f"got {sm.call_count} sends")
    finally:
        await conn.close()
        try: os.remove(db_path)
        except: pass


# =====================================================================
# (ج) ثوابت ومساعدات — static
# =====================================================================
def test_12_blocklist_contains_exactly_four():
    print("\n--- Test 12: BLOCKED_SENDER_USERNAMES holds the 4 operator-named users ---")
    expected = {'eng_mutasem', 'han0f0', 'farmah5', 'x3badix'}
    got = set(bot.BLOCKED_SENDER_USERNAMES)
    record("12: blocklist == operator's four usernames",
           got == expected, f"got {sorted(got)!r}")
    record("12: frozenset (immutable, typo-safe)",
           isinstance(bot.BLOCKED_SENDER_USERNAMES, frozenset),
           f"type={type(bot.BLOCKED_SENDER_USERNAMES)!r}")


def test_13_username_normalization_helper():
    print("\n--- Test 13: _sender_username_normalized sanitization contract ---")

    class U:
        def __init__(self, u):
            self.username = u

    cases = [
        ("plain lowercase kept", U('eng_mutasem'), 'eng_mutasem'),
        ("uppercase lowered", U('HAN0F0'), 'han0f0'),
        ("@ stripped", U('@farmah5'), 'farmah5'),
        ("stray chars removed", U('x3badix!!'), 'x3badix'),
        ("too short → ''", U('ab'), ''),
        ("None entity → ''", None, ''),
        ("None username → ''", types.SimpleNamespace(username=None), ''),
    ]
    all_ok = True
    for name, ent, want in cases:
        got = bot._sender_username_normalized(ent)
        if got != want:
            all_ok = False
            print(f"         mismatch [{name}]: got {got!r} want {want!r}")
    record("13: sanitization contract (7 cases)", all_ok)


def test_14_repeat_signature_contract():
    print("\n--- Test 14: _sender_repeat_signature — order/tashkeel invariant ---")
    a = bot._sender_repeat_signature("مين يحل لي واجب")
    b = bot._sender_repeat_signature("واجب يحل مين لي")  # ترتيب معكوس
    c = bot._sender_repeat_signature("مِنْ يَحِلُّ واجب")  # تشكيل (من→مين لهجة؟ لا — مختلف)
    d = bot._sender_repeat_signature("ابغى تقرير تفاضل")  # نص مختلف كليًا
    record("14: word order does NOT change signature",
           a == b and a != '', f"a={a[:10]}… b={b[:10]}…")
    record("14: totally different text → different signature",
           a != d, f"a={a[:10]}… d={d[:10]}…")
    record("14: empty/whitespace text → '' (guard skipped)",
           bot._sender_repeat_signature("") == ''
           and bot._sender_repeat_signature("   ") == '')


async def main():
    print("=" * 70)
    print("Sender Block + Repeat Guard — Test Suite [SENDER-BLOCK-v4.4.10]")
    print("=" * 70)
    print("⚠️  NO Telegram credentials — SIMULATION ONLY (in-process SQLite +")
    print("    AsyncMock bot_client). Exercises REAL bot.Monitor._on_user_message")
    print("    + _handle_request_path production code paths.")
    print("Operator order (2026-09-08): block repeat-offenders even if genuine")
    print("=" * 70)
    await test_1_blocked_sender_sync_entity_no_send()
    await test_2_blocked_sender_api_resolve_no_send()
    await test_3_block_case_insensitive()
    await test_4_username_sanitized_before_match()
    await test_5_all_four_blocked_usernames_genuine_requests()
    await test_6_unblocked_sender_still_passes()
    await test_7_same_sender_same_text_one_alert()
    await test_8_same_sender_different_text_both_pass()
    await test_9_different_senders_same_text_both_pass()
    await test_10_repeat_by_sender_id_without_username()
    await test_11_rapid_identical_text_content_dedup_intact()
    test_12_blocklist_contains_exactly_four()
    test_13_username_normalization_helper()
    test_14_repeat_signature_contract()
    print("\n" + "=" * 70)
    passed = sum(1 for r in RESULTS if r['passed'])
    failed = sum(1 for r in RESULTS if not r['passed'])
    total = len(RESULTS)
    print(f"TOTAL: {total} | PASS: {passed} | FAIL: {failed}")
    print("=" * 70)
    if failed:
        print("\nFAILED CASES:")
        for r in RESULTS:
            if not r['passed']:
                print(f"  ❌ {r['name']}: {r['detail']}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
