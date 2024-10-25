import click
import requests
import logging
from datetime import datetime
from feedgen.feed import FeedGenerator
import json
import os
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Configure logging
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

class MastodonRSSGenerator:
    def __init__(self, config_file: str, debug: bool = False):
        self.config = self._load_config(config_file)
        if debug:
            logger.setLevel(logging.DEBUG)
        
    def _load_config(self, config_file: str) -> Dict:
        """Load configuration from file"""
        logger.debug(f"Loading configuration from {config_file}")
        
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Configuration file {config_file} not found")
            
        with open(config_file) as f:
            config = json.load(f)
            
        required_fields = ['access_token', 'mastodon_instance', 'mastodon_username']
        for field in required_fields:
            if not config.get(field):
                raise ValueError(f"Missing or empty {field} in config file")
                
        if not config.get('feed_item_limit') or not isinstance(config['feed_item_limit'], int):
            config['feed_item_limit'] = 5
            
        return config

    def _fetch_mastodon_data(self, url: str) -> Tuple[Optional[List], Optional[str]]:
        """Fetch data from Mastodon API"""
        logger.debug(f"Fetching data from: {url}")
        
        headers = {'Authorization': f"Bearer {self.config['access_token']}"}
        
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

    def _create_feed_item(self, feed: FeedGenerator, status: Dict):
        """Create an RSS feed item from a status"""
        entry = feed.add_entry()
        entry.id(status['id'])
        entry.title(f"@{status['account']['username']}: {status['content'][:100]}...")
        entry.link(href=status['url'])
        entry.published(datetime.fromisoformat(status['created_at'].replace('Z', '+00:00')))
        
        # Create description with media
        description = status['content']
        description += f"\n\n<p><a href='{status['url']}'>Link to original toot</a></p>"
        
        if status.get('media_attachments'):
            description += "\n\n<h3>Attachments:</h3>\n"
            for media in status['media_attachments']:
                if media['type'] == 'image':
                    description += f"<p><img src='{media['url']}' alt='{media.get('description', '')}' style='max-width:100%;'/></p>\n"
                elif media['type'] == 'video':
                    description += f"<p><video src='{media['url']}' controls style='max-width:100%;'>Your browser doesn't support video tags.</video></p>\n"
                else:
                    description += f"<p>Attachment: <a href='{media['url']}'>{media['type']}</a></p>\n"
                    
        entry.description(description)

    def generate_feed(self) -> str:
        """Generate the RSS feed"""
        items_per_page = 40
        favorites = []
        bookmarks = []
        
        # Fetch favorites
        next_url = f"{self.config['mastodon_instance']}/api/v1/favourites?limit={items_per_page}"
        while len(favorites) < self.config['feed_item_limit']:
            data, next_url = self._fetch_mastodon_data(next_url)
            if not data:
                break
            favorites.extend(data)
            if len(data) < items_per_page or not next_url:
                break
                
        # Fetch bookmarks
        next_url = f"{self.config['mastodon_instance']}/api/v1/bookmarks?limit={items_per_page}"
        while len(bookmarks) < self.config['feed_item_limit']:
            data, next_url = self._fetch_mastodon_data(next_url)
            if not data:
                break
            bookmarks.extend(data)
            if len(data) < items_per_page or not next_url:
                break

        # Combine and sort items
        all_items = {item['id']: item for item in favorites + bookmarks}
        sorted_items = sorted(
            all_items.values(),
            key=lambda x: datetime.fromisoformat(x['created_at'].replace('Z', '+00:00')),
            reverse=True
        )

        # Create feed
        fg = FeedGenerator()
        fg.title(f"Mastodon Favorites and Bookmarks by @{self.config['mastodon_username']}")
        fg.link(href=f"{self.config['mastodon_instance']}/@{self.config['mastodon_username']}")
        fg.description(f"A feed of Mastodon favorites and bookmarks by @{self.config['mastodon_username']}")
        
        for item in sorted_items[:self.config['feed_item_limit']]:
            self._create_feed_item(fg, item)
            
        return fg.rss_str(pretty=True)

@click.command()
@click.option('--config', '-c', default='mastodon_config.json', help='Path to configuration file')
@click.option('--debug/--no-debug', default=False, help='Enable debug output')
@click.option('--output', '-o', help='Output file (optional, defaults to stdout)')
def main(config: str, debug: bool, output: Optional[str]):
    """Generate RSS feed from Mastodon favorites and bookmarks"""
    try:
        generator = MastodonRSSGenerator(config, debug)
        feed_content = generator.generate_feed()
        
        if output:
            with open(output, 'wb') as f:
                f.write(feed_content)
        else:
            click.echo(feed_content.decode('utf-8'))
            
    except Exception as e:
        logger.error(f"Error generating feed: {e}")
        raise click.ClickException(str(e))

if __name__ == '__main__':
    main()
