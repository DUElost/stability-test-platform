# 未配置引导按错误码判断（#1226，R13-F15）

Status: implemented
Class: bug-fix

## Decision

`AssistantPage` 发送失败分支用
`toApiError(err).message.includes('ai_not_configured')` 判断是否切换引导横幅；而
`toApiError` 把后端 `detail={"code": "ai_not_configured", "message": <人话>}` 归一为
`ApiError{code, message}`——真实响应中 message 是说明文案、不含错误码，判断
**永不命中**：普通用户只收到一个 toast，看不到应有的「AI 助手尚未启用」引导。
既有测试把 code 与 message 写成同一字符串，掩盖了该缺陷。

修正：

- 判断改为 `toApiError(err).code === NOT_CONFIGURED_CODE`（`ApiError.code` 为唯一判据）；
- 非命中分支保持 `toast.error(apiError.message || '发送失败')` 不变；
- 测试改用与后端一致的分离 code/message，并补一条「其他错误仍 toast 且不误触
  横幅」回归（验收 3）。

## Alternatives

- 同时匹配 code 或 message（兼容旧响应）：真实后端只有 code 语义，双判据会引入
  新的模糊匹配面；不需要。
- 后端把码也塞进 message：污染面向用户的文案，明确不做。

## Verification

- `npx vitest run src/pages/assistant/AssistantPage.test.tsx` → 4/4（改 1 例为分离
  code/message；新增 1 例其他错误 guard）
- 红绿：未修复实现上「code + 人话 message」用例失败（横幅未显示）
- 全量前端套件 / type-check / eslint / build / `check:quick` 通过

## Revisit

- 若未来出现更多需要引导的错误码，可把「码 → 引导」抽成映射表；当前单码无需抽象。
