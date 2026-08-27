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


async def test_snapshot_migration_pointer_in_bot():
    print("\n--- Test: bot.py points user to migration file (not inline SQL) ---")
    import inspect
    src = inspect.getsource(bot.Monitor)
    # bot.py should reference the migration file path so users run the canonical SQL
    record("bot.py: references supabase/message_journal_snapshot.sql migration",
           'supabase/message_journal_snapshot.sql' in src
           or 'message_journal_snapshot.sql' in src,
           "missing migration file pointer")
    # bot.py must warn that CREATE POLICY IF NOT EXISTS is unsupported by Postgres
    record("bot.py: warns CREATE POLICY IF NOT EXISTS unsupported",
           'CREATE POLICY IF NOT EXISTS' in src,
           "missing CREATE POLICY IF NOT EXISTS warning")
    # bot.py must recommend the DROP-then-CREATE idempotent pattern
    record("bot.py: recommends DROP POLICY IF EXISTS then CREATE POLICY",
           'DROP POLICY IF EXISTS' in src,
           "missing DROP POLICY IF EXISTS recommendation")


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
    print("\n--- Test: ready-to-execute migration SQL file present + correct ---")
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
        # Regression guard: Postgres does NOT support CREATE POLICY IF NOT EXISTS.
        # The migration MUST use DROP POLICY IF EXISTS then CREATE POLICY instead.
        record("SQL file: NO 'CREATE POLICY IF NOT EXISTS' (Postgres unsupported)",
               'CREATE POLICY IF NOT EXISTS' not in sql,
               "regression: CREATE POLICY IF NOT EXISTS reintroduced")
        record("SQL file: uses DROP POLICY IF EXISTS (idempotent pattern)",
               'DROP POLICY IF EXISTS' in sql,
               "missing DROP POLICY IF EXISTS")
        record("SQL file: has index on state",
               'idx_journal_snapshot_state' in sql)
        record("SQL file: has index on received_at",
               'idx_journal_snapshot_received_at' in sql)
        # PRIMARY KEY already creates a unique index on (chat_id, msg_id);
        # a separate CREATE UNIQUE INDEX on the same columns is redundant.
        record("SQL file: NO redundant unique index on PK columns",
               'CREATE UNIQUE INDEX IF NOT EXISTS idx_journal_snapshot_pk' not in sql,
               "regression: redundant unique index on PK columns")


async def main():
    print("=" * 70)
    print("Supabase Journal Snapshot — Test Suite [PR-6]")
    print("=" * 70)
    await test_snapshot_migration_pointer_in_bot()
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
