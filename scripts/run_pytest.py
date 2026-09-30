#!/usr/bin/env python3
"""Run project pytest only inside a verified cgroup v2 memory boundary.

Linux/systemd user scopes are the default launcher. An existing tighter boundary
is accepted, including container/CI limits, but missing limits never mean PASS.
This entry does not load env files; run_pytest.sh owns optional .env.test loading.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MEMORY_MAX = 6 * 1024**3
CHILD_FLAG = "--_bounded-child"


def memory_boundary(
    membership: Path = Path("/proc/self/cgroup"),
    mount: Path = Path("/sys/fs/cgroup"),
) -> tuple[int, int] | None:
    """Read effective ancestor limits; both memory and swap must be bounded.

    Each ancestor independently constrains descendants. Limits may therefore be
    supplied by different ancestors. Unreadable/malformed evidence fails closed.
    """
    try:
        relative = next(
            line.removeprefix("0::/")
            for line in membership.read_text().splitlines()
            if line.startswith("0::/")
        )
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            return None
        current = mount / relative
        memory, swap = [], []
        while True:
            for name, values in (("memory.max", memory), ("memory.swap.max", swap)):
                try:
                    limit = (current / name).read_text().strip()
                except FileNotFoundError:
                    # The cgroup v2 hierarchy root has no resource-limit files.
                    if current == mount:
                        continue
                    raise
                if limit != "max":
                    value = int(limit)
                    if value < 0:
                        return None
                    values.append(value)
            if current == mount:
                break
            current = current.parent
        if memory and swap and min(memory) <= MEMORY_MAX and min(swap) == 0:
            return min(memory), min(swap)
    except (OSError, ValueError, StopIteration):
        pass
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    bounded_child = bool(args and args[0] == CHILD_FLAG)
    if bounded_child:
        args.pop(0)
    boundary = memory_boundary()
    if boundary is not None:
        print(f"[pytest] cgroup memory.max={boundary[0]} memory.swap.max=0", flush=True)
        os.chdir(ROOT)
        try:
            os.execv(sys.executable, [sys.executable, "-m", "pytest", *args])
        except OSError as exc:
            print(f"[BLOCK] cannot start pytest: {exc.strerror}", file=sys.stderr)
            return 2
    if bounded_child or sys.platform != "linux":
        print("[BLOCK] pytest requires a verified cgroup v2 limit <=6 GiB, swap=0", file=sys.stderr)
        return 2
    # env -i gates retain their application isolation. Add only user-bus routing
    # needed by systemd-run, without restoring HOME, credentials or env files.
    env = os.environ.copy()
    runtime = f"/run/user/{os.getuid()}"
    env.setdefault("XDG_RUNTIME_DIR", runtime)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    command = [
        "systemd-run", "--user", "--scope",
        "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0", "--",
        sys.executable, str(Path(__file__).resolve()), CHILD_FLAG, *args,
    ]
    try:
        code = subprocess.run(command, cwd=ROOT, env=env).returncode
        return code if code >= 0 else 128 - code
    except OSError as exc:
        print(f"[BLOCK] cannot establish pytest memory boundary: {exc.strerror}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
