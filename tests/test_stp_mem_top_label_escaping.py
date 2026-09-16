"""`stp-mem-top.sh` 的 exposition 不变量（#2016 第 2 条）。

故障形态：`comm` / `unit` / `cmdline` 全部取自外部——进程可用 `prctl(PR_SET_NAME)`
把名字设成任意 15 字节（含换行也合法），异常 cgroup 名同理。旧实现只转义反斜杠与
双引号，**不处理换行**，于是标签值里的裸 LF 会把一行 Prometheus 样本截断成两行：
textfile collector 判整个 `stp_hostproc.prom` 解析失败，**全部** `stp_hostproc_*`
序列消失（不是少一条，是全丢）。同一批值还写进 TSV 日志，`read -r key anon` 会在
首个 TAB 处错切列。

分工：
- 行为用例真跑 `stp_sanitize`（从脚本里按名提取，不复制第二份实现）；
- 接线用例钉住「四个外部来源值都过 sanitize」——只加工具不接线等于没修；
- 端到端用例在临时目录真跑一次脚本，断言 exposition 每行都合法（既覆盖本机真实
  进程，也能抓住把脚本改坏的编辑）。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "deploy" / "control-plane" / "node-exporter" / "stp-mem-top.sh"

# 带标签的一行样本：name{k="v",...} value [timestamp]。标签值只允许 `\\`、`\"`、
# `\n` 三类转义，不得出现裸 CR / LF / TAB。
LABELLED_SAMPLE = re.compile(
    r'^stp_hostproc_anon_bytes\{comm="(?:[^"\\\n\r\t]|\\\\.)*",'
    r'unit="(?:[^"\\\n\r\t]|\\\\.)*"\} \d+(?: [0-9.]+)?\n$'
)
# 无标签的机器总量
TOTAL_SAMPLE = re.compile(r"^stp_hostproc_anon_total_bytes \d+(?: [0-9.]+)?\n$")


def _extract_function(name: str) -> str:
    """从脚本里按名取出函数定义（连同 `local` / `printf -v` 的原始形态）。"""
    text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL
    )
    assert match, (
        f"{name} 已不在 {SCRIPT.name} 里（或改了形状）——本文件的断言会变成恒真"
    )
    return match.group(0)


def _run_bash(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "-c", body], capture_output=True, text=True, timeout=30)


def test_sanitize_strips_every_line_or_column_breaking_char():
    body = (
        _extract_function("stp_sanitize")
        + "\nvalue=$(printf 'a\\nb\\rc\\td')\nstp_sanitize value\nprintf '%s' \"$value\"\n"
    )
    out = _run_bash(body)
    assert out.returncode == 0, out.stderr
    assert out.stdout == "a b c d", f"控制字符未清干净：{out.stdout!r}"


def test_sanitize_leaves_legitimate_label_characters_alone():
    """cgroup 名里的 `\\x2d`（systemd 转义）与空格/引号是**真值**，不得顺手改。"""
    literal = 'app-gnome-yandex\\x2d-browser.scope "q"'
    body = (
        _extract_function("stp_sanitize")
        + "\nvalue=$(printf '%s' \"$0\")\nstp_sanitize value\nprintf '%s' \"$value\"\n"
    )
    out = subprocess.run(
        ["bash", "-c", body, literal], capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout == literal


def test_every_externally_sourced_value_is_sanitized():
    """四个来源值必须在**取值之后立刻**规范化，且都发生在写盘之前。

    只定义不调用 = 没修；漏一个（尤其 `cmdline`）等于留一条注入通路。
    """
    text = SCRIPT.read_text(encoding="utf-8")
    assignments = {
        "comm": '    comm=$(cat "$d/comm"',
        "unit": "    unit=$(awk -F:",
        "cmdline": "    cmdline=$(tr '\\0' ' '",
        "name": "    name=$(awk ",
    }
    for var, marker in assignments.items():
        assert marker in text, f"脚本形状已变：找不到 `{var}` 的取值行，本用例会变成恒真"
        tail = text[text.index(marker):text.index(marker) + 400]
        assert f"stp_sanitize {var}" in tail, (
            f"`{var}` 取自外部但未在取值处规范化（#2016：LF/CR/TAB 会同时污染 "
            f"exposition 与 TSV 两处 sink）"
        )


def test_rendered_exposition_is_parseable(tmp_path):
    """真跑一次脚本：每条样本单独成行，且严格文法可解析。"""
    textfile = tmp_path / "node-exporter"
    textfile.mkdir()
    out = _run_bash(
        f"STP_MEM_TOP_TEXTFILE_DIR={textfile} STP_MEM_TOP_LOG={tmp_path / 'top.tsv'} "
        f"bash {SCRIPT}"
    )
    assert out.returncode == 0, out.stderr
    prom = (textfile / "stp_hostproc.prom").read_text(encoding="utf-8")
    metric = [line for line in prom.splitlines(keepends=True) if not line.startswith("#")]
    assert metric, "没有任何样本，本用例会变成恒真"
    bad = [
        line for line in metric
        if not (LABELLED_SAMPLE.match(line) or TOTAL_SAMPLE.match(line))
    ]
    assert not bad, f"exposition 含非法行（collector 会丢弃整个文件）：{bad[:3]!r}"
    assert "# TYPE stp_hostproc_anon_bytes gauge" in prom
    assert "# TYPE stp_hostproc_anon_total_bytes gauge" in prom
