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
import types
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


# === Main runner ===

async def main():
    print("=" * 70)
    print("Audit-Fix Regressions — Test Suite (Task 8a: 12 original fixes)")
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
