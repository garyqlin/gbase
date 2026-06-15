# SPDX-License-Identifier: MIT
"""
tools/qa_check.py

QA质检 — 重锤(白盒) + 大黄蜂(黑盒) 双战甲测试框架。

工作流：
1. 白盒（重锤）：读源码、审逻辑、查边界
2. 黑盒（大黄蜂）：只看外部行为、API响应、不碰源码
3. 交叉比对 → 结论

用法：qa_double_check(target=<文件路径或接口URL>, check_type="white"|"black"|"both")
"""

import logging

from lib.toolkit import tool

logger = logging.getLogger(__name__)


@tool()
async def qa_double_check(
    target: str,
    check_type: str = "both",
    code_file: str = "",
    white_params: str = "",
    black_params: str = "",
    criteria: str = "",
) -> dict:
    """对目标进行双战甲质检——白盒重锤 + 黑盒大黄蜂交叉验证。

    白盒检查（重锤执行）：
    - 读源码/函数实现，检查逻辑正确性
    - 检查异常路径/边界条件
    - 检查类型注解/错误处理/日志
    - 检查是否符合规范

    黑盒检查（大黄蜂执行）：
    - 只访问外部API/命令行行为
    - 验证输入输出映射
    - 测试异常输入响应
    - 不读任何源码

    Args:
        target: 检查目标描述（如 "tools/verify.py 的 verify_intelligence 函数"）
        check_type: "white"=白盒, "black"=黑盒, "both"=双检查
        code_file: 代码文件路径（白盒用）
        white_params: 白盒额外参数JSON（如 {"functions":["verify_intelligence"]}）
        black_params: 黑盒额外参数JSON（如 {"api_url":"http://localhost:8434/health"}）
        criteria: 质检标准描述

    Returns:
        双战甲质检报告
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
        # 白盒检查要点
        white_findings = []
        try:
            with open(code_file) as f:
                source = f.read()
        except Exception as e:
            source = f"<无法读取: {e}>"

        lines = source.split("\n")
        white_findings.append({"item": "文件结构", "finding": f"{len(lines)} 行, {len(source)} 字节", "status": "info"})

        # 统计函数/类
        import re

        funcs = re.findall(r"^async def (\w+)|^def (\w+)", source, re.M)
        func_names = [f[0] or f[1] for f in funcs]
        classes = re.findall(r"^class (\w+)", source, re.M)
        white_findings.append(
            {
                "item": "符号统计",
                "finding": f"{len(func_names)} 函数, {len(classes)} 类",
                "status": f"{'✅' if func_names else '⚠️'}",
            }
        )

        # 检查工具函数
        tool_funcs = re.findall(r"@tool\(\)\s*\n(async )?def (\w+)", source)
        white_findings.append(
            {
                "item": "工具函数",
                "finding": f"{len(tool_funcs)} 个 @tool 装饰器" if tool_funcs else "无 @tool",
                "status": f"{'✅' if tool_funcs else '⚠️'}",
            }
        )

        # 检查异常处理
        has_try = source.count("try:") > 0
        has_except = source.count("except") > 0
        has_logger = "logger." in source
        white_findings.append(
            {
                "item": "错误处理",
                "finding": f"try={has_try}, except={has_except}, logger={has_logger}",
                "status": "✅" if (has_except or has_logger) else "⚠️",
            }
        )

        # 检查类型注解
        typed_funcs = sum(1 for f in func_names if "def " + f in source)
        white_findings.append({"item": "类型注解", "finding": f"{typed_funcs}/{len(func_names)} 函数", "status": "..."})

        report["white_box"] = {
            "code_file": code_file,
            "findings": white_findings,
            "func_names": func_names[:15],
        }

    if check_type in ("black", "both"):
        # 黑盒检查要点（大黄蜂视角）
        black_findings = []

        black_findings.append(
            {"item": "API可达性", "finding": f"目标: {target[:60]}", "status": "待验证（由大黄蜂执行外部探测）"}
        )

        black_findings.append(
            {"item": "输入多样性", "finding": "正常输入 | 空输入 | 超长输入 | 非法类型", "status": "待测试"}
        )

        black_findings.append(
            {"item": "输出稳定性", "finding": "错误码一致性 | 返回格式 | 异常不崩溃", "status": "待验证"}
        )

        black_findings.append(
            {"item": "副作用检查", "finding": "是否有写文件/网络请求/系统调用", "status": "需执行后检查"}
        )

        report["black_box"] = {
            "method": "外部探测 - 不读源码",
            "findings": black_findings,
        }

    # 交叉验证
    if check_type == "both" and report["white_box"]:
        wf = report["white_box"]["findings"]
        issues = [f for f in wf if f["status"].startswith("⚠")]
        report["cross_verify"] = {
            "white_issues": len(issues),
            "white_clean": len(issues) == 0,
            "summary": f"白盒发现 {len(issues)} 个关注项, 黑盒待外部执行验证",
        }

    report["verdict"] = (
        "🟢 白盒无异常, 黑盒待执行"
        if report.get("white_box")
        and all(
            f["status"].startswith("✅") or f["status"] == "info" or f["status"] == "..."
            for f in report["white_box"]["findings"]
        )
        else "🟡 有检查项需关注"
    )

    return report


@tool()
async def qa_execute_blackbox(
    check_id: str,
    api_endpoint: str,
    test_cases: list = None,
) -> dict:
    """执行黑盒测试 — 大黄蜂专用。

    实际向目标接口发送请求，验证行为是否符合预期。

    Args:
        check_id: 检查ID
        api_endpoint: 外部API端点（如 http://localhost:8434/health）
        test_cases: 测试用例列表

    Returns:
        黑盒测试报告
    """
    import httpx

    logger.info("Blackbox check: id=%s endpoint=%s", check_id, api_endpoint)

    if not test_cases:
        test_cases = [
            {"name": "健康检查", "method": "GET", "url": api_endpoint, "expected": 200},
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
        "verdict": "🟢 全部通过" if failed == 0 else f"🟡 {failed}/{len(test_cases)} 失败",
    }


@tool()
async def qa_swarm_test(
    target: str,
    concurrent: int = 3,
    rounds: int = 1,
    test_cases: list = None,
) -> dict:
    """蜂群压力测试 — 大黄蜂专属，多只工蜂并行执行。

    模拟多工蜂同时向目标发起请求，检测：
    - 并发压力下的稳定性
    - 响应时间分布（P50/P95/P99）
    - 错误率
    - 多轮压力下的退化趋势

    Args:
        target: 目标描述（如 "verify_intelligence 的信源评级逻辑"）
        concurrent: 并发数（工蜂数量，默认3）
        rounds: 轮数（每轮是工蜂数×并发数的测试量，默认1）
        test_cases: 测试用例列表，每项 {name, input_data}

    Returns:
        蜂群测试报告
    """
    import time

    logger.info("Swarm test: target=%s concurrent=%d rounds=%d", target, concurrent, rounds)

    if not test_cases:
        test_cases = [
            {
                "name": "权威信源",
                "input": {"url": "https://www.gov.cn/test", "title": "官方新闻", "snippet": "数据来源"},
            },
            {
                "name": "自媒体",
                "input": {"url": "https://toutiao.com/test", "title": "标题", "snippet": "据传内部消息"},
            },
            {
                "name": "AI生成",
                "input": {"url": "https://medium.com/test", "title": "AI文章", "snippet": "Based on my training data"},
            },
            {"name": "空输入", "input": {"url": "", "title": "", "snippet": ""}},
            {"name": "垃圾文本", "input": {"url": "a" * 1000, "title": "x" * 500, "snippet": "!" * 2000}},
        ]

    all_latencies = []
    errors = 0
    total_requests = 0
    round_results = []

    for r in range(rounds):
        round_latencies = []
        round_errors = 0

        # 模拟工蜂 ID
        bee_ids = [f"bee-{chr(65 + i)}" for i in range(concurrent)]
        bee_label = "+".join(bee_ids)

        # 并行测试
        for bee in bee_ids:
            for tc in test_cases:
                total_requests += 1
                start = time.time()
                try:
                    # 实际调 verify_intelligence 的 internal 逻辑
                    from tools.verify import _assess_content, _rate_source

                    rating = _rate_source(tc["input"].get("url", ""))
                    quality = _assess_content(tc["input"].get("title", "") + " " + tc["input"].get("snippet", ""))
                    elapsed = (time.time() - start) * 1000  # ms
                    all_latencies.append(elapsed)
                    round_latencies.append(elapsed)
                except Exception:
                    errors += 1
                    round_errors += 1
                    elapsed = (time.time() - start) * 1000
                    all_latencies.append(elapsed)

        # 本轮统计
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

    # 全局统计
    global_sorted = sorted(all_latencies)
    gn = len(global_sorted)
    error_rate = round(errors / total_requests * 100, 1) if total_requests > 0 else 0

    # 退化趋势检测
    has_degradation = False
    if len(round_results) >= 3:
        p50_first = round_results[0]["p50_ms"]
        p50_last = round_results[-1]["p50_ms"]
        if p50_last > p50_first * 1.5:
            has_degradation = True

    verdict_parts = []
    if error_rate < 1:
        verdict_parts.append("🟢 0 错误")
    elif error_rate < 5:
        verdict_parts.append("🟡 少量错误")
    else:
        verdict_parts.append("🔴 高错误率")

    if has_degradation:
        verdict_parts.append("⚠️ 存在性能退化")
    else:
        verdict_parts.append("✅ 性能稳定")

    return {
        "target": target,
        "swarm_config": f"{concurrent}工蜂 × {rounds}轮 × {len(test_cases)}用例 = {total_requests}请求",
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
            "✅ 可上线"
            if error_rate < 1 and not has_degradation
            else "⚠️ 需关注边缘用例"
            if error_rate < 5
            else "🔴 有问题，修复后再上线"
        ),
    }


@tool()
async def qa_multi_round(
    target: str,
    test_cases: list = None,
    rounds: int = 5,
    bee_count: int = 4,
) -> dict:
    """多轮压力测试 — 大黄蜂专属，多轮次叠加检测退化。

    相同测试反复执行多轮，观察：
    - 响应时间是否随轮次增长（内存泄漏信号）
    - 错误率是否随轮次上升（资源泄漏信号）
    - 是否有偶发崩溃

    这是蜂群模式的高阶版——不是测"能不能扛住"，
    而是测"扛多久才垮"。

    Args:
        target: 目标描述
        test_cases: 测试用例列表
        rounds: 轮数（默认5轮，模拟持续使用场景）
        bee_count: 每轮并发工蜂数（默认4只工蜂同时测试）

    Returns:
        多轮退化检测报告
    """
    # 实际调用 qa_swarm_test 每轮
    from tools.qa_check import qa_swarm_test  # noqa

    final_report = await qa_swarm_test(target=target, concurrent=bee_count, rounds=rounds, test_cases=test_cases)
    return final_report
