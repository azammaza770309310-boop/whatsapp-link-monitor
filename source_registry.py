#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SourceRegistry + PollingScheduler + MessageClaim
=================================================

Unified source discovery, reader selection, message dedup, and polling
scheduling for the WhatsApp/Telegram link monitor.

Design goals:
- chat_id UNIQUE = 1 source, even if 5 accounts can read it
- Monitor preferred as reader; Joiner only as fallback
- Load balancing across multiple Monitors (least-loaded)
- Atomic message claim with claim_token + lease_until
  (prevents stale workers from corrupting fresh claims)
- Fair scheduling: next_poll_at is the basis; tier sets the rate, not priority
- Anti-starvation: oldest-due source wins (regardless of tier)
- Covers groups + supergroups + channels (no arbitrary Top-N limit)

This module is additive: it does not modify or delete any existing function.
"""
import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# -------------------------------------------------------------------
# MessageClaim — atomic claim with claim_token + lease_until
# -------------------------------------------------------------------

class MessageClaim:
    """Atomic message claim with lease + token.

    State machine:
        (no row)  ──claim()──►  'claimed'   ──mark_processed()──►  'processed'
                                     │
                                     ├──mark_failed()──►  'failed' (retryable)
                                     │
                                     └──lease expires──►  (re-claimable)

    Contract:
    - claim() returns claim_token (str) if THIS caller wins, None otherwise.
    - mark_processed(token) and mark_failed(token) verify state='claimed'
      AND claim_token=?. Stale token → returns False (no-op).
    - lease_until prevents stuck claims: if a worker crashes after claim
      but before mark_processed, the lease expires and another worker
      can re-claim after LEASE_DURATION_S seconds.
    """

    LEASE_DURATION_S = int(os.environ.get('LEASE_DURATION_S', '180'))  # [L07] was hardcoded 60s — too short for a 4-account fan-out under FloodWait; 180s avoids premature re-claim of in-flight work.

    def __init__(self, prod_db):
        self.prod_db = prod_db

    async def claim(self, chat_id: int, msg_id: int, source: str, phone: str) -> Optional[str]:
        """Atomic claim — returns claim_token if winner, None if already claimed/processed.

        Race-safe via SQLite PRIMARY KEY + INSERT OR IGNORE + CAS UPDATE.
        busy_timeout=5000 (set in DatabaseManager._ensure_conn) handles lock contention.

        Retry logic:
        - state='failed' → re-claimable (CAS UPDATE)
        - state='claimed' + lease_until < now → stale, re-claimable (CAS UPDATE)
        - state='claimed' + lease_until >= now → busy, return None
        - state='processed' → return None
        """
        token = str(uuid.uuid4())
        now = datetime.now()
        lease_until = now + timedelta(seconds=self.LEASE_DURATION_S)

        conn = await self.prod_db._conn()

        # 1. Try INSERT (for brand-new messages)
        cursor = await conn.execute(
            """INSERT OR IGNORE INTO processed_messages
               (chat_id, msg_id, state, source, claimant_phone, claim_token,
                claimed_at, lease_until, attempt_count)
               VALUES (?, ?, 'claimed', ?, ?, ?, ?, ?, 1)""",
            (chat_id, msg_id, source, phone, token,
             now.isoformat(), lease_until.isoformat())
        )
        await conn.commit()
        if cursor.rowcount > 0:
            return token  # Winner via INSERT

        # 2. Already exists — check state + lease
        cursor = await conn.execute(
            "SELECT state, lease_until FROM processed_messages WHERE chat_id=? AND msg_id=?",
            (chat_id, msg_id)
        )
        row = await cursor.fetchone()
        if not row:
            # Rare: row disappeared between INSERT and SELECT — retry
            return None

        state = row[0]
        lease_until_str = row[1]
        can_retry = False

        if state == 'failed':
            can_retry = True
        elif state == 'claimed' and lease_until_str:
            try:
                lease_until_dt = datetime.fromisoformat(lease_until_str)
                if now > lease_until_dt:
                    can_retry = True  # Stale claim
            except Exception:
                can_retry = True  # Invalid date format — treat as stale

        if not can_retry:
            return None  # Busy or processed

        # 3. CAS UPDATE — atomic conditional update
        cursor = await conn.execute(
            """UPDATE processed_messages
               SET state='claimed', source=?, claimant_phone=?, claim_token=?,
                   claimed_at=?, lease_until=?, attempt_count=attempt_count+1
               WHERE chat_id=? AND msg_id=?
               AND (state='failed'
                    OR (state='claimed' AND lease_until < ?))""",
            (source, phone, token, now.isoformat(), lease_until.isoformat(),
             chat_id, msg_id, now.isoformat())
        )
        await conn.commit()
        if cursor.rowcount > 0:
            return token  # Winner via retry
        return None  # Lost race to another retry

    async def mark_processed(self, chat_id: int, msg_id: int, claim_token: str) -> bool:
        """Mark as processed — verifies claim_token to prevent stale worker corruption."""
        conn = await self.prod_db._conn()
        cursor = await conn.execute(
            """UPDATE processed_messages
               SET state='processed', processed_at=?
               WHERE chat_id=? AND msg_id=? AND state='claimed' AND claim_token=?""",
            (datetime.now().isoformat(), chat_id, msg_id, claim_token)
        )
        await conn.commit()
        return cursor.rowcount > 0

    async def mark_failed(self, chat_id: int, msg_id: int, claim_token: str, error: str) -> bool:
        """Mark as failed — verifies claim_token. Allows retry by another worker."""
        conn = await self.prod_db._conn()
        cursor = await conn.execute(
            """UPDATE processed_messages
               SET state='failed', last_error=?, processed_at=?
               WHERE chat_id=? AND msg_id=? AND state='claimed' AND claim_token=?""",
            (error[:500], datetime.now().isoformat(), chat_id, msg_id, claim_token)
        )
        await conn.commit()
        return cursor.rowcount > 0

    async def cleanup_stale_and_old(self):
        """Periodic cleanup:
        - 'claimed' with lease_until < now → DELETE (re-claimable on next poll)
        - 'processed' older than 7 days → DELETE
        - 'failed' older than 30 days → DELETE
        """
        conn = await self.prod_db._conn()
        now = datetime.now().isoformat()
        # Stuck claims (lease expired but not cleaned by retry)
        await conn.execute(
            "DELETE FROM processed_messages WHERE state='claimed' AND lease_until < ?",
            (now,)
        )
        # Successfully processed older than 7 days
        await conn.execute(
            """DELETE FROM processed_messages
               WHERE state='processed' AND processed_at < datetime('now', '-7 days')"""
        )
        # Failed older than 30 days (keep for diagnostics)
        await conn.execute(
            """DELETE FROM processed_messages
               WHERE state='failed' AND claimed_at < datetime('now', '-30 days')"""
        )
        await conn.commit()


# -------------------------------------------------------------------
# SourceRegistry — chat_id UNIQUE, reader_phones, load-balanced selection
# -------------------------------------------------------------------

class SourceRegistry:
    """Unified source registry: 1 chat_id = 1 source, even if 5 accounts see it.

    In-memory indices (rebuilt from DB on startup):
    - _chat_to_phones: chat_id → [phones] (monitors first, then joiners)
    - _phone_to_role: phone → 'monitor' | 'joiner'
    - _phone_health: phone → is_connected (live health)
    - _phone_load: phone → current polling load (for load balancing)
    - _chat_locks: chat_id → asyncio.Lock (prevents parallel reads of same chat)

    Reader selection:
    1. Pick the least-loaded connected Monitor (load balancing)
    2. If no Monitor available, pick least-loaded connected Joiner (fallback)
    3. When a Monitor returns, it immediately becomes preferred again
       (load balancing picks it over Joiner on next call)
    """

    def __init__(self, prod_db, watchers: List[dict]):
        self.prod_db = prod_db
        self.watchers = watchers

        self._chat_to_phones: Dict[int, List[str]] = {}
        self._phone_to_role: Dict[str, str] = {
            w['phone']: w.get('role', 'monitor') for w in watchers
        }
        self._phone_health: Dict[str, bool] = {
            w['phone']: False for w in watchers
        }
        self._phone_load: Dict[str, int] = {
            w['phone']: 0 for w in watchers
        }
        self._chat_locks: Dict[int, asyncio.Lock] = {}
        self._lock = asyncio.Lock()

    async def load_from_db(self):
        """Fast load from monitored_chats — no Telegram API calls.

        Rebuilds _chat_to_phones from reader_phones column.
        Used on startup so PollingScheduler can start immediately.
        """
        chats = await self.prod_db.get_monitored_chats(limit=50000)
        async with self._lock:
            self._chat_to_phones.clear()
            for chat in chats:
                chat_id = chat.get('chat_id')
                if chat_id is None:
                    continue
                reader_phones_str = chat.get('reader_phones') or '[]'
                try:
                    phones = json.loads(reader_phones_str)
                except Exception:
                    phones = []
                # Backward compat: if reader_phones is empty but monitored_by is set
                if not phones and chat.get('monitored_by'):
                    phones = [chat['monitored_by']]
                # Sort: monitors first
                phones.sort(key=lambda p: 0 if self._phone_to_role.get(p) == 'monitor' else 1)
                self._chat_to_phones[chat_id] = phones
        logging.info(f"[REGISTRY] Loaded {len(self._chat_to_phones)} sources from DB")

    async def discover_all_sources_background(self, user_clients: Dict[str, object]):
        """Background discovery — iterates dialogs from all connected accounts.

        Updates reader_phones in DB. Does not delete existing sources
        (an account may be temporarily offline).
        Covers groups + supergroups + channels (excludes private chats only).
        """
        try:
            # Build chat_id → set of phones
            chat_to_phones_set: Dict[int, set] = {}
            chat_metadata: Dict[int, dict] = {}

            for phone, client in user_clients.items():
                if not client or not client.is_connected():
                    logging.info(f"[REGISTRY] {phone} not connected — skipping discovery")
                    continue
                try:
                    count = 0
                    async for dialog in client.iter_dialogs():
                        # Exclude private chats (only groups + channels)
                        if not dialog.is_group and not dialog.is_channel:
                            continue
                        chat_id = dialog.id
                        chat_to_phones_set.setdefault(chat_id, set()).add(phone)

                        # Capture metadata once (first account that sees it)
                        if chat_id not in chat_metadata:
                            username = ''
                            try:
                                if dialog.entity and hasattr(dialog.entity, 'username') and dialog.entity.username:
                                    username = dialog.entity.username
                            except Exception:
                                pass
                            link_type = 'group'
                            try:
                                if hasattr(dialog.entity, 'broadcast') and dialog.entity.broadcast:
                                    link_type = 'channel'
                            except Exception:
                                pass
                            chat_metadata[chat_id] = {
                                'chat_title': dialog.title or f'chat_{chat_id}',
                                'username': username,
                                'link_type': link_type,
                            }
                        count += 1
                    logging.info(f"[REGISTRY] {phone}: discovered {count} dialogs")
                except Exception as e:
                    logging.error(f"[REGISTRY] {phone} discovery error: {e}")

            # Update DB + in-memory indices
            # IMPORTANT: We MERGE newly-discovered phones with existing reader_phones
            # from DB, rather than replacing them. This ensures that accounts which
            # are temporarily offline (and thus not in this discovery round) are
            # NOT removed from reader_phones. They will still be available as
            # fallback readers when they come back online.
            async with self._lock:
                for chat_id, phones_set in chat_to_phones_set.items():
                    # Newly discovered phones (accounts that responded this round)
                    new_phones = set(phones_set)

                    # Preserve existing reader_phones from DB (merge)
                    existing_phones = set()
                    try:
                        conn = await self.prod_db._conn()
                        cursor = await conn.execute(
                            "SELECT reader_phones FROM monitored_chats WHERE chat_id=?",
                            (chat_id,)
                        )
                        row = await cursor.fetchone()
                        if row and row[0]:
                            existing_phones = set(json.loads(row[0]))
                    except Exception:
                        pass

                    # Merge: union of new + existing (offline accounts preserved)
                    merged_phones = new_phones | existing_phones
                    phones_list = list(merged_phones)
                    # Sort: monitors first, then joiners
                    phones_list.sort(key=lambda p: 0 if self._phone_to_role.get(p) == 'monitor' else 1)
                    self._chat_to_phones[chat_id] = phones_list

                    meta = chat_metadata.get(chat_id, {})
                    # primary_reader = first connected Monitor, else first connected phone,
                    # else first phone in list (even if offline — for reference)
                    primary_reader = ''
                    for p in phones_list:
                        if (self._phone_to_role.get(p) == 'monitor'
                                and self._phone_health.get(p, False)):
                            primary_reader = p
                            break
                    if not primary_reader:
                        for p in phones_list:
                            if self._phone_health.get(p, False):
                                primary_reader = p
                                break
                    if not primary_reader and phones_list:
                        primary_reader = phones_list[0]

                    # INSERT OR IGNORE for new, then UPDATE reader_phones/primary_reader
                    is_new = await self.prod_db.add_monitored_chat(
                        chat_id=chat_id,
                        chat_title=meta.get('chat_title', f'chat_{chat_id}'),
                        username=meta.get('username', ''),
                        link_type=meta.get('link_type', 'group'),
                        monitored_by=primary_reader,
                    )
                    # Always update reader_phones + primary_reader (new columns)
                    await self.prod_db.update_monitored_chat(
                        chat_id,
                        reader_phones=json.dumps(phones_list),
                        primary_reader=primary_reader,
                        monitored_by=primary_reader,  # backward compat
                    )

            total = len(chat_to_phones_set)
            logging.info(f"[REGISTRY] Discovery complete: {total} unique sources updated (reader_phones merged with existing)")
        except Exception as e:
            logging.error(f"[REGISTRY] discovery fatal: {e}", exc_info=True)

    def update_phone_status(self, phone: str, connected: bool):
        """Update account connection status (called from _run_user_client)."""
        self._phone_health[phone] = connected
        if not connected:
            # Reset load (any in-flight polls will fail anyway)
            self._phone_load[phone] = 0

    def get_reader(self, chat_id: int) -> Optional[str]:
        """Pick least-loaded connected Monitor; fallback to Joiner.

        Load balancing: chooses the Monitor with the lowest current load
        (number of chats it's currently polling). Ties are broken randomly
        to ensure fair distribution across equally-loaded accounts.

        When a Monitor is available, it's always preferred over a Joiner,
        even if the Joiner was the last reader (stickiness is overridden
        by Monitor priority).
        """
        import random as _random
        phones = self._chat_to_phones.get(chat_id, [])
        if not phones:
            return None

        # 1. Connected Monitors
        monitors = [
            p for p in phones
            if self._phone_to_role.get(p) == 'monitor'
            and self._phone_health.get(p, False)
        ]
        if monitors:
            # Find min load, then random pick among ties
            loads = {p: self._phone_load.get(p, 0) for p in monitors}
            min_load = min(loads.values())
            tied = [p for p, l in loads.items() if l == min_load]
            chosen = _random.choice(tied)
            self._phone_load[chosen] = self._phone_load.get(chosen, 0) + 1
            return chosen

        # 2. Fallback: connected Joiners
        joiners = [
            p for p in phones
            if self._phone_to_role.get(p) == 'joiner'
            and self._phone_health.get(p, False)
        ]
        if joiners:
            loads = {p: self._phone_load.get(p, 0) for p in joiners}
            min_load = min(loads.values())
            tied = [p for p, l in loads.items() if l == min_load]
            chosen = _random.choice(tied)
            self._phone_load[chosen] = self._phone_load.get(chosen, 0) + 1
            return chosen

        return None

    def release_load(self, phone: str):
        """Release load from a phone (after polling completes)."""
        if self._phone_load.get(phone, 0) > 0:
            self._phone_load[phone] -= 1

    async def remove_reader(self, chat_id: int, phone: str) -> bool:
        """يحذف هاتفًا من reader_phones لشات محدد (عند فقدان الحساب وصوله).

        يحدّث الذاكرة + DB (reader_phones JSON). يُستدعى مثلاً عند
        ChannelPrivateError/forbidden/banned أثناء polling — الحساب لم يعد
        قادرًا على قراءة الشات، وبقاءه في reader_phones يهدر محاولات polling.
        Returns True لو حُذف الهاتف فعليًا.
        """
        async with self._lock:
            phones = self._chat_to_phones.get(chat_id, [])
            if phone not in phones:
                return False
            phones = [p for p in phones if p != phone]
            self._chat_to_phones[chat_id] = phones
        # حدّث DB (best-effort) — أصلح primary_reader لو كان هو الهاتف المحذوف
        try:
            import json as _json
            conn = await self.prod_db._conn()
            await conn.execute(
                """UPDATE monitored_chats
                   SET reader_phones=?,
                       primary_reader=CASE WHEN primary_reader=? THEN '' ELSE primary_reader END
                   WHERE chat_id=?""",
                (_json.dumps(phones), phone, chat_id))
            await conn.commit()
        except Exception as e:
            logging.debug(f"[REGISTRY] remove_reader DB update error: {e}")
        return True

    def get_chat_lock(self, chat_id: int) -> asyncio.Lock:
        """Per-chat lock — prevents parallel reads of same source."""
        if chat_id not in self._chat_locks:
            self._chat_locks[chat_id] = asyncio.Lock()
        return self._chat_locks[chat_id]

    def get_all_chat_ids(self) -> List[int]:
        """Return all chat_ids in registry (no Top-N limit)."""
        return list(self._chat_to_phones.keys())

    def get_phone_load(self) -> Dict[str, int]:
        """Snapshot of current load per phone (for monitoring/tests)."""
        return dict(self._phone_load)


# -------------------------------------------------------------------
# PollingScheduler — fair scheduling with next_poll_at + aging
# -------------------------------------------------------------------

class PollingScheduler:
    """Fair polling scheduler — covers all sources, no starvation.

    Design:
    - next_poll_at is the basis of scheduling (per-source independent)
    - tier sets the polling rate (hot=10s, cold=600s), NOT absolute priority
    - When selecting due sources, oldest next_poll_at wins (aging)
      → a Cold source that's been due for an hour beats a Hot source due for 1 second
    - BATCH_SIZE per cycle (10) prevents API bursts
    - RateLimiter('polling') enforces per-account limits (25/min)
    - FloodWait handled dynamically: record_floodwait + delay next_poll_at
    """

    TIERS = {
        'hot':    {'max_age_min': 5,    'poll_interval_s': 10},
        'active': {'max_age_min': 60,   'poll_interval_s': 30},
        'cool':   {'max_age_min': 1440, 'poll_interval_s': 120},
        'cold':   {'max_age_min': None, 'poll_interval_s': 600},
    }

    BATCH_SIZE = 25
    BATCH_PAUSE_S = 0.3
    CYCLE_SLEEP_S = 2
    # Polling متزامن محدود — التوازي عبر الحسابات المختلفة فقط
    # (RateLimiter يسلسل عمليات نفس الحساب عبر قفل لكل هاتف + min_delay=2s)
    MAX_CONCURRENT_POLLS = 4

    def __init__(self, source_registry, prod_db, rate_limiter, floodwait_mgr,
                 message_claim, monitor_ref):
        self.registry = source_registry
        self.prod_db = prod_db
        self.rate_limiter = rate_limiter
        self.floodwait_mgr = floodwait_mgr
        self.message_claim = message_claim
        self.monitor = monitor_ref  # to call _poll_one_chat
        self._running = False

    def classify_tier(self, last_activity: Optional[datetime]) -> str:
        if not last_activity:
            return 'cold'
        age_min = (datetime.now() - last_activity).total_seconds() / 60
        if age_min < 5: return 'hot'
        if age_min < 60: return 'active'
        if age_min < 1440: return 'cool'
        return 'cold'

    async def select_due_chats(self, limit: int = None) -> List[dict]:
        """Select sources due for polling — oldest next_poll_at first (aging).

        Aging: a source that's been due longer gets priority, regardless of tier.
        This prevents Hot sources from starving Cold sources.
        """
        if limit is None:
            limit = self.BATCH_SIZE
        conn = await self.prod_db._conn()
        now = datetime.now().isoformat()
        cursor = await conn.execute(
            """SELECT chat_id, chat_title, username, link_type, monitored_by,
                      reader_phones, primary_reader, last_msg_id, last_activity,
                      poll_tier, next_poll_at
               FROM monitored_chats
               WHERE should_monitor = 1
               AND (next_poll_at IS NULL OR next_poll_at <= ?)
               ORDER BY COALESCE(next_poll_at, '1970-01-01') ASC
               LIMIT ?""",
            (now, limit)
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in rows]

    async def update_next_poll(self, chat_id: int, tier: str):
        """Update next_poll_at based on tier."""
        interval = self.TIERS[tier]['poll_interval_s']
        next_poll = datetime.now() + timedelta(seconds=interval)
        await self.prod_db.update_monitored_chat(
            chat_id,
            next_poll_at=next_poll.isoformat(),
            poll_tier=tier,
        )

    async def run(self):
        """Main polling loop — fair scheduling."""
        await asyncio.sleep(20)  # let bot finish startup
        logging.info(f"🔄 PollingScheduler started — batch={self.BATCH_SIZE}, cycle={self.CYCLE_SLEEP_S}s")
        self._running = True

        while self._running:
            try:
                # 1. Select due chats (oldest first — aging prevents starvation)
                due_chats = await self.select_due_chats(limit=self.BATCH_SIZE)
                if not due_chats:
                    await asyncio.sleep(self.CYCLE_SLEEP_S)
                    continue

                # 2. Poll with bounded concurrency — التوازي عبر الحسابات فقط.
                #    RateLimiter يسلسل عمليات نفس الحساب (قفل لكل هاتف + min_delay)،
                #    لذا التزامن هنا يزيد الإنتاجية بدون تجاوز حدود Telegram.
                sem = asyncio.Semaphore(self.MAX_CONCURRENT_POLLS)

                async def _poll_one(chat):
                    chat_id = chat['chat_id']
                    async with sem:
                        # 2a. Pick reader (Monitor preferred, load-balanced)
                        reader = self.registry.get_reader(chat_id)
                        if reader is None:
                            # No reader available — retry after cold interval
                            await self.update_next_poll(chat_id, 'cold')
                            return
                        try:
                            # Rate limit check (serializes per-account ops)
                            allowed = await self.rate_limiter.acquire(reader, 'polling')
                            if not allowed:
                                # Rate limit or FloodWait active — delay this chat
                                await self.update_next_poll(chat_id, 'cool')
                                return

                            # 2c. Poll one chat (sequential per chat via lock)
                            async with self.registry.get_chat_lock(chat_id):
                                await self.monitor._poll_one_chat(reader, chat)

                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            # FloodWaitError, RPCError, etc. — log and continue
                            logging.debug(f"[POLL] chat={chat_id} error: {e}")
                        finally:
                            # GUARANTEED release_load
                            self.registry.release_load(reader)

                        # 2d. Update next_poll_at based on tier
                        last_activity_str = chat.get('last_activity')
                        try:
                            last_activity = datetime.fromisoformat(last_activity_str) if last_activity_str else None
                        except Exception:
                            last_activity = None
                        tier = self.classify_tier(last_activity)
                        await self.update_next_poll(chat_id, tier)

                await asyncio.gather(*[_poll_one(c) for c in due_chats],
                                     return_exceptions=True)
                # 2e. Small pause between batches
                await asyncio.sleep(self.BATCH_PAUSE_S)

                # 3. Short cycle sleep
                await asyncio.sleep(self.CYCLE_SLEEP_S)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"[POLL-SCHED] error: {e}", exc_info=True)
                await asyncio.sleep(5)

    def stop(self):
        self._running = False
