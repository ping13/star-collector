# RSS Feed for Personal Favorites and Bookmarks on Mastodon

## Overview

This Python-based tool generates an RSS feed of your favorites and bookmarks from Mastodon, 
along with starred items from Feedbin. The script fetches your most recent interactions
and combines them into a single RSS feed. You can see an example feed for 
[`ping13@swiss.social`](https://swiss.social/@ping13).

## Setup

1. Copy `config.sample.yaml` to `config.yaml`
2. Edit `config.yaml` with your Mastodon instance and username
3. Set your Mastodon access token in the `MASTODON_ACCESS_TOKEN` environment variable
4. Optional: Configure Feedbin starred items feed URL

## Usage

```bash
# Basic usage (uses config.yaml by default)
python rss.py

# Specify a different config file
python rss.py --config my_config.yaml

# Output to a file instead of stdout
python rss.py --output feed.xml

# Set number of items in feed
python rss.py --limit 10

# Enable debug output
python rss.py --debug
```

## Using Large Language Models for Coding

The code was developed using [aider](https://aider.chat), which proved to be a pleasant experience. 

To set things up, I prefer utilizing [uv](https://astral.sh/uv) to use the latest
`aider-chat` release with all necessary dependencies. This can be achieved by
executing the following commands:

```bash
$ uv sync                     # uses pyproject.toml
$ uv run aider --architect
```

The `--architect` option was [recently
introduced](https://aider.chat/2024/09/26/architect.html), enabling pre-edit
reasoning for code. However, it is relatively costly, as it employs models from
both Anthropic and OpenAI.

### Dev Notes


- How to run act with Rancher:

`export DOCKER_HOST=$(docker context inspect --format '{{.Endpoints.docker.Host}}')`
