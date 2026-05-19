from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

from core.models import Template, TextStyle, Zone
from core.templates import get_source_bytes

logger = logging.getLogger(__name__)

DEFAULT_DPI = 300
PREVIEW_DPI = 120

# Lista de fuentes TrueType candidatas para Pillow (PNG).
# Pillow soporta .ttc directamente; ReportLab no de forma fiable, así que las
# fuentes que se usan para el PDF se manejan aparte en TTF_FONT_FAMILIES.
PILLOW_FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    # Linux (Streamlit Cloud)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]

# Familias TrueType candidatas para ReportLab (PDF) — con todas sus variantes.
# Se intenta registrar la primera familia disponible y se cachea el resultado.
TTF_FONT_FAMILIES: List[Tuple[str, Dict[str, str]]] = [
    (
        "DejaVuSans",
        {
            "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "italic": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
            "bolditalic": "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
        },
    ),
    (
        "LiberationSans",
        {
            "regular": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "bold": "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "italic": "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
            "bolditalic": "/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf",
        },
    ),
    (
        "Arial",
        {
            "regular": "/Library/Fonts/Arial.ttf",
            "bold": "/Library/Fonts/Arial Bold.ttf",
            "italic": "/Library/Fonts/Arial Italic.ttf",
            "bolditalic": "/Library/Fonts/Arial Bold Italic.ttf",
        },
    ),
]

_REGISTERED_PDF_FONTS: set = set()
_PDF_FAMILY_VARIANTS: Dict[str, str] = {}  # variant -> registered name (e.g. "bold" -> "DejaVuSans-Bold")
_PDF_FAMILY_RESOLVED = False  # marcador para no reintentar registro en cada llamada


def _bundled_font_families() -> List[Tuple[str, Dict[str, str]]]:
    """Familias TrueType bundleadas en `assets/fonts/`.

    Si el usuario coloca un archivo `.ttf` en `assets/fonts/`, se intenta usar
    como fallback (con la misma fuente para todas las variantes si no hay más).
    Esto evita depender de las fuentes del sistema operativo.
    """
    fonts_dir = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    if not fonts_dir.is_dir():
        return []
    families: List[Tuple[str, Dict[str, str]]] = []
    for ttf in sorted(fonts_dir.glob("*.ttf")):
        family = ttf.stem
        families.append(
            (
                family,
                {
                    "regular": str(ttf),
                    # Si no hay variantes específicas, ReportLab usará la regular.
                },
            )
        )
    return families


def _try_register_pdf_family() -> Dict[str, str]:
    """Registra la primera familia TrueType disponible en ReportLab y devuelve
    el mapeo variante → nombre registrado. Devuelve dict vacío si no hay TTF.
    """
    global _PDF_FAMILY_RESOLVED
    if _PDF_FAMILY_RESOLVED:
        return _PDF_FAMILY_VARIANTS
    _PDF_FAMILY_RESOLVED = True

    # Primero las del sistema, luego las bundleadas en el repo como último recurso.
    candidates = list(TTF_FONT_FAMILIES) + _bundled_font_families()

    for family, variants in candidates:
        regular_path = variants.get("regular") or ""
        if not regular_path or not Path(regular_path).exists():
            continue
        registered: Dict[str, str] = {}
        try:
            for variant_key, path in variants.items():
                if not path or not Path(path).exists():
                    continue
                pdf_name = family if variant_key == "regular" else f"{family}-{variant_key.capitalize()}"
                if pdf_name not in _REGISTERED_PDF_FONTS:
                    pdfmetrics.registerFont(TTFont(pdf_name, path))
                    _REGISTERED_PDF_FONTS.add(pdf_name)
                registered[variant_key] = pdf_name
            if "regular" in registered:
                _PDF_FAMILY_VARIANTS.update(registered)
                logger.info("PDF font family registrada: %s (%s variantes)", family, len(registered))
                return _PDF_FAMILY_VARIANTS
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo registrar la familia TTF %s: %s", family, exc)
            continue
    logger.warning(
        "No se encontró ninguna TTF para ReportLab; los acentos podrían no renderizarse bien."
    )
    return _PDF_FAMILY_VARIANTS


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
    logger.warning(
        "No se encontró ninguna fuente TrueType para Pillow; usando default "
        "(los acentos pueden no salir)."
    )
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


def _bytes_to_image(source_bytes: bytes, source_type: str, target_w: int, target_h: int, dpi: int) -> Image.Image:
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


def _load_background_image(template: Template, dpi: int) -> Image.Image:
    source_bytes, source_type = get_source_bytes(template)
    target_w = mm_to_px(template.width_mm, dpi)
    target_h = mm_to_px(template.height_mm, dpi)
    return _bytes_to_image(source_bytes, source_type, target_w, target_h, dpi)


def _load_back_image(template: Template, dpi: int) -> Optional[Image.Image]:
    from core.templates import get_back_source_bytes

    pair = get_back_source_bytes(template)
    if pair is None:
        return None
    back_bytes, back_type = pair
    target_w = mm_to_px(template.width_mm, dpi)
    target_h = mm_to_px(template.height_mm, dpi)
    return _bytes_to_image(back_bytes, back_type, target_w, target_h, dpi)


def render_back_png(template: Template, *, dpi: int = DEFAULT_DPI) -> Optional[bytes]:
    """Renderiza únicamente el reverso de la plantilla como PNG. None si no tiene reverso."""
    image = _load_back_image(template, dpi)
    if image is None:
        return None
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


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
    """Devuelve el nombre de la fuente a usar en ReportLab.

    Si hay alguna TrueType disponible (DejaVuSans, LiberationSans, Arial), la
    registramos una vez y la usamos: tiene cobertura Unicode completa, así que
    los acentos (`á é í ó ú ñ ¿ ¡`) se renderizan correctamente y la fuente
    queda embebida en el PDF. Si no hay ninguna TTF en el sistema, caemos a la
    Helvetica Type1 base (WinAnsi) — funciona en la mayoría de visores pero
    puede tener glifos limitados.
    """
    variants = _try_register_pdf_family()
    if variants:
        if style.bold and style.italic and "bolditalic" in variants:
            return variants["bolditalic"]
        if style.bold and "bold" in variants:
            return variants["bold"]
        if style.italic and "italic" in variants:
            return variants["italic"]
        return variants.get("regular") or variants[next(iter(variants))]

    # Fallback: fuentes base Type1 de ReportLab (sin TTF disponible).
    bold_italic_map = {
        (True, True): "Helvetica-BoldOblique",
        (True, False): "Helvetica-Bold",
        (False, True): "Helvetica-Oblique",
        (False, False): "Helvetica",
    }
    return bold_italic_map.get((style.bold, style.italic), "Helvetica")


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
    """Genera un PDF con el frente (texto) y, si la plantilla tiene reverso, una segunda página con la imagen del reverso."""
    page_w_pt = template.width_mm * mm
    page_h_pt = template.height_mm * mm

    out = io.BytesIO()
    c = rl_canvas.Canvas(out, pagesize=(page_w_pt, page_h_pt))
    from reportlab.lib.utils import ImageReader

    # ---- Página 1: frente ----
    background = _load_background_image(template, dpi)
    bg_buffer = io.BytesIO()
    background.convert("RGB").save(bg_buffer, format="PNG", optimize=True)
    bg_buffer.seek(0)
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

    # ---- Página 2: reverso (si existe) ----
    back_image = _load_back_image(template, dpi)
    if back_image is not None:
        back_buf = io.BytesIO()
        back_image.convert("RGB").save(back_buf, format="PNG", optimize=True)
        back_buf.seek(0)
        c.drawImage(
            ImageReader(back_buf),
            0,
            0,
            width=page_w_pt,
            height=page_h_pt,
            preserveAspectRatio=False,
            mask="auto",
        )
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
