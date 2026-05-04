from __future__ import annotations

from datetime import datetime

import streamlit as st

from core import history as history_module
from core import templates as templates_module
from core.auth import logout_button, require_login
from core.config import get_config, get_storage
from core.rendering import render_preview

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
    if not pending_list:
        st.info("No tienes dedicatorias pendientes. Cuando guardes una sin plantilla aparecerá aquí.")
    else:
        st.markdown(
            f"Tienes **{len(pending_list)}** dedicatorias guardadas sin renderizar. "
            "Selecciona una plantilla y genera todas (o las que elijas) en un solo paso."
        )
        if not templates_all:
            st.warning("Necesitas tener al menos una plantilla creada en la página «Plantillas» para renderizar.")
        else:
            tlabels = [f"{t.name} ({t.width_mm:.0f}×{t.height_mm:.0f} mm)" for t in templates_all]
            tchoice = st.selectbox("Plantilla a usar para generar", options=tlabels, key="bulk_tpl")
            chosen_template = templates_all[tlabels.index(tchoice)]

            # Vista previa con una de las pendientes (la primera por defecto, seleccionable)
            with st.expander("👁️ Vista previa con esta plantilla", expanded=True):
                preview_labels = [
                    f"{p.recipient_name} — {p.final_text[:40]}{'…' if len(p.final_text) > 40 else ''}"
                    for p in pending_list
                ]
                preview_idx = st.selectbox(
                    "Dedicatoria a previsualizar",
                    options=range(len(pending_list)),
                    format_func=lambda i: preview_labels[i],
                    key="bulk_preview_pick",
                )
                preview_target = pending_list[preview_idx]
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

            st.markdown("**Selecciona qué dedicatorias renderizar:**")
            select_all = st.checkbox("Seleccionar todas", value=True, key="bulk_all")

            head = st.columns([3, 3, 4, 1])
            head[0].markdown("**Destinatario**")
            head[1].markdown("**Grupo**")
            head[2].markdown("**Texto (resumen)**")
            head[3].markdown("**Incluir**")

            chosen_ids: list[str] = []
            for d in pending_list:
                row = st.columns([3, 3, 4, 1])
                row[0].write(d.recipient_name)
                row[1].write(d.recipient_group or "—")
                preview_text = (d.final_text[:80] + "…") if len(d.final_text) > 80 else d.final_text
                row[2].caption(preview_text)
                included = row[3].checkbox(
                    "incluir",
                    value=select_all,
                    key=f"bulk_inc_{d.id}",
                    label_visibility="collapsed",
                )
                if included:
                    chosen_ids.append(d.id)

            st.divider()
            cta = st.columns([2, 1])
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
                    progress = st.progress(0.0, text="Renderizando...")
                    summary = {"ok": [], "errors": []}
                    for idx, did in enumerate(chosen_ids, start=1):
                        try:
                            history_module.render_pending(did, chosen_template)
                            summary["ok"].append(did)
                        except Exception as e:  # noqa: BLE001
                            summary["errors"].append({"id": did, "error": str(e)})
                        progress.progress(
                            idx / len(chosen_ids),
                            text=f"Renderizando {idx}/{len(chosen_ids)}...",
                        )
                    progress.empty()
                    st.success(f"Generadas {len(summary['ok'])} dedicatorias.")
                    if summary["errors"]:
                        with st.expander(f"⚠️ {len(summary['errors'])} errores"):
                            for err in summary["errors"]:
                                st.code(str(err))
                    st.rerun()

        st.divider()
        st.markdown("**O renderiza individualmente:**")
        for d in pending_list:
            with st.expander(
                f"⏳ {d.recipient_name} · {d.recipient_group or '(sin grupo)'} · {d.created_at[:10]}"
            ):
                st.markdown("**Texto:**")
                st.markdown(f"> {d.final_text}")
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
                        if st.button("🚀 Generar", key=f"ind_gen_{d.id}", use_container_width=True, type="primary"):
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
                if st.button("🗑️ Eliminar pendiente", key=f"ind_del_{d.id}"):
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
        st.markdown(f"**{len(filtered)}** de {len(rendered_list)} dedicatorias")

        for d in filtered:
            tpl = template_index.get(d.template_id) if d.template_id else None
            tpl_name = tpl.name if tpl else (d.template_snapshot or {}).get("name", "(plantilla eliminada)")
            badge = " 🔁" if d.is_generic else ""
            title = f"{d.recipient_name} · {d.recipient_group or '(sin grupo)'} · {tpl_name}{badge}"
            with st.expander(f"{d.created_at[:10]} · {title}"):
                cols = st.columns([3, 4])
                with cols[0]:
                    if d.card_png_path:
                        try:
                            png_data = storage.get(d.card_png_path)
                            st.image(png_data, use_container_width=True)
                        except Exception as e:  # noqa: BLE001
                            st.warning(f"No se pudo cargar la imagen: {e}")
                with cols[1]:
                    st.markdown("**Texto final:**")
                    st.markdown(f"> {d.final_text}")
                    if d.input_mode == "audio":
                        with st.expander("Transcripción cruda"):
                            st.text(d.raw_input)
                    st.caption(
                        f"Modo: {d.input_mode} · Creada: {d.created_at} · ID: {d.id[:8]}"
                    )

                    actions = st.columns(4)
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
                                    "⬇️ PNG",
                                    data=png_bytes,
                                    file_name=f"dedicatoria_{d.recipient_name.replace(' ', '_')}.png",
                                    mime="image/png",
                                    key=f"png_{d.id}",
                                    use_container_width=True,
                                )
                            except Exception:
                                st.write("PNG no disponible")
                    with actions[2]:
                        if st.button("🔁 Duplicar", key=f"dup_{d.id}", help="Reutilizar texto y plantilla con otro destinatario"):
                            st.query_params["duplicate"] = d.id
                            st.switch_page("pages/2_Generar_dedicatoria.py")
                    with actions[3]:
                        if st.button("🗑️ Eliminar", key=f"del_{d.id}"):
                            history_module.delete_dedication(d.id)
                            st.toast("Eliminada.")
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
