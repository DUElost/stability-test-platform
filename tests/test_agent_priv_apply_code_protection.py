"""apply-code 对部署态元数据的保护面（#2091）。

背景：`apply-code` 的 rsync 带 `--delete --delete-excluded`，未在 filter 里出现
的接收端文件会被删除——`VERSION` / `ARTIFACT_DIGEST` / `ARTIFACT_DIGEST_RESOURCES`
此前既不在暂存树、也不在保护清单，于是 **code-only 收敛**会把 resources 记号删掉，
而本轮资源层未运行（无人重写）→ 文件与主机列记录分叉（列因心跳空值不覆盖而保留
旧值）。本文件锁定唯一通道的保护面：

1. wrapper（`apply-code`）——`protect` filter，防 `--delete-excluded`；
2. （#2180 起）远端脚本不再自持 rsync 面——legacy 分支已退役，脚本侧零 filter。
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "backend/agent/stp_agent_priv.py"

METADATA = ("VERSION", "ARTIFACT_DIGEST", "ARTIFACT_DIGEST_RESOURCES")


@pytest.fixture(scope="module")
def wrapper():
    spec = importlib.util.spec_from_file_location("stp_agent_priv_protect", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── ① wrapper：filter 面 ──────────────────────────────────────────────────


def test_apply_code_filters_protect_deploy_metadata(wrapper):
    filters = wrapper.build_apply_code_filters()
    for name in METADATA:
        assert "--filter=protect %s" % name in filters, filters
        # 不能 exclude：--delete-excluded 下 exclude 等于「显式删除」
        assert "--exclude=%s" % name not in filters, filters
    # resources/ 仍是 protect-only（#1950 语义不回退）；mtbf/ 仍需 exclude
    # #2019：必须是 `resources/***`（整树）——尾斜杠只护目录节点本身，
    # 源树含任一 resources/* 时接收端其余内容会被 --delete 清掉。
    assert "--filter=protect resources/***" in filters
    assert "--filter=protect resources/" not in filters
    assert "--exclude=resources/mtbf/" in filters


def test_metadata_survives_apply_code_rsync(wrapper, tmp_path):
    """用 wrapper 的真实 filter 参数跑一次 rsync：元数据必须活下来、正常删除不回归。"""
    rsync = shutil.which("rsync")
    if not rsync:
        pytest.skip("rsync 不可用")

    staged, dest = tmp_path / "staged", tmp_path / "dest"
    (staged / "agent").mkdir(parents=True)
    (dest / "agent").mkdir(parents=True)
    (staged / "agent" / "main.py").write_text("new_content", encoding="utf-8")
    (dest / "agent" / "main.py").write_text("old", encoding="utf-8")
    (dest / "agent" / "stale_module.py").write_text("stale", encoding="utf-8")
    for name in METADATA:
        (dest / "agent" / name).write_text("sha256:" + "a" * 64, encoding="utf-8")
    (dest / "agent" / "resources").mkdir()
    (dest / "agent" / "resources" / "keep.bin").write_bytes(b"big")

    argv = [rsync, "-a", "--no-owner", "--no-group", "--delete", "--delete-excluded",
            "--safe-links"] + wrapper.build_apply_code_filters() + [
        str(staged / "agent") + "/", str(dest / "agent") + "/",
    ]
    subprocess.run(argv, check=True, capture_output=True)

    for name in METADATA:
        assert (dest / "agent" / name).is_file(), f"{name} 被 apply-code 删除（#2091 回归）"
    assert (dest / "agent" / "main.py").read_text(encoding="utf-8") == "new_content"
    assert not (dest / "agent" / "stale_module.py").exists(), "普通删除语义不应失效"
    assert (dest / "agent" / "resources" / "keep.bin").is_file()


# ── ② 远端脚本：legacy rsync 面退役（#2180）→ 保护面单点在 ① ───────────────


def test_remote_script_has_no_self_owned_rsync_face():
    """#2180：脚本不再自持 rsync/filter；元数据保护只剩 wrapper 一条通道（①）。"""
    try:
        from backend.services.host_updater import _build_remote_script
    except Exception as exc:  # pragma: no cover - 取决于本机/CI 的 env 解析
        pytest.skip(f"控制面模块不可导入（{type(exc).__name__}: {exc}）")

    script = _build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        code_tar_path="/tmp/code.tar.gz",
        resources_tar_path="/tmp/resources.tar.gz",
        user="android",
        group="android",
        artifact_digest="sha256:" + "0" * 64,
        resources_digest="sha256:" + "0" * 64,
    )
    assert "sudo rsync" not in script
    assert "--exclude=" not in script
    assert "--filter=" not in script
    # code-only 收敛仍走 wrapper（保护语义在 ① 锁定）
    assert 'sudo "$PRIV" apply-code --staged "$CODE_TMP"' in script


def test_resources_tree_survives_apply_code_rsync_when_source_has_resources(wrapper, tmp_path):
    """#2019：源树含 `resources/*` 时，接收端 `resources/**` 仍必须整树存活。

    反例（修复前）：`--filter=protect resources/` 只护目录节点——源树一旦出现
    `resources/<新文件>`，`--delete` 就把接收端其余内容（mtbf 大件、aimonkey 等）
    清掉，且 rsync 退出 0（静默数据损失）。
    """
    rsync = shutil.which("rsync")
    if not rsync:
        pytest.skip("rsync 不可用")

    staged, dest = tmp_path / "staged", tmp_path / "dest"
    (staged / "agent" / "resources").mkdir(parents=True)
    (dest / "agent" / "resources" / "mtbf").mkdir(parents=True)
    (dest / "agent" / "resources" / "aimonkey").mkdir(parents=True)
    # 源树：新增一个 resources 项（触发 --delete 的删除传播）
    (staged / "agent" / "resources" / "new.bin").write_text("new", encoding="utf-8")
    # 接收端：主机本地大件 + 另一 resources 子树
    (dest / "agent" / "resources" / "mtbf" / "apk.bin").write_text("big", encoding="utf-8")
    (dest / "agent" / "resources" / "aimonkey" / "am.bin").write_text("big2", encoding="utf-8")
    # 对照：agent 目录下（非 resources）的陈旧文件仍应被删（--delete 未被削弱）
    (dest / "agent" / "stale.py").write_text("old", encoding="utf-8")
    # 暂存树里需要有一个 agent 层文件，否则 rsync 的 --delete 面不易观察
    (staged / "agent" / "main.py").write_text("new", encoding="utf-8")

    subprocess.run(
        [rsync, "-a", "--delete", "--delete-excluded", "--safe-links",
         *wrapper.build_apply_code_filters(),
         str(staged / "agent") + "/", str(dest / "agent") + "/"],
        check=True, capture_output=True,
    )

    assert (dest / "agent" / "resources" / "mtbf" / "apk.bin").exists(), "主机本地大件被清"
    assert (dest / "agent" / "resources" / "aimonkey" / "am.bin").exists(), "resources 子树被清"
    assert (dest / "agent" / "resources" / "new.bin").exists(), "源树新增项未同步"
    assert not (dest / "agent" / "stale.py").exists(), "--delete 被误削弱（对照项未删）"
