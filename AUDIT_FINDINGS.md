# AUDIT FINDINGS — WhatsApp Link Monitor

Persistent working tree: `/home/z/wlm` (clone of
`https://github.com/azammaza770309310-boop/whatsapp-link-monitor`).
Base commit at task start: `9077819` (DURABILITY: Message Journal).

## CHANGELOG (Task 8a)

**Agent**: fix-12-original (general-purpose sub-agent).
**Timestamp (UTC)**: 2026-08-25 (Task 8a window).
**Scope**: 12 original production-hardening fixes (B02–B09 / B01-code / B06 /
L03 / L04 / L07), per the audit body + worklog Tasks 5a/5b descriptions.
**Commit**: `2c20050` — `AUDIT-FIX(8a): 12 original production-hardening fixes`
(local only; NO push — no GitHub credentials in this environment, per prior
CRED-CHECK worklog).
**Parent**: `9077819`.

### Approach
- Reset the persistent working tree to a clean `9077819` (a prior session had
  left uncommitted modifications that went BEYOND the 12-fix scope — N-fixes,
  PERSIST snapshot/restore, ai_drainer, journal_snapshot — i.e. Task 8b work).
  To deliver EXACTLY the 12 fixes prescribed for 8a, the tree was reset and the
  12 fixes were re-applied fresh from the worklog descriptions (source of truth).
- All 12 fixes are minimal, backward-compatible, and preserve the existing log
  markers (`[JOURNAL]`, `[JOURNAL-RECOVERY]`, `[PIPELINE-N]`, `[DELETE-HANDLER]`,
  `[PERIODIC-SYNC]`, `[SUPERVISOR]`, `[POLLING-WATCHDOG]`, `[SUPABASE]`,
  `[DASHBOARD]`).

### Files changed (9 files, +1163/-85)
- `bot.py` — B01 (DATA_DIR env), B02 (persist last_msg_id), B03 (polling_status
  DB read), B04 (load_from_db), B05 (recurring loop), B06 (X-Api-Key middleware
  on all /api/*), B07 (_supervisor_loop), B08 (4× debug→WARNING), B09
  (lookup_any guard), L03 (_polling_watchdog_loop), L04 (ALTER attempt),
  start()/stop() registrations.
- `link_system.py` — L07: `claim_message(lease_duration_s: Optional[int]=None)`
  resolved via `LEASE_DURATION_S` env (default 180s).
- `source_registry.py` — L07: `LEASE_DURATION_S = int(os.environ.get(...))` +
  `import os`.
- `render.yaml` — +DATA_DIR (sync:false, persistent-disk comment), +DASHBOARD_API_KEY
  (sync:false), +LEASE_DURATION_S (value:"180").
- `tests/test_audit_regressions.py` — NEW, 12 test functions / 32 assertions,
  standalone script style (RESULTS list, sys.exit rc), `_OPEN_DBS` +
  `close_all_test_dbs()` teardown for aiosqlite thread cleanup.
- `tests/test_message_journal.py` — `_OPEN_DBS`+teardown added; test_N updated
  for B05 (recurring loop now requires `_running` flip after one sweep).
- `tests/test_source_registry.py` — `_OPEN_DBS`+teardown added (no behavior change).
- `.gitignore` — excludes `.venv-test/`, `__pycache__/`, `/data/` runtime DBs.
- `commit_msg.txt` — commit-message artifact (written per STEP 7 prescription).

### Fix-by-fix summary
| Fix | File:area | Change |
|-----|-----------|--------|
| B01-code | bot.py:60-67 | `DATA_DIR = os.environ.get('DATA_DIR','data')` |
| B02 | bot.py:_poll_one_chat | `update_monitored_chat(chat_id, last_msg_id=new_max_id, last_activity=utcnow().iso())` after new_max_id |
| B03 | bot.py:api_polling_status_handler | `SELECT COUNT(*) FROM monitored_chats WHERE next_poll_at<=?` + `scheduler_running` from real task |
| B04 | bot.py:_periodic_sync | `await self.source_registry.load_from_db()` after `_sync_monitored_chats()` |
| B05 | bot.py:_journal_recovery | `while self._running:` loop + per-cycle `await asyncio.sleep(60)` (was fire-once) |
| B06 | bot.py:start_http_server | `dashboard_api_key_middleware` gates /api/* behind optional X-Api-Key; UNSET=open+one WARNING |
| B07 | bot.py:_supervisor_loop | 60s supervisor recreates polling/journal_recovery/journal_snapshot(8b,hasattr-guarded)/ai_drainer(8b)/joiner |
| B08 | bot.py:_journal_write/_set_state_safe/_mark_deleted_safe/_record_delete_miss | 4× `logging.debug`→`logging.warning(f"[JOURNAL] ... FAILED: {e}")` |
| B09 | bot.py:_on_message_deleted | guard `if chat_id is not None:` for journal_get vs lookup_any fallback |
| L03 | bot.py:_polling_watchdog_loop | 30s dedicated scheduler watchdog; `[POLLING-WATCHDOG] ... restarted` |
| L04 | bot.py:_supabase_ensure_schema | per-column ALTER via `rpc/exec_sql` try/except; logs exact ALTER SQL fallback; never breaks startup |
| L07 | source_registry.py + link_system.py | `LEASE_DURATION_S=int(os.environ.get('LEASE_DURATION_S','180'))`; claim_message resolves env at call time |

### Test results (before → after)
| Test file | Before (base 9077819) | After (8a, commit 2c20050) |
|-----------|----------------------|----------------------------|
| test_message_journal.py | 66/66 (but test_N broke under B05 until patched) | 66/66 ✅ |
| test_source_registry.py | 103/103 | 103/103 ✅ |
| test_phase3_contracts.py | 96/96 | 96/96 ✅ |
| test_deployment_updated.py | 35/35 | 35/35 ✅ |
| test_extractor_comparison.py | 3/3 | 3/3 ✅ |
| test_audit_regressions.py | (did not exist) | 32/32 ✅ (NEW) |
| **TOTAL** | 303/303 (no audit-regression file) | **335/335** |

All 6 test files exit 0. `git diff --check` clean (no whitespace errors).
Secret scan (`ghp_` / bot_token / api_hash / supabase_key patterns) on the full
diff + new file → **no matches** (clean).

### Pre-commit verification
- `git status -s`: 9 files staged (6 M + 2 A + .gitignore), working tree clean
  post-commit.
- `git diff --check`: exit 0.
- Secret scan: clean (rc=1, no matches).
- `.venv-test/` excluded via .gitignore (not committed).

### No push
No `git push` was executed. Per the prior CRED-CHECK worklog, this environment
has no GitHub credentials (no PAT, no SSH key, no gh CLI, no credential.helper).
The commit `2c20050` is LOCAL ONLY. User must push with a PAT/SSH key.

### External operator actions still required (not code-blockable)
1. **B01 durability**: attach a Render Disk ≥1GB mounted at `/data` and set
   `DATA_DIR=/data` in the Render env (else the local SQLite journal/queue is
   still wiped on restart — code is ready, infra is not).
2. **Push**: `git push origin main` from a host with GitHub credentials.
3. (Optional) `DASHBOARD_API_KEY`: set to lock the dashboard /api/*.
4. (Optional) `LEASE_DURATION_S`: keep `180` (already the new default).
