"""stp-agent-priv 子命令接线契约（#2011）：真实 argv parse 断言 + 控制面调用面交叉核验。

背景（#2011）：`apply-resources` 的 argparse 接线错误——`add_parser` 返回值被丢弃、
`--digest` 被挂到 `write-digest`——使该子命令**从落地起不可用**，而 `--help` 形态的
能力探针照常 exit 0，真实调用 exit 2，配合远端脚本 `set -e` 中止整段热更新。

本文件锁定三件事：

1. 每个子命令能解析其**代表性真实 argv**（探针做不到的断言）；
2. ``selftest`` 能报出接线缺陷（控制面远端脚本开头即跑它，失配即 fail-closed，#2180）；
3. 控制面 ``_build_remote_script`` 实际发出的 ``$PRIV`` 调用**全部**被解析器接受
   ——两侧口径漂移在 PR 侧即红（#2011 形态的根因是这条断言缺失）。
4. 跨边界哨兵：env 同步的 ``STP_ENV_SYNCED=``/``STP_ENV_PATH_MISSING=`` 由 wrapper
   发射（#2180 起 host_updater 不再自持 env 写入），控制面解析面依赖其拼写。
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "backend/agent/stp_agent_priv.py"
DIGEST = "sha256:" + "0" * 64


@pytest.fixture
def wrapper(monkeypatch):
    """按路径隔离加载 wrapper（不 import 控制面模块，保持离线）。"""
    spec = importlib.util.spec_from_file_location("stp_agent_priv_contract", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_require_root", lambda: None)
    return module


# ── ① 接线契约：真实 argv parse ────────────────────────────────────────────


def test_apply_resources_accepts_staged(wrapper):
    parser = wrapper._build_parser()
    ns = parser.parse_args(["apply-resources", "--staged", "/tmp/staged"])
    assert ns.command == "apply-resources"
    assert ns.staged == "/tmp/staged"


def test_apply_resources_without_staged_is_rejected_with_reason(wrapper, capsys):
    """缺 --staged 必须报「required」——这正是 --help 探针看不出来的失败面。"""
    parser = wrapper._build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["apply-resources"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--staged" in err and "required" in err


def test_write_digest_wires_kind_and_digest(wrapper):
    parser = wrapper._build_parser()
    ns = parser.parse_args(["write-digest", "--kind", "resources", "--digest", DIGEST])
    assert (ns.command, ns.kind, ns.digest) == ("write-digest", "resources", DIGEST)


def test_usb_authorized_wires_port_and_value(wrapper):
    parser = wrapper._build_parser()
    ns = parser.parse_args(["usb-authorized", "--port", "1-5.3.1", "--value", "0"])
    assert (ns.command, ns.port, ns.value) == ("usb-authorized", "1-5.3.1", "0")


def test_usb_authorized_without_port_is_rejected_with_reason(wrapper, capsys):
    """缺 --port 必须报「required」——同 #2011 形态：真实调用的失败面。"""
    parser = wrapper._build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["usb-authorized", "--value", "0"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--port" in err and "required" in err


def test_usb_authorized_rejects_unknown_value_at_parse_time(wrapper, capsys):
    parser = wrapper._build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["usb-authorized", "--port", "1-1", "--value", "2"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_ensure_udev_rule_accepts_no_args(wrapper):
    parser = wrapper._build_parser()
    ns = parser.parse_args(["ensure-udev-rule"])
    assert ns.command == "ensure-udev-rule"


def test_full_contract_passes_on_current_wiring(wrapper):
    assert wrapper._validate_parser_contract(wrapper._build_parser()) == []


def test_contract_detects_the_2011_miswiring(wrapper):
    """复刻 #2011 形态（apply-resources 未接线 --staged）→ 校验器必须报出该子命令。"""
    broken = argparse.ArgumentParser(prog="stp-agent-priv")
    broken.add_subparsers(dest="command").add_parser(
        "apply-resources", help="sync staged resources/",
    )
    problems = wrapper._validate_parser_contract(broken)
    assert any("apply-resources rejected contract argv" in p for p in problems), problems


def test_selftest_surfaces_contract_problems(wrapper, monkeypatch, tmp_path, capsys):
    fake = tmp_path / "stp-agent-priv"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(wrapper, "WRAPPER_PATH", str(fake))
    monkeypatch.setattr(
        wrapper.os, "stat", lambda path: SimpleNamespace(st_uid=0, st_mode=0o755),
    )
    conf = {"INSTALL_DIR": "/opt/stability-test-agent"}

    assert wrapper.cmd_selftest(SimpleNamespace(), conf) == 0
    assert "STP_AGENT_PRIV_SELFTEST_OK" in capsys.readouterr().out

    monkeypatch.setattr(
        wrapper, "_validate_parser_contract", lambda parser: ["contract boom"],
    )
    assert wrapper.cmd_selftest(SimpleNamespace(), conf) == 1
    assert "contract boom" in capsys.readouterr().out


# ── ② 控制面远端脚本 ⇄ wrapper 解析器 交叉核验 ──────────────────────────────


def _extract_priv_calls(script: str) -> list[tuple[str, list[str]]]:
    """抽取远端脚本的 ``"$PRIV" <cmd> <opts...>`` 调用（跳过 --help 存在性探针）。

    返回 ``(command, [--options])``——只取选项名，不取选项值（值可能是 shell 变量或
    枚举字面量，替换占位值会引入假失败）。
    """
    calls: list[tuple[str, list[str]]] = []
    for line in script.splitlines():
        marker = '"$PRIV" '
        idx = line.find(marker)
        if idx == -1:
            continue
        tokens = line[idx + len(marker):].strip().split()
        if not tokens:
            continue
        command, opts = tokens[0], tokens[1:]
        if "--help" in opts:
            continue
        calls.append((command, [tok for tok in opts if tok.startswith("--")]))
    return calls


def _subparser_map(parser):
    for action in getattr(parser, "_actions", []):
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and choices:
            return choices
    raise AssertionError("解析器未注册子命令")


def test_remote_script_priv_calls_are_accepted_by_wrapper_parser(wrapper):
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
        artifact_digest=DIGEST,
        resources_digest=DIGEST,
    )
    calls = _extract_priv_calls(script)
    assert len(calls) >= 10, f"抽取器失效：只找到 {calls}"

    subparsers = _subparser_map(wrapper._build_parser())
    for command, options in calls:
        assert command in subparsers, f"远端脚本调用了未注册子命令: {command}"
        known = {
            opt
            for action in subparsers[command]._actions
            for opt in action.option_strings
        }
        for opt in options:
            assert opt in known, f"{command} 不认识 {opt}（#2011 形态：探针过、真调用挂）"


# ── ③ 跨边界哨兵：env 同步结果由 wrapper 发射，控制面解析（#2180） ──────────


def test_wrapper_sync_env_emits_control_plane_sentinels(wrapper):
    """#2180：env 写入面收归 wrapper 后，`STP_ENV_SYNCED=`/`STP_ENV_PATH_MISSING=`
    的**发射方**变成 wrapper——控制面 host_updater._parse_env_synced /
    _parse_env_paths_missing 仍按这两个拼写解析远端 stdout。

    这是跨模块的静默缝：wrapper 若改名/少打哨兵，API 只会静默退化为
    「无 env 键同步 / 无缺失路径」，不会报错，故在此锁定。
    """
    import backend.services.host_updater as hu

    source = WRAPPER.read_text(encoding="utf-8")
    assert 'print("STP_ENV_SYNCED=' in source
    assert 'print("STP_ENV_PATH_MISSING=' in source

    # 控制面解析器接受 wrapper 的两种实际输出（非空与空集分支）
    assert hu._parse_env_synced("STP_ENV_SYNCED=A,B\nSTP_ENV_PATH_MISSING=\nOK") == ["A", "B"]
    assert hu._parse_env_paths_missing("STP_ENV_SYNCED=\nSTP_ENV_PATH_MISSING=\nOK") == {}

    # 发射面唯一：远端脚本不再自带哨兵字面量
    script = hu._build_remote_script(
        install_dir="/opt/stability-test-agent",
        service_name="stability-test-agent",
        code_tar_path="/tmp/code.tar.gz",
        resources_tar_path="/tmp/resources.tar.gz",
        user="android",
        group="android",
    )
    assert "STP_ENV_SYNCED=" not in script
    assert "STP_ENV_PATH_MISSING=" not in script
