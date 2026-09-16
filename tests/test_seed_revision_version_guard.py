"""#2399：seed 迁移「守卫版本 / 写入版本 / sha 版本」必须四面同源，静态可查。

背景：两条 seed 迁移把守卫与写入的 ``version`` 比本文件真正 seed 的版本小一整个版本
（``t2u3v4w5x6y7`` 文件名 v138 却写 1.3.7、``u3v4w5x6y7z8`` 文件名 v139 却写 1.3.8），
于是**空库自举**产出「版本=1.3.8、内容=1.3.9」的幽灵行，而 v1.3.9 根本没有行。
``script_catalog`` 以 ``content_sha256`` 判 conflict → 新环境无法靠重扫自愈，只能
``force_rebaseline``。同族已有三次：#751（seed 注册了 ``_lib.py`` 的 sha）、
#1276（原地改已上线迁移的 sha）、#2399（本单）。

本文件守三条判据（全部纯离线，跑在 required check ``pr-agent-tests`` 的 PR 路径里）：

1. **四面同源**：``文件名版本 == 本迁移写入行的 version == sha 注释里的版本``，且
   ``_CONTENT_SHA256`` 必须等于**磁盘上被写入那一行所指版本**的入口文件 sha；
2. **豁免必须配修复**：历史 revision 不可改（#2258），所以已知的两条只能进豁免表——
   而豁免成立的前提是链尾存在一条修复迁移，且它真的处理了那个 (name, version)；
3. **「只修 sha/path 就够」这个取舍本身**：v1.3.7/1.3.8/1.3.9 三版 seed 的
   ``PARAM_SCHEMA`` / ``DEFAULT_PARAMS`` 源码块逐字相同。这个等价是**偶然的**，
   一旦有人给某个版本改参数，就必须回来重新评估修复面（见 Revisit）。

判据 1 的 sha 与入口挑选**不自己实现**：走子进程复用
``backend.services.script_catalog._iter_script_entries`` / ``sha256_file``——复制一份
「哪里是入口、怎么算指纹」就是再造一次 #2399 这种「两处真值各说各话」。子进程里显式喂
假 ``DATABASE_URL``（控制面模块在导入期解析它），不把本机连接串带进去。
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "backend" / "alembic" / "versions"
SCRIPTS = ROOT / "backend" / "agent" / "scripts"

_SEED_FILE_RE = re.compile(r"^.*_seed_(?P<name>[a-z_]+?)_v(?P<ver>\d+)_params\.py$")
_DIGITS = re.compile(r"\D")

# 已知且不可改的历史缺陷（#2258 只允许新增 revision）。每条都必须同时满足：
# (a) 由 REPAIR_REVISION 处理；(b) 缺陷签名与本表一致——否则本文件红。
KNOWN_OFF_BY_ONE: dict[str, dict[str, str]] = {
    "t2u3v4w5x6y7_seed_flash_firmware_v138_params.py": {
        "writes": "1.3.7", "seeds": "1.3.8",
        "reason": "守卫/写入版本比 sha·nfs 所指版本小一个版本",
    },
    "u3v4w5x6y7z8_seed_flash_firmware_v139_params.py": {
        "writes": "1.3.8", "seeds": "1.3.9",
        "reason": "同上（复制粘贴自上一条）",
    },
}
REPAIR_REVISION = "e5f6a7b8c9d0"


def _digits(version: str) -> str:
    """版本串去掉分隔符后的数字面。``1.3.10`` 与 ``1.31.0`` 会同形，这里只作
    「四面是不是同一个版本」的必要条件用，不用于解析语义。"""
    return _DIGITS.sub("", version)


def _seed_files() -> list[Path]:
    return sorted(
        p for p in VERSIONS.glob("*_seed_*params.py")
        if "_CONTENT_SHA256" in p.read_text(encoding="utf-8")
    )


_PROBE = r'''
import json
from pathlib import Path
from backend.services.script_catalog import _iter_script_entries, sha256_file

root = Path("backend/agent/scripts")
out = {}
for _cat, name, version, entry, _stype in _iter_script_entries(root):
    out[f"{name}@{version}"] = sha256_file(entry)
print(json.dumps(out))
'''


@functools.lru_cache(maxsize=None)
def _disk_truth() -> dict[str, str]:
    """磁盘上「扫描会登记成什么指纹」的映射，复用扫描自己的判据。"""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DATABASE_URL", "JWT_SECRET_KEY", "TESTING")
    }
    env["DATABASE_URL"] = "sqlite:///" + str(
        Path(tempfile.gettempdir()) / "stp-2399-probe-not-a-database.db"
    )
    env["JWT_SECRET_KEY"] = "stp-2399-probe-only"
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"探针子进程退出 {proc.returncode}\n{proc.stderr[-3000:]}"
    return json.loads(proc.stdout)


_ROW_BINDINGS_RE = re.compile(r'"name": "([a-z_]+)",\s*"ver": "([\d.]+)"')


def _written_rows(text: str) -> list[tuple[str, str]]:
    """本迁移 INSERT/UPDATE 用的 ``(name, ver)`` 绑定值。"""
    return _ROW_BINDINGS_RE.findall(text)


def _joined(text: str) -> str:
    """把被 Python 隐式拼接的相邻字符串字面量合回去，便于匹配完整 nfs 路径。"""
    return re.sub(r'"\s*\n\s*"', "", text)


def _const_blocks(text: str, name: str) -> str:
    m = re.search(rf"^{name} = \{{.*?^\}}", text, re.S | re.M)
    assert m, f"未找到常量 {name}——解析器需随 seed 模板更新"
    return m.group(0)


def test_scan_surface_is_not_empty():
    """恒真防护：判据覆盖面就是 issue 说的 18 条，一条都不许静默漏掉。"""
    files = _seed_files()
    assert len(files) == 18, [p.name for p in files]


@pytest.mark.parametrize("path", _seed_files(), ids=lambda p: p.name)
def test_seed_revision_versions_are_single_sourced(path):
    text = _joined(path.read_text(encoding="utf-8"))
    fname = _SEED_FILE_RE.match(path.name)
    assert fname, f"{path.name} 不匹配 seed 命名——判据需随之更新"
    declared = fname.group("ver")

    rows = _written_rows(text)
    assert rows, f"{path.name} 里找不到 (name, ver) 绑定，解析器需更新"
    names = {n for n, _ in rows}
    assert len(names) == 1, f"{path.name} 涉及多个脚本名：{sorted(names)}"
    written = sorted({v for _, v in rows})

    # 注释里的版本只在**写得出来**时参与比对：合法形态有二
    #   # sha256 of flash_firmware.py v1.3.9
    #   # sha256 of flash_firmware.py (same content across v1.0.0 and v1.0.1)
    # 后者本就没有单一版本；磁盘指纹才是权威（另一个用例直接比它）。
    sha_comment = re.search(r"# sha256 of \S+ v([\d.]+)", text)

    if path.name in KNOWN_OFF_BY_ONE:
        # 豁免面精确钉住：写人的版本、seed 的版本、sha 所指版本都必须与登记表一致
        expect = KNOWN_OFF_BY_ONE[path.name]
        assert {_digits(v) for v in written} == {_digits(expect["writes"])}, (
            f"{path.name} 的缺陷签名已变化——豁免表必须重新评估，不能沿用")
        assert _digits(declared) == _digits(expect["seeds"])
        assert sha_comment and _digits(sha_comment.group(1)) == _digits(expect["seeds"])
        return

    faces = {
        "文件名": _digits(declared),
        "写入行": _digits(written[0]),
    }
    if sha_comment:
        faces["sha 注释"] = _digits(sha_comment.group(1))
    assert len(set(faces.values())) == 1, (
        f"{path.name} 三面版本不同源：{faces}——"
        "守卫/写入版本必须与本文件 seed 的版本一致（#2399 的成因）"
    )


@pytest.mark.parametrize("path", _seed_files(), ids=lambda p: p.name)
def test_seed_sha_matches_disk_identity(path):
    """``_CONTENT_SHA256`` 必须等于「本迁移写入那一行所指版本」的磁盘入口指纹。

    这正是 scan 判 conflict 用的同一个值：不相等 → 新环境永久 conflict（#2399 现象），
    且 nfs_path 会指向另一个版本的目录 → 有静默跑错版本的可能。
    """
    truth = _disk_truth()
    text = _joined(path.read_text(encoding="utf-8"))
    sha = re.search(r'_CONTENT_SHA256 = "([0-9a-f]{64})"', text)
    assert sha, f"{path.name} 没有 64 位十六进制的 _CONTENT_SHA256"
    sha_value = sha.group(1)

    name = _SEED_FILE_RE.match(path.name).group("name")
    # 豁免的两条按「文件名声明的版本」评——那才是它本应 seed 的版本
    target = (
        KNOWN_OFF_BY_ONE[path.name]["seeds"]
        if path.name in KNOWN_OFF_BY_ONE
        else sorted({v for _, v in _written_rows(text)})[0]
    )
    key = f"{name}@{target}"
    assert key in truth, f"{key} 在磁盘脚本树里没有条目（扫描也登记不出来）"
    assert truth[key] == sha_value, (
        f"{path.name} 写入 {key} 的 content_sha256={sha_value[:12]}… "
        f"但磁盘真值是 {truth[key][:12]}… → scan 会判 conflict"
    )


def test_exemptions_are_backed_by_a_repair_revision():
    """豁免不是「知道就好」：每条已知缺陷都必须由链尾的修复迁移实际处理。"""
    repair = VERSIONS / f"{REPAIR_REVISION}_repair_flash_firmware_seed_identity_2399.py"
    assert repair.exists(), (
        f"缺修复 revision {REPAIR_REVISION}——豁免表 {sorted(KNOWN_OFF_BY_ONE)} "
        "没有补偿控制，历史文件又不可改（#2258）"
    )
    text = repair.read_text(encoding="utf-8")
    for fname, info in KNOWN_OFF_BY_ONE.items():
        seeds = info["seeds"]
        assert f"'{seeds}'" in text or f'"{seeds}"' in text, (
            f"{repair.name} 没有处理 {fname} 造成的 {seeds} 缺陷态")
    # 幽灵行由被错位那条的 sha 标识
    ghost = re.search(
        r'_CONTENT_SHA256 = "([0-9a-f]{64})"',
        (VERSIONS / "u3v4w5x6y7z8_seed_flash_firmware_v139_params.py")
        .read_text(encoding="utf-8"),
    ).group(1)
    assert ghost in text, "修复迁移未引用幽灵 sha，无法确认它认得这个缺陷签名"


def test_repair_revision_does_not_newly_activate_anything():
    """补行必须 is_active=false：修复不能顺手扩大派发面（详见迁移 docstring）。"""
    text = (VERSIONS / f"{REPAIR_REVISION}_repair_flash_firmware_seed_identity_2399.py")\
        .read_text(encoding="utf-8")
    assert "INSERT INTO script" in text, "找不到补行的 INSERT 语句块，判据需随之更新"
    assert re.search(r"CAST\(:pschema AS jsonb\), CAST\(:dparams AS jsonb\), false,", text), (
        "补行的 is_active 不是 false——空库会凭空多出一个可派发版本（#2399 docstring）")
    assert "is_active = true" not in text, "修复迁移不得把任何版本置为 active"


def test_three_versions_params_are_identical():
    """「只修 sha/nfs、不碰 default_params」的取舍前提，必须被机器守住。"""
    def blocks(rev: str) -> tuple[str, str]:
        files = list(VERSIONS.glob(f"*_{rev}_params.py"))
        assert len(files) == 1, files
        text = files[0].read_text(encoding="utf-8")
        return (_const_blocks(text, "PARAM_SCHEMA"), _const_blocks(text, "DEFAULT_PARAMS"))

    b137 = blocks("seed_flash_firmware_v137")
    b138 = blocks("seed_flash_firmware_v138")
    b139 = blocks("seed_flash_firmware_v139")
    assert b137 == b138 == b139, (
        "v1.3.7/1.3.8/1.3.9 的 seed 参数不再逐字相同——"
        "#2399 修复迁移「不动 default_params」的前提失效，需重新评估修复面"
    )


def test_repair_constants_are_verbatim_from_history():
    """修复迁移写的常量必须与被修复的历史文件逐字相同，避免「第三份真值」。

    同时钉住 sha 与 nfs 指向同一个版本——本次缺陷的确切形状就是两者错位。
    """
    repair = (VERSIONS / f"{REPAIR_REVISION}_repair_flash_firmware_seed_identity_2399.py")
    text = _joined(repair.read_text(encoding="utf-8"))
    truth = _disk_truth()

    for rev, version, sha_const, nfs_const in (
        ("seed_flash_firmware_v138", "1.3.8", "_SHA_138", "_NFS_138"),
        ("seed_flash_firmware_v139", "1.3.9", "_SHA_139", "_NFS_139"),
    ):
        src = next(VERSIONS.glob(f"*_{rev}_params.py")).read_text(encoding="utf-8")
        historical_sha = re.search(r'_CONTENT_SHA256 = "([0-9a-f]{64})"', src).group(1)
        written_sha = re.search(rf'{sha_const} = "([0-9a-f]{{64}})"', text)
        assert written_sha, f"修复迁移缺 {sha_const}"
        assert written_sha.group(1) == historical_sha, (
            f"{sha_const} 与 {rev} 历史文件里的 _CONTENT_SHA256 不再逐字相同")
        assert written_sha.group(1) == truth[f"flash_firmware@{version}"], (
            f"{sha_const} 不等于磁盘上 flash_firmware {version} 的指纹")

        nfs = re.search(rf'{nfs_const} = "([^"]+)"', text)
        assert nfs, f"修复迁移缺 {nfs_const}"
        assert f"/flash_firmware/v{version}/" in nfs.group(1), (
            f"{nfs_const} 指向的版本目录({nfs.group(1)})与 {sha_const} 的版本 {version} 不一致")
