"""Tests for parse_exp_main_summary process/package fallback (ExpMain.analyse 对齐).

agent/aee/metadata.py 是唯一事实源;core/aee_metadata.py 是薄 re-export。
参数化同时跑两个入口,守卫 re-export 接线不被破坏。
"""

from __future__ import annotations

import pytest

import backend.agent.aee.metadata as agent_metadata
import backend.core.aee_metadata as core_metadata


@pytest.fixture(params=[agent_metadata, core_metadata], ids=["agent", "core"])
def metadata_mod(request):
    return request.param


def test_core_module_reexports_agent_functions():
    assert core_metadata.parse_exp_main_summary is agent_metadata.parse_exp_main_summary
    assert core_metadata.normalize_aee_subtype is agent_metadata.normalize_aee_subtype
    assert core_metadata.normalize_package_name is agent_metadata.normalize_package_name
    assert (
        core_metadata.infer_aee_subtype_from_paths
        is agent_metadata.infer_aee_subtype_from_paths
    )


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


# ---------------------------------------------------------------------------
# ZZ_INTERNAL parsing (count_dbg_process.py aligned)
# ---------------------------------------------------------------------------


def _write_zz_internal(tmp_path, content: str) -> None:
    (tmp_path / "ZZ_INTERNAL").write_text(content, encoding="utf-8")


def test_zz_internal_je_parses_process(metadata_mod, tmp_path):
    _write_zz_internal(tmp_path, "Java (JE),field1,field2,field3,field4,field5,field6,com.example.app,")

    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result["event_subtype"] == "JE"
    assert result["package_name"] == "com.example.app"


def test_zz_internal_ne_parses_process(metadata_mod, tmp_path):
    _write_zz_internal(tmp_path, "Native (NE),f1,f2,f3,f4,f5,f6,com.test.app:push,")

    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result["event_subtype"] == "NE"
    assert result["package_name"] == "com.test.app"


def test_zz_internal_ke_process_ke(metadata_mod, tmp_path):
    _write_zz_internal(tmp_path, "Kernel (KE),f1,f2,f3,f4,f5,f6,KE at 0x1234,")

    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result["event_subtype"] == "KE"
    assert result["package_name"] == "PROCESS_KE"


def test_zz_internal_external_ee_modem(metadata_mod, tmp_path):
    _write_zz_internal(tmp_path, "External (EE),modem,")

    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result["package_name"] == "modem"


def test_zz_internal_external_ee_combo(metadata_mod, tmp_path):
    _write_zz_internal(tmp_path, "External (EE),combo,")

    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result["package_name"] == "combo"


def test_zz_internal_takes_priority_over_exp_main(metadata_mod, tmp_path):
    _write_zz_internal(tmp_path, "Java (JE),f1,f2,f3,f4,f5,f6,com.from.zz,")
    _write_exp_main(tmp_path, "Exception Class: Native (NE)\nProcess: com.from.exp\n")

    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result["event_subtype"] == "JE"
    assert result["package_name"] == "com.from.zz"


def test_zz_internal_missing_falls_back_to_exp_main(metadata_mod, tmp_path):
    _write_exp_main(tmp_path, "Exception Class: Native (NE)\nProcess: com.from.exp\n")

    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result["event_subtype"] == "NE"
    assert result["package_name"] == "com.from.exp"


def test_zz_internal_empty_returns_empty(metadata_mod, tmp_path):
    _write_zz_internal(tmp_path, "")

    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result == {}


def test_zz_internal_missing_returns_empty(metadata_mod, tmp_path):
    result = metadata_mod.parse_exp_main_summary(tmp_path)

    assert result == {}
