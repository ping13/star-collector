# Star Collector

A tool to generate an RSS feed combining your starred/favorite content from:
- Mastodon favorites and bookmarks
- Feedbin starred articles
- GitHub starred repositories

## Features

- Combines multiple star sources into a single RSS feed
- Configurable number of items per feed
- Support for Mastodon media attachments in feed items
- Environment variable support for sensitive tokens
- Detailed logging options

## Installation

1. Clone this repository
2. Install dependencies (using pip or your preferred package manager)
3. Copy `.env.sample` to `.env` and configure your tokens
4. Copy `config.yaml.sample` to `config.yaml` and configure your settings

## Configuration

Create a `config.yaml` file with your settings:

```yaml
mastodon:
  access_token: "your-token-here"  # Can also be set via MASTODON_ACCESS_TOKEN env var
  mastodon_instance: "https://your.instance"
  mastodon_username: "yourusername"

# Optional Feedbin configuration
feedbin:
  starfeed: "https://feedbin.com/starred/..."

# Optional GitHub configuration
github:
  starfeed: "https://api.github.com/users/USERNAME/starred"
```

## Usage

```bash
# Basic usage (outputs to stdout)
python rss.py

# With options
python rss.py --config config.yaml --output feed.xml --limit 10 --debug

Options:
  -c, --config TEXT                  Path to configuration file (default: config.yaml)
  --debug / --no-debug              Enable debug output
  -o, --output TEXT                 Output file (optional, defaults to stdout)
  -l, --limit INTEGER               Number of feed items to include (default: 5)
  -L, --log-level [DEBUG|INFO|WARNING|ERROR|CRITICAL]
                                    Set logging level (default: ERROR)
  --help                            Show this message and exit
```

