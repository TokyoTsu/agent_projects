"""Car Owl free vehicle check (carowl.co.uk).

The free check covers DVLA data (tax, export, V5C dates) and the full DVSA MOT
history with mileage. It does NOT cover finance, theft or write-off records; those
are paid. The site is a Blazor app, so it needs a real browser.
"""

import re

from .web import browser, ensure_allowed

URL = "https://carowl.co.uk/free-car-check"
# Everything after these markers is upsell for the paid report.
_UPSELL_MARKERS = ("⚠️ 1 in 3", "Basic Report\n£")


def normalise_reg(reg: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", reg.upper())


def free_report(registration: str) -> str:
    reg = normalise_reg(registration)
    if not 2 <= len(reg) <= 8:
        raise ValueError(f"'{registration}' doesn't look like a UK registration")
    ensure_allowed(URL)

    page = browser.new_page()
    try:
        page.goto(URL, wait_until="domcontentloaded")
        box = page.locator('input[name="_formModel.Input"]').first
        box.fill(reg)
        box.press("Enter")
        page.wait_for_url("**/r/**")
        page.get_by_text("MOT Count").first.wait_for()
        text = page.inner_text("body")
        url = page.url
        mot_details = _mot_defect_details(page)
    finally:
        page.close()

    start = text.find("AI Overview")
    text = text[start if start >= 0 else 0:]
    ends = [i for m in _UPSELL_MARKERS if (i := text.find(m)) > 0]
    if ends:
        text = text[:min(ends)]
    details = "\n\n".join(mot_details) or "No advisories or failures recorded."
    return f"Source: {url}\n\n{text.strip()}\n\nMOT ADVISORY AND FAILURE DETAILS\n\n{details}"


def _mot_defect_details(page) -> list[str]:
    """Each MOT test with advisories or failures opens a modal with the details."""
    entries = page.locator("button.history").filter(has_text=re.compile(r"advisor|major|minor|dangerous", re.I))
    details = []
    for i in range(entries.count()):
        try:
            entries.nth(i).click(timeout=5_000)
            modal = page.locator(".generic-modal").filter(has_text="Test result").first
            modal.wait_for(timeout=5_000)
            details.append(modal.inner_text().replace("MOT History\n×\n", "").strip())
            modal.locator("button.bm-close").click(timeout=3_000)
            modal.wait_for(state="hidden", timeout=5_000)
        except Exception as e:
            details.append(f"(could not open MOT entry {i + 1}: {type(e).__name__})")
            page.keyboard.press("Escape")
    return details


_KM_PER_MILE = 1.609344
_MOT_LINE = re.compile(r"^(\d{2} \w{3} \d{4}) - ([\d,]+) (mi|km)$")


def parse_report(report: str) -> dict:
    """Turn free_report() text into data: spec fields, tax/MOT/export status, and
    the MOT tests (oldest first) with result, mileage in miles and defect summary."""
    fields = {}
    for line in report.splitlines():
        if "\t" in line:
            key, _, value = line.partition("\t")
            fields[key.rstrip(" ?")] = value.strip()

    history = report.split("MOT History", 1)[1].split("Mileage Changes", 1)[0] if "MOT History" in report else ""
    tests, result, summary = [], None, ""
    for line in (l.strip() for l in history.splitlines()):
        if line in ("PASSED", "FAILED"):
            result, summary = line, ""
        elif line.startswith("- "):
            summary = line[2:]
        elif (m := _MOT_LINE.match(line)) and result:
            mileage = int(m.group(2).replace(",", ""))
            if m.group(3) == "km":
                mileage = round(mileage / _KM_PER_MILE)
            tests.append({"date": m.group(1), "result": result, "mileage": mileage, "defects": summary})
            result = None
    tests.reverse()

    head = report.split("Specification", 1)[0]
    return {
        "make": fields.get("Make"),
        "model": fields.get("Model"),
        "colour": fields.get("Colour"),
        "first_registered": fields.get("First Registration"),
        "mot_status": fields.get("MOT Status"),
        "mot_due": fields.get("MOT Due"),
        "tax": fields.get("Tax"),
        "taxed": "\nTaxed\n" in head,
        "exported": "Not Exported" not in head,
        "mot_tests": tests,
    }
