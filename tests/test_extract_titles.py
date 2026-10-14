import pytest
import requests

import extract_titles
from extract_titles import extract_title, fallback_title


class FakeCache(dict):
    def set(self, key, value):
        self[key] = value


class FakeResponse:
    def __init__(self, content="Generated Title", status=200, choices=True):
        self.status_code = status
        self._content = content
        self._choices = choices

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        if not self._choices:
            return {"choices": []}
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.fixture(autouse=True)
def env(monkeypatch, mocker):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(extract_titles, "cache", FakeCache())
    monkeypatch.setattr(extract_titles, "_new_titles", 0)
    monkeypatch.setattr(extract_titles, "settings", dict(extract_titles.DEFAULTS))
    mocker.patch("tenacity.nap.time.sleep")  # no waiting on retries


@pytest.fixture
def post(mocker):
    return mocker.patch("extract_titles.requests.post", return_value=FakeResponse())


def test_generates_title(post):
    assert extract_title("Some toot about Python packaging") == "Generated Title"
    body = post.call_args.kwargs["json"]
    assert body["model"] == extract_titles.DEFAULTS["model"]
    assert "Python packaging" in body["messages"][1]["content"]


def test_reasoning_disabled(post):
    extract_title("text")
    assert post.call_args.kwargs["json"]["reasoning"] == {"enabled": False}


def test_short_text_still_generates(post):
    extract_title("Hi")
    assert post.call_count == 1


def test_postprocessing(post):
    post.return_value = FakeResponse('"A very long headline ' + "word " * 30 + '."')
    title = extract_title("text")
    assert not title.startswith('"')
    assert len(title) <= extract_titles.MAX_TITLE_CHARS + 1


def test_body_is_truncated(post):
    extract_title("x " * 5000)
    prompt = post.call_args.kwargs["json"]["messages"][1]["content"]
    assert len(prompt) <= extract_titles.DEFAULTS["max_body_chars"]


def test_cache_hit_skips_api(post):
    extract_title("same text")
    extract_title("same text")
    assert post.call_count == 1


def test_missing_key_uses_fallback(post, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert extract_title("plain text") == "plain text"
    post.assert_not_called()


def test_http_error_uses_fallback_model_then_text(post):
    post.return_value = FakeResponse(status=400)
    assert extract_title("plain text") == "plain text"
    models = [c.kwargs["json"]["model"] for c in post.call_args_list]
    assert models == [extract_titles.DEFAULTS["model"], extract_titles.DEFAULTS["fallback_model"]]
    assert not extract_titles.cache  # failures are not cached


def test_fallback_model_used_when_primary_empty(post):
    post.side_effect = [FakeResponse(choices=False), FakeResponse("From Fallback")]
    assert extract_title("text") == "From Fallback"


def test_transient_errors_are_retried(post):
    post.side_effect = [FakeResponse(status=503), FakeResponse("After Retry")]
    assert extract_title("text") == "After Retry"
    assert post.call_count == 2


def test_budget_guard(post):
    extract_titles.configure({"max_new_titles_per_run": 1})
    assert extract_title("first") == "Generated Title"
    assert extract_title("second") == "second"
    assert post.call_count == 1


def test_context_is_used_when_text_empty(post):
    assert extract_title("", context="Card title") == "Card title"
    post.assert_not_called()


def test_fallback_title_truncates():
    assert len(fallback_title("word " * 100)) <= extract_titles.MAX_TITLE_CHARS + 1
