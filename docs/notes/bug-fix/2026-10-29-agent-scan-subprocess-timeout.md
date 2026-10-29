# Agent scan 子进程超时：600s → 1800s（#3834）

Status: implemented
Class: bug-fix

## Decision

`backend/agent/scan_runner.py` 的 `_SCAN_SUBPROCESS_TIMEOUT` 由 600s 调到 1800s，
同时作用于主扫描（`start_log_scan.py`）与 `-dedup_org` 二次去重子进程。

依据是一次生产实测：run 1124 的 host `172-21-15-xx` 扫描输入 156M AEE、
813 个 dbg、staging 73G；第二轮扫描到 595s 时工具日志仍停在「已完成解压
733/813（90.16%）」，随后被 600s 硬超时杀掉。同 run 其他 host 输入 1.1M–38M，
均在 600s 内完成。结果是该 host 的 MTK `_org.xls` 从未产出，13 台设备整体
缺席最终去重报告。

1800s 是 3 倍余量：足够覆盖当前最大输入，又保留超时兜底——扫描真挂死时
仍会释放 host 扫描槽，不会无限等待。

## Alternatives

- **只调大到 900s**：按第二轮的进度线性外推可能刚好够，但第一轮（monkey
  仍在跑时）600s 才进入解析阶段，900s 余量不足；留 1800s 避免下一台大输入
  再次踩线。
- **做成环境变量**：可运维性更好，但要接 `agent_env_sync`、环境变量登记表
  与示例文件，改动面远大于问题本身；当前只有「太短」一个事实，先改常量。
- **按 host 单独放宽**：需要新的 per-host 配置面与下发通道，且下次换一台
  大输入 host 仍会复现，不如统一调大。
- **不设超时**：挂死的扫描会永久占住 `_host_scan_semaphore`，后续 scan
  排队停摆，不可接受。

## Verification

- `backend/agent/tests/test_scan_runner.py`（含 `test_run_local_scan_timeout`）
  与 `test_unisoc_scan_runner.py`；
- 部署后在 run 1124 上重跑 final scan，期望 `units=5/5`、host `172-21-15-xx`
  产出 MTK `_org.xls`；
- 手动 merge 后核对报告覆盖 69 台设备（修复前第一轮只有 8 台，全部属于 `.60`）。

## Revisit

- 单 host 输入继续增长、1800s 再次踩线时：改为可配置（环境变量或包级配置），
  并按 host 规模做预算；
- 若超时从「输入大」变成「工具挂死」的常态，应补扫描阶段进度心跳，
  让停滞可观测，而不是继续加 timeout。

