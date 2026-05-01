from __future__ import annotations

import io

from core.config import get_config, get_gemini_client, get_openai_client


GEMINI_AUDIO_MODEL = "gemini-2.5-flash"

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
        return _transcribe_gemini(audio_bytes, filename)
    return _transcribe_openai(audio_bytes, language, filename)


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
    )
    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError("Gemini no devolvió texto en la transcripción.")
    return text.strip()
