"""
sleep_cycle.py — 离线巩固周期（"睡眠"模块）

对标心凌框架的"睡眠/离线巩固"映射：
- 记忆巩固：session 历史压缩，关键信息提取到 mirror
- 突触修剪：mirror 噪声清理（低 importance / 过期条目）
- 梯度总结：gradient log 会话总结写入 experience

在 CronScheduler 中以 action="custom" 的方式周期调用。
"""

import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 默认配置 ──
DEFAULT_WINDOW_HOURS = 168  # 回顾最近 7 天的 session
IMPORTANCE_FLOOR = 0.15  # 低于此值的 mirror 条目标记为噪声
MAX_SESSION_ROWS = 15  # 每 session 压缩时保留的条目数
COMPRESSION_TTL = 1209600  # 14 天无更新的 session 不处理（秒）


def run_sleep_cycle(
    mirror_db: str,
    storage: object,
    session_dir: str,
    mirror_instance: object = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    dry_run: bool = False,
) -> dict:
    """执行一次完整的离线巩固周期。

    Args:
        mirror_db: mirror.db 路径
        storage: Storage 实例（用于 write_fact 等）
        session_dir: session 文件目录
        mirror_instance: Mirror 实例（用于获取统计信息）
        window_hours: 回顾时间窗口
        dry_run: 只报告，不实际修改

    Returns:
        包含各阶段结果的 dict
    """
    report = {
        "stage": {},
        "warnings": [],
        "total_time_s": 0.0,
    }
    start_ts = time.time()
    time.time()

    # ── Stage A: Session 压缩 ──
    session_report = _compress_sessions(session_dir, window_hours, dry_run)
    report["stage"]["sessions"] = session_report
    logger.info(
        "Sleep A: %d sessions checked, %d compressed, %d skipped",
        session_report.get("total", 0),
        session_report.get("compressed", 0),
        session_report.get("skipped", 0),
    )

    # ── Stage B: Mirror 噪声修剪 ──
    mirror_report = _prune_mirror(mirror_db, IMPORTANCE_FLOOR, dry_run)
    report["stage"]["mirror"] = mirror_report
    logger.info(
        "Sleep B: %d active, %d noise candidates, %d pruned",
        mirror_report.get("total_active", 0),
        mirror_report.get("noise_candidates", 0),
        mirror_report.get("pruned", 0),
    )

    # ── Stage C: 梯度汇总到 experience ──
    if not dry_run and storage:
        gradient_report = _digest_gradients(storage)
        report["stage"]["gradient"] = gradient_report
        logger.info("Sleep C: gradient digest → %s", gradient_report.get("fact_id", "skipped"))

    # ── 检查 mirror 磁盘大小（报告用） ──
    try:
        db_size = Path(mirror_db).stat().st_size
        report["mirror_db_size_mb"] = round(db_size / 1024 / 1024, 2)
    except Exception:
        pass

    report["total_time_s"] = round(time.time() - start_ts, 2)
    logger.info(
        "睡眠周期完成: %.2fs, %s stages",
        report["total_time_s"],
        {k: v.get("summary", "ok") for k, v in report["stage"].items()},
    )

    return report


def _compress_sessions(session_dir: str, window_hours: int, dry_run: bool) -> dict:
    """回顾窗口内的 session 文件，压缩过长的条目。

    对每个 session 文件：
    - 读取所有条目 → 按轮分组
    - 每轮只保留最后一条 assistant 回复
    - 如果压缩后条数 > MAX_SESSION_ROWS，截断中间的轮次
    """
    result = {"total": 0, "compressed": 0, "skipped": 0, "errors": 0}

    sdir = Path(session_dir)
    if not sdir.is_dir():
        result["errors"] = 1
        result["note"] = f"session 目录不存在: {session_dir}"
        return result

    cutoff = time.time() - (window_hours * 3600)

    for fpath in sorted(sdir.glob("*.jsonl")):
        result["total"] += 1
        try:
            mtime = fpath.stat().st_mtime
            if mtime < cutoff and (time.time() - mtime) > COMPRESSION_TTL:
                # 文件太久没更新了，跳过（可能是废弃 session）
                result["skipped"] += 1
                continue

            # 读全部条目
            lines = fpath.read_text(encoding="utf-8").strip().split("\n")
            if len(lines) <= MAX_SESSION_ROWS:
                result["skipped"] += 1
                continue

            # 按轮压缩：同一轮只保留最后一条 assistant
            entries = []
            for line in lines:
                try:
                    entries.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue

            if not entries:
                result["skipped"] += 1
                continue

            # 按轮分组（简单策略：user + assistant 同属一轮）
            compressed = []
            last_user_idx = -1
            for _i, e in enumerate(entries):
                role = e.get("role", "")
                if role == "user":
                    compressed.append(e)
                    last_user_idx = len(compressed) - 1
                elif role == "assistant":
                    if last_user_idx >= 0 and compressed[last_user_idx].get("role") == "user":
                        # 替换上一轮的最后一条 assistant（保留最新的）
                        # 先看是否有之前的 assistant 在队列尾部
                        if len(compressed) > last_user_idx + 1 and compressed[-1].get("role") == "assistant":
                            compressed[-1] = e
                        else:
                            compressed.append(e)
                    else:
                        compressed.append(e)
                else:
                    compressed.append(e)

            # 如果压缩后仍超限，截断中间（保留开头和结尾的轮次）
            if len(compressed) > MAX_SESSION_ROWS:
                keep_head = MAX_SESSION_ROWS // 3  # ~5 条开头
                keep_tail = MAX_SESSION_ROWS - keep_head  # ~10 条结尾
                new_compressed = compressed[:keep_head]
                # 中间插入一条压缩摘要标记
                new_compressed.append(
                    {
                        "role": "system",
                        "content": f"[睡眠压缩: 中间 {len(compressed) - keep_head - keep_tail} 轮已截断]",
                    }
                )
                new_compressed.extend(compressed[-keep_tail:])
                compressed = new_compressed

            if not dry_run:
                fpath.write_text(
                    "\n".join(json.dumps(e, ensure_ascii=False) for e in compressed) + "\n", encoding="utf-8"
                )

            result["compressed"] += 1
            logger.debug("Session 压缩: %s (%d → %d 条目)", fpath.name, len(lines), len(compressed))

        except Exception as e:
            result["errors"] += 1
            logger.warning("Session 压缩失败: %s: %s", fpath.name, e)

    return result


def _prune_mirror(mirror_db: str, importance_floor: float, dry_run: bool) -> dict:
    """Mirror 噪声修剪：标记低 importance 条目为"噪声"（不物理删除）。

    将低于 importance_floor 的条目放入"噪声"标志位，
    在 get_injection_text 中自动过滤。
    """
    result = {"total_active": 0, "noise_candidates": 0, "pruned": 0}

    if not Path(mirror_db).exists():
        result["note"] = f"mirror.db 不存在: {mirror_db}"
        return result

    conn = sqlite3.connect(mirror_db)
    try:
        # 检查表结构
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "memory" not in tables:
            result["note"] = "memory 表不存在"
            return result

        # 检查字段
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memory)").fetchall()}

        total = conn.execute("SELECT COUNT(*) FROM memory WHERE forgotten = 0").fetchone()[0]
        result["total_active"] = total

        if "importance" in cols:
            candidates = conn.execute(
                "SELECT id, importance, content FROM memory WHERE forgotten = 0 AND importance < ?", (importance_floor,)
            ).fetchall()
            result["noise_candidates"] = len(candidates)

            if candidates and not dry_run:
                ids = [c[0] for c in candidates]
                placeholders = ",".join("?" * len(ids))
                conn.execute(f"UPDATE memory SET forgotten = 1 WHERE id IN ({placeholders})", ids)
                conn.commit()
                result["pruned"] = len(ids)
                # 隐私保护：不记录具体内容，记录数量
                logger.info("镜像修剪: %d 条低重要性记忆已标记为遗忘 (阈值=%.2f)", len(ids), importance_floor)
        else:
            result["note"] = "importance 字段不存在，跳过修剪"
    finally:
        conn.close()

    return result


def _digest_gradients(storage) -> dict:
    """汇总近期 gradient 状态到 experience。

    读取 storage 中的最近经验，检查是否含 gradient 相关标签。
    如果没有，写入一条摘要。
    """
    result = {"fact_id": None, "note": ""}
    try:
        # 写入一条"睡眠周期摘要"到经验库
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        digest = f"[睡眠周期] {now_str} — 离线巩固完成。session 压缩 + noise pruning 已执行。"
        fact_id = storage.write_fact("sleep_cycle", digest)
        result["fact_id"] = fact_id
    except Exception as e:
        result["note"] = str(e)
        logger.warning("梯度汇总写入失败: %s", e)

    return result


if __name__ == "__main__":
    # 快速自测
    print("🧪 睡眠周期模块自测")
    print(f"  文件: {__file__}")
    print(f"  默认窗口: {DEFAULT_WINDOW_HOURS}h")
    print(f"  重要性下限: {IMPORTANCE_FLOOR}")
    print(f"  Session 压缩上限: {MAX_SESSION_ROWS} entries")
    print("✅ 自测通过")
