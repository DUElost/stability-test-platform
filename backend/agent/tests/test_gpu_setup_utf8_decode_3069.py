"""#3069：gpu_setup v1.2.3 —— adb / 设备输出宽容解码，坏字节不再炸掉整窗。

判据来自 2026-09-22 的生产回归：v1.2.2 的 ``adb()`` 用
``subprocess.run(..., text=True)`` 严格解码，设备侧一个非 UTF-8 字节
（实测 ``0xf9``，位置 1）就抛 ``UnicodeDecodeError``——它不是 ``OSError``，
调用点无从兜住，于是 462/487 台倒在「读 instrument 日志」这一步，
整窗 init 全灭（run 496 / 500）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tools.dev.source_anchor import SourceGuard

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
V123 = SCRIPTS / "gpu_setup" / "v1.2.3"
V122 = SCRIPTS / "gpu_setup" / "v1.2.2"

LIB_REL = "backend/agent/scripts/gpu_setup/v1.2.3/_lib.py"
SETUP_REL = "backend/agent/scripts/gpu_setup/v1.2.3/gpu_setup.py"

#: v1.2.2 的崩溃入口形态：``subprocess.run(..., text=True, timeout=...)``。
#: 绑完整形参片段，避开模块 docstring / 注释里对旧写法的正当提及。
STRICT_DECODE_SHAPE = "text=True, timeout="

#: 生产实测的坏字节形态：位置 1 是 0xf9（UTF-8 里 0xf9 从不作首字节，也不合法）。
BROKEN_DEVICE_OUTPUT = b"\x02\xf9 INSTRUMENTATION_STATUS: class=com.transsion\n"


def _load_lib(version_dir: Path, name: str):
    sys.path.insert(0, str(version_dir))
    spec = importlib.util.spec_from_file_location(name, version_dir / "_lib.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_broken_bytes_are_the_real_regression_shape():
    """反例自证：同一段字节走严格解码必抛——这就是 v1.2.2 的失败形态。"""
    with pytest.raises(UnicodeDecodeError):
        BROKEN_DEVICE_OUTPUT.decode("utf-8")


def test_tolerant_decode_accepts_broken_bytes():
    lib = _load_lib(V123, "gpu_lib_v123_decode")
    out = lib.decode_device_output(BROKEN_DEVICE_OUTPUT)
    assert out.startswith("\x02�")          # 坏字节 → U+FFFD，不抛
    assert "INSTRUMENTATION_STATUS" in out       # 其余内容照常可读
    assert lib.decode_device_output(None) == ""  # 空输出不是异常


def test_signature_classification_survives_broken_bytes():
    """宽容解码的**目的**：坏字节不该让「早发现不兼容」这件事失效。"""
    lib = _load_lib(V123, "gpu_lib_v123_classify")
    blob = lib.decode_device_output(
        BROKEN_DEVICE_OUTPUT
        + b"java.lang.IllegalStateException: UiAutomationService already registered!\n"
    )
    assert lib.classify_compat_failure(blob) == "uiautomation_already_registered"


def test_v123_no_longer_decodes_strictly():
    """静态守卫：v1.2.3 的 adb 路径不得再出现严格解码（崩溃入口）。

    以源码形态钉住——用例跑不了真 adb，但「用没用严格解码」是可判定的。
    判据绑 ``text=True, timeout=`` 完整形参片段（v1.2.2 原形），避开散文提及。
    """
    lib = SourceGuard.of_repo_path(LIB_REL).anchored("def adb(")
    lib.assert_absent(
        STRICT_DECODE_SHAPE,
        why="#3069：严格解码会把坏字节炸成 init 失败",
    )
    lib.assert_present(
        "decode_device_output(",
        why="#3069：adb 必须走宽容解码",
    )

    setup = SourceGuard.of_repo_path(SETUP_REL).anchored("def _pre_reboot_device(")
    setup.assert_absent(
        STRICT_DECODE_SHAPE,
        why="#3069：getprop 直连同样不得严格解码",
    )
    setup.assert_present(
        "decode_device_output(",
        why="#3069：boot_completed 走宽容解码",
    )


def test_v122_kept_the_old_strict_decode_for_the_record():
    """旧版本按不可变契约保持原样——证明修的是「新版本」，不是原地改。"""
    assert "text=True" in (V122 / "_lib.py").read_text(encoding="utf-8")
