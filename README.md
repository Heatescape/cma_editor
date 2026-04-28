# CMA Editor

> A local Windows desktop application that regenerates the editable pages of a **Cotality Comparative Market Analysis (CMA)** report — keeping all original pages intact while replacing the cover, maps, and comparable property detail pages with freshly generated, fully editable content.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Screenshots](#screenshots)
- [Quick Start](#quick-start)
- [How to Use](#how-to-use)
- [Architecture](#architecture)
- [API Reference](#api-reference)
- [Distribution](#distribution)
- [Troubleshooting](#troubleshooting)
- [Dependencies](#dependencies)

---

## Overview

Real estate agents using Cotality CMA reports often need to customise comparable properties, update hero images, and adjust agent branding before presenting to clients. CMA Editor automates this workflow:

1. Upload the original Cotality PDF
2. Edit comparable sales and listings (or pull live data from realestate.com.au)
3. Click **Generate** — a polished PDF is ready in seconds

All pages not explicitly regenerated (cover letter, demographic insights, schools data, market trends, disclaimer) are preserved **byte-for-byte** from the original.

```
Original Cotality PDF
        │
        ▼
┌───────────────────────────────────────────────────┐
│  CMA Editor                                       │
│                                                   │
│  Cover page    ← regenerated (new hero image)     │
│  Cover letter  ← agent signature updated          │
│  Your Property ← preserved + redacted preamble   │
│  Maps          ← new Google Static Maps tiles     │
│  Sales cards   ← new formatted detail pages       │
│  Listing cards ← new formatted detail pages       │
│  Tail pages    ← preserved unchanged              │
└───────────────────────────────────────────────────┘
        │
        ▼
 Final CMA PDF (ready to present)
```

---

## Features

### Data Extraction
- **Auto-parses the Cotality PDF** — subject property address, agent name, agency, email, report date, all comparable sales and listings, and card photos are pre-filled on upload
- **Visual-order image matching** — uses PyMuPDF positional data to assign comparable card photos to the correct property, not the XObject-reference order that pypdf returns
- **Unit/apartment support** — handles comparables without land area; ha values auto-converted to m²; flexible regex handles all Cotality icon-text formats

### REA Scraping
- **Playwright-based scraper** bypasses Cloudflare/Akamai/Kasada bot protection using Patchright
- **ArgonautExchange JSON** — reads the full Apollo cache embedded in the REA page for maximum data accuracy
- **Multi-layer fallbacks** — JSON-LD structured data, aria-label patterns, type/value attribute arrays, and HTML regex ensure bed/bath/car are extracted even from non-standard listing formats
- **5 unique photos per property** — downloads up to 20 candidate URLs, removes duplicates by MD5 hash, keeps the first 5 distinct images; click-to-select in the UI
- **Fields scraped:** address, suburb, state, postcode, coordinates, bedrooms, bathrooms, carspaces, land size, build size, price, property type, year built, listing date, sold date, days on market, headline, agency name, hero image + gallery
- **429 rate-limit handling** — automatically retries after 15 s and 45 s when REA returns HTTP 429; randomised per-page delays (3–7 s) and inter-suburb delays (4–9 s) reduce the chance of being rate-limited
- **Persistent Chrome profile** — saves session cookies to `~/.cma_rea_profile` so the scraper reuses an existing REA login between runs; bypasses bot-detection fingerprinting more effectively than an ephemeral browser

### Maps
- **Google Static Maps** integration — each comparable page gets a fresh map tile centred on the subject property
- **Custom teardrop markers** — numbered callouts rendered at 3× supersampling then downscaled (LANCZOS) for crisp output; white border for readability on any background
- **Smart overlap resolution** — two-pass algorithm: rotational spread (orbital angle solver) followed by force-directed head separation; handles any number of co-located properties without manual adjustment
- **Three map pages** — Overview (all), Sales only, Listings only; each page optionally included

### PDF Generation
- **ReportLab** renderer — 5 comparable cards per page, compact layout with icon strip (bed / bath / car / land / build), meta grid, thumbnail, distance from subject
- **SSE progress stream** — per-card live progress with ETA ("Rendering comparable sales (3/7)… ~14s remaining")
- **Correct page splicing** — new pages merged with original using pypdf; original agent signature block white-rect redacted; Cotality preamble on "Your Property" page replaced cleanly

### UI & Workflow
- **Continuous numbering** — Sales 1–N, Listings N+1–M; deleting a property immediately renumbers everything including the other tab; Undo restores the original number
- **Drag-to-reorder** — map pin numbers follow the list order
- **Bulk actions** — Fetch All REA, Geocode All, Show/hide all beds/baths/cars/land/build
- **Auto-find comparables** — searches REA automatically using configurable min–max range filters for beds, baths, cars, land size, and build size; results populate the list for review before importing
- **Collapsible sections** — click the Step 4 or Step 5 header to collapse/expand the comparable list, keeping the screen tidy when focusing on other steps
- **5-second undo** — soft-delete with countdown before permanent removal
- **Offline-first** — only scraping, geocoding, and map tile steps require internet
- **Auto-shutdown** — server exits 30 seconds after the browser tab closes; no background processes

### Installation & Distribution
- **Zero-touch setup** — `start.bat` creates the venv, installs packages, and downloads Chromium on first run
- **Auto-update packages** — hashes `requirements.txt` on each launch; re-runs pip install when the hash changes (handles new package versions in distributed builds)
- **Desktop shortcut** — `create_shortcut.bat` creates a branded shortcut with custom icon
- **One-click packaging** — `build_release.bat` produces a self-contained `CMA-Editor.zip` ready to distribute

---

## Screenshots

> The app runs entirely in your local browser at `http://localhost:8000`.

### Step 4 — Comparable Sales
```
┌──────────────────────────────────────────────────────────────────┐
│  Comparable sales  (7)          [Fetch All REA]                  │
│  Show all: [✓]🛏  [✓]🛁  [✓]🚗  [✓]⬚  [✓]⬚                     │
├──────────────────────────────────────────────────────────────────┤
│  [1] 13 SYDNEY STREET RIVERSTONE NSW 2765    Fetched ✓     ×    │
│      https://www.realestate.com.au/sold/...      [Re-fetch]      │
│      [📷][📷][📷][📷][📷]  Replace photo                         │
│      Address           ║  Sold Price                             │
│      13 Sydney St…     ║  $950,000                               │
│      [✓]🛏 3  [✓]🛁 2  [✓]🚗 1                                   │
│      [✓]⬚ 450  [✓]⬚ 180                                         │
│      Sold Date         ║  Latitude      ║  Longitude             │
│      2025-11-04        ║  -33.68721     ║  150.95392             │
│      ✓ Coordinates filled                                        │
├──────────────────────────────────────────────────────────────────┤
│  [2] Deleted: 45 MAIN ROAD RIVERSTONE…                  [Undo]  │
├──────────────────────────────────────────────────────────────────┤
│  [2] 67 OAK AVENUE RIVERSTONE NSW 2765       Fetched ✓     ×    │
│      …                                                           │
└──────────────────────────────────────────────────────────────────┘
```

### Generated PDF — Comparable Sales Card
```
┌──────────────────────────────────────────────────────────────────┐
│ 🔵 1   13 SYDNEY STREET RIVERSTONE NSW 2765      SOLD $950,000  │
├────────────────┬─────────────────────────────────────────────────┤
│                │  3 🛏  2 🛁  1 🚗  450m² ⬚  180m² ⬚           │
│   [property    │  ─────────────────────────────────────────      │
│    photo]      │  Year Built    2019    DOM        42            │
│                │  Sold Date     04 Nov 25   Distance  0.8km      │
│                │  First Listing $985,000                         │
└────────────────┴─────────────────────────────────────────────────┘
```

---

## Quick Start

### Requirements

- **Windows 10 or later**
- **Python 3.10+** — download from [python.org](https://www.python.org/downloads/)
  - ✅ During install: tick **"Add Python to PATH"**
- **~500 MB free disk space** (venv + Chromium)
- **Google Maps API key** — needed for maps and geocoding ([get one free](https://console.cloud.google.com/))

### First Run

1. Unzip `CMA-Editor.zip` (or clone this repo) to any folder, e.g. `C:\CMA-Editor`
2. Double-click **`start.bat`**
3. First run installs all packages and downloads Chromium — takes **2–5 minutes**, shows progress
4. Your browser opens automatically at `http://localhost:8000`

Subsequent runs start in a **hidden terminal window** and open the browser immediately (< 5 seconds).

### Optional: Desktop Shortcut

Double-click **`create_shortcut.bat`** once to create a branded **CMA Editor** shortcut on your Desktop. Launch the app from there without opening the project folder.

---

## How to Use

The app guides you through **6 steps** in the left sidebar.

---

### Step 1 — Upload Original PDF

Upload the Cotality-generated CMA PDF. The app extracts and pre-fills:

| Extracted | Used in |
|-----------|---------|
| Subject property address | Step 3 |
| Subject beds / baths / cars / land / build | Step 3 |
| Agent name, agency, email, report date | Step 6 |
| All comparable sales with addresses, prices, features, photos | Step 4 |
| All comparable listings with addresses, prices, features, photos | Step 5 |

Optionally upload a **replacement hero image** — the large photo on the cover page. You can also change it in Step 3.

---

### Step 2 — Google Maps API Key

Required for:
- Rendering comparable location maps (Maps Static API)
- "Geocode All" and the per-row Geocode button (Geocoding API)

**Get a free key:** [console.cloud.google.com](https://console.cloud.google.com/) → Enable **Maps Static API** and **Geocoding API**

Your key is saved in the browser (`localStorage`) — you only need to enter it once per device.

---

### Step 3 — Subject Property

| Field | Notes |
|-------|-------|
| **Hero image** | Photo shown on the cover page; drag-and-drop or click to upload |
| **Address** | Full street address as it will appear on the cover |
| **Latitude / Longitude** | Used to centre maps and calculate distances; click **⊕ Auto-fill coordinates** to geocode automatically |
| **Property type** | House / Unit / Townhouse / Land |
| **Bedrooms / Bathrooms / Carspaces** | Shown on the cover page |
| **Land size** | m² |
| **Build / Floor size** | m² (optional) |

---

### Step 4 — Comparable Sales

#### Adding Comparables

| Method | How |
|--------|-----|
| **From PDF** | Pre-filled automatically on upload |
| **From REA** | Click **+ From realestate.com.au**, paste URL, click **Fetch** |
| **Manual** | Click **+ Manual entry**, fill in fields directly |

#### Bulk Actions

| Button | Effect |
|--------|--------|
| **Fetch All REA** | Scrapes every row that has a URL, one by one, updating live |
| **Auto-find Comparables** | Searches REA automatically using the filters below and populates the panel with candidate URLs for review |
| **Geocode All** | Sends all un-geocoded addresses to Google in one batch |
| **Show all: ✓🛏 ✓🛁 ✓🚗 ✓⬚ ✓⬚** | Toggle bed/bath/car/land/build visibility for all rows at once |

#### Auto-find Filters

The **Auto-find filters** panel (below the sort buttons) controls what REA is searched for:

| Filter | Description |
|--------|-------------|
| **🛏 Beds min – max** | Bedroom count range; pre-filled from the subject property |
| **🛁 Baths min – max** | Bathroom count range; pre-filled from the subject property |
| **🚗 Cars min – max** | Car space count range; pre-filled from the subject property |
| **Land m² min – max** | Land size range (houses and land only) |
| **Build m² min – max** | Building size range (houses only) |
| **Suburbs** | Additional suburbs to search alongside the subject suburb |
| **Distance km radius** | Restrict results to properties within N km of the subject |

All filters are optional — uncheck the checkbox to disable that filter for the search. After reviewing the candidate URLs in the result panel, click **Import selected** to add them to the list.

#### Per-Row Controls

- **Photo strip** — up to 5 thumbnails; click any to set as the card photo; or upload your own
- **Re-fetch** — re-scrapes from REA, overwriting all fields
- **X button** — soft-deletes with a 5-second **Undo** window; all subsequent numbers update immediately
- **Drag handle** — reorder rows; numbers and map pins update in real time

#### Numbering

Sales and Listings share a **single continuous sequence**: if there are 7 sales (1–7), listings start at 8. Deleting or reordering a property renumbers everything automatically.

---

### Step 5 — Comparable Listings

Identical layout to Step 4. Listing-specific fields:

| Field | Notes |
|-------|-------|
| **Listing Date** | ISO date YYYY-MM-DD |
| **Listing Price** | Display string, e.g. "$700,000" or "Contact Agent" |
| **Days on Market** | Calculated from listing date, or enter manually |

---

### Step 6 — Report Details

| Field | Notes |
|-------|-------|
| **Agent Name** | Pre-filled from PDF; appears on cover page |
| **Agency Name** | Pre-filled from PDF |
| **Agent Email** | Pre-filled from PDF |
| **Report Date** | Pre-filled from PDF ("Prepared on …") |
| **Marker size** | Small / Medium / Large — affects all map pin sizes |
| **Include maps** | Toggle: Overview map, Sales map, Listings map |

---

### Generate

Click **Generate PDF**. The button is enabled only when:
- A PDF session is loaded
- A Google Maps API key is entered
- The subject property address is filled
- All comparable rows have been fetched or filled (no loading / error states)
- At least one comparable exists

**During generation**, a progress bar shows the current step and remaining time:

```
Rendering comparable sales (3/7)...             ~14s remaining  42%
████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░
```

**When complete**, a green result box appears with a **Download** link. The generated PDF:

- Replaces the cover page (new hero image, new agent block)
- Updates the cover letter (agent details redacted and replaced)
- Replaces comparable map pages (fresh Google Static Maps tiles)
- Replaces comparable detail pages (5 cards per page, all new data)
- Keeps all other pages unchanged from the original

---

## Architecture

```
cma_editor/
├── backend/
│   ├── app.py                  FastAPI server — HTTP endpoints + SSE progress + heartbeat
│   ├── scrapers/
│   │   └── rea.py              Patchright scraper: ArgonautExchange → JSON-LD → HTML fallbacks
│   ├── generators/
│   │   ├── models.py           Pydantic models: SubjectProperty, ComparableProperty, CMAInputs
│   │   ├── styles.py           Colours, fonts, page dimensions (Cotality template constants)
│   │   ├── chrome.py           Shared ReportLab helpers: page chrome, H1, HR, text wrapping
│   │   ├── pages.py            Per-page renderers: cover, maps, comparable cards (5/page)
│   │   └── builder.py          Orchestrator: render new pages → splice with original PDF
│   └── utils/
│       ├── helpers.py          PDF text parsing (subject + comparables), image extraction,
│       │                       PyMuPDF visual-order image sorting, haversine, geocoding
│       └── maps.py             Google Static Maps: custom markers, overlap resolution,
│                               teardrop callouts, Web Mercator projection
├── frontend/
│   ├── index.html              Single-page app (vanilla JS, no build step required)
│   └── icons/                  bed / bath / car / land / build PNG icons
│                               (extracted from original Cotality PDF, used in UI + PDF)
├── icon/
│   └── cma.ico                 App icon for the Windows desktop shortcut
├── requirements.txt
├── start.bat                   Launcher: venv setup, package install, hidden server, auto-open browser
├── create_shortcut.bat         Creates a branded Desktop shortcut
├── build_release.bat           Wrapper → build_release.ps1
└── build_release.ps1           Packages project into CMA-Editor.zip for distribution
```

### PDF Generation Pipeline

```
POST /api/generate
        │
        ├─ 5%   Scan original PDF → classify pages by heading text
        ├─ 10%  Render cover page (ReportLab)
        ├─ 20%  Render overview map (Google Static Maps + custom markers)
        ├─ 35%  Render sales map
        ├─ 55%  Render comparable sales cards (5 per page, per-card SSE progress)
        ├─ 68%  Render listings map
        ├─ 80%  Render comparable listings cards
        └─ 90%  Splice pages with pypdf → write final PDF → return download URL
```

### REA Scraper Data Flow

```
URL → Patchright (Chromium)
        │
        ├─ window.ArgonautExchange  (Apollo cache, most reliable)
        │    └─ urqlClientCache → details.listing → all fields
        │
        ├─ JSON-LD structured data  (<script type="application/ld+json">)
        │    └─ schema.org numberOfBedrooms / numberOfBathroomsTotal / …
        │
        ├─ type/value attribute arrays  ({type: "BEDROOM", value: "3"})
        │
        ├─ aria-label patterns  (aria-label="3 Bedrooms")
        │
        └─ HTML regex fallbacks  (title, h1, text patterns)

Images → _populate_images()
        ├─ media.mainImage.templatedUrl  → index=0
        ├─ media.images[].templatedUrl  → index=i (gallery order)
        ├─ Full-tree walker             → any remaining Image objects
        └─ Download 20 candidates → MD5 dedup → keep 5 unique
```

---

## API Reference

### Session

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/session` | Upload original PDF + optional hero image. Returns `session_id`, extracted subject/agent/comparables data |
| `POST` | `/api/upload-hero` | Replace the hero image for an existing session |
| `POST` | `/api/upload-comparable-thumb` | Upload a custom photo for a specific comparable row |

**`POST /api/session` response:**
```json
{
  "session_id": "abc123",
  "subject_info": { "address": "…", "bedrooms": 4, "land_size_m2": 450, … },
  "agent_info":   { "agent_name": "…", "agency_name": "…", "agent_email": "…", "report_date": "…" },
  "comparables_info": {
    "sales":    [ { "address": "…", "price_display": "$950,000", "bedrooms": 3, … } ],
    "listings": [ … ]
  }
}
```

### Scraping & Geocoding

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/scrape` | Scrape a REA URL. Returns full property data + up to 5 downloaded images |
| `POST` | `/api/geocode` | Single address → `{ lat, lng }` via Google Geocoding API |
| `POST` | `/api/geocode-batch` | Array of addresses → array of `{ lat, lng }` results |

**`POST /api/scrape` response:**
```json
{
  "ok": true,
  "data": {
    "address": "13 Sydney Street, Riverstone NSW 2765",
    "bedrooms": 4, "bathrooms": 2, "carspaces": 2,
    "land_size_m2": 450, "build_size_m2": 240,
    "price_display": "$950,000", "price_numeric": 950000,
    "sold_date": "2025-11-04", "days_on_market": 42,
    "latitude": -33.68721, "longitude": 150.95392,
    "thumb_token": "abc123",
    "images": [ { "thumb_token": "…", "thumbnail_path": "…" }, … ]
  }
}
```

### PDF Generation

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/generate` | Start PDF generation. Returns SSE stream of progress events |
| `GET`  | `/api/download/{session_id}` | Download the generated PDF |
| `GET`  | `/api/thumb/{token}` | Serve a downloaded thumbnail image by token |

**SSE event format:**
```
data: {"step": "Rendering comparable sales (3/7)...", "pct": 42}

data: {"done": true, "download_url": "/api/download/abc123"}
```

### Maintenance

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/heartbeat` | Browser keepalive — server exits 30s after last heartbeat |
| `GET`  | `/api/health` | Returns `{"ok": true}` |

---

## Distribution

### Building a Release

```bat
build_release.bat
```

Creates **`CMA-Editor.zip`** in the project folder (~3–5 MB). Excludes: `.git`, `.venv`, `uploads/`, `output/`, `.tmp/`, debug scripts, and PDFs.

### What the Recipient Does

1. Unzip to any folder, e.g. `C:\CMA-Editor`
2. *(Optional)* Double-click `create_shortcut.bat` once for a Desktop shortcut
3. Double-click `start.bat` — first run installs everything automatically

**First-run time:** 2–5 minutes (package install + Chromium download ~200 MB)  
**Subsequent runs:** < 5 seconds

### Auto-Update Behaviour

`start.bat` hashes `requirements.txt` on every launch and compares with the stored hash in `.venv\.installed`. If the hash has changed (e.g. a new package was added in a newer release), pip automatically updates packages without requiring Chromium to be re-downloaded.

---

## Troubleshooting

### "Scrape failed: Could not find ArgonautExchange JSON"
The REA page returned a CAPTCHA or bot-detection block.
- Wait 5 minutes, then try again
- Verify the URL is still live in your browser
- This is rare on local machines with a residential IP

### Auto-find returns "HTTP ERROR 429" / rate limited
REA temporarily blocked the scraper for sending too many requests.
- The scraper automatically retries after 15 s and 45 s — wait for the retry to complete
- If it keeps happening, wait a few minutes before running Auto-find again
- **Persistent profile login** reduces 429s: on first Auto-find the browser opens — log in to realestate.com.au in that window. The session is saved to `~/.cma_rea_profile` and reused on every subsequent run, making the scraper appear as a real logged-in user
- To disable the persistent profile, set the environment variable `CMA_CHROME_PROFILE=` (empty) before launching

### "Maps show a grey placeholder box"
- The Google Maps API key is invalid, or Maps Static API is not enabled
- Verify at: [console.cloud.google.com/apis/library/static-maps-backend.googleapis.com](https://console.cloud.google.com/apis/library/static-maps-backend.googleapis.com)

### "Upload failed: Failed to fetch"
The server didn't start. Check `server.log` in the app folder for the Python traceback.

Common causes:
- Python not found in PATH → reinstall Python with "Add to PATH" ticked
- Port 8000 already in use → `start.bat` attempts to kill it automatically; if it fails, restart your PC
- Missing package → delete `.venv\` and re-run `start.bat` to reinstall

### "Comparable distances show as —"
The comparable or subject property is missing lat/lng. Click **⊕ Auto-fill coordinates** on the subject, or use **Geocode All** in the comparable tab.

### "Generation gets stuck / times out"
- Restart the server (close the terminal, re-run `start.bat`)
- Check `server.log` for a Python traceback indicating the stuck step

### Packaged version not reading PDF data
Make sure you are using a build created after `pymupdf` was added to `requirements.txt`. Delete `.venv\` from the installed copy and re-run `start.bat` to force a clean reinstall.

---

## Debug Scraping

To inspect what data REA returns for a listing, run from the project folder:

```bat
.venv\Scripts\python debug_scrape.py "https://www.realestate.com.au/property-house-nsw-..."
```

Saves `debug_raw.json` with the full ArgonautExchange JSON payload — useful for diagnosing why a field isn't being extracted.

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi` | ≥0.110 | HTTP server framework |
| `uvicorn[standard]` | ≥0.27 | ASGI server |
| `python-multipart` | ≥0.0.9 | File upload support |
| `pydantic` | ≥2.6 | Request/response validation and data models |
| `requests` | ≥2.31 | HTTP downloads (thumbnails, map tiles) |
| `beautifulsoup4` | ≥4.12 | HTML parsing fallback |
| `patchright` | ≥1.42 | Playwright fork — bypasses REA bot detection |
| `pypdf` | ≥4.0 | PDF text extraction and page splicing |
| `pymupdf` | ≥1.23 | Position-aware image extraction from PDF pages |
| `reportlab` | ≥4.1 | PDF page generation (comparable cards, maps, cover) |
| `Pillow` | ≥10.2 | Image processing, format conversion, thumbnail handling |

### External Services

| Service | Used for | Required |
|---------|----------|----------|
| Google Maps Static API | Map tile images | Yes, for maps |
| Google Maps Geocoding API | Address → coordinates | Yes, for geocoding |
| realestate.com.au | Live property data | Optional (manual entry works offline) |

---

## License

Private — for internal use by VPI Group.
