from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Tuple

from core.config import get_storage

BACKUP_PREFIXES = ("templates/", "contacts/", "history/")
SNAPSHOT_PREFIX = "backups/"
SNAPSHOT_MARKER = SNAPSHOT_PREFIX + "_last_snapshot.txt"
SNAPSHOT_PATTERN = re.compile(r"^backups/snapshot-(\d{8}-\d{6})\.zip$")
DEFAULT_MIN_HOURS = 24.0
DEFAULT_MAX_KEEP = 14


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


def list_snapshots() -> List[Tuple[str, datetime, int]]:
    """Devuelve una lista de (path, fecha_creacion_utc, tamano_bytes) ordenada por fecha desc."""
    storage = get_storage()
    paths = storage.list(SNAPSHOT_PREFIX)
    snapshots: List[Tuple[str, datetime, int]] = []
    for path in paths:
        match = SNAPSHOT_PATTERN.match(path)
        if not match:
            continue
        try:
            ts = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        try:
            size = len(storage.get(path))
        except Exception:
            size = 0
        snapshots.append((path, ts, size))
    snapshots.sort(key=lambda s: s[1], reverse=True)
    return snapshots


def _read_marker() -> Optional[datetime]:
    storage = get_storage()
    if not storage.exists(SNAPSHOT_MARKER):
        return None
    try:
        raw = storage.get(SNAPSHOT_MARKER).decode("utf-8").strip()
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def auto_snapshot_if_needed(
    min_hours: float = DEFAULT_MIN_HOURS,
    max_keep: int = DEFAULT_MAX_KEEP,
) -> Optional[str]:
    """Crea un snapshot dentro del bucket si han pasado más de `min_hours` desde el último.
    Devuelve la ruta del snapshot creado o None si no hizo falta."""
    storage = get_storage()
    now = datetime.now(timezone.utc)

    last = _read_marker()
    if last is not None and (now - last) < timedelta(hours=min_hours):
        return None

    paths = [p for p in _all_paths() if not p.startswith(SNAPSHOT_PREFIX)]
    if not paths:
        # Aunque no haya datos, marcamos para no reintentar continuamente.
        storage.put(SNAPSHOT_MARKER, now.isoformat(timespec="seconds").encode("utf-8"))
        return None

    ts_str = now.strftime("%Y%m%d-%H%M%S")
    snap_path = f"{SNAPSHOT_PREFIX}snapshot-{ts_str}.zip"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(paths):
            try:
                zf.writestr(path, storage.get(path))
            except Exception:
                continue
        zf.writestr(
            "MANIFEST.txt",
            f"Snapshot automático\nCreado: {now.isoformat(timespec='seconds')}\nArchivos: {len(paths)}\n",
        )

    storage.put(snap_path, buf.getvalue())
    storage.put(SNAPSHOT_MARKER, now.isoformat(timespec="seconds").encode("utf-8"))

    # Podar antiguos
    snapshots = list_snapshots()
    if len(snapshots) > max_keep:
        for old_path, _, _ in snapshots[max_keep:]:
            try:
                storage.delete(old_path)
            except Exception:
                continue

    return snap_path


def restore_snapshot(snapshot_path: str, *, overwrite: bool = True) -> dict:
    """Restaura desde un snapshot guardado en el propio bucket."""
    storage = get_storage()
    data = storage.get(snapshot_path)
    return restore_from_zip(data, overwrite=overwrite)


def delete_snapshot(snapshot_path: str) -> bool:
    storage = get_storage()
    if not SNAPSHOT_PATTERN.match(snapshot_path):
        return False
    if not storage.exists(snapshot_path):
        return False
    storage.delete(snapshot_path)
    return True
