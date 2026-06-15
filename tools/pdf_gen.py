# SPDX-License-Identifier: MIT
"""PDF 文档生成工具（v10.0 — 设计升级版：色块系统 + 卡片布局 + 层级优化）

基于 v9.4 改造：
- 新增 section 类型：左竖条色块标识的章节标题
- 新增 highlight 类型：带背景色块的强调段落
- 新增 card 类型：双列卡片布局
- 优化 note 类型：左侧色块标签风格
- 字体层级微调：2:1.5:1.25:1 比例
- 色块系统：主色20% / 辅色10% / 中性色70%
"""

import logging
import os

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from lib.toolkit import tool

logger = logging.getLogger(__name__)

# ============================================================
# 注册中文字体 — 使用系统 STHeiti
# ============================================================
CN_REGULAR = "STHeiti"
CN_BOLD = "STHeiti-Bold"


def _register_fonts():
    regular_path = "/System/Library/Fonts/STHeiti Light.ttc"
    bold_path = "/System/Library/Fonts/STHeiti Medium.ttc"

    if os.path.exists(regular_path):
        pdfmetrics.registerFont(TTFont(CN_REGULAR, regular_path))
    else:
        logger.warning(f"中文字体未找到: {regular_path}")
        return False

    if os.path.exists(bold_path):
        pdfmetrics.registerFont(TTFont(CN_BOLD, bold_path))

    pdfmetrics.registerFontFamily(
        CN_REGULAR,
        normal=CN_REGULAR,
        bold=CN_BOLD,
        italic=CN_REGULAR,
        boldItalic=CN_BOLD,
    )
    return True


_register_fonts()

# ============================================================
# 配色方案 — 所有主题封面统一白底深字
# ============================================================
THEMES = {
    "mckinsey": {
        "primary": "#002D5A",
        "primary_light": "#4A90D9",
        "primary_pale": "#E8F0FE",
        "accent": "#F5A623",
        "cover_bg": "#FFFFFF",
        "cover_text": "#002D5A",
        "cover_sub": "#002D5A",
        "body_text": "#333333",
        "muted_text": "#999999",
        "table_header_bg": "#002D5A",
        "table_header_text": "#FFFFFF",
        "table_row_even": "#FFFFFF",
        "table_row_odd": "#F5F7FA",
        "table_border": "#D0D5DD",
        "section_bar": "#002D5A",
        "section_bg": "#E8F0FE",
        "highlight_bg": "#F0F5FF",
        "card_bg": "#FFFFFF",
        "card_border": "#D0D5DD",
        "tag_bg": "#002D5A",
        "tag_text": "#FFFFFF",
    },
    "bcg": {
        "primary": "#006B54",
        "primary_light": "#00A88F",
        "primary_pale": "#E0F2F1",
        "accent": "#FF8F00",
        "cover_bg": "#FFFFFF",
        "cover_text": "#006B54",
        "cover_sub": "#006B54",
        "body_text": "#333333",
        "muted_text": "#999999",
        "table_header_bg": "#006B54",
        "table_header_text": "#FFFFFF",
        "table_row_even": "#FFFFFF",
        "table_row_odd": "#F2F9F7",
        "table_border": "#C8E6C9",
        "section_bar": "#006B54",
        "section_bg": "#E0F2F1",
        "highlight_bg": "#EDF7F5",
        "card_bg": "#FFFFFF",
        "card_border": "#C8E6C9",
        "tag_bg": "#006B54",
        "tag_text": "#FFFFFF",
    },
    "bain": {
        "primary": "#7A0026",
        "primary_light": "#C62828",
        "primary_pale": "#FFEBEE",
        "accent": "#FF8F00",
        "cover_bg": "#FFFFFF",
        "cover_text": "#7A0026",
        "cover_sub": "#7A0026",
        "body_text": "#333333",
        "muted_text": "#999999",
        "table_header_bg": "#7A0026",
        "table_header_text": "#FFFFFF",
        "table_row_even": "#FFFFFF",
        "table_row_odd": "#FFF5F7",
        "table_border": "#F8BBD0",
        "section_bar": "#7A0026",
        "section_bg": "#FFEBEE",
        "highlight_bg": "#FFF5F7",
        "card_bg": "#FFFFFF",
        "card_border": "#F8BBD0",
        "tag_bg": "#7A0026",
        "tag_text": "#FFFFFF",
    },
    "deloitte": {
        "primary": "#00843D",
        "primary_light": "#00A86B",
        "primary_pale": "#E8F5E9",
        "accent": "#FF6B35",
        "cover_bg": "#FFFFFF",
        "cover_text": "#00843D",
        "cover_sub": "#00843D",
        "body_text": "#333333",
        "muted_text": "#999999",
        "table_header_bg": "#00843D",
        "table_header_text": "#FFFFFF",
        "table_row_even": "#FFFFFF",
        "table_row_odd": "#F1F8E9",
        "table_border": "#C8E6C9",
        "section_bar": "#00843D",
        "section_bg": "#E8F5E9",
        "highlight_bg": "#F1F8E9",
        "card_bg": "#FFFFFF",
        "card_border": "#C8E6C9",
        "tag_bg": "#00843D",
        "tag_text": "#FFFFFF",
    },
    "pwc": {
        "primary": "#2C3E50",
        "primary_light": "#5D7B93",
        "primary_pale": "#EBF0F5",
        "accent": "#E67E22",
        "cover_bg": "#FFFFFF",
        "cover_text": "#2C3E50",
        "cover_sub": "#2C3E50",
        "body_text": "#333333",
        "muted_text": "#999999",
        "table_header_bg": "#2C3E50",
        "table_header_text": "#FFFFFF",
        "table_row_even": "#FFFFFF",
        "table_row_odd": "#F5F8FA",
        "table_border": "#D0D5DD",
        "section_bar": "#E67E22",
        "section_bg": "#FFF3E0",
        "highlight_bg": "#FEF8F0",
        "card_bg": "#FFFFFF",
        "card_border": "#D0D5DD",
        "tag_bg": "#E67E22",
        "tag_text": "#FFFFFF",
    },
    "ey": {
        "primary": "#212121",
        "primary_light": "#616161",
        "primary_pale": "#F5F5F5",
        "accent": "#FFE600",
        "cover_bg": "#FFFFFF",
        "cover_text": "#212121",
        "cover_sub": "#212121",
        "body_text": "#333333",
        "muted_text": "#999999",
        "table_header_bg": "#212121",
        "table_header_text": "#FFFFFF",
        "table_row_even": "#FFFFFF",
        "table_row_odd": "#F5F5F5",
        "table_border": "#E0E0E0",
        "section_bar": "#FFE600",
        "section_bg": "#FFFDE7",
        "highlight_bg": "#FAFAFA",
        "card_bg": "#FFFFFF",
        "card_border": "#E0E0E0",
        "tag_bg": "#212121",
        "tag_text": "#FFFFFF",
    },
    "kpmg": {
        "primary": "#00338D",
        "primary_light": "#5B9BD5",
        "primary_pale": "#E3F2FD",
        "accent": "#00A3E0",
        "cover_bg": "#FFFFFF",
        "cover_text": "#00338D",
        "cover_sub": "#00338D",
        "body_text": "#333333",
        "muted_text": "#999999",
        "table_header_bg": "#00338D",
        "table_header_text": "#FFFFFF",
        "table_row_even": "#FFFFFF",
        "table_row_odd": "#F0F7FF",
        "table_border": "#BBDEFB",
        "section_bar": "#00338D",
        "section_bg": "#E3F2FD",
        "highlight_bg": "#F0F7FF",
        "card_bg": "#FFFFFF",
        "card_border": "#BBDEFB",
        "tag_bg": "#00338D",
        "tag_text": "#FFFFFF",
    },
    "blue": {
        "primary": "#1A3A5C",
        "primary_light": "#4A90D9",
        "primary_pale": "#E8F0FE",
        "accent": "#E67E22",
        "cover_bg": "#FFFFFF",
        "cover_text": "#1A3A5C",
        "cover_sub": "#1A3A5C",
        "body_text": "#333333",
        "muted_text": "#999999",
        "table_header_bg": "#1A3A5C",
        "table_header_text": "#FFFFFF",
        "table_row_even": "#FFFFFF",
        "table_row_odd": "#F5F7FA",
        "table_border": "#D0D5DD",
        "section_bar": "#1A3A5C",
        "section_bg": "#E8F0FE",
        "highlight_bg": "#F0F5FF",
        "card_bg": "#FFFFFF",
        "card_border": "#D0D5DD",
        "tag_bg": "#1A3A5C",
        "tag_text": "#FFFFFF",
    },
    "gray": {
        "primary": "#2D2D2D",
        "primary_light": "#666666",
        "primary_pale": "#F5F5F5",
        "accent": "#E67E22",
        "cover_bg": "#FFFFFF",
        "cover_text": "#2D2D2D",
        "cover_sub": "#2D2D2D",
        "body_text": "#333333",
        "muted_text": "#999999",
        "table_header_bg": "#2D2D2D",
        "table_header_text": "#FFFFFF",
        "table_row_even": "#FFFFFF",
        "table_row_odd": "#F5F5F5",
        "table_border": "#D0D5DD",
        "section_bar": "#E67E22",
        "section_bg": "#FEF5E7",
        "highlight_bg": "#F8F8F8",
        "card_bg": "#FFFFFF",
        "card_border": "#D0D5DD",
        "tag_bg": "#2D2D2D",
        "tag_text": "#FFFFFF",
    },
}

DEFAULT_THEME = "blue"


def hex_color(h):
    return HexColor(h) if isinstance(h, str) and h.startswith("#") else h


def build_styles(theme_colors, font_size="normal"):
    c = {k: hex_color(v) for k, v in theme_colors.items()}

    # 字体层级比例 2:1.5:1.25:1:0.85:0.75
    if font_size == "small":
        sizes = {
            "cover_title": 28,
            "cover_subtitle": 14,
            "h1": 20,
            "h2": 15,
            "h3": 13,
            "body": 9,
            "small": 8,
            "caption": 7,
        }
    elif font_size == "large":
        sizes = {
            "cover_title": 40,
            "cover_subtitle": 20,
            "h1": 26,
            "h2": 20,
            "h3": 17,
            "body": 12,
            "small": 10,
            "caption": 9,
        }
    else:
        sizes = {
            "cover_title": 32,
            "cover_subtitle": 16,
            "h1": 24,
            "h2": 18,
            "h3": 15,
            "body": 10.5,
            "small": 9,
            "caption": 8,
        }

    return {
        "cover_title": ParagraphStyle(
            "CoverTitle",
            fontName=CN_BOLD,
            fontSize=sizes["cover_title"],
            textColor=c["cover_text"],
            alignment=TA_LEFT,
            leading=sizes["cover_title"] * 1.3,
            spaceAfter=12,
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            fontName=CN_REGULAR,
            fontSize=sizes["cover_subtitle"],
            textColor=c["cover_sub"],
            alignment=TA_LEFT,
            leading=sizes["cover_subtitle"] * 1.5,
        ),
        "h1": ParagraphStyle(
            "H1",
            fontName=CN_BOLD,
            fontSize=sizes["h1"],
            textColor=c["primary"],
            alignment=TA_LEFT,
            leading=sizes["h1"] * 1.3,
            spaceBefore=24,
            spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "H2",
            fontName=CN_BOLD,
            fontSize=sizes["h2"],
            textColor=c["primary"],
            alignment=TA_LEFT,
            leading=sizes["h2"] * 1.3,
            spaceBefore=18,
            spaceAfter=10,
        ),
        "h3": ParagraphStyle(
            "H3",
            fontName=CN_BOLD,
            fontSize=sizes["h3"],
            textColor=c["body_text"],
            alignment=TA_LEFT,
            leading=sizes["h3"] * 1.4,
            spaceBefore=14,
            spaceAfter=8,
        ),
        "body": ParagraphStyle(
            "Body",
            fontName=CN_REGULAR,
            fontSize=sizes["body"],
            textColor=c["body_text"],
            alignment=TA_JUSTIFY,
            leading=sizes["body"] * 1.7,
            spaceBefore=3,
            spaceAfter=6,
        ),
        "body_left": ParagraphStyle(
            "BodyLeft",
            fontName=CN_REGULAR,
            fontSize=sizes["body"],
            textColor=c["body_text"],
            alignment=TA_LEFT,
            leading=sizes["body"] * 1.7,
            spaceBefore=3,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "Small",
            fontName=CN_REGULAR,
            fontSize=sizes["small"],
            textColor=c["muted_text"],
            alignment=TA_LEFT,
            leading=sizes["small"] * 1.5,
        ),
        "caption": ParagraphStyle(
            "Caption",
            fontName=CN_REGULAR,
            fontSize=sizes["caption"],
            textColor=c["muted_text"],
            alignment=TA_LEFT,
            leading=sizes["caption"] * 1.4,
        ),
        "table_cell": ParagraphStyle(
            "TableCell",
            fontName=CN_REGULAR,
            fontSize=sizes["body"],
            textColor=c["body_text"],
            alignment=TA_LEFT,
            leading=sizes["body"] * 1.4,
        ),
        "table_header": ParagraphStyle(
            "TableHeader",
            fontName=CN_BOLD,
            fontSize=sizes["body"],
            textColor=c["table_header_text"],
            alignment=TA_LEFT,
            leading=sizes["body"] * 1.4,
        ),
        "quote": ParagraphStyle(
            "Quote",
            fontName=CN_REGULAR,
            fontSize=sizes["body"],
            textColor=c["body_text"],
            alignment=TA_LEFT,
            leading=sizes["body"] * 1.7,
            leftIndent=18,
            rightIndent=18,
            spaceBefore=10,
            spaceAfter=10,
        ),
        "note": ParagraphStyle(
            "Note",
            fontName=CN_REGULAR,
            fontSize=sizes["small"],
            textColor=c["muted_text"],
            alignment=TA_LEFT,
            leading=sizes["small"] * 1.5,
            leftIndent=12,
        ),
        "cover_info": ParagraphStyle(
            "CoverInfo", fontName=CN_REGULAR, fontSize=10, textColor=c["cover_text"], alignment=TA_LEFT, leading=16
        ),
        # 新增：色块标签内文字
        "tag": ParagraphStyle(
            "Tag",
            fontName=CN_BOLD,
            fontSize=sizes["small"],
            textColor=c["tag_text"],
            alignment=TA_CENTER,
            leading=sizes["small"] * 1.4,
        ),
        # 新增：卡片标题
        "card_title": ParagraphStyle(
            "CardTitle",
            fontName=CN_BOLD,
            fontSize=sizes["h3"],
            textColor=c["primary"],
            alignment=TA_LEFT,
            leading=sizes["h3"] * 1.3,
            spaceBefore=4,
            spaceAfter=6,
        ),
        # 新增：高亮段落
        "highlight": ParagraphStyle(
            "Highlight",
            fontName=CN_REGULAR,
            fontSize=sizes["body"],
            textColor=c["body_text"],
            alignment=TA_LEFT,
            leading=sizes["body"] * 1.7,
            spaceBefore=6,
            spaceAfter=6,
        ),
    }


def build_cover_elements(styles, theme_colors, title, subtitle="", date="", company=""):
    elements = []
    c = {k: hex_color(v) for k, v in theme_colors.items()}

    elements.append(Spacer(1, 120))
    elements.append(Paragraph(title, styles["cover_title"]))
    elements.append(Spacer(1, 10))

    from reportlab.platypus import HRFlowable

    elements.append(HRFlowable(width="30%", thickness=3, color=c["cover_text"], spaceAfter=20, spaceBefore=0))

    if subtitle:
        elements.append(Paragraph(subtitle, styles["cover_subtitle"]))
        elements.append(Spacer(1, 8))

    elements.append(Spacer(1, 180))

    if date:
        elements.append(Paragraph(date, styles["cover_info"]))
    if company:
        elements.append(Paragraph(company, styles["cover_info"]))

    return elements


def build_table(styles, theme_colors, headers, rows, col_widths=None):
    c = {k: hex_color(v) for k, v in theme_colors.items()}
    header_style = styles["table_header"]
    cell_style = styles["table_cell"]

    table_data = [[Paragraph(h, header_style) for h in headers]]
    for row in rows:
        table_data.append([Paragraph(str(cell), cell_style) for cell in row])

    if col_widths is None:
        page_width = A4[0] - 5 * cm
        col_widths = [page_width / len(headers)] * len(headers)

    t = Table(table_data, colWidths=col_widths, repeatRows=1)

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), c["table_header_bg"]),
        ("TEXTCOLOR", (0, 0), (-1, 0), c["table_header_text"]),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, c["table_border"]),
        ("LINEBELOW", (0, 0), (-1, 0), 0, c["table_header_bg"]),
    ]

    for i in range(1, len(table_data)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), c["table_row_odd"]))
        else:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), c["table_row_even"]))

    t.setStyle(TableStyle(style_cmds))
    return t


def build_note_box(_styles, theme_colors, text, box_type="info"):
    """优化版：左侧色块标签 + 白底正文（标签风格）"""
    c = {k: hex_color(v) for k, v in theme_colors.items()}

    if box_type == "info":
        tag_bg = c["primary"]
        tag_text_color = c["tag_text"]
        label = "信息"
        body_bg = c["primary_pale"]
    elif box_type == "warning":
        tag_bg = HexColor("#FF9800")
        tag_text_color = HexColor("#FFFFFF")
        label = "注意"
        body_bg = HexColor("#FFF3E0")
    elif box_type == "success":
        tag_bg = HexColor("#4CAF50")
        tag_text_color = HexColor("#FFFFFF")
        label = "结论"
        body_bg = HexColor("#E8F5E9")
    else:
        tag_bg = c["primary"]
        tag_text_color = c["tag_text"]
        label = "信息"
        body_bg = c["primary_pale"]

    tag_style = ParagraphStyle(
        "TagLabel", fontName=CN_BOLD, fontSize=8, textColor=tag_text_color, alignment=TA_CENTER, leading=12
    )
    body_para = ParagraphStyle(
        "NoteBody",
        fontName=CN_REGULAR,
        fontSize=9,
        textColor=c["body_text"],
        alignment=TA_LEFT,
        leading=14,
        leftIndent=6,
    )

    # 左侧色块标签（窄列）+ 右侧正文（宽列）
    page_width = A4[0] - 5 * cm
    tag_col_width = 1.2 * cm
    body_col_width = page_width - tag_col_width - 0.3 * cm

    tag_cell = Table([[Paragraph(label, tag_style)]], colWidths=[tag_col_width], rowHeights=[28])
    tag_cell.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), tag_bg),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    body_cell = Table([[Paragraph(text, body_para)]], colWidths=[body_col_width])
    body_cell.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), body_bg),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    outer = Table([[tag_cell, body_cell]], colWidths=[tag_col_width, body_col_width])
    outer.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "STRETCH"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return outer


def build_section(styles, theme_colors, title, number=""):
    """带左竖条色块标识的章节标题"""
    c = {k: hex_color(v) for k, v in theme_colors.items()}

    page_width = A4[0] - 5 * cm
    bar_width = 4 * mm
    text_width = page_width - bar_width - 2 * mm

    # 左竖条
    bar = Table([[""]], colWidths=[bar_width], rowHeights=[24])
    bar.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), c["section_bar"]),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    # 标题文字
    prefix = f"<b>{number}</b> · " if number else ""
    title_para = Paragraph(f"{prefix}{title}", styles["h2"])

    title_cell = Table([[title_para]], colWidths=[text_width])
    title_cell.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), c["section_bg"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    outer = Table([[bar, title_cell]], colWidths=[bar_width, text_width])
    outer.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return outer


def build_highlight(styles, theme_colors, text, icon=""):
    """带背景色块的强调段落"""
    c = {k: hex_color(v) for k, v in theme_colors.items()}

    page_width = A4[0] - 5 * cm
    icon_prefix = f"{icon} " if icon else ""

    para = Paragraph(f"{icon_prefix}{text}", styles["highlight"])

    t = Table([[para]], colWidths=[page_width])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), c["highlight_bg"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LINELEFT", (0, 0), (-1, -1), 3, c["primary"]),
            ]
        )
    )
    return t


def build_card(_styles, theme_colors, cards, columns=2):
    """卡片布局：双列或三列卡片"""
    c = {k: hex_color(v) for k, v in theme_colors.items()}

    page_width = A4[0] - 5 * cm
    gap = 4 * mm
    col_count = min(columns, len(cards))
    col_width = (page_width - gap * (col_count - 1)) / col_count

    # 构建行数据
    row_data = []
    card_idx = 0
    while card_idx < len(cards):
        row_cells = []
        for _ in range(col_count):
            if card_idx < len(cards):
                card = cards[card_idx]
                card_title = card.get("title", "")
                card_body = card.get("body", "")
                card_icon = card.get("icon", "")

                # 卡片内容
                icon_prefix = f"{card_icon} " if card_icon else ""
                title_para = Paragraph(
                    f"{icon_prefix}<b>{card_title}</b>",
                    ParagraphStyle(
                        "CardTitle2",
                        fontName=CN_BOLD,
                        fontSize=10,
                        textColor=c["primary"],
                        alignment=TA_LEFT,
                        leading=14,
                        spaceAfter=4,
                    ),
                )
                body_para = Paragraph(
                    card_body,
                    ParagraphStyle(
                        "CardBody",
                        fontName=CN_REGULAR,
                        fontSize=8.5,
                        textColor=c["body_text"],
                        alignment=TA_LEFT,
                        leading=13,
                    ),
                )

                cell_content = [title_para, Spacer(1, 2), body_para]
                cell = Table([[c] for c in cell_content], colWidths=[col_width - 1.2 * cm])
                cell.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), c["card_bg"]),
                            ("BOX", (0, 0), (-1, -1), 0.5, c["card_border"]),
                            ("LEFTPADDING", (0, 0), (-1, -1), 10),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                            ("TOPPADDING", (0, 0), (-1, -1), 10),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                        ]
                    )
                )
                row_cells.append(cell)
                card_idx += 1
            else:
                row_cells.append(Table([[""]], colWidths=[col_width], rowHeights=[1]))

        row = Table([row_cells], colWidths=[col_width] * col_count)
        row.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        row_data.append(row)
        row_data.append(Spacer(1, 4 * mm))

    # 去掉最后一个 spacer
    if row_data and isinstance(row_data[-1], Spacer):
        row_data = row_data[:-1]

    return row_data


def build_list(styles, theme_colors, items, ordered=False):
    body_style = styles["body_left"]
    c = {k: hex_color(v) for k, v in theme_colors.items()}

    list_items = []
    for i, item in enumerate(items):
        prefix = f"<b>{i + 1}.</b> " if ordered else "• "

        p = Paragraph(f"{prefix}{item}", body_style)
        list_items.append(ListItem(p, leftIndent=24, value=i + 1 if ordered else None))

    return ListFlowable(
        list_items,
        bulletType="bullet" if not ordered else "1",
        start="1" if ordered else None,
        leftIndent=12,
        bulletFontSize=8,
        bulletColor=c["primary"],
    )


def header_footer(canvas, doc):
    canvas.saveState()

    theme_name = getattr(doc, "_theme_name", DEFAULT_THEME)
    theme = THEMES.get(theme_name, THEMES[DEFAULT_THEME])
    c = {k: hex_color(v) for k, v in theme.items()}

    page_width = A4[0]
    page_height = A4[1]

    # 页眉线
    canvas.setStrokeColor(c["primary"])
    canvas.setLineWidth(0.5)
    canvas.line(2 * cm, page_height - 1.8 * cm, page_width - 2 * cm, page_height - 1.8 * cm)

    # 页码
    canvas.setFont(CN_REGULAR, 8)
    canvas.setFillColor(c["muted_text"])
    canvas.drawRightString(page_width - 2 * cm, 1.5 * cm, f"- {doc.page} -")

    canvas.restoreState()


@tool()
def gen_pdf(
    output_path: str,
    title: str,
    content: list,
    theme: str = "blue",
    font_size: str = "normal",
    subtitle: str = "",
    date: str = "",
    company: str = "",
):
    """生成专业 PDF 报告（大厂咨询报告风格，支持中文）

    v10.0 新增类型:
      - section: 带左竖条色块标识的章节标题。参数: text, number(可选)
      - highlight: 带背景色块+左竖线的强调段落。参数: text, icon(可选)
      - card: 卡片布局。参数: cards=[{title, body, icon}], columns=2/3
      - note 优化: 左侧色块标签 + 白底正文

    Args:
        output_path: 输出 PDF 路径（必须 .pdf 结尾）
        title: 报告标题（封面用）
        content: 内容结构，每项为 {"type": "...", ...}
            type 支持: h1/h2/h3/body/body_left/table/list/note/quote/
                       pagebreak/spacer/section/highlight/card
        theme: 配色主题（mckinsey/bcg/bain/deloitte/pwc/ey/kpmg/blue/gray）
        font_size: 字号（small/normal/large）
        subtitle: 封面副标题
        date: 封面日期
        company: 封面公司名
    """
    if not output_path.endswith(".pdf"):
        output_path += ".pdf"

    theme = theme.lower()
    if theme not in THEMES:
        theme = DEFAULT_THEME

    theme_colors = THEMES[theme]
    styles = build_styles(theme_colors, font_size)
    c = {k: hex_color(v) for k, v in theme_colors.items()}

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        topMargin=3 * cm,
        bottomMargin=2.5 * cm,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
    )
    doc._theme_name = theme

    elements = []

    cover_elements = build_cover_elements(styles, theme_colors, title, subtitle, date, company)
    elements.extend(cover_elements)
    elements.append(PageBreak())

    for item in content:
        item_type = item.get("type", "body")

        if item_type == "h1":
            elements.append(Paragraph(item["text"], styles["h1"]))
        elif item_type == "h2":
            elements.append(Paragraph(item["text"], styles["h2"]))
        elif item_type == "h3":
            elements.append(Paragraph(item["text"], styles["h3"]))
        elif item_type == "body":
            elements.append(Paragraph(item["text"], styles["body"]))
        elif item_type == "body_left":
            elements.append(Paragraph(item["text"], styles["body_left"]))

        elif item_type == "section":
            section = build_section(styles, theme_colors, title=item.get("text", ""), number=item.get("number", ""))
            elements.append(section)
            elements.append(Spacer(1, 6))

        elif item_type == "highlight":
            hl = build_highlight(styles, theme_colors, text=item.get("text", ""), icon=item.get("icon", ""))
            elements.append(hl)
            elements.append(Spacer(1, 8))

        elif item_type == "card":
            cards = item.get("cards", [])
            columns = item.get("columns", 2)
            if cards:
                card_elements = build_card(styles, theme_colors, cards, columns)
                elements.extend(card_elements)
                elements.append(Spacer(1, 8))

        elif item_type == "table":
            headers = item.get("headers", [])
            rows = item.get("rows", [])
            col_widths = item.get("col_widths", None)
            if headers and rows:
                t = build_table(styles, theme_colors, headers, rows, col_widths)
                elements.append(t)
                elements.append(Spacer(1, 10))

        elif item_type == "list":
            items = item.get("items", [])
            ordered = item.get("ordered", False)
            if items:
                elements.append(build_list(styles, theme_colors, items, ordered))
                elements.append(Spacer(1, 8))

        elif item_type == "note":
            text = item.get("text", "")
            box_type = item.get("box_type", "info")
            if text:
                elements.append(build_note_box(styles, theme_colors, text, box_type))
                elements.append(Spacer(1, 8))

        elif item_type == "quote":
            text = item.get("text", "")
            if text:
                from reportlab.platypus import Table as _Table

                quote_p = Paragraph(text, styles["quote"])
                quote_table = _Table([[quote_p]], colWidths=[A4[0] - 5 * cm])
                quote_table.setStyle(
                    TableStyle(
                        [
                            ("LEFTPADDING", (0, 0), (-1, -1), 18),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                            ("TOPPADDING", (0, 0), (-1, -1), 10),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                            ("BACKGROUND", (0, 0), (-1, -1), c["primary_pale"]),
                            ("LINEBEFORE", (0, 0), (-1, -1), 3, c["primary"]),
                        ]
                    )
                )
                elements.append(quote_table)
                elements.append(Spacer(1, 8))

        elif item_type == "pagebreak":
            elements.append(PageBreak())
        elif item_type == "spacer":
            height = item.get("height", 12)
            elements.append(Spacer(1, height))

    doc.build(elements, onFirstPage=header_footer, onLaterPages=header_footer)
    return f"PDF 已生成: {output_path}"
