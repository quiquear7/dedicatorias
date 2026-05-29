from __future__ import annotations

import json
import uuid
from typing import Dict, List, Optional

from core.config import get_storage
from core.models import Dedication, Template, now_iso

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
    back_png_bytes: Optional[bytes] = None,
    extra_png_bytes: Optional[List[bytes]] = None,
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
    back_png_path: Optional[str] = None
    audio_path: Optional[str] = None
    extra_paths: List[str] = []

    storage.put(pdf_path, pdf_bytes)
    storage.put(png_path, png_bytes)
    if back_png_bytes:
        back_png_path = f"{base}/card_back.png"
        storage.put(back_png_path, back_png_bytes)
    if extra_png_bytes:
        for idx, part_bytes in enumerate(extra_png_bytes, start=2):
            part_path = f"{base}/card_part_{idx}.png"
            storage.put(part_path, part_bytes)
            extra_paths.append(part_path)
    if audio_bytes:
        audio_path = f"{base}/audio.{audio_extension.lstrip('.').lower() or 'webm'}"
        storage.put(audio_path, audio_bytes)

    now = now_iso()
    dedication = Dedication(
        id=dedication_id,
        recipient_name=recipient_name,
        recipient_group=recipient_group,
        input_mode=input_mode,  # type: ignore[arg-type]
        raw_input=raw_input,
        corrected_text=corrected_text,
        final_text=final_text,
        status="rendered",
        template_id=template.id,
        template_snapshot=template.to_dict(),
        card_pdf_path=pdf_path,
        card_png_path=png_path,
        card_back_png_path=back_png_path,
        card_extra_png_paths=extra_paths,
        contact_id=contact_id,
        audio_path=audio_path,
        is_generic=is_generic,
        tags=list(tags or []),
        rendered_at=now,
    )
    index = _load_index()
    index[dedication_id] = dedication.to_dict()
    _save_index(index)

    _trigger_auto_snapshot()
    return dedication


def save_pending(
    *,
    recipient_name: str,
    recipient_group: str,
    contact_id: Optional[str],
    input_mode: str,
    raw_input: str,
    corrected_text: str,
    final_text: str,
    audio_bytes: Optional[bytes] = None,
    audio_extension: str = "webm",
    is_generic: bool = False,
    tags: Optional[List[str]] = None,
) -> Dedication:
    """Guarda una dedicatoria sin renderizar. Quedará pendiente hasta que se llame a render_pending()."""
    storage = get_storage()
    dedication_id = str(uuid.uuid4())
    base = f"history/{dedication_id}"
    audio_path: Optional[str] = None
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
        status="pending",
        contact_id=contact_id,
        audio_path=audio_path,
        is_generic=is_generic,
        tags=list(tags or []),
    )
    index = _load_index()
    index[dedication_id] = dedication.to_dict()
    _save_index(index)

    _trigger_auto_snapshot()
    return dedication


def render_pending(dedication_id: str, template: Template) -> Dedication:
    """Renderiza una dedicatoria pendiente con la plantilla indicada y la marca como 'rendered'.

    Si el texto no cabe entero en la zona de la plantilla (ni siquiera al
    tamaño mínimo), se parte en varias tarjetas. El PDF resultante contiene
    todas las partes intercaladas con el reverso; los PNG van como
    `card.png` (parte 1) y `card_part_2.png`, `card_part_3.png`, … para las
    siguientes.
    """
    from core.rendering import render_dedication_parts

    dedication = get_dedication(dedication_id)
    if dedication is None:
        raise KeyError(f"Dedicatoria no encontrada: {dedication_id}")
    if dedication.status == "rendered":
        # Idempotente: si ya está renderizada con esta plantilla, no hacemos nada.
        if dedication.template_id == template.id:
            return dedication
        # Si quiere renderizarse con otra plantilla, regeneramos los archivos.

    storage = get_storage()
    base = f"history/{dedication_id}"
    pdf_path = f"{base}/card.pdf"
    png_path = f"{base}/card.png"
    back_png_path: Optional[str] = None

    result = render_dedication_parts(
        template,
        dedication.recipient_name,
        dedication.final_text,
        include_back=template.has_back,
    )
    fronts: List[bytes] = result["fronts"]
    back_bytes = result["back"]
    pdf_bytes = result["pdf"]

    storage.put(pdf_path, pdf_bytes)
    storage.put(png_path, fronts[0])
    if back_bytes:
        back_png_path = f"{base}/card_back.png"
        storage.put(back_png_path, back_bytes)
    elif dedication.card_back_png_path:
        # Limpiar reverso anterior si la nueva plantilla no tiene reverso.
        try:
            storage.delete(dedication.card_back_png_path)
        except Exception:
            pass

    # Limpia extras antiguos (de una versión anterior partida con N partes
    # distinto) antes de reescribir los nuevos.
    for old_path in dedication.card_extra_png_paths or []:
        try:
            storage.delete(old_path)
        except Exception:
            pass
    new_extra_paths: List[str] = []
    for idx, part_bytes in enumerate(fronts[1:], start=2):
        part_path = f"{base}/card_part_{idx}.png"
        storage.put(part_path, part_bytes)
        new_extra_paths.append(part_path)

    dedication.status = "rendered"
    dedication.template_id = template.id
    dedication.template_snapshot = template.to_dict()
    dedication.card_pdf_path = pdf_path
    dedication.card_png_path = png_path
    dedication.card_back_png_path = back_png_path
    dedication.card_extra_png_paths = new_extra_paths
    dedication.rendered_at = now_iso()

    index = _load_index()
    index[dedication_id] = dedication.to_dict()
    _save_index(index)
    return dedication


def render_pending_bulk(dedication_ids: List[str], template: Template) -> Dict[str, object]:
    """Renderiza varias dedicatorias en lote. Devuelve resumen {ok, errors}."""
    summary: Dict[str, object] = {"ok": [], "errors": []}
    for did in dedication_ids:
        try:
            render_pending(did, template)
            summary["ok"].append(did)  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001
            summary["errors"].append({"id": did, "error": str(e)})  # type: ignore[union-attr]
    _trigger_auto_snapshot()
    return summary


def list_pending() -> List[Dedication]:
    return [d for d in list_dedications() if d.is_pending]


def list_rendered() -> List[Dedication]:
    return [d for d in list_dedications() if not d.is_pending]


def _trigger_auto_snapshot() -> None:
    try:
        from core.backup import auto_snapshot_if_needed

        auto_snapshot_if_needed()
    except Exception:
        pass


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


def unrender_dedication(dedication_id: str) -> Optional[Dedication]:
    """Borra solo los archivos renderizados (PDF/PNG/reverso) y devuelve la
    dedicatoria al estado `pending`, conservando el texto, el audio y la
    referencia al destinatario. Permite re-renderizar con otra plantilla sin
    perder la dedicatoria.
    """
    storage = get_storage()
    dedication = get_dedication(dedication_id)
    if dedication is None:
        return None

    for path in (
        dedication.card_pdf_path,
        dedication.card_png_path,
        dedication.card_back_png_path,
        *(dedication.card_extra_png_paths or []),
    ):
        if path:
            try:
                storage.delete(path)
            except Exception:
                pass

    dedication.status = "pending"
    dedication.template_id = None
    dedication.template_snapshot = None
    dedication.card_pdf_path = None
    dedication.card_png_path = None
    dedication.card_back_png_path = None
    dedication.card_extra_png_paths = []
    dedication.rendered_at = None

    index = _load_index()
    index[dedication.id] = dedication.to_dict()
    _save_index(index)
    return dedication


def list_using_template(template_id: str) -> List[Dedication]:
    """Devuelve las dedicatorias que están enlazadas a una plantilla dada."""
    return [d for d in list_dedications() if d.template_id == template_id]


def count_for_contact(contact_id: str) -> int:
    return sum(1 for d in list_dedications() if d.contact_id == contact_id)
