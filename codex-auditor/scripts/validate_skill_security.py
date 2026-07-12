#!/usr/bin/env python3
"""Small, deliberately conservative static check for this offline skill."""
from __future__ import annotations
import ast
import sys
from pathlib import Path

FORBIDDEN = {"requests", "socket", "urllib", "http", "httpx", "aiohttp", "ftplib", "telnetlib", "subprocess"}
ALLOWED = {"__future__", "argparse", "datetime", "hashlib", "html", "json", "math", "os", "re", "stat", "sys", "collections", "dataclasses", "pathlib", "typing", "unicodedata", "ast", "importlib", "tempfile", "audit_codex_usage", "rich_audit"}
FORBIDDEN_OS_CALLS = {"system", "popen", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe"}

def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    errors = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [name.name.split(".")[0] for name in node.names] if isinstance(node, ast.Import) else [(node.module or "").split(".")[0]]
                for name in names:
                    if name in FORBIDDEN or name not in ALLOWED:
                        errors.append(f"{path}: forbidden or non-standard import {name}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec", "__import__"}:
                errors.append(f"{path}: dynamic execution {node.func.id}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and node.func.attr in FORBIDDEN_OS_CALLS:
                errors.append(f"{path}: forbidden process execution os.{node.func.attr}")
    if errors:
        print("\n".join(errors))
        return 1
    print("security validation passed: standard-library only, no forbidden network/process imports")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
