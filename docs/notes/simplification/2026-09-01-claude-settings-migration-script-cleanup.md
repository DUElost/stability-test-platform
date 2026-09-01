# Claude 权限语法收敛 + 迁移脚本归档 + dist-prod.bak 清理

日期：2026-09-01 · 类型：simplification

## 决定了什么

1. **`.claude/settings.json`**：删除全部 `Write(...)` deny 规则，只保留
   `Edit(...)`。Claude Code 2.1.x 文件权限匹配只认 `Edit(path)`；
   并存 `Write` 会在 `claude -p`（如 `run_gov_evals.py`）下告警/阻断。
2. **`tools/ci_check_migrations.py`**：移入 `tools/archive/`，原路径留
   stub（exit 2 + 指向新门禁）。脚本不支持 merge 迁移 tuple
   `down_revision`，且硬编码已删的 workflow/tool 模型，误跑必 7 项失败。
3. **`frontend/dist-prod.bak-*`**：保留最新 2 个回滚点，删除其余 14 个
   （~106 MB）。部署 skill 增加「只保留最近 2 个 bak」收尾步骤。

## 放弃的备选

- **重写 `ci_check_migrations.py` 支持 tuple down_revision**：功能已被
  `test_alembic_*` + `pr-migrate-empty-db` 覆盖，维护成本不值得。
- **把 bak 清理写进 cron**：部署频率低，skill 内联一步足够。

## 如何验证

- `.claude/settings.json` 仅含 4 条 `Edit(...)` deny。
- `python tools/ci_check_migrations.py` → exit 2 + 指向 pytest/CI。
- `ls frontend/dist-prod.bak-* | wc -l` → 2。
- `python scripts/run_gates.py check:gov` 不再因 Write 权限规则阻断
  （需 Claude CLI 已登录）。

## 何时重议

- Claude Code 权限 DSL 再次变更时复查 settings.json。
- 若需本地一键迁移检查，在 `run_gates.py` 挂 `pytest tests/test_alembic_*`
  而非复活 archive 脚本。
