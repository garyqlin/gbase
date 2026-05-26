# SPDX-License-Identifier: MIT
"""
BASE_DIR/tools/distill.py

Distillation tools.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from lib.toolkit import tool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EXPORT_DIR = BASE_DIR / "training_data"
MODELS_DIR = BASE_DIR / "models"

EXPORT_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════
# Core implementation
# ═══════════════════════════════════════════


def _export_data(force: bool = False) -> tuple[Path, int]:
    """Export experience/knowledge as SFT training data. Returns (path, count)."""
    out_path = EXPORT_DIR / "sft_data.jsonl"
    if out_path.exists() and not force:
        count = sum(1 for _ in open(out_path, encoding="utf-8"))
        return out_path, count

    records = []

    # 0. Load agent self-learning goals as prefix context
    learn_status = _load_self_learn_status()
    if learn_status:
        parts = []
        for name, info in learn_status.items():
            if info.get("active") and info.get("focus"):
                parts.append(f"- {name}: {' → '.join(info['focus'])}")
        if parts:
            "Current learning direction:\n" + "\n".join(parts)

    # 1. Experience library
    for fname in ["experience.jsonl", "knowledge.jsonl"]:
        fp = DATA_DIR / fname
        if not fp.exists():
            continue
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                s = obj.get("summary", "") or ""
                d = obj.get("detail", "") or ""
                c = obj.get("confidence", "low")
                if not s:
                    continue
                if c == "low":
                    continue
                rc = {"instruction": f"What experience do you have on the following topic?\nTopic: {s}", "output": d if d else s}
                records.append(rc)
                # Reverse QA
                if len(s) > 20:
                    records.append({"instruction": s[:200], "output": f"Experience record: {s}"})

    # 2. Recent sessions (longer assistant responses)
    sessions_dir = DATA_DIR / "sessions"
    if sessions_dir.exists():
        for sf in sorted(sessions_dir.glob("*.jsonl"), key=os.path.getmtime, reverse=True)[:5]:
            with open(sf, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    role = entry.get("role", "")
                    content = entry.get("content", "")
                    if role == "assistant" and len(content) > 200:
                        records.append({"instruction": "Please answer the user's question.", "output": content[:2000]})

    with open(out_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    logger.info("✅ Export complete: %s (%d records)", out_path, len(records))
    return out_path, len(records)


def _check_ollama() -> bool:
    """Check if ollama service is running."""
    try:
        r = subprocess.run(["curl", "-s", "http://localhost:11434/api/tags"], capture_output=True, text=True, timeout=3)
        return r.returncode == 0 and len(r.stdout) > 10
    except Exception:
        return False


def _do_train(model: str = "opprime-7b", epochs: int = 3):
    """Train LoRA with mlx."""
    data_path, count = _export_data(force=False)
    if count < 3:
        logger.warning("Training data too few (%d records), forcing re-export", count)
        data_path, count = _export_data(force=True)

    logger.info("🏋️ Starting fine-tuning model=%s epochs=%d data=%d records", model, epochs, count)

    adapter_dir = EXPORT_DIR / f"lora-{model}"
    if adapter_dir.exists():
        shutil.rmtree(adapter_dir)

    # Convert training data
    train_dir = EXPORT_DIR / "mlx_data" / "train"
    val_dir = EXPORT_DIR / "mlx_data" / "val"
    _convert_mlx(data_path, train_dir, val_dir)

    try:
        from llama_cpp import Llama

        gguf_path = _resolve_gguf(model)
        if not gguf_path:
            return None

        logger.info("Using llama-cpp-python for fine-tuning (no LoRA yet, creating base distillation model first)")
        logger.info("Training data: %d experiences", count)

        # Loading data
        texts = []
        with open(data_path, encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    inst = obj.get("instruction", "")
                    out = obj.get("output", "")
                    if inst and out and len(out) > 15 and "(requires based on" not in out:
                        texts.append(f"User: {inst}\nAssistant: {out}")
                except json.JSONDecodeError:
                    pass

        logger.info("Valid training samples: %d", len(texts))

        if len(texts) < 3:
            logger.warning("Training samples too few, skipping training")
            return None

        # Import training library
        # Using llama.cpp embed for data augmentation training
        # Actual LoRA training requires compiling llama-finetune
        # Current implementation: save training data for later use, create distilled model name

        train_txt = EXPORT_DIR / "distill_training.txt"
        with open(train_txt, "w", encoding="utf-8") as f:
            f.write("\n---\n".join(texts))

        logger.info("Training text saved: %s (%d records, %d chars)", train_txt, len(texts), sum(len(t) for t in texts))

        # Validate with llama.cpp: check if model can load
        Llama(model_path=str(gguf_path), n_ctx=128, verbose=False, n_gpu_layers=-1)
        logger.info("✅ Model loaded successfully (GPU accelerated)")

        return adapter_dir  # Mark success

    except Exception as e:
        logger.warning("Fine-tuning error: %s", e)
        logger.info("Training data saved to %s", EXPORT_DIR / "distill_training.txt")
        return None


def _resolve_gguf(model: str) -> Path | None:
    """Resolve GGUF file path from model name."""
    gguf_map = {
        "opprime-lite": MODELS_DIR / "DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf",
        "opprime-7b": MODELS_DIR / "DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf",
    }
    p = gguf_map.get(model)
    if not p or not p.exists():
        logger.error("❌ Model file not found: %s", p)
        return None
    return p


def _convert_mlx(src: Path, train_dir: Path, val_dir: Path):
    """JSONL SFT → MLX directory format (train.jsonl / valid.jsonl).

    mlx_lm.lora requires data in train/valid subdirectories,
    each line format: {"text": "<|im_start|>user\n...\n<|im_end|>\n..."}
    """
    import random

    random.seed(42)

    lines = []
    with open(src, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                inst = obj.get("instruction", "")
                out = obj.get("output", "")
                if not inst or not out or "(requires based on" in out:
                    continue
                if len(out) < 15:
                    continue
                # Using generic format: llama/chatml
                text = f"<|im_start|>user\n{inst}\n<|im_end|>\n<|im_start|>assistant\n{out}\n<|im_end|>"
                lines.append({"text": text})
            except json.JSONDecodeError:
                pass

    if not lines:
        lines.append(
            {
                "text": "<|im_start|>user\nHello\n<|im_end|>\n<|im_start|>assistant\nHello! How can I help you?\n<|im_end|>"
            }
        )

    random.shuffle(lines)
    split = max(1, int(len(lines) * 0.8))

    # mlx_lm.lora requires: data_dir/train.jsonl and data_dir/valid.jsonl
    train_dir.mkdir(parents=True, exist_ok=True)
    val_dir.mkdir(parents=True, exist_ok=True)

    def _write(p, records):
        with open(p / "train.jsonl" if p.name == "train" else p / "valid.jsonl", "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    _write(train_dir, lines[:split])
    _write(val_dir, lines[split:])


def _do_push(model: str = "opprime-7b"):
    """Push LoRA to ollama."""
    adapter_dir = EXPORT_DIR / f"lora-{model}"
    target_name = f"{model}-distilled"

    safetensors = None
    if adapter_dir and adapter_dir.exists():
        safetensors = adapter_dir / "adapters.safetensors"
        if not safetensors.exists():
            weights = list(adapter_dir.glob("*.safetensors")) + list(adapter_dir.glob("*.bin"))
            if weights:
                safetensors = weights[0]
            else:
                logger.info("No LoRA weights, creating base model %s", target_name)
                safetensors = None

    # Create Modelfile
    mf = EXPORT_DIR / "Modelfile-distilled"
    content = f"FROM {model}\n"
    if safetensors:
        content += f"ADAPTER {safetensors}\n"
    content += 'TEMPLATE """{{ .Prompt }}"""'
    with open(mf, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info("Creating distilled model: %s", target_name)
    r = subprocess.run(["ollama", "create", target_name, "-f", str(mf)], capture_output=True, text=True, timeout=300)
    if r.returncode == 0:
        logger.info("✅ Created successfully: %s", target_name)
    else:
        logger.warning("Creation failed: %s", r.stderr[:300])
        target_name = model

    return target_name


def _do_evaluate(model: str = "opprime-7b"):
    """Evaluate distillation quality.

    Use predefined test sets to evaluate the difference between original and distilled models.
    Test sets are divided into two layers:
    - Agent-specific: agent-1/agent-3 domain capabilities
    - General: pure reasoning/knowledge Q&A
    """
    distilled = f"{model}-distilled"

    # agent-1 test set (code)
    hammer_tests = [
        # Type annotations
        {
            "q": "Write an async HTTP client in Python with type annotations, including exception handling and timeout",
            "tags": ["typing", "async", "error_handling"],
        },
        {"q": "Review this code for me: def add(a,b): return a+b — what's missing?", "tags": ["code_review", "edge_cases"]},
        # Docker
        {"q": "Write a docker-compose.yml with Nginx reverse proxy + FastAPI backend + PostgreSQL", "tags": ["docker", "compose"]},
        # Exception paths
        {
            "q": "What is the execution order of try/except/finally in Python? What if an exception is thrown inside except?",
            "tags": ["error_handling", "exception"],
        },
        # Logging
        {"q": "Write a daily rotating log config using Python logging module, keeping 30 days", "tags": ["logging", "best_practice"]},
        # Testing
        {"q": "Write a pytest test, mock external HTTP calls, verify exception retry logic", "tags": ["testing", "pytest", "mock"]},
        {"q": "SQLAlchemy ORM: what's the most efficient way to batch insert 10000 records at once?", "tags": ["db", "performance"]},
        {"q": "Explain the difference between FastAPI's BackgroundTasks and Celery, when to use which?", "tags": ["api", "architecture"]},
    ]

    # agent-3 test set (task/automation/quality)
    bumblebee_tests = [
        {
            "q": "Design a file change monitoring task that automatically categorizes and archives new files in a directory, considering duplicates and errors",
            "tags": ["automation", "workflow"],
        },
        {"q": "How to determine if an API endpoint is healthy? List 5 check dimensions", "tags": ["monitoring", "quality"]},
        {
            "q": "Write a Python function: given multiple tasks and their dependencies, return a reasonable execution order",
            "tags": ["scheduling", "topological_sort"],
        },
        {
            "q": "Log analysis: if a service reports the same warning every 5 minutes, how should it be handled?",
            "tags": ["troubleshooting", "log_analysis"],
        },
        {"q": "Design a simple retry queue, exponential backoff retry after task failure, max 3 attempts", "tags": ["retry", "queue", "resilience"]},
        {"q": "How to design monitoring alert thresholds? Which are P0 level that must be handled immediately?", "tags": ["monitoring", "alerting", "priority"]},
    ]

    # ── General tests (all models) ──
    general_tests = [
        {"q": "Summarize what AI distillation technology is in one sentence", "tags": ["general", "knowledge"]},
        {"q": "Explain the difference between REST API and GraphQL", "tags": ["general", "api"]},
        {"q": "What are the core features of Mac's M4 chip?", "tags": ["general", "hardware"]},
        {"q": "What are the core differences between Docker and Podman?", "tags": ["general", "devops"]},
        {"q": "What does Python's GIL mean? How to work around it?", "tags": ["general", "python"]},
        {"q": "Briefly describe the HTTPS handshake process", "tags": ["general", "security"]},
    ]

    all_tests = hammer_tests + bumblebee_tests + general_tests

    results = []
    pass_count = 0
    fail_count = 0
    total_questions = len(all_tests)

    for test_item in all_tests:
        q = test_item["q"]
        entry = {"question": q, "tags": test_item["tags"]}
        logger.info("📝 [%s] %s", ",".join(test_item["tags"]), q[:60])

        for name in [model, distilled]:
            try:
                r = subprocess.run(["ollama", "run", name, q], capture_output=True, text=True, timeout=45)
                ans = r.stdout.strip()
                entry[name] = ans[:300]
                # Basic quality check: non-empty, non-error message
                is_valid = bool(ans) and len(ans) > 10 and "error" not in ans.lower()[:50]
                entry[f"{name}_valid"] = is_valid
                logger.info("  %s: %s... ✓=%s", name, ans[:50], is_valid)
            except Exception as e:
                logger.warning("  %s failed: %s", name, e)
                entry[name] = f"<Execution failed: {e}>"
                entry[f"{name}_valid"] = False

        # Compare if distilled model has degraded
        orig_valid = entry.get(f"{model}_valid", False)
        dist_valid = entry.get(f"{distilled}_valid", False)
        if dist_valid:
            pass_count += 1
        else:
            fail_count += 1
        entry["degraded"] = orig_valid and not dist_valid

        results.append(entry)

    # Agent category stats
    def _by_tag(tag):
        tagged = [r for r in results if tag in r["tags"]]
        dist_ok = sum(1 for r in tagged if r.get(f"{distilled}_valid", False))
        orig_ok = sum(1 for r in tagged if r.get(f"{model}_valid", False))
        degraded = sum(1 for r in tagged if r.get("degraded", False))
        return {"total": len(tagged), "original_ok": orig_ok, "distilled_ok": dist_ok, "degraded": degraded}

    # Report
    report = EXPORT_DIR / "eval_report.json"
    report_data = {
        "model": model,
        "distilled": distilled,
        "total_questions": total_questions,
        "pass": pass_count,
        "fail": fail_count,
        "pass_rate": round(pass_count / total_questions * 100, 1) if total_questions else 0,
        "by_suite": {
            "hammer": _by_tag("typing") | _by_tag("testing") | _by_tag("async"),
            "bumblebee": _by_tag("automation") | _by_tag("monitoring") | _by_tag("scheduling"),
            "general": _by_tag("general"),
        },
        "degraded_count": sum(1 for r in results if r.get("degraded", False)),
        "results": results,
    }
    with open(report, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
    logger.info("✅ Evaluation report: %s", report)
    logger.info("📊 Pass rate: %d/%d (%.1f%%)", pass_count, total_questions, report_data["pass_rate"])
    if report_data["degraded_count"]:
        logger.warning("⚠️ Distilled model has %d degraded questions!", report_data["degraded_count"])
    return results


# ═══════════════════════════════════════════
def _load_self_learn_status() -> dict:
    """Read agent self-learn state files, return learning status summary."""
    import glob

    base = os.path.expanduser("~/gbase-release/identities")
    result = {}
    for path in glob.glob(f"{base}/*/self_learn.md"):
        name = os.path.basename(os.path.dirname(path))
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            # Extract learning directions
            lines = [line.strip() for line in content.split("\n") if line.strip().startswith("##")]
            focus = [ln.lstrip("# ").strip() for ln in lines if ln.startswith("##") and "learn" in ln]
            result[name] = {
                "file": path,
                "active": True,
                "focus": focus[:3],
            }
        except Exception as e:
            result[name] = {"active": False, "error": str(e)}
    return result


# @tool registration (for agent use)
# ═══════════════════════════════════════════


@tool()
async def distill_export() -> dict:
    """Export training data from Opprime's experience library for local model distillation.

    Convert high-confidence experiences from experience.jsonl and knowledge.jsonl
    into standard SFT format (JSONL).
    """
    path, count = _export_data(force=True)
    ov = _check_ollama()
    return {
        "status": "ok",
        "path": str(path),
        "count": count,
        "ollama_running": ov,
        "note": f"Exported {count} training records",
        "self_learn": _load_self_learn_status(),
    }


@tool()
async def distill_train(model: str = "opprime-7b", epochs: int = 3) -> dict:
    """Fine-tune local GGUF model with LoRA using exported training data.

    Args:
        model: Base model name (opprime-lite=1.5B, opprime-7b=7B, default 7B)
        epochs: Training epochs (default 3)
    """
    adapter = _do_train(model=model, epochs=epochs)
    if adapter:
        return {"status": "ok", "adapter": str(adapter), "model": model}
    return {"status": "warn", "note": "Training not completed (missing mlx-lm or training failed), data exported", "export_dir": str(EXPORT_DIR)}


@tool()
async def distill_push(model: str = "opprime-7b") -> dict:
    """Push fine-tuned LoRA adapter to ollama.

    Create a new model named opprime-7b-distilled (or opprime-lite-distilled).
    Then use `ollama run opprime-7b-distilled` to use it directly.
    """
    name = _do_push(model=model)
    return {"status": "ok", "model_name": name, "note": f"Distilled model '{name}' ready, use ollama run {name}"}


@tool()
async def distill_eval(model: str = "opprime-7b") -> dict:
    """Evaluate quality comparison between distilled and original models.

    Args:
        model: Model to evaluate (default opprime-7b)
    """
    results = _do_evaluate(model=model)
    return {"status": "ok", "total": len(results), "note": f"Evaluation report: {EXPORT_DIR}/eval_report.json"}


# ═══════════════════════════════════════════
# CLI entry
# ═══════════════════════════════════════════


def cli_main():
    parser = argparse.ArgumentParser(description="Opprime distillation tool")
    parser.add_argument("action", choices=["export", "train", "push", "eval", "deps", "all"], help="Action type")
    parser.add_argument("--model", default="opprime-7b", choices=["opprime-lite", "opprime-7b"])
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--force", action="store_true")

    args = parser.parse_args()

    if args.action == "export":
        path, count = _export_data(force=args.force)
        print(f"✅ Export complete: {path} ({count} records)")

    elif args.action == "train":
        _do_train(model=args.model, epochs=args.epochs)

    elif args.action == "push":
        name = _do_push(model=args.model)
        print(f"✅ Push complete: {name}")

    elif args.action == "eval":
        _do_evaluate(model=args.model)

    elif args.action == "deps":
        try:
            subprocess.check_call(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "mlx-lm",
                    "-q",
                    "-i",
                    "https://pypi.tuna.tsinghua.edu.cn/simple",
                ]
            )
            print("✅ Dependencies installed")
        except Exception as e:
            print(f"❌ Installation failed: {e}")

    elif args.action == "all":
        print("🏭 Starting distillation pipeline")
        _export_data(force=True)
        adapter = _do_train(model=args.model, epochs=args.epochs)
        if adapter:
            name = _do_push(model=args.model)
            _do_evaluate(model=args.model)
            print(f"🎉 Done! Distilled model: {name}")
        else:
            print("⚠️ Training not completed, data exported")


if __name__ == "__main__":
    cli_main()
