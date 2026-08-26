#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fast-Delete Rescue — Evidence Harness (SIMULATION ONLY)
=========================================================
[PR-2 / user-request #1.3] يثبت مسار إنقاذ الرابط عبر LRB عند حذف
الرسالة بسرعات مختلفة. NO Telegram credentials في هذه البيئة، لذا
هذا SIMULATION ONLY (يستدعي نفس دوال الإنتاج bot.Monitor._on_user_message
و _on_message_deleted على namespace مُحاكى مع SQLite حقيقي).

المسار المُختبر (production code path، بدون تعديل):
  1. NewMessage يحتوي رابطًا
     → extract_links (regex نقي)
     → _link_ring_put (LRB فورًا، قبل pre-cache)
  2. (delay ms) — يحاكي زمن وصول DELETE بعد ظهور الرسالة
  3. MessageDeleted
     → _link_ring_pop (LRB hit؟)
     → _rescue_link_only (reconstruct + central dedup + enqueue_link)
  4. Re-fire DELETE لنفس msg_id
     → central dedup يمنع إعادة enqueue

سرعات الحذف المُختبَرة: 100ms, 250ms, 500ms, 1000ms, 2000ms
عدد التجارب لكل سرعة: 5 (إجمالي 25 محاولة)

للحصول على PRODUCTION VERIFIED حقيقي يجب:
  - توفير credentials مراقِب Telegram في Render env
  - نشر SHA الحالي على Render (push→auto-deploy)
  - إنشاء Test Group مملوك لنا
  - تنفيذ Fast-Delete Trial حقيقي

دليل كل تجربة (بلا PII):
  - chat_id, message_id, normalized_link
  - message_received_at, raw_capture_at, LRB_write_at
  - delete_received_at, LRB_hit_at, rescue_at, enqueue_at
  - dedup_result, capture_to_delete_ms, delete_to_rescue_ms, final_result
"""
import asyncio
import os
import sys
import time
import logging
import types
import statistics
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
from link_system import ProductionDB, GroupState, LinkNormalizer  # noqa: E402
from source_registry import MessageClaim  # noqa: E402

RESULTS = []
EVIDENCE_LOG = []  # full per-trial evidence (no PII)


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
    def __init__(self, raw_text, chat_id, msg_id, sender_id=42):
        self.raw_text = raw_text
        self.chat_id = chat_id
        self.id = msg_id
        self.sender_id = sender_id
        self.chat = None
        self.sender = None


class FakeDeleteEvent:
    def __init__(self, ids, chat_id=None):
        self.deleted_ids = list(ids)
        self.chat_id = chat_id


def make_instrumented_monitor(prod_db, channel_id=-1009999999):
    """Build a fake Monitor namespace with REAL bot.Monitor methods + timing
    instrumentation. Each wrapper captures high-resolution perf_counter timestamps
    into a per-trial dict so we can compute end-to-end latencies.
    """
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
        # instrumentation bag — filled by wrappers
        _timing={},
    )
    for method_name in (
        '_journal_enabled', '_journal_write', '_journal_set_state_safe',
        '_journal_mark_deleted_safe', '_record_delete_miss',
        '_rescue_enqueue_links', '_spawn_reconcile',
        '_reconcile_chat_after_delete_miss', '_journal_recovery',
        '_link_ring_put', '_link_ring_pop', '_link_ring_evict',
        '_normalized_to_link_data', '_rescue_link_only',
        '_on_user_message', '_on_message_deleted',
    ):
        setattr(fm, method_name,
                types.MethodType(getattr(bot.Monitor, method_name), fm))

    # Wrap _link_ring_put to capture LRB_write_at
    orig_put = fm._link_ring_put

    async def put_wrapped(chat_id, msg_id, normalized_links):
        t0 = time.perf_counter()
        await orig_put(chat_id, msg_id, normalized_links)
        fm._timing['LRB_write_at'] = t0
        fm._timing['lrb_entries_after_put'] = len(fm._link_ring)

    fm._link_ring_put = put_wrapped

    # Wrap _link_ring_pop to capture LRB_hit_at + whether it hit
    orig_pop = fm._link_ring_pop

    async def pop_wrapped(chat_id, msg_id):
        t0 = time.perf_counter()
        out = await orig_pop(chat_id, msg_id)
        fm._timing['LRB_hit_at'] = t0
        fm._timing['LRB_hit'] = bool(out)
        return out

    fm._link_ring_pop = pop_wrapped

    # Wrap _rescue_link_only to capture rescue_at + dedup_result
    orig_rescue = fm._rescue_link_only

    async def rescue_wrapped(chat_id, msg_id, source_phone, normalized_links):
        t0 = time.perf_counter()
        new_count = await orig_rescue(chat_id, msg_id, source_phone, normalized_links)
        fm._timing['rescue_at'] = t0
        fm._timing['rescue_new_count'] = new_count
        fm._timing['dedup_result'] = 'NEW' if new_count > 0 else 'DUPLICATE_OR_KNOWN'
        return new_count

    fm._rescue_link_only = rescue_wrapped

    return fm


async def run_single_trial(delay_ms: int, trial_idx: int, chat_id_base: int):
    """Run a single Fast-Delete trial and return an evidence dict (no PII)."""
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_instrumented_monitor(prod_db)
        chat = chat_id_base + trial_idx
        msg_id = 100000 + trial_idx * 10 + delay_ms
        # unique link per trial so dedup doesn't collide across trials
        link_username = f"FastDeleteSimTrial{delay_ms}_{trial_idx}"
        raw_text = f"جديد https://t.me/{link_username}"

        evidence = {
            'trial': f"{delay_ms}ms#{trial_idx}",
            'chat_id': chat,
            'message_id': msg_id,
            'normalized_link': f"tg:user:{link_username.lower()}",
            'delay_ms': delay_ms,
        }

        # === 1. NewMessage fire ===
        t_msg_received = time.perf_counter()
        fm._timing.clear()
        ev = FakeNewMessageEvent(raw_text, chat, msg_id)
        await fm._on_user_message(ev, '+SIM_SOURCE')
        t_after_new = time.perf_counter()

        # extract_links runs synchronously inside _on_user_message; LRB_write_at
        # is captured by the wrapper. raw_capture_at ≈ immediately after extract.
        evidence['message_received_at'] = round(t_msg_received, 6)
        evidence['raw_capture_at'] = round(t_after_new, 6)
        evidence['LRB_write_at'] = round(fm._timing.get('LRB_write_at', 0), 6)
        evidence['LRB_has_link'] = (chat, msg_id) in fm._link_ring
        evidence['capture_to_extract_ms'] = round(
            (fm._timing.get('LRB_write_at', t_after_new) - t_msg_received) * 1000, 3)

        # === 2. Simulate Telegram delivery delay before DELETE arrives ===
        await asyncio.sleep(delay_ms / 1000.0)

        # === 3. MessageDeleted fire ===
        t_delete_received = time.perf_counter()
        fm._timing.clear()  # clear so pop/rescue timestamps reflect DELETE phase only
        # preserve LRB_has_link from before clear (already in evidence)
        ev_del = FakeDeleteEvent([msg_id], chat)
        await fm._on_message_deleted(ev_del, '+SIM_SOURCE')
        t_after_delete = time.perf_counter()

        evidence['delete_received_at'] = round(t_delete_received, 6)
        evidence['LRB_hit_at'] = round(fm._timing.get('LRB_hit_at', 0), 6)
        evidence['LRB_hit'] = fm._timing.get('LRB_hit', False)
        evidence['rescue_at'] = round(fm._timing.get('rescue_at', 0), 6)
        evidence['dedup_result'] = fm._timing.get('dedup_result', 'MISSED')
        evidence['enqueue_at'] = round(fm._timing.get('rescue_at', 0), 6)

        # verify link_queue has exactly 1 row for this normalized_link
        cur = await conn.execute(
            "SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
            (evidence['normalized_link'],))
        cnt = (await cur.fetchone())[0]
        evidence['queue_count_after_delete'] = cnt

        # === 4. Re-fire DELETE (dedup must prevent re-enqueue) ===
        fm._timing.clear()
        # LRB was already popped in step 3 — so LRB is empty now.
        # This re-fire should produce a DELETE-MISS (no LRB hit) but NOT enqueue
        # because the link is already in link_queue (central dedup).
        ev_del2 = FakeDeleteEvent([msg_id], chat)
        await fm._on_message_deleted(ev_del2, '+SIM_SOURCE')
        cur2 = await conn.execute(
            "SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
            (evidence['normalized_link'],))
        cnt2 = (await cur2.fetchone())[0]
        evidence['queue_count_after_refire'] = cnt2

        # latency computations
        evidence['capture_to_delete_ms'] = round(
            (t_delete_received - t_msg_received) * 1000, 3)
        evidence['delete_to_rescue_ms'] = round(
            (fm._timing.get('rescue_at', t_after_delete) - t_delete_received) * 1000, 3)

        # final verdict
        if evidence['LRB_hit'] and cnt == 1 and cnt2 == 1:
            evidence['final_result'] = 'RESCUED_ONCE'
        elif not evidence['LRB_hit']:
            evidence['final_result'] = 'MISSED'
        elif cnt2 > 1:
            evidence['final_result'] = 'DUPLICATE_LEAK'
        else:
            evidence['final_result'] = 'UNKNOWN'

        return evidence
    finally:
        await conn.close()
        try:
            os.remove(db_path)
        except Exception:
            pass


async def test_fast_delete_rescue_simulation():
    print("\n--- Fast-Delete Rescue SIMULATION (5 delays × 5 trials = 25 attempts) ---")
    delays = [100, 250, 500, 1000, 2000]
    trials_per_delay = 5
    all_evidence = []
    for delay_ms in delays:
        for i in range(trials_per_delay):
            ev = await run_single_trial(delay_ms, i, chat_id_base=-1002000000)
            all_evidence.append(ev)
            EVIDENCE_LOG.append(ev)
            status_icon = "✅" if ev['final_result'] == 'RESCUED_ONCE' else "❌"
            print(f"  {status_icon} {ev['trial']}: "
                  f"capture→delete={ev['capture_to_delete_ms']:.1f}ms "
                  f"delete→rescue={ev['delete_to_rescue_ms']:.3f}ms "
                  f"LRB_hit={ev['LRB_hit']} "
                  f"queue(after_del={ev['queue_count_after_delete']},"
                  f"after_refire={ev['queue_count_after_refire']}) "
                  f"→ {ev['final_result']}")

    # === Aggregate stats ===
    attempts = len(all_evidence)
    captures = sum(1 for e in all_evidence if e['LRB_has_link'])
    lrb_hits = sum(1 for e in all_evidence if e['LRB_hit'])
    rescues = sum(1 for e in all_evidence if e['final_result'] == 'RESCUED_ONCE')
    misses = sum(1 for e in all_evidence if e['final_result'] == 'MISSED')
    duplicates = sum(1 for e in all_evidence
                     if e['queue_count_after_refire'] > e['queue_count_after_delete'])
    capture_rate = (captures / attempts * 100) if attempts else 0
    rescue_rate = (rescues / attempts * 100) if attempts else 0
    delete_to_rescue_latencies = [e['delete_to_rescue_ms'] for e in all_evidence
                                  if e['LRB_hit']]
    if delete_to_rescue_latencies:
        median_lat = statistics.median(delete_to_rescue_latencies)
        sorted_lats = sorted(delete_to_rescue_latencies)
        p95_idx = max(0, int(0.95 * len(sorted_lats)) - 1)
        p95_lat = sorted_lats[p95_idx]
        mean_lat = statistics.mean(delete_to_rescue_latencies)
    else:
        median_lat = p95_lat = mean_lat = 0.0

    print("\n" + "=" * 70)
    print("Fast-Delete Rescue — Aggregate Report (SIMULATION ONLY)")
    print("=" * 70)
    print(f"  attempts              : {attempts}")
    print(f"  captures (LRB put)    : {captures}")
    print(f"  LRB hits on DELETE    : {lrb_hits}")
    print(f"  rescues (once)        : {rescues}")
    print(f"  misses                : {misses}")
    print(f"  duplicate leaks       : {duplicates}")
    print(f"  capture rate          : {capture_rate:.1f}%")
    print(f"  rescue rate           : {rescue_rate:.1f}%")
    print(f"  delete→rescue median  : {median_lat:.3f}ms")
    print(f"  delete→rescue mean    : {mean_lat:.3f}ms")
    print(f"  delete→rescue p95     : {p95_lat:.3f}ms")
    print("=" * 70)

    # === Hard assertions ===
    record("ALL 25 trials rescued exactly once",
           rescues == attempts and misses == 0,
           f"rescues={rescues} misses={misses}")
    record("ALL 25 trials captured link into LRB",
           captures == attempts, f"captures={captures}/{attempts}")
    record("ALL 25 LRB hits on DELETE",
           lrb_hits == attempts, f"lrb_hits={lrb_hits}/{attempts}")
    record("NO duplicate leaks on re-fire DELETE",
           duplicates == 0, f"duplicates={duplicates}")
    record("capture rate = 100%",
           capture_rate == 100.0, f"capture_rate={capture_rate:.1f}%")
    record("rescue rate = 100%",
           rescue_rate == 100.0, f"rescue_rate={rescue_rate:.1f}%")
    record("delete→rescue median < 50ms (in-process)",
           median_lat < 50, f"median={median_lat:.3f}ms")
    record("delete→rescue p95 < 100ms (in-process)",
           p95_lat < 100, f"p95={p95_lat:.3f}ms")

    # === Per-delay breakdown ===
    print("\n  Per-delay breakdown:")
    for delay_ms in delays:
        sub = [e for e in all_evidence if e['delay_ms'] == delay_ms]
        sub_rescued = sum(1 for e in sub if e['final_result'] == 'RESCUED_ONCE')
        sub_lats = [e['delete_to_rescue_ms'] for e in sub if e['LRB_hit']]
        sub_med = statistics.median(sub_lats) if sub_lats else 0
        print(f"    {delay_ms}ms: {sub_rescued}/{len(sub)} rescued, "
              f"median delete→rescue = {sub_med:.3f}ms")
    print()


async def test_link_only_rescue_without_metadata():
    """[user #2 Link-Only Architecture] LRB يحتوي روابط فقط — بدون raw_text،
    بدون metadata — لكن الإنقاذ ينجح. مبدأ: الرابط أهم من الرسالة."""
    print("\n--- Link-Only Rescue (no raw_text, no metadata, no sender) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_instrumented_monitor(prod_db)
        chat = -1007777000
        msg_id = 77001
        # Directly inject into LRB only — simulating Raw hook captured link
        # but message was deleted BEFORE any NewMessage fired.
        # So: no _msg_cache entry, no journal row, no sender, no chat title.
        await fm._link_ring_put(chat, msg_id, ['tg:user:linkonlytest'])

        # Sanity: LRB has the link, _msg_cache is empty
        record("LRB has the link (link-only, no metadata)",
               (chat, msg_id) in fm._link_ring,
               "LRB missing")
        record("_msg_cache is empty (no NewMessage fired)",
               len(fm._msg_cache) == 0,
               f"cache size={len(fm._msg_cache)}")

        # Fire DELETE — only LRB has the link
        ev_del = FakeDeleteEvent([msg_id], chat)
        await fm._on_message_deleted(ev_del, '+SIM_SOURCE')

        cur = await conn.execute(
            "SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
            ('tg:user:linkonlytest',))
        cnt = (await cur.fetchone())[0]
        record("Link-only rescue enqueued the link",
               cnt == 1, f"got {cnt}")
        record("LRB hit metric fired",
               fm.metrics.record_link_ring_hit.called, "not called")
        record("delete_rescued('link_ring') metric fired",
               fm.metrics.record_delete_rescued.called, "not called")
        record("delete_miss metric NOT fired (LRB rescued despite no metadata)",
               not fm.metrics.record_delete_miss.called,
               "delete_miss fired erroneously")
    finally:
        await conn.close()
        try:
            os.remove(db_path)
        except: pass


async def test_raw_and_newmessage_dedup():
    """[user #3] Raw hook + NewMessage لنفس الرسالة → لا duplicate.
    كل من Raw و NewMessage يكتب LRB لنفس المفتاح (chat_id, msg_id) —
    لكن dict overwrite يعني المفتاح نفسه يحتفظ بقيمة واحدة فقط.
    وعند DELETE، pop يُرجع قائمة واحدة فقط → rescue واحد فقط."""
    print("\n--- Raw + NewMessage dedup (same msg_id → 1 rescue) ---")
    prod_db, db_path, conn = await make_test_db()
    try:
        fm = make_instrumented_monitor(prod_db)
        chat = -1008888000
        msg_id = 88001
        # 1. Raw hook fires first (writes LRB)
        await fm._link_ring_put(chat, msg_id, ['tg:user:rawplusnew'])
        # 2. NewMessage fires (writes LRB again — overwrite, no duplicate)
        await fm._link_ring_put(chat, msg_id, ['tg:user:rawplusnew'])
        record("LRB has exactly 1 entry after Raw + NewMessage",
               len(fm._link_ring) == 1, f"size={len(fm._link_ring)}")
        # 3. Fire DELETE
        ev_del = FakeDeleteEvent([msg_id], chat)
        await fm._on_message_deleted(ev_del, '+SIM_SOURCE')
        cur = await conn.execute(
            "SELECT COUNT(*) FROM link_queue WHERE normalized_link=?",
            ('tg:user:rawplusnew',))
        cnt = (await cur.fetchone())[0]
        record("only 1 enqueue despite Raw + NewMessage both writing LRB",
               cnt == 1, f"got {cnt}")
    finally:
        await conn.close()
        try:
            os.remove(db_path)
        except: pass


async def main():
    print("=" * 70)
    print("Fast-Delete Rescue — Evidence Harness [SIMULATION ONLY]")
    print("=" * 70)
    print("⚠️  NO Telegram credentials in this environment — this is SIMULATION.")
    print("    It exercises the REAL bot.Monitor._on_user_message +")
    print("    _on_message_deleted production code paths on an in-process")
    print("    SQLite test DB. For PRODUCTION VERIFIED, push the SHA + run")
    print("    a real Fast-Delete trial against a Test Group we own.")
    print("=" * 70)
    await test_fast_delete_rescue_simulation()
    await test_link_only_rescue_without_metadata()
    await test_raw_and_newmessage_dedup()
    print("\n" + "=" * 70)
    passed = sum(1 for r in RESULTS if r['passed'])
    failed = sum(1 for r in RESULTS if not r['passed'])
    print(f"RESULTS: {passed}/{passed + failed} passed, {failed} failed")
    print("=" * 70)
    # Print evidence summary line (no PII — all are test usernames)
    print(f"\nEvidence trials recorded: {len(EVIDENCE_LOG)} (see EVIDENCE_LOG list)")
    print(f"All trials final_result breakdown: " +
          ', '.join(f"{k}={sum(1 for e in EVIDENCE_LOG if e['final_result']==k)}"
                    for k in ('RESCUED_ONCE', 'MISSED', 'DUPLICATE_LEAK', 'UNKNOWN')))
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
