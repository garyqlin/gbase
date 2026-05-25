# SPDX-License-Identifier: MIT
"""
search_webui.py — Standalone Web UI for Gbase search engines.

A self-contained FastAPI application serving a polished dark-theme search dashboard.
Integrates both search.py (parallel multi-engine) and honeycomb_search.py (meta search).

Run directly:
    python3 tools/search_webui.py [port]

Or via main.py routing (auto-mounted).
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from lib.fetcher import Fetcher

logger = logging.getLogger("search-webui")

# ── Config ──────────────────────────────────────────────

DEFAULT_PORT = 8450
HISTORY_FILE = Path(__file__).resolve().parent.parent / "data" / "search_history.jsonl"
HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

ENGINES_CONFIG = {
    "google": {
        "label": "Google",
        "icon": "🔍",
        "channel": "web",
        "lang": "all",
        "description": "Global search engine — best for English and international queries",
    },
    "bing": {
        "label": "Bing",
        "icon": "🅱️",
        "channel": "web",
        "lang": "all",
        "description": "Microsoft search engine — strong English + Chinese results",
    },
    "duckduckgo": {
        "label": "DuckDuckGo",
        "icon": "🦆",
        "channel": "privacy",
        "lang": "en",
        "description": "Privacy-first search engine, no tracking",
    },
    "qwant": {
        "label": "Qwant",
        "icon": "🔎",
        "channel": "privacy",
        "lang": "all",
        "description": "European privacy search engine (French origin)",
    },
    "startpage": {
        "label": "Startpage",
        "icon": "🛡️",
        "channel": "privacy",
        "lang": "en",
        "description": "Privacy search engine powered by Google results",
    },
    "swisscows": {
        "label": "Swisscows",
        "icon": "🇨🇭",
        "channel": "privacy",
        "lang": "en",
        "description": "Swiss privacy search engine — family-friendly",
    },
    "prosearch": {
        "label": "ProSearch",
        "icon": "🚀",
        "channel": "power",
        "lang": "all",
        "description": "High-power engine via ProSearch API — best for deep results",
    },
    "honeycomb": {
        "label": "Honeycomb",
        "icon": "🐝",
        "channel": "meta",
        "lang": "all",
        "description": "Multi-wave meta search — 6 dimensions, auto-expansion, gap analysis",
    },
}

ENGINE_CHANNELS = {
    "web": {"label": "Web Search", "icon": "🌐"},
    "privacy": {"label": "Privacy Search", "icon": "🛡️"},
    "power": {"label": "Power Search", "icon": "🚀"},
    "meta": {"label": "Meta Search", "icon": "🐝"},
}


# ── Imports (lazy) ─────────────────────────────────────

def _get_search_tools():
    """Lazy import search modules (avoid startup loading)."""
    from tools import search as search_mod
    from tools import honeycomb_search as honeycomb_mod

    return search_mod, honeycomb_mod


async def _run_parallel_search(query: str, engines: list[str]) -> dict:
    """Run parallel search via search.py's engine system."""
    search_mod, _ = _get_search_tools()
    fetcher = Fetcher()

    # Build engine map directly
    results = []
    seen_urls = set()
    tasks = []

    for engine_name in engines:
        if engine_name == "prosearch":
            tasks.append(_run_prosearch(query, engine_name))
            continue
        func = search_mod.ENGINE_MAP.get(engine_name)
        if func:
            tasks.append(_run_engine(func, query, engine_name, fetcher))

    if tasks:
        for task_result in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(task_result, Exception):
                continue
            for r in task_result:
                url = r.get("url", "").split("?")[0].split("#")[0]
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(r)

    # Sort by engine weight
    def weight_key(r):
        eng = r.get("_engine", "")
        cfg = ENGINES_CONFIG.get(eng, {})
        return {"google": 2.0, "prosearch": 1.8, "bing": 1.5, "duckduckgo": 1.0, "qwant": 0.9, "startpage": 0.8, "swisscows": 0.7}.get(eng, 0.5)

    results.sort(key=weight_key, reverse=True)

    await fetcher.close()
    return {
        "query": query,
        "engines": engines,
        "total": len(results),
        "results": results[:20],
    }


async def _run_engine(func, query: str, engine_name: str, fetcher) -> list[dict]:
    """Run a single engine function."""
    try:
        engine_result = await func(query, fetcher) if engine_name != "bing_cn" else await func(query, fetcher, "zh-CN")
        for r in engine_result:
            r["_engine"] = engine_name
        return engine_result
    except Exception as e:
        logger.debug("Engine %s failed: %s", engine_name, e)
        return []


async def _run_prosearch(query: str, engine_name: str) -> list[dict]:
    """Run ProSearch via honeycomb module."""
    _, honeycomb_mod = _get_search_tools()
    try:
        results = await honeycomb_mod._search_prosearch(query, 10)
        return [{"title": r.title, "url": r.url, "snippet": r.snippet, "_engine": engine_name} for r in results]
    except Exception as e:
        logger.debug("ProSearch failed: %s", e)
        return []


async def _run_honeycomb(query: str, depth: str = "normal") -> dict:
    """Run honeycomb meta search."""
    _, honeycomb_mod = _get_search_tools()
    try:
        result = await honeycomb_mod.honeycomb_search(query, depth)
        return result
    except Exception as e:
        logger.error("Honeycomb search failed: %s", e)
        return {"error": str(e)}


def _save_history(query: str, engines: list[str], results_count: int):
    """Save search to history JSONL."""
    try:
        entry = {
            "id": str(uuid.uuid4())[:8],
            "ts": time.time(),
            "query": query,
            "engines": engines,
            "results": results_count,
        }
        with open(HISTORY_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load_history(limit: int = 20) -> list[dict]:
    """Load recent search history."""
    if not HISTORY_FILE.exists():
        return []
    try:
        entries = []
        with open(HISTORY_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        entries.reverse()
        return entries[:limit]
    except Exception:
        return []


# ── Create FastAPI app ─────────────────────────────────

app = FastAPI(title="Gbase Search Web UI", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── HTML Template ──────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gbase Search</title>
<style>
  :root {
    --bg: #0d1117;
    --bg2: #161b22;
    --bg3: #21262d;
    --border: #30363d;
    --text: #e6edf3;
    --text2: #8b949e;
    --accent: #58a6ff;
    --accent2: #3fb950;
    --accent3: #f0883e;
    --accent4: #a371f7;
    --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    --radius: 8px;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: var(--font);
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }
  /* Header */
  .header {
    background: var(--bg2);
    border-bottom: 1px solid var(--border);
    padding: 16px 24px;
    display: flex; align-items: center; gap: 16px;
    position: sticky; top: 0; z-index: 100;
  }
  .header h1 {
    font-size: 20px; font-weight: 600;
    background: linear-gradient(135deg, var(--accent), var(--accent4));
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .header .subtitle { color: var(--text2); font-size: 13px; margin-left: auto; }
  /* Search bar */
  .search-section {
    max-width: 900px; margin: 32px auto; padding: 0 20px;
  }
  .search-box {
    display: flex; gap: 8px; align-items: center;
    background: var(--bg2); border: 1px solid var(--border);
    border-radius: 12px; padding: 4px;
    transition: border-color .2s;
  }
  .search-box:focus-within { border-color: var(--accent); }
  .search-box input {
    flex: 1; background: transparent; border: none; outline: none;
    color: var(--text); font-size: 16px; padding: 12px 16px;
    font-family: var(--font);
  }
  .search-box input::placeholder { color: var(--text2); }
  .search-box button {
    background: var(--accent); color: #fff; border: none;
    border-radius: 8px; padding: 10px 24px; font-size: 15px;
    font-weight: 500; cursor: pointer; white-space: nowrap;
    transition: opacity .2s;
  }
  .search-box button:hover { opacity: .85; }
  .search-box button:disabled { opacity: .4; cursor: not-allowed; }
  /* Channel tabs */
  .channels {
    max-width: 900px; margin: 16px auto 0; padding: 0 20px;
    display: flex; gap: 6px; flex-wrap: wrap;
  }
  .channel-btn {
    padding: 6px 14px; border: 1px solid var(--border);
    border-radius: 20px; background: var(--bg2); color: var(--text2);
    font-size: 13px; cursor: pointer; transition: all .2s;
    font-family: var(--font);
  }
  .channel-btn:hover { border-color: var(--text2); color: var(--text); }
  .channel-btn.active {
    background: var(--accent); color: #fff; border-color: var(--accent);
  }
  /* Engine pills */
  .engine-pills {
    max-width: 900px; margin: 12px auto 0; padding: 0 20px;
    display: flex; gap: 6px; flex-wrap: wrap;
  }
  .engine-pill {
    padding: 4px 12px; border-radius: 14px; font-size: 12px;
    cursor: pointer; transition: all .2s; user-select: none;
    border: 1px solid var(--border); background: var(--bg3); color: var(--text2);
  }
  .engine-pill:hover { border-color: var(--text2); }
  .engine-pill.active { background: var(--bg3); border-color: var(--accent); color: var(--accent); }
  .engine-pill .dot {
    display: inline-block; width: 7px; height: 7px; border-radius: 50%;
    margin-right: 5px;
  }
  /* Depth selector */
  .depth-selector {
    max-width: 900px; margin: 12px auto 0; padding: 0 20px;
    display: none; gap: 6px; align-items: center;
  }
  .depth-selector.visible { display: flex; }
  .depth-selector label { color: var(--text2); font-size: 13px; }
  .depth-selector select {
    background: var(--bg3); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 4px 10px; font-size: 13px; font-family: var(--font);
  }
  /* Status bar */
  .status {
    max-width: 900px; margin: 12px auto 0; padding: 0 20px;
    color: var(--text2); font-size: 13px; display: none;
  }
  .status.visible { display: block; }
  /* Results */
  .results-section {
    max-width: 900px; margin: 0 auto; padding: 0 20px 60px;
  }
  .result-card {
    background: var(--bg2); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 16px 20px; margin-top: 12px;
    transition: border-color .15s;
  }
  .result-card:hover { border-color: #484f58; }
  .result-card .source-tag {
    display: inline-block; font-size: 11px; padding: 2px 8px;
    border-radius: 10px; background: var(--bg3); color: var(--text2);
    margin-bottom: 6px;
  }
  .result-card h3 { font-size: 16px; font-weight: 500; margin-bottom: 4px; }
  .result-card h3 a {
    color: var(--accent); text-decoration: none;
  }
  .result-card h3 a:hover { text-decoration: underline; }
  .result-card .url {
    font-size: 12px; color: var(--accent2); margin-bottom: 6px;
    word-break: break-all;
  }
  .result-card .snippet {
    font-size: 14px; color: var(--text2); line-height: 1.5;
  }
  /* Honeycomb info */
  .hc-summary {
    background: var(--bg2); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 16px 20px; margin-top: 12px;
  }
  .hc-summary h3 { font-size: 14px; color: var(--accent3); margin-bottom: 8px; }
  .hc-summary .stat { font-size: 13px; color: var(--text2); margin: 3px 0; }
  .hc-summary .gap-badge {
    display: inline-block; font-size: 11px; padding: 2px 8px;
    border-radius: 10px; background: var(--bg3); color: var(--text2); margin: 2px;
  }
  /* Error */
  .error-card {
    background: #2d1b1b; border: 1px solid #6b3030;
    border-radius: var(--radius); padding: 16px 20px; margin-top: 12px;
    color: #f85149;
  }
  /* Loading */
  .spinner {
    display: inline-block; width: 16px; height: 16px;
    border: 2px solid var(--text2); border-top-color: var(--accent);
    border-radius: 50%; animation: spin .6s linear infinite;
    vertical-align: middle; margin-right: 8px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  /* Responsive */
  @media (max-width: 640px) {
    .search-box { flex-direction: column; padding: 8px; }
    .search-box input { width: 100%; }
    .search-box button { width: 100%; }
  }
</style>
</head>
<body>
<div class="header">
  <h1>Gbase Search</h1>
  <div class="subtitle">Multi-engine · Privacy-first · Meta search</div>
</div>

<div class="search-section">
  <div class="search-box">
    <input type="text" id="search-input" placeholder="Search across 8+ engines..." autofocus
           onkeydown="if(event.key==='Enter') doSearch()">
    <button id="search-btn" onclick="doSearch()">Search</button>
  </div>
</div>

<div class="channels" id="channels"></div>
<div class="engine-pills" id="engine-pills"></div>
<div class="depth-selector" id="depth-selector">
  <label>Depth:</label>
  <select id="depth-select">
    <option value="quick">Quick (1 wave)</option>
    <option value="normal" selected>Normal (2 waves)</option>
    <option value="full">Full (3 waves)</option>
  </select>
</div>
<div class="status" id="status"></div>
<div class="results-section" id="results"></div>

<script>
// Engine configuration
const ENGINES = JSON.parse('{{ENGINES_JSON}}');
const CHANNELS = JSON.parse('{{CHANNELS_JSON}}');
const DEFAULT_ENGINES = ["google","bing","duckduckgo","qwant"];

// State
let selectedChannel = 'all';
let selectedEngines = [...DEFAULT_ENGINES];

// Initialize UI
function init() {
  const channelsDiv = document.getElementById('channels');
  const allBtn = document.createElement('button');
  allBtn.className = 'channel-btn active';
  allBtn.textContent = 'All';
  allBtn.onclick = () => selectChannel('all');
  channelsDiv.appendChild(allBtn);

  Object.entries(CHANNELS).forEach(([key, ch]) => {
    const btn = document.createElement('button');
    btn.className = 'channel-btn';
    btn.textContent = ch.icon + ' ' + ch.label;
    btn.onclick = () => selectChannel(key);
    channelsDiv.appendChild(btn);
  });

  renderEnginePills();
  setChannel('all');
}

function selectChannel(ch) {
  selectedChannel = ch;
  document.querySelectorAll('.channel-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.channel-btn')[ch === 'all' ? 0 : Object.keys(CHANNELS).indexOf(ch) + 1].classList.add('active');

  if (ch === 'all') {
    selectedEngines = Object.keys(ENGINES).filter(e => e !== 'honeycomb');
  } else if (ch === 'meta') {
    selectedEngines = ['honeycomb'];
  } else {
    selectedEngines = Object.keys(ENGINES).filter(e => ENGINES[e].channel === ch);
  }
  renderEnginePills();
  updateDepthVisibility();
}

function renderEnginePills() {
  const div = document.getElementById('engine-pills');
  div.innerHTML = '';
  const colors = { web: '#58a6ff', privacy: '#3fb950', power: '#f0883e', meta: '#a371f7' };
  Object.entries(ENGINES).forEach(([key, eng]) => {
    const pill = document.createElement('span');
    pill.className = 'engine-pill' + (selectedEngines.includes(key) ? ' active' : '');
    pill.innerHTML = `<span class="dot" style="background:${colors[eng.channel]||'#888'}"></span>${eng.icon} ${eng.label}`;
    pill.dataset.engine = key;
    pill.onclick = () => toggleEngine(key);
    div.appendChild(pill);
  });
  updateDepthVisibility();
}

function toggleEngine(key) {
  if (selectedChannel !== 'all') return;
  const idx = selectedEngines.indexOf(key);
  if (idx >= 0) selectedEngines.splice(idx, 1);
  else selectedEngines.push(key);
  renderEnginePills();
}

function updateDepthVisibility() {
  const ds = document.getElementById('depth-selector');
  ds.className = 'depth-selector' + (selectedEngines.includes('honeycomb') ? ' visible' : '');
}

async function doSearch() {
  const q = document.getElementById('search-input').value.trim();
  if (!q) return;
  const btn = document.getElementById('search-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Searching...';
  document.getElementById('results').innerHTML = '';
  const status = document.getElementById('status');
  status.className = 'status visible';
  status.innerHTML = '<span class="spinner"></span> Searching across ' + selectedEngines.length + ' engine(s)...';

  try {
    const resp = await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: q,
        engines: selectedEngines,
        depth: document.getElementById('depth-select').value
      })
    });
    const data = await resp.json();
    renderResults(data, q);
    status.className = 'status';
  } catch (e) {
    status.innerHTML = 'Error: ' + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Search';
  }
}

function renderResults(data, query) {
  const div = document.getElementById('results');
  div.innerHTML = '';

  if (data.error) {
    div.innerHTML = '<div class="error-card">Search failed: ' + data.error + '</div>';
    return;
  }

  // Summary line
  const summary = document.createElement('div');
  summary.style.cssText = 'color: var(--text2); font-size: 13px; margin-top: 12px;';
  summary.textContent = data.engine_count
    ? (data.engine_count + ' engine(s) · ' + data.result_count + ' results for "' + query + '"')
    : (data.results ? data.results.total_unique + ' unique results' : '');
  div.appendChild(summary);

  // Honeycomb: show wave info
  if (data.waves) {
    const hc = document.createElement('div');
    hc.className = 'hc-summary';
    hc.innerHTML = '<h3>🐝 Honeycomb Meta Search</h3>';
    (data.waves || []).forEach(w => {
      const cov = w.coverage || 0;
      hc.innerHTML += '<div class="stat">Wave "' + w.wave + '": ' + w.result_count + ' results, coverage ' + cov + '%' +
        (w.is_saturated ? ' ✅ Saturated' : '') + '</div>';
    });
    hc.innerHTML += '<div class="stat">Total: ' + data.elapsed_s + 's</div>';
    if (data.results) {
      const dims = data.results.by_dimension || {};
      Object.entries(dims).forEach(([dim, items]) => {
        hc.innerHTML += '<div class="stat">— ' + dim + ': ' + items.length + ' results</div>';
      });
    }
    div.appendChild(hc);
  }

  // Results
  let results = [];

  // Parallel search format
  if (data.results && Array.isArray(data.results)) {
    results = data.results;
  }
  // Honeycomb format (by_dimension)
  if (data.results && data.results.by_dimension) {
    Object.values(data.results.by_dimension).forEach(items => {
      items.forEach(item => {
        results.push({
          title: item.title,
          url: item.url,
          snippet: item.snippet,
          _engine: item.source,
          dimension: item.dimension || 'general'
        });
      });
    });
  }
  // Fallback: flat results
  if (data.results && Array.isArray(data.results) && data.results.length > 0 && data.results[0].title !== undefined) {
    results = data.results;
  }

  if (results.length === 0) {
    div.innerHTML += '<div style="color: var(--text2); margin-top: 24px;">No results found for "' + query + '". Try different keywords or engines.</div>';
    return;
  }

  results.forEach(r => {
    const card = document.createElement('div');
    card.className = 'result-card';
    const engine = r._engine || r.source || '?';
    const engCfg = ENGINES[engine] || { icon: '🔍', label: engine };
    const dimTag = r.dimension ? '<span class="source-tag" style="margin-left:4px;">' + r.dimension + '</span>' : '';
    card.innerHTML =
      '<div><span class="source-tag">' + engCfg.icon + ' ' + engCfg.label + '</span>' + dimTag + '</div>' +
      '<h3><a href="' + (r.url || '#') + '" target="_blank" rel="noopener">' + escHtml(r.title || '') + '</a></h3>' +
      '<div class="url">' + escHtml((r.url || '').substring(0, 120)) + '</div>' +
      '<div class="snippet">' + escHtml(r.snippet || '') + '</div>';
    div.appendChild(card);
  });
}

function escHtml(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
init();
</script>
</body>
</html>"""


# ── Routes ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the search dashboard."""
    eng_json = json.dumps(ENGINES_CONFIG, ensure_ascii=False)
    ch_json = json.dumps(ENGINE_CHANNELS, ensure_ascii=False)
    html = HTML_TEMPLATE.replace("{{ENGINES_JSON}}", eng_json).replace("{{CHANNELS_JSON}}", ch_json)
    return HTMLResponse(html)


@app.post("/api/search")
async def search(request: Request):
    """Run search across selected engines."""
    body = await request.json()
    query = body.get("query", "").strip()
    engines = body.get("engines", ["google", "bing", "duckduckgo", "qwant"])
    depth = body.get("depth", "normal")

    if not query:
        return JSONResponse({"error": "Query is required"}, status_code=400)

    start = time.time()

    # Honeycomb branch
    if "honeycomb" in engines:
        result = await _run_honeycomb(query, depth)
        result["elapsed_s"] = round(time.time() - start, 2)
        _save_history(query, engines, len(result.get("results", {}).get("by_dimension", {})) if result else 0)
        return result

    # Parallel branch
    result = await _run_parallel_search(query, engines)
    result["engine_count"] = len(engines)
    result["elapsed_s"] = round(time.time() - start, 2)
    _save_history(query, engines, result["total"])

    return result


@app.get("/api/engines")
async def get_engines():
    """Return engine configuration."""
    return ENGINES_CONFIG


@app.get("/api/channels")
async def get_channels():
    """Return channel configuration."""
    return ENGINE_CHANNELS


@app.get("/api/history")
async def get_history(limit: int = Query(20, ge=1, le=100)):
    """Return search history."""
    return {"history": _load_history(limit)}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "search-webui"}


# ── Standalone runner ──────────────────────────────────

def run(port: int = DEFAULT_PORT):
    """Run the Web UI server."""
    logger.info("=" * 50)
    logger.info("  Gbase Search Web UI")
    logger.info("  Port: %d", port)
    logger.info("  Open: http://localhost:%d", port)
    logger.info("=" * 50)

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    run(port)
