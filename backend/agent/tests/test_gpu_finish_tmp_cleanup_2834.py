"""#2834：gpu_finish v1.0.6 必须回收本次自建的临时结果目录（成功与失败路径都要）。

背景（实测，见 issue 正文）：v1.0.0–v1.0.5 把 test_log.txt `adb pull` 到
``tempfile.mkdtemp(prefix="gpu-results-")`` 后**从不删除**，宿主 ``/tmp``（tmpfs）
单调累积——.68 打满 100% 并把该机 agent 热更新撞成 ENOSPC 失败。

加载方式沿用 `test_gpu_scripts.py` 的 importlib + sys.path 注入先例。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_REL = "gpu_finish/gpu_finish.py"


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


@pytest.fixture
def mod(tmp_path, monkeypatch):
    """加载 v1.0.6，并把临时目录根指到 tmp_path（不污染真实 /tmp，也不跨用例串状态）。"""
    module = _load("gpu_finish_mod_v106", _REL)
    module._TEMP_RESULT_DIRS.clear()
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(tmp_path))
    yield module
    module._TEMP_RESULT_DIRS.clear()


def _dirs(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in tmp_path.iterdir() if p.is_dir())


def test_mk_registers_and_discard_removes_whole_tree(mod, tmp_path) -> None:
    """登记 → 回收：目录连同里面的 pull 结果一起消失。"""
    d = mod._mk_result_tmpdir()
    (d / "test_log.txt").write_text("GPU_RUN_END rc=0\n", encoding="utf-8")
    assert d.is_dir() and str(d).startswith(str(tmp_path))

    mod._discard_result_tmpdirs()

    assert not d.exists()
    assert _dirs(tmp_path) == []


def test_pull_site_uses_the_registered_tmpdir(mod, monkeypatch) -> None:
    """**拉取点必须走登记路径**——否则回收清单永远是空的（这次的失效正是这么发生的）。

    只测「建了目录、并把登记好的目录交出去」这一环：stub 掉 adb 与体积探测，
    让 `_pull_result_log()` 在离线环境里可判定。
    """
    monkeypatch.setattr(mod, "result_log_bytes", lambda: 12)

    def fake_adb(*args, **kwargs):
        target = Path(args[2])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("GPU_RUN_END rc=0\n", encoding="utf-8")
        return 0, "", ""

    monkeypatch.setattr(mod, "adb", fake_adb)

    local = mod._pull_result_log()

    assert local.is_file()
    assert local.parent in mod._TEMP_RESULT_DIRS, "pull 自建目录未登记 ⇒ 无人回收"
    mod._discard_result_tmpdirs()
    assert not local.parent.exists(), "登记了却没回收"


def test_failure_path_also_reclaims(mod, monkeypatch, tmp_path) -> None:
    """`_run` 抛错（ENOSPC/离线那类）时目录同样不能留下——失败窗才是累积主力。"""
    monkeypatch.setattr(mod, "params", lambda: {})

    def _boom(_cfg):
        mod._mk_result_tmpdir()
        raise RuntimeError("adb pull failed: No space left on device")

    monkeypatch.setattr(mod, "_run", _boom)
    captured: dict = {}
    monkeypatch.setattr(
        mod, "output_result", lambda ok, **kw: captured.update({"ok": ok, **kw})
    )

    with pytest.raises(SystemExit) as exc:
        mod.main()

    assert exc.value.code == 1
    assert captured["ok"] is False and "No space left" in captured["error_message"]
    assert _dirs(tmp_path) == [], "失败路径把临时目录留在了 /tmp（就是本单报的形态）"


def test_success_path_reclaims(mod, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(mod, "params", lambda: {})

    def _ok(_cfg):
        d = mod._mk_result_tmpdir()
        (d / "test_log.txt").write_text("x", encoding="utf-8")
        return {"metrics": {"run_id": "gpu_x"}}

    monkeypatch.setattr(mod, "_run", _ok)
    captured: dict = {}
    monkeypatch.setattr(mod, "output_result", lambda ok, **kw: captured.update({"ok": ok}))

    mod.main()

    assert captured["ok"] is True
    assert _dirs(tmp_path) == []


def test_shape_guard_refuses_to_delete_anything_else(mod, tmp_path, capsys) -> None:
    """形状判据：不是「临时根下 + 本族前缀」的目录**一律不删**，且要说一声。

    没有这道判据，一次变量名写错就会 rmtree 到别人的目录——那比留几个临时文件严重
    一个量级，所以判据本身必须有反例用例（否则它和「没有判据」在测试里长得一样）。
    """
    foreign = tmp_path / "not-mine"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("data", encoding="utf-8")
    nested = tmp_path / "gpu-results-parent" / "child"
    nested.mkdir(parents=True)
    elsewhere = tmp_path / "gpu-results-ok"          # 形状正确，应被删
    elsewhere.mkdir()

    mod._TEMP_RESULT_DIRS.extend([foreign, nested, elsewhere])
    mod._discard_result_tmpdirs()

    assert foreign.is_dir(), "误删了非本族前缀的目录"
    assert nested.is_dir(), "误删了嵌套在子目录里的东西（只允许临时根的直接子项）"
    assert not elsewhere.exists()
    err = capsys.readouterr().err
    assert "skip tmp cleanup" in err and "not-mine" in err


def test_guard_is_shared_by_create_and_delete(mod) -> None:
    """建与删必须共用同一个前缀常量：两处各写一份字面量时，改一处即静默失效。"""
    assert mod._PULL_TMP_PREFIX == "gpu-results-"
    created = mod._mk_result_tmpdir()
    assert created.name.startswith(mod._PULL_TMP_PREFIX)
    mod._discard_result_tmpdirs()
    assert not created.exists()
