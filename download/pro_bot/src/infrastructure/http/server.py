"""HTTP server for health checks and metrics."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from aiohttp import web

from src.core.config import HttpConfig
from src.core.logging import get_logger

logger = get_logger(__name__)


class HttpServer:
    """Async HTTP server for health monitoring."""

    def __init__(
        self,
        config: HttpConfig,
        health_data_provider=None,
    ) -> None:
        self._config = config
        self._health_data_provider = health_data_provider
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    def _create_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self._health_handler)
        app.router.add_get("/health", self._health_handler)
        app.router.add_get("/metrics", self._metrics_handler)
        return app

    async def start(self) -> None:
        """Start the HTTP server."""
        app = self._create_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            self._config.host,
            self._config.port,
        )
        await self._site.start()
        logger.info(
            "HTTP server started",
            extra={
                "extra_data": {
                    "host": self._config.host,
                    "port": self._config.port,
                }
            },
        )

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        logger.info("HTTP server stopped")

    async def _health_handler(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        data: Dict[str, Any] = {"status": "ok"}
        if self._health_data_provider:
            try:
                extra = await self._health_data_provider()
                data.update(extra)
            except Exception as e:
                logger.error(f"Health data provider error: {e}")
                data["status"] = "degraded"
                data["error"] = str(e)
        return web.json_response(data)

    async def _metrics_handler(self, request: web.Request) -> web.Response:
        """Metrics endpoint."""
        if self._health_data_provider:
            try:
                data = await self._health_data_provider()
                return web.json_response(data)
            except Exception as e:
                return web.json_response(
                    {"error": str(e)}, status=500
                )
        return web.json_response({"error": "no metrics provider"}, status=404)


async def start_keep_alive(url: Optional[str]) -> asyncio.Task:
    """Start a keep-alive task that pings the service URL."""
    if not url:
        logger.info("Keep-alive disabled (no URL provided)")
        # Return a no-op task
        async def noop():
            pass
        return asyncio.create_task(noop())

    import aiohttp
    url = url.rstrip("/")
    health_url = f"{url}/health"
    logger.info(
        "Keep-alive enabled",
        extra={"extra_data": {"url": health_url}},
    )

    async def keep_alive_loop():
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    await asyncio.sleep(600)
                    async with session.get(health_url, timeout=10) as r:
                        logger.debug(
                            "Keep-alive ping",
                            extra={"extra_data": {"status": r.status}},
                        )
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"Keep-alive failed: {e}")

    return asyncio.create_task(keep_alive_loop())
