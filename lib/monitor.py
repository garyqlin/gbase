"""自建无依赖轻量监控 /metrics 端点。

设计原则：
- 零依赖（只用标准库）
- asyncio 单线程安全（无锁）
- 每个进程独立实例，聚合靠外部轮询
- 8 个核心指标，不多不少
"""

import time
from collections import deque

# ── 采样窗口: 保留最近 1000 次请求的耗时（~16KB）──
_MAX_SAMPLES = 1000


class _Metrics:
    """单例监控收集器。每个进程一个实例。"""

    def __init__(self):
        self._start = time.time()
        self._requests = 0  # int counter
        self._errors = 0  # int counter
        self._latencies = deque(maxlen=_MAX_SAMPLES)  # ms

    # ── public API ──

    def inc_request(self):
        self._requests += 1

    def inc_error(self):
        self._errors += 1

    def record_latency(self, ms: float):
        self._latencies.append(ms)
        # 注意：不重复计 requests_total，调用方自己 inc （/ask 端点已在 try 前 inc）

    # ── snapshot ──

    def snapshot(self) -> dict:
        uptime = int(time.time() - self._start)
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        mem = _rss_mb()
        lat_arr = list(self._latencies)
        p50, p95, p99 = _percentiles(lat_arr) if lat_arr else (0, 0, 0)
        return {
            "timestamp": now,
            "uptime_seconds": uptime,
            "requests_total": self._requests,
            "error_total": self._errors,
            "error_rate_pct": round(self._errors / max(self._requests, 1) * 100, 2),
            "latency_ms": {
                "p50": p50,
                "p95": p95,
                "p99": p99,
                "samples": len(lat_arr),
            },
            "memory_mb": mem,
        }


# ── 全局实例 ──
_metrics = _Metrics()


def get_metrics() -> _Metrics:
    return _metrics


def metrics_dict() -> dict:
    return _metrics.snapshot()


# ── helper ──


def _rss_mb() -> int:
    """当前进程 RSS 内存（MB），跨平台。"""
    import sys

    try:
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return round(rss / 1024 / 1024)  # macOS: bytes
        else:
            return round(rss / 1024)  # Linux: KB
    except Exception:
        return 0


def _percentiles(arr):
    """O(n log n) 计算 P50/P95/P99。arr 非空。"""
    s = sorted(arr)
    n = len(s)
    return (
        s[n * 50 // 100],
        s[n * 95 // 100],
        s[n * 99 // 100],
    )
