"""HTTP API for the car search agent.

Start:  .venv/bin/uvicorn server:app --port 8000
Docs:   http://localhost:8000/docs  (try every endpoint from the browser)
"""

import copy
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

import agent
from sites import toyota, web

CONFIG_PATH = Path(__file__).parent / "requirements.yaml"

app = FastAPI(
    title="Car Search Agent",
    description="Searches the allowed UK car sites for cars matching your requirements, "
    "runs free vehicle reports, and returns a shortlist with every link checked.",
)

# One worker: the Playwright browser belongs to the thread that started it, and one
# search at a time keeps traffic to the sites light.
_executor = ThreadPoolExecutor(max_workers=1)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


class SearchOverrides(BaseModel):
    """Anything left out is taken from requirements.yaml."""

    engine: Literal["plain", "ai"] | None = Field(None, description="plain = no API key needed; ai = Claude")
    search: dict | None = Field(None, examples=[{"model": "Yaris", "price_max": 8500, "mileage_max": 80000}])
    must_have: list[str] | None = None
    nice_to_have: list[str] | None = None
    shortlist_size: int | None = Field(None, ge=1, le=20)
    shortlist_order: str | None = Field(None, examples=["price, lowest first"])


def _config_with(overrides: SearchOverrides | None) -> dict:
    config = agent.load_config(CONFIG_PATH)
    if overrides:
        data = overrides.model_dump(exclude_none=True)
        config["search"] = {**(config.get("search") or {}), **data.pop("search", {})}
        config.update(data)
    return config


def _run_job(job_id: str, config: dict) -> None:
    job = _jobs[job_id]

    def log(line: str) -> None:
        job["progress"].append(f"{datetime.now():%H:%M:%S} {line}")

    job.update(status="running", started_at=datetime.now().isoformat(timespec="seconds"))
    try:
        result = agent.run_search(config, log)
        job.update(status="done", **result)
    except Exception as e:
        job.update(status="failed", error=agent.friendly_error(e))
    job["finished_at"] = datetime.now().isoformat(timespec="seconds")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


@app.post("/api/searches", status_code=202, summary="Start a car search")
def start_search(overrides: SearchOverrides | None = None):
    """Starts a search in the background using requirements.yaml (plus any overrides).
    A search takes several minutes; poll the returned status_url until status is
    "done" or "failed"."""
    config = _config_with(overrides)
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "requirements": copy.deepcopy({k: v for k, v in config.items() if k != "allowed_sites"}),
            "progress": [],
        }
    _executor.submit(_run_job, job_id, config)
    return {"id": job_id, "status": "queued", "status_url": f"/api/searches/{job_id}"}


@app.get("/api/searches", summary="List searches started since the server began")
def list_searches():
    return [
        {k: j.get(k) for k in ("id", "status", "created_at", "finished_at", "report_file", "error")}
        for j in _jobs.values()
    ]


@app.get("/api/searches/{job_id}", summary="Search status, progress, report and link check")
def get_search(job_id: str):
    """When status is "done": `report` is the Markdown report, `links` lists every link
    in it with `valid` true/false, and `report_url` serves the saved file."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "No search with that id")
    out = dict(job)
    if job.get("report_file"):
        out["report_url"] = f"/api/reports/{job['report_file']}"
        out["valid_links"] = sum(link["valid"] for link in job["links"])
        out["invalid_links"] = sum(not link["valid"] for link in job["links"])
    return out


@app.get("/api/reports", summary="List saved reports (newest first)")
def list_reports():
    files = sorted(agent.REPORTS_DIR.glob("*.md"), reverse=True) if agent.REPORTS_DIR.exists() else []
    return [{"name": f.name, "url": f"/api/reports/{f.name}", "size_bytes": f.stat().st_size} for f in files]


@app.get("/api/reports/{name}", response_class=PlainTextResponse, summary="Get a saved report (Markdown)")
def get_report(name: str):
    path = (agent.REPORTS_DIR / name).resolve()
    if path.parent != agent.REPORTS_DIR.resolve() or path.suffix != ".md" or not path.exists():
        raise HTTPException(404, "Report not found")
    return PlainTextResponse(path.read_text(), media_type="text/markdown; charset=utf-8")


# A page to probe for each known site; other sites in allowed_sites get their home page.
_SITE_PROBES = {
    "autotrader.co.uk": "https://www.autotrader.co.uk/",
    "toyota.co.uk": "https://www.toyota.co.uk/used-cars",
    "carowl.co.uk": "https://carowl.co.uk/free-car-check",
}


def _probe(domain: str) -> dict:
    start = time.monotonic()
    result = {"site": domain, "reachable": False}
    try:
        if domain == "toyota-europe.com":
            result["detail"] = f"{toyota._query([], count=1)['totalResultCount']} cars in Toyota approved-used stock"
        elif domain == "atcdn.co.uk":
            result["detail"] = "AutoTrader photo server: only used for listing photos, not probed"
            result["reachable"] = None
            return result
        else:
            url = _SITE_PROBES.get(domain, f"https://www.{domain}/")
            result["detail"] = f"HTTP {web.http_get(url).status_code} from {url}"
        result["reachable"] = True
    except (httpx.HTTPError, web.SiteNotAllowed, KeyError, ValueError) as e:
        result["detail"] = f"{type(e).__name__}: {e}"[:300]
    result["ms"] = round((time.monotonic() - start) * 1000)
    return result


@app.get("/api/sites", summary="Check every allowed site is reachable")
def check_sites():
    """Quick health check of each site in allowed_sites. Doesn't use Claude, so it's free."""
    config = agent.load_config(CONFIG_PATH)
    web.set_allowed_sites(config["allowed_sites"])
    return [_probe(d.lower().removeprefix("www.")) for d in config["allowed_sites"]]
