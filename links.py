"""Check every link in a report: on an allowed site, reachable, and still the same page.

Sold AutoTrader adverts redirect to the search page with HTTP 200, so a status check
alone isn't enough. A link only counts as valid if the page it lands on still
contains the listing's ID.
"""

import re
from urllib.parse import urlparse

import httpx

from sites import web

_URL = re.compile(r"https?://[^\s<>()\[\]\"'`|]+")
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{10,}", re.I)


def extract_urls(markdown: str) -> list[str]:
    return list(dict.fromkeys(u.rstrip(".,;:*_") for u in _URL.findall(markdown)))


def check(url: str) -> dict:
    result = {"url": url, "valid": False, "status": None, "reason": ""}
    try:
        web.ensure_allowed(url)
    except web.SiteNotAllowed:
        result["reason"] = "not an allowed site"
        return result
    try:
        r = web.http_get(url)
    except httpx.HTTPStatusError as e:
        result["status"] = e.response.status_code
        result["reason"] = "listing removed or page not found" if e.response.status_code == 404 else "HTTP error"
        return result
    except httpx.HTTPError as e:
        result["reason"] = f"unreachable ({type(e).__name__})"
        return result

    result["status"] = r.status_code
    listing_id = _ID.search(urlparse(url).path)
    if listing_id and listing_id.group(0) not in str(r.url):
        result["reason"] = "listing no longer available (redirected away)"
        return result
    result["valid"] = True
    return result


def check_report(markdown: str) -> tuple[str, list[dict]]:
    """Return the report with dead links marked inline, plus a summary section,
    and the per-link results."""
    results = [check(u) for u in extract_urls(markdown)]
    for r in results:
        if not r["valid"]:
            # Mark after "(url)" so Markdown links still render, or after a bare url.
            pattern = rf"\({re.escape(r['url'])}\)|(?<!\(){re.escape(r['url'])}(?=[.,;:*_]*(?:[\s<>()\[\]|]|$))"
            note = f" ⚠️ LINK INVALID: {r['reason']}"
            markdown = re.sub(pattern, lambda m: m.group(0) + note, markdown)

    lines = ["", "## Link check", ""]
    if not results:
        lines.append("No links found in the report.")
    for r in results:
        mark = "✅" if r["valid"] else f"❌ {r['reason']}"
        lines.append(f"- {mark}: {r['url']}")
    return markdown + "\n" + "\n".join(lines) + "\n", results
