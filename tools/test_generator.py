# SPDX-License-Identifier: MIT
"""
gbase/tools/test_generator.py

Auto-generate unit tests from code.
Designed for agent-1 (engineering arm).
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
    """Auto-generate unit tests for a source code file.

    Supports Python, JavaScript, TypeScript, Java.
    Test files are generated in the same directory as the source file.

    Args:
        file_path: Source file path (absolute or relative)
        language: Language, auto-detect or specify python / javascript / java / ruby
        output_ext: Output extension override, auto-inferred if not provided

    Returns:
        Generated test file path and statistics
    """
    # Build working directory
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

        # Attempt to extract test file path
        test_file = ""
        for line in output.split("\n"):
            if "✅ Test file:" in line or "test file:" in line.lower():
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
        return {"success": False, "error": "Test generation timed out (30s)"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@tool()
async def generate_tests_batch(directory: str, language: str = "python") -> dict:
    """Batch generate tests for all source files in a directory.

    Args:
        directory: Directory path
        language: Language (python / javascript / java)

    Returns:
        List of generation results for each file
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
        return {"success": False, "error": "Batch generation timed out (60s)"}
    except Exception as e:
        return {"success": False, "error": str(e)}
