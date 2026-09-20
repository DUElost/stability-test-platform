# Agent 测试跨边界 import 收敛（#739 面①）——第二批：剩余 7 个全部迁移，棘轮清零

Status: implemented
Class: bug-fix

## Decision

第二批（收口批）：把棘轮上剩余 **7 个**文件全部迁到 `backend/tests/` 对应域，
`_CONTROL_PLANE_IMPORTS` **清空**——`tests/test_agent_test_import_ratchet.py`
从「存量台账」转为**终态不变式守卫**（agent 测试不得再 import 控制面，任何越界即红）。

| 原文件（`backend/agent/tests/`） | 去向 | 备注 |
|---|---|---|
| `test_aee_metadata.py` | `backend/tests/core/` | agent↔core 的 metadata 对齐（双端 parity 断言） |
| `test_login_lockout.py` | `backend/tests/core/` | 纯逻辑（线程/时序），迁入后复用 backend/tests 的 `_reset_login_lockout_state` |
| `test_pipeline_validator_parity_738.py` | `backend/tests/core/` | **有意**的双端 parity；源码锚点从 `parents[1]/[2]` 改为仓库根反查 |
| `test_cron_scheduler.py` | `backend/tests/scheduler/` | 控制面 cron（overlap / dedup / retention），mock 驱动 |
| `test_mtbf_suite.py` | `backend/tests/services/` | `services.mtbf_suite` 单元；共享 fixtures 路径改为按仓库根反查（数据引用，非 import 边界） |
| `test_p3_3_multi_instance.py` | `backend/tests/realtime/` | 多实例（sid registry / RPC / singleton 调度包装） |
| `test_saq_scan_pipeline.py` | `backend/tests/tasks/` | scan_task / merge / auto_archive_sweep（最大一处，1287 行） |

两批合计：**迁移 12 + 就地解耦 2 = 14**，与原始存量一致。

**迁移期修的两处路径陷阱**（`__file__` 相对锚点在搬走后会静默指向别处）：
`test_mtbf_suite` 的 `fixtures/mtbf`（数据仍留在 agent 测试目录，被 `test_mtbf_scripts`
共用）与 `test_pipeline_validator_parity_738` 的双端源码路径——都改为**仓库根反查**并留注释。

**代价（与第一批同）**：迁出用例退出 PR 门禁的 agent step，改由夜间 `backend-test`
（与本地 `check:full`）覆盖；moved 用例在 backend/tests 的 conftest 下会附带一次
DB 会话/TRUNCATE（它们本身多为 mock 驱动、不读写库），成本可接受。

## Alternatives

- **保留 `test_pipeline_validator_parity_738` 在 agent 套件不迁（原「有意」条目）**：
  弃——边界守卫是方向性的，parity 断言需要同时 import 两份实现，放在控制面套件
  （`backend/tests/core/`）同样成立，且能让棘轮真正清零，避免「永久例外」把台账变成许可。
- **为 moved 用例豁免 backend/tests 的 autouse DB fixture**（新增 no_db marker 机制）：
  弃——为一个搬家动作引入共享 conftest 的新机制，收益（省一次容器/TRUNCATE）不抵
  影响面（backend/tests 全体用例的 fixture 语义）。
- **迁移时同时清理 old 目录的空 fixture/残留**：弃——`fixtures/mtbf` 仍被
  `test_mtbf_scripts.py` 使用，不动。
- **第二批再拆两半（先小后大）**：弃——7 个文件均 mock 驱动、无 conftest fixture 依赖，
  一次迁完可让棘轮一次清零；拆两半会让「清零」延后一批而无额外收益。

## Verification

- 迁移后 7 文件在新位置：**151 passed**（15.7s，含 testcontainer 启动）；
- 残留越界扫描（AST，全 `backend/agent/tests/*.py`）：**0 个文件**；
- 边界守卫三件套（ratchet / import boundary / env selfsufficiency）：**14 passed**；
- `ruff check`（7 个 moved 文件 + 棘轮）→ All checks passed；
- agent 套件（`env -i`，干净环境）：**2053 passed**（第二批迁出 151；第一批后为 2204，
  差值与本批迁移数一致）；
- `python scripts/run_gates.py check:pr` → **[OK] check:pr（21 gates）**（含 agent-tests /
  agent-tests-collect / repo-level tests / pr-migrate 空库迁移 + seed 身份对拍）。

## Revisit

- **清单已清零**：任何新增 agent→控制面 import 直接被
  `test_no_new_file_imports_control_plane` 判红；确有例外时在 `_CONTROL_PLANE_IMPORTS`
  登记并写明「为何不能迁移/解耦」。
- **覆盖位**：迁出用例现由夜间 `backend-test` 与本地 `check:full` 覆盖。若夜间对这批
  文件出现稳定失败，先按「纯净 main 同文件对照组」判断是否与搬家相关（本次搬家只动
  文件位置与两处路径锚点）。
- **#739 其余面**：面②（431 处静默异常吞咽治理）与面③（`tools/archive/` 墓碑清理 +
  依赖收敛）仍未动，各自可独立认领；本单保持 open 直至三面收敛。
- **fixtures 共享**：`backend/agent/tests/fixtures/mtbf` 现被两侧共用（agent 的
  `test_mtbf_scripts` + 控制面的 `test_mtbf_suite`）；若后续 agent 套件再瘦身，
  可考虑把该数据挪到中立位置（`tests/fixtures/`），属独立小单。
