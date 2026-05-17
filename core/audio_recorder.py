from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Optional

import streamlit.components.v1 as components

_COMPONENT_NAME = "dedicatorias_audio_recorder"
_FRONTEND_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "components",
    "audio_recorder",
    "frontend",
)

_component_func = components.declare_component(_COMPONENT_NAME, path=_FRONTEND_DIR)


@dataclass(frozen=True)
class Recording:
    audio_bytes: bytes
    mime: str
    filename: str
    size: int
    duration_ms: int
    rev: int


def audio_recorder(*, key: Optional[str] = None) -> Optional[Recording]:
    """Renderiza el grabador custom con pausa/reanudar/reset.

    Devuelve una Recording cuando hay audio grabado, None si todavía no hay nada
    o si el usuario hizo reset.
    """
    payload = _component_func(key=key, default=None)
    if not payload or not isinstance(payload, dict):
        return None
    b64 = payload.get("audio_b64")
    if not b64:
        return None
    try:
        audio_bytes = base64.b64decode(b64)
    except Exception:
        return None
    if not audio_bytes:
        return None
    mime = (payload.get("mime") or "audio/webm").split(";")[0]
    ext = payload.get("ext") or "webm"
    return Recording(
        audio_bytes=audio_bytes,
        mime=mime,
        filename=f"audio.{ext}",
        size=int(payload.get("size") or len(audio_bytes)),
        duration_ms=int(payload.get("duration_ms") or 0),
        rev=int(payload.get("rev") or 0),
    )