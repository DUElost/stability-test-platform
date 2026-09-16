"""Read deployed Agent code revision from VERSION file."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# ADR-0040 D2：ARTIFACT_DIGEST 由部署流程受控写入，格式 sha256:<64 hex>。
_ARTIFACT_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# #2016：kind → 文件名的**全集**。原先是 `"code" if kind == "code" else 另一个`，
# 于是任何拼写错误（`"resource"`/`"full"`/空串）都静默去读 resources 那份身份——
# 报出去的是「另一个真实存在的摘要」，控制面会据此算出假的 aligned/drift，
# 比读不到更坏。现在只认表里的键，未知键按缺失处理并说出来。
_ARTIFACT_DIGEST_FILES: dict[str, str] = {
    "code": "ARTIFACT_DIGEST",
    "resources": "ARTIFACT_DIGEST_RESOURCES",
}
#: 身份文件的候选目录（源码态 → 部署态）。提出为常量是为了让「表外 kind 不读另一份身份」
#: 有用例可钉（原先目录对写死在函数体内，测试无从注入）。
_ARTIFACT_DIGEST_DIRS: tuple[Path, ...] = (
    Path(__file__).resolve().parent,
    Path("/opt/stability-test-agent/agent"),
)


def read_agent_code_revision() -> str:
    """Return git short SHA written by hot-update, or '' if unavailable."""
    candidates = [
        Path(__file__).resolve().parent / "VERSION",
        Path("/opt/stability-test-agent/agent/VERSION"),
    ]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            first_line = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
            if first_line:
                return first_line.split()[0]
        except OSError:
            continue
    return ""


def read_artifact_digest(kind: Literal["code", "resources"] = "code") -> str:
    """Return the deployed artifact digest (ADR-0040), or '' if unavailable/invalid.

    与 read_agent_code_revision 同候选路径、同信任模型（部署流程是唯一合法
    写入者）；格式不合法按缺失处理（心跳上报空值，控制面按 drift 收敛）。
    ``kind``（#1963，P2 身份分层）：code → ARTIFACT_DIGEST；
    resources → ARTIFACT_DIGEST_RESOURCES；表外的 kind 视为读不到（返回 '' 并 warning），
    绝不退化成「读另一份身份」（#2016）。
    """
    filename = _ARTIFACT_DIGEST_FILES.get(kind)
    if filename is None:
        logger.warning(
            "read_artifact_digest: unknown kind %r (known: %s); reporting missing "
            "instead of guessing another artifact identity",
            kind, sorted(_ARTIFACT_DIGEST_FILES),
        )
        return ""
    for directory in _ARTIFACT_DIGEST_DIRS:
        path = directory / filename
        try:
            if not path.is_file():
                continue
            first_line = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
            if _ARTIFACT_DIGEST_RE.match(first_line):
                return first_line
        except OSError:
            continue
    return ""
