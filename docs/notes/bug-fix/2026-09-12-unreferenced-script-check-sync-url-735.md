# 零引用退役诊断工具崩溃修复（#735 §2 验收项 1）

Status: implemented
Class: bug-fix

## Decision

`backend/scripts/check_unreferenced_script_versions.py` 的 `main()` 把
`resolve_database_url()` 解析出的**异步驱动** URL 直接交给同步 `create_engine`——生产库
形态即 `postgresql+asyncpg://`，首次连接即抛
`MissingGreenlet: greenlet_spawn has not been called`（#735 §1.3 崩溃现场）。

修复：在 `create_engine` 前插入既有归一化入口
`backend.core.database.normalize_sync_database_url`（`asyncpg`/`psycopg2`/裸
`postgresql://` → `postgresql+psycopg://`；`sqlite+aiosqlite://` → `sqlite://`）。
同仓已有先例：`backend/scripts/migration/preflight_execution_protocol.py:37`、
`backend/core/leader_election.py:65`。

**范围限定**：只修工具崩溃，不改查询语义、不改退役判据。`compute_reference_counts`
的 SQL 与「active 且零引用」候选口径零改动（`--json` 输出结构不变）。本单**不含**退役
47 个零引用活跃版本的执行动作——那是对生产库的**写操作**，按 AGENTS.md「本机可能同时
是生产控制面和生产数据库宿主」纪律须单独走生产变更流程，不在本 PR 内做。

## Alternatives

- **在脚本内联做 URL 字符串替换**：弃——同一转换已在 `backend/core/database.py` 有单
  一实现且被两处复用，内联会复制出第二个真源，将来驱动矩阵变化时漏改一处即复现本 bug；
- **改用 `create_async_engine` + `asyncio.run` 把工具改成异步**：弃——工具是只读诊断、
  无并发需求，为绕开一个 URL 归一化而引入事件循环与异步会话，成本远大于收益，且会改变
  工具的可嵌入性（`compute_reference_counts` 目前接受同步连接/会话，测试直接复用）；
- **只修不测**：弃——崩溃点在 `main()`，而既有测试只覆盖 `compute_reference_counts`，
  这正是该 bug 能存活至今的原因（见 Verification）。

## Verification

- **红绿双向**（关键）：新增 `test_main_normalizes_async_url_before_sync_engine`——把
  `create_engine` 换为捕获 URL 的 fake，断言交给它的是
  `postgresql+psycopg://...` 且不含 `+asyncpg`；
  - 还原为修复前的 `create_engine(url)` → **1 failed**（该测试失败）；
  - 修复版 → **6 passed**；
- 旧行为复现：直接以 `postgresql+asyncpg://` 调同步 `create_engine` 并连接，得
  `MissingGreenlet`（与 #735 §1.3 现场一致），确认修复针对的是真实崩溃而非假想路径；
- `normalize_sync_database_url` 三种生产形态参数化断言（asyncpg / 裸 postgresql /
  aiosqlite）→ 通过（与 `backend/tests/test_database_config.py` 既有断言同口径）；
- **工具端到端实跑**：`python -m backend.scripts.check_unreferenced_script_versions`
  对已配置库完成真实 SELECT 并正常出表（修复前该命令即 #735 §1.3 的崩溃命令）；
- `python -m pytest backend/tests/test_script_reference_check.py
  backend/tests/test_database_config.py -q` → **15 passed**；
- `python -m ruff check`（两改动文件）→ All checks passed；
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (7 gates)`。

未跑：生产库只读统计（引用分布 / 行数占比）——属 #735 执行范围的独立待验证项，本单不代跑。

## Revisit

- **退役动作仍待执行**：本单只修工具。#735 §2 验收项 2（退役零引用活跃版本）与
  `P1「不可变收窄至零引用退役」` 均未动——P1 至今无 ADR 承接，且其启动前置
  （`plan_step` 引用分布 + 行数占比两个只读统计）仍未做；
- **同类崩溃面未普查**：本单只修 #735 点名的这一处。仓内其他 `create_engine` 调用点若
  同样直接吃 `resolve_database_url()` 结果，会在同样条件下复现同类崩溃——是否做一次
  全仓普查（可加门禁）留待裁决，本单不顺手扩大范围；
- 若将来 `normalize_sync_database_url` 的驱动映射变化，本工具随之受益（这正是选择复用
  单一入口而非内联替换的理由）。
