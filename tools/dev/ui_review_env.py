"""Prepare the local STP device and host preview used by desktop UI reviewers."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import html
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy/ui-review"
PROJECT = "stp-ui-review"
ORIGIN = "http://stp-ui-review.localhost:15179"
LOCAL_URL = "http://127.0.0.1:15179"
ADMIN = "ui-review-admin"
USER = "ui-review-user"
FIXTURE_VERSION = "devices-hosts-v1"
BUILD_MEMORY = str(3 * 1024**3)
STATE_DIR = Path.home() / ".local/state/stp-ui-review"


class PreviewError(Exception):
    """A step in preparing the preview did not complete."""


def capture(*command: str) -> str:
    return subprocess.check_output(command, cwd=REPO, text=True).strip()


def compose(root: Path, *arguments: str) -> None:
    subprocess.run(
        [
            "docker", "compose", "--project-name", PROJECT, "--project-directory", str(root),
            "--env-file", str(root / "review.env"), "-f", str(root / "docker-compose.yml"),
            "-f", str(root / "compose.review.yml"), *arguments,
        ],
        cwd=root,
        env=docker_environment(),
        check=True,
    )


def docker_environment() -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "DOCKER_BUILDKIT": "0",
    }


def request(opener, path: str, *, data=None, form=False, method=None):
    headers = {"Origin": ORIGIN, "Host": ORIGIN.removeprefix("http://")}
    body = None
    if data is not None:
        headers["Content-Type"] = (
            "application/x-www-form-urlencoded" if form else "application/json"
        )
        body = (urlencode(data) if form else json.dumps(data)).encode()
    with opener.open(
        Request(LOCAL_URL + path, data=body, headers=headers, method=method), timeout=5,
    ) as response:
        return json.load(response)


def session(username: str, password: str, role: str):
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
    request(
        opener, "/api/v1/auth/login", data={"username": username, "password": password}, form=True,
    )
    identity = request(opener, "/api/v1/auth/me")
    if identity["username"] != username or identity["role"] != role:
        raise PreviewError(f"Unexpected test identity for {username}")
    return opener


def read_credentials(root: Path) -> dict[str, str]:
    return json.loads((root / "credentials.json").read_text(encoding="utf-8"))


def write_private(path: Path, value: str) -> None:
    with open(path, "x", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600)) as file:
        file.write(value)


def write_status(root: Path, *, ready: bool, inventory=None) -> None:
    status = {
        "ready": ready,
        "git_sha": (root / "git-sha").read_text(encoding="utf-8").strip(),
        "fixture": FIXTURE_VERSION,
        "origin": ORIGIN,
        "roles": {"admin": ADMIN, "user": USER},
        "routes": ["/devices", "/hosts"],
    }
    if inventory is not None:
        status["inventory"] = inventory
        status["verified_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    public = root / "ui-review-public"
    public.mkdir(exist_ok=True)
    temporary = public / "status.new"
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(public / "status.json")
    (public / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>STP UI review</title>'
        '<h1>STP 设备与主机巡检</h1>'
        f'<pre>{html.escape(json.dumps(status, ensure_ascii=False, indent=2))}</pre>'
        '<p><a href="/login">登录</a> · <a href="/devices">物理设备</a> · '
        '<a href="/hosts">主机集群</a> · <a href="status.json">验收快照</a> · '
        '<a href="/health">实时健康检查</a></p>',
        encoding="utf-8",
    )


def extract_source(root: Path, sha: str) -> None:
    paths = (
        "backend", "frontend", "deploy/nginx/frontend-docker.conf",
        "tests/fixtures/plan_parameter_merge.json", "tools/dev/fake_agent.py",
        "docker-compose.yml", "Dockerfile.backend", "Dockerfile.frontend", ".dockerignore",
    )
    with tempfile.TemporaryFile() as archive:
        subprocess.run(["git", "archive", sha, *paths], cwd=REPO, stdout=archive, check=True)
        archive.seek(0)
        with tarfile.open(fileobj=archive) as source:
            source.extractall(root, filter="data")
    shutil.copyfile(DEPLOY / "compose.yml", root / "compose.review.yml")
    shutil.copyfile(DEPLOY / "fixtures.py", root / "ui-review-fixtures.py")
    with (root / ".dockerignore").open("a", encoding="utf-8") as file:
        file.write("\nreview.env\ncredentials.json\n")
    (root / "git-sha").write_text(sha + "\n", encoding="utf-8")


def prepare(root: Path, sha: str, previous_credentials: dict[str, str] | None) -> None:
    extract_source(root, sha)
    credentials = previous_credentials or {
        role: secrets.token_urlsafe(24) for role in ("admin", "user")
    }
    write_private(root / "credentials.json", json.dumps(credentials) + "\n")
    values = {
        "UI_REVIEW_SHA": sha,
        "UI_REVIEW_ORIGIN": ORIGIN,
        "UI_REVIEW_JWT_SECRET": secrets.token_urlsafe(48),
        "UI_REVIEW_AGENT_SECRET": secrets.token_urlsafe(32),
        "POSTGRES_DB": "stp_ui_review",
        "POSTGRES_USER": "stp_ui_review",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "STP_ADMIN_USER": ADMIN,
        "STP_ADMIN_PASSWORD": credentials["admin"],
    }
    write_private(root / "review.env", "".join(f"{key}={value}\n" for key, value in values.items()))
    # The base compose file declares this path. The override clears env_file.
    (root / ".env.server").touch(mode=0o600)
    write_status(root, ready=False)


def build(root: Path, sha: str) -> None:
    for image, dockerfile in (
        ("server", "Dockerfile.backend"), ("frontend", "Dockerfile.frontend"),
    ):
        subprocess.run(
            [
                "docker", "build", "--memory", BUILD_MEMORY, "--memory-swap", BUILD_MEMORY,
                "--cpu-quota", "200000", "--file", dockerfile,
                "--tag", f"stp-ui-review-{image}:{sha}", ".",
            ],
            cwd=root, env=docker_environment(), check=True,
        )


def inventory(opener) -> dict:
    devices = request(opener, "/api/v1/devices?limit=100")["items"]
    hosts = request(opener, "/api/v1/hosts?limit=100")["items"]
    return {
        "hosts": len(hosts),
        "devices": len(devices),
        "online": sum(device["status"] == "ONLINE" for device in devices),
        "offline": sum(device["status"] == "OFFLINE" for device in devices),
    }


def wait_until_ready(root: Path) -> None:
    credentials = read_credentials(root)
    admin = None
    expected = {"hosts": 2, "devices": 60, "online": 58, "offline": 2}
    deadline = time.monotonic() + 120
    last_error = "services are starting"
    while time.monotonic() < deadline:
        try:
            if admin is None:
                admin = session(ADMIN, credentials["admin"], "admin")
            actual = inventory(admin)
        except (URLError, TimeoutError) as error:
            last_error = str(error)
        else:
            if actual == expected:
                break
            last_error = f"unexpected fixture inventory {actual}"
        time.sleep(2)
    else:
        raise PreviewError(f"Preview readiness timed out: {last_error}")

    request(
        admin, "/api/v1/users",
        data={"username": USER, "password": credentials["user"], "role": "user"},
    )
    user = session(USER, credentials["user"], "user")
    if inventory(user) != expected:
        raise PreviewError("Ordinary user could not read the expected fixture inventory")
    write_status(root, ready=True, inventory=actual)


def read_status(sha: str) -> dict:
    opener = build_opener(ProxyHandler({}))
    status = request(opener, "/ui-review/status.json")
    if status["git_sha"] != sha or not status["ready"]:
        raise PreviewError("The requested revision did not complete preview setup")
    health = request(opener, "/health")["data"]
    if health["status"] != "healthy":
        raise PreviewError("The preview server is unhealthy")
    return status


def print_ready(root: Path) -> None:
    print(f"Ready: {ORIGIN}/ui-review/")
    print(f"Test credentials: {root / 'credentials.json'}")


def start(state: Path, root: Path, revision: str, *, reset: bool) -> None:
    previous_credentials = read_credentials(root) if root.exists() else None
    previous_sha = (root / "git-sha").read_text(encoding="utf-8").strip() if root.exists() else None
    sha = previous_sha if reset else capture(
        "git", "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}",
    )
    if sha is None:
        raise PreviewError("Preview has not been prepared")
    if not reset and sha == previous_sha:
        try:
            read_status(sha)
        except (PreviewError, URLError, TimeoutError) as error:
            raise PreviewError(f"Preview needs recovery. Use reset to recreate it. {error}") from error
        print_ready(root)
        return

    with tempfile.TemporaryDirectory(prefix="prepare-", dir=state) as scratch:
        candidate = Path(scratch) / "stack"
        candidate.mkdir()
        prepare(candidate, sha, previous_credentials)
        if not reset:
            build(candidate, sha)
        if root.exists():
            compose(root, "down", "--volumes", "--remove-orphans")
            shutil.rmtree(root)
        candidate.rename(root)

    compose(root, "up", "-d", "--no-build")
    wait_until_ready(root)
    print_ready(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("up").add_argument("--sha", required=True)
    for action in ("status", "reset", "down"):
        commands.add_parser(action)
    args = parser.parse_args()
    state = STATE_DIR
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = state / "stack"
    try:
        with (state / ".lock").open("a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action == "up":
                start(state, root, args.sha, reset=False)
            elif not (root / "review.env").is_file():
                raise PreviewError("Preview has not been prepared")
            elif args.action == "reset":
                start(state, root, "", reset=True)
            elif args.action == "status":
                sha = (root / "git-sha").read_text(encoding="utf-8").strip()
                status = read_status(sha)
                print(json.dumps(status, ensure_ascii=False, indent=2))
            else:
                compose(root, "down", "--volumes", "--remove-orphans")
                shutil.rmtree(root)
    except (PreviewError, subprocess.CalledProcessError, URLError, TimeoutError) as error:
        parser.exit(1, f"UI review environment: {error}\n")
    except BlockingIOError:
        parser.exit(1, "UI review environment: another operation is in progress\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
