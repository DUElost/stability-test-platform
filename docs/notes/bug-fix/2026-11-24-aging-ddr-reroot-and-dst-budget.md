# Aging DDR 巡检/收尾自动重提 adb root 与 DST 离线预算上调（1.0.1）

Status: implemented
Class: bug-fix

## Decision

基于一台 Agent 主机（`172.21.x.x`）两台 P525AA 设备首次端到端跑测（PlanRun #1653）实测证据，将 `aging_ddr_check`、`aging_ddr_disarm`、`aging_ddr_finish` 升级至 `1.0.1` 并同步 `backend/schemas/pipeline_templates/aging_ddr.json`：

1. **重启后自动重提 `adb root`**：userdebug 设备经 `REBOOT_DST` / `REBOOT` 重启后 `adbd` 退回 `uid=2000`（`shell`），三族脚本在设备在线且 `sys.boot_completed==1` 后统一调用 `ensure_adb_root()` 自动恢复 `uid=0`，避免重启后无法访问 `/data/user/0/com.ape.aging/files/datastore/`。
2. **修复 `read_datastore_file` 将非 root 权限拒绝误判为 `absent`**：在单调用 shell 探针入口显式检查 `[ "$(id -u)" != "0" ]` 并返回 `__STP_AGING_DS_DENIED__` → `"denied"`，杜绝 POSIX `[ ! -f ... ]` 在 `EACCES` 下返回真导致误报 `absent` 的问题。
3. **默认 `max_offline_seconds` 从 `2400`（40 分钟）上调至 `14400`（4 小时）**：P525AA 实机两台单轮 `REBOOT_DST` 实测离线耗时分别为 `10,049s`（2h47m）与 `10,264s`（2h51m），40 分钟预算会在正常 DST 期间提前触发离线告警并拉长退避周期；4 小时默认预算覆盖全量 DST 窗口，同时保留按机型通过 step params 覆盖的能力。

## Alternatives

- **仅在文档中提示运维手工 `adb root`**：不可接受——每次 `REBOOT_DST` 与 `REBOOT` 都会重置 `adbd` 身份，必须由脚本侧自动自愈。
- **改用 `shell` 可读的导出目录 `exports/ddr_report_*.csv` 替代 DataStore 判断**：仅能覆盖已完结导出的终态，无法读取运行中进度（`current_module_pass`、`current_reboot_count`）、无法区分运行中与异常退出，也无法由 `aging_ddr_disarm` 清除续跑标志，故仍以 root 读 DataStore 为主通道。

## Verification

- PlanRun #1653 实机复核：两台设备补做 `adb root` 并触发 `manual-retry` 后，15s 内 `aging_ddr_check` 立即识别 `DDR:DONE_PASS` → `patrol_complete: true` → `aging_ddr_disarm` → `aging_ddr_finish` 归档 `.csv`（1205B）与 `.json`（3155B）至中心存储，PlanRun #1653 终态 `SUCCESS`。
- 单元测试与门禁结果见本 PR 实际运行输出（`scripts/run_pytest.py`、`check_script_packages.py`、`check_tool_manifest.py`、`ruff`、`check_governance_surface.py`）。

## Revisit

- 若后续出现更大内存容量机型导致单轮 DST 超过 4 小时，在对应 Plan 的 `aging_ddr_check.params.max_offline_seconds` 按机型调高，无需再改脚本默认。
