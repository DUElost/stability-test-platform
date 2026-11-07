"""#3889: ScanRunner 扫描根必须委托正式解析器 aee.paths.get_aee_local_root。

部署布局（``agent`` 顶层包、无 ``backend.`` 包）下，``scan_runner.py`` 曾用
``try: from backend.agent.aee.paths import get_aee_local_root except ImportError``
落到一个**本地重写**的回退实现，它只认 ``STP_AEE_LOCAL_ROOT``，没有 SSD 回退，
于是扫描根解析为 ``/mnt/hdd/aee_events``，与采集侧（正式解析器）不一致。

验收（反例先行，在未修复的 main 上先失败）：
- 部署布局回归：空 ``STP_AEE_LOCAL_ROOT`` + 可写 SSD 回退目录 → 扫描根 == 采集根 == SSD 目录；
  main 上应为 ``/mnt/hdd/aee_events``。
- ``bootstrap_subsystems``：一次解析、ScanRunner 与采集侧同源。
- 静态守卫：``backend/agent`` 生产代码（不含 tests）只 ``aee/paths.py`` 有 ``def get_aee_local_root``。
- ``STP_AEE_LOCAL_ROOT`` 有值时行为与 main 一致（覆盖 15.101 这类配置）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO_ROOT / "backend" / "agent"
BACKEND_DIR = REPO_ROOT / "backend"

# 在独立解释器里模拟 Agent 安装布局：sys.path 只有 backend/ 目录（顶层 ``agent``
# 包可用），并把 ``backend`` 置为 None 以确定性禁止 ``backend.`` 顶层包，复刻
# #3889 最小复现。子进程打印一行 PROBE_JSON= 供 pytest 断言。
_INSTALL_LAYOUT_PROBE = r"""
import json, sys
sys.modules["backend"] = None  # 安装布局没有 backend. 顶层包
try:
    import backend  # noqa: F401
    backend_importable = True
except ImportError:
    backend_importable = False
import agent.aee.paths as paths
__FSTYPE_PATCH__
import agent.scan_runner as sr
canonical = str(paths.get_aee_local_root())
runner = sr.ScanRunner.instance()
runner.configure(force=True)
print("PROBE_JSON=" + json.dumps({
    "backend_importable": backend_importable,
    "canonical": canonical,
    "scan_root": str(runner._hdd_root),
    "same_object": sr.get_aee_local_root is paths.get_aee_local_root,
}))
"""


def _probe_install_layout(
    tmp_path: Path, *, local_root: str, ssd_dir: Path, fake_fstype: bool = False,
) -> dict:
    """在临时 cwd（仓库外）以安装布局运行探针，返回解析后的 JSON。"""
    ssd_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_DIR)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["STP_AEE_LOCAL_ROOT"] = local_root
    env["STP_AEE_SSD_FALLBACK_ROOT"] = str(ssd_dir)
    # 15.101 的 /mnt/hdd/aee_events 是真实可写 HDD；CI 的 tmp_path 多为 tmpfs，
    # 正式解析器用 _is_writable_hdd_root 排除 tmpfs，此处按需把夹具目录当 ext4。
    # 直接把这一行代码注入探针源码，避免引入一个需登记的自造环境变量。
    fstype_line = (
        'paths._mount_fstype_for_path = lambda p: "ext4"  # 夹具非 tmpfs'
        if fake_fstype else "pass  # 保持真实文件系统判定"
    )
    code = _INSTALL_LAYOUT_PROBE.replace("__FSTYPE_PATCH__", fstype_line)
    # 不把生产控制面的 NFS/包引用等键带进子进程，避免解析器读到环境噪声。
    for key in (
        "STP_AEE_NFS_ROOT", "STP_NFS_ROOT", "STP_AEE_CIFS_ROOT",
        "STP_WATCHER_NFS_BASE_DIR", "STP_CONTROL_PLANE_STORAGE_ROOT",
        "STP_DEDUP_SCAN_PACKAGE_REF", "STP_UNISOC_LOG_SCAN_PACKAGE_REF",
        "STP_UNISOC_SCAN_RESULT_PACKAGE_REF",
    ):
        env.pop(key, None)
    proc = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        "安装布局探针失败：\nstdout:\n" + proc.stdout + "\nstderr:\n" + proc.stderr
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE_JSON=")]
    assert lines, "探针未输出 PROBE_JSON：\n" + proc.stdout + "\n" + proc.stderr
    return json.loads(lines[-1][len("PROBE_JSON="):])


def test_install_layout_scan_root_delegates_to_canonical_ssd_fallback(tmp_path):
    """反例：main 上扫描根为 /mnt/hdd/aee_events（缺 SSD 回退），与采集根不一致。"""
    ssd = tmp_path / "ssd_fallback"
    data = _probe_install_layout(tmp_path, local_root="", ssd_dir=ssd)

    assert data["backend_importable"] is False, "探针未真正模拟安装布局"
    assert data["canonical"] == str(ssd), "采集根应回退到可写 SSD 目录"
    assert data["scan_root"] == str(ssd), (
        "扫描根必须等于采集根 == SSD 回退目录；"
        "未修复的 main 会得到 /mnt/hdd/aee_events（本地回退实现缺 SSD 回退），实测="
        + data["scan_root"]
    )
    assert data["scan_root"] == data["canonical"], "采集根与扫描根必须同源"
    assert data["same_object"] is True, (
        "scan_runner 的 get_aee_local_root 必须就是 aee.paths 的同一函数对象"
    )


def test_configured_local_root_behavior_matches_main(tmp_path):
    """``STP_AEE_LOCAL_ROOT`` 有值时行为与 main 一致（覆盖 15.101 类配置）。

    这是 no-regression 守卫：main 与修复后都应把扫描根解析为配置目录，且优于
    SSD 回退目录。它的反例是「忽略 STP_AEE_LOCAL_ROOT」的变异（会把根落到 SSD）。
    """
    configured = tmp_path / "hdd_local"
    configured.mkdir()
    ssd = tmp_path / "ssd_fallback"
    data = _probe_install_layout(
        tmp_path, local_root=str(configured), ssd_dir=ssd, fake_fstype=True,
    )

    assert data["backend_importable"] is False
    assert data["canonical"] == str(configured), "配置根应被正式解析器采纳"
    assert data["scan_root"] == str(configured), "扫描根行为与 main 一致：等于配置根"
    assert data["scan_root"] == data["canonical"], "采集根与扫描根同源"
    assert data["scan_root"] != str(ssd), "配置根存在且可写时不得落到 SSD 回退"


def test_bootstrap_passes_single_resolved_root_to_scan_runner():
    """反例：main 上 ScanRunner.configure() 不带 hdd_root，需 bootstrap 内二次解析。"""
    import backend.agent.bootstrap_subsystems as bs

    resolved = "/resolved/aee_root"
    archiver = MagicMock()
    archiver.configure.return_value = archiver
    event_uploader = MagicMock()
    event_uploader.configure.return_value = event_uploader
    disk_monitor = MagicMock()
    disk_monitor.configure.return_value = disk_monitor
    with (
        patch.object(bs, "get_aee_local_root", return_value=Path(resolved)) as resolve_stub,
        patch.object(bs, "resolve_shared_storage_root", return_value="/tmp/cifs"),
        patch.object(bs, "get_disk_archive_settings", return_value=MagicMock()),
        patch.object(bs, "watcher_subsystem_enabled", return_value=False),
        patch.object(bs.LogArchiver, "instance", return_value=archiver),
        patch.object(bs.ScanRunner, "instance") as scan_instance,
        patch.object(bs.UnisocScanRunner, "instance") as unisoc_instance,
        patch.object(bs.UploadManager, "instance"),
        patch.object(bs.EventUploader, "instance", return_value=event_uploader),
        patch.object(bs.LocalDiskMonitor, "instance", return_value=disk_monitor),
    ):
        bs.start_disk_and_watcher_subsystems(
            local_db=MagicMock(),
            api_url="http://x",
            agent_secret="s",
            host_id="h1",
            agent_instance_id="inst",
            adb=MagicMock(),
            adb_path="adb",
            sio_client=MagicMock(),
        )

    # 采集根只解析一次（bootstrap 第 54 行），不在 ScanRunner.configure 内二次解析。
    resolve_stub.assert_called_once_with()
    # ScanRunner 收到与采集侧同源的 hdd_root；main 上 configure() 无参 → 断言失败。
    # LogArchiver 的 configure 只收 run_log_dir，不收 hdd_root；采集侧真正持有
    # hdd_root 的是 LocalDiskMonitor，断言二者与 ScanRunner 拿到同一解析值（同源）。
    scan_instance.return_value.configure.assert_called_once_with(hdd_root=resolved)
    assert disk_monitor.configure.call_args.kwargs["hdd_root"] == resolved
    archiver.configure.assert_called_once()
    # UnisocScanRunner.configure 签名同样接受 hdd_root 形参，一并按规格传入
    #（其内部 del 丢弃并由 MTK ScanRunner._prepare_scan_root 派生扫描根，属同源）。
    unisoc_instance.return_value.configure.assert_called_once_with(hdd_root=resolved)


def test_only_one_get_aee_local_root_definition_in_agent_sources():
    """反例：main 上 scan_runner.py + aee/paths.py 共两处 def → 断言失败。"""
    pattern = re.compile(r"^\s*def\s+get_aee_local_root\b")
    hits = []
    for path in sorted(AGENT_DIR.rglob("*.py")):
        if "tests" in path.relative_to(AGENT_DIR).parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.match(line):
                hits.append((str(path.relative_to(REPO_ROOT)), lineno))

    assert len(hits) == 1, (
        "backend/agent 生产代码（不含 tests）只允许 aee/paths.py 定义 get_aee_local_root，"
        "实际定义：" + repr(hits)
    )
    assert hits[0][0] == "backend/agent/aee/paths.py", (
        "唯一的 get_aee_local_root 必须位于 aee/paths.py，实际：" + repr(hits)
    )
    scan_runner_src = (AGENT_DIR / "scan_runner.py").read_text(encoding="utf-8")
    assert "def get_aee_local_root" not in scan_runner_src, (
        "scan_runner.py 不得再重写 get_aee_local_root，只能从 .aee.paths 导入"
    )
    assert "from .aee.paths import get_aee_local_root" in scan_runner_src, (
        "scan_runner.py 必须用相对导入委托正式解析器"
    )
