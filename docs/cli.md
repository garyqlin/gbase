# CLI Reference

The `gbase` command is available after `pip install gbase`.

## Commands

### `gbase init`

Create a new GBase project in the current directory.

```bash
gbase init
gbase init --dir /path/to/project
```

Creates:
- `.env` — configuration template (edit with your API key)
- `data/` — memory and experience storage
- `identities/` — identity profiles
- `skills/` — skill modules

---

### `gbase chat`

Start an interactive chat session (requires the full GBase repo; pip-only version shows setup instructions).

```bash
gbase chat
gbase chat --identity my-custom-agent
```

---

### `gbase serve`

Start the HTTP server.

```bash
gbase serve
gbase serve --port 8430
```

---

### `gbase version`

Show version information.

```bash
gbase --version
gbase version
```

## From source (full framework)

Clone the repo for all features including armors, web chat, and RSI:

```bash
python3 main.py cli                        # CLI mode
python3 main.py --mode web --port 8765     # Web chat
python3 main.py --arm hammer               # Armor mode
python3 main.py --arm ink                  # Ink armor
```
