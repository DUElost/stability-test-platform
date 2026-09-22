#!/usr/bin/env python3
"""ADR-0051 Phase 2a：平台脚本版本目录 ⇄ 确定性包 ⇄ ``tool_manifest.json`` 的登记与等价检查。

把 ``backend/agent/scripts/<name>/v<version>/``（Phase 3 前仍是发布单元）逐目录打成
**确定性** tar.gz（复用 ``package_tool_asset.build_deterministic_tar_gz``），整包
``package_sha256`` 登记进 Git 唯一事实源 ``tool_manifest.json``；门禁模式再从当前树
**重建**每个包并与登记值比对——manifest 里的 sha 因此永远可由任何人从 Git 复现，
不依赖 NFS（ADR-0033 D3「PR 门禁只管 Git 侧」）。

登记口径（对齐 ADR-0033 v1.13 C1 / ADR-0051 D3、D4）：

- 成员 = ``git ls-files`` 枚举的**已跟踪**文件（未跟踪的 ``__pycache__`` / 产物不进包，
  sha 只取决于 Git 内容）；目录无跟踪文件时（测试夹具）退回打包器的排除面枚举；
- ``script`` = 与 ``script_catalog._pick_entry`` 同判据的入口文件（首个非 ``_`` 前缀的
  ``.py``/``.sh``）；``python`` = ``null``（平台脚本族由 Agent 自身解释器执行）；
- 版本条目按版本号自然序追加；已登记条目只做幂等复核（append-only 由
  ``check_tool_manifest.py`` 执法，本工具不改写既有条目）。

等价检查（``--check``，默认；进 ``tool-manifest`` 门禁）：

- 每个版本目录都必须已登记，且重建 sha == 登记 sha、入口文件 == 登记 ``script``、
  ``python`` 为 null；
- manifest 里属于脚本族的条目若无对应目录且未 ``retired`` → 红（Phase 3 前目录仍是
  发布单元，不得先删目录后删条目）；外部工具族（``python`` 非 null）不在本工具射程。

用法::

    python tools/dev/check_script_packages.py --self-test
    python tools/dev/check_script_packages.py                      # --check
    python tools/dev/check_script_packages.py --register           # 登记缺失条目（写 manifest）
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
#: 与 ``backend.core.legacy_aee.LEGACY_AEE_SCRIPT_NAMES`` 同值（本工具 stdlib-only，
#: 不 import backend；对拍见 tests/test_adr0051_phase2a.py）。
LEGACY_SCRIPT_NAMES = frozenset({"scan_aee", "export_mobilelogs"})
_VERSION_DIR_RE = re.compile(r"^v(?P<ver>[A-Za-z0-9][A-Za-z0-9._-]*)$")


def _load_packer():
    spec = importlib.util.spec_from_file_location("package_tool_asset", Path(__file__).with_name("package_tool_asset.py"))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def version_key(version: str) -> tuple:
    """纯函数：``1.3.17`` > ``1.3.9``；非数字段按字符串排在数字段之后。"""
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"[.\-]", version))


def pick_entry(version_dir: Path) -> Path | None:
    """与 ``script_catalog._pick_entry`` 同判据：排序后首个非 ``_`` 前缀的 .py/.sh 文件。"""
    for p in sorted(version_dir.iterdir()):
        if p.is_file() and p.suffix in ENTRY_SUFFIXES and not p.name.startswith("_"):
            return p
    return None


def iter_version_dirs(scripts_root: Path) -> list[tuple[str, str, Path]]:
    """``[(name, version, dir)]``，族名字典序、版本自然序；镜像 ``_iter_script_entries`` 的目录判据。"""
    out: list[tuple[str, str, Path]] = []
    if not scripts_root.is_dir():
        return out
    for name_dir in sorted(p for p in scripts_root.iterdir() if p.is_dir()):
        if name_dir.name in LEGACY_SCRIPT_NAMES:
            continue
        versions: list[tuple[str, Path]] = []
        for vdir in name_dir.iterdir():
            m = _VERSION_DIR_RE.match(vdir.name)
            if vdir.is_dir() and m:
                versions.append((m.group("ver"), vdir))
        for ver, vdir in sorted(versions, key=lambda t: version_key(t[0])):
            out.append((name_dir.name, ver, vdir))
    return out


def tracked_files(version_dir: Path, packer) -> list[Path]:
    """``git ls-files`` 下的已跟踪文件（相对 ``version_dir``）；不在 Git 里则退回排除面枚举。"""
    proc = subprocess.run(
        ["git", "-C", str(version_dir), "ls-files", "-z", "--", "."],
        capture_output=True, check=False,
    )
    if proc.returncode == 0:
        rels = sorted(Path(x.decode("utf-8")) for x in proc.stdout.split(b"\0") if x)
        rels = [r for r in rels if (version_dir / r).is_file()]
        if rels:
            return rels
    return packer.collect_package_files(version_dir)


def expected_entries(scripts_root: Path, packer, *, out_dir: Path | None = None) -> dict[tuple[str, str], dict]:
    """从当前树重建每个版本目录的包并返回登记要素；``out_dir`` 给定时 tarball 落 ``{name}/{version}.tar.gz``。"""
    result: dict[tuple[str, str], dict] = {}
    with tempfile.TemporaryDirectory(prefix="stp-script-pkg-") as tmp:
        for name, version, vdir in iter_version_dirs(scripts_root):
            entry = pick_entry(vdir)
            if entry is None:
                continue  # scan 同样不登记无入口的目录
            target = (out_dir / name / f"{version}.tar.gz") if out_dir else (Path(tmp) / f"{name}-{version}.tar.gz")
            facts = packer.build_deterministic_tar_gz(vdir, target, files=tracked_files(vdir, packer))
            result[(name, version)] = {
                "package_sha256": facts["package_sha256"],
                "script": entry.name,
                "file_count": facts["file_count"],
                "bytes": facts["bytes"],
                "dir": vdir,
            }
    return result


def _manifest_index(doc: dict) -> dict[tuple[str, str], dict]:
    idx: dict[tuple[str, str], dict] = {}
    for name, tool in (doc.get("tools") or {}).items():
        for e in tool.get("versions") or []:
            idx[(name, str(e.get("version")))] = e
    return idx


def check(doc: dict, expected: dict[tuple[str, str], dict], scripts_root: Path) -> list[str]:
    """纯函数：manifest ⇄ 树 等价违例列表（空 = 绿）。"""
    errs: list[str] = []
    idx = _manifest_index(doc)
    for (name, version), exp in expected.items():
        e = idx.get((name, version))
        if e is None:
            errs.append(f"{name}@{version}: 版本目录未登记进 tool_manifest.json（跑 --register）")
            continue
        if e.get("python") is not None:
            errs.append(f"{name}@{version}: 平台脚本族 python 须为 null（Agent 自身解释器），实际 {e.get('python')!r}")
        if e.get("package_sha256") != exp["package_sha256"]:
            errs.append(
                f"{name}@{version}: 重建 sha {exp['package_sha256'][:12]} ≠ 登记 {str(e.get('package_sha256'))[:12]}"
                "——已登记版本目录被原地改动，或登记时树不干净；内容变了请发新版本"
            )
        if e.get("script") != exp["script"]:
            errs.append(f"{name}@{version}: 入口 {exp['script']!r} ≠ 登记 script {e.get('script')!r}")
    script_families = {name for name, _ in expected} | {
        p.name for p in scripts_root.iterdir() if scripts_root.is_dir() and p.is_dir()
    }
    for (name, version), e in idx.items():
        if (name, version) in expected:
            continue
        is_script_family = name in script_families or e.get("python") is None
        if not is_script_family:
            continue  # 外部工具族：不在本工具射程
        if not e.get("retired"):
            errs.append(
                f"{name}@{version}: 登记条目无对应版本目录且未 retired——Phase 3 前目录仍是发布单元，"
                "不得先删目录；退役请 retired:true"
            )
    return errs


def register(doc: dict, expected: dict[tuple[str, str], dict], packer) -> tuple[dict, int]:
    """把树上有、manifest 没有的版本追加登记（幂等；异 sha 冲突交给 register_entry 拒绝）。"""
    added = 0
    for (name, version), exp in expected.items():
        doc, appended = packer.register_entry(
            doc, name, version, exp["package_sha256"], packer.artifact_path_for(name, version), None, exp["script"]
        )
        added += int(appended)
    return doc, added


def run_self_test() -> int:
    packer = _load_packer()
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "scripts"
        v1 = root / "alpha" / "v1.0.0"
        v2 = root / "alpha" / "v1.0.10"
        v3 = root / "alpha" / "v1.0.9"
        sh = root / "beta" / "v2.0.0"
        for d in (v1, v2, v3, sh):
            d.mkdir(parents=True)
        for d in (v1, v2, v3):
            (d / "_adb.py").write_text("ADB = 1\n", encoding="utf-8")
            (d / "alpha.py").write_text(f"print('{d.name}')\n", encoding="utf-8")
            (d / "capabilities.json").write_text("[]\n", encoding="utf-8")
            (d / "__pycache__").mkdir()
            (d / "__pycache__" / "alpha.cpython-313.pyc").write_text("x", encoding="utf-8")
        (sh / "beta.sh").write_text("echo beta\n", encoding="utf-8")
        (root / "scan_aee").mkdir()
        (root / "scan_aee" / "v1.0.0").mkdir()
        (root / "scan_aee" / "v1.0.0" / "scan_aee.py").write_text("legacy\n", encoding="utf-8")

        dirs = iter_version_dirs(root)
        if [(n, v) for n, v, _ in dirs] != [("alpha", "1.0.0"), ("alpha", "1.0.9"), ("alpha", "1.0.10"), ("beta", "2.0.0")]:
            failures.append(f"目录枚举/版本自然序错：{[(n, v) for n, v, _ in dirs]}")
        if pick_entry(v1).name != "alpha.py" or pick_entry(sh).name != "beta.sh":
            failures.append("入口判据应跳过 _ 前缀、接受 .sh")

        exp = expected_entries(root, packer)
        if set(exp) != {("alpha", "1.0.0"), ("alpha", "1.0.9"), ("alpha", "1.0.10"), ("beta", "2.0.0")}:
            failures.append(f"expected_entries 键集错：{sorted(exp)}")
        if exp[("alpha", "1.0.0")]["file_count"] != 3:
            failures.append("包成员应为 3（__pycache__ 排除）")
        exp2 = expected_entries(root, packer)
        if {k: v["package_sha256"] for k, v in exp.items()} != {k: v["package_sha256"] for k, v in exp2.items()}:
            failures.append("同树复跑 sha 应相同（确定性）")

        doc = {"schema_version": 1, "tools": {}}
        doc, added = register(doc, exp, packer)
        if added != 4 or check(doc, exp, root):
            failures.append(f"登记后 check 应绿：added={added} errs={check(doc, exp, root)}")
        if [e["version"] for e in doc["tools"]["alpha"]["versions"]] != ["1.0.0", "1.0.9", "1.0.10"]:
            failures.append("登记顺序应为版本自然序")
        if any(e["python"] is not None for t in doc["tools"].values() for e in t["versions"]):
            failures.append("平台脚本族 python 应为 null")
        _, added2 = register(doc, exp, packer)
        if added2 != 0:
            failures.append("重复登记应幂等")

        (v1 / "alpha.py").write_text("print('patched in place')\n", encoding="utf-8")
        exp_mut = expected_entries(root, packer)
        if not any("重建 sha" in e for e in check(doc, exp_mut, root)):
            failures.append("原地改版本目录应红")
        (v1 / "alpha.py").write_text("print('v1.0.0')\n", encoding="utf-8")

        missing = json.loads(json.dumps(doc))
        missing["tools"]["beta"]["versions"] = []
        if not any("未登记" in e for e in check(missing, exp, root)):
            failures.append("目录未登记应红")

        nonnull = json.loads(json.dumps(doc))
        nonnull["tools"]["alpha"]["versions"][0]["python"] = "venv/bin/python"
        if not any("python 须为 null" in e for e in check(nonnull, exp, root)):
            failures.append("平台脚本族 python 非 null 应红")

        ghost = json.loads(json.dumps(doc))
        ghost["tools"]["alpha"]["versions"].append(
            {"version": "9.9.9", "package_sha256": "a" * 64, "artifact": "packages/alpha/9.9.9.tar.gz",
             "python": None, "script": "alpha.py", "retired": False}
        )
        if not any("无对应版本目录" in e for e in check(ghost, exp, root)):
            failures.append("无目录且未退役的条目应红")
        ghost["tools"]["alpha"]["versions"][-1]["retired"] = True
        if check(ghost, exp, root):
            failures.append(f"无目录但已退役应绿：{check(ghost, exp, root)}")

        external = json.loads(json.dumps(doc))
        external["tools"]["Start-Log-Scan"] = {"versions": [
            {"version": "2026.09.22", "package_sha256": "b" * 64, "artifact": "packages/Start-Log-Scan/2026.09.22.tar.gz",
             "python": "venv/bin/python", "script": "start_log_scan.py", "retired": False}
        ]}
        if check(external, exp, root):
            failures.append(f"外部工具族不在射程，应绿：{check(external, exp, root)}")

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        return 1
    print("[OK] check_script_packages self-test 红绿双向（枚举/入口/确定性/登记/原地改/未登记/幽灵条目/外部族豁免）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scripts-root", type=Path, default=DEFAULT_SCRIPTS_ROOT)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--register", action="store_true", help="把树上未登记的版本目录追加进 manifest（写文件）")
    ap.add_argument("--publish", action="store_true", help="把全部脚本包写到 --packages-root/{name}/{version}.tar.gz 并派生 manifest.json 副本")
    ap.add_argument("--packages-root", type=Path, default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return run_self_test()

    packer = _load_packer()
    doc = packer.load_manifest(args.manifest)
    out_dir = args.packages_root if args.publish else None
    if args.publish and out_dir is None:
        print("--publish 需要 --packages-root", file=sys.stderr)
        return 2
    expected = expected_entries(args.scripts_root, packer, out_dir=out_dir)

    if args.register:
        doc, added = register(doc, expected, packer)
        packer.dump_manifest(doc, args.manifest)
        print(f"[OK] 登记 {added} 条新版本条目 → {args.manifest}（树上共 {len(expected)} 个版本目录）")
    errs = check(doc, expected, args.scripts_root)
    if args.publish and not errs:
        packer.write_site_manifest_copy(doc, out_dir)
        total = sum(v["bytes"] for v in expected.values())
        print(f"[OK] 发布 {len(expected)} 个包（{total / 1e6:.1f} MB）→ {out_dir}，manifest.json 副本已派生")
    if errs:
        for e in errs:
            print(f"[FAIL] {e}", file=sys.stderr)
        print(f"[FAIL] check_script_packages：{len(errs)} 项不等价", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"[OK] check_script_packages：{len(expected)} 个版本目录与 tool_manifest.json 登记等价")
    return 0


if __name__ == "__main__":
    os.chdir(REPO_ROOT)
    sys.exit(main())
