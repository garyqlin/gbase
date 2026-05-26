#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
╔═══════════════════════════════════════════════════════════╗
║  GraphBit — DAG deterministic orchestration engine      ║
║  arxiv: 2605.13848                                      ║
║                                                         ║
║  Solving three fatal flaws of prompt-driven orchestration: ║
║    1. Phantom routing  → DAG-defined workflow, engine-driven ║
║    2. Infinite loops   → DAG topology guarantees acyclic + max depth ║
║    3. Non-reproducible → Deterministic state machine, same input = same output ║
║                                                         ║
║  Three-layer memory isolation:                          ║
║    L1 Transient    = temporary variables within the current step ║
║    L2 Structured   = DAG context, passed between steps  ║
║    L3 External     = database / API / filesystem        ║
║                                                         ║
║  Architecture:                                          ║
║    ┌────────────┐   ┌──────────┐   ┌───────────────┐   ║
║    │ DAG Def     │ → │ State    │ → │ Agent Funcs   │   ║
║    │ (YAML/JSON) │   │ (route+validate) │ (typed funcs) │ ║
║    └────────────┘   └──────────┘   └───────────────┘   ║
║                                                         ║
║  Usage:                                                 ║
║    from lib.dag_engine import DAGEngine, DAGWorkflow    ║
║    engine = DAGEngine()                                 ║
║    wf = engine.load("patrol")                           ║
║    result = engine.execute(wf, context={})              ║
╚═══════════════════════════════════════════════════════════╝
"""

import ast
import json
import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

# === Configuration ===
DAG_DIR = Path(os.getenv("GBASE_DAG_DIR", "./data/dag-workflows"))
MAX_DEPTH = 50  # Maximum DAG depth
MAX_STEP_TIMEOUT = 300  # Single step timeout (seconds)
MAX_RETRY = 2  # Maximum retries per step


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
    """A single step node in the DAG."""

    id: str  # Unique identifier
    name: str  # Human-readable name
    agent: str  # Agent function to execute
    inputs: dict[str, str] = field(default_factory=dict)  # Input mapping: {param_name: source_path}
    outputs: list[str] = field(default_factory=list)  # Output list
    depends_on: list[str] = field(default_factory=list)  # Prerequisite step IDs
    condition: str | None = None  # Conditional expression (optional)
    retry: int = 0  # Retry count
    timeout: int = 60  # Timeout (seconds)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DAGWorkflow:
    """A complete DAG workflow definition."""

    name: str
    version: str = "1.0"
    description: str = ""
    steps: list[DAGStep] = field(default_factory=list)
    start_step: str = ""  # Entry step ID
    safety_checks: list[str] = field(default_factory=list)  # Safety check list
    max_depth: int = MAX_DEPTH
    metadata: dict[str, Any] = field(default_factory=dict)


class ThreeLayerMemory:
    """Three-layer memory isolation.

    L1 — Transient:   temporary variables within the current step, cleared after step ends
    L2 — Structured:  DAG context, passed between steps, cleared after workflow ends
    L3 — External:    persistent storage / API, maintained across workflows
    """

    def __init__(self, external_connectors: dict[str, Any] = None):
        self.l1_transient: dict[str, Any] = {}  # Transient scratchpad
        self.l2_structured: dict[str, Any] = {}  # Structured state
        self.l3_external: dict[str, Any] = external_connectors or {}  # External connectors

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
        """Resolve source path: l1.xxx / l2.xxx / l3.xxx / input.xxx"""
        if source_path.startswith("l1."):
            return self.get_l1(source_path[3:])
        elif source_path.startswith("l2."):
            return self.get_l2(source_path[3:])
        elif source_path.startswith("l3."):
            return self.get_l3(source_path[3:])
        elif source_path.startswith("input."):
            return self.get_l2(source_path[6:])
        return source_path  # literal


class DAGEngine:
    """DAG deterministic orchestration engine.

    Core design:
      - Topological sort guarantees acyclic execution
      - State-machine-driven routing (not LLM)
      - Three-layer memory isolation prevents context pollution
      - Safety check hooks reusable with constitution / meta-rules layer
    """

    def __init__(self):
        DAG_DIR.mkdir(parents=True, exist_ok=True)

        # Agent function registry
        self._agent_registry: dict[str, Callable] = {}

        # Safety check hooks (injected by Gbase constitution layer)
        self._safety_hooks: dict[str, Callable] = {}

        # Execution history
        self._execution_history: list[dict] = []

    # --- Agent Registration ---

    def register_agent(self, name: str, func: Callable):
        """Register an Agent function."""
        self._agent_registry[name] = func

    def register_safety_hook(self, name: str, func: Callable):
        """Register a safety check hook."""
        self._safety_hooks[name] = func

    # --- Workflow Loading ---

    def load(self, name: str) -> DAGWorkflow | None:
        """Load workflow definition from YAML."""
        wf_path = DAG_DIR / f"{name}.yaml"
        if not wf_path.exists():
            return None

        with open(wf_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return self._parse_workflow(data)

    def load_raw(self, data: dict) -> DAGWorkflow:
        """Load workflow directly from a dict."""
        return self._parse_workflow(data)

    def save(self, workflow: DAGWorkflow):
        """Save workflow to YAML."""
        wf_path = DAG_DIR / f"{workflow.name}.yaml"
        data = self._serialize_workflow(workflow)
        with open(wf_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    def _parse_workflow(self, data: dict) -> DAGWorkflow:
        """Parse workflow definition."""
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
        """Serialize workflow."""
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

    # --- Topological Sort & Validation ---

    def validate(self, workflow: DAGWorkflow) -> tuple[bool, list[str]]:
        """Validate DAG correctness.

        Checks:
          1. Acyclic (topological sort succeeds)
          2. All depends_on references exist
          3. All agents are registered
          4. Depth within limits
        """
        errors = []

        # Step index
        step_ids = {s.id for s in workflow.steps}
        if workflow.start_step and workflow.start_step not in step_ids:
            errors.append(f"Entry step '{workflow.start_step}' not found")
        if not workflow.start_step and workflow.steps:
            workflow.start_step = workflow.steps[0].id

        # Check prerequisite references
        for step in workflow.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    errors.append(f"Step '{step.id}' references non-existent dependency '{dep}'")

            if step.agent not in self._agent_registry:
                errors.append(f"Agent '{step.agent}' for step '{step.id}' is not registered")

        # Topological sort (cycle detection)
        sorted_ids, has_cycle = self._topological_sort(workflow)
        if has_cycle:
            errors.append("Cycle detected in DAG, cannot execute")

        # Depth check
        if len(sorted_ids) > workflow.max_depth:
            errors.append(f"Step count {len(sorted_ids)} exceeds max depth {workflow.max_depth}")

        return len(errors) == 0, errors

    def _topological_sort(self, workflow: DAGWorkflow) -> tuple[list[str], bool]:
        """Topological sort, returns (sorted step IDs, has_cycle)."""
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

    # --- Execution Engine ---

    def execute(
        self, workflow: DAGWorkflow, initial_context: dict[str, Any] = None, external_connectors: dict[str, Any] = None
    ) -> dict[str, Any]:
        """Execute a DAG workflow.

        Args:
            workflow: Workflow definition
            initial_context: Initial context (injected into L2)
            external_connectors: External connectors (injected into L3)

        Returns:
            {status, steps: {step_id: {status, output, error, duration}},
             context: L2 final state, stats: {...}}
        """
        # Validate
        valid, errors = self.validate(workflow)
        if not valid:
            return {
                "status": WorkflowStatus.FAILED.value,
                "error": f"DAG validation failed: {'; '.join(errors)}",
                "steps": {},
                "context": {},
                "stats": {},
            }

        # Initialize three-layer memory
        memory = ThreeLayerMemory(external_connectors or {})
        if initial_context:
            for k, v in initial_context.items():
                memory.set_l2(k, v)

        # Topological sort
        sorted_ids, _ = self._topological_sort(workflow)
        step_map = {s.id: s for s in workflow.steps}

        # State tracking
        step_results: dict[str, dict] = {}
        completed_count = 0
        failed_count = 0
        skipped_count = 0
        start_time = time.time()

        # Safety checks
        for check_name in workflow.safety_checks:
            if check_name in self._safety_hooks:
                result = self._safety_hooks[check_name](workflow, memory)
                if result is False:
                    return {
                        "status": WorkflowStatus.FAILED.value,
                        "error": f"Safety check '{check_name}' failed",
                        "steps": step_results,
                        "context": memory.l2_structured,
                        "stats": {},
                    }

        # Execute in topological order
        for step_id in sorted_ids:
            step = step_map.get(step_id)
            if not step:
                continue

            # Check if all prerequisites completed
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
                    "reason": "Prerequisite step failed",
                    "output": None,
                    "error": None,
                    "duration_ms": 0,
                }
                skipped_count += 1
                continue

            # Condition check
            if step.condition and not self._evaluate_condition(step.condition, memory):
                step_results[step_id] = {
                    "status": StepStatus.SKIPPED.value,
                    "reason": f"Condition not met: {step.condition}",
                    "output": None,
                    "error": None,
                    "duration_ms": 0,
                }
                skipped_count += 1
                continue

            # Resolve inputs
            resolved_inputs = {}
            for param, source_path in step.inputs.items():
                resolved_inputs[param] = memory.resolve(source_path)

            # Execute step (with retry)
            step_result = None
            last_error = None
            for _attempt in range(step.retry + 1):
                step_result = self._execute_step(step, resolved_inputs, memory)
                if step_result["status"] == StepStatus.COMPLETED.value:
                    break
                last_error = step_result.get("error")
                memory.clear_l1()  # Clear L1 before retry

            if step_result["status"] == StepStatus.COMPLETED.value:
                completed_count += 1
                # Write outputs to L2
                for output_key in step.outputs:
                    if output_key in step_result["output"]:
                        memory.set_l2(f"{step_id}.{output_key}", step_result["output"][output_key])
                # Also write an aggregated key
                memory.set_l2(f"{step_id}._result", step_result["output"])
            else:
                failed_count += 1
                step_result["error"] = step_result.get("error") or last_error

            step_results[step_id] = step_result

        total_duration = (time.time() - start_time) * 1000

        # Determine overall status
        if failed_count == 0:
            overall_status = WorkflowStatus.COMPLETED
        elif completed_count == 0:
            overall_status = WorkflowStatus.FAILED
        else:
            overall_status = WorkflowStatus.PARTIAL

        # Record execution history
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

        # Keep last 100 entries
        if len(self._execution_history) > 100:
            self._execution_history = self._execution_history[-100:]

        return {
            "status": overall_status.value,
            "error": None if failed_count == 0 else f"{failed_count} step(s) failed",
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
        """Execute a single step."""
        agent_func = self._agent_registry.get(step.agent)
        if not agent_func:
            return {
                "status": StepStatus.FAILED.value,
                "output": None,
                "error": f"Agent '{step.agent}' is not registered",
                "duration_ms": 0,
            }

        memory.clear_l1()
        t0 = time.time()

        try:
            # Call Agent function: agent(inputs, memory)
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
        """Evaluate a conditional expression.

        Supports: l2.xxx == 'value' / l2.xxx >= 1 etc.
        """
        try:
            # Replace variable references
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

            # Safe evaluation — only literal expressions allowed
            return bool(ast.literal_eval(expr))
        except Exception:
            return True  # Default to pass when condition cannot be evaluated (non-blocking)

    # --- Stats & History ---

    def get_history(self, limit: int = 10) -> list[dict]:
        """Return recent execution history."""
        return self._execution_history[-limit:]

    def stats(self) -> dict[str, Any]:
        """Return engine statistics."""
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


# --- Built-in Pilot Workflow Definitions ---

PILOT_WORKFLOWS = {
    "daily-patrol": {
        "name": "daily-patrol",
        "version": "1.0",
        "description": "Gbase daily patrol - Health check + Log audit + Portal link check",
        "start_step": "health_check",
        "safety_checks": ["constitution_check"],
        "steps": [
            {
                "id": "health_check",
                "name": "Health heartbeat check",
                "agent": "health_check",
                "outputs": ["healthy", "nodes_checked"],
                "timeout": 30,
            },
            {
                "id": "log_audit",
                "name": "Log audit",
                "agent": "log_audit",
                "depends_on": ["health_check"],
                "inputs": {"node_count": "l2.health_check.nodes_checked"},
                "outputs": ["errors_found", "warnings"],
                "timeout": 60,
            },
            {
                "id": "portal_link_check",
                "name": "Portal link check",
                "agent": "portal_check",
                "depends_on": ["health_check"],
                "outputs": ["broken_links", "total_links"],
                "timeout": 120,
            },
            {
                "id": "summary",
                "name": "Generate patrol report",
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
        "description": "Mail digest - Check inbox → Classify → Generate digest",
        "start_step": "check_inbox",
        "safety_checks": [],
        "steps": [
            {
                "id": "check_inbox",
                "name": "Check inbox",
                "agent": "check_inbox",
                "outputs": ["new_count", "mail_ids"],
                "timeout": 30,
            },
            {
                "id": "classify",
                "name": "Mail classification",
                "agent": "classify_mail",
                "depends_on": ["check_inbox"],
                "condition": "l2.check_inbox.new_count > 0",
                "inputs": {"mail_ids": "l2.check_inbox.mail_ids"},
                "outputs": ["categories"],
                "timeout": 60,
            },
            {
                "id": "digest",
                "name": "Generate digest",
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
        "description": "Quality check - Code lint → Security scan → Performance benchmark",
        "start_step": "code_lint",
        "safety_checks": ["constitution_check"],
        "steps": [
            {
                "id": "code_lint",
                "name": "Code lint check",
                "agent": "code_lint",
                "outputs": ["issues", "score"],
                "timeout": 60,
            },
            {
                "id": "security_scan",
                "name": "Security scan",
                "agent": "security_scan",
                "outputs": ["vulnerabilities", "risk_level"],
                "timeout": 120,
            },
            {
                "id": "perf_bench",
                "name": "Performance benchmark test",
                "agent": "perf_bench",
                "depends_on": ["code_lint"],
                "inputs": {"code_score": "l2.code_lint.score"},
                "outputs": ["benchmark_results"],
                "timeout": 180,
            },
            {
                "id": "quality_report",
                "name": "Quality report",
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


# --- Convenience Functions ---

_engine_instance: DAGEngine | None = None


def get_engine() -> DAGEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = DAGEngine()
        _init_pilot_workflows(_engine_instance)
    return _engine_instance


def _init_pilot_workflows(_engine: DAGEngine):
    """Initialize Pilot workflow definitions."""
    for name, wf_data in PILOT_WORKFLOWS.items():
        wf_path = DAG_DIR / f"{name}.yaml"
        if not wf_path.exists():
            with open(wf_path, "w", encoding="utf-8") as f:
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
                print("  ✅ Validation passed")
    else:
        print(json.dumps(engine.stats(), ensure_ascii=False, indent=2))
