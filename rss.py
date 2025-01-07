import sys
import click
import requests
import logging
from envyaml import EnvYAML
import re
import feedparser
from datetime import datetime
import dateutil
from feedgen.feed import FeedGenerator
import json
import os
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse
from dotenv import load_dotenv
from html2text import html2text, HTML2Text
import extract_titles

# Configure logging
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)


text_maker = HTML2Text()
text_maker.ignore_links = True
text_maker.ignore_images = True

def is_iso_format(date_str):
    try:
        dateutil.parser.isoparse(date_str)
        return True
    except ValueError:
        return False
    
class StarRSSGenerator:
    def __init__(self, config_file: str, feed_item_limit: int = 5, debug: bool = False, log_level: str = 'ERROR'):
        # Set log level first
        logger.setLevel(getattr(logging, log_level.upper()))
        # Then override with debug if specified
        if debug:
            logger.setLevel(logging.DEBUG)
        self.config = self._load_config(config_file)
        self.feed_item_limit = feed_item_limit
        
    def _load_config(self, config_file: str) -> Dict:
        """Load configuration from file"""
        logger.debug(f"Loading configuration from {config_file}")
        
        # Load environment variables
        load_dotenv()
        
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Configuration file {config_file} not found")
            
        config = EnvYAML(config_file)
        
        if 'mastodon' not in config:
            raise ValueError("Missing 'mastodon' section in config file")

        # Override access_token from environment if available
        if os.getenv('MASTODON_ACCESS_TOKEN'):
            config['mastodon']['access_token'] = os.getenv('MASTODON_ACCESS_TOKEN')
        
        required_fields = ['access_token', 'mastodon_instance', 'mastodon_username']
        for field in required_fields:
            if not config['mastodon'].get(field):
                raise ValueError(f"Missing or empty {field} in mastodon config section")
                
        return config

    def _fetch_mastodon_data(self, url: str) -> Tuple[Optional[List], Optional[str]]:
        """Fetch data from Mastodon API"""
        logger.debug(f"Fetching data from: {url}")
        
        headers = {'Authorization': f"Bearer {self.config['mastodon']['access_token']}"}
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            next_url = None
            if 'Link' in response.headers:
                links = requests.utils.parse_header_links(response.headers['Link'])
                for link in links:
                    if link.get('rel') == 'next':
                        next_url = link.get('url')
                        
            return response.json(), next_url
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching data: {e}")
            return None, None

    def _strip_html(self, text: str) -> str:
        """Remove HTML tags from text"""
        clean = re.compile('<.*?>')
        return re.sub(clean, '', text)

    def _ensure_iso_datetime(self, date_str: str) -> str:
        """Convert various datetime strings to ISO format with UTC timezone"""
        try:
            # Parse the date string to datetime
            if is_iso_format(date_str):  # Already ISO-like format
                dt = dateutil.parser.isoparse(date_str)
            else:  # Try parsing other formats
                dt = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %z")
            # Return in ISO format with UTC timezone
            return dt.isoformat()
        except Exception as e:
            logger.warning(f"Date parsing error: {e} for date: {date_str}")
            return date_str

    def _fetch_rss_feeds(self, fg) -> bool:
        """Fetch starred items from Feedbin RSS feed"""
        if 'rss' not in self.config or not self.config['rss']:
            logger.debug("No Feedbin configuration found, skipping")
            return False

        for url in self.config["rss"]["urls"]:
            try:
                feed = feedparser.parse(url)

                # Sort entries by published date (newest first)
                sorted_entries = sorted(
                    feed.entries,
                    key=lambda entry: entry.published_parsed if hasattr(entry, "published_parsed") else None,
                    reverse=True
                )
                
                for entry in sorted_entries[:self.feed_item_limit]:
                    fe = fg.add_entry()
                    fe.title(entry.title)
                    fe.link(href=entry.link)
                    fe.description(entry.description)
                    fe.pubDate(entry.published)

                    if hasattr(entry, "tags"):
                        # for some reason, feedparser generates attributes in the
                        # tags with value None. this irritates feedgen, so I filter
                        # the attributes with None value in here.
                        filtered_tags = [
                            {k: v for k, v in d.items() if v is not None}
                            for d in entry.tags
                        ]
                        fe.category(filtered_tags)

            except Exception as e:
                logger.error(f"Error fetching RSS feed for {url}: {e}")
                raise

        return True
                

    def _create_feed_item_from_mastodon(self, feed: FeedGenerator, status: Dict):
        """Create an RSS feed item from a status"""
        
        if status.get("visibility") != "public":
            logger.info("Ignoring non-public toot")
            return False

        entry = feed.add_entry()
        entry.id(status['id'])
        content = status['content'] 
        title_text = extract_titles.extract_title(text_maker.handle(content))
        assert isinstance(title_text, str), "title is not a string"
        entry.title(f"{title_text} (@{status['account']['username']})")
        entry.link(href=status['url'])
        entry.published(dateutil.parser.isoparse(self._ensure_iso_datetime(status['created_at'])))
        entry.category([{'term': 'Mastodon'}])
        
        # Create description with media
        description = status['content']
        description += f"\n\n<p><a href='{status['url']}'>Link to original toot</a></p>"
        
        if status.get('media_attachments'):
            description += "\n\n<h3>Attachments:</h3>\n"
            for media in status['media_attachments']:
                if media['type'] == 'image':
                    description += f"<p><img src='{media['url']}' alt='{media.get('description', '')}' width='100%'/></p>\n"
                elif media['type'] == 'video':
                    description += f"<p><video src='{media['url']}' controls width='100%'>Your browser doesn't support video tags.</video></p>\n"
                else:
                    description += f"<p>Attachment: <a href='{media['url']}'>{media['type']}</a></p>\n"
                    
        entry.description(description)
        return True

    def generate_feed(self) -> str:
        """Generate the RSS feed"""

        # We always assume there is a mastodon config
        
        mastodon_items_per_page = min(40, self.feed_item_limit)
        mastodon_instance = self.config['mastodon']['mastodon_instance']

        assert isinstance(self.config['mastodon']["types"], list), "Bad Configuration, expect a list for mastodon.types"
                
        # Create feed from the items above
        fg = FeedGenerator()
        mastodon_config = self.config['mastodon']
        fg.title(f"Star Collection for {mastodon_config['mastodon_username']}")
        fg.link(href=f"{mastodon_config['mastodon_instance']}/@{mastodon_config['mastodon_username']}")
        fg.description(f"A collection of favourites on multiple platforms by @{mastodon_config['mastodon_username']}")

        
        # Mastodon favorites and bookmarks
        mastodon_items = []        
        for item_type in self.config['mastodon']["types"]:
            ## Fetch Mastodon favorites
            next_url = f"{mastodon_instance}/api/v1/{item_type}?limit={mastodon_items_per_page}"
            while len(mastodon_items) < self.feed_item_limit:
                data, next_url = self._fetch_mastodon_data(next_url)
                if not data:
                    break
                mastodon_items.extend(data)
                if len(data) < mastodon_items_per_page or not next_url:
                    break

        # remove possible duplicates of Mastodon favorites and bookmarks
        all_items = {item['id']: item for item in mastodon_items}
        sorted_items = sorted(
            all_items.values(),
            key=lambda x: dateutil.parser.isoparse(self._ensure_iso_datetime(x['created_at'])),
            reverse=True
        )                
        for item in sorted_items:
            self._create_feed_item_from_mastodon(fg, item)
            
        # Fetch items from other RSS feeds
        self._fetch_rss_feeds(fg)



        # hack: sort ascending
        fg._FeedGenerator__feed_entries = sorted(
            fg._FeedGenerator__feed_entries,
            key=lambda x: x.pubDate(),
            reverse=True
        )              

        return fg.rss_str(pretty=True)

@click.command()
@click.option('--config', '-c', default='config.yaml', help='Path to configuration file')
@click.option('--debug/--no-debug', default=False, help='Enable debug output')
@click.option('--output', '-o', help='Output file (optional, defaults to stdout)')
@click.option('--limit', '-l', default=5, help='Number of feed items to include per source', type=int)
@click.option('--log-level', '-L', 
    type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], case_sensitive=False),
    default='ERROR',
    help='Set logging level')
def main(config: str, debug: bool, output: Optional[str], limit: int, log_level: str):
    """Generate RSS feed from Mastodon favorites and bookmarks"""
    try:
        generator = StarRSSGenerator(config, feed_item_limit=limit, debug=debug, log_level=log_level)
        feed_content = generator.generate_feed()
        
        if output:
            with open(output, 'wb') as f:
                f.write(feed_content)
        else:
            click.echo(feed_content.decode('utf-8'))
            
    except Exception as e:
        logger.error(f"Error generating feed: {e}")
        raise #click.ClickException(str(e))

if __name__ == '__main__':
    main()
