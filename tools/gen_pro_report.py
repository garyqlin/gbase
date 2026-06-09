#!/usr/bin/env python3
"""
gen_pro_report — 专业企业调研报告生成器
使用 Playwright HTML→PDF 渲染，输出印刷级报告
"""

import os
from datetime import datetime

from playwright.sync_api import sync_playwright

from lib.toolkit import tool

# ─── HTML 模板 ──────────────────────────────────────────────────────────────

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
/* ===== 全局 ===== */
@page {
  size: A4;
  margin: 20mm 25mm 25mm 25mm;
}
body {
  font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  color: #1a1a2e;
  line-height: 1.7;
  font-size: 10.5pt;
}

/* ===== 封面 ===== */
.cover-page {
  width: 100%;
  height: 100vh;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  text-align: center;
  page-break-after: always;
  background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
  color: white;
  padding: 40px;
  box-sizing: border-box;
}
.cover-page .top-line {
  width: 80px;
  height: 4px;
  background: #e94560;
  margin-bottom: 40px;
  border-radius: 2px;
}
.cover-page h1 {
  font-size: 28pt;
  font-weight: 700;
  letter-spacing: 4px;
  margin: 0 0 16px 0;
}
.cover-page .subtitle {
  font-size: 13pt;
  color: rgba(255,255,255,0.7);
  margin-bottom: 60px;
  letter-spacing: 2px;
}
.cover-page .info-grid {
  display: grid;
  grid-template-columns: auto auto;
  gap: 10px 30px;
  font-size: 10pt;
  color: rgba(255,255,255,0.6);
  text-align: left;
}
.cover-page .info-grid .label { text-align: right; }
.cover-page .info-grid .value { text-align: left; }
.cover-page .bottom-bar {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 6px;
  background: #e94560;
}
.cover-page .confidential {
  position: absolute;
  bottom: 30px;
  font-size: 8pt;
  color: rgba(255,255,255,0.3);
  letter-spacing: 6px;
}

/* ===== 目录 ===== */
.toc-page {
  page-break-after: always;
}
.toc-page h2 {
  font-size: 18pt;
  color: #1a1a2e;
  border-bottom: 2px solid #e94560;
  padding-bottom: 8px;
  margin-bottom: 24px;
}
.toc-item {
  display: flex;
  align-items: center;
  padding: 6px 0;
  border-bottom: 1px dotted #ddd;
  font-size: 11pt;
}
.toc-item .num {
  color: #e94560;
  font-weight: 600;
  margin-right: 12px;
  min-width: 30px;
}
.toc-item .title {
  flex: 1;
}
.toc-item .page {
  color: #999;
  font-size: 10pt;
}
.toc-item.sub {
  padding-left: 42px;
  font-size: 10pt;
  color: #555;
}

/* ===== 正文 ===== */
h1 {
  font-size: 18pt;
  color: #1a1a2e;
  border-bottom: 2px solid #e94560;
  padding-bottom: 8px;
  margin-top: 32px;
  margin-bottom: 20px;
  page-break-before: always;
}
h1:first-of-type { page-break-before: avoid; }
h2 {
  font-size: 14pt;
  color: #302b63;
  margin-top: 24px;
  margin-bottom: 12px;
}
h3 {
  font-size: 12pt;
  color: #444;
  margin-top: 18px;
  margin-bottom: 8px;
}
p { margin: 8px 0; }

/* ===== 表格 ===== */
table {
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
  font-size: 9.5pt;
}
thead th {
  background: #302b63;
  color: white;
  padding: 10px 12px;
  text-align: left;
  font-weight: 600;
}
tbody td {
  padding: 8px 12px;
  border-bottom: 1px solid #e0e0e0;
}
tbody tr:nth-child(even) {
  background: #f8f8fc;
}
tbody tr:hover {
  background: #eeeef8;
}

/* ===== 结论框 ===== */
.conclusion-box {
  border: 2px solid #302b63;
  border-left: 6px solid #e94560;
  border-radius: 6px;
  padding: 16px 20px;
  margin: 20px 0;
  background: #f8f8fc;
}
.conclusion-box .label {
  font-weight: 700;
  color: #e94560;
  font-size: 11pt;
  margin-bottom: 8px;
}
.conclusion-box p { margin: 4px 0; }

/* ===== 注释框 ===== */
.note-box {
  border-left: 4px solid #667eea;
  background: #f0f4ff;
  padding: 12px 16px;
  margin: 14px 0;
  border-radius: 0 4px 4px 0;
  font-size: 9.5pt;
  color: #333;
}
.note-box .icon { font-weight: 700; color: #667eea; }

.warning-box {
  border-left: 4px solid #f6ad55;
  background: #fffaf0;
  padding: 12px 16px;
  margin: 14px 0;
  border-radius: 0 4px 4px 0;
  font-size: 9.5pt;
  color: #333;
}
.warning-box .icon { font-weight: 700; color: #dd6b20; }

/* ===== 列表 ===== */
ul, ol { margin: 8px 0; padding-left: 24px; }
li { margin: 4px 0; }

/* ===== 分割线 ===== */
hr {
  border: none;
  border-top: 1px solid #e0e0e0;
  margin: 24px 0;
}

/* ===== 页脚页码 ===== */
@page {
  @bottom-center {
    content: counter(page);
    font-size: 9pt;
    color: #999;
  }
}
</style>
</head>
<body>

<!-- 封面 -->
<div class="cover-page">
  <div class="top-line"></div>
  <h1>{{TITLE}}</h1>
  <div class="subtitle">{{SUBTITLE}}</div>
  <div class="info-grid">
    <span class="label">报告日期</span>
    <span class="value">{{DATE}}</span>
    <span class="label">编制单位</span>
    <span class="value">{{AUTHOR}}</span>
    <span class="label">版本</span>
    <span class="value">V{{VERSION}}</span>
    <span class="label">密级</span>
    <span class="value">{{CLASSIFICATION}}</span>
  </div>
  <div class="bottom-bar"></div>
  <div class="confidential">CONFIDENTIAL</div>
</div>

<!-- 目录 -->
<div class="toc-page">
  <h2>目录</h2>
  {{TOC_ITEMS}}
</div>

<!-- 正文 -->
{{CONTENT}}

</body>
</html>
"""


def _build_toc(sections):
    """生成目录 HTML"""
    items = []
    for sec in sections:
        cls = "sub" if sec.get("level", 1) > 1 else ""
        num = sec.get("num", "")
        title = sec.get("title", "")
        page = sec.get("page", "")
        items.append(
            f'<div class="toc-item {cls}">'
            f'<span class="num">{num}</span>'
            f'<span class="title">{title}</span>'
            f'<span class="page">{page}</span>'
            f"</div>"
        )
    return "\n".join(items)


def _build_content(blocks):
    """构建正文 HTML"""
    parts = []
    for block in blocks:
        t = block.get("type", "p")
        if t == "h1":
            parts.append(f"<h1>{block['text']}</h1>")
        elif t == "h2":
            parts.append(f"<h2>{block['text']}</h2>")
        elif t == "h3":
            parts.append(f"<h3>{block['text']}</h3>")
        elif t == "p":
            parts.append(f"<p>{block['text']}</p>")
        elif t == "table":
            parts.append(_build_table(block))
        elif t == "conclusion":
            parts.append(
                f'<div class="conclusion-box">'
                f'<div class="label">{block.get("label", "结论")}</div>'
                f"{block['text']}</div>"
            )
        elif t == "note":
            icon = block.get("icon", "📌")
            parts.append(f'<div class="note-box"><span class="icon">{icon}</span> {block["text"]}</div>')
        elif t == "warning":
            icon = block.get("icon", "⚠️")
            parts.append(f'<div class="warning-box"><span class="icon">{icon}</span> {block["text"]}</div>')
        elif t == "list":
            items = "".join(f"<li>{item}</li>" for item in block.get("items", []))
            tag = "ol" if block.get("ordered") else "ul"
            parts.append(f"<{tag}>{items}</{tag}>")
        elif t == "hr":
            parts.append("<hr>")
    return "\n".join(parts)


def _build_table(block):
    """构建表格 HTML"""
    headers = block.get("headers", [])
    rows = block.get("rows", [])
    html = "<table><thead><tr>"
    for h in headers:
        html += f"<th>{h}</th>"
    html += "</tr></thead><tbody>"
    for row in rows:
        html += "<tr>"
        for cell in row:
            html += f"<td>{cell}</td>"
        html += "</tr>"
    html += "</tbody></table>"
    return html


@tool()
def generate_report(
    output_path: str,
    title: str = "企业关联关系深度调研报告",
    subtitle: str = "企业关联关系深度调研报告",
    author: str = "GBase 智能调研系统",
    version: str = "1.0",
    classification: str = "内部公开",
    sections: list = None,
    content_blocks: list = None,
):
    """
    生成专业 PDF 报告

    Args:
        output_path: 输出 PDF 路径
        title: 封面大标题
        subtitle: 封面副标题
        author: 编制单位
        version: 版本号
        classification: 密级
        sections: 目录结构 [{"num":"1","title":"...","level":1,"page":"1"}, ...]
        content_blocks: 正文内容块列表
    """
    if sections is None:
        sections = []
    if content_blocks is None:
        content_blocks = []

    date_str = datetime.now().strftime("%Y年%m月%d日")

    html = (
        TEMPLATE.replace("{{TITLE}}", title)
        .replace("{{SUBTITLE}}", subtitle)
        .replace("{{DATE}}", date_str)
        .replace("{{AUTHOR}}", author)
        .replace("{{VERSION}}", version)
        .replace("{{CLASSIFICATION}}", classification)
        .replace("{{TOC_ITEMS}}", _build_toc(sections))
        .replace("{{CONTENT}}", _build_content(content_blocks))
    )

    # 写入临时 HTML
    tmp_html = output_path.replace(".pdf", ".html")
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(html)

    # Playwright 渲染
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=output_path,
            format="A4",
            margin={"top": "20mm", "bottom": "25mm", "left": "25mm", "right": "25mm"},
            print_background=True,
        )
        browser.close()

    # 清理临时 HTML
    os.remove(tmp_html)

    return output_path


# ═══════════════════════════════════════════════════════════════════════════════
# 命令行入口
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        # 生成演示报告
        out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/demo_report.pdf"
        generate_report(
            output_path=out,
            title="企业关联关系深度调研报告",
            subtitle="—— 基于公开工商信息的关联关系核查",
            sections=[
                {"num": "1", "title": "调研概要", "level": 1, "page": "1"},
                {"num": "1.1", "title": "调研背景", "level": 2, "page": "1"},
                {"num": "1.2", "title": "调研方法", "level": 2, "page": "1"},
                {"num": "2", "title": "企业基本信息", "level": 1, "page": "2"},
                {"num": "3", "title": "关联关系核查", "level": 1, "page": "3"},
                {"num": "3.1", "title": "法定代表人维度", "level": 2, "page": "3"},
                {"num": "3.2", "title": "注册地址维度", "level": 2, "page": "3"},
                {"num": "3.3", "title": "对外投资维度", "level": 2, "page": "4"},
                {"num": "4", "title": "综合结论", "level": 1, "page": "5"},
            ],
            content_blocks=[
                {"type": "h1", "text": "1. 调研概要"},
                {"type": "h2", "text": "1.1 调研背景"},
                {
                    "type": "p",
                    "text": "受客户委托，对以下三家企业进行关联关系核查，判断其是否存在法人交叉、地址重合、投资关联等关联关系：",
                },
                {
                    "type": "list",
                    "items": [
                        "上海硕科国际贸易有限公司",
                        "上海旗迹源供应链管理有限公司",
                        "上海黑加仑供应链管理有限公司",
                    ],
                },
                {"type": "h2", "text": "1.2 调研方法"},
                {
                    "type": "p",
                    "text": "本次调研采用多维度交叉核查方法，覆盖以下维度：",
                },
                {
                    "type": "list",
                    "items": [
                        "法定代表人维度：核查三家企业法人代表是否存在同一人",
                        "注册地址维度：核查三家企业注册地址是否存在重合",
                        "对外投资维度：核查三家企业是否存在共同投资或交叉持股",
                    ],
                },
                {
                    "type": "note",
                    "icon": "📌",
                    "text": "数据来源：国家企业信用信息公示系统、天眼查、企查查等公开渠道。",
                },
                {"type": "h1", "text": "2. 企业基本信息"},
                {
                    "type": "table",
                    "headers": ["企业名称", "法定代表人", "注册资本", "成立日期", "注册地址"],
                    "rows": [
                        ["上海硕科国际贸易有限公司", "石要峰", "500万元", "2016-03-15", "上海市金山区"],
                        ["上海旗迹源供应链管理有限公司", "乔冠旗", "300万元", "2020-07-22", "上海市徐汇区"],
                        ["上海黑加仑供应链管理有限公司", "王汉中", "800万元", "2018-11-08", "上海市虹口区"],
                    ],
                },
                {"type": "h1", "text": "3. 关联关系核查"},
                {"type": "h2", "text": "3.1 法定代表人维度"},
                {
                    "type": "p",
                    "text": "经核查，三家企业法定代表人分别为石要峰、乔冠旗、王汉中，三人姓名完全不同，且各自名下无交叉任职记录。",
                },
                {
                    "type": "table",
                    "headers": ["企业", "法定代表人", "名下其他企业", "交叉情况"],
                    "rows": [
                        ["上海硕科", "石要峰", "上海硕科广告有限公司", "—"],
                        ["上海旗迹源", "乔冠旗", "无公开关联企业", "❌ 与石要峰无交叉"],
                        ["上海黑加仑", "王汉中", "淮北市利平物流等3家", "❌ 与石、乔均无交叉"],
                    ],
                },
                {"type": "h2", "text": "3.2 注册地址维度"},
                {
                    "type": "p",
                    "text": "三家企业注册地址分属上海市不同行政区，无地址重合：",
                },
                {
                    "type": "list",
                    "items": [
                        "上海硕科：金山区",
                        "上海旗迹源：徐汇区",
                        "上海黑加仑：虹口区",
                    ],
                },
                {"type": "h2", "text": "3.3 对外投资维度"},
                {
                    "type": "p",
                    "text": "经核查，三家企业对外投资均为0，不存在通过共同投资形成关联关系的可能。",
                },
                {
                    "type": "warning",
                    "icon": "⚠️",
                    "text": "唯一值得注意的发现：王汉中（黑加仑法人）名下另有3家关联企业，分布在安徽、北京、浙江，均为物流/货代行业。但这些企业与硕科、旗迹源无任何交叉。",
                },
                {"type": "h1", "text": "4. 综合结论"},
                {
                    "type": "conclusion",
                    "label": "调研结论",
                    "text": "<p><strong>三家企业之间不存在关联关系。</strong></p><p>经法定代表人、注册地址、对外投资三个维度的交叉核查，三家企业在所有可查维度上均无交叉。王汉中名下另有物流相关企业，但与本次调研的另外两家企业无关。</p>",
                },
            ],
        )
        print(f"✅ 报告已生成: {out}")
