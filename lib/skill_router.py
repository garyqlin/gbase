# SPDX-License-Identifier: MIT
"""
gbase/lib/skill_router.py

轻量级技能路由引擎 — 根据用户输入自动匹配并加载适用 Skill。

与现有的 SkillLoader 配合工作：
- SkillLoader: 扫描/加载 skills/ 目录中的 SKILL.md
- SkillRouter: 根据用户输入，自动匹配最相关的 Skill，加载其内容

匹配策略：
1. 基于触发词（triggers/tags）的精确匹配（最高分）
2. 基于 description 的关键词模糊匹配
3. 基于名称的关键词匹配
4. 支持组合：多个词匹配同一个 skill 时加权

使用方式：
    router = SkillRouter(skill_loader, skills_index_path="skills-index.json")
    result = router.get_route_instruction(user_input)
    parts.append(result)
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SYNONYM_MAP = {
    "video": ["video", "vid", "影片", "视频", "movie", "film", "clip", "mp4", "动画"],
    "image": ["image", "img", "picture", "photo", "pic", "图片", "图像", "照片", "截图"],
    "audio": ["audio", "sound", "music", "音频", "声音", "音乐"],
    "document": ["document", "doc", "文档", "docx", "report", "reporting", "报告", "pdf"],
    "slide": ["slide", "slides", "ppt", "pptx", "演示", "幻灯片", "presentation"],
    "search": ["search", "搜", "查找", "find", "query", "查询"],
    "code": ["code", "coding", "programming", "代码", "编程", "develop", "implementation", "实现"],
    "review": ["review", "audit", "审", "检查", "code review", "pr", "pull request"],
    "debug": ["debug", "debugging", "调试", "fix", "修复", "bug", "issue"],
    "test": ["test", "testing", "测试", "automated test", "unit test"],
    "deploy": ["deploy", "deployment", "部署", "发布", "release", "ship", "push"],
    "design": ["design", "ui", "ux", "设计", "layout", "visual", "视觉"],
    "plan": ["plan", "planning", "规划", "方案", "design doc", "architecture"],
    "meeting": ["meeting", "会议", "agenda", "minutes", "纪要", "transcript"],
    "note": ["note", "notes", "笔记", "纪要", "summary", "总结"],
    "research": ["research", "调研", "analysis", "分析", "investigate", "调查", "explore"],
    "security": ["security", "安全", "vulnerability", "vuln", "cve", "audit"],
    "database": ["database", "db", "数据库", "migration", "migrate", "schema"],
    "api": ["api", "rest", "endpoint", "接口", "graphql", "webhook"],
    "ticket": ["ticket", "issue", "jira", "linear", "工单", "bug", "task"],
    "github": ["github", "git", "pr", "pull request", "commit", "repo", "仓库"],
    "game": ["game", "游戏", "gaming", "godot", "unity", "3d"],
    "animation": ["animation", "animate", "gif", "动画", "transition", "easing"],
    "brand": ["brand", "品牌", "logo", "color", "theme", "主题"],
    "mcp": ["mcp", "model context protocol", "server", "tool server"],
}

STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "this", "that", "these", "those", "i", "you", "he", "she", "it",
    "we", "they", "me", "my", "your", "his", "her", "its", "our",
    "their", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "do", "does", "did", "has",
    "have", "had", "can", "could", "will", "would", "shall", "should",
    "may", "might", "no", "not", "nor", "so", "if", "then", "else",
    "when", "where", "why", "how", "which", "who", "whom",
    "了", "的", "是", "在", "和", "就", "也", "都", "要", "会",
    "有", "没", "不", "很", "吧", "吗", "呢", "啊", "哦", "嗯",
    "请", "帮", "把", "给", "让", "从", "被", "向", "往", "用",
    "想", "能", "可以", "应该", "需要", "有点", "一些", "这个",
}


class SkillRouter:
    """轻量级技能路由引擎。

    与 SkillLoader 配合：
    - SkillLoader 负责扫描/加载 skills/ 目录的 SKILL.md
    - SkillRouter 负责根据用户输入匹配最相关的 Skill
    """

    def __init__(
        self,
        skill_loader,
        skills_index_path: str | Path | None = None,
    ):
        self.skill_loader = skill_loader
        self.skills_index_path = Path(skills_index_path) if skills_index_path else None
        self._external_skills: list[dict] = []

        if self.skills_index_path and self.skills_index_path.exists():
            self._load_external_index()

    def _load_external_index(self):
        try:
            with open(self.skills_index_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._external_skills = data.get("skills", data.get("index", []))
            elif isinstance(data, list):
                self._external_skills = data
            logger.info(
                "外部技能索引加载: %d 个 (from %s)",
                len(self._external_skills),
                self.skills_index_path,
            )
        except Exception as e:
            logger.warning("外部技能索引加载失败: %s", e)

    def _tokenize(self, text: str) -> list[str]:
        """将文本拆分为 token。

        中文：按 2-gram 滑动窗口分组（"视频"作为一个 token），
              同时保留单字作为 fallback。
        英文：按空格分词。
        """
        text = text.lower()
        tokens: list[str] = []

        # 先按空格拆分
        for part in text.split():
            part = part.strip(".,!?;:()[]{}\"'`~@#$%^&*+=/\\|<>")
            if not part or part in STOP_WORDS:
                continue
            # 中文分词：提取所有长度 >= 2 的词组 + 单字
            if any(ord(c) > 127 for c in part):
                chars = [c for c in part if c.strip() and c not in STOP_WORDS and ord(c) > 127]
                if len(chars) >= 2:
                    # 2-gram 滑动窗口
                    for i in range(len(chars) - 1):
                        bigram = chars[i] + chars[i+1]
                        if not all(c in STOP_WORDS for c in bigram):
                            tokens.append(bigram)
                # 也保留单字（低权重 fallback）
                tokens.extend(chars)
            else:
                tokens.append(part)

        return list(dict.fromkeys(tokens))  # 去重保留顺序

    def _match_score(self, skill: dict, tokens: list[str]) -> float:
        """计算 skill 与输入 token 的匹配分数。"""
        score = 0.0
        name = skill.get("name", "").lower()
        desc = (
            skill.get("description") or skill.get("desc") or skill.get("short") or ""
        ).lower()
        triggers = [t.lower() for t in skill.get("triggers", skill.get("tags", []))]
        full_text = f"{name} {desc} {' '.join(triggers)}"

        for token in tokens:
            if token == name:
                score += 10.0
            elif token in name:
                score += 5.0
            elif any(token in t for t in triggers):
                score += 4.0
            elif token in desc:
                score += 2.0
            else:
                # 同义词匹配（跨语言）
                for syn_group in SYNONYM_MAP.values():
                    if token in syn_group:
                        # 同义词组里找一个中英文都能匹配的
                        # 如 token="视频"匹配"video"，full_text里有"videos"
                        token_is_chinese = ord(token[0]) > 127 if token else False
                        if token_is_chinese:
                            # 中文 token → 找同义词组中的英文词匹配 full_text
                            eng_variants = [s for s in syn_group if all(ord(c) < 128 for c in s)]
                            if any(e in full_text for e in eng_variants):
                                score += 1.5
                                break
                        else:
                            # 英文 token → 找同义词组中的中文词匹配 full_text
                            cn_variants = [s for s in syn_group if any(ord(c) > 127 for c in s)]
                            if any(c in full_text for c in cn_variants) or token in full_text:
                                score += 1.5
                                break

        return score

    def route(self, user_input: str, top_k: int = 5) -> list[dict]:
        """根据用户输入匹配最相关的 Skill。

        Returns:
            [{"name", "score", "description", "source"}, ...]
        """
        tokens = self._tokenize(user_input)
        if not tokens:
            return []

        candidates: list[dict] = []

        # 1. 匹配外部技能索引
        for skill in self._external_skills:
            if not isinstance(skill, dict):
                continue
            score = self._match_score(skill, tokens)
            if score > 0:
                candidates.append({
                    "name": skill.get("name", "unknown"),
                    "score": score,
                    "description": skill.get("description", ""),
                    "source": "awesome-codex",
                })

        # 2. 匹配本地 skills/ 目录
        try:
            local_skills = self.skill_loader.get_skill_index()
            for skill in local_skills:
                score = self._match_score(skill, tokens)
                if score > 0:
                    candidates.append({
                        "name": skill.get("name", "unknown"),
                        "score": score,
                        "description": skill.get("description", ""),
                        "source": "local",
                        "triggers": skill.get("triggers", []),
                    })
        except Exception as e:
            logger.warning("本地 skill 匹配失败: %s", e)

        # 去重 + 排序（同分时名称精确匹配优先）
        seen = set()
        unique = []
        tokens_lower = [t.lower() for t in tokens]
        for c in sorted(
            candidates,
            key=lambda x: (
                x["score"],  # 主排序：分数
                1 if x["name"].lower() in tokens_lower else 0,  # 次排序：名称精确匹配提权
                1 if any(t in x["name"].lower() for t in tokens_lower) else 0,  # 三排序：名称子串匹配
            ),
            reverse=True,
        ):
            if c["name"] not in seen:
                seen.add(c["name"])
                unique.append(c)

        return unique[:top_k]

    def load_skill_content(self, skill_name: str) -> Optional[str]:
        """加载匹配到的 Skill 的完整内容。"""
        # 本地
        content = self.skill_loader.get_skill_content(skill_name)
        if content:
            return content

        # awesome-codex-skills
        if self.skills_index_path:
            parent = self.skills_index_path.parent
            for subdir in ["awesome-codex-skills", "awesome-codex-skills/skills"]:
                skill_md = parent / subdir / skill_name / "SKILL.md"
                if skill_md.exists():
                    try:
                        return skill_md.read_text(encoding="utf-8")
                    except Exception as e:
                        logger.warning("读取 %s 失败: %s", skill_md, e)

        return None

    def get_route_instruction(self, user_input: str, inject_lines: int = 35) -> str:
        """生成路由指令 — 注入到 system_prompt 的前置知识区块。"""
        matches = self.route(user_input)
        if not matches:
            return ""

        lines = ["## Skill Route (auto-matched by input)"]
        lines.append(
            "The following skills match your current task. Read the relevant SKILL.md "
            "before starting work."
        )
        lines.append("")

        for m in matches:
            lines.append(
                f"- **{m['name']}** (score={m['score']:.1f}, "
                f"source={m['source']}) — {m['description'][:100]}"
            )
            content = self.load_skill_content(m["name"])
            if content:
                parts = content.split("\n")
                inject_cnt = min(inject_lines, len(parts))
                lines.append("  ```")
                lines.extend("  " + p for p in parts[:inject_cnt])
                if len(parts) > inject_cnt:
                    lines.append(f"  ... (full: {len(parts)} lines)")
                lines.append("  ```")

        lines.append("")
        lines.append("(360+ skills available total — others load on demand via `read_file`)")
        return "\n".join(lines)
