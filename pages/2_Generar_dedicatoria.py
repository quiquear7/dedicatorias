from __future__ import annotations

from typing import Optional

import streamlit as st

from core import contacts as contacts_module
from core import history as history_module
from core import templates as templates_module
from core.auth import logout_button, require_login
from core.config import get_config
from core.correction import correct_dedication, refine_text
from core.diff import html_diff
from core.models import Contact, Template
from core.rendering import render_back_png, render_pdf, render_png, render_preview
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
    "saved_as_pending": False,
    "loaded_duplicate_from": None,
    "_pdf_bytes": None,
    "_png_bytes": None,
    "_back_png_bytes": None,
    "versions": [],  # lista de dicts {label, text}
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

steps_labels = ["1. Destinatario", "2. Texto", "3. Revisión", "4. Plantilla / Guardar", "5. Exportar"]
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
        st.info(
            "💡 **Para que se grabe todo bien**: pulsa el botón rojo, **espera ~1 segundo en silencio antes de hablar**, "
            "di la dedicatoria, y al terminar **espera otro segundo en silencio antes de pulsar Stop**. "
            "Si la última palabra suena baja, prolongarla un poco también ayuda."
        )
        audio_value = st.audio_input("🎤 Graba tu dedicatoria")
        if audio_value is not None:
            audio_bytes = audio_value.getvalue()
            st.session_state["audio_bytes"] = audio_bytes
            st.session_state["audio_filename"] = audio_value.name or "audio.webm"
            st.caption("👆 Escucha tu grabación arriba antes de transcribir. Si falta algo, vuelve a grabar.")
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
                st.session_state["versions"] = [
                    {"label": "Transcripción cruda", "text": raw},
                    {"label": "Corrección IA", "text": corrected},
                ]
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
            versions = [{"label": "Texto introducido", "text": typed.strip()}]
            if run_correction and corrected != typed.strip():
                versions.append({"label": "Corrección IA", "text": corrected})
            st.session_state["versions"] = versions
            _go(3)

    st.divider()
    _back_button(1)

# --- Step 3: Revisión ---
elif step == 3:
    st.subheader("Revisión del texto")

    versions = st.session_state.get("versions") or []
    # Por compatibilidad con sesiones antiguas que no tengan versions:
    if not versions:
        versions = [{"label": "Texto", "text": st.session_state["final_text"]}]
        st.session_state["versions"] = versions

    tab_edit, tab_compare = st.tabs(["📝 Editar", f"🔍 Comparar versiones ({len(versions)})"])

    with tab_edit:
        # Texto crudo de referencia
        if st.session_state["input_mode"] == "audio":
            with st.expander("Transcripción cruda (sin tocar)"):
                st.text(st.session_state["raw_input"])
        else:
            with st.expander("Texto original introducido"):
                st.text(st.session_state["raw_input"])

        final_text = st.text_area(
            "Texto final (editable)",
            value=st.session_state["final_text"],
            height=240,
            key="final_text_area",
        )
        # Si el usuario edita manualmente, lo guardamos como nueva versión sólo si cambia significativamente
        if final_text != st.session_state["final_text"]:
            st.session_state["final_text"] = final_text
            if not versions or versions[-1]["text"] != final_text:
                versions.append({"label": "Edición manual", "text": final_text})
                st.session_state["versions"] = versions

        st.divider()
        st.markdown("**✨ Refinar con IA**")
        st.caption(
            "Dale instrucciones libres a la IA y aplicará los cambios sobre el texto actual: "
            "ej. *«hazla más corta»*, *«añade un toque de humor»*, *«hazla más formal»*, *«tradúcela al catalán»*."
        )
        instr = st.text_input(
            "Instrucciones",
            placeholder="Ej. hazla más cariñosa y resume en 2 líneas",
            key="refine_instruction",
            label_visibility="collapsed",
        )
        rcols = st.columns([1, 1, 3])
        with rcols[0]:
            if st.button("✨ Refinar con IA", type="primary", disabled=not instr.strip() or not cfg.is_ai_ready):
                with st.spinner("Refinando..."):
                    try:
                        new_text = refine_text(st.session_state["final_text"], instr)
                        if new_text and new_text != st.session_state["final_text"]:
                            label = f"Refinado: «{instr.strip()[:40]}{'…' if len(instr.strip())>40 else ''}»"
                            versions.append({"label": label, "text": new_text})
                            st.session_state["versions"] = versions
                            st.session_state["final_text"] = new_text
                            st.session_state["corrected_text"] = new_text
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Error: {e}")
        with rcols[1]:
            if st.button("🤖 Re-corregir desde cero", help="Vuelve a aplicar la corrección base sobre el texto crudo original"):
                with st.spinner("Corrigiendo..."):
                    try:
                        corrected = correct_dedication(st.session_state["raw_input"])
                        if corrected != st.session_state["final_text"]:
                            versions.append({"label": "Re-corrección", "text": corrected})
                            st.session_state["versions"] = versions
                        st.session_state["corrected_text"] = corrected
                        st.session_state["final_text"] = corrected
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Error: {e}")

        st.divider()
        cols = st.columns([1, 3])
        with cols[0]:
            _back_button(2)
        with cols[1]:
            if st.button(
                "Confirmar y elegir plantilla →",
                type="primary",
                disabled=not st.session_state["final_text"].strip(),
                use_container_width=True,
            ):
                _go(4)

    with tab_compare:
        if len(versions) < 2:
            st.info(
                "Aún no hay con qué comparar. Cuando refines la dedicatoria con instrucciones o "
                "edites el texto, aparecerán aquí las distintas versiones."
            )
        else:
            st.markdown("Selecciona dos versiones para comparar palabra por palabra:")
            labels = [f"{i+1}. {v['label']}" for i, v in enumerate(versions)]
            cols = st.columns(2)
            with cols[0]:
                left_idx = st.selectbox("Antes", options=range(len(versions)), format_func=lambda i: labels[i], index=0, key="diff_left")
            with cols[1]:
                right_idx = st.selectbox("Después", options=range(len(versions)), format_func=lambda i: labels[i], index=len(versions) - 1, key="diff_right")

            left_text = versions[left_idx]["text"]
            right_text = versions[right_idx]["text"]
            left_html, right_html = html_diff(left_text, right_text)

            dcols = st.columns(2)
            with dcols[0]:
                st.caption(f"📛 {versions[left_idx]['label']}")
                st.markdown(left_html, unsafe_allow_html=True)
            with dcols[1]:
                st.caption(f"✅ {versions[right_idx]['label']}")
                st.markdown(right_html, unsafe_allow_html=True)

            st.markdown("Leyenda: <span style='background:#ffd6d6;color:#a40000;text-decoration:line-through'>quitado</span> · <span style='background:#d6ffd6;color:#0a6900'>añadido</span>", unsafe_allow_html=True)

            st.divider()
            if st.button(f"⬆️ Usar la versión «{versions[right_idx]['label']}» como texto final"):
                st.session_state["final_text"] = right_text
                st.session_state["corrected_text"] = right_text
                st.toast("Texto final actualizado.")
                st.rerun()

# --- Step 4: Plantilla ---
elif step == 4:
    st.subheader("Plantilla")
    templates = templates_module.list_templates()

    if not templates:
        st.warning("Todavía no tienes plantillas. Puedes guardar la dedicatoria como pendiente y generar el archivo de impresión más tarde, cuando subas una plantilla.")
        cols = st.columns([1, 3])
        with cols[0]:
            _back_button(3)
        with cols[1]:
            if st.button("💾 Guardar como pendiente", type="primary"):
                try:
                    saved = history_module.save_pending(
                        recipient_name=st.session_state["recipient_name"],
                        recipient_group=st.session_state["recipient_group"],
                        contact_id=st.session_state["contact_id"],
                        input_mode=st.session_state["input_mode"],
                        raw_input=st.session_state["raw_input"],
                        corrected_text=st.session_state["corrected_text"],
                        final_text=st.session_state["final_text"],
                        audio_bytes=st.session_state.get("audio_bytes") if st.session_state["input_mode"] == "audio" else None,
                        is_generic=st.session_state["is_generic"],
                    )
                    st.session_state["saved_dedication_id"] = saved.id
                    st.session_state["saved_as_pending"] = True
                    _go(5)
                except Exception as e:  # noqa: BLE001
                    st.error(f"Error guardando: {e}")
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

        cols = st.columns([1, 1, 2])
        with cols[0]:
            _back_button(3)
        with cols[1]:
            if st.button("💾 Guardar pendiente"):
                try:
                    saved = history_module.save_pending(
                        recipient_name=st.session_state["recipient_name"],
                        recipient_group=st.session_state["recipient_group"],
                        contact_id=st.session_state["contact_id"],
                        input_mode=st.session_state["input_mode"],
                        raw_input=st.session_state["raw_input"],
                        corrected_text=st.session_state["corrected_text"],
                        final_text=st.session_state["final_text"],
                        audio_bytes=st.session_state.get("audio_bytes") if st.session_state["input_mode"] == "audio" else None,
                        is_generic=st.session_state["is_generic"],
                    )
                    st.session_state["saved_dedication_id"] = saved.id
                    st.session_state["saved_as_pending"] = True
                    _go(5)
                except Exception as e:  # noqa: BLE001
                    st.error(f"Error guardando: {e}")
        with cols[2]:
            if st.button("Generar tarjeta ahora →", type="primary"):
                _go(5)

# --- Step 5: Export ---
elif step == 5:
    if st.session_state.get("saved_as_pending"):
        st.success("✅ Dedicatoria guardada como **pendiente**.")
        st.markdown(
            "Cuando tengas las plantillas listas, ve a la página **📜 Historial → pestaña Pendientes** "
            "y pulsa **«Generar todas con plantilla»** para crear los archivos de impresión en lote, "
            "o renderiza esta dedicatoria individualmente."
        )
        if st.button("Crear otra dedicatoria"):
            _reset_flow()
            st.rerun()
        st.stop()

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
                    back_png_bytes = render_back_png(template) if template.has_back else None
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
                        back_png_bytes=back_png_bytes,
                        audio_bytes=st.session_state.get("audio_bytes") if st.session_state["input_mode"] == "audio" else None,
                        is_generic=st.session_state["is_generic"],
                    )
                st.session_state["saved_dedication_id"] = saved.id
                st.session_state["_pdf_bytes"] = pdf_bytes
                st.session_state["_png_bytes"] = png_bytes
                st.session_state["_back_png_bytes"] = back_png_bytes
            except Exception as e:  # noqa: BLE001
                st.error(f"Error generando: {e}")
                _back_button(4)
                st.stop()

        st.success("Dedicatoria generada y guardada en el historial.")

        # Vista de las dos caras si hay reverso, o sólo el frente.
        if st.session_state.get("_back_png_bytes"):
            tab_front, tab_back = st.tabs(["📄 Frente (con texto)", "🔄 Reverso"])
            with tab_front:
                st.image(st.session_state["_png_bytes"], use_container_width=True, caption="Frente")
            with tab_back:
                st.image(st.session_state["_back_png_bytes"], use_container_width=True, caption="Reverso")
        else:
            st.image(st.session_state["_png_bytes"], use_container_width=True, caption="Vista final")

        slug = st.session_state["recipient_name"].replace(" ", "_") or "tarjeta"
        n_cols = 3 if st.session_state.get("_back_png_bytes") else 2
        cols = st.columns(n_cols)
        with cols[0]:
            label = "⬇️ PDF (frente + reverso)" if st.session_state.get("_back_png_bytes") else "⬇️ Descargar PDF (imprenta)"
            st.download_button(
                label,
                data=st.session_state["_pdf_bytes"],
                file_name=f"dedicatoria_{slug}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        with cols[1]:
            st.download_button(
                "⬇️ PNG frente (300 dpi)",
                data=st.session_state["_png_bytes"],
                file_name=f"dedicatoria_{slug}_frente.png",
                mime="image/png",
                use_container_width=True,
            )
        if st.session_state.get("_back_png_bytes"):
            with cols[2]:
                st.download_button(
                    "⬇️ PNG reverso (300 dpi)",
                    data=st.session_state["_back_png_bytes"],
                    file_name=f"dedicatoria_{slug}_reverso.png",
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
