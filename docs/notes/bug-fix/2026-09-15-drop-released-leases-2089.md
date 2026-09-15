# 删除 `released_leases` 死字段（后端 + 前端类型两侧）

Status: implemented
Class: bug-fix

## Decision

删除 `abort_plan_run` / `abort_jobs_for_host` 返回体、审计 `details`、日志与 docstring 里的
`released_leases`，并同步删除前端 `frontend/src/utils/api/types.ts` 里
`PlanRunAbortResult.released_leases`。

### 为什么是「删」而不是「补」

该字段自 `#1985` 前后就**恒为 0**：`plan_run_abort.py` 里只 `released_leases = 0` 后读出，
**从未自增**；真正释放租约的是 reconciler / recycler（abort 只置 `abort_requested`，由 Agent
退出后各自回收）。也就是说它承诺了一个**该路径根本没有的动作**。

于是调用方看到 `released_leases: 0` 会合理地读成「本次 abort 没有释放任何租约（所以没有并发
副作用）」，而事实是「这个路径从不释放租约」——这是**假信号**，比缺字段更坏。按本仓既有的
「文档宣称 > 实现」口径（见共享行加锁表 Revisit），选择删除。

### 为什么两侧一起删

`#787`（`docs/notes/bug-fix/2026-09-12-types-schema-drift-787.md`）立过的纪律是
**「后端是权威 schema，`types.ts` 以它为入口同步」**——所以 `types.ts` 里存在该键本身
并不算错（当时后端确实返回它）。只有当**后端不再返回**时，前端类型才必须一起删；反之只删
前端会立刻违反 787 的硬不变量。本单两侧同时删，保持该不变量成立。

### 保留 / 顺带确认的事实

`docs/reviews/PLATFORM_HEALTH_REVIEW_2026-09-03.md` 里 [C] 项记录过 `PlanRunAbortResult` 与
后端键漂移的历史——那是**历史审查记录**，描述当时的观测，**不回改**（本单只更新
`docs/notes/` 下仍作为活契约使用的登记项）。

## Alternatives

- **保留字段，改 docstring 说明「恒为 0」**：否决。字段名与返回位置都指向「本次释放了几个
  租约」，靠 docstring 抵消不了误读；而且它已经漂进前端类型，等于把假信号写进契约。
- **实现它（abort 自己释放租约并计数）**：否决。那是**行为变更**（ADR-0022 D7 把租约释放交给
  reconciler / recycler，abort 只请求），与「清理死字段」不同量级，需独立裁决。
- **只删前端类型**：否决。违反 `#787` 的「后端权威」——前端类型必须镜像后端实际返回。
- **顺手把 `types.ts` 里其它可疑键一起清**：否决。`#787` 已按现值逐项收口过一轮（删了 6 个
  幽灵键）；本单只处理已确认死的那一个，避免把「清理」做成第二轮未经核对的漂移。
- **先加 deprecation 周期再删**：否决。它是只读键、无消费者、无版本化 API 契约文档；本仓的
  前端类型与后端同仓同步发布，没有跨版本兼容面。

## Verification

| 项 | 结果 |
|---|---|
| 全仓残留（`*.py` / `*.ts` / `*.tsx`） | `released_leases` / `releasedLeases` **零命中**（仅历史审查文档按上面说明保留） |
| 前端 `npx tsc --noEmit` | **exit 0** —— 按 `#787` 的判据，「删除后 tsc 零错误」即证明**无消费点** |
| 受影响的 API / 服务测试 | `test_plan_run_abort_api.py` + `test_admission_queue_step2.py` → **53 passed** |
| 根 `tests/` | **591 passed** |
| `ruff check` / `check_governance_surface.py --check` | All checks passed / `[OK]` |

## Revisit

- **若将来真需要「释放了几个租约」**：应在**真正释放的位置**（reconciler / recycler）埋计数器，
  而不是在 abort 的返回体里复活这个键——abort 本来就不释放租约，这是 ADR-0022 D7 的设计。
- **跨语言契约仍未机制化**（`#787` 的 Revisit 已登记）：本次仍靠「grep 全仓 + tsc + 人工核对」
  闭合。若再出现第二例键漂移，应按 `#787` 的结论上「TS ↔ 后端 schema 自动对拍」。
- **审计 `details` 的消费者**：本单按「无消费者」删除了 `details` 里的该键；若有外部审计
  分析脚本依赖它，需在那侧改用 abort 事件里 `abort_requested_jobs` 等仍存在的字段。
