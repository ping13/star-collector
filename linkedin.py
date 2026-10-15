"""LinkedIn reactions as a star source (Member Data Portability changelog API).

The changelog only exposes ~28 days of events, so reactions are merged into a
small JSON state file that is published next to the feed and re-read on the
next run. Events carry only a URN; the post text is looked up from the public
post page (og:description) and turned into a headline by extract_titles.
"""
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from typing import NamedTuple

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
FULL_REFRESH_DAYS = 7  # re-read the whole window now and then to self-heal
# Bump when the interpretation of changelog events changes (new event types,
# different filtering): stored cursors are then stale, so a full re-read is forced.
PARSER_VERSION = 2
DAY_MS = 86_400_000

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


def window_start_ms(now_ms: int | None = None) -> int:
    now_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    return now_ms - WINDOW_DAYS * DAY_MS


def fetch_events(token: str, since_ms: int | None = None) -> list[dict]:
    """Changelog events processed since `since_ms` (default: the whole 28-day window)."""
    since_ms = since_ms or window_start_ms()
    events, start = [], 0
    while True:
        page = _get_page(token, start, since_ms)
        if not page:
            return events
        events.extend(page)
        start += len(page)
        time.sleep(0.2)


POST_URN_PREFIXES = ("urn:li:activity:", "urn:li:ugcPost:", "urn:li:share:")


def _post_urn(value) -> str | None:
    """A post URN from a plain string or a small dict wrapping one.

    instantReposts carry e.g. {"ugcPost": "urn:li:ugcPost:1"} as repostedContent.
    """
    if isinstance(value, dict):
        return next(filter(None, map(_post_urn, value.values())), None)
    if isinstance(value, str) and value.startswith(POST_URN_PREFIXES):
        return value
    return None


INSTANT_REPOST_RE = re.compile(r"^urn:li:instantRepost:\((urn:li:[A-Za-z]+:\d+),\d+\)$")


def _instant_repost_target(resource_id: str) -> str | None:
    """Original post URN embedded in urn:li:instantRepost:(<post urn>,<id>)."""
    match = INSTANT_REPOST_RE.match(resource_id)
    return _post_urn(match.group(1)) if match else None


def _reshare(activity: dict) -> tuple[str | None, str]:
    """(original post URN, commentary) of a ugcPost; no URN for an own original post.

    A reshare with commentary has ugcOrigin "RESHARE" and points at the original
    via responseContext {"parent": ..., "root": ...}.
    """
    context = activity.get("responseContext") or {}
    urn = _post_urn(context.get("root")) or _post_urn(context.get("parent"))
    content = (activity.get("specificContent") or {}).get("com.linkedin.ugc.ShareContent") or {}
    text = (content.get("shareCommentary") or {}).get("text") or ""
    return urn, " ".join(text.split())


class Resolved(NamedTuple):
    kept: dict[str, dict]      # urn -> {reaction, ts[, repost_id, comment]}
    removed: set[str]          # reactions deleted or now excluded
    reposts_removed: set[str]  # reposts that were undone


def resolve_events(events: list[dict], exclude: list[str],
                   known_reposts: dict[str, str] | None = None) -> Resolved:
    """Reduce changelog events to the current state of each post.

    Latest event per post wins; DELETE removes. Likes/reactions come from
    `socialActions/likes`; plain reposts from `instantReposts` and reshares with
    commentary from `ugcPosts` (kept with their "comment"). Both are reported as
    reaction "REPOST" of the original post (add it to `exclude` to leave reposts
    out); own original posts are ignored. A repost outranks a reaction on the
    same post. Events by other members (e.g. their likes on your own posts,
    which the changelog also reports) are skipped. `known_reposts` maps the resourceId
    of reposts seen in earlier runs to their post URN, so that a later DELETE
    can be matched in an incremental fetch.
    """
    latest: dict[str, dict] = {}
    reposts: dict[str, dict] = {}
    repost_ids = dict(known_reposts or {})  # repost resourceId -> original post URN
    reposts_removed: set[str] = set()

    def add_repost(urn: str, rid: str, ts: int, comment: str = "") -> None:
        reposts[urn] = {"reaction": "REPOST", "ts": ts, "repost_id": rid}
        if comment:
            reposts[urn]["comment"] = comment
        reposts_removed.discard(urn)
        if rid:
            repost_ids[rid] = urn

    def remove_repost(urn: str | None) -> None:
        if urn:
            reposts.pop(urn, None)
            reposts_removed.add(urn)

    for event in sorted(events, key=lambda e: e.get("capturedAt") or e.get("processedAt") or 0):
        ts = event.get("capturedAt") or event.get("processedAt") or 0
        resource, method = event.get("resourceName"), event.get("method")
        actor, owner = event.get("actor"), event.get("owner")
        if actor and owner and actor != owner:
            continue  # someone else's action on your content

        if resource == "socialActions/likes":
            urn = event.get("resourceId") or ""
            if not urn.startswith(POST_URN_PREFIXES):
                continue  # comments and anything else
            if method == "DELETE":
                latest[urn] = {"deleted": True, "ts": ts}
            elif method == "CREATE":
                reaction = (event.get("activity") or {}).get("reactionType")
                if reaction:
                    latest[urn] = {"reaction": reaction, "ts": ts}

        elif resource == "instantReposts":
            rid = event.get("resourceId") or ""
            if method == "CREATE":
                urn = (_post_urn((event.get("activity") or {}).get("repostedContent"))
                       or _instant_repost_target(rid))
                if urn:
                    add_repost(urn, rid, ts)
            elif method == "DELETE":
                remove_repost(repost_ids.get(rid) or _instant_repost_target(rid))

        elif resource == "ugcPosts":
            rid = event.get("resourceId") or ""
            activity = event.get("activity") or {}
            if method == "DELETE" or activity.get("lifecycleState") == "DELETED":
                remove_repost(repost_ids.get(rid))
            elif method in ("CREATE", "UPDATE"):
                urn, comment = _reshare(activity)
                if urn:  # own original posts are not reactions
                    created = (activity.get("created") or {}).get("time") or ts
                    add_repost(urn, rid, created, comment[:500])

    merged = {**latest, **reposts}  # a repost wins over a plain reaction
    kept = {u: v for u, v in merged.items() if not v.get("deleted") and v["reaction"] not in exclude}
    removed = set(merged) - set(kept)
    return Resolved(kept, removed, reposts_removed - set(kept))


def reactions_from_events(events: list[dict], exclude: list[str]) -> dict[str, dict]:
    """Current {urn: {reaction, ts[, comment]}} for a batch of events (see resolve_events)."""
    kept = resolve_events(events, exclude).kept
    return {u: {k: v[k] for k in ("reaction", "ts", "comment") if k in v} for u, v in kept.items()}


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


def _normalize_state(raw: dict) -> dict:
    """Current format: {"items": {...}, "last_processed_at": ms, "last_full_fetch": ms,
    "parser_version": n}.

    Older state files were a flat {urn: item} mapping.
    """
    if "items" in raw and isinstance(raw["items"], dict):
        return {"items": raw["items"],
                "last_processed_at": int(raw.get("last_processed_at") or 0),
                "last_full_fetch": int(raw.get("last_full_fetch") or 0),
                "parser_version": int(raw.get("parser_version") or 0)}
    return {"items": raw, "last_processed_at": 0, "last_full_fetch": 0, "parser_version": 0}


def load_state(source: str | None, url: str | None = None) -> dict:
    """Read the state file, else the published copy; empty on first run."""
    raw: dict = {}
    if source and os.path.exists(source):
        with open(source) as f:
            raw = json.load(f)
    elif url:
        try:
            response = requests.get(url, timeout=20)
            if response.status_code == 200:
                raw = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Could not load LinkedIn state from %s: %s", url, exc)
    return _normalize_state(raw)


def save_state(state: dict, path: str) -> None:
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


def _apply_events(items: dict[str, dict], events: list[dict], exclude: list[str], full: bool) -> None:
    """Merge a batch of changelog events into the stored items."""
    known = {v["repost_id"]: u for u, v in items.items() if v.get("repost_id")}
    res = resolve_events(events, exclude, known)

    for urn in res.reposts_removed:
        items.pop(urn, None)
    for urn in res.removed:
        # a plain reaction event must not undo a stored repost
        if items.get(urn, {}).get("reaction") != "REPOST":
            items.pop(urn, None)
    if full:
        # the full window is authoritative: anything it no longer reports is gone
        start = window_start_ms()
        for urn in [u for u, v in items.items() if v.get("ts", 0) >= start and u not in res.kept]:
            if items[urn].get("reaction") != "REPOST" or urn in res.removed:
                del items[urn]

    for urn, fresh in res.kept.items():
        entry = items.setdefault(urn, {})
        if entry.get("reaction") == "REPOST" and fresh["reaction"] != "REPOST":
            continue
        entry.update(url=POST_URL.format(urn=urn), reaction=fresh["reaction"], ts=fresh["ts"])
        for key in ("repost_id", "comment"):
            if fresh.get(key):
                entry[key] = fresh[key]


def _enrich(items: dict[str, dict], max_lookups: int) -> None:
    """Look up post text and headline for items that lack them."""
    lookups = 0
    for urn, entry in items.items():
        if "text" not in entry and lookups < max_lookups:
            lookups += 1
            entry["text"] = (fetch_post_text(urn) or "")[:500]
        if "title" not in entry and "text" in entry:
            # only persist real headlines; fallbacks are retried next run
            title = extract_titles.try_extract_title(entry["text"]) if entry["text"] else None
            if title:
                entry["title"] = title


def collect(config: dict | None) -> list[dict]:
    """Return feed-ready LinkedIn items [{id, url, title, text, reaction, ts[, comment]}].

    Normally only events since the last run are fetched (one API request);
    once a week, or without usable state, the whole 28-day window is read.
    """
    cfg = {**DEFAULTS, **(config or {})}
    state = load_state(cfg["state_file"], cfg["state_url"])
    items = state["items"]
    token = os.getenv("LINKEDIN_ACCESS_TOKEN")

    days = token_days_left(os.getenv("LINKEDIN_TOKEN_EXPIRES"))
    if days is not None and days < TOKEN_WARN_DAYS:
        _announce("warning", f"LinkedIn token expires in {days} days: run `make linkedin-token`")

    if not token:
        logger.warning("LINKEDIN_ACCESS_TOKEN not set, using stored LinkedIn items only")
    else:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        window = window_start_ms(now_ms)
        full = (
            state["last_processed_at"] < window
            or now_ms - state["last_full_fetch"] > FULL_REFRESH_DAYS * DAY_MS
            or state["parser_version"] != PARSER_VERSION
        )
        try:
            events = fetch_events(token, window if full else state["last_processed_at"])
        except TokenExpired:
            _announce("warning", "LinkedIn token expired or invalid: run `make linkedin-token`")
        except requests.RequestException as exc:
            logger.warning("LinkedIn fetch failed, using stored items: %s", exc)
        else:
            _apply_events(items, events, cfg["exclude_reactions"], full)
            latest = max((e.get("processedAt") or 0 for e in events), default=0)
            state["last_processed_at"] = max(latest, state["last_processed_at"]) or now_ms
            if full:
                state["last_full_fetch"] = now_ms
            state["parser_version"] = PARSER_VERSION
            _enrich(items, cfg["max_lookups_per_run"])

    newest = sorted(items.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)
    state["items"] = items = dict(newest[: cfg["max_items"]])
    if cfg["state_file"]:
        save_state(state, cfg["state_file"])
    result = []
    for urn, v in items.items():
        if "text" not in v:
            continue  # not looked up yet (per-run lookup cap)
        title = v.get("title") or (
            extract_titles.fallback_title(v["text"]) if v["text"]
            else f"LinkedIn post ({v['reaction'].title()})"
        )
        result.append({"id": urn, **v, "title": title})
    return result
