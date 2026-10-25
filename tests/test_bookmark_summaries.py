from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

import bookmark_summaries as bs

NOW = datetime.now(timezone.utc)
YT_GENERIC = ("Auf YouTube findest du die angesagtesten Videos und Tracks. Außerdem kannst du "
              "eigene Inhalte hochladen und mit Freunden oder gleich der ganzen Welt teilen.")
REAL = "A long, hand-written description of what this article is actually about."
SETTINGS = dict(bs.DEFAULTS, exclude_tags=["private"])


def bookmark(id, url, description="", days_ago=1, tags=()):
    added = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {"id": id, "url": url, "title": f"Title {id}", "description": description,
            "date_added": added, "tag_names": list(tags)}


@pytest.fixture(autouse=True)
def no_homepages(mocker):
    return mocker.patch("bookmark_summaries.homepage_description", return_value="")


@pytest.mark.parametrize("url, expected", [
    ("https://m.youtube.com/watch?v=abc&ra=m", "https://www.youtube.com/watch?v=abc"),
    ("https://m.youtube.com/watch?v=abc&pp=ugUEEgJlbg%3D%3D", "https://www.youtube.com/watch?v=abc"),
    ("https://m.youtube.com/watch%3Fv%3DMiUHjLxm3V0", "https://www.youtube.com/watch?v=MiUHjLxm3V0"),
    ("https://youtube.com/watch?v=abc&si=xyz", "https://youtube.com/watch?v=abc"),
    ("https://en.m.wikipedia.org/wiki/Foo", "https://en.wikipedia.org/wiki/Foo"),
    ("https://example.com/a?id=3&utm_source=x", "https://example.com/a?id=3"),
    ("https://media.ccc.de/v/talk%23t%3D2209", "https://media.ccc.de/v/talk"),
])
def test_normalize_url(url, expected):
    assert bs.normalize_url(url) == expected


def test_finds_empty_short_and_shared_descriptions():
    bookmarks = [
        bookmark(1, "https://m.youtube.com/watch?v=a", YT_GENERIC),
        bookmark(2, "https://m.youtube.com/watch?v=b", YT_GENERIC),
        bookmark(3, "https://example.com/empty"),
        bookmark(4, "https://example.com/missing", "404 Fehler"),
        bookmark(5, "https://example.com/real", REAL),
        bookmark(6, "https://example.com/note", "Cool WebGPU demo"),
    ]
    reasons = {b["id"]: r for b, r in bs.find_candidates(bookmarks, SETTINGS, NOW - timedelta(days=7))}
    assert reasons == {1: "shared by 2 bookmarks", 2: "shared by 2 bookmarks",
                       3: "empty", 4: "looks like an error page"}


def test_same_url_twice_is_not_shared():
    bookmarks = [bookmark(1, "https://m.youtube.com/watch?v=a&ra=m", REAL),
                 bookmark(2, "https://www.youtube.com/watch?v=a", REAL)]
    assert bs.find_candidates(bookmarks, SETTINGS, NOW - timedelta(days=7)) == []


def test_older_shared_bookmarks_count_but_are_not_candidates():
    bookmarks = [bookmark(1, "https://m.youtube.com/watch?v=a", YT_GENERIC),
                 bookmark(2, "https://m.youtube.com/watch?v=b", YT_GENERIC, days_ago=30)]
    candidates = bs.find_candidates(bookmarks, SETTINGS, NOW - timedelta(days=7))
    assert [(b["id"], r) for b, r in candidates] == [(1, "shared by 2 bookmarks")]


def test_skips_excluded_tags():
    bookmarks = [bookmark(1, "https://example.com/a", tags=["private"])]
    assert bs.find_candidates(bookmarks, SETTINGS, NOW - timedelta(days=7)) == []


def test_homepage_description_counts_as_generic(no_homepages):
    no_homepages.side_effect = lambda host: REAL if host == "example.com" else ""
    bookmarks = [bookmark(1, "https://example.com/a", REAL), bookmark(2, "https://other.org/b", REAL + " ")]
    reasons = {b["id"]: r for b, r in bs.find_candidates(bookmarks, SETTINGS, NOW - timedelta(days=7))}
    # bookmark 2 has the same text (after whitespace cleanup) but on another URL: shared
    assert reasons == {1: "shared by 2 bookmarks", 2: "shared by 2 bookmarks"}
    bookmarks = [bookmark(1, "https://example.com/a", REAL)]
    reasons = {b["id"]: r for b, r in bs.find_candidates(bookmarks, SETTINGS, NOW - timedelta(days=7))}
    assert reasons == {1: "same as the site's homepage description"}
    # a bookmarked homepage rightly carries the homepage description
    assert bs.find_candidates([bookmark(1, "https://example.com/", REAL)], SETTINGS,
                              NOW - timedelta(days=7)) == []


def test_load_settings_merges_rss_exclusions_and_title_model(tmp_path):
    config = tmp_path / "c.yaml"
    config.write_text("mastodon:\n  access_token: ${UNSET}\nrss:\n  exclude_categories: [private]\n"
                      "titles:\n  model: a/haiku\n  fallback_model: b/qwen\n"
                      "bookmarks:\n  days: 3\n  exclude_tags: [secret]\n  fallback_model: c/x\n  unknown: 1\n")
    settings = bs.load_settings(str(config))
    assert settings["days"] == 3
    assert settings["exclude_tags"] == ["private", "secret"]
    assert (settings["model"], settings["fallback_model"]) == ("a/haiku", "c/x")
    assert "unknown" not in settings


class Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise bs.requests.HTTPError(response=self)


MODEL_SETTINGS = dict(SETTINGS, model="a/haiku", fallback_model="b/qwen")


def openrouter(text, cost=0.001):
    return Response(200, {"choices": [{"message": {"content": text}}], "usage": {"cost": cost}})


def test_extract_page_strips_links_and_images(mocker):
    markdown = "# Title\n\n![logo](https://x/logo.png)Read [the paper](https://x/p.pdf) now.\n\n\n\nEnd"
    post = mocker.patch("bookmark_summaries.requests.post", return_value=Response(
        200, {"data": [{"url": "https://x", "markdown": markdown}]}))
    assert bs.extract_page("https://x", "tok") == "# Title\n\nRead the paper now.\n\nEnd"
    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer tok"}
    assert post.call_args.kwargs["json"]["pages"] == [{"url": "https://x"}]


@pytest.mark.parametrize("response, message", [
    (Response(200, {"data": [{"url": "https://x", "error": "timeout"}]}), "timeout"),
    (Response(200, {"data": [], "errors": [{"message": "Count of URLs"}]}), "Count of URLs"),
    (Response(401, {"errors": [{"message": "Unauthorized"}]}), "v1 key"),
])
def test_extract_page_errors(mocker, response, message):
    mocker.patch("bookmark_summaries.requests.post", return_value=response)
    with pytest.raises(bs.SummaryError, match=message):
        bs.extract_page("https://x", "tok")


def test_summarize(mocker):
    post = mocker.patch("bookmark_summaries.requests.post", return_value=openrouter(" Two  sentences.\n", 0.002))
    assert bs.summarize("T", "https://x", "page " * 10_000, MODEL_SETTINGS, "key") == ("Two sentences.", 0.002)
    body = post.call_args.kwargs["json"]
    assert body["model"] == "a/haiku"
    assert "in English" in body["messages"][0]["content"]
    assert len(body["messages"][1]["content"]) < MODEL_SETTINGS["max_page_chars"] + 100


def test_summarize_skip_and_fallback(mocker):
    mocker.patch("bookmark_summaries.requests.post", return_value=openrouter("SKIP."))
    assert bs.summarize("T", "https://x", "Error 404", MODEL_SETTINGS, "key") == (None, 0.001)
    # the first model fails for good, the fallback answers
    failing = Response(400, {})
    post = mocker.patch("bookmark_summaries.requests.post", side_effect=[failing, openrouter("Fine.")])
    assert bs.summarize("T", "https://x", "text", MODEL_SETTINGS, "key") == ("Fine.", 0.001)
    assert [c.kwargs["json"]["model"] for c in post.call_args_list] == ["a/haiku", "b/qwen"]


def test_summarize_fails_when_all_models_fail(mocker):
    mocker.patch("bookmark_summaries.requests.post", return_value=Response(400, {}))
    with pytest.raises(bs.SummaryError, match="a/haiku"):
        bs.summarize("T", "https://x", "text", MODEL_SETTINGS, "key")


@pytest.mark.parametrize("url, item, expected", [
    ("https://news.ycombinator.com/item?id=1", {"type": "story", "url": "https://a.org/x"}, "https://a.org/x"),
    ("https://news.ycombinator.com/item?id=2", {"type": "story", "title": "Ask HN: …"}, None),
    ("https://news.ycombinator.com/item?id=3", {"type": "comment", "text": "…"}, None),
    ("https://example.com/item?id=4", None, None),
])
def test_hn_article_url(mocker, url, item, expected):
    get = mocker.patch("bookmark_summaries.requests.get", return_value=Response(200, item))
    assert bs.hn_article_url(url) == expected
    if item is None:
        get.assert_not_called()


@pytest.fixture
def cli(mocker, tmp_path, monkeypatch):
    for name in ("LINKDING_API_TOKEN", "KAGI_API_TOKEN", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(name, name.lower())
    mocker.patch("bookmark_summaries.load_dotenv")
    config = tmp_path / "c.yaml"
    config.write_text("titles:\n  model: a/haiku\nbookmarks:\n  max_summaries_per_run: 2\n")
    mocker.patch("bookmark_summaries.fetch_bookmarks", return_value=[
        bookmark(1, "https://m.youtube.com/watch?v=a&ra=m"),
        bookmark(2, "https://news.ycombinator.com/item?id=7", days_ago=2),
        bookmark(3, "https://example.com/c", days_ago=3)])
    mocker.patch("bookmark_summaries.hn_article_url",
                 side_effect=lambda url: "https://a.org/story" if "ycombinator" in url else None)
    extract = mocker.patch("bookmark_summaries.extract_page", return_value="page text")
    summarize = mocker.patch("bookmark_summaries.summarize", return_value=("A summary.", 0.002))
    update = mocker.patch("bookmark_summaries.update_description")
    return lambda *args: CliRunner().invoke(bs.main, ["-c", str(config), *args]), extract, summarize, update


def test_dry_run_calls_nothing(cli):
    run, extract, summarize, update = cli
    result = run()
    assert result.exit_code == 0, result.output
    assert "3 with an empty or generic description" in result.output
    assert "fetch:   https://www.youtube.com/watch?v=a" in result.output
    assert "fetch:   https://a.org/story  (article of the HN story)" in result.output
    extract.assert_not_called()
    summarize.assert_not_called()
    update.assert_not_called()


def test_summarize_does_not_write(cli):
    run, extract, summarize, update = cli
    result = run("--summarize")
    assert result.exit_code == 0, result.output
    assert [c.args[0] for c in extract.call_args_list] == ["https://www.youtube.com/watch?v=a",
                                                            "https://a.org/story"]
    assert "summary: A summary." in result.output
    update.assert_not_called()


def test_apply_writes_up_to_the_limit(cli):
    run, extract, summarize, update = cli
    result = run("--apply")
    assert result.exit_code == 0, result.output
    assert [c.args[2:] for c in update.call_args_list] == [(1, "A summary."), (2, "A summary.")]
    assert "2 summarized, 0 skipped, 0 failed; about $0.012" in result.output


def test_skipped_pages_are_not_written(cli):
    run, extract, summarize, update = cli
    summarize.return_value = (None, 0.001)
    result = run("--apply")
    assert result.exit_code == 0, result.output
    assert "0 summarized, 2 skipped" in result.output
    update.assert_not_called()


def test_fails_only_when_every_summary_fails(cli):
    run, extract, summarize, update = cli
    extract.side_effect = [bs.SummaryError("timeout"), "page text"]
    assert run("--apply").exit_code == 0
    extract.side_effect = bs.SummaryError("Kagi rejected the API key")
    result = run("--apply")
    assert result.exit_code == 1
    assert "failed:  Kagi rejected the API key" in result.output


def test_hours_window(cli):
    run, extract, summarize, update = cli
    result = run("--hours", "36")  # bookmarks 1 (1 day old) only
    assert result.exit_code == 0, result.output
    assert "1 with an empty or generic description added in the last 36 hours" in result.output
