import argparse
import logging
import os
import sys
from pathlib import Path

import diskcache
import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
PROMPT_VERSION = 1
MAX_TITLE_CHARS = 80

SYSTEM_PROMPT = (
    "You write tight headlines for social media posts. Reply with one headline "
    "only: at most 10 words, same language as the post, no quotes, no trailing "
    "period, no hashtags, no preamble. State what the post is about; do not "
    "address the reader."
)

DEFAULTS = {
    "model": "anthropic/claude-haiku-5.5",
    "fallback_model": "qwen/qwen3.7-flash",
    "max_body_chars": 1500,
    "max_new_titles_per_run": 40,
    "timeout": 15,
}
settings = dict(DEFAULTS)

# generated titles are cached so that only new items cost money
cache = diskcache.Cache(str(Path(__file__).parent / "title-generator.cache"))

_new_titles = 0  # API-generated titles in this process, see max_new_titles_per_run


def configure(config: dict | None) -> None:
    """Override defaults with the optional `titles:` block of sc_config.yaml."""
    settings.update({k: v for k, v in (config or {}).items() if k in DEFAULTS})


def fallback_title(text: str) -> str:
    return _shorten(" ".join(text.split()))


def _shorten(title: str, limit: int = MAX_TITLE_CHARS) -> str:
    title = " ".join(title.split()).strip().strip("\"'“”‘’«»").strip()
    title = title.rstrip(".")
    if len(title) <= limit:
        return title
    cut = title[:limit].rsplit(" ", 1)[0] or title[:limit]
    return cut.rstrip(" ,;:-") + "…"


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, (requests.ConnectionError, requests.Timeout))


@retry(
    retry=retry_if_exception(_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=8),
    reraise=True,
)
def _call_model(model: str, prompt: str, api_key: str) -> str:
    response = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 100,
            "reasoning": {"enabled": False},  # thinking would eat the token budget
        },
        timeout=settings["timeout"],
    )
    response.raise_for_status()
    choices = response.json().get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message", {}).get("content") or "").strip()


def _generate(prompt: str) -> str | None:
    """Return a title from the primary model, then the fallback model, else None."""
    global _new_titles
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY not set, using truncated text as title")
        return None
    if _new_titles >= settings["max_new_titles_per_run"]:
        logger.warning("Title budget of %s per run reached", settings["max_new_titles_per_run"])
        return None

    _new_titles += 1
    for model in dict.fromkeys([settings["model"], settings.get("fallback_model")]):
        if not model:
            continue
        try:
            title = _shorten(_call_model(model, prompt, api_key))
        except requests.RequestException as exc:
            logger.warning("Title generation with %s failed: %s", model, exc)
            continue
        if title:
            return title
        logger.warning("Model %s returned an empty title", model)
    return None


def extract_title(text: str, context: str = "") -> str:
    """Return a short headline for `text`; `context` (e.g. a link card) is optional."""
    body = " ".join(text.split())[: settings["max_body_chars"]]
    if not body:
        return fallback_title(context)

    prompt = body if not context else f"{body}\n\nLinked page: {context[:300]}"
    key = (settings["model"], PROMPT_VERSION, prompt)
    cached = cache.get(key)
    if cached:
        return cached

    title = _generate(prompt)
    if title is None:
        return fallback_title(body)  # not cached: retry on the next run
    cache.set(key, title)
    logger.info("Generated title: %s", title)
    return title


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--model", help="Override the model")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    if args.model:
        settings["model"] = args.model
    print(extract_title(sys.stdin.read().strip()))
