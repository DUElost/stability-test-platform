# 控制面 host 健康探针——解析/对账第一切片（#2983）

Status: implemented
Class: bug-fix

## Decision

先交可离线验收的纯函数层，不接 SSH/调度：

1. `backend/services/host_health_probe.py`：journal 解析（复用
   `kernel_usb_faults.parse_kernel_usb_faults`）、lsusb 拓扑分类
   （盲 / 空柜 / OK）、与 agent `health.reasons` 对账、连续 N 轮转红助手。
2. 测试钉子：.102 HC died 回放 → `AGENT_MUTE`；.90/.91 形空柜 → 不升失明；
   连续 2 轮才 `consecutive_strike_open`。

SSH 白名单采集 + cron/SAQ 接线 + 告警规则 = 后续切片（本 PR 刻意不做）。

## Alternatives

- **同 PR 接线 SSH**：需要并发帽、凭据路径、审计 actor，易与解析层纠缠；否决。
- **复制一份 journal 解析**：会与 Agent 签名漂移；改为 import 纯函数。

## Verification

- `python -m pytest backend/tests/services/test_host_health_probe_2983.py -q`
- `python scripts/run_gates.py check:quick`（eslint 视 worktree node_modules）

## Revisit

下一切片：固定 argv 探针执行器（`create_ssh_client` + sudo -S）与 ONLINE 断面调度。
