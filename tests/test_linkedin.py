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
    assert json.load(open(cfg["state_file"]))["items"]["urn:li:activity:1"]["title"] == "A Headline"


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
    assert "title" not in json.load(open(cfg["state_file"]))["items"]["urn:li:activity:1"]

    gen.return_value = "Real Headline"  # next run the model works
    assert linkedin.collect(cfg)[0]["title"] == "Real Headline"


def repost(original, rid="r1", ts=NOW_MS, method="CREATE"):
    event = {"resourceName": "instantReposts", "resourceId": rid, "method": method,
             "capturedAt": ts, "processedAt": ts, "activity": {}}
    if method == "CREATE":
        event["activity"] = {"repostedContent": original}
    return event


def test_repost_is_included_even_with_like_excluded():
    result = linkedin.reactions_from_events([repost("urn:li:ugcPost:7")], ["LIKE"])
    assert result == {"urn:li:ugcPost:7": {"reaction": "REPOST", "ts": NOW_MS}}


def test_repost_outranks_reaction_on_same_post():
    urn = "urn:li:activity:3"
    events = [repost(urn, ts=1), like(urn, "LIKE", ts=2), like("urn:li:activity:4", "PRAISE")]
    result = linkedin.reactions_from_events(events, ["LIKE"])
    assert result[urn]["reaction"] == "REPOST"
    assert result["urn:li:activity:4"]["reaction"] == "PRAISE"


def test_deleted_repost_is_removed():
    events = [repost("urn:li:ugcPost:7", rid="r9", ts=1), repost("", rid="r9", ts=2, method="DELETE")]
    assert linkedin.reactions_from_events(events, []) == {}


def test_repost_can_be_excluded():
    assert linkedin.reactions_from_events([repost("urn:li:ugcPost:7")], ["REPOST"]) == {}


def test_repost_target_as_dict_and_junk():
    events = [repost({"urn": "urn:li:share:5"}, rid="a"), repost("not-a-urn", rid="b"), repost(None, rid="c")]
    assert list(linkedin.reactions_from_events(events, [])) == ["urn:li:share:5"]


def stored(**items):
    return {"items": items, "last_processed_at": 0, "last_full_fetch": 0}


def item(reaction="PRAISE", ts=NOW_MS, **extra):
    return {"url": "u", "reaction": reaction, "ts": ts, "title": "T", "text": "x", **extra}


def test_first_run_reads_full_window_then_incremental(mocker, monkeypatch, cfg):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    mocker.patch("linkedin.fetch_post_text", return_value="text")
    mocker.patch("linkedin.extract_titles.try_extract_title", return_value="T")
    fetch = mocker.patch("linkedin.fetch_events", return_value=[like("urn:li:activity:1", ts=NOW_MS)])

    linkedin.collect(cfg)
    first_since = fetch.call_args.args[1]
    assert first_since <= NOW_MS - 27 * linkedin.DAY_MS  # whole 28-day window
    saved = json.load(open(cfg["state_file"]))
    assert saved["last_processed_at"] == NOW_MS and saved["last_full_fetch"] > 0

    fetch.return_value = []
    linkedin.collect(cfg)
    assert fetch.call_args.args[1] == NOW_MS  # incremental: since last processedAt
    assert json.load(open(cfg["state_file"]))["last_processed_at"] == NOW_MS


def test_weekly_full_refresh(mocker, monkeypatch, cfg):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    old = NOW_MS - 8 * linkedin.DAY_MS
    linkedin.save_state({"items": {}, "last_processed_at": NOW_MS - 1000, "last_full_fetch": old},
                        cfg["state_file"])
    fetch = mocker.patch("linkedin.fetch_events", return_value=[])
    linkedin.collect(cfg)
    assert fetch.call_args.args[1] <= NOW_MS - 27 * linkedin.DAY_MS


def test_incremental_delete_removes_stored_item(mocker, monkeypatch, cfg):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    state = stored(**{"urn:li:activity:1": item(), "urn:li:activity:2": item()})
    state.update(last_processed_at=NOW_MS - 1000, last_full_fetch=NOW_MS - 1000)
    linkedin.save_state(state, cfg["state_file"])
    mocker.patch("linkedin.fetch_events", return_value=[like("urn:li:activity:1", method="DELETE")])
    assert [i["id"] for i in linkedin.collect(cfg)] == ["urn:li:activity:2"]


def test_like_does_not_downgrade_stored_repost(mocker, monkeypatch, cfg):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    state = stored(**{"urn:li:ugcPost:7": item("REPOST", repost_id="r1")})
    state.update(last_processed_at=NOW_MS - 1000, last_full_fetch=NOW_MS - 1000)
    linkedin.save_state(state, cfg["state_file"])
    mocker.patch("linkedin.fetch_events", return_value=[like("urn:li:ugcPost:7", "LIKE")])
    result = linkedin.collect(cfg)
    assert result[0]["reaction"] == "REPOST"


def test_incremental_repost_delete_uses_stored_repost_id(mocker, monkeypatch, cfg):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    state = stored(**{"urn:li:ugcPost:7": item("REPOST", repost_id="r1")})
    state.update(last_processed_at=NOW_MS - 1000, last_full_fetch=NOW_MS - 1000)
    linkedin.save_state(state, cfg["state_file"])
    mocker.patch("linkedin.fetch_events", return_value=[repost("", rid="r1", method="DELETE")])
    assert linkedin.collect(cfg) == []


def test_legacy_flat_state_is_migrated(cfg):
    with open(cfg["state_file"], "w") as f:
        json.dump({"urn:li:activity:1": item()}, f)
    state = linkedin.load_state(cfg["state_file"])
    assert list(state["items"]) == ["urn:li:activity:1"]
    assert state["last_processed_at"] == 0


def test_failed_fetch_keeps_cursor(mocker, monkeypatch, cfg):
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "t")
    state = stored(**{"urn:li:activity:1": item()})
    state.update(last_processed_at=NOW_MS - 5000, last_full_fetch=NOW_MS - 5000)
    linkedin.save_state(state, cfg["state_file"])
    mocker.patch("linkedin.fetch_events", side_effect=requests.ConnectionError("down"))
    linkedin.collect(cfg)
    assert json.load(open(cfg["state_file"]))["last_processed_at"] == NOW_MS - 5000
