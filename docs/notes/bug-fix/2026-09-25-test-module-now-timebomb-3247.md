# 测试时间炸弹：模块级 `_NOW` 遇上请求时刻的新鲜度窗口（#3247 后续）

Status: implemented
Class: bug-fix

关联：[#3247](https://github.com/DUElost/stability-test-platform/issues/3247)（夜间 backstop）、
[promtool 与遮蔽修复 Note](./2026-09-25-promtool3-left-open-and-ci-step-masking-3247.md)（前序 PR #3269）、
#1804 / ADR-0038 D5（退役主机读面过滤，被测对象）。

## Decision

PR #3269 分支上的全量 CI（run 36097489277）里，repo-level 与 agent 两步都已转绿，`Run backend tests`
只剩 1 项失败：`test_host_retirement_read_filters_1804.py::TestStatsFaces::test_file_server_active_hosts_exclude_retired`
（`assert [] == ['fs-active']`）。

原因在测试本身，不是被测代码。模块级 `_NOW = datetime.now(...)` 在**收集期**求值，种子 host 的心跳取
`_NOW - 5s`；而 file-server 的活跃口径是**请求时刻** `now - 180s`。所以只要这条用例在收集完成 175 秒之后
才执行，种子心跳就会被判为陈旧，过滤结果为空。

- 那次运行的 runner 整体慢了约 2.6 倍：backend 这一步用了 45.4 分钟，PR #3266 的同类运行只用了 17.2 分钟。
  按进度行测算，最长的一段也只有 2.3 分钟，说明是均匀变慢，不是某个用例卡住。
- 该用例在第 6 分钟才被执行到，于是越过了 180 秒的窗口。以往 runner 快，它在窗口内执行完，这个问题从未暴露。

修法：`_host()` 按**调用时刻**取 now。`_NOW` 只剩下天级偏移的用法，保留不动。

## Alternatives

- **拉长 `STP_FILE_SERVER_AGENT_FRESH_SECONDS`**：会把测试和一个运维旋钮耦合在一起，而且只是把炸弹的
  引信拉长，runner 再慢一点照样会炸。
- **全仓把模块级 now 常量都改掉**：AST 扫描 `backend/tests`、`tests`、`backend/agent/tests`，模块级或类体里
  求值当前时间的常量一共 5 处。除本处外：
  - 3 处只用于 `started_at` / `retired_at`，属于天级或无窗口的用法；
  - 1 处（`test_coordinator_peers.py`）的进度新鲜度按 `pipeline_engine.py` 的注释「现在只作诊断」，不参与判定。

  它们都不与短时窗耦合，本单不改。

## Verification

- **复现**：把模块级 `_NOW` 临时改成 10 分钟前（模拟收集后很久才执行），旧代码下恰好 CI 上那一项失败，
  同文件其余 12 项通过。
- **修后**：正常执行与模拟晚执行两种情况下，同文件都是 **13 passed**。
- AST 扫描结果见上（5 处，仅本处与短时窗耦合）。
- `python scripts/run_gates.py check:quick` 与全量 CI 的结果见 PR 描述。

## Revisit

- 新写测试时，凡是被测逻辑按「请求或调用时刻减窗口」判定新鲜度的，种子时间一律在调用时刻求值，
  不要用模块级常量。如果再出现同型问题，把本节的 AST 扫描固化成守卫（对与短时窗耦合的用法判红）。
