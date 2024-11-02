import click
import requests
import logging
import yaml
import re
import feedparser
from datetime import datetime
from feedgen.feed import FeedGenerator
import json
import os
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

class MastodonRSSGenerator:
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
            
        with open(config_file) as f:
            config = yaml.safe_load(f)
        
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

    def _fetch_feedbin_stars(self) -> List[Dict]:
        """Fetch starred items from Feedbin RSS feed"""
        if 'feedbin' not in self.config or not self.config['feedbin'].get('starfeed'):
            logger.debug("No Feedbin configuration found, skipping")
            return []

        logger.debug(f"Fetching Feedbin stars")

        try:
            feed = feedparser.parse(self.config['feedbin']['starfeed'])
            
            feedbin_items = []
            for entry in feed.entries:
                # Convert Feedbin entry to a format similar to Mastodon items
                item = {
                    'id': f"feedbin_{entry.get('id', '')}",
                    'content': entry.get('description', ''),
                    'url': entry.get('link', ''),
                    'created_at': entry.get('published', ''),
                    'account': {
                        'username': 'Feedbin Star'
                    },
                    'media_attachments': []
                }
                feedbin_items.append(item)

            logger.debug(f"Found {len(feedbin_items)} Feedbin stars")
                
            return feedbin_items[:self.feed_item_limit]
            
        except Exception as e:
            logger.error(f"Error fetching Feedbin stars: {e}")
            return []

    def _fetch_github_stars(self) -> List[Dict]:
        """Fetch starred repositories from GitHub"""
        if 'github' not in self.config or not self.config['github'].get('starfeed'):
            logger.debug("No GitHub configuration found, skipping")
            return []

        logger.debug(f"Fetching GitHub stars")
                
        try:
            response = requests.get(self.config['github']['starfeed'])
            response.raise_for_status()
            stars = response.json()
            
            github_items = []
            for star in stars:
                # Convert GitHub star to a format similar to Mastodon items
                description = f"{star['description'] or ''}\n\nLanguage: {star['language'] or 'Not specified'}\n⭐ {star['stargazers_count']}"
                
                item = {
                    'id': f"github_{star['id']}",
                    'content': description,
                    'url': star['html_url'],
                    'created_at': star['created_at'],
                    'account': {
                        'username': f"GitHub: {star['owner']['login']}"
                    },
                    'media_attachments': []
                }
                github_items.append(item)

            logger.debug(f"Found {len(github_items)} GitHub stars")

            return github_items[:self.feed_item_limit]
            
        except Exception as e:
            logger.error(f"Error fetching GitHub stars: {e}")
            return []

    def _create_feed_item(self, feed: FeedGenerator, status: Dict):
        """Create an RSS feed item from a status"""
        entry = feed.add_entry()
        entry.id(status['id'])
        clean_content = self._strip_html(status['content'])[:100]
        title_text = clean_content.replace('&', '&#x26;').replace('<', '&#x3C;')
        entry.title(f"@{status['account']['username']}: {title_text}...")
        entry.link(href=status['url'])
        entry.published(datetime.fromisoformat(status['created_at'].replace('Z', '+00:00')))
        
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

    def generate_feed(self) -> str:
        """Generate the RSS feed"""

        # Mastodon
        mastodon_items_per_page = 40
        mastodon_instance = self.config['mastodon']['mastodon_instance']
        ## Fetch Mastodon favorites
        favorites = []
        next_url = f"{mastodon_instance}/api/v1/favourites?limit={mastodon_items_per_page}"
        while len(favorites) < self.feed_item_limit:
            data, next_url = self._fetch_mastodon_data(next_url)
            if not data:
                break
            favorites.extend(data)
            if len(data) < mastodon_items_per_page or not next_url:
                break
                
        ## Fetch Mastodon bookmarks
        bookmarks = []
        next_url = f"{mastodon_instance}/api/v1/bookmarks?limit={mastodon_items_per_page}"
        while len(bookmarks) < self.feed_item_limit:
            data, next_url = self._fetch_mastodon_data(next_url)
            if not data:
                break
            bookmarks.extend(data)

            if len(data) < mastodon_items_per_page or not next_url:
                break

        # Fetch stars from Feedbin
        feedbin_stars = self._fetch_feedbin_stars()

        # Fetch stars from GitHub
        github_stars = self._fetch_github_stars()

        # Combine and sort items
        all_items = {item['id']: item for item in favorites + bookmarks + feedbin_stars + github_stars}
        sorted_items = sorted(
            all_items.values(),
            key=lambda x: datetime.fromisoformat(x['created_at'].replace('Z', '+00:00')),
            reverse=True
        )

        # Create feed from the items above
        fg = FeedGenerator()
        fg.title(f"Mastodon Favorites and Bookmarks by @{self.config['mastodon_username']}")
        fg.link(href=f"{self.config['mastodon_instance']}/@{self.config['mastodon_username']}")
        fg.description(f"A feed of Mastodon favorites and bookmarks by @{self.config['mastodon_username']}")
        
        for item in sorted_items[:self.feed_item_limit]:
            self._create_feed_item(fg, item)
            
        return fg.rss_str(pretty=True)

@click.command()
@click.option('--config', '-c', default='config.yaml', help='Path to configuration file')
@click.option('--debug/--no-debug', default=False, help='Enable debug output')
@click.option('--output', '-o', help='Output file (optional, defaults to stdout)')
@click.option('--limit', '-l', default=5, help='Number of feed items to include', type=int)
@click.option('--log-level', '-L', 
    type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], case_sensitive=False),
    default='ERROR',
    help='Set logging level')
def main(config: str, debug: bool, output: Optional[str], limit: int, log_level: str):
    """Generate RSS feed from Mastodon favorites and bookmarks"""
    try:
        generator = MastodonRSSGenerator(config, feed_item_limit=limit, debug=debug, log_level=log_level)
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
