from __future__ import annotations

import io
from typing import Optional

import streamlit as st

from core import templates as templates_module
from core.config import get_config
from core.models import Template, TextStyle, Zone
from core.rendering import PREVIEW_DPI, render_preview

st.set_page_config(page_title="Plantillas", page_icon="🎨", layout="wide")
st.title("🎨 Plantillas de tarjeta")
st.caption("Sube el diseño de tu tarjeta, define las medidas y la zona donde irá la dedicatoria.")

cfg = get_config()
if not cfg.is_storage_ready:
    st.error("Almacenamiento no configurado.")
    st.stop()


def _detect_source_type(file_name: str) -> str:
    name = file_name.lower()
    if name.endswith(".pdf"):
        return "pdf"
    return "image"


def _zone_form(prefix: str, default: Zone, max_w: float, max_h: float) -> Zone:
    cols = st.columns(4)
    with cols[0]:
        x = st.number_input("X (mm)", min_value=0.0, max_value=max_w, value=float(default.x_mm), step=1.0, key=f"{prefix}_x")
    with cols[1]:
        y = st.number_input("Y (mm)", min_value=0.0, max_value=max_h, value=float(default.y_mm), step=1.0, key=f"{prefix}_y")
    with cols[2]:
        w = st.number_input("Ancho (mm)", min_value=1.0, max_value=max_w, value=float(default.width_mm), step=1.0, key=f"{prefix}_w")
    with cols[3]:
        h = st.number_input("Alto (mm)", min_value=1.0, max_value=max_h, value=float(default.height_mm), step=1.0, key=f"{prefix}_h")
    return Zone(x_mm=x, y_mm=y, width_mm=w, height_mm=h)


def _style_form(prefix: str, default: TextStyle) -> TextStyle:
    cols = st.columns([2, 1, 1, 1])
    with cols[0]:
        font_family = st.selectbox(
            "Fuente",
            options=["Helvetica"],
            index=0,
            key=f"{prefix}_font",
            help="De momento sólo Helvetica. Las fuentes personalizadas llegan en una fase posterior.",
        )
    with cols[1]:
        size = st.number_input("Tamaño (pt)", min_value=4.0, max_value=120.0, value=float(default.font_size_pt), step=1.0, key=f"{prefix}_size")
    with cols[2]:
        align = st.selectbox(
            "Alineación",
            options=["left", "center", "right"],
            index=["left", "center", "right"].index(default.align),
            key=f"{prefix}_align",
        )
    with cols[3]:
        color = st.color_picker("Color", value=default.color_hex, key=f"{prefix}_color")
    cols2 = st.columns([1, 1, 2])
    with cols2[0]:
        bold = st.checkbox("Negrita", value=default.bold, key=f"{prefix}_bold")
    with cols2[1]:
        italic = st.checkbox("Cursiva", value=default.italic, key=f"{prefix}_italic")
    with cols2[2]:
        line_height = st.number_input("Interlineado", min_value=0.8, max_value=3.0, value=float(default.line_height), step=0.1, key=f"{prefix}_lh")
    return TextStyle(
        font_family=font_family,
        font_size_pt=size,
        color_hex=color,
        align=align,  # type: ignore[arg-type]
        line_height=line_height,
        bold=bold,
        italic=italic,
    )


tab_create, tab_list = st.tabs(["➕ Nueva plantilla", f"📚 Existentes ({len(templates_module.list_templates())})"])

with tab_create:
    left, right = st.columns([1, 1])

    with left:
        name = st.text_input("Nombre de la plantilla", value=st.session_state.get("tpl_name", ""), key="tpl_name", placeholder="Ej. Tarjeta navideña 10x15")
        uploaded = st.file_uploader(
            "Diseño (PNG, JPG o PDF)",
            type=["png", "jpg", "jpeg", "pdf"],
            key="tpl_upload",
        )
        cols_dim = st.columns(2)
        with cols_dim[0]:
            width_mm = st.number_input("Ancho de la tarjeta (mm)", min_value=10.0, max_value=1000.0, value=150.0, step=1.0, key="tpl_w")
        with cols_dim[1]:
            height_mm = st.number_input("Alto de la tarjeta (mm)", min_value=10.0, max_value=1000.0, value=100.0, step=1.0, key="tpl_h")

        st.markdown("**Zona de la dedicatoria** (donde irá el texto principal)")
        text_zone_default = Zone(x_mm=10.0, y_mm=20.0, width_mm=width_mm - 20.0, height_mm=height_mm - 40.0)
        text_zone = _zone_form("tz", text_zone_default, width_mm, height_mm)
        text_style = _style_form("ts", TextStyle(font_size_pt=14.0, align="center"))

        use_name_zone = st.checkbox("Añadir zona separada para el nombre del destinatario", value=False, key="tpl_use_name_zone")
        name_zone: Optional[Zone] = None
        name_style: Optional[TextStyle] = None
        if use_name_zone:
            st.markdown("**Zona del nombre**")
            name_zone_default = Zone(x_mm=10.0, y_mm=10.0, width_mm=width_mm - 20.0, height_mm=12.0)
            name_zone = _zone_form("nz", name_zone_default, width_mm, height_mm)
            name_style = _style_form("ns", TextStyle(font_size_pt=18.0, align="center", bold=True))

    with right:
        st.markdown("**Vista previa**")
        if uploaded is None:
            st.info("Sube un diseño para ver el preview con texto de ejemplo.")
        else:
            try:
                source_bytes = uploaded.getvalue()
                source_type = _detect_source_type(uploaded.name)
                fake_template = Template(
                    id="preview",
                    name=name or "preview",
                    source_path="preview/source",
                    source_type=source_type,  # type: ignore[arg-type]
                    width_mm=width_mm,
                    height_mm=height_mm,
                    text_zone=text_zone,
                    text_style=text_style,
                    name_zone=name_zone,
                    name_style=name_style,
                )

                from core.rendering import _draw_text_pillow, _hex_to_rgba, mm_to_px
                from PIL import Image, ImageDraw

                if source_type == "pdf":
                    import pypdfium2 as pdfium

                    pdf = pdfium.PdfDocument(source_bytes)
                    page = pdf[0]
                    bitmap = page.render(scale=PREVIEW_DPI / 72.0)
                    bg = bitmap.to_pil().convert("RGBA")
                    pdf.close()
                else:
                    bg = Image.open(io.BytesIO(source_bytes)).convert("RGBA")
                target_w = mm_to_px(width_mm, PREVIEW_DPI)
                target_h = mm_to_px(height_mm, PREVIEW_DPI)
                if bg.size != (target_w, target_h):
                    bg = bg.resize((target_w, target_h), Image.LANCZOS)

                if name_zone:
                    _draw_text_pillow(bg, "Nombre destinatario", name_zone, name_style or text_style, PREVIEW_DPI)
                _draw_text_pillow(
                    bg,
                    "Ejemplo de dedicatoria. Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
                    text_zone,
                    text_style,
                    PREVIEW_DPI,
                )

                draw = ImageDraw.Draw(bg)
                for zone in [z for z in [text_zone, name_zone] if z is not None]:
                    x0 = mm_to_px(zone.x_mm, PREVIEW_DPI)
                    y0 = mm_to_px(zone.y_mm, PREVIEW_DPI)
                    x1 = x0 + mm_to_px(zone.width_mm, PREVIEW_DPI)
                    y1 = y0 + mm_to_px(zone.height_mm, PREVIEW_DPI)
                    draw.rectangle([x0, y0, x1, y1], outline=(255, 0, 128, 255), width=2)

                preview_buf = io.BytesIO()
                bg.convert("RGB").save(preview_buf, format="PNG")
                st.image(preview_buf.getvalue(), use_container_width=True)
                st.caption("El recuadro magenta indica la zona definida (sólo visible en el preview).")
            except Exception as e:  # noqa: BLE001
                st.error(f"No se pudo generar el preview: {e}")

    st.divider()
    if st.button("💾 Guardar plantilla", type="primary", disabled=uploaded is None or not name.strip()):
        try:
            ext = uploaded.name.rsplit(".", 1)[-1].lower()
            templates_module.create_template(
                name=name.strip(),
                source_bytes=uploaded.getvalue(),
                source_extension=ext,
                source_type=_detect_source_type(uploaded.name),
                width_mm=width_mm,
                height_mm=height_mm,
                text_zone=text_zone,
                text_style=text_style,
                name_zone=name_zone,
                name_style=name_style,
            )
            st.success(f"Plantilla «{name}» guardada.")
            for k in ["tpl_name", "tpl_upload"]:
                st.session_state.pop(k, None)
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"No se pudo guardar: {e}")

with tab_list:
    templates = templates_module.list_templates()
    if not templates:
        st.info("Todavía no hay plantillas.")
    else:
        for tpl in templates:
            with st.expander(f"🎴 {tpl.name} · {tpl.width_mm:.0f}×{tpl.height_mm:.0f} mm · {tpl.source_type.upper()}"):
                cols = st.columns([2, 3, 1])
                with cols[0]:
                    try:
                        thumb = render_preview(tpl, "Nombre", "Texto de muestra para visualizar la plantilla.")
                        st.image(thumb, use_container_width=True)
                    except Exception as e:  # noqa: BLE001
                        st.warning(f"No se pudo renderizar miniatura: {e}")
                with cols[1]:
                    st.markdown(
                        f"- **Dimensiones**: {tpl.width_mm:.1f} × {tpl.height_mm:.1f} mm\n"
                        f"- **Zona texto**: ({tpl.text_zone.x_mm:.0f}, {tpl.text_zone.y_mm:.0f}) "
                        f"{tpl.text_zone.width_mm:.0f}×{tpl.text_zone.height_mm:.0f} mm\n"
                        f"- **Fuente**: {tpl.text_style.font_family} {tpl.text_style.font_size_pt:.0f}pt "
                        f"({tpl.text_style.align})\n"
                        f"- **Zona nombre**: {'sí' if tpl.name_zone else 'no'}\n"
                        f"- **Creada**: {tpl.created_at}"
                    )
                with cols[2]:
                    if st.button("🗑️ Eliminar", key=f"del_{tpl.id}"):
                        templates_module.delete_template(tpl.id)
                        st.toast(f"Plantilla «{tpl.name}» eliminada.")
                        st.rerun()
