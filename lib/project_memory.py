# SPDX-License-Identifier: MIT
"""
lib/project_memory.py

波段二核心：长项目记忆引擎。

解决 GBase 最大的跨对话痛点——每次新对话都要重新理解项目上下文。

核心能力：
- Phase/Task 进度追踪：记录"在做什么、做到哪了、下一步是什么"
- Decision 决策日志：关键决策 + 上下文 + 推理链，可回溯
- Context 上下文快照：对话结束自动Save，对话开始自动恢复
- 与进化引擎联动：每次进化记录进记忆，每次改动有上下文

数据目录：data/project_memory/
"""

import json
import os
import time
from datetime import datetime

# ── 路径配置 ────────────────────────────────────────

MEMORY_DIR = "/home/gbase-v2/data/project_memory"
PHASES_PATH = os.path.join(MEMORY_DIR, "phases.json")
DECISIONS_PATH = os.path.join(MEMORY_DIR, "decisions.jsonl")
CONTEXT_PATH = os.path.join(MEMORY_DIR, "context.json")
STATUS_PATH = os.path.join(MEMORY_DIR, "status.md")

# ── 内部工具 ────────────────────────────────────────


def _ensure_dirs():
    os.makedirs(MEMORY_DIR, exist_ok=True)


def _load_json(path: str, default=None) -> dict:
    if default is None:
        default = {}
    _ensure_dirs()
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            pass
    return default


def _save_json(path: str, data: dict):
    _ensure_dirs()
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def _append_jsonl(path: str, entry: dict):
    _ensure_dirs()
    entry["_ts"] = datetime.now().isoformat()
    with open(path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


# ── Phase/Task 进度追踪 ──────────────────────────────


def set_phase(phase_id: str, label: str, description: str, status: str = "active"):
    """
    设置或更新一个阶段。

    Args:
        phase_id: 阶段 ID（如 "band-1", "band-2"）
        label: 阶段名称（如 "波段一：核心基础设施"）
        description: 阶段描述
        status: active / completed / paused
    """
    phases = _load_json(PHASES_PATH)
    if phase_id not in phases:
        phases[phase_id] = {
            "label": label,
            "description": description,
            "status": status,
            "tasks": {},
            "created": datetime.now().isoformat(),
            "updated": datetime.now().isoformat(),
        }
    else:
        old_status = phases[phase_id].get("status")
        phases[phase_id]["status"] = status
        phases[phase_id]["updated"] = datetime.now().isoformat()
        if old_status != status and status == "completed":
            phases[phase_id]["completed"] = datetime.now().isoformat()
    _save_json(PHASES_PATH, phases)
    _refresh_status_md()
    return phases[phase_id]


def add_task(phase_id: str, task_id: str, label: str, description: str = "", status: str = "todo"):
    """在阶段下添加任务。status: todo / doing / done"""
    phases = _load_json(PHASES_PATH)
    if phase_id not in phases:
        raise ValueError(f"阶段 {phase_id} 不存在，请先用 set_phase 创建")
    phases[phase_id]["tasks"][task_id] = {
        "label": label,
        "description": description,
        "status": status,
        "updated": datetime.now().isoformat(),
    }
    phases[phase_id]["updated"] = datetime.now().isoformat()
    _save_json(PHASES_PATH, phases)
    _refresh_status_md()
    return phases[phase_id]


def update_task(phase_id: str, task_id: str, status: str):
    """更新任务状态。"""
    phases = _load_json(PHASES_PATH)
    if phase_id not in phases or task_id not in phases[phase_id].get("tasks", {}):
        raise ValueError(f"任务 {phase_id}/{task_id} 不存在")
    old_status = phases[phase_id]["tasks"][task_id].get("status")
    phases[phase_id]["tasks"][task_id]["status"] = status
    phases[phase_id]["tasks"][task_id]["updated"] = datetime.now().isoformat()
    phases[phase_id]["updated"] = datetime.now().isoformat()

    # 自动检测阶段是否完成
    tasks = phases[phase_id]["tasks"]
    if all(t["status"] == "done" for t in tasks.values()) and tasks:
        phases[phase_id]["status"] = "completed"
        phases[phase_id]["completed"] = datetime.now().isoformat()

    _save_json(PHASES_PATH, phases)
    _refresh_status_md()

    # 如果任务完成，记录决策
    if old_status != "done" and status == "done":
        record_decision(
            phase=phase_id,
            task=task_id,
            decision=f"完成 {phases[phase_id]['tasks'][task_id]['label']}",
            context=f"任务状态从 {old_status} → done",
        )
    return phases[phase_id]


def get_progress() -> dict:
    """获取完整进度报告。"""
    phases = _load_json(PHASES_PATH)
    active = [p for p in phases.values() if p.get("status") == "active"]
    completed = [p for p in phases.values() if p.get("status") == "completed"]

    total_tasks = sum(len(p.get("tasks", {})) for p in phases.values())
    done_tasks = sum(sum(1 for t in p.get("tasks", {}).values() if t.get("status") == "done") for p in phases.values())

    return {
        "phases_total": len(phases),
        "phases_active": len(active),
        "phases_completed": len(completed),
        "tasks_total": total_tasks,
        "tasks_done": done_tasks,
        "current_phase": active[-1]["label"] if active else "无活跃阶段",
        "phases": phases,
    }


# ── Decision 决策日志 ───────────────────────────────


def record_decision(phase: str, task: str, decision: str, context: str = "", impact: str = "", alternatives: str = ""):
    """记录一个关键决策及其上下文。"""
    entry = {
        "phase": phase,
        "task": task,
        "decision": decision,
        "context": context,
        "impact": impact,
        "alternatives": alternatives,
    }
    _append_jsonl(DECISIONS_PATH, entry)
    return entry


def get_decisions(phase: str = "", limit: int = 20) -> list:
    """获取决策记录，可按阶段筛选。"""
    results = []
    if os.path.exists(DECISIONS_PATH):
        with open(DECISIONS_PATH) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if not phase or entry.get("phase") == phase:
                        results.append(entry)
                except json.JSONDecodeError:
                    continue
    return results[-limit:]


# ── Context 上下文快照 ──────────────────────────────


def save_context(
    overview: str, current_task: str = "", next_steps: list = None, active_files: list = None, notes: str = ""
):
    """
    Save当前工作上下文——对话结束时调用。
    下次对话开始时自动恢复。
    """
    context = {
        "overview": overview,
        "current_task": current_task,
        "next_steps": next_steps or [],
        "active_files": active_files or [],
        "notes": notes,
        "saved_at": datetime.now().isoformat(),
    }
    _save_json(CONTEXT_PATH, context)
    return context


def load_context() -> dict:
    """加载上次Save的上下文。"""
    context = _load_json(CONTEXT_PATH, {})
    if context:
        age = time.time() - datetime.fromisoformat(context.get("saved_at", "2000-01-01T00:00:00")).timestamp()
        context["age_hours"] = round(age / 3600, 1)
        context["fresh"] = age < 86400  # 24小时内
    return context


# ── Status.md 生成 ─────────────────────────────────


def _refresh_status_md():
    """生成人类可读的进度文件。"""
    progress = get_progress()

    lines = [
        "# GBase 项目进度",
        f"> 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"**总进度**: {progress['tasks_done']}/{progress['tasks_total']} 任务完成",
        f"**当前阶段**: {progress['current_phase']}",
        f"**活跃阶段**: {progress['phases_active']} | **已完成**: {progress['phases_completed']}",
        "",
        "---",
        "",
    ]

    for pid, p in progress.get("phases", {}).items():
        status_icon = {"active": "🔄", "completed": "✅", "paused": "⏸️"}.get(p.get("status"), "❓")
        lines.append(f"## {status_icon} {p.get('label', pid)}")
        lines.append(f"> {p.get('description', '')}")
        lines.append(f"> 状态: {p.get('status', 'unknown')}")
        lines.append("")

        tasks = p.get("tasks", {})
        if tasks:
            lines.append("| 任务 | 状态 |")
            lines.append("|------|------|")
            for tid, t in tasks.items():
                icon = {"todo": "⬜", "doing": "🔧", "done": "✅"}.get(t.get("status"), "❓")
                lines.append(f"| {icon} {t.get('label', tid)} | {t.get('status', '')} |")
            lines.append("")
        else:
            lines.append("*暂无任务*")
            lines.append("")

        lines.append("---")
        lines.append("")

    _ensure_dirs()
    with open(STATUS_PATH, "w") as f:
        f.write("\n".join(lines))


# ── 公开 API ────────────────────────────────────────


def init_project_memory():
    """
    初始化项目记忆——首次运行时调用。
    会创建默认的阶段结构。
    """
    _ensure_dirs()

    # 波段一：核心基础设施
    set_phase("band-1", "波段一：核心基础设施", "Backup系统 + 进化引擎 + AI自愈管道", "completed")
    add_task("band-1", "backup-system", "写前自动Backup系统", "lib/backup.py", "done")
    add_task("band-1", "evolution-engine", "自动进化触发链路", "lib/evolution_engine.py", "done")
    add_task("band-1", "writefile-integration", "write_file 集成Backup", "tools/write_file.py", "done")
    add_task("band-1", "rollback-tool", "Rollback CLI 工具", "tools/rollback.py", "done")
    add_task("band-1", "self-heal", "AI自愈管道 (lifeline)", "lib/lifeline.py", "done")

    # 波段二：长项目记忆 + 进化闭环
    set_phase("band-2", "波段二：长项目记忆 + 进化闭环", "项目记忆引擎 + 进化引擎集成 + 记忆-进化联动", "active")
    add_task("band-2", "project-memory", "项目记忆引擎", "lib/project_memory.py", "doing")
    add_task("band-2", "evo-integration", "进化引擎集成到 write_file", "集成 full_evolution_cycle", "todo")
    add_task("band-2", "memory-evo-link", "记忆-进化联动", "决策记录 ↔ 进化评估", "todo")
    add_task("band-2", "auto-context-recovery", "对话开始时自动恢复上下文", "加载上次 context.json", "todo")
    add_task("band-2", "progress-card", "进度可视化卡片", "从 phases.json 生成飞书卡片", "todo")

    # Save初始上下文
    save_context(
        overview="波段二启动：正在创建项目记忆引擎。波段一已完成（Backup+进化引擎+自愈管道）。",
        current_task="创建项目记忆引擎 (lib/project_memory.py)",
        next_steps=[
            "验证 project_memory.py 语法和函数",
            "进化引擎集成到 write_file",
            "记忆-进化联动闭环",
            "自动上下文恢复",
            "进度可视化卡片",
        ],
        active_files=["lib/project_memory.py", "lib/evolution_engine.py", "lib/backup.py", "tools/write_file.py"],
        notes="用户特别强调「长项目记忆」——这是 GBase 最大的跨对话痛点。",
    )

    record_decision(
        phase="band-2",
        task="project-memory",
        decision="采用 Phase/Task/Decision/Context 四层记忆模型",
        context="GBase 每次新对话都需要重新理解项目上下文，缺乏跨对话记忆。"
        "四层模型：Phase 记宏观进度 → Task 记具体任务 → Decision 记关键决策 → Context 记当前上下文。",
        impact="跨对话无需重新理解项目，对话开始自动恢复上下文。与进化引擎联动，形成「记忆→评估→进化→记忆」闭环。",
        alternatives="方案A：仅用 markdown 文件记录（太松散，无法结构化查询）；"
        "方案B：仅用鉴面记忆（鉴面记原则/教训，不记项目进度）。"
        "选择方案C：专用项目记忆引擎，JSON结构化，与鉴面互补。",
    )

    return get_progress()


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "init":
        result = init_project_memory()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif cmd == "status":
        result = get_progress()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif cmd == "context":
        ctx = load_context()
        print(json.dumps(ctx, indent=2, ensure_ascii=False))
    else:
        print(f"未知命令: {cmd}")
        print("可用: init, status, context")
