from __future__ import annotations

from typing import Iterable, List, Optional

from core.ai_retry import with_retry
from core.config import get_config, get_gemini_client, get_openai_client

OPENAI_CORRECTION_MODEL = "gpt-4o-mini"
GEMINI_CORRECTION_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = (
    "Eres un editor que corrige dedicatorias de tarjetas en español. "
    "Tu trabajo es arreglar ortografía, gramática, puntuación y mayúsculas, "
    "sin alterar el contenido emocional, el tono, ni las palabras del autor. "
    "No reescribas, no añadas frases que no estén, no cambies expresiones coloquiales si las hubiera. "
    "Mantén los saltos de línea originales. Devuelve únicamente la dedicatoria corregida, "
    "sin comillas, sin comentarios, sin etiquetas."
)

REFINE_SYSTEM_PROMPT = (
    "Eres un editor que ayuda a refinar dedicatorias de tarjetas en español según las instrucciones del usuario. "
    "Recibirás el texto actual y unas instrucciones específicas. Aplica las instrucciones manteniendo el sentido y "
    "el espíritu de la dedicatoria. No añadas comillas, no expliques los cambios, no añadas comentarios — devuelve "
    "ÚNICAMENTE el texto refinado, listo para imprimir en la tarjeta."
)

REWRITE_FRAGMENT_SYSTEM_PROMPT = (
    "Eres un editor que reescribe fragmentos de una dedicatoria en español según una instrucción del usuario. "
    "Recibirás SOLO un fragmento (una o varias frases) y unas instrucciones. Devuelve únicamente el fragmento "
    "reescrito, sin comillas, sin explicaciones, sin etiquetas, sin añadir frases nuevas que no estén implícitas "
    "en la instrucción. Mantén el tono general (cariñoso, formal, etc.) salvo que la instrucción pida lo contrario."
)


def _on_retry_toast(prefix: str):
    """Devuelve un callback que muestra un toast en Streamlit cuando reintentamos."""

    def _cb(attempt: int, exc: BaseException, delay: float) -> None:  # noqa: ARG001
        try:
            import streamlit as st

            st.toast(
                f"⏳ {prefix}: la IA está saturada, reintentando en {delay:.1f}s "
                f"(intento {attempt})…"
            )
        except Exception:  # noqa: BLE001
            pass

    return _cb


def correct_dedication(raw_text: str) -> str:
    if not raw_text or not raw_text.strip():
        return ""
    cfg = get_config()
    if cfg.ai_provider == "gemini":
        return with_retry(_correct_gemini, raw_text, on_retry=_on_retry_toast("Corrección IA"))
    return with_retry(_correct_openai, raw_text, on_retry=_on_retry_toast("Corrección IA"))


def _correct_openai(raw_text: str) -> str:
    client = get_openai_client()
    response = client.chat.completions.create(
        model=OPENAI_CORRECTION_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": raw_text.strip()},
        ],
        temperature=0.2,
    )
    text = response.choices[0].message.content or ""
    return text.strip()


def _correct_gemini(raw_text: str) -> str:
    from google.genai import types

    client = get_gemini_client()
    response = client.models.generate_content(
        model=GEMINI_CORRECTION_MODEL,
        contents=raw_text.strip(),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
        ),
    )
    text = getattr(response, "text", None) or ""
    return text.strip()


def refine_text(current_text: str, instruction: str) -> str:
    """Aplica una instrucción libre del usuario sobre el texto actual."""
    if not current_text.strip():
        return ""
    instruction = (instruction or "").strip()
    if not instruction:
        return current_text.strip()
    cfg = get_config()
    user_message = (
        f"Texto actual de la dedicatoria:\n---\n{current_text.strip()}\n---\n\n"
        f"Instrucciones del usuario: {instruction}"
    )

    def _run_gemini() -> str:
        from google.genai import types

        client = get_gemini_client()
        response = client.models.generate_content(
            model=GEMINI_CORRECTION_MODEL,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=REFINE_SYSTEM_PROMPT,
                temperature=0.5,
            ),
        )
        text = getattr(response, "text", None) or ""
        return text.strip()

    def _run_openai() -> str:
        client = get_openai_client()
        response = client.chat.completions.create(
            model=OPENAI_CORRECTION_MODEL,
            messages=[
                {"role": "system", "content": REFINE_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.5,
        )
        return (response.choices[0].message.content or "").strip()

    callback = _on_retry_toast("Refinado IA")
    if cfg.ai_provider == "gemini":
        return with_retry(_run_gemini, on_retry=callback)
    return with_retry(_run_openai, on_retry=callback)


def rewrite_fragment(fragment: str, instruction: str) -> str:
    """Reescribe un fragmento concreto (una o varias frases) según una instrucción."""
    fragment = (fragment or "").strip()
    if not fragment:
        return ""
    instruction = (instruction or "").strip()
    if not instruction:
        return fragment
    cfg = get_config()
    user_message = (
        f"Fragmento a reescribir:\n---\n{fragment}\n---\n\n"
        f"Instrucciones del usuario: {instruction}"
    )

    def _run_gemini() -> str:
        from google.genai import types

        client = get_gemini_client()
        response = client.models.generate_content(
            model=GEMINI_CORRECTION_MODEL,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=REWRITE_FRAGMENT_SYSTEM_PROMPT,
                temperature=0.5,
            ),
        )
        text = getattr(response, "text", None) or ""
        return text.strip()

    def _run_openai() -> str:
        client = get_openai_client()
        response = client.chat.completions.create(
            model=OPENAI_CORRECTION_MODEL,
            messages=[
                {"role": "system", "content": REWRITE_FRAGMENT_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.5,
        )
        return (response.choices[0].message.content or "").strip()

    callback = _on_retry_toast("Reescritura de frase")
    if cfg.ai_provider == "gemini":
        return with_retry(_run_gemini, on_retry=callback)
    return with_retry(_run_openai, on_retry=callback)


def split_into_sentences(text: str) -> List[str]:
    """Parte un texto en frases preservando los signos de puntuación finales.

    No es un tokenizador lingüístico — pensado para textos cortos de dedicatoria.
    Mantiene saltos de línea como separadores fuertes.
    """
    import re

    if not text:
        return []
    # Separadores: ., !, ? (incluyendo ¡ ¿ que cierran), seguidos de espacio o fin
    # Conservamos el delimitador con un lookbehind capturando.
    chunks: List[str] = []
    # Primero partimos por saltos de línea, luego por puntuación.
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            chunks.append("")  # marcador de salto en blanco
            continue
        parts = re.split(r"(?<=[\.!?…])\s+", paragraph.strip())
        for p in parts:
            cleaned = p.strip()
            if cleaned:
                chunks.append(cleaned)
    return chunks


def join_sentences(sentences: Iterable[Optional[str]]) -> str:
    """Une frases (incluyendo None/"" para saltos de párrafo) con espacios o saltos."""
    out_lines: List[str] = []
    buffer: List[str] = []
    for s in sentences:
        if s is None or s == "":
            if buffer:
                out_lines.append(" ".join(buffer))
                buffer = []
            out_lines.append("")
        else:
            buffer.append(s.strip())
    if buffer:
        out_lines.append(" ".join(buffer))
    # Limpia párrafos vacíos al principio/final y compacta dobles vacíos.
    while out_lines and out_lines[0] == "":
        out_lines.pop(0)
    while out_lines and out_lines[-1] == "":
        out_lines.pop()
    return "\n".join(out_lines)
