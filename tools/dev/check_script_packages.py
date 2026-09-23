#!/usr/bin/env python3
"""ADR-0051 Phase 3：平台脚本族 **源码树** ⇄ 确定性包 ⇄ ``tool_manifest.json`` 的登记与等价门禁。

Phase 3 起 ``backend/agent/scripts/<name>/`` 是**每族一棵可演进的源码树**（入口 + 伴随文件 +
``capabilities.json``，不再有 ``v<version>/`` 目录）；版本号只住在 ``tool_manifest.json``。
发布单元 = 从源码树打出的确定性 tar.gz（`package_tool_asset.build_deterministic_tar_gz`），
整包 ``package_sha256`` 即身份。已登记的包不可变（append-only 门禁）；源码树可变，但
**改了树就必须登记新版本**——否则 ``--check`` 红。

门禁判定（``--check``，默认；进 ``tool-manifest`` 门禁）：

- 树下不得再出现 ``v<version>/`` 目录（Phase 3 后目录模型已退役）；
- 每个族树必须有入口文件（首个非 ``_`` 前缀的 ``.py``/``.sh``，与 ``script_catalog`` 同判据）；
- 每个族树的**重建 sha 必须等于该族最新未退役登记条目的 ``package_sha256``**——不等 = 改了树
  没发版本（跑 ``--register <name> <version>``），或树被回退到旧版本；
- 每个族树的入口文件名必须等于最新条目的 ``script``；平台族 ``python`` 必须为 null；
- 族的归类以**树集**为判据（Phase 4a 起 ``python: null`` 有二义：平台族 与 无包内解释器的
  外部工具族——`--python-absent` 登记）：有树 = 平台族，必须登记且 sha 匹配；无树的条目
  （外部工具族，或整个族被删）不做等价、不判红——**平台族整树删除未退役的保护移交 PR 评审
  + ADR-0051 D5（删除按继承的 ADR-0039 D2 人工 PR + 证据）**。

登记（``--register <name> <version>``）：从族树打包并追加条目（幂等：同版本同 sha 放行；
异 sha 拒绝——版本号不可复用）。``--publish --packages-root <站点包源>``：把**每族最新**条目的
包写到 ``packages/{name}/{version}.tar.gz`` 并派生 ``manifest.json`` 副本（老版本包已在站点，
本工具不重打——它们的源码只在 Git 历史里）。

成员 = ``git ls-files`` 已跟踪文件（sha 只取决于 Git 内容）；不在 Git 时退回打包器排除面。

用法::

    python tools/dev/check_script_packages.py --self-test
    python tools/dev/check_script_packages.py                          # --check
    python tools/dev/check_script_packages.py --register gpu_setup 1.2.4
    python tools/dev/check_script_packages.py --publish --packages-root /mnt/stp-aee/packages
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCRIPTS_ROOT = REPO_ROOT / "backend" / "agent" / "scripts"
DEFAULT_MANIFEST = REPO_ROOT / "tool_manifest.json"
ENTRY_SUFFIXES = (".py", ".sh")
#: 与 ``backend.core.legacy_aee.LEGACY_AEE_SCRIPT_NAMES`` 同值（本工具 stdlib-only，不 import backend）。
LEGACY_SCRIPT_NAMES = frozenset({"scan_aee", "export_mobilelogs"})
_VERSION_DIR_RE = re.compile(r"^v[0-9][A-Za-z0-9._-]*$")


def _load_packer():
    spec = importlib.util.spec_from_file_location("package_tool_asset", Path(__file__).with_name("package_tool_asset.py"))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def version_key(version: str) -> tuple:
    """纯函数：``1.3.17`` > ``1.3.9``；非数字段按字符串排在数字段之后。"""
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"[.\-]", version))


def pick_entry(tree: Path) -> Path | None:
    """与 ``script_catalog._pick_entry`` 同判据：排序后首个非 ``_`` 前缀的 .py/.sh 文件。"""
    for p in sorted(tree.iterdir()):
        if p.is_file() and p.suffix in ENTRY_SUFFIXES and not p.name.startswith("_"):
            return p
    return None


def iter_family_trees(scripts_root: Path) -> list[tuple[str, Path]]:
    """``[(name, tree)]``，族名字典序；跳过 legacy 名与隐藏/下划线目录。"""
    out: list[tuple[str, Path]] = []
    if not scripts_root.is_dir():
        return out
    for d in sorted(p for p in scripts_root.iterdir() if p.is_dir()):
        if d.name in LEGACY_SCRIPT_NAMES or d.name.startswith((".", "_")):
            continue
        out.append((d.name, d))
    return out


def stray_version_dirs(scripts_root: Path) -> list[str]:
    """Phase 3 后不允许的 ``<name>/v<version>/`` 目录（相对路径列表）。"""
    out: list[str] = []
    for name, tree in iter_family_trees(scripts_root):
        for child in sorted(tree.iterdir()):
            if child.is_dir() and _VERSION_DIR_RE.match(child.name):
                out.append(f"{name}/{child.name}")
    return out


def tracked_files(tree: Path, packer) -> list[Path]:
    """``git ls-files`` 下的已跟踪文件（相对 ``tree``）；不在 Git 里则退回排除面枚举。"""
    proc = subprocess.run(["git", "-C", str(tree), "ls-files", "-z", "--", "."], capture_output=True, check=False)
    if proc.returncode == 0:
        rels = sorted(Path(x.decode("utf-8")) for x in proc.stdout.split(b"\0") if x)
        rels = [r for r in rels if (tree / r).is_file()]
        if rels:
            return rels
    return packer.collect_package_files(tree)


def build_family(tree: Path, packer, out: Path) -> dict | None:
    """从族树打包：``{package_sha256, script, file_count, bytes}``；无入口返回 None。"""
    entry = pick_entry(tree)
    if entry is None:
        return None
    facts = packer.build_deterministic_tar_gz(tree, out, files=tracked_files(tree, packer))
    return {"package_sha256": facts["package_sha256"], "script": entry.name,
            "file_count": facts["file_count"], "bytes": facts["bytes"]}


def rebuild_all(scripts_root: Path, packer, *, out_dir: Path | None = None,
                versions: dict[str, str] | None = None) -> dict[str, dict]:
    """每族从当前树重建包；``out_dir`` 给定时按 ``versions[name]`` 落 ``{name}/{version}.tar.gz``。"""
    result: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="stp-script-pkg-") as tmp:
        for name, tree in iter_family_trees(scripts_root):
            if out_dir is not None and versions and name in versions:
                target = out_dir / name / f"{versions[name]}.tar.gz"
            else:
                target = Path(tmp) / f"{name}.tar.gz"
            facts = build_family(tree, packer, target)
            if facts is not None:
                result[name] = facts
    return result


def script_families(doc: dict) -> dict[str, list[dict]]:
    """manifest 里的平台脚本族（任一条目 ``python`` 为 null 即平台族）→ 版本条目列表。"""
    out: dict[str, list[dict]] = {}
    for name, tool in (doc.get("tools") or {}).items():
        versions = tool.get("versions") or []
        if any(e.get("python") is None for e in versions):
            out[name] = versions
    return out


def latest_entry(versions: list[dict]) -> dict | None:
    live = [e for e in versions if not e.get("retired")]
    return max(live, key=lambda e: version_key(str(e.get("version")))) if live else None


def check(doc: dict, rebuilt: dict[str, dict], scripts_root: Path) -> list[str]:
    """纯函数：族树 ⇄ manifest 最新条目等价违例列表（空 = 绿）。"""
    errs: list[str] = []
    for rel in stray_version_dirs(scripts_root):
        errs.append(f"{rel}: Phase 3 后不得再有版本目录——每族只留一棵源码树，版本号住 tool_manifest.json")
    fams = script_families(doc)
    for name, _tree in iter_family_trees(scripts_root):
        facts = rebuilt.get(name)
        if facts is None:
            errs.append(f"{name}: 族树无入口文件（首个非 _ 前缀的 .py/.sh）")
            continue
        versions = fams.get(name)
        if not versions:
            errs.append(f"{name}: 族树未登记进 tool_manifest.json（跑 --register {name} <version>）")
            continue
        latest = latest_entry(versions)
        if latest is None:
            errs.append(f"{name}: 全部条目已 retired 但族树仍在——退役族请删树")
            continue
        if latest.get("python") is not None:
            errs.append(f"{name}@{latest.get('version')}: 平台脚本族 python 须为 null，实际 {latest.get('python')!r}")
        if latest.get("package_sha256") != facts["package_sha256"]:
            errs.append(
                f"{name}: 族树重建 sha {facts['package_sha256'][:12]} ≠ 最新登记 {name}@{latest.get('version')} "
                f"的 {str(latest.get('package_sha256'))[:12]}——改了树没发版本：跑 --register {name} <新版本号>"
            )
        if latest.get("script") != facts["script"]:
            errs.append(f"{name}: 入口 {facts['script']!r} ≠ 最新登记 script {latest.get('script')!r}")
    return errs


def register(doc: dict, name: str, version: str, facts: dict, packer) -> tuple[dict, bool]:
    return packer.register_entry(doc, name, version, facts["package_sha256"],
                                 packer.artifact_path_for(name, version), None, facts["script"])


def run_self_test() -> int:
    packer = _load_packer()
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "scripts"
        for fam in ("alpha", "beta"):
            d = root / fam
            d.mkdir(parents=True)
            (d / "_adb.py").write_text("ADB = 1\n", encoding="utf-8")
            (d / f"{fam}.py").write_text(f"print('{fam}')\n", encoding="utf-8")
            (d / "capabilities.json").write_text("[]\n", encoding="utf-8")
            (d / "__pycache__").mkdir()
            (d / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")
        (root / "scan_aee").mkdir()
        (root / "scan_aee" / "scan_aee.py").write_text("legacy\n", encoding="utf-8")

        if [n for n, _ in iter_family_trees(root)] != ["alpha", "beta"]:
            failures.append("族枚举应跳过 legacy")
        rebuilt = rebuild_all(root, packer)
        if set(rebuilt) != {"alpha", "beta"} or rebuilt["alpha"]["file_count"] != 3:
            failures.append(f"重建键集/成员数错：{ {k: v['file_count'] for k, v in rebuilt.items()} }")
        if rebuild_all(root, packer)["alpha"]["package_sha256"] != rebuilt["alpha"]["package_sha256"]:
            failures.append("同树复跑 sha 应相同")

        doc = {"schema_version": 1, "tools": {}}
        for fam in ("alpha", "beta"):
            doc, added = register(doc, fam, "1.0.0", rebuilt[fam], packer)
            if not added:
                failures.append("首次登记应追加")
        if check(doc, rebuilt, root):
            failures.append(f"登记后 check 应绿：{check(doc, rebuilt, root)}")
        if register(doc, "alpha", "1.0.0", rebuilt["alpha"], packer)[1]:
            failures.append("同版本同 sha 重登记应幂等")

        # 改树不发版本 → 红；登记新版本 → 绿；最新版按自然序（1.0.10 > 1.0.9）
        (root / "alpha" / "alpha.py").write_text("print('alpha v2')\n", encoding="utf-8")
        rebuilt2 = rebuild_all(root, packer)
        if not any("改了树没发版本" in e for e in check(doc, rebuilt2, root)):
            failures.append("改树未登记应红")
        try:
            register(doc, "alpha", "1.0.0", rebuilt2["alpha"], packer)
            failures.append("版本号复用（异 sha）应被拒")
        except SystemExit:
            pass
        doc, _ = register(doc, "alpha", "1.0.9", rebuilt2["alpha"], packer)
        (root / "alpha" / "alpha.py").write_text("print('alpha v3')\n", encoding="utf-8")
        rebuilt3 = rebuild_all(root, packer)
        doc, _ = register(doc, "alpha", "1.0.10", rebuilt3["alpha"], packer)
        if check(doc, rebuilt3, root):
            failures.append(f"登记 1.0.10 后应绿：{check(doc, rebuilt3, root)}")
        if latest_entry(doc["tools"]["alpha"]["versions"])["version"] != "1.0.10":
            failures.append("最新版应按自然序取 1.0.10")

        # 退役最新版 → 最新回落到 1.0.9 → 树（v3）与之不等 → 红
        doc["tools"]["alpha"]["versions"][-1]["retired"] = True
        if not any("改了树没发版本" in e for e in check(doc, rebuilt3, root)):
            failures.append("退役最新版后树应与回落版本不等 → 红")
        doc["tools"]["alpha"]["versions"][-1]["retired"] = False

        # 版本目录残留 → 红
        (root / "beta" / "v9.9.9").mkdir()
        if not any("不得再有版本目录" in e for e in check(doc, rebuilt3, root)):
            failures.append("残留 v 目录应红")
        (root / "beta" / "v9.9.9").rmdir()

        # 无树条目（外部族 python=null，Phase 4a 二义）不判红——归类以树集为判据
        ghost = json.loads(json.dumps(doc))
        ghost["tools"]["gamma"] = {"versions": [{"version": "1.0.0", "package_sha256": "a" * 64,
                                                 "artifact": "packages/gamma/1.0.0.tar.gz", "python": None,
                                                 "script": "gamma.py", "retired": False}]}
        if check(ghost, rebuilt3, root):
            failures.append(f"无树条目应豁免（外部族语义）：{check(ghost, rebuilt3, root)}")

        # 外部工具族（python 非 null）不在射程
        ext = json.loads(json.dumps(doc))
        ext["tools"]["Start-Log-Scan"] = {"versions": [{"version": "2026.09.22", "package_sha256": "b" * 64,
                                                        "artifact": "packages/Start-Log-Scan/2026.09.22.tar.gz",
                                                        "python": "venv/bin/python", "script": "s.py", "retired": False}]}
        if check(ext, rebuilt3, root):
            failures.append(f"外部工具族应豁免：{check(ext, rebuilt3, root)}")

        # publish：只落最新版
        out = Path(tmp) / "packages"
        latest = {n: latest_entry(v)["version"] for n, v in script_families(doc).items()}
        rebuild_all(root, packer, out_dir=out, versions=latest)
        if not (out / "alpha" / "1.0.10.tar.gz").is_file() or (out / "alpha" / "1.0.9.tar.gz").exists():
            failures.append("publish 应只落每族最新版")

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        return 1
    print("[OK] check_script_packages self-test 红绿双向（族树/重建/登记/改树未发版/版本复用/退役回落/残留 v 目录/无树条目豁免/外部族豁免/publish）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scripts-root", type=Path, default=DEFAULT_SCRIPTS_ROOT)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--register", nargs=2, metavar=("NAME", "VERSION"), help="从族树打包并追加登记（写 manifest）")
    ap.add_argument("--publish", action="store_true", help="把每族最新条目的包写到 --packages-root 并派生 manifest.json 副本")
    ap.add_argument("--packages-root", type=Path, default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return run_self_test()

    packer = _load_packer()
    doc = packer.load_manifest(args.manifest)

    if args.register:
        name, version = args.register
        tree = args.scripts_root / name
        if not tree.is_dir():
            print(f"[FAIL] 族树不存在：{tree}", file=sys.stderr)
            return 2
        with tempfile.TemporaryDirectory() as tmp:
            facts = build_family(tree, packer, Path(tmp) / "p.tar.gz")
        if facts is None:
            print(f"[FAIL] {name}: 族树无入口文件", file=sys.stderr)
            return 2
        doc, added = register(doc, name, version, facts, packer)
        packer.dump_manifest(doc, args.manifest)
        print(f"[OK] {'登记' if added else '幂等复核'} {name}@{version} sha={facts['package_sha256'][:12]} "
              f"（{facts['file_count']} 文件 / {facts['bytes']} bytes）→ {args.manifest}")

    if args.publish and args.packages_root is None:
        print("--publish 需要 --packages-root", file=sys.stderr)
        return 2
    latest = {n: latest_entry(v)["version"] for n, v in script_families(doc).items() if latest_entry(v)}
    rebuilt = rebuild_all(args.scripts_root, packer,
                          out_dir=args.packages_root if args.publish else None, versions=latest)
    errs = check(doc, rebuilt, args.scripts_root)
    if args.publish and not errs:
        packer.write_site_manifest_copy(doc, args.packages_root)
        print(f"[OK] 发布 {len(rebuilt)} 个族的最新包 → {args.packages_root}，manifest.json 副本已派生")
    if errs:
        for e in errs:
            print(f"[FAIL] {e}", file=sys.stderr)
        print(f"[FAIL] check_script_packages：{len(errs)} 项不等价", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"[OK] check_script_packages：{len(rebuilt)} 个族树与 tool_manifest.json 最新登记等价")
    return 0


if __name__ == "__main__":
    os.chdir(REPO_ROOT)
    sys.exit(main())
