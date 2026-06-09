#!/usr/bin/env python3
"""
gbase.py — GBase framework entry point

Usage:
    python3 main.py                     # Feishu bot mode (default)
    python3 main.py --mode web          # Web chat interface (browser)
    python3 main.py --mode web --port 8765
"""

import asyncio
import atexit
import contextlib
import logging
import os
import sys
import time
from pathlib import Path

# ── 路径 ──
# 去掉 gbase 包在 site-packages 中的 lib/ 冲突（它的 lib 不包含 channels/）
sys.path = [p for p in sys.path if "site-packages" not in p or "gbase" not in p.lower()]
sys.path.insert(0, str(Path(__file__).parent))

logger = logging.getLogger("gbase-gbase")

# ── 加载 .env（与 gbase/main.py 相同的逻辑） ──
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#"):
                continue
            if "=" not in _line:
                continue
            _key, _value = _line.split("=", 1)
            _key, _value = _key.strip(), _value.strip()
            if not os.environ.get(_key):
                os.environ[_key] = _value
    logger.info(".env 已加载 (%s)", _env_path)

# ── 飞书 Bot 配置（从环境变量读取，不硬编码） ──
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
ENCRYPT_KEY = os.environ.get("FEISHU_ENCRYPT_KEY", "")
PORT = int(os.environ.get("FEISHU_PORT", "8440"))

# 启动时校验必备配置
if not APP_ID or not APP_SECRET or not ENCRYPT_KEY:
    logger.warning("飞书 Bot 配置不完整：请设置 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_ENCRYPT_KEY 环境变量")

# ── GBase/GBase 内核配置 ──
IDENTITY_NAME = "gbase"
MODEL = "deepseek-chat"
DEEPSEEK_API_KEY = os.environ.get("OPPRIME_DEEPSEEK_API_KEY", "")
DATA_DIR = str(Path(__file__).parent / "data")


def _ensure_dirs():
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)


async def run():
    import uvicorn

    os.environ.setdefault("GBASE_DATA_DIR", DATA_DIR)
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from openai import AsyncOpenAI

    from lib.channels.feishu import FeishuChannel
    from lib.experience import ExperienceEngine
    from lib.identity import load_identity
    from lib.kernel import Kernel
    from lib.mirror import Mirror
    from lib.storage import Storage
    from tools.mirror_tool import set_mirror_instance

    _ensure_dirs()

    # ── 日志：按日期切割，保留 90 天 ──
    import logging.handlers

    _file_handler = logging.handlers.TimedRotatingFileHandler(
        str(Path(DATA_DIR) / "gbase.log"),
        when="midnight",
        interval=1,
        backupCount=90,
        encoding="utf-8",
    )
    _file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    _file_handler.suffix = "%Y-%m-%d"
    logger.addHandler(_file_handler)
    logger.setLevel(logging.INFO)

    # ── 存储引擎 ──
    storage = Storage(data_dir=DATA_DIR)
    storage.setup()
    exp = ExperienceEngine(storage)

    # ── 鉴面引擎 ──
    mirror_path = str(Path(DATA_DIR) / "mirror.db")
    mirror = Mirror(db_path=mirror_path)
    mirror.setup()
    set_mirror_instance(mirror)
    mstats = mirror.get_stats()
    logger.info(
        "鉴面引擎: %d 活跃记忆, %d 已遗忘 (db=%s)",
        mstats["total_active"],
        mstats["total_forgotten"],
        mirror._db_path,
    )

    # ── LLM 客户端 ──
    api_key = DEEPSEEK_API_KEY
    if not api_key:
        logger.error("OPPRIME_DEEPSEEK_API_KEY 未设置")
        print("❌ 请设置 OPPRIME_DEEPSEEK_API_KEY 环境变量")
        sys.exit(1)

    client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    model = MODEL

    # ── 身份 + kernel ──
    identity = load_identity(
        IDENTITY_NAME,
        root_dir=str(Path(__file__).parent / "identities"),
        experience_engine=exp,
    )
    system_prompt = identity.get_system_prompt()
    logger.info("身份: %s (%d chars)", IDENTITY_NAME, len(system_prompt))

    kernel = Kernel(
        client=client,
        model=model,
        system_prompt=system_prompt,
        experience_engine=exp,
        mirror_engine=mirror,
        data_dir=DATA_DIR,
    )

    # ── 飞书通道 ──
    channel = FeishuChannel(
        app_id=APP_ID,
        app_secret=APP_SECRET,
        encrypt_key=ENCRYPT_KEY,
    )
    channel.set_kernel(kernel)
    from lib.toolkit import auto_scan
    from lib.toolkit import set_global as tk_set_global
    from tools import register_default

    tk_set_global("feishu_channel", channel)
    tk_set_global("storage", storage)
    tk_set_global("experience", exp)
    register_default()
    auto_scan("tools")

    # ── 定时调度器 + RSS 自主学习 ──
    from lib.auto_learn import AutoLearner
    from lib.scheduler import CronScheduler

    scheduler = CronScheduler(db_path=str(Path(DATA_DIR) / "cron.db"))
    scheduler.set_sender(channel.send_text)
    tk_set_global("scheduler", scheduler)
    logger.info("定时调度器已初始化")

    async def _auto_learn_run(msg: str, platform: str = "auto_learn", session=None):
        return await kernel.run(
            user_message=msg,
            platform=platform,
            session=session,
        )

    auto_learner = AutoLearner(channel.send_text, _auto_learn_run)
    learn_owner = os.environ.get("OPPRIME_LEARN_OWNER", "")
    if learn_owner:
        auto_learner.set_owner(learn_owner)
    tk_set_global("auto_learner", auto_learner)
    scheduler.set_learner(auto_learner)
    logger.info("自主学习引擎已挂载")

    # ── 注册 RSS 学习定时任务 ──
    try:
        existing = scheduler.list_jobs()
        has_learn = any("学习" in j.get("message", "") for j in existing)
        if not has_learn:
            msg = "本时段自主学习任务：请阅读所有 RSS 学习方向的最新文章，理解知识并沉淀到 memory。完成后给用户发学习报告。"
            import time as _t

            first_utc = _t.time() + 120  # 2 分钟后首次
            scheduler.add_job(
                {"type": "every", "interval": 43200, "first_run": first_utc},
                msg,
                owner_id=learn_owner or "",
                action="learn",
            )
            logger.info("已注册 RSS 自主学习定时任务 (2分钟后首次, 每12小时)")
        else:
            logger.info("RSS 自主学习任务已存在，跳过")
    except Exception as e:
        logger.error("注册 RSS 学习任务失败: %s", e)

    # ── 注册离线巩固周期（睡眠模块） ──
    from lib.sleep_cycle import run_sleep_cycle

    async def _sleep_cycle_callback(job_id: int, _message: str, owner_id: str):
        """睡眠周期回调：session 压缩 + mirror 修剪 + 梯度汇总 + baseline 自动保存。"""
        logger.info("💤 睡眠周期开始 (job=%d)", job_id)
        try:
            report = run_sleep_cycle(
                mirror_db=mirror._db_path if hasattr(mirror, "_db_path") else str(Path(DATA_DIR) / "mirror.db"),
                storage=storage,
                session_dir=str(Path(DATA_DIR) / "sessions"),
                mirror_instance=mirror,
            )

            # Phase 1: 自动保存 mirror baseline（冗余备份 #65）
            baseline_path = ""
            try:
                baseline_path = mirror.save_baseline(label="auto", data_dir=str(Path(DATA_DIR) / "metrics"))
                if baseline_path:
                    logger.info("Mirror baseline 已保存: %s", baseline_path)
            except Exception as be:
                logger.warning("Baseline 保存失败（非严重）: %s", be)

            # Phase 2: defragment — 为经验库加推论阶梯标签 & metrics
            try:
                metrics_dir = Path(DATA_DIR) / "metrics"
                # 统计 rsi_quality.jsonl 行数
                qf = metrics_dir / "rsi_quality.jsonl"
                qcount = len(qf.read_text(encoding="utf-8").strip().split("\n")) if qf.exists() else 0
                # 统计 rsi_ladder.jsonl 行数
                lf = metrics_dir / "rsi_ladder.jsonl"
                lcount = len(lf.read_text(encoding="utf-8").strip().split("\n")) if lf.exists() else 0
                metrics_summary = f"RSI 指标: {qcount} quality + {lcount} ladder 条目"
            except Exception:
                metrics_summary = "RSI 指标统计失败"

            summary = (
                f"💤 睡眠周期完成 ({report['total_time_s']}s)\n"
                f"• Sessions: {report['stage']['sessions']['compressed']} compressed / {report['stage']['sessions']['total']} total\n"
                f"• Mirror: {report['stage']['mirror']['pruned']} pruned / {report['stage']['mirror']['total_active']} active\n"
                f"• Baseline: {'saved' if baseline_path else 'skipped'}\n"
                f"• {metrics_summary}\n"
                f"• Mirror DB: {report.get('mirror_db_size_mb', '?')} MB"
            )
            logger.info(summary)
            # 给主人发飞书通知
            if owner_id:
                try:
                    await channel.send_text(owner_id, summary)
                except Exception as se:
                    logger.warning("睡眠通知发送失败: %s", se)
        except Exception as e:
            logger.error("睡眠周期异常: %s", e, exc_info=True)

    scheduler.register_callback("sleep", _sleep_cycle_callback)
    try:
        existing = scheduler.list_jobs()
        has_sleep = any("sleep" in j.get("action", "") for j in existing)
        if not has_sleep:
            _sleep_first = time.time() + 600  # 10分钟后首次
            scheduler.add_job(
                {"type": "every", "interval": 86400, "first_run": _sleep_first},
                "💤 离线巩固周期: session 压缩 + mirror 修剪 + 梯度汇总",
                owner_id=learn_owner or "",
                action="sleep",
            )
            logger.info("已注册离线巩固周期 (10分钟后首次, 每24小时)")
        else:
            logger.info("离线巩固周期已存在，跳过")
    except Exception as e:
        logger.error("注册睡眠周期失败: %s", e)

    # ── FastAPI ──
    app = FastAPI(title="Gundam GBase")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/health")
    async def health():
        return {"status": "ok", "app": "gbase-gbase", "port": PORT}

    @app.post("/feishu/webhook")
    @app.post("/")
    async def feishu_webhook(request: Request):
        raw = await request.body()
        logger.info("飞书 webhook: %d bytes", len(raw))
        try:
            result = await channel.handle_event(raw)
            return result
        except Exception as e:
            logger.error("webhook 处理异常: %s", e, exc_info=True)
            return {"code": 0}

    @app.post("/ask")
    async def ask(request: Request):
        """
        HTTP ask 端点——扎古师父用来测试记忆用的。
        直接发文本，直接收回复，走 kernel.run 不走飞书通道。
        支持 session_id 隔离不同会话。
        """
        body = await request.json()
        message = body.get("message", "")
        use_session = body.get("session", True)
        session_id = body.get("session_id", "default")
        logger.info("Ask endpoint: %s (session=%s, id=%s)", message[:80], use_session, session_id)

        from lib.session import JsonlSessionManager

        _session = None
        if use_session:
            safe_id = session_id.replace("/", "_").replace("\\", "_").strip()
            session_path = str(Path(__file__).parent / "data" / f"ask-session-{safe_id}.jsonl")
            _session = JsonlSessionManager(session_path, max_context=200)

        try:
            response = await kernel.run(
                user_message=message,
                platform="ask",
                session=_session,
                max_seconds=180,
            )
            if _session:
                _session.close()
            return {"reply": response, "status": "ok"}
        except Exception as e:
            logger.error("Ask 端点异常: %s", e, exc_info=True)
            return {"reply": f"[Error: {e}]", "status": "error"}

    # ── P1: 异步 Startup 守护 ──
    async def _startup_guard():
        """Startup 5秒后执行自检：记忆健康 + 端口可达"""
        await asyncio.sleep(5)
        errors = []
        try:
            _st = storage
            if _st and _st._conn:
                _k = _st._conn.execute("SELECT COUNT(*) FROM entries WHERE type='knowledge'").fetchone()[0]
                _e = _st._conn.execute("SELECT COUNT(*) FROM entries WHERE type='experience'").fetchone()[0]
                logger.info("🧠 记忆健康: knowledge=%d, experience=%d", _k, _e)
                _st._conn.execute("SELECT 1").fetchone()
            else:
                errors.append("memory/storage 未初始化")
            import httpx as _httpx

            _port_ok = False
            for _retry in range(3):
                try:
                    _r = _httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=3)
                    if _r.status_code == 200:
                        logger.info("✅ 端口自检: 127.0.0.1:%d/health → %d", PORT, _r.status_code)
                        _port_ok = True
                        break
                except Exception:
                    if _retry < 2:
                        await asyncio.sleep(1)
            if not _port_ok:
                errors.append(f"端口 {PORT} 自检超时")
        except Exception as _e:
            errors.append(str(_e))
        if errors:
            logger.warning("⚠️ Startup 自检发现 %d 个问题: %s", len(errors), "; ".join(errors))
        else:
            logger.info("✅ Startup 自检全部通过")
            # 🚀 RSI: 启动后执行一次完整进化周期
            try:
                from lib.evolution_engine import full_evolution_cycle

                await full_evolution_cycle()
                logger.info("🚀 RSI 进化周期完成")
            except Exception as _evo_e:
                logger.warning("⚠️ RSI 进化周期异常: %s", _evo_e)

    # ── 启动 ──
    asyncio.create_task(channel.start_heartbeat())
    asyncio.create_task(_startup_guard())
    logger.info("━━━━━━━━━━━━━━━━━━━")
    logger.info("GBase 飞书通道启动")
    logger.info(f"端口: {PORT}, Bot: {APP_ID}")
    logger.info(f"身份: {IDENTITY_NAME}, 模型: {model}")
    logger.info(f"数据目录: {DATA_DIR}")
    logger.info("━━━━━━━━━━━━━━━━━━━")

    # 周期性 WAL checkpoint
    async def _periodic_wal_checkpoint():
        while True:
            await asyncio.sleep(300)
            try:
                if storage and storage._conn:
                    cursor = storage._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    _, pages, _ = cursor.fetchone()
                    if pages > 0:
                        logger.info("WAL checkpoint: %d pages", pages)
            except Exception as e:
                logger.warning("WAL checkpoint 失败: %s", e)

    asyncio.create_task(_periodic_wal_checkpoint())

    @atexit.register
    def _shutdown_checkpoint():
        with contextlib.suppress(Exception):
            storage.close()

    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def _run_web():
    """Web chat mode (browser interface)."""
    import uvicorn

    os.environ.setdefault("GBASE_DATA_DIR", DATA_DIR)
    from openai import AsyncOpenAI

    from lib.experience import ExperienceEngine
    from lib.identity import load_identity
    from lib.kernel import Kernel
    from lib.mirror import Mirror
    from lib.storage import Storage
    from tools.mirror_tool import set_mirror_instance

    _ensure_dirs()

    # ── Logging ──
    import logging.handlers

    _file_handler = logging.handlers.TimedRotatingFileHandler(
        str(Path(DATA_DIR) / "gbase-web.log"),
        when="midnight",
        interval=1,
        backupCount=90,
        encoding="utf-8",
    )
    _file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    _file_handler.suffix = "%Y-%m-%d"
    logger.addHandler(_file_handler)
    logger.setLevel(logging.INFO)

    # ── Storage ──
    storage = Storage(data_dir=DATA_DIR)
    storage.setup()
    exp = ExperienceEngine(storage)

    # ── Mirror ──
    mirror_path = str(Path(DATA_DIR) / "mirror.db")
    mirror = Mirror(db_path=mirror_path)
    mirror.setup()
    set_mirror_instance(mirror)
    mstats = mirror.get_stats()
    logger.info("鉴面引擎: %d 活跃记忆, %d 已遗忘", mstats["total_active"], mstats["total_forgotten"])

    # ── LLM client ──
    api_key = DEEPSEEK_API_KEY
    client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")

    # ── Identity + Kernel ──
    identity = load_identity(
        IDENTITY_NAME,
        root_dir=str(Path(__file__).parent / "identities"),
        experience_engine=exp,
    )
    kernel = Kernel(
        client=client,
        model=MODEL,
        system_prompt=identity.get_system_prompt(),
        experience_engine=exp,
        mirror_engine=mirror,
        data_dir=DATA_DIR,
    )

    from lib.toolkit import auto_scan
    from lib.toolkit import set_global as tk_set_global
    from tools import register_default

    tk_set_global("storage", storage)
    tk_set_global("experience", exp)
    register_default()
    auto_scan("tools")

    # ── WebChat channel ──
    from lib.channels.webchat import WebChatChannel

    channel = WebChatChannel(kernel=kernel, storage=storage, data_dir=DATA_DIR)
    app = channel.create_app(title="GBase Web Chat")

    logger.info("━━━━━━━━━━━━━━━━━━━")
    logger.info("GBase Web Chat 启动")
    logger.info(f"端口: {WEB_PORT}, 模型: {MODEL}")
    logger.info(f"数据目录: {DATA_DIR}")
    logger.info(f"访问: http://localhost:{WEB_PORT}")
    logger.info("━━━━━━━━━━━━━━━━━━━")

    config = uvicorn.Config(app, host="0.0.0.0", port=WEB_PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    import sys

    # Simple CLI arg parsing
    args = sys.argv[1:]
    MODE = "feishu"
    WEB_PORT = int(os.environ.get("GBASE_WEB_PORT", "8765"))

    for i, arg in enumerate(args):
        if arg == "--mode" and i + 1 < len(args):
            MODE = args[i + 1]
        if arg == "--port" and i + 1 < len(args):
            WEB_PORT = int(args[i + 1])
        if arg in ("-m", "--mode"):
            pass  # handled

    if MODE == "web":
        asyncio.run(_run_web())
    else:
        asyncio.run(run())
