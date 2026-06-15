# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/test_generator.py

YF-test-generator 集成：从代码自动生成单元测试。
专为重锤（工程臂）设计。
"""

import asyncio
import logging
import os
import sys

from lib.toolkit import tool

logger = logging.getLogger(__name__)
SKILL_DIR = os.path.expanduser("~/.qclaw/skills/YF-test-generator/scripts")


@tool()
async def generate_tests(file_path: str, language: str = "auto", output_ext: str = "") -> dict:
    """为指定源代码文件自动生成单元测试。

    支持 Python、JavaScript、TypeScript、Java。
    测试文件会生成在源文件同目录下。

    Args:
        file_path: 源文件路径（绝对路径或相对路径）
        language: 语言，自动检测或指定 python / javascript / java / ruby
        output_ext: 输出扩展名覆盖，不传则自动推断

    Returns:
        生成的测试文件路径和统计信息
    """
    # 构建工作目录
    workdir = os.path.expanduser("~")

    cmd = [
        sys.executable or "python3",
        os.path.join(SKILL_DIR, "generate_tests.py"),
        "--file",
        file_path,
    ]
    if language and language != "auto":
        cmd.extend(["--lang", language])
    if output_ext:
        cmd.extend(["--ext", output_ext])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        output = stdout.decode("utf-8", errors="replace")
        errors = stderr.decode("utf-8", errors="replace")

        # 尝试提取测试文件路径
        test_file = ""
        for line in output.split("\n"):
            if "✅ 测试文件:" in line or "test file:" in line.lower():
                test_file = line.split(":", 1)[1].strip()
                break

        return {
            "success": proc.returncode == 0,
            "file": file_path,
            "test_file": test_file,
            "output": output[:2000],
            "errors": errors[:500] if errors else "",
        }
    except TimeoutError:
        return {"success": False, "error": "测试生成超时（30秒）"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@tool()
async def generate_tests_batch(directory: str, language: str = "python") -> dict:
    """批量为一个目录下的所有源码文件生成测试。

    Args:
        directory: 目录路径
        language: 语言（python / javascript / java）

    Returns:
        每个文件的生成结果列表
    """
    workdir = os.path.expanduser("~")

    cmd = [
        sys.executable or "python3",
        os.path.join(SKILL_DIR, "generate_tests.py"),
        "--dir",
        directory,
        "--lang",
        language,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

        return {
            "success": proc.returncode == 0,
            "directory": directory,
            "output": stdout.decode("utf-8", errors="replace")[:3000],
            "errors": stderr.decode("utf-8", errors="replace")[:500] if stderr else "",
        }
    except TimeoutError:
        return {"success": False, "error": "批量生成超时（60秒）"}
    except Exception as e:
        return {"success": False, "error": str(e)}
