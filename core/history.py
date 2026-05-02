from __future__ import annotations

import json
import uuid
from typing import Dict, List, Optional

from core.config import get_storage
from core.models import Dedication, Template

INDEX_PATH = "history/_index.json"


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


def list_dedications() -> List[Dedication]:
    index = _load_index()
    items = [Dedication.from_dict(item) for item in index.values()]
    items.sort(key=lambda d: d.created_at, reverse=True)
    return items


def get_dedication(dedication_id: str) -> Optional[Dedication]:
    index = _load_index()
    raw = index.get(dedication_id)
    return Dedication.from_dict(raw) if raw else None


def save_generated(
    *,
    template: Template,
    recipient_name: str,
    recipient_group: str,
    contact_id: Optional[str],
    input_mode: str,
    raw_input: str,
    corrected_text: str,
    final_text: str,
    pdf_bytes: bytes,
    png_bytes: bytes,
    audio_bytes: Optional[bytes] = None,
    audio_extension: str = "webm",
    is_generic: bool = False,
    tags: Optional[List[str]] = None,
) -> Dedication:
    storage = get_storage()
    dedication_id = str(uuid.uuid4())
    base = f"history/{dedication_id}"
    pdf_path = f"{base}/card.pdf"
    png_path = f"{base}/card.png"
    audio_path: Optional[str] = None

    storage.put(pdf_path, pdf_bytes)
    storage.put(png_path, png_bytes)
    if audio_bytes:
        audio_path = f"{base}/audio.{audio_extension.lstrip('.').lower() or 'webm'}"
        storage.put(audio_path, audio_bytes)

    dedication = Dedication(
        id=dedication_id,
        recipient_name=recipient_name,
        recipient_group=recipient_group,
        input_mode=input_mode,  # type: ignore[arg-type]
        raw_input=raw_input,
        corrected_text=corrected_text,
        final_text=final_text,
        template_id=template.id,
        template_snapshot=template.to_dict(),
        card_pdf_path=pdf_path,
        card_png_path=png_path,
        contact_id=contact_id,
        audio_path=audio_path,
        is_generic=is_generic,
        tags=list(tags or []),
    )
    index = _load_index()
    index[dedication_id] = dedication.to_dict()
    _save_index(index)

    # Snapshot automático silencioso (no bloquea si falla).
    try:
        from core.backup import auto_snapshot_if_needed

        auto_snapshot_if_needed()
    except Exception:
        pass

    return dedication


def update_dedication(dedication: Dedication) -> Dedication:
    index = _load_index()
    if dedication.id not in index:
        raise KeyError(f"Dedicatoria no encontrada: {dedication.id}")
    index[dedication.id] = dedication.to_dict()
    _save_index(index)
    return dedication


def delete_dedication(dedication_id: str) -> bool:
    storage = get_storage()
    index = _load_index()
    if dedication_id not in index:
        return False
    storage.delete(f"history/{dedication_id}")
    del index[dedication_id]
    _save_index(index)
    return True


def count_for_contact(contact_id: str) -> int:
    return sum(1 for d in list_dedications() if d.contact_id == contact_id)
