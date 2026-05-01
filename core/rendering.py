from __future__ import annotations

import io
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

from core.models import Template, TextStyle, Zone
from core.templates import get_source_bytes

DEFAULT_DPI = 300
PREVIEW_DPI = 120

PILLOW_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
]

_REGISTERED_PDF_FONTS: set[str] = set()


def _hex_to_rgba(hex_color: str) -> Tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) == 6:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return r, g, b, 255
    if len(h) == 8:
        r, g, b, a = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)
        return r, g, b, a
    return 0, 0, 0, 255


def mm_to_px(mm_value: float, dpi: int) -> int:
    return int(round(mm_value * dpi / 25.4))


def _load_pillow_font(style: TextStyle, dpi: int):
    size_px = max(int(round(style.font_size_pt * dpi / 72.0)), 6)
    for path in PILLOW_FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size_px)
            except OSError:
                continue
    return ImageFont.load_default(size=size_px) if hasattr(ImageFont, "load_default") else ImageFont.load_default()


def _wrap_pillow(text: str, font: ImageFont.ImageFont, max_width_px: float) -> List[str]:
    lines: List[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = (current + " " + word).strip()
            if font.getlength(candidate) <= max_width_px or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def _measure_pillow_line_height(font: ImageFont.ImageFont, multiplier: float) -> float:
    ascent, descent = font.getmetrics()
    return (ascent + descent) * multiplier


def _draw_text_pillow(
    image: Image.Image,
    text: str,
    zone: Zone,
    style: TextStyle,
    dpi: int,
) -> bool:
    """Devuelve True si el texto cabe completo, False si se ha truncado."""
    if not text:
        return True
    draw = ImageDraw.Draw(image)
    font = _load_pillow_font(style, dpi)
    color = _hex_to_rgba(style.color_hex)

    zone_x_px = mm_to_px(zone.x_mm, dpi)
    zone_y_px = mm_to_px(zone.y_mm, dpi)
    zone_w_px = mm_to_px(zone.width_mm, dpi)
    zone_h_px = mm_to_px(zone.height_mm, dpi)

    lines = _wrap_pillow(text, font, zone_w_px)
    line_height_px = _measure_pillow_line_height(font, style.line_height)

    max_lines = max(1, int(zone_h_px // max(line_height_px, 1)))
    fits = len(lines) <= max_lines
    visible = lines[:max_lines]

    total_height = line_height_px * len(visible)
    y_start = zone_y_px + max(0, (zone_h_px - total_height) / 2)

    for idx, line in enumerate(visible):
        line_width = font.getlength(line)
        if style.align == "left":
            x = zone_x_px
        elif style.align == "right":
            x = zone_x_px + zone_w_px - line_width
        else:
            x = zone_x_px + (zone_w_px - line_width) / 2
        y = y_start + idx * line_height_px
        draw.text((x, y), line, font=font, fill=color)
    return fits


def _load_background_image(template: Template, dpi: int) -> Image.Image:
    source_bytes, source_type = get_source_bytes(template)
    target_w = mm_to_px(template.width_mm, dpi)
    target_h = mm_to_px(template.height_mm, dpi)
    if source_type == "pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(source_bytes)
        page = pdf[0]
        scale = dpi / 72.0
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil().convert("RGBA")
        pdf.close()
    else:
        pil = Image.open(io.BytesIO(source_bytes)).convert("RGBA")
    if pil.size != (target_w, target_h):
        pil = pil.resize((target_w, target_h), Image.LANCZOS)
    return pil


def render_png(
    template: Template,
    recipient: str,
    dedication: str,
    *,
    dpi: int = DEFAULT_DPI,
) -> Tuple[bytes, dict]:
    image = _load_background_image(template, dpi)
    warnings: dict = {}

    if template.name_zone and template.name_style and recipient:
        fits_name = _draw_text_pillow(image, recipient, template.name_zone, template.name_style, dpi)
        if not fits_name:
            warnings["name_overflow"] = True

    fits_text = _draw_text_pillow(image, dedication, template.text_zone, template.text_style, dpi)
    if not fits_text:
        warnings["text_overflow"] = True

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), warnings


def _ensure_pdf_font(style: TextStyle) -> str:
    name = style.font_family
    base_fonts = {"Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Times-Roman", "Courier"}
    bold_italic_map = {
        ("Helvetica", True, True): "Helvetica-BoldOblique",
        ("Helvetica", True, False): "Helvetica-Bold",
        ("Helvetica", False, True): "Helvetica-Oblique",
        ("Helvetica", False, False): "Helvetica",
    }
    key = (name if name in {"Helvetica"} else "Helvetica", style.bold, style.italic)
    return bold_italic_map.get(key, "Helvetica")


def _wrap_pdf(text: str, font_name: str, font_size: float, max_width_pt: float) -> List[str]:
    lines: List[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = (current + " " + word).strip()
            if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width_pt or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def _draw_text_pdf(
    c: rl_canvas.Canvas,
    text: str,
    zone: Zone,
    style: TextStyle,
    page_height_pt: float,
) -> bool:
    if not text:
        return True
    font_name = _ensure_pdf_font(style)
    c.setFillColor(HexColor(style.color_hex))
    c.setFont(font_name, style.font_size_pt)

    zone_x_pt = zone.x_mm * mm
    zone_w_pt = zone.width_mm * mm
    zone_h_pt = zone.height_mm * mm
    zone_top_pt = page_height_pt - (zone.y_mm * mm)

    line_height_pt = style.font_size_pt * style.line_height
    lines = _wrap_pdf(text, font_name, style.font_size_pt, zone_w_pt)
    max_lines = max(1, int(zone_h_pt // max(line_height_pt, 1)))
    fits = len(lines) <= max_lines
    visible = lines[:max_lines]

    total_height = line_height_pt * len(visible)
    y_top = zone_top_pt - max(0, (zone_h_pt - total_height) / 2)
    ascent_offset = style.font_size_pt * 0.8

    for idx, line in enumerate(visible):
        line_width = pdfmetrics.stringWidth(line, font_name, style.font_size_pt)
        if style.align == "left":
            x = zone_x_pt
        elif style.align == "right":
            x = zone_x_pt + zone_w_pt - line_width
        else:
            x = zone_x_pt + (zone_w_pt - line_width) / 2
        y = y_top - ascent_offset - idx * line_height_pt
        c.drawString(x, y, line)
    return fits


def render_pdf(
    template: Template,
    recipient: str,
    dedication: str,
    *,
    dpi: int = DEFAULT_DPI,
) -> Tuple[bytes, dict]:
    page_w_pt = template.width_mm * mm
    page_h_pt = template.height_mm * mm
    background = _load_background_image(template, dpi)

    bg_buffer = io.BytesIO()
    background.convert("RGB").save(bg_buffer, format="PNG", optimize=True)
    bg_buffer.seek(0)

    out = io.BytesIO()
    c = rl_canvas.Canvas(out, pagesize=(page_w_pt, page_h_pt))
    from reportlab.lib.utils import ImageReader

    c.drawImage(
        ImageReader(bg_buffer),
        0,
        0,
        width=page_w_pt,
        height=page_h_pt,
        preserveAspectRatio=False,
        mask="auto",
    )
    warnings: dict = {}
    if template.name_zone and template.name_style and recipient:
        if not _draw_text_pdf(c, recipient, template.name_zone, template.name_style, page_h_pt):
            warnings["name_overflow"] = True
    if not _draw_text_pdf(c, dedication, template.text_zone, template.text_style, page_h_pt):
        warnings["text_overflow"] = True

    c.showPage()
    c.save()
    return out.getvalue(), warnings


def render_preview(
    template: Template,
    recipient: str,
    dedication: str,
) -> bytes:
    png_bytes, _ = render_png(template, recipient, dedication, dpi=PREVIEW_DPI)
    return png_bytes
