"""
Debug tool: scrape one REA URL and dump the raw JSON structure.

Run from the project root:
    python debug_scrape.py "https://www.realestate.com.au/property-house-nsw-riverstone-..."

Output:
  - Prints the normalized ScrapedProperty fields so you can see what was extracted
  - Saves the full ArgonautExchange JSON to debug_raw.json
  - Saves the page HTML to debug_page.html (useful if scraping fails)

If something is missing / wrong in the extracted data, share debug_raw.json
so the field mappings can be corrected.
"""
import sys
import json
import re
import os

# Make sure backend package is importable from project root
sys.path.insert(0, os.path.dirname(__file__))

from backend.scrapers.rea import REAScraper, ScraperError

def main():
    if len(sys.argv) < 2:
        print("Usage: python debug_scrape.py <realestate.com.au URL>")
        sys.exit(1)

    url = sys.argv[1]
    print(f"\n{'='*60}")
    print(f"Scraping: {url}")
    print(f"{'='*60}\n")

    # Intercept raw HTML for diagnostics
    original_parse = REAScraper._parse_html

    captured = {}
    def patched_parse(self, url, html, argonaut_live):
        captured["html"] = html
        captured["argonaut_live"] = argonaut_live
        argonaut_from_html = REAScraper._extract_argonaut(html)
        captured["argonaut_html"] = argonaut_from_html
        return original_parse(self, url, html, argonaut_live)

    REAScraper._parse_html = patched_parse

    try:
        with REAScraper() as s:
            prop = s.scrape(url)
    except ScraperError as e:
        print(f"[SCRAPE FAILED] {e}\n")
        # Save HTML so we can inspect what came back
        if "html" in captured:
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(captured["html"])
            print("HTML saved to debug_page.html — check if it's a CAPTCHA/block page.")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)
    finally:
        REAScraper._parse_html = original_parse

    print("EXTRACTED FIELDS:")
    print("-" * 40)
    for field, value in prop.to_dict().items():
        if field in ("image_urls",):
            print(f"  {field}: ({len(value)} items) {value[:2]}")
        else:
            print(f"  {field}: {value!r}")

    # Save raw JSON
    raw = prop.raw_json
    with open("debug_raw.json", "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nFull ArgonautExchange JSON saved to: debug_raw.json ({len(json.dumps(raw))} chars)")
    print("Share this file if field mappings need to be fixed.\n")

    # Top-level key summary
    print("ArgonautExchange top-level keys:")
    for k in list(raw.keys())[:20]:
        v = raw[k]
        if isinstance(v, dict):
            print(f"  {k!r}: dict({len(v)} keys)")
        elif isinstance(v, list):
            print(f"  {k!r}: list({len(v)})")
        elif isinstance(v, str) and len(v) > 80:
            print(f"  {k!r}: str({len(v)} chars)")
        else:
            print(f"  {k!r}: {v!r}")


if __name__ == "__main__":
    main()
