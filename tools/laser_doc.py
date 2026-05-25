# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/laser_doc.py

Laser doc writer: developer docs + test plans.
Provides doc authoring capabilities for Laser (test arm): write developer docs first, then test based on the docs.
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
    """Run a shell script and return the result"""
    cmd = ["bash", script_path] + [str(a) for a in args if a]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        return {"status": "ok", "stdout": stdout.decode(), "stderr": stderr.decode()}
    except TimeoutError:
        return {"status": "error", "message": "Execution timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tool()
async def scan_project(project_dir: str) -> dict:
    """Scan project structure — automatically identify routes/controllers/migrations/configs/tests.

    Used for understanding the project landscape before writing developer docs,
    and for system analysis before drafting test plans.

    Args:
        project_dir: Project directory path

    Returns:
        Scan result containing categorized lists: routes/controllers/migrations/configs/tests
    """
    if not os.path.isdir(project_dir):
        return {"status": "error", "message": f"Directory not found: {project_dir}"}

    info = {"routes": [], "controllers": [], "migrations": [], "configs": [], "tests": []}

    for root, dirs, files in os.walk(project_dir):
        # Skip directories not to scan
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
    """Create developer documentation skeleton files (README/API/architecture docs, etc.).

    Call scan_project first to understand the project structure before invoking this tool to write docs.
    Doc format should follow the YF-documentation-zh skill specification.

    Args:
        project_dir: Project directory path
        doc_type: Document type
            - readme: Project README (quick start/install/usage)
            - api: API reference manual (endpoints/params/responses/error codes)
            - architecture: Architecture overview (tech stack/modules/data flow)
            - dev_guide: Developer guide (env setup/code conventions/branch strategy)
            - deploy: Deployment docs (env requirements/config/deploy steps)
            - changelog: Changelog

    Returns:
        Skeleton file path, to be filled in by you (the LLM) per the specification
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
        "message": f"Skeleton created, please write content per the YF-documentation-zh specification to {filepath}",
        "path": filepath,
        "doc_type": doc_type,
    }


@tool()
async def author_test_plan(project_dir: str, module: str = "") -> dict:
    """Generate a test plan skeleton based on docs and project scan results.

    Ensure developer docs are in place first (call author_doc to write at least README or API docs before testing).
    Test plan is generated based on project structure and API definitions, covering scenario flows + exception boundaries.

    Args:
        project_dir: Project directory path
        module: Test module name (optional, defaults to full test coverage)

    Returns:
        Test plan file path, to be filled in by you (the LLM) per the Laser testing methodology
    """
    test_dir = os.path.join(project_dir, "tests")
    os.makedirs(test_dir, exist_ok=True)

    plan_name = f"TEST_PLAN_{module or 'full'}.md"
    plan_path = os.path.join(test_dir, plan_name)

    return {
        "status": "ok",
        "message": f"Test plan skeleton created, please fill in per the testing methodology to {plan_path}",
        "path": plan_path,
        "module": module or "full",
    }
