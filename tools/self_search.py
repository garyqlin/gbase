# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/self_search.py

搜自己工具：从经验/知识库中搜索与用户问题相关的记录。
使用 LLM 做语义匹配，零额外依赖。

流程：
1. 从 storage 读取所有经验（最近 limit 条）
2. 把问题 + 经验摘要发给 LLM → LLM 判断哪些最相关
3. 返回匹配的经验（最多 5 条）
"""

import contextlib
import logging

from lib.toolkit import get_global, tool

logger = logging.getLogger(__name__)

_SEARCH_LIMIT = 50  # 保留最近 50 条经验用于搜索


@tool()
async def search_self(question: str) -> dict:
    """从你自己的经验/知识库中搜索相关信息。

    当你需要回忆过去对话中出现的知识点、技巧、配置或决策时，
    调用此工具来搜索自己的记忆。特别适合：
    - 用户问到与技术配置、历史决策、常用设置相关的问题
    - 用户问了但你感觉答案好像在之前对话里出现过
    - 用户用了自然语言描述（不是精确关键词）

    Args:
        question: 用户的问题或关键词
    """
    exp_engine = get_global("experience_engine")
    client = get_global("llm_client")
    model = get_global("llm_model") or "deepseek-chat"

    if not exp_engine or not client:
        return {"result": "记忆系统尚未就绪。"}

    # 读取最近的记录（含 injected=True 的预注经验）
    exps = exp_engine.storage.read_recent("experience", limit=_SEARCH_LIMIT)

    if not exps:
        return {"result": "目前还没有存储任何经验。"}

    # 构造给 LLM 的搜索请求
    candidates = []
    for i, exp in enumerate(exps):
        s = exp.get("summary", "") or ""
        c = exp.get("confidence", "low")
        if s.strip():
            candidates.append(f"{i}. [{c}] {s}")

    if not candidates:
        return {"result": "经验库存在但没有可用的摘要内容。"}

    search_prompt = f"""你是 Opprime 的记忆搜索系统。

用户刚才问了一个问题，你需要从已有的经验库里找出最相关的 1-3 条。
请注意：经验是之前对话里学到的知识点，不是用户现在问的内容。

用户问题：「{question}」

已有经验（共 {len(candidates)} 条）：
{chr(10).join(candidates)}

请返回最相关的经验编号（从 0 开始），每行一个编号，只输出编号。如果不相关则返回空。"""

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": search_prompt}],
            temperature=0.1,
            max_tokens=200,
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("搜自己 LLM 调用失败: %s", e)
        return {"result": "搜索失败。"}

    # 解析编号
    matched = []
    lines = text.strip().split("\n")
    for line in lines:
        try:
            idx = int(line.strip())
            if 0 <= idx < len(exps):
                matched.append(exps[idx])
        except (ValueError, IndexError):
            pass

    if not matched:
        return {"result": f"没有找到与「{question}」相关的经验。"}

    # 记录一次命中
    for m in matched:
        rid = m.get("id") or m.get("rowid")
        if rid:
            with contextlib.suppress(Exception):
                exp_engine.storage.record_hit(rid)

    results = []
    for m in matched[:3]:
        s = m.get("summary", "")
        c = m.get("confidence", "low")
        results.append(f"[{c}] {s}")

    return {
        "result": "找到以下相关经验：\n" + "\n".join(results),
        "count": len(matched),
    }
