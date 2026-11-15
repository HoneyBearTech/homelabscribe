import json

import pytest

from homelabscribe import notify
from homelabscribe.config import Config, NotifyConfig
from homelabscribe.diff import Change, Diff
from homelabscribe.skip import Skip

NTFY = NotifyConfig(type="ntfy", url="http://ntfy.invalid/lab")
DISCORD = NotifyConfig(type="discord", url="http://discord.invalid/hook", on=["errors"])


def _diff(*details):
    return Diff(
        since="a", until="b", changes=[Change(kind="added", service="Plex", detail=d) for d in details]
    )


def test_nothing_to_say_sends_nothing(tmp_path):
    sent = []
    notify.notify_after_scan([NTFY], tmp_path, _diff(), [], post=lambda *a: sent.append(a))
    assert sent == []


def test_changes_are_sent_and_respect_on(tmp_path):
    sent = []
    notify.notify_after_scan(
        [NTFY, DISCORD], tmp_path, _diff("added on atlas"), [], post=lambda t, *a: sent.append(t.type)
    )
    assert sent == ["ntfy"]  # discord only wants errors


def test_errors_only_repeat_when_they_change(tmp_path):
    sent = []
    post = lambda t, title, body: sent.append(body)  # noqa: E731
    notify.notify_after_scan([NTFY], tmp_path, None, ["Docker 'atlas': down"], post=post)
    notify.notify_after_scan([NTFY], tmp_path, None, ["Docker 'atlas': down"], post=post)
    assert len(sent) == 1
    notify.notify_after_scan([NTFY], tmp_path, None, [], post=post)
    assert "can be read again" in sent[-1] and len(sent) == 2


def test_long_messages_are_trimmed():
    title, body = notify.build_message(_diff(*[f"d{i}" for i in range(40)]), [], False, ["changes"])
    assert body.count("\n") == notify.MAX_LINES and "more" in body.splitlines()[-1]


def test_failures_do_not_leak_address_or_token(tmp_path):
    def boom(*a):
        raise RuntimeError("http://discord.invalid/hook?secret")

    failed = notify.send([DISCORD], "t", "b", post=boom)
    assert failed == ["discord: RuntimeError"]


def test_notify_config_parses():
    cfg = Config.model_validate({"notify": [{"type": "ntfy", "url": "https://ntfy.sh/x", "token": "t"}]})
    assert cfg.notify[0].on == ["changes", "errors"]


@pytest.mark.parametrize("content", ["[]", "null", '{"errors": 5}', '{"errors": [1, null]}', "{", "", "5"])
def test_a_state_file_with_another_shape_counts_as_empty(tmp_path, content):
    (tmp_path / notify.STATE_FILE).write_text(content)
    sent = []
    notify.notify_after_scan([DISCORD], tmp_path, None, ["Docker host 'a'"], post=lambda *a: sent.append(a))
    assert len(sent) == 1


def test_a_state_file_that_is_not_utf8_counts_as_empty(tmp_path):
    (tmp_path / notify.STATE_FILE).write_bytes(b"\xff\xfe\x00")
    sent = []
    notify.notify_after_scan([DISCORD], tmp_path, None, ["x"], post=lambda *a: sent.append(a))
    assert len(sent) == 1


def test_a_state_file_write_that_fails_keeps_the_old_file(tmp_path, monkeypatch):
    import errno
    import os

    (tmp_path / notify.STATE_FILE).write_text('{"errors": ["a"]}')

    def boom(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "replace", boom)
    failed = notify.notify_after_scan([DISCORD], tmp_path, None, ["b"], post=lambda *a: None)
    assert (tmp_path / notify.STATE_FILE).read_text() == '{"errors": ["a"]}'
    assert failed == ["state file: could not be written"]


# --- skipped files ---------------------------------------------------------------------------------------


def _run(tmp_path, skips, *, targets=(DISCORD,), errors=(), diff=None):
    sent = []
    failed = notify.notify_after_scan(
        list(targets),
        tmp_path,
        diff,
        list(errors),
        skips,
        post=lambda t, title, body: sent.append((t.type, title, body)),
    )
    assert failed == []
    return sent


def _state(tmp_path):
    return json.loads((tmp_path / notify.STATE_FILE).read_text())


def test_a_new_skipped_file_is_sent_with_the_fixed_title(tmp_path):
    sent = _run(tmp_path, [Skip("services/plex.md", "not UTF-8, left as it is")])
    assert sent == [("discord", "HomelabScribe", "Skipped: services/plex.md (not UTF-8, left as it is)")]
    assert _state(tmp_path) == {"errors": [], "skips": ["services/plex.md"]}


def test_the_same_skipped_file_is_sent_once_over_several_runs(tmp_path):
    skip = Skip("services/plex.md", "not UTF-8, left as it is")
    assert len(_run(tmp_path, [skip])) == 1
    assert _run(tmp_path, [skip]) == []
    assert _run(tmp_path, [Skip("services/plex.md", "another reason")]) == []  # the file, not the reason
    assert _state(tmp_path)["skips"] == ["services/plex.md"]


def test_only_the_new_file_is_sent_and_a_file_in_both_places_counts_once(tmp_path):
    _run(tmp_path, [Skip("a.md", "r")])
    sent = _run(tmp_path, [Skip("a.md", "r"), Skip("b.md", "r"), Skip("b.md", "r2")])
    assert [s[2] for s in sent] == ["Skipped: b.md (r)"]


def test_a_file_that_returns_is_sent_again_and_the_recovery_line_comes_once(tmp_path):
    skip = Skip("a.md", "r")
    _run(tmp_path, [skip])
    sent = _run(tmp_path, [])
    assert [s[2] for s in sent] == ["Every skipped file can be read again."]
    assert _run(tmp_path, []) == []
    assert [s[2] for s in _run(tmp_path, [skip])] == ["Skipped: a.md (r)"]


def test_the_recovery_lines_for_files_and_sources_are_separate(tmp_path):
    _run(tmp_path, [Skip("a.md", "r")], errors=["Docker host 'a'"])
    sent = _run(tmp_path, [], errors=["Docker host 'a'"])
    assert [s[2] for s in sent] == ["Every skipped file can be read again."]
    _run(tmp_path, [Skip("a.md", "r")], errors=["Docker host 'a'"])
    sent = _run(tmp_path, [Skip("a.md", "r")])
    assert [s[2] for s in sent] == ["Every source can be read again."]


def test_a_target_with_only_changes_gets_no_skips_but_state_still_records_them(tmp_path):
    only_changes = NotifyConfig(type="ntfy", url="http://ntfy.invalid/lab", on=["changes"])
    sent = _run(tmp_path, [Skip("a.md", "r")], targets=[only_changes], diff=_diff("added on atlas"))
    assert [s[2] for s in sent] == ["Plex: added on atlas"]
    assert _run(tmp_path, [Skip("a.md", "r")], targets=[only_changes]) == []
    assert _state(tmp_path)["skips"] == ["a.md"]


def test_skips_come_with_errors_before_changes(tmp_path):
    sent = _run(
        tmp_path,
        [Skip("a.md", "r")],
        targets=[NTFY],
        errors=["Docker host 'a'"],
        diff=_diff("added on atlas"),
    )
    assert sent[0][2].splitlines() == [
        "Could not read: Docker host 'a'",
        "Skipped: a.md (r)",
        "Plex: added on atlas",
    ]


@pytest.mark.parametrize(
    "content",
    ['{"skips": 5}', '{"skips": [1, null]}', '{"skips": {"a.md": 1}}', "[]", "{", ""],
)
def test_an_old_or_damaged_state_file_counts_skips_as_empty(tmp_path, content):
    (tmp_path / notify.STATE_FILE).write_text(content)
    sent = _run(tmp_path, [Skip("a.md", "r")])
    assert [s[2] for s in sent] == ["Skipped: a.md (r)"]


def test_an_old_state_file_keeps_its_errors(tmp_path):
    (tmp_path / notify.STATE_FILE).write_text('{"errors": ["Docker host \'a\'"]}')
    sent = _run(tmp_path, [Skip("a.md", "r")], errors=["Docker host 'a'"])
    assert [s[2] for s in sent] == ["Skipped: a.md (r)"]
    assert _state(tmp_path) == {"errors": ["Docker host 'a'"], "skips": ["a.md"]}


def test_a_state_file_that_is_not_utf8_counts_skips_as_empty(tmp_path):
    (tmp_path / notify.STATE_FILE).write_bytes(b"\xff\xfe\x00")
    assert len(_run(tmp_path, [Skip("a.md", "r")])) == 1


def test_many_long_names_fit_both_services_and_say_how_many_were_left_out(tmp_path):
    skips = [Skip(f"services/{i:03d}-" + "x" * 250 + ".md", "not UTF-8, left as it is") for i in range(40)]
    ((title, body),) = [m[1:] for m in _run(tmp_path, skips)]
    assert len(f"**{title}**\n{body}") <= 2000
    assert len(body.encode()) < 4096 // 1.2
    last = body.splitlines()[-1]
    shown = [ln for ln in body.splitlines() if ln.startswith("Skipped: ")]
    assert last == f"...and {40 - len(shown)} more" and 0 < len(shown) < 15
    assert all(ln.endswith("(not UTF-8, left as it is)") for ln in shown)  # whole lines only


def test_wide_characters_stay_under_the_byte_budget():
    wide = "é\U0001f600" * 100
    lines = [f"Skipped: {wide}{i} (r)" for i in range(30)]
    body = notify._trim(lines)
    assert len(body.encode()) <= notify.MAX_BYTES and len(body) <= notify.MAX_CHARS


def test_a_message_within_the_budget_is_not_cut():
    body = notify._trim(["a", "b", "c"])
    assert body == "a\nb\nc"


def test_one_line_longer_than_the_budget_is_counted_as_left_out():
    assert notify._trim(["x" * 5000, "y"]) == "...and 2 more"


def test_skip_lines_hold_only_the_relative_text(tmp_path):
    sent = _run(tmp_path, [Skip("services/plex.md", "not UTF-8, left as it is")])
    assert str(tmp_path) not in sent[0][2] and "/" not in sent[0][2].replace("services/plex.md", "")
