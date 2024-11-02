

.PHONY: help

help:		## output help for all targets
	@echo "These are the targets of this Makefile:"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

DOCKER_HOST=$(docker context inspect --format '{{.Endpoints.docker.Host}}')

all: help

action:		## Run Github Actions locally, https://github.com/nektos/act
	DOCKER_HOST=$(shell docker context inspect --format '{{.Endpoints.docker.Host}}') act schedule --secret-file .env --var-file .vars

test:		## test the generation of feeds and see if it is a valid feed
	uv run --no-dev python rss.py --limit 200 | uv run python validate_feed.py

aider:		## Start a chat with an LLM to change your code
	uv run aider --architect




