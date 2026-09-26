"""stp-agent-priv `read-kernel-log` 只读窄面（#2957 / ADR-0037 D7）。

守什么（对应 #2957 的裁决与实施规格）：

- **参数面只有两个整数**：`--boot` 与 `--since-epoch` 互斥必选、`--since-epoch`
  ∈ [0, now]、`--lines` ∈ [1, 5000]；`allow_abbrev=False`（`--since` 这类缩写不接受）；
  任何其它 journalctl 选项（`--file`/`-D`/`-M`/`-u`/`--grep`…）在解析层不可达；
- **子进程形态固定**：`journalctl -k --no-pager -o cat` + 窗口/行数；环境清空后只留
  `LC_ALL=C` 与 `PATH=/usr/bin:/bin`（不设 SYSTEMD_PAGER），30s 超时，8 MiB 上限；
- **截断/超时/非零退出**：以 exit 3 + `STP_READ_KERNEL_LOG_*` 标记告知，**不做部分投递**
  ——偏小的计数不得冒充完整结果（#2957 实测 `--boot` 373,600 行）；
- 与既有 flash 窄面同款加载方式（单文件脚本，不拉起包导入）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "backend/agent/stp_agent_priv.py"


@pytest.fixture
def wrapper(monkeypatch):
    spec = importlib.util.spec_from_file_location("stp_agent_priv_kernel_log", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_require_root", lambda: None)
    return module


def _args(**overrides):
    base = {"boot": False, "since_epoch": None, "lines": None}
    base.update(overrides)
    return SimpleNamespace(**base)


def _stub_journalctl(tmp_path: Path, body: str) -> Path:
    """写一个记录 argv 的 journalctl 桩；返回路径（调用方 monkeypatch JOURNALCTL_BIN）。"""
    stub = tmp_path / "journalctl"
    stub.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$0.argv"\n' + body,
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def _stub_argv(stub: Path) -> list[str]:
    return (stub.parent / (stub.name + ".argv")).read_text(encoding="utf-8").splitlines()


# ── 解析层：互斥必选 / 缩写禁用 / 未知选项不可达 ────────────────────────────


def test_boot_and_since_epoch_are_wired(wrapper):
    parser = wrapper._build_parser()
    ns = parser.parse_args(["read-kernel-log", "--boot"])
    assert (ns.command, ns.boot, ns.since_epoch) == ("read-kernel-log", True, None)
    ns = parser.parse_args(["read-kernel-log", "--since-epoch", "1700000000", "--lines", "500"])
    assert (ns.since_epoch, ns.lines, ns.boot) == (1700000000, 500, False)


def test_window_argument_is_required_and_mutually_exclusive(wrapper, capsys):
    parser = wrapper._build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["read-kernel-log"])
    assert excinfo.value.code == 2
    assert "required" in capsys.readouterr().err

    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["read-kernel-log", "--boot", "--since-epoch", "1"])
    assert excinfo.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_abbreviations_are_rejected(wrapper, capsys):
    """allow_abbrev=False：`--since` 不得被当作 `--since-epoch` 接受。"""
    parser = wrapper._build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["read-kernel-log", "--since", "1700000000"])
    assert excinfo.value.code == 2
    # argparse 先报互斥组缺失（`--since` 未被识别为任何已知选项）——两种文案都算拒绝
    err = capsys.readouterr().err
    assert "unrecognized arguments" in err or "one of the arguments" in err


@pytest.mark.parametrize(
    "extra",
    [
        ["--file", "/etc/passwd"],
        ["-D", "/var/log/journal"],
        ["-M", "other"],
        ["-u", "sshd"],
        ["--grep", "password"],
        ["--output", "json"],
    ],
)
def test_other_journalctl_options_are_unreachable(wrapper, capsys, extra):
    """路径/单元/匹配表达式等选项面在解析层就不可达（比 wrapper 内校验更前一道）。"""
    parser = wrapper._build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["read-kernel-log", "--boot"] + extra)
    assert excinfo.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_non_integer_arguments_are_rejected_at_parse_time(wrapper, capsys):
    parser = wrapper._build_parser()
    for argv in (
        ["read-kernel-log", "--since-epoch", "1e9"],
        ["read-kernel-log", "--since-epoch", "1700000000.5"],
        ["read-kernel-log", "--boot", "--lines", "many"],
    ):
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args(argv)
        assert excinfo.value.code == 2
    assert "invalid int value" in capsys.readouterr().err


# ── 执行层：取值校验（解析层之外的独立复核）─────────────────────────────────


@pytest.mark.parametrize("value", [-1, -100])
def test_execute_layer_rejects_negative_since(wrapper, value):
    with pytest.raises(wrapper.PrivError):
        wrapper.validate_since_epoch(value, now=1_700_000_000)


def test_execute_layer_rejects_future_since(wrapper):
    with pytest.raises(wrapper.PrivError):
        wrapper.validate_since_epoch(1_700_000_001, now=1_700_000_000)
    assert wrapper.validate_since_epoch(1_700_000_000, now=1_700_000_000) == 1_700_000_000
    assert wrapper.validate_since_epoch(0, now=1_700_000_000) == 0


@pytest.mark.parametrize("value", [0, -1, 5001, 10**9])
def test_execute_layer_rejects_lines_out_of_range(wrapper, value):
    with pytest.raises(wrapper.PrivError):
        wrapper.validate_kernel_log_lines(value)


def test_execute_layer_accepts_lines_bounds(wrapper):
    assert wrapper.validate_kernel_log_lines(None) is None
    assert wrapper.validate_kernel_log_lines(1) == 1
    assert wrapper.validate_kernel_log_lines(5000) == 5000


def test_argv_builder_whitelists_flags_only(wrapper):
    assert wrapper.build_kernel_log_argv(boot=False, since_epoch=1_700_000_000, lines=None) == [
        wrapper.JOURNALCTL_BIN, "-k", "--no-pager", "-o", "cat",
        "--since", "@1700000000",
    ]
    assert wrapper.build_kernel_log_argv(boot=True, since_epoch=None, lines=1) == [
        wrapper.JOURNALCTL_BIN, "-k", "--no-pager", "-o", "cat", "--boot", "--lines=1",
    ]


# ── 子命令行为：成功 / 环境清空 / 截断 / 超时 / 失败 ─────────────────────────


def test_success_prints_raw_output_and_returns_zero(wrapper, monkeypatch, tmp_path, capsys):
    stub = _stub_journalctl(tmp_path, 'echo "HC died; cleaning up"\nexit 0\n')
    monkeypatch.setattr(wrapper, "JOURNALCTL_BIN", str(stub))

    assert wrapper.cmd_read_kernel_log(_args(since_epoch=1_700_000_000), None) == 0
    assert capsys.readouterr().out == "HC died; cleaning up\n"
    assert _stub_argv(stub) == ["-k", "--no-pager", "-o", "cat", "--since", "@1700000000"]


def test_boot_and_lines_are_passed_through(wrapper, monkeypatch, tmp_path):
    stub = _stub_journalctl(tmp_path, "exit 0\n")
    monkeypatch.setattr(wrapper, "JOURNALCTL_BIN", str(stub))

    assert wrapper.cmd_read_kernel_log(_args(boot=True, lines=5000), None) == 0
    assert _stub_argv(stub) == ["-k", "--no-pager", "-o", "cat", "--boot", "--lines=5000"]


def test_child_env_is_cleared_and_locale_pinned(wrapper, monkeypatch, tmp_path, capsys):
    """裁决约束②：不复用 `_run`（后者继承调用者环境）——环境清空后只留两项。"""
    stub = _stub_journalctl(
        tmp_path,
        'printf "LC_ALL=%s\\nPATH=%s\\nPAGER=%s\\n" "$LC_ALL" "$PATH" "$PAGER"\n',
    )
    monkeypatch.setattr(wrapper, "JOURNALCTL_BIN", str(stub))
    monkeypatch.setenv("PAGER", "less")
    monkeypatch.setenv("SYSTEMD_PAGER", "less")

    assert wrapper.cmd_read_kernel_log(_args(boot=True), None) == 0
    out = capsys.readouterr().out
    assert "LC_ALL=C" in out
    assert "PATH=/usr/bin:/bin" in out
    assert "PAGER=\n" in out, "调用者环境泄漏进了子进程（PAGER 未清空）"


def test_nonzero_rc_is_failure_with_marker(wrapper, monkeypatch, tmp_path, capsys):
    stub = _stub_journalctl(tmp_path, 'echo "journal boom" >&2\nexit 1\n')
    monkeypatch.setattr(wrapper, "JOURNALCTL_BIN", str(stub))

    assert wrapper.cmd_read_kernel_log(_args(boot=True), None) == 3
    captured = capsys.readouterr()
    assert "STP_READ_KERNEL_LOG_FAILED" in captured.err and "journal boom" in captured.err
    assert captured.out == "", "失败时不得投递部分输出"


def test_missing_journalctl_is_failure(wrapper, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(wrapper, "JOURNALCTL_BIN", str(tmp_path / "absent"))
    assert wrapper.cmd_read_kernel_log(_args(boot=True), None) == 3
    assert "STP_READ_KERNEL_LOG_FAILED" in capsys.readouterr().err


def test_truncated_output_is_rejected_not_partially_delivered(wrapper, monkeypatch, tmp_path, capsys):
    """#2957：`--boot` 实测 373,600 行 ⇒ 截断样本必须显式失败（偏小计数不可当完整结果）。"""
    stub = _stub_journalctl(tmp_path, 'i=0\nwhile [ $i -lt 200 ]; do echo "line $i"; i=$((i+1)); done\n')
    monkeypatch.setattr(wrapper, "JOURNALCTL_BIN", str(stub))
    monkeypatch.setattr(wrapper, "MAX_KERNEL_LOG_BYTES", 512)

    assert wrapper.cmd_read_kernel_log(_args(boot=True), None) == 3
    captured = capsys.readouterr()
    assert "STP_READ_KERNEL_LOG_TRUNCATED" in captured.err
    assert captured.out == "", "截断样本不得部分投递"


def test_timeout_is_rejected_with_marker(wrapper, monkeypatch, tmp_path, capsys):
    stub = _stub_journalctl(tmp_path, "sleep 5\n")
    monkeypatch.setattr(wrapper, "JOURNALCTL_BIN", str(stub))
    monkeypatch.setattr(wrapper, "KERNEL_LOG_TIMEOUT_SECONDS", 0.3)

    assert wrapper.cmd_read_kernel_log(_args(boot=True), None) == 3
    assert "STP_READ_KERNEL_LOG_TIMEOUT" in capsys.readouterr().err


def test_subcommand_is_registered_in_contract_table(wrapper):
    """selftest / capabilities 都按契约表走：新子命令必须在表里（#2011 的接线面）。"""
    assert "read-kernel-log" in wrapper._SUBCOMMAND_CONTRACT
    assert wrapper._validate_parser_contract(wrapper._build_parser()) == []
