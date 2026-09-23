#!/usr/bin/env python3
"""ADR-0033 Phase B 第一切片（#3075）：外部工具资产打包与登记。

把中心存储过渡形态 ``tools/{name}/`` 的**当前源码树**打成**确定性** tar.gz，
计算整包 ``package_sha256``，并登记进 Git 侧唯一事实源 ``tool_manifest.json``。

设计约束（对齐 #3075 正文「实现前裁决」六条）：

- **C1** 整包指纹命名 ``package_sha256``，与 ``script.content_sha256``
  （entry-file 单文件 sha，ADR-0020）语义分离；脚本侧不做包版本 pin。
- **C2** Git manifest 是唯一登记源：派生方向 = manifest → 拉取 → 校验 →
  ``tools_cache``；本工具是唯一的写入者（追加条目 + 生成站点派生副本
  ``packages/manifest.json``），杜绝手改第二事实源。append-only 与退役
  判定由门禁 ``check_tool_manifest.py`` 把守，本工具只做同版本同 sha 幂等。
- **C4** 产物落每站中心存储 ``packages/{name}/{version}.tar.gz``（与过渡
  目录 ``tools/`` 物理分开）；``--packages-root`` 即站点中心存储根。
- **C5** manifest 只装分发字段（version / package_sha256 / artifact /
  python / script / retired）。其中 ``python``/``script`` 是**包内相对路径**
  ——回答「产物在包里何处」，不含执行契约（``--check-env`` / ``summary.json``
  / 退出码），后者仍属 ``verify_tool_contract.py`` 判定面。

默认排除运行态噪声（``logs/`` ``tmp/`` ``result/`` ``merge_result/``
``__pycache__`` ``*.pyc`` ``.ace-tool/`` ``~``）：工具目录长期就地运行留下的
产物不是工具资产（866M 的 Start-Log-Scan 目录里 827M 是 logs）。

用法::

    # 打样板包 + 登记 Git manifest + 发布到站点中心存储（一条链）：
    python tools/dev/package_tool_asset.py \\
        --src /mnt/stp-aee/tools/Start-Log-Scan \\
        --name Start-Log-Scan --version 2026.09.22 \\
        --python-relative venv/bin/python --script-relative start_log_scan.py \\
        --write-manifest --packages-root /mnt/stp-aee/packages

    # 只算 sha 不落任何盘（审查/复跑用）：
    python tools/dev/package_tool_asset.py --src <dir> --name <n> --version <v> --dry-run
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "tool_manifest.json"

#: 运行态目录/文件：随目录就地产生，不属工具资产。
DEFAULT_EXCLUDE_DIRS = frozenset(
    {"logs", "tmp", "result", "merge_result", "__pycache__", ".ace-tool", ".git", ".pytest_cache"}
)
DEFAULT_EXCLUDE_FILES = frozenset({"~", ".DS_Store"})
DEFAULT_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".log")


def is_excluded(rel: Path, exclude_dirs: frozenset[str], exclude_files: frozenset[str]) -> bool:
    """纯函数：``rel`` 相对包根的路径是否命中排除规则（任一父目录名命中即整棵排除）。"""
    parts = rel.parts[:-1]
    if any(p in exclude_dirs for p in parts):
        return True
    if rel.name in exclude_files:
        return True
    return rel.name.endswith(DEFAULT_EXCLUDE_SUFFIXES)


def collect_package_files(src: Path, exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS) -> list[Path]:
    """列出包内文件（相对 ``src``，确定性排序；排除运行态；symlink 以链接本体计）。"""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        rel_dir = Path(dirpath).relative_to(src)
        dirnames[:] = sorted(d for d in dirnames if d not in exclude_dirs)
        for name in sorted(filenames):
            rel = rel_dir / name if str(rel_dir) != "." else Path(name)
            if is_excluded(rel, exclude_dirs, DEFAULT_EXCLUDE_FILES):
                continue
            out.append(rel)
    return sorted(out)


def _normalized_mode(info: "tarfile.TarInfo") -> int:
    """纯函数：tar 成员权限位 → Git 语义（symlink 0o777；目录 0o755；文件按可执行位 0o755/0o644）。"""
    if info.issym():
        return 0o777
    if info.isdir():
        return 0o755
    return 0o755 if info.mode & 0o111 else 0o644


def build_deterministic_tar_gz(
    src: Path, out: Path, *, version_stamp: int = 0, files: list[Path] | None = None
) -> dict:
    """把 ``src``（排除面后）打成确定性 tar.gz 写入 ``out``，返回登记要素。

    ``files`` 显式给定包内成员（相对 ``src``）时跳过 ``collect_package_files``——
    ADR-0051 Phase 2a 用 ``git ls-files`` 枚举版本目录，让 sha 只取决于 Git 内容、
    与工作树里的未跟踪产物无关。

    确定性来源：成员按相对路径排序；uid/gid 归零、uname/gname 清空、mtime 固定；
    gzip 头 mtime=0；GNU 格式（无 PAX 扩展头）。同内容复跑必得同 sha。
    """
    files = sorted(files) if files is not None else collect_package_files(src)
    out.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=version_stamp) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar:
            for rel in files:
                full = src / rel
                info = tar.gettarinfo(str(full), arcname=rel.as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = version_stamp
                # ADR-0051 Phase 2b 勘误：权限位按 Git 语义归一化——只保留「可执行位」
                # （文件 0644 / 0755，目录 0755，symlink 0777）。否则同一份 Git 内容在
                # umask 002 的机器上打出 0664、在 CI 上打出 0644，sha 分叉（#3165 后
                # 实测：210 个脚本包全部因此不等价，72250d4b 曾按 644 环境重登记）。
                # 与 ADR-0040 digest 的 (relpath, 可执行位, sha) 三元组同口径。
                info.mode = _normalized_mode(info)
                if full.is_symlink():
                    tar.addfile(info)  # symlink：数据段即链接目标
                    continue
                with open(full, "rb") as fh:
                    tar.addfile(info, fh)
    data = buf.getvalue()
    out.write_bytes(data)
    return {
        "package_sha256": hashlib.sha256(data).hexdigest(),
        "file_count": len(files),
        "bytes": len(data),
    }


def validate_relative_member(p: str, *, field: str) -> str | None:
    """纯函数：包内相对路径合法性（分发字段与门禁共用）。返回错误说明或 None。"""
    if not p or p.startswith("/") or "\\" in p or ":" in p.split("/")[0]:
        return f"{field} 必须是包内相对路径（不得绝对/空）：{p!r}"
    if any(seg in ("", "..") for seg in p.split("/")):
        return f"{field} 不得含 '..' 或空段：{p!r}"
    return None


def artifact_path_for(name: str, version: str) -> str:
    """C4：站点中心存储包源布局（manifest.artifact 的唯一拼法，门禁同判据）。"""
    return f"packages/{name}/{version}.tar.gz"


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "tools": {}}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema_version") != 1 or not isinstance(doc.get("tools"), dict):
        raise SystemExit(f"{path}: 仅支持 schema_version=1 且含 tools 对象")
    return doc


def register_entry(
    doc: dict, name: str, version: str, sha: str, artifact: str, python_rel: str | None, script_rel: str
) -> tuple[dict, bool]:
    """纯函数：向 manifest 文档追加版本条目（幂等：同版本同 sha 放行，异 sha 拒绝）。

    ``python_rel=None`` = 包内无解释器，由 Agent 自身解释器执行（ADR-0051 D4：平台脚本族）。

    返回 (doc, appended)。append-only/退役规则由门禁统一执法；这里只防手滑覆盖。
    """
    tool = doc["tools"].setdefault(name, {"versions": []})
    for entry in tool["versions"]:
        if entry.get("version") == version:
            if entry.get("package_sha256") == sha:
                return doc, False  # 幂等重登
            raise SystemExit(
                f"append-only 冲突：{name}@{version} 已登记 sha {entry.get('package_sha256')}，"
                f"拒绝改为 {sha}——内容变了就该发新版本号"
            )
    tool["versions"].append(
        {
            "version": version,
            "package_sha256": sha,
            "artifact": artifact,
            "python": python_rel,
            "script": script_rel,
            "retired": False,
        }
    )
    return doc, True


def dump_manifest(doc: dict, path: Path) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_site_manifest_copy(doc: dict, packages_root: Path) -> None:
    """C2 派生副本：站点 ``packages/manifest.json`` 只由本函数从 Git 源生成。"""
    (packages_root / "manifest.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, type=Path, help="工具源码目录（过渡形态 tools/{name}/）")
    ap.add_argument("--name", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--python-relative", default="", help="包内解释器相对路径（登记字段，可空=纯审查不登记）")
    ap.add_argument("--script-relative", default="", help="包内入口脚本相对路径（登记字段）")
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--write-manifest", action="store_true", help="把新条目追加进 Git manifest 唯一事实源")
    ap.add_argument("--packages-root", type=Path, default=None, help="站点中心存储根（C4：写包 + 派生 manifest 副本）")
    ap.add_argument("--dry-run", action="store_true", help="只计算并打印要素，不写任何文件")
    args = ap.parse_args()

    if not (args.src.is_dir() and not args.src.is_symlink()):
        print(f"[FAIL] --src 必须是实目录：{args.src}", file=sys.stderr)
        return 1
    for field, value in (("python", args.python_relative), ("script", args.script_relative)):
        if value:  # 空=未指定（纯审查）；--write-manifest 时下方统一要求
            err = validate_relative_member(value, field=field)
            if err:
                print(f"[FAIL] {err}", file=sys.stderr)
                return 1

    if args.write_manifest and not (args.python_relative and args.script_relative):
        print("[FAIL] --write-manifest 必须同时给 --python-relative 与 --script-relative（C5 分发字段）", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="stp-pack-") as td:
        tar_tmp = Path(td) / "package.tar.gz"
        facts = build_deterministic_tar_gz(args.src, tar_tmp)
        payload = tar_tmp.read_bytes()  # 发布复用同一字节，不依赖「复跑必同 sha」的第二重保证

    print(json.dumps({"name": args.name, "version": args.version, **facts}, ensure_ascii=False, sort_keys=True))
    if args.dry_run:
        return 0

    if args.write_manifest:
        doc = load_manifest(args.manifest)
        doc, appended = register_entry(
            doc, args.name, args.version, facts["package_sha256"],
            artifact_path_for(args.name, args.version), args.python_relative, args.script_relative,
        )
        if appended:
            dump_manifest(doc, args.manifest)
            print(f"[OK] 已登记 {args.name}@{args.version} → {args.manifest}", file=sys.stderr)
        else:
            print(f"[OK] {args.name}@{args.version} 已登记且 sha 一致（幂等）", file=sys.stderr)
    else:
        if args.packages_root and not args.manifest.exists():
            print(
                "[FAIL] 站点派生副本只能从 Git manifest 生成（C2）：先 --write-manifest 登记，"
                f"或让 {args.manifest} 已存在再发布",
                file=sys.stderr,
            )
            return 1
        doc = load_manifest(args.manifest) if args.manifest.exists() else None

    if args.packages_root:
        assert doc is not None
        dest = args.packages_root / f"{args.name}/{args.version}.tar.gz"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        write_site_manifest_copy(doc, args.packages_root)
        print(f"[OK] 包已发布 {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
