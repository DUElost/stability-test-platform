# Skill 建设台账逐项裁定与首批落地（#843）

Status: implemented
Class: process

## Decision

对 `#843` 台账全部候选按「三看（发现）+ 四判据（防膨胀）」逐项裁定；本轮**先建 3 个
高价值 skill**（用户裁决），其余按裁定降级/延缓。裁定证据：近 30 天
`backend/api/routes` 160 次提交 / `frontend/src/utils/api/types.ts` 57 次提交；新增脚本
版本目录 0 次但 2026-07-31 SHA 漂移事故在案（判据 3 高代价成立）；权威文档
（production-diagnostics / script-versioning / dependencies-and-quality）均在。

### 裁定表

| # | 候选 | 裁定 | 依据 |
|---|---|---|---|
| 1 | `add-api-endpoint` | **建（整体）** | 高频 + 漏同步 `types.ts` 属静默失败；整体 SOP 使契约同步不可跳过 |
| 2 | `sync-api-contracts` 分段 | 不建（并入 1） | 同一触发场景两个 skill 竞争加载；同步段为 #1 的 SOP 步骤 |
| 3 | `prod-db-readonly-diagnose` | **建** | 判据 3=生产数据；坑明确（`backend/.env` 不含 `DATABASE_URL`） |
| 4 | `script-version-retire` + `script-version-authoring` | **合并建 `script-version-lifecycle`** | 同一失败模式/同一权威文档（07-31 事故）；触发描述覆盖两场景 |
| 5 | 依赖 lock 重生成 + 漂移排查 | 不建 | 命中降级条件：漂移检测已在 CI（gate）、`regenerate-lock.sh` 已机械化 |
| 6 | `diagnose-device-stall` | 延缓（下批） | 判据 3 + 跳步误判成立；本轮按裁决先建 3 个 |
| 7 | `signal-link-health-triage` | 延缓（下批） | 判据已固化，防 agent 重新推导既定取舍 |
| 8 | `scan-artifact-gap-triage` | 延缓（下批） | 同上；「部分报表优于零报表」为既定取舍 |
| 9 | admin token / hot-update env 分级 | 不建（留 `control-plane-deploy` §0） | 已覆盖；独立建造成碎片化 |
| 10 | C 类 3 条（空白行 / AEE 链 / 测试库红线） | 确认不建 | 分别由 gate / docs / `test-env-self-check` 覆盖 |

### 本轮落地（3 个，`.claude/skills/`）

统一五段结构：触发描述 frontmatter → 前置检查 → SOP → 后置验证 → 踩坑守卫（薄，
不复制易变事实，指向权威文档）：

1. `add-api-endpoint` —— Schema→路由→服务→`types.ts`→前端客户端→测试 全链路；
2. `prod-db-readonly-diagnose` —— 凭据来源/直连姿势/红线（指向
   `docs/operations/production-diagnostics.md`）；
3. `script-version-lifecycle` —— 新建（全量副本 + SHA 校验 + 扫描）与退役
   （usage 判读 → `is_active=false` → 409 处置）双路径（指向
   `docs/development/script-versioning.md`）。

## Alternatives

- **分段建 `sync-api-contracts`**：放弃——同触发场景双 skill 竞争，且「漏同步」正是
  全链路 SOP 要防的步骤，拆出后反而更易被跳过；
- **`script-version-retire` / `authoring` 分立**：放弃——同一权威文档与同一事故守卫，
  描述可覆盖两场景，分立占名额；
- **为每个 harness 复制一份 skill**（`.codebuddy/skills/` 等）：放弃——适配表已定
  「Claude Code 读 `.claude/skills/`、其他 harness 走 AGENTS.md/文档地图」，复制会
  造成多份漂移源；
- **依赖 lock 建 skill**：放弃——「能机械判定 → gate」台账降级条件命中。

## Verification

- `python tools/dev/check_governance_surface.py --check` → 全绿（含 **S7**：三个新
  SKILL.md 的 name 与目录一致、description 非空）；
- 内容逐条对照权威文档核对（`script-versioning.md` / `production-diagnostics.md` /
  `DOC-MAP.md` 与 `AGENTS.md` 硬不变量），未发明新约束；
- `python scripts/run_gates.py check:quick` 通过。

## Revisit

- 下批 3 个 triage skill（`diagnose-device-stall` / `signal-link-health-triage` /
  `scan-artifact-gap-triage`）：触发条件 = 任一场景再次手工执行（判据 1 成立）即建；
- 存量 4 skill 的「踩坑守卫」段**统一显式化**（归拢既有负向约束，不发明新约束）；
- `.claude/skills` 对非 Claude harness 的可见性说明（harness-adapters 补一行）；
- 台账下一次例行复核窗口：2026-10-03（或新增第 5 个 skill 前——本轮已提前满足该
  前置审查）。
