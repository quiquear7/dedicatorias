from __future__ import annotations

import hmac

import streamlit as st

from core.config import get_config
from core.version import get_build_info


SESSION_KEY = "authenticated"


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


def require_login() -> None:
    """Bloquea la página si no hay sesión iniciada y hay APP_PASSWORD configurada."""
    inject_busy_styles()
    cfg = get_config()
    if not cfg.app_password:
        return  # sin contraseña, acceso libre
    if st.session_state.get(SESSION_KEY):
        return

    st.title("🔐 Acceso")
    st.caption("Esta app está protegida con contraseña.")
    with st.form("login", clear_on_submit=False):
        password = st.text_input("Contraseña", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Entrar", type="primary")
    if submitted:
        if hmac.compare_digest(password, cfg.app_password):
            st.session_state[SESSION_KEY] = True
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
