# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/skill_loader.py

Skill loader — reads SKILL.md from skills/ directory.
Responsible for:
- Scanning all skills in the skills/ directory
- Parsing SKILL.md tags/description/triggers (supports both yaml frontmatter and triggers: inline formats)
- Providing skill index summaries
- Providing full SKILL.md content for injection
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SkillLoader:
    """Skill loader."""

    def __init__(self, skills_dir: str = "skills"):
        self.skills_dir = Path(skills_dir)
        self._index: list[dict] = []  # [{name, triggers, description}, ...]
        self._contents: dict[str, str] = {}  # {name: full_skill_md_content}
        self._loaded = False

    def _parse_frontmatter(self, content: str) -> dict:
        """Parse tags/description from yaml frontmatter (--- block).

        Note: does not depend on a yaml library; uses line-by-line parsing of common key: value pairs.
        """
        result = {"tags": [], "description": ""}
        lines = content.split("\n")
        in_fm = False

        for line in lines:
            stripped = line.strip()
            if stripped == "---":
                if not in_fm:
                    in_fm = True
                    continue
                else:
                    break  # end of frontmatter

            if not in_fm:
                continue

            lower = stripped.lower()

            if lower.startswith("tags:"):
                raw = line.split(":", 1)[1].strip() if ":" in line else ""
                raw = raw.strip('"').strip("'")
                # tags may be comma-separated or multi-line (one tag per line)
                parts = [t.strip() for t in raw.split(",") if t.strip()]
                result["tags"].extend(parts)

            if lower.startswith("description:"):
                desc = line.split(":", 1)[1].strip() if ":" in line else ""
                result["description"] = desc.strip('"').strip("'")

        return result

    def load(self):
        """Scan the skills/ directory and load all skills."""
        if self._loaded:
            return
        self._index = []
        self._contents = {}

        if not self.skills_dir.is_dir():
            logger.warning("Skill directory not found: %s", self.skills_dir)
            self._loaded = True
            return

        for entry in sorted(self.skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue

            try:
                content = skill_md.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to read %s/SKILL.md: %s", entry.name, e)
                continue

            # parse metadata
            triggers = []
            description = ""

            # try yaml frontmatter first
            fm = self._parse_frontmatter(content)
            triggers = list(fm.get("tags", []))
            description = fm.get("description", "")

            # then try lines with triggers: / description: prefix (non-frontmatter)
            for line in content.split("\n"):
                ll = line.lower().strip()
                if ll.startswith("triggers:"):
                    raw = line.split(":", 1)[1].strip() if ":" in line else ""
                    extra = [t.strip().strip('"').strip("'") for t in raw.split(",") if t.strip()]
                    triggers.extend(extra)
                if not description and ll.startswith("description:"):
                    desc = line.split(":", 1)[1].strip() if ":" in line else ""
                    if desc:
                        description = desc

            # deduplicate triggers
            triggers = list(dict.fromkeys(triggers))

            # if still no description, extract from content body
            if not description:
                for line in content.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("-") and not line.startswith("---"):
                        description = line[:120]
                        break

            skill_info = {
                "name": entry.name,
                "triggers": triggers,
                "description": description[:200],
            }
            self._index.append(skill_info)
            self._contents[entry.name] = content

        self._loaded = True
        logger.info("Skills loaded: %d", len(self._index))

    # -- Public API --

    def get_skill_names(self) -> list[str]:
        """Get the list of all skill names."""
        self.load()
        return [s["name"] for s in self._index]

    def get_skill_index(self) -> list[dict]:
        """Get the skill index (summary info)."""
        self.load()
        return list(self._index)

    def get_skill_content(self, name: str) -> str | None:
        """Get the full SKILL.md content for a given skill."""
        self.load()
        return self._contents.get(name)

    def get_injection_text(self) -> str:
        """Generate the skill index text to inject into the system prompt."""
        self.load()
        if not self._index:
            return ""

        lines = [
            "## 🧠 Available Skills",
            "The following are your available skill packages (loaded from the skills/ directory).",
            "When a user question involves a relevant domain, read the SKILL.md guidance first, then invoke tools.",
        ]
        for s in self._index:
            triggers_str = ", ".join(s["triggers"][:5]) if s["triggers"] else ""
            if triggers_str:
                lines.append(f"- **{s['name']}** — triggers: {triggers_str} → {s['description']}")
            else:
                lines.append(f"- **{s['name']}** — {s['description']}")

        return "\n".join(lines)
