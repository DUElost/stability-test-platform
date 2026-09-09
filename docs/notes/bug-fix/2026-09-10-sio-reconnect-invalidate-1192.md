# Socket.IO 重连后 REST 校准（#1192）

Status: implemented  
Class: bug-fix

## Decision

`useCrossClientSync` 在 Socket `onConnect`（含断线重连）时调用
`invalidateCrossClientSyncQueries`，失效与 `plan_changed` /
`project_changed` 相同的 React Query 键，弥补断线期间漏掉的广播事件。
提取 `invalidatePlanSyncQueries` / `invalidateProjectSyncQueries` 供消息
处理与重连共用。

涉及：`frontend/src/hooks/useCrossClientSync.ts`；
测试见 `useCrossClientSync.test.ts`。

## Alternatives

- 各列表页加 `refetchInterval`：无法针对断线瞬间、浪费带宽。
- 重连后 `invalidateQueries()` 全量：范围过大，与 visibility 路径重复且
  可能扰动无关缓存。
- 仅依赖 #1112 房间重订阅：只恢复订阅，不拉取断线期间变更。

## Verification

- `cd frontend && npm run test -- --run src/hooks/useCrossClientSync.test.ts`
- `npm run typecheck`（frontend）

## Revisit

若 Plan 编辑页需「dirty 时不 refetch plan 详情」，在 Plan 编辑器侧用
`enabled`/本地 dirty 门控，不在本 hook 缩范围。
