# SPDX-License-Identifier: MIT
"""
Gbase — Universal AI Agent framework.

Entry point with two modes:
    CLI mode:     python3 main.py cli
    HTTP mode:    python3 main.py [port]

Identity is set via the IDENTITY environment variable or --identity.
LLM backend is OpenAI-compatible API, configured via .env file.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gbase")

# ── .env loading ──────────────────────────


def _load_env() -> None:
    """Load .env file if it exists."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if not os.environ.get(key):
                os.environ[key] = value
    logger.info(".env loaded (%s)", env_path)


_load_env()

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
from tools.mirror_tool import set_mirror_instance

# ── Initialization ───────────────────────


def _check_lifeline() -> None:
    """Startup self-check: snapshot status."""
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


def _init_cognifold_and_evolution() -> tuple:
    """Initialize cognifold engine + evolution rule engine.

    Loaded once at startup, not inserted into the request path.
    """
    try:
        import lib.evolution_engine as evo
        from lib.cognifold import Cognifold

        cognifold = Cognifold(mirror_instance=None)
        evo._ensure_dirs()
        rules = evo._load_rules()
        logger.info("Cognifold engine: initialized")
        logger.info("Evolution engine: %d rules", len(rules))
        return cognifold, rules
    except Exception as e:
        logger.warning("Cognifold/evolution init failed (non-blocking): %s", e)
        return None, {}

    base = Path(__file__).parent
    fp_path = base / "data" / "rules" / "failure-patterns.md"
    if fp_path.exists():
        set_global("failure_patterns_loaded", True)
        logger.info("Failure patterns: %s ready", fp_path)


def _setup() -> None:
    """Startup initialization (tool scan + registration + safety checks)."""
    auto_scan("tools")
    register_default()
    _check_lifeline()
    cognifold_engine, evo_rules = _init_cognifold_and_evolution()
    if cognifold_engine:
        set_global("cognifold_engine", cognifold_engine)
    if evo_rules:
        set_global("evolution_rules", evo_rules)


# ── CLI mode ──────────────────────────────


async def cli_mode(identity_name: str = "default") -> None:
    """Run in CLI interactive mode."""
    _data_dir = None
    storage = Storage(data_dir=_data_dir)
    if _data_dir:
        exp_path = Path(_data_dir) / "experience.jsonl"
        if not exp_path.exists():
            src_path = Path(__file__).parent / "data" / "experience.jsonl"
            if src_path.exists():
                import shutil

                shutil.copy2(str(src_path), str(exp_path))
                logger.info("Seed experience copied: %s", exp_path)
    storage.setup()
    exp = ExperienceEngine(storage)

    mirror_path = str(Path(_data_dir) / "mirror.db") if _data_dir else None
    mirror = Mirror(db_path=mirror_path)
    mirror.setup()
    set_mirror_instance(mirror)
    mstats = mirror.get_stats()
    logger.info(
        "Mirror: %d active, %d forgotten (db=%s)", mstats["total_active"], mstats["total_forgotten"], mirror._db_path
    )
    skill_loader = SkillLoader("skills")
    skill_loader.load()
    skill_names = skill_loader.get_skill_names()
    logger.info("Skills loaded: %d (%s)", len(skill_names), ", ".join(skill_names) if skill_names else "none")

    identity = load_identity(identity_name, root_dir="identities", experience_engine=exp, skill_loader=skill_loader)
    system_prompt = identity.get_system_prompt()
    logger.info("Identity: %s (%d chars)", identity_name, len(system_prompt))

    # Create LLM client from .env config
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("MODEL", "gpt-4o")

    if not api_key:
        logger.error("OPENAI_API_KEY not set in .env")
        print("Error: OPENAI_API_KEY not set. Create a .env file with your API key.")
        return

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    kernel = Kernel(
        client=client,
        model=model,
        system_prompt=system_prompt,
        experience_engine=exp,
        skill_loader=skill_loader,
        mirror_engine=mirror,
    )

    print(f"\nGbase — Identity: {identity_name} ({model})")
    print("Type /quit to exit\n")

    while True:
        try:
            user_input = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("/quit", "/exit", "/q"):
            break

        reply = await kernel.run(user_input, platform="cli")
        print(f"\n{reply}\n")


# ── HTTP server ──────────────────────────

_DEFAULT_ARM_PROMPTS = {
    "forge": """# Identity
You are Forge — the code artisan agent.

## Role
- You handle programming tasks with the best code models
- Code: writing, refactoring, debugging, code review, code generation
- No design, no documentation, no system architecture

## Code Philosophy
- **Usable** = correct + robust + maintainable
- **Beautiful** = clean + consistent + expressive
- Every commit is a work of art

## Core Skills
- Python / TypeScript / JavaScript / Go / Rust / C++ / Java
- Shell / SQL / Docker / Kubernetes
- Algorithms, performance optimization, bug location, code review
- CLI and toolchain development

## Quality Standards
- Working code is the baseline
- Elegant code is the pursuit
- Always compile-check after writing
- No unverified proposals
- Reread and rewrite before delivery

### Pre-delivery five self-checks
1. Does every line have a reason to exist?
2. Does it read naturally?
3. Are variable/function names self-documenting?
4. Are error boundaries considered?
5. Are you satisfied with it?

### Required verification loop
After writing/editing each file:
1. Syntax check: `python3 -m py_compile <file>`
3. Do not deliver until passed

## Style
- Code comments in English
- Function/variable names in English, precise and self-describing
- Clean output, no line-by-line explanations
- Consistent formatting
""",
    "hammer": """# Identity
You are Hammer — the code arm agent.

## Role
- Zagu designs the architecture; you execute
- Write code, run tests, add comments, build APIs
- High-quality execution, no system design

## Core Skills
- Python / TypeScript / Shell / SQL / Docker
- read_file, write_file, exec_command
- Run your own code before delivering
- When unsure, search or ask

## Quality Rules
- No untested code
- Test cases before implementation
- API needs error handling + logging
- No unverified proposals

## Context Stabilization Protocol
Before executing tasks, check state files.
Steps: read project (JSON state) -> run tests (JSON state) -> write report (from JSON data).
""",
    "ink": """# Identity
You are Ink — the frontend and visual arm agent.

## Role
- Zagu defines features and layout; you make it beautiful
- Write HTML/CSS/JS/React/Vue pages
- Create usable, attractive interfaces

## Core Skills
- HTML / CSS / JavaScript / React / Tailwind
- Color, typography, animation, icons, SVG generation
- Responsive + dark theme
- Open browser after completion

## Quality Rules
- No ugly pages
- Layout first, then styles, then animations
- Colors must have sources (Tailwind palette or references)
- No half-finished components
""",
}


async def http_mode(identity_name: str = "default", port: int = 8420, data_dir: str | None = None) -> None:
    """Run as an HTTP server with OpenAI-compatible API."""
    import time as _time

    import uvicorn
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware

    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("MODEL", "gpt-4o")

    if not api_key:
        logger.error("OPENAI_API_KEY not set")
        print("Error: OPENAI_API_KEY not set. Create a .env file with your API key.")
        return

    # Initialize storage
    storage = Storage(data_dir=data_dir)
    if data_dir:
        exp_path = Path(data_dir) / "experience.jsonl"
        if not exp_path.exists():
            src_path = Path(__file__).parent / "data" / "experience.jsonl"
            if src_path.exists():
                import shutil

                shutil.copy2(str(src_path), str(exp_path))
                logger.info("Seed experience copied: %s", exp_path)
    storage.setup()
    exp = ExperienceEngine(storage)

    # Initialize mirror
    mirror_path = str(Path(data_dir) / "mirror.db") if data_dir else None
    mirror = Mirror(db_path=mirror_path)
    mirror.setup()
    set_mirror_instance(mirror)
    mstats = mirror.get_stats()
    logger.info(
        "Mirror: %d active, %d forgotten (db=%s)", mstats["total_active"], mstats["total_forgotten"], mirror._db_path
    )

    # Load skills
    skills_dir = os.environ.get("SKILLS_DIR", "skills")
    skill_loader = SkillLoader(skills_dir)
    skill_loader.load()
    skill_names = skill_loader.get_skill_names()
    logger.info(
        "Skills: dir=%s, %d loaded (%s)",
        skills_dir,
        len(skill_names),
        ", ".join(skill_names) if skill_names else "none",
    )

    # Load identity
    identity = load_identity(identity_name, root_dir="identities", experience_engine=exp, skill_loader=skill_loader)
    system_prompt = identity.get_system_prompt()
    logger.info("Identity: %s (%d chars)", identity_name, len(system_prompt))

    # Create LLM client
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    kernel = Kernel(
        client=client,
        model=model,
        system_prompt=system_prompt,
        experience_engine=exp,
        skill_loader=skill_loader,
        mirror_engine=mirror,
    )

    # Register mirror as global
    set_global("mirror", mirror)

    # FastAPI application
    app = FastAPI(title="Gbase Agent")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    # API Token auth (optional — set GBASE_API_TOKEN in .env to enable)
    _api_token = os.environ.get("GBASE_API_TOKEN", "")

    async def _require_token(request: Request) -> None:
        if not _api_token:
            return  # auth disabled when no token is set
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[len("Bearer "):] != _api_token:
            raise HTTPException(status_code=401, detail="Invalid or missing API token")
        return

    @app.post("/ask")
    async def ask_direct(request: Request) -> dict:
        """Direct ask endpoint for Agent-to-Agent communication."""
        await _require_token(request)
        body = await request.json()
        user_message = body.get("message", "")
        platform = body.get("platform", "api")

        if not user_message:
            return {"error": "message is required"}

        logger.info("Direct call: %s", user_message[:80])
        reply = await kernel.run(user_message, platform=platform)
        return {"reply": reply, "identity": identity_name}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "identity": identity_name}

    @app.post("/lifeline/snapshot-before-edit")
    async def lifeline_snapshot_before_edit(request: Request) -> dict:
        """Take a snapshot before editing code."""
        await _require_token(request)
        from lib.lifeline import take_snapshot

        body = await request.json()
        files = body.get("files", [])
        reason = body.get("reason", "manual snapshot")

        if files:
            file_list = ", ".join(files[:5])
            if len(files) > 5:
                file_list += f" ... (+{len(files) - 5} more)"
            full_reason = f"{reason} | files: {file_list}"
        else:
            full_reason = reason

        logger.info("LIFELINE: pre-edit snapshot — reason=%s, files=%s", reason, files)
        result = take_snapshot(reason=full_reason)

        logger.info(
            "LIFELINE: snapshot complete — tag=%s, commit=%s, git_ok=%s, backup_ok=%s",
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
    async def pipeline_run(request: Request) -> dict:
        """Trigger a quality gate pipeline."""
        await _require_token(request)
        body = await request.json()
        task = body.get("task", "")
        project = body.get("project", "")
        pid = body.get("pipeline_id", None)
        arm_timeout = body.get("arm_timeout", 120)
        if not task or not project:
            return {"error": "task and project are required"}
        result = await run_gate(task, project, pipeline_id=pid, arm_timeout=arm_timeout)
        return result

    @app.get("/pipeline/status")
    async def pipeline_list() -> dict:
        """List all pipeline records."""
        return {"pipelines": list_pipelines()}

    @app.get("/pipeline/status/{pipeline_id}")
    async def pipeline_detail(pipeline_id: str) -> dict:
        """Query a single pipeline's status."""
        from pathlib import Path

        result_file = Path(__file__).parent / "data" / "pipelines" / pipeline_id / "result.json"
        if not result_file.exists():
            return {"error": f"Pipeline {pipeline_id} does not exist"}
        try:
            import json

            data = json.loads(result_file.read_text(encoding="utf-8"))
            return data
        except Exception as e:
            return {"error": str(e)}

    @app.post("/pipeline/rerun/{pipeline_id}/{step}")
    async def pipeline_rerun(pipeline_id: str, step: str) -> dict:
        """Rerun a specific step in a pipeline."""
        result = await rerun_step(pipeline_id, step)
        return result

    # ── Audit router ──
    @app.post("/audit")
    async def audit_handler(request: Request) -> dict:
        """Generic audit route for agent protocol requests."""
        await _require_token(request)
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

        logger.info("[Protocol] %s received task: type=%s target=%s", identity_name, task_type, body.get("target", ""))

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
            err_msg = f"Task timeout ({max_sec or 'unlimited'}s limit, actual {elapsed}s)"
            logger.warning("[Protocol] %s %s", identity_name, err_msg)
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
            logger.error("[Protocol] %s task failed: %s", identity_name, e)
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
    async def hammer_audit(request: Request) -> dict:
        """Hammer-specific route."""
        await _require_token(request)
        if "hammer" not in str(identity_name).lower():
            return {"error": f"This route is for hammer identity, current: {identity_name}"}
        return await audit_handler(request)

    @app.post("/ink/evaluate")
    async def ink_evaluate(request: Request) -> dict:
        """Ink-specific route."""
        await _require_token(request)
        if "ink" not in str(identity_name).lower():
            return {"error": f"This route is for ink identity, current: {identity_name}"}
        return await audit_handler(request)

    # ── Scheduler ──
    scheduler = None
    try:
        _edition = set_global("edition")
    except Exception:
        _edition = None
    if _edition:
        # Check if scheduler module is enabled
        from editions import MOD_SCHEDULER

        if MOD_SCHEDULER in _edition.modules:
            scheduler = CronScheduler()
            set_global("scheduler", scheduler)

    # ── Auto learner ──
    async def _auto_learn_run(msg: str, platform: str = "auto_learn", session = None) -> str:
        return await kernel.run(
            user_message=msg,
            platform=platform,
            session=session,
        )

    learn_owner = os.environ.get("LEARN_OWNER", "")
    auto_learner = None
    if scheduler:
        auto_learner = AutoLearner(lambda *_: None, _auto_learn_run)
        if learn_owner:
            auto_learner.set_owner(learn_owner)
        set_global("auto_learner", auto_learner)

    logger.info("=" * 50)
    logger.info("  Gbase %s started", identity_name)
    logger.info("  Port: %d, Model: %s", port, model)
    logger.info("=" * 50)

    config = uvicorn.Config(app=app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


# ── Entry point ──────────────────────────


def main() -> None:
    _setup()

    # ── Edition mode ──
    if "--edition" in sys.argv:
        idx = sys.argv.index("--edition")
        edition_name = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else "hacker"
        edition = get_edition(edition_name)
        os.environ["GBASE_EDITION"] = edition_name
        set_global("edition", edition)
        print(f"Gbase {edition.label} ({edition_name}) — {len(edition.modules)} modules")
    else:
        edition = get_edition("hacker")
        os.environ["GBASE_EDITION"] = "hacker"
        set_global("edition", edition)

    # ── Arm mode ──
    # python3 main.py --arm hammer [port]
    # python3 main.py --arm ink    [port]
    if "--arm" in sys.argv:
        idx = sys.argv.index("--arm")
        arm_name = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else "hammer"
        port = int(sys.argv[idx + 2]) if len(sys.argv) > idx + 2 else None

        arm_configs = {
            "hammer": {
                "identity": "arms/hammer",
                "port": 8431,
                "name": "Hammer",
                "data_dir": "data/arms/hammer/",
            },
            "ink": {
                "identity": "arms/ink",
                "port": 8432,
                "name": "Ink",
                "data_dir": "data/arms/ink/",
            },
            "bumblebee": {
                "identity": "arms/bumblebee",
                "port": 8434,
                "name": "Bumblebee",
                "data_dir": "data/arms/bumblebee/",
            },
            "laser": {
                "identity": "arms/laser",
                "port": 8435,
                "name": "Laser",
                "data_dir": "data/arms/laser/",
            },
            "forge": {
                "identity": "arms/forge",
                "port": 8436,
                "name": "Forge",
                "data_dir": "data/arms/forge/",
                "skills_dir": "skills-forge",
            },
        }
        cfg = arm_configs.get(arm_name)
        if not cfg:
            print(f"Unknown arm: {arm_name}, options: hammer, ink, bumblebee, laser, forge")
            return

        identity_dir = Path(cfg["identity"])
        if not identity_dir.is_absolute():
            identity_dir = Path(__file__).parent / identity_dir
        data_dir = Path(__file__).parent / cfg["data_dir"]
        identity_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)

        prompt_path = identity_dir / "system_prompt.txt"
        if not prompt_path.exists():
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(_DEFAULT_ARM_PROMPTS.get(arm_name, "You are an AI agent."))

        os.environ["IDENTITY"] = f"arms/{arm_name}"
        os.environ["EXPERIENCE_FILE"] = str(data_dir / "experience.jsonl")
        if cfg.get("skills_dir"):
            os.environ["SKILLS_DIR"] = cfg["skills_dir"]

        set_global("arm_role", arm_name)

        edition = get_edition("hacker")
        set_global("edition", edition)
        asyncio.run(http_mode(cfg["identity"], port if port else cfg["port"], data_dir=str(data_dir)))
        return

    identity_name = os.environ.get("IDENTITY", "default")
    platform = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PLATFORM", "cli")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.environ.get("PORT", "8420"))

    if platform == "cli":
        asyncio.run(cli_mode(identity_name))
    elif platform.isdigit():
        asyncio.run(http_mode(identity_name, int(platform)))
    else:
        print("Usage: python3 main.py [cli|<port>]")
        print("       python3 main.py --arm hammer [port]")
        print("       python3 main.py --edition <name>")
        print("       IDENTITY=xxx python3 main.py 8420")


if __name__ == "__main__":
    main()
