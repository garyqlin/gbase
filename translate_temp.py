import re
import sys

def translate_chinese_comment(line):
    # 翻译中文注释映射
    translations = {
        "内核循环:调 LLM → 工具执行 → 再调 → 回复。": "Kernel loop: LLM invocation → tool execution → recall → response.",
        "三层架构的 Layer 2:": "Layer 2 of the three-tier architecture:",
        "只做一件事:LLM 调用 + tool_call 执行循环": "- Single responsibility: LLM call + tool_call execution loop",
        "不做:记忆注入、经验存储、侦察兵、认知检测": "- Out of scope: memory injection, experience storage, scout, cognitive detection",
        "最多 5 层工具调用深度": "- Maximum 5 levels of tool call depth",
        "GMem 集成钩子": "GMem Integration Hook",
        "GMem 是 GBase 自带的记忆系统，通过升级 mirror / toolkit / experience 三个子模块实现": "# GMem is GBase's built-in memory system, implemented by upgrading three submodules: mirror / toolkit / experience",
        "不依赖外部服务，不引入新依赖": "# No external service dependencies, no new dependencies introduced",
        "P0: KV Cache 准备 → hot_pattern_observe() 跟踪高频模式": "# P0: KV Cache preparation → hot_pattern_observe() tracks high-frequency patterns",
        "P1: 异步记忆调度 → create_task 非阻塞经验提取 + async_record": "# P1: Asynchronous memory scheduling → create_task non-blocking experience extraction + async_record",
        "P2: 经验标准化 → export/import 版本校验 + 筛选": "# P2: Experience standardization → export/import version verification + filtering",
        "P3: 实体关系图 → gmem_relations 表 + predict() 多跳扩展": "# P3: Entity relationship graph → gmem_relations table + predict() multi-hop extension",
        "GMem P1: 异步后台任务（不阻塞主线程）": "GMem P1: Asynchronous background tasks (non-blocking main thread)",
        "后台记录 mirror 记忆。空实现——记录决策完全交给 agent 自己决定。": "Record mirror memory in background. Empty implementation - recording decision is entirely delegated to the agent.",
        "自动笔记触发器：检测到深度工作时后台写入 L4 笔记。": "Automatic note trigger: writes L4 note in background when deep work is detected.",
        "触发条件（需同时满足）：": "Trigger conditions (all must be met):",
        "工具调用 >= 5 次（说明做了实质性工作）": "- Tool calls >= 5 times (indicates substantial work completed)",
        "IP 回复长度 > 300 字（说明内容充实）": "- IP response length > 300 characters (indicates rich content)",
        "不是简单回复（不含纯问答特征）": "- Not a simple response (no pure Q&A characteristics)",
        "这样做的理由：": "Rationale:",
        "高达（Gundam）的任务一轮结束没有自动触发 note_write": "- Gundam tasks do not automatically trigger note_write after round completion",
        "热记忆 mirror 会衰减，深度调研/设计的内容重启后就只剩碎片": "- Hot memory mirror decays, deep research/design content leaves only fragments after restart",
        "L4 笔记不衰减，是唯一可靠的持久层": "- L4 notes do not decay, it is the only reliable persistence layer",
        "与其依赖 LLM 记得主动 call note_write，不如系统自动兜底": "- Instead of relying on LLM to remember to call note_write actively, the system provides automatic fallback",
        "但 LLM 主动写的（含 judgment 的）远优于自动的，所以 auto 只作为兜底，不替代手动": "- However, notes actively written by LLM (with judgment) are far superior to automatic ones, so auto only acts as fallback, not replacement for manual notes",
        "条件 1：工具调用量达标": "Condition 1: Tool call count meets threshold",
        "条件 2：回复内容充实": "Condition 2: Response content is substantial",
        "条件 3：不是纯简单问答（探测）": "Condition 3: Not pure simple Q&A (probe)",
        "自动生成笔记标题": "Auto generate note title",
        "智能估算笔记内容（防止太长超过合理范围）": "Smart estimate note content (prevent exceeding reasonable length)",
        "从工具调用数推测任务深度": "Estimate task depth from tool call count",
        "系统自动存档": "System Auto Archive",
        "本次任务": "Current Task",
        "产出摘要": "Output Summary",
        "GMem P0: 深度搜索后自动保存结果摘要到 mirror。": "GMem P0: Automatically save result summary to mirror after deep search.",
        "从 kernel 文件层级推算搜索深度": "Estimate search depth from kernel file hierarchy",
        "GMem P0 搜索摘要记录失败:": "GMem P0 search summary recording failed:",
        "后台提取经验（含反脆弱: 失败经验）。": "Extract experience in background (includes anti-fragility: failure experience).",
        "异步经验提取异常:": "Asynchronous experience extraction exception:",
        "判断工具返回的错误是否值得自动重试。": "Determine if tool returned error is worth automatic retry.",
        "网络超时、连接失败等临时性错误可重试；": "Temporary errors such as network timeout, connection failure are retryable;",
        "参数错误、权限不足等不可重试。": "Parameter errors, insufficient permissions, etc. are not retryable.",
        "从 config.yaml 读取，不存在则默认 15": "Read from config.yaml, default 15 if not exists",
        "可": "Configurable"
    }
    
    for zh, en in translations.items():
        if zh in line:
            # 保留注释符号
            if line.strip().startswith('#'):
                line = line.replace(zh, en)
            else:
                # 处理docstring
                line = line.replace(zh, en)
    return line

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    processed = []
    in_docstring = False
    for line in lines:
        # 处理docstring
        if '"""' in line:
            in_docstring = not in_docstring
        # 处理注释和docstring中的中文
        if '#' in line or in_docstring or 'logger.' in line:
            line = translate_chinese_comment(line)
        processed.append(line)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(processed)
    
    print(f"Processed {filepath} successfully")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python translate_temp.py <filepath>")
        sys.exit(1)
    process_file(sys.argv[1])