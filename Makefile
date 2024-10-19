

.PHONY: help

help:		## output help for all targets
	@echo "These are the targets of this Makefile:"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'



all: help

aider:		## Start a chat with an LLM to change your code
	uv run aider-chat --architect




