# 主机硬删除历史保护回归见证（#796）

Status: implemented
Class: bug-fix

## Decision

#796（R14，data-loss）要求的删除前依赖预检，已由 #937（PR #1380，主干
11f5bd0e）落地：`DELETE /hosts/{id}` 依次拒绝 ONLINE、活跃 Job、历史 Job、
设备、PlanRunHost 投影，commit 再捕 `IntegrityError` 兜底 409
（`backend/api/routes/hosts.py:477-549`）。本单不重复实现，补两处缺口使
#796 可验收：

1. **回归见证**：#937 只测了设备分支；#796 的核心数据丢失场景——**历史
   Job**——与 plan_run_host 投影分支无测试。新增两用例锁定「409 + 行保留」，
   防未来重构退回 FK CASCADE 静默清空；
2. **端点 docstring 修正**：原写「仅挡 ONLINE/活跃 Job」，与实现的五重预检
   不符，改为完整列出——#796 修复方向明确要求端点文档显式声明该契约。

## Alternatives

- **仅以证据关闭 #796（不复写代码）**——接近可行（行为已在主干交付），但
  核心场景无回归保护，且未来任何一次「简化删除路径」重构都可能无声退化；
- **实现 host 软删/归档流程**——放弃：数据模型方向级变更须 ADR；#796 给出
  的第一条修复方向（预检 409）已被 #937 采纳，归档属独立需求（见 Revisit）。

## Verification

- 新增用例（`backend/tests/api/test_hosts.py::TestHostHardDeleteGuards`）：
  - `test_delete_host_with_job_history_is_409_and_preserves_history`：历史
    Job 存在 → 409 且 detail 含「历史 Job」；随后 `expire_all()` 强制回源
    断言 job/device/host 行仍在（409 是数据保护而非部分删除）；
  - `test_delete_host_with_plan_run_projection_is_409`：仅剩投影（无
    job/device）→ 409 且 detail 含「投影」。
- **反例实证**：临时禁用三处预检（mutation）→ 两用例均转红——返回
  `200 host deleted`，即旧行为下历史被级联清空；恢复后 4 passed；
- `pytest backend/tests/api/test_hosts.py` 全绿（见 PR）；
- `check:quick` 见 PR 描述。

## Revisit

- 「有历史的退役主机」目前既不可硬删也无归档入口；若运营出现高频清理需求，
  由独立 ADR 裁决 host 归档/软删语义（#937 的 Revisit 同向留置）；
- 前端当前无 host 删除入口，`api.hosts.remove`（`frontend/src/utils/api/hosts.ts:25`）
  为未调用代码；上线删除 UI 时需按 409 detail 呈现归档指引。
