"""monkey_setup v2.3.0 的 PROGRESS 打戳（#115 阶段 2）。

用**假 adb 可执行**验证 `adb_push_progress` 的进度解析，不碰真设备：

- 假 adb 输出 `[ NN%]` 进度行（adb push 的真实格式）
- 断言 on_progress 收到递增的百分比
- 断言超时路径仍工作
- 断言 PROGRESS 戳行格式符合协议（stderr、seq 单调递增）
"""

import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "monkey_setup" / "v2.3.0"


def _load_module(name: str, path: Path):
    """显式加载脚本模块。

    不能用 sys.path.insert + import：全量测试时其他用例已经 import 了同名的
    `_adb`，模块缓存会让这里的 import 拿到别处的旧模块（单独跑 4 passed、
    全量 4 failed 就是这么来的）。importlib 按文件加载，还给模块一个唯一名。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# monkey_setup 模块级 `from _adb import ...`，必须先把 _adb 放进 sys.modules
_adb = _load_module("_adb_v230", _SCRIPT_DIR / "_adb.py")
sys.modules["_adb"] = _adb
monkey_setup = _load_module("monkey_setup_v230", _SCRIPT_DIR / "monkey_setup.py")


@pytest.fixture
def fake_adb(tmp_path: Path, request) -> Path:
    """一个按参数分派行为的假 adb。

    `adb -s SERIAL push a b` → 输出 [ 0%]..[100%] 进度行后退出 0。
    param 控制变体：
      - None: 进度行走 stdout（GNU adb 风格）
      - "stderr": 进度行走 stderr（常见真实 adb 风格）
      - "hang": push 挂住（供超时测试）
      - "no-progress": 不输出进度行
    """
    variant = getattr(request, "param", None)
    script = tmp_path / "adb"
    # 注意：shebang 必须在**文件第一行**。dedent 对前导空行无效，
    # 空行开头的 shebang 内核不认，posix_spawn 报 Exec format error。
    body = textwrap.dedent(f"""\
        import sys, time
        args = sys.argv[1:]
        variant = {variant!r}
        if "push" in args:
            if variant == "hang":
                time.sleep(600)
            for pct in range(0, 101, 20):
                line = f"[{{pct:4d}}%] /sdcard/x"
                if variant == "stderr":
                    sys.stderr.write(line + "\\n"); sys.stderr.flush()
                elif variant != "no-progress":
                    print(line); sys.stdout.flush()
                time.sleep(0.1)
            sys.exit(0)
        sys.exit(0)
    """)
    script.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    script.chmod(0o755)
    return script


@pytest.fixture(autouse=True)
def _env(fake_adb, monkeypatch):
    """adb 脚本需要 STP_DEVICE_SERIAL（device_serial() 未设会 sys.exit）。"""
    monkeypatch.setenv("STP_DEVICE_SERIAL", "FAKESERIAL")


def _run_push(fake_adb: Path, *, timeout: int = 60, on_progress=None) -> None:
    _adb.adb_push_progress("/tmp/src.bin", "/sdcard/dst.bin",
                           timeout=timeout, on_progress=on_progress)


class TestPushProgress:
    def test_progress_is_reported_increasing(self, fake_adb, monkeypatch):
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        seen: list[int] = []
        _run_push(fake_adb, on_progress=seen.append)
        assert seen, "on_progress 从未被调用"
        assert seen == sorted(seen)
        assert seen[-1] == 100

    def test_success_returns_cleanly(self, fake_adb, monkeypatch):
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        _run_push(fake_adb)

    @pytest.mark.parametrize("fake_adb", ["stderr"], indirect=True)
    def test_progress_on_stderr_is_parsed_too(self, fake_adb, monkeypatch):
        """真实 adb push 的进度行常见走 stderr —— 只读 stdout 会漏。"""
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        seen: list[int] = []
        _run_push(fake_adb, on_progress=seen.append)
        assert seen, "stderr 上的进度行没有被解析"
        assert seen[-1] == 100

    @pytest.mark.parametrize("fake_adb", ["no-progress"], indirect=True)
    def test_no_progress_lines_reports_success(self, fake_adb, monkeypatch):
        """没有进度行也必须正常完成（兼容无进度输出的 adb 变体）。"""
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        _run_push(fake_adb)

    @pytest.mark.parametrize("fake_adb", ["hang"], indirect=True)
    def test_timeout_still_works(self, fake_adb, monkeypatch):
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        with pytest.raises(subprocess.TimeoutExpired):
            _adb.adb_push_progress(
                "/tmp/src.bin", "/sdcard/dst.bin",
                timeout=2, on_progress=None,
            )


class TestProgressStampFormat:
    def test_stamp_is_json_on_stderr_with_monotonic_seq(self, capsys):
        """协议契约：PROGRESS <json>，seq 单调递增，语义字段仅供人读。"""
        emit = monkey_setup._make_progress("push")
        emit(written_bytes=1000)
        emit(written_bytes=2000)
        err = capsys.readouterr().err
        stamps = [l for l in err.splitlines() if l.startswith("PROGRESS ")]
        assert len(stamps) == 2, err
        seqs = []
        for line in stamps:
            payload = json.loads(line[len("PROGRESS "):])
            assert "seq" in payload
            assert payload["step"] == "push"
            seqs.append(payload["seq"])
        assert seqs == [1, 2], "seq 必须严格递增"
        # 打戳走 stderr——stdout 是结果契约
        assert capsys.readouterr().out == ""
