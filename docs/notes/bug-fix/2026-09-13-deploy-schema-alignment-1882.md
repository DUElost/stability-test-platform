# Deploy schema alignment guard (#1882)

Status: implemented
Class: bug-fix

## Decision

生产在 main 超前于 DB schema（缺 `host.retired_at`）时仍能重启，根因是
`check-deploy-source.sh` 只验 git 分支/干净度，且 systemd `ExecStartPre=-`
仅为 advisory。本 PR 在三处补 `alembic_version == code head`：

- `tools/dev/check_alembic_at_head.py`：有 `DATABASE_URL` 时阻塞不对齐；
  未配置则 WARN + exit 0（不挡无 DB 开发机）。
- `check-deploy-source.sh`：git 检查通过后调用上述脚本（人工 runbook 路径）。
- systemd：**硬** `ExecStartPre`（无减号）跑 `check_alembic_at_head.py`——
  `stability-backend.service`（upgrade 之后）与
  `stability-backend-nomigrate.service`（事故路径）均挂载；分支守卫仍保留
  `ExecStartPre=-`（避免脏工作树 + Restart=always 进 start-limit）。
- `backend/core/schema_revision.py` + `/health`：生产类环境
  （`ENV=production|internal`）且非 `TESTING=1` 时，schema 落后返回
  503 `SCHEMA_NOT_AT_HEAD`；对齐时在 healthy payload 附带
  `alembic_revision` / `alembic_head`。

2026-09-14 同形态复发后的补口（#1938）：pull 到含 #1907 新列的 main 后未跑
迁移即重启 → host 查询 500 累计 237,315 次、48 台主机心跳停更、错误日志
~170KB/s（≈14GB/天）刷屏；而**本机 routine 门禁全绿放行**——`scripts/run_gates.py`
任一 profile 都不跑上面的检查脚本。补齐两处：

- `scripts/run_gates.py` 新增 `schema-at-head` gate：调用
  `tools/dev/check_alembic_at_head.py`，纳入 `check:quick` / `check:pr` 首位
  （fail-fast；未配 `DATABASE_URL` 的机器/工作树仍 WARN 跳过恒绿），
  `check:full` 按「全 GATES − FULL_EXCLUDE」自动包含；
- 结构守卫 `tests/test_run_gates_schema_gate.py`：直接读模块断言 gate 目标、
  quick/pr 接线、首位顺序与 full 不排除，防静默漂移；
- 生产机 `/etc/systemd/system/stability-backend.service` 按本仓模板重渲染
  （补齐「启动前 `alembic upgrade head`」+ 硬 schema 门禁，`daemon-reload`
  后下次启动生效）——即下方 Revisit 第一条的收口。

## Alternatives

- 仅去掉 `check-deploy-source` 的减号——会把分支/脏树也变成硬失败，引发
  start-limit；schema 单独硬拦更贴事故。
- 仅 `/health` 503——进程已对外接请求，双/三 guard 更稳。
- 无 `DATABASE_URL` 时 exit 1——会误伤纯 git 检查场景，选 WARN + 0。
- （#1938）run_gates 补口只进 `check:full`——夜间全量才拦，白天 pull/重启
  事故时点不会触发，等于没拦。
- （#1938）改做成 GitHub Actions 检查——CI 无生产库访问权，脚本在 Actions
  里因未配置 `DATABASE_URL` 恒跳过；CI 侧迁移拦截仍由 required check
  `pr-migrate-empty-db`（空库迁移链）承担。

## Verification

- `pytest tests/test_check_alembic_at_head.py backend/tests/test_health_schema_revision.py backend/tests/test_deployment_files.py -q`
- `python tools/dev/check_alembic_at_head.py`（无 DATABASE_URL → WARN exit 0）
- （#1938）`pytest tests/test_run_gates_schema_gate.py -q`（接线结构断言）
- （#1938）`python scripts/run_gates.py check:quick`（本机=生产工作树：
  `schema-at-head` 绿；未配 `DATABASE_URL` 的他机/工作树为 WARN 跳过）
- （#1938）单元文件重渲染：`systemctl cat stability-backend` 与
  `deploy/control-plane/systemd/stability-backend.service` 渲染后逐行一致，
  且 `systemctl show stability-backend -p ExecStartPre` 含迁移与硬门禁两行

## Revisit

- ~~生产 unit 文件需按模板重渲染后 `daemon-reload` 才生效。~~ 2026-09-14（#1938）
  已完成：`stability-backend.service` 按模板重渲染并 `daemon-reload`（备份
  `stability-backend.service.bak-20260914`），下次启动起「先 `alembic upgrade head`
  再硬 schema 门禁」。`stability-backend-nomigrate.service`（事故路径备用）仍未
  装到生产机，按需再装。
- `/health` 对齐字段仅在 at-head 时返回；如需 dev 观测 mismatch，可另开 debug 端点。
