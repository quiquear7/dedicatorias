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


# ----------------------------------------------------------------------------
# Imposición A4: agrupa varias tarjetas en hojas A4 para imprenta.
# ----------------------------------------------------------------------------

A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0


def compute_centered_grid_mm(
    card_w_mm: float,
    card_h_mm: float,
    page_w_mm: float = A4_WIDTH_MM,
    page_h_mm: float = A4_HEIGHT_MM,
    *,
    max_cols: int = 2,
    max_rows: int = 2,
    gutter_mm: float = 0.0,
) -> List[Tuple[float, float]]:
    """Calcula las posiciones (X, Y_desde_arriba) en mm de una cuadrícula de
    tarjetas ``card_w_mm × card_h_mm`` **centrada** en la página.

    Decide cuántas columnas y filas caben de verdad (hasta ``max_cols × max_rows``)
    y centra el bloque resultante, de modo que los márgenes sobrantes sean
    simétricos en ambos ejes. Esa simetría es justo lo que hace que el reverso
    impreso coincida físicamente con el anverso tras el volteo dúplex (da igual
    si la impresora voltea por el borde largo o por el corto), y evita que la
    fila inferior se salga de la hoja y se recorte.

    Las tarjetas demasiado grandes para caber ni siquiera 1×1 se anclan en la
    esquina (margen 0) en lugar de desbordar con márgenes negativos.
    """
    def _fit(count_max: int, card: float, page: float) -> int:
        n = max(1, count_max)
        while n > 1 and n * card + (n - 1) * gutter_mm > page + 1e-6:
            n -= 1
        return n

    cols = _fit(max_cols, card_w_mm, page_w_mm)
    rows = _fit(max_rows, card_h_mm, page_h_mm)

    block_w = cols * card_w_mm + (cols - 1) * gutter_mm
    block_h = rows * card_h_mm + (rows - 1) * gutter_mm
    left = max(0.0, (page_w_mm - block_w) / 2.0)
    top = max(0.0, (page_h_mm - block_h) / 2.0)

    positions: List[Tuple[float, float]] = []
    for r in range(rows):
        for col in range(cols):
            x = left + col * (card_w_mm + gutter_mm)
            y = top + r * (card_h_mm + gutter_mm)
            positions.append((x, y))
    return positions


def _draw_crop_guides(
    c: "rl_canvas.Canvas",
    positions_mm: List[Tuple[float, float]],
    card_w_mm: float,
    card_h_mm: float,
    page_w_mm: float,
    page_h_mm: float,
) -> None:
    """Dibuja líneas finas grises (0.5pt) en los márgenes exteriores del A4
    siguiendo los bordes de las tarjetas, para guiar el corte con guillotina
    sin invadir el área impresa.
    """
    if not positions_mm:
        return

    xs = sorted({x for x, _ in positions_mm} | {x + card_w_mm for x, _ in positions_mm})
    ys_top = sorted(
        {y for _, y in positions_mm} | {y + card_h_mm for _, y in positions_mm}
    )

    top_block_mm = min(y for _, y in positions_mm)
    bottom_block_mm = max(y + card_h_mm for _, y in positions_mm)
    left_block_mm = min(x for x, _ in positions_mm)
    right_block_mm = max(x + card_w_mm for x, _ in positions_mm)

    c.saveState()
    c.setStrokeColorRGB(0.6, 0.6, 0.6)
    c.setLineWidth(0.5)

    # Verticales: tramos en margen superior (0 .. top_block) e inferior
    for x_mm in xs:
        x_pt = x_mm * mm
        c.line(x_pt, page_h_mm * mm, x_pt, (page_h_mm - top_block_mm) * mm)
        c.line(x_pt, (page_h_mm - bottom_block_mm) * mm, x_pt, 0)

    # Horizontales: tramos en margen izquierdo y derecho
    for y_top_mm in ys_top:
        y_pt = (page_h_mm - y_top_mm) * mm
        c.line(0, y_pt, left_block_mm * mm, y_pt)
        c.line(right_block_mm * mm, y_pt, page_w_mm * mm, y_pt)

    c.restoreState()


def render_imposed_a4_pdf(
    front_pngs: List[bytes],
    back_png: Optional[bytes] = None,
    *,
    card_width_mm: float = 80.0,
    card_height_mm: float = 130.0,
    grid_positions_mm: Optional[List[Tuple[float, float]]] = None,
    page_width_mm: float = A4_WIDTH_MM,
    page_height_mm: float = A4_HEIGHT_MM,
    include_crop_guides: bool = False,
) -> bytes:
    """Genera un único PDF A4 con las tarjetas dispuestas en cuadrícula 2×2
    (4 tarjetas por hoja), intercalando hojas de anverso y reverso para
    impresión a doble cara.

    Args:
        front_pngs: PNGs (uno por dedicatoria) con el frente ya renderizado.
        back_png: PNG del reverso común a todas las dedicatorias (o None si
            no aplica; en ese caso no se generan hojas de reverso).
        card_width_mm, card_height_mm: tamaño físico de cada tarjeta.
        grid_positions_mm: lista de (X, Y_desde_arriba) en mm para cada hueco
            de la cuadrícula. Si es None, se calcula con
            ``compute_centered_grid_mm`` a partir del tamaño real de la tarjeta,
            centrando el bloque en la hoja y eligiendo cuántas tarjetas caben
            (hasta 2×2 = 4 por hoja).
        include_crop_guides: si True, añade líneas grises 0.5pt en los
            márgenes para guiar el corte.

    El reverso se replica idénticamente en cada hueco de la hoja de reverso.
    Como las posiciones se centran en la hoja, los márgenes sobrantes son
    simétricos en ambos ejes; así, tras el volteo dúplex de la impresora (por
    borde largo o corto), cada tarjeta del anverso queda físicamente sobre su
    reverso.
    """
    from reportlab.lib.utils import ImageReader

    positions = grid_positions_mm or compute_centered_grid_mm(
        card_width_mm, card_height_mm, page_width_mm, page_height_mm
    )
    cards_per_sheet = len(positions)
    if cards_per_sheet == 0:
        raise ValueError("grid_positions_mm no puede estar vacío")

    page_w_pt = page_width_mm * mm
    page_h_pt = page_height_mm * mm
    card_w_pt = card_width_mm * mm
    card_h_pt = card_height_mm * mm

    def _y_rl(y_top_mm: float) -> float:
        return (page_height_mm - y_top_mm - card_height_mm) * mm

    out = io.BytesIO()
    c = rl_canvas.Canvas(out, pagesize=(page_w_pt, page_h_pt))

    # ReportLab necesita un ImageReader nuevo por uso fiable; el reverso se
    # repite muchas veces, así que lo cacheamos.
    back_reader = ImageReader(io.BytesIO(back_png)) if back_png else None

    total = len(front_pngs)
    if total == 0:
        c.save()
        return out.getvalue()

    sheets = (total + cards_per_sheet - 1) // cards_per_sheet
    for sheet_idx in range(sheets):
        # Hoja de anversos (página impar).
        for slot in range(cards_per_sheet):
            global_idx = sheet_idx * cards_per_sheet + slot
            if global_idx >= total:
                break
            x_mm, y_top_mm = positions[slot]
            c.drawImage(
                ImageReader(io.BytesIO(front_pngs[global_idx])),
                x_mm * mm,
                _y_rl(y_top_mm),
                width=card_w_pt,
                height=card_h_pt,
                preserveAspectRatio=False,
                mask="auto",
            )
        if include_crop_guides:
            _draw_crop_guides(
                c, positions, card_width_mm, card_height_mm,
                page_width_mm, page_height_mm,
            )
        c.showPage()

        # Hoja de reversos (página par): 4 reversos idénticos. Se emite también
        # en la última hoja aunque haya huecos en el anverso, para que la
        # paginación dúplex no se descuadre.
        if back_reader is not None:
            for slot in range(cards_per_sheet):
                x_mm, y_top_mm = positions[slot]
                c.drawImage(
                    back_reader,
                    x_mm * mm,
                    _y_rl(y_top_mm),
                    width=card_w_pt,
                    height=card_h_pt,
                    preserveAspectRatio=False,
                    mask="auto",
                )
            if include_crop_guides:
                _draw_crop_guides(
                    c, positions, card_width_mm, card_height_mm,
                    page_width_mm, page_height_mm,
                )
            c.showPage()

    c.save()
    return out.getvalue()
