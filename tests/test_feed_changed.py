import requests
from click.testing import CliRunner

import feed_changed


def rss(build_date, *items):
    entries = "".join(f"<item><title>{t}</title><link>https://x/{t}</link>"
                      f"<description>{d}</description></item>" for t, d in items)
    return (f'<?xml version="1.0"?><rss version="2.0"><channel><title>Stars</title>'
            f"<lastBuildDate>{build_date}</lastBuildDate>{entries}</channel></rss>")


class Live:
    def __init__(self, text=None, error=None):
        self.text, self.error = text, error

    def raise_for_status(self):
        if self.error:
            raise self.error


def run(mocker, tmp_path, new, live):
    mocker.patch("feed_changed.requests.get", return_value=live)
    feed = tmp_path / "new.rss"
    feed.write_text(new)
    return CliRunner().invoke(feed_changed.main, [str(feed), "https://example.com/stars.rss"]).output


def test_only_build_date_differs(mocker, tmp_path):
    output = run(mocker, tmp_path, rss("Mon", ("a", "1")), Live(rss("Sun", ("a", "1"))))
    assert output.strip() == "changed=false"


def test_new_or_edited_item(mocker, tmp_path):
    assert "changed=true" in run(mocker, tmp_path, rss("Mon", ("b", "2"), ("a", "1")), Live(rss("Sun", ("a", "1"))))
    assert "changed=true" in run(mocker, tmp_path, rss("Mon", ("a", "new text")), Live(rss("Sun", ("a", "1"))))


def test_unreadable_live_feed_counts_as_changed(mocker, tmp_path):
    live = Live(error=requests.HTTPError("404"))
    assert "changed=true" in run(mocker, tmp_path, rss("Mon", ("a", "1")), live)
