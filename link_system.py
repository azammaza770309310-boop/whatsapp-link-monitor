#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Production Link Management System — Production Grade Architecture
==================================================================

Designed by: Senior/Principal Telethon Engineer
Goal: Zero FloodWait, zero bans, months of uptime.

Architecture:
  Event Handler → extract link → normalize → enqueue to DB → DONE (zero API calls)
  
  Scheduler (every 60s):
    → pick QUEUED link
    → check membership (Hybrid Cache: DB → Memory → API)
    → AI verify (only if new)
    → Rate Limiter gate
    → Join (via Rate Limiter)
    → update state machine

Components:
  1. LinkNormalizer — unified link format
  2. StateMachine — 12 states per group
  3. RateLimiter — centralized, all Telegram calls pass through
  4. FloodWaitManager — DB-backed, no retry loops
  5. MembershipCache — hybrid (DB → Memory → API)
  6. Scheduler — picks 1 task per minute, respects FloodWait
  7. Worker — processes link queue
  8. Metrics — real monitoring
"""

import asyncio
import hashlib
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import aiosqlite

# -------------------------------------------------------------------
# 1. Link Normalizer — unified format for all link variants
# -------------------------------------------------------------------

class LinkNormalizer:
    """يوحد جميع صيغ الروابط في صيغة واحدة قابلة للمقارنة."""

    # Patterns for Telegram links
    TG_PATTERNS = [
        re.compile(r'(?:https?://)?t\.me/(\+[\w]+|joinchat/[\w]+|[\w]+)(?:/(\d+))?', re.I),
        re.compile(r'(?:https?://)?telegram\.me/(\+[\w]+|joinchat/[\w]+|[\w]+)(?:/(\d+))?', re.I),
    ]
    # WhatsApp patterns
    WA_PATTERNS = [
        re.compile(r'(?:https?://)?chat\.whatsapp\.com/([\w]+)', re.I),
    ]

    @staticmethod
    def extract_links(text: str) -> List[dict]:
        """يستخرج كل الروابط من النص ويعيدها بصيغة موحدة.
        
        Returns: [{raw, normalized, link_type, username, invite_hash, msg_id}, ...]
        
        Note: يستبعد روابط الرسائل (t.me/username/123) لأنها مو دعوات انضمام.
        """
        if not text:
            return []

        links = []
        seen = set()

        # Telegram links
        for pattern in LinkNormalizer.TG_PATTERNS:
            for match in pattern.finditer(text):
                raw = match.group(0)
                identifier = match.group(1)
                msg_id = match.group(2) if match.lastindex >= 2 else None

                # === استبعاد روابط الرسائل (t.me/username/123) ===
                # هذه روابط رسائل في قنوات، مو دعوات انضمام
                if msg_id is not None:
                    continue

                # Determine type
                if identifier.startswith('+') or identifier.startswith('joinchat/'):
                    invite_hash = identifier.lstrip('+').replace('joinchat/', '')
                    normalized = f"tg:invite:{invite_hash.lower()}"
                    link_type = "telegram_private"
                    username = None
                else:
                    username = identifier.lower()
                    normalized = f"tg:user:{username}"
                    link_type = "telegram"
                    invite_hash = None

                if normalized not in seen:
                    seen.add(normalized)
                    # Ensure raw starts with https://
                    if not raw.startswith('http'):
                        raw = 'https://' + raw
                    # إزالة /msg_id من الرابط لو موجود بالخطأ
                    raw = re.sub(r'/\d+$', '', raw)
                    links.append({
                        'raw': raw,
                        'normalized': normalized,
                        'link_type': link_type,
                        'username': username,
                        'invite_hash': invite_hash,
                        'msg_id': None,  # دايماً None — ما نريد روابط رسائل
                    })

        # WhatsApp links
        for pattern in LinkNormalizer.WA_PATTERNS:
            for match in pattern.finditer(text):
                raw = match.group(0)
                invite = match.group(1).lower()
                normalized = f"wa:invite:{invite}"
                if normalized not in seen:
                    seen.add(normalized)
                    if not raw.startswith('http'):
                        raw = 'https://' + raw
                    links.append({
                        'raw': raw,
                        'normalized': normalized,
                        'link_type': 'whatsapp',
                        'username': None,
                        'invite_hash': invite,
                        'msg_id': None,
                    })

        return links


# -------------------------------------------------------------------
# 2. State Machine — 12 states per group
# -------------------------------------------------------------------

class GroupState:
    """حالات المجموعة في قاعدة البيانات."""
    UNKNOWN = 'UNKNOWN'
    DISCOVERED = 'DISCOVERED'
    QUEUED = 'QUEUED'
    JOINING = 'JOINING'
    JOINED = 'JOINED'
    FAILED = 'FAILED'
    PRIVATE = 'PRIVATE'
    INVALID = 'INVALID'
    EXPIRED = 'EXPIRED'
    FLOODWAIT = 'FLOODWAIT'
    BANNED = 'BANNED'
    ALREADY_MEMBER = 'ALREADY_MEMBER'


# -------------------------------------------------------------------
# 3. Rate Limiter — centralized, all Telegram calls pass through
# -------------------------------------------------------------------

class RateLimiter:
    """Rate Limiter مركزي بحدود منفصلة لكل نوع عملية.

    الحدود:
    - JOIN: 5 انضمام/ساعة لكل حساب
    - IMPORT_INVITE: 5/ساعة
    - GET_ENTITY: 30/دقيقة (مع cache)
    - MESSAGE_SEND: 20/دقيقة
    - GENERIC: 20/دقيقة
    """

    # حدود كل نوع عملية (مخففة)
    OP_LIMITS = {
        'join':              {'max': 5,   'window': 3600, 'min_delay': 120},  # 5/ساعة, 120 ثانية بين كل واحد
        'import_invite':     {'max': 5,   'window': 3600, 'min_delay': 120},
        'join_channel':      {'max': 5,   'window': 3600, 'min_delay': 120},
        'get_entity':        {'max': 30,  'window': 60,   'min_delay': 2},
        'membership_check':  {'max': 20,  'window': 60,   'min_delay': 3},
        'message_send':      {'max': 20,  'window': 60,   'min_delay': 3},
        'generic':           {'max': 20,  'window': 60,   'min_delay': 3},
    }

    def __init__(self, db):
        self.db = db
        # Per-phone, per-operation tracking: {(phone, op): [timestamps]}
        self._requests: Dict[Tuple[str, str], List[float]] = {}
        # Per-phone lock
        self._locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    def _get_limit(self, operation: str) -> dict:
        """يجلب إعدادات الحد للعملية."""
        # Join aliases
        if operation in ('join', 'join_channel', 'import_invite'):
            # shared limit for all join operations
            return self.OP_LIMITS['join']
        return self.OP_LIMITS.get(operation, self.OP_LIMITS['generic'])

    async def check(self, phone: str, operation: str = 'generic') -> bool:
        """تحقق فقط هل يمكن تنفيذ العملية بدون حجز أو تسجيل.

        على عكس acquire()، هذه لا تضيف timestamp للذاكرة ولا تسجل في DB.
        تستخدم للفحص المسبق قبل استدعاء acquire() الفعلي.
        """
        async with self._global_lock:
            if phone not in self._locks:
                self._locks[phone] = asyncio.Lock()

        async with self._locks[phone]:
            now = time.time()

            # 1. Check FloodWait from DB
            floodwait = await self.db.get_floodwait(phone)
            if floodwait and floodwait > now:
                wait = int(floodwait - now)
                logging.warning(f"[RATE] {phone} FloodWait active ({wait}s left) — blocked {operation}")
                return False

            # 2. Determine limit
            limit = self._get_limit(operation)
            max_count = limit['max']
            window = limit['window']

            # 3. For join operations: check Memory + DB
            if operation in ('join', 'join_channel', 'import_invite'):
                all_join_times = []
                for join_op in ('join', 'join_channel', 'import_invite'):
                    key = (phone, join_op)
                    if key in self._requests:
                        all_join_times.extend([t for t in self._requests[key] if now - t < window])
                mem_count = len(all_join_times)
                db_count = await self.db.count_operations(phone, 'join', window)
                total_count = max(mem_count, db_count)

                if total_count >= max_count:
                    logging.warning(f"[RATE] {phone} join limit ({total_count}/{max_count} in {window}s) — blocked (check)")
                    return False
                return True
            else:
                key = (phone, operation)
                if key in self._requests:
                    self._requests[key] = [t for t in self._requests[key] if now - t < window]
                else:
                    self._requests[key] = []
                if len(self._requests[key]) >= max_count:
                    logging.warning(f"[RATE] {phone} {operation} limit ({max_count}/{window}s) — blocked (check)")
                    return False
                return True

    async def acquire(self, phone: str, operation: str = 'generic') -> bool:
        """تحقق هل يمكن تنفيذ استدعاء الآن. يحجز العملية لكن لا يسجلها كنجاح.

        IMPORTANT: هذه الطريقة تحجز (reserve) العملية فقط.
        لتسجيل العملية كنجاح فعلي، استخدم record_success() بعد نجاح API.

        يستخدم Memory + DB للتتبع (يستمر بعد إعادة التشغيل).
        """
        async with self._global_lock:
            if phone not in self._locks:
                self._locks[phone] = asyncio.Lock()

        async with self._locks[phone]:
            now = time.time()

            # 1. Check FloodWait from DB
            floodwait = await self.db.get_floodwait(phone)
            if floodwait and floodwait > now:
                wait = int(floodwait - now)
                logging.warning(f"[RATE] {phone} FloodWait active ({wait}s left) — blocked {operation}")
                return False

            # 2. Determine limit
            limit = self._get_limit(operation)
            max_count = limit['max']
            window = limit['window']
            min_delay = limit['min_delay']

            # 3. For join operations: check Memory + DB (with reserved but not confirmed)
            if operation in ('join', 'join_channel', 'import_invite'):
                # Memory count (includes reserved timestamps)
                all_join_times = []
                for join_op in ('join', 'join_channel', 'import_invite'):
                    key = (phone, join_op)
                    if key in self._requests:
                        all_join_times.extend([t for t in self._requests[key] if now - t < window])
                mem_count = len(all_join_times)

                # DB count (confirmed operations only)
                db_count = await self.db.count_operations(phone, 'join', window)
                # Use max of both — reserved count includes pending
                total_count = max(mem_count, db_count)

                if total_count >= max_count:
                    logging.warning(f"[RATE] {phone} join limit ({total_count}/{max_count} in {window}s) — blocked")
                    return False

                # Min delay
                if all_join_times:
                    last = max(all_join_times)
                    elapsed = now - last
                    if elapsed < min_delay:
                        import random
                        wait = min_delay - elapsed + random.uniform(5, 15)
                        logging.debug(f"[RATE] {phone} join cooldown {wait:.0f}s")
                        await asyncio.sleep(wait)

                # === RESERVE ONLY — do NOT log_operation yet ===
                # Will be confirmed by record_success() after API succeeds
            else:
                # Non-join: Memory only (less critical)
                key = (phone, operation)
                if key in self._requests:
                    self._requests[key] = [t for t in self._requests[key] if now - t < window]
                else:
                    self._requests[key] = []

                if len(self._requests[key]) >= max_count:
                    logging.warning(f"[RATE] {phone} {operation} limit ({max_count}/{window}s) — blocked")
                    return False

                if self._requests[key]:
                    last = self._requests[key][-1]
                    elapsed = now - last
                    if elapsed < min_delay:
                        import random
                        wait = min_delay - elapsed + random.uniform(0.5, 2.0)
                        logging.debug(f"[RATE] {phone} waiting {wait:.1f}s before {operation}")
                        await asyncio.sleep(wait)

                # Log membership_check to DB (these are read-only, safe to count immediately)
                if operation == 'membership_check':
                    await self.db.log_operation(phone, 'membership_check')

            # 4. Record reservation in Memory (timestamp marks attempt)
            key = (phone, operation)
            if key not in self._requests:
                self._requests[key] = []
            self._requests[key].append(time.time())
            return True

    async def record_success(self, phone: str, operation: str = 'generic'):
        """سجل نجاح العملية في DB — استدعِها فقط بعد نجاح Telegram API.

        هذا يفصل بين "reservation" (acquire) و "confirmation" (record_success).
        العمليات الفاشلة لا تُسجل في DB، فلا تضخم العدادات.
        """
        if operation in ('join', 'join_channel', 'import_invite'):
            # سجل كـ 'join' موحد — كل أنواع الانضمام تشترك في الحد
            try:
                await self.db.log_operation(phone, 'join')
                logging.debug(f"[RATE] {phone} recorded successful {operation}")
            except Exception as e:
                logging.error(f"[RATE] Failed to record operation: {e}")

    async def record_floodwait(self, phone: str, seconds: int):
        """سجل FloodWait في قاعدة البيانات."""
        next_retry = time.time() + seconds
        await self.db.set_floodwait(phone, next_retry)
        logging.warning(f"[RATE] {phone} FloodWait: {seconds}s (retry after {datetime.fromtimestamp(next_retry).strftime('%H:%M:%S')})")


# -------------------------------------------------------------------
# 4. FloodWait Manager — DB-backed, no retry loops
# -------------------------------------------------------------------

class FloodWaitManager:
    """يدير FloodWait عبر قاعدة البيانات — لا retry loops."""

    def __init__(self, db):
        self.db = db

    async def is_blocked(self, phone: str) -> Tuple[bool, int]:
        """تحقق هل الحساب محظور الآن. يعيد (is_blocked, seconds_remaining)."""
        next_retry = await self.db.get_floodwait(phone)
        if next_retry is None:
            return False, 0
        now = time.time()
        if next_retry > now:
            return True, int(next_retry - now)
        # FloodWait expired — clear it
        await self.db.clear_floodwait(phone)
        return False, 0

    async def block(self, phone: str, seconds: int):
        """حظر الحساب لمدة محددة."""
        await self.db.set_floodwait(phone, time.time() + seconds)

    async def get_blocked_accounts(self) -> List[dict]:
        """جلب كل الحسابات المحظورة حالياً."""
        return await self.db.get_all_floodwaits()


# -------------------------------------------------------------------
# 5. Membership Cache — hybrid (DB → Memory → API)
# -------------------------------------------------------------------

class MembershipCache:
    """Hybrid Cache للعضوية: Database → Memory → Telegram API.
    
    الترتيب:
    1. فحص الذاكرة (سريع جداً)
    2. فحص قاعدة البيانات (سريع)
    3. استدعاء API (بطيء، بس عند الحاجة)
    4. تحديث الذاكرة + قاعدة البيانات
    """

    def __init__(self, db, rate_limiter):
        self.db = db
        self.rate_limiter = rate_limiter
        # Memory cache: {(phone, normalized_link): (is_member, timestamp)}
        self._cache: Dict[Tuple[str, str], Tuple[bool, float]] = {}
        self._cache_ttl = 3600  # 1 hour in memory
        self._db_ttl_days = 7  # 7 days in DB — لا يعيد فحص قبلها
        self._lock = asyncio.Lock()

    async def check_membership(self, phone: str, normalized_link: str, client=None) -> Optional[bool]:
        """يفحص العضوية بالترتيب: Memory → DB → API.
        
        Returns: True (مشترك), False (غير مشترك), None (تعذر الفحص)
        """
        cache_key = (phone, normalized_link)
        now = time.time()

        # 1. Memory cache
        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached and (now - cached[1]) < self._cache_ttl:
                logging.debug(f"[CACHE] Memory hit: {phone} → {normalized_link} = {cached[0]}")
                return cached[0]

        # 2. Database cache (with 7-day TTL)
        db_result = await self.db.get_membership_with_ttl(phone, normalized_link, self._db_ttl_days)
        if db_result is not None:
            # Update memory
            async with self._lock:
                self._cache[cache_key] = (db_result, now)
            logging.debug(f"[CACHE] DB hit: {phone} → {normalized_link} = {db_result}")
            return db_result

        # 3. Telegram API (only if needed)
        if client is None or not client.is_connected():
            return None

        # Check rate limiter
        allowed = await self.rate_limiter.acquire(phone, 'membership_check')
        if not allowed:
            return None

        api_result = await self._api_check(client, normalized_link, phone)

        # 4. Update DB + Memory
        if api_result is not None:
            await self.db.set_membership(phone, normalized_link, api_result)
            async with self._lock:
                self._cache[cache_key] = (api_result, now)

        return api_result

    async def _api_check(self, client, normalized_link: str, phone: str) -> Optional[bool]:
        """فحص العضوية عبر Telegram API — استدعاء واحد فقط."""
        try:
            from telethon.tl.functions.channels import GetParticipantRequest
            from telethon.errors import UserNotParticipantError, FloodWaitError

            # Parse normalized link
            if normalized_link.startswith('tg:user:'):
                username = normalized_link.split('tg:user:')[1]
            elif normalized_link.startswith('tg:invite:'):
                # Private links can't be checked via GetParticipantRequest
                return None
            else:
                return None

            # Get entity (1 API call)
            entity = await client.get_entity(username)

            # === DISTINGUISH ENTITY TYPES ===
            # User: has first_name, no broadcast, no megagroup → NOT a joinable target
            # Channel: broadcast=True → broadcast channel
            # Megagroup: megagroup=True → supergroup (joinable)
            # Gigagroup: gigagroup=True → broadcast-style group (joinable)
            # Regular Chat: no broadcast, no megagroup → basic group

            is_user = (
                hasattr(entity, 'first_name') and entity.first_name
                and not hasattr(entity, 'megagroup')
                and not hasattr(entity, 'broadcast')
                and not hasattr(entity, 'gigagroup')
            )

            if is_user:
                # Entity is a USER, not a group/channel — cannot be "member" of a user
                logging.debug(f"[CACHE] {phone} → {normalized_link} = USER (not a group)")
                return None  # Not applicable, don't cache

            # Check membership (1 API call)
            try:
                await client(GetParticipantRequest(channel=entity, participant="me"))
                return True  # Is member
            except UserNotParticipantError:
                return False  # Not member
            except FloodWaitError as e:
                await self.rate_limiter.record_floodwait(phone, e.seconds)
                return None
            except Exception as e:
                logging.debug(f"[CACHE] GetParticipant failed for {phone}: {type(e).__name__}: {e}")
                return None

        except FloodWaitError as e:
            await self.rate_limiter.record_floodwait(phone, e.seconds)
            return None
        except Exception as e:
            logging.debug(f"[CACHE] API check failed for {phone}: {type(e).__name__}: {e}")
            return None

    def invalidate(self, phone: str = None, normalized_link: str = None):
        """إلغاء صحة الكاش."""
        async def _do_invalidate():
            async with self._lock:
                if phone is None:
                    self._cache.clear()
                elif normalized_link is None:
                    # Remove all entries for this phone
                    self._cache = {k: v for k, v in self._cache.items() if k[0] != phone}
                else:
                    self._cache.pop((phone, normalized_link), None)
        # Fire and forget
        asyncio.create_task(_do_invalidate())


# -------------------------------------------------------------------
# 6. Metrics — real monitoring
# -------------------------------------------------------------------

class Metrics:
    """يجمع إحصائيات حقيقية للمراقبة مع Logs تفصيلية."""

    def __init__(self):
        self._data = {
            'api_calls_per_account': {},    # {phone: count}
            'join_attempts_per_account': {}, # {phone: count} — محاولات الانضمام
            'joins_today_per_account': {},  # {phone: count} — انضمامات ناجحة اليوم
            'floodwait_per_account': {},    # {phone: count}
            'last_join_per_account': {},    # {phone: timestamp}
            'total_joins': 0,
            'total_floodwait': 0,
            'total_peerflood': 0,
            'queue_size': 0,
            'total_retries': 0,
            'total_skips': 0,
            'skip_reasons': {},             # {reason: count}
            'total_duplicates': 0,          # روابط مكررة تم تجاهلها (DB)
            'membership_skips': 0,          # روابط تم تجاهلها بسبب العضوية
            'processing_times': [],         # [seconds, ...]
        }
        self._lock = asyncio.Lock()

    async def record_api_call(self, phone: str):
        async with self._lock:
            self._data['api_calls_per_account'][phone] = self._data['api_calls_per_account'].get(phone, 0) + 1

    async def record_join_attempt(self, phone: str):
        """سجل محاولة انضمام لحساب."""
        async with self._lock:
            self._data['join_attempts_per_account'][phone] = self._data['join_attempts_per_account'].get(phone, 0) + 1
            logging.info(f"[METRIC] Join attempt #{self._data['join_attempts_per_account'][phone]} for {phone}")

    async def record_join_success(self, phone: str):
        """سجل انضمام ناجح."""
        async with self._lock:
            self._data['total_joins'] += 1
            self._data['joins_today_per_account'][phone] = self._data['joins_today_per_account'].get(phone, 0) + 1
            self._data['last_join_per_account'][phone] = datetime.now().isoformat()
            logging.info(f"[METRIC] ✅ Join SUCCESS for {phone} (total today: {self._data['joins_today_per_account'][phone]})")

    async def record_join(self):
        """Legacy method — calls record_join_success without phone."""
        async with self._lock:
            self._data['total_joins'] += 1

    async def record_floodwait(self, phone: str = None):
        """سجل FloodWait."""
        async with self._lock:
            self._data['total_floodwait'] += 1
            if phone:
                self._data['floodwait_per_account'][phone] = self._data['floodwait_per_account'].get(phone, 0) + 1
                logging.warning(f"[METRIC] ⚠️ FloodWait #{self._data['floodwait_per_account'][phone]} for {phone}")

    async def record_skip(self, reason: str = ''):
        """سجل تجاهل رابط مع السبب."""
        async with self._lock:
            self._data['total_skips'] += 1
            if reason:
                self._data['skip_reasons'][reason] = self._data['skip_reasons'].get(reason, 0) + 1
                logging.info(f"[METRIC] Skipped link: {reason} (total: {self._data['skip_reasons'][reason]})")

    async def record_duplicate(self):
        """سجل رابط مكرر تم تجاهله بقاعدة البيانات."""
        async with self._lock:
            self._data['total_duplicates'] += 1
            if self._data['total_duplicates'] % 10 == 0:
                logging.info(f"[METRIC] {self._data['total_duplicates']} duplicate links skipped by DB")

    async def record_membership_skip(self):
        """سجل رابط تم تجاهله لأن الحساب منضم سابقاً."""
        async with self._lock:
            self._data['membership_skips'] += 1
            if self._data['membership_skips'] % 5 == 0:
                logging.info(f"[METRIC] {self._data['membership_skips']} links skipped (already member)")

    async def record_processing_time(self, seconds: float):
        async with self._lock:
            self._data['processing_times'].append(seconds)
            if len(self._data['processing_times']) > 100:
                self._data['processing_times'] = self._data['processing_times'][-100:]

    async def update_queue_size(self, size: int):
        async with self._lock:
            self._data['queue_size'] = size

    async def get_summary(self) -> dict:
        async with self._lock:
            times = self._data['processing_times']
            avg_time = sum(times) / len(times) if times else 0
            return {
                **self._data,
                'avg_processing_time': round(avg_time, 2),
                'processing_times': [],  # Don't return the full array
            }

    def format_prometheus(self) -> str:
        """يصدر الإحصائيات بصيغة Prometheus."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            data = loop.run_until_complete(self.get_summary())
        except:
            data = self._data

        lines = []
        lines.append(f"# HELP link_system_total_joins Total successful joins")
        lines.append(f"# TYPE link_system_total_joins counter")
        lines.append(f"link_system_total_joins {data.get('total_joins', 0)}")
        lines.append(f"# HELP link_system_total_floodwait Total FloodWait errors")
        lines.append(f"# TYPE link_system_total_floodwait counter")
        lines.append(f"link_system_total_floodwait {data.get('total_floodwait', 0)}")
        lines.append(f"# HELP link_system_queue_size Current queue size")
        lines.append(f"# TYPE link_system_queue_size gauge")
        lines.append(f"link_system_queue_size {data.get('queue_size', 0)}")
        lines.append(f"# HELP link_system_total_skips Total skipped links")
        lines.append(f"# TYPE link_system_total_skips counter")
        lines.append(f"link_system_total_skips {data.get('total_skips', 0)}")
        lines.append(f"# HELP link_system_total_duplicates Total duplicate links rejected")
        lines.append(f"# TYPE link_system_total_duplicates counter")
        lines.append(f"link_system_total_duplicates {data.get('total_duplicates', 0)}")
        lines.append(f"# HELP link_system_avg_processing_time Average link processing time in seconds")
        lines.append(f"# TYPE link_system_avg_processing_time gauge")
        lines.append(f"link_system_avg_processing_time {data.get('avg_processing_time', 0)}")
        return '\n'.join(lines) + '\n'


# -------------------------------------------------------------------
# 7. Database Extensions — new tables for the production system
# -------------------------------------------------------------------

async def init_production_tables(db):
    """ينشئ الجداول الجديدة للنظام الإنتاجي.
    
    Tables:
    - link_queue: قائمة الروابط المنتظرة للمعالجة
    - group_states: حالة كل مجموعة (state machine)
    - membership_cache: كاش العضوية في DB
    - floodwait_tracker: حظر FloodWait لكل حساب
    - metrics_log: سجل الإحصائيات
    """
    conn = await db._ensure_conn()

    # جدول قائمة الروابط المنتظرة
    await conn.execute("""CREATE TABLE IF NOT EXISTS link_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_link TEXT NOT NULL,
        normalized_link TEXT NOT NULL,
        link_type TEXT,
        username TEXT,
        invite_hash TEXT,
        msg_id TEXT,
        group_name TEXT,
        sender_name TEXT,
        sender_contact TEXT,
        source_phone TEXT,
        message_text TEXT,
        message_link TEXT,
        status TEXT DEFAULT 'QUEUED',
        enqueued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        processed_at TIMESTAMP,
        attempt_count INTEGER DEFAULT 0,
        last_error TEXT,
        next_retry_at TIMESTAMP,
        member_count INTEGER,
        priority INTEGER DEFAULT 3,
        UNIQUE(normalized_link)
    )""")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON link_queue (status)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_next_retry ON link_queue (next_retry_at)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_priority ON link_queue (priority, status)")
    # migration: أضف الأعمدة لو الجدول قديم
    try:
        await conn.execute("ALTER TABLE link_queue ADD COLUMN member_count INTEGER")
    except Exception:
        pass  # العمود موجود
    try:
        await conn.execute("ALTER TABLE link_queue ADD COLUMN priority INTEGER DEFAULT 3")
    except Exception:
        pass

    # جدول حالة المجموعات (state machine)
    await conn.execute("""CREATE TABLE IF NOT EXISTS group_states (
        normalized_link TEXT PRIMARY KEY,
        raw_link TEXT,
        group_title TEXT,
        state TEXT DEFAULT 'UNKNOWN',
        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_attempt TIMESTAMP,
        attempt_count INTEGER DEFAULT 0,
        last_error TEXT,
        next_retry_at TIMESTAMP,
        joined_by TEXT,
        member_count INTEGER
    )""")

    # جدول كاش العضوية
    await conn.execute("""CREATE TABLE IF NOT EXISTS membership_cache (
        phone TEXT NOT NULL,
        normalized_link TEXT NOT NULL,
        is_member INTEGER,
        checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (phone, normalized_link)
    )""")

    # جدول FloodWait
    await conn.execute("""CREATE TABLE IF NOT EXISTS floodwait_tracker (
        phone TEXT PRIMARY KEY,
        next_retry_at REAL NOT NULL,
        reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # جدول سجل العمليات الحساسة (DB-backed Rate Limiter)
    await conn.execute("""CREATE TABLE IF NOT EXISTS api_operations_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone TEXT NOT NULL,
        action_type TEXT NOT NULL,
        timestamp REAL NOT NULL,
        success INTEGER DEFAULT 1
    )""")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_op_phone_type ON api_operations_log (phone, action_type)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_op_timestamp ON api_operations_log (timestamp)")

    # جدول إعدادات النظام (system_settings) — يحفظ JOIN_PAUSED عبر إعادة التشغيل
    await conn.execute("""CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # جدول المجموعات المراقبة (monitored_chats) — يمنع التكرار بين المراقبين
    await conn.execute("""CREATE TABLE IF NOT EXISTS monitored_chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        chat_title TEXT,
        username TEXT,
        link_type TEXT,
        monitored_by TEXT,
        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        member_count INTEGER,
        ai_classification TEXT,
        ai_country TEXT,
        ai_relevance INTEGER,
        ai_description TEXT,
        should_monitor INTEGER DEFAULT 1,
        UNIQUE(chat_id)
    )""")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_monitored_chat_id ON monitored_chats (chat_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_monitored_should ON monitored_chats (should_monitor)")

    await conn.commit()
    logging.info("✅ Production tables initialized (link_queue, group_states, membership_cache, floodwait_tracker, api_operations_log, system_settings, monitored_chats)")


# -------------------------------------------------------------------
# Database helper methods (added to DatabaseManager)
# -------------------------------------------------------------------

class ProductionDB:
    """يغلف عمليات قاعدة البيانات الخاصة بالنظام الإنتاجي."""

    def __init__(self, db):
        self.db = db  # Original DatabaseManager

    async def _conn(self):
        return await self.db._ensure_conn()

    # === Link Queue ===

    async def enqueue_link(self, link_data: dict, allow_requeue: bool = False) -> bool:
        """يضيف رابط لقائمة الانتظار.

        Args:
            link_data: dict يحتوي على raw, normalized, link_type, username, ...
            allow_requeue: لو True، يعيد الرابط لـ QUEUED لو كان DONE/REJECTED سابقاً

        Returns:
            True لو انضاف جديد أو أُعيد لـ QUEUED
            False لو مكرر وباقي QUEUED (ما يحتاج إعادة)
        """
        conn = await self._conn()
        try:
            # محاولة إدخال جديد
            cursor = await conn.execute(
                """INSERT OR IGNORE INTO link_queue
                (raw_link, normalized_link, link_type, username, invite_hash, msg_id,
                 group_name, sender_name, sender_contact, source_phone, message_text, message_link, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED')""",
                (link_data['raw'], link_data['normalized'], link_data['link_type'],
                 link_data.get('username'), link_data.get('invite_hash'), link_data.get('msg_id'),
                 link_data.get('group_name', ''), link_data.get('sender_name', ''),
                 link_data.get('sender_contact', ''), link_data.get('source_phone', ''),
                 link_data.get('message_text', ''), link_data.get('message_link'))
            )
            await conn.commit()
            if cursor.rowcount > 0:
                return True  # انضاف جديد

            # لو ما انضاف (مكرر) و allow_requeue=True — حاول إعادة لـ QUEUED
            if allow_requeue:
                cursor = await conn.execute(
                    """UPDATE link_queue
                       SET status = 'QUEUED',
                           member_count = NULL,
                           priority = 3,
                           next_retry_at = NULL,
                           attempt_count = 0,
                           last_error = NULL
                       WHERE normalized_link = ?
                       AND status IN ('DONE', 'REJECTED', 'FAILED')""",
                    (link_data['normalized'],)
                )
                await conn.commit()
                if cursor.rowcount > 0:
                    logging.info(f"[ENQUEUE] Re-queued: {link_data['raw'][:50]}")
                    return True

            return False
        except Exception as e:
            logging.error(f"Enqueue error: {e}")
            return False

    async def get_link_status(self, normalized_link: str) -> Optional[str]:
        """يجيب status رابط في queue."""
        conn = await self._conn()
        cursor = await conn.execute(
            "SELECT status FROM link_queue WHERE normalized_link = ?",
            (normalized_link,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_queued_links(self, limit: int = 1) -> List[dict]:
        """يجلب روابط QUEUED جاهزة للمعالجة — مرتبة حسب الأولوية.

        الأولوية:
          1 = member_count >= 10,000 (تجمع عالي)
          2 = member_count >= 1,000 (تجمع متوسط)
          3 = member_count < 1,000 أو غير معروف
        """
        conn = await self._conn()
        now = datetime.now().isoformat()
        cursor = await conn.execute(
            """SELECT id, raw_link, normalized_link, link_type, username, invite_hash,
                      msg_id, group_name, sender_name, sender_contact, source_phone,
                      message_text, message_link, attempt_count, member_count, priority
               FROM link_queue 
               WHERE status = 'QUEUED' 
               AND (next_retry_at IS NULL OR next_retry_at <= ?)
               ORDER BY priority ASC, member_count DESC NULLS LAST, enqueued_at ASC LIMIT ?""",
            (now, limit))
        rows = await cursor.fetchall()
        return [{'id': r[0], 'raw_link': r[1], 'normalized_link': r[2], 'link_type': r[3],
                 'username': r[4], 'invite_hash': r[5], 'msg_id': r[6], 'group_name': r[7],
                 'sender_name': r[8], 'sender_contact': r[9], 'source_phone': r[10],
                 'message_text': r[11], 'message_link': r[12], 'attempt_count': r[13],
                 'member_count': r[14], 'priority': r[15]} for r in rows]

    async def update_link_priority(self, link_id: int, member_count: int) -> None:
        """يحدّث member_count و priority لرابط في القائمة.

        priority:
          1 = HIGH (>= 5,000 عضو)
          2 = MEDIUM (>= 1,000 عضو)
          3 = LOW (>= 500 عضو)
          4 = REJECT (< 500 عضو — ما ينضم)
        """
        if member_count is None or member_count <= 0:
            priority = 3
        elif member_count >= 5000:
            priority = 1
        elif member_count >= 1000:
            priority = 2
        elif member_count >= 500:
            priority = 3
        else:
            priority = 3

        conn = await self._conn()
        await conn.execute(
            """UPDATE link_queue SET member_count = ?, priority = ? WHERE id = ?""",
            (member_count, priority, link_id))
        await conn.commit()

    async def get_unscored_links(self, limit: int = 10) -> List[dict]:
        """يجلب روابط QUEUED بدون member_count — لأخذ الأولوية.

        تُستخدم من قبل مهمة _priority_scorer الخلفية.
        """
        conn = await self._conn()
        cursor = await conn.execute(
            """SELECT id, raw_link, normalized_link, link_type, username
               FROM link_queue
               WHERE status = 'QUEUED' AND member_count IS NULL
               ORDER BY enqueued_at ASC LIMIT ?""",
            (limit,))
        rows = await cursor.fetchall()
        return [{'id': r[0], 'raw_link': r[1], 'normalized_link': r[2],
                 'link_type': r[3], 'username': r[4]} for r in rows]

    async def update_queue_status(self, link_id: int, status: str, error: str = None, next_retry: datetime = None):
        """يحدّث حالة رابط في القائمة."""
        conn = await self._conn()
        await conn.execute(
            """UPDATE link_queue SET status = ?, last_error = ?, next_retry_at = ?,
               processed_at = ?, attempt_count = attempt_count + 1 WHERE id = ?""",
            (status, error, next_retry.isoformat() if next_retry else None,
             datetime.now().isoformat(), link_id))
        await conn.commit()

    async def get_queue_size(self) -> int:
        """عدد الروابط في QUEUED."""
        conn = await self._conn()
        cursor = await conn.execute("SELECT COUNT(*) FROM link_queue WHERE status = 'QUEUED'")
        row = await cursor.fetchone()
        return row[0] if row else 0

    # === Monitored Chats ===

    async def is_chat_monitored(self, chat_id: int) -> bool:
        """يتحقق هل المجموعة مراقبة بالفعل (يمنع التكرار)."""
        conn = await self._conn()
        cursor = await conn.execute(
            "SELECT 1 FROM monitored_chats WHERE chat_id = ? AND should_monitor = 1",
            (chat_id,))
        row = await cursor.fetchone()
        return row is not None

    async def add_monitored_chat(self, chat_id: int, chat_title: str, username: str = '',
                                  link_type: str = '', monitored_by: str = '',
                                  member_count: int = 0) -> bool:
        """يضيف مجموعة للمراقبة — يرجع True لو جديدة، False لو مكررة."""
        conn = await self._conn()
        try:
            await conn.execute(
                """INSERT OR IGNORE INTO monitored_chats
                (chat_id, chat_title, username, link_type, monitored_by, member_count, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (chat_id, chat_title, username, link_type, monitored_by, member_count,
                 datetime.now().isoformat(), datetime.now().isoformat()))
            await conn.commit()
            return conn.total_changes > 0
        except Exception as e:
            logging.error(f"add_monitored_chat error: {e}")
            return False

    async def update_monitored_chat(self, chat_id: int, **fields):
        """يحدّث بيانات مجموعة مراقبة (مثل AI classification)."""
        if not fields:
            return
        conn = await self._conn()
        set_parts = []
        values = []
        for k, v in fields.items():
            set_parts.append(f"{k} = ?")
            values.append(v)
        set_parts.append("last_seen = ?")
        values.append(datetime.now().isoformat())
        values.append(chat_id)
        await conn.execute(
            f"UPDATE monitored_chats SET {', '.join(set_parts)} WHERE chat_id = ?",
            values)
        await conn.commit()

    async def get_monitored_chats(self, limit: int = 200) -> List[dict]:
        """يجلب كل المجموعات المراقبة."""
        conn = await self._conn()
        cursor = await conn.execute(
            """SELECT chat_id, chat_title, username, link_type, monitored_by,
                      member_count, ai_classification, ai_country, ai_relevance,
                      ai_description, should_monitor, first_seen, last_seen
               FROM monitored_chats
               WHERE should_monitor = 1
               ORDER BY ai_relevance DESC, last_seen DESC LIMIT ?""",
            (limit,))
        rows = await cursor.fetchall()
        return [{'chat_id': r[0], 'chat_title': r[1] or '', 'username': r[2] or '',
                 'link_type': r[3] or '', 'monitored_by': r[4] or '',
                 'member_count': r[5] or 0,
                 'ai_classification': r[6] or '', 'ai_country': r[7] or '',
                 'ai_relevance': r[8] or 0, 'ai_description': r[9] or '',
                 'should_monitor': r[10], 'first_seen': r[11], 'last_seen': r[12]} for r in rows]

    async def get_unclassified_chats(self, limit: int = 10) -> List[dict]:
        """يجلب مجموعات لم يُصنّفها AI بعد."""
        conn = await self._conn()
        cursor = await conn.execute(
            """SELECT chat_id, chat_title, username, member_count
               FROM monitored_chats
               WHERE ai_classification IS NULL AND should_monitor = 1
               ORDER BY first_seen ASC LIMIT ?""",
            (limit,))
        rows = await cursor.fetchall()
        return [{'chat_id': r[0], 'chat_title': r[1] or '', 'username': r[2] or '',
                 'member_count': r[3] or 0} for r in rows]

    # === Group States ===

    async def get_group_state(self, normalized_link: str) -> Optional[str]:
        """يجلب حالة مجموعة من DB."""
        conn = await self._conn()
        cursor = await conn.execute(
            "SELECT state FROM group_states WHERE normalized_link = ?", (normalized_link,))
        row = await cursor.fetchone()
        return row[0] if row else None

    async def set_group_state(self, normalized_link: str, state: str, raw_link: str = None, group_title: str = None, joined_by: str = None, member_count: int = None, error: str = None):
        """يحدّث أو ينشئ حالة مجموعة."""
        conn = await self._conn()
        now = datetime.now().isoformat()
        await conn.execute(
            """INSERT INTO group_states (normalized_link, raw_link, group_title, state, first_seen, last_seen, last_attempt, last_error, joined_by, member_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(normalized_link) DO UPDATE SET
               state = ?, last_seen = ?, last_attempt = ?, last_error = ?,
               joined_by = COALESCE(?, joined_by),
               member_count = COALESCE(?, member_count),
               attempt_count = attempt_count + 1""",
            (normalized_link, raw_link, group_title, state, now, now, now, error, joined_by, member_count,
             state, now, now, error, joined_by, member_count))
        await conn.commit()

    # === Membership Cache (DB layer) ===

    async def get_membership(self, phone: str, normalized_link: str) -> Optional[bool]:
        """يجلب العضوية من DB."""
        conn = await self._conn()
        cursor = await conn.execute(
            "SELECT is_member FROM membership_cache WHERE phone = ? AND normalized_link = ?",
            (phone, normalized_link))
        row = await cursor.fetchone()
        if row is not None:
            return bool(row[0])
        return None

    async def set_membership(self, phone: str, normalized_link: str, is_member: bool):
        """يحفظ العضوية في DB."""
        conn = await self._conn()
        await conn.execute(
            """INSERT OR REPLACE INTO membership_cache (phone, normalized_link, is_member, checked_at)
               VALUES (?, ?, ?, ?)""",
            (phone, normalized_link, 1 if is_member else 0, datetime.now().isoformat()))
        await conn.commit()

    # === FloodWait Tracker ===

    async def get_floodwait(self, phone: str) -> Optional[float]:
        """يجلب وقت إعادة المحاولة لحساب محظور."""
        conn = await self._conn()
        cursor = await conn.execute(
            "SELECT next_retry_at FROM floodwait_tracker WHERE phone = ?", (phone,))
        row = await cursor.fetchone()
        return row[0] if row else None

    async def set_floodwait(self, phone: str, next_retry_ts: float, reason: str = 'FloodWait'):
        """يسجل حظر FloodWait."""
        conn = await self._conn()
        await conn.execute(
            """INSERT OR REPLACE INTO floodwait_tracker (phone, next_retry_at, reason, created_at)
               VALUES (?, ?, ?, ?)""",
            (phone, next_retry_ts, reason, datetime.now().isoformat()))
        await conn.commit()

    async def clear_floodwait(self, phone: str):
        """يمسح حظر FloodWait."""
        conn = await self._conn()
        await conn.execute("DELETE FROM floodwait_tracker WHERE phone = ?", (phone,))
        await conn.commit()

    # === DB-Backed Rate Limiter Operations ===

    async def log_operation(self, phone: str, action_type: str, success: bool = True):
        """يسجل عملية حساسة في SQLite (يستمر بعد إعادة التشغيل)."""
        conn = await self._conn()
        await conn.execute(
            "INSERT INTO api_operations_log (phone, action_type, timestamp, success) VALUES (?, ?, ?, ?)",
            (phone, action_type, time.time(), 1 if success else 0))
        await conn.commit()

    async def count_operations(self, phone: str, action_type: str, window_seconds: int) -> int:
        """يعد العمليات في نافذة زمنية محددة من DB."""
        conn = await self._conn()
        cutoff = time.time() - window_seconds
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM api_operations_log WHERE phone = ? AND action_type = ? AND timestamp > ?",
            (phone, action_type, cutoff))
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def count_joins_today(self, phone: str) -> int:
        """يعد انضمامات اليوم من DB."""
        return await self.count_operations(phone, 'join', 86400)

    async def count_membership_checks_today(self, phone: str) -> int:
        """يعد فحوصات العضوية اليوم من DB."""
        return await self.count_operations(phone, 'membership_check', 86400)

    async def get_all_floodwaits(self) -> List[dict]:
        """يجلب كل الحسابات المحظورة."""
        conn = await self._conn()
        cursor = await conn.execute(
            "SELECT phone, next_retry_at, reason FROM floodwait_tracker WHERE next_retry_at > ?",
            (time.time(),))
        rows = await cursor.fetchall()
        return [{'phone': r[0], 'next_retry_at': r[1], 'reason': r[2]} for r in rows]

    # === Joined Groups (for dashboard) ===

    async def get_joined_groups(self, limit: int = 50) -> List[dict]:
        """يجلب المجموعات المنضم إليها."""
        conn = await self._conn()
        cursor = await conn.execute(
            """SELECT normalized_link, raw_link, group_title, state, joined_by, last_attempt, member_count
               FROM group_states 
               WHERE state IN ('JOINED', 'ALREADY_MEMBER')
               ORDER BY last_attempt DESC LIMIT ?""",
            (limit,))
        rows = await cursor.fetchall()
        return [{'group_link': r[1], 'group_title': r[2], 'status': r[3],
                 'joined_by_phone': r[4], 'join_date': r[5], 'member_count': r[6]} for r in rows]

    async def get_pending_count(self) -> int:
        """عدد المجموعات المعلقة."""
        conn = await self._conn()
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM group_states WHERE state IN ('DISCOVERED', 'QUEUED')")
        row = await cursor.fetchone()
        return row[0] if row else 0

    # === System Settings (DB-backed, survives restart) ===

    async def get_setting(self, key: str, default: str = None) -> Optional[str]:
        """يجلب إعداد من system_settings."""
        conn = await self._conn()
        cursor = await conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else default

    async def set_setting(self, key: str, value: str):
        """يحفظ إعداد في system_settings."""
        conn = await self._conn()
        await conn.execute(
            "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, datetime.now().isoformat()))
        await conn.commit()

    async def get_membership_with_ttl(self, phone: str, normalized_link: str, ttl_days: int = 7) -> Optional[bool]:
        """يجلب العضوية من DB مع TTL — لا يعيد فحص قبل ttl_days."""
        conn = await self._conn()
        cursor = await conn.execute(
            "SELECT is_member, checked_at FROM membership_cache WHERE phone = ? AND normalized_link = ?",
            (phone, normalized_link))
        row = await cursor.fetchone()
        if row is not None:
            is_member = bool(row[0])
            checked_at = row[1]
            # تحقق من TTL
            if checked_at:
                try:
                    checked_dt = datetime.fromisoformat(checked_at)
                    age_days = (datetime.now() - checked_dt).days
                    if age_days < ttl_days:
                        return is_member  # لا يزال صالحاً
                    else:
                        return None  # انتهت صلاحيته → needs recheck
                except Exception:
                    return is_member  # لو ما نقدر نحلل التاريخ، استخدم القيمة
            return is_member
        return None  # غير موجود
