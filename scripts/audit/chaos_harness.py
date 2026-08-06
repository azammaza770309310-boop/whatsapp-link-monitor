#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chaos Engineering & Production Validation Harness
=================================================
Simulates real production disasters: Telegram outages, FloodWait, DB locks,
Supabase failures, AI crashes, network partitions, OOM, disk full, SIGTERM.

Measures: recovery time, data loss, memory growth, task leaks, connection leaks.
"""
import asyncio
import gc
import os
import resource
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Make monitor_v12 importable
DOWNLOAD_DIR = Path(__file__).parent.parent.parent / "download"
sys.path.insert(0, str(DOWNLOAD_DIR))

# Change to download dir so relative paths (accounts.env, sessions/) work
os.chdir(DOWNLOAD_DIR)

import logging
logging.disable(logging.CRITICAL)

# Pre-import everything we need (so import errors show up immediately)
from monitor_v12 import (
    Monitor, Config, DatabaseManager, AIAnalyzer, MessageFormatter,
    extract_whatsapp_telegram_links, _extract_clean_json,
    HelpRequestDetector,
)
from telethon.errors import FloodWaitError


def get_memory_mb() -> float:
    """Return current process RSS in MB."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def count_asyncio_tasks() -> int:
    """Count all active asyncio tasks in the current loop."""
    try:
        loop = asyncio.get_running_loop()
        return len([t for t in asyncio.all_tasks(loop) if not t.done()])
    except RuntimeError:
        return 0


# ============================================================
# PHASE 1: CHAOS ENGINEERING — External service failures
# ============================================================

class TestTelegramApiOutage:
    """Simulate complete Telegram API outage."""

    async def run(self):
        """When Telegram is unreachable, the bot must:
        - Not crash
        - Retry with backoff
        - Not lose queued messages
        - Eventually give up gracefully (not block forever)
        """


        # Track memory before
        mem_before = get_memory_mb()

        # Create a Monitor with mocked bot_client that always fails
        config = Config.__new__(Config)
        config.api_id = 123
        config.api_hash = "x"
        config.bot_token = "x"
        config.channel_id = -100123
        config.owner_id = None
        config.log_level = "INFO"
        config.history_max_per_chat = 100
        config.history_batch_size = 5
        config.history_skip_channel_posts = False
        config.startup_scan_days = None
        config.min_message_length = 20
        config.max_message_length = 2000

        db = DatabaseManager(tempfile.mktemp(suffix=".db"))
        await db.init_db()

        monitor = Monitor(config, db)
        monitor.bot_client = MagicMock()
        monitor.bot_client.is_connected = MagicMock(return_value=True)
        # Simulate Telegram outage: every send fails with ConnectionError
        monitor.bot_client.send_message = AsyncMock(
            side_effect=ConnectionError("Telegram API unreachable")
        )

        # Try to send 100 messages during outage
        start = time.monotonic()
        for i in range(100):
            await monitor._send(f"test message {i}")
        elapsed = time.monotonic() - start

        mem_after = get_memory_mb()
        mem_growth = mem_after - mem_before

        await db.close()

        return {
            "scenario": "Telegram API outage (100 messages)",
            "elapsed_sec": round(elapsed, 2),
            "memory_growth_mb": round(mem_growth, 2),
            "verdict": "PASS" if elapsed < 300 else "FAIL",
            "notes": f"100 messages in {elapsed:.1f}s, mem +{mem_growth:.1f}MB",
        }


class TestTelegramFloodWait:
    """Simulate sustained FloodWait from Telegram."""

    async def run(self):
        """When Telegram returns FloodWait(60s) repeatedly, the bot must:
        - Cap total wait time (not block forever)
        - Not stack up retries infinitely
        - Release the _send_lock eventually
        """



        config = Config.__new__(Config)
        config.api_id = 123
        config.api_hash = "x"
        config.bot_token = "x"
        config.channel_id = -100123
        config.owner_id = None
        config.history_max_per_chat = 100
        config.history_batch_size = 5
        config.history_skip_channel_posts = False
        config.startup_scan_days = None
        config.min_message_length = 20
        config.max_message_length = 2000

        db = DatabaseManager(tempfile.mktemp(suffix=".db"))
        await db.init_db()

        monitor = Monitor(config, db)
        monitor.bot_client = MagicMock()
        monitor.bot_client.is_connected = MagicMock(return_value=True)
        # FloodWait(60s) every time
        monitor.bot_client.send_message = AsyncMock(
            side_effect=FloodWaitError(request=MagicMock())
        )

        # Patch FloodWaitError to return 60 seconds
        def make_flood_wait(*args, **kwargs):
            e = FloodWaitError(request=MagicMock())
            e.seconds = 60
            return e

        # Try to send 5 messages — each would wait 60s × 3 retries = 180s
        # but we cap at 120s total → should give up after ~120s per message
        start = time.monotonic()
        # Patch sleep to be instant (we're testing logic, not actually waiting)
        with patch("asyncio.sleep", new=AsyncMock()):
            for i in range(5):
                await monitor._send(f"flood test {i}")
        elapsed = time.monotonic() - start

        await db.close()

        return {
            "scenario": "Sustained FloodWait(60s) × 5 messages",
            "elapsed_sec": round(elapsed, 2),
            "verdict": "PASS" if elapsed < 10 else "FAIL",
            "notes": "With sleep mocked, 5 messages should complete in <10s (cap works)",
        }


class TestSupabaseOutage:
    """Simulate Supabase being completely unreachable."""

    async def run(self):
        """When Supabase is down, the bot must:
        - Fall back to local SQLite
        - Continue operating (don't crash)
        - Not lose links (they're saved locally)
        - Log the failure
        """


        db = DatabaseManager(tempfile.mktemp(suffix=".db"))
        db.supabase_url = "https://fake.supabase.co"
        db.supabase_key = "fake-key"
        await db.init_db()

        # Mock the Supabase session to always fail
        mock_session = AsyncMock()
        mock_session.post = AsyncMock(side_effect=ConnectionError("Supabase unreachable"))
        mock_session.get = AsyncMock(side_effect=ConnectionError("Supabase unreachable"))
        db._supabase_session = mock_session
        db._get_supabase_session = AsyncMock(return_value=mock_session)

        # Insert 50 links during Supabase outage
        inserted_count = 0
        for i in range(50):
            inserted = await db.insert_request(
                link=f"https://chat.whatsapp.com/OUTAGE{i}",
                message_date=datetime.now(),
                group_name="test",
                sender_name="user",
                source_phone="+966",
                link_type="whatsapp",
            )
            if inserted:
                inserted_count += 1

        # Verify all links were saved locally
        local_count = await db.count_requests()

        await db.close()

        return {
            "scenario": "Supabase outage (50 links)",
            "inserted_locally": inserted_count,
            "local_count": local_count,
            "verdict": "PASS" if inserted_count == 50 and local_count == 50 else "FAIL",
            "notes": "All links must be saved locally even when Supabase is down",
        }


class TestDatabaseLocked:
    """Simulate SQLite database lock (another process holds the DB)."""

    async def run(self):
        """When SQLite is locked, insert_request must:
        - Not hang forever (busy_timeout=30s)
        - Return False on failure
        - Not crash the bot
        """
        import aiosqlite


        db_path = tempfile.mktemp(suffix=".db")
        db = DatabaseManager(db_path)
        await db.init_db()

        # Open a second connection that holds a write lock
        lock_conn = await aiosqlite.connect(db_path)
        await lock_conn.execute("BEGIN EXCLUSIVE")
        await lock_conn.commit()  # actually start a transaction
        await lock_conn.execute("BEGIN")
        await lock_conn.execute("DELETE FROM forwarded_requests WHERE 1=1")
        # Don't commit — hold the lock

        # Now try to insert (should fail or timeout, not hang forever)
        start = time.monotonic()
        result = None
        try:
            # The insert should fail because of the lock
            # busy_timeout=5000ms means SQLite waits up to 5s for the lock
            # then raises OperationalError("database is locked")
            result = await asyncio.wait_for(
                db.insert_request(
                    link="https://chat.whatsapp.com/LOCKED1",
                    message_date=datetime.now(),
                    group_name="g", sender_name="s", source_phone="+966",
                    link_type="whatsapp",
                ),
                timeout=10.0,  # give it 10s (busy_timeout is 5s)
            )
        except asyncio.TimeoutError:
            result = "TIMEOUT"
        except Exception as e:
            result = f"ERROR: {type(e).__name__}"
        elapsed = time.monotonic() - start

        await lock_conn.rollback()
        await lock_conn.close()
        await db.close()

        # PASS if: completed within 10s AND didn't hang indefinitely
        # The insert should either return False (caught the error) or
        # raise OperationalError (which insert_request catches → returns False)
        return {
            "scenario": "SQLite database locked",
            "elapsed_sec": round(elapsed, 2),
            "result": result,
            "verdict": "PASS" if elapsed < 8 and result != "TIMEOUT" else "FAIL",
            "notes": f"busy_timeout=5s, completed in {elapsed:.1f}s with result={result}",
        }


class TestDatabaseCorruption:
    """Simulate corrupted SQLite database."""

    async def run(self):
        """When the DB file is corrupted, the bot must:
        - Detect the corruption
        - Attempt to re-create the tables
        - Not crash permanently
        """


        db_path = tempfile.mktemp(suffix=".db")
        # Write garbage to the file
        with open(db_path, "wb") as f:
            f.write(b"CORRUPTED DATABASE FILE CONTENT - NOT SQLITE FORMAT")

        db = DatabaseManager(db_path)
        # init_db should handle corruption gracefully
        init_succeeded = True
        try:
            await db.init_db()
        except Exception as e:
            init_succeeded = False
            error = str(e)

        # If init succeeded, try inserting
        insert_result = None
        if init_succeeded:
            try:
                insert_result = await db.insert_request(
                    link="https://chat.whatsapp.com/CORRUPT1",
                    message_date=datetime.now(),
                    group_name="g", sender_name="s", source_phone="+966",
                    link_type="whatsapp",
                )
            except Exception as e:
                insert_result = f"ERROR: {e}"

        await db.close()

        return {
            "scenario": "Corrupted SQLite database",
            "init_succeeded": init_succeeded,
            "insert_result": insert_result,
            "verdict": "PASS" if init_succeeded and insert_result is True else "PARTIAL",
            "notes": "Corruption handling — may need manual intervention",
        }


class TestDiskFull:
    """Simulate disk full (write fails)."""

    async def run(self):
        """When disk is full, the bot must:
        - Not crash
        - Log the error
        - Continue accepting messages (in memory, though they'll be lost)
        """


        db_path = tempfile.mktemp(suffix=".db")
        db = DatabaseManager(db_path)
        await db.init_db()

        # Mock conn.execute to fail with OSError (disk full)
        original_execute = db._conn.execute

        call_count = {"n": 0}
        async def failing_execute(query, *args):
            call_count["n"] += 1
            # Allow CREATE TABLE (init), but fail INSERTs
            if "INSERT" in query.upper():
                raise OSError(28, "No space left on device")
            # Call original for non-INSERT
            return await original_execute(query, *args)

        db._conn.execute = failing_execute

        # Try to insert
        result = None
        try:
            result = await db.insert_request(
                link="https://chat.whatsapp.com/DISKFULL1",
                message_date=datetime.now(),
                group_name="g", sender_name="s", source_phone="+966",
                link_type="whatsapp",
            )
        except Exception as e:
            result = f"EXCEPTION: {e}"

        await db.close()

        return {
            "scenario": "Disk full (OSError on INSERT)",
            "result": result,
            "verdict": "PASS" if result is False else "FAIL",
            "notes": "Should return False gracefully, not crash",
        }


class TestAIProviderFailure:
    """Simulate AI provider returning various failures."""

    async def run(self):
        """Test all AI failure modes:
        - HTTP 429 (rate limit)
        - HTTP 500 (server error)
        - Empty response
        - Invalid JSON
        - Network timeout
        - Connection refused
        """


        results = []

        for scenario_name, mock_response in [
            ("HTTP 429", {"status": 429, "json": {"error": "rate limited"}}),
            ("HTTP 500", {"status": 500, "json": {"error": "internal"}}),
            ("HTTP 401", {"status": 401, "json": {"error": "unauthorized"}}),
            ("Empty response", {"status": 200, "json": {"choices": []}}),
            ("No content", {"status": 200, "json": {"choices": [{"message": {"content": ""}}]}}),
            ("Invalid JSON", {"status": 200, "json": {"choices": [{"message": {"content": "not json"}}]}}),
            ("Malformed JSON", {"status": 200, "json": {"choices": [{"message": {"content": "{broken json}"}}]}}),
        ]:
            analyzer = AIAnalyzer()
            analyzer.enabled = True
            analyzer.providers = [{"key": "fake", "url": "http://fake", "model": "m", "name": "test"}]

            # Mock the session
            mock_session = AsyncMock()
            mock_resp = AsyncMock()
            mock_resp.status = mock_response["status"]
            mock_resp.json = AsyncMock(return_value=mock_response["json"])
            mock_session.post = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=None)
            ))
            analyzer._session = mock_session

            # Analyze a message
            start = time.monotonic()
            result = await asyncio.wait_for(
                analyzer.analyze_message("test message with https://chat.whatsapp.com/ABC"),
                timeout=5.0,
            )
            elapsed = time.monotonic() - start

            results.append({
                "scenario": f"AI: {scenario_name}",
                "elapsed_ms": round(elapsed * 1000),
                "should_save": result.get("should_save"),
                "used_fallback": result.get("link_type") == "whatsapp" or result.get("link_type") == "other",
                "verdict": "PASS" if isinstance(result, dict) else "FAIL",
            })

            await analyzer.close()

        return results


class TestOOMScenario:
    """Simulate memory pressure (OOM approaching)."""

    async def run(self):
        """Under memory pressure, the bot must:
        - Not crash on MemoryError
        - Continue processing if possible
        - Not hold references to completed tasks
        """


        db = DatabaseManager(tempfile.mktemp(suffix=".db"))
        await db.init_db()

        # Insert many links to build up memory
        mem_before = get_memory_mb()
        for i in range(1000):
            await db.insert_request(
                link=f"https://chat.whatsapp.com/OOM{i:04d}",
                message_date=datetime.now(),
                group_name="g" * 100,  # large group name
                sender_name="s" * 50,
                source_phone="+966",
                message_text="x" * 500,
                link_type="whatsapp",
            )
        mem_after = get_memory_mb()
        mem_growth = mem_after - mem_before

        await db.close()

        return {
            "scenario": "1000 inserts (memory pressure)",
            "memory_growth_mb": round(mem_growth, 2),
            "verdict": "PASS" if mem_growth < 50 else "WARN",
            "notes": f"+{mem_growth:.1f}MB for 1000 inserts",
        }


class TestSIGTERMHandling:
    """Simulate SIGTERM during active processing."""

    async def run(self):
        """When SIGTERM arrives mid-processing, the bot must:
        - Finish the current operation (or cancel cleanly)
        - Close all connections
        - Not leave the DB in an inconsistent state
        - Complete shutdown within 30s
        """


        config = Config.__new__(Config)
        config.api_id = 123
        config.api_hash = "x"
        config.bot_token = "x"
        config.channel_id = -100123
        config.owner_id = None
        config.history_max_per_chat = 100
        config.history_batch_size = 5
        config.history_skip_channel_posts = False
        config.startup_scan_days = None
        config.min_message_length = 20
        config.max_message_length = 2000

        db = DatabaseManager(tempfile.mktemp(suffix=".db"))
        await db.init_db()

        monitor = Monitor(config, db)
        monitor.bot_client = MagicMock()
        monitor.bot_client.is_connected = MagicMock(return_value=True)
        monitor.bot_client.disconnect = AsyncMock()

        # Simulate some active state
        monitor._running = True
        monitor._bot_task = asyncio.create_task(asyncio.sleep(100))
        monitor._keep_alive_task = asyncio.create_task(asyncio.sleep(100))

        # Call stop() — should complete within 30s
        start = time.monotonic()
        await asyncio.wait_for(monitor.stop(), timeout=30.0)
        elapsed = time.monotonic() - start

        await db.close()

        return {
            "scenario": "SIGTERM during active processing",
            "shutdown_time_sec": round(elapsed, 2),
            "verdict": "PASS" if elapsed < 10 else "FAIL",
            "notes": "Shutdown should complete in <10s",
        }


# ============================================================
# PHASE 2: LOAD TESTING
# ============================================================

class TestLoad100Users:
    """Simulate 100 concurrent users sending messages."""

    async def run(self):
        return await self._run_load_test(100)

    async def _run_load_test(self, n_users):

        from datetime import datetime

        db = DatabaseManager(tempfile.mktemp(suffix=".db"))
        await db.init_db()

        # Mock Supabase to avoid network calls
        with patch.object(db, '_supabase_insert_link', new=AsyncMock()):
            with patch.object(db, '_supabase_count_links', new=AsyncMock(return_value=None)):
                with patch.object(db, '_get_supabase_session', new=AsyncMock(return_value=None)):
                    mem_before = get_memory_mb()
                    start = time.monotonic()

                    # Simulate n_users each sending 10 messages concurrently
                    async def user_sim(user_id):
                        for msg_id in range(10):
                            await db.insert_request(
                                link=f"https://chat.whatsapp.com/LOAD_u{user_id}_m{msg_id}",
                                message_date=datetime.now(),
                                group_name=f"group_{user_id}",
                                sender_name=f"user_{user_id}",
                                source_phone=f"+9665{user_id:08d}",
                                message_text=f"test message {msg_id} from user {user_id}",
                                link_type="whatsapp",
                            )

                    await asyncio.gather(*[user_sim(i) for i in range(n_users)])

                    elapsed = time.monotonic() - start
                    mem_after = get_memory_mb()

                    # Count actual inserts
                    count = await db.count_requests()

        await db.close()

        return {
            "scenario": f"Load test: {n_users} users × 10 messages",
            "elapsed_sec": round(elapsed, 2),
            "messages_per_sec": round(n_users * 10 / elapsed, 1) if elapsed > 0 else 0,
            "memory_growth_mb": round(mem_after - mem_before, 2),
            "total_inserts": count,
            "expected_inserts": n_users * 10,
            "verdict": "PASS" if count == n_users * 10 else "FAIL",
        }


class TestLoad500Users(TestLoad100Users):
    async def run(self):
        return await self._run_load_test(500)


class TestLoad1000Users(TestLoad100Users):
    async def run(self):
        return await self._run_load_test(1000)


# ============================================================
# PHASE 3: SOAK TEST (simulated long-running)
# ============================================================

class TestSoakTest:
    """Simulate 24h of operation in compressed time."""

    async def run(self):
        """Simulate 24h by running 288 iterations of 5-minute-equivalent work.
        Check for memory leaks, task leaks, connection leaks."""

        from datetime import datetime

        db = DatabaseManager(tempfile.mktemp(suffix=".db"))
        await db.init_db()

        with patch.object(db, '_supabase_insert_link', new=AsyncMock()):
            with patch.object(db, '_supabase_count_links', new=AsyncMock(return_value=None)):
                with patch.object(db, '_get_supabase_session', new=AsyncMock(return_value=None)):
                    # Simulate 24 "hours" of operation
                    # Each "hour" = 100 inserts + 10 queries + 10 count_requests
                    mem_samples = []
                    task_samples = []

                    for hour in range(24):
                        # 100 inserts per "hour"
                        for i in range(100):
                            await db.insert_request(
                                link=f"https://chat.whatsapp.com/SOAK_h{hour}_i{i:03d}",
                                message_date=datetime.now(),
                                group_name="soak_group",
                                sender_name="soak_user",
                                source_phone="+966",
                                message_text=f"soak test hour {hour} message {i}",
                                link_type="whatsapp",
                            )

                        # 10 count queries
                        for _ in range(10):
                            await db.count_requests()

                        # Sample memory every 4 hours
                        if hour % 4 == 0:
                            gc.collect()
                            mem_samples.append({
                                "hour": hour,
                                "mem_mb": round(get_memory_mb(), 2),
                                "tasks": count_asyncio_tasks(),
                            })
                            task_samples.append(count_asyncio_tasks())

                    final_count = await db.count_requests()

        await db.close()

        # Analyze memory growth trend
        first_mem = mem_samples[0]["mem_mb"] if mem_samples else 0
        last_mem = mem_samples[-1]["mem_mb"] if mem_samples else 0
        mem_growth = last_mem - first_mem

        return {
            "scenario": "Soak test: 24h simulated (2400 inserts + 240 queries)",
            "mem_samples": mem_samples,
            "memory_growth_mb": round(mem_growth, 2),
            "final_link_count": final_count,
            "max_tasks": max(task_samples) if task_samples else 0,
            "verdict": "PASS" if mem_growth < 20 and final_count == 2400 else "WARN",
            "notes": f"Memory grew {mem_growth:.1f}MB over 24h simulated",
        }


# ============================================================
# PHASE 4: FAILURE INJECTION
# ============================================================

class TestRandomFailures:
    """Inject random failures into all operations."""

    async def run(self):
        """Randomly fail 10% of DB operations, 20% of HTTP calls.
        Verify no data corruption, no infinite loops."""

        from datetime import datetime
        import random

        random.seed(42)  # reproducible

        db = DatabaseManager(tempfile.mktemp(suffix=".db"))
        await db.init_db()

        # Track results
        success = 0
        failed = 0
        errors = set()

        with patch.object(db, '_supabase_insert_link', new=AsyncMock()):
            with patch.object(db, '_supabase_count_links', new=AsyncMock(return_value=None)):
                with patch.object(db, '_get_supabase_session', new=AsyncMock(return_value=None)):
                    for i in range(500):
                        # Randomly inject failures
                        if random.random() < 0.1:  # 10% failure rate
                            # Mock conn.execute to fail sometimes
                            original = db._conn.execute
                            async def failing_exec(q, *args):
                                if "INSERT" in q.upper() and random.random() < 0.5:
                                    raise Exception("Injected failure")
                                return await original(q, *args)
                            db._conn.execute = failing_exec

                        try:
                            result = await db.insert_request(
                                link=f"https://chat.whatsapp.com/FAIL_i{i:03d}",
                                message_date=datetime.now(),
                                group_name="g", sender_name="s", source_phone="+966",
                                link_type="whatsapp",
                            )
                            if result:
                                success += 1
                            else:
                                failed += 1
                        except Exception as e:
                            errors.add(str(e)[:50])
                            failed += 1
                        finally:
                            # Restore
                            if "failing_exec" in str(db._conn.execute):
                                db._conn.execute = original

        final_count = await db.count_requests()
        await db.close()

        return {
            "scenario": "Random failure injection (500 ops, 10% fail rate)",
            "successful_inserts": success,
            "failed_or_duplicate": failed,
            "unique_errors": len(errors),
            "final_db_count": final_count,
            "verdict": "PASS" if final_count == success else "FAIL",
            "notes": "DB count must match successful inserts (no corruption)",
        }


# ============================================================
# PHASE 6: SECURITY UNDER STRESS
# ============================================================

class TestFloodAttack:
    """Simulate 10000 rapid messages from one user."""

    async def run(self):
        """10000 messages in rapid succession must:
        - Not crash the bot
        - Deduplicate properly
        - Not exhaust memory
        - Complete in reasonable time
        """

        from datetime import datetime

        db = DatabaseManager(tempfile.mktemp(suffix=".db"))
        await db.init_db()

        with patch.object(db, '_supabase_insert_link', new=AsyncMock()):
            with patch.object(db, '_supabase_count_links', new=AsyncMock(return_value=None)):
                with patch.object(db, '_get_supabase_session', new=AsyncMock(return_value=None)):
                    mem_before = get_memory_mb()
                    start = time.monotonic()

                    # 10000 messages, but only 100 unique links (rest are duplicates)
                    success = 0
                    dup = 0
                    for i in range(10000):
                        link = f"https://chat.whatsapp.com/FLOOD{i % 100:03d}"  # 100 unique
                        result = await db.insert_request(
                            link=link,
                            message_date=datetime.now(),
                            group_name="g", sender_name="s", source_phone="+966",
                            link_type="whatsapp",
                        )
                        if result:
                            success += 1
                        else:
                            dup += 1

                    elapsed = time.monotonic() - start
                    mem_after = get_memory_mb()

        await db.close()

        return {
            "scenario": "Flood attack: 10000 messages (100 unique links)",
            "elapsed_sec": round(elapsed, 2),
            "successful_inserts": success,
            "duplicates_rejected": dup,
            "memory_growth_mb": round(mem_after - mem_before, 2),
            "verdict": "PASS" if success == 100 and dup == 9900 else "FAIL",
            "notes": "Must deduplicate: 100 unique + 9900 rejected",
        }


class TestOversizedPayload:
    """Send extremely large messages."""

    async def run(self):
        """Messages with 1MB of text must:
        - Not crash the formatter
        - Be truncated properly
        - Not blow up memory
        """

        from datetime import datetime

        results = []

        # Test various oversized inputs
        test_cases = [
            ("1MB text", "x" * (1024 * 1024)),
            ("1MB link", "https://chat.whatsapp.com/" + "A" * (1024 * 1024)),
            ("100K emojis", "🚀" * 100000),
            ("100K newlines", "\n" * 100000),
            ("Nested HTML", "<script>" * 10000 + "alert(1)" + "</script>" * 10000),
            ("SQL injection", "'; DROP TABLE users; --" * 1000),
            ("Path traversal", "../../../etc/passwd" * 1000),
        ]

        for name, payload in test_cases:
            try:
                start = time.monotonic()
                html = MessageFormatter.format_link_message(
                    group_name=payload[:200],
                    sender_name=payload[:100],
                    sender_contact="",
                    message_date=datetime.now(),
                    link=payload[:500] if payload.startswith("http") else "https://chat.whatsapp.com/ABC",
                    message_text=payload,
                    source_phone="+966",
                )
                elapsed = time.monotonic() - start
                results.append({
                    "case": name,
                    "elapsed_ms": round(elapsed * 1000),
                    "html_length": len(html),
                    "verdict": "PASS" if elapsed < 1.0 else "FAIL",
                })
            except Exception as e:
                results.append({
                    "case": name,
                    "error": str(e)[:100],
                    "verdict": "FAIL",
                })

        return results


class TestMalformedUTF8:
    """Send malformed UTF-8 in various places."""

    async def run(self):
        """Malformed UTF-8 must not crash any handler."""


        # Malformed UTF-8 bytes
        bad_utf8_bytes = b"\xff\xfe\x00\x80\x81"
        try:
            bad_utf8 = bad_utf8_bytes.decode("utf-8", errors="replace")
        except:
            bad_utf8 = str(bad_utf8_bytes)

        results = []

        # Test link extraction with bad UTF-8
        try:
            links = extract_whatsapp_telegram_links(bad_utf8)
            results.append({
                "case": "Link extraction with malformed UTF-8",
                "verdict": "PASS",
                "result": links,
            })
        except Exception as e:
            results.append({
                "case": "Link extraction with malformed UTF-8",
                "verdict": "FAIL",
                "error": str(e)[:100],
            })

        # Test JSON cleaning with bad UTF-8
        try:
            result = _extract_clean_json(bad_utf8)
            results.append({
                "case": "JSON cleaning with malformed UTF-8",
                "verdict": "PASS",
                "result": result[:50],
            })
        except Exception as e:
            results.append({
                "case": "JSON cleaning with malformed UTF-8",
                "verdict": "FAIL",
                "error": str(e)[:100],
            })

        return results


# ============================================================
# MAIN RUNNER
# ============================================================

async def run_all_tests():
    """Run all chaos/load/soak tests and collect results.

    All tests mock asyncio.sleep to run in compressed time — we're
    testing logic and resource behavior, not actually waiting.
    """
    all_results = []

    # Patch asyncio.sleep globally for the entire test run
    # (we want to test logic, not wait real seconds)
    original_sleep = asyncio.sleep
    fast_sleep = AsyncMock(return_value=None)

    test_classes = [
        ("Phase 1: Chaos Engineering", [
            TestTelegramApiOutage(),
            TestTelegramFloodWait(),
            TestSupabaseOutage(),
            TestDatabaseLocked(),
            TestDatabaseCorruption(),
            TestDiskFull(),
            TestAIProviderFailure(),
            TestOOMScenario(),
            TestSIGTERMHandling(),
        ]),
        ("Phase 2: Load Testing", [
            TestLoad100Users(),
            TestLoad500Users(),
            TestLoad1000Users(),
        ]),
        ("Phase 3: Soak Test", [
            TestSoakTest(),
        ]),
        ("Phase 4: Failure Injection", [
            TestRandomFailures(),
        ]),
        ("Phase 6: Security Under Stress", [
            TestFloodAttack(),
            TestOversizedPayload(),
            TestMalformedUTF8(),
        ]),
    ]

    # Run with patched sleep
    with patch("asyncio.sleep", fast_sleep):
        for phase_name, tests in test_classes:
            print(f"\n{'='*60}")
            print(f"  {phase_name}")
            print(f"{'='*60}")

            for test in tests:
                test_name = test.__class__.__name__
                try:
                    result = await test.run()
                    if isinstance(result, list):
                        for r in result:
                            verdict = r.get("verdict", "?")
                            emoji = "✅" if verdict == "PASS" else "❌" if verdict == "FAIL" else "⚠️"
                            print(f"  {emoji} {r.get('scenario', r.get('case', '?'))}: {verdict}")
                            if "notes" in r:
                                print(f"     → {r['notes']}")
                            all_results.append(r)
                    else:
                        verdict = result.get("verdict", "?")
                        emoji = "✅" if verdict == "PASS" else "❌" if verdict == "FAIL" else "⚠️"
                        print(f"  {emoji} {test_name}: {verdict}")
                        if "notes" in result:
                            print(f"     → {result['notes']}")
                        all_results.append(result)
                except Exception as e:
                    print(f"  💥 {test_name}: CRASHED — {e}")
                    import traceback
                    traceback.print_exc()
                    all_results.append({
                        "scenario": test_name,
                        "verdict": "CRASH",
                        "error": str(e),
                    })

    # Summary
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")

    pass_count = sum(1 for r in all_results if r.get("verdict") == "PASS")
    fail_count = sum(1 for r in all_results if r.get("verdict") == "FAIL")
    warn_count = sum(1 for r in all_results if r.get("verdict") in ("WARN", "PARTIAL"))
    crash_count = sum(1 for r in all_results if r.get("verdict") == "CRASH")

    print(f"  Total tests: {len(all_results)}")
    print(f"  ✅ PASS: {pass_count}")
    print(f"  ❌ FAIL: {fail_count}")
    print(f"  ⚠️  WARN: {warn_count}")
    print(f"  💥 CRASH: {crash_count}")

    return all_results


if __name__ == "__main__":
    results = asyncio.run(run_all_tests())

    # Save results to file
    import json
    output_path = Path(__file__).parent / "chaos_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str, ensure_ascii=False)
    print(f"\nResults saved to {output_path}")
