"""
Shared page-level drawing helpers: header (Vision Property logo),
footer (Cotality copyright + optional REA attribution), rules, etc.
"""
from __future__ import annotations

import os
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader

from .styles import (
    PAGE_WIDTH, PAGE_HEIGHT, MARGIN_LEFT, MARGIN_RIGHT,
    COLOR_TEXT, COLOR_MUTED, COLOR_FOOTER_RULE,
    FONT_REGULAR, FONT_BOLD,
    SIZE_TINY,
    COTALITY_FOOTER, REA_ATTRIBUTION,
)


# ---- Logo handling ---------------------------------------------------

# Logo + agency name are set once per generation and cached here.
_LOGO_PATH: str | None = None
_AGENCY_NAME: str = ""


def set_logo_path(path: str, agency_name: str = "") -> None:
    global _LOGO_PATH, _AGENCY_NAME
    _LOGO_PATH = path
    _AGENCY_NAME = agency_name


def draw_header(c: Canvas) -> None:
    """Draw the agency logo/name top-right on the current page."""
    if _LOGO_PATH and os.path.exists(_LOGO_PATH):
        try:
            img = ImageReader(_LOGO_PATH)
            iw, ih = img.getSize()
            # Target height ~40pt, maintain aspect ratio
            target_h = 40
            target_w = iw * (target_h / ih)
            x = PAGE_WIDTH - MARGIN_RIGHT - target_w
            y = PAGE_HEIGHT - 30 - target_h
            c.drawImage(
                img, x, y,
                width=target_w, height=target_h,
                mask="auto", preserveAspectRatio=True,
            )
        except Exception:
            _draw_text_logo(c)
    else:
        _draw_text_logo(c)


def _draw_text_logo(c: Canvas) -> None:
    """Fallback: draw agency name as text when logo image is unavailable."""
    name = _AGENCY_NAME or "VISION PROPERTY INVESTMENT GROUP"
    # Split into main name + subtitle if possible
    parts = name.upper().rsplit(" ", 1)
    if len(name) > 20 and len(parts) == 2:
        main, sub = parts
    else:
        main, sub = name.upper(), ""

    c.setFont(FONT_BOLD, 11)
    c.setFillColor(COLOR_TEXT)
    c.drawRightString(PAGE_WIDTH - MARGIN_RIGHT, PAGE_HEIGHT - 45, main)
    if sub:
        c.setFont(FONT_REGULAR, 7)
        c.setFillColor(COLOR_MUTED)
        c.drawRightString(PAGE_WIDTH - MARGIN_RIGHT, PAGE_HEIGHT - 55, sub)


def draw_footer(c: Canvas, include_rea: bool = False) -> None:
    """Draw the standard Cotality footer, optionally with REA attribution."""
    y = 45
    footer_width = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    # Horizontal rule
    c.setStrokeColor(COLOR_FOOTER_RULE)
    c.setLineWidth(0.5)
    c.line(MARGIN_LEFT, y + 25, PAGE_WIDTH - MARGIN_RIGHT, y + 25)

    c.setFont(FONT_REGULAR, SIZE_TINY)
    c.setFillColor(COLOR_MUTED)

    # Wrap footer text to fit within page width
    wrapped = wrap_text(COTALITY_FOOTER.replace("\n", " "), footer_width, FONT_REGULAR, SIZE_TINY)
    for i, line in enumerate(wrapped):
        c.drawString(MARGIN_LEFT, y + 12 - i * 9, line)

    if include_rea:
        rea_y = y + 12 - len(wrapped) * 9
        c.setFont(FONT_BOLD, SIZE_TINY)
        c.setFillColor(COLOR_MUTED)
        c.drawString(MARGIN_LEFT, rea_y, REA_ATTRIBUTION)


def draw_page_chrome(c: Canvas, include_rea_attribution: bool = False) -> None:
    """Convenience: draw header + footer together."""
    draw_header(c)
    draw_footer(c, include_rea=include_rea_attribution)


# ---- Text helpers ----------------------------------------------------


def draw_h1(c: Canvas, text: str, x: float, y: float) -> None:
    from .styles import SIZE_H1
    c.setFont(FONT_BOLD, SIZE_H1)
    c.setFillColor(COLOR_TEXT)
    c.drawString(x, y, text)


def draw_muted(c: Canvas, text: str, x: float, y: float, size: int = 9) -> None:
    c.setFont(FONT_REGULAR, size)
    c.setFillColor(COLOR_MUTED)
    c.drawString(x, y, text)


def draw_hr(c: Canvas, y: float, x1: float | None = None, x2: float | None = None) -> None:
    from .styles import COLOR_RULE
    c.setStrokeColor(COLOR_RULE)
    c.setLineWidth(0.5)
    c.line(
        x1 if x1 is not None else MARGIN_LEFT,
        y,
        x2 if x2 is not None else PAGE_WIDTH - MARGIN_RIGHT,
        y,
    )


def wrap_text(text: str, width: float, font: str, size: int) -> list[str]:
    """Break text into lines that fit within `width` points."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if stringWidth(trial, font, size) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines
