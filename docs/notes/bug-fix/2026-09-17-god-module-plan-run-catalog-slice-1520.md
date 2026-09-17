# God-module 垂直切片：列表/详情/jobs 装配层下沉 service（#1520 第二刀）

Status: implemented
Class: bug-fix

## Decision

叠在 watcher 切片（PR #2545）之后，把 plan_runs.py 剩下的最大只读域——
**`GET /plan-runs`（列表页）、`GET /plan-runs/{id}`（详情）、`GET /plan-runs/{id}/jobs`**
的装配层整族抽到 `backend/services/plan_run_catalog.py`：

| 符号 | 职责 |
|---|---|
| `build_plan_run_list_page` | list/count/stats 三查询共享过滤器 + 分页 + plan 名/设备数批量装配（#747 distinct device 口径在此） |
| `build_plan_run_detail` / `build_plan_run_jobs` | 详情与 jobs 列表装配（trace 分组、serial 富化） |
| `plan_run_out` 族（`_plan_run_out`/`_job_out`/`_step_out`/`_project_run_context`/`_plan_run_capabilities`/`_apply_plan_run_list_filters`） | DTO 装配 + dispatch_state deadline/stale/retryable 派生 |

路由退化为 Query 声明 + `ok(build_*(...))` 薄壳；私有名经路由 `noqa: F401`
re-export 保既有测试导入（`test_plan_runs_api` 从路由导入 `_plan_run_out`）。

**`plan_runs.py` 1264 → 987**（本刀 -277；chain 切片由 cursor 在窗，落 main 后
预计 ~850）。

### 为什么是这一刀（并行边界）

领单前查窗：cursor 已 declare **plan_run chain 下沉**（CODING/LIVE，其 WIP 已在造
`backend/services/plan_run_read_common.py`，导出 `iso`/`duration_seconds`）。chain、
timeline、events、devices 已被占或已抽；**list/detail/jobs 与其符号零相交**
（chain 用 `_chain_node_from_run` + `duration_seconds`，不碰装配层），是唯一不需要
协调即可并行的整域。`--force` 领单 = 同一 issue 面并行切片的边界人工确认（契约 §3.4）。

### 时间格式化副本就地收编进 `plan_run_read_common`（rebase 时机的顺水推舟）

本刀初版在 catalog 里自带 `_iso`/`_aware` 过渡副本（出口原写在 Revisit：等 chain 切片
的 `plan_run_read_common` 落地后统一收编）。rebase 到新 main 时 **read_common 已随
chain PR #2560 合入**——出口条件当场成立，于是把 catalog/watcher/export 三处服务内
`_iso`（含 catalog 的 `_aware`）全部收编为 `plan_run_read_common.iso/aware` 单源。
**不新建第二个 common**（§3.5：同一主题不并行造权威位），只是消费已合入的权威位。

顺带修掉一个我 PR #2545 带进 main 的缺陷：`plan_run_watcher_summary.py` 里 `_iso`
被**重复定义两次**（F811 自遮蔽，第二份覆盖第一份、语义恰好相同所以零行为影响——
CI 默认规则集不含 F811，本地 quick 也照跑绿灯，属门禁盲区）。收编即消除。
main 上 `plan_runs.py` 的 `_iso` 路由副本（log-events/summary/artifacts 还在用）
**不在本刀动**：那是 chain 切片之后的路由公共区，归属其所有者排期。

### 顺带修复：main 上的红测试（与本刀无因果，如实归因）

`test_plan_run_abort_api::test_abort_running_job_releases_lease_only_after_agent_ack`
在 origin/main（`21f34595`）上已红：agent 侧各切片把 `complete_job` 的 broadcast
调用移进 `services/agent_completion` 后，测试仍 patch
`backend.api.routes.agent_api.broadcast_run_job_update`（属性已不存在 →
`AttributeError`）。required check `pr-agent-tests` 只跑 agent 套件、盖不住这类
控制面测试的跨模块 patch 目标漂移，所以红了没人见。修法即既有纪律
**「patch 打在调用时解析的模块上」**：两处目标改指 `agent_completion`。
随本刀带走而不另开单：它是本刀验证路径上的红灯，且改动只有 2 行 + 注释归因。

## Alternatives

- **等 chain 合入后再动**：弃——符号面零相交，唯一文本相交在 import 区（rebase 可吸收）；
- **本刀顺手把 watcher/export 的 `_iso` 副本一起收敛**：弃——那要落地 read_common 的
  API 裁决，属 cursor 在窗主题，不抢；
- **列表与详情拆两刀**：弃——`_plan_run_out` 是三端点共同的装配根，拆开必造中间层。

## Verification

- 16 文件控制面回归批（list/detail/jobs/abort/archive/export/aggregation/manual/
  dedup/project/grace/log-events/dispatch/reconciler 全数）→ **363 passed**
  （首轮 362+1 红为上述 main 既有红，修复后全绿）；
- `test_plan_run_abort_api.py` 单独复跑 21 passed；
- `run_gates.py check:quick` 与 ruff/compileall → 见 PR。

## Revisit

- 路由模块残留在用的 `_iso`/`_aware`（plan_runs.py 本体）等 read_common 主题自然
  扩到路由层时一并收编；剩余厚块：summary 路由 + artifacts list 手搓 dict
  （#2187 正规化候选，可顺势进 `test_api_response_shape_contract` 的 opt-in 文件集）。
- **CI 默认 ruff 规则集不含 F811**——服务内重复 defs 只有显式 `--select F811` 才见。
  若同类自遮蔽再现（watcher 这次就是），考虑给 lint 门禁加 F811（独立小单判）。
