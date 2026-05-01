from __future__ import annotations

from datetime import date, datetime, timezone

import streamlit as st

from core import history as history_module
from core import templates as templates_module
from core.config import get_config, get_storage

st.set_page_config(page_title="Historial", page_icon="📜", layout="wide")
st.title("📜 Historial de dedicatorias")
st.caption("Consulta dedicatorias pasadas, vuelve a descargarlas o duplícalas para otra persona.")

cfg = get_config()
if not cfg.is_storage_ready:
    st.error("Almacenamiento no configurado.")
    st.stop()

dedications = history_module.list_dedications()

if not dedications:
    st.info("Todavía no has generado ninguna dedicatoria. Ve a «Generar dedicatoria» para crear la primera.")
    st.stop()

groups = sorted({d.recipient_group for d in dedications if d.recipient_group})
template_index = {t.id: t for t in templates_module.list_templates()}

with st.sidebar:
    st.markdown("### Filtros")
    name_filter = st.text_input("Buscar por nombre", value="")
    group_filter = st.selectbox("Grupo", options=["(todos)", *groups])
    template_filter = st.selectbox(
        "Plantilla",
        options=["(todas)", *[t.name for t in templates_module.list_templates()]],
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
        tpl = template_index.get(d.template_id)
        tpl_name = tpl.name if tpl else d.template_snapshot.get("name", "")
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


filtered = [d for d in dedications if _matches(d)]
st.markdown(f"**{len(filtered)}** de {len(dedications)} dedicatorias")

storage = get_storage()

for d in filtered:
    tpl = template_index.get(d.template_id)
    tpl_name = tpl.name if tpl else d.template_snapshot.get("name", "(plantilla eliminada)")
    badge = " 🔁" if d.is_generic else ""
    title = f"{d.recipient_name} · {d.recipient_group or '(sin grupo)'} · {tpl_name}{badge}"
    with st.expander(f"{d.created_at[:10]} · {title}"):
        cols = st.columns([3, 4])
        with cols[0]:
            try:
                png_data = storage.get(d.card_png_path)
                st.image(png_data, use_container_width=True)
            except Exception as e:  # noqa: BLE001
                st.warning(f"No se pudo cargar la imagen: {e}")
        with cols[1]:
            st.markdown(f"**Texto final:**")
            st.markdown(f"> {d.final_text}")
            if d.input_mode == "audio":
                with st.expander("Transcripción cruda"):
                    st.text(d.raw_input)
            st.caption(
                f"Modo: {d.input_mode} · "
                f"Creada: {d.created_at} · "
                f"ID: {d.id[:8]}"
            )

            actions = st.columns(4)
            with actions[0]:
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
