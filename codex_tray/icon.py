"""Generate DPI-aware Windows tray icons from the remaining quota."""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

GREEN = (16, 124, 16)       # Fluent UI: #107C10
YELLOW = (255, 185, 0)      # Fluent UI: #FFB900
RED = (209, 52, 56)         # Fluent UI: #D13438
GRAY = (120, 120, 120)
WHITE = (255, 255, 255)
DPI_ICON_SIZES = (16, 20, 24, 32, 40, 48)


def color_for_ratio(ratio: float) -> tuple[int, int, int]:
    if ratio < 0.2:
        return RED
    if ratio < 0.6:
        return YELLOW
    return GREEN


def _font(size: int):
    try:
        return ImageFont.truetype("arialbd.ttf", size)
    except OSError:
        try:
            return ImageFont.truetype("arial.ttf", size)
        except OSError:
            return ImageFont.load_default()


def _measure(draw: ImageDraw.ImageDraw, text: str, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1], -bbox[0], -bbox[1]


def _draw_text(img: Image.Image, text: str, color: tuple[int, int, int],
               size_px: int) -> None:
    """Render centred digits with stable height across 1/2/3-digit values."""
    draw = ImageDraw.Draw(img)
    # Keep a visible moat between the digits and the progress ring. At the
    # smallest 16px output this leaves roughly two physical pixels per side.
    max_width = int(size_px * 0.62)
    max_height = int(size_px * 0.50)

    # Select the font by height only. If a three-digit value is too wide, it
    # is condensed horizontally below instead of becoming shorter than a
    # one- or two-digit value.
    font_size = max(5, int(size_px * 0.72))
    while True:
        try:
            font = ImageFont.truetype("arialnb.ttf", font_size)
        except OSError:
            try:
                font = ImageFont.truetype("arialbd.ttf", font_size)
            except OSError:
                font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if h <= max_height or font_size <= 5:
            break
        font_size -= 1

    glyph = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    glyph_draw = ImageDraw.Draw(glyph)
    glyph_draw.text((-bbox[0], -bbox[1]), text, fill=color, font=font)
    if glyph.width > max_width:
        glyph = glyph.resize((max_width, glyph.height), Image.Resampling.LANCZOS)

    x = (size_px - glyph.width) // 2
    y = (size_px - glyph.height) // 2
    img.alpha_composite(glyph, (x, y))


def render(text: str = "?",
           status: str = "ok",
           size_px: int = 32,
           gauge_percent: float | None = None) -> Image.Image:
    """Render one exact-size transparent icon with a circular progress track.

    Args:
        text: digits drawn in the centre (e.g. "99").
        status: 'ok' | 'err' | 'expired' | 'color=R,G,B' encoded override.
        gauge_percent: remaining quota in the 0..100 range.  It controls the
            length and colour of the progress arc. None draws a neutral ring.

    Colours:
        gauge_percent >= 60  -> GREEN
        gauge_percent 20..<60 -> YELLOW
        gauge_percent <  20  -> RED
        status in {"err", "expired"} -> GRAY
    """
    # Draw at 4x and downsample once.  This keeps curves and small digits clean
    # while still returning the exact native DPI size requested by Windows.
    output_size = max(8, int(size_px))
    scale = 4
    work_size = output_size * scale
    img = Image.new("RGBA", (work_size, work_size), (0, 0, 0, 0))

    # Pick the Fluent UI state colour based on remaining-% OR status.
    if status == "err" or status == "expired":
        frame_color = GRAY
        text_color = GRAY
    elif status.startswith("color="):
        try:
            r, g, b = (int(x) for x in status[6:].split(","))
            frame_color = text_color = (r, g, b)
        except Exception:
            frame_color = text_color = WHITE
    else:
        if gauge_percent is None:
            frame_color = GRAY
        else:
            frame_color = color_for_ratio(
                max(0.0, min(100.0, gauge_percent)) / 100.0
            )
        text_color = frame_color

    draw = ImageDraw.Draw(img)

    # Keep the circle close to the canvas edge and deliberately thin. A heavy
    # ring competes with two- and three-digit values at 16/20px.
    # Use the full Windows tray-icon canvas. Pillow draws the outline inward,
    # so the circle can touch the bounds without losing its stroke.
    margin = 0
    ring_width = max(scale, int(work_size * 0.055))
    ring_box = [margin, margin, work_size - margin - 1, work_size - margin - 1]

    # A low-contrast full track makes the amount legible even near zero.
    track_color = (128, 128, 128, 90)
    draw.ellipse(ring_box, outline=track_color, width=ring_width)
    if status in {"err", "expired"} or gauge_percent is None:
        draw.ellipse(ring_box, outline=frame_color, width=ring_width)
    else:
        pct = max(0.0, min(100.0, gauge_percent))
        if pct >= 99.95:
            draw.ellipse(ring_box, outline=frame_color, width=ring_width)
        elif pct > 0:
            draw.arc(
                ring_box,
                start=-90,
                end=-90 + 360.0 * pct / 100.0,
                fill=frame_color,
                width=ring_width,
            )

    # Centre digits.
    _draw_text(img, text, text_color, work_size)
    return img.resize((output_size, output_size), Image.Resampling.LANCZOS)


def render_dpi_set(text: str = "?", status: str = "ok",
                   gauge_percent: float | None = None) -> dict[int, Image.Image]:
    """Return native tray artwork for every supported Windows DPI size."""
    return {
        size: render(text=text, status=status, size_px=size,
                     gauge_percent=gauge_percent)
        for size in DPI_ICON_SIZES
    }


def to_ico_bytes(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
