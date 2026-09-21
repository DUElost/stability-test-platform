# 控制面 host 健康探针——解析/对账 + SSH 白名单执行器（#2983）

Status: implemented
Class: bug-fix

## Decision

分两切片，本 PR 覆盖①+②，**仍不接 cron/告警**：

1. **解析/对账**：journal（复用 `kernel_usb_faults`）、lsusb 盲/空柜、与 agent
   `health.reasons` 对账、连续 N 轮转红助手。
2. **SSH 执行器**：`PROBE_ARGV_WHITELIST` 固定 argv；`sudo -S -p '' -- …`；
   `select_probe_host_ids`（ONLINE ∧ 非 retired ∧ 非维护窗）。口令只走 stdin，
   不进日志/异常。

## Alternatives

- **同 PR 接 SAQ/cron**：并发帽与告警规则未定，易与可测纯层纠缠；否决。
- **复制 journal 解析**：与 Agent 签名漂移；改为 import 纯函数。

## Verification

- `python -m pytest backend/tests/services/test_host_health_probe_2983.py -q`

## Revisit

下一切片：调度入口（并发帽 + 连续窗状态落库）+ 对账告警规则；#2972 可消费本探针拓扑。
