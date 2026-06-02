# SPDX-License-Identifier: MIT
"""
飞书卡片发送工具。
通过 toolkit 全局上下文中的 "feishu_channel" 实现。
"""

import logging

from lib.toolkit import get_global, tool

logger = logging.getLogger(__name__)


@tool(
    name="send_card",
    description="发送飞书卡片消息给用户。只接受 title, content, note, template 这四个参数。不要传入 sections/body/color。content 是正文（支持markdown），title 是标题（纯文本）。",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "卡片标题，纯文本"},
            "content": {"type": "string", "description": "卡片正文内容，支持 markdown 格式（**粗体**、- 列表等）"},
            "note": {"type": "string", "description": "卡片底部备注文字（可选）"},
            "template": {
                "type": "string",
                "description": "卡片主题色：blue/wathet/turquoise/green/yellow/orange/red/carmine/violet/purple/indigo/grey/default",
                "enum": [
                    "blue",
                    "wathet",
                    "turquoise",
                    "green",
                    "yellow",
                    "orange",
                    "red",
                    "carmine",
                    "violet",
                    "purple",
                    "indigo",
                    "grey",
                    "default",
                ],
            },
        },
        "required": ["title", "content"],
    },
)
async def send_card(
    title: str,
    content: str = "",
    note: str = "",
    template: str = "indigo",
    body: str = "",
    sections: str = "",
    color: str = "",
):
    # Parameter alias support: body -> content, sections -> content, color -> template
    if content == "" and body:
        content = body
    if content == "" and sections:
        content = sections
    if color:
        template = color
    """发送飞书卡片消息。
    
    自动获取当前用户的 open_id（从全局上下文的 feishu_sender_id）。
    """
    channel = get_global("feishu_channel")
    if not channel:
        return {"error": "飞书通道未初始化"}

    open_id = get_global("feishu_sender_id", "")
    message_id = get_global("feishu_message_id", "")

    if not open_id and not message_id:
        return {"error": "未找到用户身份"}

    # 构建卡片
    elements = []

    # 正文
    if content:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})

    # 备注
    if note:
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": note}]})

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title[:200]},
            "template": template if template else "indigo",
        },
        "elements": elements if elements else [{"tag": "div", "text": {"tag": "plain_text", "content": " "}}],
    }

    await channel.send_card(open_id, card, message_id=message_id)
    return {"result": f"卡片已发送: {title[:40]}"}
