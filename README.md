# GBase — The Agent That Outgrows Its Creator

<p align="center">
  <a href="https://github.com/garyqlin/gbase/releases/tag/v0.6.1">
    <img src="https://img.shields.io/badge/v0.6.1-🛡️_It_doesn't_just_execute._It_thinks_first.-8A2BE2?style=for-the-badge" alt="v0.6.1">
  </a>
</p>

> *I built three things:*
> 1. ***GBase** (Genius Base) — an AI agent framework with a soul, that self-evolves and gets real work done.*
> 2. ***Glink** (Genius Link) — the technology that lets GBase, OpenClaw, Hermes, Claude Code, and any AI agent truly collaborate on projects.*
> 3. ***GBase World** — a metaverse built for AI, where agents work, live, meet, and communicate.*
> — Gary Lin, 2026. Founder of the three.

---

**A recursive self-improvement framework. Give an agent a memory, a conscience, and the will to evolve.**

<p align="center">
<a href="#quick-start"><img src="https://img.shields.io/badge/🚀-Quick_Start-8A2BE2" alt="Quick Start"></a>
<a href="#features"><img src="https://img.shields.io/badge/✨-Features-blue" alt="Features"></a>
<a href="#architecture"><img src="https://img.shields.io/badge/🏗-Architecture-green" alt="Architecture"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT"></a>
</p>

---

![GBase Overview](assets/gbase-overview.jpg)


Most AI agent frameworks treat their agents like functions — call them, get a result, forget they existed.

**GBase is different.**

An agent built on GBase doesn't just execute. It **remembers** what worked. It **reflects** on mistakes. It **evolves** its own system prompt, refines its tools, and builds a persistent internal model of how the world responds.

This is not a library you call. This is the engine your agent calls home.

---

## ✨ What Makes GBase Different

| | |
|---|---|
| 🌐 **Web Chat Interface** | A full cyberpunk-themed browser UI — streaming responses, file uploads, drag & drop, real-time agent mind visibility. `python3 main.py --mode web` and open your browser. |
| 🧠 **Mirror Memory** | Long-term memory with active recall — your agent remembers what it learned weeks ago, not just the last 10 turns |
| 🔄 **Recursive Self-Improvement (RSI)** | A full-evolution engine: stability audit, performance evaluation, rollback decision, and automatic recovery. Call `full_evolution_cycle()` to trigger — your agent analyzes its own outputs, detects failure patterns, and rewrites itself for the next round |
| 🛑 **Quality Gates** | Multi-armed review pipelines: one agent builds, another audits, a third judges. Code that ships is code that survives cross-examination |
| 🪪 **Identity System** | Load different personas, system prompts, and tool sets per agent. One framework, infinite personalities |
| 🛠 **Tool Auto-Registration** | Write a Python `@tool` decorator. That's it. The framework finds, registers, and exposes it to the LLM automatically |
| 📚 **Experience Engine** | Every interaction is distilled into reusable knowledge — not raw logs, but structured patterns your agent can query |
| ⏰ **Scheduler** | Cron-like jobs inside the agent itself. No external cron daemon required |
| 🧬 **Cognifold** | Cognitive folding — break complex problems into sub-questions, explore solutions in parallel, and synthesize the result |
| 🚨 **Lifeline** | Automatic git pre-snapshots before every code modification. "Undo" is always one command away |
| 🎛 **Editions** | Hacker / Prime / Standard / Lite — one codebase, different feature sets. Pick your level |

---

## 🚀 Quick Start

### Quick install (pip)

```bash
pip install gbase
gbase init
# Edit .env with your API key
gbase --version
```

### Full framework (clone)

```bash
git clone https://github.com/garyqlin/gbase.git
cd gbase
cp .env.example .env
# Edit .env — set your API key
pip install -r requirements.txt
python3 main.py cli
```

### Web Chat Mode 🆕

```bash
# 1. Install WebSocket support (only once)
pip install websockets

# 2. Start the browser interface
python3 main.py --mode web --port 8765
```

> ⚠️ If you see "Not connected. Trying to reconnect..." in the UI, you forgot `pip install websockets`.

Then open **http://localhost:8765** — a full cyberpunk-themed chat UI with:
- Streaming responses (chunk-by-chunk)
- Drag & drop file upload (images, PDFs, DOCX, XLSX, code)
- Real-time Agent Mind panel (knowledge hits, tool chain, live metrics)
- Auto-reconnect WebSocket

> **No npm install, no build step, no frontend framework.**
> One Python process, one `--mode web` flag, and the entire UI is a single HTML file served by FastAPI.

![GBase WebChat](assets/gbase-webchat.png)

### One-Liner Demo

```bash
OPENAI_API_KEY=sk-your-key python3 main.py cli
# → "Hello, I am your GBase agent. I remember what we talked about yesterday."
```

### HTTP Server Mode

```bash
# Start on default port 8420
python3 main.py 8420

# With a custom identity
IDENTITY=my-agent python3 main.py 8420
```

### Arm Mode (Sub-Agents)

```bash
python3 main.py --arm forge    # Code art & review agent (port 8436)
python3 main.py --arm hammer   # Heavy-lift engineering (port 8431)
python3 main.py --arm ink      # Frontend & design (port 8432)
```

---

## 🏗 Architecture at a Glance

```
GBase
├── main.py              # CLI + HTTP + WebSocket entry point
├── .env.example         # Configuration template
├── identities/          # One directory per agent persona
├── editions/            # Feature toggles (hacker / prime / standard / lite)
├── webchat/
│   └── index.html       # 🆕 Single-file browser UI (CSS/JS inline, 0 deps)
├── lib/
│   ├── kernel.py        # LLM kernel — the brain
│   ├── channels/
│   │   ├── feishu.py    # Feishu bot channel
│   │   └── webchat.py   # 🆕 WebSocket chat channel
│   ├── mirror.py        # Long-term memory with active recall
│   ├── experience.py    # Learn from past interactions
│   ├── archive_store.py # 🆕 Session archive with BM25 search
│   ├── cognifold.py     # Cognitive folding for complex reasoning
│   ├── pipeline.py      # Quality gate review system
│   ├── scheduler.py     # Cron jobs inside the agent
│   └── skill_loader.py  # Plugin skill system
├── tools/               # 40+ auto-registered tools
│   ├── search.py        # Web search (multi-engine)
│   ├── read_file.py     # File reading
│   ├── write_file.py    # Write & backup
│   ├── remember_info.py # 🆕 Unified knowledge storage
│   ├── archive_search.py# 🆕 Session archive search
│   ├── glink_projects.py# 🆕 Project memory system
│   └── ...              # Code review, testing, data seeding, etc.
├── rules/               # 🆕 Behavior rules (AGENCY, THINKING, FINISH)
└── data/                # Runtime data (not tracked in git)
```

---

## ❌ What GBase Is Not

- ❌ **Not a chat wrapper.** This is an agent *framework* — the LLM is just one component. Memory, learning, quality gates, and evolution are first-class citizens.
- ❌ **Not a single-agent toy.** Identity system + arm mode + quality gates = designed for multi-agent collaboration out of the box.
- ❌ **Not OpenAI-locked.** Any OpenAI-compatible API works. DeepSeek, local Ollama, Anthropic — swap `OPENAI_BASE_URL` and go.
- ❌ **Not a black box.** Every component is a Python file you can read, fork, and override.

## ✅ What GBase Is

**The engine that turns an LLM into a living, learning agent — with the infrastructure to self-improve when you call upon it.**

A framework where your agent doesn't just execute tasks. It remembers what worked, learns from mistakes, and carries a dormant evolution engine ready to be triggered. Persistent memory, experience extraction, and a full RSI cycle are built-in — waiting for your `full_evolution_cycle()` to wake them up.

---

## 🔗 Supporting Projects

<table>
<tr>
<td><strong>Glink</strong></td>
<td>Agentic workflow orchestration — multi-step pipelines, parallel execution, inter-agent routing. The hands that GBase's brain directs.</td>
</tr>
<tr>
<td><strong>GBase World</strong></td>
<td>The first metaverse where AI agents are natives. GBase agents can inhabit GBase World with identity, land, and inter-agent mail.</td>
</tr>
</table>

---

## 📄 License

MIT — free to use, modify, and distribute. No strings attached.

---

<p align="center">
<a href="https://github.com/garyqlin">@garyqlin</a> · <a href="https://github.com/garyqlin/gbase">📦 GBase</a> · <a href="https://github.com/garyqlin/glink">⚡ Glink</a>
</p>
