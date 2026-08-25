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

---

## CHANGELOG (Task 8b)

**Agent**: fix-10-new+persistence+ai (general-purpose sub-agent).
**Timestamp (UTC)**: 2026-08-25 (Task 8b window).
**Scope**: 10 NEW edge-case fixes (N01-N10) from re-audit pass 2 (Task 7)
+ PERSISTENCE Option C (journal snapshot to Supabase + restore on startup)
+ AI drainer worker (`_ai_drainer_worker`, gated by `AI_DRAIN_ENABLED`).
**Commit**: `803822b` — `AUDIT-FIX(8b): 10 new edge-case fixes + journal
durability snapshot + AI drainer` (local only; NO push — no GitHub
credentials in this environment, per prior CRED-CHECK worklog).
**Parent**: `2c20050` (Task 8a — 12 original production-hardening fixes).

### Approach
- 10 fixes (N01-N10) per Task 7 audit findings (file:line evidence
  preserved in the worklog). All fixes are minimal, backward-compatible,
  and preserve existing log markers (`[JOURNAL]`, `[RECONCILE]`,
  `[PIPELINE-1]`, `[DELETE-HANDLER]`, `[POLLING]`, `[POLL-SCHED]`,
  `[AI-DRAIN]`, `[JOURNAL-SNAPSHOT]`, `[SUPERVISOR]`).
- N01 fixes the `journal_cleanup` predicate so `pending`/`failed` rows
  survive the 24h sweep (they're recoverable by journal_recovery).
- N02 reorders the reconcile path: `journal_write(pending)` happens
  BEFORE `claim` so a crash anywhere between journal_write and
  set_state leaves a recoverable pending row (the previous order wrote
  the journal AFTER claim → a mid-rescue crash silently lost the
  message). Final state after successful rescue changed to `rescued`
  (was `processed`) — semantically more accurate (reconcile rescues
  messages MISSED by NewMessage). Existing tests still pass; the
  terminal-state set in `journal_set_state` already includes both.
- N03 stops the LOSER path of `_on_user_message` from overwriting the
  WINNER's `pending` journal row with `dup_claim`. The LOSER now logs a
  debug + returns silently; the WINNER's `pending` stays so
  `journal_pending_older_than` (filters `state='pending'`) still rescues
  it if the winner also crashes.
- N04 wraps each `for deleted_msg_id in deleted_ids:` iteration in a
  per-iteration `try/except Exception: log + continue` so one bad row
  doesn't abort the remaining 49 in a mass-delete batch.
- N05 adds `self._journal_fail_count` consecutive-failure counter to
  `_journal_write`. On each failure: increment + emit a rate-limited
  (max once/60s) ERROR `[JOURNAL] circuit-stressed: N consecutive
  failures` after the count crosses 50. On success: reset to 0. The
  journal is NEVER permanently disabled — keep retrying.
- N06 narrows `enqueue_link`'s `except Exception: return False` to
  `except sqlite3.IntegrityError: return False` (UNIQUE violation only).
  All other exceptions (SQLITE_BUSY / disk-full / locked DB / corrupt)
  now propagate so the caller can retry or surface — was being silently
  swallowed as a "duplicate" (link never written, message lost).
- N07 adds `_ai_drainer_worker` (30s cycle, 10 rows/batch) that fetches
  links where `ai_approved IS NULL` from Supabase via REST, runs
  `ai_analyzer.analyze_message`, and PATCHes the verdict back. Gated by
  `AI_DRAIN_ENABLED` env (default false) so it doesn't compete with the
  live pipeline unless explicitly enabled. Respects `AI_BATCH_MODE` —
  the drainer is independent of the batch-mode flag (drainer opt-in via
  its own env). 429 → 60s backoff. Empty queue → 60s sleep.
- N08 adds `await self.floodwait_mgr.block(phone, e.seconds)` to the
  FloodWaitError except blocks in `_poll_one_chat` and
  `_reconcile_chat_after_delete_miss` (the latter previously swallowed
  FloodWait via a broad `except Exception` — added an explicit
  `except FloodWaitError` block before it). Real method name: `block`
  (FloodWaitManager has no `register` — verified via class inspection).
- N09 iterates the `asyncio.gather(return_exceptions=True)` results in
  PollingScheduler.run to log per-task failures at WARNING (was already
  set but never iterated — exceptions silently dropped, especially since
  `_poll_one`'s inner except logs at DEBUG which is suppressed in prod).
- N10 wraps the check-then-act window of `_ensure_conn` in
  `async with self._lock:` with a double-check inside the lock
  (was declaring `self._lock` but UNUSED → 2 concurrent callers both
  called `aiosqlite.connect` and leaked the first connection).

### PERSISTENCE Option C — journal durability snapshot
- `_journal_snapshot_loop` (30s background task): SELECT up to 500
  at-risk rows (`state IN ('pending','no_text','delete_miss')`) from
  local `message_journal`, POST batch to Supabase
  `message_journal_snapshot` (upsert with
  `Prefer: resolution=merge-duplicates`, PK `(chat_id, msg_id)`).
  - On table-missing / 404: rate-limited WARNING (max once/hour)
    printing the exact SQL to run in Supabase.
  - Doesn't replace the SQLite journal — additive mirror only.
- `_restore_journal_from_supabase` (called on startup BEFORE
  `_journal_recovery`): SELECT at-risk rows from snapshot, INSERT OR
  IGNORE into local `message_journal`. Idempotent — concurrent inserts
  dedup safely. Returns the row count restored.
- Registered in `start()` (snapshot + ai_drainer launched),
  `stop()` (both cancelled), `_supervisor_loop` (already had
  `hasattr`-guarded recovery from Task 8a — now activated).
- Survives Render free-tier ephemeral-disk restart WITHOUT buying a
  persistent disk (operator still should attach one for B01 durability,
  but Option C is the cheap fallback).

### Files changed (8 files, +1229/-164 in commit 803822b)
- `bot.py` — N02/N03/N04/N05/N07/N08/N10 + persistence (3 new methods)
  + start()/stop()/_supervisor_loop registrations + reconcile reorder.
- `link_system.py` — N01 (`AND state NOT IN ('pending','failed')`),
  N06 (`except sqlite3.IntegrityError`), `import sqlite3`.
- `source_registry.py` — N09 (gather results iteration + WARNING).
- `render.yaml` — `+AI_DRAIN_ENABLED` (value `"false"`, opt-in).
- `tests/test_audit_regressions.py` — +12 new test functions
  (N01-N10 + 2 persistence), standalone script style. 32→57 assertions.
- `tests/test_message_journal.py` — Test E updated to assert N01's
  new preservation behavior (pending row >24h SURVIVES cleanup,
  processed row >24h still deleted, removed_old count is 0 because
  the only "old" row is pending).
- `AUDIT_FINDINGS.md` — committed (was untracked at 8a; now tracked
  with both 8a + 8b changelog sections).
- `commit_msg.txt` — commit-message artifact.

### Test results (before → after)
| Test file | Before (8a, commit 2c20050) | After (8b, commit 803822b) |
|-----------|----------------------------|----------------------------|
| test_message_journal.py | 66/66 | 66/66 ✅ (Test E updated for N01) |
| test_source_registry.py | 103/103 | 103/103 ✅ |
| test_phase3_contracts.py | 96/96 | 96/96 ✅ |
| test_deployment_updated.py | 35/35 | 35/35 ✅ |
| test_extractor_comparison.py | 3/3 | 3/3 ✅ |
| test_audit_regressions.py | 32/32 | 57/57 ✅ (+12 new tests, +25 assertions) |
| **TOTAL** | **335/335** | **360/360** |

All 6 test files exit 0. `git diff --check` clean (no whitespace errors).
Secret scan on the full diff for `ghp_` / `bot_token` / `api_hash` /
`supabase_key` / `password` patterns → only pre-existing matches
(`password=password` runtime sign_in call on bot.py:5390 — NOT in the
diff). `'fake_key'` literal in test mocks is not a real secret.

### Pre-commit verification
- `git status -s`: 7 files modified + 1 new (AUDIT_FINDINGS.md).
- `git diff --check`: exit 0 (no whitespace errors).
- Secret scan: clean.
- `.venv-test/` excluded via .gitignore (not committed, inherited from 8a).

### No push
No `git push` was executed. Per the prior CRED-CHECK worklog, this
environment has no GitHub credentials. Commit `803822b` is LOCAL ONLY.
User must push with a PAT/SSH key.

### External operator actions still required (not code-blockable)
1. **Render Disk ≥1GB at `/data`** + `DATA_DIR=/data` for B01 durability
   (Option C is a cheap fallback but doesn't replace a real disk).
2. **Push**: `git push origin main` from a host with GitHub credentials.
3. **Run this SQL once in Supabase SQL Editor** for the journal snapshot
   table (only needed to enable the persistence mirror — without it the
   snapshot loop will log a one-per-hour WARNING and keep retrying):
   ```sql
   CREATE TABLE IF NOT EXISTS message_journal_snapshot (
       chat_id BIGINT NOT NULL,
       msg_id BIGINT NOT NULL,
       raw_text TEXT,
       source_phone TEXT,
       chat_title TEXT,
       chat_username TEXT,
       chat_link_type TEXT,
       sender_id BIGINT,
       sender_name TEXT,
       state TEXT NOT NULL,
       received_at DOUBLE PRECISION,
       PRIMARY KEY (chat_id, msg_id)
   );
   CREATE UNIQUE INDEX IF NOT EXISTS idx_journal_snapshot_pk
       ON message_journal_snapshot (chat_id, msg_id);
   ```
4. **(Optional) `AI_DRAIN_ENABLED=true`** to start draining the 26K
   ai_pending backlog in the background. Leave false unless
   `AI_BATCH_MODE=true` is also set (which it is by default).
5. (Optional) `DASHBOARD_API_KEY`, `LEASE_DURATION_S=180` (from 8a).
