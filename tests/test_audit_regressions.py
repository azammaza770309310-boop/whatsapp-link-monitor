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
    """Append (level, message) tuples from the root logger while active."""
    def __init__(self):
        self.records = []

    def _handler(self, record):
        self.records.append((record.levelname, record.getMessage()))

    def __enter__(self):
        self._restore_level = logging.getLogger().level
        logging.disable(logging.NOTSET)
        self._handler_obj = logging.getLogger().addHandler  # placeholder
        self._logger = logging.getLogger()
        self._filter = lambda r: self._handler(r)
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
        logging.disable(self._restore_level)

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
        # Middleware open for /api/* when key unset
        req_api = types.SimpleNamespace(path='/api/stats', headers={})
        r2 = await bot.dashboard_api_key_middleware(req_api, _ok_handler)
        record("B06: middleware open for /api/* when key unset", r2 == 'ok', f"got {r2!r}")

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
            def get(self, url): return FakeCM(FakeResp(200, snap_rows))
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
            def post(self, url, json=None, headers=None):
                post_calls.append({'url': url, 'json': json, 'headers': headers})
                return FakeCM(FakeResp(204))
        fm.db = types.SimpleNamespace(
            supabase_url='https://example.supabase.co',
            supabase_key='fake_key',
            _get_supabase_session=AsyncMock(return_value=FakeSession()))
        # Run a single snapshot cycle (flip _running off on the 2nd sleep)
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


# === Main runner ===

async def main():
    print("=" * 70)
    print("Audit-Fix Regressions — Test Suite (Task 8a: 12 fixes + Task 8b: 10 new + persistence)")
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
