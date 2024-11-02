# Mastodon RSS Generator

A Python script to generate RSS/Atom feeds from your Mastodon favorites and bookmarks.

## Installation

1. Clone this repository
2. Install dependencies using your preferred Python package manager:
   ```bash
   pip install .
   ```

## Configuration

1. Copy `mastodon_config.json.example` to `mastodon_config.json`
2. Edit `mastodon_config.json` with your:
   - Mastodon access token
   - Instance URL
   - Username
   - Desired feed item limit

## Usage

Generate RSS feed:
```bash
python rss.py --config mastodon_config.json
```

Options:
- `--config/-c`: Path to config file (default: mastodon_config.json)
- `--debug`: Enable debug output
- `--output/-o`: Output file (default: stdout)

## License

This project is licensed under the MIT License - see the LICENSE file for details.
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

## License

[Include your license information here]
