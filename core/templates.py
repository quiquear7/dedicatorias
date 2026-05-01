from __future__ import annotations

import json
import uuid
from typing import Dict, List, Optional, Tuple

from core.config import get_storage
from core.models import Template

INDEX_PATH = "templates/_index.json"


def _load_index() -> Dict[str, dict]:
    storage = get_storage()
    if not storage.exists(INDEX_PATH):
        return {}
    raw = storage.get(INDEX_PATH).decode("utf-8")
    if not raw.strip():
        return {}
    return json.loads(raw)


def _save_index(index: Dict[str, dict]) -> None:
    storage = get_storage()
    payload = json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8")
    storage.put(INDEX_PATH, payload)


def list_templates() -> List[Template]:
    index = _load_index()
    templates = [Template.from_dict(item) for item in index.values()]
    templates.sort(key=lambda t: t.created_at, reverse=True)
    return templates


def get_template(template_id: str) -> Optional[Template]:
    index = _load_index()
    raw = index.get(template_id)
    return Template.from_dict(raw) if raw else None


def save_template(template: Template, source_bytes: Optional[bytes] = None) -> Template:
    storage = get_storage()
    if source_bytes is not None:
        storage.put(template.source_path, source_bytes)
    index = _load_index()
    index[template.id] = template.to_dict()
    _save_index(index)
    return template


def create_template(
    name: str,
    source_bytes: bytes,
    source_extension: str,
    source_type: str,
    width_mm: float,
    height_mm: float,
    text_zone,
    text_style,
    name_zone=None,
    name_style=None,
) -> Template:
    if not name.strip():
        raise ValueError("El nombre de la plantilla es obligatorio.")
    template_id = str(uuid.uuid4())
    ext = source_extension.lstrip(".").lower() or ("pdf" if source_type == "pdf" else "png")
    source_path = f"templates/{template_id}/source.{ext}"
    template = Template(
        id=template_id,
        name=name.strip(),
        source_path=source_path,
        source_type=source_type,
        width_mm=float(width_mm),
        height_mm=float(height_mm),
        text_zone=text_zone,
        text_style=text_style,
        name_zone=name_zone,
        name_style=name_style,
    )
    return save_template(template, source_bytes=source_bytes)


def delete_template(template_id: str) -> bool:
    storage = get_storage()
    index = _load_index()
    if template_id not in index:
        return False
    storage.delete(f"templates/{template_id}")
    del index[template_id]
    _save_index(index)
    return True


def get_source_bytes(template: Template) -> Tuple[bytes, str]:
    storage = get_storage()
    return storage.get(template.source_path), template.source_type
