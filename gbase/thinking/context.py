"""
L0 ContextLever — Input Representation Restructuring

Helps any LLM "see the whole picture before acting":
restructure the problem representation, scan dependencies, identify blind spots.

Features:
  - Context scanning: entity/action/constraint extraction + directory scan
  - Dependency walking: cross-file import graph + circular detection
  - Problem mapping: multi-dimensional problem characterization
"""

import ast
import os
import re
from collections import defaultdict
from typing import Any

# ──────────────────────────────────────────────
# Context scanning
# ──────────────────────────────────────────────


def context_scan(task: str) -> dict[str, Any]:
    """Scan a task description and produce a structured problem representation.

    When directory paths are detected in the task, automatically performs
    os.walk recursive scan and returns real file structure.

    Args:
        task: Raw task description.

    Returns:
        {
            "entities": [...],
            "actions": [...],
            "constraints": [...],
            "dependencies": [...],
            "risk_zones": [...],
            "known_unknowns": [...],
            "recommended_approach": str,
            "directory_scan": {
                "files": [{ "path": str, "size": int, "type": str }],
                "total_files": int,
                "total_size": int,
                "file_types": { "py": 5, ... },
                "depth": int,
                "entry_points": [str]
            }  # only when valid directory path detected
        }
    """
    entities = _extract_entities_v2(task)
    actions = _extract_actions(task)
    constraints = _extract_constraints(task)
    dependencies = _infer_dependencies(task, entities)
    risk_zones = _identify_risks(task, entities, actions)
    unknowns = _identify_known_unknowns(task, entities)

    result = {
        "entities": entities,
        "actions": actions,
        "constraints": constraints,
        "dependencies": dependencies,
        "risk_zones": risk_zones,
        "known_unknowns": unknowns,
        "recommended_approach": _recommend_approach(task, actions, entities),
    }

    dirs_found = _extract_directory_paths(task)
    if dirs_found:
        scan = _scan_directory(dirs_found[0])
        if scan and scan.get("files"):
            result["directory_scan"] = scan

    return result


def _extract_entities_v2(text: str) -> list[str]:
    """Extract entities from task text: paths, ports, tech keywords."""
    entities = set()

    # File/directory paths
    paths = re.findall(
        r"(?:~|/[\w\-]+)[^\s,;)\"]*",
        text,
    )
    for p in paths:
        expanded = os.path.expanduser(p)
        expanded = expanded.rstrip(",.;:])}")
        if os.path.exists(expanded):
            tag = "dir:" if os.path.isdir(expanded) else "file:"
            entities.add(f"{tag}{os.path.realpath(expanded)}")

    # Current directory references
    cwd_refs = re.findall(r"[`']?\./([^\s,;)\"']+)", text)
    for ref in cwd_refs:
        full = os.path.join(os.getcwd(), ref)
        if os.path.exists(full):
            tag = "dir:" if os.path.isdir(full) else "file:"
            entities.add(f"{tag}{full}")

    # Port numbers
    ports = re.findall(r"port[:\s]*(\d+)|端口[:\s]*(\d+)", text.lower())
    for match in ports:
        port = match[0] or match[1]
        entities.add(f"port:{port}")

    # Technology keywords
    tech_keywords = re.findall(r"(?:RAG|FTS5|SQLite|API|DB|config|docker|git|ssh|port|lsof|PID)", text)
    for kw in tech_keywords:
        entities.add(f"tech:{kw}")

    return sorted(entities)


def _extract_directory_paths(text: str) -> list[str]:
    """Extract directory paths from task text."""
    paths = re.findall(r"(?:~|/[\w\-]+)[^\s,;)\"']*", text)
    valid_dirs = []
    for p in paths:
        expanded = os.path.expanduser(p).rstrip(",.;:])}")
        if os.path.isdir(expanded):
            valid_dirs.append(expanded)
    home_refs = re.findall(r"~/[^\s,;)\"']+", text)
    for p in home_refs:
        expanded = os.path.expanduser(p).rstrip(",.;:])}")
        if os.path.isdir(expanded):
            valid_dirs.append(expanded)
    return valid_dirs


def _scan_directory(root: str, max_files: int = 200) -> dict[str, Any]:
    """Recursively scan a directory, return real file structure.

    Args:
        root: Directory path to scan.
        max_files: Max files to collect (safety limit).

    Returns:
        {
            "files": [{ "path": str, "size": int, "type": str, "rel_path": str }],
            "total_files": int,
            "total_size": int,
            "file_types": { "py": 12, "json": 5, ... },
            "depth": int,
            "entry_points": ["main.py", ...]
        }
    """
    if not os.path.isdir(root):
        return {}

    files = []
    file_types: dict[str, int] = defaultdict(int)
    max_depth = 0
    entry_candidates = []

    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d
                for d in dirnames
                if not d.startswith(".")
                and d
                not in (
                    "__pycache__",
                    "node_modules",
                    ".git",
                    ".venv",
                    "venv",
                )
            ]

            rel_dir = os.path.relpath(dirpath, root)
            current_depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
            max_depth = max(max_depth, current_depth)

            for fname in filenames:
                if len(files) >= max_files:
                    break
                if fname.startswith("."):
                    continue

                fpath = os.path.join(dirpath, fname)
                try:
                    fsize = os.path.getsize(fpath)
                except OSError:
                    fsize = 0

                ext = os.path.splitext(fname)[1].lstrip(".").lower() or "no_ext"
                rel_path = os.path.relpath(fpath, root)
                file_types[ext] += 1

                files.append(
                    {
                        "path": fpath,
                        "rel_path": rel_path,
                        "size": fsize,
                        "type": ext,
                    }
                )

                if (
                    fname
                    in (
                        "main.py",
                        "app.py",
                        "server.py",
                        "index.html",
                        "index.js",
                        "cli.py",
                    )
                    and current_depth <= 1
                ):
                    entry_candidates.append(rel_path)

            if len(files) >= max_files:
                break

    except PermissionError:
        pass

    total_size = sum(f["size"] for f in files)

    return {
        "files": files,
        "total_files": len(files),
        "total_size": total_size,
        "file_types": dict(sorted(file_types.items(), key=lambda x: -x[1])),
        "depth": max_depth,
        "entry_points": entry_candidates[:5],
    }


# ──────────────────────────────────────────────
# Dependency walking (cross-file import + circular detection)
# ──────────────────────────────────────────────


def dependency_walk(root_path: str, depth: int = 3) -> dict[str, Any]:
    """Walk file dependency chain.

    Scans .py files recursively, builds an import graph,
    and detects circular imports.

    Args:
        root_path: Starting path (file or directory).
        depth: Recursion depth.

    Returns:
        {
            "root": str,
            "type": "file" | "directory" | "missing",
            "imports": [module_name],
            "missing": [module_name],
            "chain": [str],
            "graph": [{ "from": str, "to": str }],
            "circular_imports": [[str]],
            "all_files": [str],
            "total_py_files": int,
        }
    """
    root_path = os.path.expanduser(root_path)

    if not os.path.exists(root_path):
        return {"root": root_path, "type": "missing", "imports": [], "missing": [root_path], "chain": []}

    if os.path.isdir(root_path):
        return _walk_directory_deps(root_path, depth)

    return _walk_file_deps(root_path, depth)


def _walk_directory_deps(dir_path: str, _depth: int) -> dict[str, Any]:
    """Scan all .py files in a directory for imports."""
    py_files = []
    for dirpath, dirnames, filenames in os.walk(dir_path):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
        for f in filenames:
            if f.endswith(".py") and not f.startswith("."):
                py_files.append(os.path.join(dirpath, f))
        if len(py_files) > 100:
            break

    all_imports = set()
    all_missing = set()
    graph = []
    modules_map = {}

    for fp in py_files:
        rel = os.path.relpath(fp, dir_path)
        mod_name = rel.replace(os.sep, ".").rstrip(".py")
        if mod_name.endswith(".__init__"):
            mod_name = mod_name[:-9]
        modules_map[mod_name] = fp
        parts = mod_name.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[:i])
            if parent not in modules_map:
                modules_map[parent] = fp

    for fp in py_files:
        imports, missing = _parse_imports(fp)
        all_imports.update(imports)
        all_missing.update(missing)
        for imp in imports:
            if imp in modules_map:
                from_file = os.path.relpath(fp, dir_path)
                to_file = os.path.relpath(modules_map[imp], dir_path)
                if from_file != to_file:
                    graph.append({"from": from_file, "to": to_file})

    circular = _detect_circular_imports(graph)

    return {
        "root": dir_path,
        "type": "directory",
        "imports": sorted(all_imports),
        "missing": sorted(all_missing),
        "chain": py_files[:20],
        "total_py_files": len(py_files),
        "graph": graph,
        "circular_imports": circular,
        "all_files": [os.path.relpath(f, dir_path) for f in py_files],
    }


def _walk_file_deps(file_path: str, depth: int) -> dict[str, Any]:
    """Analyze a single .py file's dependencies."""
    imports, missing = _parse_imports(file_path)
    chain = [file_path]
    sub_imports = set()
    sub_missing = set()

    if depth > 1:
        for imp in imports:
            if imp.startswith(("lib.", "tools.", "core.")):
                imp_path = _resolve_import_path(file_path, imp)
                if imp_path and os.path.exists(imp_path) and imp_path not in chain:
                    chain.append(imp_path)
                    sub_result = _walk_file_deps(imp_path, depth - 1)
                    sub_imports.update(sub_result.get("imports", []))
                    sub_missing.update(sub_result.get("missing", []))

    return {
        "root": file_path,
        "type": "file",
        "imports": sorted(set(imports) | sub_imports),
        "missing": sorted(set(missing) | sub_missing),
        "chain": chain[:20],
    }


def _parse_imports(file_path: str) -> tuple[set[str], set[str]]:
    """Parse .py file imports using AST."""
    imports: set[str] = set()
    missing: set[str] = set()

    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return imports, missing

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return imports, missing

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not alias.name.startswith("include"):
                    imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.module.startswith("."):
            imports.add(node.module.split(".")[0])

    stdlib = {
        "os",
        "sys",
        "json",
        "re",
        "time",
        "math",
        "random",
        "datetime",
        "typing",
        "collections",
        "pathlib",
        "functools",
        "itertools",
        "subprocess",
        "shutil",
        "tempfile",
        "io",
        "hashlib",
        "uuid",
        "asyncio",
        "threading",
        "multiprocessing",
        "pickle",
        "sqlite3",
        "logging",
        "argparse",
        "configparser",
        "copy",
        "inspect",
        "abc",
        "enum",
        "base64",
        "textwrap",
        "string",
        "struct",
        "http",
        "urllib",
        "socket",
        "email",
        "importlib",
    }

    for imp in imports:
        if imp not in stdlib:
            resolved = _resolve_import_path(file_path, imp)
            if resolved is None or not os.path.exists(resolved):
                missing.add(imp)

    return imports, missing


def _resolve_import_path(file_path: str, module: str) -> str | None:
    """Resolve a module import to a local file path."""
    dir_path = os.path.dirname(os.path.abspath(file_path))
    parts = module.split(".")

    candidates = [
        os.path.join(dir_path, *parts) + ".py",
        os.path.join(dir_path, *parts[:-1], parts[-1], "__init__.py"),
        os.path.join(dir_path, "..", *parts) + ".py",
    ]

    for candidate in candidates:
        normalized = os.path.normpath(candidate)
        if os.path.exists(normalized):
            return normalized

    return None


def _detect_circular_imports(graph: list[dict[str, str]]) -> list[list[str]]:
    """Detect circular imports using DFS."""
    adj = defaultdict(list)
    for edge in graph:
        adj[edge["from"]].append(edge["to"])

    cycles = []
    visited = set()
    path = []

    def dfs(node: str):
        if node in path:
            idx = path.index(node)
            cycle = path[idx:] + [node]
            if cycle and cycle[0] == min(cycle):
                cycles.append(cycle)
            return
        if node in visited:
            return
        visited.add(node)
        path.append(node)
        for neighbor in adj.get(node, []):
            dfs(neighbor)
        path.pop()

    for node in list(adj.keys()):
        dfs(node)

    return cycles


# ──────────────────────────────────────────────
# Actions, constraints, risks, unknowns
# ──────────────────────────────────────────────


def _extract_actions(text: str) -> list[str]:
    """Extract actions from task text."""
    actions = []
    action_patterns = [
        (r"修[復复改]", "repair"),
        (r"重[啟启寫写新建做]", "modify"),
        (r"[創创新建生]", "create"),
        (r"[刪删移除清除]", "delete"),
        (r"[審审檢查查验驗]", "audit"),
        (r"[測試试評评]", "test"),
        (r"[部發发推送]", "deploy"),
        (r"[調调试跟踪偵]", "debug"),
        (r"[遷迁移複复制]", "migrate"),
    ]
    for pattern, action_type in action_patterns:
        if re.search(pattern, text) and action_type not in actions:
            actions.append(action_type)
    return actions


def _extract_constraints(text: str) -> list[str]:
    """Extract constraints from task text."""
    constraints = []
    constraint_patterns = [
        (r"不[要能會]", "禁止"),
        (r"必須|一定|務必", "強制"),
        (r"小心|注意|謹慎|安全", "警告"),
        (r"快[速點速]", "時效"),
        (r"保[留持證]", "保留"),
    ]
    for pattern, constraint_type in constraint_patterns:
        if re.search(pattern, text):
            constraints.append(constraint_type)
    return constraints


def _infer_dependencies(_task: str, entities: list[str]) -> list[str]:
    """Infer dependencies from task context."""
    dependencies = set()
    for entity in entities:
        if entity.startswith(("file:", "dir:")):
            path = entity.split(":", 1)[1]
            if os.path.exists(path):
                dependencies.add(f"file_exists:{path}")
            else:
                dependencies.add(f"file_missing:{path}")
        elif entity.startswith("port:"):
            port = entity[5:]
            dependencies.add(f"port_check:{port}")
    return sorted(dependencies)


def _identify_risks(_task: str, entities: list[str], actions: list[str]) -> list[str]:
    """Identify risk zones."""
    risks = []
    if any(e.startswith("port:") for e in entities):
        risks.append("Port conflict: ensure no existing process uses the same port")
    if "modify" in actions:
        risks.append("Modification: backup original files before changes")
    if any("config" in e.lower() or "env" in e.lower() for e in entities):
        risks.append("Config change: back up before modifying, prepare rollback")
    if "delete" in actions:
        risks.append("Delete operation: irreversible, double-check target")
    return risks


def _identify_known_unknowns(_task: str, entities: list[str]) -> list[str]:
    """Identify information gaps."""
    unknowns = []
    if not any(e.startswith(("file:", "dir:")) for e in entities):
        unknowns.append("No file paths specified — need to confirm target")
    if not any(e.startswith("port:") for e in entities):
        unknowns.append("No ports mentioned — confirm if network services are involved")
    return unknowns


def _recommend_approach(_task: str, actions: list[str], _entities: list[str]) -> str:
    """Recommend execution approach based on task characteristics."""
    if not actions:
        return "Insufficient information. Please provide more detail."

    approach_parts = []
    if "audit" in actions:
        approach_parts += [
            "1. Scan: confirm status of all relevant files",
            "2. Decompose: categorize (interface/data/config/deployment)",
            "3. Verify: test each finding",
        ]
    if "repair" in actions or "modify" in actions:
        approach_parts += [
            "1. Backup original files",
            "2. Small, incremental changes with verification after each step",
        ]
    if "debug" in actions:
        approach_parts += ["1. Confirm scope of problem", "2. Trace up the dependency chain"]
    if "create" in actions:
        approach_parts += ["1. Write tests before implementation (TDD)"]
    if not approach_parts:
        return "Safe mode: 1. Read-only first 2. Confirm dependencies 3. Iterate incrementally"

    return "\n".join(approach_parts)


# ──────────────────────────────────────────────
# Problem mapping (multi-dimensional characterization)
# ──────────────────────────────────────────────


def problem_mapping(task: str) -> dict[str, Any]:
    """Map a problem onto multiple dimensions for richer representation.

    Args:
        task: Raw task description.

    Returns:
        {
            "dimensions": {
                "scope": str,
                "complexity": str,
                "risk_level": str,
                "time_sensitivity": str,
            },
            "perspectives": [str],
        }
    """
    task_lower = task.lower()
    entities = _extract_entities_v2(task)

    scope = "multi_component" if len(entities) > 3 else "single_component" if entities else "single_file_or_service"

    complexity_hints = len(re.findall(r"因為|所以|但是|如果|當|除非|because|therefore|if|when|unless", task))
    complexity = "complex" if complexity_hints > 5 else "medium" if complexity_hints >= 2 else "simple"

    risk_count = sum(
        1
        for kw in ["delete", "remove", "kill", "restart", "rm", "overwrite", "刪除", "修改", "重啟"]
        if kw in task_lower
    )
    risk_level = "high" if risk_count >= 3 else "medium" if risk_count >= 1 else "low"

    time_keywords = {
        "immediate": ["马上", "立刻", "紧急", "asap", "urgent", "now"],
        "today": ["今天", "today", "tonight"],
    }
    time_sensitivity = "flexible"
    for level, words in time_keywords.items():
        if any(kw in task_lower for kw in words):
            time_sensitivity = level
            break

    perspectives = []
    if entities:
        perspectives.append(f"Technical: involves {', '.join(entities[:3])}")
    if any(e.startswith(("file:", "dir:")) for e in entities):
        perspectives.append("Operations: ensure backup and rollback plan")
    perspectives.append("Maintenance: prefer lower-dependency solutions")

    return {
        "dimensions": {
            "scope": scope,
            "complexity": complexity,
            "risk_level": risk_level,
            "time_sensitivity": time_sensitivity,
        },
        "perspectives": perspectives,
    }
