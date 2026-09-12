# services 反向 import api.routes 下沉闭环 + 分层门禁（#1519）

Status: implemented
Class: bug-fix

## Decision

`backend/services/` 下 4 处以函数内局部 import 引用 `api.routes` 的**私有**
符号（局部 import 正是为规避循环导入的痕迹，恰证依赖方向反了）：

| 位置 | 被引符号 |
|---|---|
| `ai_assistant/plan_run_ops.py` ×3 处 | `_load_job_in_run`、`_MANUAL_ACTION_JOB_STATUSES`、`_device_currently_disconnected`、`_emit_job_status_invalidation` |
| `ai_assistant/dispatch.py` | `_require_active_wifi_pool`、`_require_wifi_pool_matches_plan` |

修复（符号下沉 + 门禁防扩散）：

1. 新增 `services/plan_run_queries.py`：`load_job_in_run`、
   `MANUAL_ACTION_JOB_STATUSES`、`derive_device_link_status`、
   `device_currently_disconnected`（后两者为断连语义唯一事实源，连带下沉）；
2. 新增 `services/plan_run_events.py`：`emit_job_status_invalidation`；
3. 新增 `services/plan_wifi.py`：`require_active_wifi_pool`、
   `require_wifi_pool_matches_plan`（sync 域——`services/resource_pool.py`
   是 async 服务，不混放）；
4. `plan_runs.py` / `plans.py` 删除原定义、改从服务层 import（公开名）；
   `ai_assistant` 两消费方改新路径；
5. 新增门禁 `tools/dev/check_layering.py`：扫描 `backend/services/**` 的
   import 语句行（`from/import backend.api.routes`），违规 exit 1；带
   `--self-test` 红绿双向自证；接入 `run_gates.py`（`layering` gate，
   `check:pr` profile + `check:full` 自动纳入）；**CI 锚**：按 S5x 要求登记
   `GATE_TO_CI_ANCHOR` 并接入 `ci.yml` lint job（新增 step「分层检查」，
   与 immutability 同模式，含 self-test）。

异常语义保持：下沉函数继续抛 `HTTPException`（404/400）——与消费方既有
行为零变化；服务层已有 HTTPException 先例（同两个 ai_assistant 文件）。

## Alternatives

- **改为域异常 + 路由包装**——放弃（本单）：行为面扩大且消费方在服务层、
  无路由包装点；issue 未要求改变异常契约；
- **一并拆解 God-module（#1520）**——放弃（本单）：下沉 6 符号已闭合
  #1519 的确定性缺口；整体拆解是独立工程（issue 亦说「或至少同批登记」）；
- **门禁加进 check:quick**——留 Revisit：quick 定位「最快一轮」，先随
  `check:pr` 与同族（ip-leak/immutability）一致；若扩散风险再现再升。

## Verification

- **门禁探针**：构造违规文件目录 → `scan()` 检出 1 条（`from
  backend.api.routes...`）；`--self-test` 正例 4（含缩进/`import` 形态）与
  反例 5（注释/字符串/`routes_dummy` 不同模块名）全过；修复后全仓扫描
  0 违规；探针同时暴露并修复了 scan 对目录外路径 `relative_to` 的容错缺口；
- `import backend.main` 成功；`pytest --collect-only` **2303 collected**；
- `backend/tests/api` + `services/test_ai_plan_run_ops.py` 全量 **1039
  passed**（9m52s，覆盖两个路由与 ai 助手消费链的全部既有行为）；
- 残留核对：`services/` 对 `backend.api.routes` 的 import 语句 0 处
  （仅注释/文档字符串提及）；
- ruff / `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- 门禁放 `check:pr`；`check:quick` 是否纳入（升格阻断面）视 R15 门禁批次
  统一评估；
- `#1520`（God-module 拆解）是其根因面：本单下沉 6 符号后，路由仍承载
  大量业务逻辑；后续拆解时新的共享符号应直接落 services；
- `services/ai_assistant/plan_run_ops.py` 等消费方的 HTTPException 语义若
  未来分层收敛（域异常），随该层专项设计处理。
