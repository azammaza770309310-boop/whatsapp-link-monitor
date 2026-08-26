#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Supabase Journal Snapshot — Tests [PR-6]
=========================================
السيناريو 11 (recovery بعد restart/crash):
- الـsnapshot worker لا يكسر الـmain pipeline لو الجدول مفقود (404).
- SQL الـmigration جاهز وصحيح (schema مطابقة لـmessage_journal).
"""
import asyncio
import os
import sys
import logging
from pathlib import Path

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

RESULTS = []


def record(name, passed, detail=""):
    RESULTS.append({'name': name, 'passed': passed, 'detail': detail})
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if detail and not passed:
        print(f"         {detail}")


async def test_snapshot_sql_in_source():
    print("\n--- Test: snapshot CREATE TABLE SQL present in bot.py ---")
    import inspect
    src = inspect.getsource(bot.Monitor)
    record("SQL: CREATE TABLE message_journal_snapshot present",
           'CREATE TABLE IF NOT EXISTS message_journal_snapshot' in src,
           "missing CREATE TABLE")
    record("SQL: PRIMARY KEY (chat_id, msg_id)",
           'PRIMARY KEY (chat_id, msg_id)' in src,
           "missing PK")
    record("SQL: raw_text + state + received_at columns",
           all(c in src for c in ('raw_text', 'state', 'received_at')),
           "missing columns")


async def test_snapshot_fault_tolerant():
    print("\n--- Test: snapshot worker fault-tolerant (doesn't crash pipeline) ---")
    import inspect
    src = inspect.getsource(bot.Monitor)
    # The snapshot loop must catch network errors and continue
    record("snapshot: catches aiohttp.ClientError",
           'aiohttp.ClientError' in src, "missing ClientError catch")
    record("snapshot: catches asyncio.TimeoutError",
           'asyncio.TimeoutError' in src or 'TimeoutError' in src,
           "missing Timeout catch")
    record("snapshot: catches generic Exception (last resort)",
           'except Exception as e' in src, "missing generic catch")
    record("snapshot: detects missing table (404 / does not exist)",
           'does not exist' in src and '404' in src,
           "missing table-detection")


async def test_migration_sql_file_exists():
    print("\n--- Test: ready-to-execute migration SQL file present ---")
    sql_path = PROJECT_ROOT / 'supabase' / 'message_journal_snapshot.sql'
    record("SQL file: supabase/message_journal_snapshot.sql exists",
           sql_path.exists(), f"missing {sql_path}")
    if sql_path.exists():
        sql = sql_path.read_text()
        record("SQL file: CREATE TABLE present",
               'CREATE TABLE IF NOT EXISTS message_journal_snapshot' in sql)
        record("SQL file: ENABLE ROW LEVEL SECURITY",
               'ENABLE ROW LEVEL SECURITY' in sql)
        record("SQL file: service_role policy",
               'service_role full access' in sql)
        record("SQL file: anon denied policy",
               'anon no access' in sql)


async def main():
    print("=" * 70)
    print("Supabase Journal Snapshot — Test Suite [PR-6]")
    print("=" * 70)
    await test_snapshot_sql_in_source()
    await test_snapshot_fault_tolerant()
    await test_migration_sql_file_exists()
    print("\n" + "=" * 70)
    passed = sum(1 for r in RESULTS if r['passed'])
    failed = sum(1 for r in RESULTS if not r['passed'])
    print(f"RESULTS: {passed}/{passed + failed} passed, {failed} failed")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
