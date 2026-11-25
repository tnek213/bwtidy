import http.server
import json
import subprocess
import threading
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def manual(load_script):
    return load_script("manual")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t.test", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit_file(repo: Path, name: str) -> None:
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-m", name)


@pytest.fixture
def repos(tmp_path):
    """A bare origin and a clone on main with one commit pushed."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(origin), str(clone)], check=True, capture_output=True)
    git(clone, "checkout", "-B", "main")
    commit_file(clone, "a")
    git(clone, "push", "-u", "origin", "main")
    return origin, clone


def push_from_elsewhere(origin: Path, tmp_path: Path, name: str) -> None:
    other = tmp_path / "other"
    if not other.exists():
        subprocess.run(["git", "clone", str(origin), str(other)], check=True, capture_output=True)
    git(other, "pull", "origin", "main")
    commit_file(other, name)
    git(other, "push", "origin", "main")


def test_clean_main_is_accepted(repos, manual):
    manual.check_checkout(repos[1])


def test_refuses_another_branch(repos, manual):
    git(repos[1], "checkout", "-b", "feature")
    with pytest.raises(SystemExit, match="on feature, switch to main"):
        manual.check_checkout(repos[1])


def test_refuses_a_detached_head(repos, manual):
    git(repos[1], "checkout", "--detach")
    with pytest.raises(SystemExit, match="detached HEAD"):
        manual.check_checkout(repos[1])


def test_refuses_a_dirty_tree_and_names_the_files(repos, manual):
    clone = repos[1]
    (clone / "a").write_text("changed")
    (clone / "new-file").write_text("x")
    with pytest.raises(SystemExit) as error:
        manual.check_checkout(clone)
    message = str(error.value)
    assert "uncommitted changes" in message
    assert "a" in message
    assert "new-file" in message


def test_update_fast_forwards_to_origin(repos, tmp_path, manual):
    origin, clone = repos
    push_from_elsewhere(origin, tmp_path, "b")
    manual.update(clone)
    assert (clone / "b").exists()
    assert git(clone, "rev-parse", "HEAD") == git(clone, "rev-parse", "origin/main")


def test_update_refuses_diverged_main(repos, tmp_path, manual):
    origin, clone = repos
    push_from_elsewhere(origin, tmp_path, "b")
    commit_file(clone, "local")
    with pytest.raises(SystemExit, match="cannot fast-forward"):
        manual.update(clone)


def test_update_refuses_main_ahead_of_origin(repos, manual):
    clone = repos[1]
    commit_file(clone, "local")
    with pytest.raises(SystemExit, match="not on origin/main"):
        manual.update(clone)


def test_update_fails_without_a_reachable_origin(repos, manual):
    origin, clone = repos
    git(clone, "remote", "set-url", "origin", str(origin) + "-gone")
    with pytest.raises(SystemExit, match="git fetch origin failed"):
        manual.update(clone)


def test_main_refuses_a_bad_option(manual):
    assert manual.main(["--nope"]) == 2


@pytest.fixture
def health_server():
    version = {"value": "261029 abc1234"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"status": "ok", "version": version["value"]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", version
    server.shutdown()


def test_verify_version_prints_the_banner_when_equal(health_server, capsys, manual):
    url, _ = health_server
    manual.verify_version("261029 abc1234", url)
    assert "Testing 261029 abc1234" in capsys.readouterr().out


def test_verify_version_fails_when_different(health_server, manual):
    url, version = health_server
    version["value"] = "261001 old0000"
    with pytest.raises(SystemExit, match="261001 old0000"):
        manual.verify_version("261029 abc1234", url)


def test_verify_version_fails_when_the_backend_does_not_answer(manual):
    with pytest.raises(SystemExit, match="could not read the version"):
        manual.verify_version("x", "http://127.0.0.1:9")
