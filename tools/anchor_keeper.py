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
    """Initialize three anchors (CHANGELOG+DECISIONS+BOUNDARIES) and verification directory in the project. Must be called before starting a new project or refactoring. Parameter project_dir is optional, defaults to current directory."""
    cmd = [os.path.join(SCRIPTS_DIR, "init-anchor.sh")]
    if project_dir:
        cmd.append(project_dir)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return (result.stdout + result.stderr).strip() or f"anchor_init: completed (returncode={result.returncode})"


@tool
def anchor_check(project_dir: str = None) -> str:
    """Check if current changes fall within anchor scope. Returns PASS/WARN/FAIL. Should be called before each modification."""
    cmd = [os.path.join(SCRIPTS_DIR, "anchor-check.sh")]
    if project_dir:
        cmd.append(project_dir)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return (result.stdout + result.stderr).strip() or f"anchor_check: completed (returncode={result.returncode})"


@tool
def anchor_status(project_dir: str = None) -> str:
    """View the full anchor status of the project. Returns four-dimensional status: anchors, legacy artifacts, verification sets, and tests."""
    cmd = [os.path.join(SCRIPTS_DIR, "anchor-status.sh")]
    if project_dir:
        cmd.append(project_dir)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return (result.stdout + result.stderr).strip() or f"anchor_status: completed (returncode={result.returncode})"


@tool
def golden_capture(url: str, name: str = None) -> str:
    """Capture the current API output as a Golden Master verification set. Used to record functional behavior before refactoring. Parameter url is the API endpoint, name is optional output filename."""
    cmd = [os.path.join(SCRIPTS_DIR, "golden-capture.sh"), url]
    if name:
        cmd.append(name)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return (result.stdout + result.stderr).strip() or f"golden_capture: completed (returncode={result.returncode})"


@tool
def golden_verify(project_dir: str = None) -> str:
    """Compare current output against the Golden Master verification set. Returns PASS/FAIL. Should be called after modifications to confirm no functionality loss."""
    base = project_dir or "."
    golden_dir = os.path.join(base, "ANCHOR.d", "golden")
    cmd = [os.path.join(SCRIPTS_DIR, "golden-verify.sh"), golden_dir]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return (result.stdout + result.stderr).strip() or f"golden_verify: completed (returncode={result.returncode})"


@tool
def legacy_inventory(old_dir: str, output_dir: str = None) -> str:
    """Scan legacy directory to generate a legacy artifact inventory. Used to record must-preserve features and fixed bugs before rewriting. Parameter old_dir is the legacy directory, output_dir is optional."""
    cmd = [os.path.join(SCRIPTS_DIR, "legacy-inventory.sh"), old_dir]
    if output_dir:
        cmd.append(output_dir)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return (result.stdout + result.stderr).strip() or f"legacy_inventory: completed (returncode={result.returncode})"
