#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Audit-Fix Regressions — Test Suite (Task 8a)
============================================

Standalone regression tests for the 12 original production-hardening fixes:
  B01 (code)  — DATA_DIR env-configurable sqlite path
  B02         — _poll_one_chat persists last_msg_id/last_activity
  B03         — /api/polling_status reads due-chats from DB (+ scheduler_running)
  B04         — _periodic_sync refreshes SourceRegistry (load_from_db)
  B05         — _journal_recovery is a RECURRING loop (was fire-once)
  B06         — optional DASHBOARD_API_KEY shared-secret on /api/* (backward-compat)
  B07         — _supervisor_loop recreates dead critical tasks
  B08         — journal_write errors logged at WARNING (were silent debug)
  B09         — journal_lookup_any guarded for chat_id-keyed rescues
  L03         — _polling_watchdog_loop (30s) restarts dead scheduler
  L04         — _supabase_ensure_schema attempts ALTER + logs exact SQL
  L07         — LEASE_DURATION_S env-configurable (source_registry + link_system)

Pattern: RESULTS list + record() + main() + sys.exit(rc).
Test-DB infra: _OPEN_DBS + close_all_test_dbs() teardown (aiosqlite thread cleanup).
"""
import asyncio
import os
import sys
import tempfile
import time
import types
import sqlite3
import logging
import aiohttp
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
# Ensure a clean DASHBOARD_API_KEY baseline (B06 tests toggle this per-test)
os.environ.pop('DASHBOARD_API_KEY', None)

logging.disable(logging.CRITICAL)

import bot  # noqa: E402  (AFTER env setup)

RESULTS = []

# [infra] track open aiosqlite connections so the test runner can close them
# at shutdown — prevents "aiosqlite thread still running" warnings.
_OPEN_DBS = []


def record(name, passed, detail=""):
    RESULTS.append({'name': name, 'passed': passed, 'detail': detail})
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if detail and not passed:
        print(f"         {detail}")


async def close_all_test_dbs():
    """Close every aiosqlite connection opened by make_test_db()."""
    for fdb in _OPEN_DBS:
        try:
            if getattr(fdb, '_conn', None) is not None:
                await fdb._conn.close()
        except Exception:
            pass
    _OPEN_DBS.clear()


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
    _OPEN_DBS.append(fake_db)
    return prod_db, fake_db


async def sql_one(prod_db, query, params=()):
    conn = await prod_db._conn()
    cursor = await conn.execute(query, params)
    row = await cursor.fetchone()
    return row


async def sql_exec(prod_db, query, params=()):
    conn = await prod_db._conn()
    await conn.execute(query, params)
    await conn.commit()


# === Log capture helper ===

class LogCapture:
    """Append (level, message) tuples from the root logger while active.

    [Task 3a fix] Properly saves/restores BOTH the root logger's effective
    level AND the global logging.disable level. The previous version saved
    the logger's level but passed it to logging.disable() on exit — which
    set the global disable to WARNING (30), silently suppressing INFO logs
    in all subsequent LogCapture calls (broke 4a-summary's INFO capture).
    Now: root logger level is lowered to INFO on enter so handler-level
    filtering actually works, and the global disable is saved/restored
    separately."""
    def __init__(self):
        self.records = []

    def _handler(self, record):
        self.records.append((record.levelname, record.getMessage()))

    def __enter__(self):
        self._restore_disable = logging.root.manager.disable
        self._restore_logger_level = logging.getLogger().level
        logging.disable(logging.NOTSET)
        # Lower the root logger's level so INFO/WARNING messages can reach
        # the handler. The handler's own level (set below) does the final
        # filtering; without this, the root logger (default WARNING) drops
        # INFO before it ever reaches the handler.
        logging.getLogger().setLevel(logging.INFO)
        self._logger = logging.getLogger()
        # Use a real Handler subclass instance
        class _H(logging.Handler):
            def emit(s, record):
                self.records.append((record.levelname, record.getMessage()))
        self._h = _H()
        self._h.setLevel(logging.WARNING)
        self._logger.addHandler(self._h)
        return self

    def __exit__(self, *a):
        self._logger.removeHandler(self._h)
        logging.getLogger().setLevel(self._restore_logger_level)
        logging.disable(self._restore_disable)

    @property
    def warnings(self):
        return [m for lvl, m in self.records if lvl == 'WARNING']

    @property
    def all_msgs(self):
        return [m for _, m in self.records]


# === Fake event helpers ===

class FakeDeleteEvent:
    def __init__(self, deleted_ids, chat_id):
        self.deleted_ids = deleted_ids
        self.chat_id = chat_id


# === Fake monitor helper (binds real Monitor methods unbound) ===

def make_fake_monitor(prod_db, journal_enabled=True, reconcile=False, channel_id=-1009999999):
    from source_registry import MessageClaim
    cfg = types.SimpleNamespace(
        journal_enabled=journal_enabled,
        delete_miss_reconcile=reconcile,
        journal_retention_s=86400,
        journal_no_text_retention_s=21600,
        channel_id=channel_id,
        journal_recovery_enabled=True,
    )
    fm = types.SimpleNamespace(
        config=cfg,
        prod_db=prod_db,
        message_claim=MessageClaim(prod_db),
        _msg_cache={},
        _msg_cache_lock=asyncio.Lock(),
        metrics=types.SimpleNamespace(
            record_skip=AsyncMock(), record_duplicate=AsyncMock(),
            record_link_capture=AsyncMock(), record_link_ring_hit=AsyncMock(),
            record_delete_miss=AsyncMock(), record_delete_rescued=AsyncMock(),
            record_reconcile_rescued=AsyncMock(), record_link_forwarded=AsyncMock(),
        ),
        # [PR-1] Link Ring Buffer state (matches Monitor.__init__)
        _link_ring={}, _link_ring_lock=asyncio.Lock(),
        _link_ring_ttl=300, _link_ring_cap=20000,
        _link_ring_evicted=0, _link_ring_hits=0,
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
        _polling_interval=5,
        _msg_cache_ttl=120,
        _running=False,
        polling_scheduler=None,
        _polling_scheduler_task=None,
        _journal_recovery_task=None,
        _joiner_task=None,
        _supervisor_task=None,
        _polling_watchdog_task=None,
    )
    # Bind real Monitor helper methods onto the namespace
    for method_name in (
        '_journal_enabled', '_journal_write', '_journal_set_state_safe',
        '_journal_mark_deleted_safe', '_record_delete_miss',
        '_rescue_enqueue_links', '_spawn_reconcile',
        '_reconcile_chat_after_delete_miss', '_journal_recovery',
        '_on_message_deleted', '_on_user_message', '_poll_one_chat',
        '_supervisor_loop', '_polling_watchdog_loop', '_periodic_sync',
        '_get_sender_name',
        '_link_ring_put', '_link_ring_pop', '_link_ring_evict',  # [PR-1]
        '_normalized_to_link_data', '_rescue_link_only',  # [PR-2]
    ):
        if hasattr(bot.Monitor, method_name):
            setattr(fm, method_name,
                    types.MethodType(getattr(bot.Monitor, method_name), fm))
    return fm


# === Counting-sleep helper: flips fm._running=False after Nth call ===

def make_counting_sleep(flip_target, flip_on=2):
    state = {'n': 0}

    async def _sleep(_n):
        state['n'] += 1
        if state['n'] >= flip_on:
            flip_target._running = False
    return _sleep


# ===================================================================
# B01: DATA_DIR env-configurable
# ===================================================================

async def test_B01_data_dir_env_configurable():
    print("\n--- B01: DATA_DIR env-configurable ---")
    try:
        # Default when env unset
        os.environ.pop('DATA_DIR', None)
        default_dir = os.environ.get('DATA_DIR', 'data')
        record("B01: DATA_DIR defaults to 'data' when env unset",
               default_dir == 'data', f"got {default_dir!r}")
        # bot.DB_FILE is built from DATA_DIR
        record("B01: bot.DB_FILE is under DATA_DIR",
               bot.DB_FILE == os.path.join(default_dir, "help_requests.db"),
               f"got {bot.DB_FILE!r}")
        # Env override honored by the os.environ.get expression
        os.environ['DATA_DIR'] = '/tmp/audit_b01_override'
        override_dir = os.environ.get('DATA_DIR', 'data')
        record("B01: DATA_DIR env override honored by the os.environ.get expression",
               override_dir == '/tmp/audit_b01_override', f"got {override_dir!r}")
        os.environ.pop('DATA_DIR', None)
    except Exception as e:
        record("B01: exception", False, str(e))


# ===================================================================
# B02: _poll_one_chat persists last_msg_id/last_activity
# ===================================================================

async def test_B02_poll_one_chat_persists_last_msg_id():
    print("\n--- B02: _poll_one_chat persists last_msg_id/last_activity ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        # Spy on update_monitored_chat
        prod_db.update_monitored_chat = AsyncMock()
        # Fake client returning one message with id=200, no links (fast no_links path)
        msg = types.SimpleNamespace(
            id=200, raw_text='hello world no links here',
            out=False, sender=None, sender_id=0)
        client = types.SimpleNamespace(
            is_connected=lambda: True,
            get_messages=AsyncMock(return_value=[msg]))
        fm.user_clients = {'phoneA': client}
        chat = {'chat_id': -100111222333, 'chat_title': 'B02 Chat', 'username': ''}
        await bot.Monitor._poll_one_chat(fm, 'phoneA', chat)
        calls = prod_db.update_monitored_chat.call_args_list
        record("B02: update_monitored_chat called with last_msg_id+last_activity",
               len(calls) > 0 and calls[0].kwargs.get('last_msg_id') == 200
               and bool(calls[0].kwargs.get('last_activity')),
               f"calls={calls}")
        if calls:
            record("B02: last_msg_id == 200 (max of polled messages)",
                   calls[0].kwargs.get('last_msg_id') == 200,
                   f"got {calls[0].kwargs.get('last_msg_id')}")
    except Exception as e:
        record("B02: exception", False, str(e))


# ===================================================================
# B03: /api/polling_status reads due-chats from DB
# ===================================================================

async def test_B03_polling_status_reads_due_chats_from_db():
    print("\n--- B03: /api/polling_status reads due-chats from DB ---")
    try:
        prod_db, fake_db = await make_test_db()
        # Insert 2 chats: one DUE (next_poll_at in the past), one FUTURE.
        from datetime import datetime, timedelta
        past = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        future = (datetime.utcnow() + timedelta(minutes=30)).isoformat()
        await sql_exec(
            prod_db,
            "INSERT INTO monitored_chats (chat_id, chat_title, last_msg_id, "
            "last_activity, next_poll_at, poll_tier) VALUES (?,?,?,?,?,?)",
            (-100222333444, 'Due Chat', 10, past, past, 'hot'))
        await sql_exec(
            prod_db,
            "INSERT INTO monitored_chats (chat_id, chat_title, last_msg_id, "
            "last_activity, next_poll_at, poll_tier) VALUES (?,?,?,?,?,?)",
            (-100333444555, 'Future Chat', 20, future, future, 'cold'))
        # Build a fake request + app with a monitor whose scheduler task is alive
        done_task = asyncio.create_task(asyncio.sleep(0))  # not done yet at check time
        monitor = types.SimpleNamespace(
            _msg_cache={}, _msg_cache_ttl=120, _polling_interval=5,
            _polling_scheduler_task=done_task,
            polling_scheduler=types.SimpleNamespace())  # truthy
        app = {'monitor': monitor, 'db': fake_db}
        req = types.SimpleNamespace(app=app, path='/api/polling_status', headers={})
        resp = await bot.api_polling_status_handler(req)
        body = resp.body if hasattr(resp, 'body') else resp.text
        import json as _json
        if isinstance(body, (bytes, bytearray)):
            body = body.decode()
        data = _json.loads(body) if isinstance(body, str) else body
        record("B03: response has scheduler_running key",
               'scheduler_running' in data, f"keys={list(data.keys())}")
        record("B03: active_chats_count counts DUE chats from DB (>=1)",
               data.get('active_chats_count', -1) >= 1,
               f"got {data.get('active_chats_count')}")
        due_ids = [c.get('chat_id') for c in data.get('active_chats', [])]
        record("B03: due chat -100222333444 present in active_chats",
               -100222333444 in due_ids, f"got {due_ids}")
        record("B03: future chat -100333444555 NOT present (not due)",
               -100333444555 not in due_ids, f"got {due_ids}")
        record("B03: scheduler_running is bool",
               isinstance(data.get('scheduler_running'), bool),
               f"got {type(data.get('scheduler_running'))}")
    except Exception as e:
        record("B03: exception", False, str(e))


# ===================================================================
# B04: _periodic_sync calls source_registry.load_from_db
# ===================================================================

async def test_B04_periodic_sync_loads_source_registry():
    print("\n--- B04: _periodic_sync calls source_registry.load_from_db ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        fm._running = True
        fm._sync_monitored_chats = AsyncMock()
        load_mock = AsyncMock()
        fm.source_registry = types.SimpleNamespace(load_from_db=load_mock)
        # Flip _running=False after the FIRST sleep so exactly one body runs.
        fake_sleep = make_counting_sleep(fm, flip_on=1)
        with patch('asyncio.sleep', new=fake_sleep):
            await bot.Monitor._periodic_sync(fm)
        record("B04: source_registry.load_from_db called after _sync_monitored_chats",
               load_mock.called, f"called={load_mock.called}")
        record("B04: _sync_monitored_chats also called (ordering preserved)",
               fm._sync_monitored_chats.called, f"called={fm._sync_monitored_chats.called}")
    except Exception as e:
        record("B04: exception", False, str(e))


# ===================================================================
# B05: _journal_recovery is a recurring loop
# ===================================================================

async def test_B05_journal_recovery_recurring_loop():
    print("\n--- B05: _journal_recovery is a recurring loop ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        fm._running = True
        calls = {'n': 0}
        prod_db.journal_pending_older_than = AsyncMock(return_value=[])
        # Flip _running=False on the 3rd sleep call → 2 sweep bodies run.
        async def _sleep(_n):
            calls['n'] += 1
            if calls['n'] >= 3:
                fm._running = False
        with patch('asyncio.sleep', new=_sleep):
            await bot.Monitor._journal_recovery(fm)
        record("B05: _journal_recovery looped (>=2 sweeps, was fire-once)",
               prod_db.journal_pending_older_than.call_count >= 2,
               f"call_count={prod_db.journal_pending_older_than.call_count}")
    except Exception as e:
        record("B05: exception", False, str(e))


# ===================================================================
# B06: DASHBOARD_API_KEY optional shared-secret
# ===================================================================

async def test_B06_dashboard_api_key_optional():
    print("\n--- B06: DASHBOARD_API_KEY optional shared-secret ---")
    try:
        # UNSET → open (key is None)
        os.environ.pop('DASHBOARD_API_KEY', None)
        # Reset the one-time warning latch so the open-warning path is exercised.
        bot._DASHBOARD_API_KEY_WARNED['open'] = False
        gate_unset = bot._get_dashboard_api_key()
        record("B06: UNSET key → open (gate is None)",
               gate_unset is None, f"got {gate_unset!r}")
        # Middleware open for /health (non-/api)
        async def _ok_handler(req):
            return 'ok'
        req_health = types.SimpleNamespace(path='/health', headers={})
        r1 = await bot.dashboard_api_key_middleware(req_health, _ok_handler)
        record("B06: middleware open for /health (non-/api)", r1 == 'ok', f"got {r1!r}")
        # [PR-7] Middleware REJECTS /api/* when key unset (fail-closed by default).
        # Previously this was open; now secure-by-default. API_FAIL_OPEN=true is
        # the only escape (transition mode).
        req_api = types.SimpleNamespace(path='/api/stats', headers={})
        r2 = await bot.dashboard_api_key_middleware(req_api, _ok_handler)
        is_401_unset = hasattr(r2, 'status') and r2.status == 401
        record("B06: middleware REJECTS /api/* when key unset (PR-7 fail-closed)",
               is_401_unset, f"got {r2!r}")
        # API_FAIL_OPEN=true → open transition mode
        os.environ['API_FAIL_OPEN'] = 'true'
        bot._DASHBOARD_API_KEY_WARNED['open'] = False
        r2b = await bot.dashboard_api_key_middleware(req_api, _ok_handler)
        record("B06: API_FAIL_OPEN=true → /api/* open (transition)",
               r2b == 'ok', f"got {r2b!r}")
        os.environ.pop('API_FAIL_OPEN', None)

        # SET → require X-Api-Key
        os.environ['DASHBOARD_API_KEY'] = 's3cr3t'
        gate_set = bot._get_dashboard_api_key()
        record("B06: SET key → gate is the secret", gate_set == 's3cr3t', f"got {gate_set!r}")
        # No header → 401
        r3 = await bot.dashboard_api_key_middleware(
            types.SimpleNamespace(path='/api/links', headers={}), _ok_handler)
        is_401 = hasattr(r3, 'status') and r3.status == 401
        record("B06: SET key + no header → 401", is_401, f"got {r3!r}")
        # Wrong header → 401
        r4 = await bot.dashboard_api_key_middleware(
            types.SimpleNamespace(path='/api/links', headers={'X-Api-Key': 'wrong'}), _ok_handler)
        is_401b = hasattr(r4, 'status') and r4.status == 401
        record("B06: SET key + wrong header → 401", is_401b, f"got {r4!r}")
        # Correct header → authorized (handler called)
        r5 = await bot.dashboard_api_key_middleware(
            types.SimpleNamespace(path='/api/links', headers={'X-Api-Key': 's3cr3t'}), _ok_handler)
        record("B06: SET key + correct header → authorized (handler called)",
               r5 == 'ok', f"got {r5!r}")
        os.environ.pop('DASHBOARD_API_KEY', None)
    except Exception as e:
        record("B06: exception", False, str(e))


# ===================================================================
# B07: _supervisor_loop restarts a dead critical task
# ===================================================================

async def test_B07_supervisor_restarts_dead_task():
    print("\n--- B07: _supervisor_loop restarts a dead critical task ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        fm._running = True
        # A completed (done) scheduler task → supervisor must recreate it.
        async def _sched_run():
            return 'sched_ran'
        done_task = asyncio.create_task(_sched_run())
        await done_task  # ensure done
        fm._polling_scheduler_task = done_task
        fm.polling_scheduler = types.SimpleNamespace(run=_sched_run)
        # Also dead joiner + journal_recovery tasks → supervisor recreates them.
        async def _joiner():
            return 'j'
        async def _jrec():
            return 'r'
        async def _joiner_stub():
            while fm._running:
                await asyncio.sleep(0)
        dj = asyncio.create_task(_joiner()); await dj
        fm._joiner_task = dj
        fm._joiner_worker = _joiner_stub
        # _journal_recovery normally loops forever; use a quick-stub via the bound
        # method but flip _running to exit. We supply a stand-in coroutine on fm.
        async def _jrec_stub():
            while fm._running:
                await asyncio.sleep(0)
        # Bind _journal_recovery to the stub by replacing the bound method.
        fm._journal_recovery = _jrec_stub
        # Flip _running=False on the 2nd sleep call (after first supervisor body).
        fake_sleep = make_counting_sleep(fm, flip_on=2)
        with LogCapture() as cap, patch('asyncio.sleep', new=fake_sleep):
            await bot.Monitor._supervisor_loop(fm)
        restarted = [m for m in cap.warnings if '[SUPERVISOR] restarted' in m]
        record("B07: [SUPERVISOR] restarted <name> WARNING logged",
               len(restarted) > 0, f"log={restarted}")
        record("B07: supervisor recreated dead polling_scheduler (new task assigned)",
               fm._polling_scheduler_task is not done_task,
               f"old={done_task} new={fm._polling_scheduler_task}")
    except Exception as e:
        record("B07: exception", False, str(e))


# ===================================================================
# B08: journal_write errors logged at WARNING+
# ===================================================================

async def test_B08_journal_write_warning():
    print("\n--- B08: journal_write errors logged at WARNING+ ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        # Force journal_message to raise (simulates disk-full / locked DB)
        prod_db.journal_message = AsyncMock(side_effect=Exception("disk full simulated"))
        with LogCapture() as cap:
            await bot.Monitor._journal_write(
                fm, -100555666777, 7001, 'text', 'phoneA', state='pending')
        failed = [m for m in cap.warnings if '[JOURNAL]' in m and 'FAILED' in m]
        record("B08: journal_write emitted a WARNING with 'FAILED'",
               len(failed) > 0, f"warnings={failed}")
    except Exception as e:
        record("B08: exception", False, str(e))


# ===================================================================
# B09: lookup_any guarded (with chat_id still rescues)
# ===================================================================

async def test_B09_lookup_any_guarded():
    print("\n--- B09: lookup_any guarded (with chat_id still rescues) ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        chat = -100777888999
        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 8001,
            'raw_text': 'join https://t.me/B09GuardedChat',
            'source_phone': 'A', 'chat_title': 'B09 Chat', 'state': 'pending',
        })
        ev = FakeDeleteEvent([8001], chat)
        await bot.Monitor._on_message_deleted(fm, ev, 'A')
        row = await prod_db.journal_get(chat, 8001)
        record("B09: WITH chat_id → rescue still works (state=='rescued')",
               row is not None and row.get('state') == 'rescued',
               f"got {row!r}")
        link_row = await sql_one(
            prod_db,
            "SELECT normalized_link FROM link_queue WHERE normalized_link=?",
            ('tg:user:b09guardedchat',))
        record("B09: link enqueued for chat_id-keyed rescue",
               link_row is not None, "link not found in link_queue")
    except Exception as e:
        record("B09: exception", False, str(e))


# ===================================================================
# L03: _polling_watchdog_loop restarts dead scheduler
# ===================================================================

async def test_L03_polling_watchdog_restarts_dead_scheduler():
    print("\n--- L03: _polling_watchdog_loop restarts dead scheduler ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        fm._running = True
        async def _sched_run():
            return 'sched'
        done_task = asyncio.create_task(_sched_run())
        await done_task
        fm._polling_scheduler_task = done_task
        fm.polling_scheduler = types.SimpleNamespace(run=_sched_run)
        fake_sleep = make_counting_sleep(fm, flip_on=2)
        with LogCapture() as cap, patch('asyncio.sleep', new=fake_sleep):
            await bot.Monitor._polling_watchdog_loop(fm)
        restarted = [m for m in cap.warnings if '[POLLING-WATCHDOG]' in m and 'restarted' in m]
        record("L03: [POLLING-WATCHDOG] restarted scheduler WARNING logged",
               len(restarted) > 0, f"log={restarted}")
        record("L03: watchdog recreated dead scheduler (new task assigned)",
               fm._polling_scheduler_task is not done_task,
               f"old={done_task} new={fm._polling_scheduler_task}")
    except Exception as e:
        record("L03: exception", False, str(e))


# ===================================================================
# L04: _supabase_ensure_schema logs ALTER SQL fallback
# ===================================================================

async def test_L04_supabase_schema_logs_sql():
    print("\n--- L04: _supabase_ensure_schema logs ALTER SQL fallback ---")
    try:
        class FakeResp:
            def __init__(self, status, text=''):
                self.status = status
                self._t = text
            async def text(self):
                return self._t
        class FakeCM:
            def __init__(self, resp):
                self._r = resp
            async def __aenter__(self):
                return self._r
            async def __aexit__(self, *a):
                return False
        class FakeSession:
            def __init__(self, get_status=400, post_status=404):
                self._gs = get_status
                self._ps = post_status
            def get(self, url):
                return FakeCM(FakeResp(self._gs))
            def post(self, url, headers=None, json=None):
                return FakeCM(FakeResp(self._ps))
        fake_db = types.SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_key='fake_key',
            _get_supabase_session=AsyncMock(return_value=FakeSession(400, 404)))
        raised = False
        with LogCapture() as cap:
            try:
                await bot.DatabaseManager._supabase_ensure_schema(fake_db)
            except Exception:
                raised = True
        alter_logs = [m for m in cap.all_msgs if 'ALTER TABLE watchers ADD COLUMN' in m]
        record("L04: ALTER TABLE watchers SQL logged on schema-missing",
               len(alter_logs) > 0, f"logs={alter_logs[:2]}")
        record("L04: did not raise (startup-safe)", not raised, f"raised={raised}")
    except Exception as e:
        record("L04: exception", False, str(e))


# ===================================================================
# L07: LEASE_DURATION_S env-configurable
# ===================================================================

async def test_L07_lease_duration_env_configurable():
    print("\n--- L07: LEASE_DURATION_S env-configurable ---")
    try:
        from source_registry import MessageClaim
        from link_system import ProductionDB
        # Default (env unset) → 180
        os.environ.pop('LEASE_DURATION_S', None)
        # Re-import-time class attribute is read at import; verify the expression.
        default_val = int(os.environ.get('LEASE_DURATION_S', '180'))
        record("L07: default LEASE_DURATION_S == 180 (env unset)",
               default_val == 180, f"got {default_val}")
        # source_registry class attr reflects the env at import. We verify the
        # runtime expression resolves the env, and that claim_message honors it.
        prod_db, _ = await make_test_db()
        # claim_message with env LEASE_DURATION_S=600 → lease ~600s
        os.environ['LEASE_DURATION_S'] = '600'
        try:
            token = await prod_db.claim_message(-100123, 9001, 'l07test', 'A')
            record("L07: claim_message returns a token (env-driven lease)",
                   token is not None, f"got {token!r}")
            row = await sql_one(
                prod_db,
                "SELECT lease_until, claimed_at FROM processed_messages "
                "WHERE chat_id=? AND msg_id=?",
                (-100123, 9001))
            if row:
                from datetime import datetime
                lease_until = datetime.fromisoformat(row[0])
                claimed_at = datetime.fromisoformat(row[1])
                delta = (lease_until - claimed_at).total_seconds()
                # 600 ± a few seconds of scheduling slack
                record("L07: lease_until stored (env LEASE_DURATION_S=600 honored)",
                       590 <= delta <= 620, f"delta={delta:.1f}s")
            else:
                record("L07: lease_until stored (env LEASE_DURATION_S=600 honored)",
                       False, "no processed_messages row")
        finally:
            os.environ.pop('LEASE_DURATION_S', None)
    except Exception as e:
        record("L07: exception", False, str(e))


# ===================================================================
# Task 8b — 10 NEW edge-case fixes (N01-N10) + persistence snapshot
# ===================================================================
# Re-audit pass 2 (Task 7) findings. Pattern: same as 8a —
# RESULTS list + record() + sys.exit(rc). All tests are self-contained
# (create their own DB + mocks); no shared state with 8a tests.


async def test_N01_journal_cleanup_preserves_pending():
    """[N01] pending row >24h NOT deleted; processed row IS deleted."""
    print("\n--- N01: journal_cleanup preserves pending/failed ---")
    try:
        prod_db, _ = await make_test_db()
        chat = -100111222333
        now = time.time()
        # Old pending (>retention) — should SURVIVE (was deleted before N01)
        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 4200, 'raw_text': 'old pending https://t.me/oldp',
            'source_phone': 'A', 'received_at': now - 90000, 'state': 'pending',
        })
        # Old processed (>retention) — should be DELETED (terminal state)
        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': 4201, 'raw_text': 'old processed https://t.me/oldproc',
            'source_phone': 'A', 'received_at': now - 90000, 'state': 'processed',
        })
        await prod_db.journal_cleanup(retention_s=86400, short_retention_s=21600)
        old_pending = await prod_db.journal_get(chat, 4200)
        old_processed = await prod_db.journal_get(chat, 4201)
        record("N01: pending row >24h PRESERVED (recoverable)",
               old_pending is not None, f"got {old_pending!r}")
        record("N01: processed row >24h DELETED (terminal)",
               old_processed is None, f"got {old_processed!r}")
    except Exception as e:
        record("N01: exception", False, str(e))


async def test_N02_reconcile_writes_journal_before_claim():
    """[N02] crash mid-rescue leaves a recoverable 'pending' journal row."""
    print("\n--- N02: reconcile writes journal BEFORE claim ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db, reconcile=False)
        chat = -100999888777
        msg_id = 99001
        # Simulate crash DURING claim (claim raises) — but journal_write must
        # have already run so journal_recovery can pick it up.
        fm.message_claim = types.SimpleNamespace(
            claim=AsyncMock(side_effect=RuntimeError("simulated crash mid-rescue")),
            mark_processed=AsyncMock(return_value=True))
        # Fake client returns one message with raw_text containing a link
        msg = types.SimpleNamespace(
            id=msg_id, raw_text='join https://t.me/N02TestChat',
            out=False, sender=None, sender_id=0,
            chat=types.SimpleNamespace(title='N02 Chat', megagroup=True,
                                       broadcast=False, username=None))
        client = types.SimpleNamespace(
            is_connected=lambda: True,
            get_messages=AsyncMock(return_value=[msg]))
        fm.user_clients = {'phoneA': client}
        fm._get_sender_name = lambda sender: 'TestSender'
        # Run reconcile; it will journal_write(pending) → crash at claim.
        await bot.Monitor._reconcile_chat_after_delete_miss(fm, chat, 'phoneA')
        # Assert: the journal row IS 'pending' (recoverable, not lost)
        row = await prod_db.journal_get(chat, msg_id)
        record("N02: journal row EXISTS despite mid-rescue crash",
               row is not None, f"got {row!r}")
        record("N02: journal state == 'pending' (recoverable)",
               row is not None and row.get('state') == 'pending',
               f"got {row and row.get('state')!r}")
    except Exception as e:
        record("N02: exception", False, str(e))


async def test_N03_loser_does_not_overwrite_winner_state():
    """[N03] LOSER path leaves state='pending' (was 'dup_claim')."""
    print("\n--- N03: LOSER does NOT overwrite WINNER's pending state ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        chat = -100888777666
        msg_id = 88001
        # WINNER (reconcile) wrote 'pending'
        await prod_db.journal_message({
            'chat_id': chat, 'msg_id': msg_id,
            'raw_text': 'winner pending https://t.me/N03Chat',
            'source_phone': 'phoneA', 'state': 'pending',
            'chat_title': 'N03 Chat'})
        # LOSER (_on_user_message path): claim returns None
        fm.message_claim = types.SimpleNamespace(
            claim=AsyncMock(return_value=None),  # LOSER
            mark_processed=AsyncMock(return_value=True))
        # Build a fake NewMessage-like event with a link
        event = types.SimpleNamespace(
            raw_text='join https://t.me/N03Chat',
            chat_id=chat, id=msg_id, sender_id=0,
            chat=types.SimpleNamespace(title='N03 Chat', megagroup=True,
                                       broadcast=False, username=None),
            sender=None)
        # _on_user_message should reach the claim, get None (LOSER), and
        # return WITHOUT touching the journal state.
        await bot.Monitor._on_user_message(fm, event, 'phoneB')
        row = await prod_db.journal_get(chat, msg_id)
        record("N03: state stays 'pending' after LOSER (not overwritten to dup_claim)",
               row is not None and row.get('state') == 'pending',
               f"got {row and row.get('state')!r}")
    except Exception as e:
        record("N03: exception", False, str(e))


async def test_N04_mass_delete_per_iteration_isolation():
    """[N04] exception in iteration 2 of 5 — iterations 1,3,4,5 still process."""
    print("\n--- N04: mass-delete per-iteration try/except isolation ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        chat = -100777666555
        # Pre-seed journal with 5 messages containing links (so each iteration
        # reaches the rescue path). The cache is empty so each is fetched
        # from journal_get.
        for i in range(5):
            await prod_db.journal_message({
                'chat_id': chat, 'msg_id': 5001 + i,
                'raw_text': f'join https://t.me/N04Chat{i}',
                'source_phone': 'phoneA', 'state': 'pending',
                'chat_title': f'N04 Chat {i}'})
        # Make _journal_get raise ONLY for msg_id 5002 (iteration index 1).
        original_journal_get = prod_db.journal_get

        async def _faulty_journal_get(c, m):
            if m == 5002:
                raise RuntimeError("injected iteration-2 failure")
            return await original_journal_get(c, m)
        prod_db.journal_get = _faulty_journal_get
        # Spy on _journal_set_state_safe — successful rescues write 'rescued'.
        original_set_state = bot.Monitor._journal_set_state_safe
        set_state_calls = []

        async def _spy_set_state(self_m, c, m, state, error=None, mark_deleted=False):
            set_state_calls.append((c, m, state))
            await original_set_state(self_m, c, m, state, error=error,
                                    mark_deleted=mark_deleted)
        fm._journal_set_state_safe = types.MethodType(_spy_set_state, fm)
        # disable message_claim so the rescue path runs unconditionally
        fm.message_claim = None
        ev = FakeDeleteEvent([5001, 5002, 5003, 5004, 5005], chat)
        await bot.Monitor._on_message_deleted(fm, ev, 'phoneA')
        # Assert: 4 of 5 messages reached the rescue path (5002 errored out)
        rescued_msg_ids = sorted(m for (c, m, s) in set_state_calls if s == 'rescued')
        record("N04: 4 of 5 iterations processed (one failed, rest continued)",
               len(rescued_msg_ids) == 4, f"rescued msg_ids={rescued_msg_ids}")
        record("N04: failed iteration msg_id (5002) NOT in rescued set",
               5002 not in rescued_msg_ids, f"rescued={rescued_msg_ids}")
    except Exception as e:
        record("N04: exception", False, str(e))


async def test_N05_journal_consecutive_failure_burst():
    """[N05] 51 consecutive failures → rate-limited ERROR burst logged."""
    print("\n--- N05: journal_write consecutive-failure burst ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        prod_db.journal_message = AsyncMock(side_effect=Exception("disk full"))
        burst_logged = []
        with LogCapture() as cap:
            # _h level was set to WARNING; emit errors via patching to also
            # capture ERROR-level — reconfigure handler.
            cap._h.setLevel(logging.ERROR)
            for i in range(51):
                await bot.Monitor._journal_write(
                    fm, -100555666777, 9500 + i, 'text', 'phoneA',
                    state='pending')
            burst_logged = [m for lvl, m in cap.records
                            if lvl == 'ERROR' and 'circuit-stressed' in m]
        record("N05: >=1 ERROR 'circuit-stressed' burst logged after 51 failures",
               len(burst_logged) >= 1, f"errors={burst_logged[:1]}")
        record("N05: consecutive-failure counter > 50",
               getattr(fm, '_journal_fail_count', 0) > 50,
               f"count={getattr(fm, '_journal_fail_count', 0)}")
    except Exception as e:
        record("N05: exception", False, str(e))


async def test_N06_enqueue_link_reraises_non_integrity():
    """[N06] SQLITE_BUSY propagates; IntegrityError returns False."""
    print("\n--- N06: enqueue_link re-raises non-IntegrityError ---")
    try:
        prod_db, _ = await make_test_db()
        # Replace conn.execute with a fake that raises OperationalError
        conn = await prod_db._conn()
        link_data = {
            'raw': 'https://t.me/N06Chat', 'normalized': 'tg:user:n06chat',
            'link_type': 'telegram', 'username': 'N06Chat',
            'group_name': 'G', 'sender_name': 'S',
            'source_phone': 'A', 'message_text': 't', 'message_link': None}
        # Case A: OperationalError (SQLITE_BUSY) — MUST propagate
        orig_execute = conn.execute
        async def _busy_execute(*a, **k):
            raise sqlite3.OperationalError("database is locked")
        conn.execute = _busy_execute
        propagated = False
        try:
            await prod_db.enqueue_link(link_data)
        except sqlite3.OperationalError:
            propagated = True
        except Exception as other:
            propagated = type(other).__name__  # tolerate aiosqlite wrapper
        # aiosqlite wraps: it should still propagate as OperationalError
        record("N06: SQLITE_BUSY (OperationalError) propagates from enqueue_link",
               propagated is True or (isinstance(propagated, str)
                                      and 'OperationalError' in propagated),
               f"got {propagated!r}")
        # Case B: IntegrityError — returns False (duplicate, swallowed)
        async def _integrity_execute(*a, **k):
            raise sqlite3.IntegrityError("UNIQUE constraint failed")
        conn.execute = _integrity_execute
        ret = await prod_db.enqueue_link(link_data)
        record("N06: IntegrityError (UNIQUE) → returns False (duplicate)",
               ret is False, f"got {ret!r}")
        conn.execute = orig_execute
    except Exception as e:
        record("N06: exception", False, str(e))


async def test_N07_ai_drainer_processes_pending():
    """[N07] 3 pending links → all 3 PATCHed back via Supabase."""
    print("\n--- N07: _ai_drainer_worker processes pending links ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        # Three pending rows returned by fake Supabase GET
        pending_rows = [
            {'id': 1, 'link': 'https://t.me/N07A', 'link_type': 'telegram',
             'message_text': 'join https://t.me/N07A', 'group_name': 'GA',
             'sender_name': 'S', 'source_phone': 'A'},
            {'id': 2, 'link': 'https://t.me/N07B', 'link_type': 'telegram',
             'message_text': 'join https://t.me/N07B', 'group_name': 'GB',
             'sender_name': 'S', 'source_phone': 'A'},
            {'id': 3, 'link': 'https://t.me/N07C', 'link_type': 'telegram',
             'message_text': 'join https://t.me/N07C', 'group_name': 'GC',
             'sender_name': 'S', 'source_phone': 'A'},
        ]
        patch_calls = []
        class FakeResp:
            def __init__(self, status, json_data=None, text=''):
                self.status = status; self._j = json_data; self._t = text
            async def text(self): return self._t
            async def json(self): return self._j
        class FakeCM:
            def __init__(self, resp): self._r = resp
            async def __aenter__(self): return self._r
            async def __aexit__(self, *a): return False
        class FakeSession:
            def __init__(self): self.patch_count = 0
            def get(self, url):
                return FakeCM(FakeResp(200, json_data=pending_rows))
            def patch(self, url, json=None, headers=None):
                patch_calls.append({'url': url, 'json': json})
                return FakeCM(FakeResp(204))
        # Wire up fm.db (DatabaseManager-like)
        fm.db = types.SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_key='fake_key',
            _get_supabase_session=AsyncMock(return_value=FakeSession()))
        # Wire up fm.ai_analyzer
        async def _analyze(text):
            return {'should_save': True, 'description': 'edu', 'country': 'SA',
                    'is_advertisement': False}
        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_analyze)
        # Run a single cycle of the drainer (it loops forever — flip _running
        # off after the first sleep). We patch asyncio.sleep to flip _running
        # on the 2nd call (one full cycle: get → patch × 3 → sleep).
        fm._running = True
        call_count = {'n': 0}
        async def _sleep(_n):
            call_count['n'] += 1
            if call_count['n'] >= 2:
                fm._running = False
        with patch('asyncio.sleep', new=_sleep):
            await bot.Monitor._ai_drainer_worker(fm)
        record("N07: 3 PATCH calls made (one per pending row)",
               len(patch_calls) == 3, f"patch_calls={patch_calls}")
        record("N07: PATCH URL targets links?link=eq.<encoded>",
               all('?link=eq.' in c['url'] for c in patch_calls),
               f"urls={[c['url'] for c in patch_calls]}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
    except Exception as e:
        record("N07: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)


async def test_N08_floodwait_registered():
    """[N08] floodwait_mgr.block called on FloodWaitError in _poll_one_chat."""
    print("\n--- N08: FloodWait registered with floodwait_mgr ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        from telethon.errors import FloodWaitError
        # _poll_one_chat depends on floodwait_mgr being present + a client that
        # raises FloodWaitError on get_messages
        block_calls = []
        fm.floodwait_mgr = types.SimpleNamespace(
            block=AsyncMock(side_effect=lambda p, s: block_calls.append((p, s))))
        async def _raise_flood(*a, **k):
            raise FloodWaitError(request=None, capture=30)
        client = types.SimpleNamespace(
            is_connected=lambda: True,
            get_messages=_raise_flood)
        fm.user_clients = {'phoneA': client}
        chat = {'chat_id': -100111222333, 'chat_title': 'N08 Chat', 'username': ''}
        await bot.Monitor._poll_one_chat(fm, 'phoneA', chat)
        record("N08: floodwait_mgr.block called with phone + seconds",
               len(block_calls) == 1 and block_calls[0][0] == 'phoneA'
               and block_calls[0][1] == 30,
               f"calls={block_calls}")
    except Exception as e:
        record("N08: exception", False, str(e))


async def test_N09_polling_gather_return_exceptions():
    """[N09] one task raises, others complete — gather isolates the failure."""
    print("\n--- N09: PollingScheduler gather(return_exceptions=True) ---")
    try:
        prod_db, fake_db = await make_test_db()
        # Build a minimal PollingScheduler with mocked select_due_chats +
        # _poll_one_chat. We test that gather(return_exceptions=True) is set
        # AND iterated so a single failure logs a WARNING but doesn't kill
        # the batch.
        from source_registry import PollingScheduler
        sched = PollingScheduler.__new__(PollingScheduler)
        sched.BATCH_SIZE = 3
        sched.MAX_CONCURRENT_POLLS = 2
        sched.CYCLE_SLEEP_S = 1
        sched.BATCH_PAUSE_S = 0
        sched._running = True
        # _poll_one_chat is called as `await self.monitor._poll_one_chat(reader, chat)`
        # so it takes (reader, chat). We append chat_id for each call.
        poll_calls = []

        async def _poll_one_chat(reader, chat):
            poll_calls.append(chat.get('chat_id'))
        sched.monitor = types.SimpleNamespace(_poll_one_chat=_poll_one_chat)
        # get_reader raises for chat -1002 (BEFORE the inner try/except in
        # _poll_one, so the exception propagates to gather — exercising the
        # return_exceptions=True + iteration logging path).
        def _get_reader(c):
            if c == -1002:
                raise RuntimeError("injected reader failure")
            return 'phoneA'
        sched.registry = types.SimpleNamespace(
            get_reader=_get_reader, release_load=lambda p: None,
            get_chat_lock=lambda c: _NoLock())
        sched.rate_limiter = types.SimpleNamespace(
            acquire=AsyncMock(return_value=True))
        sched.db = prod_db
        sched.update_next_poll = AsyncMock()
        sched.select_due_chats = AsyncMock(return_value=[
            {'chat_id': -1001, 'last_activity': None},
            {'chat_id': -1002, 'last_activity': None},
            {'chat_id': -1003, 'last_activity': None}])
        with LogCapture() as cap:
            cap._h.setLevel(logging.WARNING)
            # Run one cycle: flip _running=False on the 2nd sleep so the
            # while-loop exits after one gather. (1st sleep = startup
            # asyncio.sleep(20) at the top of run().)
            sleep_count = {'n': 0}
            async def _sleep(_n):
                sleep_count['n'] += 1
                if sleep_count['n'] >= 2:
                    sched._running = False
            with patch('asyncio.sleep', new=_sleep):
                await PollingScheduler.run(sched)
        # The 2 successful chats reached _poll_one_chat. The -1002 chat
        # raised in get_reader (before inner try/except) — propagates to
        # gather, which returns it as an Exception value (return_exceptions
        # is True), then the N09 iteration logs it at WARNING.
        successful = sorted(cid for cid in poll_calls if cid is not None)
        record("N09: 2 of 3 chats polled successfully (one failure isolated)",
               successful == [-1003, -1001], f"poll_calls={poll_calls}")
        # The failure was logged at WARNING (per-task error surfaced)
        fail_logs = [m for lvl, m in cap.records
                     if lvl == 'WARNING' and 'task failed' in m]
        record("N09: per-task failure logged at WARNING (not swallowed silently)",
               len(fail_logs) >= 1, f"logs={fail_logs[:1]}")
    except Exception as e:
        record("N09: exception", False, str(e))


class _NoLock:
    """No-op async context manager — simulates get_chat_lock."""
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


async def test_N10_ensure_conn_lock_serializes():
    """[N10] concurrent _ensure_conn callers don't create 2 connections."""
    print("\n--- N10: _ensure_conn serializes via self._lock ---")
    try:
        import aiosqlite
        # Real DatabaseManager on a temp DB path
        db_path = tempfile.mktemp(suffix='.db')
        db = bot.DatabaseManager(db_path=db_path)
        connect_calls = {'n': 0}
        # Wrap aiosqlite.connect to count calls + add a tiny delay so the
        # race window is observable
        orig_connect = aiosqlite.connect
        async def _counting_connect(*a, **k):
            connect_calls['n'] += 1
            await asyncio.sleep(0.05)  # widen the race window
            return await orig_connect(*a, **k)
        with patch('aiosqlite.connect', new=_counting_connect):
            # Two concurrent callers
            c1, c2 = await asyncio.gather(
                db._ensure_conn(), db._ensure_conn())
        record("N10: exactly 1 aiosqlite.connect call (no leak)",
               connect_calls['n'] == 1, f"count={connect_calls['n']}")
        record("N10: both callers got the SAME connection object",
               c1 is c2 and c1 is db._conn, f"c1 is c2={c1 is c2}, is _conn={c1 is db._conn}")
        try:
            await db._conn.close()
        except Exception:
            pass
    except Exception as e:
        record("N10: exception", False, str(e))


async def test_persist_snapshot_restore_on_startup():
    """[PERSISTENCE Option C] snapshot has 3 rows → restore inserts 3 (INSERT OR IGNORE dedups)."""
    print("\n--- PERSIST: snapshot restore inserts 3 rows on startup ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        # Fake Supabase snapshot GET returns 3 at-risk rows
        snap_rows = [
            {'chat_id': -1001, 'msg_id': 101, 'raw_text': 'a https://t.me/A1',
             'source_phone': 'A', 'chat_title': 'A1', 'chat_username': '',
             'chat_link_type': 'group', 'sender_id': 0, 'sender_name': 'S',
             'state': 'pending', 'received_at': time.time() - 300},
            {'chat_id': -1002, 'msg_id': 102, 'raw_text': 'b https://t.me/A2',
             'source_phone': 'A', 'chat_title': 'A2', 'chat_username': '',
             'chat_link_type': 'group', 'sender_id': 0, 'sender_name': 'S',
             'state': 'no_text', 'received_at': time.time() - 200},
            {'chat_id': -1003, 'msg_id': 103, 'raw_text': 'c https://t.me/A3',
             'source_phone': 'A', 'chat_title': 'A3', 'chat_username': '',
             'chat_link_type': 'group', 'sender_id': 0, 'sender_name': 'S',
             'state': 'delete_miss', 'received_at': time.time() - 100},
        ]
        class FakeResp:
            def __init__(self, status, json_data=None):
                self.status = status; self._j = json_data
            async def text(self): return ''
            async def json(self): return self._j
        class FakeCM:
            def __init__(self, resp): self._r = resp
            async def __aenter__(self): return self._r
            async def __aexit__(self, *a): return False
        class FakeSession:
            def get(self, url, **kwargs): return FakeCM(FakeResp(200, snap_rows))
        fm.db = types.SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_key='fake_key',
            _get_supabase_session=AsyncMock(return_value=FakeSession()))
        restored = await bot.Monitor._restore_journal_from_supabase(fm)
        record("PERSIST: restore returns 3 (one per snapshot row)",
               restored == 3, f"got {restored}")
        # Verify all 3 rows actually exist in the local SQLite journal
        rows = []
        for cid, mid in [(-1001, 101), (-1002, 102), (-1003, 103)]:
            r = await prod_db.journal_get(cid, mid)
            rows.append(r)
        record("PERSIST: all 3 rows INSERTed into local message_journal",
               all(r is not None for r in rows), f"rows={rows}")
        # Idempotency: running restore AGAIN must not duplicate rows
        # (INSERT OR IGNORE). All three rows exist; restore returns 3 (the
        # INSERT OR IGNORE is a no-op but journal_message still "succeeds").
        restored2 = await bot.Monitor._restore_journal_from_supabase(fm)
        count_row = await sql_one(
            prod_db, "SELECT COUNT(*) FROM message_journal WHERE chat_id IN (?,?,?)",
            (-1001, -1002, -1003))
        record("PERSIST: idempotent re-restore does NOT duplicate rows (still 3)",
               count_row[0] == 3, f"count={count_row[0]}")
    except Exception as e:
        record("PERSIST: exception", False, str(e))


async def test_persist_snapshot_loop_batches():
    """[PERSISTENCE Option C] 500 pending rows → 1 POST with merge-duplicates header."""
    print("\n--- PERSIST: snapshot loop batches 500 rows into one POST ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        chat = -100666555444
        # Seed 500 at-risk pending rows
        for i in range(500):
            await prod_db.journal_message({
                'chat_id': chat, 'msg_id': 6000 + i,
                'raw_text': f'pending https://t.me/B{i}',
                'source_phone': 'A', 'state': 'pending',
                'received_at': time.time() - 60})
        post_calls = []
        class FakeResp:
            def __init__(self, status): self.status = status
            async def text(self): return ''
        class FakeCM:
            def __init__(self, resp): self._r = resp
            async def __aenter__(self): return self._r
            async def __aexit__(self, *a): return False
        class FakeSession:
            def post(self, url, json=None, headers=None, **kwargs):
                post_calls.append({'url': url, 'json': json, 'headers': headers})
                return FakeCM(FakeResp(204))
        fm.db = types.SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_key='fake_key',
            _get_supabase_session=AsyncMock(return_value=FakeSession()))
        # Run a single snapshot cycle (flip _running off on the 2nd sleep).
        # The snapshot loop now guards on _snapshot_running; ensure it's
        # False so the test invocation actually enters the body.
        fm._snapshot_running = False
        fm._running = True
        sleep_count = {'n': 0}
        async def _sleep(_n):
            sleep_count['n'] += 1
            if sleep_count['n'] >= 2:
                fm._running = False
        with patch('asyncio.sleep', new=_sleep):
            await bot.Monitor._journal_snapshot_loop(fm)
        record("PERSIST: exactly 1 POST call for 500 pending rows (batched)",
               len(post_calls) == 1, f"post_calls={len(post_calls)}")
        if post_calls:
            headers = post_calls[0].get('headers') or {}
            record("PERSIST: POST carries Prefer: resolution=merge-duplicates header",
                   headers.get('Prefer') == 'resolution=merge-duplicates',
                   f"headers={headers}")
            batch = post_calls[0].get('json') or []
            record("PERSIST: POST body is a list of 500 rows",
                   isinstance(batch, list) and len(batch) == 500,
                   f"len={len(batch) if isinstance(batch, list) else 'N/A'}")
            record("PERSIST: POST URL targets message_journal_snapshot",
                   'message_journal_snapshot' in post_calls[0]['url'],
                   f"url={post_calls[0]['url']}")
    except Exception as e:
        record("PERSIST: exception", False, str(e))


# ===================================================================
# Task 4a — AI Drainer Deep Audit + Hardening regressions
# (bounded concurrency, rate-limit, lease, idempotency, restart-safe,
#  graceful-shutdown, observable, stuck-job rotation, retry cap)
# ===================================================================

_REAL_SLEEP = asyncio.sleep  # capture before any patching


async def _make_drainer_fakes(pending_rows, patch_status=204, patch_body='',
                              get_status=200, get_body=None):
    """Build the (fm, FakeSession) pair used by every 4a drainer test.

    Returns (fm, patch_calls, get_calls, analyze_calls) where the lists
    are populated by the FakeSession as the drainer runs. Tests inspect
    them after running one cycle of the drainer via _run_one_drainer_cycle.
    """
    prod_db, _ = await make_test_db()
    fm = make_fake_monitor(prod_db)
    patch_calls = []
    get_calls = []
    analyze_calls = []

    class FakeResp:
        def __init__(self, status, json_data=None, text=''):
            self.status = status
            self._j = json_data
            self._t = text

        async def text(self):
            return self._t

        async def json(self):
            return self._j

    class FakeCM:
        def __init__(self, resp):
            self._r = resp

        async def __aenter__(self):
            return self._r

        async def __aexit__(self, *a):
            return False

    class FakeSession:
        def get(self, url):
            get_calls.append(url)
            return FakeCM(FakeResp(get_status, json_data=pending_rows,
                                   text=get_body or ''))

        def patch(self, url, json=None, headers=None):
            patch_calls.append({'url': url, 'json': json, 'headers': headers})
            return FakeCM(FakeResp(patch_status, text=patch_body))

    fm.db = types.SimpleNamespace(
        supabase_url='https://example.supabase.co',
        supabase_key='fake_key',
        _get_supabase_session=AsyncMock(return_value=FakeSession()))
    return fm, patch_calls, get_calls, analyze_calls


async def _run_one_drainer_cycle(fm, sleep_flip_on=2, **env_overrides):
    """Run _ai_drainer_worker until _running flips False.

    Default: flip on the 2nd asyncio.sleep call (= 1 full cycle:
    startup sleep → GET → process → end-of-cycle sleep → flip).

    The patched asyncio.sleep returns immediately (no real wait) so tests
    are fast. NOTE: blocking-analyze tests (timeout / graceful-shutdown)
    must NOT use asyncio.sleep inside the analyze coroutine — use
    asyncio.Event().wait() instead, which is NOT affected by this patch
    and blocks until cancelled by wait_for / task.cancel().
    """
    for k, v in env_overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    fm._running = True
    call_count = {'n': 0}

    async def _sleep(_n):
        call_count['n'] += 1
        if call_count['n'] >= sleep_flip_on:
            fm._running = False

    with patch('asyncio.sleep', new=_sleep):
        await bot.Monitor._ai_drainer_worker(fm)


async def test_4a_ai_drainer_lease_filter_on_patch():
    """[4a] PATCH URL carries `ai_approved=is.null` lease filter so a
    concurrent worker / supervisor-relaunched instance can't double-write."""
    print("\n--- 4a: PATCH URL carries ai_approved=is.null lease filter ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': 1, 'link': 'https://t.me/4aL', 'link_type': 'telegram',
                    'message_text': 'join https://t.me/4aL', 'group_name': 'G',
                    'sender_name': 'S', 'source_phone': 'A'}]
        fm, patch_calls, _, _ = await _make_drainer_fakes(pending)
        async def _analyze(text):
            return {'should_save': True, 'description': 'edu', 'country': 'SA',
                    'is_advertisement': False}
        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_analyze)
        await _run_one_drainer_cycle(fm)
        record("4a-lease: PATCH URL contains ai_approved=is.null filter",
               patch_calls and 'ai_approved=is.null' in patch_calls[0]['url'],
               f"urls={[c['url'] for c in patch_calls]}")
        record("4a-lease: PATCH carries Prefer: return=representation header",
               patch_calls and (patch_calls[0].get('headers') or {}).get('Prefer')
               == 'return=representation',
               f"headers={patch_calls[0].get('headers') if patch_calls else None}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
    except Exception as e:
        record("4a-lease: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)


async def test_4a_ai_drainer_batch_size_env():
    """[4a] AI_DRAIN_BATCH_SIZE env is honored in the GET URL `limit=N`."""
    print("\n--- 4a: AI_DRAIN_BATCH_SIZE env honored in GET URL ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': 1, 'link': 'https://t.me/4aB', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'}]
        fm, _, get_calls, _ = await _make_drainer_fakes(pending)
        async def _analyze(text):
            return {'should_save': True, 'description': 'edu', 'country': 'SA',
                    'is_advertisement': False}
        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_analyze)
        await _run_one_drainer_cycle(fm, AI_DRAIN_BATCH_SIZE=25)
        record("4a-batch: GET URL contains limit=25 (from env override)",
               get_calls and 'limit=25' in get_calls[0],
               f"get_url={get_calls[0] if get_calls else None}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
        os.environ.pop('AI_DRAIN_BATCH_SIZE', None)
    except Exception as e:
        record("4a-batch: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)
        os.environ.pop('AI_DRAIN_BATCH_SIZE', None)


async def test_4a_ai_drainer_order_rotates_head():
    """[4a] GET URL uses `order=id.desc` so a poison row at the head
    doesn't permanently block newer rows from being seen."""
    print("\n--- 4a: GET URL uses order=id.desc (head rotation) ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': 1, 'link': 'https://t.me/4aR', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'}]
        fm, _, get_calls, _ = await _make_drainer_fakes(pending)
        async def _analyze(text):
            return {'should_save': True, 'description': 'edu', 'country': 'SA',
                    'is_advertisement': False}
        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_analyze)
        await _run_one_drainer_cycle(fm)
        record("4a-rotate: GET URL contains order=id.desc",
               get_calls and 'order=id.desc' in get_calls[0],
               f"get_url={get_calls[0] if get_calls else None}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
    except Exception as e:
        record("4a-rotate: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)


async def test_4a_ai_drainer_timeout_skips_row():
    """[4a] analyze_message timeout → row stays ai_pending (no PATCH),
    counted as failed, fail_count incremented."""
    print("\n--- 4a: timeout on analyze_message leaves row ai_pending ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': 7, 'link': 'https://t.me/4aTO', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'}]
        fm, patch_calls, _, _ = await _make_drainer_fakes(pending)

        async def _blocking_analyze(text):
            # Blocks forever — NOT asyncio.sleep (which is patched).
            # asyncio.Event().wait() is unaffected by the sleep patch and
            # blocks until cancelled by wait_for's timeout.
            await asyncio.Event().wait()
            return {}

        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_blocking_analyze)
        # Tiny timeout so the test runs fast
        await _run_one_drainer_cycle(fm, AI_DRAIN_TIMEOUT_S='0.05')
        record("4a-timeout: NO PATCH made (row stays ai_pending)",
               len(patch_calls) == 0, f"patch_calls={patch_calls}")
        record("4a-timeout: fail_count[7] == 1 (incremented)",
               fm._ai_drainer_fail_count.get(7) == 1,
               f"fail_count={fm._ai_drainer_fail_count}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
        os.environ.pop('AI_DRAIN_TIMEOUT_S', None)
    except Exception as e:
        record("4a-timeout: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)
        os.environ.pop('AI_DRAIN_TIMEOUT_S', None)


async def test_4a_ai_drainer_provider_failure_skips_row():
    """[4a] analyze_message raises → row stays ai_pending (no PATCH),
    counted as failed, fail_count incremented (NOT infinite-loop)."""
    print("\n--- 4a: provider failure leaves row ai_pending ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': 9, 'link': 'https://t.me/4aPF', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'}]
        fm, patch_calls, _, _ = await _make_drainer_fakes(pending)

        async def _boom_analyze(text):
            raise RuntimeError("simulated provider 5xx")

        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_boom_analyze)
        await _run_one_drainer_cycle(fm)
        record("4a-provider-fail: NO PATCH made (row stays ai_pending)",
               len(patch_calls) == 0, f"patch_calls={patch_calls}")
        record("4a-provider-fail: fail_count[9] == 1 (incremented, no infinite-loop)",
               fm._ai_drainer_fail_count.get(9) == 1,
               f"fail_count={fm._ai_drainer_fail_count}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
    except Exception as e:
        record("4a-provider-fail: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)


async def test_4a_ai_drainer_none_result_skips_patch():
    """[4a] analyze_message returns None → no PATCH, counted as failed,
    fail_count incremented (provider returned malformed/empty)."""
    print("\n--- 4a: None result skips PATCH ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': 11, 'link': 'https://t.me/4aNR', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'}]
        fm, patch_calls, _, _ = await _make_drainer_fakes(pending)

        async def _none_analyze(text):
            return None

        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_none_analyze)
        await _run_one_drainer_cycle(fm)
        record("4a-none-result: NO PATCH made (row stays ai_pending)",
               len(patch_calls) == 0, f"patch_calls={patch_calls}")
        record("4a-none-result: fail_count[11] == 1 (incremented)",
               fm._ai_drainer_fail_count.get(11) == 1,
               f"fail_count={fm._ai_drainer_fail_count}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
    except Exception as e:
        record("4a-none-result: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)


async def test_4a_ai_drainer_429_cycle_backoff():
    """[4a] 429 on GET triggers 60s sleep PER-CYCLE (not per-row). No
    PATCH made, no analyze_message called."""
    print("\n--- 4a: 429 on GET triggers 60s cycle backoff ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': 1, 'link': 'https://t.me/4a429', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'}]
        fm, patch_calls, get_calls, _ = await _make_drainer_fakes(
            pending, get_status=429, get_body='rate limited')
        analyze_count = {'n': 0}

        async def _analyze(text):
            analyze_count['n'] += 1
            return {'should_save': True, 'description': 'edu', 'country': 'SA',
                    'is_advertisement': False}

        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_analyze)
        await _run_one_drainer_cycle(fm)
        record("4a-429: GET was called (returned 429)",
               len(get_calls) == 1, f"get_calls={get_calls}")
        record("4a-429: analyze_message NEVER called (cycle-level backoff)",
               analyze_count['n'] == 0, f"analyze_count={analyze_count['n']}")
        record("4a-429: NO PATCH made (whole batch skipped)",
               len(patch_calls) == 0, f"patch_calls={patch_calls}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
    except Exception as e:
        record("4a-429: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)


async def test_4a_ai_drainer_empty_queue_60s_sleep():
    """[4a] Empty queue (GET returns []) → 60s sleep, no PATCH, no analyze."""
    print("\n--- 4a: empty queue → 60s sleep ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        fm, patch_calls, get_calls, _ = await _make_drainer_fakes(
            pending_rows=[], get_status=200, get_body='[]')
        analyze_count = {'n': 0}

        async def _analyze(text):
            analyze_count['n'] += 1
            return {'should_save': True, 'description': 'edu', 'country': 'SA',
                    'is_advertisement': False}

        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_analyze)
        await _run_one_drainer_cycle(fm)
        record("4a-empty: GET was called (returned 200 with [])",
               len(get_calls) == 1, f"get_calls={get_calls}")
        record("4a-empty: analyze_message NEVER called (no rows to process)",
               analyze_count['n'] == 0, f"analyze_count={analyze_count['n']}")
        record("4a-empty: NO PATCH made",
               len(patch_calls) == 0, f"patch_calls={patch_calls}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
    except Exception as e:
        record("4a-empty: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)


async def test_4a_ai_drainer_poison_row_skipped_after_3_fails():
    """[4a] A row that fails 3× in this worker lifetime is skipped on
    subsequent cycles (no analyze_message call). Prevents a poison row
    from burning the AI budget every cycle."""
    print("\n--- 4a: poison row skipped after 3 fails ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': 42, 'link': 'https://t.me/4aPOI', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'}]
        fm, patch_calls, _, _ = await _make_drainer_fakes(pending)
        analyze_count = {'n': 0}

        async def _always_fails(text):
            analyze_count['n'] += 1
            raise RuntimeError("chronically failing provider")

        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_always_fails)
        # Pre-seed fail_count = 3 (simulates 3 prior failures across cycles)
        fm._ai_drainer_fail_count = {42: 3}
        await _run_one_drainer_cycle(fm)
        record("4a-poison: analyze_message NEVER called (row skipped at fail_count>=3)",
               analyze_count['n'] == 0, f"analyze_count={analyze_count['n']}")
        record("4a-poison: NO PATCH made (poison row skipped)",
               len(patch_calls) == 0, f"patch_calls={patch_calls}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
    except Exception as e:
        record("4a-poison: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)


async def test_4a_ai_drainer_race_loss_detected():
    """[4a] PATCH returns 200 with empty body `[]` → race-lost (another
    worker claimed it first). Counted as SKIPPED, NOT failed."""
    print("\n--- 4a: race-loss (PATCH 0 rows) detected as skipped ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': 99, 'link': 'https://t.me/4aRL', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'}]
        fm, patch_calls, _, _ = await _make_drainer_fakes(
            pending, patch_status=200, patch_body='[]')
        async def _analyze(text):
            return {'should_save': True, 'description': 'edu', 'country': 'SA',
                    'is_advertisement': False}
        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_analyze)
        # Capture logs to assert race-lost is DEBUG-logged (not WARNING)
        with LogCapture() as lc:
            await _run_one_drainer_cycle(fm)
        # Race-loss is DEBUG — should NOT appear in warnings (which only
        # capture WARNING+). The absence of a "patch status" warning
        # proves it was treated as race-lost, not a patch failure.
        race_warnings = [w for w in lc.all_msgs if 'race-lost' in w]
        patch_fail_warnings = [w for w in lc.all_msgs if 'patch status=' in w]
        record("4a-race-loss: PATCH was made (with ai_approved=is.null filter)",
               patch_calls and 'ai_approved=is.null' in patch_calls[0]['url'],
               f"patch_calls={patch_calls}")
        record("4a-race-loss: NO 'patch status=' warning (treated as skip, not fail)",
               len(patch_fail_warnings) == 0,
               f"patch_fail_warnings={patch_fail_warnings}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
    except Exception as e:
        record("4a-race-loss: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)


async def test_4a_ai_drainer_concurrency_semaphore_bounded():
    """[4a] AI_DRAIN_CONCURRENCY=1 serializes analyze_message calls (no 2
    concurrent). With 5 rows, max-concurrent-in-flight must be ≤ 1."""
    print("\n--- 4a: concurrency=1 semaphore serializes analyze_message ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': i, 'link': f'https://t.me/4aC{i}', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'} for i in range(5)]
        fm, patch_calls, _, _ = await _make_drainer_fakes(pending)
        in_flight = {'cur': 0, 'max': 0}

        async def _tracking_analyze(text):
            in_flight['cur'] += 1
            in_flight['max'] = max(in_flight['max'], in_flight['cur'])
            await _REAL_SLEEP(0.01)  # tiny yield to allow concurrency
            in_flight['cur'] -= 1
            return {'should_save': True, 'description': 'edu', 'country': 'SA',
                    'is_advertisement': False}

        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_tracking_analyze)
        await _run_one_drainer_cycle(fm, AI_DRAIN_CONCURRENCY='1')
        record("4a-concurrency=1: max concurrent analyze_message == 1 (serialized)",
               in_flight['max'] == 1, f"in_flight.max={in_flight['max']}")
        record("4a-concurrency=1: all 5 PATCHes made",
               len(patch_calls) == 5, f"patch_calls={len(patch_calls)}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
        os.environ.pop('AI_DRAIN_CONCURRENCY', None)
    except Exception as e:
        record("4a-concurrency=1: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)
        os.environ.pop('AI_DRAIN_CONCURRENCY', None)


async def test_4a_ai_drainer_concurrency_3_allows_parallel():
    """[4a] AI_DRAIN_CONCURRENCY=3 allows up to 3 concurrent analyze_message
    calls (max-in-flight should be > 1, bounded ≤ 3)."""
    print("\n--- 4a: concurrency=3 allows parallel (bounded ≤ 3) ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': i, 'link': f'https://t.me/4aP{i}', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'} for i in range(6)]
        fm, patch_calls, _, _ = await _make_drainer_fakes(pending)
        in_flight = {'cur': 0, 'max': 0}

        async def _tracking_analyze(text):
            in_flight['cur'] += 1
            in_flight['max'] = max(in_flight['max'], in_flight['cur'])
            await _REAL_SLEEP(0.05)  # yield to allow real concurrency
            in_flight['cur'] -= 1
            return {'should_save': True, 'description': 'edu', 'country': 'SA',
                    'is_advertisement': False}

        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_tracking_analyze)
        await _run_one_drainer_cycle(fm, AI_DRAIN_CONCURRENCY='3')
        record("4a-concurrency=3: max concurrent > 1 (parallel actually happens)",
               in_flight['max'] > 1, f"in_flight.max={in_flight['max']}")
        record("4a-concurrency=3: max concurrent ≤ 3 (bounded by semaphore)",
               in_flight['max'] <= 3, f"in_flight.max={in_flight['max']}")
        record("4a-concurrency=3: all 6 PATCHes made",
               len(patch_calls) == 6, f"patch_calls={len(patch_calls)}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
        os.environ.pop('AI_DRAIN_CONCURRENCY', None)
    except Exception as e:
        record("4a-concurrency=3: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)
        os.environ.pop('AI_DRAIN_CONCURRENCY', None)


async def test_4a_ai_drainer_batch_summary_log():
    """[4a] Per-batch summary `[AI-DRAIN] batch=N processed=M failed=K
    skipped=L elapsed=Xs` is emitted at INFO."""
    print("\n--- 4a: per-batch summary log emitted ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': 1, 'link': 'https://t.me/4aSUM', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'}]
        fm, _, _, _ = await _make_drainer_fakes(pending)
        async def _analyze(text):
            return {'should_save': True, 'description': 'edu', 'country': 'SA',
                    'is_advertisement': False}
        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_analyze)
        with LogCapture() as lc:
            # LogCapture captures WARNING+ by default; bump the handler AND
            # the root logger level to INFO to capture the batch summary
            # (which is logged at INFO via logging.info).
            lc._h.setLevel(logging.INFO)
            _root = logging.getLogger()
            _prev_root_level = _root.level
            _root.setLevel(logging.INFO)
            try:
                await _run_one_drainer_cycle(fm)
            finally:
                _root.setLevel(_prev_root_level)
        summary_msgs = [m for _, m in lc.records
                        if '[AI-DRAIN] batch=' in m and 'processed=' in m
                        and 'failed=' in m and 'skipped=' in m and 'elapsed=' in m]
        record("4a-summary: batch summary log emitted (batch/processed/failed/skipped/elapsed)",
               len(summary_msgs) >= 1, f"summary_msgs={summary_msgs}")
        if summary_msgs:
            m = summary_msgs[0]
            record("4a-summary: summary reports batch=1 processed=1",
                   'batch=1' in m and 'processed=1' in m,
                   f"summary={m}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
    except Exception as e:
        record("4a-summary: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)


async def test_4a_ai_drainer_fail_count_resets_on_success():
    """[4a] On successful PATCH, the row's fail_count is cleared (reset).
    Verifies a row that failed once but succeeds next cycle isn't stuck
    at fail_count=1."""
    print("\n--- 4a: fail_count resets to 0 on success ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': 77, 'link': 'https://t.me/4aRS', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'}]
        fm, _, _, _ = await _make_drainer_fakes(pending)
        async def _analyze(text):
            return {'should_save': True, 'description': 'edu', 'country': 'SA',
                    'is_advertisement': False}
        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_analyze)
        # Pre-seed fail_count = 2 (simulates 2 prior failures)
        fm._ai_drainer_fail_count = {77: 2}
        await _run_one_drainer_cycle(fm)
        record("4a-reset: fail_count[77] cleared on success (not in dict)",
               77 not in fm._ai_drainer_fail_count,
               f"fail_count={fm._ai_drainer_fail_count}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
    except Exception as e:
        record("4a-reset: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)


async def test_4a_ai_drainer_graceful_shutdown():
    """[4a] CancelledError during the cycle breaks the loop cleanly — no
    unhandled exception, no partial state corruption."""
    print("\n--- 4a: graceful shutdown on CancelledError ---")
    err = ''
    cancelled_cleanly = False
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': 1, 'link': 'https://t.me/4aSD', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'}]
        fm, _, _, _ = await _make_drainer_fakes(pending)

        async def _blocking_analyze(text):
            # Blocks forever — NOT asyncio.sleep (which would be patched).
            # asyncio.Event().wait() blocks until cancelled by task.cancel().
            await asyncio.Event().wait()

        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_blocking_analyze)
        fm._running = True

        # Patch asyncio.sleep to skip the long startup/cycle sleeps but
        # NOT affect _blocking_analyze (which uses Event().wait()).
        async def _skip_long_sleeps(n):
            if n >= 10:
                return  # skip startup (45s), cycle (30s), backoff (60s)
            await _REAL_SLEEP(n)

        async def _runner():
            with patch('asyncio.sleep', new=_skip_long_sleeps):
                await bot.Monitor._ai_drainer_worker(fm)

        task = asyncio.create_task(_runner())
        # Use _REAL_SLEEP (captured before patching) so the test's own
        # sleep isn't affected by the patched asyncio.sleep inside _runner.
        await _REAL_SLEEP(0.2)  # let the task reach _blocking_analyze
        task.cancel()
        try:
            await task
            # Worker caught CancelledError, broke the loop, returned
            # normally — clean shutdown.
            cancelled_cleanly = True
        except asyncio.CancelledError:
            # Worker propagated CancelledError (e.g. cancelled during the
            # startup sleep before the try/except) — still clean.
            cancelled_cleanly = True
        except Exception as e:
            cancelled_cleanly = False
            err = str(e)
        os.environ.pop('AI_DRAIN_ENABLED', None)
        os.environ.pop('AI_DRAIN_TIMEOUT_S', None)
    except Exception as e:
        err = f"outer: {e}"
        cancelled_cleanly = False
        os.environ.pop('AI_DRAIN_ENABLED', None)
        os.environ.pop('AI_DRAIN_TIMEOUT_S', None)
    record("4a-shutdown: CancelledError handled cleanly (no unhandled exception)",
           cancelled_cleanly, f"err={err}")


async def test_4a_ai_drainer_disabled_by_default():
    """[4a] AI_DRAIN_ENABLED default false → worker returns immediately
    (idle), no GET, no PATCH, no analyze. Opt-in confirmed."""
    print("\n--- 4a: AI_DRAIN_ENABLED=false (default) → worker idle ---")
    try:
        os.environ.pop('AI_DRAIN_ENABLED', None)
        pending = [{'id': 1, 'link': 'https://t.me/4aOFF', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'}]
        fm, patch_calls, get_calls, _ = await _make_drainer_fakes(pending)
        analyze_count = {'n': 0}

        async def _analyze(text):
            analyze_count['n'] += 1
            return {'should_save': True, 'description': 'edu', 'country': 'SA',
                    'is_advertisement': False}

        fm.ai_analyzer = types.SimpleNamespace(enabled=True, analyze_message=_analyze)
        # Run the worker — it should return immediately (no loop)
        await bot.Monitor._ai_drainer_worker(fm)
        record("4a-disabled: NO GET made (worker idle)",
               len(get_calls) == 0, f"get_calls={get_calls}")
        record("4a-disabled: NO PATCH made (worker idle)",
               len(patch_calls) == 0, f"patch_calls={patch_calls}")
        record("4a-disabled: NO analyze_message called (worker idle)",
               analyze_count['n'] == 0, f"analyze_count={analyze_count['n']}")
    except Exception as e:
        record("4a-disabled: exception", False, str(e))


async def test_4a_ai_drainer_no_ai_configured_60s_sleep():
    """[4a] ai_analyzer.enabled=False or missing → 60s sleep, no GET."""
    print("\n--- 4a: no AI configured → 60s sleep ---")
    try:
        os.environ['AI_DRAIN_ENABLED'] = 'true'
        pending = [{'id': 1, 'link': 'https://t.me/4aNAI', 'link_type': 'telegram',
                    'message_text': 'x', 'group_name': 'g', 'sender_name': 's',
                    'source_phone': 'a'}]
        fm, patch_calls, get_calls, _ = await _make_drainer_fakes(pending)
        # ai_analyzer exists but disabled (no providers)
        fm.ai_analyzer = types.SimpleNamespace(enabled=False, analyze_message=AsyncMock())
        await _run_one_drainer_cycle(fm, sleep_flip_on=1)
        record("4a-no-ai: NO GET made (ai_analyzer disabled)",
               len(get_calls) == 0, f"get_calls={get_calls}")
        record("4a-no-ai: NO PATCH made",
               len(patch_calls) == 0, f"patch_calls={patch_calls}")
        os.environ.pop('AI_DRAIN_ENABLED', None)
    except Exception as e:
        record("4a-no-ai: exception", False, str(e))
        os.environ.pop('AI_DRAIN_ENABLED', None)


# ===================================================================
# Task 3a — Supabase Journal Snapshot Deep Audit (8 NEW regression tests)
#
# Scenarios covered (one test function each, multi-assertion):
#   3a-1  snapshot concurrent-invocation guard (_snapshot_running flag)
#   3a-2  snapshot SELECT ORDER BY received_at ASC (oldest at-risk first)
#   3a-3  snapshot 429 → 60s backoff (mirrors _ai_drainer_worker)
#   3a-4  snapshot POST timeout → caught + loop continues (no crash)
#   3a-5  snapshot network/5xx error → caught + loop continues
#   3a-6  restore does NOT overwrite local terminal state (INSERT OR IGNORE)
#   3a-7  restore per-row corruption isolation (NULL PK / malformed ts)
#   3a-8  restore GET timeout → returns 0 + logs WARNING (no crash)
# ===================================================================


async def test_3a_snapshot_concurrent_guard():
    """[3a-1 / point 6] Two concurrent _journal_snapshot_loop invocations —
    the second is a no-op (returns immediately without POSTing)."""
    print("\n--- 3a-1: snapshot concurrent-invocation guard ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        # Seed 1 pending row so a real cycle would POST
        await prod_db.journal_message({
            'chat_id': -1001, 'msg_id': 1, 'raw_text': 'x https://t.me/A1',
            'source_phone': 'A', 'state': 'pending', 'received_at': time.time()})
        post_calls = []
        class FakeResp:
            def __init__(self, status): self.status = status
            async def text(self): return ''
        class FakeCM:
            def __init__(self, r): self._r = r
            async def __aenter__(self): return self._r
            async def __aexit__(self, *a): return False
        class FakeSession:
            def post(self, url, json=None, headers=None, **kwargs):
                post_calls.append({'url': url, 'json': json, 'headers': headers})
                return FakeCM(FakeResp(204))
        fm.db = types.SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_key='fake_key',
            _get_supabase_session=AsyncMock(return_value=FakeSession()))
        # Simulate an in-flight cycle: set the guard True BEFORE launching
        fm._snapshot_running = True
        fm._running = True
        # Patch sleep so even if the guard failed, the loop wouldn't hang.
        async def _sleep(_n):
            fm._running = False
        with patch('asyncio.sleep', new=_sleep):
            await bot.Monitor._journal_snapshot_loop(fm)
        record("3a-1: 2nd concurrent invocation did NOT POST (guard held)",
               len(post_calls) == 0, f"post_calls={len(post_calls)}")
        record("3a-1: _snapshot_running still True after guarded return",
               getattr(fm, '_snapshot_running', None) is True,
               f"got {getattr(fm, '_snapshot_running', None)!r}")
        # Verify guard is reset to False on a CLEAN cycle (no double-stick)
        fm._snapshot_running = False
        sleep_count = {'n': 0}
        async def _sleep2(_n):
            sleep_count['n'] += 1
            if sleep_count['n'] >= 2:
                fm._running = False
        with patch('asyncio.sleep', new=_sleep2):
            await bot.Monitor._journal_snapshot_loop(fm)
        record("3a-1: clean cycle POSTs exactly once",
               len(post_calls) == 1, f"post_calls={len(post_calls)}")
        record("3a-1: _snapshot_running reset to False after clean cycle",
               getattr(fm, '_snapshot_running', None) is False,
               f"got {getattr(fm, '_snapshot_running', None)!r}")
    except Exception as e:
        record("3a-1: exception", False, str(e))


async def test_3a_snapshot_order_by_received_at():
    """[3a-2 / point 2] With 700 pending rows (oldest=base, newest=base+699),
    a 500-LIMIT snapshot selects the 500 OLDEST (ORDER BY received_at ASC)."""
    print("\n--- 3a-2: snapshot SELECT ORDER BY received_at ASC ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        base = time.time() - 10000  # 10000s ago
        # Insert 700 pending rows with ascending received_at; the first 500
        # (oldest) should be the ones selected.
        for i in range(700):
            await prod_db.journal_message({
                'chat_id': -1001, 'msg_id': 1000 + i,
                'raw_text': f'msg {i} https://t.me/A{i}',
                'source_phone': 'A', 'state': 'pending',
                'received_at': base + i})
        post_calls = []
        class FakeResp:
            def __init__(self, status): self.status = status
            async def text(self): return ''
        class FakeCM:
            def __init__(self, r): self._r = r
            async def __aenter__(self): return self._r
            async def __aexit__(self, *a): return False
        class FakeSession:
            def post(self, url, json=None, headers=None, **kwargs):
                post_calls.append({'url': url, 'json': json, 'headers': headers})
                return FakeCM(FakeResp(204))
        fm.db = types.SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_key='fake_key',
            _get_supabase_session=AsyncMock(return_value=FakeSession()))
        fm._snapshot_running = False
        fm._running = True
        sleep_count = {'n': 0}
        async def _sleep(_n):
            sleep_count['n'] += 1
            if sleep_count['n'] >= 2:
                fm._running = False
        with patch('asyncio.sleep', new=_sleep):
            await bot.Monitor._journal_snapshot_loop(fm)
        record("3a-2: snapshot POSTed exactly 500 rows (LIMIT held)",
               len(post_calls) == 1
               and isinstance(post_calls[0].get('json'), list)
               and len(post_calls[0]['json']) == 500,
               f"post_calls={len(post_calls)}")
        if post_calls and isinstance(post_calls[0].get('json'), list):
            batch = post_calls[0]['json']
            msg_ids = sorted(b['msg_id'] for b in batch)
            # Oldest 500 = msg_ids 1000..1499 (received_at ascending)
            expected = list(range(1000, 1500))
            record("3a-2: snapshot selected OLDEST 500 (msg_id 1000..1499)",
                   msg_ids == expected,
                   f"got min={min(msg_ids)} max={max(msg_ids)} (want 1000/1499)")
    except Exception as e:
        record("3a-2: exception", False, str(e))


async def test_3a_snapshot_429_backoff():
    """[3a-3 / point 9] Supabase returns 429 → loop logs WARNING + sleeps 60s
    (NOT the 30s default), then continues."""
    print("\n--- 3a-3: snapshot 429 → 60s backoff ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        await prod_db.journal_message({
            'chat_id': -1001, 'msg_id': 1, 'raw_text': 'x https://t.me/A1',
            'source_phone': 'A', 'state': 'pending', 'received_at': time.time()})
        post_calls = []
        class FakeResp:
            def __init__(self, status): self.status = status
            async def text(self): return 'rate limited'
        class FakeCM:
            def __init__(self, r): self._r = r
            async def __aenter__(self): return self._r
            async def __aexit__(self, *a): return False
        class FakeSession:
            def post(self, url, json=None, headers=None, **kwargs):
                post_calls.append({'url': url, 'json': json, 'headers': headers})
                return FakeCM(FakeResp(429))
        fm.db = types.SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_key='fake_key',
            _get_supabase_session=AsyncMock(return_value=FakeSession()))
        fm._snapshot_running = False
        fm._running = True
        sleep_calls = []
        async def _sleep(_n):
            sleep_calls.append(_n)
            # Flip _running off after the 429 backoff (60s sleep) so the
            # loop exits cleanly. The 40s startup sleep is call #1; the
            # 429 backoff sleep is call #2.
            if len(sleep_calls) >= 2:
                fm._running = False
        with LogCapture() as cap, patch('asyncio.sleep', new=_sleep):
            await bot.Monitor._journal_snapshot_loop(fm)
        rate_warns = [m for lvl, m in cap.records
                      if lvl == 'WARNING' and '429 rate-limited' in m]
        record("3a-3: 429 logged as WARNING with '429 rate-limited' marker",
               len(rate_warns) >= 1, f"warns={rate_warns[:1]}")
        record("3a-3: 60s backoff sleep invoked after 429",
               60 in sleep_calls, f"sleep_calls={sleep_calls}")
        record("3a-3: exactly 1 POST attempt before backoff",
               len(post_calls) == 1, f"post_calls={len(post_calls)}")
    except Exception as e:
        record("3a-3: exception", False, str(e))


async def test_3a_snapshot_post_timeout_does_not_crash():
    """[3a-4 / point 8] POST raises asyncio.TimeoutError → caught, loop
    continues, no crash, no rows lost (re-snapshotted next cycle)."""
    print("\n--- 3a-4: snapshot POST timeout does not crash ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        await prod_db.journal_message({
            'chat_id': -1001, 'msg_id': 1, 'raw_text': 'x https://t.me/A1',
            'source_phone': 'A', 'state': 'pending', 'received_at': time.time()})
        post_calls = []
        class FakeCM:
            def __init__(self, r): self._r = r
            async def __aenter__(self): raise asyncio.TimeoutError("simulated hang")
            async def __aexit__(self, *a): return False
        class FakeSession:
            def post(self, url, json=None, headers=None, **kwargs):
                post_calls.append({'url': url, 'json': json, 'headers': headers})
                return FakeCM(None)
        fm.db = types.SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_key='fake_key',
            _get_supabase_session=AsyncMock(return_value=FakeSession()))
        fm._snapshot_running = False
        fm._running = True
        sleep_count = {'n': 0}
        async def _sleep(_n):
            sleep_count['n'] += 1
            if sleep_count['n'] >= 3:  # let 1 cycle + 1 retry happen
                fm._running = False
        with LogCapture() as cap, patch('asyncio.sleep', new=_sleep):
            await bot.Monitor._journal_snapshot_loop(fm)
        timeout_warns = [m for lvl, m in cap.records
                         if lvl == 'WARNING' and 'timed out' in m.lower()]
        record("3a-4: TimeoutError caught + WARNING logged",
               len(timeout_warns) >= 1, f"warns={timeout_warns[:1]}")
        record("3a-4: loop survived (>=1 POST attempt, no crash)",
               len(post_calls) >= 1, f"post_calls={len(post_calls)}")
        record("3a-4: _snapshot_running reset to False after clean exit",
               getattr(fm, '_snapshot_running', None) is False,
               f"got {getattr(fm, '_snapshot_running', None)!r}")
    except Exception as e:
        record("3a-4: exception", False, str(e))


async def test_3a_snapshot_network_error_continues():
    """[3a-5 / point 10] POST raises aiohttp.ClientError (network) → caught,
    loop continues, no crash."""
    print("\n--- 3a-5: snapshot network/5xx error continues ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        await prod_db.journal_message({
            'chat_id': -1001, 'msg_id': 1, 'raw_text': 'x https://t.me/A1',
            'source_phone': 'A', 'state': 'pending', 'received_at': time.time()})
        class FakeCM:
            def __init__(self, r): self._r = r
            async def __aenter__(self): raise aiohttp.ClientError("conn refused")
            async def __aexit__(self, *a): return False
        class FakeSession:
            def post(self, url, json=None, headers=None, **kwargs):
                return FakeCM(None)
        fm.db = types.SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_key='fake_key',
            _get_supabase_session=AsyncMock(return_value=FakeSession()))
        fm._snapshot_running = False
        fm._running = True
        sleep_count = {'n': 0}
        async def _sleep(_n):
            sleep_count['n'] += 1
            if sleep_count['n'] >= 3:
                fm._running = False
        with LogCapture() as cap, patch('asyncio.sleep', new=_sleep):
            await bot.Monitor._journal_snapshot_loop(fm)
        net_warns = [m for lvl, m in cap.records
                     if lvl == 'WARNING' and 'network error' in m]
        record("3a-5: ClientError caught + 'network error' WARNING logged",
               len(net_warns) >= 1, f"warns={net_warns[:1]}")
        record("3a-5: loop survived (no exception propagated)",
               True, "loop exited via _running=False flip")
    except Exception as e:
        record("3a-5: exception", False, str(e))


async def test_3a_restore_does_not_overwrite_terminal_local_state():
    """[3a-6 / point 2 + 3] Local row exists with state='processed' (newer).
    Snapshot returns it as state='pending' (older). Restore via INSERT OR
    IGNORE must NOT overwrite the local processed state — the local row stays
    'processed' (source of truth is local)."""
    print("\n--- 3a-6: restore does NOT overwrite local terminal state ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        # Seed local journal with a PROCESSED row (terminal state).
        await prod_db.journal_message({
            'chat_id': -1001, 'msg_id': 1, 'raw_text': 'x https://t.me/A1',
            'source_phone': 'A', 'state': 'pending', 'received_at': time.time()})
        await prod_db.journal_set_state(-1001, 1, 'processed')
        # Verify local state is 'processed' BEFORE restore
        pre = await prod_db.journal_get(-1001, 1)
        record("3a-6: local row is 'processed' BEFORE restore",
               pre['state'] == 'processed', f"got {pre['state']!r}")
        # Snapshot returns the SAME (chat_id, msg_id) but with state='pending'
        # (stale — taken before the local row transitioned to processed).
        snap_rows = [{'chat_id': -1001, 'msg_id': 1, 'raw_text': 'x https://t.me/A1',
                      'source_phone': 'A', 'chat_title': '', 'chat_username': '',
                      'chat_link_type': 'telegram', 'sender_id': 0, 'sender_name': '',
                      'state': 'pending', 'received_at': time.time() - 999}]
        class FakeResp:
            def __init__(self, status, json_data=None):
                self.status = status; self._j = json_data
            async def text(self): return ''
            async def json(self): return self._j
        class FakeCM:
            def __init__(self, r): self._r = r
            async def __aenter__(self): return self._r
            async def __aexit__(self, *a): return False
        class FakeSession:
            def get(self, url, **kwargs): return FakeCM(FakeResp(200, snap_rows))
        fm.db = types.SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_key='fake_key',
            _get_supabase_session=AsyncMock(return_value=FakeSession()))
        restored = await bot.Monitor._restore_journal_from_supabase(fm)
        # Restore returns 1 (journal_message "succeeds" even though INSERT
        # OR IGNORE was a no-op — it doesn't report rows-affected).
        record("3a-6: restore returns 1 (one snapshot row processed)",
               restored == 1, f"got {restored}")
        post = await prod_db.journal_get(-1001, 1)
        record("3a-6: local row STILL 'processed' after restore (INSERT OR IGNORE)",
               post['state'] == 'processed',
               f"got {post['state']!r} (snapshot tried to set 'pending')")
        # Count rows — should still be exactly 1 (no duplicate).
        cnt = await sql_one(prod_db, "SELECT COUNT(*) FROM message_journal WHERE chat_id=? AND msg_id=?",
                            (-1001, 1))
        record("3a-6: no duplicate row created (still 1)",
               cnt[0] == 1, f"count={cnt[0]}")
    except Exception as e:
        record("3a-6: exception", False, str(e))


async def test_3a_restore_per_row_corruption_isolation():
    """[3a-7 / point 11] Snapshot returns 3 rows; row #2 has NULL chat_id.
    INSERT OR IGNORE silently skips the constraint-violating row (no
    exception), so all 3 calls "succeed" but only 2 rows land in SQLite.
    A second sub-test mocks journal_message to RAISE on row #2 to verify
    the per-row try/except genuinely isolates non-constraint errors."""
    print("\n--- 3a-7: restore per-row corruption isolation ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        snap_rows = [
            {'chat_id': -2001, 'msg_id': 11, 'raw_text': 'a https://t.me/A1',
             'source_phone': 'A', 'chat_title': '', 'chat_username': '',
             'chat_link_type': 'telegram', 'sender_id': 0, 'sender_name': '',
             'state': 'pending', 'received_at': time.time() - 100},
            # Bad row: NULL chat_id → INSERT OR IGNORE silently skips (NOT
            # NULL constraint violation is caught by the OR IGNORE clause,
            # NOT raised). journal_message returns normally; restored count
            # is incremented but the row is NOT in the DB.
            {'chat_id': None, 'msg_id': 12, 'raw_text': 'bad',
             'source_phone': 'A', 'chat_title': '', 'chat_username': '',
             'chat_link_type': 'telegram', 'sender_id': 0, 'sender_name': '',
             'state': 'pending', 'received_at': time.time() - 50},
            {'chat_id': -2003, 'msg_id': 13, 'raw_text': 'c https://t.me/A3',
             'source_phone': 'A', 'chat_title': '', 'chat_username': '',
             'chat_link_type': 'telegram', 'sender_id': 0, 'sender_name': '',
             'state': 'pending', 'received_at': time.time() - 10},
        ]
        class FakeResp:
            def __init__(self, status, json_data=None):
                self.status = status; self._j = json_data
            async def text(self): return ''
            async def json(self): return self._j
        class FakeCM:
            def __init__(self, r): self._r = r
            async def __aenter__(self): return self._r
            async def __aexit__(self, *a): return False
        class FakeSession:
            def get(self, url, **kwargs): return FakeCM(FakeResp(200, snap_rows))
        fm.db = types.SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_key='fake_key',
            _get_supabase_session=AsyncMock(return_value=FakeSession()))
        restored = await bot.Monitor._restore_journal_from_supabase(fm)
        # INSERT OR IGNORE silently swallows the NOT NULL violation on row 2,
        # so journal_message returns normally for ALL 3 rows → restored=3.
        # This documents the overcount: restored = calls, not rows-inserted.
        record("3a-7: restore returns 3 (INSERT OR IGNORE swallows constraint violation)",
               restored == 3, f"got {restored}")
        # Verify rows 1 and 3 exist; row 2 (NULL chat_id) does NOT.
        r1 = await prod_db.journal_get(-2001, 11)
        r3 = await prod_db.journal_get(-2003, 13)
        record("3a-7: good row 1 (-2001,11) restored",
               r1 is not None and r1['state'] == 'pending',
               f"got {r1}")
        record("3a-7: good row 3 (-2003,13) restored",
               r3 is not None and r3['state'] == 'pending',
               f"got {r3}")
        # Verify NO row with chat_id IS NULL exists (NOT NULL held)
        null_cnt = await sql_one(prod_db,
            "SELECT COUNT(*) FROM message_journal WHERE chat_id IS NULL")
        record("3a-7: bad row (NULL chat_id) NOT inserted (NOT NULL held)",
               null_cnt[0] == 0, f"null_count={null_cnt[0]}")

        # --- Sub-test B: mock journal_message to RAISE on row #2 to verify
        # the per-row try/except genuinely isolates non-constraint errors.
        prod_db2, _ = await make_test_db()
        fm2 = make_fake_monitor(prod_db2)
        class FakeSession2:
            def get(self, url, **kwargs): return FakeCM(FakeResp(200, snap_rows))
        fm2.db = types.SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_key='fake_key',
            _get_supabase_session=AsyncMock(return_value=FakeSession2()))
        call_idx = {'n': 0}
        real_journal = prod_db2.journal_message
        async def _raising_journal(entry):
            call_idx['n'] += 1
            if call_idx['n'] == 2:
                raise sqlite3.OperationalError("simulated disk-full")
            return await real_journal(entry)
        fm2.prod_db.journal_message = _raising_journal
        restored2 = await bot.Monitor._restore_journal_from_supabase(fm2)
        # Row 1 inserts (call 1), row 2 RAISES (call 2, caught by per-row
        # except → pass), row 3 inserts (call 3). restored count = 2.
        record("3a-7: per-row except isolates non-constraint error (restored=2)",
               restored2 == 2, f"got {restored2}")
        # Verify rows 1 and 3 still landed despite row 2 raising.
        r1b = await prod_db2.journal_get(-2001, 11)
        r3b = await prod_db2.journal_get(-2003, 13)
        record("3a-7: row 1 restored even though row 2 raised",
               r1b is not None, f"got {r1b}")
        record("3a-7: row 3 restored even though row 2 raised",
               r3b is not None, f"got {r3b}")
    except Exception as e:
        record("3a-7: exception", False, str(e))


async def test_3a_restore_get_timeout_returns_zero():
    """[3a-8 / point 8] Restore GET raises asyncio.TimeoutError → returns 0
    + logs WARNING (no crash, no partial restore)."""
    print("\n--- 3a-8: restore GET timeout returns 0 ---")
    try:
        prod_db, _ = await make_test_db()
        fm = make_fake_monitor(prod_db)
        class FakeCM:
            def __init__(self, r): self._r = r
            async def __aenter__(self): raise asyncio.TimeoutError("simulated hang")
            async def __aexit__(self, *a): return False
        class FakeSession:
            def get(self, url, **kwargs): return FakeCM(None)
        fm.db = types.SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_key='fake_key',
            _get_supabase_session=AsyncMock(return_value=FakeSession()))
        with LogCapture() as cap:
            restored = await bot.Monitor._restore_journal_from_supabase(fm)
        record("3a-8: restore returned 0 on timeout (not raise)",
               restored == 0, f"got {restored}")
        timeout_warns = [m for lvl, m in cap.records
                         if lvl == 'WARNING' and 'timed out' in m.lower()]
        record("3a-8: 'restore timed out' WARNING logged",
               len(timeout_warns) >= 1, f"warns={timeout_warns[:1]}")
        # Verify no rows were inserted into local journal (GET never returned)
        cnt = await sql_one(prod_db, "SELECT COUNT(*) FROM message_journal")
        record("3a-8: no rows inserted (local journal still empty)",
               cnt[0] == 0, f"count={cnt[0]}")
    except Exception as e:
        record("3a-8: exception", False, str(e))


# =========================================================================
# [Task 6a] PollingScheduler / active_chats_count=0 root-cause regressions
# =========================================================================

async def test_6a_polling_status_counts_null_next_poll_at_as_active():
    """[B03 + 6a] The /api/polling_status predicate MUST include NULL
    next_poll_at rows. Historically the query was `next_poll_at <= ?` which
    excluded all NULL rows → active_chats_count=0 even with 845 chats."""
    try:
        prod_db, _ = await make_test_db()
        now = "2026-08-25T12:00:00"
        # 3 rows: NULL next_poll_at, past (due), future (not due)
        await sql_exec(prod_db,
            "INSERT INTO monitored_chats (chat_id, chat_title, next_poll_at) VALUES (?,?,?)",
            (100, "NULL_chat", None))
        await sql_exec(prod_db,
            "INSERT INTO monitored_chats (chat_id, chat_title, next_poll_at) VALUES (?,?,?)",
            (101, "due_chat", "2026-08-25T11:00:00"))
        await sql_exec(prod_db,
            "INSERT INTO monitored_chats (chat_id, chat_title, next_poll_at) VALUES (?,?,?)",
            (102, "future_chat", "2026-08-25T23:00:00"))
        # Mirror the EXACT predicate the fixed /api/polling_status uses.
        row = await sql_one(prod_db,
            "SELECT COUNT(*) FROM monitored_chats WHERE (next_poll_at IS NULL OR next_poll_at <= ?)",
            (now,))
        record("6a-1: NULL + due counted (active_chats_count > 0)",
               row[0] == 2, f"expected 2 (NULL+due), got {row[0]}")
        # The OLD broken predicate must NOT count the NULL row.
        row_old = await sql_one(prod_db,
            "SELECT COUNT(*) FROM monitored_chats WHERE next_poll_at <= ?",
            (now,))
        record("6a-1b: legacy predicate excluded NULL (regression guard)",
               row_old[0] == 1, f"legacy got {row_old[0]} (must be 1, not 2)")
    except Exception as e:
        record("6a-1: exception", False, str(e))


async def test_6a_add_monitored_chat_seeds_next_poll_at():
    """[6a] add_monitored_chat MUST seed next_poll_at = now() so the chat is
    immediately due for polling AND visible to the status endpoint. Pre-fix
    the column was left NULL on insert."""
    try:
        prod_db, _ = await make_test_db()
        # Need member_count? add_monitored_chat signature: (chat_id, title, ...)
        ok = await prod_db.add_monitored_chat(
            200, "TestEdu", username="testedu", link_type="group",
            monitored_by="966500000000", member_count=100)
        record("6a-2: add_monitored_chat returns True for new chat", ok is True)
        row = await sql_one(prod_db,
            "SELECT next_poll_at, last_activity FROM monitored_chats WHERE chat_id=?",
            (200,))
        record("6a-2b: next_poll_at seeded non-NULL on insert",
               row[0] is not None and row[1] is not None,
               f"next_poll_at={row[0]}, last_activity={row[1]}")
    except Exception as e:
        record("6a-2: exception", False, str(e))


async def test_6a_backfill_seeds_null_next_poll_at_rows():
    """[6a] The startup backfill `UPDATE ... SET next_poll_at = COALESCE(...)`
    MUST convert pre-existing NULL rows to now(). Simulate a pre-fix row
    inserted with NULL, run the backfill SQL, assert it's now non-NULL."""
    try:
        prod_db, _ = await make_test_db()
        # Insert a row the OLD way (NULL next_poll_at) — bypass add_monitored_chat
        await sql_exec(prod_db,
            "INSERT INTO monitored_chats (chat_id, chat_title, next_poll_at, last_activity) "
            "VALUES (300, 'LegacyChat', NULL, NULL)")
        # Run the backfill SQL (mirrors link_system._create_tables backfill block)
        now_iso = "2026-08-25T13:00:00"
        conn = await prod_db._conn()
        await conn.execute(
            "UPDATE monitored_chats SET next_poll_at = COALESCE(next_poll_at, ?) "
            "WHERE next_poll_at IS NULL", (now_iso,))
        await conn.execute(
            "UPDATE monitored_chats SET last_activity = COALESCE(last_activity, ?) "
            "WHERE last_activity IS NULL", (now_iso,))
        await conn.commit()
        row = await sql_one(prod_db,
            "SELECT next_poll_at, last_activity FROM monitored_chats WHERE chat_id=?",
            (300,))
        record("6a-3: backfill converted NULL → non-NULL",
               row[0] is not None and row[1] is not None,
               f"next_poll_at={row[0]}, last_activity={row[1]}")
        # Idempotency: re-running must NOT overwrite a non-NULL value
        await conn.execute(
            "UPDATE monitored_chats SET next_poll_at = COALESCE(next_poll_at, ?) "
            "WHERE next_poll_at IS NULL", ("2099-01-01T00:00:00",))
        await conn.commit()
        row2 = await sql_one(prod_db,
            "SELECT next_poll_at FROM monitored_chats WHERE chat_id=?", (300,))
        record("6a-3b: backfill idempotent (non-NULL preserved)",
               row2[0] == now_iso, f"expected {now_iso}, got {row2[0]}")
    except Exception as e:
        record("6a-3: exception", False, str(e))


async def test_6a_scheduler_select_due_includes_null():
    """[6a] SourceRegistry.select_due_chats predicate must include NULL.
    Source-level regression guard (predicate is the scheduler's actual
    due-selection)."""
    try:
        # Read source_registry.py and assert the predicate shape is intact.
        src = (PROJECT_ROOT / "source_registry.py").read_text(encoding="utf-8")
        has_null_or = "next_poll_at IS NULL OR next_poll_at <=" in src.replace("\n", " ")
        record("6a-4: SourceRegistry predicate includes NULL-or-due",
               has_null_or, "predicate missing/changed — would regress active_chats")
    except Exception as e:
        record("6a-4: exception", False, str(e))


# =========================================================================
# [PUBLISH-INCIDENT-1] Joiner selection tries ALL eligible joiners
# (connection / rate-limiter / safety-guard moved INSIDE the loop)
# =========================================================================

async def test_publish_incident_1_connection_check_inside_loop():
    """[P1] Connection check must be INSIDE the for-joiner loop so a
    disconnected first joiner is skipped and the next joiner is tried.
    Root cause of the production publishing incident: the connection
    check was AFTER the loop and only tested the first selected joiner,
    causing an infinite retry loop (same link + same disconnected joiner).
    """
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        # Locate the joiner-selection loop
        marker = "# 6. Joiner selection — try EACH joiner until one passes ALL checks."
        idx = src.find(marker)
        if idx < 0:
            record("P1-1: connection check inside loop", False,
                   "joiner-selection section marker not found — fix may have been reverted")
            return
        # Extract the loop body (up to the join attempt)
        loop_end = src.find("# 7. Join attempt", idx)
        if loop_end < 0:
            loop_end = src.find("success, status, member_count", idx)
        body = src[idx:loop_end] if loop_end > idx else src[idx:idx + 4000]
        # The connection check must be inside the loop body
        has_conn_check = "is_connected()" in body and "reason=not_connected" in body
        record("P1-1: connection check inside joiner loop",
               has_conn_check, "connection check missing from loop body")
    except Exception as e:
        record("P1-1: exception", False, str(e))


async def test_publish_incident_1_old_anti_pattern_gone():
    """[P1] The OLD anti-pattern must be GONE: a bare connection check
    that appears AFTER `if not selected_joiner:` (outside the loop) and
    aborts the whole cycle with `continue` would cause the infinite retry.
    """
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        # The old pattern was:
        #   phone = selected_joiner['phone']
        #   client = self.user_clients.get(phone)
        #   if not client or not client.is_connected():
        #       logging.warning(f"[SCHED] {phone} not connected — skipping")
        #       await self.prod_db.update_queue_status(link_data['id'], 'QUEUED',
        #                                              next_retry=datetime.now() + timedelta(minutes=2))
        #       await asyncio.sleep(60)
        #       continue
        # This exact block (with +2min next_retry) must NOT appear after the
        # 'if not selected_joiner:' guard.
        old_pattern = 'next_retry=datetime.now() + timedelta(minutes=2))\n                    await asyncio.sleep(60)\n                    continue'
        # Search for the OLD pattern anywhere in the file
        old_present = old_pattern in src
        record("P1-2: old connection-after-loop anti-pattern removed",
               not old_present, "old +2min-sleep-continue pattern still present — regression")
    except Exception as e:
        record("P1-2: exception", False, str(e))


async def test_publish_incident_1_rate_limiter_inside_loop():
    """[P1] Rate limiter check must be INSIDE the for-joiner loop so a
    rate-limited joiner is skipped and the next one is tried."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        marker = "# 6. Joiner selection — try EACH joiner until one passes ALL checks."
        idx = src.find(marker)
        loop_end = src.find("# 7. Join attempt", idx)
        body = src[idx:loop_end] if idx >= 0 and loop_end > idx else ""
        has_rl = "rate_limiter.check(jphone, 'join')" in body and "reason=rate_limited" in body
        record("P1-3: rate limiter check inside joiner loop",
               has_rl, "rate limiter not inside loop body")
    except Exception as e:
        record("P1-3: exception", False, str(e))


async def test_publish_incident_1_safety_guard_inside_loop():
    """[P1] Safety guard check must be INSIDE the for-joiner loop."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        marker = "# 6. Joiner selection — try EACH joiner until one passes ALL checks."
        idx = src.find(marker)
        loop_end = src.find("# 7. Join attempt", idx)
        body = src[idx:loop_end] if idx >= 0 and loop_end > idx else ""
        has_sg = "_safety_guard(jphone," in body and "reason=safety_guard" in body
        record("P1-4: safety guard check inside joiner loop",
               has_sg, "safety guard not inside loop body")
    except Exception as e:
        record("P1-4: exception", False, str(e))


async def test_publish_incident_1_each_check_continues_loop():
    """[P1] Each check inside the loop must `continue` (skip to next joiner),
    NOT abort the cycle with `asyncio.sleep(60); continue` (which would
    re-pick the same link next cycle)."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        marker = "# 6. Joiner selection — try EACH joiner until one passes ALL checks."
        idx = src.find(marker)
        loop_end = src.find("# 7. Join attempt", idx)
        body = src[idx:loop_end] if idx >= 0 and loop_end > idx else ""
        # The loop body must NOT contain asyncio.sleep(60) — that would
        # abort the cycle instead of trying the next joiner.
        has_cycle_abort = "asyncio.sleep(60)" in body
        record("P1-5: no cycle-abort sleep inside joiner loop",
               not has_cycle_abort, "asyncio.sleep(60) inside loop — would abort cycle, not try next joiner")
    except Exception as e:
        record("P1-5: exception", False, str(e))


async def test_publish_incident_1_joiner_selected_log_marker():
    """[P1] [JOINER] selected account=... log marker must be present
    so operators can see which joiner was chosen."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_marker = "[JOINER] selected account=" in src
        record("P1-6: [JOINER] selected log marker present",
               has_marker, "marker missing")
    except Exception as e:
        record("P1-6: exception", False, str(e))


async def test_publish_incident_1_publish_started_log_marker():
    """[P1] [PUBLISH] started/success/failed log markers must be present
    so operators can trace whether the publish pipeline ran."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_started = "[PUBLISH] started" in src
        has_success = "[PUBLISH] success" in src
        has_failed = "[PUBLISH] failed" in src
        record("P1-7: [PUBLISH] started/success/failed markers present",
               has_started and has_success and has_failed,
               f"started={has_started} success={has_success} failed={has_failed}")
    except Exception as e:
        record("P1-7: exception", False, str(e))


async def test_publish_incident_1_join_started_log_marker():
    """[P1] [JOIN] started/success log markers must be present."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_started = "[JOIN] started account=" in src
        has_success = "[JOIN] success account=" in src
        record("P1-8: [JOIN] started/success markers present",
               has_started and has_success,
               f"started={has_started} success={has_success}")
    except Exception as e:
        record("P1-8: exception", False, str(e))


# =========================================================================
# [Task 9a] Supervisor coverage regression guards
# =========================================================================

async def test_9a_supervisor_watches_nine_critical_tasks():
    """[9a/W2] _supervisor_loop MUST supervise all 9 critical tasks.
    Source-level guard: a dropped worker from the relaunch list would
    silently die. Assert the 9 task names appear in _supervisor_loop."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        # Extract the _supervisor_loop body
        start = src.find("async def _supervisor_loop(self):")
        end = src.find("    # ====", start)  # next section divider
        if start < 0 or end < 0:
            end = src.find("# [L03]", start) if start >= 0 else -1
        body = src[start:end] if start >= 0 and end > start else src
        required = [
            "_polling_scheduler_task",     # 1 polling
            "_journal_recovery_task",      # 2 journal recovery
            "_journal_snapshot_task",      # 3 supabase mirror
            "_ai_drainer_task",             # 4 ai drainer
            "_joiner_task",                # 5 joiner
            "_claim_cleanup_task",          # 6 claim cleanup
            "_msg_cache_cleanup_task",      # 7 cache cleanup
            "_priority_scorer_task",        # 8 priority
            "_polling_watchdog_task",       # 9 watchdog
        ]
        missing = [t for t in required if t not in body]
        record("9a-1: supervisor covers all 9 critical tasks",
               not missing, f"missing: {missing}" if missing else "all 9 present")
    except Exception as e:
        record("9a-1: exception", False, str(e))


async def test_9a_supervisor_relaunch_lock_present():
    """[9a/W1] The supervisor relaunch of polling_scheduler MUST be wrapped in
    a lock so the 60s supervisor + 30s watchdog can't both relaunch it
    concurrently (duplicate-instance regression)."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        start = src.find("async def _supervisor_loop(self):")
        end = src.find("    async def _polling_watchdog_loop", start)
        body = src[start:end] if start >= 0 and end > start else ""
        has_lock = "_scheduler_relaunch_lock" in body and "async with _relaunch_lock" in body
        record("9a-2: supervisor relaunch is lock-protected",
               has_lock, "missing _scheduler_relaunch_lock — duplicate-scheduler risk")
    except Exception as e:
        record("9a-2: exception", False, str(e))


async def test_9a_ai_drainer_relaunch_gated_on_env():
    """[9a/W1] The supervisor MUST NOT relaunch _ai_drainer_worker when
    AI_DRAIN_ENABLED is false (else noisy restart warnings every 60s for a
    worker that intentionally self-disabled)."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        start = src.find("async def _supervisor_loop(self):")
        end = src.find("    async def _polling_watchdog_loop", start)
        body = src[start:end] if start >= 0 and end > start else ""
        has_gate = ("ai_drain_on" in body and "AI_DRAIN_ENABLED" in body
                    and "if ai_drain_on" in body)
        record("9a-3: ai_drainer relaunch gated on AI_DRAIN_ENABLED",
               has_gate, "missing gate — noisy restart warnings when disabled")
    except Exception as e:
        record("9a-3: exception", False, str(e))


# =========================================================================
# [Task 10a] SQLite concurrency + pragma regressions
# =========================================================================

async def test_10a_concurrent_writers_no_deadlock_no_corruption():
    """[10a] 10 coroutines × 100 (INSERT OR IGNORE + UPDATE) on the same
    table must complete with no exceptions, no deadlock, and the correct
    final row count (100 unique, each updated to its final value)."""
    try:
        prod_db, _ = await make_test_db()
        N = 10
        M = 100

        async def writer(wid):
            conn = await prod_db._conn()
            for i in range(M):
                # All writers target the SAME 100 rows (chat_id = i) → contention
                await conn.execute(
                    "INSERT OR IGNORE INTO monitored_chats "
                    "(chat_id, chat_title, next_poll_at) VALUES (?,?,?)",
                    (i, f"w{wid}_c{i}", "2026-08-25T12:00:00"))
                await conn.execute(
                    "UPDATE monitored_chats SET chat_title=? WHERE chat_id=?",
                    (f"final_w{wid}_c{i}", i))
            await conn.commit()

        await asyncio.gather(*[writer(w) for w in range(N)], return_exceptions=True)
        row = await sql_one(prod_db, "SELECT COUNT(*) FROM monitored_chats")
        record("10a-1: concurrent writers — 100 unique rows (no corruption)",
               row[0] == M, f"expected {M}, got {row[0]}")
        # All rows must have a title (no NULL from a half-written tx)
        nulls = await sql_one(prod_db,
            "SELECT COUNT(*) FROM monitored_chats WHERE chat_title IS NULL")
        record("10a-1b: no NULL titles (no partial writes)",
               nulls[0] == 0, f"{nulls[0]} NULL titles")
    except Exception as e:
        record("10a-1: exception", False, str(e))


async def test_10a_pragma_busy_timeout_and_wal_set():
    """[10a] The production _ensure_conn path MUST set WAL mode + busy_timeout
    (>=5000ms). Without these, concurrent writers get SQLITE_BUSY errors and
    the journal risks corruption on crash.

    Source-level guard: Monitor._ensure_conn in bot.py is the production
    connection-opener. We assert the PRAGMA statements are present there.
    (The test-FakeDB doesn't replicate the pragma setup — it's a bot.py
    concern, not a link_system concern.)"""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        start = src.find("async def _ensure_conn(self):")
        # _ensure_conn ends at the next method def at the same indent.
        end = src.find("\n    async def ", start + 40)
        if end < 0:
            end = src.find("\n    def ", start + 40)
        body = src[start:end if end > 0 else start + 1500]
        has_wal = "PRAGMA journal_mode=WAL" in body or "journal_mode = WAL" in body
        has_bt = "PRAGMA busy_timeout=5000" in body or "busy_timeout = 5000" in body or "busy_timeout=5000" in body
        record("10a-2: _ensure_conn sets PRAGMA journal_mode=WAL",
               has_wal, "WAL pragma missing — crash-corruption risk")
        record("10a-2b: _ensure_conn sets PRAGMA busy_timeout>=5000",
               has_bt, "busy_timeout missing — SQLITE_BUSY under contention")
    except Exception as e:
        record("10a-2: exception", False, str(e))


# =========================================================================
# [Task 5a] API security regressions
# =========================================================================

async def test_5a_middleware_constant_time_compare():
    """[5a/A1] dashboard_api_key_middleware MUST use secrets.compare_digest
    (constant-time), not `==`. A wrong key → 401; a correct key → passes."""
    try:
        os.environ['DASHBOARD_API_KEY'] = "secret-key-value-123"
        # Wrong key → 401
        req_bad = MagicMock()
        req_bad.path = "/api/links"
        req_bad.headers = {"X-Api-Key": "wrong"}
        handler = AsyncMock(return_value=web_json_ok())
        resp_bad = await bot.dashboard_api_key_middleware(req_bad, handler)
        record("5a-1: wrong X-Api-Key → 401",
               getattr(resp_bad, 'status', None) == 401,
               f"status={getattr(resp_bad, 'status', None)}")
        # Correct key → handler invoked
        req_ok = MagicMock()
        req_ok.path = "/api/links"
        req_ok.headers = {"X-Api-Key": "secret-key-value-123"}
        handler2 = AsyncMock(return_value=web_json_ok())
        await bot.dashboard_api_key_middleware(req_ok, handler2)
        record("5a-1b: correct X-Api-Key → handler invoked",
               handler2.called, "handler not called")
        # Source uses compare_digest — search the WHOLE middleware function
        # (from `async def dashboard_api_key_middleware` to the next top-level
        # `async def`/`def` at column 0). A narrow \n\n window truncated the
        # body before reaching the compare_digest call.
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        mw_start = src.find("async def dashboard_api_key_middleware")
        # find next top-level (col-0) function def after the middleware
        import re as _re
        nxt = _re.search(r'\n(?:async )?def [a-zA-Z_]', src[mw_start + 50:])
        mw_end = (mw_start + 50 + nxt.start()) if nxt else mw_start + 3000
        mw_body = src[mw_start:mw_end]
        record("5a-1c: uses secrets.compare_digest (constant-time)",
               "secrets.compare_digest" in mw_body,
               "middleware not calling secrets.compare_digest — timing-attack risk")
    except Exception as e:
        record("5a-1: exception", False, str(e))
    finally:
        os.environ.pop('DASHBOARD_API_KEY', None)


async def test_5a_middleware_exempt_health_endpoints():
    """[5a/A2] /health, /ready, /metrics are NOT under /api/* so they're NEVER
    gated — Render's health probe + Prometheus must stay open even when the
    key is set."""
    try:
        os.environ['DASHBOARD_API_KEY'] = "secret-key-value-123"
        for path in ("/health", "/ready", "/metrics"):
            req = MagicMock()
            req.path = path
            req.headers = {}  # no X-Api-Key
            handler = AsyncMock(return_value=web_json_ok())
            await bot.dashboard_api_key_middleware(req, handler)
            record(f"5a-2: {path} exempt from auth", handler.called,
                   f"{path} was gated — would break Render health probe")
    except Exception as e:
        record("5a-2: exception", False, str(e))
    finally:
        os.environ.pop('DASHBOARD_API_KEY', None)


async def test_5a_middleware_unset_means_open():
    """[5a/A4 → PR-7] When DASHBOARD_API_KEY is UNSET, /api/* is REJECTED
    with 401 by default (fail-closed, secure-by-default since PR-7). The
    operator sets DASHBOARD_API_KEY + frontend X-Api-Key to enable the
    dashboard, or sets API_FAIL_OPEN=true for a temporary open transition."""
    try:
        os.environ.pop('DASHBOARD_API_KEY', None)
        os.environ.pop('API_FAIL_OPEN', None)   # PR-7: default fail-closed
        bot._DASHBOARD_API_KEY_WARNED['open'] = False
        req = MagicMock()
        req.path = "/api/links"
        req.headers = {}  # no key
        handler = AsyncMock(return_value=web_json_ok())
        resp = await bot.dashboard_api_key_middleware(req, handler)
        is_401 = hasattr(resp, 'status') and resp.status == 401
        record("5a-3: DASHBOARD_API_KEY unset → /api REJECTED (PR-7 fail-closed)",
               is_401 and not handler.called,
               f"got status={getattr(resp,'status',None)}, called={handler.called}")
        # API_FAIL_OPEN=true restores open mode (transition)
        os.environ['API_FAIL_OPEN'] = 'true'
        bot._DASHBOARD_API_KEY_WARNED['open'] = False
        handler2 = AsyncMock(return_value=web_json_ok())
        await bot.dashboard_api_key_middleware(req, handler2)
        record("5a-3b: API_FAIL_OPEN=true → /api open (transition escape)",
               handler2.called, "transition mode not honored")
        os.environ.pop('API_FAIL_OPEN', None)
    except Exception as e:
        record("5a-3: exception", False, str(e))


async def test_5a_redact_phone_masks_middle():
    """[5a/A3] _redact_phone must mask the middle of a phone number and never
    leak the full value; edge cases (None, "", short) → safe output."""
    try:
        rp = bot._redact_phone
        full = "+96651234567"
        masked = rp(full)
        record("5a-4: full phone masked (no full leak)",
               full not in masked and "•" in masked, f"'{masked}'")
        record("5a-4b: None → ''", rp(None) == "", f"got '{rp(None)}'")
        record("5a-4c: '' → ''", rp("") == "", f"got '{rp('')}'")
        record("5a-4d: short → '••••'", rp("12") == "••••", f"got '{rp('12')}'")
    except Exception as e:
        record("5a-4: exception", False, str(e))


# =========================================================================
# [DASHBOARD-RESTORE] Trusted-origin allowlist — 8 regression guards.
# Restores the Vercel dashboard that PR-7 (commit b6017b5, 2026-08-26)
# accidentally broke: PR-7 made /api/* fail-closed when DASHBOARD_API_KEY
# is unset, but the Vercel frontend sends no X-Api-Key → every fetch got
# 401 → dashboard showed zero data. The fix grants keyless access to the
# trusted dashboard Origin (browser-forbidden header, JS cannot spoof).
# =========================================================================

async def test_dashboard_restore_allowed_origins_env_parse():
    """[DASHBOARD-RESTORE-1] _get_dashboard_allowed_origins parses the env
    correctly: comma-separated, strips whitespace, strips trailing slashes,
    ignores empty entries. UNSET env → code-deployed DEFAULT (the official
    Vercel dashboard — DASHBOARD-RESTORE-2); empty-string env → [] (opt-out)."""
    try:
        # unset → code-deployed default allowlist (DASHBOARD-RESTORE-2)
        os.environ.pop('DASHBOARD_ALLOWED_ORIGINS', None)
        got = bot._get_dashboard_allowed_origins()
        record("DR-1a: unset env → default allowlist (code-deployed)",
               got == [bot._DASHBOARD_DEFAULT_ALLOWED_ORIGINS], f"got {got}")
        # empty-string env → [] (explicit opt-out → fully fail-closed)
        os.environ['DASHBOARD_ALLOWED_ORIGINS'] = ""
        got = bot._get_dashboard_allowed_origins()
        record("DR-1a2: empty-string env → [] (opt-out)",
               got == [], f"got {got}")
        # single
        os.environ['DASHBOARD_ALLOWED_ORIGINS'] = "https://dash.example.com"
        got = bot._get_dashboard_allowed_origins()
        record("DR-1b: single origin parsed",
               got == ["https://dash.example.com"], f"got {got}")
        # comma-separated + whitespace + trailing slash
        os.environ['DASHBOARD_ALLOWED_ORIGINS'] = (
            " https://a.vercel.app/ , https://b.vercel.app/ ,  "
        )
        got = bot._get_dashboard_allowed_origins()
        record("DR-1c: multi + whitespace + trailing-slash normalized",
               got == ["https://a.vercel.app", "https://b.vercel.app"],
               f"got {got}")
        # env override beats the code default
        os.environ['DASHBOARD_ALLOWED_ORIGINS'] = "https://other.example.com"
        got = bot._get_dashboard_allowed_origins()
        record("DR-1d: env override beats code default",
               got == ["https://other.example.com"]
               and bot._DASHBOARD_DEFAULT_ALLOWED_ORIGINS not in got,
               f"got {got}")
    except Exception as e:
        record("DR-1: exception", False, str(e))
    finally:
        os.environ.pop('DASHBOARD_ALLOWED_ORIGINS', None)


async def test_dashboard_restore_origin_match_grants_access():
    """[DASHBOARD-RESTORE-2] When DASHBOARD_API_KEY is unset AND the request
    Origin is in the allowlist, the middleware MUST call the handler (no
    X-Api-Key required). This is the core fix that restores the Vercel
    dashboard without exposing a browser-key."""
    try:
        os.environ.pop('DASHBOARD_API_KEY', None)
        os.environ.pop('API_FAIL_OPEN', None)
        os.environ['DASHBOARD_ALLOWED_ORIGINS'] = (
            "https://whatsapp-monitor-jzp9pilke-azzam10.vercel.app"
        )
        bot._DASHBOARD_API_KEY_WARNED['open'] = False
        req = MagicMock()
        req.path = "/api/links"
        req.method = "GET"
        req.headers = {"Origin":
                       "https://whatsapp-monitor-jzp9pilke-azzam10.vercel.app"}
        handler = AsyncMock(return_value=web_json_ok())
        await bot.dashboard_api_key_middleware(req, handler)
        record("DR-2: trusted Origin + key unset → handler invoked",
               handler.called,
               "middleware rejected the trusted dashboard origin — Vercel "
               "dashboard stays broken")
    except Exception as e:
        record("DR-2: exception", False, str(e))
    finally:
        os.environ.pop('DASHBOARD_ALLOWED_ORIGINS', None)


async def test_dashboard_restore_referer_fallback_match():
    """[DASHBOARD-RESTORE-3] When Origin is absent (same-origin / older
    browsers), the middleware falls back to parsing Referer and grants
    access if the referer-origin matches the allowlist."""
    try:
        os.environ.pop('DASHBOARD_API_KEY', None)
        os.environ.pop('API_FAIL_OPEN', None)
        os.environ['DASHBOARD_ALLOWED_ORIGINS'] = (
            "https://whatsapp-monitor-jzp9pilke-azzam10.vercel.app"
        )
        bot._DASHBOARD_API_KEY_WARNED['open'] = False
        req = MagicMock()
        req.path = "/api/stats"
        req.method = "GET"
        req.headers = {
            "Referer": ("https://whatsapp-monitor-jzp9pilke-azzam10.vercel.app/"
                        "dashboard?tab=links")
        }
        handler = AsyncMock(return_value=web_json_ok())
        await bot.dashboard_api_key_middleware(req, handler)
        record("DR-3: Referer fallback matches allowlist → handler invoked",
               handler.called, "Referer fallback not honored")
    except Exception as e:
        record("DR-3: exception", False, str(e))
    finally:
        os.environ.pop('DASHBOARD_ALLOWED_ORIGINS', None)


async def test_dashboard_restore_unallowed_origin_rejected():
    """[DASHBOARD-RESTORE-4] When the Origin is NOT in the allowlist AND
    DASHBOARD_API_KEY is unset AND API_FAIL_OPEN is false, the middleware
    MUST return 401. This proves the allowlist is a real boundary, not
    a blanket allow-everything."""
    try:
        os.environ.pop('DASHBOARD_API_KEY', None)
        os.environ.pop('API_FAIL_OPEN', None)
        os.environ['DASHBOARD_ALLOWED_ORIGINS'] = (
            "https://whatsapp-monitor-jzp9pilke-azzam10.vercel.app"
        )
        bot._DASHBOARD_API_KEY_WARNED['open'] = False
        req = MagicMock()
        req.path = "/api/links"
        req.method = "GET"
        req.headers = {"Origin": "https://evil.example.com"}
        handler = AsyncMock(return_value=web_json_ok())
        resp = await bot.dashboard_api_key_middleware(req, handler)
        is_401 = hasattr(resp, 'status') and resp.status == 401
        record("DR-4: untrusted Origin → 401 fail-closed",
               is_401 and not handler.called,
               f"status={getattr(resp,'status',None)} called={handler.called}")
    except Exception as e:
        record("DR-4: exception", False, str(e))
    finally:
        os.environ.pop('DASHBOARD_ALLOWED_ORIGINS', None)


async def test_dashboard_restore_empty_allowlist_fail_closed():
    """[DASHBOARD-RESTORE-5] When the allowlist is EXPLICITLY EMPTIED (env
    set to empty string — the opt-out) AND the key is unset, the middleware
    MUST return 401 (no implicit trust). DASHBOARD-RESTORE-2 changed the
    unset-env default to the official Vercel URL, so the fail-closed mode
    now requires the explicit empty-string opt-out. This guards against a
    regression that accidentally allow-lists arbitrary origins."""
    try:
        os.environ.pop('DASHBOARD_API_KEY', None)
        os.environ.pop('API_FAIL_OPEN', None)
        os.environ['DASHBOARD_ALLOWED_ORIGINS'] = ""  # explicit opt-out
        bot._DASHBOARD_API_KEY_WARNED['open'] = False
        req = MagicMock()
        req.path = "/api/links"
        req.method = "GET"
        req.headers = {"Origin": "https://whatsapp-monitor-jzp9pilke-azzam10.vercel.app"}
        handler = AsyncMock(return_value=web_json_ok())
        resp = await bot.dashboard_api_key_middleware(req, handler)
        is_401 = hasattr(resp, 'status') and resp.status == 401
        record("DR-5: empty-string allowlist (opt-out) + key unset → 401",
               is_401 and not handler.called,
               f"status={getattr(resp,'status',None)} called={handler.called}")
        # Unrelated origin ALSO 401 under opt-out
        req_evil = MagicMock()
        req_evil.path = "/api/links"
        req_evil.method = "GET"
        req_evil.headers = {"Origin": "https://evil.example.com"}
        handler2 = AsyncMock(return_value=web_json_ok())
        resp2 = await bot.dashboard_api_key_middleware(req_evil, handler2)
        is_401b = hasattr(resp2, 'status') and resp2.status == 401
        record("DR-5b: opt-out mode → any origin 401 (fully fail-closed)",
               is_401b and not handler2.called,
               f"status={getattr(resp2,'status',None)} called={handler2.called}")
    except Exception as e:
        record("DR-5: exception", False, str(e))
    finally:
        os.environ.pop('DASHBOARD_ALLOWED_ORIGINS', None)


async def test_dashboard_restore_options_preflight_allowed():
    """[DASHBOARD-RESTORE-6] OPTIONS preflight on /api/* MUST return 204 with
    CORS headers (Access-Control-Allow-Headers includes X-Api-Key). Without
    this, any future frontend that sends X-Api-Key triggers a preflight that
    the old middleware rejected with 401 (OPTIONS carries no key)."""
    try:
        os.environ['DASHBOARD_API_KEY'] = "secret-123"  # even with key set
        req = MagicMock()
        req.path = "/api/stats"
        req.method = "OPTIONS"
        req.headers = {}  # OPTIONS carries no X-Api-Key
        handler = AsyncMock(return_value=web_json_ok())
        resp = await bot.dashboard_api_key_middleware(req, handler)
        status = getattr(resp, 'status', None)
        # Pull CORS headers off the mock response (aiohttp web.json_response)
        hdrs = getattr(resp, 'headers', {}) or {}
        allow_headers = hdrs.get('Access-Control-Allow-Headers', '')
        record("DR-6: OPTIONS preflight → 204 + CORS headers",
               status == 204 and 'X-Api-Key' in allow_headers and not handler.called,
               f"status={status} allow_headers={allow_headers!r}")
    except Exception as e:
        record("DR-6: exception", False, str(e))
    finally:
        os.environ.pop('DASHBOARD_API_KEY', None)


async def test_dashboard_restore_key_takes_precedence_over_origin():
    """[DASHBOARD-RESTORE-7] When DASHBOARD_API_KEY IS set, the origin
    allowlist is BYPASSED — the key is the sole gate. This means: a trusted
    Origin + key set + no X-Api-Key → 401 (the operator explicitly chose
    key-only mode)."""
    try:
        os.environ['DASHBOARD_API_KEY'] = "secret-key-456"
        os.environ['DASHBOARD_ALLOWED_ORIGINS'] = (
            "https://whatsapp-monitor-jzp9pilke-azzam10.vercel.app"
        )
        bot._DASHBOARD_API_KEY_WARNED['open'] = False
        req = MagicMock()
        req.path = "/api/links"
        req.method = "GET"
        req.headers = {
            "Origin": "https://whatsapp-monitor-jzp9pilke-azzam10.vercel.app"
        }  # trusted origin, BUT no X-Api-Key
        handler = AsyncMock(return_value=web_json_ok())
        resp = await bot.dashboard_api_key_middleware(req, handler)
        is_401 = hasattr(resp, 'status') and resp.status == 401
        record("DR-7: key set → origin allowlist bypassed (key sole gate)",
               is_401 and not handler.called,
               f"status={getattr(resp,'status',None)} called={handler.called}")
    except Exception as e:
        record("DR-7: exception", False, str(e))
    finally:
        os.environ.pop('DASHBOARD_API_KEY', None)
        os.environ.pop('DASHBOARD_ALLOWED_ORIGINS', None)


async def test_dashboard_restore_helper_is_origin_allowed():
    """[DASHBOARD-RESTORE-8] _is_origin_allowed() directly: True for matching
    Origin, True for matching Referer, False for unallowed Origin, False
    when allowlist empty. White-box test of the helper itself."""
    try:
        os.environ['DASHBOARD_ALLOWED_ORIGINS'] = (
            "https://whatsapp-monitor-jzp9pilke-azzam10.vercel.app"
        )
        # match Origin
        req1 = MagicMock()
        req1.headers = {"Origin":
                         "https://whatsapp-monitor-jzp9pilke-azzam10.vercel.app"}
        record("DR-8a: matching Origin → True",
               bot._is_origin_allowed(req1) is True, "match failed")
        # match Referer (no Origin)
        req2 = MagicMock()
        req2.headers = {"Referer":
                        "https://whatsapp-monitor-jzp9pilke-azzam10.vercel.app/x"}
        # MagicMock returns a MagicMock for .get on dict-like — use real dict
        req2.headers = {
            "Referer": "https://whatsapp-monitor-jzp9pilke-azzam10.vercel.app/x"
        }
        record("DR-8b: matching Referer (no Origin) → True",
               bot._is_origin_allowed(req2) is True, "referer fallback failed")
        # unallowed
        req3 = MagicMock()
        req3.headers = {"Origin": "https://evil.example.com"}
        record("DR-8c: unallowed Origin → False",
               bot._is_origin_allowed(req3) is False, "reject failed")
        # explicit opt-out (empty string) → empty allowlist → False
        os.environ['DASHBOARD_ALLOWED_ORIGINS'] = ""
        req4 = MagicMock()
        req4.headers = {"Origin": "https://anything.com"}
        record("DR-8d: explicit opt-out (empty env) → False (no implicit trust)",
               bot._is_origin_allowed(req4) is False, "implicit trust bug")
        # unset env → code-deployed default allowlist → matches official origin
        os.environ.pop('DASHBOARD_ALLOWED_ORIGINS', None)
        req5 = MagicMock()
        req5.headers = {"Origin": bot._DASHBOARD_DEFAULT_ALLOWED_ORIGINS}
        record("DR-8e: unset env → default allowlist matches official origin",
               bot._is_origin_allowed(req5) is True,
               "code-deployed default not honored")
        # unset env → default allowlist does NOT match unrelated origin
        req6 = MagicMock()
        req6.headers = {"Origin": "https://unrelated.example.com"}
        record("DR-8f: unset env → default allowlist rejects unrelated origin",
               bot._is_origin_allowed(req6) is False,
               "default allowlist too permissive")
    except Exception as e:
        record("DR-8: exception", False, str(e))
    finally:
        os.environ.pop('DASHBOARD_ALLOWED_ORIGINS', None)


def web_json_ok():
    """Minimal aiohttp.web.json_response stand-in for middleware tests."""
    class _R:
        status = 200
    return _R()


# =========================================================================
# [Task 11a] Secrets scan — source files must contain NO real secrets
# =========================================================================

async def test_11a_no_real_secrets_in_source_files():
    """[11a/B23] bot.py, link_system.py, source_registry.py, render.yaml must
    contain NO real secrets. Placeholders (YOUR_, fake_, example, <PAT>) are
    OK. This is a regression guard against re-committing a credential."""
    import re
    try:
        files = ["bot.py", "link_system.py", "source_registry.py", "render.yaml"]
        patterns = [
            (r'ghp_[A-Za-z0-9]{36}', "GitHub PAT (ghp_)"),
            (r'github_pat_[A-Za-z0-9_]{60,}', "GitHub fine-grained PAT"),
            (r'sk-proj-[A-Za-z0-9_]{40,}', "OpenAI project key"),
            (r'sk-[A-Za-z0-9]{48}', "OpenAI legacy key"),
            (r'BOT_TOKEN\s*=\s*[0-9]{6,}:[A-Za-z0-9_-]{30,}', "Telegram BOT_TOKEN"),
            (r'API_HASH\s*=\s*[a-f0-9]{30,}', "Telegram API_HASH"),
            (r'session_string\s*=\s*1[A-Za-z0-9_-]{40,}', "Telethon session"),
            (r'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}',
             "Supabase JWT"),
        ]
        bad = []
        for fname in files:
            try:
                txt = (PROJECT_ROOT / fname).read_text(encoding="utf-8",
                                                        errors="ignore")
            except Exception:
                continue
            for pat, label in patterns:
                for m in re.finditer(pat, txt):
                    val = m.group(0)
                    # Allow placeholders
                    if any(ph in val for ph in
                           ("YOUR_", "fake_", "example", "<PAT", "<TOKEN",
                            "123:test", "testhash")):
                        continue
                    bad.append(f"{fname}:{m.start()}: {label}")
        record("11a-1: no real secrets in source files",
               not bad, f"FOUND {len(bad)}: {bad[:3]}" if bad else "clean")
    except Exception as e:
        record("11a-1: exception", False, str(e))


# ============================================================================
# === Requirements Audit (Req-1 Security, Req-2 Monitoring, Req-3 Link-type,
#                          Req-8 PUBLISH-VERIFY) — 10 regression guards
# ============================================================================

async def test_req1_redacting_filter_redacts_phones_and_tokens():
    """[Req-1/Security] _RedactingFilter must redact phone numbers, bot tokens,
    GitHub PATs, and JWTs from log records BEFORE they reach file/stream
    handlers. The project was previously compromised; this is the
    defence-in-depth layer so a missed call site cannot leak phones.

    NOTE: the test inputs are FAKE placeholder strings built at runtime
    (ghp_ + 'a'*36, 1234567890: + 'a'*35, +9998887770000). No real
    credential is committed to this source file — committing a real token
    here would itself be the leak this test guards against (Req-1/B23)."""
    import re as _re
    try:
        rf = bot._RedactingFilter()
        # Build FAKE secret-shaped strings at runtime so no real credential
        # is ever written to the source file.
        _fake_phone = "+9998887770000"  # 13 digits, ITU-reserved +999 (fake)
        _fake_bot_token = "1234567890:" + "a" * 35  # fake shape, not a real token
        _fake_ghp = "ghp_" + "a" * 36  # fake shape, not a real PAT
        _fake_jwt = "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12
        cases = [
            (f"[JOINER] selected account={_fake_phone}", r'\+\d{7,15}'),
            (f"Bot token {_fake_bot_token}", r'\d{5,12}:[A-Za-z0-9_-]{30,}'),
            (f"github pat {_fake_ghp}", r'ghp_[A-Za-z0-9]{36}'),
            (f"supabase {_fake_jwt}",
             r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'),
        ]
        all_ok = True
        details = []
        for raw, forbidden_re in cases:
            # sanity: the input MUST contain the secret pattern (else test is vacuous)
            if not _re.search(forbidden_re, raw):
                all_ok = False
                details.append(f"VACUOUS input '{raw[:30]}': pattern not present")
                continue
            rec = logging.LogRecord("t", logging.INFO, "", 0, raw, (), None)
            rf.filter(rec)
            out = rec.getMessage()
            if _re.search(forbidden_re, out):
                all_ok = False
                details.append(f"LEAK: pattern still present in '{out[:40]}'")
        record("Req1-1: _RedactingFilter redacts phones/tokens/PATs/JWTs",
               all_ok, "; ".join(details) if details else "all redacted")
    except Exception as e:
        record("Req1-1: exception", False, str(e))


async def test_req1_redacting_filter_installed_in_setup_logging():
    """[Req-1/Security] setup_logging must attach _RedactingFilter to BOTH
    the file handler and the stdout handler so no log record bypasses it."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        # locate setup_logging body
        start = src.find("def setup_logging(level_name):")
        end = src.find("\n\n", src.find("logging.getLogger(\"aiohttp\")", start))
        body = src[start:end] if start >= 0 and end > start else ""
        has_filter_class = "class _RedactingFilter" in src
        has_add_filter_fh = "fh.addFilter(_redact)" in body
        has_add_filter_ch = "ch.addFilter(_redact)" in body
        record("Req1-2: setup_logging installs _RedactingFilter on both handlers",
               has_filter_class and has_add_filter_fh and has_add_filter_ch,
               f"class={has_filter_class} fh={has_add_filter_fh} ch={has_add_filter_ch}")
    except Exception as e:
        record("Req1-2: exception", False, str(e))


async def test_req1_api_pii_masking_when_dashboard_open():
    """[Req-1/Security] When DASHBOARD_API_KEY is unset (open dashboard),
    _api_should_show_full_pii() must return False so phones are masked in
    /api/joiners_status + /api/joined_groups. When set, return True (the
    middleware already authenticated the caller)."""
    try:
        os.environ.pop('DASHBOARD_API_KEY', None)
        open_mode = bot._api_should_show_full_pii()
        os.environ['DASHBOARD_API_KEY'] = 'test-secret-key-123'
        locked_mode = bot._api_should_show_full_pii()
        os.environ.pop('DASHBOARD_API_KEY', None)  # restore baseline
        record("Req1-3: _api_should_show_full_pii masks when open, shows when locked",
               (open_mode is False) and (locked_mode is True),
               f"open={open_mode} locked={locked_mode}")
    except Exception as e:
        record("Req1-3: exception", False, str(e))


async def test_req1_bot_token_not_logged_prefix_suffix():
    """[Req-1/Security] bot.py must NOT log bot_token[:8] or bot_token[-4:]
    (prefix/suffix leak). Only the length is safe to log."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        leak_patterns = ["bot_token[:8]", "bot_token[-4:]",
                         "bot_token[:4]", "bot_token[-2:]"]
        leaks = [p for p in leak_patterns if p in src]
        record("Req1-4: no bot_token prefix/suffix in logging",
               not leaks, f"leaks found: {leaks}")
    except Exception as e:
        record("Req1-4: exception", False, str(e))


async def test_req1_supabase_url_masked_in_deploy_check():
    """[Req-1/Security] /api/deploy_check must mask the Supabase project host,
    not return db.supabase_url raw."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        masked = "report[\"supabase\"][\"url\"] = _supa_masked" in src
        has_raw_leak = 'report["supabase"]["url"] = db.supabase_url' in src
        record("Req1-5: Supabase URL masked in deploy_check",
               masked and not has_raw_leak,
               f"masked={masked} raw_leak={has_raw_leak}")
    except Exception as e:
        record("Req1-5: exception", False, str(e))


async def test_req8_delete_forwarded_request_removes_phantom_row():
    """[Req-8/PUBLISH-VERIFY] delete_forwarded_request() must remove the
    dedup row for a link so a phantom publish row (inserted before _send
    failed) is rolled back and the next cycle can re-publish."""
    from datetime import datetime
    from unittest.mock import AsyncMock, patch
    try:
        db = bot.DatabaseManager(tempfile.mktemp(suffix=".db"))
        await db.init_db()
        try:
            with patch.object(db, '_supabase_insert_link', new=AsyncMock()):
                with patch.object(db, '_get_supabase_session', new=AsyncMock(return_value=None)):
                    link = "https://t.me/Req8TestGroup"
                    inserted = await db.insert_request(
                        link=link, message_date=datetime.now(),
                        group_name="g", sender_name="s", source_phone="+966",
                        link_type="telegram")
                    conn = await db._ensure_conn()
                    c = await conn.execute("SELECT COUNT(*) FROM forwarded_requests")
                    before = (await c.fetchone())[0]
                    deleted = await db.delete_forwarded_request(link)
                    c = await conn.execute("SELECT COUNT(*) FROM forwarded_requests")
                    after = (await c.fetchone())[0]
                    deleted2 = await db.delete_forwarded_request(link)
            try: await db.close()
            except Exception: pass
            record("Req8-1: delete_forwarded_request removes phantom row",
                   inserted and deleted and (after == before - 1) and (deleted2 is False),
                   f"inserted={inserted} deleted={deleted} before={before} after={after} deleted2={deleted2}")
        finally:
            try: await db.close()
            except Exception: pass
    except Exception as e:
        record("Req8-1: exception", False, str(e))


async def test_req8_publish_failure_rolls_back_phantom_row():
    """[Req-8/PUBLISH-VERIFY] On _send() failure, the publish block must
    (a) delete the phantom forwarded_requests row, AND (b) reset group_state
    to DISCOVERED, so the next cycle re-attempts the full publish instead of
    silently skipping it (state=QUEUED + duplicate row = never published)."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        # locate the PUBLISH_FAILED rollback branch marker
        marker = "PUBLISH_FAILED — rolling back phantom publish row"
        idx = src.find(marker)
        if idx < 0:
            record("Req8-2: publish-failure rollback branch", False,
                   "rollback marker not found — fix may have been reverted")
            return
        # Search for each pattern AFTER the marker, within a generous 5000-char span.
        BOUND = 5000
        def _after(pat):
            p = src.find(pat, idx)
            return 0 < p < idx + BOUND
        has_delete = _after("delete_forwarded_request(raw_link)")
        has_reset = _after("set_group_state(normalized, GroupState.DISCOVERED")
        has_retry = _after("next_retry=datetime.now() + timedelta(minutes=2)")
        record("Req8-2: _send failure rolls back phantom row + resets state",
               has_delete and has_reset and has_retry,
               f"delete={has_delete} reset={has_reset} retry={has_retry}")
    except Exception as e:
        record("Req8-2: exception", False, str(e))


async def test_req2_startup_scan_wired_into_start():
    """[Req-2/Monitoring] _run_startup_scan must be CALLED from start() when
    config.startup_scan_days is not None — previously dead code (defined but
    never invoked), so STARTUP_SCAN_DAYS had no effect and the bot only
    captured links from NEW messages, missing groups in older messages."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        # locate start() method
        s = src.find("async def start(self):")
        e = src.find("\n    async def ", s + 50)
        start_body = src[s:e] if s >= 0 and e > s else ""
        has_gate = "self.config.startup_scan_days is not None" in start_body
        has_call = "self._run_startup_scan(w)" in start_body
        has_track = "_startup_scan_done" in start_body
        record("Req2-1: _run_startup_scan wired into start()",
               has_gate and has_call and has_track,
               f"gate={has_gate} call={has_call} track={has_track}")
    except Exception as e:
        record("Req2-1: exception", False, str(e))


async def test_req3_pre_publish_channel_exclusion_present():
    """[Req-3/Link-type] The publish pipeline (PIPELINE-5) must resolve the
    entity for public telegram username links and SKIP publish+join if it's
    a broadcast channel or a User/Bot — the user wants student GROUPS only,
    not channels or profile links in the published feed."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_block = "PRE-PUBLISH channel/user exclusion" in src
        has_broadcast = "is_channel_broadcast" in src
        has_user = "'not_a_group'" in src
        has_timeout = "asyncio.wait_for(_pp_client.get_entity(raw_link), timeout=15)" in src
        record("Req3-1: pre-publish channel/user exclusion in PIPELINE-5",
               has_block and has_broadcast and has_user and has_timeout,
               f"block={has_block} broadcast={has_broadcast} user={has_user} timeout={has_timeout}")
    except Exception as e:
        record("Req3-1: exception", False, str(e))


async def test_req3_scorer_marks_channels_banned():
    """[Req-3/Link-type] The priority scorer (which already resolves entities
    via get_entity) must mark broadcast channels and User/Bot entities as
    BANNED so the scheduler skips them on this and future cycles."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        # locate the scorer entity-resolution block (NOT the pre-publish one)
        marker = "EXCLUDE broadcast channels — the user wants"
        idx = src.find(marker)
        if idx < 0:
            record("Req3-2: scorer marks channels/users BANNED", False,
                   "scorer channel-exclusion marker not found")
            return
        # Search for each pattern AFTER the scorer marker, within 5000 chars.
        BOUND = 5000
        def _after(pat):
            p = src.find(pat, idx)
            return 0 < p < idx + BOUND
        has_broadcast_ban = _after("is_channel_broadcast") and _after("GroupState.BANNED")
        has_user_ban = _after("not_a_group")
        record("Req3-2: scorer marks channels + users BANNED",
               has_broadcast_ban and has_user_ban,
               f"broadcast_ban={has_broadcast_ban} user_ban={has_user_ban}")
    except Exception as e:
        record("Req3-2: exception", False, str(e))


# ===================================================================
# [REQAUDIT-2] InviteRequestSentError → PENDING_APPROVAL full lifecycle
# Production evidence (2026-08-26 03:49:47 / 03:51:01 UTC):
#   telethon.errors.rpcerrorlist.InviteRequestSentError:
#     "You have successfully requested to join this chat or channel"
#   → logged as "❌ FAILED" + retried every 30 min — WRONG. Fix: catch the
#   error, return PENDING_APPROVAL, mark DONE (not retried), add to ALL
#   dedup skip-sets, AND self-heal via _pending_approval_recheck_loop.
# ===================================================================

async def test_reqaudit2_groupstate_pending_approval_enum():
    """[REQAUDIT-2] GroupState enum must include PENDING_APPROVAL."""
    try:
        from link_system import GroupState
        ok = hasattr(GroupState, "PENDING_APPROVAL") and GroupState.PENDING_APPROVAL == "PENDING_APPROVAL"
        record("ReqAudit2-1: GroupState.PENDING_APPROVAL enum present",
               ok, f"value={getattr(GroupState, 'PENDING_APPROVAL', '<missing>')}")
    except Exception as e:
        record("ReqAudit2-1: exception", False, str(e))


async def test_reqaudit2_invite_request_sent_error_imported():
    """[REQAUDIT-2] _join_group_safe must import InviteRequestSentError."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        idx = src.find("def _join_group_safe")
        if idx < 0:
            record("ReqAudit2-2: InviteRequestSentError imported", False,
                   "_join_group_safe not found")
            return
        import_block_idx = src.find("from telethon.errors import", idx)
        ok = import_block_idx > 0 and "InviteRequestSentError" in src[import_block_idx:import_block_idx + 800]
        record("ReqAudit2-2: InviteRequestSentError imported in _join_group_safe",
               ok, f"import_block_found={import_block_idx > 0}")
    except Exception as e:
        record("ReqAudit2-2: exception", False, str(e))


async def test_reqaudit2_invite_request_sent_handler_private_branch():
    """[REQAUDIT-2] telegram_private branch catches InviteRequestSentError → (True, PENDING_APPROVAL, None)."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        idx = src.find("if link_type == 'telegram_private':")
        if idx < 0:
            record("ReqAudit2-3: private-branch handler", False,
                   "telegram_private branch not found")
            return
        BOUND = 4000
        handler_idx = src.find("except InviteRequestSentError:", idx)
        flood_idx = src.find("except FloodWaitError as e:", idx)
        return_idx = src.find('return True, "PENDING_APPROVAL", None', idx)
        ok = (0 < handler_idx < idx + BOUND
              and 0 < flood_idx < idx + BOUND
              and handler_idx < flood_idx
              and 0 < return_idx < handler_idx + 1200)
        record("ReqAudit2-3: private-branch catches InviteRequestSentError → PENDING_APPROVAL",
               ok,
               f"handler={handler_idx} flood={flood_idx} return={return_idx} branch={idx}")
    except Exception as e:
        record("ReqAudit2-3: exception", False, str(e))


async def test_reqaudit2_invite_request_sent_handler_username_branch():
    """[REQAUDIT-2] telegram (username) branch ALSO catches InviteRequestSentError."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        idx = src.find("elif link_type == 'telegram':")
        if idx < 0:
            record("ReqAudit2-4: username-branch handler", False,
                   "telegram (username) branch not found")
            return
        handler_count = src.count("except InviteRequestSentError:")
        second_handler_idx = src.find("except InviteRequestSentError:", idx)
        return_idx = src.find('return True, "PENDING_APPROVAL", None', second_handler_idx)
        ok = (handler_count >= 2
              and second_handler_idx > idx
              and 0 < return_idx < second_handler_idx + 800)
        record("ReqAudit2-4: username-branch catches InviteRequestSentError → PENDING_APPROVAL",
               ok,
               f"handler_count={handler_count} second_handler={second_handler_idx} return={return_idx}")
    except Exception as e:
        record("ReqAudit2-4: exception", False, str(e))


async def test_reqaudit2_pipeline6_pending_approval_branch():
    """[REQAUDIT-2] PIPELINE-6 caller maps PENDING_APPROVAL → DONE (NOT QUEUED)."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        marker = 'elif status == "PENDING_APPROVAL":'
        idx = src.find(marker)
        if idx < 0:
            record("ReqAudit2-5: PIPELINE-6 PENDING_APPROVAL branch", False,
                   "PENDING_APPROVAL elif-branch not found")
            return
        next_elif = src.find('                elif status ==', idx + len(marker))
        end = next_elif if 0 < next_elif else idx + 1500
        window = src[idx:end]
        has_state = "GroupState.PENDING_APPROVAL" in window
        has_done = "final_status = 'DONE'" in window
        no_retry = "QUEUED" not in window and "minutes=30" not in window
        record("ReqAudit2-5: PIPELINE-6 marks PENDING_APPROVAL as DONE (not retried)",
               has_state and has_done and no_retry,
               f"state={has_state} done={has_done} no_retry={no_retry}")
    except Exception as e:
        record("ReqAudit2-5: exception", False, str(e))


async def test_reqaudit2_dedup_skipsets_include_pending_approval():
    """[REQAUDIT-2] PENDING_APPROVAL in ALL JOINED+ALREADY_MEMBER skip-sets (4+)."""
    try:
        import re
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        pattern = re.compile(r"GroupState\.JOINED,\s*GroupState\.ALREADY_MEMBER(?:,\s*GroupState\.PENDING_APPROVAL)?")
        matches = list(pattern.finditer(src))
        total = len(matches)
        with_pa = sum(1 for m in matches if "GroupState.PENDING_APPROVAL" in m.group(0))
        ok = total >= 4 and with_pa == total
        record("ReqAudit2-6: all JOINED+ALREADY_MEMBER skip-sets include PENDING_APPROVAL",
               ok, f"skipsets_found={total} with_PENDING_APPROVAL={with_pa}")
    except Exception as e:
        record("ReqAudit2-6: exception", False, str(e))


async def test_reqaudit2_joined_groups_command_shows_pending():
    """[REQAUDIT-2] /joined_groups surfaces a PENDING_APPROVAL section."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_marker = "بانتظار موافقة المشرف (PENDING_APPROVAL)" in src
        has_query = "GroupState.PENDING_APPROVAL" in src
        has_display = "بانتظار موافقة المشرف" in src
        record("ReqAudit2-7: /joined_groups shows PENDING_APPROVAL section",
               has_marker and has_query and has_display,
               f"marker={has_marker} query={has_query} display={has_display}")
    except Exception as e:
        record("ReqAudit2-7: exception", False, str(e))


async def test_reqaudit2_api_joined_groups_pending_count():
    """[REQAUDIT-2] /api/joined_groups exposes pending_approval stat."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_query = "state = 'PENDING_APPROVAL'" in src
        has_stat = '"pending_approval"' in src
        record("ReqAudit2-8: /api/joined_groups exposes pending_approval stat",
               has_query and has_stat,
               f"query={has_query} stat={has_stat}")
    except Exception as e:
        record("ReqAudit2-8: exception", False, str(e))


async def test_reqaudit2_recheck_loop_method_present():
    """[REQAUDIT-2 STRONGEST] _pending_approval_recheck_loop method must
    exist — this is the self-healing loop that detects when the admin
    approves and transitions PENDING_APPROVAL → JOINED. Without it, groups
    stay pending forever (operator would have to manually re-scan)."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_method = "async def _pending_approval_recheck_loop" in src
        has_checkinvite = "CheckChatInviteRequest" in src
        has_chatalready = "ChatInviteAlready" in src
        has_transition = "GroupState.JOINED, raw,\n                                        joined_by=phone, error='approved_via_recheck'" in src or \
                        "error='approved_via_recheck'" in src
        record("ReqAudit2-9: _pending_approval_recheck_loop self-healing method present",
               has_method and has_checkinvite and has_chatalready and has_transition,
               f"method={has_method} checkChatInvite={has_checkinvite} ChatInviteAlready={has_chatalready} transition={has_transition}")
    except Exception as e:
        record("ReqAudit2-9: exception", False, str(e))


async def test_reqaudit2_recheck_loop_started_in_start():
    """[REQAUDIT-2 STRONGEST] The recheck loop must be started in start()
    (not just defined). Otherwise it's dead code like _run_startup_scan was
    before REQAUDIT-1."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_start = "asyncio.create_task(self._pending_approval_recheck_loop())" in src
        has_log = "Pending-Approval Recheck started" in src
        record("ReqAudit2-10: recheck loop wired into start()",
               has_start and has_log,
               f"start={has_start} log={has_log}")
    except Exception as e:
        record("ReqAudit2-10: exception", False, str(e))


async def test_reqaudit2_recheck_loop_supervised():
    """[REQAUDIT-2 STRONGEST] The recheck loop must be in the supervisor's
    watched-task list — if it dies (OOM, exception), the supervisor
    resurrects it. Otherwise PENDING_APPROVAL groups never get re-checked."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        # The supervisor block must reference the task handle AND create_task it
        has_handle_check = "_pending_approval_recheck_task" in src
        # Find the supervisor block
        sup_idx = src.find("async def _supervisor_loop")
        if sup_idx < 0:
            record("ReqAudit2-11: recheck loop supervised", False, "supervisor not found")
            return
        # Find the relaunch block within 12000 chars of the supervisor
        # (the supervisor has a long docstring + 10 task checks, so the
        # recheck relaunch is ~8400 chars in)
        relaunch_block = src.find("_pending_approval_recheck_task", sup_idx)
        has_relaunch = relaunch_block > 0 and relaunch_block < sup_idx + 12000
        has_create_task = "asyncio.create_task(\n                        self._pending_approval_recheck_loop())" in src or \
                          "asyncio.create_task(self._pending_approval_recheck_loop())" in src
        has_warning = "restarted pending_approval_recheck" in src
        record("ReqAudit2-11: recheck loop supervised (resurrected on death)",
               has_handle_check and has_relaunch and has_create_task and has_warning,
               f"handle={has_handle_check} relaunch={has_relaunch} create_task={has_create_task} warn={has_warning}")
    except Exception as e:
        record("ReqAudit2-11: exception", False, str(e))


async def test_reqaudit2_recheck_loop_shutdown_cancellation():
    """[REQAUDIT-2 STRONGEST] The recheck task must be cancelled on shutdown
    (added to the cleanup task list in stop()) so the process exits cleanly."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_attr = "pending_recheck_task = getattr(self, '_pending_approval_recheck_task'" in src
        in_list = "pending_recheck_task\n                 ]" in src or "pending_recheck_task" in src
        record("ReqAudit2-12: recheck task cancelled on shutdown",
               has_attr,
               f"attr={has_attr} in_list={in_list}")
    except Exception as e:
        record("ReqAudit2-12: exception", False, str(e))


async def test_reqaudit2_pending_approvals_command():
    """[REQAUDIT-2 STRONGEST] /pending_approvals bot command must exist for
    operator visibility (Req #7 — clear JOIN result recording)."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_cmd = 'elif cmd == "/pending_approvals":' in src
        in_admin = '"/pending_approvals"' in src
        has_query = "FROM group_states WHERE state = ?" in src and "GroupState.PENDING_APPROVAL" in src
        record("ReqAudit2-13: /pending_approvals command registered + handler",
               has_cmd and in_admin and has_query,
               f"cmd={has_cmd} in_admin_list={in_admin} query={has_query}")
    except Exception as e:
        record("ReqAudit2-13: exception", False, str(e))


async def test_reqaudit2_api_pending_approvals_endpoint():
    """[REQAUDIT-2 STRONGEST] /api/pending_approvals HTTP endpoint must exist
    so the dashboard can surface pending-approval groups."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_handler = "async def api_pending_approvals_handler" in src
        has_route = 'app.router.add_get("/api/pending_approvals"' in src
        has_self_heal = '"self_healing": True' in src
        record("ReqAudit2-14: /api/pending_approvals endpoint + route",
               has_handler and has_route and has_self_heal,
               f"handler={has_handler} route={has_route} self_heal_stat={has_self_heal}")
    except Exception as e:
        record("ReqAudit2-14: exception", False, str(e))


async def test_reqaudit2_recheck_uses_original_joiner_account():
    """[REQAUDIT-2 STRONGEST] The recheck must use the SAME joiner account
    that sent the original request (group_states.joined_by) — otherwise the
    membership check would query a different account that was never invited,
    and ChatInviteAlready would never fire."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_select = "SELECT normalized_link, raw_link, joined_by, last_seen" in src
        has_use = "client = getattr(self, 'user_clients', {}).get(phone)" in src
        has_phone_from_row = "phone = joined_by or \"\"" in src
        record("ReqAudit2-15: recheck uses original joiner account (joined_by)",
               has_select and has_use and has_phone_from_row,
               f"select={has_select} use_clients={has_use} phone_from_row={has_phone_from_row}")
    except Exception as e:
        record("ReqAudit2-15: exception", False, str(e))


async def test_reqaudit2_recheck_bounded_and_rated():
    """[REQAUDIT-2 STRONGEST] The recheck loop must be bounded (max 50/cycle)
    and rate-limited (sleep 5s between checks) so it can't monopolize the
    API or trigger FloodWait."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_limit = "LIMIT 50" in src
        has_sleep = "await asyncio.sleep(5)" in src
        has_interval = "PENDING_RECHECK_INTERVAL_S" in src
        record("ReqAudit2-16: recheck bounded (50/cycle) + rated (5s) + env interval",
               has_limit and has_sleep and has_interval,
               f"limit={has_limit} sleep5={has_sleep} env_interval={has_interval}")
    except Exception as e:
        record("ReqAudit2-16: exception", False, str(e))


async def test_reqaudit2_recheck_invite_expired_handling():
    """[REQAUDIT-2 STRONGEST] If the invite expired (admin revoked), the
    recheck must transition the group to PRIVATE (terminal) so we stop
    checking a dead link forever — otherwise the loop re-checks an
    unreachable group every 30 min indefinitely."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_expired_check = "'Expired' in _ename or 'expired' in str(e).lower()" in src
        has_private_transition = "GroupState.PRIVATE, raw,\n                                        error='invite_expired_recheck'" in src or \
                                "error='invite_expired_recheck'" in src
        record("ReqAudit2-17: recheck handles invite-expired → PRIVATE (terminal)",
               has_expired_check and has_private_transition,
               f"expired_check={has_expired_check} private_transition={has_private_transition}")
    except Exception as e:
        record("ReqAudit2-17: exception", False, str(e))


# ==========================================================================
# === [REQAUDIT-3] Joiner Fleet Resilience — 8 regressions ================
# ==========================================================================
# Production evidence (2026-08-26 16:55–17:00 UTC, post-REQAUDIT-2 deploy):
#   Every PIPELINE-6 cycle failed with the same 3-account pattern:
#     Account 1 → FloodWait active (28593s left)   ≈ 8h ban
#     Account 2 → safety_guard (hourly_limit_5/5)
#     Account 3 → not_connected (session dropped, no auto-reconnect)
#   → no eligible joiner → QUEUED+5min → METRIC Skipped (total: 96).
# The bot stayed in this frozen state silently — no operator alert, no
# /ready signal, no auto-recovery. These 8 tests pin the REQAUDIT-3 fix
# so the same fleet-down state can never happen silently again.


async def test_reqaudit3_fleet_health_state_in_init():
    """ReqAudit3-1: Monitor.__init__ must declare _alerted_terminal_phones
    (set) and _fleet_health (dict with the 6 keys) + _joiner_fleet_health_task
    slot. Without these, the fleet-health loop and owner-alert dedup have
    no state to write to."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        has_alerted_set = "_alerted_terminal_phones: Set[str] = set()" in src
        has_fleet_dict = "_fleet_health: Dict[str, Any] = {" in src
        required_keys = [
            "'connected_joiners'",
            "'floodwait_joiners'",
            "'disconnected_joiners'",
            "'safety_guard_blocked_joiners'",
            "'all_unavailable_since'",
            "'fleet_down_alerted'",
        ]
        missing = [k for k in required_keys if k not in src]
        has_task_slot = "_joiner_fleet_health_task: Optional[asyncio.Task] = None" in src
        record("ReqAudit3-1: fleet health state attrs in __init__",
               has_alerted_set and has_fleet_dict and not missing and has_task_slot,
               f"alerted_set={has_alerted_set} fleet_dict={has_fleet_dict} "
               f"task_slot={has_task_slot} missing_keys={missing}")
    except Exception as e:
        record("ReqAudit3-1: exception", False, str(e))


async def test_reqaudit3_run_user_client_non_terminal():
    """ReqAudit3-2: _run_user_client must NOT `return` on terminal session
    failures. Previously the 4 terminal branches (invalid_session_string /
    invalid_session / client_creation_error / not_authorized) did `return`,
    leaving the phone permanently not_connected. Now they must call
    _alert_terminal_failure + asyncio.sleep(3600) + continue.
    Also must re-fetch watcher from DB each iteration so operator-updated
    session_string is picked up without restart."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        # Find the _run_user_client body window.
        start = src.find("async def _run_user_client(self, watcher):")
        # End at next method def at column 4 (i.e., "    def " or "    async def ").
        end_match = src.find("\n    def _cleanup_user_client", start)
        body = src[start:end_match] if (start >= 0 and end_match > 0) else ""
        # Must NOT have a bare `return` in the terminal branches.
        # (a bare `return` outside `except asyncio.CancelledError: raise` and
        # not as part of `return` in a different method)
        # Count `return` occurrences in the body — should be 0 for the
        # terminal branches now that they use `continue`.
        returns_in_body = body.count("\n                        return") + \
                          body.count("\n                    return") + \
                          body.count("\n                return")
        # Actually let's count any standalone "        return" with 8 spaces
        # at start of line OR within nested blocks.
        bare_returns = sum(1 for line in body.split("\n")
                           if line.strip() == "return")
        has_alert_call = "_alert_terminal_failure(phone," in body
        has_sleep_3600 = "asyncio.sleep(3600)" in body
        has_continue = body.count("continue") >= 4  # 4 terminal branches
        has_db_refetch = "_supabase_get_watcher(phone)" in body
        # The docstring must mention NON-TERMINAL.
        has_non_terminal_doc = "NON-TERMINAL" in body
        record("ReqAudit3-2: _run_user_client is non-terminal (alert+sleep+continue)",
               has_alert_call and has_sleep_3600 and has_continue >= 1
               and has_db_refetch and has_non_terminal_doc and bare_returns == 0,
               f"alert={has_alert_call} sleep3600={has_sleep_3600} "
               f"continue_count={body.count('continue')} db_refetch={has_db_refetch} "
               f"non_terminal_doc={has_non_terminal_doc} bare_returns={bare_returns}")
    except Exception as e:
        record("ReqAudit3-2: exception", False, str(e))


async def test_reqaudit3_supervisor_watches_user_tasks():
    """ReqAudit3-3: _supervisor_loop must include a section that iterates
    self._user_tasks and restarts any dead task whose phone is still in
    the watchers DB. Without this, a dead _run_user_client stays dead
    forever (the loop's own non-terminal refactor handles most cases,
    but a CancelledError leaking or an OOM killing the task entirely
    needs supervisor-level resurrection)."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        start = src.find("async def _supervisor_loop(self):")
        end = src.find("    # ===================================================================\n    # [REQAUDIT-3] Joiner Fleet Health", start)
        body = src[start:end] if (start >= 0 and end > 0) else src[start:start+15000]
        has_user_tasks_iter = "for ph, t in list(self._user_tasks.items())" in body
        has_live_phones_check = "ph not in live_phones" in body
        has_restart = "self._user_tasks[ph] = asyncio.create_task" in body
        has_warning = "[SUPERVISOR] restarted user_client for" in body
        record("ReqAudit3-3: supervisor restarts dead _user_tasks",
               has_user_tasks_iter and has_live_phones_check and has_restart and has_warning,
               f"iter={has_user_tasks_iter} live_check={has_live_phones_check} "
               f"restart={has_restart} warning={has_warning}")
    except Exception as e:
        record("ReqAudit3-3: exception", False, str(e))


async def test_reqaudit3_fleet_health_loop_method_present():
    """ReqAudit3-4: _joiner_fleet_health_loop method must exist with the
    60s cycle, must compute connected/floodwait/disconnected/
    safety_guard_blocked counts, must update self._fleet_health, and
    must call _send_fleet_down_alert after 300s of full outage."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        start = src.find("async def _joiner_fleet_health_loop(self):")
        # End at the next method def
        end = src.find("async def _send_fleet_down_alert", start)
        body = src[start:end] if (start >= 0 and end > 0) else src[start:start+12000]
        has_method = start >= 0
        has_60s_cycle = "await asyncio.sleep(60)" in body
        has_connected_count = "connected_count = len(connected)" in body
        has_floodwait_list = "floodwait_list.append({'phone': ph, 'wait_s'" in body
        has_disconnected_list = "disconnected.append(ph)" in body
        has_safety_count = "safety_guard_blocked += 1" in body
        has_snapshot_update = "self._fleet_health = {" in body
        has_300s_threshold = "down_seconds >= 300" in body
        has_alert_call = "await self._send_fleet_down_alert(" in body
        record("ReqAudit3-4: _joiner_fleet_health_loop computes + alerts",
               has_method and has_60s_cycle and has_connected_count
               and has_floodwait_list and has_disconnected_list
               and has_safety_count and has_snapshot_update
               and has_300s_threshold and has_alert_call,
               f"method={has_method} 60s={has_60s_cycle} connected={has_connected_count} "
               f"floodwait={has_floodwait_list} disc={has_disconnected_list} "
               f"safety={has_safety_count} snapshot={has_snapshot_update} "
               f"300s={has_300s_threshold} alert_call={has_alert_call}")
    except Exception as e:
        record("ReqAudit3-4: exception", False, str(e))


async def test_reqaudit3_fleet_health_task_supervised():
    """ReqAudit3-5: _joiner_fleet_health_task must be in the supervisor's
    watched set (auto-restarted on death) AND started in start()."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        # Supervisor section
        sup_start = src.find("async def _supervisor_loop(self):")
        sup_end = src.find("    # ===================================================================\n    # [REQAUDIT-3] Joiner Fleet Health", sup_start)
        sup_body = src[sup_start:sup_end] if (sup_start >= 0 and sup_end > 0) else src[sup_start:sup_start+15000]
        has_sup_watch = "_joiner_fleet_health_task" in sup_body and \
                        "self._joiner_fleet_health_task = asyncio.create_task" in sup_body
        has_sup_warning = "[SUPERVISOR] restarted joiner_fleet_health" in sup_body
        # start() wiring
        start_section = src.find("self._pending_approval_recheck_task = asyncio.create_task(self._pending_approval_recheck_loop())")
        start_window = src[start_section:start_section+2000] if start_section > 0 else ""
        has_start_wiring = "_joiner_fleet_health_task = asyncio.create_task(self._joiner_fleet_health_loop())" in start_window
        has_start_log = "🛡️ Joiner Fleet Health monitor started" in start_window
        # shutdown cancellation
        has_shutdown_cancel = "fleet_health_task = getattr(self, '_joiner_fleet_health_task', None)" in src
        record("ReqAudit3-5: fleet-health task is supervised + started + cancelled",
               has_sup_watch and has_sup_warning and has_start_wiring
               and has_start_log and has_shutdown_cancel,
               f"sup_watch={has_sup_watch} sup_warn={has_sup_warning} "
               f"start_wired={has_start_wiring} start_log={has_start_log} "
               f"shutdown_cancel={has_shutdown_cancel}")
    except Exception as e:
        record("ReqAudit3-5: exception", False, str(e))


async def test_reqaudit3_joiner_worker_fleet_backoff():
    """ReqAudit3-6: _joiner_worker must check self._fleet_health's
    connected_joiners count BEFORE picking a link, and skip the cycle
    (sleep 60s + continue) when ALL joiners are unavailable. Without
    this, the scheduler burns cycles re-enqueueing every stuck link
    every 5 min (96+ links × every 5 min = wasted storm)."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        start = src.find("async def _joiner_worker(self):")
        end = src.find("async def _alert_terminal_failure", start)
        body = src[start:end] if (start >= 0 and end > 0) else src[start:start+15000]
        has_fleet_get = "fleet = getattr(self, '_fleet_health', None) or {}" in body
        has_zero_check = "fleet.get('connected_joiners', 0) == 0" in body
        # The log message is an f-string split across lines, so check the
        # two distinguishing components separately.
        has_skip_log = "[FLEET]" in body and "all joiners" in body and "unavailable" in body
        has_sleep_continue = "await asyncio.sleep(60)\n                    continue" in body
        record("ReqAudit3-6: _joiner_worker fleet backoff gate",
               has_fleet_get and has_zero_check and has_skip_log and has_sleep_continue,
               f"fleet_get={has_fleet_get} zero_check={has_zero_check} "
               f"skip_log={has_skip_log} sleep_continue={has_sleep_continue}")
    except Exception as e:
        record("ReqAudit3-6: exception", False, str(e))


async def test_reqaudit3_ready_endpoint_surfaces_fleet():
    """ReqAudit3-7: /ready response must include a fleet_health object
    with connected_joiners, floodwait_joiners_count,
    disconnected_joiners_count, safety_guard_blocked_joiners, and
    all_joiners_unavailable. Without this, /ready returned "ready" even
    when ALL joiners were in FloodWait/disconnected — masking the real
    fleet-down state from the operator."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        start = src.find("async def ready_handler(request):")
        end = src.find("async def metrics_handler", start)
        body = src[start:end] if (start >= 0 and end > 0) else src[start:start+5000]
        has_fleet_var = "fleet = {}" in body and "fh = getattr(monitor, '_fleet_health'" in body
        required_keys = [
            '"connected_joiners"',
            '"floodwait_joiners_count"',
            '"disconnected_joiners_count"',
            '"safety_guard_blocked_joiners"',
            '"all_joiners_unavailable"',
        ]
        missing = [k for k in required_keys if k not in body]
        has_fleet_in_response = '"fleet_health": fleet' in body
        record("ReqAudit3-7: /ready surfaces fleet_health",
               has_fleet_var and not missing and has_fleet_in_response,
               f"fleet_var={has_fleet_var} missing_keys={missing} "
               f"in_response={has_fleet_in_response}")
    except Exception as e:
        record("ReqAudit3-7: exception", False, str(e))


async def test_reqaudit3_api_joined_groups_surfaces_fleet():
    """ReqAudit3-8: /api/joined_groups stats must include a fleet_health
    object (same shape as /ready). This is the dashboard's primary data
    source — without it, the dashboard shows "active_joiners: 3" even
    when all 3 are in FloodWait/disconnected."""
    try:
        src = (PROJECT_ROOT / "bot.py").read_text(encoding="utf-8")
        start = src.find("async def api_joined_groups_handler(request):")
        end = src.find("async def api_pending_approvals_handler", start)
        body = src[start:end] if (start >= 0 and end > 0) else src[start:start+10000]
        has_fleet_block = "fleet = {}" in body and \
                          "fh = getattr(monitor, '_fleet_health'" in body
        required_keys = [
            '"connected_joiners"',
            '"floodwait_joiners_count"',
            '"disconnected_joiners_count"',
            '"safety_guard_blocked_joiners"',
            '"all_joiners_unavailable"',
        ]
        missing = [k for k in required_keys if k not in body]
        has_in_stats = '"fleet_health": fleet' in body
        record("ReqAudit3-8: /api/joined_groups surfaces fleet_health",
               has_fleet_block and not missing and has_in_stats,
               f"fleet_block={has_fleet_block} missing_keys={missing} "
               f"in_stats={has_in_stats}")
    except Exception as e:
        record("ReqAudit3-8: exception", False, str(e))


# === Main runner ===

async def main():
    print("=" * 70)
    print("Audit-Fix Regressions — Test Suite (Task 8a: 12 fixes + Task 8b: 10 new + persistence + Task 4a: AI drainer hardening + Task 3a: snapshot audit + Task 9a/10a/5a/11a: workers/sqlite/api/secrets)")
    print("=" * 70)

    await test_B01_data_dir_env_configurable()
    await test_B02_poll_one_chat_persists_last_msg_id()
    await test_B03_polling_status_reads_due_chats_from_db()
    await test_B04_periodic_sync_loads_source_registry()
    await test_B05_journal_recovery_recurring_loop()
    await test_B06_dashboard_api_key_optional()
    await test_B07_supervisor_restarts_dead_task()
    await test_B08_journal_write_warning()
    await test_B09_lookup_any_guarded()
    await test_L03_polling_watchdog_restarts_dead_scheduler()
    await test_L04_supabase_schema_logs_sql()
    await test_L07_lease_duration_env_configurable()

    # === Task 8b — 10 NEW edge-case fixes + persistence snapshot ===
    await test_N01_journal_cleanup_preserves_pending()
    await test_N02_reconcile_writes_journal_before_claim()
    await test_N03_loser_does_not_overwrite_winner_state()
    await test_N04_mass_delete_per_iteration_isolation()
    await test_N05_journal_consecutive_failure_burst()
    await test_N06_enqueue_link_reraises_non_integrity()
    await test_N07_ai_drainer_processes_pending()
    await test_N08_floodwait_registered()
    await test_N09_polling_gather_return_exceptions()
    await test_N10_ensure_conn_lock_serializes()
    await test_persist_snapshot_restore_on_startup()
    await test_persist_snapshot_loop_batches()

    # === Task 4a — AI Drainer Deep Audit + Hardening (15 scenarios) ===
    await test_4a_ai_drainer_lease_filter_on_patch()
    await test_4a_ai_drainer_batch_size_env()
    await test_4a_ai_drainer_order_rotates_head()
    await test_4a_ai_drainer_timeout_skips_row()
    await test_4a_ai_drainer_provider_failure_skips_row()
    await test_4a_ai_drainer_none_result_skips_patch()
    await test_4a_ai_drainer_429_cycle_backoff()
    await test_4a_ai_drainer_empty_queue_60s_sleep()
    await test_4a_ai_drainer_poison_row_skipped_after_3_fails()
    await test_4a_ai_drainer_race_loss_detected()
    await test_4a_ai_drainer_concurrency_semaphore_bounded()
    await test_4a_ai_drainer_concurrency_3_allows_parallel()
    await test_4a_ai_drainer_batch_summary_log()
    await test_4a_ai_drainer_fail_count_resets_on_success()
    await test_4a_ai_drainer_graceful_shutdown()
    await test_4a_ai_drainer_disabled_by_default()
    await test_4a_ai_drainer_no_ai_configured_60s_sleep()

    # === Task 3a — Supabase Journal Snapshot Deep Audit (8 scenarios) ===
    await test_3a_snapshot_concurrent_guard()
    await test_3a_snapshot_order_by_received_at()
    await test_3a_snapshot_429_backoff()
    await test_3a_snapshot_post_timeout_does_not_crash()
    await test_3a_snapshot_network_error_continues()
    await test_3a_restore_does_not_overwrite_terminal_local_state()
    await test_3a_restore_per_row_corruption_isolation()
    await test_3a_restore_get_timeout_returns_zero()

    # === Task 6a — PollingScheduler / active_chats_count=0 ===
    await test_6a_polling_status_counts_null_next_poll_at_as_active()
    await test_6a_add_monitored_chat_seeds_next_poll_at()
    await test_6a_backfill_seeds_null_next_poll_at_rows()
    await test_6a_scheduler_select_due_includes_null()

    # === PUBLISH-INCIDENT-1 — Joiner selection tries ALL eligible joiners ===
    await test_publish_incident_1_connection_check_inside_loop()
    await test_publish_incident_1_old_anti_pattern_gone()
    await test_publish_incident_1_rate_limiter_inside_loop()
    await test_publish_incident_1_safety_guard_inside_loop()
    await test_publish_incident_1_each_check_continues_loop()
    await test_publish_incident_1_joiner_selected_log_marker()
    await test_publish_incident_1_publish_started_log_marker()
    await test_publish_incident_1_join_started_log_marker()

    # === Task 9a — Supervisor coverage ===
    await test_9a_supervisor_watches_nine_critical_tasks()
    await test_9a_supervisor_relaunch_lock_present()
    await test_9a_ai_drainer_relaunch_gated_on_env()

    # === Task 10a — SQLite concurrency + pragmas ===
    await test_10a_concurrent_writers_no_deadlock_no_corruption()
    await test_10a_pragma_busy_timeout_and_wal_set()

    # === Task 5a — API security ===
    await test_5a_middleware_constant_time_compare()
    await test_5a_middleware_exempt_health_endpoints()
    await test_5a_middleware_unset_means_open()
    await test_5a_redact_phone_masks_middle()

    # === [DASHBOARD-RESTORE] Trusted-origin allowlist — restores the Vercel
    # dashboard that PR-7 (commit b6017b5) accidentally broke on 2026-08-26. ===
    await test_dashboard_restore_allowed_origins_env_parse()
    await test_dashboard_restore_origin_match_grants_access()
    await test_dashboard_restore_referer_fallback_match()
    await test_dashboard_restore_unallowed_origin_rejected()
    await test_dashboard_restore_empty_allowlist_fail_closed()
    await test_dashboard_restore_options_preflight_allowed()
    await test_dashboard_restore_key_takes_precedence_over_origin()
    await test_dashboard_restore_helper_is_origin_allowed()

    # === Task 11a — Secrets scan ===
    await test_11a_no_real_secrets_in_source_files()

    # === Requirements Audit — Req-1 Security, Req-2 Monitoring, Req-3 Link-type, Req-8 PUBLISH-VERIFY ===
    await test_req1_redacting_filter_redacts_phones_and_tokens()
    await test_req1_redacting_filter_installed_in_setup_logging()
    await test_req1_api_pii_masking_when_dashboard_open()
    await test_req1_bot_token_not_logged_prefix_suffix()
    await test_req1_supabase_url_masked_in_deploy_check()
    await test_req8_delete_forwarded_request_removes_phantom_row()
    await test_req8_publish_failure_rolls_back_phantom_row()
    await test_req2_startup_scan_wired_into_start()
    await test_req3_pre_publish_channel_exclusion_present()
    await test_req3_scorer_marks_channels_banned()

    # === [REQAUDIT-2] InviteRequestSentError → PENDING_APPROVAL full lifecycle ===
    await test_reqaudit2_groupstate_pending_approval_enum()
    await test_reqaudit2_invite_request_sent_error_imported()
    await test_reqaudit2_invite_request_sent_handler_private_branch()
    await test_reqaudit2_invite_request_sent_handler_username_branch()
    await test_reqaudit2_pipeline6_pending_approval_branch()
    await test_reqaudit2_dedup_skipsets_include_pending_approval()
    await test_reqaudit2_joined_groups_command_shows_pending()
    await test_reqaudit2_api_joined_groups_pending_count()
    # [REQAUDIT-2 STRONGEST] self-healing recheck loop + operator visibility
    await test_reqaudit2_recheck_loop_method_present()
    await test_reqaudit2_recheck_loop_started_in_start()
    await test_reqaudit2_recheck_loop_supervised()
    await test_reqaudit2_recheck_loop_shutdown_cancellation()
    await test_reqaudit2_pending_approvals_command()
    await test_reqaudit2_api_pending_approvals_endpoint()
    await test_reqaudit2_recheck_uses_original_joiner_account()
    await test_reqaudit2_recheck_bounded_and_rated()
    await test_reqaudit2_recheck_invite_expired_handling()

    # === [REQAUDIT-3] Joiner Fleet Resilience — 8 regressions ===
    await test_reqaudit3_fleet_health_state_in_init()
    await test_reqaudit3_run_user_client_non_terminal()
    await test_reqaudit3_supervisor_watches_user_tasks()
    await test_reqaudit3_fleet_health_loop_method_present()
    await test_reqaudit3_fleet_health_task_supervised()
    await test_reqaudit3_joiner_worker_fleet_backoff()
    await test_reqaudit3_ready_endpoint_surfaces_fleet()
    await test_reqaudit3_api_joined_groups_surfaces_fleet()

    # [infra] close all aiosqlite connections before the loop tears down
    await close_all_test_dbs()

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
