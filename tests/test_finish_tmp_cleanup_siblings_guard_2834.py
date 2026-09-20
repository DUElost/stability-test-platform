"""#2834 同形静态守卫：powercycle_finish / sleep_finish 不得回潮到裸 mkdtemp。"""

from __future__ import annotations

from pathlib import Path

from tools.dev.source_anchor import SourceGuard

REPO = Path(__file__).resolve().parents[1]

ENTRIES = [
    (
        "backend/agent/scripts/powercycle_finish/v1.0.5/powercycle_finish.py",
        'Path(tempfile.mkdtemp(prefix="powercycle-results-"))',
    ),
    (
        "backend/agent/scripts/sleep_finish/v1.0.3/sleep_finish.py",
        'Path(tempfile.mkdtemp(prefix="sleep-results-"))',
    ),
]


def test_pull_sites_use_registered_tmpdir() -> None:
    for rel, leak in ENTRIES:
        guard = SourceGuard.of_repo_path(rel).anchored("def _mk_result_tmpdir() -> Path:")
        guard.assert_present("local = _mk_result_tmpdir()", why="#2834：拉取点必须走登记路径")
        guard.assert_absent(leak, why="#2834：登记之外的 mkdtemp 无人回收")


def test_cleanup_wired_into_main_finally() -> None:
    for rel, _leak in ENTRIES:
        text = (REPO / rel).read_text(encoding="utf-8")
        body = text.split("def main() -> None:", 1)[-1]
        guard = SourceGuard.of_repo_path(rel).anchored("def main() -> None:")
        guard.assert_present("_discard_result_tmpdirs()", why="#2834：main 退出前必须回收")
        assert "finally:" in body
        assert "_discard_result_tmpdirs()" in body.rpartition("finally:")[2]


def test_create_and_delete_share_prefix_constant() -> None:
    for rel, _leak in ENTRIES:
        text = (REPO / rel).read_text(encoding="utf-8")
        assert text.count("_PULL_TMP_PREFIX") >= 3
        assert 'startswith("powercycle-results-")' not in text
        assert 'startswith("sleep-results-")' not in text
