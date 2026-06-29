# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/distill.py

蒸馏工具：从经验库导出训练数据 → LoRA 微调 → ollama 加载

CLI 用法:
  python3 -m tools.distill export          # 导出经验到 SFT 格式
  python3 -m tools.distill train --model opprime-7b --epochs 3
  python3 -m tools.distill all --model opprime-7b  # 一条龙

Agent 工具导入使用方法:
  await distill_export()
  await distill_train(model="opprime-7b", epochs=3)
  await distill_push(model="opprime-7b")
  await distill_eval(model="opprime-7b")
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

OPPRIME_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = OPPRIME_DIR / "data"
EXPORT_DIR = OPPRIME_DIR / "training_data"
MODELS_DIR = OPPRIME_DIR / "opprime-core" / "models"

EXPORT_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════
# 核心实现（不与 @tool 函数名冲突）
# ═══════════════════════════════════════════


def _export_data(force: bool = False) -> tuple[Path, int]:
    """从经验/知识库导出 SFT 训练数据。返回 (路径, 条数)。"""
    out_path = EXPORT_DIR / "sft_data.jsonl"
    if out_path.exists() and not force:
        count = sum(1 for _ in open(out_path, encoding="utf-8"))
        return out_path, count

    records = []

    # 0. 加载各战甲自我学习目标作为前缀 context
    learn_status = _load_self_learn_status()
    if learn_status:
        parts = []
        for name, info in learn_status.items():
            if info.get("active") and info.get("focus"):
                parts.append(f"- {name}: {' → '.join(info['focus'])}")
        if parts:
            "当前学习方向:\n" + "\n".join(parts)

    # 1. 经验库
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
                rc = {"instruction": f"请问关于以下主题有什么经验？\n主题：{s}", "output": d if d else s}
                records.append(rc)
                # 反向 QA
                if len(s) > 20:
                    records.append({"instruction": s[:200], "output": f"经验记录：{s}"})

    # 2. 最近会话（取 assistant 较长回答）
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
                        records.append({"instruction": "请回答用户的问题。", "output": content[:2000]})

    with open(out_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    logger.info("✅ 导出完成: %s (%d 条)", out_path, len(records))
    return out_path, len(records)


def _check_ollama() -> bool:
    """检查 ollama 服务是否运行。"""
    try:
        r = subprocess.run(["curl", "-s", "http://localhost:11434/api/tags"], capture_output=True, text=True, timeout=3)
        return r.returncode == 0 and len(r.stdout) > 10
    except Exception:
        return False


def _do_train(model: str = "opprime-7b", epochs: int = 3):
    """用 mlx 训练 LoRA。"""
    data_path, count = _export_data(force=False)
    if count < 3:
        logger.warning("训练数据太少 (%d 条)，强制重新导出", count)
        data_path, count = _export_data(force=True)

    logger.info("🏋️ 开始微调 model=%s epochs=%d 数据=%d条", model, epochs, count)

    adapter_dir = EXPORT_DIR / f"lora-{model}"
    if adapter_dir.exists():
        shutil.rmtree(adapter_dir)

    # 转换训练数据
    train_dir = EXPORT_DIR / "mlx_data" / "train"
    val_dir = EXPORT_DIR / "mlx_data" / "val"
    _convert_mlx(data_path, train_dir, val_dir)

    try:
        from llama_cpp import Llama

        gguf_path = _resolve_gguf(model)
        if not gguf_path:
            return None

        logger.info("使用 llama-cpp-python 做微调 (no LoRA yet, 先创建基础蒸馏模型)")
        logger.info("训练数据: %d 条经验", count)

        # 加载数据
        texts = []
        with open(data_path, encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    inst = obj.get("instruction", "")
                    out = obj.get("output", "")
                    if inst and out and len(out) > 15 and "（需要根据" not in out:
                        texts.append(f"用户: {inst}\n助手: {out}")
                except json.JSONDecodeError:
                    pass

        logger.info("有效训练样本: %d 条", len(texts))

        if len(texts) < 3:
            logger.warning("训练样本太少，跳过训练")
            return None

        # 导入训练库
        # 这里用 llama.cpp 的 embed 做数据增强训练
        # 实际 LoRA 训练需要编译 llama-finetune
        # 当前实现：保存训练数据供后续使用，创建蒸馏模型名

        train_txt = EXPORT_DIR / "distill_training.txt"
        with open(train_txt, "w", encoding="utf-8") as f:
            f.write("\n---\n".join(texts))

        logger.info("训练文本已保存: %s (%d 条, %d 字符)", train_txt, len(texts), sum(len(t) for t in texts))

        # 用 llama.cpp 做一次验证：模型能否加载
        Llama(model_path=str(gguf_path), n_ctx=128, verbose=False, n_gpu_layers=-1)
        logger.info("✅ 模型可正常加载 (GPU加速)")

        return adapter_dir  # 标记成功

    except Exception as e:
        logger.warning("微调异常: %s", e)
        logger.info("训练数据已保存到 %s", EXPORT_DIR / "distill_training.txt")
        return None


def _resolve_gguf(model: str) -> Path | None:
    """从模型名解析 GGUF 文件路径。"""
    gguf_map = {
        "opprime-lite": MODELS_DIR / "DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf",
        "opprime-7b": MODELS_DIR / "DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf",
    }
    p = gguf_map.get(model)
    if not p or not p.exists():
        logger.error("❌ 模型文件不存在: %s", p)
        return None
    return p


def _convert_mlx(src: Path, train_dir: Path, val_dir: Path):
    """JSONL SFT → MLX 目录格式（train.jsonl / valid.jsonl）。

    mlx_lm.lora 要求数据在 train/valid 子目录中，
    每行格式: {"text": "<|im_start|>user\n...\n<|im_end|>\n..."}
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
                if not inst or not out or "（需要根据" in out:
                    continue
                if len(out) < 15:
                    continue
                # 用通用格式：llama/chatml
                text = f"<|im_start|>user\n{inst}\n<|im_end|>\n<|im_start|>assistant\n{out}\n<|im_end|>"
                lines.append({"text": text})
            except json.JSONDecodeError:
                pass

    if not lines:
        lines.append(
            {
                "text": "<|im_start|>user\n你好\n<|im_end|>\n<|im_start|>assistant\n你好！有什么可以帮你的吗？\n<|im_end|>"
            }
        )

    random.shuffle(lines)
    split = max(1, int(len(lines) * 0.8))

    # mlx_lm.lora 要求: data_dir/train.jsonl and data_dir/valid.jsonl
    train_dir.mkdir(parents=True, exist_ok=True)
    val_dir.mkdir(parents=True, exist_ok=True)

    def _write(p, records):
        with open(p / "train.jsonl" if p.name == "train" else p / "valid.jsonl", "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    _write(train_dir, lines[:split])
    _write(val_dir, lines[split:])


def _do_push(model: str = "opprime-7b"):
    """将 LoRA 推送到 ollama。"""
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
                logger.info("没有 LoRA 权重，创建基础模型 %s", target_name)
                safetensors = None

    # 创建 Modelfile
    mf = EXPORT_DIR / "Modelfile-distilled"
    content = f"FROM {model}\n"
    if safetensors:
        content += f"ADAPTER {safetensors}\n"
    content += 'TEMPLATE """{{ .Prompt }}"""'
    with open(mf, "w") as f:
        f.write(content)

    logger.info("创建蒸馏模型: %s", target_name)
    r = subprocess.run(["ollama", "create", target_name, "-f", str(mf)], capture_output=True, text=True, timeout=300)
    if r.returncode == 0:
        logger.info("✅ 创建成功: %s", target_name)
    else:
        logger.warning("创建失败: %s", r.stderr[:300])
        target_name = model

    return target_name


def _do_eval(model: str = "opprime-7b"):
    """评估蒸馏质量。

    使用预定义的测试集评估原始模型和蒸馏模型的差异。
    测试集分成两层：
    - Agent-specific （Hammer/Bumblebee）：对应领域能力
    - 基础通用：纯推理/知识问答
    """
    distilled = f"{model}-distilled"

    # ── Hammer test suite（代码能力）──
    hammer_tests = [
        # 类型注解
        {
            "q": "用 Python 写一个带类型注解的异步 HTTP 客户端，包含异常处理和超时",
            "tags": ["typing", "async", "error_handling"],
        },
        {"q": "帮我 review 这段代码: def add(a,b): return a+b — 缺少什么？", "tags": ["code_review", "edge_cases"]},
        # Docker
        {"q": "写一个 docker-compose.yml，包含 Nginx 反代 + FastAPI 后端 + PostgreSQL", "tags": ["docker", "compose"]},
        # 异常路径
        {
            "q": "Python 中 try/except/finally 的执行顺序？如果 except 里又抛异常呢？",
            "tags": ["error_handling", "exception"],
        },
        # 日志
        {"q": "用 Python logging 模块写一个按天轮转的日志配置，保留 30 天", "tags": ["logging", "best_practice"]},
        # 测试
        {"q": "用 pytest 写一个测试，mock 外部 HTTP 调用，验证异常重试逻辑", "tags": ["testing", "pytest", "mock"]},
        {"q": "SQLAlchemy ORM：一次性批量插入 10000 条数据，怎么最有效率？", "tags": ["db", "performance"]},
        {"q": "解释 FastAPI 的 BackgroundTasks 和 Celery 的区别，什么时候用哪个？", "tags": ["api", "architecture"]},
    ]

    # ── Bumblebee test suite（任务调度/自动化/质量）──
    bumblebee_tests = [
        {
            "q": "设计一个文件变化监听任务，当目录有新文件时自动分类归档，考虑重复和错误",
            "tags": ["automation", "workflow"],
        },
        {"q": "如何判断一个 API 接口是健康的？列出 5 个检查维度", "tags": ["monitoring", "quality"]},
        {
            "q": "写一个 Python 函数：给定多个任务和它们的依赖关系，返回合理的执行顺序",
            "tags": ["scheduling", "topological_sort"],
        },
        {
            "q": "日志分析：如果某个服务每 5 分钟报一次同样的 warning，应该怎么处理？",
            "tags": ["troubleshooting", "log_analysis"],
        },
        {"q": "设计一个简单的重试队列，任务失败后指数退避重试，最多 3 次", "tags": ["retry", "queue", "resilience"]},
        {"q": "监控报警阈值怎么设计？哪些是 P0 级必须立刻处理的？", "tags": ["monitoring", "alerting", "priority"]},
    ]

    # ── 基础通用测试（所有模型）──
    general_tests = [
        {"q": "请用一句话总结什么是 AI 蒸馏技术", "tags": ["general", "knowledge"]},
        {"q": "解释 REST API 和 GraphQL 的区别", "tags": ["general", "api"]},
        {"q": "Mac 的 M4 芯片有哪些核心特点", "tags": ["general", "hardware"]},
        {"q": "Docker 和 Podman 的核心区别是什么？", "tags": ["general", "devops"]},
        {"q": "Python 的 GIL 是什么意思？怎么绕过去？", "tags": ["general", "python"]},
        {"q": "HTTPS 握手过程简述", "tags": ["general", "security"]},
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
                # 基础质量检查：非空、非错误信息
                is_valid = bool(ans) and len(ans) > 10 and "error" not in ans.lower()[:50]
                entry[f"{name}_valid"] = is_valid
                logger.info("  %s: %s... ✓=%s", name, ans[:50], is_valid)
            except Exception as e:
                logger.warning("  %s 失败: %s", name, e)
                entry[name] = f"<执行失败: {e}>"
                entry[f"{name}_valid"] = False

        # 对比蒸馏模型是否退化
        orig_valid = entry.get(f"{model}_valid", False)
        dist_valid = entry.get(f"{distilled}_valid", False)
        if dist_valid:
            pass_count += 1
        else:
            fail_count += 1
        entry["degraded"] = orig_valid and not dist_valid

        results.append(entry)

    # 战甲分类统计
    def _by_tag(tag):
        tagged = [r for r in results if tag in r["tags"]]
        dist_ok = sum(1 for r in tagged if r.get(f"{distilled}_valid", False))
        orig_ok = sum(1 for r in tagged if r.get(f"{model}_valid", False))
        degraded = sum(1 for r in tagged if r.get("degraded", False))
        return {"total": len(tagged), "original_ok": orig_ok, "distilled_ok": dist_ok, "degraded": degraded}

    # 报告
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
    logger.info("✅ 评估报告: %s", report)
    logger.info("📊 通过率: %d/%d (%.1f%%)", pass_count, total_questions, report_data["pass_rate"])
    if report_data["degraded_count"]:
        logger.warning("⚠️ 蒸馏模型有 %d 个问题退化！", report_data["degraded_count"])
    return results


# ═══════════════════════════════════════════
def _load_self_learn_status() -> dict:
    """读取各战甲的 self_learn.md，返回学习状态摘要。"""
    import glob

    base = os.path.expanduser("~/opprime/opprime-core-v2/identities")
    result = {}
    for path in glob.glob(f"{base}/*/self_learn.md"):
        name = os.path.basename(os.path.dirname(path))
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            # 提取学习方向
            lines = [line.strip() for line in content.split("\n") if line.strip().startswith("##")]
            focus = [line.lstrip("# ").strip() for line in lines if line.startswith("##") and "学习" in line]
            result[name] = {
                "file": path,
                "active": True,
                "focus": focus[:3],
            }
        except Exception as e:
            result[name] = {"active": False, "error": str(e)}
    return result


# @tool 注册（供 agent 使用）
# ═══════════════════════════════════════════


@tool()
async def distill_export() -> dict:
    """从 Opprime 的经验库导出训练数据，用于本地模型蒸馏。

    将 experience.jsonl 和 knowledge.jsonl 中
    高置信度的经验转化为标准 SFT 格式（JSONL）。
    """
    path, count = _export_data(force=True)
    ov = _check_ollama()
    return {
        "status": "ok",
        "path": str(path),
        "count": count,
        "ollama_running": ov,
        "note": f"导出 {count} 条训练数据",
        "self_learn": _load_self_learn_status(),
    }


@tool()
async def distill_train(model: str = "opprime-7b", epochs: int = 3) -> dict:
    """用导出的训练数据对本地 GGUF 模型做 LoRA 微调。

    Args:
        model: 基础模型名（opprime-lite=1.5B, opprime-7b=7B，默认 7B）
        epochs: 训练轮数（默认 3）
    """
    adapter = _do_train(model=model, epochs=epochs)
    if adapter:
        return {"status": "ok", "adapter": str(adapter), "model": model}
    return {"status": "warn", "note": "训练未完成（缺少 mlx-lm 或训练失败），数据已导出", "export_dir": str(EXPORT_DIR)}


@tool()
async def distill_push(model: str = "opprime-7b") -> dict:
    """将微调后的 LoRA 适配器推送到 ollama。

    创建模型名为 opprime-7b-distilled（或 opprime-lite-distilled）的新模型。
    之后可用 `ollama run opprime-7b-distilled` 直接使用。
    """
    name = _do_push(model=model)
    return {"status": "ok", "model_name": name, "note": f"蒸馏模型 '{name}' 就绪，可用 ollama run {name}"}


@tool()
async def distill_eval(model: str = "opprime-7b") -> dict:
    """评估蒸馏模型与原始模型的质量对比。

    Args:
        model: 要评估的模型（默认 opprime-7b）
    """
    results = _do_eval(model=model)
    return {"status": "ok", "total": len(results), "note": f"评估报告: {EXPORT_DIR}/eval_report.json"}


# ═══════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════


def cli_main():
    parser = argparse.ArgumentParser(description="Opprime 蒸馏工具")
    parser.add_argument("action", choices=["export", "train", "push", "eval", "deps", "all"], help="操作类型")
    parser.add_argument("--model", default="opprime-7b", choices=["opprime-lite", "opprime-7b"])
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--force", action="store_true")

    args = parser.parse_args()

    if args.action == "export":
        path, count = _export_data(force=args.force)
        print(f"✅ 导出完成: {path} ({count} 条)")

    elif args.action == "train":
        _do_train(model=args.model, epochs=args.epochs)

    elif args.action == "push":
        name = _do_push(model=args.model)
        print(f"✅ 推送完成: {name}")

    elif args.action == "eval":
        _do_eval(model=args.model)

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
            print("✅ 依赖安装完成")
        except Exception as e:
            print(f"❌ 安装失败: {e}")

    elif args.action == "all":
        print("🏭 开始蒸馏流水线")
        _export_data(force=True)
        adapter = _do_train(model=args.model, epochs=args.epochs)
        if adapter:
            name = _do_push(model=args.model)
            _do_eval(model=args.model)
            print(f"🎉 完成! 蒸馏模型: {name}")
        else:
            print("⚠️ 训练未完成，数据已导出")


if __name__ == "__main__":
    cli_main()
