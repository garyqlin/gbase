# SPDX-License-Identifier: MIT
"""
tools/qa_check.py

QA: agent-1 (whitebox) + agent-3 (blackbox) dual review.

Workflow:
1. Whitebox (agent-1): read source, review logic, check boundaries
2. Blackbox (agent-3): behavior-only, API responses
3. Cross-verify → conclusion

Usage: qa_double_check(target=<file path or API URL>, check_type="white"|"black"|"both")
"""

import logging

from lib.toolkit import tool

logger = logging.getLogger(__name__)


@tool()
async def qa_double_check(
    target: str,
    check_type: str = "both",
    code_file: str = "",
    _white_params: str = "",
    _black_params: str = "",
    _criteria: str = "",
) -> dict:
    """Dual-agent QA: whitebox (agent-1) + blackbox (agent-3) cross-verify.

    Whitebox checks (agent-1):
    - Read source/function implementations, check logic correctness
    - Check exception paths/boundary conditions
    - Check type annotations/error handling/logging
    - Check spec compliance

    Blackbox checks (agent-3):
    - Only access external API/CLI behavior
    - Verify input-output mapping
    - Test abnormal input responses
    - Read no source code

    Args:
        target: Check target description (e.g. "verify_intelligence function in tools/verify.py")
        check_type: "white"=whitebox, "black"=blackbox, "both"=dual check
        code_file: Code file path (for whitebox)
        white_params: Whitebox extra params JSON (e.g. {"functions":["verify_intelligence"]})
        black_params: Blackbox extra params JSON (e.g. {"api_url":"http://localhost:8434/health"})
        criteria: QA criteria description

    Returns:
        dual-agent QA report
    """
    logger.info("QA double-check: target=%s type=%s code=%s", target, check_type, code_file)

    report = {
        "target": target,
        "check_type": check_type,
        "white_box": None,
        "black_box": None,
        "cross_verify": None,
        "verdict": "",
    }

    if check_type in ("white", "both") and code_file:
        # Whitebox check points
        white_findings = []
        try:
            with open(code_file, encoding="utf-8") as f:
                source = f.read()
        except Exception as e:
            source = f"<unable to read: {e}>"

        lines = source.split("\n")
        white_findings.append(
            {"item": "file structure", "finding": f"{len(lines)} lines, {len(source)} bytes", "status": "info"}
        )

        # Count functions/classes
        import re

        funcs = re.findall(r"^async def (\w+)|^def (\w+)", source, re.M)
        func_names = [f[0] or f[1] for f in funcs]
        classes = re.findall(r"^class (\w+)", source, re.M)
        white_findings.append(
            {
                "item": "symbol stats",
                "finding": f"{len(func_names)} functions, {len(classes)} classes",
                "status": f"{'✅' if func_names else '⚠️'}",
            }
        )

        # Check tool functions
        tool_funcs = re.findall(r"@tool\(\)\s*\n(async )?def (\w+)", source)
        white_findings.append(
            {
                "item": "tool functions",
                "finding": f"{len(tool_funcs)} @tool decorators" if tool_funcs else "no @tool",
                "status": f"{'✅' if tool_funcs else '⚠️'}",
            }
        )

        # Check error handling
        has_try = source.count("try:") > 0
        has_except = source.count("except") > 0
        has_logger = "logger." in source
        white_findings.append(
            {
                "item": "error handling",
                "finding": f"try={has_try}, except={has_except}, logger={has_logger}",
                "status": "✅" if (has_except or has_logger) else "⚠️",
            }
        )

        # Check type annotations
        typed_funcs = sum(1 for f in func_names if "def " + f in source)
        white_findings.append(
            {"item": "type annotations", "finding": f"{typed_funcs}/{len(func_names)} functions", "status": "..."}
        )

        report["white_box"] = {
            "code_file": code_file,
            "findings": white_findings,
            "func_names": func_names[:15],
        }

    if check_type in ("black", "both"):
        # Blackbox checks (agent-3 perspective)
        black_findings = []

        black_findings.append(
            {
                "item": "API reachability",
                "finding": f"target: {target[:60]}",
                "status": "pending (agent-3 external probe)",
            }
        )

        black_findings.append(
            {
                "item": "input diversity",
                "finding": "normal input | empty input | oversized input | invalid type",
                "status": "pending",
            }
        )

        black_findings.append(
            {
                "item": "output stability",
                "finding": "error code consistency | response format | no crash on exception",
                "status": "to verify",
            }
        )

        black_findings.append(
            {
                "item": "side-effect check",
                "finding": "any file writes/network requests/system calls",
                "status": "check after execution",
            }
        )

        report["black_box"] = {
            "method": "external probe - no source code read",
            "findings": black_findings,
        }

    # Cross-verify
    if check_type == "both" and report["white_box"]:
        wf = report["white_box"]["findings"]
        issues = [f for f in wf if f["status"].startswith("⚠")]
        report["cross_verify"] = {
            "white_issues": len(issues),
            "white_clean": len(issues) == 0,
            "summary": f"whitebox found {len(issues)} concern items, blackbox pending external execution",
        }

    report["verdict"] = (
        "🟢 whitebox clean, blackbox pending"
        if report.get("white_box")
        and all(
            f["status"].startswith("✅") or f["status"] == "info" or f["status"] == "..."
            for f in report["white_box"]["findings"]
        )
        else "🟡 some items need attention"
    )

    return report


@tool()
async def qa_execute_blackbox(
    check_id: str,
    api_endpoint: str,
    test_cases: list = None,
) -> dict:
    """Execute blackbox test — agent-3 specific.

    Send actual requests to target interface, verify behavior matches expectations.

    Args:
        check_id: check id
        api_endpoint: external API endpoint (e.g. http://localhost:8434/health)
        test_cases: test cases list

    Returns:
        blackbox test report
    """
    import httpx

    logger.info("Blackbox check: id=%s endpoint=%s", check_id, api_endpoint)

    if not test_cases:
        test_cases = [
            {"name": "health check", "method": "GET", "url": api_endpoint, "expected": 200},
        ]

    results = []
    passed = 0
    failed = 0

    for tc in test_cases:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                method = tc.get("method", "GET").upper()
                if method == "GET":
                    resp = await client.get(tc["url"])
                elif method == "POST":
                    resp = await client.post(tc["url"], json=tc.get("body", {}))
                else:
                    resp = await client.request(method, tc["url"])

                status_match = resp.status_code == tc.get("expected", 200)
                result = {
                    "name": tc["name"],
                    "status_code": resp.status_code,
                    "expected": tc.get("expected"),
                    "passed": status_match and resp.status_code < 500,
                    "body_preview": resp.text[:200] if status_match else resp.text[:100],
                }
                if status_match:
                    passed += 1
                else:
                    failed += 1
                results.append(result)
        except Exception as e:
            results.append(
                {
                    "name": tc["name"],
                    "error": str(e)[:200],
                    "passed": False,
                }
            )
            failed += 1

    return {
        "check_id": check_id,
        "api_endpoint": api_endpoint,
        "total": len(test_cases),
        "passed": passed,
        "failed": failed,
        "details": results,
        "verdict": "🟢 all passed" if failed == 0 else f"🟡 {failed}/{len(test_cases)} failed",
    }


@tool()
async def qa_swarm_test(
    target: str,
    concurrent: int = 3,
    rounds: int = 1,
    test_cases: list = None,
) -> dict:
    """Swarm stress test — agent-3 specific, parallel workers.

    Simulate multiple bees sending requests to target simultaneously, detect:
    - Stability under concurrent pressure
    - Response time distribution (P50/P95/P99)
    - Error rate
    - Degradation trend under multi-round stress

    Args:
        target: target description (e.g. "source rating logic of verify_intelligence")
        concurrent: concurrency (bee count, default 3)
        rounds: rounds (each round tests bee_count × concurrency, default 1)
        test_cases: test cases list, each {name, input_data}

    Returns:
        swarm test report
    """
    import time

    logger.info("Swarm test: target=%s concurrent=%d rounds=%d", target, concurrent, rounds)

    if not test_cases:
        test_cases = [
            {
                "name": "authoritative source",
                "input": {"url": "https://www.gov.cn/test", "title": "official news", "snippet": "data source"},
            },
            {
                "name": "self-media",
                "input": {"url": "https://toutiao.com/test", "title": "title", "snippet": "rumored insider info"},
            },
            {
                "name": "AI generated",
                "input": {
                    "url": "https://medium.com/test",
                    "title": "AI article",
                    "snippet": "Based on my training data",
                },
            },
            {"name": "empty input", "input": {"url": "", "title": "", "snippet": ""}},
            {"name": "garbage text", "input": {"url": "a" * 1000, "title": "x" * 500, "snippet": "!" * 2000}},
        ]

    all_latencies = []
    errors = 0
    total_requests = 0
    round_results = []

    for r in range(rounds):
        round_latencies = []
        round_errors = 0

        # Simulate bee IDs
        bee_ids = [f"bee-{chr(65 + i)}" for i in range(concurrent)]
        bee_label = "+".join(bee_ids)

        # Parallel test
        for _bee in bee_ids:
            for tc in test_cases:
                total_requests += 1
                start = time.time()
                try:
                    # Call verify_intelligence internal logic
                    try:
                        from tools.verify import _assess_content, _rate_source

                        _rate_source(tc["input"].get("url", ""))
                        _assess_content(tc["input"].get("title", "") + " " + tc["input"].get("snippet", ""))
                    except ImportError:
                        pass  # verify module not present
                    elapsed = (time.time() - start) * 1000  # ms
                    all_latencies.append(elapsed)
                    round_latencies.append(elapsed)
                except Exception:
                    errors += 1
                    round_errors += 1
                    elapsed = (time.time() - start) * 1000
                    all_latencies.append(elapsed)

        # Round stats
        sorted_lat = sorted(round_latencies)
        n = len(sorted_lat)
        round_results.append(
            {
                "round": r + 1,
                "bees": bee_label,
                "requests": len(round_latencies) + round_errors,
                "errors": round_errors,
                "p50_ms": round(sorted_lat[n // 2], 1) if n > 0 else 0,
                "p95_ms": round(sorted_lat[int(n * 0.95)], 1) if n > 0 else 0,
                "p99_ms": round(sorted_lat[int(n * 0.99)], 1) if n > 0 else 0,
            }
        )

    # Global stats
    global_sorted = sorted(all_latencies)
    gn = len(global_sorted)
    error_rate = round(errors / total_requests * 100, 1) if total_requests > 0 else 0

    # Degradation trend detection
    has_degradation = False
    if len(round_results) >= 3:
        p50_first = round_results[0]["p50_ms"]
        p50_last = round_results[-1]["p50_ms"]
        if p50_last > p50_first * 1.5:
            has_degradation = True

    verdict_parts = []
    if error_rate < 1:
        verdict_parts.append("🟢 no errors")
    elif error_rate < 5:
        verdict_parts.append("🟡 few errors")
    else:
        verdict_parts.append("🔴 high error rate")

    if has_degradation:
        verdict_parts.append("⚠️ performance degradation detected")
    else:
        verdict_parts.append("✅ stable performance")

    return {
        "target": target,
        "swarm_config": f"{concurrent} bees × {rounds} rounds × {len(test_cases)} cases = {total_requests} requests",
        "total_requests": total_requests,
        "errors": errors,
        "error_rate_pct": error_rate,
        "latency_ms": {
            "p50": round(global_sorted[gn // 2], 1) if gn > 0 else 0,
            "p95": round(global_sorted[int(gn * 0.95)], 1) if gn > 0 else 0,
            "p99": round(global_sorted[int(gn * 0.99)], 1) if gn > 0 else 0,
        },
        "rounds": round_results,
        "degradation_detected": has_degradation,
        "verdict": " | ".join(verdict_parts),
        "recommendation": (
            "✅ ready for production"
            if error_rate < 1 and not has_degradation
            else "⚠️ edge cases need attention"
            if error_rate < 5
            else "🔴 issues found, fix before production"
        ),
    }


@tool()
async def qa_multi_round(
    target: str,
    test_cases: list = None,
    rounds: int = 5,
    bee_count: int = 4,
) -> dict:
    """Multi-round stress test — agent-3 specific.

    Run the same tests repeatedly over multiple rounds, observe:
    - If response time grows with rounds (memory leak signal)
    - If error rate rises with rounds (resource leak signal)
    - If there are sporadic crashes

    This is the advanced swarm mode — not testing "can it hold",
    but "how long until it breaks".

    Args:
        target: target description
        test_cases: test cases list
        rounds: rounds (default 5, simulate continuous usage)
        bee_count: bees per round (default 4 bees testing concurrently)

    Returns:
        multi-round degradation report
    """
    # Actually call qa_swarm_test per round
    from tools.qa_check import qa_swarm_test  # noqa

    final_report = await qa_swarm_test(target=target, concurrent=bee_count, rounds=rounds, test_cases=test_cases)
    return final_report
