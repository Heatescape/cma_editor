"""
FastAPI backend for CMA Editor.

Endpoints:
    POST /api/session          - start a new session, upload original PDF + hero image
    POST /api/scrape           - scrape a REA URL (returns preview data)
    POST /api/generate         - generate final PDF from all inputs
    GET  /api/download/{id}    - download generated PDF
    GET  /                     - serve frontend

Run with:
    uvicorn backend.app:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import json
import queue
import threading

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .scrapers.rea import REAScraper, ScraperError, scrape_one, search_rea
from .generators.models import SubjectProperty, ComparableProperty, CMAInputs
from .generators.builder import build_final_pdf
from .utils.helpers import (
    extract_logo_from_pdf, extract_subject_from_pdf, extract_agent_from_pdf,
    extract_comparables_from_pdf, extract_subject_hero_image_from_pdf,
    download_image, haversine_km, format_land_size, geocode_address,
)


# --- Storage paths -----------------------------------------------------

BASE_DIR = Path(__file__).parent.parent
WORK_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
FRONTEND_DIR = BASE_DIR / "frontend"
WORK_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


# --- App setup ---------------------------------------------------------

app = FastAPI(title="CMA Editor")

# Heartbeat: shut down the server 30 s after the browser tab is closed.
_last_heartbeat: float = 0.0
_HEARTBEAT_TIMEOUT = 30  # seconds before shutdown after last heartbeat


@app.on_event("startup")
async def _start_heartbeat_watcher():
    global _last_heartbeat
    _last_heartbeat = time.time()
    asyncio.create_task(_heartbeat_watcher())


async def _heartbeat_watcher():
    await asyncio.sleep(90)  # grace period for browser to open and page to load
    while True:
        await asyncio.sleep(10)
        if time.time() - _last_heartbeat > _HEARTBEAT_TIMEOUT:
            os._exit(0)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Pydantic models ---------------------------------------------------

class ScrapeRequest(BaseModel):
    url: str


class GeocodeRequest(BaseModel):
    address: str
    api_key: str


class GeocodeBatchRequest(BaseModel):
    addresses: list[str]
    api_key: str


class PropertyData(BaseModel):
    """Generic comparable input from the UI."""
    index: int
    url: str = ""
    address: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    carspaces: Optional[int] = None
    land_size_m2: Optional[float] = None
    land_size_display: str = ""
    price_display: str = ""
    status: str = "Sold"
    sold_date: str = ""
    listing_date: str = ""
    first_listing: str = ""
    last_listing: str = ""
    days_on_market: str = ""
    year_built: str = ""
    thumbnail_url: str = ""    # URL (from scraping) - backend will download
    thumbnail_path: str = ""   # already-downloaded local path (from scrape response)
    headline: str = ""
    from_rea: bool = True
    property_type: str = "house"
    build_size_m2: Optional[float] = None
    build_size_display: str = ""
    floor_size_m2: Optional[float] = None
    floor_size_display: str = ""
    show_beds: bool = True
    show_baths: bool = True
    show_cars: bool = True
    show_land: bool = True
    show_build: bool = True


class SubjectData(BaseModel):
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
    hero_image_path: str = ""   # local path saved from /api/upload-hero
    property_type: str = "house"
    build_size_m2: Optional[float] = None
    build_size_display: str = ""
    floor_size_m2: Optional[float] = None
    floor_size_display: str = ""


class AutoFindRequest(BaseModel):
    session_id: str
    listing_type: str            # "sold" or "buy"
    property_type: Optional[str] = None  # frontend-selected type overrides PDF extraction
    force_suburb: bool = False   # skip building search, go straight to suburb
    beds_min:  Optional[int] = None
    beds_max:  Optional[int] = None
    baths_min: Optional[int] = None
    baths_max: Optional[int] = None
    cars_min:  Optional[int] = None
    cars_max:  Optional[int] = None
    land_size_min:  Optional[float] = None
    land_size_max:  Optional[float] = None
    build_size_min: Optional[float] = None
    build_size_max: Optional[float] = None
    extra_suburbs:  list[str]       = Field(default_factory=list)
    distance_km:    Optional[float] = None


class GenerateRequest(BaseModel):
    session_id: str
    subject: SubjectData
    sales: list[PropertyData] = Field(default_factory=list)
    listings: list[PropertyData] = Field(default_factory=list)
    google_maps_api_key: str
    keep_original_comparables: bool = False
    agent_name: str = ""
    agency_name: str = ""
    agent_email: str = ""
    report_date: str = ""
    marker_size: str = "medium"
    include_overview_map: bool = True
    include_sales_map: bool = True
    include_listings_map: bool = True


# --- Session helpers ---------------------------------------------------

def session_dir(session_id: str) -> Path:
    """Return the per-session working directory, creating it if needed."""
    d = WORK_DIR / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- Endpoints ---------------------------------------------------------

@app.post("/api/geocode")
def geocode(req: GeocodeRequest):
    """Resolve an address to lat/lng using Google Maps Geocoding API."""
    try:
        lat, lng = geocode_address(req.address, req.api_key)
        return {"lat": lat, "lng": lng}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Geocoding error: {e}")


@app.post("/api/geocode-batch")
def geocode_batch(req: GeocodeBatchRequest):
    """Geocode multiple addresses in one call. Returns a result per address."""
    results = []
    for addr in req.addresses:
        try:
            lat, lng = geocode_address(addr, req.api_key)
            results.append({"address": addr, "lat": lat, "lng": lng})
        except Exception as e:
            results.append({"address": addr, "error": str(e)})
    return {"results": results}


@app.post("/api/session")
async def create_session(
    original_pdf: UploadFile = File(...),
    hero_image: Optional[UploadFile] = File(None),
):
    """
    Start a new editing session. Uploads:
      - original CMA PDF (required)
      - new hero image for the cover (optional; can be uploaded later)
    Returns session_id + extracted logo path.
    """
    session_id = uuid.uuid4().hex
    sdir = session_dir(session_id)

    # Save original PDF
    pdf_path = sdir / "original.pdf"
    with open(pdf_path, "wb") as f:
        shutil.copyfileobj(original_pdf.file, f)

    # Save hero image if provided
    hero_path = ""
    if hero_image is not None:
        ext = Path(hero_image.filename or "").suffix or ".jpg"
        hero_path = str(sdir / f"hero{ext}")
        with open(hero_path, "wb") as f:
            shutil.copyfileobj(hero_image.file, f)

    # Extract logo
    logo_path = extract_logo_from_pdf(str(pdf_path), str(sdir / "logo"))

    # Extract subject property info, agent details, and comparables from the PDF
    subject_info     = extract_subject_from_pdf(str(pdf_path))
    # Persist for /api/auto-find-comparables
    with open(sdir / "subject_info.json", "w", encoding="utf-8") as _f:
        json.dump(subject_info, _f)
    agent_info       = extract_agent_from_pdf(str(pdf_path))
    comparables_info = extract_comparables_from_pdf(
        str(pdf_path),
        image_output_dir=str(sdir / "comparable_thumbs"),
    )

    # Auto-extract subject hero image from page 1 of the original PDF
    # (user can still override via the hero_image file input)
    if not hero_path:
        extracted_hero = extract_subject_hero_image_from_pdf(
            str(pdf_path), str(sdir / "extracted_hero")
        )
        if extracted_hero:
            hero_path = extracted_hero

    subject_hero_token = ""
    if hero_path:
        subject_hero_token = uuid.uuid4().hex
        _thumb_registry[subject_hero_token] = hero_path

    # Register preview tokens for each comparable's extracted thumbnail.
    for kind in ("sales", "listings"):
        for comp in comparables_info.get(kind, []):
            tp = comp.get("thumbnail_path") or ""
            if tp and os.path.exists(tp):
                tok = uuid.uuid4().hex
                _thumb_registry[tok] = tp
                comp["thumb_token"] = tok

    return {
        "session_id": session_id,
        "original_pdf_path": str(pdf_path),
        "hero_image_path": hero_path,
        "hero_image_token": subject_hero_token,
        "logo_path": logo_path or "",
        "subject_info": subject_info,
        "agent_info": agent_info,
        "comparables_info": comparables_info,
    }


@app.post("/api/upload-hero")
async def upload_hero(
    session_id: str = Form(...),
    hero_image: UploadFile = File(...),
):
    """Replace / add the hero image for an existing session."""
    sdir = session_dir(session_id)
    ext = Path(hero_image.filename or "").suffix or ".jpg"
    hero_path = sdir / f"hero{ext}"
    # clear any previous hero files
    for p in sdir.glob("hero.*"):
        try:
            p.unlink()
        except Exception:
            pass
    with open(hero_path, "wb") as f:
        shutil.copyfileobj(hero_image.file, f)
    return {"hero_image_path": str(hero_path)}


@app.post("/api/upload-comparable-thumb")
async def upload_comparable_thumb(
    session_id: str = Form(...),
    image: UploadFile = File(...),
):
    """Upload a replacement photo for a comparable row."""
    sdir = session_dir(session_id)
    uploads_dir = sdir / "comp_uploads"
    uploads_dir.mkdir(exist_ok=True)
    ext = Path(image.filename or "").suffix or ".jpg"
    dest = uploads_dir / f"comp_{uuid.uuid4().hex[:8]}{ext}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(image.file, f)
    tok = uuid.uuid4().hex
    _thumb_registry[tok] = str(dest)
    return {"thumb_token": tok, "thumbnail_path": str(dest)}


@app.post("/api/auto-find-comparables")
def auto_find_comparables(req: AutoFindRequest):
    """
    Search REA for comparable properties based on the session's subject info.
    Returns up to 10 listing URLs for the user to review before importing.
    """
    if req.listing_type not in ("sold", "buy"):
        raise HTTPException(400, "listing_type must be 'sold' or 'buy'")

    sdir = session_dir(req.session_id)
    info_path = sdir / "subject_info.json"
    if not info_path.exists():
        raise HTTPException(404, "Session not found or subject info unavailable — upload a PDF first")

    with open(info_path, encoding="utf-8") as f:
        subject_info = json.load(f)

    # Frontend-selected property type takes precedence over PDF extraction
    if req.property_type:
        subject_info["property_type"] = req.property_type

    try:
        result = search_rea(
            subject_info,
            req.listing_type,
            force_suburb=req.force_suburb,
            beds_min=req.beds_min,
            beds_max=req.beds_max,
            baths_min=req.baths_min,
            baths_max=req.baths_max,
            cars_min=req.cars_min,
            cars_max=req.cars_max,
            land_size_min=req.land_size_min,
            land_size_max=req.land_size_max,
            build_size_min=req.build_size_min,
            build_size_max=req.build_size_max,
            extra_suburbs=req.extra_suburbs,
            distance_km=req.distance_km,
        )
        return {"ok": True, **result}
    except ScraperError as e:
        raise HTTPException(502, f"REA search failed: {e}")
    except Exception as e:
        raise HTTPException(500, f"Unexpected search error: {e}")


@app.post("/api/scrape")
def scrape(req: ScrapeRequest):
    """Scrape a REA URL and return structured data + downloaded thumbnail."""
    if not req.url.startswith("http"):
        raise HTTPException(400, "Invalid URL")

    try:
        prop = scrape_one(req.url)
    except ScraperError as e:
        raise HTTPException(502, f"Scrape failed: {e}")
    except Exception as e:
        raise HTTPException(500, f"Unexpected scrape error: {e}")

    # Download up to 5 unique images.
    # Fetch more candidates than needed so MD5 dedup can still yield 5 after
    # removing any gallery slots that resolve to the same underlying photo.
    images: list[dict] = []
    urls_to_dl = [u for u in (prop.image_urls or []) if u][:20]
    if not urls_to_dl and prop.hero_image_url:
        urls_to_dl = [prop.hero_image_url]
    if urls_to_dl:
        tmpdir = tempfile.mkdtemp(prefix="rea_thumb_")
        hint = (prop.address or "thumb")[:20]
        def _dl(url: str) -> dict | None:
            path = download_image(url, tmpdir, filename_hint=hint) or ""
            if not path:
                return None
            tok = uuid.uuid4().hex
            _thumb_registry[tok] = path
            return {"thumb_token": tok, "thumbnail_path": path}
        with ThreadPoolExecutor(max_workers=10) as pool:
            images = [r for r in pool.map(_dl, urls_to_dl) if r]

        # Remove images with identical content, then keep at most 5 unique ones.
        seen_hashes: set[str] = set()
        deduped: list[dict] = []
        for img in images:
            try:
                with open(img["thumbnail_path"], "rb") as fh:
                    h = hashlib.md5(fh.read()).hexdigest()
                if h not in seen_hashes:
                    seen_hashes.add(h)
                    deduped.append(img)
            except Exception:
                deduped.append(img)
        images = deduped[:5]

    thumb_path = images[0]["thumbnail_path"] if images else ""
    thumb_token = images[0]["thumb_token"] if images else ""

    return {
        "ok": True,
        "data": {
            "url": prop.url,
            "address": prop.address,
            "latitude": prop.latitude,
            "longitude": prop.longitude,
            "bedrooms": prop.bedrooms,
            "bathrooms": prop.bathrooms,
            "carspaces": prop.carspaces,
            "land_size_m2": prop.land_size_m2,
            "land_size_display": format_land_size(prop.land_size_m2),
            "build_size_m2": prop.build_size_m2,
            "build_size_display": format_land_size(prop.build_size_m2) if prop.build_size_m2 else "",
            "price_display": prop.price_display,
            "status": prop.status,
            "sold_date": prop.sold_date,
            "listing_date": prop.listing_date,
            "first_listing": prop.listing_date,
            "last_listing": "",
            "days_on_market": str(prop.days_on_market or ""),
            "year_built": str(prop.year_built or ""),
            "headline": prop.headline,
            "thumbnail_url": prop.hero_image_url,
            "thumbnail_path": thumb_path,
            "thumb_token": thumb_token,
            "images": images,
        },
    }


@app.post("/api/generate")
def generate(req: GenerateRequest):
    """Generate the final PDF, streaming SSE progress events then a final result."""
    sdir = session_dir(req.session_id)
    original_pdf = sdir / "original.pdf"
    if not original_pdf.exists():
        raise HTTPException(404, "Session not found or original PDF missing")

    logo_dir = sdir / "logo"
    logos = list(logo_dir.glob("*.png")) if logo_dir.exists() else []
    logo_path = str(logos[0]) if logos else None

    subject = SubjectProperty(
        address=req.subject.address,
        suburb=req.subject.suburb,
        state=req.subject.state,
        postcode=req.subject.postcode,
        latitude=req.subject.latitude,
        longitude=req.subject.longitude,
        bedrooms=req.subject.bedrooms,
        bathrooms=req.subject.bathrooms,
        carspaces=req.subject.carspaces,
        land_size_m2=req.subject.land_size_m2,
        hero_image_path=req.subject.hero_image_path,
        property_type=req.subject.property_type,
        build_size_m2=req.subject.build_size_m2,
        build_size_display=req.subject.build_size_display,
        floor_size_m2=req.subject.floor_size_m2,
        floor_size_display=req.subject.floor_size_display,
    )
    sales    = [_to_comparable(p, subject, is_listing=False) for p in req.sales]
    listings = [_to_comparable(p, subject, is_listing=True)  for p in req.listings]
    inputs = CMAInputs(
        subject=subject, sales=sales, listings=listings,
        google_maps_api_key=req.google_maps_api_key,
        keep_original_comparables=req.keep_original_comparables,
        agent_name=req.agent_name, agency_name=req.agency_name,
        agent_email=req.agent_email, report_date=req.report_date,
        marker_size=req.marker_size,
        include_overview_map=req.include_overview_map,
        include_sales_map=req.include_sales_map,
        include_listings_map=req.include_listings_map,
    )
    output_path = OUTPUT_DIR / f"CMA-{req.session_id}.pdf"

    # Use a thread-safe queue to pass progress events from the worker thread
    # to the SSE generator.
    _q: queue.Queue = queue.Queue()

    def _progress(label: str, pct: int) -> None:
        _q.put({"step": label, "pct": pct})

    def _worker():
        try:
            build_final_pdf(
                original_pdf_path=str(original_pdf),
                inputs=inputs,
                output_path=str(output_path),
                logo_path=logo_path,
                progress=_progress,
            )
            _q.put({"done": True, "download_url": f"/api/download/{req.session_id}"})
        except Exception as exc:
            import traceback
            traceback.print_exc()
            _q.put({"error": str(exc)})

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    _deadline = time.time() + 600  # 10-minute hard limit

    def _sse_stream():
        while True:
            try:
                msg = _q.get(timeout=30)
            except queue.Empty:
                if time.time() > _deadline:
                    yield "data: {\"error\": \"Generation timed out (>10 min)\"}\n\n"
                    break
                # Send a keepalive SSE comment so the browser keeps the connection open.
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(msg)}\n\n"
            if "done" in msg or "error" in msg:
                break
        t.join(timeout=5)

    return StreamingResponse(_sse_stream(), media_type="text/event-stream")


def _normalize_status(raw: str, is_listing: bool) -> str:
    """Convert scraper status values to display labels."""
    s = (raw or "").lower().strip()
    if s in ("sold", "recently sold"):
        return "Sold"
    if s in ("for_sale", "for sale", "listing"):
        return "For Sale"
    return "For Sale" if is_listing else "Sold"


def _to_comparable(
    p: PropertyData, subject: SubjectProperty, is_listing: bool,
) -> ComparableProperty:
    # Compute distance
    dist = None
    if (p.latitude is not None and p.longitude is not None
            and subject.latitude is not None and subject.longitude is not None):
        dist = haversine_km(
            subject.latitude, subject.longitude,
            p.latitude, p.longitude,
        )

    # Determine land size display
    land_disp = p.land_size_display or (
        format_land_size(p.land_size_m2) if p.land_size_m2 else "-"
    )

    return ComparableProperty(
        index=p.index,
        address=p.address,
        latitude=p.latitude,
        longitude=p.longitude,
        bedrooms=p.bedrooms,
        bathrooms=p.bathrooms,
        carspaces=p.carspaces,
        land_size_m2=p.land_size_m2,
        land_size_display=land_disp,
        price_display=p.price_display,
        status=_normalize_status(p.status, is_listing),
        sold_date=p.sold_date,
        listing_date=p.listing_date,
        first_listing=p.first_listing,
        last_listing=p.last_listing,
        days_on_market=p.days_on_market,
        year_built=p.year_built,
        distance_km=dist,
        thumbnail_path=p.thumbnail_path,
        source_url=p.url,
        from_rea=p.from_rea,
        headline=p.headline,
        property_type=p.property_type,
        build_size_m2=p.build_size_m2,
        build_size_display=p.build_size_display,
        floor_size_m2=p.floor_size_m2,
        floor_size_display=p.floor_size_display,
        show_beds=p.show_beds,
        show_baths=p.show_baths,
        show_cars=p.show_cars,
        show_land=p.show_land,
        show_build=p.show_build,
    )


# token -> local path; populated when /api/scrape downloads a thumbnail
_thumb_registry: dict[str, str] = {}

@app.get("/api/thumb/{token}")
async def thumb(token: str):
    """Serve a downloaded thumbnail by its token (no raw paths exposed)."""
    p = _thumb_registry.get(token)
    if not p or not os.path.exists(p):
        raise HTTPException(404, "Thumbnail not found")
    return FileResponse(p)


@app.get("/api/download/{session_id}")
async def download(session_id: str):
    path = OUTPUT_DIR / f"CMA-{session_id}.pdf"
    if not path.exists():
        raise HTTPException(404, "PDF not yet generated")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"CMA-Edited-{session_id[:8]}.pdf",
    )


# --- Frontend ----------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>CMA Editor backend running. Frontend not yet built.</h1>")


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.post("/api/heartbeat")
async def heartbeat():
    global _last_heartbeat
    _last_heartbeat = time.time()
    return {"ok": True}
