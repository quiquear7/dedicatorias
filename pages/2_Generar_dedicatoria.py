from __future__ import annotations

from typing import Optional

import streamlit as st

from core import contacts as contacts_module
from core import history as history_module
from core import templates as templates_module
from core.auth import logout_button, require_login
from core.config import get_config
from core.correction import correct_dedication
from core.models import Contact, Template
from core.rendering import render_pdf, render_png, render_preview
from core.transcription import transcribe

st.set_page_config(page_title="Generar dedicatoria", page_icon="✍️", layout="wide")
require_login()
logout_button()
st.title("✍️ Generar dedicatoria")

cfg = get_config()
if not cfg.is_storage_ready:
    st.error("Almacenamiento no configurado.")
    st.stop()

DEFAULT_STATE = {
    "step": 1,
    "contact_id": None,
    "recipient_name": "",
    "recipient_group": "",
    "input_mode": "audio",
    "raw_input": "",
    "corrected_text": "",
    "final_text": "",
    "audio_bytes": None,
    "audio_filename": None,
    "selected_template_id": None,
    "is_generic": False,
    "saved_dedication_id": None,
    "loaded_duplicate_from": None,
}

for key, default in DEFAULT_STATE.items():
    st.session_state.setdefault(key, default)


def _reset_flow():
    for key, default in DEFAULT_STATE.items():
        st.session_state[key] = default
    try:
        st.query_params.clear()
    except Exception:
        pass


duplicate_id = st.query_params.get("duplicate") if hasattr(st, "query_params") else None
if isinstance(duplicate_id, list):
    duplicate_id = duplicate_id[0] if duplicate_id else None

if duplicate_id and st.session_state.get("loaded_duplicate_from") != duplicate_id:
    src = history_module.get_dedication(duplicate_id)
    if src:
        st.session_state["loaded_duplicate_from"] = duplicate_id
        st.session_state["raw_input"] = src.raw_input
        st.session_state["corrected_text"] = src.corrected_text
        st.session_state["final_text"] = src.final_text
        st.session_state["selected_template_id"] = src.template_id
        st.session_state["input_mode"] = "text"
        st.session_state["step"] = 1
        st.session_state["recipient_name"] = ""
        st.session_state["recipient_group"] = ""
        st.session_state["contact_id"] = None
        st.info(
            f"Has cargado la dedicatoria del historial («{src.recipient_name}»). "
            "Selecciona el nuevo destinatario para duplicarla."
        )

step = st.session_state["step"]

steps_labels = ["1. Destinatario", "2. Texto", "3. Revisión", "4. Plantilla", "5. Exportar"]
st.progress((step - 1) / 4, text=f"Paso {step} de 5 — {steps_labels[step - 1]}")


def _go(next_step: int):
    st.session_state["step"] = next_step
    st.rerun()


def _back_button(target: int):
    if st.button("← Atrás"):
        _go(target)


# --- Step 1: Destinatario ---
if step == 1:
    st.subheader("Destinatario")
    contacts = contacts_module.list_contacts()

    mode = st.radio(
        "¿Cómo quieres elegir al destinatario?",
        options=["Contacto existente", "Nuevo contacto"],
        horizontal=True,
        index=0 if contacts else 1,
    )

    if mode == "Contacto existente":
        if not contacts:
            st.warning("Aún no tienes destinatarios. Cambia a «Nuevo contacto» o créalo desde la página Destinatarios.")
        else:
            groups = sorted({c.group for c in contacts if c.group})
            group_filter = st.selectbox("Filtrar por grupo", options=["(todos)", *groups])
            visible = [c for c in contacts if group_filter == "(todos)" or c.group == group_filter]
            if not visible:
                st.info("No hay destinatarios en ese grupo.")
            else:
                labels = [c.label for c in visible]
                default_index = 0
                if st.session_state["contact_id"]:
                    for i, c in enumerate(visible):
                        if c.id == st.session_state["contact_id"]:
                            default_index = i
                            break
                pick = st.selectbox("Destinatario", options=labels, index=default_index)
                chosen = visible[labels.index(pick)]
                if st.button("Continuar →", type="primary"):
                    st.session_state["contact_id"] = chosen.id
                    st.session_state["recipient_name"] = chosen.name
                    st.session_state["recipient_group"] = chosen.group
                    _go(2)
    else:
        existing_groups = contacts_module.list_groups()
        col1, col2 = st.columns(2)
        with col1:
            new_name = st.text_input("Nombre", value=st.session_state["recipient_name"])
        with col2:
            if existing_groups:
                group_pick = st.selectbox("Grupo", options=["— Nuevo —", *existing_groups])
                if group_pick == "— Nuevo —":
                    new_group = st.text_input("Nombre del nuevo grupo", value="")
                else:
                    new_group = group_pick
            else:
                new_group = st.text_input("Grupo", value=st.session_state["recipient_group"])
        save_contact = st.checkbox("Guardar como destinatario para futuras dedicatorias", value=True)
        if st.button("Continuar →", type="primary", disabled=not new_name.strip()):
            contact_id: Optional[str] = None
            if save_contact:
                contact: Contact = contacts_module.find_or_create(new_name, new_group)
                contact_id = contact.id
            st.session_state["contact_id"] = contact_id
            st.session_state["recipient_name"] = new_name.strip()
            st.session_state["recipient_group"] = (new_group or "").strip()
            _go(2)

# --- Step 2: Texto (audio o tecleado) ---
elif step == 2:
    st.subheader("Texto de la dedicatoria")
    st.caption(f"Para: **{st.session_state['recipient_name']}**" + (f" · {st.session_state['recipient_group']}" if st.session_state['recipient_group'] else ""))
    tab_audio, tab_text = st.tabs(["🎤 Grabar audio", "⌨️ Escribir texto"])

    with tab_audio:
        if not cfg.is_ai_ready:
            st.warning("No hay clave de IA configurada. Añade OPENAI_API_KEY o GOOGLE_API_KEY al .env.")
        audio_value = st.audio_input("Graba tu dedicatoria")
        if audio_value is not None:
            audio_bytes = audio_value.getvalue()
            st.session_state["audio_bytes"] = audio_bytes
            st.session_state["audio_filename"] = audio_value.name or "audio.webm"
            if st.button("Transcribir y corregir", type="primary"):
                provider = "Gemini" if cfg.ai_provider == "gemini" else "Whisper"
                with st.spinner(f"Transcribiendo con {provider}..."):
                    try:
                        raw = transcribe(audio_bytes, filename=st.session_state["audio_filename"])
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Error transcribiendo: {e}")
                        st.stop()
                with st.spinner("Corrigiendo con IA..."):
                    try:
                        corrected = correct_dedication(raw)
                    except Exception as e:  # noqa: BLE001
                        st.warning(f"No se pudo corregir, uso el texto crudo: {e}")
                        corrected = raw
                st.session_state["input_mode"] = "audio"
                st.session_state["raw_input"] = raw
                st.session_state["corrected_text"] = corrected
                st.session_state["final_text"] = corrected
                _go(3)

    with tab_text:
        typed = st.text_area(
            "Dedicatoria",
            value=st.session_state["raw_input"] if st.session_state["input_mode"] == "text" else "",
            height=200,
            placeholder="Pega o escribe aquí la dedicatoria.",
        )
        run_correction = st.checkbox("Pasar también por corrección IA", value=False, help="Marca esto si quieres limpiar ortografía/puntuación. Si tu texto ya está pulido, déjalo desmarcado.")
        if st.button("Continuar →", type="primary", disabled=not typed.strip()):
            st.session_state["input_mode"] = "text"
            st.session_state["raw_input"] = typed.strip()
            if run_correction:
                with st.spinner("Corrigiendo con IA..."):
                    try:
                        corrected = correct_dedication(typed)
                    except Exception as e:  # noqa: BLE001
                        st.warning(f"No se pudo corregir, uso el texto crudo: {e}")
                        corrected = typed.strip()
            else:
                corrected = typed.strip()
            st.session_state["corrected_text"] = corrected
            st.session_state["final_text"] = corrected
            _go(3)

    st.divider()
    _back_button(1)

# --- Step 3: Revisión ---
elif step == 3:
    st.subheader("Revisión del texto")
    if st.session_state["input_mode"] == "audio":
        with st.expander("Transcripción cruda (de Whisper)"):
            st.text(st.session_state["raw_input"])
    else:
        with st.expander("Texto introducido"):
            st.text(st.session_state["raw_input"])

    final_text = st.text_area(
        "Texto final (editable)",
        value=st.session_state["final_text"],
        height=240,
    )
    st.session_state["final_text"] = final_text

    cols = st.columns([1, 1, 2])
    with cols[0]:
        _back_button(2)
    with cols[1]:
        if st.button("🤖 Re-corregir") and cfg.is_ai_ready:
            with st.spinner("Corrigiendo de nuevo..."):
                try:
                    corrected = correct_dedication(st.session_state["raw_input"])
                    st.session_state["corrected_text"] = corrected
                    st.session_state["final_text"] = corrected
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Error: {e}")
    with cols[2]:
        if st.button("Confirmar y elegir plantilla →", type="primary", disabled=not final_text.strip()):
            _go(4)

# --- Step 4: Plantilla ---
elif step == 4:
    st.subheader("Plantilla")
    templates = templates_module.list_templates()
    if not templates:
        st.error("No tienes plantillas. Crea una en la página «Plantillas» antes de continuar.")
        _back_button(3)
    else:
        labels = [f"{t.name} ({t.width_mm:.0f}×{t.height_mm:.0f} mm)" for t in templates]
        default_idx = 0
        if st.session_state["selected_template_id"]:
            for i, t in enumerate(templates):
                if t.id == st.session_state["selected_template_id"]:
                    default_idx = i
                    break
        choice = st.selectbox("Elige plantilla", options=labels, index=default_idx)
        chosen = templates[labels.index(choice)]
        st.session_state["selected_template_id"] = chosen.id

        with st.spinner("Generando vista previa..."):
            try:
                preview = render_preview(chosen, st.session_state["recipient_name"], st.session_state["final_text"])
                st.image(preview, use_container_width=True, caption="Vista previa")
            except Exception as e:  # noqa: BLE001
                st.error(f"Error en preview: {e}")

        cols = st.columns([1, 3])
        with cols[0]:
            _back_button(3)
        with cols[1]:
            if st.button("Generar tarjeta →", type="primary"):
                _go(5)

# --- Step 5: Export ---
elif step == 5:
    st.subheader("Generar e imprimir")
    template = templates_module.get_template(st.session_state["selected_template_id"])
    if not template:
        st.error("La plantilla seleccionada ya no existe.")
        _back_button(4)
    else:
        if st.session_state["saved_dedication_id"] is None:
            try:
                with st.spinner("Renderizando PDF + PNG a 300 dpi..."):
                    pdf_bytes, pdf_warn = render_pdf(template, st.session_state["recipient_name"], st.session_state["final_text"])
                    png_bytes, png_warn = render_png(template, st.session_state["recipient_name"], st.session_state["final_text"])
                if pdf_warn.get("text_overflow") or png_warn.get("text_overflow"):
                    st.warning("⚠️ El texto no cabe completamente en la zona definida. Considera reducir el tamaño de fuente o ampliar la zona en la plantilla.")
                if pdf_warn.get("name_overflow") or png_warn.get("name_overflow"):
                    st.warning("⚠️ El nombre no cabe en su zona.")
                with st.spinner("Guardando en historial..."):
                    saved = history_module.save_generated(
                        template=template,
                        recipient_name=st.session_state["recipient_name"],
                        recipient_group=st.session_state["recipient_group"],
                        contact_id=st.session_state["contact_id"],
                        input_mode=st.session_state["input_mode"],
                        raw_input=st.session_state["raw_input"],
                        corrected_text=st.session_state["corrected_text"],
                        final_text=st.session_state["final_text"],
                        pdf_bytes=pdf_bytes,
                        png_bytes=png_bytes,
                        audio_bytes=st.session_state.get("audio_bytes") if st.session_state["input_mode"] == "audio" else None,
                        is_generic=st.session_state["is_generic"],
                    )
                st.session_state["saved_dedication_id"] = saved.id
                st.session_state["_pdf_bytes"] = pdf_bytes
                st.session_state["_png_bytes"] = png_bytes
            except Exception as e:  # noqa: BLE001
                st.error(f"Error generando: {e}")
                _back_button(4)
                st.stop()

        st.success("Dedicatoria generada y guardada en el historial.")
        st.image(st.session_state["_png_bytes"], use_container_width=True, caption="Vista final")

        slug = st.session_state["recipient_name"].replace(" ", "_") or "tarjeta"
        cols = st.columns(2)
        with cols[0]:
            st.download_button(
                "⬇️ Descargar PDF (imprenta)",
                data=st.session_state["_pdf_bytes"],
                file_name=f"dedicatoria_{slug}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        with cols[1]:
            st.download_button(
                "⬇️ Descargar PNG (300 dpi)",
                data=st.session_state["_png_bytes"],
                file_name=f"dedicatoria_{slug}.png",
                mime="image/png",
                use_container_width=True,
            )

        is_generic = st.checkbox(
            "Marcar esta dedicatoria como genérica (para reutilizar con otros destinatarios)",
            value=st.session_state["is_generic"],
            key="generic_toggle",
        )
        if is_generic != st.session_state["is_generic"]:
            st.session_state["is_generic"] = is_generic
            saved = history_module.get_dedication(st.session_state["saved_dedication_id"])
            if saved:
                saved.is_generic = is_generic
                history_module.update_dedication(saved)
                st.toast("Estado actualizado.")

        st.divider()
        if st.button("Crear otra dedicatoria"):
            _reset_flow()
            st.rerun()
