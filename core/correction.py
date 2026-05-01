from __future__ import annotations

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


def correct_dedication(raw_text: str) -> str:
    if not raw_text or not raw_text.strip():
        return ""
    cfg = get_config()
    if cfg.ai_provider == "gemini":
        return _correct_gemini(raw_text)
    return _correct_openai(raw_text)


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
