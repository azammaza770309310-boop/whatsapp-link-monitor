#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Supabase Database Manager
يتعامل مع قاعدة بيانات Supabase البعيدة بدلاً من SQLite المحلي
"""
import os
import re
import asyncio
import logging
import hashlib
import aiohttp
from datetime import datetime
from typing import List, Dict, Optional

class SupabaseManager:
    """مدير قاعدة بيانات Supabase"""

    def __init__(self):
        self.url = os.getenv("SUPABASE_URL", "")
        self.key = os.getenv("SUPABASE_KEY", "")
        self.headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def insert_link(self, link: str, link_type: str, message_text: str,
                          group_name: str, sender_name: str, sender_contact: str,
                          source_phone: str, message_link: str = None) -> bool:
        """إدراج رابط جديد في Supabase"""
        session = await self._get_session()
        data = {
            "link": link,
            "link_type": link_type,
            "message_text": message_text[:500] if message_text else None,
            "group_name": group_name,
            "sender_name": sender_name,
            "sender_contact": sender_contact,
            "source_phone": source_phone,
            "message_link": message_link
        }
        try:
            async with session.post(f"{self.url}/rest/v1/links", json=data) as resp:
                if resp.status in (200, 201):
                    return True
                else:
                    text = await resp.text()
                    if "duplicate key" in text.lower():
                        return False  # مكرر
                    logging.error(f"Supabase insert error: {resp.status} - {text}")
                    return False
        except Exception as e:
            logging.error(f"Supabase insert exception: {e}")
            return False

    async def add_watcher(self, phone: str, display_name: str, session_string: str) -> bool:
        """إضافة مستخدم مراقب"""
        session = await self._get_session()
        data = {
            "phone": phone,
            "display_name": display_name,
            "session_string": session_string,
            "is_active": True
        }
        try:
            async with session.post(f"{self.url}/rest/v1/watchers", json=data) as resp:
                if resp.status in (200, 201):
                    return True
                text = await resp.text()
                if "duplicate key" in text.lower():
                    # تحديث الموجود
                    async with session.patch(
                        f"{self.url}/rest/v1/watchers?phone=eq.{phone}",
                        json={"display_name": display_name, "session_string": session_string, "is_active": True}
                    ) as upd_resp:
                        return upd_resp.status in (200, 204)
                return False
        except Exception as e:
            logging.error(f"Supabase add_watcher error: {e}")
            return False

    async def get_active_watchers(self) -> List[Dict]:
        """جلب كل المستخدمين المراقبين النشطين"""
        session = await self._get_session()
        try:
            async with session.get(f"{self.url}/rest/v1/watchers?is_active=eq.true&select=phone,display_name,session_string") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data
                return []
        except Exception as e:
            logging.error(f"Supabase get_watchers error: {e}")
            return []

    async def count_links(self) -> int:
        """عد الروابط"""
        session = await self._get_session()
        try:
            async with session.get(f"{self.url}/rest/v1/links?select=id&limit=1", headers={**self.headers, "Prefer": "count=exact"}) as resp:
                count = resp.headers.get("content-range", "0").split("/")[-1]
                return int(count) if count.isdigit() else 0
        except:
            return 0
