"""
Scraper for realestate.com.au property pages.

Uses Playwright to bypass Cloudflare/Akamai protection and extract
the hidden ArgonautExchange JSON data embedded in the page.

Data extracted from:
  - window.ArgonautExchange - contains full property data (Apollo cache)
  - Image URLs from the page itself as fallback
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from patchright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# Persistent Chrome profile directory — reuses cookies/login between runs.
# Override with env var CMA_CHROME_PROFILE (set to empty string to disable).
_DEFAULT_PROFILE_DIR = Path.home() / ".cma_rea_profile"
CHROME_PROFILE_DIR: Optional[Path] = (
    Path(os.environ["CMA_CHROME_PROFILE"]) if "CMA_CHROME_PROFILE" in os.environ
    else _DEFAULT_PROFILE_DIR
) or None


# ---------- Data model ---------------------------------------------------


@dataclass
class ScrapedProperty:
    """Normalized property data extracted from a REA listing page."""

    url: str
    address: str = ""
    suburb: str = ""
    state: str = ""
    postcode: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    carspaces: Optional[int] = None
    land_size_m2: Optional[float] = None
    build_size_m2: Optional[float] = None
    price_display: str = ""              # e.g. "$1,075,000" or "Contact Agent" or "Price on Application"
    price_numeric: Optional[int] = None  # numeric version if available
    property_type: str = ""
    year_built: Optional[int] = None
    listing_date: str = ""               # ISO date string, first listing
    sold_date: str = ""                  # ISO date string if sold
    days_on_market: Optional[int] = None
    status: str = ""                     # 'sold', 'for_sale', 'listing'
    headline: str = ""                   # listing headline/title
    agency_name: str = ""
    hero_image_url: str = ""             # primary photo URL
    image_urls: list[str] = field(default_factory=list)
    raw_json: dict = field(default_factory=dict)  # keep original for debugging

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw_json", None)  # usually too big to send to frontend
        return d


# ---------- Scraper ------------------------------------------------------


class REAScraper:
    """
    Playwright-based scraper for realestate.com.au.

    Usage:
        with REAScraper() as s:
            prop = s.scrape("https://www.realestate.com.au/property-house-nsw-...")
    """

    def __init__(self, headless: bool = False, timeout_ms: int = 45000,
                 profile_dir: Optional[Path] = CHROME_PROFILE_DIR):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.profile_dir = profile_dir
        self._pw = None
        self._browser = None
        self._context = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        if self.profile_dir:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            try:
                # Persistent context: cookies/session survive between runs.
                # On first launch the browser opens so the user can log in to REA.
                self._browser = None
                self._context = self._pw.chromium.launch_persistent_context(
                    str(self.profile_dir),
                    headless=self.headless,
                    viewport={"width": 1440, "height": 900},
                    locale="en-AU",
                    timezone_id="Australia/Sydney",
                )
            except Exception:
                # Profile locked (another instance running) — fall back to ephemeral context
                self._browser = self._pw.chromium.launch(headless=self.headless)
                self._context = self._browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    locale="en-AU",
                    timezone_id="Australia/Sydney",
                )
        else:
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="en-AU",
                timezone_id="Australia/Sydney",
            )
        self._warmup_done = False
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    def _wait_for_real_page(self, page, timeout_ms: int = 30000) -> None:
        """Wait until the page has real content (not just a Kasada challenge)."""
        try:
            # Wait for <title> to be non-empty — Kasada challenge pages have no title
            page.wait_for_function(
                "() => document.title.length > 0",
                timeout=timeout_ms,
            )
        except PlaywrightTimeout:
            pass

    def scrape(self, url: str) -> ScrapedProperty:
        """Scrape a REA property page and return normalized data."""
        if not self._context:
            raise RuntimeError("Scraper must be used as a context manager")

        _429_delays = [15, 45]
        html = ""
        argonaut_live = None
        for attempt in range(len(_429_delays) + 1):
            page = self._context.new_page()
            try:
                # Warm up: visit homepage so Kasada JS challenge completes
                if not self._warmup_done:
                    try:
                        page.goto("https://www.realestate.com.au/", wait_until="domcontentloaded", timeout=self.timeout_ms)
                        self._wait_for_real_page(page)
                    except Exception:
                        pass
                    self._warmup_done = True

                resp = None
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                except Exception:
                    pass  # HTTP error codes raise here — still try to read content
                self._wait_for_real_page(page)
                # Extra settle time for JS to populate data
                page.wait_for_timeout(random.randint(3000, 7000))

                html = page.content()
                # Try to read window.ArgonautExchange directly from the browser context too
                try:
                    argonaut_live = page.evaluate(
                        "() => window.ArgonautExchange ? JSON.parse(JSON.stringify(window.ArgonautExchange)) : null"
                    )
                except Exception:
                    argonaut_live = None

            finally:
                page.close()

            is_429 = (resp is not None and resp.status == 429) or "HTTP ERROR 429" in html
            if is_429 and attempt < len(_429_delays):
                time.sleep(_429_delays[attempt])
                continue
            break

        return self._parse_html(url, html, argonaut_live)

    # ---------- Parsing helpers ------------------------------------------

    @staticmethod
    def _extract_argonaut(html: str) -> Optional[dict]:
        """
        Pull the window.ArgonautExchange blob out of the raw HTML.

        Handles both forms we've seen:
            window.ArgonautExchange = {...};
            window.ArgonautExchange={...};
        """
        m = re.search(
            r"window\.ArgonautExchange\s*=\s*(\{.*?\})\s*;\s*</script>",
            html,
            re.DOTALL,
        )
        if not m:
            # Try without the trailing script tag (sometimes inline)
            m = re.search(
                r"window\.ArgonautExchange\s*=\s*(\{.+?\})\s*;",
                html,
                re.DOTALL,
            )
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _walk(obj, predicate):
        """Yield every sub-object satisfying predicate(obj)."""
        if isinstance(obj, dict):
            if predicate(obj):
                yield obj
            for v in obj.values():
                yield from REAScraper._walk(v, predicate)
        elif isinstance(obj, list):
            for v in obj:
                yield from REAScraper._walk(v, predicate)

    @staticmethod
    def _unwrap_argonaut(data: dict) -> dict:
        """
        Unwrap the nested ArgonautExchange structure.

        Modern REA pages store data as:
          { "resi-property_...": { "urqlClientCache": "<JSON string>" } }
        where the inner JSON has query-ID keys whose "data" values are
        themselves JSON strings containing the actual listing data.
        """
        # Try the urqlClientCache path first
        for top_val in data.values():
            if not isinstance(top_val, dict):
                continue
            cache_str = top_val.get("urqlClientCache")
            if not isinstance(cache_str, str):
                continue
            try:
                cache = json.loads(cache_str)
            except (json.JSONDecodeError, TypeError):
                continue
            # Find the first entry whose "data" contains a listing
            for entry in cache.values():
                if not isinstance(entry, dict):
                    continue
                inner_str = entry.get("data")
                if not isinstance(inner_str, str):
                    continue
                try:
                    inner = json.loads(inner_str)
                except (json.JSONDecodeError, TypeError):
                    continue
                # Look for the listing object at details.listing
                listing = (inner.get("details") or {}).get("listing")
                if isinstance(listing, dict) and "address" in listing:
                    return inner
        # Fallback: return original data (old Apollo-style cache)
        return data

    def _parse_html(self, url: str, html: str, argonaut_live: Optional[dict]) -> ScrapedProperty:
        data = argonaut_live or self._extract_argonaut(html)
        if not data:
            raise ScraperError(
                f"Could not find ArgonautExchange JSON on page. "
                f"The page may be a captcha/block page. URL: {url}"
            )

        prop = ScrapedProperty(url=url, raw_json=data)

        # Unwrap the nested urqlClientCache structure
        unwrapped = self._unwrap_argonaut(data)
        listing = (unwrapped.get("details") or {}).get("listing")

        if not listing:
            # Fallback: walk the tree for a listing-like object (old format)
            candidates = list(
                self._walk(
                    data,
                    lambda o: isinstance(o, dict)
                    and ("address" in o or "propertyType" in o or "listingId" in o),
                )
            )
            for c in candidates:
                if isinstance(c.get("address"), dict) and ("price" in c or "propertyType" in c):
                    listing = c
                    break
            if listing is None and candidates:
                listing = candidates[0]

        if listing:
            self._populate_from_listing(prop, listing)

        # Images: collect from listing media or page-level media blocks
        self._populate_images(prop, listing or data)

        # Status detection (sold vs for sale)
        self._populate_status(prop, unwrapped, html)

        # HTML fallback for bed/bath/car counts — try multiple formats and field names
        _html_patterns: dict[str, list[str]] = {
            "bedrooms": [
                r'"bedrooms"\s*:\s*\{"value"\s*:\s*(\d+)',   # {"value":N}
                r'"bedrooms"\s*:\s*(\d+)',                    # plain int
                r'"beds"\s*:\s*(\d+)',
                r'"numBedrooms"\s*:\s*(\d+)',
                r'"bedroomCount"\s*:\s*(\d+)',
                r'aria-label="(\d+)\s+[Bb]ed',
                r'"type"\s*:\s*"BEDROOMS?"\s*,\s*"value"\s*:\s*"(\d+)"',
            ],
            "bathrooms": [
                r'"bathrooms"\s*:\s*\{"value"\s*:\s*(\d+)',
                r'"bathrooms"\s*:\s*(\d+)',
                r'"baths"\s*:\s*(\d+)',
                r'"numBathrooms"\s*:\s*(\d+)',
                r'"bathroomCount"\s*:\s*(\d+)',
                r'aria-label="(\d+)\s+[Bb]ath',
                r'"type"\s*:\s*"BATHROOMS?"\s*,\s*"value"\s*:\s*"(\d+)"',
            ],
            "carspaces": [
                r'"parkingSpaces"\s*:\s*\{"value"\s*:\s*(\d+)',
                r'"parkingSpaces"\s*:\s*(\d+)',
                r'"carspaces"\s*:\s*(\d+)',
                r'"garages"\s*:\s*(\d+)',
                r'"parkingCount"\s*:\s*(\d+)',
                r'"numParking"\s*:\s*(\d+)',
                r'aria-label="(\d+)\s+[Cc]ar',
                r'"type"\s*:\s*"PARKING"\s*,\s*"value"\s*:\s*"(\d+)"',
            ],
        }
        for attr, patterns in _html_patterns.items():
            if getattr(prop, attr) is None:
                for pattern in patterns:
                    m = re.search(pattern, html)
                    if m:
                        setattr(prop, attr, int(m.group(1)))
                        break

        # Last-resort: parse page <title> or h1 text for "X bed Y bath Z car" pattern.
        # REA titles often look like "3 Bed 2 Bath 1 Parking" or
        # "Studio Apartment with 1 Bathroom".
        if prop.bedrooms is None or prop.bathrooms is None or prop.carspaces is None:
            title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            title_text = title_m.group(1) if title_m else ""
            h1_m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
            h1_text = h1_m.group(1) if h1_m else ""
            scan = f"{title_text} {h1_text}"
            if prop.bedrooms is None:
                bm = re.search(r"(\d+)\s*[Bb]ed", scan)
                if bm:
                    prop.bedrooms = int(bm.group(1))
            if prop.bathrooms is None:
                bm = re.search(r"(\d+)\s*[Bb]ath", scan)
                if bm:
                    prop.bathrooms = int(bm.group(1))
            if prop.carspaces is None:
                bm = re.search(r"(\d+)\s*(?:[Cc]ar|[Pp]arking|[Gg]arage)", scan)
                if bm:
                    prop.carspaces = int(bm.group(1))

        # JSON-LD structured data (most reliable when present).
        # REA embeds schema.org/Accommodation or schema.org/Product JSON-LD.
        if prop.bedrooms is None or prop.bathrooms is None or prop.carspaces is None:
            for ld_m in re.finditer(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html, re.DOTALL | re.IGNORECASE,
            ):
                try:
                    ld = json.loads(ld_m.group(1))
                except (json.JSONDecodeError, ValueError):
                    continue
                # Support both single objects and @graph arrays
                items = ld.get("@graph", [ld]) if isinstance(ld, dict) else (ld if isinstance(ld, list) else [])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if prop.bedrooms is None:
                        prop.bedrooms = self._to_int(
                            item.get("numberOfBedrooms") or item.get("numberOfRooms")
                        )
                    if prop.bathrooms is None:
                        prop.bathrooms = self._to_int(
                            item.get("numberOfBathroomsTotal") or item.get("numberOfBathrooms")
                        )
                    if prop.carspaces is None:
                        prop.carspaces = self._to_int(
                            item.get("numberOfParkingSpaces") or item.get("numberOfGarages")
                        )
                    if all(x is not None for x in (prop.bedrooms, prop.bathrooms, prop.carspaces)):
                        break

        # HTML fallback for sold date ("Sold on 04 Nov 2025")
        if not prop.sold_date:
            m = re.search(
                r"[Ss]old\s+(?:on\s+)?(\d{1,2}\s+\w+\s+\d{4})",
                html,
            )
            if m:
                prop.sold_date = self._parse_display_date(m.group(1))

        return prop

    def _populate_from_listing(self, prop: ScrapedProperty, L: dict) -> None:
        # Address
        addr = L.get("address") or {}
        if isinstance(addr, dict):
            display = addr.get("display") or {}
            if isinstance(display, dict):
                prop.address = display.get("fullAddress") or display.get("shortAddress") or ""
                geocode = display.get("geocode") or {}
                if isinstance(geocode, dict):
                    prop.latitude = geocode.get("latitude")
                    prop.longitude = geocode.get("longitude")
            elif isinstance(display, str):
                prop.address = display
            # Also check addr.location for older format
            if prop.latitude is None:
                loc = addr.get("location") or {}
                if isinstance(loc, dict):
                    prop.latitude = loc.get("latitude")
                    prop.longitude = loc.get("longitude")
            prop.suburb = addr.get("suburb") or ""
            prop.state = addr.get("state") or ""
            prop.postcode = addr.get("postcode") or ""

        # General features — try multiple key paths REA has used across versions
        features = (
            L.get("generalFeatures")
            or L.get("features")
            or L.get("propertyFeatures")
            or L.get("listingFeatures")
            or L.get("structuredFeatures")
            or {}
        )

        def _fval(obj, *keys):
            """Get an int from a feature dict entry that may be int or {value:N}."""
            for k in keys:
                v = obj.get(k) if isinstance(obj, dict) else None
                if v is None:
                    continue
                if isinstance(v, dict):
                    return self._to_int(v.get("value") or v.get("displayValue"))
                return self._to_int(v)
            return None

        if isinstance(features, dict):
            prop.bedrooms  = _fval(features, "bedrooms")
            prop.bathrooms = _fval(features, "bathrooms")
            prop.carspaces = _fval(features, "parkingSpaces", "carspaces", "garages")

        # Fallback: walk entire listing for dicts with bed/bath/car keys (any naming)
        _bed_keys  = ("bedrooms", "bedroomCount", "numBedrooms", "numberOfBedrooms", "beds")
        _bath_keys = ("bathrooms", "bathroomCount", "numBathrooms", "numberOfBathrooms", "baths")
        _car_keys  = ("parkingSpaces", "carspaces", "garages", "parkingCount", "numParking", "parking")

        if prop.bedrooms is None or prop.bathrooms is None or prop.carspaces is None:
            for o in self._walk(
                L,
                lambda d: isinstance(d, dict)
                and any(k in d for k in _bed_keys + _bath_keys + _car_keys),
            ):
                if prop.bedrooms is None:
                    prop.bedrooms = _fval(o, *_bed_keys)
                if prop.bathrooms is None:
                    prop.bathrooms = _fval(o, *_bath_keys)
                if prop.carspaces is None:
                    prop.carspaces = _fval(o, *_car_keys)
                if all(x is not None for x in (prop.bedrooms, prop.bathrooms, prop.carspaces)):
                    break

        # Fallback: walk for {type, value} attribute-style arrays (newer REA format)
        if prop.bedrooms is None or prop.bathrooms is None or prop.carspaces is None:
            _type_map = {
                "BEDROOM": "bedrooms", "BEDROOMS": "bedrooms",
                "BATHROOM": "bathrooms", "BATHROOMS": "bathrooms",
                "PARKING": "carspaces", "CAR": "carspaces", "CARSPACE": "carspaces",
            }
            for o in self._walk(
                L,
                lambda d: isinstance(d, dict) and "type" in d and "value" in d,
            ):
                attr = _type_map.get(str(o.get("type", "")).upper())
                if attr and getattr(prop, attr) is None:
                    setattr(prop, attr, self._to_int(o.get("value")))

        # Land size — new format uses propertySizes.land, old uses landSize
        land = (L.get("propertySizes") or {}).get("land") or L.get("landSize") or {}
        if isinstance(land, dict):
            val = land.get("displayValue") or land.get("value")
            unit_obj = land.get("sizeUnit") or {}
            unit = (unit_obj.get("displayValue") if isinstance(unit_obj, dict) else "") or land.get("unit", "")
            unit = unit.lower()
            if val is not None:
                try:
                    v = float(str(val).replace(",", ""))
                    if "hectare" in unit or unit == "ha":
                        v *= 10000
                    prop.land_size_m2 = v
                except (TypeError, ValueError):
                    pass

        # Building area (house build size / apartment floor area)
        building = (L.get("propertySizes") or {}).get("building") or {}
        for bkey in ("buildingArea", "floorArea", "buildingSize", "internalArea"):
            building = building or L.get(bkey) or {}
        if isinstance(building, dict):
            val = building.get("displayValue") or building.get("value")
            unit_obj = building.get("sizeUnit") or {}
            unit = (unit_obj.get("displayValue") if isinstance(unit_obj, dict) else "") or building.get("unit", "")
            unit = unit.lower()
            if val is not None:
                try:
                    bv = float(str(val).replace(",", ""))
                    if "hectare" in unit or unit == "ha":
                        bv *= 10000
                    prop.build_size_m2 = bv
                except (TypeError, ValueError):
                    pass

        # Property type
        pt = L.get("propertyType")
        if isinstance(pt, dict):
            prop.property_type = pt.get("display") or pt.get("id") or ""
        elif isinstance(pt, str):
            prop.property_type = pt

        # Price
        price = L.get("price") or {}
        if isinstance(price, dict):
            prop.price_display = price.get("display") or ""
        elif isinstance(price, str):
            prop.price_display = price

        # Listing dates
        for key in ("listingDate", "listedDate", "dateListed", "firstListedDate"):
            v = L.get(key)
            if v:
                prop.listing_date = self._extract_date_str(v)
                break

        # Sold date + sold price — try multiple key paths
        sold = L.get("soldDetails") or L.get("sold") or {}
        if isinstance(sold, dict):
            raw_date = (
                sold.get("date")
                or sold.get("soldDate")
                or sold.get("contractDate")
                or sold.get("settlementDate")
                or ""
            )
            prop.sold_date = self._extract_date_str(raw_date)
            sp = sold.get("price") or sold.get("soldPrice") or {}
            if isinstance(sp, dict):
                prop.price_display = sp.get("display") or prop.price_display
                prop.price_numeric = self._to_int(sp.get("value"))
        # Fallback: look for dateSold / saleDate at top level
        if not prop.sold_date:
            for key in ("dateSold", "saleDate", "soldAt", "contractDate"):
                v = L.get(key)
                if v:
                    prop.sold_date = self._extract_date_str(v)
                    break

        # Headline / title
        prop.headline = L.get("title") or L.get("headline") or ""
        if not prop.headline:
            desc = L.get("description")
            if isinstance(desc, dict):
                prop.headline = desc.get("headline", "")

        # Agency
        listing_company = L.get("listingCompany") or L.get("agency") or {}
        if isinstance(listing_company, dict):
            prop.agency_name = listing_company.get("name") or ""

        # Numeric price parse from display string if still missing
        if prop.price_numeric is None and prop.price_display:
            prop.price_numeric = self._parse_price(prop.price_display)

    def _populate_images(self, prop: ScrapedProperty, data: dict) -> None:
        """
        Collect image URLs. REA media entries typically have 'templatedUrl'
        with {size} and sometimes {index} placeholders.
        """
        seen: set[str] = set()

        def _resolve(t: str, idx: int = 0) -> str:
            """Substitute size and index placeholders."""
            return t.replace("{size}", "1024x768").replace("{index}", str(idx))

        def _add(url: str) -> None:
            if url and url.startswith("http") and url not in seen:
                seen.add(url)
                prop.image_urls.append(url)

        # Structured path: listing.media.mainImage + listing.media.images[]
        media_block = data.get("media") or {}
        if isinstance(media_block, dict):
            main_img = media_block.get("mainImage") or {}
            if isinstance(main_img, dict) and main_img.get("templatedUrl"):
                _add(_resolve(main_img["templatedUrl"], 0))
            for idx, img in enumerate(media_block.get("images") or []):
                if not isinstance(img, dict):
                    continue
                t = img.get("templatedUrl")
                if isinstance(t, str):
                    # Use the image's own index field when available so each
                    # gallery slot gets a distinct URL (not all resolved to "0").
                    real_idx = img.get("index", idx)
                    _add(_resolve(t, real_idx))

        # Walk the full tree only when the structured media block gave us nothing
        # (some sold listings store the gallery under a non-standard key).
        # Skipping this when we already have images prevents agent profile photos
        # and agency logos (also typed "Image" in the JSON) from contaminating
        # the property photo list.
        if not prop.image_urls:
            for media in self._walk(
                data,
                lambda o: isinstance(o, dict)
                and (o.get("__typename") == "Image" or "templatedUrl" in o),
            ):
                t = media.get("templatedUrl")
                if isinstance(t, str):
                    real_idx = media.get("index", 0)
                    _add(_resolve(t, real_idx))
                u = media.get("url")
                if isinstance(u, str):
                    _add(u)

        if prop.image_urls:
            prop.hero_image_url = prop.image_urls[0]

    def _populate_status(self, prop: ScrapedProperty, data: dict, html: str) -> None:
        listing = (data.get("details") or {}).get("listing") or {}
        typename = listing.get("__typename", "")
        if "Sold" in typename:
            prop.status = "sold"
        elif "Buy" in typename:
            prop.status = "for_sale"
        else:
            # Fallback: walk for channel info
            for o in self._walk(
                data, lambda d: isinstance(d, dict) and ("productDepth" in d or "channel" in d)
            ):
                channel = o.get("channel") or ""
                if channel == "sold":
                    prop.status = "sold"
                    break
                elif channel == "buy":
                    prop.status = "for_sale"
                    break
        if not prop.status:
            if prop.sold_date:
                prop.status = "sold"
            else:
                prop.status = "for_sale"

    # ---------- small helpers --------------------------------------------

    @staticmethod
    def _extract_date_str(v) -> str:
        """
        Safely pull a YYYY-MM-DD string from a date value that may be:
          - a plain ISO string: "2025-11-04"
          - a dict with 'value', 'display', or 'date' key: {"display": "04 Nov 2025", ...}
        Returns "" if nothing useful is found.
        """
        if not v:
            return ""
        if isinstance(v, dict):
            # Prefer ISO-looking value key, fall back to display
            candidate = v.get("value") or v.get("date") or v.get("display") or ""
            v = str(candidate)
        else:
            v = str(v)
        # If it looks like an ISO date already, take first 10 chars
        if re.match(r"\d{4}-\d{2}-\d{2}", v):
            return v[:10]
        # Otherwise try to parse a human-readable date
        return REAScraper._parse_display_date(v) if v else ""

    @staticmethod
    def _parse_display_date(s: str) -> str:
        """
        Convert a human-readable date like '04 Nov 2025' to ISO 'YYYY-MM-DD'.
        Returns the original string on parse failure.
        """
        import calendar
        months = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
        months.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})
        parts = s.strip().split()
        if len(parts) == 3:
            day, mon, year = parts
            mon_num = months.get(mon.lower()[:3])
            if mon_num:
                try:
                    return f"{int(year):04d}-{mon_num:02d}-{int(day):02d}"
                except ValueError:
                    pass
        return s[:10]

    @staticmethod
    def _to_int(v) -> Optional[int]:
        if v is None:
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_price(s: str) -> Optional[int]:
        """Pull the first dollar amount from a price string."""
        if not s:
            return None
        m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", s)
        if not m:
            return None
        try:
            return int(float(m.group(1).replace(",", "")))
        except ValueError:
            return None


class ScraperError(Exception):
    """Raised when REA scraping fails (blocked, page not found, etc.)."""


# ---------- module-level convenience ------------------------------------


def scrape_one(url: str, headless: bool = False) -> ScrapedProperty:
    """Convenience: scrape a single URL."""
    with REAScraper(headless=headless) as s:
        return s.scrape(url)


def scrape_many(urls: list[str], headless: bool = False) -> list[ScrapedProperty | ScraperError]:
    """
    Scrape multiple URLs sequentially, reusing one browser instance.
    Returns a list where each element is either a ScrapedProperty or a
    ScraperError (so the caller can surface per-URL failures).
    """
    results: list[ScrapedProperty | ScraperError] = []
    with REAScraper(headless=headless) as s:
        for u in urls:
            try:
                results.append(s.scrape(u))
            except Exception as e:
                results.append(ScraperError(str(e)))
    return results


# ---------- Search helpers ----------------------------------------------


def _parse_au_address(address: str) -> dict:
    """
    Extract suburb, state, postcode from an Australian address string.
    Used as a fallback when subject_info doesn't include these fields.
    """
    _STATES = {"NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"}
    m = re.search(
        r"\b(" + "|".join(_STATES) + r")\s+(\d{4})\s*$",
        address, re.IGNORECASE,
    )
    if not m:
        return {}
    state    = m.group(1).upper()
    postcode = m.group(2)
    before   = address[:m.start()].strip().rstrip(",").strip()

    if "," in before:
        suburb = before.rsplit(",", 1)[-1].strip()
    else:
        _STREET_TYPES = {
            "street", "st", "road", "rd", "avenue", "ave", "drive", "dr",
            "court", "ct", "place", "pl", "crescent", "cres", "boulevard",
            "blvd", "lane", "ln", "way", "close", "terrace", "tce",
            "parade", "pde", "grove", "gr", "highway", "hwy", "circuit", "cct",
        }
        words = before.split()
        cut = len(words)
        for j in range(len(words) - 1, -1, -1):
            if words[j].lower().rstrip(".") in _STREET_TYPES:
                cut = j + 1
                break
        suburb = " ".join(words[cut:]) if cut < len(words) else (words[-1] if words else "")

    return {"suburb": suburb.title(), "state": state, "postcode": postcode}


def _extract_building_address(address: str) -> Optional[str]:
    """
    Return the building street address from a unit address.
    '5/12 George Street, Parramatta NSW 2150' → '12 George Street'
    '5A/12-14 Main Road' → '12-14 Main Road'
    """
    m = re.match(
        r"^\s*[\w]+\s*/\s*(\d[\w-]*\s+\w[^,]*?)(?:\s*,|\s+[A-Z][a-z]+\s+[A-Z]{2,3}|\s+\d{4})",
        address.strip(),
    )
    if m:
        return m.group(1).strip()
    return None


def _build_search_url(
    channel: str,
    suburb: str,
    state: str,
    postcode: str,
    beds_min: Optional[int] = None,
    beds_max: Optional[int] = None,
    baths_min: Optional[int] = None,
    baths_max: Optional[int] = None,
    cars_min: Optional[int] = None,
    cars_max: Optional[int] = None,
    building_address: Optional[str] = None,
    land_min: Optional[float] = None,
    land_max: Optional[float] = None,
    build_min: Optional[float] = None,
    build_max: Optional[float] = None,
) -> str:
    """
    Build a REA search results page URL.

    URL format: https://www.realestate.com.au/{channel}/in-{location}/list-1
    location:   {suburb}%2C+{state}+{postcode}            (suburb search)
                {building}%2C+{suburb}%2C+{state}+{postcode} (building search)

    Filter params: beds-min/max, baths-min/max, cars-min/max, land-min/max, building-size-min/max
    Verify these URL patterns with debug_scrape.py if results are unexpected.
    """
    suburb_slug = suburb.lower().replace(" ", "-")
    state_slug  = state.lower()
    pc_part     = f"+{postcode}" if postcode else ""

    if building_address:
        bldg_slug = building_address.lower().replace(" ", "+")
        location  = f"{bldg_slug}%2C+{suburb_slug}%2C+{state_slug}{pc_part}"
    else:
        location  = f"{suburb_slug}%2C+{state_slug}{pc_part}"

    url = f"https://www.realestate.com.au/{channel}/in-{location}/list-1"

    params: list[str] = []
    if beds_min  is not None: params.append(f"beds-min={beds_min}")
    if beds_max  is not None: params.append(f"beds-max={beds_max}")
    if baths_min is not None: params.append(f"baths-min={baths_min}")
    if baths_max is not None: params.append(f"baths-max={baths_max}")
    if cars_min  is not None: params.append(f"cars-min={cars_min}")
    if cars_max  is not None: params.append(f"cars-max={cars_max}")
    if land_min  is not None: params.append(f"land-min={int(land_min)}")
    if land_max  is not None: params.append(f"land-max={int(land_max)}")
    if build_min is not None: params.append(f"building-size-min={int(build_min)}")
    if build_max is not None: params.append(f"building-size-max={int(build_max)}")

    if params:
        url += "?" + "&".join(params)
    return url


def _extract_search_metadata(html: str) -> dict[str, dict]:
    """
    Extract per-listing {beds, baths, cars} from a REA search results page.
    Returns {normalized_url: {beds, baths, cars}}.

    REA embeds generalFeatures inside window.ArgonautExchange → urqlClientCache
    → inner JSON data strings. Each listing object has _links.canonical.href
    (the canonical URL) and generalFeatures with bedroom/bathroom/parkingSpaces.
    """
    # Find ArgonautExchange (same extraction as _extract_argonaut)
    m = re.search(
        r"window\.ArgonautExchange\s*=\s*(\{.*?\})\s*;\s*</script>",
        html, re.DOTALL,
    )
    if not m:
        m = re.search(r"window\.ArgonautExchange\s*=\s*(\{.+?\})\s*;", html, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}

    meta: dict[str, dict] = {}
    base = "https://www.realestate.com.au"

    def _fval(obj, *keys):
        for k in keys:
            v = obj.get(k) if isinstance(obj, dict) else None
            if v is None:
                continue
            if isinstance(v, dict):
                return REAScraper._to_int(v.get("value") or v.get("displayValue"))
            return REAScraper._to_int(v)
        return None

    def _normalise_url(u: str) -> str:
        if not u.startswith("http"):
            u = base + u
        return u.split("?")[0].rstrip("/").lower()

    def _process(obj):
        if not isinstance(obj, dict):
            return
        gf = obj.get("generalFeatures")
        if not isinstance(gf, dict):
            return
        # Must have at least beds or baths to be useful
        if not any(k in gf for k in ("bedrooms", "bathrooms", "parkingSpaces")):
            return
        # Extract canonical URL from _links or direct keys
        url = None
        links = obj.get("_links") or {}
        if isinstance(links, dict):
            canonical = links.get("canonical") or {}
            if isinstance(canonical, dict):
                url = canonical.get("href") or canonical.get("path")
        if not url:
            url = (obj.get("listingUrl") or obj.get("canonicalUrl") or obj.get("url") or "")
        if not url:
            return
        key = _normalise_url(url)
        meta[key] = {
            "beds":  _fval(gf, "bedrooms"),
            "baths": _fval(gf, "bathrooms"),
            "cars":  _fval(gf, "parkingSpaces", "carspaces", "garages"),
        }

    def _walk(obj):
        if isinstance(obj, dict):
            _process(obj)
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    for top_val in data.values():
        if not isinstance(top_val, dict):
            continue
        cache_str = top_val.get("urqlClientCache")
        if isinstance(cache_str, str):
            try:
                cache = json.loads(cache_str)
                for entry in cache.values():
                    if not isinstance(entry, dict):
                        continue
                    inner_str = entry.get("data")
                    if not isinstance(inner_str, str):
                        continue
                    try:
                        inner = json.loads(inner_str)
                        _walk(inner)
                    except json.JSONDecodeError:
                        pass
            except json.JSONDecodeError:
                pass
        else:
            _walk(top_val)

    return meta


def _extract_urls_from_search_html(
    html: str, channel: str, property_type: Optional[str] = None
) -> list[str]:
    """
    Extract deduplicated property listing URLs from a REA search results page.

    Actual REA URL formats (verified from live pages):
      sold channel: /sold/property-{type}-{state}-{suburb}-{numeric_id}
      buy  channel: /property-{type}-{state}-{suburb}-{numeric_id}

    property_type: if given, only keep URLs whose path type segment matches
    (e.g. "apartment" keeps apartment/unit, "house" keeps house/townhouse/villa).
    """
    base = "https://www.realestate.com.au"
    seen: set[str] = set()
    urls: list[str] = []

    if channel == "sold":
        patterns = [
            r'href="(/sold/property-[a-z]+-[a-z][^"?#\s]+-\d{8,})"',
            r'"(https://www\.realestate\.com\.au/sold/property-[^"?#\s]+-\d{8,})"',
        ]
    else:
        patterns = [
            r'href="(/property-[a-z]+-[a-z][^"?#\s]+-\d{8,})"',
            r'"(https://www\.realestate\.com\.au/property-[^"?#\s]+-\d{8,})"',
        ]

    # Resolve which REA type slugs match the subject property type
    _TYPE_GROUPS: dict[str, set[str]] = {
        "apartment": {"apartment", "unit", "studio"},
        "unit":      {"apartment", "unit", "studio"},
        "townhouse": {"townhouse", "villa", "terrace", "semidetached"},
        "house":     {"house", "acreage", "rural"},
        "villa":     {"townhouse", "villa", "terrace"},
        "land":      {"land", "acreage"},
    }
    pt_lower = (property_type or "").lower()
    allowed_slugs = _TYPE_GROUPS.get(pt_lower)

    for pat in patterns:
        for m in re.finditer(pat, html):
            u = m.group(1)
            if not u.startswith("http"):
                u = base + u
            if u in seen:
                continue
            # Filter by property type if requested
            if allowed_slugs:
                # URL path: /sold/property-{type_slug}-{state}-... or /property-{type_slug}-...
                slug_m = re.search(r'/property-([a-z]+)-[a-z]{2,3}-', u)
                if slug_m and slug_m.group(1) not in allowed_slugs:
                    continue
            seen.add(u)
            urls.append(u)

    return urls


def _search_page(
    scraper: REAScraper,
    search_url: str,
    channel: str,
    property_type: Optional[str] = None,
    beds_min: Optional[int] = None,
    beds_max: Optional[int] = None,
    baths_min: Optional[int] = None,
    baths_max: Optional[int] = None,
    cars_min: Optional[int] = None,
    cars_max: Optional[int] = None,
) -> tuple[list[str], int]:
    """
    Navigate to a REA search results page and return (filtered_urls, raw_count).

    filtered_urls: URLs matching the bed/bath/cars filter (or all if no filter).
    raw_count: total URLs found on the page before filtering — used to detect
               a genuinely empty page vs a page where all results were filtered out.

    beds/baths/cars: filter by extracting per-listing metadata from the embedded
    ArgonautExchange JSON. REA ignores these URL params server-side.
    """
    _429_delays = [15, 45]  # seconds to wait before each retry
    html = ""
    for attempt in range(len(_429_delays) + 1):
        page = scraper._context.new_page()
        try:
            if not scraper._warmup_done:
                try:
                    page.goto(
                        "https://www.realestate.com.au/",
                        wait_until="domcontentloaded",
                        timeout=scraper.timeout_ms,
                    )
                    scraper._wait_for_real_page(page)
                except Exception:
                    pass  # warmup failure is non-fatal
                scraper._warmup_done = True

            resp = None
            try:
                resp = page.goto(search_url, wait_until="domcontentloaded", timeout=scraper.timeout_ms)
            except Exception:
                pass  # HTTP error codes (403, 404) raise here — still try to read content
            scraper._wait_for_real_page(page)
            page.wait_for_timeout(random.randint(3000, 7000))
            html = page.content()
        finally:
            page.close()

        # Detect rate-limiting and retry with backoff
        is_429 = (resp is not None and resp.status == 429) or "HTTP ERROR 429" in html
        if is_429 and attempt < len(_429_delays):
            time.sleep(_429_delays[attempt])
            continue
        break

    urls = _extract_urls_from_search_html(html, channel, property_type)
    raw_count = len(urls)

    # Apply bed/bath/cars filter using metadata embedded in the search results JSON.
    # REA ignores these URL params server-side; this is the workaround.
    any_filter = beds_min is not None or beds_max is not None or baths_min is not None or baths_max is not None or cars_min is not None or cars_max is not None
    if any_filter and urls:
        meta = _extract_search_metadata(html)
        if meta:
            def _norm(u: str) -> str:
                return u.split("?")[0].rstrip("/").lower()

            filtered: list[str] = []
            for u in urls:
                m = meta.get(_norm(u))
                if m is None:
                    # No metadata for this URL — include it (safer than excluding)
                    filtered.append(u)
                    continue
                v = m.get("beds")
                if v is not None:
                    if beds_min is not None and v < beds_min: continue
                    if beds_max is not None and v > beds_max: continue
                v = m.get("baths")
                if v is not None:
                    if baths_min is not None and v < baths_min: continue
                    if baths_max is not None and v > baths_max: continue
                v = m.get("cars")
                if v is not None:
                    if cars_min is not None and v < cars_min: continue
                    if cars_max is not None and v > cars_max: continue
                filtered.append(u)
            urls = filtered

    return urls, raw_count


def search_rea(
    subject_info: dict,
    listing_type: str,
    force_suburb: bool = False,
    beds_min:  Optional[int]   = None,
    beds_max:  Optional[int]   = None,
    baths_min: Optional[int]   = None,
    baths_max: Optional[int]   = None,
    cars_min:  Optional[int]   = None,
    cars_max:  Optional[int]   = None,
    land_size_min:  Optional[float] = None,
    land_size_max:  Optional[float] = None,
    build_size_min: Optional[float] = None,
    build_size_max: Optional[float] = None,
    extra_suburbs:  Optional[list[str]] = None,
    distance_km:    Optional[float] = None,
) -> dict:
    """
    Search REA for comparable properties matching the subject property.

    listing_type: "sold" or "buy"
    force_suburb: skip building-level search and go straight to suburb search.
    beds/baths/cars_min/max: range filter for bedrooms/bathrooms/carspaces.
    land_size_min/max: land size range filter (m²).
    build_size_min/max: building size range filter (m²).
    extra_suburbs: additional suburb names to search alongside the subject suburb.
    distance_km: radius in km (stored; suburb-based search used unless only distance provided).

    Returns a dict:
      {
        "urls":                list[str],   # up to 10 URLs found
        "needs_fallback":      bool,
        "fallback_description": str,
      }

    Strategy for Apartment (unless force_suburb=True or extra_suburbs given):
      1. Same building + beds/baths/cars
      2. Same building + beds/baths (relax cars)
      → if < 3 results: return with needs_fallback=True (caller decides)
    Strategy for House & Land or Land Only (or after user confirms fallback):
      Search each suburb in all_suburbs; combine results.
    """
    channel = "sold" if listing_type == "sold" else "buy"

    property_type = (subject_info.get("property_type") or "").lower()
    # Frontend filter panel sends explicit values (or None to skip that filter).
    # None means "no filter" — do not fall back to subject_info values.
    bed_min   = beds_min
    bed_max   = beds_max
    bath_min  = baths_min
    bath_max  = baths_max
    car_min   = cars_min
    car_max   = cars_max
    land_min  = land_size_min
    land_max  = land_size_max
    build_min = build_size_min
    build_max = build_size_max

    suburb    = (subject_info.get("suburb")   or "").strip()
    state     = (subject_info.get("state")    or "").strip()
    postcode  = (subject_info.get("postcode") or "").strip()
    address   = (subject_info.get("address")  or "").strip()

    # Derive suburb/state/postcode from the address string when not explicit
    if not suburb or not postcode:
        parsed   = _parse_au_address(address)
        suburb   = suburb   or parsed.get("suburb",   "")
        state    = state    or parsed.get("state",    "NSW")
        postcode = postcode or parsed.get("postcode", "")

    if not suburb:
        raise ScraperError(
            f"Cannot determine suburb from subject info — "
            f"check that the subject address is filled in: {address!r}"
        )

    state = state or "NSW"
    fallback_desc = f"suburb-wide search in {suburb} {state} {postcode}"

    # Build list of suburbs to search: subject suburb + any extra suburbs
    all_suburbs = [{"suburb": suburb, "state": state, "postcode": postcode}]
    for raw in (extra_suburbs or []):
        raw = raw.strip()
        if not raw:
            continue
        has_postcode = bool(re.search(r'\d{4}', raw))
        if has_postcode:
            # "Suburb STATE 2155" format — parse fully
            parsed_extra = _parse_au_address(raw)
            extra_suburb = parsed_extra.get("suburb", raw) if parsed_extra else raw
            extra_state  = parsed_extra.get("state",  state) if parsed_extra else state
            extra_post   = parsed_extra.get("postcode", "") if parsed_extra else ""
        else:
            # Plain suburb name — keep same state, no postcode (avoid wrong postcode)
            parsed_extra = _parse_au_address(raw + f" {state} 0000")
            extra_suburb = parsed_extra.get("suburb", raw) if parsed_extra else raw
            extra_state  = state
            extra_post   = ""
        all_suburbs.append({"suburb": extra_suburb, "state": extra_state, "postcode": extra_post})

    is_land = property_type == "land"

    # Treat as strata type if property_type says so or address has a unit prefix
    is_unit = property_type in ("unit", "apartment", "townhouse")
    if not is_unit and not is_land and re.match(r"^\d+[A-Za-z]?/", address):
        is_unit = True

    building_address = _extract_building_address(address) if is_unit else None

    def _search_suburbs(scraper, bldg=None, c_min=car_min, c_max=car_max, apply_bed_filter=True):
        """Search all suburbs and return deduplicated URLs.

        apply_bed_filter=False: skip metadata-based bed/bath/cars filtering (fallback mode).
        """
        seen: set[str] = set()
        results: list[str] = []
        bdn_min = None if is_land else bed_min
        bdn_max = None if is_land else bed_max
        bth_min = None if is_land else bath_min
        bth_max = None if is_land else bath_max
        crs_min = None if is_land else c_min
        crs_max = None if is_land else c_max
        # Land size: apartments never filter by land size
        lmin_arg = land_min  if not is_unit else None
        lmax_arg = land_max  if not is_unit else None
        # Build size: only house & land (not apartments, not land-only)
        bmin_arg = build_min if (not is_unit and not is_land) else None
        bmax_arg = build_max if (not is_unit and not is_land) else None
        # When a bed/bath filter is active, paginate up to 2 pages per suburb
        # so we can collect enough matching results from REA's unfiltered pages.
        has_filter = bdn_min is not None or bdn_max is not None or bth_min is not None or bth_max is not None
        max_pages = 2 if (apply_bed_filter and has_filter) else 1
        # Metadata filtering args — only passed when apply_bed_filter is True
        f_bdn_min = bdn_min if apply_bed_filter else None
        f_bdn_max = bdn_max if apply_bed_filter else None
        f_bth_min = bth_min if apply_bed_filter else None
        f_bth_max = bth_max if apply_bed_filter else None
        f_crs_min = crs_min if apply_bed_filter else None
        f_crs_max = crs_max if apply_bed_filter else None
        for sub_info in all_suburbs:
            base_url = _build_search_url(
                channel,
                sub_info["suburb"], sub_info["state"], sub_info["postcode"],
                beds_min=bdn_min, beds_max=bdn_max,
                baths_min=bth_min, baths_max=bth_max,
                cars_min=crs_min, cars_max=crs_max,
                building_address=bldg,
                land_min=lmin_arg, land_max=lmax_arg,
                build_min=bmin_arg, build_max=bmax_arg,
            )
            for page_num in range(1, max_pages + 1):
                paged_url = base_url.replace("/list-1", f"/list-{page_num}")
                page_urls, raw_count = _search_page(
                    scraper, paged_url, channel, property_type,
                    beds_min=f_bdn_min, beds_max=f_bdn_max,
                    baths_min=f_bth_min, baths_max=f_bth_max,
                    cars_min=f_crs_min, cars_max=f_crs_max,
                )
                for u in page_urls:
                    if u not in seen:
                        seen.add(u)
                        results.append(u)
                # Stop when enough results, or page was genuinely empty before filtering.
                # A page where all results were filtered out (raw_count > 0 but page_urls empty)
                # is NOT empty — the next page may have matching results.
                if len(results) >= 10 or raw_count == 0:
                    break
                # Brief pause between paginated pages of the same suburb
                time.sleep(random.uniform(2, 5))
            # Longer pause between different suburbs to avoid rate limiting
            time.sleep(random.uniform(4, 9))
        return results

    with REAScraper() as s:
        # For apartments with only the subject suburb and no forced suburb, try building first
        if is_unit and building_address and not force_suburb and len(all_suburbs) == 1:
            # Level 1: same building, full criteria
            urls = _search_suburbs(s, bldg=building_address)
            if len(urls) >= 3:
                return {"urls": urls[:10], "needs_fallback": False, "fallback_description": ""}

            # Level 2: same building, relax carspaces
            urls = _search_suburbs(s, bldg=building_address, c_min=None, c_max=None)
            if len(urls) >= 3:
                return {"urls": urls[:10], "needs_fallback": False, "fallback_description": ""}

            # Not enough results — ask the user before going suburb-wide
            return {
                "urls": urls[:10],
                "needs_fallback": True,
                "fallback_description": fallback_desc,
            }

        # House & Land, Land Only, forced suburb, or multi-suburb apartment search
        urls = _search_suburbs(s)

        # If bed/bath filter returned too few results, retry unfiltered so the
        # user at least gets some candidates (with a notice).
        if len(urls) < 3 and (bed_min is not None or bed_max is not None or bath_min is not None or bath_max is not None):
            urls_unfiltered = _search_suburbs(s, apply_bed_filter=False)
            if urls_unfiltered:
                beds_label  = f"{bed_min}" + (f"-{bed_max}" if bed_max != bed_min else "") + "b" if bed_min is not None else ""
                baths_label = f"{bath_min}" + (f"-{bath_max}" if bath_max != bath_min else "") + "b" if bath_min is not None else ""
                filter_label = beds_label + baths_label
                return {
                    "urls": urls_unfiltered[:10],
                    "needs_fallback": True,
                    "fallback_description": (
                        f"fewer than 3 {filter_label} matches found in {suburb} — "
                        f"showing all {property_type}s (manual bed check required)"
                    ),
                }

    return {"urls": urls[:10], "needs_fallback": False, "fallback_description": ""}
