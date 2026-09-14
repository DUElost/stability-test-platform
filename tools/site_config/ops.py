"""Injectable local system operations for the site installer (I3).

Every side-effecting system interaction (commands, users, mounts, ownership,
platform facts) routes through :class:`Ops` so tests can substitute a
controllable implementation.  Command output is never echoed into reports:
callers map failures to stable check ids.
"""

from __future__ import annotations

import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""


class Ops(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
        cwd: str | Path | None = None,
    ) -> CommandResult: ...

    def hostname(self) -> str: ...

    def local_addresses(self) -> set[str]: ...

    def os_release(self) -> dict[str, str]: ...

    def machine(self) -> str: ...

    def user_exists(self, name: str) -> bool: ...

    def create_user(self, name: str, home: str) -> None: ...

    def ensure_dir(self, path: Path, mode: int, owner: str) -> None: ...

    def chown(self, path: Path, owner: str) -> None: ...

    def path_uid(self, path: Path) -> int | None: ...

    def is_mount(self, path: Path) -> bool: ...

    def command_exists(self, name: str) -> bool: ...


class LocalOps:
    """Real implementation for a root/local install on the target host."""

    def run(
        self,
        argv: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
        cwd: str | Path | None = None,
    ) -> CommandResult:
        process = subprocess.run(
            list(argv),
            env=env,
            input=input_text,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        return CommandResult(tuple(argv), process.returncode, process.stdout)

    def hostname(self) -> str:
        return socket.gethostname()

    def local_addresses(self) -> set[str]:
        addresses = {"127.0.0.1"}
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                addresses.add(info[4][0])
        except OSError:
            pass
        return addresses

    def os_release(self) -> dict[str, str]:
        release: dict[str, str] = {}
        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator:
                    release[key.strip()] = value.strip().strip('"')
        except OSError:
            pass
        return release

    def machine(self) -> str:
        return os.uname().machine

    def user_exists(self, name: str) -> bool:
        return self.run(["getent", "passwd", name]).returncode == 0

    def create_user(self, name: str, home: str) -> None:
        self.run(["useradd", "--system", "--create-home", "--home-dir", home, name])

    def ensure_dir(self, path: Path, mode: int, owner: str) -> None:
        directory = Path(path)
        if not directory.exists():
            directory.mkdir(parents=True, mode=mode)
        else:
            os.chmod(directory, mode)
        self.chown(directory, owner)

    def chown(self, path: Path, owner: str) -> None:
        self.run(["chown", "-R", f"{owner}:{owner}", str(path)])

    def path_uid(self, path: Path) -> int | None:
        try:
            return os.lstat(path).st_uid
        except OSError:
            return None

    def is_mount(self, path: Path) -> bool:
        """True when *path* is a mount point, including same-device bind mounts.

        ``os.path.ismount`` misses bind mounts of the same filesystem, so the
        authoritative check reads the mount table (``/proc/self/mountinfo``).
        """
        target = os.path.realpath(path)
        if target == "/":
            return True
        try:
            with open("/proc/self/mountinfo", encoding="utf-8") as handle:
                for line in handle:
                    fields = line.split(" ")
                    if len(fields) > 4 and _unescape_mount_field(fields[4]) == target:
                        return True
        except OSError:
            return os.path.ismount(path)
        return False

    def command_exists(self, name: str) -> bool:
        return shutil_which(name) is not None


def shutil_which(name: str) -> str | None:
    for directory in os.environ.get("PATH", os.defpath).split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _unescape_mount_field(field: str) -> str:
    """Decode the octal escapes mountinfo uses for spaces and backslashes."""
    out: list[str] = []
    index = 0
    while index < len(field):
        char = field[index]
        if char == "\\" and index + 3 < len(field) and field[index + 1 : index + 4].isdigit():
            out.append(chr(int(field[index + 1 : index + 4], 8)))
            index += 4
            continue
        out.append(char)
        index += 1
    return "".join(out)
