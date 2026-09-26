"""ADR-0051 Phase 2b：``script:<name>`` 步骤按 **包身份** 解析到本机 ``tools_cache``。

「DB 权威 → 包身份」这一环（ADR-0051 D4）：``ScriptRegistry`` 解析出的条目若带
``package_sha256``（控制面 scan 从 ``tool_manifest.json`` 回填），就经 ``tool_cache.ensure_package``
把 ``packages/{name}/{version}.tar.gz`` 拉到 ``tools_cache/{name}/{version}/`` 并整包核验，
脚本从包内执行；任何不可用（行无包 sha / 包源缺失 / sha 不符 / 包内无入口）=
``PackageUnavailable`` → 步骤 exit 2 显式失败——**无 tree 回退**（回退目标已随 Phase 3 删除）。

开关 ``STP_SCRIPT_PACKAGES``（Agent 侧；控制面源键 ``STP_AGENT_SCRIPT_PACKAGES`` 经
既有 env 推送链下发）：

- **缺省（未配置）= ``strict``**（2026-09-24 落地审查修正）：Phase 3 已删除
  ``agent/scripts/`` 版本目录，``off``/``on`` 的 tree 回退目标**已不存在**——新装/重装/
  漏配主机若默认 off 会静默走死路径。包不可用现在必须显式失败，不能假装还有退路。
- ``off`` / ``on`` 仍被解析但**只作过渡兼容**（等价 strict 的告警别名）：存量 fleet 的
  ``.env`` 里有显式 ``STP_SCRIPT_PACKAGES=strict``，无行为变化；两模式随台账
  ``script-packages-off-on-modes`` 到期删除解析分支。
- ``strict``：只走包，失败 = ``PackageUnavailable``（步骤 exit 2）。

三处形态耦合的出口（ADR-0051 D4）：cwd = 包解压根；PYTHONPATH 注入的 agent 目录不再由
``nfs_path`` 的 ``parents[3]`` 推导；终止宽限按脚本 **名** 判定。这些在 ``pipeline_engine``
落地，本模块只负责「解析到哪个路径、来源是什么」。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, MutableMapping, Optional

from . import tool_cache
from .tool_requirements import RequirementError, load_requirements

logger = logging.getLogger(__name__)

SWITCH_ENV = "STP_SCRIPT_PACKAGES"


class PackageUnavailable(Exception):
    """``strict`` 模式下包不可用（拉取/核验失败或包内无入口）。"""


@dataclass(frozen=True)
class ResolvedScript:
    path: str      #: 实际执行的入口文件绝对路径（恒为包内）
    cwd: str       #: 子进程工作目录 = 包解压根
    source: str = "package"  #: 恒 "package"——tree 回退目标已随 Phase 3 版本目录删除


def package_mode(env: Optional[Mapping[str, str]] = None) -> str:
    """纯函数：``STP_SCRIPT_PACKAGES`` → ``strict``（缺省）；``off``/``on`` 为过渡别名 → strict + WARNING。

    2026-09-24：tree 回退目标（agent/scripts/ 版本目录）已随 Phase 3 消失，回退语义不再存在，
    配置了 off/on 的主机视同 strict——但保留一次告警让漏配/旧配置在日志里可见。
    """
    raw = ((env if env is not None else os.environ).get(SWITCH_ENV) or "").strip().lower()
    if raw in ("", "strict"):
        return "strict"
    logger.warning("script_packages_mode_%s_is_retired_fallback_tree_deleted → strict（台账 script-packages-off-on-modes）", raw)
    return "strict"


def _entry_basename(nfs_path: str) -> str:
    return os.path.basename((nfs_path or "").replace("\\", "/"))


def resolve_script_path(entry, env: Optional[Mapping[str, str]] = None) -> ResolvedScript:
    """把 ``ScriptEntry`` 解析为可执行路径。

    ``entry`` 只需 ``name`` / ``version`` / ``nfs_path`` / ``package_sha256``（可缺省为 None）。
    永不抛出（``strict`` 例外：抛 ``PackageUnavailable``）。
    """
    environ = env if env is not None else os.environ
    package_mode(environ)  # 归一（恒 strict）+ 旧值告警
    sha = getattr(entry, "package_sha256", None)
    if not sha:
        raise PackageUnavailable(f"{entry.name}@{entry.version}: no_package_sha")
    packages_root = tool_cache._packages_root(environ)
    cache_root = tool_cache._cache_root(environ)
    if not packages_root or not cache_root:
        raise PackageUnavailable(f"{entry.name}@{entry.version}: roots_undefined")
    pkg_dir = tool_cache.ensure_package(entry.name, entry.version, str(sha), packages_root, cache_root)
    if not pkg_dir:
        raise PackageUnavailable(f"{entry.name}@{entry.version}: package_unavailable")
    script_abs = Path(pkg_dir) / _entry_basename(entry.nfs_path)
    if not script_abs.is_file():
        raise PackageUnavailable(f"{entry.name}@{entry.version}: entry_missing_in_package")
    return ResolvedScript(path=str(script_abs), cwd=str(pkg_dir))


def verify_package(entry: Mapping[str, object], env: Optional[Mapping[str, str]] = None) -> tuple[bool, Optional[str]]:
    """``verify_scripts`` RPC 的包侧判定：``(ok, error)``；``on``/``strict`` 且带 ``package_sha256`` 时才介入。

    成功即已把包预热进 ``tools_cache``（派发前拉包，步骤启动零等待）。
    """
    environ = env if env is not None else os.environ
    package_mode(environ)  # 旧值告警入口（结果恒 strict）
    sha = entry.get("package_sha256")
    if not sha:
        return True, None  # 行无包身份（历史 seed 未回填）：由调用方按文件 sha 判定
    packages_root = tool_cache._packages_root(environ)
    cache_root = tool_cache._cache_root(environ)
    if not packages_root or not cache_root:
        return False, "package_roots_undefined"
    name, version = str(entry.get("name") or ""), str(entry.get("version") or "")
    pkg_dir = tool_cache.ensure_package(name, version, str(sha), packages_root, cache_root)
    if not pkg_dir:
        return False, "package_unavailable"
    if not (Path(pkg_dir) / _entry_basename(str(entry.get("nfs_path") or ""))).is_file():
        return False, "package_entry_missing"
    # ADR-0051 v1.7 D7：声明的工具依赖一并核验预热——派发前把工具包（刷机工具 ~150MB）拉进
    # tools_cache，缺失在 precheck/presence 暴露，而不是等到步骤启动时才失败。
    tool_err = _resolve_required_tools(Path(pkg_dir), environ, None)
    if tool_err:
        return False, tool_err
    return True, None


def _resolve_required_tools(
    package_root: Path, environ: Mapping[str, str], inject_into: Optional[MutableMapping[str, str]]
) -> Optional[str]:
    """解析包根声明的全部工具依赖：成功返回 None（``inject_into`` 非空时写入各 env 键）；否则返回错误串。"""
    try:
        requirements = load_requirements(package_root)
    except RequirementError as exc:
        return f"required_tools_invalid: {exc}"
    for req in requirements:
        tool = tool_cache.resolve_packaged_tool_ref(req.name, req.version, environ)
        if tool is None or not tool.root:
            logger.error("required_tool_unavailable %s@%s package_root=%s", req.name, req.version, package_root)
            return f"required_tool_unavailable: {req.name}@{req.version}"
        if inject_into is not None:
            inject_into[req.env] = tool.root
            logger.info("required_tool_injected %s@%s %s=%s", req.name, req.version, req.env, tool.root)
    return None


def inject_required_tools(package_root: Path, env: MutableMapping[str, str]) -> Optional[str]:
    """ADR-0051 v1.7 D7：把脚本包声明的工具依赖核验拉取后注入**本步** env；失败返回错误串。

    fail-closed：声明坏 / 工具包缺失 / sha 不符都让步骤 exit 2（环境/工具错误，ADR-0033），
    **不**回退主机 env 里同名键——回退会让「包面坏了」被旧的主机路径掩盖，恰是本机制要消灭的形态。
    """
    return _resolve_required_tools(package_root, env, env)
