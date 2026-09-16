# -*- coding: utf-8 -*-
"""#2285 守卫：monkey_teardown 的 `cleanup_paths` 必须逐项 shell 引号化。

对**动态解析出的最新版本目录**断言（沿用 #2048 的做法）：既有用例钉的是 v1.0.2，
新版本一出现就无人覆盖——只要再出现「从旧基线拷贝」把注入面带回，这里立刻红。

契约：这些路径会拼进**设备侧 shell**（`rm -rf <list>` 与回读探针的 `for` 列表），
故「设备 shell 解析出的 argv」必须与原始路径列表逐项一致——含空格不拆项、含
`;`/`$( )`/反引号不成为第二条命令。

版本目录不可变（ADR-0020/0039）：本文件只读，不改任何既有版本。
"""

from __future__ import annotations

import importlib.util
import shlex
import sys
from pathlib import Path
from subprocess import CompletedProcess

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _version_key(name: str) -> tuple[int, ...]:
    """数字段排序（字典序会把 v1.0.9 排到 v1.0.10 后面）。"""
    return tuple(int(part) for part in name[1:].split("."))


def _latest_version_dir(script: str) -> Path:
    base = _SCRIPTS / script
    dirs = [p for p in base.iterdir() if p.is_dir() and p.name.startswith("v")]
    assert dirs, f"{base} 下没有版本目录"
    return max(dirs, key=lambda p: _version_key(p.name))


def _load_latest_monkey_teardown():
    path = _latest_version_dir("monkey_teardown") / "monkey_teardown.py"
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("_adb", None)
        sys.modules.pop("monkey_teardown_latest", None)
        spec = importlib.util.spec_from_file_location("monkey_teardown_latest", path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_adb", None)
        sys.path.remove(str(path.parent))


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> CompletedProcess:
    return CompletedProcess(args=["adb"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_latest_monkey_teardown_quotes_cleanup_paths(monkeypatch):
    """最新版本的 cleanup 命令必须与 `shlex.quote` 逐项引号化的形态一致。"""
    mod = _load_latest_monkey_teardown()
    seen: list[str] = []

    def responder(cmd, timeout=10):
        seen.append(cmd)
        return _cp(0)

    monkeypatch.setattr(mod, "adb_shell_quiet", responder)

    paths = [
        "/data/local/tmp/aim",
        "/data/local/tmp/a b",                # 空格：不引号化会被拆成两项
        "/data/local/tmp/x;rm -rf /sdcard",   # 设备侧命令注入面
        "/data/local/tmp/q'uote",             # 单引号：shlex 需转义
    ]
    mod._cleanup_device_resources(paths, verify=True)

    quoted = " ".join(shlex.quote(str(p)) for p in paths)
    rm_cmd = next(c for c in seen if c.startswith("rm -rf"))
    probe_cmd = next(c for c in seen if c.startswith("for p in"))

    assert rm_cmd == f"rm -rf {quoted}", rm_cmd
    assert probe_cmd.startswith(f"for p in {quoted}; do"), probe_cmd

    # 设备 shell 会解析出的 argv（去掉 `rm -rf` 两个 token）必须与原始列表逐项一致
    assert shlex.split(rm_cmd)[2:] == paths, rm_cmd
