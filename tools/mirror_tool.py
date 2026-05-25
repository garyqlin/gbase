# SPDX-License-Identifier: MIT
"""Mirror engine — persistent memory with Ebbinghaus decay and tagging."""

import argparse
import json
import logging
import os

from lib.mirror import Mirror

logger = logging.getLogger(__name__)

_MIRROR_INSTANCE: Mirror | None = None
_MIRROR_PATH = os.environ.get(
    "MIRROR_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "mirror.db"),
)


def _get_mirror() -> Mirror:
    global _MIRROR_INSTANCE
    if _MIRROR_INSTANCE is None:
        _MIRROR_INSTANCE = Mirror(db_path=_MIRROR_PATH)
        _MIRROR_INSTANCE.setup()
    return _MIRROR_INSTANCE


def main():
    """CLI entry point for mirror tool."""
    parser = argparse.ArgumentParser(description="Mirror engine CLI")
    sp = parser.add_subparsers(dest="command")

    p_record = sp.add_parser("record")
    p_record.add_argument("type", choices=["lesson", "insight", "principle", "pattern", "context"])
    p_record.add_argument("content")
    p_record.add_argument("--tags", default="")
    p_record.add_argument("--source", default="")
    p_record.add_argument("--strength", type=float, default=1.0)

    p_verify = sp.add_parser("verify")
    p_verify.add_argument("content")
    p_verify.add_argument("--type", dest="mtype", default=None)

    sp.add_parser("inject")
    sp.add_parser("review")
    sp.add_parser("stats")
    sp.add_parser("decay")
    sp.add_parser("forget")
    sp.add_parser("recall")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    m = Mirror()
    m.setup()

    if args.command == "record":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None
        m.record(args.type, args.content, tags=tags, source=args.source, strength=args.strength)
        print(f"Recorded [{args.type}] {args.content[:60]}")
    elif args.command == "verify":
        m.verify(args.content, args.mtype)
        print(f"Verified: {args.content[:60]}")
    elif args.command == "inject":
        import sys
        content = sys.stdin.read().strip()
        if content:
            m.record("insight", content)
            print(f"Injected insight ({len(content)} chars)")
        else:
            print("Nothing to inject (stdin empty)")
    elif args.command == "review":
        items = m.review()
        print(f"Review: {len(items)} items")
        for item in items[:10]:
            print(f"  [{item.get('type')}] {item.get('content', '')[:80]}")
    elif args.command == "stats":
        stats = m.stats()
        print(json.dumps(stats, indent=2))
    elif args.command == "decay":
        m.decay()
        print("Decay applied.")
    elif args.command == "forget":
        pat = getattr(args, "pattern", None) or input("Pattern to forget: ")
        n = m.forget(pat)
        print(f"Forgot {n} records")
    elif args.command == "recall":
        query = input("Query: ") if not hasattr(args, "query") else args.query
        results = m.recall(query)
        for r in results:
            print(f"  [{r.get('strength', 0):.2f}] {r.get('content', '')[:100]}")


if __name__ == "__main__":
    main()
