"""Build a standalone, distributable skill zip.

The repository is a single distribution unit: it is not installed into
.workbuddy/skills and does not auto-load. Ship the zip and install it explicitly.
"""

from __future__ import annotations

import fnmatch
import zipfile
from pathlib import Path
from typing import Optional

_SKIP_DIRS = {".git", "data", "out", "dist", "__pycache__", ".venv", "venv", "htmlcov"}
_SKIP_FILES = (".DS_Store", "*.pyc", "*.pyo", ".env", "*.tmp", "*.zip", "*.key", "*.pem")


def _excluded(relative: str) -> bool:
    parts = relative.split("/")
    if any(part in _SKIP_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    return any(fnmatch.fnmatch(name, pattern) for pattern in _SKIP_FILES)


def build_package(root: str | Path, out_dir: Optional[str | Path] = None) -> Path:
    source = Path(root).resolve()
    if not (source / "SKILL.md").is_file():
        raise FileNotFoundError("SKILL.md is required before packaging")
    destination = Path(out_dir).resolve() if out_dir else source / "dist"
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / (source.name + ".zip")

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            if _excluded(relative):
                continue
            archive.write(path, source.name + "/" + relative)
    return target
