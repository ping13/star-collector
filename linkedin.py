"""LinkedIn reactions as a star source (Member Data Portability changelog API).

The changelog only exposes ~28 days of events, so reactions are merged into a
small JSON state file that is published next to the feed and re-read on the
next run. Events carry only a URN; the post text is looked up from the public
post page (og:description) and turned into a headline by extract_titles.
"""
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

import extract_titles

logger = logging.getLogger(__name__)

BASE = "https://api.linkedin.com/rest"
API_VERSION = "202312"
WINDOW_DAYS = 28
POST_URL = "https://www.linkedin.com/feed/update/{urn}/"
USER_AGENT = "Mozilla/5.0 (compatible; star-collector/1.0)"
TOKEN_WARN_DAYS = 30

DEFAULTS = {
    "exclude_reactions": ["LIKE"],
    "max_items": 50,
    "max_lookups_per_run": 20,
    "state_file": "build/linkedin-state.json",
    "state_url": None,
}


class TokenExpired(Exception):
    pass


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, (requests.ConnectionError, requests.Timeout))


_retry = retry(retry=retry_if_exception(_retryable), stop=stop_after_attempt(3),
               wait=wait_exponential(multiplier=1, max=8), reraise=True)


@_retry
def _get_page(token: str, start: int, since_ms: int) -> list[dict]:
    response = requests.get(
        f"{BASE}/memberChangeLogs",
        headers={
            "Authorization": f"Bearer {token}",
            "LinkedIn-Version": API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        },
        params={"q": "memberAndApplication", "count": 50, "start": start,
                "sortBy": "LAST_MODIFIED", "startTime": since_ms},
        timeout=30,
    )
    if response.status_code in (401, 403):
        raise TokenExpired(response.text[:200])
    response.raise_for_status()
    return response.json().get("elements", [])


def fetch_events(token: str, since: datetime | None = None) -> list[dict]:
    since = since or datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    since_ms = int(since.timestamp() * 1000)
    events, start = [], 0
    while True:
        page = _get_page(token, start, since_ms)
        if not page:
            return events
        events.extend(page)
        start += len(page)
        time.sleep(0.2)


def reactions_from_events(events: list[dict], exclude: list[str]) -> dict[str, dict]:
    """Latest event per post wins; DELETE removes. Returns {urn: {reaction, ts}}.

    Reaction removals and type changes are resolved before filtering, so a post
    that was liked and then upgraded to PRAISE counts as PRAISE.
    """
    latest: dict[str, dict] = {}
    for event in events:
        if event.get("resourceName") != "socialActions/likes":
            continue
        urn = event.get("resourceId") or ""
        if not urn.startswith(("urn:li:activity:", "urn:li:ugcPost:", "urn:li:share:")):
            continue  # comments and anything else
        ts = event.get("capturedAt") or event.get("processedAt") or 0
        if urn in latest and latest[urn]["ts"] > ts:
            continue
        if event.get("method") == "DELETE":
            latest[urn] = {"deleted": True, "ts": ts}
        elif event.get("method") == "CREATE":
            reaction = (event.get("activity") or {}).get("reactionType")
            if reaction:
                latest[urn] = {"reaction": reaction, "ts": ts}
    return {
        urn: v for urn, v in latest.items()
        if not v.get("deleted") and v["reaction"] not in exclude
    }


@_retry
def _fetch_page(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    response.raise_for_status()
    return response.text


def fetch_post_text(urn: str) -> str | None:
    """Public post text from og:description (falls back to og:title)."""
    try:
        soup = BeautifulSoup(_fetch_page(POST_URL.format(urn=urn)), "html.parser")
    except requests.RequestException as exc:
        logger.warning("Could not fetch LinkedIn post %s: %s", urn, exc)
        return None
    for prop in ("og:description", "og:title"):
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content", "").strip():
            return " ".join(tag["content"].split())
    return None


def load_state(source: str | None, url: str | None = None) -> dict[str, dict]:
    """Read the state file, else the published copy; empty on first run."""
    if source and os.path.exists(source):
        with open(source) as f:
            return json.load(f)
    if url:
        try:
            response = requests.get(url, timeout=20)
            if response.status_code == 200:
                return response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Could not load LinkedIn state from %s: %s", url, exc)
    return {}


def save_state(state: dict[str, dict], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)


def token_days_left(expires: str | None, today: date | None = None) -> int | None:
    if not expires:
        return None
    try:
        return (date.fromisoformat(expires) - (today or date.today())).days
    except ValueError:
        return None


def _announce(level: str, message: str) -> None:
    logger.warning(message)
    if os.getenv("GITHUB_ACTIONS"):
        print(f"::{level}::{message}", file=sys.stderr)


def collect(config: dict | None) -> list[dict]:
    """Return feed-ready LinkedIn items [{id, url, title, text, reaction, ts}]."""
    cfg = {**DEFAULTS, **(config or {})}
    state = load_state(cfg["state_file"], cfg["state_url"])
    token = os.getenv("LINKEDIN_ACCESS_TOKEN")

    days = token_days_left(os.getenv("LINKEDIN_TOKEN_EXPIRES"))
    if days is not None and days < TOKEN_WARN_DAYS:
        _announce("warning", f"LinkedIn token expires in {days} days: run `make linkedin-token`")

    if not token:
        logger.warning("LINKEDIN_ACCESS_TOKEN not set, using stored LinkedIn items only")
    else:
        try:
            fresh = reactions_from_events(fetch_events(token), cfg["exclude_reactions"])
        except TokenExpired:
            _announce("warning", "LinkedIn token expired or invalid: run `make linkedin-token`")
        except requests.RequestException as exc:
            logger.warning("LinkedIn fetch failed, using stored items: %s", exc)
        else:
            lookups = 0
            for urn, reaction in fresh.items():
                entry = state.setdefault(urn, {})
                entry.update(url=POST_URL.format(urn=urn), reaction=reaction["reaction"], ts=reaction["ts"])
                if "text" not in entry and lookups < cfg["max_lookups_per_run"]:
                    lookups += 1
                    entry["text"] = (fetch_post_text(urn) or "")[:500]
                if "title" not in entry and "text" in entry:
                    # only persist real headlines; fallbacks are retried next run
                    title = extract_titles.try_extract_title(entry["text"]) if entry["text"] else None
                    if title:
                        entry["title"] = title
            # inside the 28-day window the API is authoritative: anything it no
            # longer reports (removed or now excluded) is dropped
            window_start = int((datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).timestamp() * 1000)
            for urn in [u for u, v in state.items() if v.get("ts", 0) >= window_start and u not in fresh]:
                del state[urn]

    newest = sorted(state.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)
    state = dict(newest[: cfg["max_items"]])
    if cfg["state_file"]:
        save_state(state, cfg["state_file"])
    items = []
    for urn, v in state.items():
        if "text" not in v:
            continue  # not looked up yet (per-run lookup cap)
        title = v.get("title") or (
            extract_titles.fallback_title(v["text"]) if v["text"]
            else f"LinkedIn post ({v['reaction'].title()})"
        )
        items.append({"id": urn, **v, "title": title})
    return items
