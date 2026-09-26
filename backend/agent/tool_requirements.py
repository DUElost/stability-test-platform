"""ADR-0051 v1.7 D7：脚本包声明的工具依赖（``capabilities.json`` 的 ``requires_tools``）。

纯标准库、无包内相对导入——Agent 运行时（``script_packages``）与仓库门禁
（``tools/dev/check_script_packages.py`` 按文件路径加载）共用同一份判据，
避免「门禁放行、运行时拒绝」的两套口径。

声明形态（脚本族树 / 脚本包根的 ``capabilities.json`` 顶层键）::

    {
      "capabilities": ["progress_stamps"],
      "requires_tools": {
        "flashtool": {"version": "1.2444.00.100", "env": "STP_FLASH_TOOL_DIR"}
      }
    }

语义：引擎执行该脚本版本前，按 ``(name, version)`` 经 ``tools_cache`` 核验拉取 ``kind=tool``
包，把**包根目录**注入本步子进程 env 的 ``env`` 键——不写主机 ``.env``、不新增主机 env 键
（ADR-0033 §5.4 例外③）。工具版本随脚本版本内容寻址：换工具版本 = 发脚本新版本。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

CAPABILITIES_FILE = "capabilities.json"
REQUIRES_KEY = "requires_tools"

#: 与 ``tool_manifest.json`` 族名同形（``tools_cache/<name>/<version>/`` 是路径段，必须保守）。
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: 版本同为路径段：不得含 ``/``、不得是 ``.``/``..``。
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
#: 注入键只允许 ``*_DIR`` 形态——语义就是「一个目录」，也把可写面收窄到不会撞 PATH/LD_* 之类。
_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*_DIR$")
#: 引擎自有的 ``*_DIR`` 步骤键（``pipeline_engine`` 先写入）——包内声明不得覆写引擎语义。
RESERVED_ENV_KEYS = frozenset({"STP_LOG_DIR", "STP_AGENT_INSTALL_DIR"})


class RequirementError(ValueError):
    """``requires_tools`` 声明形态非法（门禁判红 / 运行时步骤 exit 2）。"""


@dataclass(frozen=True)
class ToolRequirement:
    name: str
    version: str
    env: str


def parse_requirements(doc: object) -> list[ToolRequirement]:
    """已解析的 ``capabilities.json`` 文档 → 依赖列表（按族名排序）；无声明 → ``[]``。"""
    if not isinstance(doc, dict) or REQUIRES_KEY not in doc:
        return []
    raw = doc[REQUIRES_KEY]
    if not isinstance(raw, dict):
        raise RequirementError(f"{REQUIRES_KEY} 必须是对象（族名 → {{version, env}}）")
    out: list[ToolRequirement] = []
    seen_env: dict[str, str] = {}
    for name in sorted(raw):
        spec = raw[name]
        if not isinstance(name, str) or not _NAME_RE.match(name) or name in (".", ".."):
            raise RequirementError(f"工具族名非法：{name!r}")
        if not isinstance(spec, dict) or set(spec) != {"version", "env"}:
            raise RequirementError(f"{name}：声明必须恰为 {{version, env}} 两键，实为 {spec!r}")
        version, env = spec["version"], spec["env"]
        if not isinstance(version, str) or not _VERSION_RE.match(version) or version in (".", ".."):
            raise RequirementError(f"{name}：version 非法 {version!r}")
        if not isinstance(env, str) or not _ENV_RE.match(env):
            raise RequirementError(f"{name}：env 须为 *_DIR 形态的大写键，实为 {env!r}")
        if env in RESERVED_ENV_KEYS:
            raise RequirementError(f"{name}：env {env} 是引擎自有键，不得由包声明覆写")
        if env in seen_env:
            raise RequirementError(f"{name}：env {env} 已被 {seen_env[env]} 占用")
        seen_env[env] = name
        out.append(ToolRequirement(name=name, version=version, env=env))
    return out


def load_requirements(package_root: Path) -> list[ToolRequirement]:
    """读包根 / 族树根的 ``capabilities.json``；文件缺失 → ``[]``，存在但坏 → ``RequirementError``。

    与控制面 ``read_capabilities`` 的「坏文件当无能力」不同：依赖声明坏了就是**不可执行**，
    不能静默当成「无依赖」放行（否则脚本拿不到工具目录，失败点后移到工具调用处）。
    """
    path = package_root / CAPABILITIES_FILE
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RequirementError(f"{CAPABILITIES_FILE} 不可读：{exc}") from exc
    return parse_requirements(doc)
