# 建号表单提交以 DOM 值为准：密码管理器自动填充不再被前端校验挡下（#2453）

Status: implemented
Class: bug-fix

- 日期：2026-09-17
- 相关：`#2435`（同一表单的用户名字符集）、`#2406`（同一表单的密码字节上限）——本单是同一表单的**第三条独立缺陷**

## Decision

### 1. 提交与校验改用**表单 DOM 实际值**（`FormData`），state 降为回落

`UserModal` 的四个字段是**受控组件**（`value={formData.*}` + `onChange`），而
`validate()` / `handleSubmit` 只读 React state（`formData`）。密码管理器/浏览器自动填充是
**直接写 `.value`**（常伴**非冒泡** `input` 事件），React 的 `onChange` 收不到 → state 仍为空
→ 校验报「请输入密码」→ **提交根本不发出**。

改动：

- 四个字段补 `name`（`username` / `password` / `confirmPassword` / `role`）；
- 新增 `readFormValues(form)`：`new FormData(form)` 读到的值为准，读不到才回落 state；
- `validate(values)` 接受入参（默认仍是 `formData`，保持既有调用面），
  `handleSubmit` 用同一份 `values` 做校验与提交——两条路径**不可能再分叉**。

### 2. 为什么是「读 DOM」而不是「同步 state」

可选做法是给字段加 `onInput`/`onBlur` 或在 fill 后强制 `setFormData(...)` 同步，但这些都要
**猜管理器的行为**（何时填、填完是否触发事件、是否冒泡）。提交时读 DOM 不依赖任何假设：
无论值是谁写进去的，**表单此刻是什么就提交什么**。这也是本单的判据来源——现场证据显示
提交**从未到达服务端**（nginx 当日 `POST /api/v1/users` 仅手动输入那一条且 200），
所以问题一定在「取值」这一层，而不是校验规则（那两条已由 #2406/#2435 修好并在运行版本中生效）。

### 3. 不改的（写入 Revisit）

- **编辑态的确认密码可见性**仍由 state 驱动（`{(formData.password || !isEditMode) && …}`）：
  自动填充在编辑态不会让确认框出现。本单只收口**建号**这条现场路径；改它需要把该字段的
  可见性也改为 DOM 驱动（或引入受控同步），属另一处改动。
- **其余 6 个同类表单不动**（见下「系统性」）。

## Alternatives

- **只在 \`onChange\` 之外再加 \`onInput\`**：对「非冒泡事件」无效，且仍依赖管理器是否派发事件。否。
- **给输入框加 \`autoComplete=\"new-password\"\` 让管理器别填**：那是让用户放弃密码管理器，
  与诉求相反。否。
- **在提交前 \`document.querySelector\` 逐字段读**：等于手写 FormData，且绕开表单语义；
  用 \`name\` + \`FormData\` 更标准（顺带补上了缺失的 \`name\`）。否。
- **顺手修掉另外 6 个同类表单**：见「系统性」——它们形态各异（登录走 cookie 会话、改密走另一端点），
  且登录页涉及会话与 CSRF 的重放路径，混在一个 PR 里评审面过大。**否（另立跟进单）**。

## Verification

- `./node_modules/.bin/vitest run --root "$PWD" src/pages/users/components/UserModal.test.tsx`
  → **14 passed**：新增两条自动填充用例（模拟「直写 `.value` + **非冒泡** `input` 事件」：
  ① 三个字段都自动填充 → 提交发出且值取自 DOM；② 两次确认密码被改成不一致 → 仍被拦，
  证明校验用的也是同一份 DOM 值）。
- **红向反证**：把 `handleSubmit` 退回「只认 state」→ 上述两条**红**（其余 12 条仍绿）；
  还原即绿。
- `./node_modules/.bin/vitest run --root "$PWD"`（全量）→ **943 passed / 118 files，零失败**。
- `python scripts/run_gates.py check:quick` → **10 gates 绿**。

## Revisit

- **系统性：另外 6 个表单是同一形态**（扫描结果，2026-09-17）——
  `LoginPage` / `RegisterPage` / `ChangePasswordPage` / `AddHostModal` / `WifiPage` /
  `AiAssistantSettingsPage` 全都**受控且零 `name` 属性**。**登录页优先级最高**（密码管理器最常
  自动填充的就是登录表单，被同一机制挡下时用户等于登不进来）。建议按本单同一手法处理，
  或抽一个共享的 `readFormValues` 助手；已另立跟进单。
- **编辑态的确认密码可见性**：见 §3，若要一并支持自动填充，需要把可见性判据也从 state 改到 DOM。
- **为什么现场表现是「卡顿 + 自动刷新」**：与本病灶**无关**。同一时段的 nginx 日志显示
  09:53:50 起该浏览器出现 401（`auth/me`/`refresh` 全 401）→ 应用走 401 恢复路径后跳登录页
  （多标签 refresh 轮转竞态在 `client.ts` 内已有注释说明）；那是**会话失效**的表现，
  与「表单能不能提交」是两件事。若要收口它，另立单排查会话 TTL/多标签轮转。
