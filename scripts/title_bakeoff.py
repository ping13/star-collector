"""Compare headline models on recent Mastodon favourites.

Usage: uv run python scripts/title_bakeoff.py [model ...]
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import extract_titles  # noqa: E402

MODELS = [
    "anthropic/claude-haiku-5.5",
    "google/gemini-2.5-flash-lite",
    "mistralai/mistral-small-3.2-24b-instruct",
    "qwen/qwen3.7-flash",
]

load_dotenv()
models = sys.argv[1:] or MODELS
r = requests.get(
    "https://swiss.social/api/v1/accounts/lookup", params={"acct": "ping13"}, timeout=15
)
account = r.json()["id"]
statuses = requests.get(
    f"https://swiss.social/api/v1/accounts/{account}/statuses",
    params={"limit": 20, "exclude_replies": "true", "exclude_reblogs": "true"},
    timeout=15,
).json()

from html2text import html2text  # noqa: E402

for status in statuses[:20]:
    text = " ".join(html2text(status["content"]).split())
    if not text:
        continue
    print(f"\n> {text[:140]}")
    for model in models:
        try:
            extract_titles.settings["model"] = model
            title = extract_titles._shorten(
                extract_titles._call_model(model, text[:1500], os.environ["OPENROUTER_API_KEY"])
            ) or "(empty)"
        except requests.RequestException as exc:
            title = f"ERROR {exc}"
        print(f"  {model.split('/')[-1][:28]:28s} {title}")
