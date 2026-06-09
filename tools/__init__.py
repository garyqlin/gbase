# SPDX-License-Identifier: MIT
"""
gbase/tools/__init__.py

Tool set and platform route registration.
Each identity can override in their own configuration.
"""

from lib.toolkit import register_platform_map, register_toolset

# Tools auto-registered via @tool decorator
# YF-image-base and scene skills
from . import (  # noqa: F401
    anchor_keeper,  # noqa: F401  # noqa: F401
    archive_search,  # noqa: F401
    commit_helper,  # noqa: F401
    crypto_helper,  # noqa: F401
    cua_tools,  # noqa: F401
    data_seeder,  # noqa: F401
    docx_gen,  # noqa: F401
    file_checker,  # noqa: F401
    glink_projects,  # noqa: F401
    honeycomb_search,  # noqa: F401
    jwt_helper,  # noqa: F401
    laser_doc,  # noqa: F401
    log_profiler,  # noqa: F401
    memory_profiler,  # noqa: F401
    mock_server,  # noqa: F401
    network_tools,  # noqa: F401
    pdf_gen,  # noqa: F401
    pptx_gen,  # noqa: F401
    prompt_helper,  # noqa: F401
    query_profiler,  # noqa: F401
    remember_info,  # noqa: F401
    schema_tools,  # noqa: F401
    security_watch,  # noqa: F401
    test_generator,  # noqa: F401
    xlsx_gen,  # noqa: F401
    yf_image_tools,  # noqa: F401
)


def register_default():
    _register_new_skills()


def _register_new_skills():
    """Register new skills to corresponding toolsets.
    Import triggers @tool decorator; here we only add keywords.
    """
    register_toolset(
        "test",
        ["测试", "生成测试", "单元测试", "写测试", "test", "autotest"],
        [
            "generate_tests",
            "generate_tests_batch",
        ],
    )
    register_toolset(
        "security",
        ["安全检查", "安全扫描", "漏洞", "密钥", "CVE", "安全", "security"],
        [
            "security_scan_directory",
        ],
    )
    register_toolset(
        "log_analysis",
        ["日志分析", "日志", "错误分析", "慢请求", "log", "分析日志"],
        [
            "analyze_log_file",
        ],
    )
    register_toolset(
        "database",
        ["数据库", "查询分析", "慢查询", "sql分析", "数据库优化", "query", "sql"],
        [
            "profile_database",
        ],
    )
    register_toolset(
        "seed",
        ["测试数据", "演示数据", "种子数据", "填充数据", "数据生成", "seed data"],
        [
            "seed_test_data",
        ],
    )
    register_toolset(
        "mock",
        ["mock", "mock api", "模拟接口", "mock服务器", "本地api", "api模拟"],
        [
            "start_mock_server",
            "stop_mock_server",
        ],
    )

    register_toolset("memory", ["内存", "内存泄漏", "OOM", "内存分析", "memory"], ["analyze_memory"])
    register_toolset("prompt", ["优化prompt", "prompt模板", "prompt管理", "prompt优化"], ["optimize_prompt"])
    register_toolset("network", ["网络", "端口", "ping", "dns", "网络诊断", "net", "connect"], ["check_network"])
    register_toolset("jwt", ["jwt", "token解码", "jwt验证", "token解析"], ["jwt_decode", "jwt_verify"])
    register_toolset("file_check", ["文件校验", "文件哈希", "完整性", "hash", "checksum"], ["file_hash", "file_verify"])
    register_toolset(
        "schema", ["schema验证", "格式检查", "json验证", "yaml验证", "数据校验"], ["validate_file", "infer_schema"]
    )
    register_toolset("crypto", ["加密", "密钥", "证书", "解密", "加解密", "密钥生成"], ["generate_key", "cert_info"])

    register_platform_map("cli", ["test", "security", "log_analysis", "commit", "database", "seed", "mock"])
    # feishu platform removed for release
    register_platform_map("api", ["test", "security", "log_analysis", "commit", "database", "seed", "mock"])

    register_toolset(
        "web",
        ["search", "find", "lookup", "query"],
        [
            "search_web",
            "fetch_page",
            "honeycomb_search",
        ],
    )

    register_toolset(
        "weather",
        ["天气", "温度", "下雨", "下雪", "风力"],
        [
            "get_weather",
        ],
    )

    register_toolset(
        "reminder",
        ["提醒", "闹钟", "定时", "记住", "几点", "多久"],
        [
            "cron_add",
            "cron_list",
            "cron_remove",
            "cron_toggle",
            "reminder_add",
            "reminder_list",
            "reminder_delete",
        ],
    )

    register_toolset(
        "self",
        ["经验", "总结", "过去", "记住", "记忆", "知识", "记得", "忘记"],
        [
            "search_self",
            "remember_fact",
            "search_knowledge",
            "forget_knowledge",
        ],
    )

    register_toolset(
        "read",
        ["*"],
        [
            "read_file",
            "write_file",
        ],
    )

    register_toolset(
        "cua",
        ["cua", "桌面操作", "屏幕操作", "浏览器操作", "点击", "截图"],
        [
            "cua_plan",
            "cua_execute",
        ],
    )
    register_toolset(
        "memory_mgmt",
        ["记忆加载", "记忆状态", "今日记忆", "昨日记忆", "memory load"],
        [
            "memory_load",
            "memory_status",
        ],
    )
    register_toolset(
        "safe_exec",
        ["安全执行", "运行命令", "shell命令", "执行命令", "exec safe"],
        [
            "exec_safe",
        ],
    )
    register_toolset(
        "manga",
        ["漫画视频", "转视频", "漫剧", "漫画转", "漫画动画", "manga"],
        [
            "manga_video_plan",
            "manga_video_estimate",
        ],
    )

    register_toolset(
        "exec",
        ["运行", "执行", "测试", "编译", "跑一下", "运行一下", "run", "execute"],
        [
            "exec_command",
        ],
    )

    register_toolset(
        "learn",
        ["学习", "学", "研究方向", "上课"],
        [
            "add_learn_topic",
            "list_learn_topics",
            "remove_learn_topic",
        ],
    )

    register_toolset(
        "path",
        ["路径", "我在哪", "目录", "dir", "where", "pwd", "位置"],
        [
            "my_current_path",
            "my_project_roots",
        ],
    )

    register_toolset(
        "brain",
        ["*"],
        [
            "search_knowledge",
        ],
    )

    register_toolset(
        "card",
        ["card", "send card", "notification"],
        [
            "send_card",
        ],
    )

    register_toolset(
        "mail",
        ["邮件", "邮箱", "mail", "email", "收件箱", "写信", "发信"],
        [
            "check_inbox",
            "send_mail",
            "list_all_mailboxes",
        ],
    )

    # ── Code Index Toolset ──
    register_toolset(
        "code",
        [
            "代码",
            "编程",
            "函数",
            "类",
            "文件在哪",
            "实现",
            "改代码",
            "修bug",
            "写代码",
            "开发",
            "索引",
            "搜索代码",
        ],
        [
            "search_code",
            "build_code_index",
        ],
    )

    # ── Safe Write Toolset ──
    register_toolset(
        "safe_write",
        ["改文件", "写文件", "修改", "创建文件", "新建文件", "编辑", "写入"],
        [
            "write_file",
        ],
    )

    # ── Rollback Toolset (Phase 1: Brake) ──
    register_toolset(
        "rollback",
        [
            "回滚",
            "恢复",
            "备份",
            "撤销",
            "undo",
            "rollback",
            "还原",
            "退回去",
            "改错了",
            "恢复到",
            "检查点",
        ],
        [
            "rollback_list",
            "rollback_restore",
            "rollback_cleanup",
            "rollback_stats",
        ],
    )

    # ── Mirror Toolset ──
    register_toolset(
        "mirror",
        [
            "记住",
            "忘记",
            "鉴面",
            "经验",
            "教训",
            "原则",
            "学会",
            "学到",
            "记住这次",
            "下次注意",
        ],
        [
            "mirror_record",
            "mirror_verify",
            "mirror_review",
            "mirror_stats",
        ],
    )

    # ── Code Session Toolset ──
    register_toolset(
        "code_session",
        [
            "代码会话",
            "写代码",
            "编程会话",
            "coding",
            "不断",
            "持续",
            "一直改",
            "再改",
            "继续改",
        ],
        [
            "code_session",
            "search_code",
            "write_file",
        ],
    )

    # ── Document: Laser Documentation Authoring ──
    register_toolset(
        "doc",
        [
            "文档",
            "写文档",
            "README",
            "API文档",
            "架构说明",
            "开发文档",
            "部署文档",
            "文档模板",
            "生成文档",
            "doc",
            "documentation",
            "写说明",
            "word",
            "excel",
            "ppt",
            "pdf",
            "表格",
            "演示",
        ],
        [
            "scan_project",
            "author_doc",
            "author_test_plan",
            "gen_docx",
            "gen_xlsx",
            "gen_pptx",
            "gen_pdf",
        ],
    )

    # ── Anchor Armor: Project Anchor Management ──
    register_toolset(
        "anchor",
        [
            "锚点",
            "锚",
            "anchor",
            "范围检查",
            "校验集",
            "Golden Master",
            "遗产品",
            "重构锚点",
            "初始化锚点",
            "锚点状态",
        ],
        [
            "anchor_init",
            "anchor_check",
            "anchor_status",
            "golden_capture",
            "golden_verify",
            "legacy_inventory",
        ],
    )

    # ── YF Image Suite ──
    register_toolset(
        "image",
        [
            "图片",
            "图像",
            "生成图",
            "文生图",
            "风格模仿",
            "信息图",
            "海报",
            "简历图",
            "PPT图",
            "image",
            "generate image",
            "infographic",
            "style imitate",
            "resume image",
        ],
        [
            "yf_generate_image",
            "yf_recognize_image",
            "yf_optimize_text",
            "yf_create_infographic",
            "yf_imitate_style",
            "yf_create_resume_image",
            "yf_create_ppt",
        ],
    )

    # Platform routes (all activated, including rollback)
    register_platform_map(
        "cli",
        [
            "web",
            "weather",
            "reminder",
            "self",
            "read",
            "exec",
            "mail",
            "brain",
            "path",
            "code",
            "safe_write",
            "rollback",
            "code_session",
            "mirror",
            "anchor",
            "doc",
            "image",
        ],
    )
    register_platform_map(
        "api",
        [
            "chat",
            "web",
            "weather",
            "reminder",
            "self",
            "read",
            "brain",
            "path",
            "code",
            "safe_write",
            "rollback",
            "code_session",
            "mirror",
            "anchor",
            "doc",
        ],
    )
    register_platform_map(
        "default",
        [
            "web",
            "weather",
            "reminder",
            "self",
            "learn",
            "read",
            "exec",
            "mail",
            "brain",
            "path",
            "code",
            "safe_write",
            "rollback",
            "code_session",
            "mirror",
            "anchor",
            "doc",
        ],
    )

    # ── Agent-3: intelligence verification toolset ──
    register_toolset(
        "verify",
        [
            "验证",
            "可信度",
            "核实",
            "去假存真",
            "情报",
            "交叉验证",
            "判断真假",
            "来源",
            "信源",
            "置信",
        ],
        [
            "verify_intelligence",
            "verify_report",
        ],
    )

    # ── QA: whitebox (agent-1) + blackbox (agent-3) dual review ──
    register_toolset(
        "qa",
        [
            "测试",
            "质检",
            "QA",
            "审代码",
            "审逻辑",
            "检查",
            "验证",
            "bug",
            "缺陷",
            "质量",
            "黑盒",
            "白盒",
            "cross check",
        ],
        [
            "qa_double_check",
            "qa_execute_blackbox",
        ],
    )

    # ── Swarm test: parallel stress test ──
    register_toolset(
        "swarm",
        [
            "压力测试",
            "并发",
            "蜂群",
            "多轮",
            "退化",
            "压测",
            "性能",
            "稳定性",
            "stress",
            "load",
        ],
        [
            "qa_swarm_test",
            "qa_multi_round",
        ],
    )

    # ── Hive: swarm worker manager ──
    register_toolset(
        "beehive",
        [
            "工蜂",
            "蜂巢",
            "营蜂",
            "召募",
            "蜂群",
            "孵化",
            "任务分配",
            "蜂王",
            "bee",
        ],
        [
            "bee_recruit",
            "bee_deploy",
            "bee_complete",
            "bee_roster",
            "bee_learn",
            "bee_hive_status",
        ],
    )

    # ── Distillation Engine (train local model from experience)
    register_toolset(
        "distill",
        [
            "蒸馏",
            "训练",
            "微调",
            "学习",
            "经验",
            "积累",
            "distill",
            "train",
            "finetune",
            "lora",
            "进化",
            "提升模型",
            "本地模型",
            "训练模型",
        ],
        [
            "distill_export",
            "distill_train",
            "distill_push",
            "distill_eval",
        ],
    )
