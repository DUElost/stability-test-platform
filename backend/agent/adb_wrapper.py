import shlex
import subprocess
from typing import List, Optional, Union


class AdbError(Exception):
    pass


class AdbWrapper:
    def __init__(self, adb_path: str = "adb", timeout: float = 30.0) -> None:
        self.adb_path = adb_path
        self.timeout = timeout

    def _run(self, args: List[str], timeout: Optional[float] = None) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [self.adb_path] + args,
            capture_output=True,
            text=True,
            timeout=timeout or self.timeout,
        )
        if result.returncode != 0:
            raise AdbError(result.stderr.strip() or result.stdout.strip())
        return result

    def devices(self) -> List[str]:
        result = self._run(["devices", "-l"])
        lines = result.stdout.splitlines()
        serials = []
        for line in lines[1:]:
            parts = line.split()
            if parts:
                serials.append(parts[0])
        return serials

    def shell(self, serial: str, cmd: Union[str, List[str]], timeout: Optional[float] = None) -> subprocess.CompletedProcess:
        """Run an adb shell command.

        Accepts either a string (split via shlex) or a pre-tokenized list of args,
        so pipeline actions can call shell() with plain shell strings.
        """
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        return self._run(["-s", serial, "shell"] + cmd, timeout=timeout)

    def install(self, serial: str, apk_path: str) -> subprocess.CompletedProcess:
        return self._run(["-s", serial, "install", "-r", "-g", "-t", apk_path])

    def push(self, serial: str, local_path: str, remote_path: str) -> subprocess.CompletedProcess:
        return self._run(["-s", serial, "push", local_path, remote_path])

    def pull(self, serial: str, remote_path: str, local_path: str) -> subprocess.CompletedProcess:
        return self._run(["-s", serial, "pull", remote_path, local_path])

    def get_pid(self, serial: str, process_name: str) -> Optional[str]:
        result = self.shell(serial, ["ps"])
        for line in result.stdout.splitlines():
            if process_name in line:
                parts = line.split()
                if len(parts) > 1:
                    return parts[1]
        return None

    def kill_process(self, serial: str, pid: str) -> subprocess.CompletedProcess:
        return self.shell(serial, ["kill", "-9", pid])
