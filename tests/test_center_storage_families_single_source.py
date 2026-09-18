"""中心存储族清单的单一来源守卫（#2188）。

**本单要防的失效**：同一个事实——「中心存储根下有哪些族、各族按什么主键分目录」——
在仓库里被抄成三份，而且三份互不相同（tip ``e40f7a13`` 复核）：

| 消费方 | 当时自带的清单 | 问题 |
|---|---|---|
| ``cron_scheduler.purge_run_storage_dirs``（retention 写侧） | devices/dedup/jira/_meta | 只有写侧知道 ``_meta`` |
| ``measure_center_storage.CENTER_FAMILIES``（E-1/E-2 读侧） | devices/dedup/jira/**jobs** | **缺 ``_meta``** |
| ``docs/design/2026-scan-upload-merge-contract.md`` 路径表 | devices/dedup/jira/jobs | 缺 ``_meta`` |

后果不是读数难看，而是**安全网缺口**：``_meta/{run_id}/``（#2188 D 步的上传清单分片）
被 retention 清、却不在测量族里 → 该族残留对 E-2 运维对账结构性不可见，「漏桶=E-2 必挂」
这句 commit 自述判据不成立。反向也一样：``jobs/{job_id}`` 按 job 主键分桶，被读侧当 run
统计进 ``run_counts`` / ``top_runs_by_bytes``，与「应已清理 run 清单」对账时会产出既非
漏删也非干净的第三种读数。

判据（由强到弱三条）：

1. **导入身份同一**：写侧与读侧引用的必须是正本同一个对象（本地重绑一份即红）；
2. **主键维度不混**：``jobs`` 是 job 主键，不得进 ``RUN_FAMILIES``；
3. **清单不得再被抄**：生产面除正本外不再出现硬编码族清单（源扫描，锚点先行——
   按 #2639 的纪律，扫描面塌陷与「扫到了却零命中」都判红）。
"""

from __future__ import annotations

import os

# backend.scheduler 的导入链要 DATABASE_URL（root tests/ 无 conftest 注入）。
# 先例：tests/test_settings_scheduler.py、tests/metrics_registry.py（CI 已设 dummy，
# setdefault 不覆盖）。本文件是静态对拍，不连库、不建表。
os.environ.setdefault("DATABASE_URL", "sqlite:///./test-center-storage-families.db")

from pathlib import Path  # noqa: E402

from backend import storage_families as sf  # noqa: E402
from backend.scheduler import cron_scheduler  # noqa: E402
from backend.scripts import measure_center_storage as mcs  # noqa: E402
from tools.dev.source_anchor import SourceGuard  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
#: 正本自身（允许出现族清单字面量的唯一位置）。
CANONICAL = "backend/storage_families.py"
#: 人看的第三份清单：中心存储路径契约表。
CONTRACT_DOC = "docs/design/2026-scan-upload-merge-contract.md"
#: 族清单必然连排出现的两个族名——生产面再出现即说明有人抄了第四份清单。
FAMILY_LIST_NEEDLE = '"dedup", "jira"'
#: 生产面扫描根 / 排除面（与 tools/dev/check_inner_imports.py 同一口径）。
PROD_DIRS = ("backend", "tools", "scripts")
EXCLUDE_PARTS = ("__pycache__",)
EXCLUDE_PREFIXES = (
    "backend/tests/",
    "backend/agent/tests/",
    "backend/agent/scripts/",  # 已发布脚本版本，ADR-0020 不可修改
    "backend/alembic/versions/",  # 历史 revision 不可改写
    "backend/agent/resources/",  # vendored
)


# ── 判据 1：单一来源，且消费方引用的是同一对象 ──────────────────────────────


def test_all_families_is_derived_not_a_fourth_copy() -> None:
    assert sf.ALL_FAMILIES == sf.RUN_FAMILIES + (sf.JOBS_FAMILY,)
    assert len(set(sf.ALL_FAMILIES)) == len(sf.ALL_FAMILIES)


def test_consumers_reference_the_same_objects_not_local_rebindings() -> None:
    """写侧（retention）与读侧（测量）必须引用正本对象本身。"""
    assert cron_scheduler.RUN_FAMILIES is sf.RUN_FAMILIES
    assert cron_scheduler.JOBS_FAMILY is sf.JOBS_FAMILY
    assert mcs.RUN_FAMILIES is sf.RUN_FAMILIES
    assert mcs.ALL_FAMILIES is sf.ALL_FAMILIES


def test_purged_run_families_are_all_measurable() -> None:
    """「漏桶=E-2 必挂」的机械版：写侧清的每一族，读侧都必须测得到。"""
    assert set(cron_scheduler.RUN_FAMILIES) <= set(mcs.ALL_FAMILIES)
    assert "_meta" in sf.RUN_FAMILIES  # 曾被测量侧整族漏掉的那一族


# ── 判据 2：run 主键与 job 主键不得混进同一份分解 ───────────────────────────


def test_jobs_family_is_job_keyed_and_not_a_run_family() -> None:
    assert sf.JOBS_FAMILY not in sf.RUN_FAMILIES
    assert sf.JOBS_FAMILY in sf.ALL_FAMILIES
    assert mcs.RUN_FAMILIES == sf.RUN_FAMILIES  # 读侧的 run 分解面 = 正本 run 族


def test_non_key_entries_only_name_real_families() -> None:
    assert set(sf.NON_KEY_ENTRIES) <= set(sf.ALL_FAMILIES)
    assert sf.NON_KEY_ENTRIES["devices"] == frozenset({"unassigned"})


# ── 判据 3：清单不得再被抄（源扫描，锚点先行）──────────────────────────────


def _prod_python_files(root: Path, *, require_all: bool = False) -> list[Path]:
    files: list[Path] = []
    for rel in PROD_DIRS:
        base = root / rel
        if not base.is_dir():
            # 扫描根消失 = 判据会静默零命中；对真实仓库必须三根俱在，变异自证的
            # 合成树里只放 backend/，故 require_all 由调用方决定。
            assert not require_all, f"生产面扫描根消失：{rel}"
            continue
        for path in base.rglob("*.py"):
            posix = path.relative_to(root).as_posix()
            if any(part in path.parts for part in EXCLUDE_PARTS):
                continue
            if posix.startswith(EXCLUDE_PREFIXES):
                continue
            files.append(path)
    return files


def _family_list_offenders(root: Path) -> list[str]:
    """返回「自带族清单字面量」的生产面文件（相对路径，升序）。"""
    offenders: list[str] = []
    for path in _prod_python_files(root):
        posix = path.relative_to(root).as_posix()
        if posix == CANONICAL:
            continue
        if FAMILY_LIST_NEEDLE in path.read_text(encoding="utf-8"):
            offenders.append(posix)
    return sorted(offenders)


def test_known_consumers_route_through_the_canonical_list() -> None:
    """两个已知消费方必须走正本：锚点不在=用例过期，字面量回潮=防线回归。"""
    write_side = SourceGuard.of_module(cron_scheduler).anchored("for sub in RUN_FAMILIES:")
    write_side.assert_absent(FAMILY_LIST_NEEDLE, why="#2188：purge 桶清单必须来自单一来源")

    read_side = SourceGuard.of_module(mcs).anchored("for family in ALL_FAMILIES")
    read_side.assert_absent(FAMILY_LIST_NEEDLE, why="#2188：测量族清单必须来自单一来源")


def test_no_other_production_file_hardcodes_a_family_list() -> None:
    """正本之外，生产面不得再出现第四份族清单。"""
    scanned = _prod_python_files(REPO_ROOT, require_all=True)
    assert len(scanned) > 300, f"生产面扫描面塌陷：只扫到 {len(scanned)} 个文件"
    assert _family_list_offenders(REPO_ROOT) == [], (
        "这些文件自带了中心存储族清单，应改为引用 "
        f"{CANONICAL}（#2188）：{_family_list_offenders(REPO_ROOT)}"
    )


def test_path_contract_doc_lists_every_family(tmp_path: Path) -> None:
    """路径契约表（人看的第三份）也必须逐族齐全——它同样漂过（缺 ``_meta``）。

    判据取「``{root}/<族>/`` 形状出现在『中心存储路径』节里」，不按整篇文档扫：
    别的章节提到同名目录不算数，那正是 #2643/#2639 数错过计数的位置。
    """
    text = (REPO_ROOT / CONTRACT_DOC).read_text(encoding="utf-8")
    start = text.index("## 中心存储路径")
    rest = text[start + len("## 中心存储路径"):]
    nxt = rest.find("\n## ")
    section = rest[: nxt if nxt >= 0 else len(rest)]
    assert section.strip(), f"{CONTRACT_DOC} 的『中心存储路径』节为空"

    missing = [f for f in sf.ALL_FAMILIES if f"{{root}}/{f}/" not in section]
    assert not missing, f"路径契约表缺族：{missing}（正本见 {CANONICAL}）"


def test_the_scan_detects_a_poisoned_copy_and_ignores_the_canonical_one(
    tmp_path: Path,
) -> None:
    """变异自证：判据必须真的能抓到抄清单的文件，也不把正本误判为 offender。

    没有这条，判据 3 与被注释掉的判据无法区分——#2639 数过的那种「守卫恒绿」。
    """
    poison_dir = tmp_path / "backend" / "services"
    poison_dir.mkdir(parents=True)
    poison = poison_dir / "pretend_consumer.py"
    poison.write_text(
        'FAMILIES = ("devices", "dedup", "jira", "_meta")\n', encoding="utf-8"
    )
    # 抓到：抄了清单的文件必须出现在 offender 清单里
    assert _family_list_offenders(tmp_path) == ["backend/services/pretend_consumer.py"]

    poison.unlink()
    canonical = tmp_path / CANONICAL
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text(
        'RUN_FAMILIES = ("devices", "dedup", "jira", "_meta")\n'
        'MERGE_REPORT_FAMILIES = ("dedup", "jira")\n',
        encoding="utf-8",
    )
    # 不误报：正本自己带着两份清单，必须被豁免（否则判据会在第一天就红得没法用）
    assert _family_list_offenders(tmp_path) == []
