# ruff 扫描集纳入根 tests/（门禁盲区收口）

Status: implemented
Class: process

## Decision

`ruff` 门禁的扫描集此前是 `backend/ tools/ scripts/`——**根 `tests/` 不在其中**，其
lint 债因此长期不可见（#2535 是靠人工另跑 `ruff check tests/` 才发现三处 F401/F841）。

本次把 **`tests/`** 加入两处（保持逐字一致，gate-parity）：

- `scripts/run_gates.py` 的 `ruff` gate（`check:quick` / `check:pr` / `check:full`）；
- `.github/workflows/ci.yml` 的 `Ruff` step（`lint` job，required check）。

**零风险翻转的时机**：#2535 已把 `tests/` 的三处债清零，且 `ruff.toml` **早已**为
`tests/**` 配了 `per-file-ignores`（`T20`/`F811`，与 `backend/tests/**` 同口径）——
即项目本来就预期 `tests/` 会被 lint，只是扫描集漏了它。

S5x 锚点无需新增：`ruff` 的锚点是 step **name**（`Ruff`），命令变更不影响配对。

## Alternatives

- **只在 `run_gates` 加、CI 不动**：否决。那会造成「本地红、CI 绿」，正是 #825
  gate-parity 要消除的形态（且 required check 才是拦截点）。
- **新增一条独立的 `tests-lint` gate**：否决。同一工具、同一版本、同一份 `ruff.toml`，
  拆成两条只会让「本地命令与 CI 命令一致」这条不变量更难维持。
- **顺手把 `tools/dev/*.py` 等之外的目录（如 `backend/alembic/versions/`）也纳入**：
  不做。已发布脚本版本（ADR-0020）与 alembic 历史 revision（#2258）**不可修改**，
  对它们报 lint 债无法修，只会制造噪声；`backend/` 已整体在扫描集内但 `ruff.toml`
  的 `extend-exclude` 会把这两类排除（与既有配置一致）。

## Verification

- **反例构造（先证伪再采信）**：往根 `tests/test_god_files_ceiling.py` 注入
  `import json  # counterexample` → `ruff check backend/ tools/ scripts/ tests/`
  **Found 1 error**（旧扫描集不会看到它）；恢复后全绿。
- 实测命令与结果：
  - `python -m ruff check backend/ tools/ scripts/ tests/` → All checks passed；
  - `python scripts/run_gates.py check:quick` → **[OK] check:quick (11 gates)**；
  - 两处命令 `grep -n "ruff check"` 逐字一致（gate-parity）。

## Revisit

- **`tests/` 的规则面刻意较宽**（`per-file-ignores` 放行 `T20`/`F811`）：与
  `backend/tests/**` 同口径，本次不动。若将来要收紧（例如禁止 `print`），应先改
  `ruff.toml` 再谈扫描集——顺序反了会一次性爆出大量存量。
- **门禁范围的下一步**：`check:quick` 现在 11 条 gate；若继续收编新扫描面
  （如 `frontend/` 脚本、`deploy/` 配置），按本单同一形态先清债再翻转，
  并在 `GATE_TO_CI_ANCHOR` 里确认锚点仍指向 PR 可达的 step。
