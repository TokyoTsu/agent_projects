"""AutoTrader UK.

Search results are loaded by JavaScript from AutoTrader's own GraphQL gateway, so
search runs in a real browser and reads that response. Listing pages are
server-rendered, so they're fetched with plain HTTP (browser as a fallback).
"""

import json
import re
from urllib.parse import urlencode

from .web import browser, ensure_allowed, http_get

BASE = "https://www.autotrader.co.uk"

FUEL_TYPES = {
    "petrol": "Petrol",
    "diesel": "Diesel",
    "hybrid": "Petrol Hybrid",
    "petrol hybrid": "Petrol Hybrid",
    "diesel hybrid": "Diesel Hybrid",
    "plug-in hybrid": "Petrol Plug-in Hybrid",
    "electric": "Electric",
}

# Listing types that AutoTrader injects but which may not match the filters.
_SKIP_TYPES = {"YOU_MAY_ALSO_LIKE"}


def search(
    postcode: str,
    make: str | None = None,
    model: str | None = None,
    radius_miles: int | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    mileage_max: int | None = None,
    fuel_type: str | None = None,
    transmission: str | None = None,
    body_type: str | None = None,
    page: int = 1,
    sort: str | None = None,
) -> dict:
    """sort: None for AutoTrader's relevance order, or "price-asc" for cheapest first."""
    params = {
        "postcode": postcode,
        "make": make,
        "model": model,
        "radius": radius_miles if isinstance(radius_miles, int) else None,  # None = national
        "price-from": price_min,
        "price-to": price_max,
        "year-from": year_min,
        "year-to": year_max,
        "maximum-mileage": mileage_max,
        "fuel-type": FUEL_TYPES.get((fuel_type or "").lower(), fuel_type),
        "transmission": transmission,
        "body-type": body_type,
        "page": page if page > 1 else None,
        "sort": sort,
    }
    url = f"{BASE}/car-search?" + urlencode({k: v for k, v in params.items() if v})
    ensure_allowed(url)

    tab = browser.new_page()
    try:
        with tab.expect_response(lambda r: "SearchResultsListingsGridQuery" in r.url) as resp:
            tab.goto(url, wait_until="domcontentloaded")
        payload = resp.value.json()
    finally:
        tab.close()

    results = next(p["data"]["searchResults"] for p in payload if "searchResults" in p.get("data", {}))
    listings = []
    for item in results["listings"]:
        if not item.get("advertId") or item.get("type") in _SKIP_TYPES:
            continue
        ctx = (item.get("trackingContext") or {}).get("advertContext") or {}
        badges = [b["displayText"] for b in item.get("badges") or []]
        listings.append({
            "advert_id": item["advertId"],
            "listing_type": item.get("type"),
            "title": item.get("title"),
            "variant": item.get("subTitle"),
            "price": item.get("price"),
            "year": ctx.get("year"),
            "badges": badges,  # mileage, reg year, price rating, write-off category (e.g. "Cat S")
            "location": item.get("vehicleLocation"),
            "seller_type": item.get("sellerType"),
            "headline": item.get("attentionGrabber"),
            "url": f"{BASE}/car-details/{item['advertId']}",
        })
    page_info = results["page"]
    return {
        "search_url": url,
        "page": page_info["number"],
        "total_pages": page_info["count"],
        "total_results": page_info["results"]["count"],
        "listings": listings,
    }


def _hydration_data(html: str) -> dict:
    m = re.search(r"window\.__staticRouterHydrationData = JSON\.parse\((\".*?\")\);</script>", html, re.S)
    if not m:
        raise ValueError("AutoTrader listing page layout has changed: hydration data not found")
    return json.loads(json.loads(m.group(1)))


def _listing_html(advert_id: str) -> str:
    url = f"{BASE}/car-details/{advert_id}"
    html = http_get(url).text
    if "__staticRouterHydrationData" in html:
        return html
    tab = browser.new_page()
    try:
        tab.goto(url, wait_until="domcontentloaded")
        return tab.content()
    finally:
        tab.close()


def listing(advert_id: str) -> dict:
    advert = _hydration_data(_listing_html(advert_id))["loaderData"]["car-details"]["aggregatorAdvert"]
    details = advert.get("details") or {}
    history = advert.get("history") or {}
    check = history.get("vehicleCheck") or {}
    tracking = (advert.get("advertTrackingData") or {}).get("advertContext") or {}
    images = [img["url"].replace("{resize}", "w800") for img in (advert.get("gallery") or {}).get("images", [])]
    return {
        "advert_id": advert_id,
        "url": f"{BASE}/car-details/{advert_id}",
        "title": details.get("title"),
        "variant": details.get("subTitle"),
        "price": (advert.get("gallery") or {}).get("price"),
        "mileage": tracking.get("mileage"),
        "key_specs": {s["label"]: s["value"] for s in advert.get("keySpecification") or []},
        "seller": {
            "name": (advert.get("seller") or {}).get("name"),
            "type": tracking.get("advertiserType"),
            "town": ((advert.get("seller") or {}).get("location") or {}).get("town"),
        },
        "autotrader_basic_history_check": {
            "status": check.get("status"),
            "summary": check.get("statusText"),
            "checks": [f"{c['label']}: {c['status']}" for c in check.get("basicChecks") or []],
        },
        "mot": (history.get("mot") or {}).get("status"),
        "owners": (history.get("ownersData") or {}).get("value"),
        "service_history": (history.get("serviceHistory") or {}).get("description"),
        "running_costs": {c["label"]: c["value"] for c in (advert.get("runningCostsV2") or {}).get("items", [])},
        "description": "\n".join((advert.get("description") or {}).get("text") or [])[:3000],
        "photo_count": len(images),
        "_photo_urls": images,
    }
