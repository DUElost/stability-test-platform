"""远端脚本的提权路径契约（#2024 → #2180）：wrapper 是唯一提权面，缺失即 fail-closed。

背景（#2024）：资源层的「能力协商回退」把两类主机混进同一个 legacy 臂——
(a) wrapper 在场但版本旧（sudoers 已按 ADR-0037 收窄：仅 wrapper + 固定 systemctl）；
(b) #1250 前的宽 sudoers 存量机。对 (a) 执行 `sudo rsync`/`sudo tee` 必被拒
（SSH exec 无 tty → sudo 要密码），在 `set -e` 下静默中止整段脚本。

D 步（#2180 / ADR-0037 §5 Revisit #1）：宽 sudoers 48/48 已清除，legacy 臂对
(a)(b) 都不可用；远端脚本改为**脚本头 selftest 前置 + fail-closed**，所有动作
只经 `stp-agent-priv`。本文件用 **PATH shim 沙箱真跑 `_REMOTE_SCRIPT`**：stub
sudo 按场景授权/拒绝并记录全部调用，断言三类场景（旧 wrapper / 无 wrapper /
健康 wrapper）的控制流与输出。
"""

from __future__ import annotations

import os
import re
import subprocess
import tarfile
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
        *capabilities*)
            # #2319：能力清单。默认给全量；STUB_WRAPPER_CAPS 可指定子集，用来模拟
            # 「缺一个子命令但自身自洽」的旧 wrapper（selftest 仍 exit 0）。
            if [ "${STUB_HAS_WRAPPER:-1}" = "1" ]; then
                printf '%s\n' ${STUB_WRAPPER_CAPS:-apply-code apply-resources install-schema write-version write-digest sync-env fix-ownership deps-marker restart}
                exit 0
            fi
            exit 1 ;;
        *selftest*)
            # 旧 wrapper：契约子命令缺失 → selftest 非零（tail exit 2 的等价场景）
            if [ "${STUB_WRAPPER_OLD:-0}" = "1" ]; then exit 3; fi
            [ "${STUB_HAS_WRAPPER:-1}" = "1" ] && exit 0 || exit 1 ;;
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


# ── 场景 A：wrapper 陈旧（selftest 契约校验失败）→ fail-closed + 指引，绝不裸 sudo ──
# ── 场景 B：无 wrapper（#1250 前存量机）→ 同样 fail-closed；即便宽 sudo 仍可用也不走 ──


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param({"STUB_WRAPPER_OLD": "1"}, id="old-wrapper"),
        pytest.param(
            {"STUB_HAS_WRAPPER": "0", "STUB_ALLOW_BROAD_SUDO": "1"},
            id="no-wrapper-broad-sudo-available",
        ),
    ],
)
def test_unusable_wrapper_fails_closed_with_guidance_and_no_broad_sudo(sandbox, scenario):
    result = _run(sandbox, **scenario)
    assert result.returncode != 0, "wrapper 不可用时必须显式失败（不可静默继续）"
    out = result.stdout + result.stderr
    assert "stp-agent-priv selftest failed" in out and "update_agent.yml" in out, (
        f"必须给出可执行指引（run update_agent.yml），实际输出：\n{out[-800:]}"
    )
    # fail-closed 发生在打 mode 哨兵之前：审计上就是 unknown，不伪装成已迁移主机
    assert "STP_PRIV_MODE=wrapper" not in result.stdout
    assert "STP_RESOURCES_APPLIED=1" not in result.stdout
    # legacy 哨兵/回退面彻底退役
    assert "STP_RESOURCES_PRIV_FALLBACK" not in out
    assert "STP_PRIV_FALLBACK" not in out
    assert not _broad_sudo_calls(sandbox), (
        f"wrapper 模式下不得发起裸 sudo（#2024/#2180）：{_broad_sudo_calls(sandbox)}"
    )


# ── 场景 C：健康 wrapper → 全绿；提权调用 100% 经 wrapper ───────────────────


def test_healthy_wrapper_runs_to_completion_via_wrapper_only(sandbox):
    result = _run(sandbox)
    assert result.returncode == 0, f"健康 wrapper 必须全绿：\n{result.stdout[-800:]}"
    assert "STP_PRIV_MODE=wrapper" in result.stdout
    assert "STP_RESOURCES_APPLIED=1" in result.stdout
    assert not _broad_sudo_calls(sandbox), (
        f"所有提权调用必须经 wrapper：{_broad_sudo_calls(sandbox)}"
    )
    calls = sandbox["log"].read_text()
    for sub in ("selftest", "apply-code", "fix-ownership", "restart",
                "write-digest", "apply-resources"):
        assert re.search(rf"^{re.escape(PRIV)} {sub}\b", calls, re.M), (
            f"wrapper 子命令 {sub} 未被调用：\n{calls}"
        )


# ── 静态契约：指引在场；legacy 回退面已退役 ─────────────────────────────────


def test_remote_script_contains_actionable_guidance_and_no_legacy_face():
    _REMOTE_SCRIPT, _unused = _control_plane_symbols()

    assert "stp-agent-priv selftest failed" in _REMOTE_SCRIPT
    assert "update_agent.yml" in _REMOTE_SCRIPT
    for token in (
        "STP_PRIV_FALLBACK",
        "STP_RESOURCES_PRIV_FALLBACK",
        "USE_PRIV_WRAPPER",
        "USE_RES_WRAPPER",
        "sudo rsync",
        "sudo tee",
        "sudo chown",
        "sudo mkdir",
        "sudo install",
        "sudo systemctl",
    ):
        assert token not in _REMOTE_SCRIPT, f"legacy 面未退役：{token}"


def test_old_but_self_consistent_wrapper_fails_before_any_write(sandbox):
    """#2319：缺一个子命令、但 `selftest` 仍 OK 的旧 wrapper 必须在**任何写动作之前**收口。

    这正是 #2180 删掉逐能力探针后留下的缺口：selftest 只证 wrapper 自洽，脚本会在第一次
    调用缺失子命令处被 argparse 拒绝——而此时 apply-code/restart 已经执行（#1942 修掉的
    半态，且失败文案只有 usage 行）。
    """
    result = _run(
        sandbox,
        STUB_WRAPPER_CAPS="apply-code write-version sync-env fix-ownership restart",
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "apply-resources" in output, "缺失的子命令要被点名"
    assert "update_agent.yml" in output, "必须带可执行指引"
    log = sandbox["log"].read_text(encoding="utf-8")
    assert "apply-code" not in log and "restart" not in log and "sync-env" not in log, (
        f"写动作不得执行：{log}"
    )
    assert not _broad_sudo_calls(sandbox), "失败路径不得回退裸 sudo"


def test_required_priv_subcommands_cover_every_call_in_the_script(sandbox):
    """守卫（#2319）：脚本里每个 `$PRIV <sub>` 调用都必须在期望集合内。

    否则「新增了一个 wrapper 调用、忘同步集合」会让前置判据放行旧 wrapper——判据就又
    变成自指的。
    """
    from backend.services.host_updater import _REQUIRED_PRIV_SUBCOMMANDS

    script = sandbox["script"].read_text(encoding="utf-8")
    called = set(re.findall(r'sudo "\$PRIV" ([a-z][a-z-]*)', script))
    assert called, "未从渲染脚本里解析到任何 $PRIV 调用"

    missing = called - set(_REQUIRED_PRIV_SUBCOMMANDS)
    assert not missing, (
        f"脚本调用了未登记的子命令 {sorted(missing)}——同步 host_updater._REQUIRED_PRIV_SUBCOMMANDS"
    )
