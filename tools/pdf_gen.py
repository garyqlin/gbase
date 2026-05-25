# SPDX-License-Identifier: MIT
"""PDF document generator"""

import json
import logging
import os
import subprocess

from lib.toolkit import tool

logger = logging.getLogger(__name__)


@tool()
async def gen_pdf(title: str = "Document", content: list = None, output_path: str = "") -> dict:
    """
    Generate PDF file (using reportlab).

    Args:
        title: Document title
        content: Content structure, each item is {"type": "h1|h2|p|table|image|pagebreak", "text": "...", ...}
                  - h1/h2: Heading level
                  - p: Paragraph
                  - table: {"type": "table", "headers": ["Col1"], "rows": [["Data"]]}
                  - image: {"type": "image", "path": "/path/to/img.png", "width": 400}
                  - pagebreak: Page break
        output_path: Output path

    Returns:
        {"path": "...", "size": 12345}
    """
    if content is None:
        content = [{"type": "p", "text": "(Empty document)"}]

    if not output_path:
        output_path = os.path.expanduser(f"~/Downloads/{title}.pdf")

    script = f"""import json
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Register CJK fonts (try multiple paths)
font_registered = False
for font_path in [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
]:
    try:
        pdfmetrics.registerFont(TTFont("CJK", font_path))
        font_registered = True
        break
    except Exception:
        pass

doc = SimpleDocTemplate(
    {json.dumps(output_path)},
    pagesize=A4,
    topMargin=2*cm, bottomMargin=2*cm,
    leftMargin=2.5*cm, rightMargin=2.5*cm
)

styles = getSampleStyleSheet()
font_name = "CJK" if font_registered else "Helvetica"

style_h1 = ParagraphStyle("H1C", fontName=font_name, fontSize=22, leading=30, spaceAfter=12, spaceBefore=6)
style_h2 = ParagraphStyle("H2C", fontName=font_name, fontSize=16, leading=22, spaceAfter=8, spaceBefore=4)
style_p = ParagraphStyle("PC", fontName=font_name, fontSize=11, leading=17, spaceAfter=6)

story = []
content = {json.dumps(content)}

for item in content:
    t = item.get("type", "p")
    if t == "h1":
        story.append(Paragraph(item["text"], style_h1))
    elif t == "h2":
        story.append(Paragraph(item["text"], style_h2))
    elif t == "p":
        story.append(Paragraph(item["text"], style_p))
    elif t == "table":
        headers = item.get("headers", [])
        rows = item.get("rows", [])
        data = [headers] + rows if headers else rows
        t = Table(data, repeatRows=1 if headers else 0)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0) if headers else (0,0), HexColor("#4472C4") if headers else HexColor("#FFFFFF")),
            ("TEXTCOLOR", (0,0), (-1,0) if headers else (-1,-1), HexColor("#FFFFFF") if headers else HexColor("#000000")),
            ("FONTNAME", (0,0), (-1,-1), font_name),
            ("FONTSIZE", (0,0), (-1,-1), 9),
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("GRID", (0,0), (-1,-1), 0.5, HexColor("#CCCCCC")),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ]))
        story.append(t)
        story.append(Spacer(1, 6))
    elif t == "image":
        path = item.get("path", "")
        width = item.get("width", 400)
        if path and os.path.exists(path):
            story.append(Image(path, width=width, height=width*0.75))
            story.append(Spacer(1, 6))
    elif t == "pagebreak":
        story.append(PageBreak())

doc.build(story)
print("OK:" + {json.dumps(output_path)})
"""

    result = subprocess.run(["python3", "-c", script], capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        return {"error": result.stderr.strip() or result.stdout.strip()}

    if os.path.isfile(output_path):
        size = os.path.getsize(output_path)
        return {"path": output_path, "size": size}
    return {"error": "File not generated"}
