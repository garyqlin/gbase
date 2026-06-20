# Installation

## Via pip (recommended)

```bash
pip install gbase
```

This installs the core framework including:

- `gbase` CLI command (`gbase init`, `gbase chat`, `gbase serve`)
- `gbase/thinking/` module (L0–L4 thinking pipeline)
- `lib/` — kernel, memory, session management
- `tools/` — 40+ auto-registered tools

### Optional: Web Chat

```bash
pip install gbase[server]
```

## From source

For the full developer experience (armors, identities, RSI evolution):

```bash
git clone https://github.com/garyqlin/gbase.git
cd gbase
pip install -r requirements.txt
cp .env.example .env
# edit .env — set your API key
```

## Requirements

- Python 3.11+
- An API key from one of: OpenAI, Aliyun DashScope, MiniMax, or DeepSeek

## Verify installation

```bash
gbase --version
# → GBase v0.6.1
```
