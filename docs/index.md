# GBase Documentation

> **Version:** 0.6.1 | [GitHub](https://github.com/garyqlin/gbase) | [PyPI](https://pypi.org/project/gbase/) | [Changelog](../CHANGELOG.md)

## Quick Start

```bash
pip install gbase
gbase init
# edit .env with your API key
gbase chat
```

Or clone the repo for the full experience:

```bash
git clone https://github.com/garyqlin/gbase.git
cd gbase
cp .env.example .env
# edit .env with your API key
python3 main.py cli
```

Open http://localhost:8765 in your browser for the Web Chat interface:

```bash
pip install websockets
python3 main.py --mode web
```

## What is GBase?

GBase is an AI agent framework that doesn't just execute — it remembers, reflects, and evolves.

Most frameworks treat agents like functions: call them, get a result, forget they existed. GBase gives each agent:

- **Memory** that persists across sessions (Mirror Memory)
- **Experience** distilled from every interaction (Experience Engine)
- **Self-improvement** through recursive analysis (RSI)
- **Thinking** — structured reasoning before every action (Thinking Module v0.6)

## Table of Contents

- [Core Concepts](core-concepts.md)
- [Examples](../examples/)
- [Installation](installation.md)
- [CLI Reference](cli.md)
- [API Reference](api-reference.md)
- [Contributing](../CONTRIBUTING.md)
