"""远端脚本的提权路径契约（#2024）：wrapper 模式绝不走裸 sudo，失败必须显式可指引。

背景（#2024）：资源层的「能力协商回退」把两类主机混进同一个 legacy 臂——
(a) wrapper 在场但版本旧（sudoers 已按 ADR-0037 收窄：仅 wrapper + 固定 systemctl）；
(b) #1250 前的宽 sudoers 存量机。对 (a) 执行 `sudo rsync`/`sudo tee` 必被拒
（SSH exec 无 tty → sudo 要密码），在 `set -e` 下静默中止整段脚本。

这些断言无法用静态检查覆盖（"静态看着对、运行才炸"），因此本文件用 **PATH shim
沙箱真跑 `_REMOTE_SCRIPT`**：stub `sudo` 按场景授权/拒绝并记录全部调用，然后断言
控制流与输出。
"""

from __future__ import annotations

import os
import re
import subprocess
import tarfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRIV = "/usr/local/sbin/stp-agent-priv"
SERVICE = "stability-test-agent"

SUDO_SHIM = r"""#!/usr/bin/env bash
# stub sudo：剥离 -n 等前导标志后判定目标；一切调用记入 $STUB_SUDO_LOG（已归一化）
args=("$@")
while [ "${#args[@]}" -gt 0 ] && [ "${args[0]:0:1}" = "-" ]; do
    args=("${args[@]:1}")
done
printf '%s\n' "${args[*]}" >> "$STUB_SUDO_LOG"
if [ "${args[0]}" = "__PRIV__" ]; then
    shift_args="${args[*]:1}"
    case "$shift_args" in
        *selftest*)
            [ "${STUB_HAS_WRAPPER:-1}" = "1" ] && exit 0 || exit 1 ;;
        *apply-resources*--help*)
            [ "${STUB_WRAPPER_OLD:-0}" = "1" ] && exit 3 || exit 0 ;;
        *)
            exit 0 ;;
    esac
fi
if [ "${STUB_ALLOW_BROAD_SUDO:-0}" = "1" ]; then
    exit 0
fi
echo "sudo: a password is required" >&2
exit 1
"""
SUDO_SHIM = SUDO_SHIM.replace("__PRIV__", PRIV)
SUDO_SHIM = SUDO_SHIM.replace("__PRIV__", PRIV)

SYSTEMCTL_SHIM = """#!/usr/bin/env bash
case "$1" in
    is-active) exit 0 ;;
    *) exit 0 ;;
esac
"""

PIP_SHIM = """#!/usr/bin/env bash
exit 0
"""


def _control_plane_symbols():
    try:
        from backend.services.host_updater import _REMOTE_SCRIPT, _build_remote_script
    except Exception as exc:  # pragma: no cover - 取决于本机/CI 的 env 解析
        pytest.skip(f"控制面模块不可导入（{type(exc).__name__}: {exc}）")
    return _REMOTE_SCRIPT, _build_remote_script


def _make_tarball(path: Path, files: dict[str, str]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, content in files.items():
            tmp = path.parent / f".src-{name.replace('/', '_')}"
            tmp.write_text(content, encoding="utf-8")
            tar.add(tmp, arcname=name)


@pytest.fixture
def sandbox(tmp_path):
    """构造可运行远端脚本的最小沙箱：install_dir + tar + PATH shim。"""
    _unused, _build_remote_script = _control_plane_symbols()

    install = tmp_path / "install"
    (install / "agent").mkdir(parents=True)
    (install / "agent" / "requirements.txt").write_text("x", encoding="utf-8")
    (install / "agent" / "resources").mkdir()
    (install / ".env").write_text("HOST_ID=x\n", encoding="utf-8")
    (install / "venv" / "bin").mkdir(parents=True)
    pip = install / "venv" / "bin" / "pip"
    pip.write_text(PIP_SHIM, encoding="utf-8")
    pip.chmod(0o755)

    code_tar = tmp_path / "code.tar.gz"
    res_tar = tmp_path / "resources.tar.gz"
    _make_tarball(code_tar, {"agent/main.py": "print(1)\n", "agent/requirements.txt": "x"})
    _make_tarball(res_tar, {"resources/keep.txt": "keep\n"})

    shim = tmp_path / "shim"
    shim.mkdir()
    for name, body in (("sudo", SUDO_SHIM), ("systemctl", SYSTEMCTL_SHIM)):
        p = shim / name
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)

    script = tmp_path / "remote.sh"
    script.write_text(
        _build_remote_script(
            install_dir=str(install),
            service_name=SERVICE,
            code_tar_path=str(code_tar),
            resources_tar_path=str(res_tar),
            user="android",
            group="android",
            artifact_digest="sha256:" + "a" * 64,
            resources_digest="sha256:" + "b" * 64,
        ),
        encoding="utf-8",
    )
    log = tmp_path / "sudo.log"
    log.write_text("", encoding="utf-8")
    return {
        "script": script,
        "install": install,
        "log": log,
        "env": {
            **os.environ,
            "PATH": f"{shim}:{os.environ['PATH']}",
            "STUB_SUDO_LOG": str(log),
        },
    }


def _run(sandbox, **scenario):
    env = {**sandbox["env"], **scenario}
    return subprocess.run(
        ["bash", str(sandbox["script"])],
        capture_output=True, text=True, cwd=sandbox["script"].parent, env=env,
    )


def _broad_sudo_calls(sandbox) -> list[str]:
    """记录里非 wrapper 的 sudo 调用（裸 rsync/tee/chown/…）。"""
    return [
        line for line in sandbox["log"].read_text().splitlines()
        if line.strip() and not line.startswith(PRIV)
    ]


# ── 场景 A：wrapper 在场但版本旧（sudoers 已收窄）→ 显式失败 + 指引，绝不裸 sudo ──


def test_old_wrapper_fails_fast_with_guidance_and_no_broad_sudo(sandbox):
    result = _run(sandbox, STUB_WRAPPER_OLD="1", STUB_ALLOW_BROAD_SUDO="0")
    assert result.returncode != 0, "wrapper 缺 apply-resources 时必须显式失败（不可静默继续）"
    out = result.stdout + result.stderr
    assert "STP_RESOURCES_PRIV_FALLBACK=legacy" in out, "哨兵缺失（审计需要）"
    assert "lacks apply-resources" in out and "update_agent.yml" in out, (
        f"必须给出可执行指引（run update_agent.yml），实际输出：\n{out[-800:]}"
    )
    assert not _broad_sudo_calls(sandbox), (
        f"wrapper 模式下不得发起裸 sudo（#2024）：{_broad_sudo_calls(sandbox)}"
    )


# ── 场景 B：无 wrapper（#1250 前宽 sudoers 存量机）→ legacy 路径保持可用 ──


def test_legacy_host_without_wrapper_still_uses_broad_sudo(sandbox):
    result = _run(sandbox, STUB_HAS_WRAPPER="0", STUB_ALLOW_BROAD_SUDO="1")
    assert result.returncode == 0, f"存量机 legacy 路径必须保持可用：\n{result.stdout[-800:]}"
    calls = _broad_sudo_calls(sandbox)
    assert any("rsync" in c for c in calls), f"应走 legacy rsync：{calls}"
    assert "STP_RESOURCES_APPLIED=1" in result.stdout


# ── 静态契约：失败指引在场、混合分支已移除 ──────────────────────────────────


def test_remote_script_contains_actionable_guidance_and_no_mixed_branch():
    _REMOTE_SCRIPT, _unused = _control_plane_symbols()

    assert "lacks apply-resources" in _REMOTE_SCRIPT
    assert "update_agent.yml" in _REMOTE_SCRIPT
    assert "USE_RES_WRAPPER=0" not in _REMOTE_SCRIPT, (
        "资源层不得再把「wrapper 旧版本」并入 legacy 臂（#2024 的混合分支）"
    )
