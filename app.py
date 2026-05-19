from __future__ import annotations

import streamlit as st

from core.auth import logout_button, require_login
from core.config import get_config

st.set_page_config(page_title="Dedicatorias", page_icon="💌", layout="centered")
require_login()
logout_button()
st.title("💌 Generador de tarjetas de dedicatoria")

cfg = get_config()

st.markdown(
    """
Una herramienta para crear tarjetas con dedicatorias personalizadas:
graba la dedicatoria por voz (o tecléala), revisa el texto corregido por IA,
elige una plantilla con tus medidas y genera los archivos imprimibles (PDF + PNG a 300 dpi).
"""
)

_TX_LABELS = {
    "groq": ("Groq", "GROQ_API_KEY", "whisper-large-v3"),
    "openai": ("OpenAI", "OPENAI_API_KEY", "whisper-1"),
    "gemini": ("Gemini", "GOOGLE_API_KEY", "gemini-2.5-flash"),
}
_TEXT_LABELS = {
    "openai": ("OpenAI", "OPENAI_API_KEY", "gpt-4o-mini"),
    "gemini": ("Gemini", "GOOGLE_API_KEY", "gemini-2.5-flash"),
    "groq": ("Groq", "GROQ_API_KEY", "llama-3.3-70b-versatile"),
}

st.subheader("Estado")
col1, col2 = st.columns(2)
with col1:
    st.markdown("**🎤 Transcripción de audio**")
    if cfg.is_transcription_ready:
        prov, key_name, model = _TX_LABELS.get(
            cfg.transcription_provider, ("?", "?", "?")
        )
        st.success(f"✅ {prov} · `{model}`")
        st.caption(f"Usando `{key_name}`")
    else:
        st.error("❌ Falta clave para transcripción")
        st.caption(
            "Añade `GROQ_API_KEY` (gratis), `OPENAI_API_KEY` o `GOOGLE_API_KEY` "
            "a tus secrets."
        )

    st.markdown("**✍️ Corrección y refinado de texto**")
    if cfg.is_ai_ready:
        prov, key_name, model = _TEXT_LABELS.get(
            cfg.ai_provider, ("?", "?", "?")
        )
        st.success(f"✅ {prov} · `{model}`")
        st.caption(
            f"Usando `{key_name}`. "
            "Cambia `AI_PROVIDER` en secrets para forzar el otro."
        )
    else:
        st.error("❌ Falta clave para texto")
        st.caption("Añade `OPENAI_API_KEY` o `GOOGLE_API_KEY` a tus secrets.")
with col2:
    if cfg.is_storage_ready:
        st.success(f"✅ Almacenamiento: `{cfg.storage_backend}`")
        if cfg.storage_backend == "local":
            st.caption(f"Datos en `{cfg.local_storage_root}`")
        else:
            st.caption(f"Bucket S3/R2: `{cfg.s3_bucket}`")
    else:
        st.error("❌ Almacenamiento incompleto")
        st.caption("Revisa STORAGE_BACKEND y, si usas s3, las credenciales R2.")

st.divider()
st.subheader("Cómo usarlo")
st.markdown(
    """
1. **Destinatarios** → da de alta a las personas para las que harás dedicatorias, agrupadas (familia, amigos, trabajo…).
2. **Plantillas** → sube los diseños de tus tarjetas con sus medidas (mm) y la zona donde irá el texto.
3. **Generar dedicatoria** → elige destinatario, graba audio o teclea texto, revisa, elige plantilla y descarga PDF + PNG.
4. **Historial** → consulta dedicatorias pasadas, vuelve a descargarlas o duplica una "genérica" para otra persona.
"""
)
