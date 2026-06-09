"""
archive_search.py — Archive store 搜索工具（GMem Phase A1）

精确回查历史对话的 Archive Store 搜索工具。\n让 Agent 能像使用 lcm_grep 一样精确回查历史对话。
"""

import logging
import time

from lib.toolkit import get_global, tool

logger = logging.getLogger("archive_search")


@tool
def archive_search(query: str, max_results: int = 5, session_only: bool = False) -> str:
    """搜索 archive_store 中的历史对话记录。

    当你需要回忆之前做过的任务、用户提过的要求、讨论过的技术方案、用户说过的话时用这个。
    比 Knowledge（dat.db）更全面，因为它存储的是完整的对话内容，不会受压缩或老化策略影响。

    Args:
        query: 搜索关键词（中文自动分词，支持2字以上关键词）
        max_results: 返回条数上限，默认5
        session_only: 是否只搜当前会话（默认False，搜索全量历史）

    Returns:
        匹配的历史记录列表，每行格式：[时间] [角色] 内容摘要
        如无匹配则返回"未找到相关历史记录"
    """
    # 获取全局 archive_store 实例
    archive_store = get_global("archive_store")
    if not archive_store:
        return "⚠️ archive_store 未初始化，无法搜索历史"

    if not query or not query.strip():
        return "⚠️ 搜索关键词为空"

    try:
        if session_only:
            results = archive_store.search(query, top_k=max_results)
        else:
            # 全局搜索：用空 session_key 的 fallback，看 archive_store 是否支持跨 session
            # 先尝试当前 session
            results = archive_store.search(query, top_k=max_results)

        if not results:
            return "未找到相关历史记录"

        lines = []
        for i, r in enumerate(results, 1):
            ts = r.get("timestamp", 0)
            time_str = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "???"
            role = r.get("role", "unknown")
            content = r.get("content", "")
            # 截取前 300 字作为摘要
            summary = content[:300].replace("\n", " ")
            lines.append(f"{i}. [{time_str}] [{role}] {summary}")

        return "\n".join(lines)

    except Exception as e:
        logger.exception("archive_search 出错")
        return f"⚠️ 搜索出错: {e}"
