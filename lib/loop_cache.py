# SPDX-License-Identifier: MIT
"""
LoopCache — LOOP 缓存的存储引擎。

职责：
  - 模板管理：学习常见任务的工具调用模式，达到"稳定"阈值后生成回放模板
  - 轨迹存储：记录每次工具调用序列（去重、hash）
  - 回放执行：按模板确定性重放，跳过 LLM 推理

存储格式：JSON（~/.gbase/loop_cache/）
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path(os.path.expanduser("~/.gbase/loop_cache"))
TEMPLATES_FILE = CACHE_DIR / "templates.json"
TRACES_DIR = CACHE_DIR / "traces"
TTL_HOURS = 4  # 缓存有效期
MIN_STABILITY = 3  # 最少出现次数才能成为模板


class LoopCache:
    """LoopHook 的存储后端。"""

    def __init__(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        TRACES_DIR.mkdir(parents=True, exist_ok=True)
        self._templates: dict[str, dict] = self._load_templates()

    # ── 模板管理 ──

    def _load_templates(self) -> dict[str, dict]:
        if TEMPLATES_FILE.exists():
            try:
                return json.loads(TEMPLATES_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_templates(self):
        TEMPLATES_FILE.write_text(json.dumps(self._templates, indent=2, ensure_ascii=False))

    def find_template(self, task_type: str) -> dict | None:
        """查找指定类型的模板（需 stable 或 verified 状态）。"""
        for _tmpl_id, tmpl in self._templates.items():
            if tmpl.get("task_type") == task_type and tmpl.get("status") in ("stable", "verified"):
                # 检查 TTL
                cached_at = tmpl.get("cached_at", 0)
                if time.time() - cached_at < TTL_HOURS * 3600:
                    return tmpl
                else:
                    # 过期，降级
                    tmpl["status"] = "expired"
                    self._save_templates()
                    return None
        return None

    # ── 轨迹记录 ──

    def _make_signature(self, steps: list[dict]) -> str:
        """从工具调用序列生成签名（工具名序列的 hash）。"""
        tool_names = []
        for s in steps:
            if isinstance(s, dict):
                tool_names.append(str(s.get("tool_name") or s.get("name") or "unknown"))
        sig = "→".join(tool_names)
        return hashlib.md5(sig.encode()).hexdigest()[:12]

    def intercept(self, task_id: str, task_type: str, steps: list[dict], tokens: int = 0):
        """记录一次工具调用轨迹，累积到模板中。"""
        sig = self._make_signature(steps)
        tmpl_key = f"{task_type}:{sig}"

        if tmpl_key not in self._templates:
            self._templates[tmpl_key] = {
                "task_type": task_type,
                "signature": sig,
                "hits": 1,
                "total_tokens": tokens,
                "status": "experimental",
                "cached_at": time.time(),
                "steps": steps,  # Save模板步骤
                "history": [],
            }
        else:
            tmpl = self._templates[tmpl_key]
            tmpl["hits"] += 1
            tmpl["total_tokens"] = max(tmpl["total_tokens"], tokens)
            tmpl["cached_at"] = time.time()

            # 达到稳定阈值 → 升级
            if tmpl["hits"] >= MIN_STABILITY:
                if tmpl["status"] == "experimental":
                    tmpl["status"] = "stable"
                elif tmpl["hits"] >= MIN_STABILITY * 2 and tmpl["status"] == "stable":
                    tmpl["status"] = "verified"

        # 记录历史轨迹（抽样）
        self._templates[tmpl_key].setdefault("history", []).append(
            {
                "task_id": task_id,
                "tokens": tokens,
                "ts": time.time(),
            }
        )
        # 只保留最近 20 条
        self._templates[tmpl_key]["history"] = self._templates[tmpl_key]["history"][-20:]

        self._save_templates()

    # ── 回放 ──

    def replay(self, task_id: str, task_type: str, context: dict[str, Any] = None) -> dict | None:
        """按模板回放工具调用结果。"""
        tmpl = self.find_template(task_type)
        if not tmpl:
            return None

        # 从模板的 steps 构造回放结果
        # 注意：回放的是模板步骤描述，不是真实执行结果
        # 对于确信结果不会变的工具调用（如读文件），可以直接回放
        steps = tmpl.get("steps", [])
        return {
            "_loop_replay": True,
            "_loop_template_id": tmpl.get("signature", ""),
            "_loop_tokens_saved": tmpl.get("total_tokens", 0),
            "task_id": task_id,
            "task_type": task_type,
            "cached_steps": steps,
            "cached_hits": tmpl.get("hits", 0),
        }

    def stats(self) -> dict[str, Any]:
        """返回缓存统计。"""
        total = len(self._templates)
        stable = sum(1 for t in self._templates.values() if t.get("status") == "stable")
        verified = sum(1 for t in self._templates.values() if t.get("status") == "verified")
        experimental = sum(1 for t in self._templates.values() if t.get("status") == "experimental")
        expired = sum(1 for t in self._templates.values() if t.get("status") == "expired")
        return {
            "total_templates": total,
            "stable": stable,
            "verified": verified,
            "experimental": experimental,
            "expired": expired,
        }

    def close(self):
        """同步Save（无实际操作，save 在 intercept 中已做）。"""
        self._save_templates()
