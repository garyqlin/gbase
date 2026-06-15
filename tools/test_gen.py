#!/usr/bin/env python3
"""测试 PDF 生成"""

import asyncio

from tools.pdf_gen import gen_pdf


async def main():
    content = [
        {
            "type": "cover",
            "title": "2026年中国人工智能行业研究报告",
            "subtitle": "AI Industry Research Report 2026",
            "date": "2026年5月",
            "author": "高达研究部",
        },
        {"type": "toc"},
        {"type": "h1", "text": "第一章 行业概述"},
        {"type": "p", "text": "2025年，中国人工智能产业规模突破2.1万亿元，同比增长32.5%。"},
        {"type": "h2", "text": "1.1 核心数据一览"},
        {
            "type": "table",
            "headers": ["指标", "2024年", "2025年"],
            "rows": [
                ["产业规模（亿元）", "15,800", "21,000"],
                ["AI企业数量（家）", "4,500", "5,200"],
            ],
        },
        {"type": "h2", "text": "1.2 关键发现"},
        {"type": "ul", "items": ["大模型能力持续跃升", "应用落地加速"]},
        {"type": "h2", "text": "1.3 专家观点"},
        {"type": "quote", "text": "中国AI产业正处于关键转折期。"},
        {"type": "small", "text": "—— 某头部AI研究院首席科学家"},
        {"type": "pagebreak"},
        {"type": "h1", "text": "第二章 技术发展分析"},
        {"type": "p", "text": "2025年，中国AI技术在多条技术路线上取得突破。"},
        {"type": "note", "text": "MoE架构大模型推理成本降低约60%。", "level": "info"},
        {"type": "h3", "text": "2.1.1 技术路线对比"},
        {
            "type": "table",
            "headers": ["路线", "优势", "劣势"],
            "rows": [
                ["Dense Transformer", "通用性强", "推理成本高"],
                ["MoE架构", "推理成本低", "训练复杂"],
            ],
        },
        {"type": "pagebreak"},
        {"type": "h1", "text": "结论与展望"},
        {"type": "conclusion", "text": "中国AI产业正处于从技术驱动向价值驱动转型的关键时期。"},
        {"type": "spacer", "height": 20},
        {"type": "divider"},
        {"type": "small", "text": "免责声明：本报告中的数据和观点仅供参考。"},
    ]

    result = await gen_pdf(
        title="AI行业研究报告",
        content=content,
        subtitle="2026年中国人工智能行业研究报告",
        author="高达研究部",
        output_path="./AI_Report_2026_v1.pdf",
        color_theme="mckinsey",
        show_toc=True,
        font_size="normal",
    )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
