"""Car search agent: finds used cars matching requirements.yaml and checks each one's
free vehicle report. Run:  .venv/bin/python agent.py [path/to/requirements.yaml] [--engine plain|ai]
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import anthropic
import yaml

import links
import plain
import tools

MODEL = "claude-opus-5-5"
REPORTS_DIR = Path(__file__).parent / "reports"

SYSTEM_PROMPT = """\
You are a UK used-car buying assistant. Find cars for sale that meet the buyer's \
requirements, check each serious candidate's history, and recommend a shortlist.

How to work:
1. Search every relevant source. Use search_autotrader for all makes. When the make is \
Toyota (or the buyer is open to Toyota), also use search_toyota_approved_used. If the \
buyer added other sites to allowed_sites, use fetch_page on them. Fetch extra pages of \
results if the first page doesn't give enough good candidates.
2. Drop cars that clearly fail a must-have from the search data alone (e.g. a "Cat S" \
badge when the buyer wants no write-offs).
3. For promising candidates, call get_autotrader_listing for owners, service history, \
MOT expiry and AutoTrader's basic history check.
4. Run get_free_vehicle_report for your best candidates. It needs the registration \
plate: Toyota Approved Used results include it; for AutoTrader, look for it in the \
listing description, or call view_autotrader_photos and read it from an unblurred \
plate. Confirm the report's make, model, colour and year match the listing; if they \
don't, you misread the plate, so discard that report. In each report, check MOT \
failures and advisories (especially recurring ones: corrosion, brakes, suspension, \
tyres), whether mileage only ever increases, and tax status. Reports are limited per \
run, so spend them on cars that could make the shortlist.
5. Never invent facts. Every claim about a car must come from a tool result. If you \
couldn't verify something, say "not verified". Copy every listing link exactly as \
a tool returned it; never build or guess a URL.

Your final message is saved as the buyer's report, in Markdown:
- **Summary**: one paragraph covering how many cars you looked at, how many passed, \
and your top pick and why.
- **Shortlist**: at most shortlist_size cars, all of which pass every must-have (fewer \
is fine if not enough pass). Order them by shortlist_order if the buyer gave one, \
otherwise best first. A table (#, car, price, year, mileage, location, source, link), \
then one section per car in the same order, with: how it meets each must-have and nice-to-have, \
the vehicle report findings (MOT history highlights, advisories, mileage check, tax, \
AutoTrader basic check if applicable), red flags, and questions to ask the seller.
- **Rejected**: notable cars you rejected, with the reason for each.
- **Not checked**: anything the free reports can't cover (outstanding finance, \
stolen-vehicle and write-off registers for cars with only a Car Owl report). Recommend \
a paid check before buying.
"""


def load_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text())
    if not config.get("allowed_sites"):
        raise AgentError(f"{path}: allowed_sites must list at least one site.")
    return config


def build_request(config: dict) -> str:
    requirements = {k: v for k, v in config.items() if k not in ("allowed_sites", "max_reports")}
    return (
        "Find me a car. My requirements:\n\n```yaml\n"
        + yaml.safe_dump(requirements, sort_keys=False, allow_unicode=True)
        + "```\n\nSites you may use: " + ", ".join(config["allowed_sites"])
    )


class AgentError(Exception):
    pass


def run(config: dict, log=print) -> str:
    """Run the agent and return its Markdown report."""
    tools.configure(config, log)
    client = anthropic.Anthropic()
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        tools=tools.ALL_TOOLS,
        messages=[{"role": "user", "content": build_request(config)}],
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        # If a safety classifier declines a request, retry on a fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        max_iterations=80,
    )

    final = None
    for message in runner:
        final = message
        for block in message.content:
            if block.type == "text" and message.stop_reason == "tool_use":
                log(block.text.strip())

    if final is None:
        raise AgentError("The agent returned no response.")
    if final.stop_reason == "refusal":
        raise AgentError(f"The model declined the request: {final.stop_details}")
    if final.stop_reason == "max_tokens":
        log("Warning: the final report hit the token limit and may be cut off.")
    return "\n".join(b.text for b in final.content if b.type == "text").strip()


def friendly_error(e: Exception) -> str:
    if isinstance(e, AgentError):
        return str(e)
    if isinstance(e, anthropic.AuthenticationError) or "authentication" in str(e).lower():
        return "Anthropic API key missing or invalid. Set ANTHROPIC_API_KEY and try again."
    if isinstance(e, anthropic.RateLimitError):
        return "Hit the Anthropic API rate limit. Wait a minute and run again."
    if isinstance(e, anthropic.APIStatusError):
        return f"Anthropic API error {e.status_code}: {e.message}"
    if isinstance(e, anthropic.APIConnectionError):
        return "Couldn't reach the Anthropic API. Check your internet connection."
    return f"{type(e).__name__}: {e}"


def run_search(config: dict, log=print) -> dict:
    """Run the configured engine ("plain" rules or "ai" Claude agent), check every link
    in its report, and save the report. Returns {"report_file", "report", "links"}."""
    engine = config.get("engine", "plain")
    if engine == "plain":
        report = plain.run(config, log)
    elif engine == "ai":
        report = run(config, log)
    else:
        raise AgentError(f"engine must be 'plain' or 'ai', not '{engine}'")
    log("→ checking links in the report")
    report, link_results = links.check_report(report)
    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"car-search-{datetime.now():%Y-%m-%d-%H%M%S}.md"
    out.write_text(report)
    return {"report_file": out.name, "report": report, "links": link_results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Find used cars matching requirements.yaml.")
    parser.add_argument("config", nargs="?", default=Path(__file__).parent / "requirements.yaml", type=Path)
    parser.add_argument("--engine", choices=["plain", "ai"], help="override `engine` in the config")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.engine:
            config["engine"] = args.engine
        print(f"Searching with requirements from {args.config} ({config.get('engine', 'plain')} engine) ...")
        result = run_search(config)
    except Exception as e:
        sys.exit(friendly_error(e))
    print(f"\n{'=' * 70}\n{result['report']}\n{'=' * 70}\nReport saved to {REPORTS_DIR / result['report_file']}")


if __name__ == "__main__":
    main()
