#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Integration & stress tests for the WhatsApp/Telegram Link Monitor.

These tests exercise the AIAnalyzer fallback path, the DatabaseManager
deduplication logic, and stress-test the regex extraction with thousands
of inputs to ensure no DoS vulnerabilities.

Run:
    cd download
    python -m unittest tests.test_integration -v
"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
logging.disable(logging.CRITICAL)


class TestAIAnalyzerFallback(unittest.TestCase):
    """Tests for AIAnalyzer._fallback_analysis() (no network)."""

    def test_no_link_returns_should_save_false(self):
        from monitor_v12 import AIAnalyzer
        result = AIAnalyzer._fallback_analysis("hello world no link")
        self.assertFalse(result["should_save"])
        self.assertEqual(result["link"], "")

    def test_whatsapp_link_detected(self):
        from monitor_v12 import AIAnalyzer
        text = "انضموا لمجموعتنا https://chat.whatsapp.com/ABC123"
        result = AIAnalyzer._fallback_analysis(text)
        self.assertTrue(result["should_save"])
        self.assertEqual(result["link_type"], "whatsapp")
        self.assertIn("ABC123", result["link"])

    def test_telegram_link_detected(self):
        from monitor_v12 import AIAnalyzer
        text = "قناة جديدة https://t.me/mychannel تابعونا"
        result = AIAnalyzer._fallback_analysis(text)
        self.assertTrue(result["should_save"])
        self.assertEqual(result["link_type"], "telegram")

    def test_advertiser_link_filtered(self):
        from monitor_v12 import AIAnalyzer
        text = ("احجز الآن! عرض محدود! خصم 50%\n"
                "https://chat.whatsapp.com/PROMO123")
        result = AIAnalyzer._fallback_analysis(text)
        self.assertFalse(result["should_save"], "advertiser links must be filtered")
        self.assertTrue(result["is_advertisement"])

    def test_empty_text(self):
        from monitor_v12 import AIAnalyzer
        result = AIAnalyzer._fallback_analysis("")
        self.assertFalse(result["should_save"])

    def test_none_text(self):
        from monitor_v12 import AIAnalyzer
        result = AIAnalyzer._fallback_analysis(None)
        self.assertFalse(result["should_save"])


class TestDatabaseDeduplication(unittest.TestCase):
    """Tests for DatabaseManager.insert_request() deduplication."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_first_insert_succeeds(self):
        from monitor_v12 import DatabaseManager
        db = DatabaseManager(self.db_path)
        asyncio.run(db.init_db())
        try:
            inserted = asyncio.run(db.insert_request(
                link="https://chat.whatsapp.com/UNIQUE1",
                message_date=None,
                group_name="test",
                sender_name="user",
                source_phone="+966",
                message_text="text",
                link_type="whatsapp",
            ))
            self.assertTrue(inserted)
        finally:
            asyncio.run(db.close())

    def test_duplicate_insert_returns_false(self):
        from monitor_v12 import DatabaseManager
        db = DatabaseManager(self.db_path)
        asyncio.run(db.init_db())
        try:
            # First insert succeeds
            r1 = asyncio.run(db.insert_request(
                link="https://chat.whatsapp.com/DUP1",
                message_date=None,
                group_name="g",
                sender_name="s",
                source_phone="+966",
                link_type="whatsapp",
            ))
            self.assertTrue(r1)
            # Second insert of the SAME link must be rejected
            r2 = asyncio.run(db.insert_request(
                link="https://chat.whatsapp.com/DUP1",
                message_date=None,
                group_name="g",
                sender_name="s",
                source_phone="+966",
                link_type="whatsapp",
            ))
            self.assertFalse(r2, "duplicate link must be rejected")
        finally:
            asyncio.run(db.close())

    def test_url_normalization_for_dedup(self):
        """Trailing slash and case differences should be normalized for dedup."""
        from monitor_v12 import DatabaseManager
        db = DatabaseManager(self.db_path)
        asyncio.run(db.init_db())
        try:
            r1 = asyncio.run(db.insert_request(
                link="https://chat.whatsapp.com/CASE123",
                message_date=None,
                group_name="g", sender_name="s", source_phone="+966",
                link_type="whatsapp",
            ))
            self.assertTrue(r1)
            # Same link, lowercase, trailing slash — should be detected as duplicate
            r2 = asyncio.run(db.insert_request(
                link="https://chat.whatsapp.com/case123/",
                message_date=None,
                group_name="g", sender_name="s", source_phone="+966",
                link_type="whatsapp",
            ))
            self.assertFalse(r2, "normalized duplicate must be rejected")
        finally:
            asyncio.run(db.close())

    def test_count_requests_returns_zero_for_empty(self):
        from monitor_v12 import DatabaseManager
        db = DatabaseManager(self.db_path)
        asyncio.run(db.init_db())
        try:
            # Disable Supabase for this test (it's not configured)
            with patch.object(db, '_supabase_count_links', new=AsyncMock(return_value=None)):
                count = asyncio.run(db.count_requests())
                self.assertEqual(count, 0)
        finally:
            asyncio.run(db.close())

    def test_count_increments_after_insert(self):
        from monitor_v12 import DatabaseManager
        db = DatabaseManager(self.db_path)
        asyncio.run(db.init_db())
        try:
            with patch.object(db, '_supabase_count_links', new=AsyncMock(return_value=None)):
                asyncio.run(db.insert_request(
                    link="https://chat.whatsapp.com/CNT1",
                    message_date=None, group_name="g", sender_name="s",
                    source_phone="+966", link_type="whatsapp",
                ))
                asyncio.run(db.insert_request(
                    link="https://chat.whatsapp.com/CNT2",
                    message_date=None, group_name="g", sender_name="s",
                    source_phone="+966", link_type="whatsapp",
                ))
                count = asyncio.run(db.count_requests())
                self.assertEqual(count, 2)
        finally:
            asyncio.run(db.close())


class TestRegexStress(unittest.TestCase):
    """Stress tests to verify no ReDoS vulnerabilities in the regex patterns."""

    def test_long_input_no_catastrophic_backtracking(self):
        """A 100KB input must complete in under 1 second (no ReDoS)."""
        from monitor_v12 import extract_whatsapp_telegram_links
        # Craft a malicious-looking input that could trigger backtracking
        evil = "https://chat.whatsapp.com/" + "a" * 100000
        start = time.monotonic()
        result = extract_whatsapp_telegram_links(evil)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 1.0, f"regex took {elapsed:.3f}s — possible ReDoS")
        self.assertEqual(len(result), 1)

    def test_many_links_extracted_quickly(self):
        """1000 links in one message must complete in under 2 seconds."""
        from monitor_v12 import extract_whatsapp_telegram_links
        links = [f"https://chat.whatsapp.com/L{i:04d}" for i in range(1000)]
        text = " ".join(links)
        start = time.monotonic()
        result = extract_whatsapp_telegram_links(text)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 2.0, f"1000 links took {elapsed:.3f}s")
        self.assertEqual(len(result), 1000)

    def test_mixed_valid_invalid_links(self):
        from monitor_v12 import extract_whatsapp_telegram_links
        text = (
            "https://chat.whatsapp.com/VALID1 "
            "https://wa.me/96650000000 "  # direct chat, filtered
            "https://t.me/+privatemessage "  # joinchat, filtered
            "https://t.me/validchannel "  # valid
            "https://example.com/notallowed "  # not whatsapp/tg
        )
        result = extract_whatsapp_telegram_links(text)
        self.assertEqual(len(result), 2)

    def test_unicode_arabic_text_with_links(self):
        from monitor_v12 import extract_whatsapp_telegram_links
        text = ("مرحبا بكم في مجموعة جامعة الأهلية. "
                "هذا الرابط للانضمام: https://chat.whatsapp.com/ArabicTest123 "
                "وشكراً لكم جميعاً")
        result = extract_whatsapp_telegram_links(text)
        self.assertEqual(len(result), 1)
        self.assertIn("ArabicTest123", result[0])


class TestJsonParsingStress(unittest.TestCase):
    """Stress test for _extract_clean_json() with malformed inputs."""

    def test_unclosed_json_returns_cleaned_text(self):
        from monitor_v12 import _extract_clean_json
        # Even malformed JSON should not crash
        result = _extract_clean_json('{"should_save": true')
        # Should extract the JSON-ish part without crashing
        self.assertIn("should_save", result)

    def test_nested_code_blocks(self):
        # Realistic case: AI response with text intro + code block containing JSON
        from monitor_v12 import _extract_clean_json
        text = 'Here is the JSON:\n```json\n{"a":1, "b":2}\n```\nDone.'
        result = _extract_clean_json(text)
        self.assertIn('"a":1', result)
        self.assertIn('"b":2', result)

    def test_only_text_no_json(self):
        from monitor_v12 import _extract_clean_json
        result = _extract_clean_json("just plain text, no JSON here")
        # Should return the text as-is or empty
        self.assertIsInstance(result, str)

    def test_very_large_json(self):
        """A 100KB JSON blob must be processed quickly."""
        from monitor_v12 import _extract_clean_json
        import json
        big_obj = {"data": "x" * 100000}
        text = "```json\n" + json.dumps(big_obj) + "\n```"
        start = time.monotonic()
        result = _extract_clean_json(text)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.5)
        parsed = json.loads(result)
        self.assertEqual(len(parsed["data"]), 100000)


class TestMessageFormatterStress(unittest.TestCase):
    """Stress tests for the formatter."""

    def test_very_long_message_text_truncated(self):
        from monitor_v12 import MessageFormatter
        from datetime import datetime
        long_text = "x" * 10000
        html = MessageFormatter.format_link_message(
            group_name="g", sender_name="s", sender_contact="",
            message_date=datetime.now(),
            link="https://chat.whatsapp.com/ABC",
            message_text=long_text,
            source_phone="+966",
        )
        # The text must be truncated to 150 chars + "..."
        # So total length should be far less than 10000
        self.assertLess(len(html), 2000)

    def test_unicode_emoji_preserved(self):
        from monitor_v12 import MessageFormatter
        from datetime import datetime
        html = MessageFormatter.format_link_message(
            group_name="📚 مجموعة الجامعة",
            sender_name="محمد 😊",
            sender_contact="",
            message_date=datetime.now(),
            link="https://chat.whatsapp.com/ABC",
            message_text="النص مع رموز 🚀✨",
            source_phone="+966",
        )
        self.assertIn("📚", html)
        self.assertIn("😊", html)
        self.assertIn("🚀", html)

    def test_many_non_members_rendered(self):
        from monitor_v12 import MessageFormatter
        from datetime import datetime
        non_members = [f"+9665{i:08d}" for i in range(100)]
        watchers_names = {p: f"User_{i}" for i, p in enumerate(non_members)}
        html = MessageFormatter.format_link_message(
            group_name="g", sender_name="s", sender_contact="",
            message_date=datetime.now(),
            link="https://chat.whatsapp.com/ABC",
            message_text="text",
            source_phone="+966",
            non_members=non_members,
            watchers_names=watchers_names,
        )
        # All non-members must be listed
        for p in non_members:
            self.assertIn(p, html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
