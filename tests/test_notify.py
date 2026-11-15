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


# --- a failed render, untrusted names, Discord ------------------------------------------------------------


def _round(tmp_path, skips, complete, **kw):
    sent = []
    notify.notify_after_scan(
        [DISCORD],
        tmp_path,
        None,
        [],
        skips,
        post=lambda t, title, body: sent.append(body),
        render_complete=complete,
        **kw,
    )
    return sent


def test_a_failed_render_keeps_known_skips_and_announces_no_recovery(tmp_path):
    plex = Skip("services/plex.md", "not UTF-8, left as it is")
    assert len(_round(tmp_path, [plex], True)) == 1
    assert _round(tmp_path, [], False) == []  # no "Every skipped file can be read again."
    assert _state(tmp_path)["skips"] == ["services/plex.md"]
    assert _round(tmp_path, [plex], True) == []  # still bad: not sent again


def test_a_failed_render_still_sends_new_scan_skips_and_adds_them_to_the_state(tmp_path):
    _round(tmp_path, [Skip("a.md", "r")], True)
    sent = _round(tmp_path, [Skip("changelog.json", "r")], False)
    assert sent == ["Skipped: changelog.json (r)"]
    assert _state(tmp_path)["skips"] == ["a.md", "changelog.json"]


def test_a_completed_render_after_a_failed_one_can_announce_recovery(tmp_path):
    _round(tmp_path, [Skip("a.md", "r")], True)
    _round(tmp_path, [], False)
    assert _round(tmp_path, [], True) == ["Every skipped file can be read again."]


def test_a_name_that_is_not_utf8_is_sent_escaped_and_kept_as_it_is_in_the_state(tmp_path):
    bad = Skip("services/broken-\udcff.md", "not UTF-8, left as it is")
    for target, shown in ((NTFY, "broken-\\udcff.md"), (DISCORD, "broken-\\\\udcff.md")):
        sent = []
        notify.notify_after_scan(
            [target],
            tmp_path / target.type,
            None,
            [],
            [bad],
            post=lambda t, title, body, s=sent: s.append(body),
        )
        sent[0].encode("utf-8")  # does not raise
        assert shown in sent[0]
    state = (tmp_path / "ntfy" / notify.STATE_FILE).read_bytes()
    assert json.loads(state)["skips"] == ["services/broken-\udcff.md"]
    again = []
    notify.notify_after_scan([NTFY], tmp_path / "ntfy", None, [], [bad], post=lambda *a: again.append(a))
    assert again == []  # the original path is what is compared


def test_other_lines_with_a_lone_surrogate_can_be_encoded_too():
    _, body = notify.build_message(None, ["Docker host '\udcff'"], False, ["errors"])
    body.encode("utf-8")


@pytest.mark.parametrize(
    "name",
    ["a\nCould not read: x", "a\rb", "a\tb", "a\x00b\x1fc\x7fd", "a‮b⁦c⁩d"],
)
@pytest.mark.parametrize("target", [NTFY, DISCORD])
def test_a_file_name_is_always_exactly_one_line(tmp_path, name, target):
    sent = []
    notify.notify_after_scan(
        [target], tmp_path, None, [], [Skip(f"services/{name}.md", "r")], post=lambda t, ti, b: sent.append(b)
    )
    body = sent[0]
    assert len(body.splitlines()) == 1 and body.startswith("Skipped: services/")
    assert not any(
        ord(c) < 0x20 or ord(c) == 0x7F or 0x202A <= ord(c) <= 0x202E or 0x2066 <= ord(c) <= 0x2069
        for c in body
    )


def test_escaped_names_count_as_real_lines_for_the_cap():
    skips = [Skip(f"a\nb{i}", "r") for i in range(20)]
    body = notify.build_message(None, [], False, ["errors"], skips)[1]
    assert len(body.splitlines()) == notify.MAX_LINES + 1 and body.endswith("...and 5 more")


def test_discord_names_cannot_format_or_link_but_ntfy_names_are_plain():
    skip = Skip("services/**x**`y`_z_[x](https://e.invalid)<@1>|~\\", "r")
    discord = notify.build_message(None, [], False, ["errors"], [skip], kind="discord")[1]
    assert discord == (
        "Skipped: services/\\*\\*x\\*\\*\\`y\\`\\_z\\_\\[x\\]\\(https://e.invalid\\)\\<@1\\>\\|\\~\\\\ (r)"
    )
    ntfy = notify.build_message(None, [], False, ["errors"], [skip], kind="ntfy")[1]
    assert ntfy == "Skipped: services/**x**`y`_z_[x](https://e.invalid)<@1>|~\\ (r)"


def test_every_discord_message_forbids_mentions(monkeypatch):
    import requests

    got = {}

    class _R:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(requests, "post", lambda url, json=None, **kw: got.update(json=json) or _R())
    notify._post(DISCORD, "HomelabScribe", "Skipped: @everyone <@1> (r)")
    assert got["json"]["allowed_mentions"] == {"parse": []}
    assert got["json"]["content"].startswith("**HomelabScribe**\n")


@pytest.mark.parametrize("key", ["on", '"on"'])
def test_notify_on_works_quoted_and_unquoted(tmp_path, key):
    from homelabscribe.config import load_config

    path = tmp_path / "c.yaml"
    path.write_text(f"notify:\n  - type: ntfy\n    url: http://x.invalid/t\n    {key}: [changes]\n")
    assert load_config(path).notify[0].on == ["changes"]


def test_notify_with_on_twice_is_a_config_error(tmp_path):
    from pydantic import ValidationError

    from homelabscribe.config import load_config

    path = tmp_path / "c.yaml"
    path.write_text(
        'notify:\n  - type: ntfy\n    url: http://x.invalid/t\n    on: [changes]\n    "on": [errors]\n'
    )
    with pytest.raises(ValidationError, match="`on` twice"):
        load_config(path)


# --- escaping by Unicode category, and the reason ---------------------------------------------------------


@pytest.mark.parametrize("target", [NTFY, DISCORD])
@pytest.mark.parametrize("char", [" ", " ", "\x85", "‎", "‏", "​", "﻿", "؜", "\x0b", "\x0c", "\x1c"])
def test_any_line_splitting_character_in_a_name_stays_on_one_line(tmp_path, char, target):
    sent = []
    notify.notify_after_scan(
        [target], tmp_path, None, [], [Skip(f"a{char}b.md", "r")], post=lambda t, ti, b: sent.append(b)
    )
    body = sent[0]
    assert char not in body and len(body.splitlines()) == 1 and body.startswith("Skipped: a")
    assert body.endswith("b.md (r)") and "\\" in body


def test_thirty_line_separators_are_one_line_and_the_cap_holds():
    sep = chr(0x2028)
    skips = [Skip("x" + sep * 30 + str(i), "r") for i in range(20)]
    body = notify.build_message(None, [], False, ["errors"], skips)[1]
    lines = body.splitlines()  # a real line split: nothing the name holds adds one
    shown = [ln for ln in lines if ln.startswith("Skipped: x")]
    assert len(lines) == len(shown) + 1 <= notify.MAX_LINES + 1
    assert lines[-1] == f"...and {20 - len(shown)} more"
    assert sep not in body and "\\u2028" in body
    many = notify.build_message(None, [], False, ["errors"], [Skip(f"a{sep}{i}", "r") for i in range(40)])[1]
    assert len(many.splitlines()) == notify.MAX_LINES + 1  # the cap of 15 lines still holds


def test_specific_escapes_are_readable():
    line = notify.build_message(None, [], False, ["errors"], [Skip("a\x85b‎c﻿d", "r")])[1]
    assert line == "Skipped: a\\x85b\\u200ec\\ufeffd (r)"


def test_a_reason_with_text_cannot_format_on_discord_but_keeps_its_brackets():
    class Odd(str):
        path = "a.md"

    skip = Odd("a.md (**bold** [x](https://e.invalid) @everyone\nCould not read: y)")
    body = notify.build_message(None, [], False, ["errors"], [skip], kind="discord")[1]
    assert (
        body
        == "Skipped: a.md (\\*\\*bold\\*\\* \\[x\\]\\(https://e.invalid\\) @everyone\\\\nCould not read: y)"
    )
    assert len(body.splitlines()) == 1
    plain = notify.build_message(None, [], False, ["errors"], [skip], kind="ntfy")[1]
    assert plain == "Skipped: a.md (**bold** [x](https://e.invalid) @everyone\\nCould not read: y)"
