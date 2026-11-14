"""证据区目录契约——命名、相对路径与无损复制（ADR-0062 D9 第 6 条，B8 G6）。

**为什么是契约模块**：证据区由 Agent 写入、控制面校验与消费，两侧必须对
“同一身份对应哪个相对路径”给出逐字相同的答案。Agent 侧经同包相对导入
（from .contracts.evidence_layout import ...），控制面侧绝对导入
（backend.agent.contracts.evidence_layout）。模块体只依赖标准库，import 期无 I/O。

**命名输入的来源**：D9 第 6 条要求 {项目名}_{软件版本缩写}_{测试开始时间戳}
取自设备属性。G6 不重读当前设备属性（设备可能已刷机、离线或 Agent 重启），
而是使用 DLE 在**采集登记时固化的** local_path：Agent 的 AEE 采集器当时用
get_aee_log_folder_name（离线工具 _get_aee_log_folder_name 规则）把 getprop
结果固化成目录名，local_path 作为 DLE 的不可变字段保留该派生结果。本模块只
校验该目录名的形状并原样复用；属性来源缺失（路径不含该分量）由上层记为
显式失败，不猜测、不回落到设备当前状态。

目录布局（D9 第 6 条）::

    {证据区根}/
    └── {主PlanRun编号}_{采集时目录名}/{SN}/{事件目录名}

采集时目录名形如 {product}_{version}_{timestamp}[_SU][_MMDD]_MonkeyAEEinfo，
由 get_aee_log_folder_name 产出。本模块不重新实现该规则，只校验已固化的结果。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Optional

from .aee_event_dirs import is_event_dir_basename

#: 证据区顶层族名；与 backend.storage_families.EVIDENCE_FAMILY 同字面量。
EVIDENCE_ROOT = "evidence"

#: 命名规则版本。规则实现是 backend.agent.aee.folder_name.get_aee_log_folder_name
#: （对齐 automation-toolkit 的 _get_aee_log_folder_name）。
#: 发布记录把该版本与固化目录名一起持久化；规则演进只能新增版本，不改已有规则。
NAMING_RULE_VERSION = "aee-folder-name/v1"

# 产品名允许点号（如 MLD.NX3，历史命名）；禁止空名、路径分隔符与 ..。
_FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*_MonkeyAEEinfo$")
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_EVENT_SUBDIRS = frozenset({"aee_exp", "vendor_aee_exp"})


class EvidenceLayoutError(ValueError):
    """证据区路径或目录名不符合 D9 契约。"""


def _require_component(value: str, field: str) -> str:
    if not value or value in (".", "..") or not _SAFE_COMPONENT_RE.match(value):
        raise EvidenceLayoutError(f"{field} is not a safe path component: {value!r}")
    return value


def validate_collection_folder(name: str) -> str:
    """校验采集时固化的目录名（get_aee_log_folder_name 的输出）。"""
    if not name or not _FOLDER_RE.match(name):
        raise EvidenceLayoutError(
            f"collection folder is not an AEE folder name: {name!r}"
        )
    return name


def validate_event_dir(name: str) -> str:
    """校验事件目录名（设备上的原名，形如 {时间戳}_db.{XX}）。"""
    if not name or not is_event_dir_basename(name):
        raise EvidenceLayoutError(f"event dir name is not an AEE event dir: {name!r}")
    return name


def collection_folder_from_event_path(
    local_path: str,
    *,
    serial: str,
    event_dir: Optional[str] = None,
) -> tuple[str, str]:
    """从采集时固化的 DLE.local_path 取 (采集目录名, 事件目录名)。

    布局必须是 {local_root}/{采集目录名}/{serial}/{aee_exp|vendor_aee_exp}/{事件目录名}。
    这证明命名输入来自该事件采集时的设备属性：目录名是采集器当时用 getprop
    推导并落盘的结果，之后设备刷机、离线或 Agent 重启都不改变它。

    属性来源缺失（watcher 的 jobs/ 布局、历史异形路径、serial 不符）一律抛出，
    由发布服务记为显式终态失败。
    """
    _require_component(serial, "serial")
    raw = str(local_path or "")
    if not raw or not Path(raw).is_absolute():
        raise EvidenceLayoutError(f"local_path must be absolute: {raw!r}")
    parts = Path(raw).parts
    if len(parts) < 5:
        raise EvidenceLayoutError(f"local_path has no AEE collection layout: {raw!r}")
    leaf = parts[-1]
    event_dir = validate_event_dir(event_dir or leaf)
    if leaf != event_dir:
        raise EvidenceLayoutError(
            f"local_path event dir mismatch: {leaf!r} != {event_dir!r}"
        )
    if parts[-2] not in _EVENT_SUBDIRS:
        raise EvidenceLayoutError(
            f"local_path is not under aee_exp/vendor_aee_exp: {raw!r}"
        )
    if parts[-3] != serial:
        raise EvidenceLayoutError(
            f"local_path serial mismatch: {parts[-3]!r} != {serial!r}"
        )
    return validate_collection_folder(parts[-4]), event_dir


def primary_folder_name(root_plan_run_id: int, collection_folder: str) -> str:
    """主文件夹名：{主PlanRun编号}_{采集时目录名}（同一链根共享物理父目录）。"""
    if type(root_plan_run_id) is not int or root_plan_run_id <= 0:
        raise EvidenceLayoutError("root_plan_run_id must be a positive int")
    folder = f"{root_plan_run_id}_{validate_collection_folder(collection_folder)}"
    if len(folder) > 255:
        raise EvidenceLayoutError(f"primary folder too long: {len(folder)}")
    return folder


def evidence_rel_path(
    *,
    root_plan_run_id: int,
    collection_folder: str,
    serial: str,
    event_dir: str,
) -> str:
    """构造相对日志根的完整证据路径（POSIX 分隔符，用于持久化与回执）。"""
    primary = primary_folder_name(root_plan_run_id, collection_folder)
    _require_component(serial, "serial")
    validate_event_dir(event_dir)
    return "/".join((EVIDENCE_ROOT, primary, serial, event_dir))


def parse_evidence_rel_path(
    rel_path: str,
    *,
    root_plan_run_id: Optional[int] = None,
    serial: Optional[str] = None,
    event_dir: Optional[str] = None,
) -> tuple[str, str, str, str]:
    """解析并校验证据相对路径；可选地把三段身份与期望值逐字比对。

    返回 (evidence, 主文件夹, SN, 事件目录名)。越根、缺段、多段、反斜杠、
    绝对路径、错误的链根前缀、错误 SN 与错误事件目录都在这里拒绝。
    """
    raw = str(rel_path or "")
    if not raw or "\\" in raw or raw.startswith("/"):
        raise EvidenceLayoutError(f"evidence path must be relative posix: {raw!r}")
    parts = PurePosixPath(raw).parts
    if len(parts) != 4 or parts[0] != EVIDENCE_ROOT:
        raise EvidenceLayoutError(f"evidence path must be evidence/<primary>/<SN>/<event>: {raw!r}")
    primary, sn, name = parts[1], parts[2], parts[3]
    prefix, _, folder = primary.partition("_")
    if not prefix.isdigit() or int(prefix) <= 0:
        raise EvidenceLayoutError(f"primary folder has no run prefix: {primary!r}")
    if root_plan_run_id is not None and int(prefix) != int(root_plan_run_id):
        raise EvidenceLayoutError(
            f"primary folder belongs to another chain root: {prefix} != {root_plan_run_id}"
        )
    validate_collection_folder(folder)
    _require_component(sn, "serial")
    if serial is not None and sn != serial:
        raise EvidenceLayoutError(f"evidence serial mismatch: {sn!r} != {serial!r}")
    validate_event_dir(name)
    if event_dir is not None and name != event_dir:
        raise EvidenceLayoutError(f"evidence event dir mismatch: {name!r} != {event_dir!r}")
    return (parts[0], primary, sn, name)


def dir_sha256_and_size(root: Path | str) -> tuple[str, int]:
    """目录 sha256 与字节总量：相对路径 + 文件字节，按排序遍历（单遍）。

    与 EventUploader 的历史实现逐字一致（目录内容身份）。Agent 自校验与
    控制面复核必须使用同一算法，否则同一份证据会得到两个哈希。
    """
    base = Path(root)
    digest = hashlib.sha256()
    size = 0
    for path in sorted(base.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(base)).encode())
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
                    size += len(chunk)
    return digest.hexdigest(), size


def assert_no_symlinks(root: Path | str) -> None:
    """事件目录必须是普通目录树；任何 symlink 都拒绝（证据边界）。"""
    base = Path(root)
    if base.is_symlink():
        raise EvidenceLayoutError(f"symlink not allowed: {base}")
    for entry in base.rglob("*"):
        if entry.is_symlink():
            raise EvidenceLayoutError(f"symlink not allowed under event dir: {entry}")


def ensure_dir_under_root(root: Path | str, parts: tuple[str, ...] | list[str]) -> Path:
    """在 root 下逐段创建目录，拒绝任何已存在的 symlink 分量。"""
    base = Path(root)
    if not base.is_dir() or base.is_symlink():
        raise EvidenceLayoutError(f"root is not a plain directory: {base}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(base, flags)
    try:
        for part in parts:
            _require_component(part, "path component")
            try:
                os.mkdir(part, mode=0o755, dir_fd=fd)
            except FileExistsError:
                pass
            try:
                next_fd = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                raise EvidenceLayoutError(
                    f"path component is not a plain directory: {part!r}"
                ) from exc
            os.close(fd)
            fd = next_fd
    finally:
        os.close(fd)
    return base.joinpath(*parts)


def copytree_no_symlinks(
    src: Path | str,
    dest: Path | str,
    *,
    dest_root: Path | str,
) -> None:
    """把事件目录复制到一个**新**叶子目录；源与目的都不接受 symlink。

    dest 必须尚不存在（O_EXCL 语义）：调用方先写同目录临时叶子，校验后
    rename 到正式目的路径，因此正式目的路径不会是“写到一半”的目录。
    父目录经 ensure_dir_under_root 逐段无 symlink 打开。
    """
    src_path = Path(src)
    dest_path = Path(dest)
    root_path = Path(dest_root)
    if src_path.is_symlink() or not src_path.is_dir():
        raise EvidenceLayoutError(f"source is not a plain directory: {src_path}")
    try:
        dest_rel = dest_path.relative_to(root_path)
    except ValueError as exc:
        raise EvidenceLayoutError(f"destination is not under {root_path}: {dest_path}") from exc
    if dest_rel.is_absolute() or ".." in dest_rel.parts or not dest_rel.parts:
        raise EvidenceLayoutError(f"destination escapes root: {dest_path}")
    ensure_dir_under_root(root_path, list(dest_rel.parts[:-1]))
    leaf = dest_rel.parts[-1]
    _require_component(leaf, "destination leaf")
    try:
        os.mkdir(dest_path, mode=0o755)
    except FileExistsError as exc:
        raise FileExistsError(f"destination already exists: {dest_path}") from exc

    try:
        for current, dirs, files in os.walk(src_path, topdown=True, followlinks=False):
            current_path = Path(current)
            rel = current_path.relative_to(src_path)
            for name in dirs:
                source_entry = current_path / name
                if source_entry.is_symlink():
                    raise EvidenceLayoutError(
                        f"symlink not allowed under event dir: {source_entry}"
                    )
                (dest_path / rel / name).mkdir(mode=0o755)
            for name in files:
                source_entry = current_path / name
                if source_entry.is_symlink() or not source_entry.is_file():
                    raise EvidenceLayoutError(
                        f"unsupported file type under event dir: {source_entry}"
                    )
                shutil.copyfile(source_entry, dest_path / rel / name)
    except BaseException:
        shutil.rmtree(dest_path, ignore_errors=True)
        raise
