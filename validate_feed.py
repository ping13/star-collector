import click
import feedparser
import sys


@click.command()
@click.argument('feedfile', type=click.File('r'), default='-')
def main(feedfile):
    """Validate RSS feed from file or stdin. Use - for stdin."""
    
    content = feedfile.read()
    feed = feedparser.parse(content)
    
    if feed.bozo:
        print("Invalid RSS feed")
    else:
        print("Valid RSS feed")

if __name__ == '__main__':
    main()
