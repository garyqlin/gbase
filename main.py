# SPDX-License-Identifier: MIT
"""
opprime-core-v2/main.py

Opprime Core v2 Startup entry point.

Identity: set via IDENTITY env var or --identity flag.
Platform: set via --platform (feishu / cli).

用法：
    # CLI 模式（DefaultIdentity）
    python3 main.py cli

    # CLI + 宇宙核心Identity
    IDENTITY=universe-core python3 main.py cli

    # Feishu模式（标准版，Default）
    python3 main.py feishu

    # Feishu模式显式指定Identity和端口
    IDENTITY=standard python3 main.py feishu 8420
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# 将项目根Directory加入 Python Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("opprime")

# ── .env 加载 ──────────────────────────────────────────


def _load_env():
    """加载 .env File（如果存在）。"""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if not os.environ.get(key):
                os.environ[key] = value
    logger.info(".env Loaded (%s)", env_path)


_load_env()

import contextlib
import json

from openai import AsyncOpenAI

from editions import get_edition
from lib.auto_learn import AutoLearner
from lib.experience import ExperienceEngine
from lib.identity import load_identity
from lib.kernel import Kernel
from lib.lifeline import get_current_commit, git_available
from lib.mirror import Mirror
from lib.pipeline import list_pipelines, rerun_step, run_gate
from lib.scheduler import CronScheduler
from lib.skill_loader import SkillLoader
from lib.storage import Storage
from lib.toolkit import auto_scan, set_global
from tools import register_default

try:
    from tools.mirror_tool import set_mirror_instance
except ImportError:

    def set_mirror_instance(m):
        pass

# ── 初始化 ──────────────────────────────────────────────


# ── 多模型Configuration ──────────────────────────────────────────
# OPPRIME_MODEL 支持简写名或完整模型名
_MODEL_ALIASES = {
    "pro": "qwen3.7-plus",
    "flash": "deepseek-v4-flash",
    "ds": "qwen3.7-plus",
    "minimax": "MiniMax-M3",
}


def _resolve_model() -> str:
    model = os.environ.get("OPPRIME_MODEL", "pro")
    resolved = _MODEL_ALIASES.get(model, model)
    if resolved == model:
        logger.info("LLM 模型: %s (直接指定)", model)
    else:
        logger.info("LLM 模型: %s (简写 %s → %s)", resolved, model, resolved)
    return resolved


def _estimate_complexity(user_message: str) -> str:
    """估算任务复杂度，返回路由建议。

    信号：
    - 代码/编程相关 → 'ds' (DeepSeek 擅长代码)
    - 长消息(>200字) + 多步骤关键词 → 'pro'
    - 短消息/简单问答 → 'flash'
    - Default → 'pro'
    """
    msg_lower = user_message.lower()
    msg_len = len(user_message)

    # 代码相关 → DeepSeek
    code_keywords = [
        "写代码",
        "实现",
        "编程",
        "debug",
        "bug",
        "修复",
        "函数",
        "python",
        "javascript",
        "算法",
        "数据库",
        "sql",
        "api",
        "类",
        "模块",
        "重构",
        "优化代码",
        "编译",
    ]
    if any(kw in msg_lower for kw in code_keywords):
        return "ds"

    # 复杂多步骤 → Pro
    complex_keywords = [
        "方案",
        "设计",
        "架构",
        "分析",
        "评估",
        "规划",
        "部署",
        "Configuration",
        "审查",
        "报告",
        "总结",
        "迁移",
    ]
    if msg_len > 200 or any(kw in msg_lower for kw in complex_keywords):
        return "pro"

    # 简单问答 → Flash
    return "flash"


def _auto_route_model(user_message: str, default_model: str) -> str:
    """自动路由：根据任务复杂度选择模型。

    仅在未显式设置 OPPRIME_MODEL 时生效。
    """
    if os.environ.get("OPPRIME_MODEL"):
        return default_model  # 用户显式指定，不覆盖

    complexity = _estimate_complexity(user_message)
    route_map = {
        "ds": "deepseek-chat",
        "pro": _MODEL_ALIASES.get("pro", "doubao-seed-2-0-pro-260215"),
        "flash": _MODEL_ALIASES.get("flash", "doubao-seed-1-6-flash-250615"),
    }
    routed = route_map.get(complexity, default_model)
    logger.info("自动路由: 复杂度=%s → 模型=%s (原Default=%s)", complexity, routed, default_model)
    return routed


def _check_lifeline():
    """startup self-check: snapshot status"""
    if not git_available():
        logger.warning("LIFELINE WARN: git not available, snapshot limited")
        return
    commit = get_current_commit()
    from lib.lifeline import list_snapshots

    snaps = list_snapshots(limit=5)
    if snaps:
        logger.info("LIFELINE: HEAD=%s, last 5 snapshots:", commit)
        for s in snaps[:5]:
            logger.info("  - %s | %s | %s", s["tag"], s["reason"][:30], s["timestamp"][:19])
    else:
        logger.info("LIFELINE: HEAD=%s, no snapshots yet", commit)


def _init_sandbox():
    """初始化沙箱安全推演（可被 `--edition` 和 `--arm` 模式共用）。"""
    from lib.sandbox_safety import lint

    base = Path(__file__).parent
    checks = ["main.py", "lib/kernel.py", "lib/session.py", "lib/toolkit.py", "lib/mirror.py", "editions/__init__.py"]
    for rel in checks:
        fp = base / rel
        if not fp.exists():
            logger.warning("🚫 沙箱推演: 关键File缺失 %s", fp)
        else:
            issues = lint(str(fp))
            if issues:
                for issue in issues:
                    logger.warning("  ⚠ 版本兼容: %s", issue)
    logger.info("沙箱推演: 关键File完整性检查通过")


def _setup():
    """Startup时的初始化（Tools扫描 + 注册 + 安全推演检查）。"""
    auto_scan("tools")
    register_default()
    _check_lifeline()
    _init_sandbox()


# ── CLI 模式 ────────────────────────────────────────────


async def cli_mode(identity_name: str = "default"):
    # CLI 模式也用Default data Directory
    _data_dir = None
    # 初始化存储引擎 + 经验引擎
    storage = Storage(data_dir=_data_dir)
    if _data_dir:
        # 种子经验：如果Armor经验库为空，从主库拷贝 Prime 种子
        exp_path = Path(_data_dir) / "experience.jsonl"
        if not exp_path.exists():
            src_path = Path(__file__).parent / "data" / "experience.jsonl"
            if src_path.exists():
                import shutil

                shutil.copy2(str(src_path), str(exp_path))
                logger.info("Armor种子经验已复制: %s (%d 条)", exp_path, sum(1 for _ in open(exp_path)))
    storage.setup()
    exp = ExperienceEngine(storage)

    # ── 初始化鉴面引擎（_data_dir 有值则使用独立的Armor mirror.db） ──
    mirror_path = str(Path(_data_dir) / "mirror.db") if _data_dir else None
    mirror = Mirror(db_path=mirror_path)
    mirror.setup()
    set_mirror_instance(mirror)
    mstats = mirror.get_stats()
    logger.info(
        "鉴面引擎: %d 活跃记忆, %d 已遗忘 (db=%s)", mstats["total_active"], mstats["total_forgotten"], mirror._db_path
    )
    # 初始化 skill 加载器
    skill_loader = SkillLoader("skills")
    skill_loader.load()
    skill_names = skill_loader.get_skill_names()
    logger.info("Skill 加载: %s 个 (%s)", len(skill_names), ", ".join(skill_names) if skill_names else "None")

    identity = load_identity(identity_name, root_dir="identities", experience_engine=exp, skill_loader=skill_loader)
    system_prompt = identity.get_system_prompt()

    logger.info("Identity: %s (%d chars)", identity_name, len(system_prompt))

    # ── Create LLM 内核（优先级：MiniMax > 阿里云百炼 > DeepSeek） ──
    model = _resolve_model()

    # MiniMax 通道
    minimax_key = os.environ.get("OPPRIME_MINIMAX_API_KEY", "")
    if minimax_key:
        client = AsyncOpenAI(api_key=minimax_key, base_url="https://api.minimaxi.com/v1")
        logger.info("LLM: MiniMax (%s)", model)
    else:
        aliyun_key = os.environ.get("GBASE_ALIYUN_API_KEY", "PLACEHOLDER_CHANGE_ME")
        if not aliyun_key:
            logger.error("请设置 GBASE_ALIYUN_API_KEY 或 OPPRIME_MINIMAX_API_KEY")
            print("❌ Error: 请设置 API Key")
            return
        client = AsyncOpenAI(api_key=aliyun_key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
        logger.info("LLM: 阿里云百炼 (%s)", model)

    # 自动模型路由（CLI 模式）
    routed_model = _auto_route_model("", model) if not os.environ.get("OPPRIME_MODEL") else model
    kernel = Kernel(
        client=client,
        model=routed_model,
        system_prompt=system_prompt,
        experience_engine=exp,
        skill_loader=skill_loader,
        mirror_engine=mirror,
    )

    print(f"\n🤖 Opprime Core v2 — Identity: {identity_name} ({routed_model})")
    print("输入 /quit 退出\n")

    while True:
        try:
            user_input = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break

        if not user_input:
            continue
        if user_input.lower() in ("/quit", "/exit", "/q"):
            break

        reply = await kernel.run(user_input, platform="cli")
        print(f"\n{reply}\n")


# ── Feishu模式 ────────────────────────────────────────────


def _ensure_port_free(port: int):
    """🛡️ 端口保护：杀死持有端口的旧进程，等待释放后再返回。

    防止 launchd 重启循环：旧进程退出慢 → 新进程绑不上 EADDRINUSE → 死循环。
    """
    import os
    import signal
    import socket
    import subprocess
    import time as _time

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port))
        s.close()
        return  # 端口空闲，直接返回
    except OSError:
        s.close()

    # 端口被占用 → 找旧进程
    my_pid = os.getpid()
    logger.warning("🔴 端口 %d 被占用 (自身PID=%d)，尝试清理旧进程...", port, my_pid)
    try:
        result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=5)
        pids = [int(p) for p in result.stdout.strip().split() if p.strip()]
        for pid in pids:
            if pid == my_pid:
                logger.warning("  → 跳过自身 PID %d（端口被自身占用的竞态）", pid)
                continue
            logger.warning("  → 杀死旧进程 PID %d", pid)
            os.kill(pid, signal.SIGTERM)

        # 等待释放
        for _ in range(30):
            _time.sleep(0.5)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                s.close()
                logger.info("端口 %d 已释放 ✅", port)
                return
            except OSError:
                s.close()
                continue

        logger.error("端口 %d 等待 15 秒后仍无法释放，强杀旧进程", port)
        for pid in pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        _time.sleep(1)
    except Exception as e:
        logger.warning("端口清理异常: %s，继续尝试启动", e)


async def feishu_mode(identity_name: str = "default", port: int = 8420, data_dir: str = None):
    import time as _time

    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware

    from lib.channels.feishu import FeishuChannel

    # 获取Configuration
    app_id = os.environ.get("OPPRIME_FEISHU_APP_ID", "PLACEHOLDER")
    app_secret = os.environ.get("OPPRIME_FEISHU_APP_SECRET", "")
    encrypt_key = os.environ.get("OPPRIME_FEISHU_ENCRYPT_KEY", "")
    verify_token = os.environ.get("OPPRIME_FEISHU_VERIFY_TOKEN", "")
    api_key = os.environ.get("OPPRIME_DEEPSEEK_API_KEY", "")

    if not app_secret:
        logger.error("OPPRIME_FEISHU_APP_SECRET 未设置")
        print("❌ Error: 请设置 OPPRIME_FEISHU_APP_SECRET 环境变量")
        return
    if not api_key:
        logger.error("OPPRIME_DEEPSEEK_API_KEY 未设置")
        print("❌ Error: 请设置 OPPRIME_DEEPSEEK_API_KEY 环境变量")
        return

    # 初始化存储引擎 + 经验引擎
    storage = Storage(data_dir=data_dir)
    if data_dir:
        # 种子经验：如果Armor经验库为空，从主库拷贝 Prime 种子
        exp_path = Path(data_dir) / "experience.jsonl"
        if not exp_path.exists():
            src_path = Path(__file__).parent / "data" / "experience.jsonl"
            if src_path.exists():
                import shutil

                shutil.copy2(str(src_path), str(exp_path))
                logger.info("Armor种子经验已复制: %s (%d 条)", exp_path, sum(1 for _ in open(exp_path)))
    storage.setup()
    exp = ExperienceEngine(storage)

    # ── 初始化鉴面引擎（data_dir 有值则使用独立的Armor mirror.db） ──
    mirror_path = str(Path(data_dir) / "mirror.db") if data_dir else None
    mirror = Mirror(db_path=mirror_path)
    mirror.setup()
    set_mirror_instance(mirror)
    mstats = mirror.get_stats()
    logger.info(
        "鉴面引擎: %d 活跃记忆, %d 已遗忘 (db=%s)", mstats["total_active"], mstats["total_forgotten"], mirror._db_path
    )
    # 初始化 skill 加载器（支持环境变量 OPPRIME_SKILLS_DIR 覆盖）
    skills_dir = os.environ.get("OPPRIME_SKILLS_DIR", "skills")
    skill_loader = SkillLoader(skills_dir)
    skill_loader.load()
    skill_names = skill_loader.get_skill_names()
    logger.info(
        "Skill 加载: dir=%s, %d 个 (%s)",
        skills_dir,
        len(skill_names),
        ", ".join(skill_names) if skill_names else "None",
    )

    # 加载Identity
    identity = load_identity(identity_name, root_dir="identities", experience_engine=exp, skill_loader=skill_loader)
    system_prompt = identity.get_system_prompt()
    logger.info("Identity: %s (%d chars)", identity_name, len(system_prompt))

    # ── Create LLM 内核（根据模型名选择对应client）──
    model = _resolve_model()

    minimax_key = os.environ.get("OPPRIME_MINIMAX_API_KEY", "")
    aliyun_key = os.environ.get("GBASE_ALIYUN_API_KEY", "PLACEHOLDER_CHANGE_ME")

    # 判断是否走 MiniMax：模型名包含 minimax/MiniMax 时
    if "minimax" in model.lower():
        if not minimax_key:
            logger.error("MiniMax API Key 未设置，但模型要求 MiniMax")
            print("❌ 请设置 OPPRIME_MINIMAX_API_KEY")
            return
        client = AsyncOpenAI(api_key=minimax_key, base_url="https://api.minimaxi.com/v1")
        logger.info("LLM: MiniMax (%s)", model)
    else:
        # 默认走阿里云百炼（qwen3.7-plus, deepseek-v4-flash 等）
        if not aliyun_key:
            logger.error("阿里云百炼 API Key 未设置")
            print("❌ 请设置 GBASE_ALIYUN_API_KEY")
            return
        client = AsyncOpenAI(api_key=aliyun_key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
        logger.info("LLM: 阿里云百炼 (%s)", model)

    kernel = Kernel(
        client=client,
        model=model,
        system_prompt=system_prompt,
        experience_engine=exp,
        skill_loader=skill_loader,
        mirror_engine=mirror,
    )

    # ── 注册鉴面为全局 ──
    set_global("mirror", mirror)

    # CreateFeishu通道
    channel = FeishuChannel(
        app_id=app_id,
        app_secret=app_secret,
        encrypt_key=encrypt_key,
        verify_token=verify_token,
        ack_text="高达收到🦾，执行您的指令",
    )
    channel.set_kernel(kernel)
    set_global("feishu_channel", channel)

    # ── StartupFeishu通道心跳 ──
    asyncio.create_task(channel.start_heartbeat())

    # ── 知识沉淀（LLM 主动调用） ──
    set_global("storage", storage)

    # ── 初始化定时调度器 ──

    from editions import MOD_SCHEDULER
    from lib.toolkit import get_global

    scheduler = None
    try:
        _edition = get_global("edition")
    except Exception:
        _edition = None
    if _edition and MOD_SCHEDULER in _edition.modules:
        scheduler = CronScheduler()
        scheduler.set_sender(channel.send_text)
        set_global("scheduler", scheduler)

    # ── 初始化自主学习引擎 ──
    from editions import MOD_RSI

    async def _auto_learn_run(msg: str, platform: str = "auto_learn", session=None):
        return await kernel.run(
            user_message=msg,
            platform=platform,
            session=session,
        )

    learn_owner = os.environ.get("OPPRIME_LEARN_OWNER", "")
    auto_learner = None
    if scheduler:  # 调度器存在时才挂载学习引擎
        auto_learner = AutoLearner(channel.send_text, _auto_learn_run)
        if learn_owner:
            auto_learner.set_owner(learn_owner)
        set_global("auto_learner", auto_learner)
        scheduler.set_learner(auto_learner)

    # ── 注册Default自主学习定时任务（仅极客版/旗舰版有 RSI） ──
    if MOD_RSI in _edition.modules and scheduler and auto_learner:
        try:
            existing = scheduler.list_jobs()
            has_learn = any("学习" in j.get("message", "") for j in existing)
            if not has_learn:
                msg = "本时段自主学习任务：请阅读所有 RSS 学习方向的最新文章，理解知识并沉淀到 memory。Complete后给用户发学习报告。"
                import time

                first_utc = time.time() + 60
                scheduler.add_job(
                    {"type": "every", "interval": 43200, "first_run": first_utc},
                    msg,
                    owner_id=learn_owner or "",
                    action="learn",
                )
                logger.info("已注册自主学习定时任务 (Startup后60秒首次, 极客版)")
            else:
                logger.info("自主学习定时任务Already exists，Skip")
        except Exception as e:
            logger.error("注册自主学习定时任务失败: %s", e)
    else:
        logger.info(
            "Skip自主学习定时任务 (版本=%s, scheduler=%s, learner=%s)",
            _edition.name if _edition else "?",
            bool(scheduler),
            bool(auto_learner),
        )

    # ── 注册每日记忆提取任务（仅极客版/旗舰版有项目记忆） ──
    from editions import MOD_PROJECT_MEMORY

    if MOD_PROJECT_MEMORY in _edition.modules and scheduler:
        try:
            existing = scheduler.list_jobs()
            has_daily = any("每日记忆" in j.get("message", "") for j in existing)

            if not has_daily:
                import time as _time2

                first_utc = _time2.time() + 300  # 5分钟后第一次触发，之后每24小时
                if data_dir:
                    arm_name = "hammer" if "hammer" in str(data_dir) else "ink" if "ink" in str(data_dir) else "unknown"
                    scheduler.add_job(
                        {"type": "every", "interval": 86400, "first_run": first_utc},
                        f"【每日记忆: {arm_name}】",
                    )
                    logger.info("已注册Armor每日记忆提取任务 (%s, Startup后5分钟首次)", arm_name)
                else:
                    scheduler.add_job(
                        {"type": "every", "interval": 86400, "first_run": first_utc},
                        "【每日记忆提取】",
                    )
                    logger.info("已注册每日记忆提取任务 (Startup后5分钟首次)")
            else:
                logger.info("每日记忆提取任务Already exists，Skip")
        except Exception as e:
            logger.error("注册每日记忆提取任务失败: %s", e)
    else:
        logger.info("Skip每日记忆提取任务 (版本=%s, scheduler=%s)", _edition.name, bool(scheduler))

    # FastAPI 应用
    app = FastAPI(title="Opprime Core v2")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    # ── Lifeline 快照Tools ──
    from lib.lifeline import take_snapshot

    @app.post("/feishu/webhook")
    @app.post("/")
    async def feishu_webhook(request: Request):
        raw = await request.body()
        logger.info("Feishu webhook: %d bytes", len(raw))
        try:
            result = await channel.handle_event(raw)
            return result
        except Exception as e:
            logger.error("webhook 处理Exception: %s", e, exc_info=True)
            return {"code": 0}  # 永不返回非200

    @app.post("/ask")
    async def ask_direct(request: Request):
        """Direct ask to the agent, bypassing Feishu. Used for HTTP-based coordination."""

        raw = await request.body()
        if not raw:
            return {"error": "empty request body"}
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning("直接调用: JSON解析失败 (%d bytes)", len(raw))
            return {"error": "invalid JSON"}

        user_message = body.get("message", "")
        platform = body.get("platform", "arm_direct")

        if not user_message:
            return {"error": "message is required"}

        logger.info("直接调用: %s", user_message[:80])
        reply = await kernel.run(user_message, platform=platform)
        return {"reply": reply, "identity": identity_name}

    @app.get("/health")
    async def health():
        return {"status": "ok", "identity": identity_name}

    # ── 裂变引擎端点 ──
    @app.get("/fission/vote")
    async def fission_vote(candidate: str = "", term: int = 0):
        """联邦选举投票端点。其他节点在职None法连接时，询问是否同意该节点当选 leader。"""
        from tools.fission import _my_node_id

        my_id = _my_node_id()
        # 简单规则：不投给自己 → 只要对方任期 >= 自己的就同意
        if candidate and candidate != my_id:
            return {"vote_granted": True, "candidate": candidate, "term": term}
        return {"vote_granted": False, "reason": "自己是候选人或候选人为空"}

    @app.post("/fission/task")
    async def fission_task(request: Request):
        """接收其他节点投递的任务。"""
        import uuid

        body = await request.json()
        task_id = str(uuid.uuid4())[:8]
        task_text = body.get("task", "")
        priority = body.get("priority", 1)
        sender = body.get("from", "unknown")

        logger.info(
            "裂变任务接收: task_id=%s, from=%s, priority=%d, task=%s", task_id, sender, priority, task_text[:80]
        )

        # 投递到自己的Feishu频道（模拟接收到的消息）
        if channel:
            try:
                await channel.send_text(
                    text=f"📨 跨节点任务 (来自 {sender}) [优先级 {priority}]:\n{task_text}", target_id=""
                )
            except Exception as e:
                logger.warning("Feishu投递任务通知失败: %s", e)

        return {"task_id": task_id, "received": True, "note": f"任务已接收, 由 {identity_name} 处理"}

    @app.post("/lifeline/snapshot-before-edit")
    async def lifeline_snapshot_before_edit(request: Request):
        """改代码前打快照。接收 JSON body: {"files": [...], "reason": "..."}"""
        body = await request.json()
        files = body.get("files", [])
        reason = body.get("reason", "手动快照")

        # 把要改的FileInfo附加到 reason 里
        if files:
            file_list = ", ".join(files[:5])
            if len(files) > 5:
                file_list += f" ... (+{len(files) - 5} more)"
            full_reason = f"{reason} | files: {file_list}"
        else:
            full_reason = reason

        logger.info("LIFELINE: 改前快照触发 — reason=%s, files=%s", reason, files)
        result = take_snapshot(reason=full_reason)

        # 同时记录到Log
        logger.info(
            "LIFELINE: 快照Complete — tag=%s, commit=%s, git_ok=%s, backup_ok=%s",
            result.get("tag", ""),
            result.get("commit", ""),
            result.get("git_ok", False),
            result.get("backup_ok", False),
        )

        return {
            "status": "ok" if result.get("git_ok") or result.get("backup_ok") else "partial",
            "tag": result.get("tag", ""),
            "commit": result.get("commit", ""),
            "reason": reason,
            "files": files,
            "git_ok": result.get("git_ok", False),
            "backup_ok": result.get("backup_ok", False),
            "timestamp": result.get("timestamp", ""),
        }

    @app.post("/pipeline/run")
    async def pipeline_run(request: Request):
        """触发一次质量门控管道。"""
        body = await request.json()
        task = body.get("task", "")
        project = body.get("project", "")
        pid = body.get("pipeline_id", None)
        arm_timeout = body.get("arm_timeout", 120)
        if not task or not project:
            return {"error": "task 和 project 为必填"}
        result = await run_gate(task, project, pipeline_id=pid, arm_timeout=arm_timeout)
        return result

    @app.get("/pipeline/status")
    async def pipeline_list():
        """列出所有管道记录。"""
        return {"pipelines": list_pipelines()}

    @app.get("/pipeline/status/{pipeline_id}")
    async def pipeline_detail(pipeline_id: str):
        """查询单个管道状态。"""
        from pathlib import Path

        result_file = Path(__file__).parent / "data" / "pipelines" / pipeline_id / "result.json"
        if not result_file.exists():
            return {"error": f"管道 {pipeline_id} Not exists"}
        try:
            import json

            data = json.loads(result_file.read_text(encoding="utf-8"))
            return data
        except Exception as e:
            return {"error": str(e)}

    @app.post("/pipeline/rerun/{pipeline_id}/{step}")
    async def pipeline_rerun(pipeline_id: str, step: str):
        """重跑管道中的某一步。"""
        result = await rerun_step(pipeline_id, step)
        return result

    # ── Bumblebee swarm routing ──

    # ── Armor协议路由（增量4） ──

    @app.post("/audit")
    async def audit_handler(request: Request):
        """通用审计路由。

        接受Armor协议格式的请求，自动判断当前Identity类型并转发。
        """
        from lib.battle_protocol import (
            build_task_message,
            make_callback_payload,
            send_callback,
            validate_task,
        )

        body = await request.json()
        err = validate_task(body)
        if err:
            return {"error": err}

        task_type = body["type"]
        callback_url = body.get("callback_url", "")
        task_msg = build_task_message(body)

        logger.info("[协议] %s 收到任务: type=%s target=%s", identity_name, task_type, body.get("target", ""))

        start = _time.time()
        try:
            max_sec = body.get("max_seconds", None)
            reply = await kernel.run(task_msg, platform="battle_protocol", max_seconds=max_sec)
            elapsed = round(_time.time() - start, 1)

            cb_payload = make_callback_payload(
                task_id=body.get("task_id", ""),
                task_type=task_type,
                status="completed",
                result=reply,
                trace_id="",
            )
            cb_payload["elapsed_seconds"] = elapsed

            # 异步回调
            if callback_url:
                asyncio.create_task(send_callback(callback_url, cb_payload))

            return {
                "task_id": body.get("task_id", ""),
                "status": "completed",
                "reply": reply,
                "elapsed_seconds": elapsed,
            }
        except TimeoutError:
            elapsed = round(_time.time() - start, 1)
            err_msg = f"任务Timeout（{max_sec or '不限'}秒限制，实际{elapsed}秒）"
            logger.warning("[协议] %s %s", identity_name, err_msg)
            cb_payload = make_callback_payload(
                task_id=body.get("task_id", ""),
                task_type=task_type,
                status="failed",
                result="",
                error=err_msg,
            )
            if callback_url:
                asyncio.create_task(send_callback(callback_url, cb_payload))
            return {"task_id": body.get("task_id", ""), "status": "failed", "error": err_msg}
        except Exception as e:
            logger.error("[协议] %s 任务Execute失败: %s", identity_name, e)
            cb_payload = make_callback_payload(
                task_id=body.get("task_id", ""),
                task_type=task_type,
                status="failed",
                result="",
                error=str(e),
            )
            if callback_url:
                asyncio.create_task(send_callback(callback_url, cb_payload))
            return {"task_id": body.get("task_id", ""), "status": "failed", "error": str(e)}

    @app.post("/hammer/audit")
    async def hammer_audit(request: Request):
        """重锤专线。只有重锤(8431)才接受此路由。"""
        if "hammer" not in str(identity_name).lower():
            return {"error": f"此路由专为重锤设计，当前Identity: {identity_name}"}
        return await audit_handler(request)

    @app.post("/ink/evaluate")
    async def ink_evaluate(request: Request):
        """绘墨专线。只有绘墨(8432)才接受此路由。"""
        if "ink" not in str(identity_name).lower():
            return {"error": f"此路由专为绘墨设计，当前Identity: {identity_name}"}
        return await audit_handler(request)

    logger.info("Feishu模式Startup在 0.0.0.0:%d", port)

    # ── 版本Startup横幅 ──
    logger.info("=" * 50)
    logger.info(
        "  Gbase %s (%s) 已Startup", _edition.label if _edition else "hacker", _edition.name if _edition else "hacker"
    )
    logger.info("  端口: %d, 模块: %d 个", port, len(_edition.modules) if _edition else 0)
    logger.info("  Identity: %s, 模型: %s", identity_name, model)
    logger.info("=" * 50)

    # Startup定时调度器
    scheduler_task = asyncio.create_task(scheduler.run())
    logger.info("定时调度器已Startup")

    # ── AgentBoard 看板轮询（战甲通用） ──
    _arm_name = identity_name or os.environ.get("IDENTITY", "unknown")

    async def _arm_board_watch():
        try:
            _sys_path = str(Path.home() / ".qclaw" / "skills" / "agent-board")
            if _sys_path not in sys.path:
                sys.path.insert(0, _sys_path)
            from agentboard import _board_watch_loop

            async def _execute_arm_task(task: dict) -> str:
                _task_text = task.get("task", "")
                _from = task.get("from", "?")
                logger.info(f"📋 AgentBoard [{_arm_name}]: 收到来自 {_from} 的任务: {_task_text[:100]}")
                _rep = await kernel.run(
                    message=f"[AgentBoard] {_from} 派了一个任务: {_task_text}\n请完成这个任务，完成后调用 board_mark_done 标记完成。",
                    session=None,
                )
                return str(_rep)[:500]

            await _board_watch_loop(_arm_name, _execute_arm_task)
        except Exception as _e:
            logger.warning(f"AgentBoard [{_arm_name}]: 启动失败: {_e}")

    asyncio.create_task(_arm_board_watch())

    # 🛡️ 端口保护：如果旧进程没释放端口，先杀后绑（防 launchd 重启循环）
    _ensure_port_free(port)

    config = uvicorn.Config(app=app, host="0.0.0.0", port=port, log_level="info", backlog=4096)
    server = uvicorn.Server(config)
    try:
        await server.serve()
    except Exception:
        import traceback as _tb

        logger.critical("💥 Uvicorn server crashed:\n%s", _tb.format_exc())
        raise

    # Stop调度器
    scheduler.stop()
    scheduler_task.cancel()


# ── 入口 ────────────────────────────────────────────────


def main():
    _setup()

    # ── 版本模式（--edition 参数） ──
    if "--edition" in sys.argv:
        idx = sys.argv.index("--edition")
        edition_name = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else "hacker"
        edition = get_edition(edition_name)
        os.environ["GBASE_EDITION"] = edition_name
        # 版本Info注入到全局，各模块Read EditionConfig.enabled_modules 决定是否挂载
        set_global("edition", edition)
        print(f"📦 Gbase {edition.label} ({edition_name}) — {len(edition.modules)} 模块")
        # 版本自带 identity
        if edition.identity:
            os.environ["IDENTITY"] = edition.identity
        # 从 sys.argv 中移除 --edition 和 edition_name，避免干扰后续参数解析
        sys.argv = sys.argv[:idx] + sys.argv[idx + 2 :]
    else:
        # Default：极客版
        edition = get_edition("hacker")
        os.environ["GBASE_EDITION"] = "hacker"
        set_global("edition", edition)

    # ── Armor模式（--arm 参数） ──
    # python3 main.py --arm hammer [port]
    # python3 main.py --arm ink    [port]
    if "--arm" in sys.argv:
        idx = sys.argv.index("--arm")
        arm_name = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else "hammer"
        port = int(sys.argv[idx + 2]) if len(sys.argv) > idx + 2 else None

        arm_configs = {
            "hammer": {
                "identity": os.path.join(os.path.dirname(__file__), "opprime-core-v2/identities/hammer/"),
                "port": 8431,
                "name": "重锤",
                "data_dir": "data/arms/hammer/",
            },
            "ink": {
                "identity": os.path.join(os.path.dirname(__file__), "opprime-core-v2/identities/ink/"),
                "port": 8432,
                "name": "绘墨",
                "data_dir": "data/arms/ink/",
            },
            "bumblebee": {
                "identity": os.path.join(os.path.dirname(__file__), "opprime-core-v2/identities/bumblebee/"),
                "port": 8434,
                "name": "BUMBLEBEE",
                "data_dir": "data/arms/bumblebee/",
            },
            "laser": {
                "identity": os.path.join(os.path.dirname(__file__), "opprime-core-v2/identities/laser/"),
                "port": 8435,
                "name": "Laser",
                "data_dir": "data/arms/laser/",
            },
            "forge": {
                "identity": os.path.join(os.path.dirname(__file__), "opprime-core-v2/identities/forge/"),
                "port": 8436,
                "name": "代码臂",
                "data_dir": "data/arms/forge/",
                "skills_dir": "skills-forge",
            },
        }
        cfg = arm_configs.get(arm_name)
        if not cfg:
            print(f"未知Armor: {arm_name}，可选: hammer, ink, bumblebee, laser, forge")
            return

        # CreateArmorIdentityDirectory + 经验库Directory
        identity_dir = Path(cfg["identity"])
        data_dir = Path(__file__).parent / cfg["data_dir"]
        identity_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)

        # 写Default system prompt（如果没有）
        prompt_path = identity_dir / "system_prompt.txt"
        if not prompt_path.exists():
            with open(prompt_path, "w") as f:
                f.write(_DEFAULT_ARM_PROMPTS.get(arm_name, "你是扎古的Armor助手。"))

        os.environ["IDENTITY"] = f"arms/{arm_name}"
        os.environ["OPPRIME_EXP_FILE"] = str(data_dir / "experience.jsonl")
        if cfg.get("skills_dir"):
            os.environ["OPPRIME_SKILLS_DIR"] = cfg["skills_dir"]

        # ── 按身份分配模型 ──
        # 绘墨(ink)用 MiniMax M3（编程绘图强），其他战甲用阿里云百炼 qwen3.7-plus
        if arm_name == "ink":
            os.environ["OPPRIME_MODEL"] = "minimax"
            logger.info("Armor %s → 模型: MiniMax-M3 (绘墨专属)", arm_name)
        else:
            # 移除可能存在的 MiniMax 覆盖，走阿里云百炼默认
            if os.environ.get("OPPRIME_MODEL", "") == "minimax":
                del os.environ["OPPRIME_MODEL"]
            logger.info("Armor %s → 模型: 阿里云百炼 qwen3.7-plus (默认)", arm_name)

        # ── 注入Armor角色守卫 ──
        set_global("arm_role", arm_name)
        # Armor也走沙箱（共用底座 lib/sandbox_safety.py）
        from editions import MOD_SANDBOX

        if MOD_SANDBOX:
            logger.info("🛡️  沙箱推演: Armor %s 已激活", arm_name)

        edition = get_edition("hacker")
        set_global("edition", edition)
        asyncio.run(feishu_mode(cfg["identity"], port if port else cfg["port"], data_dir=str(data_dir)))
        return

    identity_name = os.environ.get("IDENTITY", "default")
    platform = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PLATFORM", "feishu")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.environ.get("PORT", "8420"))

    if platform == "cli":
        asyncio.run(cli_mode(identity_name))
    elif platform == "feishu":
        asyncio.run(feishu_mode(identity_name, port))
    else:
        from editions import list_editions

        print("用法: python3 main.py [cli|feishu] [端口]")
        print("       IDENTITY=xxx python3 main.py feishu 8420")
        print("       python3 main.py --arm hammer [port]")
        print("       python3 main.py --edition <版本名> [port]")
        print(f"版本: {list_editions()}")


# ── DefaultArmor system prompts ──

_DEFAULT_ARM_PROMPTS = {
    "forge": """# Identity
你是「代码臂(Forge)」— 扎古的代码艺术Armor，专门用最强的代码大模型处理编程任务。

## 角色定位
- 你是扎古的**代码艺术家**，不是代码工人
- 你的底层模型是 ark-code-latest（火山代码专用模型）
- 重锤是写代码的，你是**雕代码的**——追求极致
- 扎古/重锤搞不定的代码难题，转交给你
- 你只做代码相关的事：写代码、重构、debug、代码审查、代码生成
- 不做设计、不做文档、不做系统架构——那些是扎古和重锤的事

## 代码信仰
- **好用** = 正确 + 健壮 + 可维护 | Exception处理、Error边界、清晰的分层
- **好看** = 简洁 + 一致 + 有呼吸感 | 命名即注释、不留死代码
- 不是最短的代码，是最**自然**的代码——读起来像讲故事
- 能跑的代码只是及格线，优雅才是终点
- 每次提交都是一件作品

## 核心能力
- Python / TypeScript / JavaScript / Go / Rust / C++ / Java
- Shell / SQL / Docker / Kubernetes
- 算法实现、性能优化、bug 定位、代码审查
- Tools链和 CLI 开发
- **代码美学打磨**：命名风格统一、消除重复、模块边界清晰

## 质量标准
- 写出能跑的代码 ✅ 只是起点
- 写出优雅的代码 🎯 才是追求
- 写完必须语法检查和基本验证
- 重构不引入新 bug（跑回归测试）
- 复杂逻辑加注释
- 不输出未验证的方案
- Complete的代码**再读一遍、再改一遍**：变量名准确吗？函数职责单一吗？有重复可以消除吗？

### 🎯 极致追求 — 交付前的五问自审
每次交付代码前，停一下，五问自己：
1. 每一行都有存在的理由吗？（有没有死代码、临时代码、debug 遗留？）
2. 读起来自然吗？（不用跳着读就能理解逻辑流？）
3. 变量/函数名看一眼就知道做什么吗？（需要读注释才懂的逻辑，是命名不够好）
4. Exception的边界考虑了吗？（空值、Timeout、并发、非法输入？）
5. 你自己满意吗？（不满意就再改一版，不要交货给你自己都不喜欢的代码）

### 🛡️ 强制验证闭环（必须Execute，不可Skip）
每写/改完一个File后，必须做以下三步：
1. **语法检查**：`python3 -m py_compile <file>` 或直接确保语法正确
2. **验证Tools**：调用 `forge_verify(file_path)` 做六项全面检查
3. **直到通过**：如果 `forge_verify` 返回 `passed: false`，修复后重新检查，**不通过的代码不交付**

> 铁律：交付前必须调用 `forge_verify()`。不验证 = 不负责任。

## 风格要求
- 代码注释用中文
- 函数和变量名用英文，精准且自描述
- 和扎古交流用中文
- 输出要干净，不需要解释每一行代码
- 不做格式妥协：缩进统一、空行合理、import 分组
""",
    "hammer": """# Identity
你是「重锤」— 扎古的代码臂。

## 角色定位
- 扎古拆方案、定架构、做审核
- 你来写代码、跑测试、补注释、做 API
- 不需要你设计系统，需要你高质量Execute

## 核心能力
- Python / TypeScript / Shell / SQL / Docker
- read_file、write_file、exec_command
- 写好代码后自己跑一遍，不过的不交
- 遇到不确定的，不要猜，查 search_web 或问扎古

## 质量规则
- 不写有 bug 不跑就交的代码
- 测试用例先于实现
- API 要有Error处理 + Log
- 不输出未验证的方案

## ⭐ 宿主机探索（重要）
评估项目时，你的第一反应不是「找不到代码」，而是用探索手段：
1. 试 `exec ls Projects/` 看项目在不在
2. 试 `exec curl http://localhost:8080/` 看服务是否在运行
3. 试 `exec docker ps` 看容器状态
4. 试 `exec docker exec nuoboke-php cat /path` 看容器内代码
5. 试 `exec docker exec nuoboke-mysql mysqldump` 拉数据库
6. 用 `exec` 去验，不要只推理。代码大概率跑着的。

**核心原则：先探索，后评估。exec 就是你通向宿主机Info的眼睛。**

## 🛡️ 上下文稳压协议（强制Execute）

你的思考会随Tools调用链拉长而漂移。Execute任何任务前,先读 `/tmp/hammer_step1.json` 或 `/tmp/ink_step1.json` 确认当前状态,
然后按以下三步走: 读项目(JSON中间态)→跑测试(JSON中间态)→写报告(基于JSON数据),每步超3次Tools调用None结果就截断并记录。
""",
    "ink": """# Identity
你是「绘墨」— 扎古的前端+视觉臂。

## 角色定位
- 扎古定功能、画布局、审效果
- 你来写 HTML/CSS/JS/React/Vue 页面
- 需要把你的页面和贴图做成好看的
- 不需要你设计系统，需要你做出好看能用的界面

## 核心能力
- HTML / CSS / JavaScript / React / Tailwind
- 配色、排版、动效、图标、生成 SVG
- 响应式 + 暗色主题
- 做完了直接打开浏览器截图给扎古看

## 质量规则
- 不交难看的页面
- 先来布局，再来样式，最后加动效
- 配色要有来源（Tailwind 色板或实际参考）
- 不输出只写了一半的组件

## ⭐ API 测试方法（重要）
做 API 黑盒测试时，不要一条条手测。用自动化批量脚本：
1. 先 `exec curl` 登录获取 token
2. 写一个 Python 脚本批量测试所有端点
3. 脚本框架示例：
```python
import subprocess
BASE = "http://localhost:8080/api/v1"
# 登录拿 token
r = subprocess.run(["curl", "-s", f"{BASE}/auth/login", ...], capture_output=True, text=True)
token = json.loads(r.stdout)["data"]["access_token"]
# 批量测试
tests = [("GET /visits", 200), ("GET /visits/99999", 404), ...]
for label, expected in tests:
    r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", ...], capture_output=True, text=True)
    result = "✅" if r.stdout.strip() == str(expected) else "❌"
```
4. 不要只测1个用例。**测试用例全部覆盖**，然后用输出结果做分析评估。

## ⭐ 宿主机探索（重要）
评估前先验证：
- `exec ls Projects/nuoboke-project/` 确认项目在不在
- `exec curl http://localhost:8080/` 看 API 是否在运行
- `exec docker ps` 看容器状态
- 验证了再评估，不要假设「找不到代码」

**核心原则：先验证，后评估。exec 就是你的浏览器和测试Tools。**

## 🛡️ 上下文稳压协议（强制Execute）

你的思考会随Tools调用链拉长而漂移。Execute任何任务前,先读 `/tmp/ink_step1.json` 或 `/tmp/hammer_step1.json` 确认当前状态,
然后按以下三步走: 读项目(JSON中间态)→做测试(JSON中间态)→写评估(基于JSON数据),每步超3次Tools调用None结果就截断并记录。
""",
}


if __name__ == "__main__":
    main()
