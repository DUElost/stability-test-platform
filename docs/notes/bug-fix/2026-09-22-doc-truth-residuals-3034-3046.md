# 文档真值残留收口：#3034 第 8 条 + #3046 方向②（并逐单复验 6 张「已修未关」候选）

Status: implemented
Class: bug-fix

## Decision

本轮不写产品代码，只补两处**落地后没跟上的文档真值**，并把 6 张候选单在 main 侧逐单复验：

1. **#3034 第 8 条**（`gh api` 的 `EOF` ≠ 未执行）落在
   `.claude/skills/test-env-self-check/SKILL.md` 新增小节，而不是塞进「dev 栈与假
   Agent 驱动」编号列表：它不是夹具成员，但属于同一类错误观测（**工具报错文本与真实
   执行状态不一致**），与 #3/#7 同构。工具细节不复制——权威仍是
   `docs/development/repository-workflow.md` §GitHub 交互的幂等与重试（#2131）与
   `tools/dev/gh_comment_once.py`，技能只给判据与检索入口。
2. **#3046 方向②**（信号旁直接指向 runbook）落在 runbook 两侧：§1 L4 行的**处置格**
   点名 `adb_interfaces_missing`（运维反射是重启 adb server，而 §4 对照实验已证无效），
   §3 的 #2902 段补一条判据说明；同时改掉 §3 里"L4 在页面上唯一可见的信号"这句——
   #3049 合入后它已经是假话。
3. 同段两处**行号锚点**（`capacity_reporter.py:156` / `device_discovery.py:178`）改成
   符号锚点（`::_compute_health` / `::ensure_single_adb_server`）。实测两处均已漂移
   （真身在 :202 / :288）；这正是 #3017 判据的教训——钉行号的锚点在别人合法插行后
   变成假线索，而指符号不会因为上下移动说谎。

复验结论（main=`c2c8be51`，全部只读/隔离环境）：

| 单 | 判定 | 依据 |
|---|---|---|
| #3013 | **已修未关 → 关闭** | 三条勾选项全在 main（路由 + DedupReportCard + `jira/runs?plan_run_id=`），ADR-0033 v1.8/v1.9 已记 |
| #3034 | 7 条已落 + 第 8 条本轮补 → 关闭 | 关键词逐条命中三处落点；第 8 条此前无处可查 |
| #3046 | 方向① 已落 + 方向② 本轮补 → 关闭 | `adb_interfaces_missing` 三处登记齐全；方向③ 已由报单人撤回 |
| #2972 | **保持 open** | 报单人自陈「**未**接线 heartbeat / metric；本单保持 open」 |
| #2983 | **保持 open** | 第一切片只含解析/对账层，SSH 采集与调度未做 |
| #2957 | **保持 open** | A/B/C 权限裁决未落地，判据半边只是将"假绿"变成"自曝 dark" |

## Alternatives

- **把第 8 条并入既有编号列表**：读者路径更短，但会让"dev 栈/夹具"标题下挂一条
  GitHub API 判据——分类说谎，且技能名（test-env-self-check）与本条的触发时机无关。
- **只在 repository-workflow.md 补一句**：单一来源更好，但报单人点名要"进技能"，
  且工作流文档不在测试自检的检索路径上；现方案是"判据进技能 + 权威留在工作流文档"。
- **给 #3046 直接关单不补 runbook**：方向② 是报单人 09-22 明确新增的一条，跳过它
  关单等于替报单人裁决范围。
- **顺手把 §3 全部行号锚点清一遍**：射程外，只改本轮真正读到的那两句。

## Verification

- `env -u DATABASE_URL python -m pytest backend/tests/api/test_plan_run_artifact_download.py backend/tests/services/test_plan_run_artifact_download.py backend/tests/api/test_artifact_download.py -q` → **10 passed**（main `c2c8be51`）
- `npx vitest run src/components/plan-run/DedupReportCard.test.tsx` → **16 passed**
- 路由与过滤器存在性：`backend/api/routes/dedup.py:411`（`GET /api/v1/plan-runs/{run_id}/artifacts/{artifact_id}/download`）、`:424`（DLE zip）、`:304`（`/api/v1/jira/runs?plan_run_id=`）
- 漂移锚点实测：`rg -n "adb_multiple_servers" backend/agent/capacity_reporter.py` → 追加点在 `:202`；`rg -n "def ensure_single_adb_server" backend/agent/device_discovery.py` → `:288`（文档原写 :156 / :178）
- `env -u DATABASE_URL python scripts/run_gates.py check:quick`（本 worktree，含两处文档改动与新 Note）→ **[OK] check:quick (14 gates)**

## Revisit

- L4 的**告警面**（连续 ≥2 拍去抖 + paging）与 #2972 类恢复动作仍另单——本单只闭观测面。
- 若后续给 health reason 建"文档必须点名"的门禁（`tests/test_host_health_reason_surface.py`
  目前只覆盖 指标/词表/前端标签 三处），runbook 这条人肉双向指引仍会漂移；判据值得前移。
- `gh` 的 transport 故障与 `curl` 的差异若复发，应把旁路动作固化进 `tools/dev/`（现在只是判据）。
