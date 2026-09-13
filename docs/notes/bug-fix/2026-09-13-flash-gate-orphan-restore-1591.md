# #1591 abort 后门控孤儿恢复（flash_firmware v1.3.15）

Status: implemented
Class: bug-fix

## Decision

#1591 残留「abort 后 USB 锁即时释放」：pipeline 取消默认 SIGTERM→2s→SIGKILL。
`flash_firmware` 的 `_settle_lock`（`authorized=1` 恢复门控口）若被 SIGKILL
打断，邻机口永久滞留隐藏态——体感像「锁未释放 / USB 死」。

本切片：

1. **v1.3.15**：门控口写入 `/tmp/stp-flash-firmware.gated.json`；settle 成功后清
   文件；下次刷机抢锁前 `_restore_orphaned_gates` 补恢复。
2. **pipeline_engine**：对 `flash_firmware` 路径取消/超时宽限 2s→**8s**，给
   settle 留时间（孤儿文件仍作 SIGKILL 兜底）。

判定粒度与现场 11 台拔插仍属后续，本 PR **不关闭** #1591。

## Alternatives

- 仅加长宽限：SIGKILL 仍可能打断；孤儿文件必不可少。
- Agent 全局 abort 钩子扫 sysfs：面过大，且不知哪些口被本脚本隐藏。
- 原地改 v1.3.14：违反 ADR-0020。

## Verification

- `python -m pytest backend/agent/tests/test_device_flash_scripts.py -q`
- `python tools/dev/check-script-version-immutability.py --base origin/main`
- `python scripts/run_gates.py check:quick`

## Revisit

- 部署后 `POST /scripts/scan` 并重指 PlanStep → v1.3.15；
- 判定粒度（flash 成功 + 后续失败区分）仍待平台聚合层。
