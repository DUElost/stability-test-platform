#!/usr/bin/env python3
"""ADR-0033 D2：STP Tool Contract 合规自测（新工具族准入脚手架）。

Epic #745 要求的 Contract Compliance Gate。适用范围对齐 ADR-0033 v1.2：
**新工具族**准入要求，不是存量族立刻改造。

默认靶子是仓库内 fixture ``tools/dev/fixtures/tool_contract_sample/run_tool.py``，
证明验证器本身红绿可测；真正的新族接入时对其 entrypoint 再跑本脚本。

校验：
1. ``--check-env`` 在时限内退出 0，stdout JSON 含 ready 语义；
2. ``--context`` + ``--output-dir`` 退出 0，产出 ``summary.json`` 与 ``artifacts/``；
3. 工具不得伪造平台保留退出码 ≥124。

用法::

    python tools/dev/verify_tool_contract.py
    python tools/dev/verify_tool_contract.py --entrypoint path/to/tool.py
    python tools/dev/verify_tool_contract.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENTRY = (
    ROOT / "tools" / "dev" / "fixtures" / "tool_contract_sample" / "run_tool.py"
)
CHECK_ENV_TIMEOUT_S = 5.0
RUN_TIMEOUT_S = 30.0


def _run(
    entry: Path,
    argv: list[str],
    *,
    timeout: float,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(entry), *argv]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
        check=False,
    )


def verify_entrypoint(entry: Path) -> list[str]:
    """对单个 entrypoint 跑契约检查；返回失败原因列表（空 = 通过）。"""
    failures: list[str] = []
    if not entry.is_file():
        return [f"entrypoint 不存在: {entry}"]

    try:
        check = _run(entry, ["--check-env"], timeout=CHECK_ENV_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return [f"--check-env 超过 {CHECK_ENV_TIMEOUT_S}s"]

    if check.returncode != 0:
        failures.append(f"--check-env 退出码={check.returncode}（期望 0）")
    else:
        try:
            payload = json.loads(check.stdout.strip() or "{}")
        except json.JSONDecodeError:
            failures.append("--check-env stdout 不是 JSON")
            payload = {}
        if payload.get("ready") is not True and payload.get("status") != "ready":
            # 接受 ready:true 或 status:"ready"
            failures.append(
                "--check-env JSON 缺少 ready:true / status:ready："
                f"{payload!r}"
            )

    with tempfile.TemporaryDirectory(prefix="stp-tool-contract-") as tmp:
        tmp_path = Path(tmp)
        context = tmp_path / "context.json"
        output_dir = tmp_path / "out"
        context.write_text(json.dumps({"sample": True}), encoding="utf-8")
        try:
            run = _run(
                entry,
                ["--context", str(context), "--output-dir", str(output_dir)],
                timeout=RUN_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            failures.append(f"run 超过 {RUN_TIMEOUT_S}s")
            return failures

        if run.returncode >= 124:
            failures.append(
                f"工具伪造/返回平台保留退出码 {run.returncode}（≥124 禁止）"
            )
        elif run.returncode not in (0, 1, 2):
            failures.append(
                f"run 退出码={run.returncode}，不在工具命名空间 {{0,1,2}}"
            )
        elif run.returncode != 0:
            failures.append(
                f"样板 run 期望成功退出 0，实际 {run.returncode}；"
                f"stderr={run.stderr.strip()!r}"
            )
        else:
            summary = output_dir / "summary.json"
            artifacts = output_dir / "artifacts"
            if not summary.is_file():
                failures.append("缺少 output_dir/summary.json")
            else:
                try:
                    json.loads(summary.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    failures.append("summary.json 不是合法 JSON")
            if not artifacts.is_dir():
                failures.append("缺少 output_dir/artifacts/ 目录")

    return failures


def run_self_test() -> int:
    """离线：对 fixture 绿向；对故意缺契约的假入口红向。"""
    failures: list[str] = []

    green = verify_entrypoint(DEFAULT_ENTRY)
    if green:
        failures.append(f"fixture 应绿，实际 {green}")

    with tempfile.TemporaryDirectory(prefix="stp-bad-tool-") as tmp:
        bad = Path(tmp) / "bad_tool.py"
        bad.write_text(
            "import sys\n"
            "if __name__ == '__main__':\n"
            "    sys.exit(0)\n",
            encoding="utf-8",
        )
        red = verify_entrypoint(bad)
        if not red:
            failures.append("无契约假入口应红，实际通过")

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        return 1
    print("[OK] verify_tool_contract self-test 红绿双向（fixture 绿 / 裸脚本红）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--entrypoint",
        type=Path,
        default=DEFAULT_ENTRY,
        help="待验工具入口（默认 contract sample fixture）",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    entry = args.entrypoint
    if not entry.is_absolute():
        entry = (Path.cwd() / entry).resolve()

    problems = verify_entrypoint(entry)
    if problems:
        print(f"[FAIL] Tool Contract 不合规：{entry}", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"OK: Tool Contract 通过 ({entry})")
    return 0


if __name__ == "__main__":
    # 允许在受限环境用 STP_VERIFY_TOOL_CONTRACT=0 跳过（默认不跳）
    if os.environ.get("STP_VERIFY_TOOL_CONTRACT", "1").strip() in {"0", "false", "no"}:
        print("SKIP: STP_VERIFY_TOOL_CONTRACT=0")
        raise SystemExit(0)
    raise SystemExit(main())
