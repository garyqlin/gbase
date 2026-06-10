"""
send_file 工具 — 将本地文件发送到飞书。

支持所有飞书支持的文件类型（PDF/Word/Excel/PPT/图片等）。
由 LLM 在生成文件后调用，无需人工转存。
"""

import logging
from pathlib import Path

from lib.toolkit import tool, get_global

logger = logging.getLogger(__name__)


@tool()
async def send_file(file_path: str, title: str = ""):
    """将本地文件发送到飞书给主人。

    支持的文件类型包括：PDF 文档、Word(.docx) 文档、Excel(.xlsx) 表格、
    PPT(.pptx) 演示文稿、图片、文本文件等所有飞书支持的文件格式。

    Args:
        file_path: 文件绝对路径（建议先生成文件到 /tmp/ 或用户家目录下）
        title: 发送时的显示名称（可选，默认用文件名）
    """
    fp = Path(file_path)
    if not fp.exists():
        return {"ok": False, "error": f"文件不存在: {file_path}"}
    if not fp.is_file():
        return {"ok": False, "error": f"路径不是文件: {file_path}"}

    channel = get_global("feishu_channel")
    if not channel:
        return {"ok": False, "error": "飞书通道未初始化，无法发送文件"}

    sender_id = get_global("feishu_sender_id", "")
    message_id = get_global("feishu_message_id", "")

    if not sender_id and not message_id:
        return {"ok": False, "error": "缺少发送目标的 open_id（当前会话无飞书上下文），请在飞书对话中调用此工具"}

    try:
        result = await channel.send_file(
            open_id=sender_id,
            file_path=str(fp),
            message_id=message_id if message_id else "",
        )
        if isinstance(result, dict) and result.get("ok"):
            return {"ok": True, "result": f"文件已发送: {fp.name}", "path": str(fp)}
        err = result.get("error", "未知错误") if isinstance(result, dict) else str(result)
        return {"ok": False, "error": f"文件发送失败: {err}"}
    except Exception as e:
        logger.error("send_file 异常: %s", e, exc_info=True)
        return {"ok": False, "error": f"send_file 异常: {str(e)}"}
