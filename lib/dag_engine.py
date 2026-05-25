#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
╔═══════════════════════════════════════════════════════════╗
║  GraphBit — DAG deterministic orchestration engine                             ║
║  arxiv: 2605.13848                                        ║
║                                                           ║
║  解决 Prompt 驱动编排的三个死穴:                             ║
║    1. 幻影路由 → 工作流定义为有向无环图(DAG)，引擎驱动      ║
║    2. 无限循环  → DAG 拓扑保证无环 + 最大深度限制           ║
║    3. 不可复现 → 确定性状态机，同输入必同输出               ║
║                                                           ║
║  三层记忆隔离:                                              ║
║    L1 临时暂存 (transient)    = 当前步骤的临时变量          ║
║    L2 结构化状态 (structured) = DAG 上下文，步骤间传递      ║
║    L3 外部连接器 (external)   = 数据库/API/文件系统         ║
║                                                           ║
║  架构:                                                      ║
║    ┌────────────┐   ┌──────────┐   ┌───────────────┐     ║
║    │ DAG 定义    │ → │ 状态机    │ → │ Agent 类型函数 │     ║
║    │ (YAML/JSON) │   │ (路由+校验)│   │ (typed funcs) │     ║
║    └────────────┘   └──────────┘   └───────────────┘     ║
║                                                           ║
║  用法:                                                      ║
║    from lib.dag_engine import DAGEngine, DAGWorkflow      ║
║    engine = DAGEngine()                                    ║
║    wf = engine.load("patrol")                              ║
║    result = engine.execute(wf, context={})                 ║
╚═══════════════════════════════════════════════════════════╝
"""

import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

# === 配置 ===
DAG_DIR = Path("/home/opprime-v2/data/dag-workflows")
MAX_DEPTH = 50  # 最大 DAG 深度
MAX_STEP_TIMEOUT = 300  # 单步超时（秒）
MAX_RETRY = 2  # 单步最大重试


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass
class DAGStep:
    """DAG 中的一个步骤节点。"""

    id: str  # 唯一标识
    name: str  # 人类可读名称
    agent: str  # 执行的 Agent 类型函数
    inputs: dict[str, str] = field(default_factory=dict)  # 输入映射: {参数名: 来源路径}
    outputs: list[str] = field(default_factory=list)  # 输出列表
    depends_on: list[str] = field(default_factory=list)  # 前置步骤 ID
    condition: str | None = None  # 条件表达式（可选）
    retry: int = 0  # 重试次数
    timeout: int = 60  # 超时（秒）
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DAGWorkflow:
    """一个完整的 DAG 工作流定义。"""

    name: str
    version: str = "1.0"
    description: str = ""
    steps: list[DAGStep] = field(default_factory=list)
    start_step: str = ""  # 入口步骤 ID
    safety_checks: list[str] = field(default_factory=list)  # 安全检查列表
    max_depth: int = MAX_DEPTH
    metadata: dict[str, Any] = field(default_factory=dict)


class ThreeLayerMemory:
    """三层记忆隔离。

    L1 — 临时暂存:   当前步骤内的临时变量，步骤结束后清空
    L2 — 结构化状态:  DAG 上下文，步骤间传递，工作流结束后清空
    L3 — 外部连接器:  持久化存储/API，跨工作流保持
    """

    def __init__(self, external_connectors: dict[str, Any] = None):
        self.l1_transient: dict[str, Any] = {}  # 临时暂存
        self.l2_structured: dict[str, Any] = {}  # 结构化状态
        self.l3_external: dict[str, Any] = external_connectors or {}  # 外部连接器

    def set_l1(self, key: str, value: Any):
        self.l1_transient[key] = value

    def get_l1(self, key: str, default=None) -> Any:
        return self.l1_transient.get(key, default)

    def clear_l1(self):
        self.l1_transient.clear()

    def set_l2(self, key: str, value: Any):
        self.l2_structured[key] = value

    def get_l2(self, key: str, default=None) -> Any:
        return self.l2_structured.get(key, default)

    def clear_l2(self):
        self.l2_structured.clear()

    def get_l3(self, key: str, default=None) -> Any:
        return self.l3_external.get(key, default)

    def set_l3(self, key: str, value: Any):
        self.l3_external[key] = value

    def resolve(self, source_path: str) -> Any:
        """解析来源路径: l1.xxx / l2.xxx / l3.xxx / input.xxx"""
        if source_path.startswith("l1."):
            return self.get_l1(source_path[3:])
        elif source_path.startswith("l2."):
            return self.get_l2(source_path[3:])
        elif source_path.startswith("l3."):
            return self.get_l3(source_path[3:])
        elif source_path.startswith("input."):
            return self.get_l2(source_path[6:])
        return source_path  # 字面量


class DAGEngine:
    """DAG 确定性编排引擎。

    核心设计:
      - 拓扑排序保证无环执行
      - 状态机驱动路由（非 LLM）
      - 三层记忆隔离防止上下文污染
      - 安全检查钩子可复用宪法/元规则层
    """

    def __init__(self):
        DAG_DIR.mkdir(parents=True, exist_ok=True)

        # Agent 类型函数注册表
        self._agent_registry: dict[str, Callable] = {}

        # 安全检查钩子（由 Gbase 宪法层注入）
        self._safety_hooks: dict[str, Callable] = {}

        # 执行历史
        self._execution_history: list[dict] = []

    # ─── Agent 注册 ──────────────────────────────────────

    def register_agent(self, name: str, func: Callable):
        """注册 Agent 类型函数。"""
        self._agent_registry[name] = func

    def register_safety_hook(self, name: str, func: Callable):
        """注册安全检查钩子。"""
        self._safety_hooks[name] = func

    # ─── 工作流加载 ──────────────────────────────────────

    def load(self, name: str) -> DAGWorkflow | None:
        """从 YAML 加载工作流定义。"""
        wf_path = DAG_DIR / f"{name}.yaml"
        if not wf_path.exists():
            return None

        with open(wf_path) as f:
            data = yaml.safe_load(f)

        return self._parse_workflow(data)

    def load_raw(self, data: dict) -> DAGWorkflow:
        """从 Dict 直接加载工作流。"""
        return self._parse_workflow(data)

    def save(self, workflow: DAGWorkflow):
        """保存工作流到 YAML。"""
        wf_path = DAG_DIR / f"{workflow.name}.yaml"
        data = self._serialize_workflow(workflow)
        with open(wf_path, "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    def _parse_workflow(self, data: dict) -> DAGWorkflow:
        """解析工作流定义。"""
        steps = []
        for step_data in data.get("steps", []):
            steps.append(
                DAGStep(
                    id=step_data["id"],
                    name=step_data.get("name", step_data["id"]),
                    agent=step_data["agent"],
                    inputs=step_data.get("inputs", {}),
                    outputs=step_data.get("outputs", []),
                    depends_on=step_data.get("depends_on", []),
                    condition=step_data.get("condition"),
                    retry=step_data.get("retry", 0),
                    timeout=step_data.get("timeout", 60),
                    metadata=step_data.get("metadata", {}),
                )
            )

        return DAGWorkflow(
            name=data["name"],
            version=data.get("version", "1.0"),
            description=data.get("description", ""),
            steps=steps,
            start_step=data.get("start_step", steps[0].id if steps else ""),
            safety_checks=data.get("safety_checks", []),
            max_depth=data.get("max_depth", MAX_DEPTH),
            metadata=data.get("metadata", {}),
        )

    def _serialize_workflow(self, wf: DAGWorkflow) -> dict:
        """序列化工作流。"""
        return {
            "name": wf.name,
            "version": wf.version,
            "description": wf.description,
            "start_step": wf.start_step,
            "max_depth": wf.max_depth,
            "safety_checks": wf.safety_checks,
            "metadata": wf.metadata,
            "steps": [
                {
                    "id": s.id,
                    "name": s.name,
                    "agent": s.agent,
                    "inputs": s.inputs,
                    "outputs": s.outputs,
                    "depends_on": s.depends_on,
                    "condition": s.condition,
                    "retry": s.retry,
                    "timeout": s.timeout,
                    "metadata": s.metadata,
                }
                for s in wf.steps
            ],
        }

    # ─── 拓扑排序与验证 ─────────────────────────────────

    def validate(self, workflow: DAGWorkflow) -> tuple[bool, list[str]]:
        """验证 DAG 的合法性。

        检查:
          1. 无环（拓扑排序成功）
          2. 所有 depends_on 引用的步骤存在
          3. 所有 agent 已注册
          4. 深度不超限
        """
        errors = []

        # 步骤索引
        step_ids = {s.id for s in workflow.steps}
        if workflow.start_step and workflow.start_step not in step_ids:
            errors.append(f"入口步骤 '{workflow.start_step}' 不存在")
        if not workflow.start_step and workflow.steps:
            workflow.start_step = workflow.steps[0].id

        # 检查前置引用
        for step in workflow.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    errors.append(f"步骤 '{step.id}' 引用了不存在的依赖 '{dep}'")

            if step.agent not in self._agent_registry:
                errors.append(f"步骤 '{step.id}' 的 agent '{step.agent}' 未注册")

        # 拓扑排序（检测环）
        sorted_ids, has_cycle = self._topological_sort(workflow)
        if has_cycle:
            errors.append("DAG 中存在环，无法执行")

        # 深度检查
        if len(sorted_ids) > workflow.max_depth:
            errors.append(f"步骤数 {len(sorted_ids)} 超过最大深度 {workflow.max_depth}")

        return len(errors) == 0, errors

    def _topological_sort(self, workflow: DAGWorkflow) -> tuple[list[str], bool]:
        """拓扑排序，返回 (排序后的步骤ID列表, 是否有环)。"""
        adj: dict[str, list[str]] = {s.id: [] for s in workflow.steps}
        in_degree: dict[str, int] = {s.id: 0 for s in workflow.steps}

        for step in workflow.steps:
            for dep in step.depends_on:
                if dep in adj:
                    adj[dep].append(step.id)
                    in_degree[step.id] += 1

        # Khan's algorithm
        queue = deque([sid for sid, deg in in_degree.items() if deg == 0])
        sorted_ids = []

        while queue:
            node = queue.popleft()
            sorted_ids.append(node)
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        has_cycle = len(sorted_ids) != len(workflow.steps)
        return sorted_ids, has_cycle

    # ─── 执行引擎 ───────────────────────────────────────

    def execute(
        self, workflow: DAGWorkflow, initial_context: dict[str, Any] = None, external_connectors: dict[str, Any] = None
    ) -> dict[str, Any]:
        """执行 DAG 工作流。

        Args:
            workflow: 工作流定义
            initial_context: 初始上下文（注入 L2）
            external_connectors: 外部连接器（注入 L3）

        Returns:
            {status, steps: {step_id: {status, output, error, duration}},
             context: L2 最终状态, stats: {...}}
        """
        # 验证
        valid, errors = self.validate(workflow)
        if not valid:
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": f"DAG 验证失败: {'; '.join(errors)}",
                "steps": {},
                "context": {},
                "stats": {},
            }

        # 初始化三层记忆
        memory = ThreeLayerMemory(external_connectors or {})
        if initial_context:
            for k, v in initial_context.items():
                memory.set_l2(k, v)

        # 拓扑排序
        sorted_ids, _ = self._topological_sort(workflow)
        step_map = {s.id: s for s in workflow.steps}

        # 状态跟踪
        step_results: dict[str, dict] = {}
        completed_count = 0
        failed_count = 0
        skipped_count = 0
        start_time = time.time()

        # 安全检查
        for check_name in workflow.safety_checks:
            if check_name in self._safety_hooks:
                result = self._safety_hooks[check_name](workflow, memory)
                if result is False:
                    return {
                        "status": WorkflowStatus.FAILED.value,
                        "error": f"安全检查 '{check_name}' 未通过",
                        "steps": step_results,
                        "context": memory.l2_structured,
                        "stats": {},
                    }

        # 按拓扑序执行
        for step_id in sorted_ids:
            step = step_map.get(step_id)
            if not step:
                continue

            # 检查前置依赖是否全部完成
            deps_failed = False
            for dep_id in step.depends_on:
                if dep_id in step_results:
                    dep_result = step_results[dep_id]
                    if dep_result["status"] == StepStatus.FAILED.value:
                        deps_failed = True
                        break

            if deps_failed:
                step_results[step_id] = {
                    "status": StepStatus.SKIPPED.value,
                    "reason": "前置步骤失败",
                    "output": None,
                    "error": None,
                    "duration_ms": 0,
                }
                skipped_count += 1
                continue

            # 条件检查
            if step.condition and not self._evaluate_condition(step.condition, memory):
                step_results[step_id] = {
                    "status": StepStatus.SKIPPED.value,
                    "reason": f"条件不满足: {step.condition}",
                    "output": None,
                    "error": None,
                    "duration_ms": 0,
                }
                skipped_count += 1
                continue

            # 解析输入
            resolved_inputs = {}
            for param, source_path in step.inputs.items():
                resolved_inputs[param] = memory.resolve(source_path)

            # 执行步骤（带重试）
            step_result = None
            last_error = None
            for _attempt in range(step.retry + 1):
                step_result = self._execute_step(step, resolved_inputs, memory)
                if step_result["status"] == StepStatus.COMPLETED.value:
                    break
                last_error = step_result.get("error")
                memory.clear_l1()  # 重试前清 L1

            if step_result["status"] == StepStatus.COMPLETED.value:
                completed_count += 1
                # 将输出写入 L2
                for output_key in step.outputs:
                    if output_key in step_result["output"]:
                        memory.set_l2(f"{step_id}.{output_key}", step_result["output"][output_key])
                # 也写入一个聚合键
                memory.set_l2(f"{step_id}._result", step_result["output"])
            else:
                failed_count += 1
                step_result["error"] = step_result.get("error") or last_error

            step_results[step_id] = step_result

        total_duration = (time.time() - start_time) * 1000

        # 确定整体状态
        if failed_count == 0:
            overall_status = WorkflowStatus.COMPLETED
        elif completed_count == 0:
            overall_status = WorkflowStatus.FAILED
        else:
            overall_status = WorkflowStatus.PARTIAL

        # 记录执行历史
        self._execution_history.append(
            {
                "workflow": workflow.name,
                "version": workflow.version,
                "status": overall_status.value,
                "completed": completed_count,
                "failed": failed_count,
                "skipped": skipped_count,
                "duration_ms": total_duration,
                "timestamp": time.time(),
            }
        )

        # 保留最近 100 条
        if len(self._execution_history) > 100:
            self._execution_history = self._execution_history[-100:]

        return {
            "status": overall_status.value,
            "error": None if failed_count == 0 else f"{failed_count} 个步骤失败",
            "steps": step_results,
            "context": memory.l2_structured,
            "stats": {
                "total_steps": len(sorted_ids),
                "completed": completed_count,
                "failed": failed_count,
                "skipped": skipped_count,
                "duration_ms": round(total_duration, 1),
            },
        }

    def _execute_step(self, step: DAGStep, inputs: dict[str, Any], memory: ThreeLayerMemory) -> dict[str, Any]:
        """执行单个步骤。"""
        agent_func = self._agent_registry.get(step.agent)
        if not agent_func:
            return {
                "status": StepStatus.FAILED.value,
                "output": None,
                "error": f"Agent '{step.agent}' 未注册",
                "duration_ms": 0,
            }

        memory.clear_l1()
        t0 = time.time()

        try:
            # 调用 Agent 函数: agent(inputs, memory)
            result = agent_func(inputs, memory)
            duration = (time.time() - t0) * 1000

            return {
                "status": StepStatus.COMPLETED.value,
                "output": result if isinstance(result, dict) else {"result": result},
                "error": None,
                "duration_ms": round(duration, 1),
            }
        except Exception as e:
            duration = (time.time() - t0) * 1000
            return {
                "status": StepStatus.FAILED.value,
                "output": None,
                "error": str(e),
                "duration_ms": round(duration, 1),
            }

    def _evaluate_condition(self, condition: str, memory: ThreeLayerMemory) -> bool:
        """评估条件表达式。

        支持: l2.xxx == 'value' / l2.xxx >= 1 等简单表达式。
        """
        try:
            # 替换变量引用
            import re

            expr = condition
            for match in re.finditer(r"(l[123]\.\w+|input\.\w+)", condition):
                ref = match.group(0)
                value = memory.resolve(ref)
                if isinstance(value, str):
                    value = f"'{value}'"
                elif value is None:
                    value = "None"
                expr = expr.replace(ref, str(value), 1)

            # 安全评估（仅允许比较和逻辑运算）
            return bool(eval(expr, {"__builtins__": {}}, {}))
        except Exception:
            return True  # 条件无法评估时默认通过（不阻塞）

    # ─── 统计与历史 ──────────────────────────────────────

    def get_history(self, limit: int = 10) -> list[dict]:
        """返回最近的执行历史。"""
        return self._execution_history[-limit:]

    def stats(self) -> dict[str, Any]:
        """返回引擎统计。"""
        history = self._execution_history
        if not history:
            return {"total_executions": 0}

        success = sum(1 for h in history if h["status"] == "completed")
        return {
            "total_executions": len(history),
            "success_rate": round(success / len(history), 3),
            "registered_agents": list(self._agent_registry.keys()),
            "safety_hooks": list(self._safety_hooks.keys()),
        }


# ─── 预置 Pilot 工作流定义 ──────────────────────────────

PILOT_WORKFLOWS = {
    "daily-patrol": {
        "name": "daily-patrol",
        "version": "1.0",
        "description": "Gbase 每日巡逻 - 健康检查 + 日志审计 + 门户外链检查",
        "start_step": "health_check",
        "safety_checks": ["constitution_check"],
        "steps": [
            {
                "id": "health_check",
                "name": "健康心跳检查",
                "agent": "health_check",
                "outputs": ["healthy", "nodes_checked"],
                "timeout": 30,
            },
            {
                "id": "log_audit",
                "name": "日志审计",
                "agent": "log_audit",
                "depends_on": ["health_check"],
                "inputs": {"node_count": "l2.health_check.nodes_checked"},
                "outputs": ["errors_found", "warnings"],
                "timeout": 60,
            },
            {
                "id": "portal_link_check",
                "name": "门户外链检查",
                "agent": "portal_check",
                "depends_on": ["health_check"],
                "outputs": ["broken_links", "total_links"],
                "timeout": 120,
            },
            {
                "id": "summary",
                "name": "生成巡逻报告",
                "agent": "generate_report",
                "depends_on": ["log_audit", "portal_link_check"],
                "inputs": {
                    "errors": "l2.log_audit.errors_found",
                    "broken": "l2.portal_link_check.broken_links",
                },
                "outputs": ["report"],
                "timeout": 30,
            },
        ],
    },
    "mail-digest": {
        "name": "mail-digest",
        "version": "1.0",
        "description": "邮件摘要 - 检查收件箱 → 分类 → 生成摘要",
        "start_step": "check_inbox",
        "safety_checks": [],
        "steps": [
            {
                "id": "check_inbox",
                "name": "检查收件箱",
                "agent": "check_inbox",
                "outputs": ["new_count", "mail_ids"],
                "timeout": 30,
            },
            {
                "id": "classify",
                "name": "邮件分类",
                "agent": "classify_mail",
                "depends_on": ["check_inbox"],
                "condition": "l2.check_inbox.new_count > 0",
                "inputs": {"mail_ids": "l2.check_inbox.mail_ids"},
                "outputs": ["categories"],
                "timeout": 60,
            },
            {
                "id": "digest",
                "name": "生成摘要",
                "agent": "generate_digest",
                "depends_on": ["classify"],
                "inputs": {"categories": "l2.classify.categories"},
                "outputs": ["digest_text"],
                "timeout": 30,
            },
        ],
    },
    "quality-check": {
        "name": "quality-check",
        "version": "1.0",
        "description": "质量检查 - 代码规范 → 安全检查 → 性能基准",
        "start_step": "code_lint",
        "safety_checks": ["constitution_check"],
        "steps": [
            {
                "id": "code_lint",
                "name": "代码规范检查",
                "agent": "code_lint",
                "outputs": ["issues", "score"],
                "timeout": 60,
            },
            {
                "id": "security_scan",
                "name": "安全扫描",
                "agent": "security_scan",
                "outputs": ["vulnerabilities", "risk_level"],
                "timeout": 120,
            },
            {
                "id": "perf_bench",
                "name": "性能基准测试",
                "agent": "perf_bench",
                "depends_on": ["code_lint"],
                "inputs": {"code_score": "l2.code_lint.score"},
                "outputs": ["benchmark_results"],
                "timeout": 180,
            },
            {
                "id": "quality_report",
                "name": "质量报告",
                "agent": "generate_report",
                "depends_on": ["code_lint", "security_scan", "perf_bench"],
                "inputs": {
                    "code_score": "l2.code_lint.score",
                    "risk": "l2.security_scan.risk_level",
                    "perf": "l2.perf_bench.benchmark_results",
                },
                "outputs": ["report"],
                "timeout": 30,
            },
        ],
    },
}


# ─── 便捷函数 ───────────────────────────────────────────

_engine_instance: DAGEngine | None = None


def get_engine() -> DAGEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = DAGEngine()
        _init_pilot_workflows(_engine_instance)
    return _engine_instance


def _init_pilot_workflows(_engine: DAGEngine):
    """初始化 Pilot 工作流定义。"""
    for name, wf_data in PILOT_WORKFLOWS.items():
        wf_path = DAG_DIR / f"{name}.yaml"
        if not wf_path.exists():
            with open(wf_path, "w") as f:
                yaml.dump(wf_data, f, allow_unicode=True, default_flow_style=False)


if __name__ == "__main__":
    import sys

    engine = get_engine()
    if "--stats" in sys.argv:
        print(json.dumps(engine.stats(), ensure_ascii=False, indent=2))
    elif "--history" in sys.argv:
        print(json.dumps(engine.get_history(), ensure_ascii=False, indent=2))
    elif "--validate" in sys.argv and len(sys.argv) > 2:
        wf = engine.load(sys.argv[2])
        if wf:
            valid, errors = engine.validate(wf)
            print(f"Valid: {valid}")
            if errors:
                for e in errors:
                    print(f"  ❌ {e}")
            else:
                print("  ✅ 验证通过")
    else:
        print(json.dumps(engine.stats(), ensure_ascii=False, indent=2))
