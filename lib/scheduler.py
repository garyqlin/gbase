# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/scheduler.py v2.0 — Cron Scheduler

Changelog (2026-05-19)：
1. action="custom" + action="send" + action="learn" three action types.
   - "custom": Submit message as LLM task to Kernel (silent, no notification)
   - "send": Send Feishu message (original logic)
   - "learn": Call learn_all_topics() (original logic)
2. Heartbeat: write /tmp/opprime_heartbeat every 5s for external stat
"""

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import time
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("OPPRIME_CRON_DB", "data/cron.db")
HEARTBEAT_PATH = "/tmp/opprime_heartbeat"

# ── Schedule Parsing ────────────────────────────────────────


def _parse_schedule(schedule: str) -> dict:
    s = schedule.strip()
    if s.startswith("every:"):
        raw = s[6:].strip()
        if raw.endswith("m"):
            seconds = int(raw[:-1]) * 60
        elif raw.endswith("h"):
            seconds = int(raw[:-1]) * 3600
        elif raw.endswith("s"):
            seconds = int(raw[:-1])
        else:
            seconds = int(raw)
        return {"type": "every", "interval": seconds}
    elif s.startswith("cron:"):
        return {"type": "cron", "expr": s[5:].strip()}
    elif s.startswith("at:"):
        return {"type": "at", "at": s[3:].strip()}
    raise ValueError(f"Unsupported schedule format: {schedule}")


def _next_run(schedule: dict) -> float | None:
    now = time.time()
    if schedule["type"] == "at":
        try:
            dt = datetime.fromisoformat(schedule["at"])
            ts = dt.timestamp()
        except Exception:
            logger.error("Failed to parse at time: %s", schedule["at"])
            return None
        if ts <= now:
            return None
        return ts
    elif schedule["type"] == "every":
        if "first_run" in schedule:
            first_ts = schedule["first_run"]
            if first_ts > now:
                return first_ts
            elapsed = now - first_ts
            periods = int(elapsed // schedule["interval"])
            return first_ts + (periods + 1) * schedule["interval"]
        return now + schedule["interval"]
    elif schedule["type"] == "cron":
        return now + 60
    return now + 60


def _cron_match(expr: str, dt: datetime) -> bool:
    fields = expr.strip().split()
    if len(fields) != 5:
        return False

    def _field_match(field: str, value: int, max_val: int) -> bool:
        if field == "*":
            return True
        for part in field.split(","):
            part = part.strip()
            if "/" in part:
                base, step = part.split("/")
                base_val = 0 if base == "*" else int(base)
                if (value - base_val) % int(step) != 0:
                    continue
                return True
            if "-" in part:
                lo, hi = part.split("-")
                if lo.isdigit() and hi.isdigit() and int(lo) <= value <= int(hi):
                    return True
            if part.isdigit() and int(part) == value:
                return True
        return False

    minute, hour, day, month, weekday = fields
    if not _field_match(minute, dt.minute, 59):
        return False
    if not _field_match(hour, dt.hour, 23):
        return False
    if not _field_match(day, dt.day, 31):
        return False
    if not _field_match(month, dt.month, 12):
        return False
    cron_wd = int(weekday) if weekday.isdigit() else 99
    py_wd = dt.weekday()
    if cron_wd == 0:
        if py_wd != 6:
            return False
    elif cron_wd != 99:
        if cron_wd == 7:
            if py_wd != 6:
                return False
        elif py_wd != cron_wd - 1:
            return False
    return True


# ── SQLite ──────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cron_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule    TEXT    NOT NULL,
    next_run    REAL   NOT NULL,
    message     TEXT    NOT NULL,
    owner_id    TEXT    NOT NULL DEFAULT '',
    action      TEXT    NOT NULL DEFAULT 'send',
    enabled     INTEGER NOT NULL DEFAULT 1,
    is_recurring INTEGER NOT NULL DEFAULT 1,
    created_at  REAL    NOT NULL DEFAULT (strftime('%s','now'))
);
"""

_MIGRATIONS = [
    "ALTER TABLE cron_jobs ADD COLUMN action TEXT NOT NULL DEFAULT 'send'",
]


class CronScheduler:
    """Cron Scheduler — supports three action types.

    - "send": Send Feishu message to owner
    - "learn": Call AutoLearner.learn_all_topics()
    - "custom": Submit message as LLM task to Kernel
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._sender = None  # async def send_text(open_id, text)
        self._learner = None  # AutoLearner instance
        self._kernel = None  # OpprimeKernel instance (for custom action)
        self._learning = False
        self._running = False
        self._task: asyncio.Task | None = None
        self._heartbeat_count = 0
        self._callbacks: dict[str, callable] = {}  # action name → async callback

        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()
        self._migrate()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(_SCHEMA_SQL)
            conn.commit()
        finally:
            conn.close()

    def _migrate(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(cron_jobs)").fetchall()}
            for migration_sql in _MIGRATIONS:
                parts = migration_sql.split()
                try:
                    col_idx = parts.index("COLUMN") + 1
                    col_name = parts[col_idx].strip()
                except (ValueError, IndexError):
                    continue
                if col_name not in cols:
                    logger.info("DB migration: %s", migration_sql[:60])
                    try:
                        conn.execute(migration_sql)
                        conn.commit()
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e).lower():
                            raise
        finally:
            conn.close()

    def set_sender(self, send_func):
        self._sender = send_func

    def set_learner(self, learner):
        self._learner = learner
        logger.info("Scheduler bound to AutoLearner")

    def set_kernel(self, kernel):
        """Set Kernel instance for action='custom'.

        custom action submits message as LLM task to kernel,
        skips Feishu channel, runs silently."""
        self._kernel = kernel
        logger.info("Scheduler bound to Kernel engine")

    def register_callback(self, action: str, callback):
        """Register async callback for action='callback'.

        callback receives (job_id, message, owner_id).
        On trigger, calls callback directly, skips sender/learner/kernel.
        """
        self._callbacks[action] = callback
        logger.info("Scheduler registered callback: action=%s function=%s", action, getattr(callback, "__name__", "unknown"))

    def add_job(self, schedule: dict, message: str, owner_id: str = "", action: str = "send") -> dict:
        next_ts = _next_run(schedule)
        if next_ts is None:
            return {"error": "Expired or cannot calculate next run time"}
        is_rec = 1 if schedule["type"] != "at" else 0
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "INSERT INTO cron_jobs (schedule, next_run, message, owner_id, action, is_recurring) VALUES (?, ?, ?, ?, ?, ?)",
                [json.dumps(schedule, ensure_ascii=False), next_ts, message, owner_id, action, is_rec],
            )
            conn.commit()
            job_id = cur.lastrowid
            logger.info("Cron job created: id=%d action=%s", job_id, action)
            return {"result": f"Cron job created (id={job_id})", "id": job_id, "action": action, "next_run": next_ts}
        finally:
            conn.close()

    def list_jobs(self) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT id, schedule, next_run, message, owner_id, action, enabled, is_recurring, created_at FROM cron_jobs ORDER BY next_run ASC"
            ).fetchall()
            jobs = []
            for r in rows:
                jobs.append(
                    {
                        "id": r[0],
                        "schedule": json.loads(r[1]) if isinstance(r[1], str) else r[1],
                        "next_run": r[2],
                        "message": r[3],
                        "owner_id": r[4],
                        "action": r[5] if len(r) > 5 else "send",
                        "enabled": bool(r[6]),
                        "is_recurring": bool(r[7]),
                        "created_at": r[8] if len(r) > 8 else 0,
                    }
                )
            return jobs
        finally:
            conn.close()

    def remove_job(self, job_id: int) -> dict:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute("DELETE FROM cron_jobs WHERE id = ?", [job_id])
            conn.commit()
            if cur.rowcount > 0:
                return {"result": f"Cron job {job_id} deleted"}
            return {"error": f"Job not found id={job_id} cron job"}
        finally:
            conn.close()

    def toggle_job(self, job_id: int, enabled: bool) -> dict:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "UPDATE cron_jobs SET enabled = ? WHERE id = ?",
                [1 if enabled else 0, job_id],
            )
            conn.commit()
            if cur.rowcount > 0:
                status = "enabled" if enabled else "paused"
                return {"result": f"Cron job {job_id} {status}"}
            return {"error": f"Job not found id={job_id} cron job"}
        finally:
            conn.close()

    # ── Polling Loop ──

    async def run(self):
        if not self._sender:
            raise RuntimeError("Scheduler has no sender function set (call set_sender)")

        self._running = True
        logger.info("Scheduler started (polling every 10s)")

        # ── 启动修复：重算所有过期 next_run，避免死锁 ──
        _fix_conn = sqlite3.connect(self.db_path)
        try:
            _overdue = _fix_conn.execute(
                "SELECT id, schedule FROM cron_jobs WHERE next_run <= ? AND enabled = 1 AND is_recurring = 1",
                [time.time()],
            ).fetchall()
            for row in _overdue:
                sch = json.loads(row[1])
                new_ts = _next_run(sch)
                if new_ts:
                    _fix_conn.execute(
                        "UPDATE cron_jobs SET next_run = ? WHERE id = ?",
                        [new_ts, row[0]],
                    )
            if _overdue:
                _fix_conn.commit()
                logger.info("🔄 启动修复: 重算 %d 个过期 cron 的 next_run", len(_overdue))
        except Exception as _fe:
            logger.error("❌ 启动修复失败: %s", _fe)
        finally:
            _fix_conn.close()

        heartbeat_interval = 5  # write heartbeat every 5s
        tick_count = 0

        while self._running:
            try:
                await self._tick()
                tick_count += 1
                if tick_count % 1 == 0:  # write heartbeat every cycle (~10s, enough)
                    try:
                        with open(HEARTBEAT_PATH, "w") as f:
                            f.write(str(time.time()))
                    except Exception:
                        pass
            except Exception as e:
                logger.error("Scheduler poll error: %s", e)
            await asyncio.sleep(10)

        logger.info("Scheduler stopped")

    async def _tick(self):
        now = time.time()
        now_dt = datetime.now(UTC) + timedelta(hours=8)

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT id, schedule, message, owner_id, action, enabled, is_recurring FROM cron_jobs WHERE next_run <= ? AND enabled = 1",
                [now],
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return

        for row in rows:
            job_id, schedule_json, message, owner_id, action, enabled, is_rec = (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4] if len(row) > 4 else "send",
                row[5],
                row[6] if len(row) > 6 else row[5],
            )

            schedule = json.loads(schedule_json) if isinstance(schedule_json, str) else schedule_json

            if schedule.get("type") == "cron" and not _cron_match(schedule.get("expr", ""), now_dt):
                continue

            logger.info("Cron triggered: id=%d action=%s message=%s", job_id, action, message[:60])

            # update next_run first (prevent re-trigger)
            if is_rec:
                sch = schedule.copy()
                next_ts = _next_run(sch)
                if next_ts is not None:
                    conn2 = sqlite3.connect(self.db_path)
                    try:
                        conn2.execute("UPDATE cron_jobs SET next_run = ? WHERE id = ?", [next_ts, job_id])
                        conn2.commit()
                    finally:
                        conn2.close()

            # dispatch by action
            if action in self._callbacks:
                await self._callbacks[action](job_id, message, owner_id)
            elif action == "custom":
                await self._dispatch_custom(job_id, message, owner_id)
            elif action == "learn":
                await self._dispatch_learn(job_id)
            else:
                await self._dispatch_send(job_id, message, owner_id)

            if not is_rec:
                conn3 = sqlite3.connect(self.db_path)
                try:
                    conn3.execute("DELETE FROM cron_jobs WHERE id = ?", [job_id])
                    conn3.commit()
                finally:
                    conn3.close()

    async def _dispatch_send(self, job_id: int, message: str, owner_id: str):
        if owner_id and self._sender:
            try:
                await self._sender(owner_id, message)
            except Exception as e:
                logger.error("Cron job %d send failed: %s", job_id, e)

    async def _dispatch_learn(self, job_id: int):
        if self._learning:
            logger.warning("Previous learning still in progress, skip trigger (job=%d)", job_id)
            return
        if not self._learner:
            logger.error("Cron job %d action=learn but no AutoLearner set", job_id)
            return
        self._learning = True
        try:
            logger.info("AutoLearn started (job=%d)", job_id)
            results = await self._learner.learn_all_topics()
            total_saved = sum(r.get("saved", 0) for r in results)
            logger.info("AutoLearn completed (job=%d): %d topics, saved %d", job_id, len(results), total_saved)
        except Exception as e:
            logger.error("AutoLearn error (job=%d): %s", job_id, e)
        finally:
            self._learning = False

    async def _dispatch_custom(self, job_id: int, message: str, owner_id: str):
        """Handle custom cron action: execute a script or submit to kernel.

        If message starts with `!script:`, run the given Python script as a subprocess.
        Otherwise submit the message to kernel as a cron task.
        """
        if message.startswith("!script:"):
            script_path_raw = message[len("!script:"):].strip()
            # Security: restrict scripts to the cron/ directory
            script_base = os.path.join(os.path.dirname(__file__), "..", "cron")
            script_path = os.path.abspath(os.path.join(script_base, script_path_raw))
            if not script_path.startswith(os.path.abspath(script_base)):
                logger.error("Script path escape denied: %s", script_path_raw)
                return
            if not os.path.isfile(script_path):
                logger.error("Script not found: %s", script_path)
                return
            logger.info("Cron job %d executing script: %s", job_id, script_path)
            try:
                proc = await asyncio.create_subprocess_exec(
                    "python3", script_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                exit_code = proc.returncode
                out_text = stdout.decode("utf-8", errors="replace")[:2000]
                err_text = stderr.decode("utf-8", errors="replace")[:1000]
                logger.info("Script done (job=%d): exit=%d, stdout=%d chars", job_id, exit_code, len(out_text))
                if err_text:
                    logger.warning("Script stderr (job=%d): %s", job_id, err_text)

                if exit_code != 0 and owner_id and self._sender:
                    await self._sender(owner_id, f"[Health Check] Script exit code {exit_code}\n{err_text[:500]}")
            except asyncio.TimeoutError:
                logger.error("Script timeout (job=%d): %s", job_id, script_path)
            except Exception as e:
                logger.error("Script exception (job=%d): %s", job_id, e)
            return

        if not self._kernel:
            logger.warning("Cron job %d action=custom but no Kernel set", job_id)
            return

        try:
            enriched = f"[Cron #{job_id}] {message}"
            from lib.toolkit import set_global as tk_set_global

            if owner_id:
                tk_set_global("feishu_sender_id", owner_id)

            result = await self._kernel.run(
                user_message=enriched,
                session=None,
                platform="cron",
            )

            logger.info("Custom task done (job=%d): output %d chars", job_id, len(str(result or "")))
        except Exception as e:
            logger.error("Custom task exception (job=%d): %s", job_id, e)

    def stop(self):
        self._running = False
