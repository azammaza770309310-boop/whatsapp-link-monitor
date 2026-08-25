#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Message Journal (durable SQLite write-ahead log) — Contract Tests
==================================================================

18 اختبار (A-R) تثبت:
- journal roundtrip + dedup بين حسابات متعددة (INSERT OR IGNORE)
- journal_lookup_any عبر الشاتات (أحداث الحذف بدون chat_id)
- journal_set_state timestamps (rescued_at / deleted_at / attempt_count)
- journal_pending_older_than (كشف الانهيار)
- journal_cleanup (retention طويل + قصير لـ no_text/delete_miss)
- journal_stats
- E2E: NewMessage يكتب journal (processed / no_text)
- E2E: الإنقاذ من journal بعد "إعادة تشغيل" (cache فاضي) — السيناريو الأساسي
- DELETE-MISS forensics (raw_text=NULL) + حد 50 للسحب الجماعي
- Journal Recovery للصفوف pending القديمة
- SourceRegistry.remove_reader + PollingScheduler constants
- مرونة المعالج عند تعطيل الـ journal
- دورة كاملة NewMessage→Delete عبر cache (بدون تكرار enqueue)

كل اختبار يستخدم temp DB حقيقية + asserts فعلية.
ملاحظة: تُستدعى دوال Monitor الحقيقية unbound على SimpleNamespace يحاكي الحد الأدنى.
"""
import asyncio
import os
import sys
import tempfile
import time
import json
import logging
import types
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

import bot  # noqa: E402  (AFTER env setup)

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


# === Shared helpers ===

def make_fake_monitor(prod_db, journal_enabled=True, reconcile=False, channel_id=-1009999999):
    """SimpleNamespace يحاكي Monitor بالحد الأدنى المطلوب للدوال الحقيقية.

    الدوال الحقيقية (مثل _on_user_message) تستدعي مساعداتها عبر self —
    لذا نربط مساعدات Monitor الحقيقية (unbound) على الـ namespace كـ bound methods.
    """
    from source_registry import MessageClaim
    cfg = types.SimpleNamespace(
        journal_enabled=journal_enabled,
        delete_miss_reconcile=reconcile,
        journal_retention_s=86400,
        journal_no_text_retention_s=21600,
        channel_id=channel_id,
    )
    fm = types.SimpleNamespace(
        config=cfg,
        prod_db=prod_db,
        message_claim=MessageClaim(prod_db),
        _msg_cache={},
        _msg_cache_lock=asyncio.Lock(),
        metrics=types.SimpleNamespace(record_skip=AsyncMock(), record_duplicate=AsyncMock()),
        user_clients={},
        source_registry=None,
        _delete_miss_log_ts={},
        _delete_miss_count={},
        _no_text_count=0,
        _reconcile_inflight=set(),
        _chat_poll_failures={},
        _polling_state={},
        _polling_lock=asyncio.Lock(),
        _active_polling_chats=[],
    )
    # اربط دوال Monitor المساعدة الحقيقية على الـ fake (نفس الكود الإنتاجي)
    for method_name in (
        '_journal_enabled', '_journal_write', '_journal_set_state_safe',
        '_journal_mark_deleted_safe', '_record_delete_miss',
        '_rescue_enqueue_links', '_spawn_reconcile',
        '_reconcile_chat_after_delete_miss', '_journal_recovery',
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


class FakeDeleteEvent:
    def __init__(self, deleted_ids, chat_id):
        self.deleted_ids = deleted_ids
        self.chat_id = chat_id


async def sql_one(prod_db, query, params=()):
    """ينفّذ SELECT ويرجع الصف الأول (أو None)."""
    conn = await prod_db._conn()
    cursor = await conn.execute(query, params)
    row = await cursor.fetchone()
    return row


# === Test A: journal roundtrip + multi-account dedup ===

async def test_A():
    """journal_message يكتب صفًا، وINSERT OR IGNORE يجعل أول كاتب يفوز."""
    print("\n--- Test A: journal roundtrip + multi-account dedup ---")
    try:
        prod_db, _ = await make_test_db()

        await prod_db.journal_message({
            'chat_id': -100111222333, 'msg_id': 1001,
            'raw_text': 'hello https://t.me/TestJournalAlpha',
            'source_phone': 'A', 'chat_title': 'Test Chat', 'state': 'pending',
        })

        row = await prod_db.journal_get(-100111222333, 1001)
        if not isinstance(row, dict):
            record("A: journal_get returns dict", False, f"got {type(row)}")
            return
        record("A: journal_get returns dict", True)

        if row.get('raw_text') != 'hello https://t.me/TestJournalAlpha':
            record("A: raw_text roundtrip", False, f"got {row.get('raw_text')!r}")
            return
        record("A: raw_text roundtrip", True)

        if row.get('state') != 'pending':
            record("A: state=='pending'", False, f"got {row.get('state')!r}")
            return
        record("A: state=='pending'", True)

        if row.get('source_phone') != 'A':
            record("A: source_phone=='A'", False, f"got {row.get('source_phone')!r}")
            return
        record("A: source_phone=='A'", True)

        # نفس المفتاح من حساب ثانٍ — أول كاتب يفوز (INSERT OR IGNORE)
        await prod_db.journal_message({
            'chat_id': -100111222333, 'msg_id': 1001,
            'raw_text': 'hello https://t.me/TestJournalAlpha',
            'source_phone': 'B', 'chat_title': 'Test Chat', 'state': 'pending',
        })
        row2 = await prod_db.journal_get(-100111222333, 1001)
        if row2.get('source_phone') != 'A':
            record("A: first writer wins (dedup across accounts)", False,
                   f"got {row2.get('source_phone')!r}")
            return
        record("A: first writer wins (dedup across accounts)", True)

        missing = await prod_db.journal_get(-100111222333, 9999)
        if missing is not None:
            record("A: journal_get missing key returns None", False, f"got {missing!r}")
            return
        record("A: journal_get missing key returns None", True)
    except Exception as e:
        record("A: exception", False, str(e))


# === Test B: journal_lookup_any (delete events without chat_id) ===

async def test_B():
    """نفس msg_id عبر شاتات متعددة — lookup_any يرجع صفوف raw_text فقط، الأحدث أولًا."""
    print("\n--- Test B: journal_lookup_any across chats ---")
    try:
        prod_db, _ = await make_test_db()
        now = time.time()

        # شاتان بنفس msg_id (raw_text موجود) + صف ثالث بلا نص
        await prod_db.journal_message({
            'chat_id': -100111222333, 'msg_id': 2001,
            'raw_text': 'first https://t.me/lookupone', 'source_phone': 'A',
            'received_at': now - 100, 'state': 'pending',
        })
        await prod_db.journal_message({
            'chat_id': -100222333444, 'msg_id': 2001,
            'raw_text': 'second https://t.me/lookuptwo', 'source_phone': 'B',
            'received_at': now - 50, 'state': 'pending',
        })
        await prod_db.journal_message({
            'chat_id': -100333444555, 'msg_id': 2001,
            'raw_text': None, 'source_phone': 'C',
            'received_at': now - 10, 'state': 'no_text',
        })

        rows = await prod_db.journal_lookup_any(2001)
        if len(rows) != 2:
            record("B: lookup_any returns 2 rows (raw_text NOT NULL only)", False,
                   f"got {len(rows)}")
            return
        record("B: lookup_any returns 2 rows (raw_text NOT NULL only)", True)

        if not all(r.get('raw_text') for r in rows):
            record("B: all returned rows have raw_text", False, f"got {rows}")
            return
        record("B: all returned rows have raw_text", True)

        if rows[0]['chat_id'] != -100222333444:
            record("B: newest first (received_at DESC)", False,
                   f"first chat_id={rows[0]['chat_id']}")
            return
        record("B: newest first (received_at DESC)", True)
    except Exception as e:
        record("B: exception", False, str(e))


# === Test C: journal_set_state timestamps ===

async def test_C():
    """set_state يملأ الطوابع المناسبة ويزيد attempt_count، وmark_deleted يسجل deleted_at."""
    print("\n--- Test C: journal_set_state timestamps ---")
    try:
        prod_db, _ = await make_test_db()
        chat = -100111222333

        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 3000, 'raw_text': 'txt https://t.me/stateone',
            'source_phone': 'A', 'state': 'pending',
        })

        await prod_db.journal_set_state(chat, 3000, 'rescued', mark_deleted=True)
        row = await prod_db.journal_get(chat, 3000)
        ok_state = row.get('state') == 'rescued'
        record("C: state=='rescued' after set_state", ok_state,
               f"got {row.get('state')!r}")

        record("C: rescued_at is not None", row.get('rescued_at') is not None,
               f"got {row.get('rescued_at')!r}")
        record("C: deleted_at is not None (mark_deleted=True)", row.get('deleted_at') is not None,
               f"got {row.get('deleted_at')!r}")
        record("C: processed_at is not None (terminal state)", row.get('processed_at') is not None,
               f"got {row.get('processed_at')!r}")
        record("C: attempt_count==1 after first set_state", row.get('attempt_count') == 1,
               f"got {row.get('attempt_count')!r}")

        await prod_db.journal_set_state(chat, 3000, 'no_links')
        row = await prod_db.journal_get(chat, 3000)
        record("C: attempt_count==2 after second set_state", row.get('attempt_count') == 2,
               f"got {row.get('attempt_count')!r}")

        # صف آخر — journal_mark_deleted منفصل
        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 3050, 'raw_text': 'other https://t.me/statetwo',
            'source_phone': 'A', 'state': 'pending',
        })
        await prod_db.journal_mark_deleted(chat, 3050)
        row2 = await prod_db.journal_get(chat, 3050)
        record("C: journal_mark_deleted sets deleted_at", row2.get('deleted_at') is not None,
               f"got {row2.get('deleted_at')!r}")
    except Exception as e:
        record("C: exception", False, str(e))


# === Test D: journal_pending_older_than (crash detection) ===

async def test_D():
    """pending القديمة فقط تُرجع — لا processed ولا الحديثة."""
    print("\n--- Test D: journal_pending_older_than ---")
    try:
        prod_db, _ = await make_test_db()
        chat = -100111222333

        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 4100, 'raw_text': 'old https://t.me/oldone',
            'source_phone': 'A', 'state': 'pending',
        })
        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 4101, 'raw_text': 'fresh https://t.me/freshone',
            'source_phone': 'A', 'state': 'pending',
        })

        # اجعل 4100 قديمة (600 ثانية) عبر SQL مباشرة
        conn = await prod_db._conn()
        await conn.execute(
            "UPDATE message_journal SET received_at=? WHERE chat_id=? AND msg_id=?",
            (time.time() - 600, chat, 4100))
        await conn.commit()

        rows = await prod_db.journal_pending_older_than(120)
        if len(rows) != 1:
            record("D: exactly 1 old pending row", False, f"got {len(rows)}")
            return
        record("D: exactly 1 old pending row", True)

        if rows[0]['msg_id'] != 4100:
            record("D: correct msg_id returned", False, f"got {rows[0]['msg_id']}")
            return
        record("D: correct msg_id returned", True)

        # صف processed قديم — لا يُرجع (pending فقط)
        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 4102, 'raw_text': 'done https://t.me/doneone',
            'source_phone': 'A', 'received_at': time.time() - 600, 'state': 'processed',
        })
        rows2 = await prod_db.journal_pending_older_than(120)
        record("D: old processed row NOT returned (pending only)",
               len(rows2) == 1 and rows2[0]['msg_id'] == 4100,
               f"got {[(r['msg_id'], r['state']) for r in rows2]}")
    except Exception as e:
        record("D: exception", False, str(e))


# === Test E: journal_cleanup (retention long + short) ===

async def test_E():
    """cleanup يحذف القديم كله + no_text/delete_miss الأعمق من short_retention."""
    print("\n--- Test E: journal_cleanup ---")
    try:
        prod_db, _ = await make_test_db()
        chat = -100111222333
        now = time.time()

        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 4200, 'raw_text': 'old pending https://t.me/oldp',
            'source_phone': 'A', 'received_at': now - 90000, 'state': 'pending',
        })
        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 4201, 'raw_text': 'fresh pending https://t.me/freshp',
            'source_phone': 'A', 'received_at': now - 10, 'state': 'pending',
        })
        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 4202, 'raw_text': None,
            'source_phone': 'A', 'received_at': now - 30000, 'state': 'no_text',
        })
        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 4203, 'raw_text': None,
            'source_phone': 'A', 'received_at': now - 10, 'state': 'no_text',
        })

        result = await prod_db.journal_cleanup(retention_s=86400, short_retention_s=21600)

        old_pending = await prod_db.journal_get(chat, 4200)
        record("E: old pending removed (retention_s)", old_pending is None,
               f"got {old_pending!r}")

        old_no_text = await prod_db.journal_get(chat, 4202)
        record("E: old no_text removed (short_retention_s)", old_no_text is None,
               f"got {old_no_text!r}")

        fresh_pending = await prod_db.journal_get(chat, 4201)
        record("E: fresh pending remains", fresh_pending is not None)

        fresh_no_text = await prod_db.journal_get(chat, 4203)
        record("E: fresh no_text remains", fresh_no_text is not None)

        # تأكيد عبر SQL مباشرة
        row = await sql_one(prod_db, "SELECT COUNT(*) FROM message_journal WHERE chat_id=?", (chat,))
        record("E: exactly 2 rows left in journal", row[0] == 2, f"got {row[0]}")

        record("E: cleanup returns removed counts dict",
               isinstance(result, dict) and result.get('removed_old', 0) >= 1
               and result.get('removed_light', 0) >= 1,
               f"got {result!r}")
    except Exception as e:
        record("E: exception", False, str(e))


# === Test F: journal_stats ===

async def test_F():
    """journal_stats يرجع عدّاد لكل حالة."""
    print("\n--- Test F: journal_stats ---")
    try:
        prod_db, _ = await make_test_db()
        chat = -100111222333

        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 4300, 'raw_text': 'p https://t.me/statp',
            'source_phone': 'A', 'state': 'pending',
        })
        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 4301, 'raw_text': 'd https://t.me/statd',
            'source_phone': 'A', 'state': 'processed',
        })
        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 4302, 'raw_text': None,
            'source_phone': 'A', 'state': 'no_text',
        })
        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 4303, 'raw_text': None,
            'source_phone': 'A', 'state': 'delete_miss',
        })

        stats = await prod_db.journal_stats()
        expected = {'pending': 1, 'processed': 1, 'no_text': 1, 'delete_miss': 1}
        record("F: journal_stats counts match states", stats == expected,
               f"got {stats!r}, expected {expected!r}")
    except Exception as e:
        record("F: exception", False, str(e))


# === Test G: E2E NewMessage journaling (real _on_user_message) ===

async def test_G():
    """رسالة برابط → journal 'processed' + الرابط في link_queue."""
    print("\n--- Test G: E2E NewMessage journaling ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)

        # ملاحظة: TestJournalBeta محظور (يحتوي 'bet' من قائمة القمار) — نستخدم Theta
        ev = FakeNewMessageEvent('انضموا معنا https://t.me/TestJournalTheta',
                                 -100111222333, 3001)
        await bot.Monitor._on_user_message(fm, ev, 'A')

        row = await prod_db.journal_get(-100111222333, 3001)
        if row is None:
            record("G: journal row exists after NewMessage", False, "journal_get returned None")
            return
        record("G: journal row exists after NewMessage", True)

        record("G: journal state=='processed'", row.get('state') == 'processed',
               f"got {row.get('state')!r}")

        link_row = await sql_one(
            prod_db,
            "SELECT normalized_link FROM link_queue WHERE normalized_link=?",
            ('tg:user:testjournaltheta',))
        record("G: link enqueued to link_queue", link_row is not None,
               "link_queue row for tg:user:testjournaltheta not found")
    except Exception as e:
        record("G: exception", False, str(e))


# === Test H: E2E no-text message ===

async def test_H():
    """رسالة بلا نص → journal 'no_text' مع raw_text=None، ولا صفوف في link_queue."""
    print("\n--- Test H: E2E no-text message ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)

        ev = FakeNewMessageEvent('', -100111222333, 3002)
        await bot.Monitor._on_user_message(fm, ev, 'A')

        row = await prod_db.journal_get(-100111222333, 3002)
        if row is None:
            record("H: journal row exists for no-text message", False, "None")
            return
        record("H: journal row exists for no-text message", True)

        record("H: journal state=='no_text'", row.get('state') == 'no_text',
               f"got {row.get('state')!r}")
        record("H: raw_text is None", row.get('raw_text') is None,
               f"got {row.get('raw_text')!r}")

        row_count = await sql_one(prod_db, "SELECT COUNT(*) FROM link_queue")
        record("H: link_queue empty", row_count[0] == 0, f"got {row_count[0]}")
    except Exception as e:
        record("H: exception", False, str(e))


# === Test I: E2E delete rescue from journal with EMPTY memory cache ===

async def test_I():
    """السيناريو الأساسي: NewMessage journaled الرسالة، أعيد تشغيل النظام (cache فاضي)،
    ثم وصل حدث الحذف → الإنقاذ من journal + enqueue + claim."""
    print("\n--- Test I: delete rescue from journal (empty cache = restart) ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)  # reconcile=False

        # كما لو أن NewMessage كتب journal ثم انطفأ النظام (cache اختفى)
        await prod_db.journal_message({
            'chat_id': -100111222333, 'msg_id': 4001,
            'raw_text': 'سحب https://t.me/TestJournalGamma',
            'source_phone': 'A', 'chat_title': 'Victim Chat', 'state': 'pending',
        })

        ev = FakeDeleteEvent([4001], -100111222333)
        await bot.Monitor._on_message_deleted(fm, ev, 'B')

        row = await prod_db.journal_get(-100111222333, 4001)
        if row is None:
            record("I: journal row exists", False, "None")
            return
        record("I: journal row exists", True)

        record("I: journal state=='rescued'", row.get('state') == 'rescued',
               f"got {row.get('state')!r}")
        record("I: deleted_at is not None", row.get('deleted_at') is not None,
               f"got {row.get('deleted_at')!r}")

        link_row = await sql_one(
            prod_db,
            "SELECT normalized_link FROM link_queue WHERE normalized_link=?",
            ('tg:user:testjournalgamma',))
        record("I: rescued link enqueued (tg:user:testjournalgamma)", link_row is not None,
               "link not found in link_queue")

        pm_row = await sql_one(
            prod_db,
            "SELECT state FROM processed_messages WHERE chat_id=? AND msg_id=?",
            (-100111222333, 4001))
        record("I: processed_messages state=='processed' (claim worked)",
               pm_row is not None and pm_row[0] == 'processed',
               f"got {pm_row!r}")
    except Exception as e:
        record("I: exception", False, str(e))


# === Test J: delete with chat_id=None (journal_lookup_any path) ===

async def test_J():
    """حدث حذف بدون chat_id → lookup_any يجد الرسالة ويُنقذها في شاتها الأصلي."""
    print("\n--- Test J: delete with chat_id=None (lookup_any) ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)

        await prod_db.journal_message({
            'chat_id': -100444555666, 'msg_id': 5001,
            'raw_text': 'رابط https://t.me/TestJournalDelta',
            'source_phone': 'A', 'chat_title': 'Other Chat', 'state': 'pending',
        })

        ev = FakeDeleteEvent([5001], None)
        await bot.Monitor._on_message_deleted(fm, ev, 'A')

        row = await prod_db.journal_get(-100444555666, 5001)
        record("J: rescue works via journal_lookup_any",
               row is not None and row.get('state') == 'rescued',
               f"got {row!r}")

        link_row = await sql_one(
            prod_db,
            "SELECT normalized_link FROM link_queue WHERE normalized_link=?",
            ('tg:user:testjournaldelta',))
        record("J: link 'tg:user:testjournaldelta' enqueued", link_row is not None,
               "link not found in link_queue")
    except Exception as e:
        record("J: exception", False, str(e))


# === Test K: DELETE-MISS forensics (message never seen) ===

async def test_K():
    """رسالة لم نرها أبدًا حُذفت → صف delete_miss بلا raw_text + لا enqueue."""
    print("\n--- Test K: DELETE-MISS forensics ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)  # reconcile=False، لا صف journal، cache فاضي

        ev = FakeDeleteEvent([6001], -100111222333)
        try:
            await bot.Monitor._on_message_deleted(fm, ev, 'A')
            record("K: delete handler does NOT raise on delete-miss", True)
        except Exception as e:
            record("K: delete handler does NOT raise on delete-miss", False, str(e))
            return

        row = await prod_db.journal_get(-100111222333, 6001)
        if row is None:
            record("K: delete_miss row written to journal", False, "None")
            return
        record("K: delete_miss row written to journal", True)

        record("K: state=='delete_miss'", row.get('state') == 'delete_miss',
               f"got {row.get('state')!r}")
        record("K: raw_text is None (forensics only)", row.get('raw_text') is None,
               f"got {row.get('raw_text')!r}")

        row_count = await sql_one(prod_db, "SELECT COUNT(*) FROM link_queue")
        record("K: link_queue empty", row_count[0] == 0, f"got {row_count[0]}")
    except Exception as e:
        record("K: exception", False, str(e))


# === Test L: already-processed message deleted (no double enqueue) ===

async def test_L():
    """رسالة processed حُذفت → deleted_at فقط، بدون إعادة enqueue."""
    print("\n--- Test L: already-processed deleted (no double enqueue) ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        chat = -100111222333

        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 7001,
            'raw_text': 'تم https://t.me/TestJournalEpsilon',
            'source_phone': 'A', 'chat_title': 'Done Chat', 'state': 'processed',
        })

        ev = FakeDeleteEvent([7001], chat)
        await bot.Monitor._on_message_deleted(fm, ev, 'A')

        row = await prod_db.journal_get(chat, 7001)
        record("L: state stays 'processed'", row.get('state') == 'processed',
               f"got {row.get('state')!r}")
        record("L: deleted_at set (forensics)", row.get('deleted_at') is not None,
               f"got {row.get('deleted_at')!r}")

        link_row = await sql_one(
            prod_db,
            "SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
            ('tg:user:testjournalepsilon',))
        record("L: NO 'tg:user:testjournalepsilon' enqueued", link_row[0] == 0,
               f"got {link_row[0]} row(s)")
    except Exception as e:
        record("L: exception", False, str(e))


# === Test M: mass delete capped (no crash, delete_miss rows limited to 50) ===

async def test_M():
    """سحب جماعي (100 id) → يُعالج أول 50 فقط للتحقيق، بدون استثناء."""
    print("\n--- Test M: mass delete capped at 50 ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)

        ev = FakeDeleteEvent(list(range(8000, 8100)), -100111222333)
        try:
            await bot.Monitor._on_message_deleted(fm, ev, 'A')
            record("M: mass delete runs without exception", True)
        except Exception as e:
            record("M: mass delete runs without exception", False, str(e))
            return

        row = await sql_one(
            prod_db,
            "SELECT COUNT(*) FROM message_journal WHERE chat_id=? AND state='delete_miss'",
            (-100111222333,))
        record("M: exactly 50 delete_miss rows for chat", row[0] == 50,
               f"got {row[0]}")
    except Exception as e:
        record("M: exception", False, str(e))


# === Test N: journal recovery of stale pending row (real _journal_recovery) ===

async def test_N():
    """صف pending عمره > 120 ثانية = دليل انهيار → _journal_recovery يعيد معالجته."""
    print("\n--- Test N: journal recovery of stale pending row ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)

        await prod_db.journal_message({
            'chat_id': -100111222333, 'msg_id': 8001,
            'raw_text': 'غيث https://t.me/TestJournalZeta',
            'source_phone': 'A', 'chat_title': 'Recovery Chat', 'state': 'pending',
        })

        # اجعل الصف قديمًا (older than 120s threshold)
        conn = await prod_db._conn()
        await conn.execute(
            "UPDATE message_journal SET received_at=? WHERE chat_id=? AND msg_id=?",
            (time.time() - 300, -100111222333, 8001))
        await conn.commit()

        # Patch sleep لتخطي انتظار 45 ثانية (bot يستخدم asyncio العام)
        import bot as bot_mod
        _orig_sleep = bot_mod.asyncio.sleep

        async def _fast_sleep(s, *a, **k):
            if isinstance(s, (int, float)) and s >= 10:
                return
            return await _orig_sleep(s, *a, **k)

        bot_mod.asyncio.sleep = _fast_sleep
        try:
            await bot_mod.Monitor._journal_recovery(fm)
        finally:
            bot_mod.asyncio.sleep = _orig_sleep

        row = await prod_db.journal_get(-100111222333, 8001)
        record("N: journal state=='processed' after recovery",
               row is not None and row.get('state') == 'processed',
               f"got {row!r}")

        link_row = await sql_one(
            prod_db,
            "SELECT normalized_link FROM link_queue WHERE normalized_link=?",
            ('tg:user:testjournalzeta',))
        record("N: link 'tg:user:testjournalzeta' enqueued", link_row is not None,
               "link not found in link_queue")
    except Exception as e:
        record("N: exception", False, str(e))


# === Test O: SourceRegistry.remove_reader ===

async def test_O():
    """remove_reader يحذف الهاتف من الذاكرة + DB، ويصلح primary_reader."""
    print("\n--- Test O: SourceRegistry.remove_reader ---")
    try:
        from source_registry import SourceRegistry
        prod_db, _ = await make_test_db()

        registry = SourceRegistry(prod_db, watchers=[
            {'phone': 'A', 'role': 'monitor'},
            {'phone': 'B', 'role': 'monitor'},
        ])

        await prod_db.add_monitored_chat(
            chat_id=-100111222333, chat_title='Registry Chat',
            username='regchat', link_type='group', monitored_by='A')
        await prod_db.update_monitored_chat(
            -100111222333,
            reader_phones=json.dumps(['A', 'B']),
            primary_reader='A')

        registry._chat_to_phones[-100111222333] = ['A', 'B']

        ok = await registry.remove_reader(-100111222333, 'A')
        record("O: remove_reader returns True", ok is True, f"got {ok!r}")

        in_mem = registry._chat_to_phones.get(-100111222333)
        record("O: in-memory list == ['B']", in_mem == ['B'], f"got {in_mem!r}")

        db_row = await sql_one(
            prod_db,
            "SELECT reader_phones, primary_reader FROM monitored_chats WHERE chat_id=?",
            (-100111222333,))
        record("O: DB reader_phones == '[\"B\"]'",
               db_row is not None and db_row[0] == '["B"]',
               f"got {db_row!r}")
        record("O: primary_reader cleared (not 'A')",
               db_row is not None and db_row[1] != 'A',
               f"got {db_row[1]!r}")

        ok2 = await registry.remove_reader(-100111222333, 'A')
        record("O: second remove_reader same phone returns False", ok2 is False,
               f"got {ok2!r}")
    except Exception as e:
        record("O: exception", False, str(e))


# === Test P: PollingScheduler constants ===

async def test_P():
    """ثوابت الجدولة: batch=25، تزامن=4."""
    print("\n--- Test P: PollingScheduler constants ---")
    try:
        from source_registry import PollingScheduler
        record("P: PollingScheduler.BATCH_SIZE == 25",
               PollingScheduler.BATCH_SIZE == 25,
               f"got {PollingScheduler.BATCH_SIZE}")
        record("P: PollingScheduler.MAX_CONCURRENT_POLLS == 4",
               PollingScheduler.MAX_CONCURRENT_POLLS == 4,
               f"got {PollingScheduler.MAX_CONCURRENT_POLLS}")
    except Exception as e:
        record("P: exception", False, str(e))


# === Test Q: delete handler resilient when journaling disabled ===

async def test_Q():
    """journal_enabled=False → المعالج لا ينهار (كل نداءات journal متخطاة)."""
    print("\n--- Test Q: journaling disabled resilience ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db, journal_enabled=False)

        ev = FakeDeleteEvent([9001], -100111222333)
        try:
            await bot.Monitor._on_message_deleted(fm, ev, 'A')
            record("Q: no exception with journal_enabled=False", True)
        except Exception as e:
            record("Q: no exception with journal_enabled=False", False, str(e))
            return

        # الـ journal متعطل → لا صف delete_miss
        row = await prod_db.journal_get(-100111222333, 9001)
        record("Q: no journal row written (journal disabled)", row is None,
               f"got {row!r}")

        row_count = await sql_one(prod_db, "SELECT COUNT(*) FROM link_queue")
        record("Q: link_queue still empty", row_count[0] == 0, f"got {row_count[0]}")
    except Exception as e:
        record("Q: exception", False, str(e))


# === Test R: NewMessage→Delete full-cycle (cache fast path) ===

async def test_R():
    """دورة كاملة في نفس التشغيل: NewMessage يعالج، ثم Delete → already_done
    (deleted_at فقط) بدون تكرار enqueue."""
    print("\n--- Test R: full-cycle NewMessage→Delete (cache fast path) ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        chat = -100111222333

        ev_new = FakeNewMessageEvent('جديد https://t.me/TestJournalEta', chat, 9101)
        await bot.Monitor._on_user_message(fm, ev_new, 'A')

        row = await prod_db.journal_get(chat, 9101)
        record("R: journal state=='processed' after NewMessage",
               row is not None and row.get('state') == 'processed',
               f"got {row!r}")

        ev_del = FakeDeleteEvent([9101], chat)
        await bot.Monitor._on_message_deleted(fm, ev_del, 'A')

        row = await prod_db.journal_get(chat, 9101)
        record("R: state stays 'processed' after delete (already_done)",
               row.get('state') == 'processed', f"got {row.get('state')!r}")
        record("R: deleted_at set in journal", row.get('deleted_at') is not None,
               f"got {row.get('deleted_at')!r}")

        link_count = await sql_one(
            prod_db,
            "SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
            ('tg:user:testjournaleta',))
        record("R: exactly 1 row for 'tg:user:testjournaleta' (no duplicate)",
               link_count[0] == 1, f"got {link_count[0]}")
    except Exception as e:
        record("R: exception", False, str(e))


# === Main runner ===

async def main():
    print("=" * 70)
    print("Message Journal (durable WAL) — Test Suite")
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
    await test_O()
    await test_P()
    await test_Q()
    await test_R()

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
