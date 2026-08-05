"""monkey_setup v2.3.3 的 PROGRESS 打戳（#115 阶段 2 / #133 / #138 / #139 / #140）。

用**假 adb 可执行**验证 `adb_push_progress` / `_dd_with_progress`，不碰真设备。

关键背景（真机实测 2026-08-03）：
  1. adb push 在非 TTY 下**不输出**进度行 —— 解析输出打戳无效；
  2. 传输期间 `stat -c %s` 拿不到增长 —— 该设备 adb 是"写临时文件完成后
     rename"语义。
所以 `adb_push_progress` 的进度来自**分块 push**：逐块 push 到临时文件、
设备端 cat 追加、每块完成打戳。假 adb 的 push/cat 分支必须真实读写文件。
"""

import importlib.util
import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "monkey_setup" / "v2.3.3"


def _load_module(name: str, path: Path):
    """显式加载脚本模块。

    不能用 sys.path.insert + import：全量测试时其他用例已经 import 了同名的
    `_adb`，模块缓存会让这里的 import 拿到别处的旧模块。importlib 按文件加载，
    还给模块一个唯一名。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# monkey_setup 模块级 `from _adb import ...`，必须先把 _adb 放进 sys.modules
_adb = _load_module("_adb_v233", _SCRIPT_DIR / "_adb.py")
sys.modules["_adb"] = _adb
monkey_setup = _load_module("monkey_setup_v233", _SCRIPT_DIR / "monkey_setup.py")


@pytest.fixture
def fake_adb(tmp_path: Path, request) -> Path:
    """一个按参数分派行为的假 adb。

    `push LOCAL REMOTE` → 把 LOCAL 内容写到 REMOTE（模拟传输）。
    `shell cat PART >> DST && rm PART` → 追加 PART 到 DST 并删 PART。
    `shell dd ... >> PATH` → 往 PATH 追加 n*1024 字节。
    `shell stat -c %s PATH` → 打印 PATH 大小。
    param 变体：
      - "hang"（push 挂住，供超时测试）
      - "dd-fail"（dd 分支直接退出 1）
    """
    variant = getattr(request, "param", None)
    script = tmp_path / "adb"
    # 注意：shebang 必须在**文件第一行**。dedent 对前导空行无效，
    # 空行开头的 shebang 内核不认，posix_spawn 报 Exec format error。
    body = textwrap.dedent(f"""\
        import os, re, shutil, sys, time
        args = sys.argv[1:]
        variant = {variant!r}
        if "push" in args:
            if variant == "hang":
                time.sleep(600)
            src, dst = args[-2], args[-1]
            shutil.copyfile(src, dst)
            sys.exit(0)
        if "shell" in args:
            cmd = args[args.index("shell") + 1]
            if "tar xf" in cmd:
                time.sleep(float(os.environ.get("FAKE_TAR_SLEEP", "0.15")))
                sys.exit(0)
            if cmd.startswith("cat "):
                part = cmd.split()[1].strip("'").strip('"')
                m = re.search(r">> (\\S+)", cmd)
                if m:
                    dst = m.group(1).strip("'").strip('"')
                    with open(dst, "ab") as fh:
                        fh.write(open(part, "rb").read())
                    os.unlink(part)
                else:
                    try:
                        sys.stdout.write(open(part, "rb").read().decode())
                    except FileNotFoundError:
                        pass
                sys.exit(0)
            if cmd.startswith("dd "):
                if variant == "dd-fail":
                    sys.exit(1)
                n = int(re.search(r"count=(\\d+)", cmd).group(1))
                path = re.search(r">> (\\S+)", cmd).group(1)
                with open(path, "ab") as fh:
                    fh.write(bytes(n * 1024))
                sys.exit(0)
            if cmd.startswith("stat "):
                path = cmd.split()[-1].strip("'").strip('"')
                print(os.path.getsize(path))
                sys.exit(0)
            if cmd.startswith("rm "):
                for token in cmd.split()[2:]:
                    path = token.strip("'").strip('"')
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass
                sys.exit(0)
            if cmd.startswith("mv "):
                src = cmd.split()[1].strip("'").strip('"')
                dst = cmd.split()[-1].strip("'").strip('"')
                os.rename(src, dst)
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


def _make_source(tmp_path: Path, size_bytes: int) -> Path:
    src = tmp_path / "src.bin"
    src.write_bytes(b"\0" * size_bytes)
    return src


def _collect(seen: list) -> "callable":
    """包一层：on_progress 用关键字参数 written_bytes= 调用。"""
    return lambda **kw: seen.append(kw["written_bytes"])


class TestPushProgress:
    def test_small_file_single_chunk(self, fake_adb, monkeypatch, tmp_path):
        """≤ 一块的小文件：单次 push，结束时打一次戳。"""
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        src = _make_source(tmp_path, 10 * 1024)
        seen: list[int] = []
        _adb.adb_push_progress(str(src), str(tmp_path / "dst.bin"),
                               timeout=30, on_progress=_collect(seen))
        assert seen == [10 * 1024]

    def test_large_file_chunked_accumulates(self, fake_adb, monkeypatch, tmp_path):
        """大文件分块：每块完成打戳，累计字节单调增长到全量。"""
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        monkeypatch.setattr(_adb, "_PUSH_CHUNK_MB", 1)  # 1MB 一块
        src = _make_source(tmp_path, 3 * 1024 * 1024)
        seen: list[int] = []
        _adb.adb_push_progress(str(src), str(tmp_path / "dst.bin"),
                               timeout=60, on_progress=_collect(seen))
        assert seen == [1 * 1024 * 1024, 2 * 1024 * 1024, 3 * 1024 * 1024]
        # 设备端文件完整
        assert (tmp_path / "dst.bin").stat().st_size == 3 * 1024 * 1024

    def test_success_returns_cleanly(self, fake_adb, monkeypatch, tmp_path):
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        src = _make_source(tmp_path, 5 * 1024)
        _adb.adb_push_progress(str(src), str(tmp_path / "dst.bin"),
                               timeout=30, on_progress=None)

    @pytest.mark.parametrize("fake_adb", ["hang"], indirect=True)
    def test_timeout_still_works(self, fake_adb, monkeypatch, tmp_path):
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        src = _make_source(tmp_path, 10 * 1024)
        with pytest.raises(subprocess.TimeoutExpired):
            _adb.adb_push_progress(str(src), str(tmp_path / "dst.bin"),
                                   timeout=2, on_progress=None)


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


class TestMakeProgressCompatibility:
    def test_emit_accepts_positional_pct(self, capsys):
        """#138: `on_progress(pct)` 位置参数风格不再 TypeError。"""
        emit = monkey_setup._make_progress("push")
        emit(50)
        emit(written_bytes=100)
        err = capsys.readouterr().err
        stamps = [
            json.loads(line[len("PROGRESS "):])
            for line in err.splitlines() if line.startswith("PROGRESS ")
        ]
        assert stamps[0]["pct"] == 50
        assert stamps[1]["written_bytes"] == 100
        assert [s["seq"] for s in stamps] == [1, 2]


class TestTarProgress:
    def test_tar_emits_start_periodic_end_stamps(
        self, fake_adb, monkeypatch, tmp_path, capsys
    ):
        """#139: tar 解包期间必须持续打戳，停滞钟才不会误杀慢解包。"""
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        monkeypatch.setenv("FAKE_TAR_SLEEP", "0.3")
        monkeypatch.setattr(monkey_setup, "_TAR_PROGRESS_INTERVAL_S", 0.05)
        monkeypatch.setattr(_adb, "_PROGRESS_POLL_S", 0.02)

        bundle = _make_source(tmp_path, 1024)
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps({"bundle_sha256": digest, "name": "t", "file_count": 1}),
            encoding="utf-8",
        )
        remote_dir = tmp_path / "remote"
        remote_dir.mkdir()
        cfg = {
            "bundle": str(bundle),
            "manifest": str(manifest),
            "remote_dir": str(remote_dir),
            "timeout_seconds": 5,
            "push_timeout_seconds": 5,
        }

        result = monkey_setup.step_push("FAKESERIAL", cfg)
        assert result["success"] is True

        err = capsys.readouterr().err
        stamps = [
            json.loads(line[len("PROGRESS "):])
            for line in err.splitlines() if line.startswith("PROGRESS ")
        ]
        tar_stamps = [
            s for s in stamps if s.get("phase") in ("tar_start", "tar", "tar_end")
        ]
        phases = [s["phase"] for s in tar_stamps]
        assert phases[0] == "tar_start"
        assert "tar" in phases, "解包期间必须有周期心跳戳"
        assert phases[-1] == "tar_end"
        seqs = [s["seq"] for s in stamps]
        assert seqs == sorted(seqs), "seq 必须严格递增"
        assert len(set(seqs)) == len(seqs), "seq 不得重复"
        # 0.3s 解包 / 0.05s 间隔 → 至少 3 枚周期心跳
        assert sum(1 for p in phases if p == "tar") >= 3, phases


class TestPrephaseProgress:
    def test_main_emits_init_and_step_stamps(
        self, fake_adb, monkeypatch, capsys
    ):
        """#140: 入口 init 戳 + 每个 step 的 start/end 戳，前置阶段不再无戳。"""
        monkeypatch.setenv("STP_DEVICE_SERIAL", "FAKESERIAL")
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        monkeypatch.setenv(
            "STP_STEP_PARAMS", json.dumps({"steps": ["wifi"]})
        )
        monkey_setup.main()
        out = capsys.readouterr()
        err = out.err
        stamps = [
            json.loads(line[len("PROGRESS "):])
            for line in err.splitlines() if line.startswith("PROGRESS ")
        ]
        phases = [s.get("phase") for s in stamps]
        assert phases[0] == "init", phases
        assert "step:wifi:start" in phases, phases
        assert "step:wifi:end" in phases, phases
        seqs = [s["seq"] for s in stamps]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert '"success": true' in out.out, out.out

    def test_sha256_emits_periodic_stamps(self, monkeypatch, tmp_path, capsys):
        """#140: 慢 NFS 上大文件 sha256 校验期间持续打戳。"""
        monkeypatch.setattr(monkey_setup, "_SHA256_PROGRESS_BYTES", 1024 * 1024)
        src = _make_source(tmp_path, 3 * 1024 * 1024)
        emit = monkey_setup._make_progress("push")
        digest = monkey_setup._sha256_file(str(src), on_progress=emit)
        assert digest == hashlib.sha256(src.read_bytes()).hexdigest()
        err = capsys.readouterr().err
        stamps = [
            json.loads(line[len("PROGRESS "):])
            for line in err.splitlines() if line.startswith("PROGRESS ")
        ]
        sha_stamps = [s for s in stamps if s.get("phase") == "sha256"]
        assert [s["bytes_hashed"] for s in sha_stamps] == [
            1 * 1024 * 1024,
            2 * 1024 * 1024,
            3 * 1024 * 1024,
            3 * 1024 * 1024,
        ], sha_stamps
        assert sha_stamps[-1]["done"] is True


class TestFillAccumulates:
    def test_fill_accumulates_across_chunks(self, fake_adb, monkeypatch, tmp_path):
        """多块 dd 必须**累计**——每块都"清空再写"是 blocker（review 实测）。"""
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        monkeypatch.setattr(monkey_setup, "_FILL_CHUNK_KB", 1)  # 1KB 一块
        fill = tmp_path / "fill.bin"
        # need_kb = blocks × block_size = 3KB，chunk=1KB → 3 块
        monkey_setup._dd_with_progress(
            "FAKESERIAL", str(fill), block_size=1, blocks=3, timeout=30,
        )
        assert fill.stat().st_size == 3 * 1024, "三块必须累计到 3KB"

    @pytest.mark.parametrize("fake_adb", ["dd-fail"], indirect=True)
    def test_fill_failure_is_not_silent(self, fake_adb, monkeypatch, tmp_path):
        """dd 失败必须抛 RuntimeError——"没填盘但报成功"是这次要消灭的形态。"""
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        monkeypatch.setattr(monkey_setup, "_FILL_CHUNK_KB", 1)
        fill = tmp_path / "fill.bin"
        with pytest.raises(RuntimeError):
            monkey_setup._dd_with_progress(
                "FAKESERIAL", str(fill), block_size=1, blocks=3, timeout=30,
            )


class TestRealWiringAndIsolation:
    def test_make_progress_through_real_chain(self, fake_adb, monkeypatch, tmp_path, capsys):
        """_make_progress 走真实链路（review #133 问题 1）。

        adb_push_progress 用关键字参数 on_progress(written_bytes=...) 调回调，
        与 _emit(**fields) 对齐——位置参数会让第一块就 TypeError。
        """
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        monkeypatch.setattr(_adb, "_PUSH_CHUNK_MB", 1)
        src = _make_source(tmp_path, 3 * 1024 * 1024)
        emit = monkey_setup._make_progress("push")
        _adb.adb_push_progress(str(src), str(tmp_path / "dst.bin"),
                               timeout=60, on_progress=emit)
        err = capsys.readouterr().err
        stamps = [
            json.loads(line[len("PROGRESS "):])
            for line in err.splitlines() if line.startswith("PROGRESS ")
        ]
        assert len(stamps) == 3, err
        assert [s["seq"] for s in stamps] == [1, 2, 3], "seq 必须递增"
        assert stamps[-1]["written_bytes"] == 3 * 1024 * 1024

    def test_staging_replaces_old_file_atomically(self, fake_adb, monkeypatch, tmp_path):
        """remote 预置旧内容，分块 push 后必须被原子替换（review #133 问题 2）。"""
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        monkeypatch.setattr(_adb, "_PUSH_CHUNK_MB", 1)
        src = _make_source(tmp_path, 3 * 1024 * 1024)
        dst = tmp_path / "dst.bin"
        dst.write_bytes(b"OLD-CONTENT-" * 100)  # 预置旧内容
        _adb.adb_push_progress(str(src), str(dst), timeout=60, on_progress=None)
        assert dst.read_bytes() == src.read_bytes(), "最终文件必须与源完全一致"

    def test_temp_chunk_name_is_isolated_per_call(self, fake_adb, monkeypatch, tmp_path):
        """临时块名含 pid/tid（review #133 问题 3）——并发 push 不互踩。"""
        monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
        src_text = (_SCRIPT_DIR / "_adb.py").read_text(encoding="utf-8")
        assert "{local}.{os.getpid()}.{threading.get_ident()}.chunk" in src_text

        monkeypatch.setattr(_adb, "_PUSH_CHUNK_MB", 1)
        # 两路顺序 push 到不同 dst，块名不同、互不覆盖
        a = _make_source(tmp_path, 2 * 1024 * 1024)
        d1 = tmp_path / "d1.bin"
        d2 = tmp_path / "d2.bin"
        # 两个线程的 get_ident 不同，但顺序执行也能验证块名模板隔离
        _adb.adb_push_progress(str(a), str(d1), timeout=60, on_progress=None)
        _adb.adb_push_progress(str(a), str(d2), timeout=60, on_progress=None)
        assert d1.read_bytes() == a.read_bytes()
        assert d2.read_bytes() == a.read_bytes()
