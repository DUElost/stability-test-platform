# UNISOC scan runner 对齐 GT-SPRD 工具链 CLI

Status: implemented
Class: bug-fix

## Decision

`UnisocScanRunner`（ADR-0032 D4c）按 NFS 上真实 toolkit 契约调用：

| 阶段 | 命令 |
|------|------|
| Phase 1 | `scan_log_gt.py -p <scan_root> -m sprd -i <poll_s>`（长轮询；`scan_now` 用 `timeout=poll+90` 杀进程，超时视为成功） |
| Phase 2 | `scan_result.py -d <scan_root>`（非 positional org xls） |
| 产物 glob | `**/*_org.xls`（如 `Result_*_MonkeyAEE_SPRD_*_org.xls`） |

控制面 fleet 键：`STP_AGENT_UNISOC_*` → Agent `STP_UNISOC_*`；`STP_UNISOC_LOG_SCAN_POLL_SECONDS`（默认 60）仅 Agent 读。

**运行依赖**：`scan_result.py` 导出 xls 需系统包 `python3-xlwt`（`apt install python3-xlwt`），已写入 `agent-host-onboard` §4.4。

涉及：`backend/agent/unisoc_scan_runner.py`、`backend/agent/tests/test_unisoc_scan_runner.py`、`.claude/skills/agent-host-onboard/SKILL.md`。

## Alternatives

- 改 toolkit 接受旧 argv（`-m sprd -d` + positional org）：上游工具已量产，改 Agent 成本更低。
- 在 Agent venv `pip install xlwt`：2026-08-31 canary 上 pip 无索引可达；`apt python3-xlwt` 更稳。

## Verification

- `python -m pytest backend/agent/tests/test_unisoc_scan_runner.py -q`
- Z258 canary host PlanRun #246：`scan_gt` timeout → `scan_result` → `dedup/246/unisoc/*_org.xls`
- `artifact_uri_matches_platform(..., "unisoc")` 对 `dedup/{id}/unisoc/` 路径为真

## Revisit

- 全 fleet `python3-xlwt` 纳入 Ansible `install_agent.yml` deb 依赖后，可从 onboard §4.4 手工步骤删除。
- Watcher w1 仍无 ADB pull；uniview 冒烟另开 P2。
