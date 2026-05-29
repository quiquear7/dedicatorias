from __future__ import annotations

from datetime import datetime

import streamlit as st

from core import history as history_module
from core import templates as templates_module
from core.auth import logout_button, require_login
from core.config import get_config, get_storage
from core.rendering import render_preview


def _render_dedication_text_block(d, *, key_prefix: str = "") -> None:
    """Bloque común para ver o editar el texto, nombre y grupo de una
    dedicatoria. Se usa en los expanders individuales de Pendientes y de
    Generadas. La transcripción cruda y otras acciones se manejan fuera.

    Una dedicatoria ya generada puede aparecer en las dos pestañas a la
    vez (en la lista de Pendientes con la marca «✅ Generada» y en la
    lista de Generadas), así que cada llamada recibe un `key_prefix`
    distinto para evitar colisiones de claves de widget en Streamlit.
    """
    edit_key = f"_edit_{key_prefix}{d.id}"
    if st.session_state.get(edit_key):
        new_name = st.text_input(
            "Nombre del destinatario",
            value=d.recipient_name,
            key=f"edit_name_{key_prefix}{d.id}",
        )
        new_group = st.text_input(
            "Grupo",
            value=d.recipient_group or "",
            key=f"edit_group_{key_prefix}{d.id}",
        )
        new_text = st.text_area(
            "Texto de la dedicatoria",
            value=d.final_text,
            key=f"edit_text_{key_prefix}{d.id}",
            height=140,
        )
        ecols = st.columns([1, 1, 4])
        with ecols[0]:
            if st.button(
                "💾 Guardar",
                key=f"edit_save_{key_prefix}{d.id}",
                type="primary",
            ):
                cleaned_name = (new_name or "").strip()
                if not cleaned_name:
                    st.error("El nombre del destinatario no puede estar vacío.")
                else:
                    d.recipient_name = cleaned_name
                    d.recipient_group = (new_group or "").strip()
                    d.final_text = new_text or ""
                    history_module.update_dedication(d)
                    st.session_state.pop(edit_key, None)
                    if d.is_pending:
                        st.toast("Dedicatoria actualizada.")
                    else:
                        st.toast(
                            "Texto actualizado. La tarjeta renderizada ya no "
                            "coincide; pulsa «Re-generar» para refrescarla.",
                            icon="⚠️",
                        )
                    st.rerun()
        with ecols[1]:
            if st.button("✋ Cancelar", key=f"edit_cancel_{key_prefix}{d.id}"):
                st.session_state.pop(edit_key, None)
                st.rerun()
    else:
        st.markdown("**Texto:**")
        st.markdown(f"> {d.final_text}")
        if st.button(
            "✏️ Editar texto, nombre y grupo",
            key=f"edit_btn_{key_prefix}{d.id}",
            help="Modifica el texto, el nombre del destinatario o el grupo.",
        ):
            st.session_state[edit_key] = True
            st.rerun()


@st.cache_data(show_spinner="Componiendo PDF A4 imprenta…")
def _build_a4_imposed_pdf(signature: tuple, include_crop_guides: bool) -> tuple:
    """Compone un único PDF A4 con todas las dedicatorias indicadas
    intercalando anversos y reversos (cuadrícula 2×2). `signature` es una
    tupla inmutable de (id, png_path, back_png_path, extra_png_paths_tuple,
    card_w_mm, card_h_mm, template_id) por cada tarjeta seleccionada para
    que Streamlit pueda cachear el resultado.

    Cuando una dedicatoria está partida en varias tarjetas (no cabía entera),
    `extra_png_paths_tuple` lleva los frentes de la parte 2 en adelante. Se
    imponen consecutivamente en la cuadrícula 2×2, de modo que el reverso
    de la hoja par cae sobre cada una de las partes en el orden correcto.

    Si ninguna dedicatoria seleccionada trajera el reverso ya renderizado
    (caso típico: se añadió el reverso a la plantilla *después* de
    generarlas), caemos al reverso de la plantilla actual y lo renderizamos
    al vuelo para no obligar al usuario a re-renderizar 300 tarjetas.

    Devuelve `(pdf_bytes, missing_count, warning_text_or_None)`.
    """
    from core.config import get_storage as _get_storage
    from core import rendering as rendering_module
    from core import templates as templates_module

    storage = _get_storage()
    front_bytes_list: list = []
    back_bytes: bytes | None = None
    card_w: float | None = None
    card_h: float | None = None
    missing = 0
    sizes: set = set()
    template_ids_seen: list = []

    for _did, png_path, back_path, extra_paths, w_mm, h_mm, template_id in signature:
        if w_mm and h_mm:
            sizes.add((round(float(w_mm), 1), round(float(h_mm), 1)))
            if card_w is None:
                card_w, card_h = float(w_mm), float(h_mm)
        if template_id and template_id not in template_ids_seen:
            template_ids_seen.append(template_id)
        if not png_path:
            missing += 1
            continue
        try:
            front_bytes_list.append(storage.get(png_path))
        except Exception:  # noqa: BLE001
            missing += 1
            continue
        # Frentes adicionales si la dedicatoria estaba partida en varias tarjetas.
        for extra in (extra_paths or ()):
            try:
                front_bytes_list.append(storage.get(extra))
            except Exception:  # noqa: BLE001
                missing += 1
                continue
        if back_bytes is None and back_path:
            try:
                back_bytes = storage.get(back_path)
            except Exception:  # noqa: BLE001
                back_bytes = None

    fell_back_to_template_back = False
    if back_bytes is None:
        for tid in template_ids_seen:
            tpl = templates_module.get_template(tid)
            if tpl and tpl.has_back:
                try:
                    back_bytes = rendering_module.render_back_png(tpl)
                    fell_back_to_template_back = True
                    if card_w is None:
                        card_w, card_h = float(tpl.width_mm), float(tpl.height_mm)
                    break
                except Exception:  # noqa: BLE001
                    back_bytes = None

    warning = None
    if len(sizes) > 1:
        warning = (
            "⚠️ Hay tarjetas de varios tamaños físicos en la selección. "
            f"Se usa el de la primera ({card_w:.0f}×{card_h:.0f} mm). "
            "Para imprenta agrupa selecciones del mismo formato."
        )
    elif fell_back_to_template_back:
        warning = (
            "ℹ️ Ninguna de las dedicatorias seleccionadas guardaba el reverso "
            "(se generaron antes de añadirlo a la plantilla). He cogido el "
            "reverso actual de la plantilla para componer el A4."
        )

    pdf_bytes = rendering_module.render_imposed_a4_pdf(
        front_bytes_list,
        back_bytes,
        card_width_mm=card_w or 80.0,
        card_height_mm=card_h or 130.0,
        include_crop_guides=include_crop_guides,
    )
    return pdf_bytes, missing, warning


st.set_page_config(page_title="Historial", page_icon="📜", layout="wide")
require_login()
logout_button()
st.title("📜 Historial de dedicatorias")
st.caption("Consulta dedicatorias pasadas, vuelve a descargarlas o duplícalas para otra persona.")

cfg = get_config()
if not cfg.is_storage_ready:
    st.error("Almacenamiento no configurado.")
    st.stop()

dedications = history_module.list_dedications()
pending_list = [d for d in dedications if d.is_pending]
rendered_list = [d for d in dedications if not d.is_pending]
templates_all = templates_module.list_templates()
template_index = {t.id: t for t in templates_all}
storage = get_storage()

if not dedications:
    st.info("Todavía no has guardado ninguna dedicatoria. Ve a «Generar dedicatoria» para crear la primera.")
    st.stop()

tab_rendered, tab_pending = st.tabs([
    f"✅ Generadas ({len(rendered_list)})",
    f"⏳ Pendientes de plantilla ({len(pending_list)})",
])


# ============================================================================
# Pestaña: PENDIENTES — generación masiva
# ============================================================================
with tab_pending:
    # Pestaña «Pendientes»: pendientes + ya generadas (con marca), para poder
    # consultarlas o re-renderizarlas con otra plantilla sin salir de aquí.
    display_list = pending_list + rendered_list
    if not display_list:
        st.info("Todavía no tienes dedicatorias. Cuando guardes una aparecerá aquí.")
    else:
        st.markdown(
            f"**{len(pending_list)}** pendientes por generar · "
            f"**{len(rendered_list)}** ya generadas (puedes re-renderizar con otra plantilla)."
        )
        if not templates_all:
            st.warning("Necesitas tener al menos una plantilla creada en la página «Plantillas» para renderizar.")
        else:
            tlabels = [f"{t.name} ({t.width_mm:.0f}×{t.height_mm:.0f} mm)" for t in templates_all]
            tchoice = st.selectbox("Plantilla a usar para generar", options=tlabels, key="bulk_tpl")
            chosen_template = templates_all[tlabels.index(tchoice)]

            # Vista previa: cualquiera de la lista combinada.
            with st.expander("👁️ Vista previa con esta plantilla", expanded=False):
                preview_labels = [
                    f"{'📄' if p.is_pending else '✅'} {p.recipient_name} — "
                    f"{p.final_text[:40]}{'…' if len(p.final_text) > 40 else ''}"
                    for p in display_list
                ]
                preview_idx = st.selectbox(
                    "Dedicatoria a previsualizar",
                    options=range(len(display_list)),
                    format_func=lambda i: preview_labels[i],
                    key="bulk_preview_pick",
                )
                preview_target = display_list[preview_idx]
                with st.spinner("Renderizando preview..."):
                    try:
                        png_bytes = render_preview(
                            chosen_template,
                            preview_target.recipient_name,
                            preview_target.final_text,
                        )
                        st.image(png_bytes, use_container_width=True, caption=f"Preview: {preview_target.recipient_name}")
                    except Exception as e:  # noqa: BLE001
                        st.warning(f"No se pudo generar preview: {e}")

            # Lote de generación: estado persistente entre reruns para permitir
            # pausar/reanudar/cancelar a media tanda. Cada rerun procesa UNA
            # dedicatoria y vuelve a hacer rerun, así el botón «⏸ Pausar» se
            # respeta entre tarjetas (Streamlit no es preemptible dentro del
            # mismo callback).
            JOB_KEY = "_pending_render_job"
            job = st.session_state.get(JOB_KEY)

            if job:
                total = max(1, int(job.get("total", 1)))
                ok_n = len(job.get("ok", []))
                err_n = len(job.get("errors", []))
                processed = ok_n + err_n
                finished = not job.get("remaining")
                paused = bool(job.get("paused", False))

                if finished:
                    status_line = "✅ Finalizado"
                elif paused:
                    status_line = "⏸ Pausado"
                else:
                    status_line = "⏳ Renderizando…"

                st.progress(
                    min(processed / total, 1.0),
                    text=(
                        f"{status_line} — {processed}/{total} hechas · "
                        f"{ok_n} ok · {err_n} errores"
                    ),
                )

                if not finished:
                    ctrl = st.columns([1, 1, 1])
                    with ctrl[0]:
                        if paused:
                            if st.button(
                                "▶️ Reanudar",
                                type="primary",
                                key="job_resume",
                                use_container_width=True,
                            ):
                                job["paused"] = False
                                st.session_state[JOB_KEY] = job
                                st.rerun()
                        else:
                            if st.button(
                                "⏸ Pausar",
                                key="job_pause",
                                use_container_width=True,
                                help=(
                                    "Detiene el lote tras terminar la tarjeta "
                                    "actual. Las ya hechas se conservan."
                                ),
                            ):
                                job["paused"] = True
                                st.session_state[JOB_KEY] = job
                                st.rerun()
                    with ctrl[1]:
                        if st.button(
                            "✋ Cancelar",
                            key="job_cancel",
                            use_container_width=True,
                            help=(
                                "Descarta el resto del lote. Las dedicatorias "
                                "ya renderizadas quedan en «Generadas»; el "
                                "resto sigue como Pendiente."
                            ),
                        ):
                            done = ok_n
                            left = len(job.get("remaining", []))
                            st.session_state.pop(JOB_KEY, None)
                            st.toast(
                                f"Generación cancelada: {done} hechas, "
                                f"{left} sin renderizar."
                            )
                            st.rerun()
                    with ctrl[2]:
                        st.caption(
                            "Procesa de una en una; el botón ⏸ Pausar surte "
                            "efecto entre tarjetas."
                        )
                else:
                    st.success(
                        f"Generadas {ok_n} dedicatoria(s). "
                        + (f"{err_n} fallaron." if err_n else "")
                    )
                    if job.get("errors"):
                        with st.expander(f"⚠️ {err_n} errores"):
                            for err in job["errors"]:
                                st.code(str(err))
                    if st.button("Cerrar resumen", key="job_close"):
                        st.session_state.pop(JOB_KEY, None)
                        st.rerun()

                # Procesa exactamente UNA dedicatoria por rerun si el job
                # sigue vivo y no está pausado; luego vuelve a renderizar la
                # página para reflejar el avance y leer botones nuevos.
                if not paused and not finished:
                    tpl = next(
                        (t for t in templates_all if t.id == job["template_id"]),
                        None,
                    )
                    if tpl is None:
                        job["errors"].append(
                            {"id": "*", "error": "Plantilla no encontrada."}
                        )
                        job["remaining"] = []
                        st.session_state[JOB_KEY] = job
                        st.rerun()
                    else:
                        did = job["remaining"][0]
                        try:
                            history_module.render_pending(did, tpl)
                            job["ok"].append(did)
                        except Exception as e:  # noqa: BLE001
                            job["errors"].append({"id": did, "error": str(e)})
                        job["remaining"] = job["remaining"][1:]
                        st.session_state[JOB_KEY] = job
                        st.rerun()
            else:
                st.markdown("**Selecciona qué dedicatorias renderizar:**")
                sel_cols = st.columns([1, 1, 1, 1])
                with sel_cols[0]:
                    if st.button(
                        "☑️ Sólo pendientes",
                        key="pending_sel_pending",
                        use_container_width=True,
                        help="Marca todas las pendientes y desmarca las ya generadas.",
                    ):
                        for d in display_list:
                            st.session_state[f"bulk_inc_{d.id}"] = d.is_pending
                        st.rerun()
                with sel_cols[1]:
                    if st.button(
                        "☑️ Todas",
                        key="pending_sel_all",
                        use_container_width=True,
                        help="Incluye también las ya generadas (se re-renderizarán).",
                    ):
                        for d in display_list:
                            st.session_state[f"bulk_inc_{d.id}"] = True
                        st.rerun()
                with sel_cols[2]:
                    if st.button(
                        "⬜ Desmarcar todas",
                        key="pending_sel_clear",
                        use_container_width=True,
                        help="Quita la selección de todas las dedicatorias de la lista.",
                    ):
                        for d in display_list:
                            st.session_state[f"bulk_inc_{d.id}"] = False
                        st.rerun()

                head = st.columns([1, 3, 3, 4, 1])
                head[0].markdown("**Estado**")
                head[1].markdown("**Destinatario**")
                head[2].markdown("**Grupo**")
                head[3].markdown("**Texto (resumen)**")
                head[4].markdown("**Incluir**")

                chosen_ids: list[str] = []
                for d in display_list:
                    row = st.columns([1, 3, 3, 4, 1])
                    row[0].write("📄 Pendiente" if d.is_pending else "✅ Generada")
                    row[1].write(d.recipient_name)
                    row[2].write(d.recipient_group or "—")
                    preview_text = (d.final_text[:80] + "…") if len(d.final_text) > 80 else d.final_text
                    row[3].caption(preview_text)
                    included = row[4].checkbox(
                        "incluir",
                        value=d.is_pending,
                        key=f"bulk_inc_{d.id}",
                        label_visibility="collapsed",
                    )
                    if included:
                        chosen_ids.append(d.id)

                st.divider()
                cta = st.columns([2, 1, 1])
                cta[0].markdown(
                    f"**{len(chosen_ids)}** seleccionadas para renderizar con «{chosen_template.name}»"
                )
                with cta[1]:
                    if st.button(
                        "🚀 Generar seleccionadas",
                        type="primary",
                        disabled=not chosen_ids,
                        use_container_width=True,
                    ):
                        st.session_state[JOB_KEY] = {
                            "remaining": list(chosen_ids),
                            "ok": [],
                            "errors": [],
                            "template_id": chosen_template.id,
                            "total": len(chosen_ids),
                            "paused": False,
                        }
                        st.rerun()
                with cta[2]:
                    if st.button(
                        f"🗑️ Borrar ({len(chosen_ids)})",
                        key="pending_bulk_delete",
                        disabled=not chosen_ids,
                        use_container_width=True,
                    ):
                        st.session_state["_pending_bulk_confirm_delete"] = True
                        st.rerun()

                if st.session_state.get("_pending_bulk_confirm_delete") and chosen_ids:
                    st.error(
                        f"Vas a **borrar {len(chosen_ids)} dedicatoria(s)** del historial "
                        "(incluyendo texto, audio y, si las hay, las tarjetas renderizadas). "
                        "Esta acción **no se puede deshacer**."
                    )
                    cc = st.columns([1, 1, 4])
                    with cc[0]:
                        if st.button(
                            "🗑️ Sí, borrar",
                            key="pending_bulk_delete_yes",
                            type="primary",
                        ):
                            with st.spinner(
                                f"Borrando {len(chosen_ids)} dedicatoria(s)..."
                            ):
                                ok = 0
                                for did in chosen_ids:
                                    try:
                                        if history_module.delete_dedication(did):
                                            ok += 1
                                    except Exception:
                                        pass
                                    st.session_state.pop(f"bulk_inc_{did}", None)
                            st.session_state.pop("_pending_bulk_confirm_delete", None)
                            st.toast(f"{ok} dedicatoria(s) eliminada(s).")
                            st.rerun()
                    with cc[1]:
                        if st.button("✋ Cancelar", key="pending_bulk_delete_cancel"):
                            st.session_state.pop("_pending_bulk_confirm_delete", None)
                            st.rerun()

        st.divider()
        st.markdown("**O actúa individualmente:**")
        for d in display_list:
            status_icon = "⏳" if d.is_pending else "✅"
            with st.expander(
                f"{status_icon} {d.recipient_name} · {d.recipient_group or '(sin grupo)'} · {d.created_at[:10]}"
            ):
                _render_dedication_text_block(d, key_prefix="pending_")
                if d.input_mode == "audio":
                    with st.expander("Transcripción cruda"):
                        st.text(d.raw_input)
                ind_template = None
                if templates_all:
                    cols = st.columns([3, 1, 1])
                    with cols[0]:
                        ind_choice = st.selectbox(
                            "Plantilla",
                            options=tlabels,
                            key=f"ind_tpl_{d.id}",
                        )
                        ind_template = templates_all[tlabels.index(ind_choice)]
                    with cols[1]:
                        if st.button("👁️ Preview", key=f"ind_prev_{d.id}", use_container_width=True):
                            st.session_state[f"_show_prev_{d.id}"] = True
                    with cols[2]:
                        gen_label = "🔄 Re-generar" if not d.is_pending else "🚀 Generar"
                        if st.button(gen_label, key=f"ind_gen_{d.id}", use_container_width=True, type="primary"):
                            with st.spinner("Generando..."):
                                try:
                                    history_module.render_pending(d.id, ind_template)
                                    st.toast("Dedicatoria generada.")
                                    st.rerun()
                                except Exception as e:  # noqa: BLE001
                                    st.error(f"Error: {e}")
                    if st.session_state.get(f"_show_prev_{d.id}") and ind_template:
                        with st.spinner("Renderizando preview..."):
                            try:
                                png = render_preview(ind_template, d.recipient_name, d.final_text)
                                st.image(png, use_container_width=True)
                            except Exception as e:  # noqa: BLE001
                                st.warning(f"No se pudo generar preview: {e}")
                if st.button("🗑️ Eliminar", key=f"ind_del_{d.id}"):
                    history_module.delete_dedication(d.id)
                    st.toast("Eliminada.")
                    st.rerun()


# ============================================================================
# Pestaña: GENERADAS
# ============================================================================
with tab_rendered:
    if not rendered_list:
        st.info("Aún no hay dedicatorias generadas. Las que guardes con plantilla aparecerán aquí.")
    else:
        groups = sorted({d.recipient_group for d in rendered_list if d.recipient_group})

        with st.sidebar:
            st.markdown("### Filtros (Generadas)")
            name_filter = st.text_input("Buscar por nombre", value="")
            group_filter = st.selectbox("Grupo", options=["(todos)", *groups])
            template_filter = st.selectbox(
                "Plantilla",
                options=["(todas)", *[t.name for t in templates_all]],
            )
            only_generic = st.checkbox("Sólo genéricas", value=False)
            text_query = st.text_input("Buscar en el texto", value="")
            date_range = st.date_input("Rango de fechas", value=())

        start_date = end_date = None
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date, end_date = date_range

        def _matches(d) -> bool:
            if name_filter and name_filter.lower() not in d.recipient_name.lower():
                return False
            if group_filter != "(todos)" and d.recipient_group != group_filter:
                return False
            if template_filter != "(todas)":
                tpl = template_index.get(d.template_id) if d.template_id else None
                tpl_name = tpl.name if tpl else (d.template_snapshot or {}).get("name", "")
                if tpl_name != template_filter:
                    return False
            if only_generic and not d.is_generic:
                return False
            if text_query and text_query.lower() not in d.final_text.lower():
                return False
            if start_date or end_date:
                try:
                    created = datetime.fromisoformat(d.created_at).date()
                except ValueError:
                    return True
                if start_date and created < start_date:
                    return False
                if end_date and created > end_date:
                    return False
            return True

        filtered = [d for d in rendered_list if _matches(d)]

        # ----- Acciones masivas -----
        with st.container(border=True):
            selected = [
                d for d in filtered if st.session_state.get(f"sel_{d.id}", False)
            ]
            sel_count = len(selected)
            st.markdown(
                f"**{len(filtered)}** de {len(rendered_list)} dedicatorias visibles · "
                f"**{sel_count}** seleccionada(s)."
            )

            # Fila 1: selección (marcar / limpiar)
            sel_cols = st.columns([1, 1, 3])
            with sel_cols[0]:
                if st.button(
                    f"☑️ Marcar todas ({len(filtered)})",
                    key="hist_sel_all",
                    disabled=not filtered,
                    use_container_width=True,
                ):
                    for d in filtered:
                        st.session_state[f"sel_{d.id}"] = True
                    st.rerun()
            with sel_cols[1]:
                if st.button(
                    f"⬜ Desmarcar todas ({sel_count})",
                    key="hist_sel_clear",
                    disabled=sel_count == 0,
                    use_container_width=True,
                    help="Quita la selección de todas las dedicatorias visibles.",
                ):
                    for d in filtered:
                        st.session_state[f"sel_{d.id}"] = False
                    st.rerun()

            # Opciones de exportación del modo A4 (solo afectan al PDF A4).
            st.checkbox(
                "📏 Incluir guías de corte en el PDF A4 (líneas grises 0.5pt en los márgenes)",
                key="hist_a4_crop_guides",
                value=False,
                help=(
                    "Pinta líneas finas en los márgenes exteriores del A4 para "
                    "guiar el corte posterior con guillotina. No invade el área "
                    "de las tarjetas."
                ),
            )

            # Fila 2: acciones — exportación A4 + utilidades.
            act_cols = st.columns([1, 1, 1])
            with act_cols[0]:
                # PDF A4 con imposición 2×2 + reverso intercalado.
                if sel_count > 0:
                    a4_signature = tuple(
                        (
                            d.id,
                            d.card_png_path,
                            d.card_back_png_path,
                            tuple(d.card_extra_png_paths or ()),
                            (d.template_snapshot or {}).get("width_mm"),
                            (d.template_snapshot or {}).get("height_mm"),
                            d.template_id,
                        )
                        for d in selected
                    )
                    try:
                        a4_pdf_bytes, a4_missing, a4_warning = _build_a4_imposed_pdf(
                            a4_signature,
                            st.session_state.get("hist_a4_crop_guides", False),
                        )
                        st.download_button(
                            f"📄 PDF A4 imprenta ({sel_count})",
                            data=a4_pdf_bytes,
                            file_name="dedicatorias_a4_imprenta.pdf",
                            mime="application/pdf",
                            key="hist_bulk_a4_dl",
                            type="primary",
                            use_container_width=True,
                            help=(
                                "Único PDF A4 con cuadrícula 2×2 (4 tarjetas por "
                                "hoja) y hojas de reverso intercaladas, listo para "
                                "impresión dúplex en imprenta digital."
                            ),
                        )
                        if a4_missing:
                            st.caption(
                                f"⚠️ {a4_missing} frente(s) no se han podido leer del almacenamiento."
                            )
                        if a4_warning:
                            st.caption(a4_warning)
                    except Exception as e:  # noqa: BLE001
                        st.error(f"No se pudo construir el PDF A4: {e}")
                else:
                    st.button(
                        "📄 PDF A4 imprenta (0)",
                        key="hist_bulk_a4_dl_disabled",
                        disabled=True,
                        use_container_width=True,
                    )
            with act_cols[1]:
                if st.button(
                    f"🔄 A pendientes ({sel_count})",
                    key="hist_bulk_unrender",
                    disabled=sel_count == 0,
                    use_container_width=True,
                    help=(
                        "Mueve las tarjetas seleccionadas a «Pendientes»: borra el "
                        "PDF/PNG renderizado pero conserva el texto para re-renderizar."
                    ),
                ):
                    # Sin st.rerun(): si se aborta el run antes de renderizar los
                    # checkboxes sel_<id> (que están más abajo), Streamlit limpia
                    # su session_state y selected vuelve a quedar vacío.
                    st.session_state["_hist_bulk_confirm_unrender"] = True
            with act_cols[2]:
                if st.button(
                    f"🗑️ Borrar ({sel_count})",
                    key="hist_bulk_delete",
                    disabled=sel_count == 0,
                    use_container_width=True,
                ):
                    st.session_state["_hist_bulk_confirm_delete"] = True

            # Confirmación: mover a pendientes
            if st.session_state.get("_hist_bulk_confirm_unrender") and selected:
                st.warning(
                    f"Vas a mover **{sel_count} tarjeta(s) renderizada(s)** a «Pendientes»: "
                    "se borrarán sus archivos PDF/PNG, pero el texto y los datos del "
                    "destinatario se conservan en el historial."
                )
                cc = st.columns([1, 1, 4])
                with cc[0]:
                    if st.button(
                        "🔄 Sí, mover a pendientes",
                        key="hist_bulk_unrender_yes",
                        type="primary",
                    ):
                        with st.spinner(
                            f"Moviendo {sel_count} dedicatoria(s) a pendientes..."
                        ):
                            ok = 0
                            for d in selected:
                                try:
                                    if history_module.unrender_dedication(d.id):
                                        ok += 1
                                except Exception:
                                    pass
                                st.session_state.pop(f"sel_{d.id}", None)
                        st.session_state.pop("_hist_bulk_confirm_unrender", None)
                        st.toast(f"{ok} dedicatoria(s) movidas a pendientes.")
                        st.rerun()
                with cc[1]:
                    if st.button("✋ Cancelar", key="hist_bulk_unrender_cancel"):
                        st.session_state.pop("_hist_bulk_confirm_unrender", None)
                        st.rerun()

            # Confirmación: borrar
            if st.session_state.get("_hist_bulk_confirm_delete") and selected:
                st.error(
                    f"Vas a **borrar {sel_count} dedicatoria(s)** del historial. "
                    "Esto elimina texto, audio y tarjetas. Esta acción **no se puede deshacer**."
                )
                cc = st.columns([1, 1, 4])
                with cc[0]:
                    if st.button(
                        "🗑️ Sí, borrar todas",
                        key="hist_bulk_delete_yes",
                        type="primary",
                    ):
                        with st.spinner(
                            f"Borrando {sel_count} dedicatoria(s)..."
                        ):
                            ok = 0
                            for d in selected:
                                try:
                                    if history_module.delete_dedication(d.id):
                                        ok += 1
                                except Exception:
                                    pass
                                st.session_state.pop(f"sel_{d.id}", None)
                        st.session_state.pop("_hist_bulk_confirm_delete", None)
                        st.toast(f"{ok} dedicatoria(s) eliminadas.")
                        st.rerun()
                with cc[1]:
                    if st.button("✋ Cancelar", key="hist_bulk_delete_cancel"):
                        st.session_state.pop("_hist_bulk_confirm_delete", None)
                        st.rerun()

        for d in filtered:
            tpl = template_index.get(d.template_id) if d.template_id else None
            tpl_name = tpl.name if tpl else (d.template_snapshot or {}).get("name", "(plantilla eliminada)")
            badge = " 🔁" if d.is_generic else ""
            title = f"{d.recipient_name} · {d.recipient_group or '(sin grupo)'} · {tpl_name}{badge}"
            row = st.columns([1, 30])
            with row[0]:
                st.checkbox(
                    " ",
                    key=f"sel_{d.id}",
                    label_visibility="collapsed",
                    help="Marcar para acciones masivas (PDF A4, mover a pendientes, borrar).",
                )
            with row[1]:
                expander = st.expander(f"{d.created_at[:10]} · {title}")
            with expander:
                cols = st.columns([3, 4])
                with cols[0]:
                    extra_paths = d.card_extra_png_paths or []
                    has_parts = bool(extra_paths)
                    front_paths = [d.card_png_path] + list(extra_paths) if d.card_png_path else []
                    tab_labels: list[str] = []
                    if has_parts:
                        tab_labels.extend([f"📄 Parte {i + 1}" for i in range(len(front_paths))])
                    elif d.card_png_path:
                        tab_labels.append("📄 Frente")
                    if d.card_back_png_path:
                        tab_labels.append("🔄 Reverso")
                    if not tab_labels:
                        pass
                    elif len(tab_labels) == 1 and not d.card_back_png_path and front_paths:
                        try:
                            st.image(storage.get(front_paths[0]), use_container_width=True)
                        except Exception as e:  # noqa: BLE001
                            st.warning(f"No se pudo cargar la imagen: {e}")
                    else:
                        tabs = st.tabs(tab_labels)
                        for idx, fpath in enumerate(front_paths):
                            with tabs[idx]:
                                try:
                                    st.image(storage.get(fpath), use_container_width=True)
                                except Exception as e:  # noqa: BLE001
                                    st.warning(f"No se pudo cargar el frente: {e}")
                        if d.card_back_png_path:
                            with tabs[-1]:
                                try:
                                    st.image(storage.get(d.card_back_png_path), use_container_width=True)
                                except Exception as e:  # noqa: BLE001
                                    st.warning(f"No se pudo cargar el reverso: {e}")
                    if has_parts:
                        st.caption(
                            f"📑 Esta dedicatoria está dividida en **{len(front_paths)} tarjetas** "
                            "(el texto no cabía en una sola). El nombre del destinatario aparece en todas."
                        )
                with cols[1]:
                    _render_dedication_text_block(d, key_prefix="rendered_")
                    if d.input_mode == "audio":
                        with st.expander("Transcripción cruda"):
                            st.text(d.raw_input)
                    st.caption(
                        f"Modo: {d.input_mode} · Creada: {d.created_at} · ID: {d.id[:8]}"
                        + (" · 🔄 con reverso" if d.card_back_png_path else "")
                    )

                    actions = st.columns(5 if d.card_back_png_path else 4)
                    with actions[0]:
                        if d.card_pdf_path:
                            try:
                                pdf_bytes = storage.get(d.card_pdf_path)
                                st.download_button(
                                    "⬇️ PDF",
                                    data=pdf_bytes,
                                    file_name=f"dedicatoria_{d.recipient_name.replace(' ', '_')}.pdf",
                                    mime="application/pdf",
                                    key=f"pdf_{d.id}",
                                    use_container_width=True,
                                )
                            except Exception:
                                st.write("PDF no disponible")
                    with actions[1]:
                        if d.card_png_path:
                            try:
                                png_bytes = storage.get(d.card_png_path)
                                st.download_button(
                                    "⬇️ PNG frente" if d.card_back_png_path else "⬇️ PNG",
                                    data=png_bytes,
                                    file_name=f"dedicatoria_{d.recipient_name.replace(' ', '_')}_frente.png" if d.card_back_png_path else f"dedicatoria_{d.recipient_name.replace(' ', '_')}.png",
                                    mime="image/png",
                                    key=f"png_{d.id}",
                                    use_container_width=True,
                                )
                            except Exception:
                                st.write("PNG no disponible")
                    if d.card_back_png_path:
                        with actions[2]:
                            try:
                                back_bytes = storage.get(d.card_back_png_path)
                                st.download_button(
                                    "⬇️ PNG reverso",
                                    data=back_bytes,
                                    file_name=f"dedicatoria_{d.recipient_name.replace(' ', '_')}_reverso.png",
                                    mime="image/png",
                                    key=f"backpng_{d.id}",
                                    use_container_width=True,
                                )
                            except Exception:
                                st.write("Reverso no disponible")
                    dup_idx = 3 if d.card_back_png_path else 2
                    del_idx = dup_idx + 1
                    with actions[dup_idx]:
                        if st.button("🔁 Duplicar", key=f"dup_{d.id}", help="Reutilizar texto y plantilla con otro destinatario"):
                            st.query_params["duplicate"] = d.id
                            st.switch_page("pages/2_Generar_dedicatoria.py")
                    with actions[del_idx]:
                        if st.button(
                            "🗑️ Eliminar",
                            key=f"del_{d.id}",
                            help="Borra la dedicatoria por completo: texto, audio y archivos renderizados.",
                        ):
                            st.session_state[f"_confirm_del_{d.id}"] = True

                    if st.session_state.get(f"_confirm_del_{d.id}"):
                        st.warning(
                            "Vas a **borrar toda la dedicatoria** (texto, audio y tarjeta). "
                            "Si solo quieres tirar la tarjeta renderizada y conservar el texto "
                            "para cambiar de plantilla, usa **«🔄 Tirar tarjeta y guardar como pendiente»**."
                        )
                        confirm_cols = st.columns([1, 1, 1])
                        with confirm_cols[0]:
                            if st.button(
                                "🗑️ Sí, borrar todo",
                                key=f"del_confirm_{d.id}",
                                type="primary",
                            ):
                                history_module.delete_dedication(d.id)
                                st.session_state.pop(f"_confirm_del_{d.id}", None)
                                st.toast("Dedicatoria eliminada.")
                                st.rerun()
                        with confirm_cols[1]:
                            if st.button(
                                "🔄 Tirar tarjeta y guardar como pendiente",
                                key=f"unrender_{d.id}",
                                help="Borra la tarjeta renderizada (PDF/PNG) pero conserva el texto y los datos en «Pendientes», listo para renderizar con otra plantilla.",
                            ):
                                history_module.unrender_dedication(d.id)
                                st.session_state.pop(f"_confirm_del_{d.id}", None)
                                st.toast("Tarjeta borrada. La dedicatoria queda como pendiente.")
                                st.rerun()
                        with confirm_cols[2]:
                            if st.button("✋ Cancelar", key=f"del_cancel_{d.id}"):
                                st.session_state.pop(f"_confirm_del_{d.id}", None)
                                st.rerun()

                    generic_now = st.checkbox(
                        "Marcar como genérica",
                        value=d.is_generic,
                        key=f"gen_{d.id}",
                    )
                    if generic_now != d.is_generic:
                        d.is_generic = generic_now
                        history_module.update_dedication(d)
                        st.toast("Actualizada.")
                        st.rerun()
