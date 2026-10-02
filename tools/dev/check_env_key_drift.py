#!/usr/bin/env python3
"""环境变量**键集**漂移检测：真身 vs 入库模板。

解决的是「生产凭据不可再生」带来的可观测性缺口：env 真身
（`stp-releases/env.backend`，600、未跟踪、不在 git 里、**无备份**）一旦丢失，
代码重建了也起不来服务，且没有任何东西能告诉操作者「少了哪些配置」。本脚本把
「缺什么」变成机器可判的事实，且**不需要真值本身可恢复**。

纪律：只读键名，绝不读值
------------------------
对 env 文件只做一件事——取 `^[A-Z_][A-Z0-9_]*=` 的等号**左边**。键名是结构信息，
可以进仓库、进日志、进 PR；值是凭据。刻意**不做**：算值的摘要（对短凭据可暴力
还原）、比对值是否相同、把值写进任何输出。

判定与退出码
------------
- ``0`` 无「无兜底」的缺键
- ``1`` 真身缺少模板已声明、**且代码里没有默认值兜底**的键 —— 唯一的真隐患
- ``2`` 用法错（找不到模板）

为什么不是「缺键就红」
--------------------
本脚本第一次对真身实跑时发现三个键「真身缺、模板有」：``HOST_HEARTBEAT_TIMEOUT_SECONDS``、
``UNKNOWN_GRACE_SECONDS``、``SESSION_WATCHDOG_INTERVAL_SECONDS``。逐个查证后它们
**都有代码默认值兜底**（300/300/15，与模板声明值一致），生产行为完全正确——这属于
「配置未显式化」，不是「配置错误」。若按「缺键就红」，门禁第一次接触现实就误报，
此后必然被当成噪声忽略。

故判红条件收紧为**缺键 ∧ 无默认值**：那才是「不配就真的不对」的情形。默认值表取自
``tools/dev/env_inventory.py`` 的 ``scan_reads()``（同一套 AST 解析，不另造口径）。

「真身多出模板没有的键」**永不判红**：那是常态（生产加了新配置而模板没跟上），正确
处置是在 PR 里补模板，不是门禁拦人。只打印提示。

对缺键会附带**近似键提示**：真身里多出的键若与某个缺键的编辑距离 ≤2，很可能是
打错而非真新增。这不改变检出（缺键本身已判红），只把诊断从「少了 X」提升为
「少了 X，且发现近似键 Y，疑似拼错」。

用法::

    python tools/dev/check_env_key_drift.py                  # 自动定位真身
    ENV_REAL=/path/to/env.backend python tools/dev/check_env_key_drift.py
    python tools/dev/check_env_key_drift.py --check          # 只判红，不打印明细
    python tools/dev/check_env_key_drift.py --self-test      # 离线红绿双向自证
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 默认值口径复用 env_inventory（同一套 AST 解析，不另造）。模块级导入：函数体内 import
# 会被 inner-imports 门禁计数（基线 610，本仓对新增函数体内 import 是卡口的）。
sys.path.insert(0, str(ROOT / "tools" / "dev"))
try:
    import env_inventory as _env_inventory
except Exception:  # noqa: BLE001 —— 取不到时 code_defaults() 返回空表，门禁退回保守判红
    _env_inventory = None  # type: ignore[assignment]
DEFAULT_TEMPLATE = ROOT / "deploy" / "control-plane" / "env" / ".env.backend.example"

# 只匹配行首的 `KEY=`；值部分完全不进入结果。
_KEY_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)=")

# 真身定位顺序：显式 ENV_REAL → 站点真身（ADR-0051 D6）→ 仓根（dev 形态）。
_REAL_CANDIDATES = (
    ROOT.parent / "stp-releases" / "env.backend",
    ROOT / "stp-releases" / "env.backend",
    ROOT / ".env.backend",
)


def keys_of(path: Path) -> set[str]:
    """取 env 文件的键名集合。**只读等号左边。**"""
    found: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _KEY_RE.match(line)
        if m:
            found.add(m.group(1))
    return found


def find_real(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    for cand in _REAL_CANDIDATES:
        if cand.is_file():
            return cand
    return None


def _edit_distance(a: str, b: str) -> int:
    """标准 Levenshtein 距离（两行 DP，键名都很短，足够）。"""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _near_misses(missing: set[str], extra: set[str], max_dist: int = 2) -> dict[str, list[str]]:
    """缺键 → 真身里疑似打错的近似键。

    自己算编辑距离而不用 difflib.get_close_matches：后者的 cutoff 是**相似度比例**
    （0~1），不是编辑距离，语义不同易误用。命中即提示，但**不改变判定**（缺键本身
    已经判红，这里只是把诊断从「少了 X」提升为「少了 X，且发现近似键 Y，疑似拼错」）。
    """
    hints: dict[str, list[str]] = {}
    if not missing or not extra:
        return hints
    pool = sorted(extra)
    for key in sorted(missing):
        close = [c for c in pool if _edit_distance(key, c) <= max_dist]
        if close:
            hints[key] = close
    return hints


def code_defaults() -> dict[str, str]:
    """代码里的默认值表：变量名 → 默认值。

    复用 ``tools/dev/env_inventory.py`` 的 ``scan_reads()``（同一套 AST 解析口径，
    不另造一套）。取不到时返回空 dict——此时**退回「缺键即红」**的保守语义，
    而不是静默放过。
    """
    try:
        reads = _env_inventory.scan_reads()
        return {
            name: (info.get("default") or "")
            for name, info in reads.items()
            if isinstance(info, dict)
        }
    except Exception:  # noqa: BLE001 —— 取不到就保守判红，不静默放过
        return {}


def check(
    template: Path,
    real: Path | None,
    check_only: bool,
    defaults: dict[str, str] | None = None,
) -> int:
    if not template.is_file():
        print(f"[FAIL] 找不到模板：{template}", file=sys.stderr)
        return 2

    tpl_keys = keys_of(template)

    # 真身不存在不判红：CI runner 与开发机都没有生产 env（那是部署机的特权）。
    if real is None:
        print(f"[OK] 未找到 env 真身（当前形态正常），跳过键集比对；模板含 {len(tpl_keys)} 个键")
        return 0

    defaults = code_defaults() if defaults is None else defaults
    real_keys = keys_of(real)
    missing = tpl_keys - real_keys
    extra = real_keys - tpl_keys

    hints = _near_misses(missing, extra)
    # 只有「缺键 ∧ 无默认值兜底」才判红；有默认值的降为提示。
    if defaults:
        blocking = {k for k in missing if not defaults.get(k)}
        benign = missing - blocking
    else:
        # 拿不到默认值表 → 保守：全部缺键都算阻断
        blocking, benign = set(missing), set()

    rc = 0
    if blocking:
        rc = 1
        if not check_only:
            print(
                f"[DRIFT] 真身缺少模板已声明、且**代码无默认值兜底**的键"
                f"（{len(blocking)} 个）——部署漏配或拼写错误，需人工处理："
            )
            for key in sorted(blocking):
                suffix = ""
                if key in hints:
                    suffix = f"   ← 疑似拼错，真身里的近似键：{', '.join(hints[key])}"
                print(f"    {key}{suffix}")
    if benign and not check_only:
        print(
            f"[提示] 真身未显式设置、但代码有默认值兜底的键（{len(benign)} 个）"
            f"——行为正确，属「配置未显式化」而非配置错误，不判红："
        )
        for key in sorted(benign):
            print(f"    {key}  (代码默认 {defaults.get(key)!r})")
    if extra and not check_only:
        print(
            f"[提示] 真身有而模板没有的键（{len(extra)} 个）"
            f"——多半是生产新增配置未入模板，正确处置是在 PR 里补 "
            f"{template.relative_to(ROOT)}，而不是门禁拦人："
        )
        for key in sorted(extra):
            print(f"    {key}")

    if rc == 0:
        print(
            f"[OK] env 键集无阻断性漂移（模板 {len(tpl_keys)} 键 / 真身 {len(real_keys)} 键；"
            f"缺键但有默认值兜底 {len(benign)} 个、真身多键 {len(extra)} 个，均不判红）"
        )
    return rc


def _write_env(path: Path, keys: list[str]) -> None:
    """测试夹具：写一个只有键、值全是固定假串的 env 文件（**不是真凭据**）。"""
    path.write_text(
        "\n".join(f"{k}=placeholder-not-a-real-secret" for k in keys) + "\n",
        encoding="utf-8",
    )


def self_test() -> int:
    """离线红绿双向自证：注入缺键必红、补齐必绿；且全程不涉及真凭据。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        tpl = tmp / "tpl"
        tpl.write_text(
            "JWT_SECRET_KEY=x\nDATABASE_URL=x\nSTP_CSRF_ENABLED=x\n", encoding="utf-8"
        )

        # 默认值表显式注入，保持 self-test **完全离线**（不触真实代码扫描）。
        # 形态照实身实跑的发现设：DATABASE_URL / STP_CSRF_ENABLED 有兜底默认值，
        # JWT_SECRET_KEY 没有（凭据类不给默认）——据此钉住两条语义边界。
        defaults = {"DATABASE_URL": "sqlite://", "STP_CSRF_ENABLED": "1"}

        # 绿：键集完全一致
        real_ok = tmp / "ok.env"
        _write_env(real_ok, ["JWT_SECRET_KEY", "DATABASE_URL", "STP_CSRF_ENABLED"])
        assert check(tpl, real_ok, True, defaults) == 0, "键集一致时应为绿"

        # 红：真身少一个**无默认值兜底**的键（JWT_SECRET_KEY）
        real_missing = tmp / "missing.env"
        _write_env(real_missing, ["DATABASE_URL", "STP_CSRF_ENABLED"])
        assert check(tpl, real_missing, True, defaults) == 1, "缺无兜底键时必须判红"

        # 绿：只缺**有默认值兜底**的键 —— 属「未显式化」而非配置错误，不判红。
        # 这一条是本脚本第一次对真身实跑后收紧的语义（三个缺键全部有兜底、行为正确）。
        real_soft = tmp / "soft.env"
        _write_env(real_soft, ["JWT_SECRET_KEY", "DATABASE_URL"])
        assert check(tpl, real_soft, True, defaults) == 0, "缺有兜底键不该判红"

        # 定位不到真身不算红（CI runner / 开发机的正常形态）
        assert check(tpl, None, True, defaults) == 0, "定位不到真身不应判红"

        # 绿：真身多出模板没有的键 —— 常态类，只提示不判红
        real_extra = tmp / "extra.env"
        _write_env(
            real_extra,
            ["JWT_SECRET_KEY", "DATABASE_URL", "STP_CSRF_ENABLED", "BRAND_NEW_SETTING"],
        )
        assert check(tpl, real_extra, True, defaults) == 0, "真身多键属常态，不该判红"

        # 近似键提示：缺 JWT_SECRET_KEY，真身里是 JWT_SECRET_KEEY（少一个 Y）
        real_typo = tmp / "typo.env"
        _write_env(
            real_typo, ["DATABASE_URL", "STP_CSRF_ENABLED", "JWT_SECRET_KEEY"]
        )
        hints = _near_misses({"JWT_SECRET_KEY"}, {"JWT_SECRET_KEEY"})
        assert "JWT_SECRET_KEY" in hints, f"应识别出近似键，实得 {hints}"

        # 模板缺失 → 2
        assert check(tmp / "nope", real_ok, True, defaults) == 2, "模板缺失应返回 2"

        # 只取键名：注释行、空行、缩进行、export 前缀、小写行都不应污染键集；
        # 值里含 `=` 也不能多切一刀。
        # 前几类在本仓真身/模板中实测形态为零（`^\s*[A-Z_]+=` 与 `^\s*export ` 命中数
        # 均为 0），故按「行首裸 KEY=」这一种形态取键即可；这里钉住它不会被悄悄放宽。
        tricky = tmp / "tricky.env"
        tricky.write_text(
            "# 注释里的 KEY=不算\n"
            "\n"
            "  INDENTED=不算\n"
            "export EXPORTED=不算\n"
            "lowercase=不算\n"
            "JWT_SECRET_KEY=算\n"
            "DSN=user:pass@host/db?a=b\n",
            encoding="utf-8",
        )
        assert keys_of(tricky) == {"JWT_SECRET_KEY", "DSN"}, keys_of(tricky)

    print("[OK] check_env_key_drift self-test 通过（缺键判红/多键放行/近似键提示/只取键名）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="env 键集漂移检测（只读键名，不读值）")
    ap.add_argument("--template", default=os.environ.get("ENV_TEMPLATE", str(DEFAULT_TEMPLATE)))
    ap.add_argument("--check", action="store_true", help="只判红，不打印明细")
    ap.add_argument("--self-test", action="store_true", help="离线红绿双向自证")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    return check(Path(args.template), find_real(os.environ.get("ENV_REAL")), args.check)


if __name__ == "__main__":
    raise SystemExit(main())
