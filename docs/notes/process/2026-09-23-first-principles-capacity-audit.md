# 第一性原理与 150 host / 3750 device 容量审计落盘

Status: implemented
Class: process

## Decision

将本轮静态跨链审计单独写入
[`PLATFORM_FIRST_PRINCIPLES_CAPACITY_2026-09-23_ae232a3_codex.md`](../../reviews/PLATFORM_FIRST_PRINCIPLES_CAPACITY_2026-09-23_ae232a3_codex.md)。
150 host × 25 device = 3750 device 是未来验收目标；历史文档数字与之不同
不是本轮缺陷。分别标示配置不变量、历史事故记录、条件性容量推导、
未执行的隔离/真机/生产验收。不修改业务代码、ADR、生产配置或已有审查稿。

## Alternatives

- 先提高 PG 连接数、扩大连接池或启用多实例：不采纳；ADR-0047 仍 Proposed，
  未建立包含实例数与非应用连接的预算，也未取得 150/3750 工作负载。
- 把 `GET /devices` 的 1200、每 host 5 个 permit 或旧 100-host 阶梯当作
  fleet 硬上限：不采纳；三者分别是单响应护栏、瞬时操作限额和历史验收包线。
- 为 APK、Jar、PowerShell 分别增加核心 action/script_type：不采纳；先按
  宿主、工具契约、适配器与可校验发布物收敛，方向调整须另经 ADR。
- 把 `ArtifactUploader` 的 256 有界队列抬大或改成无界队列：不采纳；
  未测量前它只是把丢弃换成内存增长与 OOM，并掩盖下游背压。若验收要求
  产物零丢失，那是与 DLE 同形的持久 outbox 设计，属方向级变更、须另立 ADR。
- 复写 #3093/#3094/#735/#3075 或覆盖他人的未跟踪审查稿：不采纳；
  使用现有工单和独立报告，避免并行冲突与重复所有权。

## Verification

- 对照本地 `main@ae232a308ee6a46890accec810448c22f1fbdfc5` 的代码、
  相邻测试、DOC-MAP、ADR-0012/0018/0026/0027/0030/0033/0047/0051 与 rollout
  记录；`git ls-remote` 依次核到远端 `2fdd240b` → `512e61a8` → `ca38d079`。
  三者均不在本审计取证基线（`ae232a30`）内；用 `gh api compare` 逐提交比对
  文件清单，确认新增的 #3200/#3206 宿主内存防线与并行 Execution 的复审文档
  **未更动报告引用的源码/ADR 锚点**。收尾前主检出被并行 Execution 切到其
  docs 分支（`8752aaf6`，本仓已知现象），故本轮以 commit 比对而非工作树
  内容作为「远端是否影响结论」的判据。
- 基线时效复核（PR 前）：`git diff --name-only ae232a30 origin/main` = 636 文件，
  与报告引用面求交后仅 3 处变化——ADR-0051 升 v1.2（Phase 3 落地：`backend/agent/scripts/`
  版本目录 210 → 0、`AGENTS.md` / `script-versioning.md` 改口径、不可变门禁退役）、
  `backend/api/routes/scripts.py` 与 `scripts/run_gates.py` 行号位移。已在报告新增
  **F06.1 时效补充**：`_VALID_SCRIPT_TYPES` 仍只有 python/shell、tool-contract 门禁
  仍不带 `--entrypoint`（两条事实在新树上依旧成立）；但版本目录此前是包失败时的
  兜底路径，退役后 #3093/#3094 的「只验 fixture / 可静默 SKIP」从绿灯偏软升级为
  **无兜底的单点**，优先级应在包化终态下重评。ADR 自述的「fleet 48/48 strict」
  本轮未逐台复核，证据等级仍记为历史记录。
- 顺带纠一处历史账：2026-09-13 链 B 审查的 F-B1（`jira/` 产物永不清理、
  成为无索引孤儿）在本基线**已在代码面关闭**——`backend/storage_families.py`
  单源族清单 + `purge_run_storage_dirs` 覆盖 `devices|dedup|jira|_meta`
  与 `jobs/{job_id}`，且 `backend/tests/scheduler/test_retention_cleanup.py`
  刻意不写死族名。历史审查文件不改写，本轮不据此重复立案。
- `gh issue view` 只读核对 #703/#2959/#3093/#3094/#735/#3075/#3131/#3152
  的本轮状态；官方 SQLAlchemy/PostgreSQL 文档复核连接池峰值和保留连接
  的算术语义。未打开生产 env、数据库、API 或主机清单。
- 实际运行过的定向用例：
  `env -u DATABASE_URL -u TEST_DATABASE_URL .venv/bin/python -m pytest
  backend/agent/tests/test_heartbeat_parallel_probe.py -q` → **2 passed**；
  `vitest run src/utils/api/devices.test.ts` → **7 passed**。两者只证局部
  语义（并发探测与异常隔离、跨 1200 翻页与 short-read 出口）。
- 两份文档的相对链接检查无缺失；Canvas 用已有 TypeScript 编译器
  `node frontend/node_modules/typescript/bin/tsc --project <canvas 目录>/tsconfig.json --noEmit`
  检查通过。空白与定向测试结果在本轮收尾时更新；不因提交前门禁
  未运行而误记为验证通过。
- Pending：真实工具契约测试、后端/Agent/前端完整测试、`check:quick`、
  崩溃产物洪峰的端到端完整率测量、`measure_center_storage` 的 E-2/E-4/E-5
  目标基数重采、单 host 25 真机与 150/3750 fleet 分档验收；本轮静态审查
  不替代生产只读或隔离压测。

## Revisit

ADR-0047 裁决并引入跨实例预算守卫、#2959 关闭，或新目标编入权威容量
验收文档时重新对拍 F01/模型；B1/B2 与真实 25 台机压测、包全 fleet
`strict`/`package_active`、#3093/#3094 验收完成后按新的 main SHA 复审。
不原地改写本次取证基线与历史结论。
