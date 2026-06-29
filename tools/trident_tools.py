#!/usr/bin/env python3
"""
Agent Trident 三叉戟工具集
────────────────────────────
让 Agent 可以：
1. 用 Trident CC 写代码（执行实现任务）
2. 用 Trident X 审查/补刀（代码审计 + ApplyPatch）
3. 通过 Trident Glink 编排项目工作流
4. 探查 CC/X 的健康状态

用法：Agent直接调以下 @tool 函数。
底层走 HTTP 直连 Trident CC（8443）/ X（8444）/ Glink（8427），
不经过 Lancer 那套 shared/ 底座，完全独立。
"""

import logging

import httpx

from lib.toolkit import tool

logger = logging.getLogger("trident")

# ── Trident 三叉戟端口 ──
TRIDENT_GLINK = "http://127.0.0.1:8427"
TRIDENT_CC = "http://127.0.0.1:8443"
TRIDENT_X = "http://127.0.0.1:8444"
TIMEOUT = 600  # CC/X 任务可能很长

# ── 工具函数 ──────────────────────────────────────────────


async def _ask(agent_url: str, task: str) -> dict:
    """通用 /ask 调用"""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{agent_url}/ask",
                json={"message": task, "session": True},
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        return {"error": f"调用 {agent_url} 超时（{TIMEOUT}s），任务可能仍在执行"}
    except Exception as e:
        return {"error": str(e)}


# ── 使用经验：如何用好 CC 和 X ──
#
# Lesson 1: Don't use CC for search. CC is the code arm — give it coding tasks.
#        搜索信息直接用 anysearch_search，CC 只用来看文件和改代码。
#
# 经验 2：任务描述要清晰。比如：
#        ❌ "看看这个文件" → 不如你自己 read_file
#        ✅ "在 /path/to/project 下实现用户登录功能，增加 auth 中间件，修改配置文件"
#        ✅ "读取 /path/to/project/src/main.py，分析其中的安全漏洞"
#
# 经验 3：第一次 CC 做探索（explore mode），不要期望它一次完成。
#        如果结果不完整，发第二次任务给它继续改。
#
# 经验 4：CC 做完后，让 X 做审计复查。
#        X 会检查代码质量、安全、遗漏功能。
#
# 经验 5：项目级任务用 Trident Glink 创建工作流。
#        小任务（单文件修改）直接调 CC。
#
# 经验 6：CC/X 调用是异步的 - 它们有自己的 session 记忆。
#        你告诉 CC "改 A 和 B"，会记住上下文继续。
#
# 经验 7：如果 CC 返回的内容看起来不完整（中途截断），
#        再发一条消息给 CC 让它继续完成。
#
# 经验 8：对于长任务（>50 次工具调用），
#        拆成子步骤分多次发给 CC，每次专注一个子任务。
#
# 经验 9：X 的审计报告如果太简略（比如只有几行），
#        可以要求 X "进行深层安全审计，检查认证绕过、注入、XSS"
#
# 经验 10：CC 和 X 不要在同一个 session 里混用。
#        一个 session 给 CC 做实现，另一个给 X 做审计。
#


@tool()
async def trident_help() -> dict:
    """返回 Trident CC/X 的使用指南（备忘录）"""
    return {
        "cc": {
            "port": 8443,
            "tool_count": 33,
            "role": "实现臂 — 代码生成、修改、探索、项目搭建",
            "best_for": [
                "实现新功能",
                "修改现有代码",
                "项目搭建（从零创建文件目录）",
                "代码探索（读文件、glob搜索）",
            ],
            "not_for": [
                "搜索网络信息（用 anysearch）",
                "发消息/卡片（这是你的工作）",
                "系统管理（关进程、查配置）",
            ],
            "session": "自动持久化，同一上下文多次调用会延续",
            "workflow": "explore（探索）→ edit（修改）→ verify（验证）",
        },
        "x": {
            "port": 8444,
            "tool_count": 20,
            "role": "审查臂 — 代码审计、补刀、精确编辑",
            "best_for": [
                "代码审计（安全、质量、风格）",
                "ApplyPatch 精确修改（不写大段代码）",
                "ExecPolicy 门禁检查",
                "Completion 验证",
            ],
            "not_for": [
                "大段代码生成（那是 CC 的工作）",
                "非代码任务",
            ],
            "session": "自动持久化",
        },
        "glink": {
            "port": 8427,
            "role": "项目总线 — 编排多步骤工作流",
            "best_for": [
                "跨多步的项目编排",
                "任务状态跟踪",
                "事件记录",
            ],
        },
    }


@tool()
async def cc_execute(task: str, project_dir: str | None = None) -> dict:
    """调用 Trident CC 执行代码任务（实现臂）

    Args:
        task: 任务描述（要清晰具体，告诉 CC 做什么）
        project_dir: 项目目录（告诉 CC 在哪工作，可选）

    Returns:
        dict: {"response": "...", "tool_calls": N, "tokens": N}
    """
    full_task = f"项目路径: {project_dir}\n\n任务: {task}" if project_dir else task
    return await _ask(TRIDENT_CC, full_task)


@tool()
async def cc_explore(task: str) -> dict:
    """调用 Trident CC 在探索模式（只读）下分析代码

    Args:
        task: 探索任务（读什么文件、分析什么结构）

    Returns:
        dict: 探索结果与分析
    """
    full_task = f"[explore mode] 请你阅读和分析代码，不要修改任何文件。\n\n{task}"
    return await _ask(TRIDENT_CC, full_task)


@tool()
async def x_audit(task: str) -> dict:
    """调用 Trident X 进行代码审计（审查臂）

    Args:
        task: 审计任务（审计什么项目、关注什么方向）

    Returns:
        dict: 审计报告
    """
    return await _ask(TRIDENT_X, task)


@tool()
async def x_apply_patch(task: str) -> dict:
    """调用 Trident X 执行精确编辑（ApplyPatch 模式）

    Args:
        task: 精确编辑任务（SEARCH/REPLACE 块）

    Returns:
        dict: 执行结果
    """
    return await _ask(TRIDENT_X, task)


@tool()
async def glink_status() -> dict:
    """查询 Trident Glink 状态 — 所有 agent 在线情况"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{TRIDENT_GLINK}/health")
            resp.raise_for_status()
            agents_resp = await client.get(f"{TRIDENT_GLINK}/status/agents")
            agents = agents_resp.json() if agents_resp.status_code == 200 else {"agents": []}
            return {
                "glink": resp.json(),
                "agents": agents.get("agents", []),
            }
    except Exception as e:
        return {"error": str(e)}


@tool()
async def health_check_cc() -> dict:
    """检查 Trident CC 健康状态"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{TRIDENT_CC}/health")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"online": False, "error": str(e)}


@tool()
async def health_check_x() -> dict:
    """检查 Trident X 健康状态"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{TRIDENT_X}/health")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"online": False, "error": str(e)}


@tool()
async def glink_workflow(project: str, steps: list) -> dict:
    """通过 Trident Glink 编排一个多步骤工作流

    Args:
        project: 项目名称
        steps: 步骤列表，每步格式：
               {"id": "step-1", "executor": "Trident-CC", "title": "任务标题", "task": "详细任务描述"}

    各步骤的 executor 可以是：
    - "Trident-CC" — 代码实现
    - "Trident-X"  — 代码审计
    - "your-agent"     — 你自己（用你自己的工具处理）

    Returns:
        dict: 工作流状态与各步骤结果
    """
    results = []
    for step in steps:
        executor = step.get("executor", "Trident-CC")
        title = step.get("title", "")
        task = step.get("task", "")

        if executor == "Trident-CC":
            result = await cc_execute(task, step.get("project_dir"))
        elif executor == "Trident-X":
            result = await x_audit(task)
        elif executor == "your-agent":
            result = {"note": f"步骤 '{title}' 分配给自己执行，需要自行处理"}
        else:
            result = {"error": f"未知执行者: {executor}"}

        results.append(
            {
                "step_id": step.get("id"),
                "executor": executor,
                "title": title,
                "status": "ok" if "error" not in result else "fail",
                "result": result,
            }
        )

    return {
        "project": project,
        "steps_completed": len(results),
        "results": results,
    }
