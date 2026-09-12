#!/usr/bin/env python3
"""分层门禁（#1519）：`backend/services/` 不得 import `backend.api.routes`。

背景：services 层曾以函数内局部 import 反向引用 api.routes 的**私有**符号
（局部 import 正是为规避循环导入的痕迹），路由层任何改名/拆分都会静默破坏
服务层且无机械拦截。#1519 把符号下沉到 services 后，本门禁防扩散。

规则：仅匹配 **import 语句行**（行首 `from backend.api.routes` /
`import backend.api.routes`）；注释与字符串中的提及不算。`backend.api.routes_*`
这类不同模块名不误报（词边界）。

退出码：有违规 → 1 并列出 `文件:行:语句`；否则 0。`--self-test` 离线红绿自证。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = ROOT / "backend" / "services"

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+backend\.api\.routes\b|import\s+backend\.api\.routes\b)"
)


def scan(services_dir: Path = SERVICES_DIR) -> list[str]:
    findings: list[str] = []
    for path in sorted(services_dir.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _IMPORT_RE.match(line):
                try:
                    shown = path.relative_to(ROOT)
                except ValueError:
                    shown = path
                findings.append(f"{shown}:{lineno}: {line.strip()}")
    return findings


def _self_test() -> int:
    positives = [
        "from backend.api.routes.plan_runs import load_job",
        "    from backend.api.routes import plans",
        "import backend.api.routes.plans",
        "\tfrom backend.api.routes.plans import x",
    ]
    negatives = [
        "from backend.services.plan_run_queries import load_job_in_run",
        "# from backend.api.routes.plans import legacy  (注释提及)",
        'x = "backend.api.routes.plans"',
        "from backend.api.routes_dummy import nope",  # 不同模块名
        "",
    ]
    bad = [s for s in positives if not _IMPORT_RE.match(s)]
    bad += [s for s in negatives if _IMPORT_RE.match(s)]
    if bad:
        print("[FAIL] self-test:", bad, file=sys.stderr)
        return 1
    print("[OK] check_layering self-test 红绿双向")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()

    findings = scan()
    if findings:
        print("[FAIL] services 反向 import api.routes（#1519 分层门禁）：", file=sys.stderr)
        for item in findings:
            print(f"        {item}", file=sys.stderr)
        print(
            "        修法：把被引用的符号下沉到 backend/services/，路由改为调用服务层。",
            file=sys.stderr,
        )
        return 1
    print("[OK] 分层门禁：services/ 无 api.routes 反向依赖")
    return 0


if __name__ == "__main__":
    sys.exit(main())
