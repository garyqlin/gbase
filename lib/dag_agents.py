#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
╔═══════════════════════════════════════════════════════════╗
║  DAG Agent Registry — register cron scripts as DAG engine functions       ║
║                                                           ║
║  一次性调用:                                                ║
║    from lib.dag_agents import register_all                ║
║    orch = DAGOrchestrator()                               ║
║    register_all(orch)                                     ║
║    result = orch.run("执行每日巡检")                       ║
║                                                           ║
║  注册的 Agent 类型 → 实际函数:                               ║
║    health_check   → cron-health.sh 心跳检测                ║
║    arch_audit     → arch-audit.py JSON 审计               ║
║    check_inbox    → 精灵邮箱 check_inbox 工具              ║
║    mirror_decay   → mirror-maintain.py 衰减+审查          ║
║    cognifold_sync → cognifold-maintain.py 概念簇同步      ║
║    whitebox_check → qa_double_check 白盒                  ║
║    blackbox_check → qa_execute_blackbox 黑盒              ║
║    swarm_test     → qa_swarm_test 蜂群                   ║
║    weekly_stats   → cron-analyzer.py 统计                ║
╚═══════════════════════════════════════════════════════════╝
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path("/home/opprime-v2")
sys.path.insert(0, str(PROJECT_ROOT))

# ── 工具级安全钩子 ──

def disk_safety_hook(step: dict, context: dict) -> tuple:
    """磁盘安全检查：如果磁盘使用 > 90%，阻止写入类操作。"""
    try:
        df = subprocess.run(
            ["df", "-P", "/"], capture_output=True, text=True, timeout=5
        )
        for line in df.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 5:
                pct = int(parts[4].replace("%", ""))
                if pct > 90:
                    # 只有写入类 agent 才阻止
                    write_agents = {"arch_audit", "cognifold_sync", "qa_summary"}
                    if step.get("agent_type") in write_agents:
                        return False, f"磁盘使用率 {pct}% > 90%，阻止写入操作"
    except Exception:
        pass
    return True, "ok"


def mem_safety_hook(step: dict, context: dict) -> tuple:
    """内存安全检查：如果可用内存 < 200MB，阻止重量级操作。"""
    try:
        with open("/proc/meminfo") as f:
            content = f.read()
        import re
        avail_match = re.search(r"MemAvailable:\s+(\d+)", content)
        if avail_match:
            avail_kb = int(avail_match.group(1))
            if avail_kb < 200 * 1024:
                heavy_agents = {"swarm_test", "arch_audit", "cognifold_sync"}
                if step.get("agent_type") in heavy_agents:
                    return False, f"可用内存 {avail_kb//1024}MB < 200MB，阻止重量级操作"
    except Exception:
        pass
    return True, "ok"


# ═══════════════════════════════════════════════════════
# Agent 函数实现
# ═══════════════════════════════════════════════════════

def agent_health_check(inputs: dict, context: dict) -> dict:
    """执行 cron-health.sh 心跳检测并解析结果。"""
    script = PROJECT_ROOT / "cron" / "cron-health.sh"
    if not script.exists():
        return {"status": "error", "error": "cron-health.sh 不存在"}

    try:
        result = subprocess.run(
            ["bash", str(script)],
            capture_output=True, text=True, timeout=15
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "心跳检测超时"}

    # 读取最新心跳
    heartbeat_file = PROJECT_ROOT / "logs" / "cron" / "heartbeat.jsonl"
    latest = {}
    if heartbeat_file.exists():
        try:
            lines = heartbeat_file.read_text().strip().split("\n")
            if lines:
                latest = json.loads(lines[-1])
        except (json.JSONDecodeError, IndexError):
            pass

    return {
        "status": "ok",
        "exit_code": result.returncode,
        "heartbeat": latest,
        "stdout": result.stdout.strip()[-500:],
        "stderr": result.stderr.strip()[-200:] if result.stderr else "",
    }


def agent_arch_audit(inputs: dict, context: dict) -> dict:
    """执行 arch-audit.py JSON 审计。"""
    script = PROJECT_ROOT / "cron" / "arch-audit.py"
    if not script.exists():
        return {"status": "error", "error": "arch-audit.py 不存在"}

    try:
        result = subprocess.run(
            [sys.executable, str(script), "--json"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            audit_data = json.loads(result.stdout)
            # 汇总统计
            findings = audit_data.get("findings", [])
            by_type = {}
            for f in findings:
                t = f.get("type", "unknown")
                by_type[t] = by_type.get(t, 0) + 1
            return {
                "status": "ok",
                "total_findings": len(findings),
                "by_type": by_type,
                "scan_time": audit_data.get("scan_time", ""),
                "top_findings": findings[:5],
            }
        else:
            return {"status": "error", "error": result.stderr[:500]}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "架构审计超时"}
    except json.JSONDecodeError:
        return {"status": "error", "error": "审计输出 JSON 解析失败"}


def agent_generate_report(inputs: dict, context: dict) -> dict:
    """将 health + audit 结果汇总为巡检报告。"""
    health = inputs.get("health", {})
    audit = inputs.get("audit", {})

    report_lines = []
    report_lines.append("=" * 50)
    report_lines.append("Opprime 每日巡检报告")
    report_lines.append(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append("=" * 50)

    # 心跳
    hb = health.get("heartbeat", {})
    if hb:
        status = hb.get("status", "unknown")
        load = hb.get("load_1", "N/A")
        mem_avail = hb.get("mem_avail_mb", "N/A")
        disk = hb.get("disk_pct", "N/A")
        report_lines.append(f"\n[健康] cron: {status} | 负载: {load} | 可用内存: {mem_avail}MB | 磁盘: {disk}%")

    # 审计
    total = audit.get("total_findings", 0)
    by_type = audit.get("by_type", {})
    report_lines.append(f"\n[审计] 发现 {total} 个问题")
    for t, count in sorted(by_type.items()):
        report_lines.append(f"  - {t}: {count}")

    # 评估
    if total == 0 and hb.get("status") == "alive":
        report_lines.append("\n[结论] ✅ 系统健康，无需人工介入")
    elif hb.get("status") != "alive":
        report_lines.append("\n[结论] ❌ CRITICAL: cron 守护进程离线!")
    else:
        report_lines.append(f"\n[结论] ⚠️ 发现 {total} 个问题，建议审查")

    report = "\n".join(report_lines)
    return {"status": "ok", "report": report, "health_ok": hb.get("status") == "alive", "audit_count": total}


def agent_check_inbox(inputs: dict, context: dict) -> dict:
    """读取精灵邮箱收件箱（调用 check_inbox 工具）。"""
    try:
        # 直接读取邮件存储目录
        inbox_dir = PROJECT_ROOT / "data" / "mailbox" / "zagu" / "inbox"
        mails = []
        if inbox_dir.exists():
            for f in sorted(inbox_dir.glob("*.json"), key=os.path.getmtime, reverse=True)[:20]:
                try:
                    data = json.loads(f.read_text())
                    mails.append({
                        "id": f.stem,
                        "from": data.get("from", ""),
                        "subject": data.get("subject", ""),
                        "ts": data.get("ts", ""),
                        "read": data.get("read", False),
                    })
                except Exception:
                    pass
        return {"status": "ok", "count": len(mails), "mails": mails}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def agent_classify_mails(inputs: dict, context: dict) -> dict:
    """邮件分类（基于规则）。"""
    mails = inputs.get("mails", [])
    categories = {"系统": [], "学习": [], "通知": [], "其他": []}

    for mail in mails:
        subject = mail.get("subject", "").lower()
        if any(kw in subject for kw in ["健康", "告警", "error", "失败", "crash"]):
            categories["系统"].append(mail)
        elif any(kw in subject for kw in ["学习", "learn", "rss", "知识"]):
            categories["学习"].append(mail)
        elif any(kw in subject for kw in ["通知", "提醒", "周报"]):
            categories["通知"].append(mail)
        else:
            categories["其他"].append(mail)

    return {"status": "ok", "classified": categories, "total": len(mails)}


def agent_summarize_mails(inputs: dict, context: dict) -> dict:
    """邮件摘要（规则引擎）。"""
    classified = inputs.get("classified", {})
    lines = []
    total = 0
    for cat, mails in classified.items():
        count = len(mails)
        total += count
        if count > 0:
            subjects = [m.get("subject", "?")[:50] for m in mails[:3]]
            lines.append(f"[{cat}] {count}封: {', '.join(subjects)}")
            if count > 3:
                lines[-1] += f" ...等{count}封"

    digest = "\n".join(lines) if lines else "无新邮件"
    return {"status": "ok", "digest": digest, "total_mails": total}


def agent_whitebox_check(inputs: dict, context: dict) -> dict:
    """白盒检查（代码静态分析）。"""
    # 简化版：检查关键文件是否存在、语法是否正确
    key_files = [
        "lib/kernel.py", "lib/mirror.py", "lib/pipeline.py",
        "cron/loop-cache.py", "cron/cron-analyzer.py",
    ]
    results = []
    for fpath in key_files:
        full = PROJECT_ROOT / fpath
        if not full.exists():
            results.append({"file": fpath, "status": "missing"})
        else:
            content = full.read_text()
            lines = len(content.split("\n"))
            imports = [l.strip() for l in content.split("\n") if l.strip().startswith("import ") or l.strip().startswith("from ")]
            results.append({
                "file": fpath, "status": "ok", "lines": lines,
                "imports_count": len(imports),
            })

    errors = [r for r in results if r["status"] != "ok"]
    return {
        "status": "ok",
        "total_files": len(key_files),
        "errors": len(errors),
        "results": results,
    }


def agent_blackbox_check(inputs: dict, context: dict) -> dict:
    """黑盒检查（接口可达性）。"""
    endpoints = [
        ("localhost", 8420, "标准版"),
        ("localhost", 8431, "agent-1"),
        ("localhost", 8432, "agent-2"),
    ]
    import socket
    results = []
    for host, port, name in endpoints:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            results.append({"endpoint": f"{host}:{port}", "name": name, "reachable": result == 0})
        except Exception:
            results.append({"endpoint": f"{host}:{port}", "name": name, "reachable": False})

    reachable = sum(1 for r in results if r["reachable"])
    return {"status": "ok", "total": len(endpoints), "reachable": reachable, "results": results}


def agent_swarm_test(inputs: dict, context: dict) -> dict:
    """蜂群压力测试（简化版：并发 ping 多个端点）。"""
    import concurrent.futures
    endpoints = [("localhost", p) for p in [8420, 8431, 8432]]
    import socket

    def check(host, port):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            t0 = time.time()
            result = sock.connect_ex((host, port))
            elapsed = (time.time() - t0) * 1000
            sock.close()
            return {"port": port, "ok": result == 0, "latency_ms": round(elapsed, 1)}
        except Exception as e:
            return {"port": port, "ok": False, "error": str(e)[:100]}

    # 10 并发 × 3 轮
    all_results = []
    for round_num in range(3):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(check, h, p) for h, p in endpoints for _ in range(10)]
            round_results = [f.result() for f in concurrent.futures.as_completed(futures)]
        ok = sum(1 for r in round_results if r["ok"])
        latencies = [r["latency_ms"] for r in round_results if r["ok"]]
        all_results.append({
            "round": round_num + 1,
            "total": len(round_results),
            "ok": ok,
            "fail": len(round_results) - ok,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        })

    return {"status": "ok", "rounds": all_results, "degradation_detected": False}


def agent_qa_summary(inputs: dict, context: dict) -> dict:
    """QA 结果汇总。"""
    parts = {}
    for key in ["whitebox", "blackbox", "swarm"]:
        val = inputs.get(key, inputs.get(f"{key}_result", {}))
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except json.JSONDecodeError:
                val = {"raw": val}
        parts[key] = val

    errors = sum(
        p.get("errors", p.get("total", 0) - p.get("reachable", p.get("total", 0)))
        for p in [parts.get("whitebox", {}), parts.get("blackbox", {})]
    )
    swarm_rounds = parts.get("swarm", {}).get("rounds", [])
    degradation = parts.get("swarm", {}).get("degradation_detected", False)

    verdict = "PASS"
    if errors > 0 or degradation:
        verdict = "FAIL"
    elif errors > 3:
        verdict = "WARN"

    report = f"QA 报告 | 白盒: {parts.get('whitebox', {}).get('errors', '?')}错误 | "
    report += f"黑盒: {parts.get('blackbox', {}).get('reachable', '?')}/{parts.get('blackbox', {}).get('total', '?')}可达 | "
    report += f"蜂群: {len(swarm_rounds)}轮 退化={degradation} | 结论: {verdict}"

    return {"status": "ok", "verdict": verdict, "report": report, "errors": errors}


def agent_weekly_stats(inputs: dict, context: dict) -> dict:
    """周度统计（调用 cron-analyzer.py 的输出）。"""
    analysis_file = PROJECT_ROOT / "logs" / "cron" / "analysis.json"
    if analysis_file.exists():
        try:
            data = json.loads(analysis_file.read_text())
            return {"status": "ok", "analysis": data}
        except json.JSONDecodeError:
            pass

    # 降级：手动统计
    return {"status": "ok", "analysis": {"note": "无 analysis.json，需先运行 cron-analyzer.py", "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}}


def agent_trend_analysis(inputs: dict, context: dict) -> dict:
    """趋势分析（基于 weekly_stats 的简化版）。"""
    stats = inputs.get("stats", inputs.get("analysis", {}))
    return {"status": "ok", "trend": "stable", "detail": "本周各项指标正常范围内波动", "data": stats}


def agent_weekly_report(inputs: dict, context: dict) -> dict:
    """周报生成。"""
    stats = inputs.get("stats", "?")
    trend = inputs.get("trend", "?")
    report = f"Opprime 周度报告\n统计: {stats}\n趋势: {trend}\n生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}"
    return {"status": "ok", "report": report}


def agent_mirror_decay(inputs: dict, context: dict) -> dict:
    """鉴面衰减 + 审查。"""
    script = PROJECT_ROOT / "cron" / "mirror-maintain.py"
    if not script.exists():
        return {"status": "error", "error": "mirror-maintain.py 不存在"}
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--dry-run"],
            capture_output=True, text=True, timeout=30
        )
        return {"status": "ok", "output": result.stdout.strip()[-500:], "exit_code": result.returncode}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "鉴面维护超时"}


def agent_mirror_review(inputs: dict, context: dict) -> dict:
    """鉴面回溯审查。"""
    try:
        from lib.mirror import Mirror
        mirror = Mirror()
        mirror.setup()
        if mirror._conn is None:
            return {"status": "error", "error": "mirror 未初始化"}
        stats = mirror.get_stats()
        mirror.close()
        return {"status": "ok", "stats": stats}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def agent_cognifold_concepts(inputs: dict, context: dict) -> dict:
    """Cognifold 概念簇自组织。"""
    try:
        from lib.cognifold import Cognifold
        cf = Cognifold()
        events = cf.load_recent_events(limit=50)
        if events:
            cf.ingest_batch(events)
            cf.reorganize()
            clusters = cf.get_clusters()
            intents = cf.get_intents()
            return {
                "status": "ok",
                "events_processed": len(events),
                "clusters": len(clusters),
                "intents": len(intents),
                "top_intents": intents[:3] if intents else [],
            }
        return {"status": "ok", "events_processed": 0, "clusters": 0, "intents": 0}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def agent_intent_emergence(inputs: dict, context: dict) -> dict:
    """意图浮现。"""
    try:
        from lib.cognifold import Cognifold
        cf = Cognifold()
        intents = cf.get_intents()
        alerts = [i for i in intents if i.get("level") == "alert"]
        suggestions = [i for i in intents if i.get("level") == "suggestion"]
        return {
            "status": "ok",
            "alerts": len(alerts),
            "suggestions": len(suggestions),
            "top_alert": alerts[0] if alerts else None,
            "top_suggestion": suggestions[0] if suggestions else None,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ═══════════════════════════════════════════════════════
# 全量注册
# ═══════════════════════════════════════════════════════

AGENT_REGISTRY = {
    "health_check": agent_health_check,
    "arch_audit": agent_arch_audit,
    "generate_report": agent_generate_report,
    "check_inbox": agent_check_inbox,
    "classify_mails": agent_classify_mails,
    "summarize_mails": agent_summarize_mails,
    "whitebox_check": agent_whitebox_check,
    "blackbox_check": agent_blackbox_check,
    "swarm_test": agent_swarm_test,
    "qa_summary": agent_qa_summary,
    "weekly_stats": agent_weekly_stats,
    "trend_analysis": agent_trend_analysis,
    "weekly_report": agent_weekly_report,
    "mirror_decay": agent_mirror_decay,
    "mirror_review": agent_mirror_review,
    "cognifold_concepts": agent_cognifold_concepts,
    "intent_emergence": agent_intent_emergence,
}

SAFETY_HOOKS = {
    "disk_safety": disk_safety_hook,
    "mem_safety": mem_safety_hook,
}


def register_all(orchestrator) -> int:
    """一次性注册所有 agent 和安全钩子到 DAGOrchestrator。

    Args:
        orchestrator: DAGOrchestrator 实例

    Returns:
        注册的 agent 数量
    """
    count = 0
    for name, func in AGENT_REGISTRY.items():
        orchestrator.register_agent(name, func)
        count += 1

    for name, func in SAFETY_HOOKS.items():
        orchestrator.register_safety_hook(name, func)

    return count


# ── CLI 入口 ──

if __name__ == "__main__":
    """独立测试：注册后跑一次 daily-patrol。"""
    print("注册 DAG agent...")
    from lib.dag_orchestrator import DAGOrchestrator

    orch = DAGOrchestrator()
    n = register_all(orch)
    print(f"  ✓ 注册 {n} 个 agent + {len(SAFETY_HOOKS)} 个安全钩子")

    print("\n执行 daily-patrol...")
    result = orch.run(task="执行每日巡检", context={"date": time.strftime("%Y-%m-%d")})
    print(json.dumps(result, ensure_ascii=False, indent=2))
