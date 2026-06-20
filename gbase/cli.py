"""GBase CLI — the entry point for `gbase` command.

Usage:
    gbase init           Create a new GBase project
    gbase chat           Start interactive chat
    gbase serve          Start HTTP server
    gbase version        Show version
"""

import argparse
from pathlib import Path


def _get_version() -> str:
    try:
        from gbase import __version__

        return __version__
    except ImportError:
        pass
    try:
        from importlib.metadata import version

        return version("gbase")
    except ImportError:
        return "unknown"


def cmd_init(args):
    """Initialize a GBase project in the current directory."""
    # 默认在当前目录创建
    target = Path(args.dir or ".")
    target = target.resolve()

    env_file = target / ".env"
    if env_file.exists():
        print(f"⚠ .env already exists at {env_file}")
        return

    # 创建基本目录
    for d in ["data", "identities", "skills"]:
        (target / d).mkdir(parents=True, exist_ok=True)

    # 写 .env
    env_example = """# GBase Configuration
# Get your API key from: https://platform.openai.com/api-keys
# or use Aliyun DashScope / MiniMax / DeepSeek

# ── Primary: OpenAI-compatible API ──
# OPENAI_API_KEY=sk-your-key-here
# OPENAI_BASE_URL=https://api.openai.com/v1

# ── Aliyun DashScope ──
# GBASE_ALIYUN_API_KEY=sk-your-aliyun-key
# GBASE_MODEL=qwen3.7-plus

# ── MiniMax ──
# OPPRIME_MINIMAX_API_KEY=sk-your-minimax-key

# ── DeepSeek ──
# DEEPSEEK_API_KEY=sk-your-deepseek-key
"""
    env_file.write_text(env_example)

    print(f"✅ GBase project initialized at {target}")
    print()
    print("Next steps:")
    print(f"  1. Edit {target / '.env'} — set your API key")
    print("  2. Run: gbase chat")
    print()


def cmd_chat(args):
    """Start interactive chat with a GBase agent."""
    print("🔄 Initializing GBase chat...")
    print()
    print("⚠ This command requires the full GBase framework")
    print("  (lib/, tools/, identities/ directories).")
    print()
    print("  For now, clone the repo and run:")
    print("    git clone https://github.com/garyqlin/gbase.git")
    print("    cd gbase && python3 main.py cli")
    print()
    print("  The pip-installed `gbase chat` command will be")
    print("  fully functional in v0.7.0.")
    print()


def cmd_serve(args):
    """Start GBase HTTP server."""
    port = args.port or 8420
    print(f"🔄 Starting GBase server on port {port}...")
    print()
    print("⚠ This command requires the full GBase framework.")
    print("  Clone the repo and run:")
    print("    git clone https://github.com/garyqlin/gbase.git")
    print("    cd gbase && python3 main.py --mode web")
    print()


def cmd_version(args):
    """Show version."""
    v = _get_version()
    print(f"GBase v{v}")
    print("The Agent That Outgrows Its Creator")
    print()


def main():
    parser = argparse.ArgumentParser(
        prog="gbase",
        description="GBase — The Agent That Outgrows Its Creator",
    )
    parser.add_argument("--version", action="store_true", help="Show version and exit")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    p_init = sub.add_parser("init", help="Create a new GBase project")
    p_init.add_argument("--dir", "-d", default=".", help="Target directory (default: .)")

    p_chat = sub.add_parser("chat", help="Start interactive chat")
    p_chat.add_argument("--identity", default="default", help="Identity name")

    p_serve = sub.add_parser("serve", help="Start HTTP server")
    p_serve.add_argument("--port", "-p", type=int, default=8420, help="Port (default: 8420)")

    sub.add_parser("version", help="Show version")

    args = parser.parse_args()

    if args.version:
        cmd_version(args)
        return

    if not args.command:
        parser.print_help()
        print()
        print(f"GBase v{_get_version()}")
        return

    cmd_map = {
        "init": cmd_init,
        "chat": cmd_chat,
        "serve": cmd_serve,
        "version": cmd_version,
    }

    cmd_map[args.command](args)


if __name__ == "__main__":
    main()
