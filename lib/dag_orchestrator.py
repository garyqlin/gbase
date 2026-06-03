#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
╔═══════════════════════════════════════════════════════════╗
║  DAG Orchestrator — GraphBit × YF-subagent-orchestrator  ║
║                                                           ║
║  定位: 替代 LLM 路由，用确定性 DAG 引擎驱动已知工作流。      ║
║        未知任务回退到 LLM（YF-subagent-orchestrator）。      ║
║                                                           ║
║  三层防护:                                                  ║
║    1. DAG first — 已知工作流走确定性引擎，零幻影路由        ║
║    2. LLM fallback — 未知任务走 LLM 编排，但结果可提炼为    ║
║       新 DAG 工作流（下次就走确定性了）                      ║
║    3. Safety hooks — 每个步骤执行前都会跑安全检查钩子        ║
║                                                           ║
║  用法:                                                      ║
║    from lib.dag_orchestrator import DAGOrchestrator       ║
║    orch = DAGOrchestrator()                               ║
║    result = orch.run(                                     ║
║        task="执行每日巡检并生成报告",                        ║
║        context={"date": "2026-05-17"}                     ║
║    )                                                      ║
║                                                           ║
║  内置 Pilot 工作流:                                          ║
║    - daily-patrol:   健康检查 → 审计 → 生成报告             ║
║    - mail-digest:    收件箱 → 分类 → 摘要 → 归档            ║
║    - quality-check:  白盒 → 黑盒 → 蜂群 → 汇总              ║
║    - weekly-review:  统计收集 → 趋势分析 → 报告生成          ║
║    - mirror-cycle:   衰减 → 审查 → 概念簇 → 意图            ║
╚═══════════════════════════════════════════════════════════╝
"""

import contextlib
import json
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# === 配置 ===
LOG_DIR = Path("/home/gbase-v2/logs/orchestrator")
DAG_WORKFLOW_STORE = LOG_DIR / "dag-workflow-history.jsonl"
MAX_HISTORY = 50

# 任务关键词 → DAG 工作流映射
TASK_TO_WORKFLOW = {
    "巡检": "daily-patrol",
    "健康检查": "daily-patrol",
    "patrol": "daily-patrol",
    "邮件摘要": "mail-digest",
    "收件箱": "mail-digest",
    "mail": "mail-digest",
    "质检": "quality-check",
    "质量检查": "quality-check",
    "qa": "quality-check",
    "测试": "quality-check",
    "周报": "weekly-review",
    "每周总结": "weekly-review",
    "weekly": "weekly-review",
    "回顾": "weekly-review",
    "鉴面": "mirror-cycle",
    "记忆维护": "mirror-cycle",
    "mirror": "mirror-cycle",
}


class DAGOrchestrator:
    """DAG 优先编排器。

    流程:
      1. 解析任务描述 → 匹配已知 DAG 工作流
      2. 命中 → DAG 引擎确定性执行
      3. 未命中 → LLM 编排（结果记录，后续可提炼为 DAG）
    """

    def __init__(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        self._dag_engine = None
        self._agents: dict[str, Callable] = {}
        self._safety_hooks: dict[str, Callable] = []
        self._last_result: dict | None = None
        self._history: list[dict] = []
        self._task_keywords: dict[str, str] = dict(TASK_TO_WORKFLOW)

    def register_agent(self, name: str, func: Callable):
        """注册一个 Agent 类型函数。

        这些函数对应 DAG 步骤中的 agent_type。
        例如：register_agent("health_check", check_health)
        """
        self._agents[name] = func
        if self._dag_engine:
            self._dag_engine.register_agent(name, func)

    def register_safety_hook(self, name: str, func: Callable):
        """注册一个安全检查钩子。

        钩子在每个 DAG 步骤执行前调用，返回 (pass: bool, reason: str)。
        """
        self._safety_hooks[name] = func
        if self._dag_engine:
            self._dag_engine.register_safety_hook(name, func)

    def add_task_keyword(self, keyword: str, workflow_name: str):
        """注册任务关键词 → 工作流映射。"""
        self._task_keywords[keyword.lower()] = workflow_name

    def _init_engine(self):
        """懒初始化 DAG 引擎。"""
        if self._dag_engine is None:
            from lib.dag_engine import DAGEngine

            self._dag_engine = DAGEngine()

            # 注册已注册的 agent 和 hook
            for name, func in self._agents.items():
                self._dag_engine.register_agent(name, func)
            for name, func in self._safety_hooks.items():
                self._dag_engine.register_safety_hook(name, func)

            # 初始化 Pilot 工作流
            self._init_pilot_workflows()

    def _init_pilot_workflows(self):
        """初始化内置 Pilot 工作流定义。

        注意：这些是骨架定义。具体的 agent_type 函数需要
        由调用方 register_agent() 注册后才能执行。
        """
        # daily-patrol: 每日巡检
        patrol = {
            "name": "daily-patrol",
            "version": "1.0",
            "description": "每日系统巡检：健康检查 → 架构审计 → 报告生成",
            "max_depth": 20,
            "steps": [
                {"id": "health", "agent_type": "health_check", "inputs": {}, "output_key": "health_result"},
                {
                    "id": "audit",
                    "agent_type": "arch_audit",
                    "inputs": {"last_results": "$health_result"},
                    "output_key": "audit_result",
                },
                {
                    "id": "report",
                    "agent_type": "generate_report",
                    "inputs": {"health": "$health_result", "audit": "$audit_result"},
                    "output_key": "final_report",
                },
            ],
            "edges": [
                {"from": "health", "to": "audit"},
                {"from": "audit", "to": "report"},
            ],
        }
        with contextlib.suppress(Exception):
            self._dag_engine.load_raw(patrol)

        # mail-digest: 邮件摘要
        mail = {
            "name": "mail-digest",
            "version": "1.0",
            "description": "收件箱 → 分类 → 摘要 → 归档",
            "max_depth": 15,
            "steps": [
                {"id": "inbox", "agent_type": "check_inbox", "inputs": {}, "output_key": "mails"},
                {
                    "id": "classify",
                    "agent_type": "classify_mails",
                    "inputs": {"mails": "$mails"},
                    "output_key": "classified",
                },
                {
                    "id": "summarize",
                    "agent_type": "summarize_mails",
                    "inputs": {"classified": "$classified"},
                    "output_key": "digest",
                },
            ],
            "edges": [
                {"from": "inbox", "to": "classify"},
                {"from": "classify", "to": "summarize"},
            ],
        }
        with contextlib.suppress(Exception):
            self._dag_engine.load_raw(mail)

        # quality-check: 质检
        quality = {
            "name": "quality-check",
            "version": "1.0",
            "description": "白盒 → 黑盒 → 蜂群 → 汇总",
            "max_depth": 20,
            "steps": [
                {"id": "whitebox", "agent_type": "whitebox_check", "inputs": {}, "output_key": "whitebox_result"},
                {"id": "blackbox", "agent_type": "blackbox_check", "inputs": {}, "output_key": "blackbox_result"},
                {
                    "id": "swarm",
                    "agent_type": "swarm_test",
                    "inputs": {"whitebox": "$whitebox_result", "blackbox": "$blackbox_result"},
                    "output_key": "swarm_result",
                },
                {
                    "id": "summary",
                    "agent_type": "qa_summary",
                    "inputs": {
                        "whitebox": "$whitebox_result",
                        "blackbox": "$blackbox_result",
                        "swarm": "$swarm_result",
                    },
                    "output_key": "final_report",
                },
            ],
            "edges": [
                {"from": "whitebox", "to": "swarm"},
                {"from": "blackbox", "to": "swarm"},
                {"from": "swarm", "to": "summary"},
            ],
        }
        with contextlib.suppress(Exception):
            self._dag_engine.load_raw(quality)

        # mirror-cycle: 鉴面维护
        mirror_wf = {
            "name": "mirror-cycle",
            "version": "1.0",
            "description": "衰减 → 审查 → 概念簇刷新 → 意图浮现",
            "max_depth": 15,
            "steps": [
                {"id": "decay", "agent_type": "mirror_decay", "inputs": {}, "output_key": "decay_result"},
                {
                    "id": "review",
                    "agent_type": "mirror_review",
                    "inputs": {"decay": "$decay_result"},
                    "output_key": "review_result",
                },
                {
                    "id": "cognifold",
                    "agent_type": "cognifold_refresh",
                    "inputs": {"review": "$review_result"},
                    "output_key": "intents",
                },
            ],
            "edges": [
                {"from": "decay", "to": "review"},
                {"from": "review", "to": "cognifold"},
            ],
        }
        with contextlib.suppress(Exception):
            self._dag_engine.load_raw(mirror_wf)

    def _match_workflow(self, task: str) -> str | None:
        """根据任务描述匹配已知 DAG 工作流。

        先用已注册的关键词匹配，再尝试加载已持久化的自定义工作流。
        """
        task_lower = task.lower()

        # 1. 关键词匹配
        for keyword, wf_name in self._task_keywords.items():
            if keyword in task_lower:
                return wf_name

        # 2. 拼音/英文变体匹配
        pinyin_map = {
            "xunjian": "daily-patrol",
            "jiancha": "daily-patrol",
            "youjian": "mail-digest",
            "shoujian": "mail-digest",
            "zhijian": "quality-check",
            "zhoubao": "weekly-review",
            "jianmian": "mirror-cycle",
        }
        for keyword, wf_name in pinyin_map.items():
            if keyword in task_lower:
                return wf_name

        return None

    def run(self, task: str, context: dict[str, Any] = None, fallback_to_llm: bool = True) -> dict[str, Any]:
        """执行任务。

        Args:
            task: 任务描述，如 "执行每日巡检"
            context: 上下文变量（日期、参数等）
            fallback_to_llm: 未命中 DAG 时是否回退到 LLM 编排

        Returns:
            执行结果，含 _dag_executed / _llm_fallback 标记
        """
        self._init_engine()
        context = context or {}

        workflow_name = self._match_workflow(task)
        start_time = time.time()

        # ── DAG 路径 ──
        if workflow_name:
            wf = self._dag_engine.load(workflow_name)
            if wf:
                is_valid, errors = self._dag_engine.validate(wf)
                if is_valid:
                    result = self._dag_engine.execute(wf, context=context)
                    result["_dag_executed"] = True
                    result["_workflow"] = workflow_name
                    result["_duration_ms"] = round((time.time() - start_time) * 1000, 1)
                    self._last_result = result
                    self._log_execution(task, workflow_name, "dag", result)
                    return result
                else:
                    # DAG 定义有误，记录后尝试 LLM
                    self._log_execution(task, workflow_name, "dag_error", {"errors": errors})

        # ── LLM 回退 ──
        if fallback_to_llm:
            result = {
                "_dag_executed": False,
                "_llm_fallback": True,
                "_task": task,
                "_message": (
                    "未匹配 DAG 工作流，建议使用 "
                    "YF-subagent-orchestrator 进行 LLM 编排。"
                    "执行完成后可调用 learn_workflow() "
                    "将结果提炼为 DAG。"
                ),
                "_duration_ms": round((time.time() - start_time) * 1000, 1),
            }
            self._last_result = result
            self._log_execution(task, "llm_fallback", "pending", result)
            return result

        # ── 无回退 ──
        return {
            "_dag_executed": False,
            "_error": "no_matching_workflow",
            "_task": task,
        }

    def learn_workflow(self, task_type: str, steps: list[dict], edges: list[dict], description: str = "") -> str:
        """从 LLM 编排结果中学习新的 DAG 工作流。

        下次同类任务就可以走确定性 DAG 了。

        Args:
            task_type: 任务类型名称（用作 workflow name）
            steps: 步骤列表
            edges: 边列表
            description: 工作流描述

        Returns:
            工作流名称
        """
        self._init_engine()

        import re as _re

        safe_name = _re.sub(r"[^a-zA-Z0-9_-]", "-", task_type.lower())
        safe_name = safe_name.strip("-") or "custom-workflow"

        # 避免重名
        counter = 1
        original = safe_name
        while self._dag_engine.load(safe_name) is not None:
            safe_name = f"{original}-{counter}"
            counter += 1

        wf_data = {
            "name": safe_name,
            "version": "1.0",
            "description": description or f"Learned from: {task_type}",
            "max_depth": 30,
            "steps": steps,
            "edges": edges,
        }

        wf = self._dag_engine.load_raw(wf_data)
        self._dag_engine.save(wf)

        # 注册关键词
        self.add_task_keyword(task_type, safe_name)

        self._log_execution(task_type, safe_name, "learned", {"steps": len(steps), "edges": len(edges)})

        return safe_name

    def _log_execution(self, task: str, workflow: str, mode: str, result: dict):
        """记录执行历史。"""
        record = {
            "ts": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "task": task,
            "workflow": workflow,
            "mode": mode,
            "success": result.get("status") != "failed",
            "duration_ms": result.get("_duration_ms", 0),
            "summary": json.dumps(
                {
                    k: str(v)[:60]
                    for k, v in result.items()
                    if not k.startswith("_") and k != "steps" and k != "history"
                },
                ensure_ascii=False,
            ),
        }

        with open(DAG_WORKFLOW_STORE, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        self._history.append(record)
        if len(self._history) > MAX_HISTORY:
            self._history = self._history[-MAX_HISTORY:]

    def list_workflows(self) -> list[dict]:
        """列出所有已注册的 DAG 工作流。"""
        self._init_engine()
        pilot_names = ["daily-patrol", "mail-digest", "quality-check", "mirror-cycle"]

        workflows = []
        for name in pilot_names:
            wf = self._dag_engine.load(name)
            if wf:
                workflows.append(
                    {
                        "name": wf.name,
                        "description": wf.description,
                        "steps": len(wf.steps),
                        "edges": len(wf.edges),
                        "is_pilot": True,
                    }
                )

        # 也检查自定义工作流
        dag_dir = Path("/home/gbase-v2/data/dag-workflows")
        if dag_dir.exists():
            for f in dag_dir.glob("*.json"):
                if f.stem not in pilot_names:
                    workflows.append(
                        {
                            "name": f.stem,
                            "description": "自定义工作流",
                            "is_pilot": False,
                        }
                    )

        return workflows

    def get_history(self, limit: int = 10) -> list[dict]:
        """获取执行历史。"""
        return self._history[-limit:]

    def stats(self) -> dict[str, Any]:
        """统计信息。"""
        dag_executed = sum(1 for h in self._history if h["mode"] == "dag")
        llm_fallback = sum(1 for h in self._history if h["mode"] == "llm_fallback")
        total = len(self._history)

        return {
            "total_executions": total,
            "dag_executed": dag_executed,
            "llm_fallback": llm_fallback,
            "dag_ratio": round(dag_executed / total * 100, 1) if total > 0 else 0,
            "workflows": len(self.list_workflows()),
        }
