#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests that verify bot.py (the file Render actually runs) is identical
to monitor_v12.py and contains all the production fixes.

This is the critical deployment-verification test: if Render runs
'python bot.py', then bot.py MUST contain all the fixes we made.
"""
import filecmp
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
logging.disable(logging.CRITICAL)


class TestBotPyIsProductionReady(unittest.TestCase):
    """bot.py is the file Render runs. It must be the audited version."""

    def test_bot_py_exists(self):
        """bot.py must exist in the project root."""
        bot_path = Path(__file__).parent.parent / "bot.py"
        self.assertTrue(bot_path.exists(),
            "bot.py must exist — Render runs 'python bot.py'")

    def test_bot_py_is_identical_to_monitor_v12(self):
        """bot.py must be identical to monitor_v12.py (the audited file).
        If they diverge, bot.py is missing fixes."""
        bot_path = Path(__file__).parent.parent / "bot.py"
        v12_path = Path(__file__).parent.parent / "monitor_v12.py"
        if not bot_path.exists() or not v12_path.exists():
            self.skipTest("bot.py or monitor_v12.py missing")
        self.assertTrue(filecmp.cmp(bot_path, v12_path, shallow=False),
            "bot.py must be byte-identical to monitor_v12.py")

    def test_bot_py_imports_cleanly(self):
        """bot.py must import without errors (no syntax/import issues)."""
        import importlib
        # Force re-import
        if 'bot' in sys.modules:
            del sys.modules['bot']
        try:
            bot = importlib.import_module('bot')
        except Exception as e:
            self.fail(f"bot.py failed to import: {e}")

    def test_bot_py_has_all_production_components(self):
        """bot.py must export all the components needed for production."""
        import importlib
        if 'bot' in sys.modules:
            del sys.modules['bot']
        bot = importlib.import_module('bot')

        required = [
            'Monitor', 'Config', 'DatabaseManager', 'AIAnalyzer',
            'MessageFormatter', 'HelpRequestDetector', 'HistoryScanner',
            'health_handler', 'ready_handler', 'metrics_handler',
            'start_http_server', 'main',
            'extract_whatsapp_telegram_links', 'is_advertiser_message',
            'is_target_university_message', 'extract_sender_contact',
            '_extract_clean_json',
            'SPAM_KEYWORDS', 'HELP_KEYWORDS', 'TARGET_UNIVERSITIES',
            'ADVERTISER_KEYWORDS', 'SCAN_COMMANDS',
        ]
        missing = [name for name in required if not hasattr(bot, name)]
        self.assertEqual(missing, [],
            f"bot.py is missing: {missing}")

    def test_bot_py_has_security_fixes(self):
        """bot.py must contain the security fixes from the audit."""
        src = (Path(__file__).parent.parent / "bot.py").read_text(encoding='utf-8')
        # HTML injection protection
        self.assertIn("_safe_url", src, "HTML injection protection missing")
        self.assertIn("html_module.escape", src, "HTML escaping missing")
        # Authorization
        self.assertIn("is not None", src, "owner_id check missing")
        # API key (this is in pro_backend, but verify bot has the right patterns)
        # URL encoding
        self.assertIn("url_quote", src, "URL encoding missing")
        # Callback data size cap
        self.assertIn("len(event.data)", src, "callback size cap missing")

    def test_bot_py_has_reliability_fixes(self):
        """bot.py must contain the reliability fixes from the audit."""
        src = (Path(__file__).parent.parent / "bot.py").read_text(encoding='utf-8')
        # Login session TTL
        self.assertIn("_login_session_ttl", src, "login session TTL missing")
        self.assertIn("_cleanup_expired_login_sessions", src, "cleanup function missing")
        # Login cooldown
        self.assertIn("_login_cooldown", src, "login cooldown missing")
        # Send retry cap
        self.assertIn("max_total_wait", src, "send retry cap missing")
        # Stop timeout
        self.assertIn("asyncio.wait_for", src, "stop timeout missing")
        # DB corruption recovery
        self.assertIn("corrupt", src.lower(), "DB corruption recovery missing")
        # busy_timeout reduced
        self.assertIn("busy_timeout=5000", src, "busy_timeout must be 5000 (not 30000)")

    def test_bot_py_has_observability(self):
        """bot.py must have /health, /ready, /metrics endpoints."""
        src = (Path(__file__).parent.parent / "bot.py").read_text(encoding='utf-8')
        self.assertIn("/health", src, "health endpoint missing")
        self.assertIn("/ready", src, "ready endpoint missing")
        self.assertIn("/metrics", src, "metrics endpoint missing")
        self.assertIn("ready_handler", src, "ready_handler missing")
        self.assertIn("metrics_handler", src, "metrics_handler missing")

    def test_bot_py_has_no_bare_except(self):
        """bot.py must have zero bare 'except:' clauses (all converted to
        'except Exception:')."""
        import re
        src = (Path(__file__).parent.parent / "bot.py").read_text(encoding='utf-8')
        # Find bare except: at start of line (with optional whitespace)
        bare_excepts = re.findall(r'^\s*except\s*:', src, re.MULTILINE)
        self.assertEqual(len(bare_excepts), 0,
            f"bot.py has {len(bare_excepts)} bare 'except:' clauses — must be 0")

    def test_bot_py_has_no_undefined_names(self):
        """bot.py must not reference undefined names (SPAM_KEYWORDS etc.)."""
        # Quick check: import and access the constants
        import importlib
        if 'bot' in sys.modules:
            del sys.modules['bot']
        bot = importlib.import_module('bot')
        # These were previously undefined — verify they exist now
        self.assertIsInstance(bot.SPAM_KEYWORDS, list)
        self.assertGreater(len(bot.SPAM_KEYWORDS), 0)
        self.assertIsInstance(bot.HELP_KEYWORDS, list)
        self.assertGreater(len(bot.HELP_KEYWORDS), 0)


class TestBotPyFunctionalCorrectness(unittest.TestCase):
    """Run the core functional tests against bot.py (not monitor_v12.py)
    to confirm the deployed file actually works."""

    def test_link_extraction_works(self):
        import importlib
        if 'bot' in sys.modules:
            del sys.modules['bot']
        bot = importlib.import_module('bot')
        links = bot.extract_whatsapp_telegram_links(
            "join https://chat.whatsapp.com/TEST123 now"
        )
        self.assertEqual(len(links), 1)
        self.assertIn("TEST123", links[0])

    def test_advertiser_detection_works(self):
        import importlib
        if 'bot' in sys.modules:
            del sys.modules['bot']
        bot = importlib.import_module('bot')
        self.assertTrue(bot.is_advertiser_message("احجز الآن - عرض محدود!"))
        self.assertFalse(bot.is_advertiser_message("محتاج مساعدة في الرياضيات"))

    def test_university_detection_works(self):
        import importlib
        if 'bot' in sys.modules:
            del sys.modules['bot']
        bot = importlib.import_module('bot')
        self.assertTrue(bot.is_target_university_message("جامعة الكويت"))
        self.assertFalse(bot.is_target_university_message("hello world"))

    def test_json_cleaning_works(self):
        import importlib
        if 'bot' in sys.modules:
            del sys.modules['bot']
        bot = importlib.import_module('bot')
        import json
        text = '```json\n{"should_save": true}\n```'
        result = bot._extract_clean_json(text)
        parsed = json.loads(result)
        self.assertTrue(parsed["should_save"])

    def test_html_injection_blocked(self):
        """bot.py's MessageFormatter must block HTML injection."""
        import importlib
        if 'bot' in sys.modules:
            del sys.modules['bot']
        from datetime import datetime
        bot = importlib.import_module('bot')
        malicious = '<script>alert(1)</script>'
        html = bot.MessageFormatter.format_link_message(
            group_name=malicious,
            sender_name="user",
            sender_contact="",
            message_date=datetime.now(),
            link="https://chat.whatsapp.com/ABC",
            message_text="text",
            source_phone="+966",
        )
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_database_dedup_works(self):
        """bot.py's DatabaseManager must deduplicate correctly."""
        import asyncio
        import tempfile
        import os
        from datetime import datetime
        from unittest.mock import AsyncMock, patch
        import importlib
        if 'bot' in sys.modules:
            del sys.modules['bot']
        bot = importlib.import_module('bot')

        async def go():
            db = bot.DatabaseManager(tempfile.mktemp(suffix=".db"))
            await db.init_db()
            try:
                with patch.object(db, '_supabase_insert_link', new=AsyncMock()):
                    with patch.object(db, '_get_supabase_session', new=AsyncMock(return_value=None)):
                        r1 = await db.insert_request(
                            link="https://chat.whatsapp.com/DEDUP1",
                            message_date=datetime.now(),
                            group_name="g", sender_name="s", source_phone="+966",
                            link_type="whatsapp")
                        r2 = await db.insert_request(
                            link="https://chat.whatsapp.com/DEDUP1",
                            message_date=datetime.now(),
                            group_name="g", sender_name="s", source_phone="+966",
                            link_type="whatsapp")
                return r1, r2
            finally:
                await db.close()

        r1, r2 = asyncio.run(go())
        self.assertTrue(r1, "First insert must succeed")
        self.assertFalse(r2, "Duplicate must be rejected")


if __name__ == "__main__":
    unittest.main(verbosity=2)
