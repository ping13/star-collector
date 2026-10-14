

.PHONY: help

help:		## output help for all targets
	@echo "These are the targets of this Makefile:"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

DOCKER_HOST=$(docker context inspect --format '{{.Endpoints.docker.Host}}')

all: help

action:		## Run Github Actions locally, https://github.com/nektos/act
	DOCKER_HOST=$(shell docker context inspect --format '{{.Endpoints.docker.Host}}') act schedule --secret-file .env --var-file .vars

lint:		## Lint with ruff
	uv run ruff check .

test:		## Run all tests
	uv run pytest tests/

validate:       ## Validate RSS feed
	uv run --no-dev python rss.py --limit 200 | uv run python validate_feed.py

titles:		## show the titles of the last 200 items
	uv run python rss.py --limit 200 | yq eval -p=xml -o=json | jq  ".rss.channel.item[].title"






title-bakeoff:	## Compare headline models on recent posts
	uv run python scripts/title_bakeoff.py

linkedin-token:	## Create a LinkedIn access token via OAuth and store it in .env
	uv run python scripts/linkedin_token.py

linkedin-token-ci:	## Create a LinkedIn token and update the GitHub secret and variable
	uv run python scripts/linkedin_token.py --set-secret
