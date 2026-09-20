"""#2834 静态守卫：gpu_finish v1.0.6 的临时目录必须「建了就登记、删了带判据」。

行为用例（`backend/agent/tests/test_gpu_finish_tmp_cleanup_2834.py`）证明**现在**的形状对；
本文件守的是**别被改回上一种形状**——那条泄漏路径的唯一特征是 `mkdtemp` 的返回值没进
回收清单，所以任何「顺手直接建目录」的改法都会复活它，而复现它要等 GPU 窗把宿主 `/tmp`
（tmpfs）累积到打满（实测 .68 100%、热更新 ENOSPC）——反馈慢到等于没有反馈。

判据绑的是**代码形状**而不是散字符串：本文件的版本说明里也出现了那个字面量，
绑散字符串会自我误伤——「判据落在注释/文字上」正是 #2641/#2642 记过的形态。

源扫描按 #2639 的纪律用 `SourceGuard`：锚点不在 ⇒ 用例过期（响亮）；形态回潮 ⇒ 防线回归
（响亮）；两类红的前缀不同，看第一行就知道该修测试还是修产品。
"""

from __future__ import annotations

from pathlib import Path

from tools.dev.source_anchor import SourceGuard

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRY_REL = "backend/agent/scripts/gpu_finish/v1.0.6/gpu_finish.py"

#: v1.0.5 的旧形态：`local = Path(tempfile.mkdtemp(prefix="gpu-results-")) / "test_log.txt"`
LEAK_SHAPE = 'Path(tempfile.mkdtemp(prefix="'


def _text() -> str:
    return (REPO_ROOT / ENTRY_REL).read_text(encoding="utf-8")


def test_temp_dirs_are_only_created_through_the_registry() -> None:
    """建临时目录必须走 `_mk_result_tmpdir()`；登记之外再出现裸 `mkdtemp` 即泄漏回潮。"""
    guard = SourceGuard.of_repo_path(ENTRY_REL).anchored("def _mk_result_tmpdir() -> Path:")
    guard.assert_present("local = _mk_result_tmpdir()", why="#2834：拉取点必须走登记路径")
    guard.assert_absent(
        LEAK_SHAPE, why="#2834：登记之外的 mkdtemp 没人回收（本次泄漏的原形）"
    )


def test_cleanup_is_wired_into_mains_finally() -> None:
    """回收必须挂在 `main()` 的 `finally`——挂在成功分支上等于失败窗继续泄漏。

    GPU 窗失败率不低（ENOSPC / 离线 / pull 超时都会抛），而失败正是把目录留下的那一路，
    所以这条不是风格问题：`finally` 之外的一切写法都只修一半。
    """
    body = _text().split("def main() -> None:", 1)[-1]
    guard = SourceGuard.of_repo_path(ENTRY_REL).anchored("def main() -> None:")
    guard.assert_present("_discard_result_tmpdirs()", why="#2834：main 退出前必须回收")
    assert "finally:" in body, "main() 里没有 finally 分支"
    assert "_discard_result_tmpdirs()" in body.rpartition("finally:")[2], (
        "回收调用落在 finally 之外（#2834：异常与 sys.exit 路径必须同样回收）"
    )


def test_create_and_delete_share_one_prefix_constant() -> None:
    """建与删共用 `_PULL_TMP_PREFIX`：两处各写一份字面量时，改一处就静默不匹配。"""
    text = _text()
    assert text.count("_PULL_TMP_PREFIX") >= 3, (
        "前缀常量引用数异常（创建侧与形状判据侧都该用它）——改回字面量即失去一致性"
    )
    assert 'startswith("gpu-results-")' not in text, (
        "删除侧硬编码前缀字面量：与创建侧脱钩后，改名会让回收静默变成空操作"
    )


def test_guard_has_teeth_on_the_old_shape() -> None:
    """变异自证：判据必须认得旧形态。没有这条，判据与被注释掉的判据在 CI 里长得一样。"""
    poisoned = _text().replace(
        "local = _mk_result_tmpdir()",
        'local = Path(tempfile.mkdtemp(prefix="gpu-results-"))',
    )
    assert poisoned != _text(), "变异未命中：创建点形态已变，本判据需同步（不要再绑散字符串）"
    assert LEAK_SHAPE in poisoned, "变异体里没有被禁形状 ⇒ 判据与真实代码已脱钩"
    assert LEAK_SHAPE not in _text()
