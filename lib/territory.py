# SPDX-License-Identifier: MIT
"""
lib/territory.py — 领地感知模块（Territory-Aware Access Control）

所有 Agent 实例的工具层通过此模块检测"是否在侵犯其他 Agent 的领地"。

核心逻辑：
  - self_edit.py：永远锁自己家（铁律，不走这里）
  - write_file / exec_command：领地检查 + 提示主人授权
  - read_file：只读不写，不阻塞但记录警告

设计原则：
  1. 领地规则在静态注册表中定义，不依赖运行时发现
  2. 被主人明确指示（通过对话上下文）的操作不拦截
  3. 代码层只做检测，授权判断交给对话层
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 📋 领地注册表
# ──────────────────────────────────────────────
# 所有已知 Agent 的家目录（用真实绝对路径，不依赖 ~）
# 新增 Agent 需在此注册
AGENT_HOMES: dict[str, str] = {
    # !!! IMPORTANT: Replace with your actual paths !!!
    # Example:
    #   "my-agent": "/path/to/my-agent/home",
}


# ──────────────────────────────────────────────
# 🔍 自动定位：用 __file__ 推导"我是谁"
# ──────────────────────────────────────────────
_self_home_cache: str | None = None


def get_self_home() -> str:
    """返回调用者所在 Agent 的家目录（通过 __file__ 解析）。"""
    global _self_home_cache
    if _self_home_cache:
        return _self_home_cache

    # 当前模块的路径
    # ⚠️ 不调用 .resolve()！文件拷贝使用的是硬链接（所有实例 inode 相同），
    # .resolve() on symlinks resolves to the origin gbase-lib/, causing self_home to be incorrect
    # 直接使用 __file__ 原始路径：gundam-home/lib/territory.py → gundam-home/
    this_file = Path(__file__)
    self_home = str(this_file.parent.parent)
    _self_home_cache = self_home
    return self_home


def get_agent_name() -> str | None:
    """根据家目录反查当前 Agent 的名。"""
    self_home = get_self_home()
    for name, home in AGENT_HOMES.items():
        if os.path.abspath(home) == self_home:
            return name
    return None


# ──────────────────────────────────────────────
# 🚧 领地侵犯检测
# ──────────────────────────────────────────────


def check_territory_violation(target_path: str) -> str | None:
    """检查目标路径是否属于其他 Agent 的领地。

    注意：target_path 需是绝对路径或 ~ 开头，函数内部会做 expanduser。

    Args:
        target_path: 目标文件/目录路径

    Returns:
        侵犯的 Agent 名称，或 None（无侵犯）
    """
    abs_target = os.path.abspath(os.path.expanduser(target_path))
    self_home = get_self_home()

    for agent_name, agent_home in AGENT_HOMES.items():
        agent_abs = os.path.abspath(agent_home)

        # 跳过自己的家
        if agent_abs == self_home:
            continue

        # 跳过同义条目（gundam/gundam_home 指向同一目录）
        # 用 set 去重
        # 判断：目标路径是否以 Agent 家开头（包括家目录本身）
        if abs_target == agent_abs or abs_target.startswith(agent_abs + "/"):
            return agent_name

    return None


def build_territory_error(violation: str, abs_path: str, caller_label: str = "操作") -> str:
    """构建领地侵犯错误信息。"""
    return (
        f"❌ 领地侵犯: {caller_label}目标路径 '{abs_path}' "
        f"属于 Agent「{violation}」的家目录。\n"
        f"这是其他 Agent 的领地，没有主人授权不能自行修改。\n"
        f"如需授权，请先获得管理员许可。"
    )


# ──────────────────────────────────────────────
# 🆘 救援白名单（Rescue White-list）
# ──────────────────────────────────────────────
# 只有在救援模式下，agent 才能访问兄弟的特定路径
# 定义的格式：{兄弟agent名: {允许的操作: [允许的路径前缀]}}
# 路径以家目录相对路径表示

RESCUE_WHITELIST: dict[str, dict[str, list[str]]] = {
    "gundam": {
        "rescue": [
            "logs/gundam-stderr.log",
            "logs/gundam-stdout.log",
            "logs/gundam.log",
            "logs/app.log",
            "logs/server.log",
        ],
    },
    "poseidon": {
        "rescue": [
            "logs/poseidon-stderr.log",
            "logs/poseidon-stdout.log",
            "logs/poseidon.log",
            "logs/app.log",
            "logs/server.log",
        ],
    },
    "opprime": {
        "rescue": [
            "logs/opprime-hammer.log",
            "logs/opprime-ink.log",
            "logs/opprime-bumblebee.log",
            "logs/opprime-laser.log",
            "logs/opprime-forge.log",
        ],
    },
    "agent-ganjiang": {
        "rescue": [
            "logs/ganjiang-stderr.log",
            "logs/ganjiang-stdout.log",
            "logs/ganjiang.log",
            "logs/startup.log",
        ],
    },
    "lancer": {
        "rescue": [
            "cc/logs/startup.log",
            "x/logs/startup.log",
        ],
    },
}


def check_rescue_permission(
    target_agent: str,
    target_path: str,
    operation: str = "rescue",
) -> bool:
    """检查在救援模式下是否允许访问指定路径。

    Args:
        target_agent: 目标 Agent 的名称（如 gundam、poseidon）
        target_path: 绝对路径或 ~ 开头的路径
        operation: 操作类型，默认 "rescue"

    Returns:
        True 允许访问，False 拒绝
    """
    abs_target = os.path.abspath(os.path.expanduser(target_path))

    # 先找到目标 Agent 的家目录
    target_home = AGENT_HOMES.get(target_agent)
    if not target_home:
        return False

    target_abs_home = os.path.abspath(target_home)

    # 路径必须以目标家目录开头
    if not abs_target.startswith(target_abs_home):
        return False

    # 计算相对路径 (从家目录开始的相对路径)
    rel_path = os.path.relpath(abs_target, target_abs_home)

    # 检查是否在白名单中
    agent_rules = RESCUE_WHITELIST.get(target_agent, {})
    allowed_paths = agent_rules.get(operation, [])

    return any(rel_path == allowed or rel_path.startswith(allowed) for allowed in allowed_paths)


# ──────────────────────────────────────────────
# 💡 rescue_tool 专用：快速获取救援可访问路径
# ──────────────────────────────────────────────

RESCUE_PORTS: dict[str, int] = {
    "gundam": 8440,
    "poseidon": 8428,
    "agent-ganjiang": 8429,
    "hammer": 8431,
    "ink": 8432,
    "bumblebee": 8434,
    "laser": 8435,
    "forge": 8436,
    "lancer_cc": 8441,
    "lancer_x": 8442,
}


def get_rescue_port(agent_name: str) -> int | None:
    """获取 Agent 的救援端口（用于 /health 检查）。
    仅可在 rescue 上下文中调用。
    """
    return RESCUE_PORTS.get(agent_name)
