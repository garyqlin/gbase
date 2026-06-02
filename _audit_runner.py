#!/usr/bin/env python3
"""Audit runner: syntax check + import check for gbase-gh project."""
import ast
import os
import sys
import py_compile

ROOT = "/Users/gary/Projects/gbase-gh"

files = ["main.py"]
for d in ["lib", "tools"]:
    for f in sorted(os.listdir(os.path.join(ROOT, d))):
        if f.endswith(".py"):
            files.append(f"{d}/{f}")

# Phase 1: Syntax Check
print("=" * 60)
print("PHASE 1: Syntax Check")
print("=" * 60)
syntax_errors = []
for f in files:
    fpath = os.path.join(ROOT, f)
    try:
        py_compile.compile(fpath, doraise=True)
        print(f"  OK: {f}")
    except py_compile.PyCompileError as e:
        syntax_errors.append((f, str(e)))
        print(f"  FAIL: {f} -> {e}")

# Phase 2: Import breakage
print()
print("=" * 60)
print("PHASE 2: Import Dependency Analysis")
print("=" * 60)

builtins = set(sys.builtin_module_names)
installed = set()
try:
    import pkg_resources
    installed = {pkg.key for pkg in pkg_resources.working_set}
except Exception:
    pass

def exists_local(imp):
    for p in [
        f"lib/{imp}.py", f"tools/{imp}.py", f"{imp}.py",
        f"lib/{imp}/__init__.py", f"tools/{imp}/__init__.py",
    ]:
        if os.path.exists(os.path.join(ROOT, p)):
            return True
    return False

def get_imports(filepath):
    with open(filepath) as fh:
        try:
            tree = ast.parse(fh.read(), filename=filepath)
        except SyntaxError:
            return [], []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split(".")[0])
    return imports, []

local_modules = set()
for f in files:
    name = f.replace(".py", "").replace("lib/", "").replace("tools/", "").replace("main", "")
    if name:
        local_modules.add(name)

missing_imports = []
all_deps = {}
for f in files:
    fpath = os.path.join(ROOT, f)
    if not os.path.exists(fpath):
        continue
    imps, _ = get_imports(fpath)
    local_deps = []
    for imp in imps:
        if imp in ("lib", "tools") or imp == f.replace(".py", "").split("/")[-1]:
            continue
        if imp in builtins or imp.lower() in installed:
            continue
        if exists_local(imp) or imp in local_modules:
            local_deps.append(imp)
            continue
        missing_imports.append((f, imp))
    all_deps[f] = local_deps

if missing_imports:
    print("\nPotential missing imports:")
    for f, imp in sorted(set(missing_imports)):
        print(f"  {f}: {imp}")
else:
    print("\nNo broken imports detected.")

print("\nLocal dependency graph:")
for f, deps in sorted(all_deps.items()):
    if deps:
        print(f"  {f} -> {deps}")

# Phase 3: Name scan
print()
print("=" * 60)
print("PHASE 3: Function/Class Scan")
print("=" * 60)

for f in files:
    fpath = os.path.join(ROOT, f)
    if not os.path.exists(fpath):
        continue
    with open(fpath) as fh:
        try:
            tree = ast.parse(fh.read(), filename=fpath)
        except SyntaxError:
            continue
    funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    print(f"  {f}: {len(funcs)} funcs, {len(classes)} classes")

print()
print("=" * 60)
print("AUDIT SUMMARY")
print("=" * 60)
print(f"Total files: {len(files)}")
print(f"Syntax errors: {len(syntax_errors)}")
if syntax_errors:
    for f, e in syntax_errors:
        print(f"  FAIL {f}: {e}")
else:
    print("  All syntax OK")
if missing_imports:
    print(f"Suspicious imports: {len(set(missing_imports))}")
else:
    print("  No broken imports")
