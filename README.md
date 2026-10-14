# Star Collector



A tool to generate an RSS feed combining your starred/favorite content from
Mastodon favorites and/or bookmarks and any personalized RSS feed you may have
(for example Feedbin or Linkding or even GitHub)

## Features

- Combines multiple star sources into a single RSS feed
- Configurable number of items per feed
- Support for Mastodon media attachments in feed items
- Environment variable support for sensitive tokens
- Detailed logging options
- Short headlines for Mastodon posts, generated via OpenRouter and cached

## Installation

1. Clone this repository
2. Install dependencies with `uv sync`
3. Copy `.env.sample` to `.env` and configure your tokens (including `OPENROUTER_API_KEY` for headlines)
4. Create a `sc_config.yaml` (see below) and configure your settings

## Configuration

Create a `sc_config.yaml` file with your settings:

```yaml
mastodon:
  access_token: "your-token-here"  # Can also be set via MASTODON_ACCESS_TOKEN env var
  mastodon_instance: "https://your.instance"
  mastodon_username: "yourusername"

# Optional RSS configuration
rss:
  urls:
    - url: https://feedbin.com/starred/${FEEDBIN_ID}.xml
      tag: feedbin
    - url: https://bookmarks.ping13.net/feeds/${LINKDING_ID}/all
      tag: linkding
```

## Usage

```bash
# Basic usage (outputs to stdout)
uv run rss.py

# With options
uv run rss.py --config sc_config.yaml --output feed.xml --limit 10 --debug

Options:
  -c, --config TEXT                 Path to configuration file (default: sc_config.yaml)
  --debug / --no-debug              Enable debug output
  -o, --output TEXT                 Output file (optional, defaults to stdout)
  -l, --limit INTEGER               Number of feed items to include (default: 5)
  -L, --log-level [DEBUG|INFO|WARNING|ERROR|CRITICAL]
                                    Set logging level (default: ERROR)
  --help                            Show this message and exit
```

## Ideas

- [LinkedIn with unofficial Python API](https://github.com/tomquirk/linkedin-api)
- Spotify
- ...

## Limitations

- Currently, there is no way to use the creation date of a like in Mastodon,
  see this [issue](https://github.com/mastodon/mastodon/issues/1608).

## LinkedIn reactions (optional)

Reactions on LinkedIn posts (except plain likes) can be added as a star source
through LinkedIn's Member Data Portability API (EEA/Switzerland members only):

1. Create a LinkedIn developer app with the *Member Data Portability API
   (Member)* product and add `http://localhost:8799/callback` as redirect URL.
2. Put `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET` into `.env`, then run
   `make linkedin-token` (once a year; add `--set-secret` via
   `uv run python scripts/linkedin_token.py --set-secret` to update the GitHub
   secret and the `LINKEDIN_TOKEN_EXPIRES` variable as well).
3. Enable the `linkedin:` block in `sc_config.yaml`.

The API only returns the last 28 days, so reactions are accumulated in
`linkedin-state.json`, which is published next to the feed and re-read on the
next run. Post text is read from the public post page and turned into a
headline. If the token expires, the feed still builds from the stored items and
a warning is logged.

## Headline generation

Mastodon posts have no title, so one is generated with an LLM through
[OpenRouter](https://openrouter.ai) (needs `OPENROUTER_API_KEY`; without it, the
start of the post is used). Configure it in `sc_config.yaml`:

```yaml
titles:
  model: anthropic/claude-haiku-5.5      # default
  fallback_model: qwen/qwen3.7-flash     # used if the primary fails
  max_new_titles_per_run: 40             # cost guard
```

Titles are cached in `title-generator.cache` (keyed by model, prompt and text),
so only new posts cost money: typically well under USD 0.10 per month. The
GitHub Action persists the cache between runs. Compare models with
`make title-bakeoff`.

## Development

```bash
make test   # run tests
make lint   # ruff
make help   # all targets
```

Architecture diagrams live in `docs/diagrams/`.
