import sys

import click
import feedparser
import requests


def items(content: str) -> list[tuple]:
    """What readers see of each item; lastBuildDate and other noise are ignored."""
    return [(e.get("link"), e.get("title"), e.get("description"), e.get("published"),
             tuple(t.get("term") for t in e.get("tags", [])))
            for e in feedparser.parse(content).entries]


@click.command()
@click.argument('feedfile', type=click.File('r'))
@click.argument('live_url')
def main(feedfile, live_url):
    """Print changed=true if FEEDFILE has other items than the feed at LIVE_URL.

    The output is meant for $GITHUB_OUTPUT. If the live feed cannot be read,
    the feed counts as changed.
    """
    try:
        response = requests.get(live_url, timeout=30)
        response.raise_for_status()
        changed = items(feedfile.read()) != items(response.text)
    except requests.RequestException as exc:
        print(f"Cannot read {live_url}: {exc}", file=sys.stderr)
        changed = True
    print(f"changed={str(changed).lower()}")


if __name__ == '__main__':
    main()
