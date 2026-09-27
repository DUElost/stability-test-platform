"""#3463 batch B1 / **G1-pc**：powercycle 三族 prefs 判据收口（F1）+ 宽容解码（F2）。

方案：#3463 §3 G1 / §4 G1 验收（powercycle_setup v1.2.7、powercycle_check v1.0.9、
powercycle_finish v1.0.7；旧版本目录已不存在，测试针对族树）。

钉住的语义（每族一组）：
1. ① root 可读时，不论 run-as 结果如何都不得 ``rm``——check/finish 旧拷贝的
   ``repair_prefs_ownership`` 以「run-as 读空」（platform 签名 shared-uid 包恒拒）
   作为删除证据，每轮删健康 prefs（#3088 主案）；
2. ② ``transient`` / ``denied`` 读失败时 ``set_stop_flags`` 不整写最小 map、
   保留文件并向步骤暴露可重试失败（raise → 引擎重试）；
3. ③ 仅 ``absent`` 时整写最小 map；
4. F2：``adb()`` bytes 采集 + ``decode_device_output``——非 UTF-8 坏字节不再抛
   ``UnicodeDecodeError``（#3069 形态，port 自 gpu_setup v1.2.3）。

夹具口径对齐 ``test_powercycle_setup_v123.py``（按命令子串派发假 adb）；
F2 处替换的是**模块属性 ``mod.subprocess``**，不打 stdlib 的 ``subprocess.run``
（同 #3223 的时钟教训：patch 共享 stdlib 模块会波及整个进程）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"
_PREFS_PATH = "/data/data/com.tinno.autotesttool/shared_prefs/powercycle_runner.xml"
_PROBE_PREFIX = "if [ ! -f "
_ABSENT_SENTINEL = "__STP_PREFS_ABSENT__"

_HEALTHY_XML = (
    "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
    "<map>\n"
    '    <int name="test_times" value="100"/>\n'
    '    <int name="current_count" value="42"/>\n'
    '    <boolean name="auto_resume" value="true"/>\n'
    '    <boolean name="running" value="true"/>\n'
    "</map>\n"
)
_MINIMAL_STOP_MAP = (
    "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
    "<map>\n"
    '    <boolean name="auto_resume" value="false"/>\n'
    '    <boolean name="running" value="false"/>\n'
    "</map>\n"
)

FAMILIES = ("powercycle_setup", "powercycle_check", "powercycle_finish")


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("_lib", None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_lib", None)
        sys.path.remove(str(path.parent))


@pytest.fixture(scope="module", params=FAMILIES)
def lib(request):
    return _load(f"pc3463_lib_{request.param}", f"{request.param}/_lib.py")


@pytest.fixture(scope="module", params=FAMILIES)
def family(request):
    return request.param


def _install_fake_adb(monkeypatch, mod, routes: list[tuple[str, tuple]]):
    """按命令子串派发假 adb；返回全部调用记录（对齐 v123 夹具）。"""
    calls: list[list] = []

    def fake_adb(*args, timeout=60):
        calls.append([str(a) for a in args])
        cmd = str(args[1]) if len(args) > 1 else ""
        for needle, result in routes:
            if needle in cmd:
                return result
        return (0, "", "")

    monkeypatch.setattr(mod, "adb", fake_adb)
    return calls


def _rm_calls(calls: list[list]) -> list[list]:
    return [c for c in calls if len(c) > 1 and str(c[1]).startswith("rm -f")]


def _runas_calls(calls: list[list]) -> list[list]:
    return [c for c in calls if len(c) > 1 and "run-as" in str(c[1])]


def _record_push(monkeypatch, mod) -> list[str]:
    pushed: list[str] = []
    monkeypatch.setattr(mod, "push_prefs_xml", lambda content: pushed.append(content))
    return pushed


# ---------------------------------------------------------------------------
# ① root 可读 ⇒ 不论 run-as 结果如何都不得 rm（repair_prefs_ownership）
# ---------------------------------------------------------------------------


def test_root_readable_prefs_never_deleted(lib, monkeypatch):
    """root 单次探测读到健康 prefs ⇒ 不 rm，且 repair 根本不走 run-as 通道。"""
    monkeypatch.setattr(lib, "is_root", lambda: True)
    calls = _install_fake_adb(monkeypatch, lib, [
        (_PROBE_PREFIX, (0, _HEALTHY_XML, "")),
        # run-as 若被（错误地）咨询，返回恒拒形态——port 后它不该被调用
        ("run-as", (1, "run-as: Permission denied", "")),
    ])

    lib.repair_prefs_ownership()

    assert _rm_calls(calls) == [], "root 可读仍发出 rm——健康 prefs 被删"
    assert _runas_calls(calls) == [], "root 路径又退化成先问 run-as（旧拷贝判据）"
    assert any(str(c[1]).startswith(_PROBE_PREFIX) for c in calls), "未走单调用同源探测"


# ---------------------------------------------------------------------------
# ② transient / denied ⇒ set_stop_flags 不整写最小 map、保留文件、可重试失败
# ---------------------------------------------------------------------------


def test_transient_read_keeps_file_and_fails_retryable(lib, monkeypatch):
    monkeypatch.setattr(lib, "is_root", lambda: True)
    calls = _install_fake_adb(monkeypatch, lib, [
        (_PROBE_PREFIX, (-1, "", "timeout")),
    ])
    pushed = _record_push(monkeypatch, lib)

    with pytest.raises(RuntimeError) as ei:
        lib.set_stop_flags()

    assert pushed == [], "瞬态读失败仍整写了 prefs"
    assert _rm_calls(calls) == [], "瞬态读失败触发删除"
    assert "重试" in str(ei.value), "报文未暴露可重试语义"


def test_denied_read_keeps_file_and_fails_retryable(lib, monkeypatch):
    monkeypatch.setattr(lib, "is_root", lambda: True)
    calls = _install_fake_adb(monkeypatch, lib, [
        (_PROBE_PREFIX, (1, f"cat: {_PREFS_PATH}: Permission denied", "")),
    ])
    pushed = _record_push(monkeypatch, lib)

    with pytest.raises(RuntimeError) as ei:
        lib.set_stop_flags()

    assert pushed == [], "被拒读失败仍整写了 prefs"
    assert _rm_calls(calls) == [], "被拒读失败触发删除"
    assert "重试" in str(ei.value)


def test_non_root_empty_runas_read_is_not_absent_evidence(lib, monkeypatch):
    """非 root：run-as 读空≠文件不存在——同样不整写、暴露可重试失败。"""
    monkeypatch.setattr(lib, "is_root", lambda: False)
    calls = _install_fake_adb(monkeypatch, lib, [
        ("run-as", (0, "", "")),
    ])
    pushed = _record_push(monkeypatch, lib)

    with pytest.raises(RuntimeError):
        lib.set_stop_flags()

    assert pushed == []
    assert _rm_calls(calls) == []


def test_finish_stop_task_propagates_retryable_failure(monkeypatch):
    """#3088 主案路径：finish 的 stop_task 不吞 set_stop_flags 的可重试失败。"""
    lib_finish = _load("pc3463_lib_finish_prop", "powercycle_finish/_lib.py")
    monkeypatch.setattr(lib_finish, "is_root", lambda: True)
    _install_fake_adb(monkeypatch, lib_finish, [
        (_PROBE_PREFIX, (-1, "", "timeout")),
    ])
    _record_push(monkeypatch, lib_finish)

    with pytest.raises(RuntimeError):
        lib_finish.stop_task(force=False)


# ---------------------------------------------------------------------------
# ③ 仅 absent ⇒ 整写最小 map（且无 rm）
# ---------------------------------------------------------------------------


def test_absent_writes_minimal_map(lib, monkeypatch):
    monkeypatch.setattr(lib, "is_root", lambda: True)
    calls = _install_fake_adb(monkeypatch, lib, [
        (_PROBE_PREFIX, (0, _ABSENT_SENTINEL, "")),
    ])
    pushed = _record_push(monkeypatch, lib)

    lib.set_stop_flags()

    assert pushed == [_MINIMAL_STOP_MAP], f"absent 未整写最小 map：{pushed}"
    assert _rm_calls(calls) == [], "absent 无可删却发出了 rm"


def test_readable_prefs_updates_fields_in_place(lib, monkeypatch):
    """正控制：可读时不整写——原 map 上改两个标志，current_count 等保留。"""
    monkeypatch.setattr(lib, "is_root", lambda: True)
    _install_fake_adb(monkeypatch, lib, [
        (_PROBE_PREFIX, (0, _HEALTHY_XML, "")),
    ])
    pushed = _record_push(monkeypatch, lib)

    lib.set_stop_flags()

    assert len(pushed) == 1
    assert 'name="auto_resume" value="false"' in pushed[0]
    assert 'name="running" value="false"' in pushed[0]
    assert 'name="current_count" value="42"' in pushed[0], "健康 prefs 被降级丢字段"


# ---------------------------------------------------------------------------
# F2：adb() bytes 采集 + 宽容 UTF-8 解码（#3069 形态）
# ---------------------------------------------------------------------------


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: bytes, stderr: bytes):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeSubprocess:
    """替换**模块属性** mod.subprocess——不打 stdlib（#3223 时钟教训同款）。"""

    def __init__(self, result: _FakeCompleted):
        self._result = result

    def run(self, *args, **kwargs):  # noqa: ANN002, ANN003
        assert "text" not in kwargs, "adb() 又退回 text=True 严格解码"
        return self._result


@pytest.mark.parametrize("family", FAMILIES)
def test_broken_bytes_do_not_crash_adb(monkeypatch, family):
    mod = _load(f"pc3463_lib_f2_{family}", f"{family}/_lib.py")
    monkeypatch.setenv("STP_DEVICE_SERIAL", "test-dev-3463")
    broken = b"\x02\xf9 cycle 1/100 start\n"
    monkeypatch.setattr(mod, "subprocess", _FakeSubprocess(_FakeCompleted(0, broken, b"")))

    rc, out, err = mod.adb("shell", "cat /sdcard/result.txt")

    assert rc == 0
    assert out.startswith("\x02\ufffd"), "坏字节应替换为 U+FFFD"
    assert "cycle 1/100 start" in out
    assert err == ""


@pytest.mark.parametrize("family", FAMILIES)
def test_decode_device_output_none_is_empty(monkeypatch, family):
    mod = _load(f"pc3463_lib_f2n_{family}", f"{family}/_lib.py")
    assert mod.decode_device_output(None) == ""
