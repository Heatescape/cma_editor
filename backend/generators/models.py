"""
Data models used by the page generators.

These are normalized from REA scraper output (and the user-provided
subject property form) before being handed to the generators.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SubjectProperty:
    """The property being appraised (page 1, 3, 15 hero)."""
    address: str
    suburb: str = ""
    state: str = "NSW"
    postcode: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    carspaces: Optional[int] = None
    land_size_m2: Optional[float] = None
    hero_image_path: str = ""   # local path to uploaded replacement image
    property_type: str = "house"   # "house" | "land" | "apartment"
    build_size_m2: Optional[float] = None
    build_size_display: str = ""
    floor_size_m2: Optional[float] = None
    floor_size_display: str = ""


@dataclass
class ComparableProperty:
    """One row in a comparable sales or listings page."""
    index: int                    # 1-based marker number
    address: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    carspaces: Optional[int] = None
    land_size_m2: Optional[float] = None
    land_size_display: str = ""   # e.g. "558m²" or "2.19ha"
    price_display: str = ""       # "$1,075,000", "-", "Contact Agent", ...
    status: str = "Sold"          # "Sold" | "For Sale" | "Listing"
    sold_date: str = ""           # ISO or display-formatted
    listing_date: str = ""
    first_listing: str = ""
    last_listing: str = ""
    days_on_market: str = ""
    year_built: str = ""
    distance_km: Optional[float] = None  # from subject
    thumbnail_path: str = ""      # local path to hero image (downloaded)
    source_url: str = ""          # REA URL, for attribution
    from_rea: bool = True         # whether data came from REA scraping
    headline: str = ""            # listing tagline
    property_type: str = "house"  # "house" | "land" | "apartment"
    build_size_m2: Optional[float] = None
    build_size_display: str = ""
    floor_size_m2: Optional[float] = None
    floor_size_display: str = ""
    show_beds: bool = True
    show_baths: bool = True
    show_cars: bool = True
    show_land: bool = True
    show_build: bool = True

    def format_features(self) -> str:
        """Compact bed/bath/car/land line used on comparable cards."""
        parts: list[str] = []
        parts.append(f"\U0001f6cf {self.bedrooms if self.bedrooms is not None else '-'}")
        parts.append(f"\U0001f6c1 {self.bathrooms if self.bathrooms is not None else '-'}")
        parts.append(f"\U0001f697 {self.carspaces if self.carspaces is not None else '-'}")
        if self.land_size_display:
            parts.append(self.land_size_display)
        elif self.land_size_m2:
            parts.append(f"{int(self.land_size_m2)}m\u00b2")
        else:
            parts.append("-")
        return "  ".join(parts)


@dataclass
class CMAInputs:
    """Everything needed to regenerate the editable pages."""
    subject: SubjectProperty
    sales: list[ComparableProperty] = field(default_factory=list)
    listings: list[ComparableProperty] = field(default_factory=list)
    google_maps_api_key: str = ""
    keep_original_comparables: bool = False
    agent_name: str = ""
    agency_name: str = ""
    agent_email: str = ""
    report_date: str = ""
    marker_size: str = "medium"
    include_overview_map: bool = True
    include_sales_map: bool = True
    include_listings_map: bool = True
