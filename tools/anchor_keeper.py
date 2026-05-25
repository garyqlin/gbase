# SPDX-License-Identifier: MIT
"""
Anchor keeper — project anchor management tool
Project anchor management: init, check, status, Golden Master capture/verify, legacy scan
"""

import os
import subprocess

from lib.toolkit import tool

SCRIPTS_DIR = os.path.expanduser("~/.qclaw/skills/YF-anchor-keeper/scripts")


@tool
def anchor_init(project_dir: str = None) -> str:
    """在项目目录初始化三锚点（CHANGELOG+DECISIONS+BOUNDARIES）和校验目录。新项目启动或重构前必须调用。参数 project_dir 可选，默认当前目录。"""
    cmd = [os.path.join(SCRIPTS_DIR, "init-anchor.sh")]
    if project_dir:
        cmd.append(project_dir)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return (result.stdout + result.stderr).strip() or f"anchor_init: completed (returncode={result.returncode})"


@tool
def anchor_check(project_dir: str = None) -> str:
    """检查当前修改是否在锚点目标范围内。输出 PASS/WARN/FAIL。每次修改前应调用此工具。"""
    cmd = [os.path.join(SCRIPTS_DIR, "anchor-check.sh")]
    if project_dir:
        cmd.append(project_dir)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return (result.stdout + result.stderr).strip() or f"anchor_check: completed (returncode={result.returncode})"


@tool
def anchor_status(project_dir: str = None) -> str:
    """查看项目锚点全景状态。返回锚点/遗产品/校验集/测试四维状态。"""
    cmd = [os.path.join(SCRIPTS_DIR, "anchor-status.sh")]
    if project_dir:
        cmd.append(project_dir)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return (result.stdout + result.stderr).strip() or f"anchor_status: completed (returncode={result.returncode})"


@tool
def golden_capture(url: str, name: str = None) -> str:
    """捕获接口当前输出作为 Golden Master 校验集。用于重构前记录功能行为。参数 url 为接口地址，name 可选输出文件名。"""
    cmd = [os.path.join(SCRIPTS_DIR, "golden-capture.sh"), url]
    if name:
        cmd.append(name)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return (result.stdout + result.stderr).strip() or f"golden_capture: completed (returncode={result.returncode})"


@tool
def golden_verify(project_dir: str = None) -> str:
    """对比当前输出与 Golden Master 校验集。返回 PASS/FAIL。修改后应调用此工具确认功能未丢失。"""
    base = project_dir or "."
    golden_dir = os.path.join(base, "ANCHOR.d", "golden")
    cmd = [os.path.join(SCRIPTS_DIR, "golden-verify.sh"), golden_dir]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return (result.stdout + result.stderr).strip() or f"golden_verify: completed (returncode={result.returncode})"


@tool
def legacy_inventory(old_dir: str, output_dir: str = None) -> str:
    """扫描旧版目录生成遗产品清单。用于重做前记录必须保留的功能和修复的bug。参数 old_dir 为旧版本目录，output_dir 可选。"""
    cmd = [os.path.join(SCRIPTS_DIR, "legacy-inventory.sh"), old_dir]
    if output_dir:
        cmd.append(output_dir)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return (result.stdout + result.stderr).strip() or f"legacy_inventory: completed (returncode={result.returncode})"
