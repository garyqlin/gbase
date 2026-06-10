"""
send_file 工具 — 将本地文件发送到飞书。

支持所有飞书支持的文件类型（PDF/Word/Excel/PPT/图片等）。
由 LLM 在生成文件后调用，无需人工转存。
"""

import logging
from pathlib import Path

from lib.toolkit import get_global, tool

logger = logging.getLogger(__name__)


@tool()
async def send_file(file_path: str):
    """将本地文件发送到飞书给主人。

    支持的文件类型包括：PDF 文档、Word(.docx) 文档、Excel(.xlsx) 表格、
    PPT(.pptx) 演示文稿、图片、文本文件等所有飞书支持的文件格式。

    Args:
        file_path: 文件绝对路径（建议先生成文件到 /tmp/ 或用户家目录下）
    """
    fp = Path(file_path)
    if not fp.exists():
        return {"ok": False, "error": f"文件不存在: {file_path}"}
    if not fp.is_file():
        return {"ok": False, "error": f"路径不是文件: {file_path}"}

    channel = get_global("feishu_channel")
    if not channel:
        return {"ok": False, "error": "飞书通道未就绪"}

    sender_id = get_global("feishu_sender_id")
    if not sender_id:
        return {"ok": False, "error": "飞书用户未识别"}

    try:
        await channel.send_file(sender_id, file_path)
        return {"ok": True, "result": f"文件已发送到飞书: {fp.name}"}
    except Exception as e:
        logger.exception("send_file 失败")
        return {"ok": False, "error": str(e)}
