"""Plain engine: finds and checks cars with fixed rules, no AI and no API key.

Steps: search AutoTrader (cheapest first) and, for Toyota, Toyota Approved Used ->
check candidates cheapest first against `rules` in requirements.yaml, running the free
Car Owl report when the plate is known -> stop when the shortlist is full -> write a
Markdown report.
"""

import re
from datetime import date, datetime

from sites import autotrader, carowl, toyota, web

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"
ICONS = {PASS: "✅", FAIL: "❌", UNKNOWN: "❓"}
MAX_AUTOTRADER_PAGES = 4
MAX_CARS_TO_CHECK = 30
# Current-style UK plate, e.g. "AB12 CDE". Older formats are too ambiguous to find in free text.
PLATE = re.compile(r"\b([A-Z]{2}\d{2}\s?[A-Z]{3})\b")
WRITE_OFF_BADGE = re.compile(r"\bCat\s?[A-Z]\b", re.I)


def _allowed(domain: str) -> bool:
    try:
        web.ensure_allowed(f"https://www.{domain}/")
        return True
    except web.SiteNotAllowed:
        return False


def _price(text) -> int | None:
    digits = re.sub(r"[^\d]", "", str(text or ""))
    return int(digits) if digits else None


def _mileage_from_badges(badges: list[str]) -> int | None:
    for b in badges:
        if b.endswith("miles"):
            return _price(b)
    return None


# ---------- search ----------

def find_candidates(s: dict, log) -> tuple[list[dict], dict]:
    cars, counts = [], {}
    in_budget = lambda p: p is not None and (not s.get("price_max") or p <= s["price_max"]) \
        and (not s.get("price_min") or p >= s["price_min"])

    if _allowed("autotrader.co.uk"):
        seen = set()
        for page in range(1, MAX_AUTOTRADER_PAGES + 1):
            log(f"→ AutoTrader search, page {page}")
            res = autotrader.search(
                s["postcode"], s.get("make"), s.get("model"), s.get("radius_miles"), s.get("price_min"),
                s.get("price_max"), s.get("year_min"), s.get("year_max"), s.get("mileage_max"),
                s.get("fuel_type"), s.get("transmission"), s.get("body_type"), page, sort="price-asc",
            )
            counts["AutoTrader"] = res["total_results"]
            for l in res["listings"]:
                price = _price(l["price"])
                # Promoted adverts can fall outside the filters, so re-check the budget.
                if l["advert_id"] in seen or not in_budget(price):
                    continue
                seen.add(l["advert_id"])
                cars.append({
                    "source": "AutoTrader", "id": l["advert_id"], "title": l["title"], "variant": l["variant"],
                    "price": price, "year": l["year"], "mileage": _mileage_from_badges(l["badges"]),
                    "location": l["location"], "url": l["url"], "badges": l["badges"],
                    "seller_type": l["seller_type"], "registration": None,
                })
            if page >= res["total_pages"]:
                break

    make = (s.get("make") or "").lower()
    if _allowed("toyota-europe.com") and make in ("", "toyota"):
        log("→ Toyota Approved Used search")
        res = toyota.search(s.get("model"), s.get("price_min"), s.get("price_max"), s.get("year_min"),
                            s.get("year_max"), s.get("mileage_max"), s.get("fuel_type"), s.get("transmission"), 50)
        if "error" in res:
            log(f"  Toyota search skipped: {res['error']}")
        else:
            counts["Toyota Approved Used"] = len(res["cars"])
            for c in res["cars"]:
                cars.append({
                    "source": "Toyota Approved Used", "id": c["id"], "title": f"Toyota {c['model']}",
                    "variant": c["variant"], "price": c["price"], "year": int(c["first_registered"][:4]),
                    "mileage": c["mileage"], "location": c["dealer_location"], "url": c["url"], "badges": [],
                    "seller_type": "Toyota dealer", "registration": c["registration"],
                    "dealer": c["dealer"], "warranty": c["warranty"],
                })

    cars.sort(key=lambda c: c["price"])
    return cars, counts


# ---------- checks ----------

def _vehicle_report(car: dict, state: dict, log) -> dict | None:
    """Run the Car Owl report if we know the plate and haven't hit max_reports.
    Discards it if the make/model don't match (e.g. a plate misread from the description)."""
    reg = car.get("registration")
    if not reg or not _allowed("carowl.co.uk"):
        return None
    if state["reports_run"] >= state["max_reports"]:
        car["report_note"] = f"not run: max_reports ({state['max_reports']}) reached"
        return None
    state["reports_run"] += 1
    log(f"→ Car Owl report for {reg}")
    try:
        text = carowl.free_report(reg)
    except Exception as e:
        car["report_note"] = f"report failed ({type(e).__name__})"
        return None
    data = carowl.parse_report(text)
    # Same make, and same model or same first-registration year (imports are often
    # registered under their home-market name, e.g. a Yaris as a "VITZ").
    want_make, want_model = car["title"].split(" ", 1)[0].lower(), car["title"].lower()
    same_model = (data["model"] or "").lower() in want_model
    same_year = bool(car["year"]) and str(car["year"]) in (data["first_registered"] or "")
    if (data["make"] or "").lower() != want_make or not (same_model or same_year):
        car["report_note"] = (f"plate {reg} belongs to a {data['make']} {data['model']} first registered "
                              f"{data['first_registered']}, which doesn't match this car; report ignored")
        car["registration"] = None
        return None
    if not same_model:
        car["report_note"] = f"registered as a {data['make']} {data['model']}"
    data["url"] = text.splitlines()[0].removeprefix("Source: ").split("#")[0]
    return data


def _years_ago(test_date: str) -> float:
    return (date.today() - datetime.strptime(test_date, "%d %b %Y").date()).days / 365.25


def evaluate(car: dict, rules: dict, state: dict, log) -> list[tuple[str, str, str, bool]]:
    """Returns (check name, status, detail, is_must) for each configured rule."""
    listing = None
    if car["source"] == "AutoTrader":
        log(f"→ AutoTrader listing {car['id']}")
        listing = autotrader.listing(car["id"])
        car["listing"] = listing
        if not car["registration"] and (m := PLATE.search((listing["description"] or "").upper())):
            car["registration"] = m.group(1).replace(" ", "")
    report = _vehicle_report(car, state, log)
    car["report"] = report
    must, nice = rules.get("must") or {}, rules.get("nice") or {}
    results = []

    if must.get("no_write_off"):
        cat = next((b for b in car["badges"] if WRITE_OFF_BADGE.search(b)), None)
        basic = (listing or {}).get("autotrader_basic_history_check", {}).get("checks", [])
        write_off = next((c for c in basic if "written off" in c.lower() or "write-off" in c.lower()), None)
        if cat:
            results.append(("No write-off", FAIL, f"AutoTrader lists it as {cat}", True))
        elif write_off and write_off.endswith(": PASSED"):
            results.append(("No write-off", PASS, f"AutoTrader basic check: {write_off}", True))
        elif write_off:
            results.append(("No write-off", FAIL, f"AutoTrader basic check: {write_off}", True))
        else:
            results.append(("No write-off", UNKNOWN, "not covered by the free checks for this source", True))

    if must.get("valid_mot"):
        at_mot = (listing or {}).get("mot") or ""
        if report and report["mot_status"]:
            ok = report["mot_status"].lower() == "valid"
            results.append(("Valid MOT", PASS if ok else FAIL, f"{report['mot_status']}, due {report['mot_due']}", True))
        elif "valid until" in at_mot.lower():
            results.append(("Valid MOT", PASS, at_mot + " (seller-supplied)", True))
        elif car["year"] and date.today().year - car["year"] < 3:
            results.append(("Valid MOT", PASS, "under 3 years old, no MOT needed yet", True))
        else:
            results.append(("Valid MOT", UNKNOWN, "no vehicle report (plate unknown)", True))

    if years := must.get("no_serious_mot_failures_years"):
        if report:
            serious = [t for t in report["mot_tests"] if t["result"] == "FAILED" and _years_ago(t["date"]) <= years
                       and re.search(r"major|dangerous", t["defects"], re.I)]
            detail = "; ".join(f"{t['date']}: {t['defects']}" for t in serious) or f"none in the last {years} years"
            results.append((f"No serious MOT failures ({years}y)", FAIL if serious else PASS, detail, True))
        else:
            results.append((f"No serious MOT failures ({years}y)", UNKNOWN, "no vehicle report (plate unknown)", True))

    if must.get("consistent_mileage"):
        if report and report["mot_tests"]:
            miles = [t["mileage"] for t in report["mot_tests"]]
            drops = [(a, b) for a, b in zip(miles, miles[1:]) if b < a]
            if car["mileage"] and car["mileage"] + 500 < miles[-1]:
                drops.append((miles[-1], car["mileage"]))
            detail = (", ".join(f"{a:,} → {b:,}" for a, b in drops) if drops
                      else f"rises steadily over {len(miles)} MOTs, last {miles[-1]:,} mi")
            results.append(("Consistent mileage", FAIL if drops else PASS, detail, True))
        else:
            results.append(("Consistent mileage", UNKNOWN, "no MOT mileage history (plate unknown)", True))

    if (max_owners := nice.get("max_owners")) is not None:
        owners = _price((listing or {}).get("owners"))
        if owners is None:
            results.append((f"≤ {max_owners} owners", UNKNOWN, "not stated", False))
        else:
            label = f"{owners} owner{'s' if owners != 1 else ''}"
            results.append((f"≤ {max_owners} owners", PASS if owners <= max_owners else FAIL, label, False))

    if nice.get("full_service_history"):
        history = (listing or {}).get("service_history") or ""
        if "full" in history.lower():
            results.append(("Full service history", PASS, history, False))
        elif history and "not provided" not in history.lower():
            results.append(("Full service history", FAIL, history, False))
        else:
            results.append(("Full service history", UNKNOWN, history or "not stated", False))

    if nice.get("dealer_with_warranty"):
        if car.get("warranty"):
            results.append(("Dealer with warranty", PASS, f"{car['dealer']}, warranty {car['warranty']}", False))
        elif (car["seller_type"] or "").upper() == "PRIVATE" or (listing or {}).get("seller", {}).get("type") == "private":
            results.append(("Dealer with warranty", FAIL, "private seller", False))
        else:
            results.append(("Dealer with warranty", UNKNOWN, "dealer, warranty not stated in listing", False))

    return results


# ---------- report ----------

def _row(car: dict) -> str:
    mileage = f"{car['mileage']:,}" if car["mileage"] else "?"
    return (f"{car['title']} {car['variant'] or ''} | £{car['price']:,} | {car['year'] or '?'} | {mileage} | "
            f"{car['location'] or '?'} | {car['source']} | [view]({car['url']})")


def _car_section(n: int, car: dict, checks: list) -> str:
    out = [f"### {n}. {car['title']} {car['variant'] or ''} — £{car['price']:,}", "", f"Listing: {car['url']}", ""]
    out += ["| Check | Type | Result | Detail |", "|---|---|---|---|"]
    out += [f"| {name} | {'must' if is_must else 'nice'} | {ICONS[st]} | {detail} |" for name, st, detail, is_must in checks]

    listing, report = car.get("listing"), car.get("report")
    if listing:
        basic = listing["autotrader_basic_history_check"]
        flags = [c.removesuffix(": FAILED") for c in basic.get("checks", []) if c.endswith(": FAILED")]
        if flags:
            out += ["", f"**⚠️ Red flag:** {'; '.join(flags)} (AutoTrader basic check)"]
        out += ["", f"**AutoTrader basic check:** {basic.get('summary') or 'not available'}", ""]
        out += [f"- {c}" for c in basic.get("checks", [])]
        seller = listing["seller"]
        who = "Private seller" if seller["type"] == "private" else f"{seller['name']} ({seller['type']})"
        out += ["", f"**Seller:** {who}, {seller['town']}"]
    if report:
        note = f" — {car['report_note']}" if car.get("report_note") else ""
        out += ["", f"**Vehicle report** ({car['registration']}{note}): {report['url']}",
                f"- {report['make']} {report['model']}, {report['colour']}, first registered {report['first_registered']}",
                f"- MOT: {report['mot_status']}, due {report['mot_due']}. Tax: {report['tax']}. "
                f"{'Exported' if report['exported'] else 'Not exported'}.",
                "- Recent MOTs:"]
        out += [f"  - {t['date']}: {t['result']}, {t['mileage']:,} mi{', ' + t['defects'] if t['defects'] else ''}"
                for t in report["mot_tests"][-5:]]
    else:
        out += ["", f"**Vehicle report:** {car.get('report_note') or 'not run (registration not shown in the listing)'}. "
                "Ask the seller for the registration and check it at https://carowl.co.uk/free-car-check"]
    return "\n".join(out)


def render(config, counts, shortlist, rejected, checked) -> str:
    s, size = config["search"], config.get("shortlist_size", 3)
    wanted = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in s.items() if v not in (None, ""))
    found = ", ".join(f"{n} on {src}" for src, n in counts.items()) or "nothing"
    lines = [
        f"# Car search report — {datetime.now():%d %b %Y %H:%M}",
        "",
        f"_Plain engine (fixed rules, no AI). Search: {wanted}._",
        "",
        "## Summary",
        "",
        f"Found {found}. Checked {checked} cars, cheapest first. {len(shortlist)} passed every must-have "
        f"check (shortlist limit {size}).",
    ]
    if shortlist:
        top = shortlist[0][0]
        lines.append(f"Cheapest passing car: {top['title']} {top['variant'] or ''} at £{top['price']:,} ({top['source']}).")
    lines += ["", "## Shortlist (price, lowest first)", ""]
    if shortlist:
        lines += ["| # | Car | Price | Year | Mileage | Location | Source | Link |", "|---|---|---|---|---|---|---|---|"]
        lines += [f"| {i} | {_row(c)} |" for i, (c, _) in enumerate(shortlist, 1)]
        lines += ["", "✅ passed · ❌ failed · ❓ couldn't verify with free data", ""]
        lines += [_car_section(i, c, chk) + "\n" for i, (c, chk) in enumerate(shortlist, 1)]
    else:
        lines.append("No car passed every must-have check. Try widening the search in requirements.yaml.")
    lines += ["", "## Rejected", ""]
    if rejected:
        lines += ["| Car | Price | Reason | Link |", "|---|---|---|---|"]
        lines += [f"| {c['title']} {c['variant'] or ''} | £{c['price']:,} | {reason} | [view]({c['url']}) |"
                  for c, reason in rejected[:20]]
    else:
        lines.append("None.")
    lines += [
        "", "## Not checked", "",
        "- **Outstanding finance**: not in any free check. Buy a full history check before paying.",
        "- **Stolen / write-off registers**: only AutoTrader's basic check covers these; Toyota Approved Used "
        "cars and Car Owl's free report don't.",
        "- **Cars with ❓**: the listing didn't show the registration, so there was no MOT history. Ask the seller.",
    ]
    return "\n".join(lines)


def run(config: dict, log=print) -> str:
    web.set_allowed_sites(config["allowed_sites"])
    rules = config.get("rules") or {}
    allow_unverified = rules.get("allow_unverified", True)
    size = config.get("shortlist_size", 3)
    state = {"reports_run": 0, "max_reports": config.get("max_reports", 8)}

    candidates, counts = find_candidates(config["search"], log)
    log(f"→ {len(candidates)} cars in budget; checking cheapest first")
    shortlist, rejected, checked = [], [], 0
    for car in candidates:
        if len(shortlist) >= size or checked >= MAX_CARS_TO_CHECK:
            break
        checked += 1
        try:
            checks = evaluate(car, rules, state, log)
        except Exception as e:
            rejected.append((car, f"couldn't check this listing ({type(e).__name__})"))
            continue
        failed = [f"{n}: {d}" for n, st, d, must in checks if must and st == FAIL]
        unverified = [n for n, st, d, must in checks if must and st == UNKNOWN]
        if failed:
            rejected.append((car, "; ".join(failed)))
        elif unverified and not allow_unverified:
            rejected.append((car, "couldn't verify: " + ", ".join(unverified)))
        else:
            shortlist.append((car, checks))
    return render(config, counts, shortlist, rejected, checked)
