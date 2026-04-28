"""
Generators for the editable pages of the CMA.

Each function takes a ReportLab Canvas and draws exactly one page,
preserving the layout of the original Cotality report.

Pages regenerated:
    - Page 1: Cover (subject address + user-uploaded hero image)
    - Page 4: Comparables Map: Sales & Listings (overview)
    - Page 5: Comparables Map: Sales (sales map + shortlist)
    - Page 6/7: Comparable Sales (detailed cards)
    - Page 8: Comparables Map: Listings (listings map + shortlist)
    - Page 9/10/11: Comparable Listings (detailed cards)

Pages 2, 3, 12, 13, 14, 15, 16 of the original PDF are preserved unchanged
and merged in by the pipeline orchestrator.
"""
from __future__ import annotations

import io
import math
import os
from typing import Iterable

from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader

from .styles import (
    PAGE_WIDTH, PAGE_HEIGHT,
    MARGIN_LEFT, MARGIN_RIGHT, MARGIN_BOTTOM, CONTENT_WIDTH,
    COLOR_TEXT, COLOR_MUTED, COLOR_RULE, COLOR_SOLD, COLOR_LISTING,
    COLOR_SUBJECT, COLOR_PANEL,
    FONT_REGULAR, FONT_BOLD,
    SIZE_H1, SIZE_H2, SIZE_BODY, SIZE_SMALL, SIZE_TINY, SIZE_LEGEND,
)
from .chrome import (
    draw_page_chrome, draw_h1, draw_muted, draw_hr, wrap_text,
)
from .models import SubjectProperty, ComparableProperty, CMAInputs
from ..utils.maps import StaticMapClient, MapMarker

_MARKER_SCALES: dict[str, float] = {"small": 0.45, "medium": 0.65, "large": 1.0}

_ICON_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "icons"))


# ======================================================================
# Page 1 - Cover
# ======================================================================

def render_cover(c: Canvas, inputs: CMAInputs) -> None:
    """
    Cover page layout (matches page 1 of the original):
      - Vision Property logo (header)
      - Title 'Comparative Market Analysis'
      - Large hero image (user-uploaded)
      - Property address
      - Prepared-on date
      - Agent name, agency, email
    """
    draw_page_chrome(c, include_rea_attribution=False)

    # Title
    c.setFont(FONT_BOLD, 22)
    c.setFillColor(COLOR_TEXT)
    c.drawString(MARGIN_LEFT, PAGE_HEIGHT - 70, "Comparative Market Analysis")

    # Hero image
    hero_y = 320
    hero_h = 360
    hero_w = CONTENT_WIDTH
    if inputs.subject.hero_image_path and os.path.exists(inputs.subject.hero_image_path):
        try:
            img = ImageReader(inputs.subject.hero_image_path)
            c.drawImage(
                img, MARGIN_LEFT, hero_y,
                width=hero_w, height=hero_h,
                preserveAspectRatio=True, anchor="c",
                mask="auto",
            )
        except Exception:
            _draw_placeholder_box(c, MARGIN_LEFT, hero_y, hero_w, hero_h,
                                  "Hero image could not be loaded")
    else:
        _draw_placeholder_box(c, MARGIN_LEFT, hero_y, hero_w, hero_h,
                              "Upload a hero image for the cover page")

    # Address
    c.setFont(FONT_BOLD, 16)
    c.setFillColor(COLOR_TEXT)
    addr = inputs.subject.address.upper()
    c.drawString(MARGIN_LEFT, 260, addr)

    # Prepared on line
    c.setFont(FONT_REGULAR, SIZE_BODY)
    c.setFillColor(COLOR_MUTED)
    c.drawString(MARGIN_LEFT, 240, "Prepared on ")
    c.setFont(FONT_BOLD, SIZE_BODY)
    c.setFillColor(COLOR_TEXT)
    c.drawString(MARGIN_LEFT + 64, 240, inputs.report_date)

    # Agent block
    c.setFont(FONT_BOLD, SIZE_BODY)
    c.drawString(MARGIN_LEFT, 200, inputs.agent_name)
    c.setFont(FONT_BOLD, SIZE_SMALL)
    c.setFillColor(COLOR_MUTED)
    c.drawString(MARGIN_LEFT, 186, inputs.agency_name.upper())

    c.setFont(FONT_REGULAR, SIZE_BODY)
    c.setFillColor(COLOR_TEXT)
    c.drawString(MARGIN_LEFT, 100, inputs.agent_email)


def _draw_placeholder_box(c: Canvas, x: float, y: float, w: float, h: float, text: str) -> None:
    c.setFillColor(COLOR_PANEL)
    c.rect(x, y, w, h, stroke=0, fill=1)
    c.setFillColor(COLOR_MUTED)
    c.setFont(FONT_REGULAR, SIZE_BODY)
    c.drawCentredString(x + w / 2, y + h / 2, text)


# ======================================================================
# Shared: Map page scaffolding
# ======================================================================

def _render_map_page(
    c: Canvas,
    title: str,
    markers: list[MapMarker],
    shortlist: list[dict],
    legend_items: list[tuple[str, str]],  # list of (color_hex_str, label_text)
    api_key: str,
    include_rea: bool,
    shortlist_columns: list[tuple[str, float, str]] | None = None,
    marker_scale: float = 1.0,
) -> None:
    """
    Layout for a "Comparables Map" page:

        +---------------------------------+
        |  Title                          |
        |                                 |
        |  [ Google Static Map ]          |
        |                                 |
        |  Legend pills                   |
        |  #  Address    bed bath car ... |
        |  #  Address    ...              |
        +---------------------------------+

    `shortlist` is a list of dicts with keys: 'index', 'address',
    'bed', 'bath', 'car', 'dom', 'note', 'kind' (subject|sold|listing).
    """
    draw_page_chrome(c, include_rea_attribution=include_rea)

    # Title
    c.setFont(FONT_BOLD, SIZE_H1)
    c.setFillColor(COLOR_TEXT)
    c.drawString(MARGIN_LEFT, PAGE_HEIGHT - 90, title)

    # Map
    map_top_y = PAGE_HEIGHT - 120
    map_h = 420
    map_y = map_top_y - map_h
    map_w = CONTENT_WIDTH

    try:
        client = StaticMapClient(api_key)
        png_bytes = client.render(markers, width=int(map_w * 2), height=int(map_h * 2), marker_scale=marker_scale)
        c.drawImage(
            ImageReader(io.BytesIO(png_bytes)),
            MARGIN_LEFT, map_y,
            width=map_w, height=map_h,
            preserveAspectRatio=False,
            mask="auto",
        )
    except Exception as e:
        _draw_placeholder_box(c, MARGIN_LEFT, map_y, map_w, map_h,
                              f"Map render failed: {e}")

    # Legend (pills)
    legend_y = map_y - 28
    _draw_legend(c, MARGIN_LEFT, legend_y, legend_items)

    # Shortlist table
    table_top = legend_y - 32
    _draw_shortlist_table(c, MARGIN_LEFT, table_top, shortlist, shortlist_columns)


def _draw_legend(c: Canvas, x: float, y: float, items: list[tuple[str, str]]) -> None:
    """Draw pin-marker + label pills horizontally (matches map pin style)."""
    from reportlab.lib.colors import HexColor
    cursor_x = x
    c.setFont(FONT_REGULAR, SIZE_LEGEND)
    pin_r = 6
    for color, label in items:
        _draw_pin_marker(c, cursor_x + pin_r, y - 1, pin_r, HexColor(color), "")
        c.setFillColor(COLOR_TEXT)
        c.drawString(cursor_x + pin_r * 2 + 6, y, label)
        cursor_x += pin_r * 2 + 6 + c.stringWidth(label, FONT_REGULAR, SIZE_LEGEND) + 28


def _draw_pin_marker(c, cx: float, cy_base: float, radius: float, color, label) -> None:
    """Draw a map-pin (teardrop) marker: circle on top, triangular tip pointing down."""
    from reportlab.lib.colors import HexColor
    circle_cy = cy_base + radius * 2.0
    dist = circle_cy - cy_base
    sin_a = radius / dist
    cos_a = math.sqrt(max(0.0, 1.0 - sin_a ** 2))
    tx1 = cx - radius * cos_a
    ty1 = circle_cy - radius * sin_a
    tx2 = cx + radius * cos_a
    ty2 = ty1
    c.setFillColor(color)
    p = c.beginPath()
    p.moveTo(cx, cy_base)
    p.lineTo(tx1, ty1)
    p.lineTo(tx2, ty2)
    p.close()
    c.drawPath(p, fill=1, stroke=0)
    c.circle(cx, circle_cy, radius, stroke=0, fill=1)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont(FONT_BOLD, max(5, int(radius * 1.3)))
    c.drawCentredString(cx, circle_cy - radius * 0.38, str(label))


def _draw_shortlist_table(
    c: Canvas,
    x: float,
    top_y: float,
    rows: list[dict],
    columns: list[tuple[str, float, str]] | None = None,
) -> None:
    """
    Render a shortlist beneath the map.

    Default columns: # | Address | bed | bath | car | extra
    """
    if columns is None:
        columns = [
            ("",          20,  "number"),
            ("Address",   280, "address"),
            ("Bed",       28,  "bed"),
            ("Bath",      32,  "bath"),
            ("Car",       30,  "car"),
            ("Price",     80,  "extra"),
        ]

    # Header row
    c.setFont(FONT_BOLD, SIZE_SMALL)
    c.setFillColor(COLOR_MUTED)
    cx = x
    for label, w, _ in columns:
        c.drawString(cx, top_y, label)
        cx += w
    draw_hr(c, top_y - 4, x1=x, x2=x + sum(w for _, w, _ in columns))

    # Rows
    row_y = top_y - 16
    for row in rows:
        _draw_shortlist_row(c, x, row_y, row, columns)
        row_y -= 16
        if row_y < MARGIN_BOTTOM + 60:
            break

    # rule below last row
    draw_hr(c, row_y + 8, x1=x, x2=x + sum(w for _, w, _ in columns))


def _draw_shortlist_row(c: Canvas, x: float, y: float, row: dict, columns) -> None:
    cx = x
    for label, w, key in columns:
        val = row.get(key, "")
        if key == "number":
            kind = row.get("kind", "sold")
            if kind == "listing":
                fill = COLOR_LISTING
            elif kind == "subject":
                fill = COLOR_SUBJECT
            else:
                fill = COLOR_SOLD
            _draw_pin_marker(c, cx + 6, y - 2, 4.5, fill, str(row.get("index", "")))
            c.setFillColor(COLOR_TEXT)
            c.setFont(FONT_REGULAR, SIZE_SMALL)
        else:
            c.setFillColor(COLOR_TEXT)
            c.setFont(FONT_REGULAR, SIZE_SMALL)
            s = str(val) if val not in (None, "") else "-"
            # truncate to fit column width (no ellipsis — compact table)
            max_w = w - 4
            while c.stringWidth(s, FONT_REGULAR, SIZE_SMALL) > max_w and len(s) > 1:
                s = s[:-1]
            c.drawString(cx, y, s)
        cx += w


# ======================================================================
# Page 4 - Overview map (sales + listings + subject all together)
# ======================================================================

def render_overview_map(c: Canvas, inputs: CMAInputs) -> None:
    """Overview map showing subject, all sales (blue), all listings (red)."""
    markers: list[MapMarker] = []
    if inputs.subject.latitude and inputs.subject.longitude:
        markers.append(MapMarker(
            lat=inputs.subject.latitude,
            lng=inputs.subject.longitude,
            label="",
            kind="subject",
        ))
    for comp in inputs.sales:
        if comp.latitude and comp.longitude:
            markers.append(MapMarker(
                lat=comp.latitude, lng=comp.longitude,
                label=str(comp.index), kind="sold",
            ))
    for comp in inputs.listings:
        if comp.latitude and comp.longitude:
            markers.append(MapMarker(
                lat=comp.latitude, lng=comp.longitude,
                label=str(comp.index), kind="listing",
            ))

    draw_page_chrome(c, include_rea_attribution=True)

    c.setFont(FONT_BOLD, SIZE_H1)
    c.setFillColor(COLOR_TEXT)
    c.drawString(MARGIN_LEFT, PAGE_HEIGHT - 90, "Comparables Map: Sales & Listings")

    # Larger map, no shortlist - just a color legend at the bottom
    map_top_y = PAGE_HEIGHT - 120
    map_h = 620
    map_y = map_top_y - map_h
    try:
        client = StaticMapClient(inputs.google_maps_api_key)
        ms = _MARKER_SCALES.get(inputs.marker_size, 1.0)
        png = client.render(markers, width=int(CONTENT_WIDTH * 2), height=int(map_h * 2), marker_scale=ms)
        c.drawImage(
            ImageReader(io.BytesIO(png)),
            MARGIN_LEFT, map_y,
            width=CONTENT_WIDTH, height=map_h,
            preserveAspectRatio=False,
            mask="auto",
        )
    except Exception as e:
        _draw_placeholder_box(c, MARGIN_LEFT, map_y, CONTENT_WIDTH, map_h,
                              f"Map render failed: {e}")

    _draw_legend(
        c, MARGIN_LEFT, map_y - 28,
        [
            ("#000000", "Your Property"),
            ("#d93b3b", "For Sale"),
            ("#1e9ad6", "Recently Sold"),
        ],
    )


# ======================================================================
# Page 5 - Comparables Map: Sales (map + sales shortlist)
# ======================================================================

def render_sales_map(c: Canvas, inputs: CMAInputs) -> None:
    markers: list[MapMarker] = []
    if inputs.subject.latitude and inputs.subject.longitude:
        markers.append(MapMarker(
            inputs.subject.latitude, inputs.subject.longitude, "", "subject"))
    for comp in inputs.sales:
        if comp.latitude and comp.longitude:
            markers.append(MapMarker(
                comp.latitude, comp.longitude, str(comp.index), "sold"))

    shortlist = [
        {
            "index": comp.index,
            "address": comp.address,
            "bed": _fmt(comp.bedrooms),
            "bath": _fmt(comp.bathrooms),
            "car": _fmt(comp.carspaces),
            "extra": comp.price_display or "-",
            "kind": "sold",
        }
        for comp in inputs.sales
    ]

    _render_map_page(
        c,
        title="Comparables Map: Sales",
        markers=markers,
        shortlist=shortlist,
        legend_items=[
            ("#000000", "Your Property"),
            ("#1e9ad6", "Recently Sold"),
        ],
        api_key=inputs.google_maps_api_key,
        include_rea=any(s.from_rea for s in inputs.sales),
        marker_scale=_MARKER_SCALES.get(inputs.marker_size, 1.0),
    )


# ======================================================================
# Page 8 - Comparables Map: Listings
# ======================================================================

def render_listings_map(c: Canvas, inputs: CMAInputs) -> None:
    markers: list[MapMarker] = []
    if inputs.subject.latitude and inputs.subject.longitude:
        markers.append(MapMarker(
            inputs.subject.latitude, inputs.subject.longitude, "", "subject"))
    for comp in inputs.listings:
        if comp.latitude and comp.longitude:
            markers.append(MapMarker(
                comp.latitude, comp.longitude, str(comp.index), "listing"))

    shortlist = [
        {
            "index": comp.index,
            "address": comp.address,
            "bed": _fmt(comp.bedrooms),
            "bath": _fmt(comp.bathrooms),
            "car": _fmt(comp.carspaces),
            "dom": f"{comp.days_on_market}" if comp.days_on_market else "-",
            "extra": comp.price_display or "-",
            "kind": "listing",
        }
        for comp in inputs.listings
    ]

    _render_map_page(
        c,
        title="Comparables Map: Listings",
        markers=markers,
        shortlist=shortlist,
        legend_items=[
            ("#000000", "Your Property"),
            ("#d93b3b", "For Sale"),
        ],
        api_key=inputs.google_maps_api_key,
        include_rea=any(l.from_rea for l in inputs.listings),
        marker_scale=_MARKER_SCALES.get(inputs.marker_size, 1.0),
        shortlist_columns=[
            ("",          22, "number"),
            ("Address",  240, "address"),
            ("Bed",       28, "bed"),
            ("Bath",      30, "bath"),
            ("Car",       28, "car"),
            ("DOM",       34, "dom"),
            ("Price",    100, "extra"),
        ],
    )


# ======================================================================
# Comparable detail cards (pages 6-7 for sales, 9-11 for listings)
# ======================================================================

CARD_HEIGHT = 122   # pt per card — compact to fit 5 per page
CARDS_PER_PAGE = 5

def render_comparable_detail_pages(
    c: Canvas,
    inputs: CMAInputs,
    comparables: list[ComparableProperty],
    title: str,
    is_listings: bool,
    progress=None,
    pct_start: int = 55,
    pct_end: int = 68,
) -> int:
    """
    Render one or more pages of comparable detail cards.
    Returns the number of pages rendered.
    progress(label, pct) is called after each card so the SSE stream
    stays alive during large batches (prevents 120 s queue timeout).
    """
    if not comparables:
        return 0

    pages_rendered = 0
    i = 0
    total = len(comparables)
    while i < len(comparables):
        batch = comparables[i:i + CARDS_PER_PAGE]
        include_rea = any(c.from_rea for c in batch)
        draw_page_chrome(c, include_rea_attribution=include_rea)

        # Title (on first page only, subsequent pages get a muted version)
        c.setFont(FONT_BOLD, SIZE_H1)
        if pages_rendered == 0:
            c.setFillColor(COLOR_TEXT)
        else:
            c.setFillColor(COLOR_MUTED)
        c.drawString(MARGIN_LEFT, PAGE_HEIGHT - 90, title)

        # Cards — start slightly higher to make room for 5 compact cards
        card_y = PAGE_HEIGHT - 108
        for j, comp in enumerate(batch):
            _draw_comparable_card(c, MARGIN_LEFT, card_y - CARD_HEIGHT, comp, is_listings)
            card_y -= CARD_HEIGHT + 4
            draw_hr(c, card_y + 2)
            # Emit per-card progress so SSE stream stays alive
            if progress:
                card_num = i + j + 1
                pct = pct_start + round((pct_end - pct_start) * card_num / total)
                progress(f"Rendering {title.lower()} ({card_num}/{total})...", pct)

        # Legend at bottom
        legend_y_base = 76
        c.setFont(FONT_REGULAR, SIZE_TINY)
        c.setFillColor(COLOR_MUTED)
        legend = "DOM = Days on market" if is_listings else \
                 "DOM = Days on market   RS = Recent sale   UN = Undisclosed Sale"
        c.drawString(MARGIN_LEFT, legend_y_base, legend)
        c.drawString(
            MARGIN_LEFT, legend_y_base - 10,
            "* This data point was edited by the author of this CMA and has not been verified by Cotality"
        )

        c.showPage()
        pages_rendered += 1
        i += CARDS_PER_PAGE

    return pages_rendered


def _draw_comparable_card(
    c: Canvas, x: float, y: float, comp: ComparableProperty, is_listing: bool,
) -> None:
    """Draw a single comparable card at (x, y) with fixed CARD_HEIGHT."""
    from reportlab.lib.colors import HexColor

    badge_color = COLOR_LISTING if is_listing else COLOR_SOLD
    HEADER_H = 26  # compact header to fit 5 cards per page

    # --- Header band (light background) ---
    header_y = y + CARD_HEIGHT - HEADER_H
    c.setFillColor(COLOR_PANEL)
    c.rect(x, header_y, CONTENT_WIDTH, HEADER_H, stroke=0, fill=1)

    # Pin badge in header
    _draw_pin_marker(c, x + 11, header_y + 4, 6, badge_color, str(comp.index))

    # Status pill (sales only) — colored background, white text
    if not is_listing:
        pill_w = 170
        pill_x = x + CONTENT_WIDTH - pill_w
        c.setFillColor(COLOR_SOLD)
        c.roundRect(pill_x, header_y + 8, pill_w, 18, 3, stroke=0, fill=1)
        c.setFont(FONT_BOLD, SIZE_SMALL)
        c.setFillColor(HexColor("#ffffff"))
        c.drawString(pill_x + 8, header_y + 12, comp.status)
        price_text = comp.price_display or "-"
        c.drawRightString(pill_x + pill_w - 8, header_y + 12, price_text)
        addr_max_w = pill_x - (x + 28) - 8
    else:
        addr_max_w = CONTENT_WIDTH - 32

    # Address in header — compact header only fits 1 line
    addr_x = x + 28
    addr_text = comp.address.upper()
    addr_lines = wrap_text(addr_text, addr_max_w, FONT_BOLD, SIZE_H2)[:1]
    if not addr_lines:
        addr_lines = [addr_text]
    c.setFillColor(COLOR_TEXT)
    c.setFont(FONT_BOLD, SIZE_H2)
    c.drawString(addr_x, header_y + (HEADER_H - SIZE_H2) / 2 + 1, addr_lines[0])

    # Divider below header
    draw_hr(c, header_y, x1=x, x2=x + CONTENT_WIDTH)

    # --- Thumbnail (vertically centred in body) ---
    body_h = CARD_HEIGHT - HEADER_H
    thumb_w, thumb_h = 100, 65
    thumb_x = x
    thumb_y = y + (body_h - thumb_h) // 2
    if comp.thumbnail_path and os.path.exists(comp.thumbnail_path):
        try:
            c.drawImage(
                ImageReader(comp.thumbnail_path),
                thumb_x, thumb_y, thumb_w, thumb_h,
                preserveAspectRatio=True, anchor="c",
                mask="auto",
            )
        except Exception:
            _draw_placeholder_box(c, thumb_x, thumb_y, thumb_w, thumb_h, "No image")
    else:
        _draw_placeholder_box(c, thumb_x, thumb_y, thumb_w, thumb_h, "No image")

    # --- Content area right of thumbnail ---
    content_x = x + thumb_w + 12
    content_w = CONTENT_WIDTH - thumb_w - 12
    col1_x = content_x
    col2_x = content_x + content_w // 2

    # Vertically centre the content block in the card body so it aligns
    # with the thumbnail rather than bunching at the top.
    N_META = 3
    ROW_GAP = 14  # compact row gap for 5-cards-per-page layout
    # Total height: feat text(10) + sep gap(6) + feat→meta(12) + (N-1)*ROW_GAP + last text(8)
    content_h = 36 + (N_META - 1) * ROW_GAP
    body_h_val = CARD_HEIGHT - HEADER_H
    feat_y = y + (body_h_val + content_h) // 2  # centred inside body
    feat_y = min(feat_y, header_y - 8)           # never overlap header band

    _draw_feature_strip(c, comp, content_x, feat_y)

    # Thin separator below feature row
    draw_hr(c, feat_y - 6, x1=content_x, x2=x + CONTENT_WIDTH)

    # --- Meta grid (2 columns, 3 rows) ---
    meta_y = feat_y - 14

    if is_listing:
        left_meta = [
            ("Year Built",    comp.year_built or "-"),
            ("Listing Date",  comp.listing_date or "-"),
            ("Listing Price", comp.price_display or "-"),
        ]
        right_meta = [
            ("DOM",      str(comp.days_on_market) if comp.days_on_market else "-"),
            ("Distance", f"{comp.distance_km:.2f}km" if comp.distance_km is not None else "-"),
        ]
    else:
        left_meta = [
            ("Year Built",     comp.year_built or "-"),
            ("Sold Date",      comp.sold_date or "-"),
            ("First Listing",  comp.first_listing or "-"),
        ]
        right_meta = [
            ("DOM",           str(comp.days_on_market) if comp.days_on_market else "-"),
            ("Distance",      f"{comp.distance_km:.2f}km" if comp.distance_km is not None else "-"),
            ("Last Listing",  comp.last_listing or "-"),
        ]

    col_w = content_w // 2
    for i, (label, value) in enumerate(left_meta):
        _meta_pair(c, col1_x, meta_y - i * ROW_GAP, label, value, col_w)
    for i, (label, value) in enumerate(right_meta):
        _meta_pair(c, col2_x, meta_y - i * ROW_GAP, label, value, col_w)


_ICON_ASPECTS: dict[str, float] = {
    "bed": 2.328, "bath": 1.094, "car": 1.797,
    "land": 1.016, "build": 1.0,
}


def _draw_prop_icon(c: Canvas, name: str, cx: float, cy: float, size: float = 7.5) -> float:
    """Draw a property icon from the extracted PNG. Returns width consumed."""
    icon_path = os.path.join(_ICON_DIR, f"{name}.png")
    aspect = _ICON_ASPECTS.get(name, 1.0)
    h = size
    w = h * aspect
    if os.path.exists(icon_path):
        try:
            c.drawImage(ImageReader(icon_path), cx, cy, width=w, height=h, mask="auto")
            return w
        except Exception:
            pass
    return w


def _draw_feature_strip(c: Canvas, comp: ComparableProperty, x: float, y: float) -> None:
    """Draw value + icon for each enabled feature (no type distinction)."""
    def _show(attr: str) -> bool:
        return getattr(comp, attr, True)

    features: list[tuple[str, str]] = []
    if _show("show_beds"):
        features.append((str(comp.bedrooms) if comp.bedrooms is not None else "-", "bed"))
    if _show("show_baths"):
        features.append((str(comp.bathrooms) if comp.bathrooms is not None else "-", "bath"))
    if _show("show_cars"):
        features.append((str(comp.carspaces) if comp.carspaces is not None else "-", "car"))
    if _show("show_land"):
        land = comp.land_size_display or (f"{int(comp.land_size_m2)}m²" if comp.land_size_m2 else None)
        if land:
            features.append((land, "land"))
    if _show("show_build"):
        build = (comp.build_size_display or
                 (f"{int(comp.build_size_m2)}m²" if comp.build_size_m2 else None) or
                 (f"{int(comp.floor_size_m2)}m²" if comp.floor_size_m2 else None))
        if build:
            features.append((build, "build"))

    ICON_SIZE = 7.5
    VAL_GAP = 3
    FEAT_GAP = 10

    cx = x
    for val_text, icon_name in features:
        c.setFont(FONT_BOLD, SIZE_BODY)
        c.setFillColor(COLOR_TEXT)
        c.drawString(cx, y, val_text)
        vw = c.stringWidth(val_text, FONT_BOLD, SIZE_BODY)
        cx += vw + VAL_GAP
        cx += _draw_prop_icon(c, icon_name, cx, y - 1, ICON_SIZE) + FEAT_GAP


def _meta_pair(c: Canvas, x: float, y: float, label: str, value: str, col_w: float = 0) -> None:
    c.setFont(FONT_REGULAR, SIZE_SMALL)
    c.setFillColor(COLOR_MUTED)
    c.drawString(x, y, label)
    c.setFillColor(COLOR_TEXT)
    label_w = c.stringWidth(label, FONT_REGULAR, SIZE_SMALL)
    val_x = max(x + 70, x + label_w + 6)
    if col_w > 0:
        avail = col_w - (val_x - x)
        if c.stringWidth(value, FONT_REGULAR, SIZE_SMALL) > avail:
            ellipsis_w = c.stringWidth("…", FONT_REGULAR, SIZE_SMALL)
            if avail <= ellipsis_w:
                value = ""
            else:
                # Shrink core until core+"…" fits — guaranteed to terminate.
                core = value
                while core and c.stringWidth(core + "…", FONT_REGULAR, SIZE_SMALL) > avail:
                    core = core[:-1]
                value = core.rstrip() + "…" if core else ""
    c.drawString(val_x, y, value)


def _fmt(v) -> str:
    return "-" if v is None else str(v)
