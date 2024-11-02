import click
import feedparser


@click.command()
@click.argument('feedfile', type=click.Path(exists=True))
def main(feedfile: str):

    feed = feedparser.parse(feedfile)
    
    if feed.bozo:
        print("Invalid RSS feed")
    else:
        print("Valid RSS feed")

if __name__ == '__main__':
    main()

    
