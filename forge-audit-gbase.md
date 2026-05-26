# 🔍 Gbase v0.2.1 — 白盒代码审计报告

> 审计日期：2026-05-26
> 审计范围：`/Users/gary/gbase-release/`（源文件: main.py + lib/ + tools/ + editions/）
> 总计：81 个 .py 文件，约 16,794 行

---

## 📊 综合评分：**6.5 / 10**

| 维度 | 分数（1-10） | 评级 |
|------|:---:|:----:|
| 安全漏洞（路径穿越/shell注入/认证） | 5 | ⚠️ 有风险 |
| 导入错误或死代码 | 7 | 尚可 |
| 硬编码路径/个人数据泄漏 | 7 | 尚可 |
| 中文残留 | 10 | ✅ 无中文残留 |
| Import 体系 | 6 | 有隐患 |
| **加权总分** | **6.5** | **需修复P0/P1** |

> 加权规则：安全 40%, 导入 20%, 硬编码 15%, 中文 10%, import体系 15%

---

## 🚨 P0 — 必须立即修复

### 1. `dag_engine.py:546` — eval() 不受控（安全漏洞）
```
lib/dag_engine.py:546:  return bool(eval(expr, {"__builtins__": {}}, {}))
```
**问题**: `eval()` 即使限制了 `__builtins__`，攻击者仍可通过 `().__class__.__bases__[0].__subclasses__()` 链执行任意代码。`condition` 字段来自 YAML 配置，若 YAML 来源不受信则构成 RCE。
**修复**: 改用 `ast.literal_eval()` 或白名单表达式解析器。
**severity**: P0

### 2. `main.py:372-570` — 所有 HTTP API **无认证**
```
main.py:372   @app.post("/ask")
main.py:386   @app.get("/health")
main.py:390   @app.post("/lifeline/snapshot-before-edit")
main.py:429   @app.post("/pipeline/run")
main.py:470   @app.post("/audit")
main.py:542   @app.post("/hammer/audit")
main.py:549   @app.post("/ink/evaluate")
```
**问题**: 开放所有端点，无 API Key / JWT / Basic Auth 等任何认证机制。snapshot-before-edit 暴露了文件级别写操作。`/audit` 端点直接转发消息给 LLM Kernel 执行，攻击者可利用 Agent 的工具能力。
**修复**: 添加 API Token 认证中间件，至少 Bearer token 校验。
**severity**: P0

### 3. `read_file.py:30` — `ALLOWED_ROOTS` 默认值指向全部家目录
```
tools/read_file.py:30: ... else [os.path.expanduser("~/")]
```
**问题**: 默认允许读取家目录下任意文件，包括 SSH 密钥 (`~/.ssh/id_*`)、token 文件、浏览器 cookie 等敏感数据。若 Agent 被 prompt injection 攻击则泄漏用户隐私。
**修复**: 限制为仅项目根目录，或依赖 `.env` 中的 `GBASE_ALLOWED_ROOTS` 环境变量配置（该环境变量未在 `.env.example` 中声明——不一致）。
**severity**: P0

### 4. `dag_agents.py:88` — 脚本路径可被 env 变量影响
```
lib/dag_agents.py:33:  PROJECT_ROOT = Path(os.getenv("GBASE_ROOT_DIR", "."))
lib/dag_agents.py:88:  script = PROJECT_ROOT / "cron" / "cron-health.sh"
lib/dag_agents.py:119: script = PROJECT_ROOT / "cron" / "arch-audit.py"
```
**问题**: 多个 agent 函数（`agent_health_check`, `agent_arch_audit`, `agent_mirror_decay`）从 `GBASE_ROOT_DIR` env变量构造脚本路径并执行。如果攻击者控制了环境变量，可指向恶意脚本。虽然不直接来自用户输入，但属于环境注入风险。
**修复**: 增加环境变量白名单校验，或 fallback 到编译时路径。
**severity**: P0

---

## 🟠 P1 — 高优先级修复

### 5. `exec.py` / `tools/exec.py` — 路径穿越防御有盲区
```
lib/exec.py:48-57:  通过 target.relative_to(_PROJECT_ROOT) 校验
```
**问题**: 校验仅在 `workdir` 非空时执行。`tools/exec.py` 使用同样的逻辑。但路径穿越防御在 `symlink` 场景下可能失败——`target.resolve()` 跟随符号链接后的路径若绕过 `_PROJECT_ROOT` 前缀则暴露。
**建议**: 在 `resolve()` 之后检查 `_PROJECT_ROOT` 是否为 `target` 的父目录（使用 `in` 或 `startswith`），并且在 Windows 上注意大小写问题。
**severity**: P1

### 6. `kernel.py:58-69` — 跨模块循环导入风险
```
lib/kernel.py:58:  try:
lib/kernel.py:59:      from main import _cfg_get
lib/kernel.py:60:      _NO_CONFIG = False
lib/kernel.py:61:  except ImportError:
lib/kernel.py:62:      _NO_CONFIG = True
```
**问题**: `__init__` 周期导入——`lib/kernel.py` 导入 `main.py`。从 `main.py` 启动时 `from lib.kernel import Kernel` 触发 `try: from main import _cfg_get`，如果在 `_cfg_get` 定义前导入则引发 ImportError。当前虽用 `try/except` 兜底，但容易隐藏 bug（部分配置分支使用默认值而非真实值）。
**修复**: 将配置提取到独立的 `config.py`，或在初始化时显式注入。
**severity**: P1

### 7. `qa_check.py:315` — 死代码引用（模块 `verify` 不存在）
```
tools/qa_check.py:315:  from tools.verify import _assess_content, _rate_source
```
**问题**: `tools/verify.py` **不存在**（目录下无此文件）。运行时在 `qa_check.py:315` 路径会抛出 `ModuleNotFoundError`。这个 lazy import 在 `qa_check.py:311` 的 `try:` 块中包裹，但 `_assess_content` 和 `_rate_source` 无法正常调用。
**结果**: `qa_verify_swarm()` 函数在不触发 `ModuleNotFoundError` 时（lazy import 兜底后）也会因为无法导入 `verify` 模块而功能受损。
**severity**: P1

### 8. `distill.py:292` — 函数名与 Python 内置函数冲突
```
tools/distill.py:292:  def _do_eval(model: str = "opprime-7b"):
tools/distill.py:499:  async def distill_eval(model: str = "opprime-7b") -> dict:
```
**问题**: 函数名为 `eval`，是 Python 内置函数。`distill_eval` 也使用了 `eval` 后缀。虽然加了 `_do` 前缀和 `distill_` 前缀不易混淆，但仍容易造成阅读歧义。
**建议**: 改名 `_do_evals` / `distill_evaluate` 以降低混淆。
**severity**: P1

### 9. `rss_fetcher.py` 和多处 — 缺少 SSL 证书验证配置
```
lib/rss_fetcher.py:177:  with urllib.request.urlopen(req, timeout=timeout) as resp:
tools/honeycomb_search.py:157:  asyncio.get_event_loop().run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=timeout)),
tools/search_tunnel.py:33:  with urllib.request.urlopen(req, timeout=TUNNEL_TIMEOUT) as resp:
```
**问题**: 使用 `urllib.request.urlopen()` 未指定 `context=ssl.create_default_context()`。某些代理环境或系统 Python 编译配置下可能跳过证书验证，导致 MITM 攻击风险。
**修复**: 显式传递 SSL context。
**severity**: P1

### 10. `rss_fetcher.py` + 多处 — `open()` 不带 `encoding` 参数
```
lib/evolution_engine.py:95:    with open(RULES_PATH) as f:
lib/evolution_engine.py:100:   with open(RULES_PATH, "w") as f:
lib/dag_engine.py:187:        with open(wf_path) as f:
lib/dag_engine.py:200:        with open(wf_path, "w") as f:
lib/backup.py:60:        with open(INDEX_PATH) as f:
lib/backup.py:68:        with open(INDEX_PATH, "w") as f:
lib/rss_fetcher.py:140:   with open(RSS_TOPICS_PATH, "w", encoding="utf-8") as f:
lib/rss_fetcher.py:148:   with open(RSS_TOPICS_PATH, encoding="utf-8") as f:
```
**问题**: 部分 `open()` 调用了 `encoding` 参数，部分没有。不带 `encoding="utf-8"` 的版本在不同平台（Windows vs Linux/macOS）上使用不同默认编码，导致跨平台运行时出错。
**修复**: 所有文本 `open()` 调用统一添加 `encoding="utf-8"`。
**severity**: P1

---

## 🟡 P2 — 建议修复

### 11. `dag_agents.py:60` — Linux-only 路径硬编码
```
lib/dag_agents.py:60:  with open("/proc/meminfo") as f:
```
**问题**: `agent_health_check` 尝试读取 `/proc/meminfo`。在 macOS 上（项目运行在 Mac Studio M4 MAX）该路径不存在，函数会抛出 `FileNotFoundError`。`try` 块兜底但返回空数据。
**修复**: 添加 `sys.platform` 检测，macOS 上使用 `sysctl hw.memsize` 或 `psutil`。
**severity**: P2

### 12. `backup.py:77` — 使用 MD5（非安全用途）
```
lib/backup.py:77:  h = hashlib.md5(...).hexdigest()[:6]
```
**问题**: MD5 用于构建备份 ID，非安全哈希用途（仅去重/防冲突），风险较低。但作为强调安全的框架，建议改用 `secrets.token_hex(8)` 或 `uuid4()` 代替。
**建议**: 使用 `os.urandom(4).hex()` 替代 MD5 作为随机后缀。
**severity**: P2

### 13. `tools/exec.py` + `lib/exec.py` — 同名工具函数（潜在冲突）
```
lib/exec.py:28:  async def exec_command(...) -> dict:
tools/exec.py:64:  async def exec_command(...) -> dict:
```
**问题**: 两个不同模块定义了完全同名的工具函数 `exec_command`，均被 `@tool()` 注册到全局注册表。后注册的会覆盖先注册的，取决于工具加载顺序。
**建议**: 统一为单一实现或将其中一个改名。
**severity**: P2

### 14. `search.py:381` — lazy import 到自身模块
```
tools/search.py:381:  from tools.search_tunnel import search_via_tunnel
```
**问题**: 看似正常，但 `search_tunnel` 模块也存在类似的方式。属于跨模块依赖的下行 risk。无明显 bug，但建议拆分为公共模块。
**建议**: 将 `search_tunnel` 和 `search_bridge` 的逻辑提取到 `lib/` 层。
**severity**: P2

### 15. 多处 — `.gitignore` 未防护敏感文件
```
requirements.txt 中没有 fastapi/uvicorn 等依赖声明
```
**问题**: `.gitignore`（参考 `.gitignore` 文件内容）应确保 `.env` 和 `*.key` 文件被排除。`.env.example` 包含了示例 API key 模板，但实际代码运行时依赖的 `.env` 是否被 gitignore 保护是关键。
**修复**: 核实 `.gitignore` 包含 `.env`、`*secret*`、`*token*` 等模式。
**severity**: P2

### 16. `scheduler.py:24` — 硬编码临时文件路径（安全/竞争）
```
lib/scheduler.py:24:  HEARTBEAT_PATH = "/tmp/opprime_heartbeat"
```
**问题**: 使用固定 `/tmp/` 路径，多实例运行时存在竞争条件。心搏信号文件被任意进程可读（默认 `/tmp` 权限）。
**修复**: 使用 `tempfile` 或 `GBASE_DATA_DIR` 下的子目录。
**severity**: P2

### 17. `.env.example` — 默认包含 `OPENAI_API_KEY` 占位符
```
.env.example:5:  OPENAI_API_KEY=sk-your-api-key-here
```
**问题**: 虽已注释为示例，但如果开发者直接将 `.env.example` 复制为 `.env` 而未替换 key，可能意外使用无效 key。风险较低。
**建议**: key 占位符使用 `OPENAI_API_KEY=__REPLACE_ME__` 更清晰。
**severity**: P2

### 18. `main.py:20` — `sys.path.insert(0, ...)` 全局路径劫持
```
main.py:20:  sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
```
**问题**: 将项目根目录插入 `sys.path` 索引 0，优先级高于标准库。如果项目内有文件名为 `json.py`、`os.py` 等会导致标准库被劫持。
**建议**: 使用 `sys.path.insert(1, ...)` 或规范化包导入。
**severity**: P2

---

## 📋 导入体系分析

### 当前模式
- **绝对导入为主**：`from lib.toolkit import tool`, `from lib.mirror import Mirror` ✅
- **相对导入**：**无** `from ..` 相对导入 ✅（全部使用绝对导入）
- **`__init__.py`**：
  - `lib/__init__.py` → **空文件** ✅
  - `tools/__init__.py` → ~620 行，极重，负责注册 + 关键词路由 + 平台映射 ⚠️
  - `editions/__init__.py` → 轻量 ⚠️ 需确认
- **循环导入**：`kernel.py → main.py`（已用 try/except 兜底）⚠️

### 问题汇总
| 文件 | 问题 | 类型 |
|------|------|------|
| `qa_check.py:315` | `from tools.verify import ...` — `verify.py` 不存在 | 死代码引用 |
| `kernel.py:58` | `from main import _cfg_get` — 循环导入 | 循环依赖 |
| `search.py:381` | `from tools.search_tunnel import ...` — 可行但笨重 | 跨模块耦合 |
| 全局 | 无 `from __future__ import annotations` 或 `annotations` 支持 | 可选改进 |

---

## ⚡ 攻击面总览

```
┌────────────────────────────────────────────────────────────┐
│                    攻击面矩阵                               │
├──────────────────────┬─────────────────────────────────────┤
│ 无认证 HTTP API      │ P0 · 所有端点未鉴权                  │
│ eval() in DAG        │ P0 · YAML->eval 可 RCE              │
│ 文件读超越家目录     │ P0 · read_file 默认 ~/              │
│ 环境变量注入可执行   │ P0 · GBASE_ROOT_DIR 指向恶意脚本     │
│ SSL 校验缺失(多处)   │ P1 · URL fetched 无证书验证          │
│ open() 缺 encoding   │ P1 · 跨平台编码问题                  │
│ 同名 exec_command    │ P2 · 注册表覆盖                      │
│ /tmp 固定路径        │ P2 · 竞争条件/信息泄漏               │
│ sys.path[0] 劫持     │ P2 · 潜在标准库覆盖                  │
└──────────────────────┴─────────────────────────────────────┘
```

---

## ✅ 修复优先级

### 必须修复（4项）
| # | 文件:行 | 问题 | 复杂度 |
|---|---------|------|--------|
| 1 | `lib/dag_engine.py:546` | eval() → ast.literal_eval() | 🟢 低 |
| 2 | `main.py:372-570` | 所有 API 加认证 | 🟡 中 |
| 3 | `tools/read_file.py:30` | ALLOWED_ROOTS 缩小范围 | 🟢 低 |
| 4 | `lib/dag_agents.py:33,88,119` | GBASE_ROOT_DIR 输入校验 | 🟢 低 |

### 高优先级修复（6项）
| # | 文件:行 | 问题 | 复杂度 |
|---|---------|------|--------|
| 5 | `lib/exec.py:48` | 路径穿越盲区（symlink） | 🟢 低 |
| 6 | `lib/kernel.py:58-69` | 循环导入 → 独立 config | 🟡 中 |
| 7 | `tools/qa_check.py:315` | 死代码引用 verify 模块 | 🟢 低 |
| 8 | `tools/distill.py:292` | 函数名与内置冲突 | 🟢 低 |
| 9 | 多处 `urlopen()` | SSL context 参数缺失 | 🟢 低 |
| 10 | 多处 `open()` | 统一 encoding="utf-8" | 🟢 低 |

### 建议修复（7项）
| # | 文件 | 问题 | 复杂度 |
|---|------|------|--------|
| 11 | `lib/dag_agents.py:60` | /proc/meminfo macOS 兼容 | 🟢 低 |
| 12 | `lib/backup.py:77` | MD5 → uuid/urandom | 🟢 低 |
| 13 | `lib/exec.py` / `tools/exec.py` | 同名冲突 | 🟡 中 |
| 14 | `tools/search.py:381` | lazy import 重组 | 🟡 中 |
| 15 | 项目根 | .gitignore 保护敏感文件 | 🟢 低 |
| 16 | `lib/scheduler.py:24` | /tmp 固定路径 | 🟢 低 |
| 17 | `.env.example` | key 占位符格式 | 🟢 低 |
| 18 | `main.py:20` | sys.path[0] → [1] | 🟢 低 |

---

## 💡 总体评价

Gbase v0.2.1 代码整体质量**尚可**，文档齐全（MIT 许可证、README、SPDX 头），中文零残留。代码架构清晰（3层架构——入口/main.py → 核心 lib/ → 工具 tools/），使用现代 async/await 模式。

**但是存在严重的攻击面暴露问题**，尤其是无认证的 HTTP API 和宽松的文件读取范围，使攻击者可以：
1. 向 `/ask` 端点发送恶意 prompt → 通过 LLM 调用任意工具
2. 通过 `read_file` 读取 SSH 密钥、浏览器 cookie、token 文件
3. 通过 `eval()` 在 YAML 工作流中执行任意表达式

这些安全问题对于 AI Agent 框架来说风险极高——因为 Agent 本身就是用来执行命令和读写文件的。

**建议**: 在发布 v0.2.2 前至少修复 P0 级别 4 个问题，尤其是 API 认证和 eval()。
