from __future__ import annotations

from datetime import datetime

import streamlit as st

from core import backup as backup_module
from core.config import get_config

st.set_page_config(page_title="Backup", page_icon="💾", layout="centered")
st.title("💾 Backup y restauración")
st.caption("Descarga una copia completa de tus plantillas, contactos e historial, o restaura desde un ZIP previo.")

cfg = get_config()
if not cfg.is_storage_ready:
    st.error("Almacenamiento no configurado.")
    st.stop()

st.markdown(f"**Backend actual**: `{cfg.storage_backend}`" + (f" · bucket `{cfg.s3_bucket}`" if cfg.storage_backend == "s3" else ""))

st.divider()
st.subheader("📊 Estado actual")

with st.spinner("Calculando tamaños..."):
    try:
        stats = backup_module.storage_stats()
    except Exception as e:  # noqa: BLE001
        st.error(f"Error leyendo el almacenamiento: {e}")
        st.stop()

cols = st.columns(4)
cols[0].metric("Archivos totales", stats["total_files"])
cols[1].metric("Plantillas", stats["indices_count"]["templates"])
cols[2].metric("Dedicatorias", stats["indices_count"]["history"])
cols[3].metric("Tamaño total", backup_module.human_size(stats["total_bytes"]))

with st.expander("Desglose por sección"):
    for section, size in stats["by_section_bytes"].items():
        st.write(f"- **{section}**: {backup_module.human_size(size)}")

st.divider()
st.subheader("⬇️ Descargar backup")
st.markdown(
    "Genera un ZIP con todas las plantillas (incluyendo imágenes/PDFs originales), "
    "los contactos y el historial completo (PDFs, PNGs y audios)."
)

if st.button("Generar ZIP de backup", type="primary"):
    with st.spinner("Empaquetando..."):
        try:
            zip_bytes = backup_module.create_backup_zip()
            st.session_state["_backup_zip"] = zip_bytes
            st.session_state["_backup_ts"] = datetime.now().isoformat(timespec="seconds")
            st.success(f"ZIP generado: {backup_module.human_size(len(zip_bytes))}")
        except Exception as e:  # noqa: BLE001
            st.error(f"Error generando backup: {e}")

if st.session_state.get("_backup_zip"):
    ts = st.session_state["_backup_ts"].replace(":", "-")
    st.download_button(
        f"⬇️ Descargar backup ({backup_module.human_size(len(st.session_state['_backup_zip']))})",
        data=st.session_state["_backup_zip"],
        file_name=f"dedicatorias_backup_{ts}.zip",
        mime="application/zip",
        use_container_width=True,
    )
    st.caption("Guarda el archivo en un sitio seguro (iCloud, Dropbox, disco externo).")

st.divider()
st.subheader("⬆️ Restaurar desde backup")
st.warning(
    "Restaurar **sobreescribe** los archivos existentes con el mismo nombre. "
    "Si quieres mezclar dos estados, descarga primero un backup actual antes de restaurar."
)

uploaded = st.file_uploader("Selecciona un ZIP de backup", type=["zip"], key="restore_zip")
if uploaded is not None:
    overwrite = st.checkbox("Sobrescribir archivos existentes", value=True)
    confirm_text = st.text_input(
        "Escribe **RESTAURAR** para confirmar",
        placeholder="RESTAURAR",
    )
    can_restore = confirm_text.strip().upper() == "RESTAURAR"
    if st.button("Restaurar ahora", type="primary", disabled=not can_restore):
        with st.spinner("Restaurando..."):
            try:
                summary = backup_module.restore_from_zip(uploaded.getvalue(), overwrite=overwrite)
                st.success(f"Restaurados {summary['restored']} archivos. Saltados: {summary['skipped']}.")
                if summary["errors"]:
                    with st.expander(f"⚠️ {len(summary['errors'])} avisos"):
                        for err in summary["errors"]:
                            st.code(err)
                st.info("Recarga el resto de páginas para ver los datos restaurados.")
            except Exception as e:  # noqa: BLE001
                st.error(f"Error restaurando: {e}")
