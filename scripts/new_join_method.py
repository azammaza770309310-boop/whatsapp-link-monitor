    async def _verify_membership(self, client, entity, phone: str, raw_link: str) -> Tuple[bool, Optional[int]]:
        """يتحقق من العضوية بعد Join API call.

        Returns:
            (True, member_count) — العضوية مؤكدة
            (False, None) — العضوية غير مؤكدة أو فشل التحقق
        """
        try:
            from telethon.tl.functions.channels import GetParticipantRequest
            from telethon.errors import UserNotParticipantError, ChannelPrivateError
        except ImportError:
            logging.error("[JOIN] Cannot import GetParticipantRequest — verification impossible")
            return False, None

        try:
            await asyncio.wait_for(
                client(GetParticipantRequest(channel=entity, participant="me")),
                timeout=15
            )
            member_count = None
            if hasattr(entity, 'participants_count'):
                member_count = entity.participants_count
            logging.info(
                f"[JOIN] ✅ MEMBERSHIP VERIFIED\n"
                f"[JOIN] phone={phone}\n"
                f"[JOIN] link={raw_link[:50]}\n"
                f"[JOIN] members={member_count}"
            )
            return True, member_count
        except UserNotParticipantError:
            logging.error(
                f"[JOIN] ❌ Membership verification FAILED\n"
                f"[JOIN] reason=UserNotParticipant\n"
                f"[JOIN] phone={phone}\n"
                f"[JOIN] link={raw_link[:50]}"
            )
            return False, None
        except asyncio.TimeoutError:
            logging.error(
                f"[JOIN] ❌ Membership verification TIMEOUT\n"
                f"[JOIN] phone={phone}\n"
                f"[JOIN] link={raw_link[:50]}"
            )
            return False, None
        except ChannelPrivateError:
            logging.error(
                f"[JOIN] ❌ Membership verification FAILED\n"
                f"[JOIN] reason=ChannelPrivate\n"
                f"[JOIN] phone={phone}\n"
                f"[JOIN] link={raw_link[:50]}"
            )
            return False, None
        except FloodWaitError as e:
            logging.warning(
                f"[JOIN] ❌ Membership verification FloodWait\n"
                f"[JOIN] seconds={e.seconds}\n"
                f"[JOIN] phone={phone}"
            )
            await self.rate_limiter.record_floodwait(phone, e.seconds)
            return False, None
        except Exception as e:
            logging.error(
                f"[JOIN] ❌ Membership verification error\n"
                f"[JOIN] error={type(e).__name__}: {str(e)[:80]}\n"
                f"[JOIN] phone={phone}\n"
                f"[JOIN] link={raw_link[:50]}"
            )
            return False, None

    async def _join_group_safe(self, client, link_data: dict, phone: str):
        """ينضم لمجموعة ويتحقق من العضوية فعلياً.

        Contract:
            - returns (True, "JOINED_VERIFIED", member_count) فقط بعد GetParticipantRequest
            - returns (True, "JOIN_UNVERIFIED", None) لو Join نجح لكن التحقق تعذر
            - returns (False, reason, None) للفشل بأنواعه

        EXPLICIT PROTECTION: Monitor accounts can NEVER join.
        """
        raw_link = link_data.get('raw', link_data.get('raw_link', ''))

        # تحقق من اتصال الـ client
        if not client or not client.is_connected():
            logging.error(f"[JOIN] ❌ client not connected for {phone}")
            return False, "DISCONNECTED", None

        # === EXPLICIT MONITOR PROTECTION ===
        w = await self.db._supabase_get_watcher(phone)
        role = w.get('role', 'monitor') if w else 'monitor'
        if role == 'monitor':
            logging.error(f"[JOIN] BLOCKED: {phone} is monitor — join_permission=false")
            return False, "MONITOR_NO_JOIN", None

        # === Per-account joiner_enabled check ===
        joiner_enabled = w.get('joiner_enabled', 1) if w else 1
        if not joiner_enabled or joiner_enabled == 0:
            logging.info(f"[JOIN] {phone} joiner_enabled=false — skipping")
            return False, "JOINER_DISABLED", None

        # === Emergency pause check ===
        if self._join_paused:
            logging.info(f"[JOIN] Blocked: join paused via /pause_join")
            return False, "PAUSED", None

        # === SIMULATION_MODE ===
        if self.simulation_mode:
            logging.info(f"[SIM] Would join: {raw_link[:50]} via {phone}")
            return False, "SIMULATION", None

        try:
            from telethon.tl.functions.channels import JoinChannelRequest
            from telethon.tl.functions.messages import ImportChatInviteRequest
            from telethon.errors import (
                UserAlreadyParticipantError, FloodWaitError,
                ChannelPrivateError, InviteHashExpiredError,
                PeerFloodError, UserBannedInChannelError,
                ChatWriteForbiddenError,
            )

            link_type = link_data['link_type']

            if link_type == 'telegram_private':
                invite_hash = link_data.get('invite_hash', '')
                if not invite_hash:
                    return False, "INVALID", None

                allowed = await self.rate_limiter.acquire(phone, 'import_invite')
                if not allowed:
                    logging.info(f"[JOIN] {phone} rate limited on import_invite — will retry later")
                    return False, "RATE_LIMITED", None

                logging.info(f"[JOIN] API request started: IMPORT_INVITE phone={phone} link={raw_link[:50]}")
                try:
                    await asyncio.wait_for(client(ImportChatInviteRequest(invite_hash)), timeout=30)
                    await self.metrics.record_api_call(phone)
                    logging.info(f"[JOIN] Telegram accepted IMPORT_INVITE request for {phone}")
                except asyncio.TimeoutError:
                    logging.error(f"[JOIN] ❌ TIMEOUT (30s) phone={phone} op=IMPORT_INVITE link={raw_link[:50]}")
                    return False, "TIMEOUT", None
                except UserAlreadyParticipantError:
                    return False, "ALREADY_MEMBER", None
                except FloodWaitError as e:
                    logging.warning(f"[JOIN] ❌ FloodWait phone={phone} seconds={e.seconds} link={raw_link[:50]}")
                    await self.rate_limiter.record_floodwait(phone, e.seconds)
                    return False, "FLOODWAIT", None
                except (ChannelPrivateError, InviteHashExpiredError) as e:
                    logging.warning(f"[JOIN] ❌ {type(e).__name__} phone={phone} link={raw_link[:50]}")
                    return False, "PRIVATE", None
                except (PeerFloodError, UserBannedInChannelError) as e:
                    logging.error(f"[JOIN] ❌ {type(e).__name__} phone={phone} link={raw_link[:50]}")
                    await self.rate_limiter.record_floodwait(phone, 3600)
                    return False, "BANNED", None
                except ChatWriteForbiddenError:
                    logging.error(f"[JOIN] ❌ ChatWriteForbidden phone={phone} link={raw_link[:50]}")
                    return False, "PRIVATE", None

                # === POST-JOIN VERIFICATION ===
                logging.info(f"[JOIN] Verifying membership after IMPORT_INVITE for {phone}...")
                # For private invite, we don't have entity easily
                # Mark as UNVERIFIED — Telegram accepted but membership not confirmed
                logging.warning(
                    f"[JOIN] ⚠️ JOIN_UNVERIFIED — Telegram accepted IMPORT_INVITE but "
                    f"verification not possible for private invite (no entity)\n"
                    f"[JOIN] phone={phone} link={raw_link[:50]}"
                )
                return True, "JOIN_UNVERIFIED", None

            elif link_type == 'telegram':
                username = link_data.get('username', '')
                if not username:
                    return False, "INVALID", None

                allowed = await self.rate_limiter.acquire(phone, 'join_channel')
                if not allowed:
                    logging.info(f"[JOIN] {phone} rate limited on join_channel — will retry later")
                    return False, "RATE_LIMITED", None

                logging.info(f"[JOIN] API request started: JOIN_CHANNEL phone={phone} link={raw_link[:50]}")
                try:
                    entity = await asyncio.wait_for(client.get_entity(username), timeout=30)

                    # تحقق: هل الكيان قناة (channel) وليس مجموعة؟
                    is_channel = False
                    if hasattr(entity, 'broadcast') and entity.broadcast:
                        is_channel = True
                    elif hasattr(entity, 'megagroup') and entity.megagroup:
                        is_channel = False  # megagroup = مجموعة كبيرة (مناسب)
                    elif hasattr(entity, 'gigagroup') and entity.gigagroup:
                        is_channel = False  # gigagroup = مجموعة عملاقة (مناسب)
                    elif (not hasattr(entity, 'megagroup') and
                          hasattr(entity, 'broadcast') and not entity.broadcast):
                        is_channel = False  # مجموعة عادية

                    if is_channel:
                        logging.info(f"[JOIN] {phone} skipped CHANNEL (broadcast): {raw_link[:50]}")
                        return False, "IS_CHANNEL", None

                    logging.info(f"[JOIN] Telegram accepted JoinChannelRequest for {phone}")
                    await asyncio.wait_for(client(JoinChannelRequest(entity)), timeout=30)
                    await self.metrics.record_api_call(phone)

                    member_count = None
                    if hasattr(entity, 'participants_count'):
                        member_count = entity.participants_count

                    # === POST-JOIN VERIFICATION ===
                    logging.info(f"[JOIN] Verifying membership for {phone} link={raw_link[:50]}...")
                    verified, verified_count = await self._verify_membership(client, entity, phone, raw_link)
                    if verified:
                        return True, "JOINED_VERIFIED", verified_count if verified_count is not None else member_count
                    else:
                        # Join API نجح لكن التحقق فشل — لا نعتبرها نجاح كامل
                        logging.warning(
                            f"[JOIN] ⚠️ JOIN_UNVERIFIED — Telegram accepted but membership not confirmed\n"
                            f"[JOIN] phone={phone} link={raw_link[:50]}"
                        )
                        return True, "JOIN_UNVERIFIED", member_count

                except asyncio.TimeoutError:
                    logging.error(f"[JOIN] ❌ TIMEOUT (30s) phone={phone} op=JOIN link={raw_link[:50]}")
                    return False, "TIMEOUT", None
                except UserAlreadyParticipantError:
                    return False, "ALREADY_MEMBER", None
                except FloodWaitError as e:
                    logging.warning(f"[JOIN] ❌ FloodWait phone={phone} seconds={e.seconds} link={raw_link[:50]}")
                    await self.rate_limiter.record_floodwait(phone, e.seconds)
                    return False, "FLOODWAIT", None
                except (ChannelPrivateError, InviteHashExpiredError) as e:
                    logging.warning(f"[JOIN] ❌ {type(e).__name__} phone={phone} link={raw_link[:50]}")
                    return False, "PRIVATE", None
                except (PeerFloodError, UserBannedInChannelError) as e:
                    logging.error(f"[JOIN] ❌ {type(e).__name__} phone={phone} link={raw_link[:50]}")
                    await self.rate_limiter.record_floodwait(phone, 3600)
                    return False, "BANNED", None
                except ChatWriteForbiddenError:
                    logging.error(f"[JOIN] ❌ ChatWriteForbidden phone={phone} link={raw_link[:50]}")
                    return False, "PRIVATE", None
                except Exception as e:
                    logging.error(f"[JOIN] ❌ {type(e).__name__}: {str(e)[:80]} phone={phone} link={raw_link[:50]}")
                    return False, "FAILED", None

            else:
                # WhatsApp links — no join needed
                return False, "SKIP", None

        except Exception as e:
            logging.error(f"[JOIN] {phone} unexpected: {e}", exc_info=True)
            return False, "FAILED", None
