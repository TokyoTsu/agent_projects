# Car search agent

Searches UK used-car sites for cars matching your requirements, pulls a free vehicle
report for the candidates, and writes a shortlist to `reports/`, with every link checked.

It has two engines, chosen by `engine:` in [requirements.yaml](requirements.yaml):

| | `plain` (default) | `ai` |
|---|---|---|
| Needs an API key / costs money | No | Yes (Anthropic API, Claude Opus) |
| Requirements | Fixed rules under `rules:` | Plain English under `must_have:` / `nice_to_have:` |
| Number plates for AutoTrader cars | Only if written in the description | Description, or read from photos |
| Report | Fixed template | Written by Claude, with seller questions |
| Speed | ~1–2 minutes | Several minutes |

## Setup (once)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
export ANTHROPIC_API_KEY=sk-ant-...      # only for the ai engine (console.anthropic.com)
```

## Use

Edit [requirements.yaml](requirements.yaml) (budget, model, mileage, rules, etc.), then
either run it once from the terminal:

```bash
.venv/bin/python agent.py                  # uses `engine` from requirements.yaml
.venv/bin/python agent.py --engine ai      # or pick one for this run
```

or start the API server with `./start.sh` and use the endpoints below. Interactive docs,
where you can try each one, are at http://localhost:8000/docs.

| Method | Endpoint | What it does |
|---|---|---|
| GET | `/api/sites` | Checks every site in `allowed_sites` is reachable |
| POST | `/api/searches` | Starts a search. Optional JSON body overrides requirements.yaml |
| GET | `/api/searches/{id}` | Status (`queued` / `running` / `done` / `failed`), progress, report, link check |
| GET | `/api/searches` | All searches since the server started |
| GET | `/api/reports` | Saved reports, newest first |
| GET | `/api/reports/{name}` | One saved report as Markdown |

```bash
curl localhost:8000/api/sites
curl -X POST localhost:8000/api/searches                     # use requirements.yaml as-is
curl -X POST localhost:8000/api/searches -H 'content-type: application/json' \
     -d '{"engine": "plain", "search": {"price_max": 9000}, "shortlist_size": 3}'
curl localhost:8000/api/searches/<id>                         # poll until "done"
```

Reports are saved in `reports/`. Before saving, every link in the report is checked
([links.py](links.py)): it must be on an allowed site, load, and still show the same
listing. A sold AutoTrader car redirects to the search page, and that counts as
invalid. Dead links are marked `⚠️ LINK INVALID` in the text, and the report ends with
a **Link check** list. The API response also has `links`, `valid_links` and
`invalid_links`.

## How the plain engine works

[plain.py](plain.py) runs the same steps every time:

1. Search AutoTrader, cheapest first (up to 4 pages), plus Toyota Approved Used when
   the make is Toyota. Re-check the price, since AutoTrader mixes in promoted adverts
   that ignore the filters.
2. Check cars cheapest first. For each one, read the AutoTrader listing (owners, service
   history, MOT, AutoTrader's basic history check). If the plate is known (Toyota
   results, or written in the AutoTrader description), run the Car Owl report and parse
   its MOT history.
3. Score it against `rules:`. A ❌ on any `must` rule rejects the car. A ❓ (couldn't
   verify with free data) is kept or rejected depending on `allow_unverified`.
4. Stop when `shortlist_size` cars pass, then write the report.

| Rule | Checked using |
|---|---|
| `no_write_off` | AutoTrader "Cat S/N/C/D" badge and its basic history check |
| `valid_mot` | Car Owl MOT status, else the listing's MOT expiry, else age under 3 years |
| `no_serious_mot_failures_years` | Car Owl MOT tests that failed with major or dangerous defects |
| `consistent_mileage` | Car Owl mileage at each MOT never goes down, and the advert's mileage isn't below the last MOT |
| `max_owners`, `full_service_history`, `dealer_with_warranty` | AutoTrader listing / Toyota warranty field |

## How the ai engine works

`agent.py` gives Claude your requirements and these tools ([tools.py](tools.py)).
Claude decides which to call and in what order:

| Tool | Source | How it's fetched |
|---|---|---|
| `search_autotrader` | AutoTrader search results | Real browser (the page loads listings with JavaScript) |
| `get_autotrader_listing` | AutoTrader listing + its free basic history check (stolen / scrapped / write-off / import / export) | Plain HTTP |
| `view_autotrader_photos` | Listing photos, so Claude can read an unblurred plate | Plain HTTP |
| `search_toyota_approved_used` | Toyota UK Approved Used stock (includes plates) | Plain HTTP (Toyota's own JSON API) |
| `get_free_vehicle_report` | Car Owl free check: full DVSA MOT history with every advisory and failure, mileage per MOT, DVLA tax / export / V5C | Real browser |
| `fetch_page` | Any page on a site in `allowed_sites` | HTTP first, browser if blocked or empty |

Both engines can only reach sites in `allowed_sites`, enforced in
[sites/web.py](sites/web.py). In the ai engine, adding a domain there lets Claude
read it with `fetch_page`. The plain engine only knows the built-in sites.

## Limitations

- **AutoTrader hides number plates.** Most AutoTrader cars get no MOT report, so their
  MOT and mileage checks show ❓. AutoTrader's own basic check still covers write-offs
  and theft for every listing. Ask the seller for the plate and check it on Car Owl.
- **The free reports don't cover outstanding finance**, and Car Owl's free check doesn't
  cover theft or write-off records. Buy a full check before you pay for a car.
- **The gov.uk MOT checker blocks automated browsers**, so MOT data comes from Car Owl,
  which shows the same DVSA records.
- Scrapers break when sites change their layout. If something starts failing, the code
  for each site is in [sites/](sites/).
- `max_reports` limits the slow Car Owl lookups per run. The ai engine also costs money
  on your Anthropic account each run.
