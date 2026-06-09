"""remember_info — 统一记忆路由工具

自动判断内容类型，写入正确的存储层级：
- 凭据/配置/事实 → Knowledge (L2)
- 调研结论/学习心得 → Notes (L4)
- 行为教训/模式 → Experience (L3)
"""

import logging
from typing import Optional
from lib.toolkit import tool, get_global

logger = logging.getLogger(__name__)


# ── 分类关键词 ──
_KNOWLEDGE_KW = [
    "密钥", "key", "key", "token", "密码",
    "端口", "地址", "路径", "配置", "域名", "URL", "url",
    "账号", "API", "api", "secret",
    "版本", "版本号", "型号", "型号",
    "安装", "安装目录", "家目录", "home",
    "生日", "出生", "年龄", "关系",  # 个人信息
]

_NOTE_KW = [
    "学到了", "总结", "总结一下", "心得", "笔记",
    "调研", "调研报告", "文章", "论文", "读了",
    "学习了", "学习了", "摘要", "提炼",
    "概念", "概念理解", "原理",
    "框架", "模式", "范式",
]

_EXPERIENCE_KW = [
    "教训", "经验", "教训", "踩坑",
    "下次注意", "下次要", "以后先", "应该先",
    "根因是", "根因", "原因", "原因是",
    "学到的", "学到", "lesson",
    "记一条", "记住", "rule", "规则",
    "模式", "pattern",
]


def _classify_content(content: str) -> str:
    """判断内容类型：knowledge / note / experience"""
    cl = content.lower()

    # 先匹配力度最高的
    for kw in _EXPERIENCE_KW:
        if kw.lower() in cl:
            return "experience"

    knowledge_score = sum(1 for kw in _KNOWLEDGE_KW if kw.lower() in cl)
    note_score = sum(1 for kw in _NOTE_KW if kw.lower() in cl)

    if knowledge_score >= note_score and knowledge_score > 0:
        return "knowledge"
    if note_score > 0:
        return "note"

    # 默认：长内容(>200字)是笔记，短内容是一条知识
    if len(content) > 200:
        return "note"
    return "knowledge"


@tool()
async def remember_info(
    content: str,
    title: str = "",
    tags: str = "",
    source: str = "",
    force_type: Optional[str] = None,
    with_kw_category: str = "general",
) -> dict:
    """统一记忆入口——自动判断内容类型写入正确层级。

    什么时候用：
    *任何时候想存东西，都用这个工具。* 不要再直接调 remember_fact / note_write。
    它会自动判断内容类型，写入 Knowledge / Notes / Experience。

    Args:
        content: 要记住的内容。自动判断类型。
        title: 标题（仅对 note 有效，knowledge 和 experience 自动用前20字）
        tags: 逗号分隔的标签，方便搜索
        source: 来源描述（如"与用户对话"、"从trace提炼"）
        force_type: 强制指定类型。可选：knowledge / note / experience / auto
                    默认 auto（自动判断）
        with_kw_category: 当内容判断为 knowledge 时的分类（默认 general）
    """
    ftype = (force_type or "auto").lower()
    if ftype == "auto":
        ftype = _classify_content(content)

    if ftype == "knowledge":
        # 从 content 推断标题
        auto_title = content[:40] if len(content) > 40 else content
        # 记忆一条事实
        from tools.knowledge import remember_fact
        result = await remember_fact(
            fact=content,
            category=with_kw_category,
            tags=tags,
        )
        return {
            "ok": result.get("id") is not None if isinstance(result, dict) else False,
            "type": "knowledge",
            "id": result.get("id"),
            "detail": f"已写入 Knowledge: {auto_title}…" if len(content) > 40 else f"已写入 Knowledge: {content}",
        }

    elif ftype == "note":
        auto_title = title or content[:30]
        from tools.note_tool import note_write
        result = await note_write(
            title=auto_title,
            content=content,
            tags=tags,
            source=source or "remember_info",
        )
        return {
            "ok": result.get("ok") if isinstance(result, dict) else result,
            "type": "note",
            "detail": f"已写入 Notes: {auto_title}",
        }

    elif ftype == "experience":
        # 写入 experience（通过 storage 直接写）
        storage = get_global("storage")
        if not storage:
            return {"error": "存储引擎未初始化", "ok": False}

        auto_title = title or content[:30]
        summary = auto_title
        # Experience 用 rule="user_lesson"
        from tools.knowledge import remember_fact
        result = await remember_fact(
            fact=content,
            category="workflow",
            tags=tags or "lesson,experience",
        )
        # 同时写入一条 rule-based experience
        try:
            with storage._lock:
                now = __import__("time").time()
                payload = {
                    "content": content,
                    "summary": summary,
                    "rule": "learned_lesson",
                    "confidence": "high" if "教训" in content or "lesson" in content.lower() else "medium",
                    "source": source or "remember_info",
                    "tags": [t.strip() for t in tags.split(",") if t.strip()] if tags else ["lesson"],
                }
                import json
                storage._conn.execute(
                    "INSERT INTO entries (type, content, summary, created_at, confidence, rule) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("experience", json.dumps(payload), summary, now,
                     payload["confidence"], payload["rule"]),
                )
                storage._conn.commit()
                exp_id = storage._conn.lastrowid
        except Exception as e:
            logger.warning("experience 写入失败（不阻断）: %s", e)
            exp_id = None

        return {
            "ok": result.get("id") is not None if isinstance(result, dict) and result.get("ok") is not False else True,
            "type": "experience",
            "id": exp_id,
            "detail": f"已写入 Experience + Knowledge: {auto_title}",
        }

    else:
        return {"error": f"不支持的强制类型: {force_type}", "ok": False}


@tool()
async def remember_info_usage() -> dict:
    """展示 remember_info 的用法示例。"""
    return {
        "usage": "任何时候想存东西，都用 remember_info 而不是 remember_fact 或 note_write",
        "examples": [
            'remember_info(content="DB_CONNECTION=localhost:3306")  # → Knowledge',
            'remember_info(content="我看完了美眉的三篇配色笔记，总结：80-15-5配色法…", source="学习美眉笔记")  # → Notes',
            'remember_info(content="教训：写代码前先 read_file 看参数签名，不要猜参数名", force_type="experience")  # → Experience',
            'remember_info(content="端口 8443 是 your-agent", tags="port,config")  # → Knowledge',
        ],
    }
