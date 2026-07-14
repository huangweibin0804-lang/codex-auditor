#!/usr/bin/env python3
"""Install or verify the packaged Skill without network access."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = ".install-manifest.json"
IGNORED_PARTS = {".DS_Store", "__pycache__", ".git", MANIFEST_NAME}


def included_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and not any(part in IGNORED_PARTS for part in path.parts) and path.suffix != ".pyc"
    )


def fingerprint(root: Path) -> str | None:
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    for path in included_files(root):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def check(source: Path, target: Path) -> dict[str, object]:
    source_hash = fingerprint(source)
    target_hash = fingerprint(target)
    return {
        "source": str(source),
        "target": str(target),
        "source_fingerprint": source_hash,
        "target_fingerprint": target_hash,
        "in_sync": bool(source_hash and source_hash == target_hash),
    }


def copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in IGNORED_PARTS or name.endswith(".pyc")}


def install(source: Path, target: Path, force: bool = False) -> dict[str, object]:
    source = source.resolve()
    target = target.expanduser().resolve()
    if not (source / "SKILL.md").is_file():
        raise ValueError(f"Skill source is missing SKILL.md: {source}")
    if source == target:
        result = check(source, target)
        result["status"] = "already_installed"
        return result
    if target.exists() and not force:
        current = check(source, target)
        if current["in_sync"]:
            current["status"] = "already_in_sync"
            return current
        raise ValueError(f"target already exists and differs: {target}; rerun with --force after reviewing the target")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.parent / f".{target.name}.installing"
    backup = target.parent / f".{target.name}.backup"
    for stale in (temporary, backup):
        if stale.exists():
            shutil.rmtree(stale)
    shutil.copytree(source, temporary, ignore=copy_ignore)
    source_hash = fingerprint(source)
    manifest = {
        "source_fingerprint": source_hash,
        "installed_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "source_directory_name": source.name,
    }
    (temporary / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        if target.exists():
            target.rename(backup)
        temporary.rename(target)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if not target.exists() and backup.exists():
            backup.rename(target)
        raise
    result = check(source, target)
    result["status"] = "installed"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Install or verify this local Codex Skill.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "install"):
        command = sub.add_parser(name)
        command.add_argument("--source", type=Path, default=PACKAGE_ROOT)
        command.add_argument("--target", type=Path, default=Path.home() / ".codex" / "skills" / "codex-auditor")
        if name == "install":
            command.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        result = check(args.source, args.target) if args.command == "check" else install(args.source, args.target, args.force)
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("in_sync") or args.command == "install" else 2


if __name__ == "__main__":
    raise SystemExit(main())
