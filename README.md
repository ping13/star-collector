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
