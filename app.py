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

st.subheader("Estado")
col1, col2 = st.columns(2)
with col1:
    if cfg.is_ai_ready:
        provider_label = "Gemini" if cfg.ai_provider == "gemini" else "OpenAI"
        st.success(f"✅ Proveedor IA: **{provider_label}**")
        if cfg.ai_provider == "gemini":
            st.caption("Usando GOOGLE_API_KEY · gemini-2.5-flash (audio + corrección)")
        else:
            st.caption("Usando OPENAI_API_KEY · whisper-1 + gpt-4o-mini")
    else:
        st.error("❌ Falta clave de IA")
        st.caption(
            "Añade `OPENAI_API_KEY` o `GOOGLE_API_KEY` a tu `.env`. "
            "Gemini tiene tier gratuito en https://aistudio.google.com/apikey."
        )
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
