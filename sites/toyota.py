"""Toyota UK Approved Used (used.toyota.co.uk).

The site's search widget calls a public JSON API on toyota-europe.com, which works
with plain HTTP. Unlike AutoTrader it includes each car's registration plate.
"""

import json
from functools import cache
from pathlib import Path

from .web import http_post_json

API = "https://usc-webcomponents.toyota-europe.com/v1/api/usedcars/results/gb/en?brand=toyota"
API_HEADERS = {"Referer": "https://used.toyota.co.uk/"}
DETAIL_URL = "https://www.toyota.co.uk/used-vehicles-in-stock/pdp.toyota-{model}-{id}"
MODEL_CACHE = Path(__file__).resolve().parent.parent / ".cache" / "toyota_models.json"

FUEL_CODES = {"petrol": "1", "diesel": "2", "electric": "3", "hybrid": "5", "plug-in hybrid": "6"}
TRANSMISSION_CODES = {"automatic": "AT", "manual": "MT"}
PAGE_SIZE = 50


def _query(filters: list[dict], offset: int = 0, count: int = PAGE_SIZE) -> dict:
    return http_post_json(API, {
        "uscEnv": "production",
        "filterContext": "used",
        "filters": filters,
        "offset": offset,
        "resultCount": count,
        "sortOrder": "cashAsc",
        "distributorCode": "94081",
    }, headers=API_HEADERS)


@cache
def model_codes() -> dict[str, str]:
    """Map model name (lowercase) to the API's model code, e.g. {"yaris": "YA"}.

    The API only exposes codes, so each code is resolved once by fetching one car
    with it, then cached on disk.
    """
    if MODEL_CACHE.exists():
        return json.loads(MODEL_CACHE.read_text())
    codes = [a["valueId"] for a in _query([], count=1)["aggregations"]["usedCarModel"]]
    names = {}
    for code in codes:
        results = _query([{"filterId": "usedCarModel", "valueIds": [code]}], count=1)["results"]
        if results:
            names[results[0]["product"]["model"]["description"].lower()] = code
    MODEL_CACHE.parent.mkdir(exist_ok=True)
    MODEL_CACHE.write_text(json.dumps(names, indent=2))
    return names


def search(
    model: str | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    mileage_max: int | None = None,
    fuel_type: str | None = None,
    transmission: str | None = None,
    max_results: int = 20,
) -> dict:
    filters = []
    if model:
        name = model.lower().removeprefix("toyota ")
        code = model_codes().get(name) or model_codes().get(f"toyota {name}")
        if not code:
            return {"error": f"Unknown Toyota model '{model}'.", "known_models": sorted(model_codes())}
        filters.append({"filterId": "usedCarModel", "valueIds": [code]})
    if year_min or year_max:
        filters.append({"filterId": "usedCarYear", "min": year_min or 1, "max": year_max or 2100})
    if mileage_max:
        filters.append({"filterId": "usedCarMileage", "min": 0, "max": mileage_max})
    if fuel_type:
        if fuel_type.lower() not in FUEL_CODES:
            return {"error": f"Unknown fuel_type '{fuel_type}'.", "valid": list(FUEL_CODES)}
        filters.append({"filterId": "usedCarFuelType", "valueIds": [FUEL_CODES[fuel_type.lower()]]})
    if transmission:
        if transmission.lower() not in TRANSMISSION_CODES:
            return {"error": f"Unknown transmission '{transmission}'.", "valid": list(TRANSMISSION_CODES)}
        filters.append({"filterId": "usedCarTransmission", "valueIds": [TRANSMISSION_CODES[transmission.lower()]]})

    # Price isn't a server-side filter; results are sorted cheapest first, so page
    # through until past the budget.
    cars, offset, total = [], 0, None
    while len(cars) < max_results:
        data = _query(filters, offset=offset)
        total = data["totalResultCount"]
        for r in data["results"]:
            price = r["price"]["sellingPriceInclVAT"]
            if price_max and price > price_max:
                return {"total_matching_before_price_filter": total, "cars": cars}
            if price_min and price < price_min:
                continue
            cars.append(_summarise(r))
            if len(cars) >= max_results:
                break
        offset += PAGE_SIZE
        if offset >= total:
            break
    return {"total_matching_before_price_filter": total, "cars": cars}


def _summarise(r: dict) -> dict:
    product, dealer = r["product"], r.get("dealer") or {}
    engine = product.get("engine") or {}
    address = dealer.get("address") or {}
    model = product["model"]["description"]
    return {
        "id": r["id"],
        "registration": r.get("licensePlate"),
        "model": model,
        "variant": product.get("versionName"),
        "price": r["price"]["sellingPriceInclVAT"],
        "mileage": r["mileage"]["value"],
        "first_registered": r["history"].get("registrationDate"),
        "fuel": engine.get("displayFuelType"),
        "transmission": ((product.get("transmission") or {}).get("transmissionType") or {}).get("description"),
        "colour": r.get("exteriorColour"),
        "warranty": (r.get("warranty") or {}).get("description"),
        "dealer": dealer.get("name"),
        "dealer_location": ", ".join(x for x in (address.get("city"), address.get("zip")) if x),
        "url": DETAIL_URL.format(model=model.lower().removeprefix("toyota ").replace(" ", "-"), id=r["id"]),
    }
