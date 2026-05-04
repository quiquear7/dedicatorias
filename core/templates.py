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
    back_bytes: Optional[bytes] = None,
    back_extension: Optional[str] = None,
    back_type: Optional[str] = None,
) -> Template:
    if not name.strip():
        raise ValueError("El nombre de la plantilla es obligatorio.")
    template_id = str(uuid.uuid4())
    ext = source_extension.lstrip(".").lower() or ("pdf" if source_type == "pdf" else "png")
    source_path = f"templates/{template_id}/source.{ext}"

    back_source_path: Optional[str] = None
    back_source_type: Optional[str] = None
    if back_bytes:
        bext = (back_extension or "").lstrip(".").lower() or ("pdf" if back_type == "pdf" else "png")
        back_source_path = f"templates/{template_id}/back.{bext}"
        back_source_type = back_type or "image"

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
        back_source_path=back_source_path,
        back_source_type=back_source_type,  # type: ignore[arg-type]
    )
    saved = save_template(template, source_bytes=source_bytes)
    if back_bytes and back_source_path:
        get_storage().put(back_source_path, back_bytes)
    return saved


def get_back_source_bytes(template: Template) -> Optional[Tuple[bytes, str]]:
    if not template.has_back or not template.back_source_path or not template.back_source_type:
        return None
    storage = get_storage()
    return storage.get(template.back_source_path), template.back_source_type


def rename_template(template_id: str, new_name: str) -> Template:
    template = get_template(template_id)
    if not template:
        raise KeyError(f"Plantilla no encontrada: {template_id}")
    if not new_name.strip():
        raise ValueError("El nombre no puede estar vacío.")
    template.name = new_name.strip()
    return save_template(template)


def set_template_back(
    template_id: str,
    back_bytes: bytes,
    back_extension: str,
    back_type: str,
) -> Template:
    """Añade o reemplaza la imagen de reverso de una plantilla."""
    template = get_template(template_id)
    if not template:
        raise KeyError(f"Plantilla no encontrada: {template_id}")
    storage = get_storage()

    # Si ya había un reverso anterior, limpiarlo si va a cambiar de extensión.
    if template.back_source_path:
        try:
            storage.delete(template.back_source_path)
        except Exception:
            pass

    bext = back_extension.lstrip(".").lower() or ("pdf" if back_type == "pdf" else "png")
    back_path = f"templates/{template_id}/back.{bext}"
    storage.put(back_path, back_bytes)

    template.back_source_path = back_path
    template.back_source_type = back_type  # type: ignore[assignment]
    return save_template(template)


def clear_template_back(template_id: str) -> Template:
    template = get_template(template_id)
    if not template:
        raise KeyError(f"Plantilla no encontrada: {template_id}")
    if template.back_source_path:
        storage = get_storage()
        try:
            storage.delete(template.back_source_path)
        except Exception:
            pass
    template.back_source_path = None
    template.back_source_type = None
    return save_template(template)


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
