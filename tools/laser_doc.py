# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/laser_doc.py

Laser 文档撰写工具 — 开发文档生成 + 测试计划生成。
为 Laser（测试臂）提供文档撰写能力：先写开发文档，再按文档做测试。
"""

import asyncio
import logging
import os
import re

from lib.toolkit import tool

logger = logging.getLogger(__name__)
SKILL_DIR = os.path.expanduser("~/.qclaw/skills")
ANCHOR_DIR = os.path.join(SKILL_DIR, "YF-anchor-keeper/scripts")


async def _run_script(script_path, *args):
    """运行 shell 脚本并返回结果"""
    cmd = ["bash", script_path] + [str(a) for a in args if a]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        return {"status": "ok", "stdout": stdout.decode(), "stderr": stderr.decode()}
    except TimeoutError:
        return {"status": "error", "message": "执行超时"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tool()
async def scan_project(project_dir: str) -> dict:
    """扫描项目结构——自动识别路由/控制器/数据库迁移/配置文件/测试文件。

    用于写开发文档前了解项目全貌，也用于制定测试计划前的系统分析。

    Args:
        project_dir: 项目目录路径

    Returns:
        扫描结果，包含 routes/controllers/migrations/configs/tests 分类列表
    """
    if not os.path.isdir(project_dir):
        return {"status": "error", "message": f"目录不存在: {project_dir}"}

    info = {"routes": [], "controllers": [], "migrations": [], "configs": [], "tests": []}

    for root, dirs, files in os.walk(project_dir):
        # 跳过不扫描的目录
        skip_dirs = {"node_modules", "vendor", ".git", "__pycache__", ".venv", "venv"}
        dirs[:] = [d for d in dirs if d not in skip_dirs]

        for f in files:
            rel = os.path.relpath(os.path.join(root, f), project_dir)

            if re.search(r"(routes|api|web)\.(php|py|js|ts)$", rel):
                info["routes"].append(rel)
            if re.search(r"/controllers?/", rel, re.IGNORECASE):
                info["controllers"].append(rel)
            if re.search(r"migration", rel, re.IGNORECASE) or "migrations" in rel:
                info["migrations"].append(rel)
            if re.search(r"\.(yaml|yml|json|toml|ini|env|conf)$", f):
                info["configs"].append(rel)
            if re.search(r"(Test|test|spec|__tests__)", rel):
                info["tests"].append(rel)

    return {"status": "ok", "project": project_dir, "result": info}


@tool()
async def author_doc(project_dir: str, doc_type: str = "readme") -> dict:
    """为项目创建开发文档骨架文件（README/API/架构说明等）。

    先调 scan_project 了解项目结构后再调用此工具撰写文档。
    文档格式请遵循 YF-documentation-zh 技能规范。

    Args:
        project_dir: 项目目录路径
        doc_type: 文档类型
            - readme: 项目 README（快速开始/安装/用法）
            - api: API 参考手册（接口列表/参数/响应/错误码）
            - architecture: 架构说明（技术栈/模块划分/数据流）
            - dev_guide: 开发指南（环境搭建/代码规范/分支策略）
            - deploy: 部署文档（环境要求/配置/部署步骤）
            - changelog: 更新日志

    Returns:
        文档骨架路径，由你（LLM）按规范填充内容
    """
    docs_dir = os.path.join(project_dir, "docs")
    os.makedirs(docs_dir, exist_ok=True)

    type_map = {
        "readme": "README.md",
        "api": "API.md",
        "architecture": "ARCHITECTURE.md",
        "dev_guide": "DEV_GUIDE.md",
        "deploy": "DEPLOY.md",
        "changelog": "CHANGELOG.md",
    }

    filename = type_map.get(doc_type, f"{doc_type.upper()}.md")
    filepath = os.path.join(docs_dir, filename)

    return {
        "status": "ok",
        "message": f"骨架已创建，请按 YF-documentation-zh 规范撰写到 {filepath}",
        "path": filepath,
        "doc_type": doc_type,
    }


@tool()
async def author_test_plan(project_dir: str, module: str = "") -> dict:
    """根据文档和项目扫描结果生成测试计划骨架。

    测试前先确保开发文档已就位（调 author_doc 写过至少 README 或 API 文档）。
    测试计划按项目结构和 API 定义生成，覆盖场景流+异常边界。

    Args:
        project_dir: 项目目录路径
        module: 测试模块名称（可选，默认全量测试）

    Returns:
        测试计划文件路径，由你（LLM）按 Laser 测试方法论填充
    """
    test_dir = os.path.join(project_dir, "tests")
    os.makedirs(test_dir, exist_ok=True)

    plan_name = f"TEST_PLAN_{module or 'full'}.md"
    plan_path = os.path.join(test_dir, plan_name)

    return {
        "status": "ok",
        "message": f"测试计划骨架已创建，请按测试方法论填充到 {plan_path}",
        "path": plan_path,
        "module": module or "full",
    }
