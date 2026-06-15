# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/cron.py

定时任务工具 — LLM 通过 @tool 调用。
调度器在 lib/scheduler.py 中（后台 asyncio Task 轮询投递）。

支持：
- 一次性：at:2026-05-14T08:00:00+08:00
- 周期：every:1800 (每30分钟) / every:30m / every:1h
- Cron：cron:0 9 * * *（每天早上9点）

由 toolkit globals 中的 scheduler 实例执行实际操作。
"""

import logging
from datetime import UTC

from lib.toolkit import get_global, tool

logger = logging.getLogger(__name__)


@tool()
async def cron_add(schedule: str, message: str, owner_id: str = "") -> dict:
    """创建一个定时任务。

    Args:
        schedule: 调度配置。格式：
            - every:<秒数>    例如 every:1800 (每30分钟)、every:60m (每60分钟)、every:1h (每小时)
            - cron:<表达式>   例如 cron:0 9 * * * (每天早上9点)、cron:*/30 * * * * (每30分钟)
            - at:<ISO时间>    例如 at:2026-05-14T08:00:00+08:00 (一次性)
        message: 到期时发送给 LLM 的消息。LLM 收到后会执行对应任务。
        owner_id: 飞书 open_id。到期后消息投递给谁。填空字符串则投递给任务创建者。
    """
    scheduler = get_global("scheduler")
    if not scheduler:
        return {"error": "定时调度器未初始化"}

    from lib.scheduler import _parse_schedule  # pylint: disable=import-outside-toplevel

    try:
        parsed = _parse_schedule(schedule)
    except ValueError as e:
        return {"error": str(e)}

    result = scheduler.add_job(parsed, message, owner_id)
    return result


@tool()
async def cron_list() -> dict:
    """列出所有定时任务。"""
    scheduler = get_global("scheduler")
    if not scheduler:
        return {"error": "定时调度器未初始化"}

    jobs = scheduler.list_jobs()
    if not jobs:
        return {"result": "没有定时任务。"}

    lines = []
    for j in jobs:
        status = "🟢" if j["enabled"] else "🔴"
        rec = "🔄" if j["is_recurring"] else "⏹️"
        sch = json_dumps_short(j["schedule"])
        next_t = format_ts(j["next_run"])
        lines.append(f"#{j['id']} {status}{rec} {sch} → 下次: {next_t}")
        lines.append(f"   消息: {j['message'][:60]}")
    return {"result": "定时任务列表：\n" + "\n".join(lines)}


@tool()
async def cron_remove(job_id: int) -> dict:
    """删除一个定时任务。

    Args:
        job_id: 定时任务 ID（通过 cron_list 查看）。
    """
    scheduler = get_global("scheduler")
    if not scheduler:
        return {"error": "定时调度器未初始化"}
    return scheduler.remove_job(job_id)


@tool()
async def cron_toggle(job_id: int, enabled: bool) -> dict:
    """启用或暂停一个定时任务。

    Args:
        job_id: 定时任务 ID。
        enabled: True=启用, False=暂停。
    """
    scheduler = get_global("scheduler")
    if not scheduler:
        return {"error": "定时调度器未初始化"}
    return scheduler.toggle_job(job_id, enabled)


def json_dumps_short(obj) -> str:
    """将 schedule dict 转为简短字符串。"""
    t = obj.get("type", "")
    if t == "every":
        return f"每{obj.get('interval', '?')}秒"
    elif t == "cron":
        return f"cron: {obj.get('expr', '?')}"
    elif t == "at":
        return f"at: {obj.get('at', '?')}"
    return str(obj)


def format_ts(ts: float) -> str:
    """将 UTC 时间戳转为北京时间可读字符串。"""
    from datetime import datetime, timedelta

    dt = datetime.fromtimestamp(ts, tz=UTC) + timedelta(hours=8)
    return dt.strftime("%m-%d %H:%M")
