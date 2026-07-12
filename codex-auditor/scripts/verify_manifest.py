#!/usr/bin/env python3
"""Verify local evidence hashes without reading or displaying log content."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def verify(manifest_path: Path, source_root: Path) -> list[str]:
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    source_cache: dict[Path, tuple[bytes, list[bytes], str] | None] = {}
    for entry in entries:
        evidence = entry.get("evidence_id", "unknown")
        source = source_root / entry.get("source_jsonl_relative_path", "")
        if source not in source_cache:
            if source.is_file():
                raw = source.read_bytes()
                source_cache[source] = (raw, raw.splitlines(), digest(raw))
            else:
                source_cache[source] = None
        cached = source_cache[source]
        if cached is None:
            errors.append(f"{evidence}: source_missing")
            continue
        raw, lines, source_digest = cached
        if source_digest != entry.get("source_jsonl_sha256"):
            errors.append(f"{evidence}: source_changed")
            continue
        line_number = entry.get("source_line_number")
        if not isinstance(line_number, int) or not 1 <= line_number <= len(lines):
            errors.append(f"{evidence}: source_line_missing")
        elif digest(lines[line_number - 1]) != entry.get("source_line_sha256"):
            errors.append(f"{evidence}: source_line_changed")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify an auditor manifest against local JSONL.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--source-root", type=Path, default=Path.home() / ".codex")
    args = parser.parse_args()
    errors = verify(args.manifest, args.source_root)
    if errors:
        shown = errors[:20]
        print("\n".join(shown))
        if len(errors) > len(shown):
            print(f"... {len(errors) - len(shown)} additional verification errors omitted")
        print(f"manifest verification failed: {len(errors)} evidence records affected")
        return 1
    print("manifest verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
