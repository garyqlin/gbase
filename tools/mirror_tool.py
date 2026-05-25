# SPDX-License-Identifier: MIT
"""
mirror_tool.py — Mirror engine tool

@tool functions + CLI entry for agents.

@tool 函数:
    mirror_record — 记录一条鉴面记忆
    mirror_verify — 验证（强化）一条记忆
    mirror_review — 回溯审查
    mirror_stats  — 查看鉴面统计

CLI 用法：
    python3 tools/mirror_tool.py record <type> <content> [--tags a,b,c] [--source src]
    python3 tools/mirror_tool.py verify <content> [--type t]
    python3 tools/mirror_tool.py inject          获取注入文本
    python3 tools/mirror_tool.py review          执行回溯
    python3 tools/mirror_tool.py stats           查看统计
    python3 tools/mirror_tool.py decay           手动衰减
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.mirror import Mirror
from lib.toolkit import tool

# ── 全局鉴面引擎实例（由 main.py 初始化时设置） ──
_mirror: Mirror | None = None


def set_mirror_instance(m: Mirror | None):
    global _mirror
    _mirror = m


def _get_mirror() -> Mirror:
    if _mirror is None:
        m = Mirror()
        m.setup()
        return m
    return _mirror

def get_mirror_instance() -> Mirror:
    """获取全局鉴面引擎实例。若未初始化则自动创建。
    供 pipeline.py / experience.py 等内部模块调用。
    """
    return _get_mirror()


@tool()
async def mirror_record(mtype: str, content: str, tags: str = "", source: str = "", strength: float = 1.0) -> dict:
    """记录一条鉴面记忆。类型：lesson(教训), insight(洞察), principle(原则), pattern(模式), context(背景)

    Args:
        mtype: 记忆类型 (lesson/insight/principle/pattern/context)
        content: 记忆内容
        tags: 逗号分隔的标签
        source: 来源
        strength: 初始强度 (0-1.0)

    Returns:
        {status: ok, type, content[:60]}
    """
    try:
        m = _get_mirror()
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        m.record(mtype, content, tags=tag_list, source=source, strength=strength)
        return {"status": "ok", "type": mtype, "content": content[:60]}
    except Exception as e:
        return {"status": "error", "error": str(e), "hint": "记录失败，稍后重试"}


@tool()
async def mirror_verify(content: str, mtype: str = "") -> dict:
    """验证（强化）一条鉴面记忆。验证次数越多，记忆越牢固。

    Args:
        content: 要验证的记忆内容
        mtype: 可选，限定类型

    Returns:
        {status: ok, content[:60]}
    """
    try:
        m = _get_mirror()
        m.verify(content, mtype or None)
        return {"status": "ok", "content": content[:60]}
    except Exception as e:
        return {"status": "error", "error": str(e), "hint": "验证失败，稍后重试"}


@tool()
async def mirror_review() -> dict:
    """回溯审查鉴面记忆。返回有效/需审视/可能过时的统计和详情。

    Returns:
        {status, checked, still_valid, needs_update, outdated, items: [...]}
    """
    m = _get_mirror()
    report = m.review()
    return {
        "status": "ok",
        "checked": report["checked"],
        "still_valid": report["still_valid"],
        "needs_update": report["needs_update"],
        "outdated": report["outdated"],
        "items": [{"status": i["status"], "type": i["type"], "content": i["content"],
                    "strength": i["strength"], "age_days": i["age_days"]}
                   for i in report["items"]],
    }


@tool()
async def mirror_stats() -> dict:
    """查看鉴面引擎统计信息。

    Returns:
        {total_active, total_forgotten, total_verified, avg_strength, by_type: {...}}
    """
    import asyncio
    last_err = None
    for attempt in range(2):
        try:
            m = _get_mirror()
            stats = m.get_stats()
            return {"status": "ok", **stats}
        except Exception as e:
            last_err = str(e)
            if attempt == 0:
                await asyncio.sleep(0.3)  # 等 WAL 锁释放后重试
    return {"status": "error", "error": last_err, "hint": "mirror.db 暂时不可用，稍后重试"}

def main():
    parser = argparse.ArgumentParser(description='鉴面引擎')
    sub = parser.add_subparsers(dest='command')



@tool()
async def mirror_forget(pattern: str) -> dict:
    """批量软删除匹配的记忆。
    Args:
        pattern: 搜索模式，如 "%工具调用次数"
    Returns:
        删除的记录数
    """
    m = _get_mirror()
    deleted = m.forget(pattern)
    return {"status": "ok", "deleted": deleted}


@tool()
async def mirror_recall(query: str, limit: int = 5) -> dict:
    """搜索鉴面记忆。
    Args:
        query: 搜索关键词
        limit: 最大返回数
    Returns:
        匹配的记忆列表
    """
    import asyncio as _asyncio
    last_err = None
    for attempt in range(2):
        try:
            m = _get_mirror()
            results = m.recall(query, limit=limit, ebbinghaus=True)
            if results:
                return {"status": "ok", "count": len(results), "results": results}
            return {"status": "ok", "count": 0, "results": [], "note": "未找到匹配记忆"}
        except Exception as e:
            last_err = str(e)
            if attempt == 0:
                await _asyncio.sleep(0.3)
    return {"status": "error", "error": last_err, "hint": "鉴面检索暂时不可用，稍后重试"}

    p_record = sub.add_parser('record')
    p_record.add_argument('type', choices=['lesson','insight','principle','pattern','context'])
    p_record.add_argument('content')
    p_record.add_argument('--tags', default='')
    p_record.add_argument('--source', default='')
    p_record.add_argument('--strength', type=float, default=1.0)

    p_verify = sub.add_parser('verify')
    p_verify.add_argument('content')
    p_verify.add_argument('--type', dest='mtype', default=None)

    sub.add_parser('inject')
    sub.add_parser('review')
    sub.add_parser('stats')
    sub.add_parser('decay')
    sub.add_parser('forget')
    sub.add_parser('recall')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    m = Mirror()
    m.setup()

    if args.command == 'record':
        tags = [t.strip() for t in args.tags.split(',') if t.strip()] if args.tags else None
        m.record(args.type, args.content, tags=tags, source=args.source, strength=args.strength)
        print(f'✅ 已记录 [{args.type}] {args.content[:60]}')

    elif args.command == 'verify':
        m.verify(args.content, args.mtype)
        print(f'✅ 已验证: {args.content[:60]}')

    elif args.command == 'inject':
        text = m.get_injection_text(max_items=5)
        print(text if text else '(无鉴面记忆)')

    elif args.command == 'review':
        report = m.review()
        print('回溯报告:')
        print(f'  检查: {report["checked"]} 条')
        print(f'  有效: {report["still_valid"]}')
        print(f'  需审视: {report["needs_update"]}')
        print(f'  可能过时: {report["outdated"]}')
        for item in report['items']:
            print(f'  [{item["status"]}] [{item["type"]}] (强度:{item["strength"]}) {item["content"]}')

    elif args.command == 'stats':
        stats = m.get_stats()
        print('鉴面引擎统计:')
        print(f'  活跃记忆: {stats["total_active"]}')
        print(f'  已遗忘: {stats["total_forgotten"]}')
        print(f'  已验证: {stats["total_verified"]}')
        print(f'  平均强度: {stats["avg_strength"]}')
        for t, c in stats.get('by_type', {}).items():
            print(f'    {t}: {c}条')

    elif args.command == 'decay':
        m.decay()
        print('✅ 衰减完成')

    m.close()

if __name__ == '__main__':
    main()
