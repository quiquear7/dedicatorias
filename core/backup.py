from __future__ import annotations

import io
import zipfile
from datetime import datetime
from typing import Iterable

from core.config import get_storage

BACKUP_PREFIXES = ("templates/", "contacts/", "history/")


def _all_paths() -> list[str]:
    storage = get_storage()
    paths: list[str] = []
    for prefix in BACKUP_PREFIXES:
        paths.extend(storage.list(prefix))
    # Excluir nuestros temporales internos por si los hubiera
    return [p for p in paths if not p.startswith("health/")]


def storage_stats() -> dict:
    storage = get_storage()
    paths = _all_paths()
    total_bytes = 0
    by_section = {"templates": 0, "contacts": 0, "history": 0}
    counts = {"templates": 0, "contacts": 0, "history": 0}
    for path in paths:
        try:
            data = storage.get(path)
            size = len(data)
        except Exception:
            size = 0
        total_bytes += size
        for section in by_section:
            if path.startswith(f"{section}/"):
                by_section[section] += size
                if path.endswith("meta.json") or path.endswith("_index.json"):
                    counts[section] += 1
                break
    return {
        "total_bytes": total_bytes,
        "total_files": len(paths),
        "by_section_bytes": by_section,
        "indices_count": counts,
    }


def create_backup_zip() -> bytes:
    storage = get_storage()
    paths = _all_paths()
    buf = io.BytesIO()
    timestamp = datetime.now().isoformat(timespec="seconds")
    manifest_lines = [f"# Backup dedicatorias", f"# Generado: {timestamp}", ""]
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(paths):
            try:
                data = storage.get(path)
            except Exception as e:  # noqa: BLE001
                manifest_lines.append(f"# ERROR leyendo {path}: {e}")
                continue
            zf.writestr(path, data)
            manifest_lines.append(f"{len(data):>10} {path}")
        zf.writestr("MANIFEST.txt", "\n".join(manifest_lines))
    return buf.getvalue()


def restore_from_zip(zip_bytes: bytes, *, overwrite: bool = True) -> dict:
    """Restaura un backup. Devuelve un resumen {restored: int, skipped: int, errors: list}."""
    storage = get_storage()
    summary = {"restored": 0, "skipped": 0, "errors": []}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for member in zf.namelist():
            if member == "MANIFEST.txt":
                continue
            if member.endswith("/"):
                continue
            if not any(member.startswith(p) for p in BACKUP_PREFIXES):
                summary["errors"].append(f"Rechazado (fuera de prefijos válidos): {member}")
                continue
            if not overwrite and storage.exists(member):
                summary["skipped"] += 1
                continue
            try:
                storage.put(member, zf.read(member))
                summary["restored"] += 1
            except Exception as e:  # noqa: BLE001
                summary["errors"].append(f"{member}: {e}")
    return summary


def human_size(num_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"
