# 4xx 提示里的「出路」必须真实存在：改写 plan 删除的假出路 + 一类缺陷的静态守卫（#2836）

Status: implemented
Class: bug-fix

## Decision

**先说本质**：这不是文案美化。用户按提示操作的前提是**那条操作存在**；`DELETE /api/v1/plans/{id}`
的 409 说「remove or archive plan runs first」，而本仓**既没有删 plan_run 的端点**，
`POST /plan-runs/{id}/archive` 归档的又只是**日志**（守卫数的是 `PlanRun` 行数，且 `plan_run`
没有 `archived` 列）——于是提示把用户推进一条走不通的路，实测那张测试 Plan（plan id=6）
永久留在库里。与 #2629（恒 0 死选项）、#2707（标题与首屏指向不同实体）同族：
**界面/文案承诺与现实不一致**。

落点选「**登记制 + 路由表反查**」而不是逐条改文案，因为一次只改一句话，下一句同样的话
还会写出来（本单扫描已证明：同类不止一处，见下）。

1. **`backend/api/routes/plans.py`**：409 detail 改为只陈述真实事实——历史按设计保留、
   run 由 retention 按 `PLAN_RUN_RETENTION_DAYS`（实测默认 3 天）老化、无 plan_run 删除端点、
   要立刻停用请用 `PUT /api/v1/plans/{id}` 改名标注。
   **刻意不写**「归档/隐藏该 Plan」——`Plan` 模型没有 `enabled`/`archived` 列，
   那会是第二个假出路（正文建议 2a 想要的东西，需裁决，不在本单顺手发明）。
2. **`tests/test_api_4xx_advice_has_real_exit.py`**：AST 扫 `backend/api/routes/*.py` 的
   4xx `HTTPException(detail=…)`，命中「出路提示」的必须登记为
   `VERIFIED`（登记的端点**必须在路由表里存在**，且 detail 必须真的点名它）或 `DEBT`
   （已知措辞不符，只许缩短）。路由表由 `APIRouter(prefix=…)` + 装饰器 AST 抽出
   （支持一个文件多个 router，如 `dedup.py` 的 `router` 与 `scan_router`）。

**同类扫描结果（本单真正的增量）**：全后端 146 条 4xx detail 里，**15 条**在承诺一个操作。
其中 10 条出路真实存在（已登记 VERIFIED），**5 条是同类缺陷**（登记为 DEBT，只报不修）：

| 位置 | 提示承诺 | 现实 |
|---|---|---|
| `hosts.py:553` | 请先停止 Agent 服务再删除 | 无「停止 Agent」API；真实出路是退役 `POST /api/v1/hosts/{id}/retire` |
| `hosts.py:580` | 请先归档/清理后再删除 | 没有 host 归档/清理端点；真实出路同样是 **retire** |
| `hosts.py:591` | 请先移除或迁移设备 | 设备侧没有删除/改挂 host 的端点（只有 `PUT /{id}/tags`、`POST /bulk-project` 改项目） |
| `hosts.py:601` | 请先清理关联 Run 后再删除 | 没有清 `PlanRunHost` 的端点；run 由 retention 按龄老化 |
| `hosts.py:621` | 请先清理关联数据 | 「关联数据」无对应端点，操作者无法自助 |

⇒ #2836 的起因不是孤例，而是 `hosts.py` 一片同形状的措辞。本单只修被点名的 plan 那条 +
建判据，**剩下五条留给 owner**（它们各自的真实出路是否就是 retire，属语义判断，不由文案 PR 代裁）。

## Alternatives

- **只改 plan 的 detail，不建守卫**：否决。这正是本单要打破的循环——同类五处就在 `hosts.py` 里躺着。
- **给 Plan 加 `enabled`/`archived` 列（正文方案 2a）**：不在本单。它改数据模型、改前端语义、
  还要定「停用后是否可再启用」，属 #2836 建议 2 的裁决；本单先把文案里的假路堵住。
- **给 plan_run 加删除端点（方案 2b）**：否决为本单范围。#796 的教训正是「删父对象连带带走历史」，
  加删除端点必须成文定义历史去向，不能顺手加。
- **判据扫全仓所有异常消息（含 5xx / 服务层 raise）**：否决。噪声会把登记表压垮，
  登记表一淹就没人再逐行核对——**登记制的价值来自每一行都被读过**。
- **用 `ptw`/OpenAPI 快照当路由真值**：否决。要起 app、导凭据，且 OpenAPI 不含未文档化端点；
  AST 抽 `APIRouter(prefix=…) + 装饰器` 更贴近运行时且离线可跑（同 #2641/#2778 的静态形状）。

## Verification

- `pytest tests/test_api_4xx_advice_has_real_exit.py -q` → **5 passed**（登记完备性、VERIFIED
  反查路由表、DEBT 只缩短、路由抽取自证、起因文案回潮钉子）
- **抽取自证是双向的**：既断言 `POST /api/v1/plan-runs/{run_id}/abort`、
  `POST /api/v1/plan-runs/{run_id}/dedup/scan`（第二个 router 的 prefix）、`PUT /api/v1/plans/{plan_id}`
  等**抽得到**，也断言 `DELETE /api/v1/plan-runs/{run_id}`、`DELETE /api/v1/hosts/{host_id}/jobs`
  这类**不存在的**端点抽不到——否则守卫会替假出路背书，那比没有守卫更糟
- **变异自证**（逐条红，还原后 5 passed）：① 把 detail 改回「remove or archive plan runs first」
  → 2 条红（未登记 + 起因钉子）；② 把某条 VERIFIED 的端点改成不存在的路径 → 反查红
- `pytest backend/tests/api -q -k plan` → **296 passed**（detail 变更无既有断言依赖，实测确认
  全仓只有本文件的注释里还出现那句旧文案）
- `ruff check` 通过（含新增测试文件）
- **pending（不当作通过）**：① DEBT 五条的清偿需要 owner 逐条裁（真实出路是否就是 retire）；
  ② 正文建议 2（Plan 停用位 / run 历史去向）**未做**，本单不代裁；③ 未在 dev 栈重跑
  「建 Plan→跑 2 个 run→删除」的端到端复现（PR 里按事实说明，不当作已验）。

## Revisit

- DEBT 清单**只许缩短**：谁改了 `hosts.py` 的措辞，必须把条目升进 `VERIFIED`（填端点与点名词），
  守卫会在措辞仍不符时保持红。别把它改成「看着像就行」的白名单。
- 若将来要覆盖 5xx/服务层消息，先解决「登记表规模 vs 每行被读过」的矛盾，再扩判据；
  宁可扩成两个登记表，也不要一个没人核对的大表。
- #2836 不因本 PR 关闭：建议 2（真出路）仍未裁决。
