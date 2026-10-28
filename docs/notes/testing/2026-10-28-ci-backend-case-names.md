# Actions backend 测试输出运行用例名

Status: implemented
Class: testing

## Decision

将 `.github/workflows/ci.yml` 中全量 `backend-test` 与 PR 信息性
`pr-backend-tests-informational` 的 backend 测试命令统一改为
`PYTHONUNBUFFERED=1 python -m pytest backend/tests/ -v`。`-v` 显示用例 node ID，
`PYTHONUNBUFFERED=1` 禁用 Python 标准输出缓冲，使正在执行的用例名可用于定位
长时间没有完成的测试。

触发事实：[B7 集成 CI 40621438787](https://github.com/DUElost/stability-test-platform/actions/runs/40621438787)
在 backend 测试步骤运行 58 分 12 秒后由 Owner 要求取消，未产生完整测试汇总。
[激活第 0–1 步结果](https://github.com/DUElost/stability-test-platform/issues/3809#issuecomment-6459963121)
记录了停止状态；本改动用于后续排查，尚未确定该次运行的阻塞用例或原因。

## Alternatives

保留 `-q` 只能看到紧凑进度，难以定位未结束的用例。`-s` 会放开被测程序的全部
标准输出，增加日志噪声；本次继续使用 pytest 默认捕获。未增加超时或调整测试集合。

初版采用 `python -u -m pytest`，但 [PR CI 40628222146](https://github.com/DUElost/stability-test-platform/actions/runs/40628222146/job/122618441955)
实际暴露 `test_ci_workflow_runs_agent_tests_frontend_vitest_and_uses_lockfile` 固定匹配
`python -m pytest backend/tests/` 的已有断言。最终改用环境变量表达同一无缓冲行为，
保留原有命令形态及守卫判据。

## Verification

通过项目 Python 入口与内存保护 runner，在干净环境运行后端守卫（testcontainers
PostgreSQL 隔离）及以下已有离线测试，共 28 passed；详细输出中可见每个用例 node ID：

```bash
env -i PATH="$PATH" HOME="$HOME" PYTHONPATH=. PYTHONUNBUFFERED=1 \
  ./scripts/project_python.sh scripts/run_pytest.py \
  backend/tests/test_ci_and_test_harness_files.py \
  tests/test_pr_backend_tests_informational_3572.py \
  tests/test_backend_test_timeout_budget_3576.py \
  tests/test_ci_test_db_guard_wiring.py -v
```

本地没有运行 backend 全量或连接生产库；本改动的 Actions 效果以新提交的运行日志为准。
`./scripts/project_python.sh scripts/run_gates.py check:quick` 退出 0（16 gates）；
其中 schema-at-head 因未配置 `DATABASE_URL` 明确跳过，未计为 schema 核验通过。

## Revisit

下一次出现长时间未结束时，先依据最后输出的 node ID 定位具体用例及其 fixture。
若阻塞发生在收集期或用例完成后的清理期，再按证据决定是否增加相应诊断输出。
