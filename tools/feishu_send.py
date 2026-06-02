"""
feishu_send.py — 飞书文件/卡片发送工具
让 GBase Kernel 可以通过 @tool 装饰器调用飞书通道的 send_file / send_card。
"""

import json
import logging

from lib.toolkit import tool

logger = logging.getLogger(__name__)


@tool(
    name="feishu_send_file",
    description="通过飞书发送文件给用户。支持 PDF、MD、TXT、图片等格式。",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "本地文件的绝对路径",
            },
            "description": {
                "type": "string",
                "description": "文件描述，可选。会作为消息上下文传递给内核理解",
            },
        },
        "required": ["file_path"],
    },
)
async def feishu_send_file(file_path: str, description: str = "") -> str:
    """发送文件到飞书"""
    from lib.toolkit import get_global

    channel = get_global("feishu_channel")
    sender_id = get_global("feishu_sender_id")
    message_id = get_global("feishu_message_id")

    if not channel:
        return "错误: feishu_channel 未注入到工具全局上下文"

    if isinstance(file_path, list):
        file_path = file_path[0] if file_path else ""

    import os

    if not os.path.exists(file_path):
        return f"错误: 文件不存在 {file_path}"

    await channel.send_file(sender_id, file_path, message_id=message_id)
    fname = os.path.basename(file_path)
    fsize = os.path.getsize(file_path)
    desc = f" ({description})" if description else ""
    return f"✅ 文件已发送: {fname} ({fsize} bytes){desc}"


@tool(
    name="feishu_send_card",
    description="通过飞书发送卡片消息。发送富文本卡片，支持标题、多行正文、图片、按钮等。",
    parameters={
        "type": "object",
        "properties": {
            "card_data": {
                "type": "string",
                "description": "卡片 JSON 字符串。格式参考飞书卡片 Builder。也可以传 'forward' 将收到的卡片原样转发给 kernel 处理",
            },
        },
        "required": ["card_data"],
    },
)
async def feishu_send_card(card_data: str) -> str:
    """发送飞书卡片消息"""
    from lib.toolkit import get_global

    channel = get_global("feishu_channel")
    sender_id = get_global("feishu_sender_id")
    message_id = get_global("feishu_message_id")

    if not channel:
        return "错误: feishu_channel 未注入到工具全局上下文"

    try:
        card = json.loads(card_data)
    except json.JSONDecodeError:
        return "错误: card_data 不是有效的 JSON"

    await channel.send_card(sender_id, card, message_id=message_id)
    return "✅ 卡片消息已发送"
