"""Tools the agent can call. Each one returns real data fetched from an allowed site."""

import base64
import json

from anthropic import beta_tool

from sites import autotrader, carowl, toyota, web

_state = {"max_reports": 8, "reports_run": set(), "log": print}


def configure(config: dict, log=print) -> None:
    """Call at the start of each run. `log` receives one progress line per tool call."""
    web.set_allowed_sites(config["allowed_sites"])
    _state.update(max_reports=config.get("max_reports", 8), reports_run=set(), log=log)


def _log(name: str, detail: str = "") -> None:
    _state["log"](f"→ {name} {detail}".rstrip())


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


@beta_tool
def search_autotrader(
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
) -> str:
    """Search used cars for sale on AutoTrader UK. Returns one page (about 25) of listings.

    Listings do not include the registration plate. Badges include mileage, registration
    year, AutoTrader's price rating, and write-off category if any (e.g. "Cat S", "Cat N").

    Args:
        postcode: UK postcode to measure distance from.
        make: Manufacturer, e.g. "Toyota".
        model: Model, e.g. "Yaris".
        radius_miles: Search radius in miles. Omit for national.
        price_min: Minimum price in GBP.
        price_max: Maximum price in GBP.
        year_min: Earliest registration year.
        year_max: Latest registration year.
        mileage_max: Maximum mileage.
        fuel_type: Petrol, Diesel, Hybrid, Plug-in hybrid, or Electric.
        transmission: Automatic or Manual.
        body_type: e.g. Hatchback, SUV, Estate, Saloon.
        page: Results page number, starting at 1.
    """
    _log("search_autotrader", f"{make or ''} {model or ''} page {page}")
    return _json(autotrader.search(
        postcode, make, model, radius_miles, price_min, price_max, year_min, year_max,
        mileage_max, fuel_type, transmission, body_type, page,
    ))


@beta_tool
def get_autotrader_listing(advert_id: str) -> str:
    """Get full details of one AutoTrader listing: specs, owners, service history, MOT
    expiry, seller, description, and AutoTrader's free basic history check (stolen,
    scrapped, imported, exported, written off).

    Args:
        advert_id: The advert_id from search_autotrader.
    """
    _log("get_autotrader_listing", advert_id)
    data = autotrader.listing(advert_id)
    data.pop("_photo_urls")
    return _json(data)


@beta_tool
def view_autotrader_photos(advert_id: str, count: int = 3) -> list:
    """Look at photos of an AutoTrader listing, e.g. to read the number plate when the
    seller hasn't blurred it. Front and rear shots are usually first.

    Args:
        advert_id: The advert_id from search_autotrader.
        count: Number of photos to view (1-6).
    """
    _log("view_autotrader_photos", advert_id)
    urls = autotrader.listing(advert_id)["_photo_urls"][: max(1, min(count, 6))]
    blocks = []
    for url in urls:
        r = web.http_get(url)
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": r.headers.get("content-type", "image/jpeg"),
                "data": base64.b64encode(r.content).decode(),
            },
        })
    return blocks or [{"type": "text", "text": "This listing has no photos."}]


@beta_tool
def search_toyota_approved_used(
    model: str | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    mileage_max: int | None = None,
    fuel_type: str | None = None,
    transmission: str | None = None,
    max_results: int = 20,
) -> str:
    """Search Toyota UK's official Approved Used stock (Toyota dealers only, nationwide,
    cheapest first). Results include the registration plate and dealer location.

    Args:
        model: Toyota model, e.g. "Yaris", "Yaris Cross", "Corolla", "C-HR", "RAV4".
        price_min: Minimum price in GBP.
        price_max: Maximum price in GBP.
        year_min: Earliest registration year.
        year_max: Latest registration year.
        mileage_max: Maximum mileage.
        fuel_type: Petrol, Diesel, Hybrid, Plug-in hybrid, or Electric.
        transmission: Automatic or Manual.
        max_results: Maximum number of cars to return (up to 50).
    """
    _log("search_toyota_approved_used", model or "")
    return _json(toyota.search(
        model, price_min, price_max, year_min, year_max, mileage_max, fuel_type,
        transmission, min(max_results, 50),
    ))


@beta_tool
def get_free_vehicle_report(registration: str) -> str:
    """Get the free Car Owl vehicle report for a UK registration: DVLA tax and export
    status, V5C dates, full MOT history with every failure and advisory, and recorded
    mileage at each MOT. Does not cover finance, theft or write-off records.

    Args:
        registration: UK number plate, e.g. "CK19 UEJ".
    """
    reg = carowl.normalise_reg(registration)
    if reg not in _state["reports_run"] and len(_state["reports_run"]) >= _state["max_reports"]:
        return (f"Report limit reached ({_state['max_reports']} per run, set max_reports in "
                "requirements.yaml). Use the reports you already have.")
    _log("get_free_vehicle_report", reg)
    _state["reports_run"].add(reg)
    return carowl.free_report(reg)


@beta_tool
def fetch_page(url: str) -> str:
    """Read the visible text of a web page. Only works for sites listed in allowed_sites;
    use it for sites the user added there, or for pages the other tools don't cover.

    Args:
        url: Full https:// URL.
    """
    _log("fetch_page", url)
    text, method = web.fetch_text(url)
    return f"(fetched via {method})\n{text[:30_000]}"


ALL_TOOLS = [
    search_autotrader,
    get_autotrader_listing,
    view_autotrader_photos,
    search_toyota_approved_used,
    get_free_vehicle_report,
    fetch_page,
]
