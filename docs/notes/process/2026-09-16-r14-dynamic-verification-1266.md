# R14 动态验证：部署/运维与可观测性归属套件隔离回归升「已完成」

Status: implemented
Class: process

## Decision

- 按 `PROJECT_REVIEW_PLAN.md` §5.1 转态条件，对 R14（#1266）做与 R01–R13 同口径的
  **隔离环境动态验证**（`unset TEST_DATABASE_URL`；升级门禁走 testcontainers；
  部署/Ansible/可观测契约以根目录与 Agent 单测为主，未触生产部署/恢复）。
- **基线**：`e6ff7e1f`（验证开始时 worktree HEAD = 当时 `origin/main`；R13 已合入
  #2376）。
- **结果**：归属套件 **301 passed / 0 failed**；`verify_control_plane_templates.py`
  → OK；容器巡检零残留。
- 同步把 §5 / §5.1 的 R14 行升「已完成」（与 R01–R13 并列；其余 1 区仍待验证）。
- F15（#1261）为文档边界项，无运行断言。

### F 项 ↔ 运行断言

| ID | Issue | 套件 | 条数 |
|---|---|---|---:|
| F01 | #1247 | install artifacts + selfcheck/host_id/pipeline_validator + playbook | — |
| F02 | #1248 | rsync protection + playbook | — |
| F03 | #1249 | upgrade_gate + host_maintenance + upgrade_gate_api | 39 |
| F04 | #1250 | agent_priv_boundary + target_paths | 52 |
| F05/F19 | #1251/#1265 | 含于 install_agent_artifacts | 12 |
| F06 | #1252 | test_agent_installer | 26 |
| F07 | #1253 | test_host_updater | 33 |
| F08 | #1254 | test_agentctl_contract | 6 |
| F09 | #1255 | test_pg_restore_drill | 6 |
| F10/F14 | #1256/#1260 | test_deployment_files + templates verifier | 19 |
| F11 | #1257 | test_prometheus_alerts_contract | 27 |
| F12 | #1258 | grafana contract + metrics gauges/auth/watchdog | 14 |
| F13 | #1259 | test_system_monitor | 22 |
| F15 | #1261 | 文档项 | — |
| F16 | #1262 | test_deploy_postgres_hardening | 5 |
| F17 | #1263 | ansible host_key + secret_source（+ playbook 分批） | 7 |
| F18 | #1264 | test_prepare_env | 6 |

## Alternatives

- **真机 Ansible 升级 / 真实 pg_restore / 生产 Grafana**：否决为本区升态门槛——
  单测与模板契约已锁定；真机 pending 见各 Note。
- **整目录 `tests/`**：否决。按 F 项唯一文件选中。

## Verification

| 批 | 结果 |
|---|---|
| install artifacts | 12 passed |
| selfcheck + pipeline_validator | 14 passed |
| update playbook | 9 passed |
| rsync mtbf protect | 4 passed |
| upgrade gate 三件套 | 39 passed |
| priv boundary | 52 passed |
| agent_installer | 26 passed |
| host_updater | 33 passed |
| agentctl contract | 6 passed |
| pg_restore_drill | 6 passed |
| deployment_files | 19 passed |
| prepare_env | 6 passed |
| prometheus alerts | 27 passed |
| grafana + metrics | 14 passed |
| system_monitor | 22 passed |
| postgres hardening | 5 passed |
| ansible host_key 等 | 7 passed |
| templates verifier | OK |
| **合计** | **301 passed** |
| 容器巡检 | 零残留 |

## Revisit

- R14「已完成」不含真机部署/恢复演练与生产 Grafana 挂载；方法能力边界见总纲
  §3 第 8 条。
- 下一区建议 R15（#1302，测试/CI 与工程治理）——最后一区。
