"""env 键集漂移检测的判红边界（`tools/dev/check_env_key_drift.py`）。

为什么要有这道门禁：生产 env 真身（`stp-releases/env.backend`，600、未跟踪、
**无备份**）一旦丢失，代码重建了也起不来服务，且无从判断「少了哪些配置」。本门禁
不要求真值可恢复，只要求**缺键这件事是机器可判的事实**。

判定边界是这组测试的全部价值。脚本第一次对真身实跑时的发现直接决定了边界该怎么划：
真身缺 `HOST_HEARTBEAT_TIMEOUT_SECONDS` / `UNKNOWN_GRACE_SECONDS` /
`SESSION_WATCHDOG_INTERVAL_SECONDS` 三个键，逐个查证**都有代码默认值兜底**
（300/300/15，与模板声明值一致），生产行为完全正确。若按「缺键就红」，门禁第一次
接触现实就误报，此后必然被当噪声忽略——所以边界钉在「缺键 ∧ 无兜底」。

纪律：本组测试全程不接触真凭据，夹具里的值都是固定假串。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "tools" / "dev"))

import check_env_key_drift as mod  # noqa: E402


def _env(tmp_path: Path, name: str, keys: list[str]) -> Path:
    """夹具：只写键名，值是固定假串（**不是**真凭据）。"""
    p = tmp_path / name
    p.write_text(
        "\n".join(f"{k}=fixture-not-a-real-secret" for k in keys) + "\n", encoding="utf-8"
    )
    return p


@pytest.fixture
def tpl(tmp_path: Path) -> Path:
    p = tmp_path / "tpl"
    p.write_text("JWT_SECRET_KEY=x\nDATABASE_URL=x\nSTP_CSRF_ENABLED=x\n", encoding="utf-8")
    return p


# 默认值表：DATABASE_URL / STP_CSRF_ENABLED 有兜底，JWT_SECRET_KEY（凭据类）没有。
DEFAULTS = {"DATABASE_URL": "sqlite://", "STP_CSRF_ENABLED": "1"}


def test_only_keynames_are_read_never_values(tmp_path: Path):
    """纪律：只取等号左边。值里含 `=` / 空格 / 换行都不得影响键集。"""
    p = tmp_path / "x.env"
    p.write_text(
        "# 注释 KEY= 不算\n"
        "  INDENTED= 不算\n"
        "export EXPORTED= 不算\n"
        "lower= 不算\n"
        "REAL_ONE=v\n"
        "DSN=user:pass@host/db?a=b&c=d\n",
        encoding="utf-8",
    )
    assert mod.keys_of(p) == {"REAL_ONE", "DSN"}


def test_missing_key_without_fallback_is_blocking(tmp_path: Path, tpl: Path):
    """缺**无兜底**的键（JWT_SECRET_KEY）→ 判红。"""
    real = _env(tmp_path, "r.env", ["DATABASE_URL", "STP_CSRF_ENABLED"])
    assert mod.check(tpl, real, True, DEFAULTS) == 1


def test_missing_key_with_fallback_is_not_blocking(tmp_path: Path, tpl: Path):
    """缺**有兜底**的键 → 不判红（这是实跑后收紧的语义边界）。"""
    real = _env(tmp_path, "r.env", ["JWT_SECRET_KEY", "DATABASE_URL"])
    assert mod.check(tpl, real, True, DEFAULTS) == 0


def test_extra_key_is_never_blocking(tmp_path: Path, tpl: Path):
    """真身多出模板没有的键是常态（生产新增配置未入模板）→ 永不判红。"""
    real = _env(
        tmp_path, "r.env",
        ["JWT_SECRET_KEY", "DATABASE_URL", "STP_CSRF_ENABLED", "BRAND_NEW"],
    )
    assert mod.check(tpl, real, True, DEFAULTS) == 0


def test_absent_real_env_is_not_blocking(tmp_path: Path, tpl: Path):
    """定位不到真身（CI runner / 开发机）不判红——那是正常形态，不是缺配置。"""
    assert mod.check(tpl, None, True, DEFAULTS) == 0


def test_missing_template_is_usage_error(tmp_path: Path):
    """模板缺失属用法错 → 2，与「判红」区分开。"""
    real = _env(tmp_path, "r.env", ["A"])
    assert mod.check(tmp_path / "nope", real, True, DEFAULTS) == 2


def test_defaults_table_absent_falls_back_to_conservative(tmp_path: Path, tpl: Path):
    """拿不到默认值表时**保守判红**（全部缺键都算阻断），而不是静默放过。

    静默放过是这里最危险的失败模式：门禁看起来绿，而真身已经缺了无兜底的凭据键。
    """
    real = _env(tmp_path, "r.env", ["DATABASE_URL"])  # 缺 JWT_SECRET_KEY
    assert mod.check(tpl, real, True, defaults={}) == 1


def test_near_miss_typo_is_reported(tmp_path: Path, tpl: Path):
    """拼错时给出近似键提示：把诊断从「少了 X」提升为「少了 X，真身里像 Y」。"""
    hints = mod._near_misses({"JWT_SECRET_KEY"}, {"JWT_SECRET_KEEY"})
    assert "JWT_SECRET_KEY" in hints
    assert "JWT_SECRET_KEEY" in hints["JWT_SECRET_KEY"]


def test_near_miss_respects_edit_distance():
    """自实现的是 Levenshtein 距离（difflib 的 cutoff 是相似度比例，语义不同）。"""
    assert mod._edit_distance("JWT_SECRET_KEY", "JWT_SECRET_KEEY") == 1
    assert mod._edit_distance("ABC", "ABC") == 0
    assert mod._edit_distance("", "ABC") == 3


def test_script_self_test_passes():
    """脚本自带的红绿双向自证必须过（它同时是 check:quick 门禁的第一步）。"""
    assert mod.self_test() == 0


def test_repository_template_and_real_env_have_no_blocking_drift():
    """对**本仓真身**实跑：当前必须无阻断性漂移。

    这是端到端的那一条：前面都是夹具，这里是真的 `stp-releases/env.backend`
    （只读键名）。拿不到真身的形态会自行返回 0。
    """
    template = Path(mod.DEFAULT_TEMPLATE)
    assert template.is_file(), f"模板缺失：{template}"
    assert mod.check(template, mod.find_real(None), True) == 0
