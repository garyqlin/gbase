# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/skill_loader.py

Skill loader — reads SKILL.md from skills/ directory.
负责：
- 扫描 skills/ 目录下的所有 skill
- 解析 SKILL.md 的 tags/description/triggers（支持 yaml frontmatter 和 triggers: 两种格式）
- 提供 skill 索引摘要
- 提供完整 SKILL.md 内容供注入
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SkillLoader:
    """Skill 加载器。"""

    def __init__(self, skills_dir: str = "skills"):
        self.skills_dir = Path(skills_dir)
        self._index: list[dict] = []  # [{name, triggers, description}, ...]
        self._contents: dict[str, str] = {}  # {name: full_skill_md_content}
        self._loaded = False

    def _parse_frontmatter(self, content: str) -> dict:
        """从 yaml frontmatter（--- 块）解析 tags/description。
        
        注意：不依赖 yaml 库，只用逐行解析常见的 key: value 格式。
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
                    break  # frontmatter 结束

            if not in_fm:
                continue

            lower = stripped.lower()

            if lower.startswith("tags:"):
                raw = line.split(":", 1)[1].strip() if ":" in line else ""
                raw = raw.strip('"').strip("'")
                # tags 可能是逗号分隔，也可能有多行（每个 tag 一行）
                parts = [t.strip() for t in raw.split(",") if t.strip()]
                result["tags"].extend(parts)

            if lower.startswith("description:"):
                desc = line.split(":", 1)[1].strip() if ":" in line else ""
                result["description"] = desc.strip('"').strip("'")

        return result

    def load(self):
        """扫描 skills/ 目录，加载所有 skill。"""
        if self._loaded:
            return
        self._index = []
        self._contents = {}

        if not self.skills_dir.is_dir():
            logger.warning("Skill 目录不存在: %s", self.skills_dir)
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
                logger.warning("读取 %s/SKILL.md 失败: %s", entry.name, e)
                continue

            # 解析元信息
            triggers = []
            description = ""

            # 先试 yaml frontmatter
            fm = self._parse_frontmatter(content)
            triggers = list(fm.get("tags", []))
            description = fm.get("description", "")

            # 再试 lines: triggers / description 前缀（非 frontmatter）
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

            # 去重 triggers
            triggers = list(dict.fromkeys(triggers))

            # 如果仍然没有 description，从内容中提取
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
        logger.info("Skill 加载完成: %d 个", len(self._index))

    # ── 公开方法 ──

    def get_skill_names(self) -> list[str]:
        """获取所有 skill 名称列表。"""
        self.load()
        return [s["name"] for s in self._index]

    def get_skill_index(self) -> list[dict]:
        """获取 skill 索引（摘要信息）。"""
        self.load()
        return list(self._index)

    def get_skill_content(self, name: str) -> str | None:
        """获取指定 skill 的完整 SKILL.md 内容。"""
        self.load()
        return self._contents.get(name)

    def get_injection_text(self) -> str:
        """生成要注入到 system prompt 的 skill 索引文本。"""
        self.load()
        if not self._index:
            return ""

        lines = ["## 🧠 可用技能",
                 "以下是你可用的技能包（从 skills/ 目录加载）。",
                 "当用户的问题涉及对应领域时，先阅读该 SKILL.md 的指引，再调用工具。"]
        for s in self._index:
            triggers_str = ", ".join(s["triggers"][:5]) if s["triggers"] else ""
            if triggers_str:
                lines.append(f"- **{s['name']}** — triggers: {triggers_str} → {s['description']}")
            else:
                lines.append(f"- **{s['name']}** — {s['description']}")

        return "\n".join(lines)
