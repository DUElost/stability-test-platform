"""升级同步的本机资源保护契约（#1248 / R14-F02）。

背景：`update_agent.yml` 的代码同步用 `--delete --delete-excluded`，
而 `resources/mtbf/`（主机本地布放、不在 Git）不在排除表里 —— 每次
Ansible 升级都会把 MTBF APK 三件套删掉，后续 MTBF 直接失败。API 热更新
（`host_updater.py`）对同一路径有 `--exclude='resources/mtbf/'` 豁免。

本文件把语义钉成可执行断言：
1. 角色默认值声明了主机本地路径，且 playbook 的 dry-run 与真实同步
   都同时发出 `--exclude` + `--filter='protect ...'`（缺一不成立）；
2. 真实 rsync 行为：拿生产同样的 flag 组合跑一遍，主机本地文件存活、
   源码删除仍照常同步、原有排除清理不被破坏；
3. 反例见证：只发 `--exclude` 时文件确实被 `--delete-excluded` 清掉。

ADR-0040 D8 R2：host-resources 层退役后 `resources/` **整树**归主机本地——Ansible 既不下发
（源树里的 resources 不传）也不删除（主机存量原样保留）。由此从**任何**源树同步都安全：
源树没有 resources（git worktree 的常态）时不再把主机资源面当「已删除」清空——这正是 #2133
事故形态，原 #2166 的「源树必须带 resources」前置断言随之撤除（本文件的真跑用例接替它的职责）。

rsync 缺失（如精简容器）时行为类用例 skip，静态用例仍生效。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
ROLE_DEFAULTS = REPO_ROOT / "tools/ansible/roles/agent_deploy/defaults/main.yml"
UPDATE_PLAYBOOK = REPO_ROOT / "tools/ansible/playbooks/update_agent.yml"
MTBF_PATH = "resources/mtbf/"
RESOURCES_PATH = "resources/"

requires_rsync = pytest.mark.skipif(
    shutil.which("rsync") is None, reason="rsync not available"
)


def _defaults() -> dict:
    return yaml.safe_load(ROLE_DEFAULTS.read_text(encoding="utf-8"))


def _host_local_flags() -> list[str]:
    flags: list[str] = []
    for item in _defaults()["agent_host_local_paths"]:
        flags += [f"--exclude={item}", f"--filter=protect {item}"]
    return flags


def _run_update(src: Path, dest: Path, extra_flags: list[str]) -> subprocess.CompletedProcess:
    excludes: list[str] = []
    for item in _defaults()["agent_install_excludes"]:
        excludes.append(f"--exclude={item}")
    return subprocess.run(
        [
            "rsync",
            "-a",
            "--delete",
            "--delete-excluded",
            *excludes,
            *extra_flags,
            f"{src}/",
            f"{dest}/",
        ],
        capture_output=True,
        text=True,
    )


def _seed_layouts(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    (src / "agent").mkdir(parents=True)
    (src / "resources/mtbf").mkdir(parents=True)
    (dest / "agent").mkdir(parents=True)
    (dest / "resources/mtbf/sub").mkdir(parents=True)

    (src / "agent/main.py").write_text("new-code\n", encoding="utf-8")
    (src / "resources/mtbf/apk.bin").write_text("control-plane-copy\n", encoding="utf-8")

    # 主机本地布放：版本与源不同 + 仅主机存在的文件（含嵌套目录）
    (dest / "resources/mtbf/apk.bin").write_text("host-local-apk\n", encoding="utf-8")
    (dest / "resources/mtbf/local-only.bin").write_text("host-only\n", encoding="utf-8")
    (dest / "resources/mtbf/sub/deep.bin").write_text("deep\n", encoding="utf-8")
    # 源码已删除的旧文件：仍应被 --delete 清掉（验收标准第 2 条）
    (dest / "agent/removed.py").write_text("stale\n", encoding="utf-8")
    # 既有排除清理语义：--delete-excluded 仍要清掉这些残留
    (dest / "__pycache__").mkdir()
    (dest / "__pycache__/x.pyc").write_text("junk\n", encoding="utf-8")
    return src, dest


def test_role_defaults_declare_whole_resources_tree_host_local():
    """ADR-0040 D8 R2：`resources/` 整树归主机本地（覆盖 mtbf/，也覆盖退役前下发的 aimonkey/flashtool）。"""
    assert RESOURCES_PATH in _defaults()["agent_host_local_paths"]


def _seed_resources_layout(tmp_path: Path, *, src_resources: bool) -> tuple[Path, Path]:
    """主机侧带退役前下发的资源副本 + mtbf；源树有/无 resources 两种形态。"""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    (src / "agent").mkdir(parents=True)
    (src / "agent/main.py").write_text("new-code\n", encoding="utf-8")
    if src_resources:
        (src / "resources/aimonkey").mkdir(parents=True)
        (src / "resources/aimonkey/monkey.bin").write_text("control-plane-copy\n", encoding="utf-8")
        (src / "resources/new-only.bin").write_text("never-shipped\n", encoding="utf-8")
    for rel, content in (
        ("resources/aimonkey/monkey.bin", "host-copy\n"),
        ("resources/flashtool/SP/flash_tool", "host-flash\n"),
        ("resources/mtbf/apk.bin", "host-mtbf\n"),
        ("agent/removed.py", "stale\n"),
    ):
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_text(content, encoding="utf-8")
    return src, dest


@requires_rsync
def test_host_resources_survive_sync_from_tree_without_resources(tmp_path):
    """#2133 事故形态（源树无 resources）→ 主机资源面整树存活；代码同步照常。"""
    src, dest = _seed_resources_layout(tmp_path, src_resources=False)

    completed = _run_update(src, dest, _host_local_flags())

    assert completed.returncode == 0, completed.stderr
    assert (dest / "resources/aimonkey/monkey.bin").read_text(encoding="utf-8") == "host-copy\n"
    assert (dest / "resources/flashtool/SP/flash_tool").exists()
    assert (dest / "resources/mtbf/apk.bin").exists()
    assert (dest / "agent/main.py").read_text(encoding="utf-8") == "new-code\n"
    assert not (dest / "agent/removed.py").exists(), "源码删除仍须同步（--delete 未被削弱）"


@requires_rsync
def test_source_tree_resources_are_not_shipped(tmp_path):
    """源树带（不同内容的）resources → 不传输、不覆盖：Ansible 不再是资源下发通道。"""
    src, dest = _seed_resources_layout(tmp_path, src_resources=True)

    completed = _run_update(src, dest, _host_local_flags())

    assert completed.returncode == 0, completed.stderr
    assert (dest / "resources/aimonkey/monkey.bin").read_text(encoding="utf-8") == "host-copy\n"
    assert not (dest / "resources/new-only.bin").exists()


@requires_rsync
def test_exclude_without_protect_wipes_host_resources(tmp_path):
    """反例见证：`resources/` 只发 `--exclude`（不配 protect）时整树被 `--delete-excluded` 清掉。"""
    src, dest = _seed_resources_layout(tmp_path, src_resources=False)

    completed = _run_update(src, dest, [f"--exclude={RESOURCES_PATH}"])

    assert completed.returncode == 0, completed.stderr
    assert not (dest / "resources").exists()


def test_playbook_guards_host_local_paths_in_dry_run_and_sync():
    text = UPDATE_PLAYBOOK.read_text(encoding="utf-8")
    fragment = (
        "{% for item in agent_host_local_paths %}"
        "--exclude='{{ item }}' --filter='protect {{ item }}'"
    )

    assert text.count(fragment) == 2, "dry-run 与真实同步必须都带保护规则"
    assert "--delete-excluded" in text


@requires_rsync
def test_host_local_files_survive_update(tmp_path):
    src, dest = _seed_layouts(tmp_path)

    completed = _run_update(src, dest, _host_local_flags())

    assert completed.returncode == 0, completed.stderr
    # 主机本地资源不传输、不删除
    assert (dest / "resources/mtbf/apk.bin").read_text(encoding="utf-8") == "host-local-apk\n"
    assert (dest / "resources/mtbf/local-only.bin").exists()
    assert (dest / "resources/mtbf/sub/deep.bin").exists()
    # 源码删除仍按预期同步，原有排除清理不被破坏
    assert not (dest / "agent/removed.py").exists()
    assert not (dest / "__pycache__/x.pyc").exists()
    assert (dest / "agent/main.py").read_text(encoding="utf-8") == "new-code\n"


@requires_rsync
def test_exclude_without_protect_witnesses_deletion_bug(tmp_path):
    """反例见证：只发 `--exclude`（修复前形态）时 MTBF 资源被清掉。

    这条锁的是根因而非风格——若 rsync 语义变化让 exclude 自动豁免，
    该用例变红，提醒重新核对保护规则是否仍然必要/正确。
    """
    src, dest = _seed_layouts(tmp_path)

    completed = _run_update(src, dest, [f"--exclude={MTBF_PATH}"])

    assert completed.returncode == 0, completed.stderr
    assert not (dest / "resources/mtbf/local-only.bin").exists()
