"""B10 U4 刷机脚本测试用：把 ``ensure_firmware`` 换成恒等本地目录上下文。

既有 ADR-0063 / 展锐刷机单测不关心真实 L1 落盘；默认用本工具把
``local_dir`` 指回 ``source_dir``，避免 ``insufficient_local_cache_space``。
U4 必测反例自行覆盖 ``_load_firmware_cache_api`` / ``ensure_firmware``。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Optional


class FakeFirmwareCacheError(Exception):
    def __init__(self, code: str, message: str = "", *, retryable: bool = False) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.retryable = bool(retryable)


@contextmanager
def identity_ensure_firmware(
    source_dir: str | Path,
    profile: str,
    *,
    progress_callback=None,
    allow_nfs_direct_fallback: bool = False,
    allow_permit_bypass: bool = False,
    **_kwargs: Any,
) -> Iterator[SimpleNamespace]:
    del allow_nfs_direct_fallback, allow_permit_bypass
    if progress_callback is not None:
        progress_callback(stage="firmware-stage-wait", wait_for="test")
        progress_callback(stage="firmware-stage-copy", copied_bytes=0)
    src = Path(source_dir)
    yield SimpleNamespace(
        local_dir=src,
        cache_key=f"test_{profile}_{src.name}",
        sealed=True,
        content_digest="sha256:" + ("ab" * 32),
        profile=profile,
        tier="primary",
        hit=True,
        total_bytes=0,
        fallback_reason=None,
        permit_degraded=False,
    )


def install_identity_firmware_cache(monkeypatch, mod, *, arity: Optional[int] = None) -> None:
    """把 ``mod._load_firmware_cache_api`` 换成恒等 ensure。

    ``arity``：返回元组长度。``None`` 时按函数注解/默认探测——MTK/BSL 为 3，
    Unisoc Fastboot 为 4。
    """
    if arity is None:
        name = getattr(mod, "__name__", "") or ""
        arity = 4 if "unisoc" in name and "bsl" not in name else 3

    def _load():
        if arity == 4:
            return identity_ensure_firmware, FakeFirmwareCacheError, 1, None
        return identity_ensure_firmware, FakeFirmwareCacheError, None

    monkeypatch.setattr(mod, "_load_firmware_cache_api", _load)
