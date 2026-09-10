"""安装后自检：用安装产物校验样例 Pipeline（#1247）。

install_agent.sh 在铺完代码与 schema 后，以安装目录为 PYTHONPATH 调用：

    cd "$INSTALL_DIR" && PYTHONPATH="$INSTALL_DIR" \
        "$INSTALL_DIR/venv/bin/python" -m agent.install_selfcheck

自检走与运行时相同的 ``validate_pipeline_def`` 入口（语义 + JSON Schema），
证明「脱离开发仓库目录」的安装产物可自洽工作（此前缺少 schema 时，合法
Pipeline 会在校验阶段失败并上报）。失败以非零码退出，安装入口据此中止，
避免带病上线。
"""

from __future__ import annotations

import sys

# 样例覆盖 schema 必填面：lifecycle.init/teardown、step 必填四字段。
# 与 backend/tests/core/test_pipeline_validator.py 的合法样例同构；
# 改 schema 必填面时该样例会先红，防止自检样例与 schema 静默漂移。
SAMPLE_PIPELINE_DEF = {
    "lifecycle": {
        "init": [
            {
                "step_id": "install_selfcheck",
                "action": "script:install_selfcheck",
                "version": "0.0.0",
                "params": {},
                "timeout_seconds": 1,
            }
        ],
        "teardown": [],
    }
}


def main() -> int:
    try:
        from .pipeline_validator import validate_pipeline_def

        ok, errors = validate_pipeline_def(SAMPLE_PIPELINE_DEF)
    except Exception as exc:
        # schema 缺失/损坏、jsonschema 未安装——正是本自检要拦截的带病形态
        print(f"INSTALL_SELFCHECK_FAIL: {exc}", file=sys.stderr)
        return 1

    if ok:
        print("INSTALL_SELFCHECK_OK: installed pipeline schema validation passed")
        return 0

    print("INSTALL_SELFCHECK_FAIL: " + "; ".join(errors), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
