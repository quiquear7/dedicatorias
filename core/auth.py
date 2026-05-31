from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import extra_streamlit_components as stx
import streamlit as st

from core.config import get_config
from core.version import get_build_info

logger = logging.getLogger(__name__)


SESSION_KEY = "authenticated"
COOKIE_NAME = "dedicatorias_auth"
COOKIE_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 días


_BUSY_STYLES = """
<style>
/* Indicador "Running..." de Streamlit en la esquina superior derecha: lo
 * convertimos en un chip prominente para que el usuario sepa que algo está
 * ejecutándose y evite volver a pulsar el botón.
 */
[data-testid="stStatusWidget"] {
    position: fixed !important;
    top: 14px !important;
    right: 14px !important;
    z-index: 999999 !important;
    padding: 10px 18px !important;
    background: linear-gradient(135deg, #f59e0b, #d97706) !important;
    color: white !important;
    font-weight: 600 !important;
    font-size: 15px !important;
    border-radius: 999px !important;
    box-shadow: 0 6px 18px rgba(0,0,0,0.28) !important;
    border: 2px solid white !important;
    pointer-events: none;
}
[data-testid="stStatusWidget"] svg,
[data-testid="stStatusWidget"] [data-testid="stStatusWidgetIcon"] {
    color: white !important;
    fill: white !important;
}

/* Spinner inline (st.spinner) un poco más grande para que sea claro */
.stSpinner > div {
    font-size: 1.05rem !important;
}
</style>
"""


def inject_busy_styles() -> None:
    """Inyecta los estilos que hacen visible el estado «procesando».

    Idempotente por página: Streamlit colapsa varias inyecciones idénticas
    en el mismo <head>.
    """
    st.markdown(_BUSY_STYLES, unsafe_allow_html=True)


def _get_cookie_manager() -> stx.CookieManager:
    """CookieManager cacheado en `session_state` para evitar montar varios
    componentes de cookies en la misma página (Streamlit los duplicaría y
    ninguno funcionaría bien)."""
    if "_cookie_manager" not in st.session_state:
        st.session_state["_cookie_manager"] = stx.CookieManager(key="dedicatorias_cookies")
    return st.session_state["_cookie_manager"]


def _sign(payload: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _make_token(secret: str, ttl_seconds: int = COOKIE_TTL_SECONDS) -> str:
    """Token `<exp_epoch>.<hmac_sha256>` firmado con la propia contraseña como
    secreto. Si la contraseña cambia, todas las cookies emitidas previamente
    dejan de ser válidas automáticamente."""
    exp = int(time.time()) + int(ttl_seconds)
    return f"{exp}.{_sign(str(exp), secret)}"


def _verify_token(token: Optional[str], secret: str) -> bool:
    if not token or "." not in token:
        return False
    exp_str, sig = token.split(".", 1)
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    expected = _sign(str(exp), secret)
    return hmac.compare_digest(sig, expected)


def _read_auth_cookie(cm: stx.CookieManager) -> Optional[str]:
    """Lee la cookie de auth. Devuelve None tanto si no existe como si el
    componente JS aún no ha cargado las cookies del navegador (en el primer
    render). En ese segundo caso, el componente forzará un rerun y la próxima
    pasada ya verá el valor."""
    try:
        cookies = cm.get_all()
    except Exception as exc:  # noqa: BLE001
        logger.debug("CookieManager.get_all() falló: %s", exc)
        return None
    if not cookies:
        return None
    value = cookies.get(COOKIE_NAME)
    return value if isinstance(value, str) else None


def require_login() -> None:
    """Bloquea la página si no hay sesión iniciada y hay APP_PASSWORD configurada.

    La sesión sobrevive a recargas y reinicios mientras la cookie firmada esté
    presente y dentro de su TTL (30 días por defecto).
    """
    inject_busy_styles()
    cfg = get_config()
    if not cfg.app_password:
        return  # sin contraseña, acceso libre
    if st.session_state.get(SESSION_KEY):
        return

    cm = _get_cookie_manager()
    token = _read_auth_cookie(cm)
    if _verify_token(token, cfg.app_password):
        st.session_state[SESSION_KEY] = True
        return

    st.title("🔐 Acceso")
    st.caption("Esta app está protegida con contraseña.")
    with st.form("login", clear_on_submit=False):
        password = st.text_input("Contraseña", type="password", autocomplete="current-password")
        remember = st.checkbox(
            "Recuérdame en este dispositivo (30 días)",
            value=True,
            help=(
                "Guarda una cookie firmada para no tener que volver a introducir "
                "la contraseña al recargar o reabrir la app durante 30 días."
            ),
        )
        submitted = st.form_submit_button("Entrar", type="primary")
    if submitted:
        if hmac.compare_digest(password, cfg.app_password):
            st.session_state[SESSION_KEY] = True
            if remember:
                token = _make_token(cfg.app_password, COOKIE_TTL_SECONDS)
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=COOKIE_TTL_SECONDS)
                try:
                    cm.set(COOKIE_NAME, token, expires_at=expires_at)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("No se pudo escribir la cookie de sesión: %s", exc)
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")
    st.stop()


def logout_button() -> None:
    """Renderiza el footer de la sidebar: botón de cerrar sesión (si hay contraseña) y versión."""
    cfg = get_config()
    with st.sidebar:
        if cfg.app_password:
            if st.button("🚪 Cerrar sesión", use_container_width=True):
                st.session_state.pop(SESSION_KEY, None)
                cm = _get_cookie_manager()
                try:
                    cm.delete(COOKIE_NAME)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("No se pudo borrar la cookie de sesión: %s", exc)
                st.rerun()
        _render_version_footer()


def _render_version_footer() -> None:
    info = get_build_info()
    st.markdown(
        f"<div style='margin-top:1rem;padding-top:0.5rem;border-top:1px solid rgba(120,120,120,0.25);"
        f"text-align:center;font-size:11px;color:#888;font-variant-numeric:tabular-nums'>"
        f"{info.display}</div>",
        unsafe_allow_html=True,
    )
