# 主机 journal 容量基线（#3804）

Status: implemented
Class: bug-fix

## Decision

2026-10-27 的只读盘点覆盖当前 77 台 Agent 主机：全部在线、全部没有显式 journal 限额、Agent 文件日志轮转配置全部在位。根分区分为小于 64 GiB 的 2 台、64–256 GiB 的 69 台、256 GiB 及以上的 6 台。

按上述三档设置 journal 预算，复用配置、安装、更新三个 Ansible 入口的单一 task。角色预算可以显式覆盖。只在配置变化时原子重启 journald，核对 Agent MainPID 保留；不主动 vacuum 或轮转。预算约束系统 journal，不改变任务日志、LocalDB、outbox 或设备日志保留行为。

本次是 #3804 的一个主机配置单元，不新增数据模型、脚本版本、Agent 运行权限或跨单元协议；各主机可以独立生效。原有日志自然淘汰仍由 journald 执行，配置回退无法恢复已经淘汰的日志。

## Alternatives

- 只修故障主机：不能覆盖其余现有主机或新机接入。
- 通过 Agent 热更新写 `/etc`：扩大运行时提权边界，且把主机配置与代码更新耦合。
- 定时强制 vacuum：反复执行清理，缺少持久的保留预算。

## Verification

- 77 台只读盘点：已完成；私有证据留在宿主，不提交地址与凭据。
- 针对性测试：`scripts/run_pytest.py` 执行 journal 基线、Ansible 配置通道、日志轮转、非交互安装与安装产物测试，60 passed。
- Ansible `--syntax-check` 与故障主机 `--check --diff`：通过；预演没有执行文件写入或服务重启。
- canary、幂等重跑、全队验收：pending。
- `check:quick` 与 required CI：pending。
- 写作约定自查：新增 runbook 句子按角色、强度词、一步一动作及警告前置核对；没有改写存量规则。

## Revisit

根分区规格、日志增长或诊断保留需求变化时调整对应档位。磁盘告警采集覆盖继续属于 #3233；本变更不宣称补齐其余告警或备份问题。
