"""Misc utilities: logo extraction, image download, distance calc."""
from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

import requests
from PIL import Image


# -------- Logo extraction from original PDF ----------------------------

def extract_logo_from_pdf(pdf_path: str, output_dir: str) -> str | None:
    """
    Pull the Vision Property logo out of page 1 of the original PDF.
    Returns path to a PNG, or None if extraction fails.

    We rely on pypdf to walk the page's image XObjects and pick the
    first one that looks logo-shaped (wider than tall, small-ish).
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        page = reader.pages[0]
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        for i, image_obj in enumerate(page.images):
            try:
                name = image_obj.name or f"img_{i}"
                data = image_obj.data
                # Open with PIL to inspect dimensions
                img = Image.open(io_bytes(data))
                w, h = img.size
                # Logo heuristic: wider than tall, not too large
                if w > h and w < 800 and h < 400 and w / max(h, 1) > 1.5:
                    out_path = out_dir / f"logo_{i}.png"
                    img.save(out_path, "PNG")
                    return str(out_path)
            except Exception:
                continue
    except Exception:
        return None
    return None


def io_bytes(data: bytes):
    import io
    return io.BytesIO(data)


# -------- Subject property extraction from PDF -------------------------

def extract_subject_from_pdf(pdf_path: str) -> dict:
    """
    Extract subject property info from a Cotality CMA PDF.

    Returns dict with keys: address, bedrooms, bathrooms, carspaces,
    land_size_m2, build_size_m2. Missing values are None.
    """
    import re
    result = {
        "address": None,
        "bedrooms": None,
        "bathrooms": None,
        "carspaces": None,
        "land_size_m2": None,
        "build_size_m2": None,
    }
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)

        # Page 1: extract address (line after "Comparative Market Analysis")
        if len(reader.pages) >= 1:
            text1 = reader.pages[0].extract_text() or ""
            lines = [l.strip() for l in text1.split("\n") if l.strip()]
            for i, line in enumerate(lines):
                if "Comparative Market Analysis" in line:
                    if i + 1 < len(lines):
                        result["address"] = lines[i + 1]
                    break

        # Page 3 ("Your Property"): extract bed/bath/car/land/build
        if len(reader.pages) >= 3:
            text3 = reader.pages[2].extract_text() or ""
            # Limit search to before "Your Property History"
            text3_body = re.split(r"Your Property History", text3, maxsplit=1)[0]

            # Cotality icon-stripped format (empirically observed):
            #   {bath}\n {bed}\n \n {land}m2\n {build}m2{cars}
            # The bed count is the LARGER of the first two numbers (heuristic).
            m = re.search(
                r"(\d{1,2})\s+"
                r"(\d{1,2})\s+"
                r"([\d,]+)\s*m[²2]"
                r"(?:\s+([\d,]+)\s*m[²2](\d{1,2})?)?",
                text3_body,
                re.DOTALL,
            )
            if m:
                num_a, num_b = int(m.group(1)), int(m.group(2))
                result["bedrooms"]  = max(num_a, num_b)
                result["bathrooms"] = min(num_a, num_b)
                try:
                    result["land_size_m2"] = float(m.group(3).replace(",", ""))
                except ValueError:
                    pass
                if m.group(4):
                    try:
                        result["build_size_m2"] = float(m.group(4).replace(",", ""))
                    except ValueError:
                        pass
                if m.group(5):
                    result["carspaces"] = int(m.group(5))
            else:
                m_land = re.search(r"(\d[\d,]*)\s*m[²2](?!\d)", text3_body)
                if m_land:
                    try:
                        result["land_size_m2"] = float(m_land.group(1).replace(",", ""))
                    except ValueError:
                        pass

            # Explicit labels, if Cotality prints them:
            m_build = re.search(r"Build(?:ing)?\s*(?:Size|Area)?\s*[:\s]\s*(\d[\d,]*)\s*m[²2]", text3, re.IGNORECASE)
            if m_build:
                try:
                    result["build_size_m2"] = float(m_build.group(1).replace(",", ""))
                except ValueError:
                    pass
            # Land label fallback
            if result["land_size_m2"] is None:
                m_l = re.search(r"Land\s*(?:Size|Area)?\s*[:\s]\s*(\d[\d,]*)\s*m[²2]", text3, re.IGNORECASE)
                if m_l:
                    try:
                        result["land_size_m2"] = float(m_l.group(1).replace(",", ""))
                    except ValueError:
                        pass

    except Exception:
        pass
    return result


# -------- Image extraction from PDF ------------------------------------

def _extract_page_images(reader, page_index: int) -> list:
    """
    Return a list of (name, PIL.Image, raw_bytes) for every image on page
    `page_index`, in source order. Wraps pypdf's `page.images`.
    Used for cover-page extraction where order doesn't matter.
    """
    out = []
    try:
        page = reader.pages[page_index]
    except Exception:
        return out
    for i, image_obj in enumerate(page.images):
        try:
            name = image_obj.name or f"img_{i}"
            data = image_obj.data
            img = Image.open(io_bytes(data))
            img.load()  # force decode so we can access .size later
            out.append((name, img, data))
        except Exception:
            continue
    return out


def _extract_page_images_positioned(pdf_path: str, page_index: int) -> list:
    """
    Return (name, PIL.Image, raw_bytes) tuples sorted by visual top-to-bottom
    order using PyMuPDF's position data. pypdf's page.images returns images in
    XObject-reference order (not visual order), so comparable card photos get
    assigned to the wrong cards. This function fixes that.

    Handles the case where multiple comparable cards share the same image XObject
    (same xref): each placement on the page generates a separate entry so that
    every card gets its own thumbnail.
    """
    try:
        import fitz
        import io

        doc = fitz.open(pdf_path)
        try:
            page = doc[page_index]
        except Exception:
            doc.close()
            return []

        # info_list has one entry per *placement* on the page (not per unique image),
        # so the same xref appears multiple times if reused across cards.
        info_list = page.get_image_info(xrefs=True)

        # Pre-extract image bytes keyed by xref to avoid redundant decoding.
        xref_cache: dict[int, bytes] = {}

        # Build positioned list: one entry per placement, sorted top→bottom.
        seen_placements: set[tuple[int, float]] = set()  # (xref, y_top rounded)
        positioned: list[tuple[float, str, bytes]] = []

        for info in info_list:
            xref = info.get("xref", 0)
            bbox = info.get("bbox", [])
            if not xref or not bbox:
                continue
            y_top = round(bbox[1], 2)
            key = (xref, y_top)
            if key in seen_placements:
                continue  # exact same placement seen twice (e.g. via form XObjects)
            seen_placements.add(key)

            if xref not in xref_cache:
                try:
                    img_dict = doc.extract_image(xref)
                    xref_cache[xref] = img_dict["image"]
                except Exception:
                    continue

            data = xref_cache[xref]
            name = f"img_{xref}"
            positioned.append((y_top, name, data))

        doc.close()

        # Sort top-to-bottom (ascending y in fitz/screen coords)
        positioned.sort(key=lambda t: t[0])

        out = []
        for _, name, data in positioned:
            try:
                img = Image.open(io.BytesIO(data))
                img.load()
                out.append((name, img, data))
            except Exception:
                continue
        return out

    except ImportError:
        return []


def extract_subject_hero_image_from_pdf(pdf_path: str, output_dir: str) -> str | None:
    """
    Pull the largest photo-like image from page 1 (cover), skipping the logo.
    Returns a path to a saved JPG / PNG, or None if nothing suitable found.
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        images = _extract_page_images(reader, 0)
        if not images:
            return None

        candidates = []
        for name, img, data in images:
            w, h = img.size
            if w < 400 or h < 300:
                continue  # likely logo / icon / small asset
            # Reject very wide banners (logos): ratio > 3:1
            if w / max(h, 1) > 3:
                continue
            candidates.append((w * h, name, img, data))

        if not candidates:
            return None

        candidates.sort(reverse=True)
        _, name, img, _ = candidates[0]
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = (img.format or "jpeg").lower()
        if ext == "jpeg":
            ext = "jpg"
        out_path = out_dir / f"subject_hero.{ext}"
        save_kwargs = {"quality": 92} if ext in ("jpg", "jpeg") else {}
        try:
            img.save(out_path, **save_kwargs)
        except Exception:
            # Some PDF-embedded images need RGB conversion before saving
            img.convert("RGB").save(out_path, **save_kwargs)
        return str(out_path)
    except Exception:
        return None


def _save_pil_image(img, out_dir: Path, stem: str) -> str | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = (img.format or "jpeg").lower()
    if ext == "jpeg":
        ext = "jpg"
    out_path = out_dir / f"{stem}.{ext}"
    save_kwargs = {"quality": 90} if ext in ("jpg", "jpeg") else {}
    try:
        img.save(out_path, **save_kwargs)
    except Exception:
        try:
            img.convert("RGB").save(out_path, **save_kwargs)
        except Exception:
            return None
    return str(out_path)


# -------- Image download -----------------------------------------------

def download_image(url: str, dest_dir: str, filename_hint: str = "img") -> str | None:
    """
    Download an image URL to dest_dir. Returns local path or None on failure.
    """
    if not url:
        return None
    try:
        r = requests.get(
            url, timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        )
        if r.status_code != 200 or len(r.content) < 1000:
            return None
        # Ensure it's an image; determine extension
        try:
            img = Image.open(io_bytes(r.content))
            ext = img.format.lower() if img.format else "jpg"
            if ext == "jpeg":
                ext = "jpg"
        except Exception:
            ext = "jpg"
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        safe_hint = "".join(ch for ch in filename_hint if ch.isalnum() or ch in "-_")[:40]
        out_path = os.path.join(dest_dir, f"{safe_hint}.{ext}")
        with open(out_path, "wb") as f:
            f.write(r.content)
        return out_path
    except Exception:
        return None


# -------- Geographic distance ------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in km."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam/2)**2
    return 2 * R * math.asin(math.sqrt(a))


# -------- Geocoding ----------------------------------------------------

def geocode_address(address: str, api_key: str) -> tuple[float, float]:
    """
    Resolve a street address to (lat, lng) via Google Maps Geocoding API.
    Raises ValueError if the address cannot be geocoded.
    """
    r = requests.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": address, "key": api_key},
        timeout=10,
    )
    data = r.json()
    status = data.get("status")
    if status != "OK":
        msg = data.get("error_message") or status
        raise ValueError(f"Geocoding failed: {msg}")
    loc = data["results"][0]["geometry"]["location"]
    return loc["lat"], loc["lng"]


# -------- Comparable property extraction from PDF --------------------

def extract_comparables_from_pdf(pdf_path: str, image_output_dir: str | None = None) -> dict:
    """
    Parse Cotality comparable pages and return pre-populated row data.

    Returns {"sales": [...], "listings": [...]} where each item is a dict
    matching the PropertyData shape expected by the frontend / GenerateRequest.

    If `image_output_dir` is given, each comparable's card thumbnail is
    extracted from the PDF and saved; the local path is stored in
    ``thumbnail_path`` on each dict (best-effort: it matches images to
    blocks by appearance order within their page).
    """
    import re

    result: dict = {"sales": [], "listings": []}

    MONTHS = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }

    def _null(s: str) -> bool:
        return not s or s.strip() in ("-", "--", "- -", "")

    def _parse_date(s: str) -> str:
        m = re.match(r"(\d{1,2})-([A-Za-z]{3})-(\d{2,4})$", (s or "").strip())
        if m:
            d, mon_str, yr = int(m.group(1)), m.group(2).capitalize(), m.group(3)
            mon = MONTHS.get(mon_str)
            if mon:
                yr_i = int(yr) + (2000 if int(yr) < 100 else 0)
                return f"{yr_i:04d}-{mon:02d}-{d:02d}"
        return s.strip() if s else ""

    def _parse_land(text: str):
        m = re.search(r"([\d,]+)\s*m[²2]", text)
        if m:
            v = float(m.group(1).replace(",", ""))
            return v, f"{int(v)}m²"
        m = re.search(r"([\d.]+)\s*ha\b", text)
        if m:
            v = float(m.group(1)) * 10_000
            return v, f"{int(v)}m²"
        return None, ""

    def _parse_block(text: str, kind: str) -> dict | None:
        addr_m = re.search(
            r"([A-Z0-9][A-Z0-9 /\-]+(?:NSW|VIC|QLD|SA|WA|TAS|ACT|NT)\s+\d{4})",
            text,
        )
        if not addr_m:
            return None
        address = addr_m.group(1).strip()

        # Price
        price_display = ""
        if kind == "sales":
            # Prefer "Sold $X" to avoid capturing "First Listing $Y" which appears earlier
            sp_m = re.search(r"\bSold\b[^\n$]*(\$[\d,]+(?:\.\d+)?)", text)
            if sp_m:
                price_display = sp_m.group(1)
            else:
                pm = re.search(r"\$[\d,.]+(?:\s*-\s*\$[\d,.]+)?", text)
                price_display = pm.group(0).strip() if pm else ""
        else:
            lp = re.search(r"Listing Price\s+(.+?)(?:\n|$)", text)
            if lp:
                raw = lp.group(1).strip()
                if re.search(r"\$", raw):
                    # Capture full price string including ranges like "$1,050,000 - $1,100,000"
                    price_display = re.sub(r"\s+", " ", raw).strip()
                elif re.search(r"contact agent", raw, re.IGNORECASE):
                    price_display = "Contact Agent"
                elif re.search(r"price on application", raw, re.IGNORECASE):
                    price_display = "Price on Application"

        # Cotality icon-stripped format — houses:
        #   {land}m² {build}m² {beds} {baths} {cars}
        #   {land}m² -          {beds} {baths} {cars}   (build unknown)
        # Units/apartments — land may be absent or a dash:
        #   - {build}m²         {beds} {baths} {cars}
        #   {build}m²           {beds} {baths} {cars}   (no land field at all)
        # Search only before "Year Built" to avoid matching year/DOM numbers.
        text_pre_yb = re.split(r"Year Built", text, maxsplit=1, flags=re.IGNORECASE)[0]
        bedrooms = bathrooms = carspaces = None
        land_m2 = land_disp = None
        build_size_m2 = None

        # Primary: two size fields then bed/bath/car
        # Land may be: m², ha (large rural lots), or dash (units/apartments)
        # Build may be: m² or dash
        feat_m = re.search(
            r"(?:([\d,]+)\s*m[²2]|([\d.]+)\s*ha|-)\s*"  # land: m², ha, or dash
            r"(?:([\d,]+)\s*m[²2]|-)\s*"                 # build: m² or dash
            r"(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})",        # beds, baths, cars
            text_pre_yb,
            re.DOTALL,
        )
        if feat_m:
            land_m2_s, land_ha_s, build_s = feat_m.group(1), feat_m.group(2), feat_m.group(3)
            if land_m2_s:
                land_m2   = float(land_m2_s.replace(",", ""))
                land_disp = f"{int(land_m2)}m²"
            elif land_ha_s:
                land_m2   = float(land_ha_s) * 10_000
                land_disp = f"{int(land_m2)}m²"
            if build_s:
                build_size_m2 = float(build_s.replace(",", ""))
            bedrooms  = int(feat_m.group(4))
            bathrooms = int(feat_m.group(5))
            carspaces = int(feat_m.group(6))
        else:
            # Unit fallback: single m² (floor/build area) directly before bed/bath/car
            unit_m = re.search(
                r"([\d,]+)\s*m[²2]\s+"
                r"(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})",
                text_pre_yb, re.DOTALL,
            )
            if unit_m:
                build_size_m2 = float(unit_m.group(1).replace(",", ""))
                bedrooms  = int(unit_m.group(2))
                bathrooms = int(unit_m.group(3))
                carspaces = int(unit_m.group(4))
            else:
                # Last resort: text-label format (manually entered comparables)
                def _feat(pattern: str) -> int | None:
                    m = re.search(pattern, text, re.IGNORECASE)
                    if m:
                        val = next((g for g in m.groups() if g is not None), None)
                        try:
                            return int(val)
                        except (TypeError, ValueError):
                            pass
                    return None
                bedrooms  = _feat(r"(\d+)\s*Bed(?:room)?s?\b|\bBed(?:room)?s?\s+(\d+)")
                bathrooms = _feat(r"(\d+)\s*Bath(?:room)?s?\b|\bBath(?:room)?s?\s+(\d+)")
                carspaces = _feat(r"(\d+)\s*(?:Car(?:space|port)?s?|Garage|Parking)\b|\b(?:Car(?:space|port)?s?|Garage|Parking)\s+(\d+)")

        # Land fallback: only when primary didn't capture land, and avoid
        # re-grabbing the build area value as land.
        if land_m2 is None:
            raw_land, raw_disp = _parse_land(text)
            if raw_land is not None and (build_size_m2 is None or abs(raw_land - build_size_m2) > 1):
                land_m2  = raw_land
                land_disp = raw_disp

        dom_m = re.search(r"DOM\s+(\d+)", text)
        dom = dom_m.group(1) if dom_m else ""

        yb_m = re.search(r"Year Built\s+(\d{4})", text)
        year_built = yb_m.group(1) if yb_m else ""

        if kind == "sales":
            sd = re.search(r"Sold Date\s+(\S+)", text)
            sold_date = _parse_date(sd.group(1)) if sd and not _null(sd.group(1)) else ""
            fl = re.search(r"First Listing\s+(.+?)(?:\n|$)", text)
            ll = re.search(r"Last Listing\s+(.+?)(?:\n|$)", text)
            first_listing = fl.group(1).strip() if fl and not _null(fl.group(1)) else ""
            last_listing  = ll.group(1).strip() if ll and not _null(ll.group(1)) else ""
            listing_date  = ""
        else:
            ld = re.search(r"Listing Date\s+(\S+)", text)
            listing_date  = _parse_date(ld.group(1)) if ld and not _null(ld.group(1)) else ""
            sold_date = first_listing = last_listing = ""

        return {
            "url": "",
            "address": address,
            "price_display": price_display,
            "status": "Sold" if kind == "sales" else "For Sale",
            "land_size_m2": land_m2,
            "land_size_display": land_disp or "",
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "carspaces": carspaces,
            "year_built": year_built,
            "days_on_market": dom,
            "sold_date": sold_date,
            "listing_date": listing_date,
            "first_listing": first_listing,
            "last_listing": last_listing,
            "from_rea": False,
            "latitude": None,
            "longitude": None,
            "thumbnail_path": "",
            "thumbnail_url": "",
            "headline": "",
            "property_type": "house",
            "build_size_m2": build_size_m2,
            "build_size_display": f"{int(build_size_m2)}m²" if build_size_m2 else "",
            "floor_size_m2": None,
            "floor_size_display": "",
        }

    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)

        out_dir = Path(image_output_dir) if image_output_dir else None

        for page_idx, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception:
                continue

            tl = text.lower()
            if "comparables map" in tl:
                continue
            if "comparable sales" in tl:
                kind = "sales"
            elif "comparable listings" in tl:
                kind = "listings"
            else:
                continue

            # Split on lines that start a new property entry:
            # index (1-2 digits) + optional house/unit number + street name (2+ uppercase letters)
            # Allow hyphens in house numbers (e.g. "316/42-44 ARMBRUSTER").
            blocks = re.split(r"\n\s*(?=\d{1,2}\s+(?:[\d][/\d\-]*\s+)?[A-Z]{2})", text)

            page_props: list[dict] = []
            for block in blocks:
                block = block.strip()
                if not block:
                    continue
                # blocks[0] starts with the page header ("Comparable Sales/Listings").
                # The FIRST property may be merged into this block — strip the header
                # line and still attempt to parse whatever property text follows.
                if block.lower().startswith("comparable"):
                    block = re.sub(r"^[^\n]+\n?", "", block).strip()
                    if not block:
                        continue
                clean = re.sub(r"^\s*\d{1,2}\s+", "", block, count=1)
                prop = _parse_block(clean, kind)
                if prop:
                    page_props.append(prop)

            if out_dir is not None and page_props:
                # Extract page images sorted by visual position (top→bottom)
                # so that photo[0] matches the top card, photo[1] the next, etc.
                page_images = _extract_page_images_positioned(pdf_path, page_idx)
                photo_images = [
                    (name, img, data)
                    for (name, img, data) in page_images
                    if img.size[0] >= 100 and img.size[1] >= 60  # skip icons
                    and img.size[0] / max(img.size[1], 1) < 3    # skip wide banners/logos
                ]
                for i, prop in enumerate(page_props):
                    if i < len(photo_images):
                        _, pimg, _ = photo_images[i]
                        saved = _save_pil_image(
                            pimg, out_dir, f"{kind}_p{page_idx}_{i}"
                        )
                        if saved:
                            prop["thumbnail_path"] = saved

            result[kind].extend(page_props)

    except Exception:
        pass

    return result


# -------- Agent info extraction from PDF ------------------------------

def extract_agent_from_pdf(pdf_path: str) -> dict:
    """
    Extract agent name, agency name, email, and report date from page 1
    of a Cotality CMA PDF.

    Cotality page-1 layout (after "Prepared on <date>"):
        <agent name>
        <AGENCY NAME>
        <email>
    """
    result = {
        "agent_name": "",
        "agency_name": "",
        "agent_email": "",
        "report_date": "",
    }
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        if not reader.pages:
            return result
        text = reader.pages[0].extract_text() or ""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for i, line in enumerate(lines):
            if line.startswith("Prepared on "):
                result["report_date"] = line[len("Prepared on "):].strip()
                remaining = [l for l in lines[i + 1:] if l]
                if len(remaining) >= 1:
                    result["agent_name"] = remaining[0]
                if len(remaining) >= 2:
                    result["agency_name"] = remaining[1]
                for l in remaining[2:]:
                    if "@" in l:
                        result["agent_email"] = l
                        break
                break
    except Exception:
        pass
    return result


# -------- Land size formatting -----------------------------------------

def format_land_size(m2: float | None) -> str:
    if m2 is None:
        return "-"
    return f"{int(m2)}m\u00b2"
