import pytest
from rss import StarRSSGenerator
import os
import tempfile
import yaml

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
