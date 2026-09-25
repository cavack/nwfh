#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

CANONICAL_DOCS = {
    "docs/ARCHITECTURE.md", "docs/MODEL.md", "docs/DECISION_ENGINE.md",
    "docs/DASHBOARD.md", "docs/DATA_AND_DATABASE.md", "docs/OPERATIONS.md",
    "docs/DEPLOYMENT.md", "docs/BACKUP_RESTORE.md", "docs/TELEGRAM.md",
    "docs/AI_ADVISORY.md", "docs/TROUBLESHOOTING.md",
    "docs/DEVELOPER_ONBOARDING.md", "docs/PROJECT_HANDOFF.md",
}
WORKFLOW_ALLOWLIST = {"ci.yml", "deploy-production.yml"}
FORBIDDEN_PARTS = {".venv", "venv", "node_modules", ".next", ".pytest_cache", "__pycache__", ".work", "backup", "backups"}
FORBIDDEN_SUFFIXES = (".pyc", ".log", ".db", ".sqlite", ".sqlite3", ".tsbuildinfo", ".bak")
CONFLICT = re.compile(r"^(?:<<<<<<< [^\r\n]*|=======|>>>>>>> [^\r\n]*)\r?$", re.MULTILINE)


SOURCE_MANIFEST = ".wfh-source-manifest"


def tracked(root: Path) -> list[str]:
    git = shutil.which("git")
    if git is not None and (root / ".git").exists():
        out = subprocess.check_output([git, "ls-files"], cwd=root, text=True)
        return [line.strip() for line in out.splitlines() if line.strip()]
    manifest = root / SOURCE_MANIFEST
    if not manifest.is_file():
        raise RuntimeError("tracked source manifest unavailable")
    return [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]


def _required_path_errors(paths: list[str]) -> list[str]:
    errors: list[str] = []
    missing = sorted(CANONICAL_DOCS - set(paths))
    if missing:
        errors.append("missing canonical docs: " + ", ".join(missing))
    required = {"README.md", "CHANGELOG.md", "Makefile", ".env.example"}
    missing_root = sorted(required - set(paths))
    if missing_root:
        errors.append("missing root handoff files: " + ", ".join(missing_root))
    return errors


def _debris_errors(paths: list[str]) -> list[str]:
    debris = [
        path for path in paths
        if FORBIDDEN_PARTS.intersection(Path(path).parts)
        or path.endswith(FORBIDDEN_SUFFIXES)
    ]
    if not debris:
        return []
    return ["tracked runtime/generated debris: " + ", ".join(sorted(debris))]


def _workflow_errors(paths: list[str]) -> list[str]:
    workflows = {Path(path).name for path in paths if path.startswith(".github/workflows/")}
    if workflows == WORKFLOW_ALLOWLIST:
        return []
    return [f"workflow set must be {sorted(WORKFLOW_ALLOWLIST)}, got {sorted(workflows)}"]


def _conflict_marker_errors(root: Path, paths: list[str]) -> list[str]:
    text_candidates = [
        path for path in paths
        if Path(path).suffix.lower() in {".md", ".py", ".ts", ".tsx", ".js", ".yml", ".yaml", ".txt", ""}
    ]
    errors: list[str] = []
    for rel in text_candidates:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if CONFLICT.search(text):
            errors.append(f"conflict marker: {rel}")
    return errors


def _terminology_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for rel in ["README.md", *sorted(CANONICAL_DOCS)]:
        path = root / rel
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if "PAPER_ONLY" in text:
            errors.append(f"deprecated PAPER_ONLY terminology in canonical surface: {rel}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate canonical WaterfallHunter repository hygiene.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    paths = tracked(root)
    errors = [
        *_required_path_errors(paths),
        *_debris_errors(paths),
        *_workflow_errors(paths),
        *_conflict_marker_errors(root, paths),
        *_terminology_errors(root),
    ]
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print(f"repository_hygiene=PASS tracked_files={len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
