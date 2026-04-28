"""
Style constants matched to the original Cotality CMA template.

All measurements in ReportLab points (1 pt = 1/72 in).
A4 portrait = 595.27 x 841.89 pt.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor, black, white

# --- Page --------------------------------------------------------------
PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN_LEFT = 50
MARGIN_RIGHT = 50
MARGIN_TOP = 50
MARGIN_BOTTOM = 50
CONTENT_WIDTH = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT

# --- Colors (eyeballed from the original Cotality PDF) -----------------
# Near-black body text
COLOR_TEXT = HexColor("#222222")
# Grey subtitle / meta text
COLOR_MUTED = HexColor("#6b6f76")
# Light grey divider lines
COLOR_RULE = HexColor("#d7d9dc")
# The Cotality blue used for "Sold" / numbered sale markers
COLOR_SOLD = HexColor("#1e9ad6")
# Red used for listing markers
COLOR_LISTING = HexColor("#d93b3b")
# Subject property pin — black (matches the "Your Property" legend in the source PDF)
COLOR_SUBJECT = HexColor("#000000")
# Light grey panel background (behind the map legend on p4)
COLOR_PANEL = HexColor("#f5f5f6")
# Header/footer divider
COLOR_FOOTER_RULE = HexColor("#dadcdf")

# --- Typography --------------------------------------------------------
FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

SIZE_H1 = 26      # "Comparable Sales" etc
SIZE_H2 = 14      # address titles in comparable cards
SIZE_BODY = 10
SIZE_SMALL = 8
SIZE_TINY = 7     # footer
SIZE_LEGEND = 12  # "Your Property" / "For Sale" / "Recently Sold" labels under maps

# --- Card layout in Comparable Sales / Listings pages -----------------
CARD_GAP = 14             # vertical space between cards
CARD_IMAGE_W = 120        # left-side thumbnail
CARD_IMAGE_H = 90
CARD_RADIUS = 3

# --- Footer text matches original exactly -----------------------------
COTALITY_FOOTER = (
    "\u00a9 Copyright 2026. RP Data Pty Ltd trading as Cotality (Cotality).  "
    "All rights reserved. No reproduction, distribution, or transmission of the copyrighted\n"
    "materials is permitted. The information is deemed reliable but not guaranteed."
)
# Appended on pages that contain REA-sourced data
REA_ATTRIBUTION = "Listing data sourced from realestate.com.au."
