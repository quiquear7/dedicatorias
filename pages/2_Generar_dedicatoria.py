from __future__ import annotations

import io
import zipfile
from typing import Dict, List, Optional

import streamlit as st

from core import contacts as contacts_module
from core import history as history_module
from core import templates as templates_module
from core.ai_retry import friendly_ai_error
from core.audio_recorder import audio_recorder
from core.auth import logout_button, require_login
from core.config import get_config
from core.correction import (
    correct_dedication,
    join_sentences,
    refine_text,
    rewrite_fragment,
    split_into_sentences,
)
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
    "recipients": [],  # lista de dicts {name, group, contact_id} cuando hay varios destinatarios
    "input_mode": "audio",
    "raw_input": "",
    "corrected_text": "",
    "final_text": "",
    "audio_bytes": None,
    "audio_filename": None,
    "selected_template_id": None,
    "is_generic": False,
    "saved_dedication_id": None,
    "saved_dedication_ids": [],  # cuando se generan varias tarjetas
    "saved_as_pending": False,
    "loaded_duplicate_from": None,
    "_pdf_bytes": None,
    "_png_bytes": None,
    "_back_png_bytes": None,
    "_rendered_items": [],  # [{recipient, dedication_id, pdf, png, back_png}]
    "versions": [],  # lista de dicts {label, text}
    "final_text_rev": 0,  # se incrementa para forzar refresco del text_area cuando la IA reescribe
    "sentences_rev": 0,  # se incrementa para resetear checkboxes del editor por frases
    "audio_widget_rev": 0,  # se incrementa para resetear el widget de grabación
    "include_back": True,  # incluir la cara trasera en PDF/ZIP cuando la plantilla la tiene
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
        st.session_state["recipients"] = []
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


def _set_recipients(recipients: List[Dict[str, Optional[str]]]) -> None:
    """Guarda la lista de destinatarios y mantiene los campos legacy del primero."""
    st.session_state["recipients"] = recipients
    if recipients:
        first = recipients[0]
        st.session_state["recipient_name"] = first.get("name", "") or ""
        st.session_state["recipient_group"] = first.get("group", "") or ""
        st.session_state["contact_id"] = first.get("contact_id")
    else:
        st.session_state["recipient_name"] = ""
        st.session_state["recipient_group"] = ""
        st.session_state["contact_id"] = None


def _current_recipients() -> List[Dict[str, Optional[str]]]:
    """Devuelve la lista de destinatarios (1 o N). Reconstruye desde campos legacy si hace falta."""
    recipients = st.session_state.get("recipients") or []
    if recipients:
        return recipients
    name = (st.session_state.get("recipient_name") or "").strip()
    if not name:
        return []
    return [
        {
            "name": name,
            "group": (st.session_state.get("recipient_group") or "").strip(),
            "contact_id": st.session_state.get("contact_id"),
        }
    ]


def _safe_filename(name: str) -> str:
    cleaned = name.replace("/", "_").replace("\\", "_").strip()
    return cleaned.replace(" ", "_") or "tarjeta"


# --- Step 1: Destinatario ---
if step == 1:
    st.subheader("Destinatario")
    contacts = contacts_module.list_contacts()

    mode = st.radio(
        "¿Cómo quieres elegir al destinatario?",
        options=["Contacto existente", "Nuevo contacto", "Varios destinatarios"],
        horizontal=True,
        index=0 if contacts else 1,
        help=(
            "«Varios destinatarios» genera una tarjeta independiente por persona "
            "compartiendo el mismo texto de dedicatoria."
        ),
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
                    _set_recipients(
                        [
                            {
                                "name": chosen.name,
                                "group": chosen.group,
                                "contact_id": chosen.id,
                            }
                        ]
                    )
                    _go(2)
    elif mode == "Nuevo contacto":
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
            _set_recipients(
                [
                    {
                        "name": new_name.strip(),
                        "group": (new_group or "").strip(),
                        "contact_id": contact_id,
                    }
                ]
            )
            _go(2)
    else:  # Varios destinatarios
        st.caption(
            "Vas a generar **la misma dedicatoria** para varias personas. Cada destinatario "
            "tendrá su propia tarjeta (PDF + PNG) con su nombre, y se guardará una entrada "
            "independiente en el historial."
        )

        selected_existing: List[Contact] = []
        if contacts:
            with st.expander("👤 Elegir entre tus contactos existentes", expanded=True):
                groups = sorted({c.group for c in contacts if c.group})
                group_filter = st.selectbox(
                    "Filtrar por grupo",
                    options=["(todos)", *groups],
                    key="multi_group_filter",
                )
                visible = [
                    c for c in contacts if group_filter == "(todos)" or c.group == group_filter
                ]
                if visible:
                    labels = [c.label for c in visible]
                    picks = st.multiselect(
                        "Selecciona varios destinatarios",
                        options=labels,
                        key="multi_existing_picks",
                    )
                    selected_existing = [visible[labels.index(p)] for p in picks]
                else:
                    st.info("No hay destinatarios en ese grupo.")
        else:
            st.info("Aún no tienes contactos guardados. Añádelos abajo manualmente.")

        with st.expander("✍️ Añadir destinatarios nuevos (uno por línea)", expanded=not contacts):
            existing_groups = contacts_module.list_groups()
            extra_names_raw = st.text_area(
                "Nombres adicionales",
                placeholder="Ej.\nAna\nLuis\nMarta",
                key="multi_extra_names",
                height=120,
            )
            colg, colsave = st.columns([2, 1])
            with colg:
                if existing_groups:
                    extra_group_pick = st.selectbox(
                        "Grupo para los nuevos",
                        options=["— Sin grupo —", "— Nuevo —", *existing_groups],
                        key="multi_extra_group_pick",
                    )
                    if extra_group_pick == "— Nuevo —":
                        extra_group = st.text_input(
                            "Nombre del nuevo grupo",
                            value="",
                            key="multi_extra_group_new",
                        )
                    elif extra_group_pick == "— Sin grupo —":
                        extra_group = ""
                    else:
                        extra_group = extra_group_pick
                else:
                    extra_group = st.text_input(
                        "Grupo para los nuevos (opcional)",
                        value="",
                        key="multi_extra_group_text",
                    )
            with colsave:
                save_new = st.checkbox(
                    "Guardar como contactos",
                    value=True,
                    key="multi_save_new",
                )

        extra_names = [
            n.strip()
            for n in (extra_names_raw or "").splitlines()
            if n.strip()
        ]

        # Lista final
        recipients: List[Dict[str, Optional[str]]] = []
        seen_keys: set = set()
        for c in selected_existing:
            key = (c.name.lower(), (c.group or "").lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            recipients.append({"name": c.name, "group": c.group, "contact_id": c.id})
        for name in extra_names:
            key = (name.lower(), (extra_group or "").lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            recipients.append(
                {"name": name, "group": (extra_group or "").strip(), "contact_id": None}
            )

        if recipients:
            st.markdown(f"**Destinatarios seleccionados ({len(recipients)}):**")
            chips = "  ".join(
                f"🏷️ {r['name']}" + (f" · _{r['group']}_" if r.get("group") else "")
                for r in recipients
            )
            st.markdown(chips)
        else:
            st.caption("Aún no has seleccionado a nadie.")

        if st.button(
            "Continuar →",
            type="primary",
            disabled=len(recipients) == 0,
            key="multi_continue",
        ):
            # Guarda contactos nuevos si toca
            final_recipients: List[Dict[str, Optional[str]]] = []
            for r in recipients:
                cid = r.get("contact_id")
                if cid is None and save_new and r["name"]:
                    contact = contacts_module.find_or_create(r["name"], r.get("group") or "")
                    cid = contact.id
                final_recipients.append(
                    {"name": r["name"], "group": r.get("group") or "", "contact_id": cid}
                )
            _set_recipients(final_recipients)
            _go(2)

# --- Step 2: Texto (audio o tecleado) ---
elif step == 2:
    st.subheader("Texto de la dedicatoria")
    _recipients_now = _current_recipients()
    if len(_recipients_now) > 1:
        nombres = ", ".join(r["name"] for r in _recipients_now)
        st.caption(
            f"Para **{len(_recipients_now)} destinatarios** (mismo texto, una tarjeta por persona): {nombres}"
        )
    else:
        st.caption(
            f"Para: **{st.session_state['recipient_name']}**"
            + (f" · {st.session_state['recipient_group']}" if st.session_state["recipient_group"] else "")
        )
    tab_audio, tab_text = st.tabs(["🎤 Grabar audio", "⌨️ Escribir texto"])

    with tab_audio:
        if not cfg.is_transcription_ready:
            st.warning(
                "No hay clave de IA para transcripción. Añade `GROQ_API_KEY` (gratis), "
                "`OPENAI_API_KEY` o `GOOGLE_API_KEY` a tus secrets."
            )
        st.info(
            "💡 **Para que se grabe todo bien**: pulsa **Grabar**, espera ~1s en silencio antes de hablar, "
            "di la dedicatoria, y al terminar espera otro segundo antes de pulsar **Detener**. "
            "Puedes **pausar** y reanudar las veces que quieras, o pulsar **🔄 Volver a grabar** para empezar de nuevo."
        )

        rec = audio_recorder(key=f"recorder_{st.session_state['audio_widget_rev']}")
        if rec is not None:
            st.session_state["audio_bytes"] = rec.audio_bytes
            st.session_state["audio_filename"] = rec.filename
            st.caption(
                f"👆 Escucha tu grabación arriba antes de transcribir "
                f"({rec.size // 1024} KB · {rec.duration_ms // 1000}s). "
                "Si falta algo, pulsa **🔄 Volver a grabar**."
            )
            cols_a = st.columns([1, 1])
            with cols_a[0]:
                if st.button("Transcribir y corregir", type="primary"):
                    provider_label = {
                        "groq": "Whisper en Groq",
                        "openai": "Whisper (OpenAI)",
                        "gemini": "Gemini",
                    }.get(cfg.transcription_provider, "IA")
                    with st.spinner(f"Transcribiendo con {provider_label}..."):
                        try:
                            raw = transcribe(rec.audio_bytes, filename=rec.filename)
                        except Exception as e:  # noqa: BLE001
                            st.error(f"Error transcribiendo. {friendly_ai_error(e)}")
                            st.stop()
                    with st.spinner("Corrigiendo con IA..."):
                        try:
                            corrected = correct_dedication(raw)
                        except Exception as e:  # noqa: BLE001
                            st.warning(
                                f"No se pudo corregir, uso el texto crudo. {friendly_ai_error(e)}"
                            )
                            corrected = raw
                    st.session_state["input_mode"] = "audio"
                    st.session_state["raw_input"] = raw
                    st.session_state["corrected_text"] = corrected
                    st.session_state["final_text"] = corrected
                    st.session_state["final_text_rev"] += 1
                    st.session_state["versions"] = [
                        {"label": "Transcripción cruda", "text": raw},
                        {"label": "Corrección IA", "text": corrected},
                    ]
                    _go(3)
            with cols_a[1]:
                if st.button("🔄 Descartar y volver a grabar", help="Limpia la grabación actual y resetea el grabador"):
                    st.session_state["audio_bytes"] = None
                    st.session_state["audio_filename"] = None
                    st.session_state["audio_widget_rev"] += 1
                    st.rerun()
        else:
            # No hay grabación todavía: limpia restos por si veníamos de un descarte
            st.session_state["audio_bytes"] = None
            st.session_state["audio_filename"] = None

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
                        st.warning(
                            f"No se pudo corregir, uso el texto crudo. {friendly_ai_error(e)}"
                        )
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

    tab_edit, tab_phrases, tab_compare = st.tabs(
        ["📝 Editar", "🪄 Editar frases", f"🔍 Comparar versiones ({len(versions)})"]
    )

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
            key=f"final_text_area_{st.session_state['final_text_rev']}",
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
                            st.session_state["final_text_rev"] += 1
                            st.session_state["sentences_rev"] += 1
                        else:
                            st.info("La IA devolvió el mismo texto. Prueba con otras instrucciones.")
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(friendly_ai_error(e))
        with rcols[1]:
            if st.button("🤖 Re-corregir desde cero", help="Vuelve a aplicar la corrección base sobre el texto crudo original"):
                with st.spinner("Corrigiendo..."):
                    try:
                        corrected = correct_dedication(st.session_state["raw_input"])
                        if corrected != st.session_state["final_text"]:
                            versions.append({"label": "Re-corrección", "text": corrected})
                            st.session_state["versions"] = versions
                            st.session_state["final_text_rev"] += 1
                            st.session_state["sentences_rev"] += 1
                        st.session_state["corrected_text"] = corrected
                        st.session_state["final_text"] = corrected
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(friendly_ai_error(e))

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

    with tab_phrases:
        st.caption(
            "Aquí puedes **borrar** frases sueltas del texto o **pedirle a la IA que reescriba "
            "solo las que marques**, sin tocar el resto."
        )
        current = st.session_state["final_text"] or ""
        sentences = split_into_sentences(current)
        if not sentences:
            st.info("Escribe primero el texto en la pestaña «📝 Editar».")
        else:
            st.markdown(f"**Frases detectadas:** {len(sentences)}")
            sel_key_base = f"phrase_sel_{st.session_state['sentences_rev']}"
            selected_indices: List[int] = []
            for i, s in enumerate(sentences):
                col_chk, col_txt = st.columns([1, 20])
                with col_chk:
                    checked = st.checkbox(
                        " ",
                        key=f"{sel_key_base}_{i}",
                        label_visibility="collapsed",
                    )
                with col_txt:
                    st.markdown(f"_{i+1}._ {s}")
                if checked:
                    selected_indices.append(i)

            st.divider()
            instr_p = st.text_input(
                "Instrucción para reescribir las frases marcadas",
                placeholder="Ej. hazlas más cortas · hazlas más formales · cámbialas por una sola frase cariñosa",
                key=f"phrase_instr_{st.session_state['sentences_rev']}",
            )
            pcols = st.columns([1, 1, 3])
            with pcols[0]:
                disabled_rewrite = (
                    not selected_indices or not instr_p.strip() or not cfg.is_ai_ready
                )
                if st.button(
                    "✏️ Reescribir seleccionadas con IA",
                    type="primary",
                    disabled=disabled_rewrite,
                    key=f"phrase_rewrite_{st.session_state['sentences_rev']}",
                ):
                    # Reescribimos cada frase marcada por separado para mantener la posición.
                    new_sentences: List[Optional[str]] = list(sentences)
                    errored = False
                    with st.spinner(
                        f"Reescribiendo {len(selected_indices)} frase(s) con la IA..."
                    ):
                        for idx in selected_indices:
                            original = sentences[idx]
                            try:
                                rewritten = rewrite_fragment(original, instr_p)
                            except Exception as e:  # noqa: BLE001
                                st.error(
                                    f"Fallo reescribiendo la frase {idx + 1}. {friendly_ai_error(e)}"
                                )
                                errored = True
                                continue
                            if rewritten:
                                new_sentences[idx] = rewritten.strip()
                    if not errored:
                        new_text = join_sentences(new_sentences)
                        if new_text and new_text != current:
                            label = (
                                f"Reescritura por frases ({len(selected_indices)}): "
                                f"«{instr_p.strip()[:40]}{'…' if len(instr_p.strip()) > 40 else ''}»"
                            )
                            versions.append({"label": label, "text": new_text})
                            st.session_state["versions"] = versions
                            st.session_state["final_text"] = new_text
                            st.session_state["corrected_text"] = new_text
                            st.session_state["final_text_rev"] += 1
                            st.session_state["sentences_rev"] += 1
                            st.toast("Frases actualizadas con IA.")
                            st.rerun()
                        else:
                            st.info("La IA devolvió el mismo texto. Prueba con otras instrucciones.")

            with pcols[1]:
                if st.button(
                    "🗑️ Borrar seleccionadas",
                    disabled=not selected_indices,
                    key=f"phrase_delete_{st.session_state['sentences_rev']}",
                ):
                    kept = [s for i, s in enumerate(sentences) if i not in selected_indices]
                    new_text = join_sentences(kept)
                    if new_text != current:
                        versions.append(
                            {
                                "label": f"Borrado de {len(selected_indices)} frase(s)",
                                "text": new_text,
                            }
                        )
                        st.session_state["versions"] = versions
                    st.session_state["final_text"] = new_text
                    st.session_state["corrected_text"] = new_text
                    st.session_state["final_text_rev"] += 1
                    st.session_state["sentences_rev"] += 1
                    st.toast("Frases eliminadas.")
                    st.rerun()

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
                st.session_state["final_text_rev"] += 1
                st.toast("Texto final actualizado.")
                st.rerun()

# --- Step 4: Plantilla ---
elif step == 4:
    st.subheader("Plantilla")
    templates = templates_module.list_templates()
    recipients_now = _current_recipients()
    multi = len(recipients_now) > 1

    if multi:
        st.info(
            f"Vas a generar **{len(recipients_now)} tarjetas** con el mismo texto, "
            f"una por destinatario: {', '.join(r['name'] for r in recipients_now)}."
        )

    def _save_pending_for_all() -> List[str]:
        ids: List[str] = []
        targets = recipients_now or [
            {
                "name": st.session_state["recipient_name"],
                "group": st.session_state["recipient_group"],
                "contact_id": st.session_state["contact_id"],
            }
        ]
        for r in targets:
            saved = history_module.save_pending(
                recipient_name=r["name"],
                recipient_group=r.get("group") or "",
                contact_id=r.get("contact_id"),
                input_mode=st.session_state["input_mode"],
                raw_input=st.session_state["raw_input"],
                corrected_text=st.session_state["corrected_text"],
                final_text=st.session_state["final_text"],
                audio_bytes=st.session_state.get("audio_bytes") if st.session_state["input_mode"] == "audio" else None,
                is_generic=st.session_state["is_generic"],
            )
            ids.append(saved.id)
        return ids

    if not templates:
        st.warning("Todavía no tienes plantillas. Puedes guardar la dedicatoria como pendiente y generar el archivo de impresión más tarde, cuando subas una plantilla.")
        cols = st.columns([1, 3])
        with cols[0]:
            _back_button(3)
        with cols[1]:
            if st.button("💾 Guardar como pendiente", type="primary"):
                try:
                    ids = _save_pending_for_all()
                    st.session_state["saved_dedication_ids"] = ids
                    st.session_state["saved_dedication_id"] = ids[0] if ids else None
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

        preview_name = (
            recipients_now[0]["name"] if recipients_now else st.session_state["recipient_name"]
        )
        with st.spinner("Generando vista previa..."):
            try:
                preview = render_preview(chosen, preview_name, st.session_state["final_text"])
                caption = "Vista previa" + (
                    f" (mostrando «{preview_name}»; las demás serán idénticas con su nombre)"
                    if multi
                    else ""
                )
                st.image(preview, use_container_width=True, caption=caption)
            except Exception as e:  # noqa: BLE001
                st.error(f"Error en preview: {e}")

        # Toggle para excluir el reverso (sólo tiene sentido si la plantilla lo tiene)
        if chosen.has_back:
            st.session_state["include_back"] = st.checkbox(
                "📄 Incluir reverso en el PDF/PNG",
                value=st.session_state.get("include_back", True),
                key="include_back_toggle",
                help=(
                    "Esta plantilla tiene reverso. Desmarca esta casilla si quieres "
                    "generar solo la parte delantera de la tarjeta para esta dedicatoria."
                ),
            )
        else:
            st.session_state["include_back"] = False

        cols = st.columns([1, 1, 2])
        with cols[0]:
            _back_button(3)
        with cols[1]:
            if st.button("💾 Guardar pendiente"):
                try:
                    ids = _save_pending_for_all()
                    st.session_state["saved_dedication_ids"] = ids
                    st.session_state["saved_dedication_id"] = ids[0] if ids else None
                    st.session_state["saved_as_pending"] = True
                    _go(5)
                except Exception as e:  # noqa: BLE001
                    st.error(f"Error guardando: {e}")
        with cols[2]:
            label_btn = (
                f"Generar {len(recipients_now)} tarjetas ahora →" if multi else "Generar tarjeta ahora →"
            )
            if st.button(label_btn, type="primary"):
                _go(5)

# --- Step 5: Export ---
elif step == 5:
    if st.session_state.get("saved_as_pending"):
        pending_ids = st.session_state.get("saved_dedication_ids") or (
            [st.session_state["saved_dedication_id"]]
            if st.session_state.get("saved_dedication_id")
            else []
        )
        if len(pending_ids) > 1:
            st.success(f"✅ {len(pending_ids)} dedicatorias guardadas como **pendientes**.")
        else:
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
        st.stop()

    recipients_now = _current_recipients()
    if not recipients_now:
        st.error("No hay destinatarios. Vuelve al paso 1.")
        _back_button(1)
        st.stop()

    rendered_items = st.session_state.get("_rendered_items") or []

    if not rendered_items:
        try:
            text_overflow = False
            name_overflow = False
            audio_bytes = (
                st.session_state.get("audio_bytes")
                if st.session_state["input_mode"] == "audio"
                else None
            )
            include_back = bool(st.session_state.get("include_back", True)) and template.has_back
            progress = st.progress(0.0, text="Renderizando...")
            for i, r in enumerate(recipients_now):
                name = r["name"]
                progress.progress(
                    i / max(1, len(recipients_now)),
                    text=f"Renderizando «{name}» ({i + 1}/{len(recipients_now)})...",
                )
                pdf_bytes, pdf_warn = render_pdf(
                    template,
                    name,
                    st.session_state["final_text"],
                    include_back=include_back,
                )
                png_bytes, png_warn = render_png(template, name, st.session_state["final_text"])
                back_png_bytes = render_back_png(template) if include_back else None
                text_overflow = text_overflow or pdf_warn.get("text_overflow") or png_warn.get("text_overflow")
                name_overflow = name_overflow or pdf_warn.get("name_overflow") or png_warn.get("name_overflow")

                saved = history_module.save_generated(
                    template=template,
                    recipient_name=name,
                    recipient_group=r.get("group") or "",
                    contact_id=r.get("contact_id"),
                    input_mode=st.session_state["input_mode"],
                    raw_input=st.session_state["raw_input"],
                    corrected_text=st.session_state["corrected_text"],
                    final_text=st.session_state["final_text"],
                    pdf_bytes=pdf_bytes,
                    png_bytes=png_bytes,
                    back_png_bytes=back_png_bytes,
                    # Solo guardamos el audio en la primera para no duplicarlo
                    audio_bytes=audio_bytes if i == 0 else None,
                    is_generic=st.session_state["is_generic"],
                )
                rendered_items.append(
                    {
                        "recipient_name": name,
                        "recipient_group": r.get("group") or "",
                        "dedication_id": saved.id,
                        "pdf": pdf_bytes,
                        "png": png_bytes,
                        "back_png": back_png_bytes,
                    }
                )
            progress.progress(1.0, text="Listo.")
            progress.empty()
            if text_overflow:
                st.warning("⚠️ El texto no cabe completamente en la zona definida. Considera reducir el tamaño de fuente o ampliar la zona en la plantilla.")
            if name_overflow:
                st.warning("⚠️ El nombre no cabe en su zona en alguna tarjeta.")

            st.session_state["_rendered_items"] = rendered_items
            st.session_state["saved_dedication_ids"] = [it["dedication_id"] for it in rendered_items]
            st.session_state["saved_dedication_id"] = rendered_items[0]["dedication_id"]
            st.session_state["_pdf_bytes"] = rendered_items[0]["pdf"]
            st.session_state["_png_bytes"] = rendered_items[0]["png"]
            st.session_state["_back_png_bytes"] = rendered_items[0]["back_png"]
        except Exception as e:  # noqa: BLE001
            st.error(f"Error generando: {e}")
            _back_button(4)
            st.stop()

    multi = len(rendered_items) > 1
    has_back = bool(rendered_items[0]["back_png"])

    if multi:
        st.success(
            f"✅ {len(rendered_items)} dedicatorias generadas y guardadas en el historial."
        )
    else:
        st.success("Dedicatoria generada y guardada en el historial.")

    # Selector de destinatario para previsualizar/descargar individualmente
    if multi:
        names = [
            f"{i + 1}. {it['recipient_name']}" + (f" · {it['recipient_group']}" if it['recipient_group'] else "")
            for i, it in enumerate(rendered_items)
        ]
        pick_label = st.selectbox("Ver tarjeta de…", options=names, index=0)
        active_idx = names.index(pick_label)
    else:
        active_idx = 0

    active = rendered_items[active_idx]

    # Vista de las dos caras si hay reverso, o sólo el frente.
    if active["back_png"]:
        tab_front, tab_back = st.tabs(["📄 Frente (con texto)", "🔄 Reverso"])
        with tab_front:
            st.image(active["png"], use_container_width=True, caption=f"Frente — {active['recipient_name']}")
        with tab_back:
            st.image(active["back_png"], use_container_width=True, caption="Reverso")
    else:
        st.image(active["png"], use_container_width=True, caption=f"Tarjeta — {active['recipient_name']}")

    slug = _safe_filename(active["recipient_name"])
    n_cols = 3 if active["back_png"] else 2
    cols = st.columns(n_cols)
    with cols[0]:
        label = "⬇️ PDF (frente + reverso)" if active["back_png"] else "⬇️ Descargar PDF (imprenta)"
        st.download_button(
            label,
            data=active["pdf"],
            file_name=f"dedicatoria_{slug}.pdf",
            mime="application/pdf",
            use_container_width=True,
            key=f"dl_pdf_{active_idx}",
        )
    with cols[1]:
        st.download_button(
            "⬇️ PNG frente (300 dpi)",
            data=active["png"],
            file_name=f"dedicatoria_{slug}_frente.png",
            mime="image/png",
            use_container_width=True,
            key=f"dl_png_{active_idx}",
        )
    if active["back_png"]:
        with cols[2]:
            st.download_button(
                "⬇️ PNG reverso (300 dpi)",
                data=active["back_png"],
                file_name=f"dedicatoria_{slug}_reverso.png",
                mime="image/png",
                use_container_width=True,
                key=f"dl_back_{active_idx}",
            )

    if multi:
        st.divider()
        st.markdown("**📦 Descargar todo en un ZIP**")
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for it in rendered_items:
                s = _safe_filename(it["recipient_name"])
                zf.writestr(f"{s}/dedicatoria_{s}.pdf", it["pdf"])
                zf.writestr(f"{s}/dedicatoria_{s}_frente.png", it["png"])
                if it["back_png"]:
                    zf.writestr(f"{s}/dedicatoria_{s}_reverso.png", it["back_png"])
        st.download_button(
            f"⬇️ ZIP con las {len(rendered_items)} tarjetas (PDF + PNG)",
            data=zip_buf.getvalue(),
            file_name="dedicatorias.zip",
            mime="application/zip",
            use_container_width=True,
            key="dl_zip_all",
        )

    is_generic = st.checkbox(
        "Marcar estas dedicatorias como genéricas (para reutilizar con otros destinatarios)"
        if multi
        else "Marcar esta dedicatoria como genérica (para reutilizar con otros destinatarios)",
        value=st.session_state["is_generic"],
        key="generic_toggle",
    )
    if is_generic != st.session_state["is_generic"]:
        st.session_state["is_generic"] = is_generic
        for it in rendered_items:
            saved = history_module.get_dedication(it["dedication_id"])
            if saved:
                saved.is_generic = is_generic
                history_module.update_dedication(saved)
        st.toast("Estado actualizado.")

    st.divider()
    if st.button("Crear otra dedicatoria"):
        _reset_flow()
        st.rerun()
