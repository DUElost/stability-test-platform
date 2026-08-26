#!/usr/bin/env python3
"""治理面行为 evals（synthesis C-G1 的 L1 层）——按需诊断工具，非门禁。

设计裁决（docs/design/2026-08-governance-surface-protection.md §L1）：
- 博客「evals 作合并门禁」的前提（多写者/高频变更/auto mode）本项目不满足，
  故本工具**不进 CI、不进 check:quick/pr**，只挂 run_gates.py 的 check:gov
  专项 profile 手动运行。触发时机：治理面结构性重写（瘦身/skills 化）、
  agent 行为与文档相悖需定位「没写到 vs 写了没传导」、新增重大契约后验证可达性。
- 答题人 = `claude -p` 无工具会话在仓库根运行——测真实摄取路径
  （CLAUDE.md 自动加载 + @import 解析），非文本包含性检查。
- 判卷人 = cases.yaml 里出题即定死的确定性正则（expect_any/forbid），
  永不使用 LLM judge；答题方无法影响评分。

用法:
    python tools/dev/run_gov_evals.py                 # 全量 case
    python tools/dev/run_gov_evals.py --only <id>     # 单条冒烟
    python tools/dev/run_gov_evals.py --self-test     # 判定逻辑自证
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CASES_PATH = os.path.join(ROOT, "tools", "dev", "gov_evals_cases.yaml")
TIMEOUT_SECONDS = 300  # 并发下每次都要整载治理面上下文，重载机器上单问可达数分钟
RETRIES = 1  # 断言失败/调用异常重试一次再判红（吸收偶发抖动/CLI 更新扰动）


def load_cases(path: str = CASES_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        cases = yaml.safe_load(fh)["cases"]
    seen: set[str] = set()
    for c in cases:
        for key in ("id", "prompt", "expect_any"):
            assert key in c, f"case 缺字段 {key}: {c.get('id')}"
        assert isinstance(c["expect_any"], list), f"{c['id']}: expect_any 必须是列表"
        assert not isinstance(c.get("forbid"), str), f"{c['id']}: forbid 必须是列表"
        assert c["id"] not in seen, f"case id 重复: {c['id']}"
        seen.add(c["id"])
        for pat in c["expect_any"] + list(c.get("forbid") or []):
            try:
                re.compile(pat)
            except re.error as exc:
                raise AssertionError(f"{c['id']}: 正则不可编译 {pat!r}: {exc}") from exc
    return cases


def grade(case: dict, response: str) -> tuple[bool, list[str]]:
    """返回 (是否通过, 失败原因)。评分只看正则，与请求方式无关。"""
    reasons: list[str] = []
    if not any(re.search(p, response) for p in case["expect_any"]):
        reasons.append(f"expect_any 均未命中: {case['expect_any']}")
    for pat in case.get("forbid") or []:
        if re.search(pat, response):
            reasons.append(f"命中禁止项: {pat!r}")
    return (not reasons, reasons)


def ask_claude(prompt: str) -> str:
    proc = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p exit={proc.returncode}: {proc.stderr[:300]}")
    return proc.stdout


def run_case(case: dict) -> dict:
    result: dict = {"case": case["id"], "pass": False, "graded": False, "reasons": [],
                    "retries": 0, "attempts": [], "response_tail": ""}
    for attempt in range(RETRIES + 1):
        try:
            response = ask_claude(case["prompt"])
        except Exception as exc:  # noqa: BLE001 —— 工具脚本宽捕获后入报告
            result["attempts"].append(f"[{attempt}] 调用失败: {type(exc).__name__}: {exc}")
            continue
        result["response_tail"] = response.strip()[-500:]
        ok, reasons = grade(case, response)
        result["attempts"].append(
            f"[{attempt}] {'PASS' if ok else 'FAIL: ' + '; '.join(reasons)}"
        )
        if ok or attempt == RETRIES:
            result.update({"pass": ok, "graded": True, "reasons": reasons,
                           "retries": attempt})
            break
    if not result["graded"]:
        # 全部 attempt 都抛异常（典型：并发下超时）——判 FAIL 但原因必须可见
        result.update({"pass": False, "reasons": ["所有尝试均调用失败，见上方明细"],
                       "retries": RETRIES})
    return result


def self_test() -> int:
    """判卷逻辑双向校验（verify-before-asserting），不打 LLM。"""
    fail: list[str] = []

    def expect(label: str, got: bool, want: bool) -> None:
        if got != want:
            fail.append(f"{label}: 预期{'过' if want else '挂'}，实际{'过' if got else '挂'}")

    case = {
        "id": "t",
        "prompt": "",
        "expect_any": [r"python -m pytest"],
        "forbid": [r"裸 pytest 即可"],
    }
    expect("关键词命中", grade(case, "应使用 python -m pytest")[0], True)
    expect("缺关键词", grade(case, "用 pytest 就好")[0], False)
    ok, reasons = grade(case, "python -m pytest；裸 pytest 即可")
    expect("命中禁止项即不通过", (not ok) and any("禁止项" in r for r in reasons), True)

    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tmp:
        tmp.write(
            "cases:\n"
            "  - id: a\n"
            "    prompt: p\n"
            "    expect_any: ['x']\n"
            "    forbid: 'oops'\n"  # 字符串型 forbid 必须被拒
        )
        bad_path = tmp.name
    try:
        try:
            load_cases(bad_path)
            fail.append("forbid 字符串未触发 AssertionError")
        except AssertionError:
            pass
    finally:
        os.unlink(bad_path)

    try:
        load_cases()
    except Exception as exc:  # noqa: BLE001 —— 自测收集器
        fail.append(f"真实 cases.yaml 结构校验异常: {exc}")

    for f in fail:
        print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
    if fail:
        print("\n判定逻辑不可信，禁止运行正式 eval", file=sys.stderr)
        return 1
    n = len(load_cases())
    print(f"[OK] 判定逻辑自测通过；{n} 条 case 的正则全部可编译、无重复 id")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="治理面行为 evals（按需诊断，非门禁）")
    ap.add_argument("--only", help="只跑指定 case id")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    ver = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    print("# 治理面行为 evals 报告\n# engine=claude  cli=" + ver + "\n")

    cases = load_cases()
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
        if not cases:
            print(f"--only 未匹配到 case: {args.only}", file=sys.stderr)
            return 2

    failed = 0
    width = max(len(c["id"]) for c in cases)
    # 每问要整载一次治理面上下文（实测 ~1min/条，重载机上更久）；workers=2 是
    # 实测折中——3 并发曾让部分调用连续撞超时。本工具按需手跑，分钟级总量可接受。
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(2, len(cases))) as pool:
        results = list(pool.map(run_case, cases))
    for r in results:
        if not r["pass"]:
            failed += 1
        mark = "PASS" if r["pass"] else "FAIL"
        retry_note = f"（第 {r['retries'] + 1} 次）" if r["retries"] else ""
        extra = "" if r["pass"] else " — " + "; ".join(r["reasons"])
        print(f"[{mark}] {r['case']:<{width}}{retry_note}{extra}")
        if not r["pass"]:
            for a in r.get("attempts", []):
                print(f"        {a}")
            if r.get("response_tail"):
                tail = r["response_tail"].replace("\n", "\n        ")
                print(f"        ── 应答尾段 ──\n        {tail}")

    verdict = "[OK]" if failed == 0 else "[FAIL]"
    print(f"\n{verdict} {len(results) - failed}/{len(results)} 通过")
    if failed:
        print(
            "\n排查顺序：① claude CLI 是否刚升级（版本见报告头）② 治理面文档是否真被改坏 "
            "③ case 正则是否过严。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
