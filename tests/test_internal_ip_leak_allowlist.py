"""#1655：device_serial.py 不得再以整文件白名单绕过内网泄漏门禁。

回归背景：`backend/core/device_serial.py` 曾在 ALLOWLIST_PREFIXES 中整文件
放行（#1356），导致该文件此后在 CI / pre-commit 完全不被扫描——将来加入
真实内网地址或序列号也不会被拦。修复：改为 SAFE_TOKENS 逐 token 放行
（``0123456789ABCDEF`` 是公开占位常量），文件本体恢复受扫描覆盖。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "check_internal_ip_leak",
    REPO_ROOT / "tools" / "dev" / "check-internal-ip-leak.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["check_internal_ip_leak"] = _mod
_spec.loader.exec_module(_mod)


def test_device_serial_not_whole_file_allowlisted():
    rel = "backend/core/device_serial.py"
    assert not _mod._is_allowlisted(rel), (
        "device_serial.py 不得整文件放行——否则该文件永远不被扫描（#1655）"
    )


def test_device_serial_content_scans_clean():
    text = (REPO_ROOT / "backend/core/device_serial.py").read_text(encoding="utf-8")
    hits = _mod.scan_text(text, "backend/core/device_serial.py")
    assert hits == [], f"占位常量应经 SAFE_TOKENS 放行，实际命中: {hits}"


def test_placeholder_token_is_safe_token():
    assert "0123456789ABCDEF" in _mod.SAFE_TOKENS
