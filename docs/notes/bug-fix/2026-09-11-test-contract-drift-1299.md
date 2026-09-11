# 测试/PR 模板/Agent Note 契约漂移收口（#1299，R15-F07）

Status: implemented
Class: bug-fix

## Decision

三处契约漂移同步（均为「文档 ↔ 机制互相矛盾」，在既有结构检查内补齐，不另建治理体系）：

1. `docs/development/testing.md` 宣传 `ALLOW_SQLITE_TESTS=1` 退路，而 `conftest`
   固定 testcontainers 且契约测试要求该变量不得存在 → 改为「无 SQLite 退路」表述并
   删除变量行；新增契约测试钉住（testing.md 不得出现该串且须提 testcontainers）。
2. `.github/pull_request_template.md` 称 security concerns 阻断合入，而
   `pr-agent.yml` 是异步顾问（非 required check、issue 跟进）→ 模板改写为顾问语义；
   新增契约测试钉住（模板须含「不阻断合入」且不得含旧表述）。
3. `check_governance_surface.py` S10 只校验头部（Status/Class），发现不了缺四节
   （#854 同类盲区）→ S10 扩展：`NOTE_HEADER_CUTOFF`（2026-09-05）起新增 Note
   必须齐备 `## Decision`/`## Alternatives`/`## Verification`/`## Revisit` 四节标题
   （只校验标题在场，内容质量不属结构检查），并为四节新增红/绿自测样例；存量 note
   沿用既有 cutoff 豁免策略不追溯。涉事 note
   `docs/notes/process/2026-09-08-dsh-web-harness-probe.md` 补齐 Decision/Alternatives。

## Alternatives

- **全量追溯补齐存量缺失 note（98/386 个）**：放弃——历史记录回填 Decision/
  Alternatives 属编造痕迹（当时的决策语境不可考），且与 S10 既有 cutoff 豁免策略
  不一致；
- **为 Notes 另建独立检查器/CI job**：放弃——issue 明确「在既有结构检查中补齐可
  确定契约，不另建治理体系」；
- **testing.md 保留 sqlite 说法但标注「已移除」**：放弃——直接删除更干净，契约
  测试以「不得出现该串」钉住。

## Verification

- `python tools/dev/check_governance_surface.py --check`：扩展后对涉事 note 报
  2 条 BLOCK（缺 Decision/Alternatives，红证）；补齐后全绿 ✅
- `--self-test`：14 条规则红/绿双向样例通过（含新增四节两例）
- `python -m pytest backend/tests/test_ci_and_test_harness_files.py` → **4 passed**
  （新增 2 条契约：testing.md 无 sqlite 退路 / PR 模板顾问语义）
- 红绿：未改文档时 2 条新契约测试失败
- `python scripts/run_gates.py check:quick`（含 gov-surface）通过

## Revisit

- 若要区分「四节标题在场」与「四节内容有实质」，可再议内容级检查（易滑向格式
  表演，暂不做）；
- cutoff 之后若再现批量缺节（如生成器产出），优先修生成器而非放宽检查。
