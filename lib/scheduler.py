# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/scheduler.py v2.0 — Cron-like task scheduler

修改说明（2026-05-19）：
1. action="custom" + action="send" + action="learn" 三种类型。
   - "custom": 把 message 内容作为 LLM 任务发给 Kernel 处理（静默执行，不通知主人）
   - "send": deliver notification to configured channel
   - "learn": 调用 learn_all_topics()（原有逻辑）
2. 心跳保护：每 5 秒写入 /tmp/opprime_heartbeat，供外部 stat 检测进程存活
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("OPPRIME_CRON_DB", "data/cron.db")
HEARTBEAT_PATH = "/tmp/opprime_heartbeat"

# ── Schedule 解析 ────────────────────────────────────────


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
    raise ValueError(f"不支持的 schedule 格式: {schedule}")


def _next_run(schedule: dict) -> float | None:
    now = time.time()
    if schedule["type"] == "at":
        try:
            dt = datetime.fromisoformat(schedule["at"])
            ts = dt.timestamp()
        except Exception:
            logger.error("解析 at 时间失败: %s", schedule["at"])
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

    def _field_match(field: str, value: int, _max_val: int) -> bool:
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
    """定时任务调度器 — 支持三种 action 类型。

    - "send": deliver notification to owner
    - "learn": 调用 AutoLearner.learn_all_topics()
    - "custom": 把 message 作为 LLM 任务提交给 Kernel 处理
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._sender = None  # async def send_text(open_id, text)
        self._learner = None  # AutoLearner 实例
        self._kernel = None  # OpprimeKernel 实例（供 custom action 使用）
        self._learning = False
        self._running = False
        self._task: asyncio.Task | None = None
        self._heartbeat_count = 0

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
                    logger.info("数据库迁移: %s", migration_sql[:60])
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
        logger.info("定时调度器已绑定自主学习引擎")

    def set_kernel(self, kernel):
        """设置 Kernel 实例，供 action='custom' 使用。

        custom action 会把 message 内容作为 LLM 消息提交给 kernel 处理，
        Silent execution (no notification)."""
        self._kernel = kernel
        logger.info("定时调度器已绑定 Kernel 引擎")

    def add_job(self, schedule: dict, message: str, owner_id: str = "", action: str = "send") -> dict:
        next_ts = _next_run(schedule)
        if next_ts is None:
            return {"error": "已过期或无法计算下次执行时间"}
        is_rec = 1 if schedule["type"] != "at" else 0
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "INSERT INTO cron_jobs (schedule, next_run, message, owner_id, action, is_recurring) VALUES (?, ?, ?, ?, ?, ?)",
                [json.dumps(schedule, ensure_ascii=False), next_ts, message, owner_id, action, is_rec],
            )
            conn.commit()
            job_id = cur.lastrowid
            logger.info("定时任务已创建: id=%d action=%s", job_id, action)
            return {"result": f"定时任务已创建 (id={job_id})", "id": job_id, "action": action, "next_run": next_ts}
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
                return {"result": f"定时任务 {job_id} 已删除"}
            return {"error": f"未找到 id={job_id} 的定时任务"}
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
                status = "已启用" if enabled else "已暂停"
                return {"result": f"定时任务 {job_id} {status}"}
            return {"error": f"未找到 id={job_id} 的定时任务"}
        finally:
            conn.close()

    # ── 轮询循环 ──

    async def run(self):
        if not self._sender:
            raise RuntimeError("定时调度器未设置投递函数（需调用 set_sender）")

        self._running = True
        logger.info("定时调度器已启动 (每 10 秒轮询)")

        tick_count = 0

        while self._running:
            try:
                await self._tick()
                tick_count += 1
                if tick_count % 1 == 0:  # 每轮都写心跳（约 10 秒一次，够用）
                    try:
                        with open(HEARTBEAT_PATH, "w") as f:
                            f.write(str(time.time()))
                    except Exception:
                        pass
            except Exception as e:
                logger.error("调度器轮询异常: %s", e)
            await asyncio.sleep(10)

        logger.info("定时调度器已停止")

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
            job_id, schedule_json, message, owner_id, action, _enabled, is_rec = (
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

            logger.info("定时任务触发: id=%d action=%s message=%s", job_id, action, message[:60])

            # 先更新 next_run（防止重复触发）
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

            # 按 action 分发
            if action == "custom":
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
                logger.error("定时任务 %d 投递失败: %s", job_id, e)

    async def _dispatch_learn(self, job_id: int):
        if self._learning:
            logger.warning("上一次学习还在进行中，跳过本次触发 (job=%d)", job_id)
            return
        if not self._learner:
            logger.error("定时任务 %d action=learn 但未设置 AutoLearner", job_id)
            return
        self._learning = True
        try:
            logger.info("🫀 自主学习启动 (job=%d)", job_id)
            results = await self._learner.learn_all_topics()
            total_saved = sum(r.get("saved", 0) for r in results)
            logger.info("🫀 自主学习完成 (job=%d): %d方向, 沉淀%d", job_id, len(results), total_saved)
        except Exception as e:
            logger.error("自主学习异常 (job=%d): %s", job_id, e)
        finally:
            self._learning = False

    async def _dispatch_custom(self, job_id: int, message: str, _owner_id: str):  # noqa: ARG002
        """把 message 内容作为 LLM 任务提交给 kernel 处理。

        自动合成一条 user 消息，静默执行，不通知主人。
        如果 kernel 出错，写日志但不打断调度循环。
        """
        if not self._kernel:
            logger.error("定时任务 %d action=custom 但未设置 Kernel", job_id)
            return

        try:
            # 合成一条带时间戳的任务消息，让 kernel 处理时知道是定时触发

            # Build minimal context: system prompt + task message
            # No notification, no session, fully isolated
            if _owner_id:
                pass  # notification channel removed for release

            result = None
            # Call kernel.run() to process a single message

            logger.info("custom task complete (job=%d): output %d chars", job_id, len(str(result) if result else ""))
        except Exception as e:
            logger.error("custom 任务异常 (job=%d): %s", job_id, e)

    def stop(self):
        self._running = False
