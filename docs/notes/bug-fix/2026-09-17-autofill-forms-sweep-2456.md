# 受控表单自动填充缺陷扫尾：4 个表单提交改以 DOM 值为准（#2456）

Status: implemented
Class: bug-fix

- 日期：2026-09-17
- 相关：`#2453`（本形态的首次定位与修复：建号表单）、`#2435`/`#2406`（同一表单的前两条独立缺陷）

## Decision

### 1. 抽一个共享助手，而不是在每页各写一遍

`frontend/src/utils/forms.ts`：

```ts
readNamedValues(root: ParentNode | null, fallback: Record<string, string>): Record<string, string>
```

按 `[name="…"]` 读每个字段的**当前 DOM 值**，读不到才回落调用方传入的 state。
`root` 既可以是 `<form>`，也可以是任意容器——**没有 `<form>` 的页面也能用同一套**。

为什么是共享而不是各页各写（`#2453` 当时的形态）：这次是**批量**修同类缺陷，若每页复制一份
「读 DOM」的实现，下一个人再遇到就会写出第四、第五种写法；而落成一处后，新增表单只需
「字段加 `name` + 提交走 `readNamedValues`」，并在注释里写清为什么。

### 2. 本次覆盖 4 个表单

| 页面 | 字段 | 说明 |
|---|---|---|
| `pages/auth/LoginPage.tsx` | `username` / `password` | **优先级最高**：密码管理器最常自动填充的就是登录表单；被挡下等于登不进来 |
| `pages/auth/RegisterPage.tsx` | `username` / `password` / `confirmPassword` | 一致性校验也改为基于 DOM 值 |
| `pages/account/ChangePasswordPage.tsx` | `oldPassword` / `newPassword` / `confirmPassword` | 长度校验与提交同样基于 DOM 值 |
| `pages/hosts/components/AddHostModal.tsx` | `name` / `ip` / `ssh_port` / `ssh_user` / `ssh_password` | `ssh_port` 由 DOM 字符串 `Number()` 还原；端口范围校验未变 |

### 3. 其余 2 个**本次不做**，理由与出口写进 Revisit

- `pages/wifi/WifiPage.tsx`：它的 `createMutation` / `updateMutation` 的 `mutationFn` **直接闭包读 state**
  （`form` / `maxDevicesValue()`），要改就得动 mutation 的入参签名（`mutate(values)`），
  与该页的编辑流交织——混进这批会把评审面拉大，且**与本次现场问题无关**（WiFi 池表单不是登录/建号路径）。
- `pages/settings/AiAssistantSettingsPage.tsx`：**没有 `<form>` 元素**，且 `handleSave` 读的是一组
  结构化状态（`t2bEntries` 列表等）而非平铺字段——它的形态是「列表编辑器」，不是「一次填完提交」。

## Alternatives

- **每页复制 `#2453` 的本地实现**：见 §1，会固化出多种写法。否。
- **只用 `FormData`（必须挂在 `<form>` 上）**：设置页没有 `<form>`；而 `[name=]` 读法对
  「容器 + 输入框」同样成立，一套覆盖两种结构。否（选了更一般的读法）。
- **顺手把 WiFi / 设置页也改掉**：见 §3。否（各自有结构前提，另单更清楚）。
- **给字段加 `onInput`/`onBlur` 同步 state**：仍要猜管理器的行为（何时填、是否派发、是否冒泡）。否。

## Verification

- `frontend/src/utils/forms.test.ts`（新增）→ 4 条：DOM 值覆盖 state 回落 / **非冒泡 input**
  也能读到 / `root` 为空原样回落 / `select` 同样可读。
- `frontend/src/pages/auth/LoginPage.test.tsx` +1 条：模拟密码管理器（直写 `.value` +
  **非冒泡** `input`）→ 点登录 → `api.auth.login` 收到的正是 DOM 里的值。
- **红向反证**：把 `LoginPage` 退回「只认 state」→ 上述用例**红**（1 failed / 6 passed）；
  还原即绿。
- `./node_modules/.bin/vitest run --root "$PWD"`（全量）→ **946 passed / 119 files，零失败**。
- `./node_modules/.bin/tsc --noEmit` 无输出；`run_gates check:quick` → **10 gates 绿**。

## Revisit

- **WifiPage（出口）**：把 `createMutation`/`updateMutation` 的 `mutationFn` 改为接收
  `values`（`mutate(readNamedValues(form, {...form, max_devices: maxDevicesInput}))`），
  字段补 `name`；`maxDevicesValue()` 的钳制逻辑随之改为对传入值做。
- **AiAssistantSettingsPage（出口）**：无 `<form>`；若要一并支持自动填充，用容器 `ref` +
  `readNamedValues(ref.current, …)` 读平铺字段——但先确认它的字段**确实**会被密码管理器填充
  （API key 多为粘贴而非代填），避免为假想场景改造。
- **新增表单的纪律**：字段一律加 `name`，提交处走 `readNamedValues`；
  这条与 `passwordRules.ts` / `usernameRules.ts`（判据与后端同源）合起来是「表单三件事」：
  **判据同源、字段有名、提交读 DOM**。
- **为什么现场还看到「卡顿 + 自动刷新」**：与本形态无关（是同一时段的会话 401 →
  跳登录），见 `#2453` 的 Note §Revisit。
