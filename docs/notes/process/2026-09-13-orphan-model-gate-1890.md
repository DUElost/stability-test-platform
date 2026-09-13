# 孤立 ORM 模型挂载门禁 + 复核指引（#1890-B2/C）

Status: implemented
Class: process

## Decision

#1890 的三个方向里，本单落**B 的一半 + C**（A 与 B1 见 Revisit）：

**B2｜孤立 ORM 模型挂载门禁**（`tools/dev/check_orphan_models.py`，源自 #734 评论
「新增模型引用计数为 0 则变红」）：

- 规则：`backend/models/` 下继承 `Base` 的类，若**类名**在
  「backend/（除 models、tests、alembic）+ tools/ + scripts/」零引用 → 变红
  （`GhostProbeModel` 实测红、清理后绿）；
- 语义：拦「新增了 ORM 模型却没有任何消费方」——#734 的 `ActionTemplate` 是
  「模型删了、表还在」；本门禁拦它的镜像形态「模型在、无人用」；
- 定义文件自身不算引用；`import backend.models.<x>`（仅 metadata 注册的模块路径
  导入）不算消费方；
- **存量豁免**：`_LEGACY_ALLOWLIST = {"PlanMigrationAudit"}`——ADR-0020 一次性
  迁移的审计表，写方是已执行完的迁移动作，表按 ADR 保留 ≥6 个月；豁免条目必须
  在 PR 写明理由；
- 接入：`run_gates.py` 新 gate `orphan-models`（进 `check:quick` 与 `check:pr`）
  → **8 gates**；`ci.yml` lint job 新增「孤立 ORM 模型检查」step；
  `GATE_TO_CI_ANCHOR` 登记（S5x 治理面校验通过）；`--self-test` 红绿双向自证。

**C｜复核指引**（`docs/notes/README.md` 新增「复核指引」节）：验证「迁移已删除 X」
时必须确认语句位于 `upgrade()` 而非 `downgrade()`——#1754 的「已先行完成」误判
即把 `f4a5b6c7d8e9.downgrade()` 里的 `op.drop_table("action_template")` 当成了
forward 迁移；给出判据（看所在函数段，必要时 `alembic current` + `\d <table>`
只读交叉验证）。

## Alternatives

- **按「表是否存在」反向扫迁移链**（解析 136 个 revision 的 upgrade/downgrade 段，
  比对 ORM 模型 ↔ 实际建表/删表）：更贴 #734 的「幽灵表」形态，但需要完整 SQL 解析
  （`op.execute` 里的裸 SQL、条件建表、schema baseline 自愈迁移都要处理），首版
  误报面大；先用「模型零消费方」这一确定性判据，后者留 Revisit；
- **把 `PlanMigrationAudit` 直接删掉而不是豁免**：弃——它的表是 ADR-0020 的审计
  证据（保留 ≥6 个月），删模型会与迁移链的 metadata 注册脱节；豁免 + 注释更诚实；
- **门禁只进 check:pr 不进 quick**：弃——扫描为毫秒级 AST/文本，进 quick 让本地
  提交前就能拦住（CI 侧仍有独立 step，锚点可查）；
- **B1（废弃端点前端零调用守卫）本次一并做**：弃——「废弃端点」需要先定义数据源
  （后端 `deprecated=True` 标记？维护列表？），且路径模板与前端 `${id}` 的匹配
  规则需要设计；属独立切片（见 Revisit）。

## Verification

- `python tools/dev/check_orphan_models.py --self-test` → 红绿双向通过
  （合成目录：GhostModel 红、WiredModel 绿、豁免后绿、models/ 自身引用不算）；
- **门禁反例**：临时新增 `backend/models/_ghost_probe_tmp.py`（零引用模型）→
  门禁 **exit 1** 并列出 `GhostProbeModel: backend/models/_ghost_probe_tmp.py`；
  删除后 **exit 0**；
- 实扫全仓：38 个模型类、1 个存量豁免（`PlanMigrationAudit`）→ 绿；
- `check:quick` → **[OK] check:quick (8 gates)**（新 gate `orphan-models` 在列）；
- `python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿
  （新锚点 `("ci.yml", "孤立 ORM 模型检查")` 登记校验通过）；
- `ruff`（新脚本 + run_gates）→ All checks passed。

## Revisit

- **A｜表级删除未做**：`action_template` 表在生产库仍在（forward 链从未 drop）。
  需 owner 决定窗口：生产只读核对（`\d action_template` + 行数 + 依赖）→ 新增迁移
  `op.drop_table`（含 downgrade 重建）→ 同步收敛 `schema_sync_baseline.json` 的
  `remove_table|action_template` / `remove_index|action_template|ix_action_template_active`
  两条白名单（表真删后白名单项应移除，否则基线长期兜住一个已不存在的差异）；
- **B1｜废弃端点前端零调用守卫未做**：需先裁定「废弃」的数据源（建议：后端路由
  `deprecated=True` 标记为唯一真源），再实现「前端调用 ↔ 路由表」比对（含路径
  模板 `${id}` 归一）；
- **更强的幽灵表门禁**：解析迁移链 upgrade 段的建表/删表，与 ORM `__tablename__`
  对账（本单只做模型侧），可覆盖「表在模型无」的另一半；解析裸 SQL 的成本高，
  若要做建议先做 `op.drop_table`/`op.create_table` 的显式子集；
- **`_LEGACY_ALLOWLIST` 的退出条件**：`PlanMigrationAudit` 表按 ADR 保留 ≥6 个月，
  到期归档/清理时应连同模型与豁免一起移除（届时门禁会自然提示）。
