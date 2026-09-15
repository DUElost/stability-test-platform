# Code scanning 复核：known_hosts 落点守卫 + 热更新失败消息泛化（alerts #78/#80，#79 判误报）

Status: implemented
Class: bug-fix

## Decision

[security/code-scanning](https://github.com/DUElost/stability-test-platform/security/code-scanning)
在 open 的三条不是新缺陷，而是 2026-08-06 已处置告警的**重新开单**：

| 现告警 | 规则 / sink | 历史告警 | 历史处置 |
|---|---|---|---|
| #80 | `py/stack-trace-exposure` `backend/api/routes/hosts.py:909` | #28 | won't fix |
| #79 | `py/clear-text-logging-sensitive-data` `backend/main.py:144` | #27 | false positive |
| #78 | `py/path-injection` `backend/core/ssh_security.py:119` | #20 | won't fix |

复活机制：CodeQL 升到 2.27.0（#78/#79 于 09-11 重开）与 #908/ADR-0040 重构使
sink 行号漂移（#80 由 `hosts.py:863` → `909`）——result fingerprint 变了，GitHub
**不会把 dismiss 状态迁移到新 fingerprint**。因此「再 dismiss 一次」只是把同一件事
留到下下次升版重做，本次对成立的两条改为修代码：

- **#78**：新增 `backend/core/ssh_security.py` 的 `normalize_known_hosts_path()`，
  只接受绝对路径或 `~/` 前缀，拒绝相对路径、`..` 段、`~user/`（他人 home）与控制
  字符；三处消费同一守卫，不复制规则——`backend/api/schemas/host.py` 的
  `HostCreate`/`HostUpdate` 字段校验（非法值 **422 早失败**，并把归一化后的落点
  入库）、`_resolve_known_hosts_path()`（写侧 sink：`trust_host_key` 的
  mkdir/touch/重写）、`create_ssh_client()`（读侧同值）。空串仍表示「未配置」，
  回落顺序 explicit > `STP_SSH_KNOWN_HOSTS` > `~/.ssh/known_hosts` 不变。
  `trust_host_key` 的 never-raise 契约保持：非法落点返回 `(False, reason)`。
- **#80**：`execute_hot_update()` 的 `except (OSError, IOError)` 分支不再把 `str(e)`
  拼进 `message`。该分支的 message 会经 `hosts.py` 的 200 body 与 502 detail 双路
  外泄，而同函数的 `unexpected_error` 兜底分支早已是「泛化消息 + 详情进日志」——
  本次把连接失败分支对齐到同一口径：分类由稳定的 `reason=ssh_connect_failed` 承载，
  根因由日志锚点 `hot_update_connection_failed` 承载。
- **#79**：**判误报并 dismiss**（代码未改）。日志实参是
  `backend/core/agent_secret.py` 的 `is_agent_secret_configured()`，返回 `bool`，
  落盘只有 `True/False`；CodeQL 把 `AGENT_SECRET` 的 secret 污点穿过布尔表达式时
  不做类型精化。本条留此记录，是为了让「日志里出现 `agent_secret_configured=`」
  这一形状在下一次复核时可被判读，而不是被当成新发现。

## Alternatives

- **第三次 won't fix（否决）**：能清页面但零加固，且已实证会随 CodeQL 升版复发；
  管理员提交的相对/穿越路径仍会在换钥阶段降级成一条 warning。
- **再加 roots 白名单（`STP_SSH_KNOWN_HOSTS_ROOTS`，否决）**：默认值必须包含各站点
  的运行用户 home 才不破坏现网（runbook 记载默认即 `~/.ssh/known_hosts`），等于
  新增配置面换一条弱守卫。known_hosts 的落点由 admin 决定，本次收的是「路径形状」
  这一维度；真要上白名单需先确定该字段是否仍属完全可信输入（见 Revisit）。
- **只在 `resolve_host_ssh_credentials()` 单点校验（否决）**：该值有两个绕过它的
  消费入口（`hosts.py` 直接把 DB 列传给 `trust_host_key`），单点会漏；且 API 侧仍需
  早失败，否则错配要等到换钥才暴露。
- **#80 保留 errno/异常类名（否决）**：`type(e).__name__` 与 `e.errno` 同样源自被捕获
  异常对象，CodeQL 的 source 不变，只是把泄露面缩小而非消除；分类信息 `reason` 已够。

## Verification

- `backend/tests/test_ssh_security.py`：`test_normalize_known_hosts_path_accepts_configured_shapes`、
  `test_normalize_known_hosts_path_rejects`（6 形态 parametrize）、
  `test_resolve_known_hosts_path_validates_env_supplied_value`（env 供给值同域受守卫）、
  `test_trust_host_key_refuses_unsafe_path_before_touching_fs`（被拒落点不得进入
  ssh-keyscan/写入，且不抛）；
- `backend/tests/api/test_hosts.py::TestKnownHostsPathValidation`：绝对路径归一化入库、
  相对路径 422、PUT 穿越 422 且不留部分写入；
- `backend/tests/services/test_host_updater.py::test_execute_hot_update_result_carries_converged_fields`
  增断言：异常原文不得出现在 `message`；
- 实跑：`pytest backend/tests/test_ssh_security.py backend/tests/services/test_host_updater.py -q`
  → 55 passed；`pytest backend/tests/api/test_hosts.py -q` → 49 passed（testcontainers
  临时库）；门禁 `ruff`/`compileall`/`env-inventory`/`layering`/`orphan-models`/
  `ai-work`/`ip-leak`/`immutability`/`invariant-diff`/`gov-surface` 全绿。
- `check:quick` 的 js 门禁（eslint/tsc/knip）未跑——worktree 无 `node_modules`，
  本次未触前端。
- **合入后 CodeQL 复算实跑结果**（#2153 于 2026-09-15 08:48 合入 → main 下一次
  `Analyze (python)` 复算后核取，页面 open 归零）：

  | 告警 | 结果 | 判读 |
  |---|---|---|
  | #80 | `fixed` | message 泛化即闭合，未动用 dismiss |
  | #79 | `dismissed / false positive` | 代码未改，本 Note 即依据 |
  | #78 | `dismissed / mitigated` | **raise 守卫不被 CodeQL 识别为 barrier**：sink 仍是 `_resolve_known_hosts_path` 的 `Path(raw).expanduser()`，行号仅由 119 漂到 146（本次插入造成的位移）——加校验清掉的是它指出的真实风险，清不掉告警本身 |

  对下一次复核的直接影响：**#78 型复活不必再复核代码**，按 `mitigated` 引用
  `normalize_known_hosts_path` 即可；要让页面真正静默，唯一路径是改用 CodeQL
  认得的收敛形状（roots 白名单 + `commonpath`，见 Alternatives——本次已否决）。
- 现网影响面核对（本机 `stp` 只读 SELECT，2026-09-15）：`host` 48 行、
  `ssh_known_hosts_path` 非空 **0 行**，且 `.env.backend` 未设
  `STP_SSH_KNOWN_HOSTS`（走 `~/.ssh/known_hosts` 回落）——新守卫对现网零影响，
  只拦未来的错配。

## Revisit

- 若 `ssh_known_hosts_path` 将来对非 admin 开放（自助登记主机），「形状守卫 + admin
  可信」的前提失效，必须升级为 roots 白名单并复核 `ssh_key_path`（同类未收口的字段）；
- 若 `startup_security_config` 日志行的实参从布尔换成任何派生自 secret 的字符串
  （长度、前缀、指纹），#79 即由误报转为真问题；
- CodeQL 再次升版时先用本表比对 open ↔ 历史告警，确认是复活而非新缺陷。
