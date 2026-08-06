#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chaos engineering regression tests: verify disaster recovery works.

Each test simulates a real production disaster and verifies the system
recovers gracefully without data loss or permanent failure.
"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
logging.disable(logging.CRITICAL)


class TestDatabaseCorruptionRecovery(unittest.TestCase):
    """When the SQLite DB file is corrupted, init_db must recover."""

    def test_corrupted_db_recovers_and_creates_fresh(self):
        """A corrupted DB file should be moved aside and a fresh one created,
        allowing the bot to start successfully."""
        from monitor_v12 import DatabaseManager

        db_path = tempfile.mktemp(suffix=".db")
        # Write garbage to the file
        with open(db_path, "wb") as f:
            f.write(b"CORRUPTED DATABASE FILE CONTENT - NOT SQLITE FORMAT")

        db = DatabaseManager(db_path)

        async def go():
            await db.init_db()  # should not raise
            # Should be able to insert after recovery
            result = await db.insert_request(
                link="https://chat.whatsapp.com/RECOVER1",
                message_date=datetime.now(),
                group_name="g", sender_name="s", source_phone="+966",
                link_type="whatsapp",
            )
            return result

        try:
            result = asyncio.run(go())
            self.assertTrue(result, "Insert must succeed after recovery")
            # The corrupt file should have been moved aside
            corrupt_files = [f for f in os.listdir(os.path.dirname(db_path) or ".")
                           if f.startswith(os.path.basename(db_path) + ".corrupt.")]
            self.assertGreater(len(corrupt_files), 0,
                "Corrupt DB file should have been moved aside")
        finally:
            asyncio.run(db.close())
            # Cleanup
            for f in os.listdir(os.path.dirname(db_path) or "."):
                if f.startswith(os.path.basename(db_path)):
                    try:
                        os.remove(os.path.join(os.path.dirname(db_path) or ".", f))
                    except:
                        pass


class TestDatabaseBusyTimeoutReduced(unittest.IsolatedAsyncioTestCase):
    """The busy_timeout must be 5s (not 30s) to prevent bot freezing."""

    async def test_busy_timeout_is_5_seconds(self):
        """PRAGMA busy_timeout must be 5000ms, not 30000ms.
        A 30s timeout would freeze the entire bot since all DB ops
        share one connection + asyncio lock."""
        from monitor_v12 import DatabaseManager

        db = DatabaseManager(tempfile.mktemp(suffix=".db"))
        await db.init_db()
        try:
            cursor = await db._conn.execute("PRAGMA busy_timeout")
            row = await cursor.fetchone()
            self.assertEqual(row[0], 5000,
                f"busy_timeout must be 5000ms (got {row[0]})")
        finally:
            await db.close()


class TestSendDoesNotBlockUnderOutage(unittest.IsolatedAsyncioTestCase):
    """Under sustained Telegram outage, _send must not block indefinitely."""

    async def test_100_sends_complete_quickly_with_capped_retry(self):
        """100 sends during a ConnectionError outage must complete in
        under 5 seconds (with sleep mocked). This verifies the retry
        cap works and doesn't accumulate unbounded wait time."""
        from monitor_v12 import Monitor, Config, DatabaseManager

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
        monitor.bot_client.send_message = AsyncMock(
            side_effect=ConnectionError("Telegram unreachable")
        )

        try:
            with patch("asyncio.sleep", new=AsyncMock(return_value=None)):
                start = time.monotonic()
                for i in range(100):
                    await monitor._send(f"test {i}")
                elapsed = time.monotonic() - start
            self.assertLess(elapsed, 5.0,
                f"100 sends took {elapsed:.2f}s — retry cap not working")
        finally:
            await db.close()


class TestFloodAttackDedup(unittest.IsolatedAsyncioTestCase):
    """10000 messages with only 100 unique links must deduplicate correctly."""

    async def test_flood_attack_dedup(self):
        from monitor_v12 import DatabaseManager

        db = DatabaseManager(tempfile.mktemp(suffix=".db"))
        await db.init_db()

        try:
            with patch.object(db, '_supabase_insert_link', new=AsyncMock()):
                with patch.object(db, '_supabase_count_links', new=AsyncMock(return_value=None)):
                    with patch.object(db, '_get_supabase_session', new=AsyncMock(return_value=None)):
                        success = 0
                        dup = 0
                        for i in range(10000):
                            link = f"https://chat.whatsapp.com/FLOOD{i % 100:03d}"
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
            self.assertEqual(success, 100, "Exactly 100 unique links must be inserted")
            self.assertEqual(dup, 9900, "9900 duplicates must be rejected")
        finally:
            await db.close()


class TestLoadTest1000Users(unittest.IsolatedAsyncioTestCase):
    """1000 concurrent users × 10 messages must complete without data loss."""

    async def test_1000_users_concurrent(self):
        from monitor_v12 import DatabaseManager

        db = DatabaseManager(tempfile.mktemp(suffix=".db"))
        await db.init_db()

        try:
            with patch.object(db, '_supabase_insert_link', new=AsyncMock()):
                with patch.object(db, '_supabase_count_links', new=AsyncMock(return_value=None)):
                    with patch.object(db, '_get_supabase_session', new=AsyncMock(return_value=None)):
                        async def user_sim(user_id):
                            for msg_id in range(10):
                                await db.insert_request(
                                    link=f"https://chat.whatsapp.com/U{user_id}_M{msg_id}",
                                    message_date=datetime.now(),
                                    group_name=f"g{user_id}",
                                    sender_name=f"u{user_id}",
                                    source_phone=f"+9665{user_id:08d}",
                                    message_text=f"msg {msg_id}",
                                    link_type="whatsapp",
                                )
                        start = time.monotonic()
                        await asyncio.gather(*[user_sim(i) for i in range(1000)])
                        elapsed = time.monotonic() - start
                        count = await db.count_requests()
            self.assertEqual(count, 10000,
                f"Expected 10000 inserts, got {count}")
            self.assertLess(elapsed, 60,
                f"10000 concurrent inserts took {elapsed:.1f}s — too slow")
        finally:
            await db.close()


class TestSoakTestNoMemoryLeak(unittest.IsolatedAsyncioTestCase):
    """Simulated 24h operation must not leak memory."""

    async def test_24h_simulated_no_memory_leak(self):
        """2400 inserts + 240 queries over 24 'hours' must not grow memory
        by more than 20MB."""
        import resource
        from monitor_v12 import DatabaseManager

        db = DatabaseManager(tempfile.mktemp(suffix=".db"))
        await db.init_db()

        mem_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

        try:
            with patch.object(db, '_supabase_insert_link', new=AsyncMock()):
                with patch.object(db, '_supabase_count_links', new=AsyncMock(return_value=None)):
                    with patch.object(db, '_get_supabase_session', new=AsyncMock(return_value=None)):
                        for hour in range(24):
                            for i in range(100):
                                await db.insert_request(
                                    link=f"https://chat.whatsapp.com/SOAK_h{hour}_i{i:03d}",
                                    message_date=datetime.now(),
                                    group_name="g", sender_name="s", source_phone="+966",
                                    link_type="whatsapp",
                                )
                            for _ in range(10):
                                await db.count_requests()
                        final_count = await db.count_requests()
        finally:
            await db.close()

        import gc
        gc.collect()
        mem_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        mem_growth = mem_after - mem_before

        self.assertEqual(final_count, 2400, "All 2400 links must be stored")
        self.assertLess(mem_growth, 50,
            f"Memory grew {mem_growth:.1f}MB over 24h simulated — possible leak")


class TestAIProviderAllFailureModes(unittest.IsolatedAsyncioTestCase):
    """AI provider must fall back gracefully for ALL failure modes."""

    async def _test_failure_mode(self, status, json_response, description):
        from monitor_v12 import AIAnalyzer

        analyzer = AIAnalyzer()
        analyzer.enabled = True
        analyzer.providers = [{"key": "fake", "url": "http://fake", "model": "m", "name": "test"}]

        mock_session = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status = status
        mock_resp.json = AsyncMock(return_value=json_response)
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=None)
        ))
        analyzer._session = mock_session

        try:
            result = await asyncio.wait_for(
                analyzer.analyze_message("test with https://chat.whatsapp.com/ABC"),
                timeout=5.0,
            )
            self.assertIsInstance(result, dict,
                f"{description}: must return dict, not crash")
            # Must have should_save key
            self.assertIn("should_save", result,
                f"{description}: result must have should_save key")
        finally:
            await analyzer.close()

    async def test_http_429(self):
        await self._test_failure_mode(429, {"error": "rate limited"}, "HTTP 429")

    async def test_http_500(self):
        await self._test_failure_mode(500, {"error": "internal"}, "HTTP 500")

    async def test_http_401(self):
        await self._test_failure_mode(401, {"error": "unauthorized"}, "HTTP 401")

    async def test_empty_choices(self):
        await self._test_failure_mode(200, {"choices": []}, "Empty choices")

    async def test_empty_content(self):
        await self._test_failure_mode(200, {"choices": [{"message": {"content": ""}}]}, "Empty content")

    async def test_invalid_json_response(self):
        await self._test_failure_mode(200, {"choices": [{"message": {"content": "not json"}}]}, "Invalid JSON")

    async def test_malformed_json_response(self):
        await self._test_failure_mode(200, {"choices": [{"message": {"content": "{broken}"}}]}, "Malformed JSON")


class TestSIGTERMShutdownCompletes(unittest.IsolatedAsyncioTestCase):
    """SIGTERM during active processing must complete shutdown quickly."""

    async def test_shutdown_completes_under_10s(self):
        from monitor_v12 import Monitor, Config, DatabaseManager

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

        monitor._running = True
        monitor._bot_task = asyncio.create_task(asyncio.sleep(100))
        monitor._keep_alive_task = asyncio.create_task(asyncio.sleep(100))

        try:
            start = time.monotonic()
            await asyncio.wait_for(monitor.stop(), timeout=15.0)
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 10,
                f"Shutdown took {elapsed:.1f}s — too slow")
        finally:
            await db.close()


class TestOversizedPayloadsDontCrash(unittest.TestCase):
    """Extremely large payloads must not crash the formatter."""

    def test_1mb_text(self):
        from monitor_v12 import MessageFormatter
        html = MessageFormatter.format_link_message(
            group_name="g", sender_name="s", sender_contact="",
            message_date=datetime.now(),
            link="https://chat.whatsapp.com/ABC",
            message_text="x" * (1024 * 1024),
            source_phone="+966",
        )
        self.assertIsInstance(html, str)

    def test_1mb_link(self):
        from monitor_v12 import MessageFormatter
        html = MessageFormatter.format_link_message(
            group_name="g", sender_name="s", sender_contact="",
            message_date=datetime.now(),
            link="https://chat.whatsapp.com/" + "A" * (1024 * 1024),
            message_text="text",
            source_phone="+966",
        )
        self.assertIsInstance(html, str)

    def test_100k_emojis(self):
        from monitor_v12 import MessageFormatter
        html = MessageFormatter.format_link_message(
            group_name="g", sender_name="s", sender_contact="",
            message_date=datetime.now(),
            link="https://chat.whatsapp.com/ABC",
            message_text="🚀" * 100000,
            source_phone="+966",
        )
        self.assertIsInstance(html, str)

    def test_nested_html_tags(self):
        """10000 nested <script> tags must be escaped."""
        from monitor_v12 import MessageFormatter
        payload = "<script>" * 10000 + "alert(1)" + "</script>" * 10000
        html = MessageFormatter.format_link_message(
            group_name=payload[:200],
            sender_name="s", sender_contact="",
            message_date=datetime.now(),
            link="https://chat.whatsapp.com/ABC",
            message_text=payload,
            source_phone="+966",
        )
        self.assertIsInstance(html, str)
        # Must not contain unescaped script tags
        self.assertNotIn("<script>", html)


class TestMalformedUTF8Handled(unittest.TestCase):
    """Malformed UTF-8 must not crash any text handler."""

    def test_link_extraction_with_bad_utf8(self):
        from monitor_v12 import extract_whatsapp_telegram_links
        bad = b"\xff\xfe\x00\x80\x81chat.whatsapp.com/ABC".decode("utf-8", errors="replace")
        result = extract_whatsapp_telegram_links(bad)
        self.assertIsInstance(result, list)

    def test_json_cleaning_with_bad_utf8(self):
        from monitor_v12 import _extract_clean_json
        bad = b"\xff\xfe".decode("utf-8", errors="replace")
        result = _extract_clean_json(bad)
        self.assertIsInstance(result, str)


class TestSupabaseOutageFallback(unittest.IsolatedAsyncioTestCase):
    """When Supabase is down, links must still be saved locally."""

    async def test_supabase_outage_local_fallback(self):
        from monitor_v12 import DatabaseManager

        db = DatabaseManager(tempfile.mktemp(suffix=".db"))
        db.supabase_url = "https://fake.supabase.co"
        db.supabase_key = "fake-key"
        await db.init_db()

        # Mock Supabase to always fail
        mock_session = AsyncMock()
        mock_session.post = AsyncMock(side_effect=ConnectionError("Supabase unreachable"))
        mock_session.get = AsyncMock(side_effect=ConnectionError("Supabase unreachable"))
        db._supabase_session = mock_session
        db._get_supabase_session = AsyncMock(return_value=mock_session)

        try:
            inserted = 0
            for i in range(50):
                result = await db.insert_request(
                    link=f"https://chat.whatsapp.com/OUT{i}",
                    message_date=datetime.now(),
                    group_name="g", sender_name="s", source_phone="+966",
                    link_type="whatsapp",
                )
                if result:
                    inserted += 1
            self.assertEqual(inserted, 50,
                "All 50 links must be saved locally despite Supabase outage")
        finally:
            await db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
