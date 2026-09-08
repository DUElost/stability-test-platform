#!/usr/bin/env python3
"""差异面不变量检查（#855 收口——强制力覆盖图 §7.1 的差集收缩工具）。

对照 docs/design/2026-08-governance-surface-protection.md §7.1：AGENTS.md
硬不变量中 context-only（依赖模型自觉）且**可静态判定**的子集，在 PR
新增行上检查。S11 测「不变量文本在场」（源侧），本工具测「产物未违反」
（结果侧）——同一不变量的两端；不重建行为验证层（§7.1 裁决）。

- 只看新增行（diff `+` 行），存量违规不误伤；
- **BLOCK（2026-09-07 升格）**：违规 exit 1。原 advisory 观察期被全库枚举
  替代——差异面 gate 在 main 上 diff 恒空，观察期收不到样本；全库枚举实证
  3/4 规则零命中、`.dict(` 唯一命中为 `patch.dict(`/`monkeypatch.dict(`
  标准惯用法（已加负向后顾豁免），精度可静态验证即无需等待；
- `--advisory` 保留为放行模式（调试/留痕用）；
- 规则策展最小集，宁缺勿误报；扩展时机 = 新不变量入覆盖图时同步加模式
  （棘轮：违规事故 → 加模式 / 记 residual，见 repository-workflow.md）。

规则：
  pydantic-v1-api    backend/**/*.py      .dict(（patch.dict/monkeypatch.dict 豁免）/
                                          .parse_obj( / .from_orm( / class Config:
  plural-table-name  backend/migrations/**create_table/CREATE TABLE 引用复数表名
                     （业务表名单数是硬不变量——任何以 s 结尾的新表名都值得看一眼）
  bare-pytest        backend/agent/scripts/**/*.sh   裸 pytest 调用（python -m pytest 放行）

注释行豁免（#1047）：行首 `#` / `//` / `--` 的新增行不参与匹配——注释里引述
禁用 API 不构成产物违规；docstring 不做逐行状态机（diff 行缺文件上下文），
docstring 内引述仍会命中，遇误报走 --advisory 留痕。

用法:
    python tools/dev/check_invariant_diff.py                # 对 origin/main，违规 exit 1
    python tools/dev/check_invariant_diff.py --base <ref>
    python tools/dev/check_invariant_diff.py --advisory     # 只留痕不阻塞
    python tools/dev/check_invariant_diff.py --self-test    # 纯函数红绿自证（离线）
"""
from __future__ import annotations

import re
import subprocess
import sys

# (规则名, 文件路径过滤器, 模式列表)。路径用 fnmatch 风格前缀匹配。
RULES: list[tuple[str, str, list[re.Pattern[str]]]] = [
    (
        "pydantic-v1-api",
        r"backend/.*\.py$",
        [
            # patch.dict / monkeypatch.dict 是 unittest.mock / pytest 标准惯用法
            # （全库枚举唯一 `.dict(` 命中面），负向后顾豁免
            re.compile(r"(?<!patch)\.dict\("),
            re.compile(r"\.parse_obj\("),
            re.compile(r"\.from_orm\("),
            re.compile(r"^\s*class Config\b"),
        ],
    ),
    (
        "plural-table-name",
        r"backend/migrations/.*\.py$",
        [
            # alembic op 风格：create_table("...s"）——业务表名单数，s 结尾即嫌疑
            re.compile(r"create_table\(\s*['\"][A-Za-z0-9_]+s['\"]", re.IGNORECASE),
            # 裸 SQL 风格：CREATE TABLE hosts（标识符可不带引号）
            re.compile(
                r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?[A-Za-z0-9_]+s\b[\"'`]?",
                re.IGNORECASE,
            ),
        ],
    ),
    (
        "bare-pytest",
        r"backend/agent/scripts/.*\.sh$",
        [
            re.compile(r"(?:^|[\s;&|(])(?<!-m )pytest\b"),
        ],
    ),
]


def added_lines(diff_text: str) -> list[tuple[str, int, str]]:
    """unified diff → [(path, new_lineno, added_line)]。纯函数。"""
    out: list[tuple[str, int, str]] = []
    path: str | None = None
    new_ln = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].split("\t")[0]
            if path.startswith("b/"):
                path = path[2:]
            continue
        if raw.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", raw)
            new_ln = int(m.group(1)) if m else new_ln
            continue
        if path is None:
            continue
        if raw.startswith("+"):
            out.append((path, new_ln, raw[1:]))
            new_ln += 1
        elif raw.startswith("-") or raw.startswith("\\", 0):
            continue
        else:
            new_ln += 1
    return out


def check(added: list[tuple[str, int, str]]) -> list[str]:
    """新增行 → 违规清单（纯函数，供 --self-test 红绿双向）。

    注释行豁免（#1047）：行首 `#` / `//` / `--`（缩进后）不参与匹配——
    本仓库 doc-heavy，注释里引述禁用 API（「旧代码用 .dict(,已迁」）
    不构成产物违规。docstring 不做逐行状态机（diff 行缺文件上下文），
    docstring 内引述仍会命中，遇误报走 --advisory 留痕。
    """
    violations: list[str] = []
    for path, lineno, line in added:
        if line.lstrip().startswith(("#", "//", "--")):
            continue
        for rule, path_re, patterns in RULES:
            if not re.match(path_re, path):
                continue
            for pat in patterns:
                if pat.search(line):
                    violations.append(
                        f"{path}:{lineno} {rule}: {line.strip()[:120]!r}"
                    )
                    break  # 同一行同一规则只报一次
    return violations


def run_self_test() -> int:
    failures: list[str] = []

    def expect(name: str, diff: str, should_flag: bool, needle: str = "") -> None:
        got = check(added_lines(diff))
        hit = any(needle in v for v in got)
        if hit != should_flag:
            failures.append(f"{name}: 预期{'红' if should_flag else '绿'}，实际{got}")

    good = """--- a/backend/api/x.py
+++ b/backend/api/x.py
@@ -1,2 +1,3 @@
 context line
+model.model_dump()
 removed line stays out
"""
    bad_dict = good.replace("model.model_dump()", "model.dict()")
    expect("pydantic .dict( 红向", bad_dict, True, "pydantic-v1-api")
    expect("pydantic model_dump( 绿向", good, False)
    # 负向后顾豁免（升格枚举实证的唯一 `.dict(` 合法命中面）
    patch_good = good.replace("model.model_dump()", 'patch.dict("os.environ", {})')
    expect("patch.dict 绿向", patch_good, False)
    monkey_good = good.replace("model.model_dump()", "monkeypatch.dict(os.environ, {})")
    expect("monkeypatch.dict 绿向", monkey_good, False)

    bad_removed = """--- a/backend/api/x.py
+++ b/backend/api/x.py
@@ -1,2 +1,2 @@
-model.dict()
+model.model_dump()
"""
    expect("存量行(删除侧)不报", bad_removed, False)

    bad_cfg = """--- a/backend/models/y.py
+++ b/backend/models/y.py
@@ -0,1 +0,2 @@
+    class Config:
+        from_attributes = True
"""
    expect("class Config 红向", bad_cfg, True, "pydantic-v1-api")
    good_cfg = bad_cfg.replace("class Config:", "model_config = ConfigDict(")
    expect("ConfigDict 绿向", good_cfg, False)

    bad_tbl = """--- a/backend/migrations/versions/abc.py
+++ b/backend/migrations/versions/abc.py
@@ -0,0 +1,1 @@
+    op.create_table("hosts", sa.Column("id", sa.Integer))
"""
    expect("复数表名红向", bad_tbl, True, "plural-table-name")
    good_tbl = bad_tbl.replace('"hosts"', '"host"')
    expect("单数表名绿向", good_tbl, False)
    bad_tbl_sql = bad_tbl.replace('op.create_table("hosts", sa.Column("id", sa.Integer))',
                                  'op.execute("CREATE TABLE hosts (id int)")')
    expect("裸 SQL 复数表名红向", bad_tbl_sql, True, "plural-table-name")

    bad_py = """--- a/backend/agent/scripts/tool/v1/run.sh
+++ b/backend/agent/scripts/tool/v1/run.sh
@@ -0,0 +1,1 @@
+pytest backend/agent/tests -q
"""
    expect("裸 pytest 红向", bad_py, True, "bare-pytest")
    good_py = bad_py.replace("+pytest", "+python -m pytest")
    expect("python -m pytest 绿向", good_py, False)

    other_path = """--- a/frontend/src/x.ts
+++ b/frontend/src/x.ts
@@ -0,0 +1,1 @@
+const d = obj.dict();
"""
    expect("backend 之外不扫", other_path, False)

    # 注释行豁免（#1047）：注释里引述禁用 API 不拦，代码行仍拦
    bad_comment = """--- a/backend/api/x.py
+++ b/backend/api/x.py
@@ -0,0 +1,2 @@
+# 旧代码用 .dict(，迁移后统一 model_dump
+x = data.dict()
"""
    expect("注释引述不拦(代码行仍拦)", bad_comment, True, "x = data.dict()")
    expect("注释行自身不报", bad_comment.replace("+x = data.dict()", ""), False, "旧代码用")

    bad_sh_comment = """--- a/backend/agent/scripts/tool/v1/run.sh
+++ b/backend/agent/scripts/tool/v1/run.sh
@@ -0,0 +1,2 @@
+# 如失败可用 pytest -k xxx 单测排查
+python -m pytest backend/agent/tests -q
"""
    expect("sh 注释提及 pytest 不拦", bad_sh_comment, False)
    bad_sql_comment = """--- a/backend/migrations/versions/abc.py
+++ b/backend/migrations/versions/abc.py
@@ -0,0 +1,2 @@
+-- 存量库另有 hosts 备份表，不在本迁移范围
+op.create_table("host", sa.Column("id", sa.Integer))
"""
    expect("SQL 注释提及复数表名不拦", bad_sql_comment, False)

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        return 1
    print("[OK] invariant-diff self-test 通过（规则红绿双向 + 路径/删除侧边界）")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return run_self_test()
    base = argv[argv.index("--base") + 1] if "--base" in argv else "origin/main"
    advisory = "--advisory" in argv
    proc = subprocess.run(
        ["git", "diff", "-U0", f"{base}...HEAD"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        # base 不可达（浅克隆/无远端）：静默放行，不阻塞工作流
        print(f"[advisory] invariant-diff: git diff {base}...HEAD 不可达，跳过")
        return 0
    violations = check(added_lines(proc.stdout))
    if not violations:
        print("[OK] invariant-diff: 新增行无不变量违规")
        return 0
    tag = "advisory" if advisory else "BLOCK"
    for v in violations:
        print(f"[{tag}] invariant-diff: {v}")
    if advisory:
        print(f"invariant-diff（advisory）：{len(violations)} 项——不阻塞")
        return 0
    print(
        f"invariant-diff：{len(violations)} 项违规——Pydantic v2 用 model_dump/"
        f"model_validate；新表名单数；测试调用用 python -m pytest"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
