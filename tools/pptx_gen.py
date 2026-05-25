# SPDX-License-Identifier: MIT
"""PPT presentation generator"""

import json
import logging
import os
import subprocess

from lib.toolkit import tool

logger = logging.getLogger(__name__)


@tool()
async def gen_pptx(title: str = "Presentation", slides: list = None, output_path: str = "") -> dict:
    """
    Generate PowerPoint (.pptx) file.

    Args:
        title: File name (without path)
        slides: List of slides, each as {"title": "Cover", "content": ["First line","Second line"], "layout": "title|content|two"}
                layout: title=title slide, content=content slide(default), two=two columns, blank=blank
        output_path: Output path

    Returns:
        {"path": "...", "size": 12345, "slides": 3}
    """
    if not slides:
        slides = [{"title": title, "content": ["Welcome"], "layout": "title"}]

    if not output_path:
        output_path = os.path.expanduser(f"~/Downloads/{title}.pptx")

    script = f"""import json
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)

slides = {json.dumps(slides)}

for sdata in slides:
    layout_name = sdata.get("layout", "content")
    if layout_name == "title":
        layout = prs.slide_layouts[6]  # blank
        slide = prs.slides.add_slide(layout)
        # Title centered large
        txBox = slide.shapes.add_textbox(Inches(1), Inches(2.5), Inches(11.333), Inches(2))
        tf = txBox.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = sdata.get("title", "")
        p.font.size = Pt(44)
        p.font.bold = True
        p.font.color.rgb = RGBColor(0x1A, 0x1A, 0x2E)
        p.alignment = PP_ALIGN.CENTER

        content = sdata.get("content", [])
        if content:
            txBox2 = slide.shapes.add_textbox(Inches(1), Inches(4.5), Inches(11.333), Inches(2))
            tf2 = txBox2.text_frame
            tf2.word_wrap = True
            for ci, line in enumerate(content):
                if ci == 0:
                    p2 = tf2.paragraphs[0]
                else:
                    p2 = tf2.add_paragraph()
                p2.text = str(line)
                p2.font.size = Pt(24)
                p2.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
                p2.alignment = PP_ALIGN.CENTER
    else:
        # Content slide
        if layout_name == "blank":
            layout = prs.slide_layouts[6]
        else:
            layout = prs.slide_layouts[1]  # title and content
        slide = prs.slides.add_slide(layout)

        # Title
        title_shape = slide.shapes.title
        if title_shape:
            title_shape.text = sdata.get("title", "")

        content = sdata.get("content", [])
        if content:
            body = slide.placeholders[1]  # content placeholder
            if body:
                tf = body.text_frame
                tf.clear()
                for ci, line in enumerate(content):
                    if ci == 0:
                        p = tf.paragraphs[0]
                    else:
                        p = tf.add_paragraph()
                    p.text = str(line)
                    p.font.size = Pt(18)
                    p.space_after = Pt(8)

prs.save({json.dumps(output_path)})
print("OK:" + {json.dumps(output_path)})
"""

    result = subprocess.run(["python3", "-c", script], capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        return {"error": result.stderr.strip() or result.stdout.strip()}

    if os.path.isfile(output_path):
        size = os.path.getsize(output_path)
        return {"path": output_path, "size": size, "slides": len(slides)}
    return {"error": "File not generated"}
