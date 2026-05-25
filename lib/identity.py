# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/identity.py

Identity loader: loads identity from identities/<name>/ directory.
"""

import functools
import logging
import os

logger = logging.getLogger(__name__)


class Identity:
    """Identity object."""

    def __init__(self, name: str, root_dir: str = "identities", experience_engine=None, skill_loader=None):
        self.name = name
        self.root = os.path.join(root_dir, name)
        self.system_prompt: str = ""
        self.soul: str = ""
        self.constitution: str = ""
        self.memory: str = ""
        self._experience_engine = experience_engine
        self._skill_loader = skill_loader

        # Track file mtimes for cache invalidation
        self._file_mtimes: dict[str, float] = {}

        self._load()

    def _load(self):
        """Load all files from the identity directory."""
        system_prompt_path = os.path.join(self.root, "system_prompt.txt")
        if os.path.exists(system_prompt_path):
            with open(system_prompt_path, encoding="utf-8") as f:
                self.system_prompt = f.read().strip()
            logger.info("Identity %s: loaded system_prompt (%d chars)", self.name, len(self.system_prompt))

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
        """Inject experience engine (optional)."""
        self._experience_engine = engine
        logger.info("Experience engine bound")

    def get_system_prompt(self) -> str:
        """Build the final system prompt (identity declaration + memory + constitution + experience + skill index)."""
        parts = [self.system_prompt]
        if self.constitution:
            parts.append(f"\n\n## CONSTITUTION\n{self.constitution}")
        if self.soul:
            parts.append(f"\n## SOUL\n{self.soul}")
        if self.memory:
            parts.append(f"\n## MEMORY\n{self.memory}")

        # Skill index (injected before experience, as experience takes priority)
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
        """Inject skill loader (optional)."""
        self._skill_loader = loader


@functools.cache
def _identity_cache_key(_name: str, _root_dir: str) -> bool:  # noqa: ARG001
    """Internal cache sentinel.
    Returns True if cache is available (not actually used for Identity object caching).
    """
    return True


_identity_store: dict = {}


def load_identity(name: str, root_dir: str = "identities", experience_engine=None, skill_loader=None) -> Identity:
    """Quick: load an identity (with caching).

    Identity objects with the same name+root_dir are cached and reused (process-level cache),
    but experience_engine and skill_loader are re-injected every time,
    as they may change at runtime.
    """
    key = (name, root_dir)
    if key in _identity_store:
        ident = _identity_store[key]
        # Re-inject runtime dependencies that may have changed
        ident.set_experience_engine(experience_engine)
        ident.set_skill_loader(skill_loader)
        return ident
    ident = Identity(name, root_dir, experience_engine=experience_engine, skill_loader=skill_loader)
    _identity_store[key] = ident
    return ident


def list_identities(root_dir: str = "identities") -> list[str]:
    """List all available identities."""
    if not os.path.isdir(root_dir):
        return []
    return sorted(
        [d for d in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, d)) and not d.startswith("_")]
    )
