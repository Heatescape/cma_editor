"""
Orchestrator: generates modified pages as a new PDF, then splices them
with the unchanged pages of the original CMA.

Pages are detected by heading text, not hardcoded indices, so the
builder works regardless of how many pages the original PDF has.

Section roles:
    Cover              REGENERATED
    Cover letter       Preserved (agent name/agency overlaid with new values)
    Your Property      Preserved (intro text + HR removed via white-rect overlay)
    Comparables Map    REGENERATED  (3 map pages)
    Comparable Sales   REGENERATED  (+ originals appended if keep_original_comparables)
    Comparable Listings REGENERATED (+ originals appended if keep_original_comparables)
    Tail pages         Preserved verbatim (demographics, schools, disclaimer …)
"""
from __future__ import annotations

import io
import os
from pathlib import Path

from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.pagesizes import A4
from pypdf import PdfReader, PdfWriter

from .models import CMAInputs
from .pages import (
    render_cover,
    render_overview_map,
    render_sales_map,
    render_listings_map,
    render_comparable_detail_pages,
)
from .chrome import set_logo_path
from .styles import MARGIN_LEFT, CONTENT_WIDTH


# ---------------------------------------------------------------------------
# Page classification
# ---------------------------------------------------------------------------

def _find_pages(reader: PdfReader) -> dict:
    """
    Scan all pages and classify by heading / body text.

    Returns a dict with lists of 0-based page indices:
        cover_letter      — "Dear Reader" / cover letter page(s)
        your_property     — "Your Property" page(s)
        sales_detail      — "Comparable Sales" detail page(s) (not map pages)
        listings_detail   — "Comparable Listings" detail page(s) (not map pages)
        tail              — pages that come after all comparable detail pages
    """
    cover_letter: list[int] = []
    your_property: list[int] = []
    sales_detail: list[int] = []
    listings_detail: list[int] = []

    for i, page in enumerate(reader.pages):
        try:
            text = (page.extract_text() or "").lower()
        except Exception:
            text = ""

        if "dear reader" in text or ("yours sincerely" in text and "proprietor" in text):
            cover_letter.append(i)
        elif "your property" in text and "comparable" not in text:
            your_property.append(i)
        elif "comparable sales" in text and "comparables map" not in text:
            sales_detail.append(i)
        elif "comparable listings" in text and "comparables map" not in text:
            listings_detail.append(i)

    # Tail: everything after the last comparable detail page
    if sales_detail or listings_detail:
        last_detail = max(
            sales_detail[-1] if sales_detail else 0,
            listings_detail[-1] if listings_detail else 0,
        )
        tail = list(range(last_detail + 1, len(reader.pages)))
    else:
        tail = []

    return {
        "cover_letter": cover_letter,
        "your_property": your_property,
        "sales_detail": sales_detail,
        "listings_detail": listings_detail,
        "tail": tail,
    }


# ---------------------------------------------------------------------------
# Page overlays
# ---------------------------------------------------------------------------

def _redact_cover_letter(original_page, agent_name: str, agency_name: str) -> None:
    """
    Produce exactly ONE signature on the cover letter page.

    A single large white rect erases everything from the footer up to just
    below "Yours Sincerely," — covering the original Serena Jeon block AND
    both Allen Zhang bio blocks. One replacement signature is then drawn at
    the top of the erased area.

    The rect spans y=88–413 (A4 coords from bottom). Adjust the drawString
    y-values if the source template positions the sign-off differently.
    """
    from reportlab.lib.colors import white, HexColor
    buf = io.BytesIO()
    oc = Canvas(buf, pagesize=A4)

    # Erase the signature block (Serena Jeon / agency name) below "Yours Sincerely,"
    # Original text sits at y≈456–466; rect must reach above that.
    oc.setFillColor(white)
    oc.rect(MARGIN_LEFT - 10, 88, CONTENT_WIDTH + 20, 402, stroke=0, fill=1)

    # Write ONE replacement signature at the original name position
    oc.setFillColor(HexColor("#1a1a1a"))
    oc.setFont("Helvetica-Bold", 10)
    oc.drawString(MARGIN_LEFT, 466, agent_name)
    oc.setFont("Helvetica", 9)
    oc.drawString(MARGIN_LEFT, 452, agency_name)

    oc.save()
    buf.seek(0)
    original_page.merge_page(PdfReader(buf).pages[0])


def _redact_page3(original_page) -> None:
    """
    Overlay a white rectangle on the "Your Property" page to erase the
    agent intro heading ("Introducing …") and the horizontal rule above it.
    """
    from reportlab.lib.colors import white
    buf = io.BytesIO()
    oc = Canvas(buf, pagesize=A4)
    oc.setFillColor(white)
    # Approximate position; adjust y / height if the output needs fine-tuning.
    oc.rect(MARGIN_LEFT - 10, 115, CONTENT_WIDTH + 20, 90, stroke=0, fill=1)
    oc.save()
    buf.seek(0)
    original_page.merge_page(PdfReader(buf).pages[0])


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_final_pdf(
    original_pdf_path: str,
    inputs: CMAInputs,
    output_path: str,
    logo_path: str | None = None,
    progress: "Callable[[str, int], None] | None" = None,
) -> str:
    """
    Generate and splice the final PDF.  Returns the output path.
    progress(step_label, pct_0_to_100) is called at key stages.
    """
    def _prog(label: str, pct: int) -> None:
        if progress:
            progress(label, pct)

    # 1. Register logo + agency name so headers can use it
    set_logo_path(
        logo_path if logo_path and os.path.exists(logo_path) else "",
        agency_name=inputs.agency_name,
    )

    # 2. Classify original PDF pages by heading text
    _prog("Scanning original PDF...", 5)
    original = PdfReader(original_pdf_path)
    pages = _find_pages(original)

    # 3. Render new pages into an in-memory buffer.
    #    Page indices are tracked dynamically so optional map pages can be omitted.
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4)
    _next = 0  # running buffer page index

    _prog("Rendering cover page...", 10)
    render_cover(c, inputs)
    c.showPage()
    cover_buf = _next; _next += 1

    overview_map_buf = None
    if inputs.include_overview_map:
        _prog("Generating overview map...", 20)
        render_overview_map(c, inputs)
        c.showPage()
        overview_map_buf = _next; _next += 1

    sales_map_buf = None
    if inputs.include_sales_map:
        _prog("Generating sales map...", 35)
        render_sales_map(c, inputs)
        c.showPage()
        sales_map_buf = _next; _next += 1

    sales_detail_start = _next
    n_sales = render_comparable_detail_pages(
        c, inputs, inputs.sales,
        title="Comparable Sales",
        is_listings=False,
        progress=progress,
        pct_start=55, pct_end=68,
    )
    _next += n_sales

    listings_map_buf = None
    if inputs.include_listings_map:
        _prog("Generating listings map...", 68)
        render_listings_map(c, inputs)
        c.showPage()
        listings_map_buf = _next; _next += 1

    listings_detail_start = _next
    n_listings = render_comparable_detail_pages(
        c, inputs, inputs.listings,
        title="Comparable Listings",
        is_listings=True,
        progress=progress,
        pct_start=78, pct_end=90,
    )

    _prog("Assembling final PDF...", 90)
    c.save()
    buf.seek(0)
    regenerated = PdfReader(buf)

    # 4. Splice pages into final PDF
    writer = PdfWriter()

    # Cover (new)
    writer.add_page(regenerated.pages[cover_buf])

    # Cover letter (original) — overlay new agent name/agency
    for idx in pages["cover_letter"]:
        page = original.pages[idx]
        _redact_cover_letter(page, inputs.agent_name, inputs.agency_name)
        writer.add_page(page)

    # Your Property (original) — erase intro text
    for idx in pages["your_property"]:
        page = original.pages[idx]
        _redact_page3(page)
        writer.add_page(page)

    # Overview map (new, if enabled)
    if overview_map_buf is not None:
        writer.add_page(regenerated.pages[overview_map_buf])

    # Sales map (new, if enabled)
    if sales_map_buf is not None:
        writer.add_page(regenerated.pages[sales_map_buf])

    # New sales detail pages
    for i in range(n_sales):
        writer.add_page(regenerated.pages[sales_detail_start + i])

    # Original sales detail pages (appended when keep_original_comparables=True)
    if inputs.keep_original_comparables:
        for idx in pages["sales_detail"]:
            writer.add_page(original.pages[idx])

    # Listings map (new, if enabled)
    if listings_map_buf is not None:
        writer.add_page(regenerated.pages[listings_map_buf])

    # New listings detail pages
    for i in range(n_listings):
        writer.add_page(regenerated.pages[listings_detail_start + i])

    # Original listings detail pages (appended when keep_original_comparables=True)
    if inputs.keep_original_comparables:
        for idx in pages["listings_detail"]:
            writer.add_page(original.pages[idx])

    # Tail pages (original, verbatim)
    for idx in pages["tail"]:
        writer.add_page(original.pages[idx])

    # 5. Write output
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        writer.write(f)

    return output_path
