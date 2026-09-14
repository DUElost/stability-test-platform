"""Read deployed Agent code revision from VERSION file."""

from __future__ import annotations

import re
from pathlib import Path

# ADR-0040 D2：ARTIFACT_DIGEST 由部署流程受控写入，格式 sha256:<64 hex>。
_ARTIFACT_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


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


def read_artifact_digest(kind: str = "code") -> str:
    """Return the deployed artifact digest (ADR-0040), or '' if unavailable/invalid.

    与 read_agent_code_revision 同候选路径、同信任模型（部署流程是唯一合法
    写入者）；格式不合法按缺失处理（心跳上报空值，控制面按 drift 收敛）。
    ``kind``（#1963，P2 身份分层）：code → ARTIFACT_DIGEST；
    resources → ARTIFACT_DIGEST_RESOURCES。
    """
    filename = "ARTIFACT_DIGEST" if kind == "code" else "ARTIFACT_DIGEST_RESOURCES"
    candidates = [
        Path(__file__).resolve().parent / filename,
        Path("/opt/stability-test-agent/agent/") / filename,
    ]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            first_line = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
            if _ARTIFACT_DIGEST_RE.match(first_line):
                return first_line
        except OSError:
            continue
    return ""
