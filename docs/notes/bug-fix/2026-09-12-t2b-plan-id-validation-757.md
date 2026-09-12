# T2b 白名单 plan_id 前端校验与保存回填（#757）

Status: implemented
Class: bug-fix

## Decision

`AiAssistantSettingsPage.handleSave` 在提交前校验每条
`t2b_auto_dispatch_allowlist.plan_id >= 1`；失败则 toast 并 focus 对应输入，
不上送。保存成功后用 `updateConfig` 返回体 `toFormState` 回填，使服务端
sanitize 丢弃结果即时可见（不再依赖「仅 form==null 时回填」）。

## Alternatives

- **仅依赖后端丢弃 + invalidate 后强制重填**：仍先报「已保存」再消失，体验更差；否决。
- **前端校验 + 成功回填**（采纳）。

## Verification

- `npm test -- --run src/pages/settings/AiAssistantSettingsPage.test.tsx`
- `python scripts/run_gates.py check:quick`

## Revisit

若产品要在空条目时禁止「添加」直到填完，可改为草稿态；当前允许添加后拦截保存即可。
