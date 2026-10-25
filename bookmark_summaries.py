"""Fill empty or generic linkding descriptions with short LLM summaries of the page.

linkding scrapes a page's meta description when a bookmark is saved. Some sites
answer that scrape with a consent page, a login wall or an app shell, so the
bookmark ends up with no description or a generic one (YouTube's "Auf YouTube
findest du ..."). This script finds those bookmarks, fetches the page as
Markdown with Kagi's Extract API (Kagi's servers do the fetching, so CI's data
center address does not get bot-check pages) and has the OpenRouter model of
the `titles:` block write a 2-3 sentence summary into the description. For a
Hacker News story the linked article is summarized, not the comment thread.

Descriptions that look written by hand are never touched. A description is
treated as generic if it is empty, an error page's text ("404 Fehler"), shared
by several bookmarks with different URLs, or the same as the site's homepage
description (unless the bookmark is the homepage). Once replaced,
a bookmark no longer matches, so runs are idempotent without any state.

Dry run by default: lists candidates without calling any paid API or writing anything.
"""
import logging
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

import click
import requests
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

KAGI_EXTRACT_URL = "https://kagi.com/api/v1/extract"
KAGI_PRICE_PER_PAGE = 0.004  # USD, $4 per 1,000 pages
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
SKIP = "SKIP"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

DEFAULTS = {
    "linkding_url": "https://bookmarks.ping13.net",
    "days": 7,  # only bookmarks added in the last N days are candidates
    "max_summaries_per_run": 10,
    "language": "English",
    "max_page_chars": 20_000,  # of the extracted Markdown sent to the model
    "model": None,  # default: titles.model
    "fallback_model": None,  # default: titles.fallback_model
    "exclude_tags": [],  # merged with rss.exclude_categories
}

SYSTEM_PROMPT = (
    "You write descriptions for a public list of bookmarked web pages. Given a page's "
    "title, URL and extracted content, write 2-3 sentences in {language} on what the page "
    "is about and its main point or findings, in at most 60 words. Open with the subject "
    "itself, never with a description of the page: write \"Git's --fixup and --autosquash "
    "can amend any past commit ...\", not \"This article explains how ...\", and "
    "\"3Blue1Brown argues that ...\", not \"The page is a YouTube video ...\". Plain text, "
    "no markdown, no preamble. If the content is "
    "an error page, a cookie, login or paywall notice, a bot check, or text that does not "
    f"match the title, reply with {SKIP} only."
)

# mobile hosts that serve app shells instead of real pages to scrapers
MOBILE_HOSTS = {"m.youtube.com": "www.youtube.com", "mobile.twitter.com": "twitter.com",
                "mobile.x.com": "x.com"}
# what a scrape of an error or bot-check page leaves behind; short notes of your own stay
ERROR_TEXT = re.compile(r"^(error )?(404|403|500)\b|not found|nicht gefunden|access denied|"
                        r"just a moment|attention required|enable javascript|are you a robot",
                        re.I)
TRACKING_PARAMS = re.compile(r"^(utm_.*|fbclid|gclid|mc_cid|mc_eid|si|pp|ra|feature)$")


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, (requests.ConnectionError, requests.Timeout))


_retry = retry(retry=retry_if_exception(_retryable), stop=stop_after_attempt(3),
               wait=wait_exponential(multiplier=1, max=8), reraise=True)


def load_settings(config_file: str) -> dict:
    """The `bookmarks:` block of sc_config.yaml over DEFAULTS.

    Read with plain YAML (no ${VAR} expansion), so the feed's tokens are not needed.
    """
    with open(config_file) as fh:
        config = yaml.safe_load(fh) or {}
    settings = dict(DEFAULTS)
    settings.update({k: v for k, v in (config.get("bookmarks") or {}).items() if k in DEFAULTS})
    titles = config.get("titles") or {}
    settings["model"] = settings["model"] or titles.get("model")
    settings["fallback_model"] = settings["fallback_model"] or titles.get("fallback_model")
    excluded = (config.get("rss") or {}).get("exclude_categories") or []
    settings["exclude_tags"] = sorted(set(settings["exclude_tags"]) | set(excluded))
    return settings


def normalize_url(url: str) -> str:
    """The URL to fetch: desktop host, decoded path, no tracking parameters."""
    parts = urlparse(url)
    path = parts.path
    query = parts.query
    if re.search("%3F|%23", path, re.I):  # e.g. /watch%3Fv%3Dabc: query or fragment encoded into the path
        path = unquote(path).partition("#")[0]
        path, _, query = path.partition("?")
    host = MOBILE_HOSTS.get(parts.netloc.lower(), parts.netloc)
    host = re.sub(r"\.m\.wikipedia\.org$", ".wikipedia.org", host)
    params = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True)
              if not TRACKING_PARAMS.match(k)]
    return urlunparse(parts._replace(netloc=host, path=path, query=urlencode(params)))


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


@_retry
def _linkding_page(base_url: str, token: str, offset: int) -> dict:
    response = requests.get(f"{base_url}/api/bookmarks/",
                            headers={"Authorization": f"Token {token}"},
                            params={"limit": 100, "offset": offset}, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_bookmarks(base_url: str, token: str) -> list[dict]:
    """All bookmarks (descriptions of older ones are needed to spot shared texts)."""
    bookmarks, offset = [], 0
    while True:
        page = _linkding_page(base_url, token, offset)
        bookmarks.extend(page["results"])
        if not page.get("next"):
            return bookmarks
        offset += len(page["results"])


def homepage_description(host: str) -> str:
    """Meta description of https://<host>/, or "" if it cannot be read."""
    try:
        response = requests.get(f"https://{host}/", headers={"User-Agent": USER_AGENT},
                                timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.info("No homepage description for %s: %s", host, exc)
        return ""
    soup = BeautifulSoup(response.text, "html.parser")
    for attrs in ({"property": "og:description"}, {"name": "description"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return _clean(tag["content"])
    return ""


def generic_reason(bookmark: dict, shared: dict[str, set[str]], homepage: dict[str, str]) -> str | None:
    """Why the bookmark's description counts as generic, or None if it looks real."""
    description = _clean(bookmark.get("description"))
    if not description:
        return "empty"
    if ERROR_TEXT.search(description) and len(description) < 80:
        return "looks like an error page"
    if len(shared.get(description, ())) > 1:
        return f"shared by {len(shared[description])} bookmarks"
    url = urlparse(normalize_url(bookmark["url"]))
    if url.path in ("", "/") and not url.query:
        return None  # a bookmarked homepage: its description is the right one
    host = url.netloc.lower()
    if host not in homepage:
        homepage[host] = homepage_description(host)
    if homepage[host] and homepage[host] == description:
        return "same as the site's homepage description"
    return None


def find_candidates(bookmarks: list[dict], settings: dict, since: datetime) -> list[tuple[dict, str]]:
    """Recent bookmarks with an empty or generic description, newest first."""
    shared: dict[str, set[str]] = defaultdict(set)
    for bookmark in bookmarks:
        if description := _clean(bookmark.get("description")):
            shared[description].add(normalize_url(bookmark["url"]))
    excluded = set(settings["exclude_tags"])
    homepage: dict[str, str] = {}
    candidates = []
    for bookmark in sorted(bookmarks, key=lambda b: b["date_added"], reverse=True):
        if datetime.fromisoformat(bookmark["date_added"].replace("Z", "+00:00")) < since:
            continue
        if excluded & set(bookmark.get("tag_names", [])):
            continue
        if reason := generic_reason(bookmark, shared, homepage):
            candidates.append((bookmark, reason))
    return candidates


class SummaryError(Exception):
    pass


def hn_article_url(url: str) -> str | None:
    """The article a Hacker News story links to (None for Ask HN, comments, other URLs)."""
    parts = urlparse(url)
    if parts.netloc != "news.ycombinator.com" or parts.path != "/item":
        return None
    item_id = dict(parse_qsl(parts.query)).get("id")
    if not item_id:
        return None
    try:
        response = requests.get(HN_ITEM_URL.format(id=item_id), timeout=15)
        response.raise_for_status()
        item = response.json() or {}
    except (requests.RequestException, ValueError) as exc:
        logger.info("Hacker News lookup for %s failed: %s", url, exc)
        return None
    return item.get("url") if item.get("type") == "story" else None


def _strip_markdown(markdown: str) -> str:
    """Drop images and link targets, which cost tokens and say nothing."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


@_retry
def extract_page(url: str, token: str) -> str:
    """The page at `url` as Markdown, fetched by Kagi's Extract API."""
    response = requests.post(KAGI_EXTRACT_URL, headers={"Authorization": f"Bearer {token}"},
                             json={"pages": [{"url": url}], "timeout": 30}, timeout=90)
    if response.status_code in (401, 403):
        raise SummaryError("Kagi rejected the API key (needs a v1 key from kagi.com/api)")
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise SummaryError("; ".join(e.get("message", str(e)) for e in body["errors"]))
    page = (body.get("data") or [{}])[0]
    if not page.get("markdown"):
        raise SummaryError(page.get("error") or "Kagi returned no content")
    return _strip_markdown(page["markdown"])


@_retry
def _call_model(model: str, system: str, prompt: str, api_key: str) -> tuple[str, float]:
    response = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": prompt}],
              "temperature": 0, "max_tokens": 300,
              "reasoning": {"enabled": False},
              "usage": {"include": True}},
        timeout=60,
    )
    response.raise_for_status()
    body = response.json()
    choices = body.get("choices") or []
    text = (choices[0].get("message", {}).get("content") or "").strip() if choices else ""
    return text, float((body.get("usage") or {}).get("cost") or 0)


def summarize(title: str, url: str, page: str, settings: dict, api_key: str) -> tuple[str | None, float]:
    """A short summary of the page, None if the model judged it unusable; and the cost in USD."""
    system = SYSTEM_PROMPT.format(language=settings["language"])
    prompt = f"Title: {title}\nURL: {url}\n\n{page[:settings['max_page_chars']]}"
    cost, errors = 0.0, []
    for model in dict.fromkeys(filter(None, [settings["model"], settings["fallback_model"]])):
        try:
            text, call_cost = _call_model(model, system, prompt, api_key)
        except requests.RequestException as exc:
            errors.append(f"{model}: {exc}")
            continue
        cost += call_cost
        if text.strip().rstrip(".").upper() == SKIP:
            return None, cost
        if text:
            return _clean(text), cost
        errors.append(f"{model}: empty answer")
    raise SummaryError("; ".join(errors) or "no model configured (titles.model)")


@_retry
def update_description(base_url: str, token: str, bookmark_id: int, description: str) -> None:
    response = requests.patch(f"{base_url}/api/bookmarks/{bookmark_id}/",
                              headers={"Authorization": f"Token {token}"},
                              json={"description": description}, timeout=30)
    response.raise_for_status()


def _short(text: str, limit: int = 100) -> str:
    text = _clean(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


@click.command()
@click.option("--config", "-c", default="sc_config.yaml", show_default=True,
              help="Configuration file (reads the bookmarks: and titles: blocks)")
@click.option("--days", type=int, help="Look at bookmarks added in the last N days "
              "(default: bookmarks.days)")
@click.option("--summarize", "do_summarize", is_flag=True,
              help="Also fetch the pages and print their summaries (costs Kagi and OpenRouter credit)")
@click.option("--apply", "do_apply", is_flag=True,
              help="Fetch summaries and write them to linkding (implies --summarize)")
@click.option("--log-level", "-L", default="WARNING", show_default=True,
              type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False))
def main(config: str, days: int | None, do_summarize: bool, do_apply: bool, log_level: str):
    """Replace empty or generic linkding descriptions with short page summaries.

    Without options this is a free dry run: it lists the candidates and why they
    were picked. --summarize adds the summaries, --apply writes them.
    """
    logging.basicConfig(level=log_level.upper(), format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()
    settings = load_settings(config)
    linkding_token = os.environ.get("LINKDING_API_TOKEN")
    if not linkding_token:
        raise click.ClickException("LINKDING_API_TOKEN is not set (linkding: Settings → Integrations)")
    paid = do_summarize or do_apply
    kagi_token = os.environ.get("KAGI_API_TOKEN")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if paid and not kagi_token:
        raise click.ClickException("KAGI_API_TOKEN is not set (a v1 key from kagi.com/api)")
    if paid and not openrouter_key:
        raise click.ClickException("OPENROUTER_API_KEY is not set")

    days = days if days is not None else settings["days"]
    since = datetime.now(timezone.utc) - timedelta(days=days)
    base_url = settings["linkding_url"].rstrip("/")
    bookmarks = fetch_bookmarks(base_url, linkding_token)
    candidates = find_candidates(bookmarks, settings, since)
    mode = "apply" if do_apply else "summarize (no writes)" if do_summarize else "dry run"
    click.echo(f"{len(bookmarks)} bookmarks, {len(candidates)} with an empty or generic "
               f"description added in the last {days} days [{mode}]")

    limit = settings["max_summaries_per_run"]
    if paid and len(candidates) > limit:
        click.echo(f"Only the newest {limit} are summarized (bookmarks.max_summaries_per_run)")
    cost = 0.0
    attempts = failures = skipped = 0
    for index, (bookmark, reason) in enumerate(candidates):
        url = normalize_url(bookmark["url"])
        article = hn_article_url(url)
        click.echo(f"\n#{bookmark['id']} {bookmark['date_added'][:10]} {_short(bookmark['title'], 70)}")
        click.echo(f"  url:     {bookmark['url']}")
        if article or url != bookmark["url"]:
            click.echo(f"  fetch:   {article or url}" + ("  (article of the HN story)" if article else ""))
        click.echo(f"  reason:  {reason}")
        if bookmark.get("description"):
            click.echo(f"  current: {_short(bookmark['description'])}")
        if not paid or index >= limit:
            continue
        attempts += 1
        try:
            page = extract_page(article or url, kagi_token)
            cost += KAGI_PRICE_PER_PAGE
            summary, model_cost = summarize(bookmark["title"], article or url, page, settings,
                                            openrouter_key)
            cost += model_cost
        except (SummaryError, requests.RequestException) as exc:
            failures += 1
            click.echo(f"  failed:  {exc}")
            continue
        if summary is None:
            skipped += 1
            click.echo("  skipped: the page content is unusable (error page, wall or junk)")
            continue
        click.echo(f"  summary: {summary}")
        if do_apply:
            update_description(base_url, linkding_token, bookmark["id"], summary)
            click.echo("  ✓ description updated")

    if attempts:
        click.echo(f"\n{attempts - failures - skipped} summarized, {skipped} skipped, "
                   f"{failures} failed; about ${cost:.3f}")
    # single unreadable pages are retried on the next run; all of them failing
    # points at a key or the API credit, which should be noticed
    if attempts and failures == attempts:
        sys.exit(1)


if __name__ == "__main__":
    main()
