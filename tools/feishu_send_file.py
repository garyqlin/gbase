# SPDX-License-Identifier: MIT
"""
飞书文件发送工具。
通过 toolkit 全局上下文中的 "feishu_channel" 实现。
支持发送本地文件、网络文件、或自动生成的文件。

类型支持：PDF / Word / Excel / 图片 / 文本 / CSV / JSON / Markdown 等
"""

import logging
from pathlib import Path

import httpx

from lib.toolkit import get_global, tool

logger = logging.getLogger(__name__)


@tool(
    name="feishu_send_file",
    description="发送文件到飞书对话。支持本地文件、网络文件URL、或自动生成的文件（PDF/Word/Excel/图片等）。"
    "file_path: 本地文件路径（绝对路径）\n"
    "url: 网络文件URL（从URL下载后发送，与file_path二选一）\n"
    "file_type: 文件类型提示（可选，用于自动生成时指定类型：pdf/docx/xlsx/png/jpg/txt/csv/json/md）",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "本地文件路径（绝对路径），与 url 二选一"},
            "url": {"type": "string", "description": "网络文件 URL，从 URL 下载后发送，与 file_path 二选一"},
            "file_type": {
                "type": "string",
                "description": "文件类型提示（可选）：pdf / docx / xlsx / png / jpg / txt / csv / json / md",
                "enum": ["pdf", "docx", "xlsx", "png", "jpg", "txt", "csv", "json", "md"],
            },
        },
        "required": [],
    },
)
async def feishu_send_file(file_path: str = "", url: str = "", file_type: str = ""):
    """发送文件到飞书对话。"""
    channel = get_global("feishu_channel")
    if not channel:
        return {"error": "飞书通道未初始化"}

    open_id = get_global("feishu_sender_id", "")
    message_id = get_global("feishu_message_id", "")

    if not open_id and not message_id:
        return {"error": "未找到用户身份"}

    resolved_path = None

    # 情况1：提供了本地路径
    if file_path:
        p = Path(file_path)
        if not p.exists():
            return {"error": f"文件不存在: {file_path}"}
        resolved_path = p

    # 情况2：提供了网络 URL
    elif url:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return {"error": f"下载失败: HTTP {resp.status_code}"}

                # 自动推断扩展名
                ct = resp.headers.get("content-type", "")
                ext_map = {
                    "application/pdf": ".pdf",
                    "text/markdown": ".md",
                    "text/plain": ".txt",
                    "application/json": ".json",
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                    "image/gif": ".gif",
                    "image/webp": ".webp",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                    "text/csv": ".csv",
                    "application/zip": ".zip",
                }
                ext = ext_map.get(ct, "")
                if file_type:
                    ext = f".{file_type}"

                # 从 URL 取文件名
                from urllib.parse import unquote, urlparse

                path_part = urlparse(url).path
                orig_name = Path(unquote(path_part)).name or f"download{ext}"
                if not orig_name.endswith(ext):
                    orig_name = f"{orig_name}{ext}"

                save_dir = Path("data/downloaded_files")
                save_dir.mkdir(parents=True, exist_ok=True)
                save_path = save_dir / orig_name
                save_path.write_bytes(resp.content)
                resolved_path = save_path
                logger.info("网络文件下载成功: %s (%d bytes)", save_path, len(resp.content))
        except Exception as e:
            return {"error": f"下载失败: {e}"}

    else:
        return {"error": "请提供 file_path（本地路径）或 url（网络URL）"}

    if not resolved_path:
        return {"error": "无法解析文件路径"}

    # 发送文件
    try:
        # 判断是否为图片（飞书图片消息和文件消息不同）
        img_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
        is_image = resolved_path.suffix.lower() in img_exts

        if is_image:
            # 图片用 image 消息类型
            await channel.send_file(open_id, str(resolved_path), message_id=message_id)
        else:
            # 文件用 file 消息类型
            await channel.send_file(open_id, str(resolved_path), message_id=message_id)

        size_kb = resolved_path.stat().st_size / 1024
        return {
            "result": f"文件已发送: {resolved_path.name} ({size_kb:.1f} KB)",
            "file": resolved_path.name,
            "size_kb": round(size_kb, 1),
        }
    except Exception as e:
        logger.error("文件发送异常: %s", e, exc_info=True)
        return {"error": f"文件发送失败: {e}"}
