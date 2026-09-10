"""install_selfcheck：安装后自检必须用真实 schema 校验样例 Pipeline（#1247）。

守两件事：
1. 样例 Pipeline 始终能通过仓库内 schema——schema 必填面变化时先在这里变红，
   而不是等某台干净安装的主机上报「合法 Pipeline 校验失败」；
2. main() 的失败出口可判定——schema 缺失等异常必须以非零码 + 明确哨兵结束，
   安装入口才能据此中止。
"""

from __future__ import annotations

from backend.agent.install_selfcheck import SAMPLE_PIPELINE_DEF, main
from backend.agent.pipeline_validator import validate_pipeline_def


def test_sample_pipeline_is_valid_against_repo_schema():
    ok, errors = validate_pipeline_def(SAMPLE_PIPELINE_DEF)

    assert ok is True, errors


def test_main_succeeds_with_installed_layout(capsys):
    assert main() == 0
    assert "INSTALL_SELFCHECK_OK" in capsys.readouterr().out


def test_main_fails_when_schema_unavailable(monkeypatch, capsys):
    import backend.agent.pipeline_validator as validator

    def _raise_missing(_pipeline_def):
        raise FileNotFoundError(
            "/opt/stability-test-agent/schemas/pipeline_schema.json"
        )

    monkeypatch.setattr(validator, "validate_pipeline_def", _raise_missing)

    assert main() == 1
    err = capsys.readouterr().err
    assert "INSTALL_SELFCHECK_FAIL" in err
    assert "pipeline_schema.json" in err
