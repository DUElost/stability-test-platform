# B6-U4：init 非特权结果接收与终态接线前检

## Decision

Refs #3729，Refs #3723。实施依据为 #3723 当前正文 v1.7.16，以及规划者对
[第一次退回](https://github.com/DUElost/stability-test-platform/issues/3723#issuecomment-6367520373)
的裁定。本次恢复原 Execution `B6-U4`，保留原 scope 并加入
`backend/agent/contracts/device_return.py`，没有重新 declare。

已完成的修改只有 `probe_entry_response` 的非特权值放行判断。`init_check` 与清偿一样
接收 `executed_unverified`、`failed`、`incomplete`、`not_executed`；不可信的 `confirmed`
与 `readback_failed` 仍返回 422。没有修改 `probe_result_policy`、有界保护或其它入口。
前检反例已转入 `backend/tests/api/test_init_probe_policy_u4_3729.py`，另补特权值与令牌对照。

后续接线因第二处规划缺口停止，尚未实现 init 检查、init HTTP 入口、心跳声明或清偿。
PR 保持 draft，交由规划者先核对，再安排独立复核。

## Alternatives

本次采用规划者批准的一处判断修正，没有绕过共享政策、伪造可信组合或复制核验。
新测试复用既有 U8 隔离组合、真实 manifest 包身份与信号桩；现有 U8 断言没有被改弱。

没有自行改变 `/complete` 对 `init_check` 结果的身份与状态处理。v1.7.16 仅授权上述
一处判断，并明确不改其它入口；终态如何区分 init 检查结果与本 job 的解除结果，
需要规划者补齐实施约定后才能接线。

## Verification

基线 `25cb3f61a42a743313fb90fe7fc8c5cd1b80eabd`，已刷新 `origin/main`，含 U8
`e80c6c901e986c9e03d348455adca21b31188edb`。开放 PR 为空，Registry 只有原
`B6-U4`。`resume`、保留全部原路径的 `update --scope` 与 `git merge --ff-only origin/main`
均退出 0。

测试均经项目入口及内存保护 runner；PG 由 testcontainers 创建，未设置任何数据库连接串，
未加载生产 env。实际日志确认 `memory.max=6442450944`、`memory.swap.max=0`。
使用既有 `signals_are_forbidden` 桩，未运行危险 cancel 变异或真实宿主信号。

- 修正前：`env -i PATH="$PATH" PYTHONPATH=. TESTING=1 JWT_SECRET_KEY=b6-isolated-test-only ./scripts/project_python.sh scripts/run_pytest.py backend/tests/api/test_init_probe_policy_u4_3729.py -q`，退出 1：**5 failed、3 passed**。
- 修正后：同样的隔离环境，`./scripts/project_python.sh scripts/run_pytest.py backend/tests/api/test_init_probe_policy_u4_3729.py backend/tests/api/test_disarm_policy_u8_3752.py -q`，退出 0：**69 passed**。包含普通完成、迟到确认、清偿与 init 政策既有回归。
- 撤回变异：只临时恢复原 `entry_point != "init_check"` 判断，运行新增测试；子测试退出 1：**5 failed、3 passed**。驱动 `finally` 按原字节恢复修正，不涉及取消或进程信号。
- 完整测试根、`check:quick` 与远端检查的最终结果在交付回告记录；本 Note 不以局部绿替代它们。

第二处反例使用真实 `check_init_probe_result` 和真实 `/complete` API，隔离 PG。
输入是方案要求的 `step_id=return_check.power-cycle`、`result=confirmed`、`source=init_check`，
证据使用 U8 已有测试可信组合。job 的冻结流水线没有开关机步骤；真实 init 政策返回 200。
为了单独验证终态消费者，夹具先按新入口的规定写入 `returned/init_check`，再回报该结果。
这不是尚未实现的新 init HTTP 入口或完整 Engine 的验收。

```text
POST /complete -> 200
job.status = COMPLETED
job.disarm_results[power-cycle].result = not_executed
device.return_state[power-cycle] = debt / source=job
open debt count = 1；JOB 租约转换为欠账租约
assert not_executed == confirmed -> 失败
```

命令：`env -i PATH="$PATH" PYTHONPATH=. TESTING=1 JWT_SECRET_KEY=b6-isolated-test-only ./scripts/project_python.sh scripts/run_pytest.py -c pytest.ini -p backend.tests.conftest /home/debian13/b6-u4-evidence-20261023/test_init_complete_preflight_3729.py -q -s`。
最终退出 1，**1 failed**。断言失败发生在成功的 API 响应与独立 Session 落库读回之后；
日志另外包含异步连接清理的 event-loop 报错，未将其当作反例原因或通过证据。

调用链证据（行号以基线为准）：

- `pipeline_runner.py:112` 把 Engine metadata 中的 `disarm_results` 转入终态载荷。
- `services/device_return.py:1685–1692` 的 `evaluate_probe_entry(job=...)` 只从冻结
  `lifecycle.teardown` 按 step id 查脚本身份；init 检查不在此列表，身份变为空。
- `services/device_return.py:1804–1810` 的 `verify_job_results` 因此按普通完成的
  不可信特权值规则回落到 trace；无该专项 teardown/足迹时默认 `not_executed`。
- `services/agent_completion.py:532–549` 保存上述结果，随后统一归还入口按该值建立欠账。

## Revisit

请规划者明确 init 检查结果与本 job 解除结果在终态载荷、脚本身份核验及专项状态更新中的
边界，并补齐允许修改的范围。实施者没有自行设计该边界，也没有修改其它入口来闭合反例。

U4 完整功能、冻结 1.0.8 经真实 Engine 的 init 验收、新 init API/锁序 NOWAIT、其它修复点
红绿与撤回变异均 pending。没有生产迁移、初始化、部署、真机、publish、scan、热更新或重指；
真机验证仍留在激活 L2。Draft 状态、Ready/auto-merge/merge 禁令继续有效。
