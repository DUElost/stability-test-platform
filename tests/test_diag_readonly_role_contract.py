"""#2632 缺口②（静态半边）：诊断只读角色的授权面与 SOP 指向契约。

**这单要防的失效有两层**：

1. 表层：SOP 让临时诊断「用专用只读角色」，而全仓**没有任何东西创建这个角色**——于是人被
   指回 `stp`（应用共享凭据）或 `postgres`（超级用户）手查，归属与误操作半径两条同时落空
   （现场记录：`postgres@stp` 至少 3 条手工查询）；
2. 深层：授权脚本自身最容易**静默失效**的那一条——只授 `SELECT ON ALL TABLES` 时，下一次
   迁移建出来的新表这个角色读不到；人的反应不是补授权，而是退回用 `stp` 查，缺口原地复活
   且无人记账。

**本文件守的是「写进仓库的那份权限面」**：口令不得入仓、`GRANT` 只能是读、不给
`PUBLIC`/应用属主加东西、脚本体必须是纯 SQL（否则无法被程序化验证）、SOP 与脚本互相
指得回来。这些在源码层就成立或就不成立。

**本文件守不住的**：`ALTER DEFAULT PRIVILEGES FOR ROLE stp` 是否真的对**未来**建的表生效
——字符串在场 ≠ 生效（`FOR ROLE` 指错角色就静默无效）。那半边由
`tests/test_diag_readonly_role_pg.py` 在真 PG16 上证明；末尾一条断言钉住那个文件在场，
免得它哪天被移出 CI、而这里还以为双保险仍在。
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SQL = REPO_ROOT / "deploy" / "postgres" / "diag-readonly.sql"
SOP = REPO_ROOT / "docs" / "operations" / "production-diagnostics.md"

DIAG_ROLE = "stp_ro"
APP_OWNER = "stp"          # 生产里执行迁移/建表的属主角色
_READ_PRIVS = {"SELECT", "USAGE", "REFERENCES", "TRIGGER"}


def _body() -> str:
    """去掉注释行与 DO 块内的 SQL（注释里可以「谈到」写权限，判据只看代码行）。"""
    lines = []
    for line in SQL.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def _grant_statements() -> list[str]:
    return re.findall(r"GRANT\s+(.+?)\s+TO\s+([^;]+);", _body(), flags=re.IGNORECASE | re.DOTALL)


# ── 静态：凭据与授权面 ──────────────────────────────────────────────────────


def test_script_carries_no_password() -> None:
    """红线：凭据不得进入代码/文档/PR diff——口令只能由运维在库侧用 psql 的 password 元命令设置。"""
    body = _body()
    assert re.search(r"PASSWORD\s+NULL", body, re.IGNORECASE), "角色必须建成无口令态"
    assert not re.search(r"PASSWORD\s+'", body, re.IGNORECASE), "脚本里出现了口令字面量"


def test_grants_are_read_only_and_target_only_the_diag_role() -> None:
    problems: list[str] = []
    grants = re.findall(
        r"GRANT\s+(?P<privs>[A-Za-z_,\s]+?)\s+ON\s+(?P<obj>.+?)\s+TO\s+(?P<to>[A-Za-z_]+);",
        _body(),
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert grants, "解析不出任何 GRANT——语句形态变了，本判据会静默放过一切授权"
    for privs, obj, target in grants:
        if target != DIAG_ROLE:
            problems.append(f"GRANT 的目标不是诊断角色：{target!r}（{obj.strip()[:50]}）")
        words = {w.strip().upper() for w in privs.split(",") if w.strip()}
        for word in words - _READ_PRIVS:
            problems.append(f"授予了非读权限 {word!r}（{obj.strip()[:50]}）")
    assert not problems, "授权面越界：\n" + "\n".join(problems)


def test_no_write_role_widening_and_no_superuser() -> None:
    """本脚本只收紧不放宽：不给应用角色加东西、不碰 PUBLIC、不要超级用户属性。"""
    body = _body()
    assert not re.search(r"GRANT\s+\S[^\n]*TO\s+(PUBLIC|stp)\b", body, re.IGNORECASE), (
        "给 PUBLIC 或应用属主加了权限——诊断脚本不该改变别人的权限面"
    )
    assert "SUPERUSER" not in body.upper().replace("NOSUPERUSER", ""), (
        "出现 SUPERUSER 授权形态（只允许显式 NOSUPERUSER）"
    )
    assert "WITH ADMIN OPTION" not in body.upper() and "GRANT OPTION" not in body.upper()


def test_read_gate_and_tracing_settings_are_present() -> None:
    """`default_transaction_read_only`（闸）与 `log_statement=all`（留痕）是本单的两条正题。"""
    body = _body()
    assert re.search(rf"ALTER ROLE {DIAG_ROLE} SET default_transaction_read_only\s*=\s*on", body, re.I)
    assert re.search(rf"ALTER ROLE {DIAG_ROLE} SET log_statement\s*=\s*'all'", body, re.I)


def test_sop_points_at_this_script_and_names_the_role_where_it_matters() -> None:
    """文档与脚本必须互相指得回来——「SOP 要求一个不存在的东西」就是本单的起因。

    判据落在**那条红线本身上**，而不是「文档里某处出现过角色名」：后者在角色被改名时
    仍能靠其它段落侥幸通过（实测如此），等于没守。
    """
    sop = SOP.read_text(encoding="utf-8")
    assert "deploy/postgres/diag-readonly.sql" in sop, "SOP 未指向创建脚本"
    bullet = [ln for ln in sop.splitlines() if "手工查询不得用" in ln]
    assert bullet, "SOP 里那条红线被删或改名——本判据失去落点，请同步"
    at = sop.index(bullet[0])
    block = sop[at : at + 900]          # 红线及其后续 900 字符（创建命令与处置都在里面）
    assert DIAG_ROLE in block, f"红线所在的段落里没有角色名 {DIAG_ROLE}（读者无从下手）"
    assert "ON_ERROR_STOP=1" in block and "diag-readonly.sql" in block, (
        "红线段落里没给出可执行的创建命令——又会退回「用 stp 吧」"
    )
    assert "不要退回" in block or "即停" in block, (
        "缺角色时的处置没写：没有这句，SOP 在角色缺失时等于默许用共享凭据"
    )
    assert len(re.findall(rf"\b{DIAG_ROLE}\b", sop)) >= 3, (
        "SOP 里角色名出现次数过少——表格与红线两处必须同名，改一处即红"
    )


def test_body_is_plain_sql_so_it_can_be_executed_programmatically() -> None:
    """文件体不得含 psql 元命令（`\\set` 等）——否则只能人肉跑，行为判据无从建立。

    `ON_ERROR_STOP` 属调用约定，写在文件头的执行示例里（`psql --set ON_ERROR_STOP=1`）。
    """
    assert not re.search(r"^\s*\\", _body(), flags=re.MULTILINE), (
        "脚本体里出现 psql 元命令；fail-fast 请放调用侧"
    )


# ── 行为：真实 PG（testcontainers）─────────────────────────────────────────

def test_the_behavioral_half_still_exists() -> None:
    """静态半边不能假装证明「默认 ACL 对未来对象生效」——那要靠真库。

    这里钉住行为文件在场：它一旦被删或被改名，`ALTER DEFAULT PRIVILEGES` 的语义就
    退回「只有字符串在场」这种静默形态（#2639 数过的那类：判据看着在跑、其实不覆盖）。
    """
    assert (REPO_ROOT / "tests" / "test_diag_readonly_role_pg.py").is_file(), (
        "行为判据文件消失——默认 ACL 对未来表是否生效将重新变成无人证明"
    )
