"""#3573 判据 3：失败用例名提取必须覆盖 vitest 的双空格形态。

缺陷：`_SUMMARY_TOKEN_RE` 写死「`Z` + **恰好一个**空格 + 摘要词 + **恰好一个**空格」，
而两个框架的实际输出不同（行首均为 GHA 时间戳 `…Z`，`cat -A` 实测）：

    pytest short summary   `…Z FAILED backend/tests/x.py::TestC::test_y`   ← Z 后单空格
    vitest 失败清单        `…Z  FAIL  src/x.test.ts > suite > name`      ← Z 后**双**空格

于是 vitest 侧**恒空**——归因块写「未取到失败用例名」，而日志里其实有。这不是装饰性缺失：
#2441 那一夜的前端红灯就因此在归因块里自述「不得作为前移评估的样本」，该类红灯再无
可用样本。

本组用**真实日志行**做 fixture（逐字节取自 GitHub Actions job log），而不是手写的
近似形态——手写容易把「以为的形态」钉成期望值，那就复现了同一个错误。
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "ci" / "backstop-attribution.sh"

# 逐字节取自真实 job log（run 36971471198 的 frontend-check，2026-10-02）。
_VITEST_LINE = (
    "2026-10-02T06:42:39.7508245Z  FAIL  src/utils/tmpRedTimingProbe.test.ts"
    " > tmp red timing probe > intentional red for #3573 timing verification"
)
# 逐字节取自真实 job log（run 36269603230 的 backend-test，2026-09-26）。
_PYTEST_LINE = (
    "2026-09-26T21:54:58.5680892Z FAILED backend/tests/test_phase0_closure.py"
    "::TestDeferredPostCompletion::test_defer_cutoff_stops_reenqueue_for_ancient_orphan"
)


def _regex() -> re.Pattern[str]:
    m = re.search(r"_SUMMARY_TOKEN_RE='([^']+)'", _SCRIPT.read_text(encoding="utf-8"))
    assert m, "脚本里找不到 _SUMMARY_TOKEN_RE"
    return re.compile(m.group(1))


def test_vitest_double_space_form_is_matched():
    """vitest 形态必须能提取出文件名。"""
    m = _regex().search(_VITEST_LINE)
    assert m, f"vitest 双空格形态未命中，正则={_regex().pattern}"
    assert "tmpRedTimingProbe.test.ts" in m.group(0)


def test_pytest_single_space_form_still_matched():
    """pytest 形态不能因放宽而失效（放宽是为了兼容，不是替换）。"""
    m = _regex().search(_PYTEST_LINE)
    assert m, f"pytest 单空格形态未命中，正则={_regex().pattern}"
    assert "test_defer_cutoff_stops_reenqueue_for_ancient_orphan" in m.group(0)


def test_body_prose_mention_does_not_match():
    """正文里出现的同名词仍不应被命中——锚点的作用就是排除它们。"""
    noise = "2026-10-02T06:42:39.7508245Z some FAIL happened in prose about FAIL  src/x.ts"
    assert not _regex().search(noise), "锚点过宽，开始命中正文噪声"


def test_extraction_takes_only_first_segment_of_vitest_path():
    """vitest 用 ` > ` 分隔层级，不是标识符的一部分，只取首段文件名。"""
    m = _regex().search(_VITEST_LINE)
    assert m
    got = m.group(0)
    assert ">" not in got, f"提取结果混入了层级分隔符：{got!r}"
    assert got.endswith("tmpRedTimingProbe.test.ts"), got


def test_both_real_lines_yield_nonempty_in_batch_flow():
    """模拟脚本里的批处理：两份日志各跑一遍 strip_ansi + grep，结果都非空。"""
    import subprocess

    pat = _regex().pattern
    for label, line in (("vitest", _VITEST_LINE), ("pytest", _PYTEST_LINE)):
        out = subprocess.run(
            ["grep", "-aoE", pat], input=line, capture_output=True, text=True
        ).stdout
        assert out.strip(), f"{label} 形态经 grep 管道后为空"
