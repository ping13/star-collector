import pytest
from unittest.mock import patch, MagicMock
from extract_titles import extract_title

@pytest.fixture
def mock_pipeline():
    with patch('extract_titles.pipeline') as mock:
        # Create a mock pipeline that returns a predictable result
        pipeline_instance = MagicMock()
        pipeline_instance.return_value = [{'summary_text': 'Generated Title'}]
        mock.return_value = pipeline_instance
        yield mock

@pytest.fixture
def mock_tokenizer():
    with patch('extract_titles.AutoTokenizer') as mock:
        tokenizer_instance = MagicMock()
        # Configure the tokenizer to return different lengths of tokens for testing
        tokenizer_instance.tokenize.return_value = ['token1', 'token2', 'token3']
        mock.from_pretrained.return_value = tokenizer_instance
        yield mock

@pytest.fixture
def mock_cache():
    with patch('extract_titles.cache') as mock:
        # Make memoize return the original function without caching
        mock.memoize.return_value = lambda f: f
        yield mock

def test_short_text(mock_pipeline, mock_tokenizer, mock_cache):
    """Test that short text is returned as-is"""
    mock_tokenizer.from_pretrained.return_value.tokenize.return_value = ['token1', 'token2']
    text = "This is a short text"
    result = extract_title(text)
    assert result == text

def test_long_text_generates_title(mock_pipeline, mock_tokenizer, mock_cache):
    """Test that long text generates a title"""
    # Configure tokenizer to return many tokens (indicating long text)
    mock_tokenizer.from_pretrained.return_value.tokenize.return_value = ['token'] * 30
    
    # Configure pipeline to return a specific title
    mock_pipeline.return_value.return_value = [{'summary_text': 'Generated Title'}]
    
    text = "This is a very long text that should generate a title" * 10
    result = extract_title(text)
    assert isinstance(result, str)
    assert len(result) < 160 # be on the safe side

