# #703 过载时 refresh/探活失败不强制登出（前端切片）

Status: implemented
Class: bug-fix

## Decision

#703 体感「abort 后被踢回登录」的前端因果链：控制面 QueuePool 打满时
`/auth/refresh` 与 `/auth/me` 常见超时/5xx，旧实现把一切 refresh 失败当成会话终态
并 `authFailureHandler` → `/login`。

本切片把会话恢复结果三分：

- `recovered`：refresh 成功 → 重放原请求
- `rejected`：HTTP 401/403 → 可走 #1039 探活；探活也 `dead` 才登出
- `transient`：超时 / 无响应 / 5xx / 429 → **保留会话**，原错误以业务失败抛回

`useSocketIO` 仅在 `recovered` 时重连；`rejected`/`transient` 仍走有界退避（不页面跳转）。

## Alternatives

- 只调大 `pool_size`：治标且掩盖 abort 写放大；后端扇出限流另单。
- refresh 失败一律重试 N 次再登出：过载时加重池压力；分类后由调用方自然重试更稳。
- 本 PR 同时改 abort 批量化：范围过大，与「勿误踢登录」正交，放入 Revisit。

## Verification

- `cd frontend && npm test -- --run src/utils/api.test.ts src/utils/client_strict.test.ts src/hooks/useSocketIO.test.ts`
- `python scripts/run_gates.py check:quick`

## Revisit

- abort 路径 host 扇出 / DB 写批量化与池观测（issue 主体压力源）仍待后端单；
- 本单不 Closes #703——仅关闭「过载误踢登录」前端缺口，issue 保持开放或拆子项。
