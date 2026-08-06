#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Red-team round 2 regression tests: deeper adversarial probes.

Each test verifies a fix found in the second round of red-team analysis.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
logging.disable(logging.CRITICAL)


class TestSendBatchRetryStormFixed(unittest.TestCase):
    """Round 2: _send_batch had no retry cap — could block for minutes on FloodWait."""

    def test_send_batch_uses_send_with_retry(self):
        """HistoryScanner._send_batch should now use _send_with_retry
        which caps total wait at 120s (instead of unbounded FloodWait sleep)."""
        src = Path("monitor_v12.py").read_text(encoding='utf-8')
        self.assertIn("_send_with_retry", src,
            "_send_with_retry method must exist")
        # Verify it has the cap
        swr_start = src.find("async def _send_with_retry")
        swr_end = src.find("async def _send_summary", swr_start)
        swr_code = src[swr_start:swr_end]
        self.assertIn("max_total_wait", swr_code,
            "_send_with_retry must cap total wait time")


class TestLoginCooldownsCleanup(unittest.TestCase):
    """Round 2: _login_cooldowns dict grew unbounded."""

    def test_cooldowns_cleaned_in_cleanup_function(self):
        """The _cleanup_expired_login_sessions function should also prune
        expired cooldown entries."""
        src = Path("monitor_v12.py").read_text(encoding='utf-8')
        cleanup_start = src.find("def _cleanup_expired_login_sessions")
        cleanup_end = src.find("async def _handle_login_step", cleanup_start)
        cleanup_code = src[cleanup_start:cleanup_end]
        self.assertIn("_login_cooldowns", cleanup_code,
            "cleanup function must prune expired cooldown entries")


class TestScanTasksPruned(unittest.TestCase):
    """Round 2: _current_scan_tasks list grew unbounded with completed tasks."""

    def test_start_scan_all_prunes_completed_tasks(self):
        src = Path("monitor_v12.py").read_text(encoding='utf-8')
        ssa_start = src.find("async def _start_scan_all")
        ssa_end = src.find("async def _run_scan_for_watcher", ssa_start)
        ssa_code = src[ssa_start:ssa_end]
        self.assertIn("t for t in self._current_scan_tasks if not t.done()", ssa_code,
            "_start_scan_all must prune completed tasks")

    def test_run_scan_for_watcher_removes_self_from_list(self):
        """Each scan task should remove itself from _current_scan_tasks in finally."""
        src = Path("monitor_v12.py").read_text(encoding='utf-8')
        rs_start = src.find("async def _run_scan_for_watcher")
        rs_end = src.find("async def _run_user_client", rs_start)
        rs_code = src[rs_start:rs_end]
        self.assertIn("asyncio.current_task()", rs_code,
            "_run_scan_for_watcher must remove self from task list")


class TestCleanupUserClient(unittest.TestCase):
    """Round 2: orphaned user_clients when session revoked."""

    def test_cleanup_user_client_exists(self):
        src = Path("monitor_v12.py").read_text(encoding='utf-8')
        self.assertIn("_cleanup_user_client", src,
            "_cleanup_user_client method must exist")

    def test_cleanup_called_on_invalid_session(self):
        """_run_user_client should call _cleanup_user_client when session is invalid."""
        src = Path("monitor_v12.py").read_text(encoding='utf-8')
        ruc_start = src.find("async def _run_user_client")
        ruc_end = src.find("def _cleanup_user_client", ruc_start)
        ruc_code = src[ruc_start:ruc_end]
        # Should be called at least twice (invalid session string + unauthorized)
        count = ruc_code.count("_cleanup_user_client(phone)")
        self.assertGreaterEqual(count, 2,
            f"_cleanup_user_client must be called on session errors (found {count})")


class TestProBackendLifespanMigration(unittest.TestCase):
    """Round 2: pro_backend used deprecated @app.on_event."""

    def test_lifespan_used(self):
        src = Path("pro_backend/main.py").read_text(encoding='utf-8')
        self.assertIn("lifespan=lifespan", src,
            "pro_backend should pass lifespan to FastAPI()")
        self.assertIn("@asynccontextmanager", src,
            "lifespan should be decorated with @asynccontextmanager")
        # The string @app.on_event may appear in comments/docstrings, so check
        # for actual decorator usage at the start of a line (after optional whitespace).
        import re
        on_event_decorators = re.findall(r'^\s*@app\.on_event', src, re.MULTILINE)
        self.assertEqual(len(on_event_decorators), 0,
            f"pro_backend should NOT use @app.on_event decorator (found {len(on_event_decorators)})")

    def test_hmac_imported_at_top_level(self):
        """hmac should be imported at module level, not inside the function."""
        src = Path("pro_backend/main.py").read_text(encoding='utf-8')
        # Check that 'import hmac' is at the top, not inside verify_api_key
        self.assertIn("import hmac\n", src[:500],
            "hmac should be imported at top level for clarity")


class TestOnCommandInputSizeCap(unittest.TestCase):
    """Round 2: _on_command had no input size limit."""

    def test_command_text_capped(self):
        """_on_command should reject oversized text."""
        src = Path("monitor_v12.py").read_text(encoding='utf-8')
        cmd_start = src.find("async def _on_command")
        cmd_end = src.find("def is_scan_running", cmd_start)
        cmd_code = src[cmd_start:cmd_end]
        self.assertIn("len(text) > 10000", cmd_code,
            "_on_command must cap text length")
        self.assertIn("len(cmd) > 100", cmd_code,
            "_on_command must cap command length")



class TestProBackendNoDuplicateDefinitions(unittest.TestCase):
    """Round 2: pro_backend had duplicate verify_api_key and SUPABASE_URL."""

    def test_no_duplicate_function_definitions(self):
        """Each function should be defined exactly once."""
        src = Path("pro_backend/main.py").read_text(encoding='utf-8')
        self.assertEqual(src.count("async def verify_api_key"), 1,
            "verify_api_key must be defined exactly once")
        self.assertEqual(src.count("async def lifespan"), 1,
            "lifespan must be defined exactly once")

    def test_no_duplicate_variable_assignments(self):
        """Module-level config vars should be assigned exactly once
        (not counting type annotations or usages in strings)."""
        src = Path("pro_backend/main.py").read_text(encoding='utf-8')
        # Count actual assignments (var = at start of line, not type annotations)
        import re
        supa_url_assigns = re.findall(r'^SUPABASE_URL\s*=', src, re.MULTILINE)
        self.assertEqual(len(supa_url_assigns), 1,
            f"SUPABASE_URL must be assigned exactly once (found {len(supa_url_assigns)})")
        supa_key_assigns = re.findall(r'^SUPABASE_KEY\s*=', src, re.MULTILINE)
        self.assertEqual(len(supa_key_assigns), 1,
            f"SUPABASE_KEY must be assigned exactly once (found {len(supa_key_assigns)})")
        valid_key_assigns = re.findall(r'^VALID_API_KEY\s*=', src, re.MULTILINE)
        self.assertEqual(len(valid_key_assigns), 1,
            f"VALID_API_KEY must be assigned exactly once (found {len(valid_key_assigns)})")
        # _app_session assignment (the line that creates it)
        app_session_assigns = re.findall(r'^_app_session\s*[:=]', src, re.MULTILINE)
        self.assertEqual(len(app_session_assigns), 1,
            f"_app_session must be assigned exactly once (found {len(app_session_assigns)})")


class TestMessageFormatterNoneLinkFixed(unittest.TestCase):
    """Round 2: format_link_message crashed on link=None."""

    def test_none_link_handled(self):
        from monitor_v12 import MessageFormatter
        from datetime import datetime
        # Should not raise
        html = MessageFormatter.format_link_message(
            group_name="g", sender_name="s", sender_contact="",
            message_date=datetime.now(),
            link=None,
            message_text="text",
            source_phone="+966",
        )
        self.assertIsInstance(html, str)
        # Should NOT contain an empty href
        self.assertNotIn('href=""', html)

    def test_empty_string_link_handled(self):
        from monitor_v12 import MessageFormatter
        from datetime import datetime
        html = MessageFormatter.format_link_message(
            group_name="g", sender_name="s", sender_contact="",
            message_date=datetime.now(),
            link="",
            message_text="text",
            source_phone="+966",
        )
        self.assertIsInstance(html, str)
        self.assertNotIn('href=""', html)

    def test_integer_link_does_not_crash(self):
        """A non-string link (e.g. int from a bug elsewhere) should not crash."""
        from monitor_v12 import MessageFormatter
        from datetime import datetime
        html = MessageFormatter.format_link_message(
            group_name="g", sender_name="s", sender_contact="",
            message_date=datetime.now(),
            link=12345,  # not a URL, but should not crash
            message_text="text",
            source_phone="+966",
        )
        self.assertIsInstance(html, str)


class TestProBackendErrorResponses(unittest.TestCase):
    """Round 2: verify pro_backend returns correct error codes."""

    def test_api_key_unset_returns_503(self):
        """When API_KEY env is unset, server returns 503 (not 401)."""
        from fastapi import HTTPException
        with patch_context("pro_backend.main.VALID_API_KEY", ""):
            from pro_backend.main import verify_api_key
            try:
                asyncio.run(verify_api_key(None))
                self.fail("Should have raised HTTPException")
            except HTTPException as e:
                self.assertEqual(e.status_code, 503)

    def test_wrong_api_key_returns_401(self):
        from fastapi import HTTPException
        with patch_context("pro_backend.main.VALID_API_KEY", "secret"):
            from pro_backend.main import verify_api_key
            try:
                asyncio.run(verify_api_key("wrong"))
                self.fail("Should have raised HTTPException")
            except HTTPException as e:
                self.assertEqual(e.status_code, 401)

    def test_correct_api_key_passes(self):
        with patch_context("pro_backend.main.VALID_API_KEY", "secret"):
            from pro_backend.main import verify_api_key
            result = asyncio.run(verify_api_key("secret"))
            self.assertEqual(result, "secret")


def patch_context(target, value):
    """Helper: patch a module attribute."""
    from unittest.mock import patch
    return patch(target, value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
