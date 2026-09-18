# main 红灯：summary/artifacts 直测随升模型与 plan_name 漂移（#1520 / #2623）

Status: implemented
Class: bug-fix

## Decision

Main CI `backend-test` 两例红（`test_summary_empty_jobs` /
`test_list_artifacts_maps_filename`）与 CI 墙钟无关——是 #1520 形状正规化与
#2623 `plan_name` 叠入后，服务直测未跟进：

1. **dict 下标 → 属性访问**：服务已返回 `PlanRunJobsSummaryOut` /
   `PlanRunJobArtifactOut`，断言仍写 `out["…"]` / `out[0]["…"]` →
   `TypeError: … is not subscriptable`；
2. **MagicMock `plan_id` 污染 `plan_name`**：未设 `plan_id` 时 MagicMock 为
   truthy，`resolve_plan_name` 走查库路径并把 `scalar_one_or_none()` 的
   MagicMock 塞进 `plan_name: Optional[str]` → `ValidationError`。空 job
   场景显式 `pr.plan_id = None` 走短路径，并断言 `plan_name is None`。

`plan_name` 的同源口径与 API 键集合已由 `test_plan_run_shape_1520` 钉住；本刀
只修直测夹具，不改生产路径。

## Alternatives

- **mock `db.execute` 返回真实 plan 名**：弃——本用例目标是空 job 聚合，不是
  plan 解析；短路径更贴「不关心 plan 名」；
- **直测改成 `model_dump()` 再 dict 断言**：弃——会掩盖「服务已返回模型」的
  契约，属性访问更直接。

## Verification

- `backend/tests/services/test_plan_run_summary_artifacts.py` → 4 passed；
- 与改动匹配的 `check:quick`：见 PR / 本地跑录。

## Revisit

- 同类 MagicMock 夹具若再接 `resolve_plan_name` 同源字段，优先显式标量，
  勿依赖 MagicMock 默认 truthy；
- Issue #1520 保持 OPEN（god-module 切片系列）；本刀 `Refs #1520`（顺带
  消 #2623 夹具债）。
