"""Regression tests for install_agent.sh host-id selection."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "install_agent.sh"


def _function_source() -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^generate_unique_host_id\(\) \{\n.*?^\}\n(?=\n# 提示用户输入 API_URL)",
        source,
    )
    assert match is not None
    return match.group(0)


def _run_generator(hosts_json: str, ip_addr: str) -> str:
    script = f"""
{_function_source()}
curl() {{ printf '%s' "$MOCK_HOSTS_JSON"; }}
generate_unique_host_id http://control-plane "{ip_addr}"
"""
    env = dict(os.environ)
    env["MOCK_HOSTS_JSON"] = hosts_json
    completed = subprocess.run(
        ["bash", "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


def test_generator_reuses_existing_host_id_for_same_ip():
    result = _run_generator(
        '[{"id":"legacy-host-id","ip":"198.51.100.6"}]',
        "198.51.100.6",
    )
    assert result == "legacy-host-id"


def test_generator_keeps_ip_id_when_response_is_not_a_host_list():
    result = _run_generator(
        '{"detail":"Not authenticated"}',
        "198.51.100.6",
    )
    assert result == "198-51-100-6"


def test_generator_suffixes_id_owned_by_different_ip():
    result = _run_generator(
        '[{"id":"198-51-100-6","ip":"198.51.100.99"}]',
        "198.51.100.6",
    )
    assert re.fullmatch(r"198-51-100-6-[0-9a-f]{4}", result)
