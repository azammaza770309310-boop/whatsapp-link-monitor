#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Observability tests: verify health/readiness/metrics endpoints work.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
logging.disable(logging.CRITICAL)


class TestHealthEndpointsExist(unittest.TestCase):
    """The bot must expose /health, /ready, and /metrics endpoints."""

    def test_ready_handler_defined(self):
        from monitor_v12 import ready_handler
        self.assertTrue(callable(ready_handler))

    def test_metrics_handler_defined(self):
        from monitor_v12 import metrics_handler
        self.assertTrue(callable(metrics_handler))

    def test_health_handler_defined(self):
        from monitor_v12 import health_handler
        self.assertTrue(callable(health_handler))

    def test_start_http_server_registers_all_routes(self):
        """start_http_server should register /, /health, /ready, /metrics."""
        src = Path("monitor_v12.py").read_text(encoding='utf-8')
        self.assertIn('app.router.add_get("/", health_handler)', src)
        self.assertIn('app.router.add_get("/health", health_handler)', src)
        self.assertIn('app.router.add_get("/ready", ready_handler)', src)
        self.assertIn('app.router.add_get("/metrics", metrics_handler)', src)

    def test_start_http_server_accepts_monitor_and_db(self):
        """start_http_server should accept monitor and db params for readiness checks."""
        src = Path("monitor_v12.py").read_text(encoding='utf-8')
        self.assertIn("async def start_http_server(monitor=None, db=None)", src)
        self.assertIn('app["monitor"] = monitor', src)
        self.assertIn('app["db"] = db', src)


class TestReadyHandlerLogic(unittest.IsolatedAsyncioTestCase):
    """The /ready endpoint must return 503 when DB or bot is not connected."""

    async def test_ready_returns_503_when_db_not_initialized(self):
        from monitor_v12 import ready_handler
        from aiohttp import web

        request = MagicMock()
        request.app = {}  # no db, no monitor

        response = await ready_handler(request)
        self.assertEqual(response.status, 503)

    async def test_ready_returns_200_when_db_and_bot_ok(self):
        from monitor_v12 import ready_handler

        # Mock db with working connection
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=(1,))
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        mock_db = MagicMock()
        mock_db._conn = mock_conn

        # Mock monitor with connected bot
        mock_monitor = MagicMock()
        mock_monitor.bot_client = MagicMock()
        mock_monitor.bot_client.is_connected = MagicMock(return_value=True)
        mock_monitor.user_clients = {}
        mock_monitor.is_scan_running = MagicMock(return_value=False)

        request = MagicMock()
        request.app = {"db": mock_db, "monitor": mock_monitor}

        response = await ready_handler(request)
        self.assertEqual(response.status, 200)

    async def test_ready_returns_503_when_bot_not_connected(self):
        from monitor_v12 import ready_handler

        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=(1,))
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)
        mock_db = MagicMock()
        mock_db._conn = mock_conn

        mock_monitor = MagicMock()
        mock_monitor.bot_client = None  # bot not connected
        mock_monitor.user_clients = {}

        request = MagicMock()
        request.app = {"db": mock_db, "monitor": mock_monitor}

        response = await ready_handler(request)
        self.assertEqual(response.status, 503)

    async def test_ready_returns_503_when_db_query_fails(self):
        from monitor_v12 import ready_handler

        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("DB corrupted"))
        mock_db = MagicMock()
        mock_db._conn = mock_conn

        mock_monitor = MagicMock()
        mock_monitor.bot_client = MagicMock()
        mock_monitor.bot_client.is_connected = MagicMock(return_value=True)
        mock_monitor.user_clients = {}

        request = MagicMock()
        request.app = {"db": mock_db, "monitor": mock_monitor}

        response = await ready_handler(request)
        self.assertEqual(response.status, 503)


class TestMetricsHandler(unittest.IsolatedAsyncioTestCase):
    """The /metrics endpoint must return Prometheus-format metrics."""

    async def test_metrics_returns_prometheus_format(self):
        from monitor_v12 import metrics_handler

        mock_db = MagicMock()
        mock_db.count_requests = AsyncMock(return_value=42)

        mock_monitor = MagicMock()
        mock_monitor.user_clients = {"phone1": MagicMock(), "phone2": MagicMock()}
        mock_monitor.is_scan_running = MagicMock(return_value=False)
        mock_monitor.bot_client = MagicMock()
        mock_monitor.bot_client.is_connected = MagicMock(return_value=True)
        mock_monitor._current_scan_tasks = []
        mock_monitor._login_sessions = {}

        request = MagicMock()
        request.app = {"db": mock_db, "monitor": mock_monitor}

        response = await metrics_handler(request)
        self.assertEqual(response.status, 200)
        self.assertIn("text/plain", response.content_type)

        # Should contain Prometheus metrics
        text = response.text
        self.assertIn("monitor_total_links 42", text)
        self.assertIn("monitor_active_watchers 2", text)
        self.assertIn("monitor_scan_running 0", text)
        self.assertIn("monitor_bot_connected 1", text)

    async def test_metrics_returns_503_when_monitor_not_initialized(self):
        from monitor_v12 import metrics_handler

        request = MagicMock()
        request.app = {}

        response = await metrics_handler(request)
        self.assertEqual(response.status, 503)


class TestStartupLogsExist(unittest.TestCase):
    """The bot must log startup, shutdown, and recovery events."""

    def test_startup_logging_exists(self):
        src = Path("monitor_v12.py").read_text(encoding='utf-8')
        self.assertIn("Monitor started", src,
            "Startup must be logged")

    def test_shutdown_logging_exists(self):
        src = Path("monitor_v12.py").read_text(encoding='utf-8')
        self.assertIn("Stopping...", src)
        self.assertIn("Stopped.", src)

    def test_recovery_logging_exists(self):
        """Key recovery events must be logged."""
        src = Path("monitor_v12.py").read_text(encoding='utf-8')
        # DB corruption recovery
        self.assertIn("Database corruption detected", src)
        # User client reconnection
        self.assertIn("Connecting user", src)
        self.assertIn("connected", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
