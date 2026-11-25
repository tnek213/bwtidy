"""scripts/prune-tags: which image tags on Docker Hub it keeps, it deletes only the rest."""

import io
import json
import subprocess
import urllib.error
from datetime import UTC, datetime, timedelta
from email.message import Message

import pytest

SECRET = "dckr_pat_secret_value"
ACCESS = "jwt-access-token"
LIST = "https://hub.docker.com/v2/namespaces/tnek213/repositories/bwtidy/tags?page_size=100"
PAGE_2 = LIST + "&page=2"
LOGIN = "https://hub.docker.com/v2/auth/token"
START = datetime(2026, 11, 1, tzinfo=UTC)
GIT_CONFIG = [
    *("-c", "user.name=t", "-c", "user.email=t@bwtidy.example"),
    *("-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"),
]


@pytest.fixture
def prune(load_script):
    return load_script("prune-tags")


def short(n: int) -> str:
    return f"{n:07x}"


def item(name: str, hours: int, digest: str | None = None) -> dict:
    pushed = (START + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    return {"name": name, "digest": digest or f"sha256:{name}", "tag_last_pushed": pushed}


class FakeHub:
    """Docker Hub as urlopen sees it: answers by method and URL, every request recorded."""

    def __init__(self, pages: dict[str, dict] | None = None) -> None:
        self.pages = pages or {}
        self.errors: dict[tuple[str, str], int] = {}
        self.requests: list[tuple[str, str, dict[str, str], dict | None]] = []

    def urlopen(self, request, timeout=None):
        method, url = request.get_method(), request.full_url
        body = json.loads(request.data) if request.data else None
        self.requests.append((method, url, dict(request.header_items()), body))
        if (method, url) in self.errors:
            raise urllib.error.HTTPError(url, self.errors[(method, url)], "Boom", Message(), None)
        if method == "POST" and url == LOGIN:
            return io.BytesIO(json.dumps({"access_token": ACCESS}).encode())
        if method == "GET" and url in self.pages:
            return io.BytesIO(json.dumps(self.pages[url]).encode())
        if method == "DELETE":
            return io.BytesIO(b"")
        raise urllib.error.HTTPError(url, 404, "Not Found", Message(), None)

    def deleted(self) -> list[str]:
        return [
            url.rstrip("/").rsplit("/", 1)[1] for m, url, _, _ in self.requests if m == "DELETE"
        ]


def listing(items: list[dict], next_url: str | None = None) -> dict:
    return {"count": len(items), "next": next_url, "previous": None, "results": items}


@pytest.fixture
def hub(prune, monkeypatch):
    fake = FakeHub()
    monkeypatch.setattr(prune.urllib.request, "urlopen", fake.urlopen)
    monkeypatch.setattr(prune, "release_commits", lambda: {"0000001": "v0.1.0"})
    monkeypatch.setenv("DOCKERHUB_PAT", SECRET)
    return fake


def tags(prune, items: list[dict]) -> list:
    return [
        prune.Tag(i["name"], i["digest"], prune.parse_time(i["tag_last_pushed"])) for i in items
    ]


def kept(prune, items: list[dict], releases: dict[str, str] | None = None) -> dict[str, tuple]:
    return {d.tag.name: d.reasons for d in prune.plan(tags(prune, items), releases or {}) if d.keep}


def test_every_tag_that_is_not_seven_lowercase_hex_characters_stays(prune):
    names = ["0.1.0-alpha.1", "1.2.3", "latest", "main", "dev", "abc123", "abc12345", "ABCDEF1"]
    items = [item(name, 0) for name in names] + [item(short(n), 100 + n) for n in range(12)]
    result = kept(prune, items)
    for name in names:
        assert result[name] == ("not a short commit",)


def test_the_ten_newest_short_commits_stay_and_older_ones_go(prune):
    items = [item(short(n), n) for n in range(13)]
    decisions = prune.plan(tags(prune, items), {})
    assert {d.tag.name for d in decisions if d.keep} == {short(n) for n in range(3, 13)}
    assert {d.tag.name for d in decisions if not d.keep} == {short(0), short(1), short(2)}
    assert kept(prune, items)[short(12)] == ("one of the 10 newest short commits",)


def test_the_short_commit_of_a_release_stays_however_old(prune):
    items = [item(short(n), n) for n in range(13)]
    result = kept(prune, items, {short(0): "v0.1.0-alpha.1"})
    assert result[short(0)] == ("the commit of v0.1.0-alpha.1",)
    assert short(1) not in result


def test_a_short_commit_with_the_digest_of_main_or_latest_stays(prune):
    items = [item(short(n), n) for n in range(13)]
    items += [item("main", 50, "sha256:" + short(0)), item("latest", 50, "sha256:" + short(1))]
    result = kept(prune, items)
    assert result[short(0)] == ("the same image as main",)
    assert result[short(1)] == ("the same image as latest",)
    assert short(2) not in result


def test_a_missing_digest_matches_nothing(prune):
    items = [item(short(n), n) for n in range(11)]
    items[0]["digest"] = None
    items.append({"name": "main", "digest": None, "tag_last_pushed": None})
    assert short(0) not in kept(prune, items)


def test_delete_removes_only_the_tags_no_rule_keeps(prune, hub, capsys):
    items = [item(short(n), n) for n in range(14)] + [
        item("0.1.0", 0),
        item("latest", 0),
        item("main", 99, "sha256:" + short(2)),
        item("feature", 0),
    ]
    hub.pages[LIST] = listing(items)
    assert prune.main(["--delete"]) == 0
    assert [u for m, u, _, _ in hub.requests if m == "DELETE"] == [
        f"https://hub.docker.com/v2/repositories/tnek213/bwtidy/tags/{short(3)}/",
        f"https://hub.docker.com/v2/repositories/tnek213/bwtidy/tags/{short(0)}/",
    ]
    out = capsys.readouterr().out
    assert f"deleted {short(3)}" in out
    assert f"keep {short(1)}: the commit of v0.1.0" in out


def test_a_dry_run_deletes_nothing_and_says_why(prune, hub, capsys):
    hub.pages[LIST] = listing([item(short(n), n) for n in range(12)] + [item("main", 0)])
    assert prune.main([]) == 0
    assert hub.deleted() == []
    out = capsys.readouterr().out
    assert f"would delete {short(0)}: an older short commit, pushed 2026-11-01 00:00 UTC" in out
    assert f"keep {short(1)}: the commit of v0.1.0" in out
    assert f"keep {short(11)}: one of the 10 newest short commits" in out
    assert "keep main: not a short commit" in out
    assert "13 tags: 12 kept, 1 would be deleted" in out


def test_it_logs_in_with_the_token_and_never_prints_it(prune, hub, capsys):
    hub.pages[LIST] = listing([item("main", 0)])
    assert prune.main([]) == 0
    method, url, _, body = hub.requests[0]
    assert (method, url, body) == ("POST", LOGIN, {"identifier": "tnek213", "secret": SECRET})
    assert hub.requests[1][2]["Authorization"] == f"Bearer {ACCESS}"
    printed = capsys.readouterr()
    assert SECRET not in printed.out + printed.err
    assert ACCESS not in printed.out + printed.err


def test_a_dry_run_without_a_token_lists_anonymously(prune, hub, monkeypatch):
    monkeypatch.delenv("DOCKERHUB_PAT")
    hub.pages[LIST] = listing([item("main", 0)])
    assert prune.main([]) == 0
    assert [(m, u) for m, u, _, _ in hub.requests] == [("GET", LIST)]
    assert "Authorization" not in hub.requests[0][2]


def test_delete_without_a_token_sends_nothing(prune, hub, monkeypatch, capsys):
    monkeypatch.delenv("DOCKERHUB_PAT")
    assert prune.main(["--delete"]) == 1
    assert hub.requests == []
    assert "DOCKERHUB_PAT" in capsys.readouterr().err


def test_the_listing_follows_every_page(prune, hub):
    hub.pages[LIST] = listing([item(short(n), n) for n in range(8)], PAGE_2)
    hub.pages[PAGE_2] = listing([item(short(n), n) for n in range(8, 14)])
    assert prune.main(["--delete"]) == 0
    assert sorted(hub.deleted()) == [short(0), short(2), short(3)]


def test_a_next_page_outside_docker_hub_stops_before_sending_the_token(prune, hub):
    hub.pages[LIST] = listing([item(short(n), n) for n in range(12)], "https://evil.example/x")
    assert prune.main(["--delete"]) == 1
    assert hub.deleted() == []
    assert all("evil.example" not in u for _, u, _, _ in hub.requests)


def test_a_failed_listing_deletes_nothing(prune, hub, capsys):
    hub.pages[LIST] = listing([item(short(n), n) for n in range(12)], PAGE_2)
    hub.errors[("GET", PAGE_2)] = 500
    assert prune.main(["--delete"]) == 1
    assert hub.deleted() == []
    err = capsys.readouterr().err
    assert "HTTP 500" in err
    assert SECRET not in err


def test_a_failed_login_deletes_nothing(prune, hub):
    hub.errors[("POST", LOGIN)] = 401
    assert prune.main(["--delete"]) == 1
    assert [m for m, _, _, _ in hub.requests] == ["POST"]


def test_an_empty_listing_deletes_nothing(prune, hub, capsys):
    hub.pages[LIST] = listing([])
    assert prune.main(["--delete"]) == 1
    assert hub.deleted() == []
    assert "no tags" in capsys.readouterr().err


def test_a_listing_of_an_unexpected_shape_deletes_nothing(prune, hub):
    hub.pages[LIST] = {"detail": "something else"}
    assert prune.main(["--delete"]) == 1
    assert hub.deleted() == []


def test_version_tags_without_git_tags_delete_nothing(prune, hub, monkeypatch, capsys):
    monkeypatch.setattr(prune, "release_commits", lambda: {})
    hub.pages[LIST] = listing([item(short(n), n) for n in range(12)] + [item("0.1.0", 0)])
    assert prune.main(["--delete"]) == 1
    assert hub.deleted() == []
    assert "git tag v*" in capsys.readouterr().err


def test_a_failed_delete_is_reported_and_the_others_go_on(prune, hub, capsys):
    hub.pages[LIST] = listing([item(short(n), n) for n in range(13)])
    failing = f"https://hub.docker.com/v2/repositories/tnek213/bwtidy/tags/{short(0)}/"
    hub.errors[("DELETE", failing)] = 403
    assert prune.main(["--delete"]) == 1
    assert sorted(hub.deleted()) == [short(0), short(2)]
    assert "HTTP 403" in capsys.readouterr().err


def test_release_commits_maps_the_short_commit_of_each_v_tag(prune, tmp_path):
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *GIT_CONFIG, *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "one")
    first = git("rev-parse", "HEAD")
    git("tag", "-a", "v0.1.0", "-m", "v0.1.0")
    git("commit", "-q", "--allow-empty", "-m", "two")
    second = git("rev-parse", "HEAD")
    git("tag", "v0.2.0")
    git("tag", "other")
    assert prune.release_commits(tmp_path) == {first[:7]: "v0.1.0", second[:7]: "v0.2.0"}


def test_release_commits_fails_outside_a_git_checkout(prune, tmp_path):
    with pytest.raises(prune.PruneError, match="git tag"):
        prune.release_commits(tmp_path)
