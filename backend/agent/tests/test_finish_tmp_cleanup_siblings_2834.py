"""#2834 同形收口：powercycle_finish / sleep_finish 必须回收自建临时结果目录。

形状与 gpu_finish v1.0.6 相同：登记 → main.finally 回收；失败路径也清。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

CASES = [
    ("powercycle_finish", "powercycle_finish/powercycle_finish.py", "powercycle-results-"),
    ("sleep_finish", "sleep_finish/sleep_finish.py", "sleep-results-"),
]


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


@pytest.fixture(params=CASES, ids=[c[0] for c in CASES])
def finish_mod(request, tmp_path, monkeypatch):
    label, rel, _prefix = request.param
    module = _load(f"{label}_tmp_cleanup", rel)
    module._TEMP_RESULT_DIRS.clear()
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(tmp_path))
    yield module, label
    module._TEMP_RESULT_DIRS.clear()


def _dirs(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in tmp_path.iterdir() if p.is_dir())


def test_mk_registers_and_discard_removes_whole_tree(finish_mod, tmp_path) -> None:
    mod, _ = finish_mod
    d = mod._mk_result_tmpdir()
    (d / "result.txt").write_text("x\n", encoding="utf-8")
    assert d.is_dir() and str(d).startswith(str(tmp_path))
    mod._discard_result_tmpdirs()
    assert not d.exists()
    assert _dirs(tmp_path) == []


def test_pull_site_uses_registered_tmpdir(finish_mod, monkeypatch) -> None:
    mod, _ = finish_mod
    monkeypatch.setattr(mod, "adb_shell", lambda *_a, **_k: "-rw-r--r-- 1 12 result.txt")
    monkeypatch.setattr(mod, "parse_size_from_ls", lambda _ls: 12)
    monkeypatch.setattr(mod, "result_paths", lambda: ["/sdcard/result.txt"])

    def fake_adb(*args, **kwargs):
        target = Path(args[2])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"ok")
        return 0, "", ""

    monkeypatch.setattr(mod, "adb", fake_adb)
    local = mod._pull_result_file()
    assert local.is_file()
    assert local.parent in mod._TEMP_RESULT_DIRS
    mod._discard_result_tmpdirs()
    assert not local.parent.exists()


def test_failure_path_also_reclaims(finish_mod, monkeypatch, tmp_path) -> None:
    mod, _ = finish_mod
    monkeypatch.setattr(mod, "params", lambda: {})

    def _boom(_cfg):
        mod._mk_result_tmpdir()
        raise RuntimeError("adb pull failed")

    monkeypatch.setattr(mod, "_run", _boom)
    captured: dict = {}
    monkeypatch.setattr(
        mod, "output_result", lambda ok, **kw: captured.update({"ok": ok, **kw})
    )
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1
    assert captured["ok"] is False
    assert _dirs(tmp_path) == []


def test_success_path_reclaims(finish_mod, monkeypatch, tmp_path) -> None:
    mod, _ = finish_mod
    monkeypatch.setattr(mod, "params", lambda: {})

    def _ok(_cfg):
        d = mod._mk_result_tmpdir()
        (d / "result.txt").write_text("x", encoding="utf-8")
        return {"metrics": {"run_id": "x"}}

    monkeypatch.setattr(mod, "_run", _ok)
    monkeypatch.setattr(mod, "output_result", lambda ok, **kw: None)
    mod.main()
    assert _dirs(tmp_path) == []


def test_shape_guard_skips_foreign_dirs(finish_mod, tmp_path, capsys) -> None:
    mod, label = finish_mod
    foreign = tmp_path / "other-results-abc"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("keep", encoding="utf-8")
    mod._TEMP_RESULT_DIRS.append(foreign)
    nested = tmp_path / "nested"
    nested.mkdir()
    inner = nested / f"{mod._PULL_TMP_PREFIX}x"
    inner.mkdir()
    mod._TEMP_RESULT_DIRS.append(inner)

    mod._discard_result_tmpdirs()

    assert foreign.exists() and (foreign / "keep.txt").exists()
    assert inner.exists()
    err = capsys.readouterr().err
    assert f"{label}: skip tmp cleanup" in err
