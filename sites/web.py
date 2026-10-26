"""Shared web access: site allowlist, plain HTTP first, real browser when needed."""

import atexit
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-GB,en;q=0.9"}

_allowed_sites: list[str] = []


class SiteNotAllowed(Exception):
    pass


def set_allowed_sites(domains: list[str]) -> None:
    _allowed_sites[:] = [d.lower().removeprefix("www.") for d in domains]


def ensure_allowed(url: str) -> None:
    """Raise unless the URL's host is one of the configured sites (or a subdomain)."""
    host = (urlparse(url).hostname or "").lower()
    if not any(host == d or host.endswith("." + d) for d in _allowed_sites):
        raise SiteNotAllowed(
            f"{host} is not in allowed_sites in requirements.yaml. "
            "Add it there if you want the agent to use it."
        )


def http_get(url: str, **kwargs) -> httpx.Response:
    ensure_allowed(url)
    r = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=30, **kwargs)
    r.raise_for_status()
    return r


def http_post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    ensure_allowed(url)
    r = httpx.post(url, json=payload, headers={**HEADERS, **(headers or {})}, timeout=30)
    r.raise_for_status()
    return r.json()


class _Browser:
    """One headless Chromium shared by all tools, started on first use."""

    def __init__(self):
        self._pw = None
        self._browser = None

    def new_page(self):
        if self._browser is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            atexit.register(self.close)
        page = self._browser.new_page(locale="en-GB", user_agent=USER_AGENT)
        page.set_default_timeout(45_000)
        return page

    def close(self):
        if self._browser is not None:
            try:
                self._browser.close()
                self._pw.stop()
            except Exception:
                pass  # at exit the connection may already be gone
            self._browser = None


browser = _Browser()


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def fetch_text(url: str, min_chars: int = 500) -> tuple[str, str]:
    """Return (visible text, method). Tries plain HTTP, falls back to a real browser
    when the page is blocked or is an empty JavaScript shell."""
    ensure_allowed(url)
    try:
        text = html_to_text(http_get(url).text)
        if len(text) >= min_chars:
            return text, "http"
    except httpx.HTTPError:
        pass

    page = browser.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass  # networkidle often never fires on ad-heavy pages; use what has loaded
    try:
        ensure_allowed(page.url)  # don't follow a redirect off the allowlist
        return page.inner_text("body"), "browser"
    finally:
        page.close()
