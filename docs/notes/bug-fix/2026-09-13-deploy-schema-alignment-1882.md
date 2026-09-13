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

## Alternatives

- 仅去掉 `check-deploy-source` 的减号——会把分支/脏树也变成硬失败，引发
  start-limit；schema 单独硬拦更贴事故。
- 仅 `/health` 503——进程已对外接请求，双/三 guard 更稳。
- 无 `DATABASE_URL` 时 exit 1——会误伤纯 git 检查场景，选 WARN + 0。

## Verification

- `pytest tests/test_check_alembic_at_head.py backend/tests/test_health_schema_revision.py backend/tests/test_deployment_files.py -q`
- `python tools/dev/check_alembic_at_head.py`（无 DATABASE_URL → WARN exit 0）

## Revisit

- 生产 unit 文件需按模板重渲染后 `daemon-reload` 才生效。
- `/health` 对齐字段仅在 at-head 时返回；如需 dev 观测 mismatch，可另开 debug 端点。
