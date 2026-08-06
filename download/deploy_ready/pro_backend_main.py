#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastAPI Backend - Secure & Optimized
يربط الواجهة بقاعدة البيانات Supabase بأمان
"""
import os
import logging
import asyncio
import hmac
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from typing import Optional
import aiohttp
from datetime import datetime, timezone

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Configuration (loaded once at import time)
# -------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
VALID_API_KEY = os.getenv("API_KEY", "")  # مفتاح سري للواجهة

# -------------------------------------------------------------------
# Shared aiohttp session (created on startup, closed on shutdown)
# -------------------------------------------------------------------
_app_session: Optional[aiohttp.ClientSession] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern lifespan context manager (replaces deprecated @app.on_event)."""
    global _app_session
    if SUPABASE_URL and SUPABASE_KEY:
        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        _app_session = aiohttp.ClientSession(
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        logger.info("✅ Supabase session initialized")
    else:
        logger.warning("⚠️ SUPABASE_URL or SUPABASE_KEY not set — endpoints will return empty")
    try:
        yield
    finally:
        if _app_session and not _app_session.closed:
            await _app_session.close()
            logger.info("✅ Supabase session closed")


app = FastAPI(title="WhatsApp Monitor API", version="2.0.0", lifespan=lifespan)

# -------------------------------------------------------------------
# CORS — secure configuration
# -------------------------------------------------------------------
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
# When credentials are allowed, "*" cannot be used per CORS spec — must be explicit.
if "*" in ALLOWED_ORIGINS:
    ALLOWED_ORIGINS = ["*"]
    CORS_ALLOW_CREDENTIALS = False
else:
    CORS_ALLOW_CREDENTIALS = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["GET"],
    allow_headers=["X-API-Key", "Content-Type", "Authorization"],
)

# -------------------------------------------------------------------
# Authentication
# -------------------------------------------------------------------
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Depends(API_KEY_HEADER)):
    """التحقق من مفتاح API.

    Fail-closed: when API_KEY env is unset, ALL requests are rejected.
    Uses constant-time comparison to prevent timing attacks.
    """
    if not VALID_API_KEY:
        # Server misconfiguration: API_KEY not set
        logger.error("API_KEY environment variable is not set — rejecting all requests")
        raise HTTPException(status_code=503, detail="Server not configured for authentication")
    # Constant-time comparison (handles None api_key safely via str() coercion)
    if not api_key or not hmac.compare_digest(str(api_key), VALID_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return api_key


def get_headers():
    """Return Supabase auth headers (for count=exact requests)."""
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }


# -------------------------------------------------------------------
# Endpoints
# -------------------------------------------------------------------
@app.get("/")
async def root():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/health")
async def api_health():
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/links")
async def get_links(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    link_type: Optional[str] = Query(None, pattern="^(whatsapp|telegram|other)$"),
    search: Optional[str] = Query(None, max_length=100),
    api_key: str = Depends(verify_api_key)
):
    """جلب الروابط من Supabase بأمان"""
    if not _app_session:
        return {"links": [], "count": 0}

    # بناء الاستعلام بأمان (URL encoding handled by aiohttp params)
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit), "offset": str(offset)}
    if link_type:
        params["link_type"] = f"eq.{link_type}"
    if search:
        # تنظيف البحث من أحرف خاصة بـ PostgREST (escape % and _)
        safe_search = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["message_text"] = f"ilike.*{safe_search}*"

    try:
        async with _app_session.get(f"{SUPABASE_URL}/rest/v1/links", params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {"links": data, "count": len(data)}
            logger.error(f"Supabase links error: {resp.status} - {await resp.text()[:200]}")
            raise HTTPException(status_code=502, detail="Database error")
    except aiohttp.ClientError as e:
        logger.error(f"Network error: {e}")
        raise HTTPException(status_code=503, detail="Service unavailable")


@app.get("/api/stats")
async def get_stats(api_key: str = Depends(verify_api_key)):
    """جلب الإحصائيات بطلبات متوازية"""
    if not _app_session:
        return {"total_links": 0, "whatsapp_links": 0, "telegram_links": 0, "active_watchers": 0}

    headers = {**get_headers(), "Prefer": "count=exact"}

    async def fetch_count(url_suffix: str):
        try:
            async with _app_session.get(
                f"{SUPABASE_URL}/rest/v1/{url_suffix}",
                headers=headers
            ) as resp:
                range_header = resp.headers.get("content-range", "*/0")
                parts = range_header.split("/")
                return int(parts[-1]) if parts[-1].isdigit() else 0
        except Exception:
            return 0

    try:
        # طلبات متوازية بدلاً من متسلسلة
        total, wa, tg, watchers = await asyncio.gather(
            fetch_count("links?select=id&limit=1"),
            fetch_count("links?link_type=eq.whatsapp&select=id&limit=1"),
            fetch_count("links?link_type=eq.telegram&select=id&limit=1"),
            fetch_count("watchers?is_active=eq.true&select=id&limit=1")
        )
        return {
            "total_links": total,
            "whatsapp_links": wa,
            "telegram_links": tg,
            "active_watchers": watchers
        }
    except Exception as e:
        logger.error(f"Stats error: {e}")
        raise HTTPException(status_code=500, detail="Internal error")
