"""
api/saint.py — Vercel serverless function
Fetches the Saint of the Day from Universalis.com and returns structured JSON.

Deploy alongside your existing api/readings.py in massfinder-readings-api.

Endpoint: GET /api/saint?date=YYYYMMDD
Response: { name, feast, bio, prayer, url }

Universalis JSON API: https://universalis.com/{YYYYMMDD}/0/Mass.json
Returns celebration metadata including feast name, rank, and liturgical colour.
Saint biography is scraped from the Universalis HTML page when available.
"""

import json
import re
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# curl_cffi provides Chrome TLS fingerprinting — same pattern as readings.py
try:
    from curl_cffi import requests as cffi_requests
    USE_CFFI = True
except ImportError:
    import urllib.request
    USE_CFFI = False

# ─────────────────────────────────────────────────────────────
# CORS headers — same as your readings endpoint
# ─────────────────────────────────────────────────────────────
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Content-Type": "application/json",
}


def fetch_url(url: str, timeout: int = 10) -> str:
    """Fetch a URL using curl_cffi (Chrome TLS) if available, else urllib."""
    if USE_CFFI:
        resp = cffi_requests.get(
            url,
            impersonate="chrome110",
            timeout=timeout,
            headers={
                "Accept": "application/json, text/html, */*",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        resp.raise_for_status()
        return resp.text
    else:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MassFinderBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8")


# ─────────────────────────────────────────────────────────────
# Universalis JSON parser
# ─────────────────────────────────────────────────────────────
def parse_universalis_json(data: dict) -> dict:
    """
    Extract celebration name and colour from Universalis Mass.json.

    Universalis JSON structure (simplified):
    {
      "celebrations": [
        { "name": "Feast of St. John of God", "rank": 3, "colour": "w" }
      ],
      "Mass_R": { ... }   ← readings; we don't use this here
    }

    The first celebration is the principal one for the day.
    Colour codes: w=white, r=red, g=green, v=violet/purple, p=rose, b=black
    """
    celebrations = data.get("celebrations") or []

    # Some Universalis responses nest under a key like "0" (location code)
    if not celebrations and isinstance(data, dict):
        for v in data.values():
            if isinstance(v, dict) and "celebrations" in v:
                celebrations = v["celebrations"]
                break

    if not celebrations:
        return {}

    principal = celebrations[0]
    name = principal.get("name", "").strip()

    colour_map = {
        "w": "white", "r": "red", "g": "green",
        "v": "violet", "p": "rose", "b": "black",
    }
    colour = colour_map.get(principal.get("colour", ""), "")
    rank = principal.get("rank", 0)

    # rank 1=solemnity, 2=feast, 3=memorial, 4=optional memorial, 5=weekday
    rank_labels = {1: "Solemnity", 2: "Feast", 3: "Memorial", 4: "Optional Memorial", 5: "Weekday"}
    rank_label = rank_labels.get(rank, "")

    return {"name": name, "rank": rank, "rank_label": rank_label, "colour": colour}


# ─────────────────────────────────────────────────────────────
# HTML bio scraper (best-effort)
# ─────────────────────────────────────────────────────────────
def scrape_saint_bio(html: str) -> str:
    """
    Attempt to extract a brief saint biography from the Universalis HTML page.

    Universalis renders saint bios in a <div class="content"> block,
    sometimes inside a <div class="bio"> or following the heading.
    We extract the first substantive paragraph after the celebration heading.
    """
    # Strip scripts and styles first
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # Look for bio div first (Universalis sometimes uses this)
    bio_match = re.search(
        r'<div[^>]*class="[^"]*bio[^"]*"[^>]*>(.*?)</div>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if bio_match:
        raw = bio_match.group(1)
        text = re.sub(r"<[^>]+>", "", raw).strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) > 60:
            return _truncate(text, 400)

    # Fallback: look for first <p> inside .content that looks like a biography
    content_match = re.search(
        r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>\s*</div>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if content_match:
        block = content_match.group(1)
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", block, re.DOTALL | re.IGNORECASE)
        for p in paragraphs:
            text = re.sub(r"<[^>]+>", "", p).strip()
            text = re.sub(r"\s+", " ", text)
            # Skip very short lines (headings, rubrics, etc.)
            if len(text) > 80:
                return _truncate(text, 400)

    return ""


def _truncate(text: str, max_len: int) -> str:
    """Truncate at sentence boundary within max_len."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    last_period = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if last_period > max_len // 2:
        return cut[: last_period + 1]
    return cut.rstrip() + "…"


# ─────────────────────────────────────────────────────────────
# Closing prayer — standard intercession phrase
# ─────────────────────────────────────────────────────────────
def build_prayer(name: str, rank: int) -> str:
    """
    Return a simple closing prayer / intercession line appropriate to the day.
    Solemnities of the Lord get a different form; saints get the standard form.
    """
    lord_feasts = {
        "The Most Holy Trinity", "The Most Holy Body and Blood of Christ",
        "Our Lord Jesus Christ, King of the Universe", "The Transfiguration of the Lord",
        "The Presentation of the Lord", "The Ascension of the Lord",
        "Pentecost Sunday", "The Exaltation of the Holy Cross",
        "The Annunciation of the Lord",
    }
    marian = {
        "Our Lady", "Blessed Virgin Mary", "Mary", "Assumption", "Immaculate",
        "Our Lady of", "Nativity of the Blessed Virgin",
    }

    if any(lf.lower() in name.lower() for lf in lord_feasts):
        return "Lord Jesus, have mercy on us."

    if any(m.lower() in name.lower() for m in marian):
        return "Holy Mary, pray for us."

    # Strip rank label prefix if Universalis includes it
    clean = re.sub(r"^(Saints?|Blessed|Venerable)\s+", "", name).strip()
    # For memorials/feasts of saints, use standard intercession
    if rank <= 4 and "saints" not in name.lower():
        return f"{name}, pray for us."

    return "All holy men and women, pray for us."


# ─────────────────────────────────────────────────────────────
# Main handler
# ─────────────────────────────────────────────────────────────
def handler(request, context=None):
    """Vercel Python serverless handler."""

    # Handle OPTIONS preflight
    method = getattr(request, "method", "GET")
    if method == "OPTIONS":
        return Response("", 204, CORS_HEADERS)

    # Parse date param
    raw_url = getattr(request, "url", "") or ""
    params = parse_qs(urlparse(raw_url).query)
    date_str = (params.get("date") or [None])[0]

    if not date_str or not re.match(r"^\d{8}$", date_str):
        # Default to today UTC
        date_str = datetime.utcnow().strftime("%Y%m%d")

    # Validate date
    try:
        dt = datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        return json_response({"error": "Invalid date"}, 400)

    # ── 1. Fetch Universalis JSON ──────────────────────────────
    json_url = f"https://universalis.com/{date_str}/0/Mass.json"
    page_url = f"https://universalis.com/{date_str}/0/Mass.htm"

    celebration = {}
    try:
        raw_json = fetch_url(json_url, timeout=8)
        data = json.loads(raw_json)
        celebration = parse_universalis_json(data)
    except Exception as e:
        print(f"[saint] JSON fetch failed: {e}", file=sys.stderr)

    if not celebration.get("name"):
        return json_response({"error": "Could not retrieve saint data", "url": page_url}, 502)

    # ── 2. Fetch HTML for bio (best-effort, non-blocking) ─────
    bio = ""
    try:
        html = fetch_url(page_url, timeout=8)
        bio = scrape_saint_bio(html)
    except Exception as e:
        print(f"[saint] HTML fetch failed: {e}", file=sys.stderr)

    # ── 3. Build response ─────────────────────────────────────
    months = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    feast_label = f"Feast Day · {months[dt.month - 1]} {dt.day}"
    if celebration.get("rank_label"):
        feast_label = f"{celebration['rank_label']} · {months[dt.month - 1]} {dt.day}"

    result = {
        "name": celebration["name"],
        "feast": feast_label,
        "bio": bio,
        "prayer": build_prayer(celebration["name"], celebration.get("rank", 5)),
        "colour": celebration.get("colour", ""),
        "url": page_url,
    }

    return json_response(result, 200)


def json_response(data: dict, status: int = 200):
    """Return a Vercel-compatible response object."""
    body = json.dumps(data, ensure_ascii=False)
    # Vercel Python runtime uses a dict-based return or Response class depending on version.
    # This pattern works with both the legacy handler and the newer @vercel/python runtime.
    return {
        "statusCode": status,
        "headers": CORS_HEADERS,
        "body": body,
    }


# ─────────────────────────────────────────────────────────────
# Local dev / testing
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    """
    Quick local test:
        python api/saint.py 20260317
    """
    import sys
    date_arg = sys.argv[1] if len(sys.argv) > 1 else datetime.utcnow().strftime("%Y%m%d")

    class MockRequest:
        method = "GET"
        url = f"http://localhost/api/saint?date={date_arg}"

    result = handler(MockRequest())
    print(json.dumps(json.loads(result["body"]), indent=2, ensure_ascii=False))
