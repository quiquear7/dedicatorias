from __future__ import annotations

import io
import logging

from core.ai_retry import ContentBlockedError, TransientAIError, with_retry
from core.config import get_config, get_gemini_client, get_openai_client

logger = logging.getLogger(__name__)


GEMINI_AUDIO_MODEL = "gemini-2.5-flash"


def _on_retry_toast_transcribe(attempt: int, exc: BaseException, delay: float) -> None:  # noqa: ARG001
    try:
        import streamlit as st

        st.toast(
            f"⏳ Transcripción: la IA está saturada, reintentando en {delay:.1f}s "
            f"(intento {attempt})…"
        )
    except Exception:  # noqa: BLE001
        pass

_TRANSCRIPTION_PROMPT = (
    "Transcribe el siguiente audio en español. "
    "Devuelve únicamente la transcripción literal, sin comentarios, "
    "sin marcas de tiempo, sin etiquetas."
)


def transcribe(audio_bytes: bytes, *, language: str = "es", filename: str = "audio.webm") -> str:
    if not audio_bytes:
        raise ValueError("audio_bytes está vacío.")
    cfg = get_config()
    if cfg.ai_provider == "gemini":
        return with_retry(
            _transcribe_gemini, audio_bytes, filename, on_retry=_on_retry_toast_transcribe
        )
    return with_retry(
        _transcribe_openai,
        audio_bytes,
        language,
        filename,
        on_retry=_on_retry_toast_transcribe,
    )


def _transcribe_openai(audio_bytes: bytes, language: str, filename: str) -> str:
    client = get_openai_client()
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = filename
    response = client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
        language=language,
        response_format="text",
    )
    if isinstance(response, str):
        return response.strip()
    return getattr(response, "text", str(response)).strip()


def _guess_mime(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".webm"):
        return "audio/webm"
    if name.endswith(".wav"):
        return "audio/wav"
    if name.endswith(".mp3"):
        return "audio/mp3"
    if name.endswith(".ogg"):
        return "audio/ogg"
    if name.endswith(".m4a") or name.endswith(".mp4"):
        return "audio/mp4"
    if name.endswith(".flac"):
        return "audio/flac"
    return "audio/webm"


def _build_gemini_config(types_module, *, system_instruction=None, temperature: float = 0.2):
    """Configuración común para Gemini 2.5 Flash: thinking desactivado para
    estos casos cortos (transcribir/corregir) evita que el modelo gaste su
    presupuesto de tokens "pensando" y devuelva una respuesta vacía.
    """
    kwargs = {"temperature": temperature}
    if system_instruction is not None:
        kwargs["system_instruction"] = system_instruction
    try:
        kwargs["thinking_config"] = types_module.ThinkingConfig(thinking_budget=0)
    except Exception:  # noqa: BLE001
        # SDK antiguo sin ThinkingConfig: no pasa nada, sigue sin la opción.
        pass
    return types_module.GenerateContentConfig(**kwargs)


def _diagnose_empty_response(response, context: str) -> None:
    """Inspecciona una respuesta de Gemini con `.text` vacío y lanza la
    excepción apropiada (transitoria o de bloqueo) con info útil.
    """
    candidates = getattr(response, "candidates", None) or []
    finish_reason = None
    if candidates:
        finish_reason = getattr(candidates[0], "finish_reason", None)
    prompt_feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(prompt_feedback, "block_reason", None) if prompt_feedback else None

    logger.warning(
        "%s: respuesta vacía (finish_reason=%s, block_reason=%s)",
        context,
        finish_reason,
        block_reason,
    )

    if block_reason:
        raise ContentBlockedError(f"{context}: {block_reason}")
    # Cualquier otro caso (vacío, MAX_TOKENS, SAFETY sin block, …) lo tratamos
    # como transitorio: la siguiente llamada normalmente devuelve algo.
    detail = f"finish_reason={finish_reason}" if finish_reason else "respuesta vacía"
    raise TransientAIError(f"{context}: {detail}")


def _transcribe_gemini(audio_bytes: bytes, filename: str) -> str:
    from google.genai import types

    client = get_gemini_client()
    mime = _guess_mime(filename)
    response = client.models.generate_content(
        model=GEMINI_AUDIO_MODEL,
        contents=[
            _TRANSCRIPTION_PROMPT,
            types.Part.from_bytes(data=audio_bytes, mime_type=mime),
        ],
        config=_build_gemini_config(types, temperature=0.0),
    )
    text = getattr(response, "text", None)
    if text and text.strip():
        return text.strip()
    _diagnose_empty_response(response, "Transcripción de audio")
    return ""  # inalcanzable, _diagnose_empty_response siempre lanza
