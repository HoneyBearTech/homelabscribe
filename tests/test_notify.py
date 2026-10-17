from homelabscribe import notify
from homelabscribe.config import Config, NotifyConfig
from homelabscribe.diff import Change, Diff

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
