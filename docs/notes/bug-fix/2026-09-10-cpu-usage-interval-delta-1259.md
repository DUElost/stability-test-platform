# Agent CPU 使用率改为区间差分采样（#1259 / R14-F13）

Status: implemented
Class: bug-fix

## Decision

本质问题：`get_cpu_usage()` 对 `/proc/stat` **单次累计计数**直接求比例
`(user+system)/total`——这是「开机以来平均值」。心跳持续上报的「当前负载」
在长时间低负载后突然满载时仍接近历史均值，反应迟钝。改为**两次采样的区间
差分**：

- 字段扩到全集（user/nice/system/idle/iowait/irq/softirq/steal，缺列补 0）；
- 与上次采样求增量：`busy = total_delta − idle_delta − iowait_delta`
  （iowait 是等 IO 的空闲，不算 CPU 忙），`usage = busy / total_delta × 100`；
- 首采无基线返回 `0.0`（仅 priming，不回报历史均值）；
- 计数器回绕（重启/热插拔出现负增量）→ 重置基线并返回 `0.0`，下一窗口从
  新基线重算；
- 心跳线程与手动心跳可能并发采集，采样状态更新加 `threading.Lock`；
- 解析/读文件异常保持既有失败语义（`0.0` + warning）。

## Alternatives

- **保留单次累计比例**——放弃：正是本单现象，低负载→满载转换迟钝；
- **函数内 sleep 一小段做阻塞差分**——放弃：心跳路径被阻塞、测试难写；跨调用
  持有上次样本天然形成窗口，零等待成本；
- **iowait 计为忙（`busy = total − idle`）**——放弃：iowait 是等 IO 的空闲；
  采用 `total − idle − iowait` 并在 docstring 写明口径；
- **引入 psutil 依赖**——放弃：agent 现用原生 `/proc` 读取，为 CPU 一个指标加
  依赖不划算；现方案用假样本 mock `open` 即可完整测试。

## Verification

实际运行（worktree `/tmp/stp-1259`，2026-09-11）：

- `pytest backend/agent/tests/test_system_monitor.py -q` → **20 passed**
  （新增/改写 5 例：首采 priming 不报历史均值、空闲→繁忙上升（95.24）、
  iowait 计空闲（0.0）、计数器回绕重置后新基线重算（50.0）、非法格式/文件
  缺失；autouse fixture 清零跨用例采样状态）；
- `TESTING=1 ... pytest backend/agent/tests/ -q`（CI `pr-agent-tests` 等价
  路径）→ **1547 passed**；
- `pytest tests/ -q` → **148 passed**；
- `ruff check .` → All checks passed；
- `check:quick` → **7 gates 全绿**。

未完成（pending）：

- 真机空闲→满载转换的现场采样验证：本机未跑 agent 心跳链路；差分语义已由
  假样本序列覆盖。

## Revisit

- 若未来需要每核/按 cgroup 的 CPU 采集，扩展 `_parse_cpu_line` 处理 `cpuN`
  行并新增 per-core 指标；
- 差分窗口长度随心跳间隔变化——比例语义与窗口无关，暂无需适配；若改为
  「窗口内平均使用率」类指标再显式引入时间戳。
