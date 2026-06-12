"""Tests for parse_exp_main_summary process/package fallback (ExpMain.analyse 对齐).

agent/aee/metadata.py 与 core/aee_metadata.py 是双副本(热更新隔离),
参数化同时跑两份,兼作行为漂移守卫。
"""

from __future__ import annotations

import pytest

import backend.agent.aee.metadata as agent_metadata
import backend.core.aee_metadata as core_metadata


@pytest.fixture(params=[agent_metadata, core_metadata], ids=["agent", "core"])
def metadata_mod(request):
    return request.param


def _write_exp_main(tmp_path, content: str) -> None:
    (tmp_path / "__exp_main.txt").write_text(content, encoding="utf-8")


def test_kernel_class_fills_process_when_missing(metadata_mod, tmp_path):
    _write_exp_main(tmp_path, "Exception Class: Kernel (KE)\nException Type: KE\n")

    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result["event_subtype"] == "KE"
    assert result["current_process"] == "Kernel (KE)"
    assert result["package_name"] == "Kernel (KE)"


def test_hwt_class_fills_process_when_missing(metadata_mod, tmp_path):
    _write_exp_main(tmp_path, "Defect Class: HWT\n")

    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result["event_subtype"] == "HWT"
    assert result["current_process"] == "HWT"
    assert result["package_name"] == "HWT"


def test_external_ee_fills_process_from_exception_type(metadata_mod, tmp_path):
    _write_exp_main(
        tmp_path,
        "Exception Class: External (EE)\nException Type: Modem EE\n",
    )

    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result["event_subtype"] == "Modem EE"
    assert result["current_process"] == "Modem EE"
    assert result["package_name"] == "Modem EE"


def test_external_ee_exception_type_colon_suffix_stripped(metadata_mod, tmp_path):
    _write_exp_main(
        tmp_path,
        "Exception Class: External (EE)\nException Type: mediaserver:remote\n",
    )

    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result["event_subtype"] == "Combo EE"
    assert result["current_process"] == "mediaserver"
    assert result["package_name"] == "mediaserver"


def test_android_process_renamed_to_android_framework(metadata_mod, tmp_path):
    _write_exp_main(
        tmp_path,
        "Exception Class: Java (JE)\nProcess: android\n",
    )

    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result["event_subtype"] == "JE"
    assert result["current_process"] == "android_framework"
    assert result["package_name"] == "android_framework"


def test_user_app_process_and_package_unchanged(metadata_mod, tmp_path):
    _write_exp_main(
        tmp_path,
        "Defect Class: Native (NE)\n"
        "Exception Type: SIGSEGV\n"
        "Process: com.example.app:push\n"
        "Package: com.example.app\n",
    )

    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result["event_subtype"] == "NE"
    assert result["current_process"] == "com.example.app"
    assert result["package_name"] == "com.example.app"
