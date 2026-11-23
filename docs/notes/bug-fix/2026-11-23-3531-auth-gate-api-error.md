# 修复 authGate 对 ApiError 401/403 误判为 transient 死路（#3531）

Status: implemented
Class: bug-fix

## Decision

1. `frontend/src/router/authGate.ts` 的 `resolveAuthGate` 改为经形状容错的 `classifyApiError(q.error)` 判定未授权（`401/403 → 'permission' → 'anonymous'`），其余错误（`5xx / 429 / TIMEOUT / NETWORK_ERROR`）仍保持 `'transient'`。
2. `frontend/src/utils/auth.ts` 的 `classifyAuthFailure` 同步兼容 `ApiError` 顶层 `.status` 与 `code === 'TIMEOUT'`，消除裸 axios 形状与 `ApiError` 形状在认证分类上的分叉。
3. `frontend/src/router/authGate.test.ts` 改用真实 `new ApiError(...)` 构造 401/403/502/503/504/429/TIMEOUT/NETWORK_ERROR 用例，补齐原先手搓 `{ response: { status } }` 掩盖真实拦截器抛形的盲区。

涉及文件：
- `frontend/src/router/authGate.ts`
- `frontend/src/utils/auth.ts`
- `frontend/src/router/authGate.test.ts`

## Alternatives

1. **仅改 `classifyAuthFailure`、不动 `authGate.ts`**：可行，但 `authGate` 直接消费 `apiClient` 产出的 `ApiError`，复用前端统一的 `classifyApiError` 语义更清晰且与其余页面（如 `WifiPage` / `loadErrorCopy`）保持一致。
2. **仅改 `authGate.ts`、不动 `classifyAuthFailure`**：保留了 `classifyAuthFailure` 遇到 `ApiError` 时静默回落 `'transient'` 的潜在陷阱，因此两侧一并收口。

## Verification

- `cd frontend && npx vitest run src/router/authGate.test.ts src/utils/api/client.test.ts`
- `cd frontend && npx tsc --noEmit && npx eslint src/router/authGate.ts src/router/authGate.test.ts src/utils/auth.ts --max-warnings 0`
- `.venv/bin/python scripts/run_gates.py check:quick`

## Revisit

无；若未来 `apiClient` 改变错误抛出形态，由 `authGate.test.ts` 与 `client.test.ts` 的真实 `ApiError` 回归用例守门。
