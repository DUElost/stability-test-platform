# T2b 白名单 plan_id 前端校验与保存回填（#757）

Status: implemented
Class: bug-fix

## Decision

1. `handleSave` 提交前校验每条 `t2b_auto_dispatch_allowlist.plan_id >= 1`
   （含 `Number.isInteger`）；失败 toast 并 focus 对应输入，不上送。
2. 保存成功后用 `updateConfig` 返回体 `toFormState` 回填，使服务端 sanitize
   （如 plan 不存在）丢弃结果即时可见。

## Alternatives

- **仅前端校验**：仍挡不住「合法整数但库中无 Plan」的静默丢弃；否决单独方案。
- **校验 + 成功回填**（采纳）。

## Verification

- `npm test -- --run src/pages/settings/AiAssistantSettingsPage.test.tsx`
- `python scripts/run_gates.py check:quick`

## Revisit

若产品要在空条目时禁止「添加」直到填完，可改为草稿态；当前允许添加后拦截保存即可。
