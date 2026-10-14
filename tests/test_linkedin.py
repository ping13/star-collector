import json
import time
from datetime import date

import pytest
import requests

import linkedin

NOW_MS = int(time.time() * 1000)


def like(urn, reaction="PRAISE", ts=NOW_MS, method="CREATE"):
    event = {"resourceName": "socialActions/likes", "resourceId": urn, "method": method,
             "capturedAt": ts, "processedAt": ts, "activity": {}}
    if method == "CREATE":
        event["activity"] = {"object": urn, "reactionType": reaction}
    return event


def test_excludes_like_and_comments():
    events = [like("urn:li:activity:1", "LIKE"), like("urn:li:activity:2", "PRAISE"),
              like("urn:li:comment:(urn:li:activity:9,3)", "PRAISE")]
    result = linkedin.reactions_from_events(events, ["LIKE"])
    assert list(result) == ["urn:li:activity:2"]
    assert result["urn:li:activity:2"]["reaction"] == "PRAISE"


def test_delete_removes_and_latest_event_wins():
    urn = "urn:li:ugcPost:5"
    events = [like(urn, "PRAISE", ts=1), like(urn, method="DELETE", ts=2)]
    assert linkedin.reactions_from_events(events, []) == {}
    events = [like(urn, "LIKE", ts=1), like(urn, "EMPATHY", ts=2)]
    assert linkedin.reactions_from_events(events, ["LIKE"])[urn]["reaction"] == "EMPATHY"
    # a plain LIKE that replaced a PRAISE no longer counts
    events = [like(urn, "PRAISE", ts=1), like(urn, "LIKE", ts=2)]
    assert linkedin.reactions_from_events(events, ["LIKE"]) == {}


def test_ignores_other_resources():
    event = {"resourceName": "invitations", "resourceId": "urn:li:activity:1", "method": "ACTION"}
    assert linkedin.reactions_from_events([event], []) == {}


def test_fetch_post_text(mocker):
    page = '<meta property="og:title" content="Title | Someone"><meta property="og:description" content="The  post\ntext">'
    mocker.patch("linkedin._fetch_page", return_value=page)
    assert linkedin.fetch_post_text("urn:li:activity:1") == "The post text"


def test_fetch_post_text_failure(mocker):
    mocker.patch("linkedin._fetch_page", side_effect=requests.ConnectionError("boom"))
    assert linkedin.fetch_post_text("urn:li:activity:1") is None


@pytest.fixture
def cfg(tmp_path):
    return {"state_file": str(tmp_path / "state.json"), "state_url": None, "max_items": 2}


def test_collect_merges_state_and_generates_title_once(mocker, monkeypatch, cfg):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    monkeypatch.delenv("LINKEDIN_TOKEN_EXPIRES", raising=False)
    mocker.patch("linkedin.fetch_events", return_value=[like("urn:li:activity:1")])
    text = mocker.patch("linkedin.fetch_post_text", return_value="Some post text")
    title = mocker.patch("linkedin.extract_titles.try_extract_title", return_value="A Headline")

    items = linkedin.collect(cfg)
    assert [i["title"] for i in items] == ["A Headline"]
    assert items[0]["url"].endswith("/feed/update/urn:li:activity:1/")

    linkedin.collect(cfg)  # second run: title comes from state, no new lookups
    assert text.call_count == 1 and title.call_count == 1
    assert json.load(open(cfg["state_file"]))["urn:li:activity:1"]["title"] == "A Headline"


def test_collect_generic_title_when_no_text(mocker, monkeypatch, cfg):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    mocker.patch("linkedin.fetch_events", return_value=[like("urn:li:activity:1", "EMPATHY")])
    mocker.patch("linkedin.fetch_post_text", return_value=None)
    assert linkedin.collect(cfg)[0]["title"] == "LinkedIn post (Empathy)"


def test_expired_token_keeps_stored_items(mocker, monkeypatch, cfg):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "old")
    linkedin.save_state({"urn:li:activity:1": {"url": "u", "reaction": "PRAISE", "ts": 1,
                                               "title": "Stored", "text": ""}}, cfg["state_file"])
    mocker.patch("linkedin.fetch_events", side_effect=linkedin.TokenExpired("expired"))
    assert [i["title"] for i in linkedin.collect(cfg)] == ["Stored"]


def test_removed_reaction_is_dropped_within_window(mocker, monkeypatch, cfg):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    linkedin.save_state({"urn:li:activity:1": {"url": "u", "reaction": "PRAISE", "ts": NOW_MS,
                                               "title": "Gone", "text": ""}}, cfg["state_file"])
    mocker.patch("linkedin.fetch_events", return_value=[like("urn:li:activity:1", method="DELETE")])
    assert linkedin.collect(cfg) == []


def test_max_items_prunes_oldest(mocker, monkeypatch, cfg):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    events = [like(f"urn:li:activity:{i}", ts=NOW_MS - i * 1000) for i in range(4)]
    mocker.patch("linkedin.fetch_events", return_value=events)
    mocker.patch("linkedin.fetch_post_text", return_value="text")
    mocker.patch("linkedin.extract_titles.try_extract_title", return_value="T")
    assert [i["id"] for i in linkedin.collect(cfg)] == ["urn:li:activity:0", "urn:li:activity:1"]


def test_token_days_left():
    assert linkedin.token_days_left("2026-11-14", today=date(2026, 10, 14)) == 31
    assert linkedin.token_days_left(None) is None
    assert linkedin.token_days_left("garbage") is None


def test_fallback_title_is_not_persisted(mocker, monkeypatch, cfg):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    mocker.patch("linkedin.fetch_events", return_value=[like("urn:li:activity:1")])
    mocker.patch("linkedin.fetch_post_text", return_value="Some post text")
    gen = mocker.patch("linkedin.extract_titles.try_extract_title", return_value=None)

    assert linkedin.collect(cfg)[0]["title"] == "Some post text"  # shown, not stored
    assert "title" not in json.load(open(cfg["state_file"]))["urn:li:activity:1"]

    gen.return_value = "Real Headline"  # next run the model works
    assert linkedin.collect(cfg)[0]["title"] == "Real Headline"
