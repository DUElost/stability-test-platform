# B6 U10：停机终态使用租约 fencing token

Status: implemented
Class: bug-fix

## Decision

按 [#3723 v1.7.24 §3.10](https://github.com/DUElost/stability-test-platform/issues/3723)
与 [#3795 派单](https://github.com/DUElost/stability-test-platform/issues/3795#issuecomment-6401421976)
修复停机 outbox 的令牌来源。`graceful_shutdown.py` 在持久化未完成解除之前读取
LocalDB `get_active_jobs()`，按 job 选择已持久化的租约令牌；该 job 缺少令牌时，
调用 LeaseRenewer 的只读 `get_fencing_token()`。该 getter 使用续租器已有的锁。
两处都没有令牌时，记录 `shutdown_incomplete_outbox_skip_no_token` 并跳过该 job。

`active_job_tokens` 和 `active_runners` 中的 worker token 保持本机 worker 身份语义。
停机 outbox 不再从这两个位置取得 fencing token。LocalDB 复用现有读取接口，
生产改动仅涉及 `graceful_shutdown.py` 与 `lease_renewer.py`。

U1 的停机持久化夹具同步区分 worker token 与租约令牌。新增 Agent 回归覆盖
LocalDB 优先、续租缓存回退、缺少两处令牌时跳过；新增后端验收用真实停机入口产出的
LocalDB outbox payload 调用真实 `/api/v1/agent/jobs/{job_id}/complete`。

## Alternatives

- 沿用 worker token 会继续被控制面的租约校验拒绝，无法保存停机终态。
- 直接读取续租器私有字典会绕过其同步边界；只读 getter 复用现有锁。
- LocalDB 已有 `get_active_jobs()`，本次无需新增查询接口或修改其 schema。

## Verification

基线：`origin/main@87761c744383b87b6ff7d9b1f8c0c2f9db4a4ac5`。
以下 pytest 均经项目 Python 入口与 `scripts/run_pytest.py`；runner 实际确认
`memory.max=6442450944`、`memory.swap.max=0`。Agent 使用 `env -i`；
后端未设置 `TEST_DATABASE_URL`，由 conftest 创建隔离的 `postgres:16` testcontainer。
测试中的 SQLite 仅为临时 Agent LocalDB，控制面业务数据使用隔离 PG。

旧代码上的生产形态反例：两个 worker 映射内均为 `worker-42-shutdown-test`，
租约令牌仅存于 LocalDB registry 与 LeaseRenewer。运行：

```bash
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py \
  backend/agent/tests/test_shutdown_fencing_u10_3795.py -q \
  -k test_shutdown_uses_lease_fencing_token
```

退出码 **1**，实际失败输出：

```text
FAILED ...::test_shutdown_uses_lease_fencing_token[registry-first]
AssertionError: assert 'worker-42-shutdown-test' == 'registry-lease-token'
FAILED ...::test_shutdown_uses_lease_fencing_token[renewer-fallback]
AssertionError: assert 'worker-42-shutdown-test' == 'cached-lease-token'
2 failed, 1 deselected in 0.18s
```

修复后的定向 Agent 回归：

```bash
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py \
  backend/agent/tests/test_shutdown_fencing_u10_3795.py \
  backend/agent/tests/test_device_return_u1_3726.py \
  backend/agent/tests/test_graceful_shutdown_736.py \
  backend/agent/tests/test_lease_renewer.py -q
```

结果：**62 passed，退出码 0**。

```bash
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py \
  backend/agent/tests/ -q
```

结果：**2886 passed in 107.65s，退出码 0**。

真实控制面验收：

```bash
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py \
  backend/tests/api/test_shutdown_outbox_complete_3795.py -q
```

结果：**4 passed，退出码 0**。LocalDB / LeaseRenewer 两种令牌来源分别覆盖
RUNNING 与 UNKNOWN；UNKNOWN 的 ACTIVE JOB 租约已过期 5 秒，job 进入 UNKNOWN
30 秒，仍在现有 300 秒宽限内。每例先确认 worker token 返回 409 且 job、租约与
欠账未改变，再用真实停机 payload 得到 200：job 为 ABORTED，`incomplete` 与
`shutdown_disarm_window_elapsed` 保留，建立来源为该 job 的 open 欠账，原 JOB
租约以同一 id 转成 ACTIVE MAINTENANCE 欠账租约，设备归还状态为 debt。
测试仅替代外部任务队列、后台组件 stop 和已耗尽窗口的等待；租约校验与事务入口真实运行。

首次后端命令因夹具 mock 目标误指函数内导入而在 setup 阶段报 4 errors，退出码 1；
改为 patch `backend.core.task_queue.get_queue` 后，同一命令重跑得到上述 4 passed。

根 `tests/`、既有后端回归、`check:quick` 与当前 PR HEAD 的 CI：pending。

## Revisit

合入前独立复核与 Owner 转 Ready：pending。本 PR 保持 Draft。
部署、全机队热更新与真实重启复验按 #3723 §5 第 9 条在另行授权的工作面执行；
本次交付不代签激活证据。后续变更若调整 LocalDB registry 或续租器的令牌生命周期，
应保留本次生产形态反例和真实 `/complete` 验收。
