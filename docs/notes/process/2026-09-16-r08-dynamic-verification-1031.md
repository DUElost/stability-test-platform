# R08 动态验证：脚本库/版本与外部工具接入归属套件隔离回归升「已完成」

Status: implemented
Class: process

## Decision

- 按 `PROJECT_REVIEW_PLAN.md` §5.1 转态条件，对 R08（#1031）做与 R01–R07 同口径的
  **隔离环境动态验证**（`unset TEST_DATABASE_URL`；控制面脚本 API 走 testcontainers，
  Agent/脚本单测不触生产库）。
- **基线**：`767ef9ea`（验证开始时 worktree HEAD；叠在 R07 动态验证文档分支 /
  PR #2337 tip 之上）。
- **结果**：归属套件 **171 passed / 0 failed**；容器巡检零残留。
- 同步把 §5 / §5.1 的 R08 行升「已完成」（与 R01–R07 并列；其余 7 区仍待验证）。

### F 项 ↔ 运行断言

| ID | Issue | 套件 | 条数 |
|---|---|---|---:|
| F01+#790 | #790 | `test_scripts.py` + `test_scripts_default_params.py`（含 F06） | 37 |
| F02 | #1025 | `test_flash_firmware_v1311.py` | 4 |
| F03/#810 | #810 | `tests/test_mtbf_run_dir_binding_810.py` | 11 |
| F04 | #813 | `test_powercycle_scripts.py` | 64 |
| F05 | #888 | `tests/test_script_version_immutability_gate.py` | 8 |
| F06 | #1026 | 含于 F01 `test_scripts.py` SHA/路径校验 | — |
| F07 | #1027+#812 | `test_monkey_setup_v237.py` + `test_base_tools_rc_guards.py` | 18 |
| F08/F09 | #816 | `test_device_script_misc_fixes.py` | 16 |
| F10 | #1028 | `test_check_state_per_job.py` | 6 |
| F11 | #1029+#787 | 文档/类型项（无本区运行断言） | — |
| F12/F13 | #1030+#810 | `test_mtbf_finish_v150.py` | 7 |

## Alternatives

- **整目录 `backend/agent/tests/` + 全 scripts API**：否决。按各 issue Agent Note 的
  精确套件选中，避免稀释证据。
- **真机刷机取消 / WiFi 特殊字符 / 磁盘写满**：否决为本区升态门槛——单测已锁定
  契约；真机环境见各 Note pending。

## Verification

| 批 | 结果 |
|---|---|
| F01/F06 scripts API | 37 passed |
| F02 flash USB gate | 4 passed |
| F03 MTBF run_dir 绑定 | 11 passed |
| F04 powercycle atomic resume | 64 passed |
| F05 immutability gate | 8 passed |
| F07 monkey_setup + base_tools rc | 18 passed |
| F08/F09 device script misc | 16 passed |
| F10 check_state per job | 6 passed |
| F12/F13 MTBF finish 唯一性 | 7 passed |
| **合计** | **171 passed** |
| 容器巡检 | 零残留 |

## Revisit

- R08「已完成」不含真机刷机/收取窗口注入与生产规模；方法能力边界见总纲 §3 第 8 条。
- F11 文档/类型漂移若再出现，走 docs/前端类型门禁，不重开本台账。
- 下一区建议 R09（#1055，设备日志采集与异常事件）。
