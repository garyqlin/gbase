# SPDX-License-Identifier: MIT
"""
gbase/tools/cron.py

Cron/scheduler tool — called via @tool.
Scheduler lives in lib/scheduler.py (background asyncio Task polling delivery).

Supports:
- One-shot:   at:2026-05-14T08:00:00+08:00
- Periodic:   every:1800 (every 30 min) / every:30m / every:1h
- Cron:       cron:0 9 * * * (daily at 9am)

The scheduler instance in toolkit globals performs the actual operations.
"""

import logging
from datetime import UTC

from lib.toolkit import get_global, tool

logger = logging.getLogger(__name__)


@tool()
async def cron_add(schedule: str, message: str, owner_id: str = "") -> dict:
    """Create a scheduled task.

    Args:
        schedule: Schedule configuration. Format:
            - every:<seconds>    e.g. every:1800 (every 30 min), every:60m, every:1h
            - cron:<expr>        e.g. cron:0 9 * * * (daily at 9am), cron:*/30 * * * * (every 30 min)
            - at:<ISO time>      e.g. at:2026-05-14T08:00:00+08:00 (one-shot)
        message: Message delivered to LLM when triggered. LLM executes the task on receipt.
        owner_id: Notification channel ID. Empty string means deliver to task creator.
    """
    scheduler = get_global("scheduler")
    if not scheduler:
        return {"error": "Scheduler not initialized"}

    from lib.scheduler import _parse_schedule  # pylint: disable=import-outside-toplevel

    try:
        parsed = _parse_schedule(schedule)
    except ValueError as e:
        return {"error": str(e)}

    result = scheduler.add_job(parsed, message, owner_id)
    return result


@tool()
async def cron_list() -> dict:
    """List all scheduled tasks."""
    scheduler = get_global("scheduler")
    if not scheduler:
        return {"error": "Scheduler not initialized"}

    jobs = scheduler.list_jobs()
    if not jobs:
        return {"result": "No scheduled tasks."}

    lines = []
    for j in jobs:
        status = "🟢" if j["enabled"] else "🔴"
        rec = "🔄" if j["is_recurring"] else "⏹️"
        sch = json_dumps_short(j["schedule"])
        next_t = format_ts(j["next_run"])
        lines.append(f"#{j['id']} {status}{rec} {sch} → next: {next_t}")
        lines.append(f"   msg: {j['message'][:60]}")
    return {"result": "Scheduled task list:\n" + "\n".join(lines)}


@tool()
async def cron_remove(job_id: int) -> dict:
    """Delete a scheduled task.

    Args:
        job_id: Scheduled task ID (view via cron_list).
    """
    scheduler = get_global("scheduler")
    if not scheduler:
        return {"error": "Scheduler not initialized"}
    return scheduler.remove_job(job_id)


@tool()
async def cron_toggle(job_id: int, enabled: bool) -> dict:
    """Enable or pause a scheduled task.

    Args:
        job_id: Scheduled task ID.
        enabled: True=enable, False=pause.
    """
    scheduler = get_global("scheduler")
    if not scheduler:
        return {"error": "Scheduler not initialized"}
    return scheduler.toggle_job(job_id, enabled)


def json_dumps_short(obj) -> str:
    """Convert schedule dict to a short string."""
    t = obj.get("type", "")
    if t == "every":
        return f"Every {obj.get('interval', '?')}s"
    elif t == "cron":
        return f"cron: {obj.get('expr', '?')}"
    elif t == "at":
        return f"at: {obj.get('at', '?')}"
    return str(obj)


def format_ts(ts: float) -> str:
    """Convert UTC timestamp to Beijing time (UTC+8) readable string."""
    from datetime import datetime, timedelta

    dt = datetime.fromtimestamp(ts, tz=UTC) + timedelta(hours=8)
    return dt.strftime("%m-%d %H:%M")
