"""Reintentos para llamadas a IA con errores transitorios (503, 429, etc.).

Gemini 2.5 sufre picos de demanda que devuelven `503 UNAVAILABLE`. Estos errores
suelen resolverse reintentando con backoff exponencial.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Códigos HTTP que merecen reintento (transitorios).
_RETRY_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}

# Estados textuales (Google API) que merecen reintento.
_RETRY_STATUSES = {
    "UNAVAILABLE",
    "RESOURCE_EXHAUSTED",
    "DEADLINE_EXCEEDED",
    "INTERNAL",
    "ABORTED",
}

# Nombres de excepciones de OpenAI / httpx que indican fallos transitorios.
_RETRY_EXC_NAMES = {
    "RateLimitError",
    "APIConnectionError",
    "APITimeoutError",
    "InternalServerError",
    "ServiceUnavailableError",
    "TimeoutException",
    "ReadTimeout",
    "ConnectError",
}


def is_transient_error(exc: BaseException) -> bool:
    """Heurística: ¿merece la pena reintentar este error?"""
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code in _RETRY_HTTP_CODES:
        return True
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and status_code in _RETRY_HTTP_CODES:
        return True
    status = getattr(exc, "status", "")
    if isinstance(status, str) and status.upper() in _RETRY_STATUSES:
        return True
    if type(exc).__name__ in _RETRY_EXC_NAMES:
        return True
    return False


def is_transient_message(exc: BaseException) -> bool:
    """Marca como transitorio si el texto del error contiene pistas conocidas."""
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "unavailable",
            "overloaded",
            "rate limit",
            "try again later",
            "high demand",
            "timeout",
            "timed out",
        )
    )


def with_retry(
    fn: Callable[..., T],
    *args: Any,
    max_attempts: int = 4,
    base_delay: float = 1.5,
    max_delay: float = 12.0,
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
    **kwargs: Any,
) -> T:
    """Llama a `fn` reintentando en caso de errores transitorios.

    `on_retry(attempt, exc, sleep_seconds)` se invoca antes de cada pausa para
    poder mostrar feedback al usuario (toast, log, etc.).
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            retryable = is_transient_error(exc) or is_transient_message(exc)
            if attempt >= max_attempts or not retryable:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, 0.5)
            logger.warning(
                "IA transitoria (intento %s/%s): %s — reintento en %.1fs",
                attempt,
                max_attempts,
                exc,
                delay,
            )
            if on_retry is not None:
                try:
                    on_retry(attempt, exc, delay)
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def friendly_ai_error(exc: BaseException) -> str:
    """Convierte una excepción de IA en un mensaje legible para el usuario."""
    if is_transient_error(exc) or is_transient_message(exc):
        return (
            "La IA está saturada en este momento (picos de demanda). "
            "Ya he reintentado varias veces sin éxito. Espera 30–60 segundos y prueba otra vez."
        )
    return f"Error de IA: {exc}"
