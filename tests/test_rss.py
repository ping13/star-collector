import pytest
from rss import StarRSSGenerator
import os
import tempfile
import yaml
import feedparser
from datetime import datetime
import xml.etree.ElementTree as ET

@pytest.fixture
def sample_config():
    config = {
        'mastodon': {
            'access_token': 'test_token',
            'mastodon_instance': 'https://test.social',
            'mastodon_username': 'test_user',
            'types': ['favourites']
        },
        'rss': {
            'urls': [
                {'url': 'https://test.com/feed', 'tag': 'test'},
            ],
            'exclude_categories': ['private', 'personal']
        }
    }
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        yaml.dump(config, f)
        config_path = f.name
    
    yield config_path
    os.unlink(config_path)

@pytest.fixture
def sample_mastodon_status():
    return {
        'id': '123456',
        'content': '<p>Test toot content</p>',
        'url': 'https://test.social/@user/123456',
        'created_at': '2024-03-14T12:00:00.000Z',
        'visibility': 'public',
        'account': {
            'display_name': 'Test User',
            'url': 'https://test.social/@user'
        },
        'media_attachments': [
            {
                'type': 'image',
                'url': 'https://test.social/media/123.jpg',
                'preview_url': 'https://test.social/media/123_small.jpg'
            }
        ]
    }

@pytest.fixture
def generator(sample_config):
    return StarRSSGenerator(sample_config)

def test_config_loading(sample_config):
    generator = StarRSSGenerator(sample_config)
    assert generator.config['mastodon']['mastodon_username'] == 'test_user'
    assert 'exclude_categories' in generator.config['rss']
    assert 'private' in generator.config['rss']['exclude_categories']

def test_feed_item_limit():
    generator = StarRSSGenerator('config.yaml', feed_item_limit=10)
    assert generator.feed_item_limit == 10

def test_invalid_config_file():
    with pytest.raises(FileNotFoundError):
        StarRSSGenerator('nonexistent.yaml')

def test_generate_feed_structure(generator, mocker):
    # Mock the network calls
    mocker.patch.object(generator, '_fetch_mastodon_data', return_value=([], None))
    mocker.patch.object(generator, '_fetch_rss_feeds', return_value=True)
    
    feed_content = generator.generate_feed()
    # Parse the feed content
    feed = feedparser.parse(feed_content)
    
    # Basic feed structure tests
    assert feed.version.startswith('rss')
    assert feed.feed.title.startswith('Star Collection for')
    assert 'description' in feed.feed
    assert 'link' in feed.feed

def test_create_feed_item_from_mastodon(generator, sample_mastodon_status, mocker):
    # Mock any potential network calls
    mocker.patch('extract_titles.extract_title', return_value='Test Title')
    
    from feedgen.feed import FeedGenerator
    fg = FeedGenerator()
    # Add required feed properties
    fg.title('Test Feed')
    fg.link(href='http://example.com')
    fg.description('Test Description')
    
    success = generator._create_feed_item_from_mastodon(fg, sample_mastodon_status)
    assert success is True
    
    # Get the generated RSS
    feed_content = fg.rss_str()
    feed = feedparser.parse(feed_content)
    
    assert len(feed.entries) == 1
    entry = feed.entries[0]
    assert entry.id == '123456'
    assert 'Test toot content' in entry.description
    assert entry.link == 'https://test.social/@user/123456'
    assert entry.published_parsed is not None

def test_private_toot_handling(generator):
    private_status = {
        'id': '123456',
        'content': 'Private toot',
        'visibility': 'private',
        'created_at': '2024-03-14T12:00:00.000Z',
        'account': {
            'display_name': 'Test User',
            'url': 'https://test.social/@user'
        }
    }
    
    from feedgen.feed import FeedGenerator
    fg = FeedGenerator()
    # Add required feed properties
    fg.title('Test Feed')
    fg.link(href='http://example.com')
    fg.description('Test Description')
    
    # Test that private toot is not added
    success = generator._create_feed_item_from_mastodon(fg, private_status)
    assert success is False
    
    # Verify no entries were added
    feed_content = fg.rss_str()
    feed = feedparser.parse(feed_content)
    assert len(feed.entries) == 0

def test_exclude_categories_handling(generator):
    # Create a test feed with entries that should and shouldn't be excluded
    from feedgen.feed import FeedGenerator
    test_fg = FeedGenerator()
    test_fg.title('Test Feed')
    test_fg.link(href='http://example.com')
    test_fg.description('Test Description')
    
    # Create a test entry that should NOT be excluded
    fe = test_fg.add_entry()
    fe.title('Public Entry')
    fe.link(href='http://example.com/1')
    fe.description('Public Description')
    fe.category([{'term': 'public'}])

    # Create a test entry that should be excluded (has 'private' category)
    fe = test_fg.add_entry()
    fe.title('Private Entry')
    fe.link(href='http://example.com/2')
    fe.description('Private Description')
    fe.category([{'term': 'private'}])
    
    # Mock feedparser.parse to return our test feed
    def mock_parse(url):
        feed_content = test_fg.rss_str()
        return feedparser.parse(feed_content)
    
    # Create a generator with exclude_categories configuration
    config = {
        'mastodon': {
            'access_token': 'test_token',
            'mastodon_instance': 'https://test.social',
            'mastodon_username': 'test_user',
            'types': ['favourites']
        },
        'rss': {
            'urls': [{'url': 'http://example.com/feed', 'tag': 'test'}],
            'exclude_categories': ['private']
        }
    }
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        yaml.dump(config, f)
        config_path = f.name
    
    test_generator = StarRSSGenerator(config_path)
    
    # Create output feed
    fg = FeedGenerator()
    fg.title('Output Feed')
    fg.link(href='http://example.com')
    fg.description('Output Description')
    
    # Mock feedparser.parse
    with pytest.mock.patch('feedparser.parse', side_effect=mock_parse):
        # Call the method that implements the exclusion logic
        test_generator._fetch_rss_feeds(fg)
    
    # Generate and parse resulting feed
    feed_content = fg.rss_str()
    feed = feedparser.parse(feed_content)
    
    # Verify only entries without excluded categories are present
    entries_with_private = [
        entry for entry in feed.entries 
        if any(tag['term'] == 'private' for tag in getattr(entry, 'tags', []))
    ]
    assert len(entries_with_private) == 0  # No entries with 'private' category should be present
    
    # Verify public entries are still there
    entries_with_public = [
        entry for entry in feed.entries 
        if any(tag['term'] == 'public' for tag in getattr(entry, 'tags', []))
    ]
    assert len(entries_with_public) == 1  # Public entry should be present

def test_iso_datetime_conversion(generator):
    # Test various date formats
    test_dates = [
        ('2024-03-14T12:00:00Z', '2024-03-14T12:00:00+00:00'),
        ('Thu, 14 Mar 2024 12:00:00 +0000', '2024-03-14T12:00:00+00:00'),
    ]
    
    for input_date, expected in test_dates:
        result = generator._ensure_iso_datetime(input_date)
        assert result == expected
