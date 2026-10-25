SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DELETE_ON_ERROR:
.DEFAULT_GOAL := help

.PHONY: help
help: ## show this help
	@echo "Star Collector"
	@echo
	@awk 'BEGIN {FS = ":.*?## "}; \
		/^###/ {printf "\n\033[1;33m%s\033[0m\n", substr($$0, 5)}; \
		/^[a-zA-Z0-9_.-]+:.*?## / {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' \
		$(MAKEFILE_LIST)

### Quality
.PHONY: test lint
test: ## run the unit tests (all network calls are mocked)
	uv run pytest tests/

lint: ## check the code with ruff (also runs in CI)
	uv run ruff check .

### Feed
.PHONY: validate titles title-bakeoff
validate: ## build the feed (200 items) and check that it is valid RSS (needs tokens in .env)
	uv run --no-dev python rss.py --limit 200 | uv run python validate_feed.py

titles: ## list the titles of the last 200 feed items (needs yq and jq)
	uv run python rss.py --limit 200 | yq eval -p=xml -o=json | jq ".rss.channel.item[].title"

title-bakeoff: ## compare headline models side by side on your 20 latest Mastodon posts (needs OPENROUTER_API_KEY)
	uv run python scripts/title_bakeoff.py

### Bookmark summaries (linkding, Kagi Extract, OpenRouter)
.PHONY: bookmarks bookmarks-preview bookmarks-apply
bookmarks: ## list recent bookmarks with an empty or generic description (free, changes nothing; ARGS="--days 90")
	uv run python bookmark_summaries.py $(ARGS)

bookmarks-preview: ## same, plus the summaries they would get (about 1 cent each, changes nothing)
	uv run python bookmark_summaries.py --summarize $(ARGS)

bookmarks-apply: ## write the summaries into those bookmarks (what the nightly job does)
	uv run python bookmark_summaries.py --apply $(ARGS)

### LinkedIn token (valid ~1 year)
.PHONY: linkedin-token linkedin-token-ci
linkedin-token: ## create a LinkedIn token via browser consent and save it to .env (for local runs)
	uv run python scripts/linkedin_token.py

linkedin-token-ci: ## same as linkedin-token, plus update the GitHub secret and expiry variable (for the nightly job)
	uv run python scripts/linkedin_token.py --set-secret

### CI
.PHONY: action
action: ## run the scheduled GitHub workflow locally with act (needs Docker, .env and .vars)
	DOCKER_HOST=$(shell docker context inspect --format '{{.Endpoints.docker.Host}}') act schedule --secret-file .env --var-file .vars
