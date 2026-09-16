# 建账号密码校验的前后端对齐：补 bcrypt 的 72 **字节**预检 + 文案中文化（#2406）

Status: implemented
Class: bug-fix

- 日期：2026-09-16
- 相关：`#281`（bcrypt 72 字节限制的引入方）、`#2406`（本单，现场反馈「新建账号建不出来」）

## Decision

### 1. 判据只保留一份：`passwordRules.ts`（组件文件不导出非组件）

新的纯模块 `frontend/src/pages/users/components/passwordRules.ts` 承载
`PASSWORD_MAX_BYTES` / `passwordByteLength()` / `passwordRuleError()`，`UserModal`
的创建态与编辑态**共用同一条** `passwordRuleError`。

**为什么先做这一步**：修复前同一条密码规则在 `validate()` 里被写了**两遍**（创建分支 /
编辑分支），任一处漏改就会再次漂移——本缺陷正是「前端一套、后端一套」的产物，所以先消除
文件内的第二份拷贝，再加规则。（顺带满足 `react-refresh/only-export-components`：组件
文件只导出组件。）

### 2. 补的是**字节维度**，不是改长度上限

后端 `PasswordStr`（`backend/core/security.py`）= `min_length=8` + `max_length=128` +
UTF-8 编码后 ≤72 字节；前端原先只有前两条。多字节字符下两者不等价：**中文 3 字节/字，
24 字即 72 字节**——正好卡在边界上，用户很容易踩。

72 这个限制本身是 #281 的正确设计（bcrypt 只取前 72 字节，静默截断会让不同密码产生同一
哈希），**不动**；补的是前端预检，让它与后端同时拒绝、且在**输入处**而不是提交后。

### 3. 服务端文案中文化（三处入口共享）

`_bcrypt_compatible_password` 的 `ValueError` 由
`password must not exceed 72 bytes when UTF-8 encoded (bcrypt limit)`
改为 `密码不能超过 72 字节（bcrypt 限制；中文约 24 字）`。

理由：这句会**原样出现在前端提示里**（`toApiError` 取 `detail` 的 `msg`），而 `PasswordStr`
是**建户 / 注册 / 改密**三处共用的字段类型，三处一并受益；英文技术串对操作者等于没说清
「该改多短」。

### 4. 不改的（写入 Revisit）

- **用户名规则的前后端差异**：前端 `[a-zA-Z0-9_]{3,}`，后端 `username: str`（无约束）。
  前端更严且**已有内联中文提示**，不构成「静默失败」；且收紧后端属于另一个决定
  （是否允许 CJK 用户名涉及同形字冒充面），不在本单。
- **`maxLength=128` 的字符上限**：与后端 `max_length=128` 一致，保留。

## Alternatives

- **只改服务端文案**：用户仍要「填完表单、提交后」才知道错，而边界是可以在输入处拦住的。
  否。
- **只改前端**：直接调 API 的脚本/其它入口（注册、改密）仍只拿到英文串。否。
- **放宽 72 字节限制**（如换算法）：那是安全设计变更，属另一个决定；本单只做对齐。否。
- **把规则放进 `frontend/src/utils/`**：该模块目前只被用户管理页用；等出现第二个消费方
  （如注册页）再上移，避免过早抽象。否（留在页面目录内）。

## Verification

- `npx vitest run src/pages/users/components/UserModal.test.tsx` → **7 passed**：
  纯规则边界（7 字符拒 / 24 汉字=72 字节放行 / 25 汉字=75 字节拒 / ASCII 72 放行·73 拒 /
  129 字符拒）+ 两条渲染用例（25 汉字 → **内联**中文提示且 `onSubmit` 未被调用；合规密码 →
  正常提交，不误伤）。
- **红向反证**：摘掉字节判据 → **3 条红**（两条边界 + 那条渲染用例）；还原即绿。
- `python -m pytest backend/tests/api/test_users.py -q` → **10 passed**（含新增断言：
  422 响应里出现「72 字节」，且不再出现英文技术串）；
  `python -m pytest backend/tests/api/ -q` → **1189 passed**（`PasswordStr` 是注册/改密共用，
  故跑整目录）。
- `npx vitest run`（全量）→ **905 passed / 2 failed**；两条红在
  `src/pages/execution/PlanRunDetailPage.test.tsx`（JOB_STATUS 失效面），**在干净 origin/main 上
  同样复现**（另开 detach worktree 实测），与本单无关。
- `python scripts/run_gates.py check:quick` → **10 gates 绿**（首次因组件文件导出非组件被
  eslint 拦下 → 按 §1 拆模块后通过）。

## Revisit

- **用户名规则一致性**：前端 `[a-zA-Z0-9_]{3,}` vs 后端无约束。若确认要允许中文用户名，
  需同时评估同形字冒充（Cyrillic `а` vs Latin `a`）与审计/日志可读性，另立单裁决。
- **规则的第二消费方**：注册页 / 改密页若也要前端预检，把 `passwordRules.ts` 上移到
  `frontend/src/utils/` 并加一条「与后端 `PasswordStr` 同源」的说明。
- **后端 `PasswordStr` 的业务文案**：本单把 72 字节那条改成了中文；若还有其它英文 `msg`
  会出现在用户界面（如 `min_length`/`max_length` 的默认 pydantic 文案），值得按同一把尺子
  过一遍——本单只动了触发本次现场问题的那一条。
