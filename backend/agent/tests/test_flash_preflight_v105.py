"""flash_preflight v1.0.5（#3075）：flashtool 解析在包模式下不断链。

v1.0.4 只按 ``script_dir/../../..`` 数相对深度——Phase 3 包模式（脚本从
``tools_cache/<name>/<ver>/`` 执行）下指向不存在的 ``<install>/resources``；
v1.0.5 优先 ``STP_FLASH_TOOL_DIR``（控制面 env_sync 注入）> ``STP_AGENT_INSTALL_DIR``
派生 > 旧相对 fallback。本文件加载**族树**（v1.0.5 字节），只测解析函数本身。
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_TREE = Path(__file__).resolve().parents[1] / "scripts" / "flash_preflight"


@pytest.fixture(scope="module")
def pf():
    spec = importlib.util.spec_from_file_location("flash_preflight_v105", _TREE / "flash_preflight.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _touch_exe(dir_: Path) -> Path:
    """建 flashtool 目录（含 flash_tool），返回**目录**路径（candidates 的输入是目录）。"""
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "flash_tool").write_text("#!/bin/sh\n", encoding="utf-8")
    return dir_


def test_env_key_wins(pf, tmp_path, monkeypatch):
    env_dir = _touch_exe(tmp_path / "envtool")
    _touch_exe(tmp_path / "install/agent/resources/flashtool/SP_Flash_Tool_Selector_exe_Linux_v1.2444.00.100")
    monkeypatch.setenv("STP_FLASH_TOOL_DIR", str(env_dir))
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    assert pf._locate_flashtool() == str(env_dir / "flash_tool")


def test_install_dir_fallback_when_no_env_key(pf, tmp_path, monkeypatch):
    exe_dir = _touch_exe(
        tmp_path / "install/agent/resources/flashtool/SP_Flash_Tool_Selector_exe_Linux_v1.2444.00.100"
    )
    monkeypatch.delenv("STP_FLASH_TOOL_DIR", raising=False)
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    assert pf._locate_flashtool() == str(exe_dir / "flash_tool")


def test_relative_fallback_still_last(pf, tmp_path, monkeypatch):
    """无两个 env 时旧相对候选仍在链尾（tree 模式/裸环境行为不倒退）。"""
    monkeypatch.delenv("STP_FLASH_TOOL_DIR", raising=False)
    monkeypatch.delenv("STP_AGENT_INSTALL_DIR", raising=False)
    cands = pf._flashtool_candidates()
    assert cands[-1] == os.path.normpath(
        os.path.join(str(_TREE), "..", "..", "..", "resources", "flashtool",
                     "SP_Flash_Tool_Selector_exe_Linux_v1.2444.00.100")
    )
    # 不断言 _locate_flashtool 的 None/命中：开发树 resources 真实存在、主机包模式不存在，
    # 结果依赖机器——只锁「候选链尾仍是旧相对路径」这一行为不倒退。


def test_candidate_order_is_env_install_relative(pf, monkeypatch):
    monkeypatch.setenv("STP_FLASH_TOOL_DIR", "/from-env")
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", "/from-install/")
    cands = pf._flashtool_candidates()
    assert cands[0] == "/from-env"
    assert cands[1].startswith(os.path.join("/from-install", "agent", "resources", "flashtool"))
    assert len(cands) == 3
