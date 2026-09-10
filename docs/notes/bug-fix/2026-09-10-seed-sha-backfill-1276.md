# 旧库 setup v1.0.2 seed sha 回填迁移（#1276）

Status: implemented
Class: bug-fix

## Decision

`backend/alembic/versions/o9p8q7r6s5t4_seed_att_stable_install_v102.py` 的
`content_sha256` 常量曾在合入 main 后被原地修正（#1171）。该迁移的 `upgrade()`
只在 `row is None` 分支 INSERT 写 sha，已存在分支仅翻转 `is_active`——因此
**在 09-08 13:25 合入到 09-09 18:31 修正之间执行过该迁移的库保留错误旧值**，
`backend/services/script_catalog.py:231` 比对 DB 与磁盘 sha 会持续报
`conflicts`（`:260-261`），直到人工 `force_rebaseline`。新库不受影响。

新增数据迁移 `t7u6v5w4x3y2_backfill_setup_v102_sha.py`（`down_revision =
s5t4u3v2w1x0`，当前单 head）：

- 精确回填 `sleep_setup` / `powercycle_setup` 的 `v1.0.2` 行：
  `UPDATE script SET content_sha256 = :new WHERE ... AND content_sha256 = :old`。
  以「仍是错误旧值」为条件，**不动**运维已 `force_rebaseline` 或有意改过磁盘
  脚本的安装。
- `downgrade()` 为 no-op（`pass`）：回填是数据修正，把已知错误值写回会覆盖
  合法 re-baseline 值，且无法区分「本次改过的行」与「本来就正确的行」；
  schema 无变化，无需回滚动作（先例：`f29aef122ec3` 合并迁移的 no-op downgrade）。

旧值取自 `db0d7e22`（修正前）的迁移常量，新值与仓库入口脚本 sha256 一致
（已实测 `41f40e54…` / `f36b155b…`）。

## Alternatives

- **继续原地改 `o9p8q7r6s5t4`（再改一次常量）**——放弃：对已迁移库同样无效，
  且「已发布迁移不可原地修改」是既有纪律（本次缺陷正是它的违例）。
- **不做迁移，只在部署 runbook 要求 `force_rebaseline`**——放弃：会依赖
  人工步骤，遗漏即长期 conflict；迁移可自动覆盖。
- **无条件 UPDATE 两行**——放弃：会覆盖 `force_rebaseline` 后的合法值；
  加 `AND content_sha256 = :old` 后语义精确。
- **downgrade 反向写回旧值**——放弃：见上，降级会污染未受本次影响的行。

## Verification

- `venv/bin/python -m pytest backend/tests/migration/test_seed_sha_backfill_1276.py -q`
  → **1 passed**（真 docker `postgres:16` 一次性容器往返：先升到
  `s5t4u3v2w1x0` → 人为写回旧 sha → 升 head 回正 → 降级 no-op → 再升幂等；
  同时断言已正确的 `powercycle_setup` 行不被误改）。
- `venv/bin/python -m pytest tests/test_alembic_heads.py -q` → **1 passed**
  （单 head 不变量成立）。
- 反事实：临时移除新迁移文件后，往返用例在 `assert _sha_of(...) == NEW_SHA`
  处失败（旧值保持）；恢复后通过。
- `python scripts/run_gates.py check:quick` → **[OK]（7 gates）**。

## Revisit

若将来出现第 3 次同类「seed 迁移常量事后修正」，应把「迁移常量与入口脚本 sha
一致性」做成门禁（如 `tests/test_script_seed_governance.py` 或 S 门禁的静态
比对），从机制上消除本类漂移，而非逐次补回填迁移。
