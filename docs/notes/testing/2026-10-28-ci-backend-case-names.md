# Actions backend 测试输出运行用例名

Status: implemented
Class: testing

## Decision

将 `.github/workflows/ci.yml` 中全量 `backend-test` 与 PR 信息性
`pr-backend-tests-informational` 的 backend 测试命令统一改为
`PYTHONUNBUFFERED=1 python -m pytest backend/tests/ -v -p scripts.ci.pytest_progress`。
CI 专用插件在 `pytest_runtest_protocol` 入口、fixture/setup 开始前，通过终端报告器
写入独立的 `[pytest-start] <nodeid>` 行并显式 flush。hook 返回 `None`，继续 pytest
原有的执行协议；测试选择、默认捕获与退出码保持原有语义。插件只在这两条 CI 命令
显式加载，不向普通测试会话全局注册。

`-v` 继续记录完成结果，`PYTHONUNBUFFERED=1` 保留 Python 无缓冲输出。实时定位依赖
带换行的开始标记：[Actions runner 2.338.0 按行读取进程输出](https://github.com/actions/runner/blob/v2.338.0/src/Runner.Sdk/ProcessInvoker.cs#L471-L480)，
仅 flush 未换行的 verbose 前缀不足以让当前用例名及时出现在 Actions 日志。

触发事实：[B7 集成 CI 40621438787](https://github.com/DUElost/stability-test-platform/actions/runs/40621438787)
在 backend 测试步骤运行 58 分 12 秒后由 Owner 要求取消，未产生完整测试汇总。
[激活第 0–1 步结果](https://github.com/DUElost/stability-test-platform/issues/3809#issuecomment-6459963121)
记录了停止状态；本改动用于后续排查，尚未确定该次运行的阻塞用例或原因。

## Alternatives

仅使用 `-v` 与无缓冲输出的做法，经[第 1 轮独立复核](https://github.com/DUElost/stability-test-platform/pull/3821#issuecomment-6462248106)
实证无法及时交付完整行。`-s` 会放开被测程序的全部标准输出，增加日志噪声；本次
继续使用 pytest 默认捕获。未增加超时或调整测试集合。

初版采用 `python -u -m pytest`，但 [PR CI 40628222146](https://github.com/DUElost/stability-test-platform/actions/runs/40628222146/job/122618441955)
实际暴露 `test_ci_workflow_runs_agent_tests_frontend_vitest_and_uses_lockfile` 固定匹配
`python -m pytest backend/tests/` 的已有断言。改用环境变量表达同一无缓冲行为，
保留原有命令形态及守卫判据。

## Verification

`tests/test_ci_pytest_progress.py` 从 workflow 提取两条真实 backend 命令，仅把测试目录
换为受控临时用例，以逐行读取器消费输出。四组用例分别阻塞 setup 与测试正文；收到
完整开始行时，控制器断言子进程仍存活且放行文件不存在。另一个用例验证默认捕获、
`-k` 选择及断言失败的退出码 1。

独立 pytest 9.1.1 环境（与被复核 CI 版本一致），经项目内存保护 runner 实测：

- 使用旧 HEAD `8e126af` 的 workflow 命令：5 failed；
- 使用修复后命令：5 passed；四组均记录
  `line_visible_while_blocked ... child_alive=True; release_absent=True`；
- 证据：`/home/debian13/stp-evidence/ci-3821-r2/red-final.log` 与
  `green-final-pytest-9.1.1.log`。隔离模拟关闭第三方插件自动加载，产生两条 asyncio
  配置项未知的 warning，未跳过用例。

通过项目 Python 入口与内存保护 runner，在干净环境运行后端守卫（testcontainers
PostgreSQL 隔离）与相关离线测试，共 33 passed：

```bash
env -i PATH="$PATH" HOME="$HOME" PYTHONPATH=. PYTHONUNBUFFERED=1 \
  ./scripts/project_python.sh scripts/run_pytest.py \
  backend/tests/test_ci_and_test_harness_files.py \
  tests/test_pr_backend_tests_informational_3572.py \
  tests/test_backend_test_timeout_budget_3576.py \
  tests/test_ci_test_db_guard_wiring.py \
  tests/test_ci_pytest_progress.py -v
```

本地没有运行 backend 全量或连接生产库。上述逐行反例验证实时交付行为；历史 HEAD
的 CI 绿灯不能替代它，新 HEAD 的 CI 与复核结果分别绑定对应提交报告。

## Revisit

下一次出现长时间未结束时，先依据最后一条 `[pytest-start]` 定位具体用例及其 fixture。
若阻塞发生在收集期或用例完成后的清理期，再按证据决定是否增加相应诊断输出。
