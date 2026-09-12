# HOST_HEARTBEAT_TIMEOUT_SECONDS 单一常量源（#1518）

Status: implemented
Class: bug-fix

## Decision

`HOST_HEARTBEAT_TIMEOUT_SECONDS` 曾在四处独立 `getenv`：`session_watchdog`
默认 **120**，`devices` / `hosts` / `reachability` 默认 **300**。未显式设
env 时，watchdog 已按 120s 判离线，诊断页/reachability 在 300s 内仍显示
心跳新鲜——排障结论与调度行为矛盾。

修复：在 `backend/core/job_timeout_config.py` 增加单一常量（默认 **300**，
与 `.env.example` / ADR-0025 一致）；四处改为 import 同一对象。`settings.py`
仍从 `hosts` 转引，无需改。

涉及文件：`job_timeout_config.py`、`session_watchdog.py`、`devices.py`、
`hosts.py`、`reachability.py`、`tests/core/test_job_timeout_config.py`。

## Alternatives

- 默认改回 120：与现行部署样例与 ADR-0025「建议 300」冲突；否决。
- 仅改 watchdog 字面量为 300、不抽公共常量：仍会再次漂移；否决。

## Verification

- `pytest backend/tests/core/test_job_timeout_config.py -q`
- `pytest backend/tests/services/test_precheck_reachability.py -q`（引用该常量）
- `python scripts/run_gates.py check:quick`

## Revisit

历史 ADR-0004 / openspec 仍写默认 120；属归档文档，不以本单批量改写。
