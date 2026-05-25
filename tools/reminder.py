# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/reminder.py

Reminder/scheduler tool. Uses JSON file storage.
"""

import json
import os
import time

from lib.toolkit import tool

REMINDER_FILE = "data/reminders.json"


def _load_reminders() -> list:
    if not os.path.exists(REMINDER_FILE):
        return []
    try:
        with open(REMINDER_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _save_reminders(reminders: list):
    os.makedirs(os.path.dirname(REMINDER_FILE) or ".", exist_ok=True)
    with open(REMINDER_FILE, "w") as f:
        json.dump(reminders, f, ensure_ascii=False, indent=2)


@tool()
async def reminder_add(content: str) -> dict:
    """添加一条提醒。"""
    reminders = _load_reminders()
    reminder = {
        "id": int(time.time() * 1000),
        "content": content,
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
        "done": False,
    }
    reminders.append(reminder)
    _save_reminders(reminders)
    return {"result": f"已添加提醒: {content}"}


@tool()
async def reminder_list() -> dict:
    """列出所有未完成的提醒。"""
    reminders = _load_reminders()
    pending = [r for r in reminders if not r.get("done")]
    if not pending:
        return {"result": "没有未完成的提醒。"}
    items = [f"- {r['content']}（{r.get('created_at', '')}）" for r in pending]
    return {"result": "未完成提醒：\n" + "\n".join(items)}


@tool()
async def reminder_delete(id: int) -> dict:
    """删除一条提醒。id 是 reminder_add 时返回的 id。"""
    reminders = _load_reminders()
    for r in reminders:
        if r.get("id") == id:
            r["done"] = True
            _save_reminders(reminders)
            return {"result": f"已删除提醒: {r['content']}"}
    return {"error": f"未找到 id={id} 的提醒"}
