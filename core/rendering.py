from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
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
# fuentes que se usan para el PDF se manejan aparte en FONT_LIBRARY.
PILLOW_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


# Biblioteca de fuentes disponibles. Cada entrada es una familia con sus
# variantes (regular/bold/italic/bolditalic). El nombre (clave) es el que ve
# el usuario en el formulario y se guarda en TextStyle.font_family. Solo se
# expondrán las familias para las que exista al menos la variante "regular".
FONT_LIBRARY: Dict[str, Dict[str, str]] = {
    "DejaVu Sans": {
        "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "italic": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        "bolditalic": "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
    },
    "DejaVu Serif": {
        "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "italic": "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
        "bolditalic": "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf",
    },
    "Liberation Sans": {
        "regular": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "bold": "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "italic": "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
        "bolditalic": "/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf",
    },
    "Liberation Serif": {
        "regular": "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "bold": "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        "italic": "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
        "bolditalic": "/usr/share/fonts/truetype/liberation/LiberationSerif-BoldItalic.ttf",
    },
    "Liberation Mono": {
        "regular": "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "bold": "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "italic": "/usr/share/fonts/truetype/liberation/LiberationMono-Italic.ttf",
        "bolditalic": "/usr/share/fonts/truetype/liberation/LiberationMono-BoldItalic.ttf",
    },
}

_REGISTERED_PDF_FONTS: set = set()
# Map: display_name -> { variant_key -> registered_pdf_name }. Se rellena
# perezosamente en _load_font_library() y se cachea.
_FONT_LIBRARY_CACHE: Optional[Dict[str, Dict[str, str]]] = None


def _bundled_font_families() -> Dict[str, Dict[str, str]]:
    """Familias TrueType bundleadas en `assets/fonts/`.

    Cada archivo `.ttf` se expone como una familia. Para tener variantes
    (bold/italic/bolditalic) usa el sufijo en el nombre del fichero, p. ej.:
    `MiFuente.ttf`, `MiFuente-Bold.ttf`, `MiFuente-Italic.ttf`,
    `MiFuente-BoldItalic.ttf`. Si solo hay regular, ReportLab usa esa para
    todas las variantes.
    """
    fonts_dir = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    if not fonts_dir.is_dir():
        return {}
    by_family: Dict[str, Dict[str, str]] = {}
    for ttf in sorted(fonts_dir.glob("*.ttf")):
        stem = ttf.stem
        variant = "regular"
        family = stem
        for suffix, key in (
            ("-BoldItalic", "bolditalic"),
            ("-BoldOblique", "bolditalic"),
            ("-Bold", "bold"),
            ("-Italic", "italic"),
            ("-Oblique", "italic"),
        ):
            if stem.endswith(suffix):
                variant = key
                family = stem[: -len(suffix)]
                break
        family_display = family.replace("_", " ")
        by_family.setdefault(family_display, {})[variant] = str(ttf)
    return by_family


def _safe_pdf_name(label: str) -> str:
    """Convierte un nombre legible en un id válido para registrar en ReportLab."""
    return "".join(c for c in label if c.isalnum()) or "Font"


def _load_font_library() -> Dict[str, Dict[str, str]]:
    """Devuelve un dict `display_name -> {variant_key -> pdf_name_registrado}`
    con todas las familias realmente disponibles en el sistema y en
    `assets/fonts/`. Cachea el resultado tras la primera llamada.
    """
    global _FONT_LIBRARY_CACHE
    if _FONT_LIBRARY_CACHE is not None:
        return _FONT_LIBRARY_CACHE
    result: Dict[str, Dict[str, str]] = {}

    # Primero las del sistema (FONT_LIBRARY), luego las bundleadas.
    candidates = list(FONT_LIBRARY.items()) + list(_bundled_font_families().items())

    for display_name, variants in candidates:
        registered = _try_register_family(display_name, variants)
        if registered:
            result[display_name] = registered

    if not result:
        logger.warning(
            "No se encontró ninguna TTF para ReportLab; "
            "los acentos podrían no renderizarse bien."
        )
    _FONT_LIBRARY_CACHE = result
    return result


def _try_register_family(display_name: str, variants: Dict[str, str]) -> Dict[str, str]:
    """Registra cada variante existente de una familia y devuelve
    {variant_key: pdf_name_registrado}. Solo devuelve algo si encuentra al
    menos la variante "regular".
    """
    safe = _safe_pdf_name(display_name)
    registered: Dict[str, str] = {}
    for variant_key, path in variants.items():
        if not path or not Path(path).exists():
            continue
        pdf_name = safe if variant_key == "regular" else f"{safe}-{variant_key.capitalize()}"
        try:
            if pdf_name not in _REGISTERED_PDF_FONTS:
                pdfmetrics.registerFont(TTFont(pdf_name, path))
                _REGISTERED_PDF_FONTS.add(pdf_name)
            registered[variant_key] = pdf_name
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "No se pudo registrar variante %s de la familia %s (%s): %s",
                variant_key,
                display_name,
                path,
                exc,
            )
            continue
    return registered if "regular" in registered else {}


def list_available_fonts() -> List[str]:
    """Familias de fuente disponibles para los formularios de plantilla."""
    return list(_load_font_library().keys())


def font_available_variants(family_name: str) -> List[str]:
    """Variantes ("regular", "bold", "italic", "bolditalic") realmente
    disponibles en disco para la familia indicada. Si la familia no existe
    o no hay variantes, devuelve lista vacía.
    """
    library = _load_font_library()
    variants = library.get(family_name)
    if not variants:
        return []
    return list(variants.keys())


def _ttf_path_for_style(style: TextStyle) -> Optional[str]:
    """Ruta del archivo TTF a usar para esta combinación de familia+bold+italic.
    Devuelve None si no hay TTF disponible (cae al default de Pillow / Helvetica).
    """
    family_dict = FONT_LIBRARY.get(style.font_family) or _bundled_font_families().get(style.font_family)
    if not family_dict:
        return None
    variant_key = "regular"
    if style.bold and style.italic:
        variant_key = "bolditalic"
    elif style.bold:
        variant_key = "bold"
    elif style.italic:
        variant_key = "italic"
    path = family_dict.get(variant_key) or family_dict.get("regular")
    if path and Path(path).exists():
        return path
    return None


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
    # Primero la fuente concreta de la familia pedida por el estilo.
    path = _ttf_path_for_style(style)
    if path:
        try:
            return ImageFont.truetype(path, size=size_px)
        except OSError:
            pass
    # Si la familia no existe en este sistema, busca cualquier TTF disponible.
    for fallback in PILLOW_FONT_CANDIDATES:
        if Path(fallback).exists():
            try:
                return ImageFont.truetype(fallback, size=size_px)
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


def compute_text_top_mm(text: str, zone: Zone, style: TextStyle, dpi: int = DEFAULT_DPI) -> float:
    """Devuelve la coordenada Y (en mm) donde realmente arrancará el texto al
    dibujarlo en `zone` con `style`. Pillow centra verticalmente el bloque de
    líneas dentro de la zona, así que la Y de inicio depende del nº de líneas y
    del tamaño tras el auto-ajuste.
    """
    if not text:
        return zone.y_mm + zone.height_mm / 2  # zona vacía: centro
    zone_w_px = mm_to_px(zone.width_mm, dpi)
    zone_h_px = mm_to_px(zone.height_mm, dpi)
    _, lines, line_height_px = _fit_pillow_font(text, style, zone_w_px, zone_h_px, dpi)
    max_lines = max(1, int(zone_h_px // max(line_height_px, 1)))
    visible = lines[:max_lines]
    total_height_px = line_height_px * len(visible)
    y_start_px = mm_to_px(zone.y_mm, dpi) + max(0, (zone_h_px - total_height_px) / 2)
    # px → mm
    return y_start_px * 25.4 / dpi


def _resolve_name_zone(
    template: Template, dedication_text: str, dpi: int = DEFAULT_DPI
) -> Optional[Zone]:
    """Si la plantilla está configurada para que el nombre siga al texto,
    devuelve una `Zone` con la Y recalculada para quedar justo encima de donde
    arranca la dedicatoria. En otro caso (o si no hay name_zone), devuelve la
    name_zone tal cual.
    """
    if not template.name_zone:
        return None
    if not template.name_follows_text or not template.text_style:
        return template.name_zone

    text_top_mm = compute_text_top_mm(
        dedication_text, template.text_zone, template.text_style, dpi
    )
    gap = max(0.0, template.name_gap_mm)
    new_y = text_top_mm - template.name_zone.height_mm - gap
    # Evita salirse por arriba: clamp a 0 si saldría fuera de la tarjeta.
    new_y = max(0.0, new_y)
    return Zone(
        x_mm=template.name_zone.x_mm,
        y_mm=new_y,
        width_mm=template.name_zone.width_mm,
        height_mm=template.name_zone.height_mm,
    )


def _fit_pillow_font(
    text: str,
    style: TextStyle,
    zone_w_px: int,
    zone_h_px: int,
    dpi: int,
) -> Tuple[ImageFont.ImageFont, List[str], float]:
    """Devuelve (font, lines, line_height_px) con el mayor tamaño de fuente en
    [font_size_min_pt, font_size_pt] que haga que el texto quepa entero en la
    zona. Si no se ha configurado mínimo (o el máximo ya cabe), usa el máximo.
    """
    from dataclasses import replace

    max_pt = style.font_size_pt
    raw_min = style.font_size_min_pt
    min_pt = float(raw_min) if raw_min and raw_min > 0 else max_pt
    min_pt = min(min_pt, max_pt)

    def _measure(pt: float) -> Tuple[ImageFont.ImageFont, List[str], float]:
        trial = replace(style, font_size_pt=pt)
        font = _load_pillow_font(trial, dpi)
        lines = _wrap_pillow(text, font, zone_w_px)
        line_h = _measure_pillow_line_height(font, style.line_height)
        return font, lines, line_h

    def _fits(font: ImageFont.ImageFont, lines: List[str], line_h: float) -> bool:
        if not lines:
            return True
        if line_h * len(lines) > zone_h_px:
            return False
        for line in lines:
            if line and font.getlength(line) > zone_w_px:
                return False
        return True

    font, lines, line_h = _measure(max_pt)
    if min_pt >= max_pt or _fits(font, lines, line_h):
        return font, lines, line_h

    best = _measure(min_pt)
    lo, hi = min_pt, max_pt
    while hi - lo > 0.25:
        mid = (lo + hi) / 2.0
        cand = _measure(mid)
        if _fits(*cand):
            best = cand
            lo = mid
        else:
            hi = mid
    return best


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
    color = _hex_to_rgba(style.color_hex)

    zone_x_px = mm_to_px(zone.x_mm, dpi)
    zone_y_px = mm_to_px(zone.y_mm, dpi)
    zone_w_px = mm_to_px(zone.width_mm, dpi)
    zone_h_px = mm_to_px(zone.height_mm, dpi)

    font, lines, line_height_px = _fit_pillow_font(text, style, zone_w_px, zone_h_px, dpi)

    max_lines = max(1, int(zone_h_px // max(line_height_px, 1)))
    visible = lines[:max_lines]
    fits = len(lines) <= max_lines and all(
        (not line) or font.getlength(line) <= zone_w_px for line in visible
    )

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
        effective_name_zone = _resolve_name_zone(template, dedication, dpi)
        fits_name = _draw_text_pillow(
            image, recipient, effective_name_zone, template.name_style, dpi
        )
        if not fits_name:
            warnings["name_overflow"] = True

    fits_text = _draw_text_pillow(image, dedication, template.text_zone, template.text_style, dpi)
    if not fits_text:
        warnings["text_overflow"] = True

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), warnings


def render_pdf(
    template: Template,
    recipient: str,
    dedication: str,
    *,
    dpi: int = DEFAULT_DPI,
    include_back: bool = True,
) -> Tuple[bytes, dict]:
    """Genera un PDF con el frente y, opcionalmente, el reverso.

    El frente se embebe a partir del PNG renderizado por Pillow para garantizar
    que PDF y PNG sean visualmente idénticos (mismo motor de texto).
    """
    from reportlab.lib.utils import ImageReader

    page_w_pt = template.width_mm * mm
    page_h_pt = template.height_mm * mm

    front_png_bytes, warnings = render_png(template, recipient, dedication, dpi=dpi)

    out = io.BytesIO()
    c = rl_canvas.Canvas(out, pagesize=(page_w_pt, page_h_pt))

    c.drawImage(
        ImageReader(io.BytesIO(front_png_bytes)),
        0,
        0,
        width=page_w_pt,
        height=page_h_pt,
        preserveAspectRatio=False,
        mask="auto",
    )
    c.showPage()

    back_image = _load_back_image(template, dpi) if include_back else None
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
