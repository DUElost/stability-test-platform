# R04 动态验证：项目/主机/设备/资源归属套件隔离回归升「已完成」

Status: implemented
Class: process

## Decision

- 按 `PROJECT_REVIEW_PLAN.md` §5.1 转态条件，对 R04（#961）做与 R01–R03 同口径的
  **隔离环境动态验证**（控制面 testcontainers PG + Agent 无 PG + Vitest）。
- **基线**：`c84c6824`（验证开始时 worktree HEAD；叠在 R03 动态验证文档分支之上）。
- **结果**：归属套件 **66 passed / 0 failed**；巡检「疑似残留 0」。
- 同步把 §5 / §5.1 的 R04 行升「已完成」（与 R01–R03 并列；其余 11 区仍待验证）。

### F 项 ↔ 运行断言

| ID | Issue | 套件 | 条数 |
|---|---|---|---:|
| F01 / F02 | #948 / #949（#1253 同形） | host_updater pip-retry + is-active 非零退出 | 2 |
| F03 | #950 | `TestUpdateHostPreserveSsh` | 3 |
| F05 / F14 / F04·F07 | #951 / #957 / #752 | bulk 无型号 422 + platforms 对拍 + #752 四写路径 | 6 |
| F06 | #952 | `TestProjectAttribution` | 4 |
| F08 / F15 | #953 / #958 | Vitest `AddDeviceModal` + `ProjectDetailPage` | 21 |
| F09 / F10 | #954 / #955 | `test_resource_pools.py` | 7 |
| F11 | #956 | wifi `host_group` 两例 | 2 |
| F17 | #960 | host_maintenance 全套 + 派发/claim 维护窗 | 17 |
| F18 | #730 | `test_heartbeat_parallel_probe.py` | 2 |
| F07 映射 | #704 | `test_discover_devices_normalizes_model_case` + mapping case | 2 |
| F16 | #959 | 文档项 | — |
| F12 / F13 / F19 | #937 / #939 / #796 | **交叉引用 R03 动态验证**（本区不重复计入 66） | — |

## Alternatives

- **整文件跑 `test_hosts` / `test_project_routes` / `test_host_updater`**：否决。非 R04
  归属断言会稀释本区证据；改用类 / 节点 / `-k` 精确选中。
- **真 SSH / 真机热更新 fleet 复跑**：否决为本区升态门槛——脚本生成与 API 契约已由
  单测锁住；fleet 属运维验收。
- **把 R03 已验的硬删/null 再算进 66**：否决。台账已映射既有 issue；交叉引用即可，
  避免双计。

## Verification

| 批 | 结果 |
|---|---|
| pip-retry + is-active | 2 passed |
| PreserveSsh | 3 passed |
| bulk/platforms/#752 | 6 passed |
| ProjectAttribution | 4 passed |
| resource_pools | 7 passed |
| wifi host_group | 2 passed |
| maintenance + dispatch/claim | 17 passed |
| heartbeat parallel | 2 passed |
| model case（agent + mapping） | 2 passed |
| Vitest AddDevice + ProjectDetail | 21 passed |
| **合计** | **66 passed** |
| 容器巡检 | 疑似残留 0 |

## Revisit

- R04「已完成」不含真机 ADB/SSH、fleet 热更新窗口压测或浏览器目视。
- F16 文档漂移若复发，走文档门禁，不重开本台账。
- 下一区建议 R05（#979）或主链 R06（#996）。
