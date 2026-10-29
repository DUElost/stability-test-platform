# Agent scan 子进程超时：600s → 1800s（#3834）

Status: implemented
Class: bug-fix

## Decision

一次完整 scan 链上有三层固定时限，全部按「小输入」时代设定；run 1124 的
host `172-21-15-xx` 证明它们挡不住 15 分钟级的大输入扫描：

| 层 | 旧值 | 新值 | 位置 |
|---|---|---|---|
| Agent 扫描子进程 | 600s | 1800s | `backend/agent/scan_runner.py` `_SCAN_SUBPROCESS_TIMEOUT` |
| 控制面 scan_task 的 SAQ 超时 | 900s | 2400s | `backend/services/dedup_scan.py` `_SCAN_TASK_SAQ_TIMEOUT` |
| 控制面 scan 轮询预算 | 300s（默认） | 1800s（站点 env） | `STP_SCAN_POLL_MAX_WAIT` |

依据：该 host 输入 156M AEE、813 个 dbg、staging 73G；第二轮工具日志在
595s 时仍停在「已完成解压 733/813（90.16%）」，随后被 600s 杀掉。同 run
其他 host 输入 1.1M–38M，均在 600s 内完成。

三层必须一起放宽：只改 Agent 超时，scan_task 仍会在 900s 被杀或 300s
轮询结束后不再登记迟到产物；只改控制面，Agent 子进程仍会被 600s 杀掉。
1800s/2400s 均保留兜底，不引入无限等待。

## Alternatives

- **只改 Agent 常量**：scan_task 900s 仍会先杀掉任务，或 300s 轮询结束后
  不再登记迟到产物——大输入 host 的 `_org.xls` 照样进不了报告。
- **只调大 Agent 到 900s**：按第二轮线性外推可能刚好够，但第一轮（monkey
  仍在跑时）600s 才进入解析阶段，余量不足。
- **做成环境变量**：可运维性更好，但要接 `agent_env_sync`、环境变量登记表
  与示例文件，改动面远大于问题本身；当前只有「太短」一个事实，先改常量。
- **不设超时**：挂死的扫描会永久占住 `_host_scan_semaphore`，后续 scan
  排队停摆，不可接受。

## Verification

- `backend/agent/tests/test_scan_runner.py`（含 `test_run_local_scan_timeout`）
  与 `test_unisoc_scan_runner.py`；
- `backend/tests/tasks/test_saq_scan_pipeline.py`；
- 部署站点 `STP_SCAN_POLL_MAX_WAIT=1800` 后，在 run 1124 上重跑 final scan，
  期望 `units=5/5`、host `172-21-15-xx` 产出 MTK `_org.xls`；
- 手动 merge 后核对报告覆盖 69 台设备（修复前第一轮只有 8 台，全部属于 `.60`）。

## Revisit

- 单 host 输入继续增长、1800s 再次踩线时：改为可配置（环境变量或包级配置），
  并按 host 规模做预算；
- 若超时从「输入大」变成「工具挂死」的常态，应补扫描阶段进度心跳，
  让停滞可观测，而不是继续加 timeout。

