# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/identity.py

身份加载器：从 identities/<name>/ 目录加载身份声明。
"""

import functools
import logging
import os

logger = logging.getLogger(__name__)


class Identity:
    """身份证象。"""

    def __init__(self, name: str, root_dir: str = "identities", experience_engine=None, skill_loader=None):
        self.name = name
        self.root = os.path.join(root_dir, name)
        self.system_prompt: str = ""
        self.soul: str = ""
        self.constitution: str = ""
        self.memory: str = ""
        self._experience_engine = experience_engine
        self._skill_loader = skill_loader

        # 记录文件 mtime 用于缓存失效
        self._file_mtimes: dict[str, float] = {}

        self._load()

    def _load(self):
        """从身份目录加载所有文件。"""
        system_prompt_path = os.path.join(self.root, "system_prompt.txt")
        if os.path.exists(system_prompt_path):
            with open(system_prompt_path, encoding="utf-8") as f:
                self.system_prompt = f.read().strip()
            logger.info("身份 %s: 加载 system_prompt (%d chars)", self.name, len(self.system_prompt))

        soul_path = os.path.join(self.root, "SOUL.md")
        if os.path.exists(soul_path):
            with open(soul_path, encoding="utf-8") as f:
                self.soul = f.read().strip()

        constitution_path = os.path.join(self.root, "CONSTITUTION.md")
        if os.path.exists(constitution_path):
            with open(constitution_path, encoding="utf-8") as f:
                self.constitution = f.read().strip()

        memory_path = os.path.join(self.root, "MEMORY.md")
        if os.path.exists(memory_path):
            with open(memory_path, encoding="utf-8") as f:
                self.memory = f.read().strip()

    def set_experience_engine(self, engine):
        """注入经验引擎（可选）。"""
        self._experience_engine = engine
        logger.info("经验引擎已绑定")

    def get_system_prompt(self) -> str:
        """构建最终的 system prompt（身份声明 + 记忆 + 宪法 + 经验 + skill 索引）。"""
        parts = [self.system_prompt]
        if self.constitution:
            parts.append(f"\n\n## 宪法\n{self.constitution}")
        if self.soul:
            parts.append(f"\n## 灵魂\n{self.soul}")
        if self.memory:
            parts.append(f"\n## 记忆\n{self.memory}")

        # skill 索引（先于经验注入，因为经验更优先）
        if self._skill_loader:
            skill_text = self._skill_loader.get_injection_text()
            if skill_text:
                parts.append("\n\n" + skill_text)

        if self._experience_engine:
            injection = self._experience_engine.get_injection_text()
            if injection:
                parts.append(injection)
        return "\n".join(parts)

    def set_skill_loader(self, loader):
        """注入 skill 加载器（可选）。"""
        self._skill_loader = loader


@functools.cache
def _identity_cache_key(name: str, root_dir: str) -> bool:
    """内部缓存哨兵：标记给定身份的配置文件是否已缓存。
    返回 True 表示缓存可用（实际不用于 Identity 对象缓存）。
    """
    return True


_identity_store: dict = {}


def load_identity(name: str, root_dir: str = "identities", experience_engine=None, skill_loader=None) -> Identity:
    """快捷：加载一个身份（带缓存）。

    相同 name+root_dir 的 Identity 对象会被缓存复用（proces 级缓存），
    但 experience_engine 和 skill_loader 每次都会重新注入，
    因为它们可能在运行时变化。
    """
    key = (name, root_dir)
    if key in _identity_store:
        ident = _identity_store[key]
        # 重新注入可能变化的运行时依赖
        ident.set_experience_engine(experience_engine)
        ident.set_skill_loader(skill_loader)
        return ident
    ident = Identity(name, root_dir, experience_engine=experience_engine, skill_loader=skill_loader)
    _identity_store[key] = ident
    return ident


def list_identities(root_dir: str = "identities") -> list[str]:
    """列出所有可用身份。"""
    if not os.path.isdir(root_dir):
        return []
    return sorted(
        [d for d in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, d)) and not d.startswith("_")]
    )
