# deploy compose PG 镜像默认可配并对齐生产 17

Status: implemented
Class: bug-fix

## Decision

#2945：`deploy/postgres/docker-compose.yml` 与根 `docker-compose.yml` 曾硬编码
`postgres:15-alpine`，生产库是 **PG17**——本地栈测不出大版本行为差（#2849 顺带）。

本 PR：

1. 两处改为 `image: ${POSTGRES_IMAGE:-postgres:17-alpine}`；
2. `.env.example` / README 写明生产 major=17 与覆盖口；
3. 守卫 `tests/test_postgres_compose_image_2945.py` 钉「默认 major == 生产 17」；
4. `test_alembic_upgrade_head_from_empty_on_postgres_17`：空库 `upgrade head` 在 17 冒烟。

**不改**：CI `pr-migrate` / 日常 testcontainers 主路径仍用 16（速度与存量夹具）；
PG15 覆盖仍由 #2863 专项承担。

## Alternatives

- **只改默认到 17、不可配**：复现 15 行为要改文件，易再次钉死。
- **CI 全面改 17**：与大量 `postgres:16` 夹具重绑，超出本单「模板对齐」射程。

## Verification

- `pytest tests/test_postgres_compose_image_2945.py -q`
- `pytest tests/test_alembic_upgrade.py::test_alembic_upgrade_head_from_empty_on_postgres_17 -q`（需 Docker）
- `check:quick`

## Revisit

- 生产升到 18 时：先改 `PRODUCTION_PG_MAJOR` 与 compose 默认，再关本守卫红。
- 若要把 CI 主路径迁到 17，另起单评估夹具面。
