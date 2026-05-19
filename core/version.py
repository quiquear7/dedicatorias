"""Información de versión de la app.

Mantener `__version__` actualizado a mano siguiendo SemVer (MAJOR.MINOR.PATCH).
La info de commit se lee automáticamente de `.git/` si está disponible.
"""
from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

__version__ = "0.3.0"

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@dataclass(frozen=True)
class BuildInfo:
    version: str
    commit_short: Optional[str]
    commit_date: Optional[str]  # ISO yyyy-mm-dd en UTC

    @property
    def display(self) -> str:
        parts = [f"v{self.version}"]
        suffix = []
        if self.commit_short:
            suffix.append(self.commit_short)
        if self.commit_date:
            suffix.append(self.commit_date)
        if suffix:
            parts.append(f"({' · '.join(suffix)})")
        return " ".join(parts)


def _read_first_line(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.readline().strip()
    except OSError:
        return None


def _resolve_head_sha(git_dir: str) -> Optional[str]:
    head = _read_first_line(os.path.join(git_dir, "HEAD"))
    if not head:
        return None
    if head.startswith("ref:"):
        ref = head.split(" ", 1)[1].strip()
        # primero archivo de ref suelto
        ref_path = os.path.join(git_dir, ref)
        sha = _read_first_line(ref_path)
        if sha:
            return sha
        # fallback a packed-refs
        packed = os.path.join(git_dir, "packed-refs")
        try:
            with open(packed, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("^"):
                        continue
                    parts = line.split(" ", 1)
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0]
        except OSError:
            return None
        return None
    return head  # HEAD desacoplado: ya es el SHA


@lru_cache(maxsize=1)
def get_build_info() -> BuildInfo:
    git_dir = os.path.join(_REPO_ROOT, ".git")
    commit_short: Optional[str] = None
    commit_date: Optional[str] = None

    if os.path.isdir(git_dir):
        sha = _resolve_head_sha(git_dir)
        if sha:
            commit_short = sha[:7]
            # fecha = mtime del archivo de ref (aprox) o del propio HEAD
            ref_path = None
            head = _read_first_line(os.path.join(git_dir, "HEAD")) or ""
            if head.startswith("ref:"):
                ref_path = os.path.join(git_dir, head.split(" ", 1)[1].strip())
            candidates = [p for p in [ref_path, os.path.join(git_dir, "HEAD")] if p and os.path.exists(p)]
            if candidates:
                try:
                    ts = max(os.path.getmtime(p) for p in candidates)
                    commit_date = _dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                except OSError:
                    pass

    return BuildInfo(version=__version__, commit_short=commit_short, commit_date=commit_date)
