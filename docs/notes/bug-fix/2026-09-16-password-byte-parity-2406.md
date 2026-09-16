# 建账号校验的前后端对齐：bcrypt 72 **字节**预检 + 用户名规则（连字符）（#2406）

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

### 3.5 用户名规则：现场第二次反馈的直接成因（前端放宽 + 后端补同一条 pattern）

现场复现后（`stp-tester` 建不出来）取证发现：**前端把连字符挡在表单里**，所以
**提交根本没发出**——nginx 访问日志里当天 `POST /api/v1/users` **零条**；
而探针（用户名合法 + 密码故意过短）显示后端**只报密码**，即后端接受该用户名。

- 前端原判据 `^[a-zA-Z0-9_]+$`（来源提交 `c66313a2`，2026-02-14，无刻意政策痕迹）
  比后端严；后端（`users.py` 的 `UserCreate`）当时**无任何字符集/长度约束**。
- 处理：两端统一为 **`^[A-Za-z0-9_.-]+$`，长度 3–64**——前端 `usernameRules.ts`，
  后端 `users.py` 的 `UserCreate` 加同一条 `Field(min_length=3, max_length=64, pattern=...)`。
  连字符与点是常见用户名字符，收紧到「字母/数字/下划线/连字符/点」比原样放宽到
  「任意字符串」更能防同形字与空白导致的身份歧义。

### 4. 不改的（写入 Revisit）

- **注册路径（`auth.py` 的 `register`）仍是另一套规则**：`min_length=2`、无字符集，
  与本次对齐后的管理端建账号不一致；`RegisterPage` 也没有前端预检。本单只收口
  「管理员建账号」这条现场出问题的路径，注册路径要否对齐另判（涉及自助注册是否
  也允许连字符/点，以及 2 字符名是否保留）。
- **`maxLength=128` 的字符上限**：与后端 `max_length=128` 一致，保留。

## Alternatives

- **只改服务端文案**：用户仍要「填完表单、提交后」才知道错，而边界是可以在输入处拦住的。
  否。
- **只改前端**：直接调 API 的脚本/其它入口（注册、改密）仍只拿到英文串。否。
- **放宽 72 字节限制**（如换算法）：那是安全设计变更，属另一个决定；本单只做对齐。否。
- **把规则放进 `frontend/src/utils/`**：该模块目前只被用户管理页用；等出现第二个消费方
  （如注册页）再上移，避免过早抽象。否（留在页面目录内）。

## Verification

- `npx vitest run src/pages/users/components/UserModal.test.tsx` → **12 passed**：密码纯规则边界
  （7 字符拒 / 24 汉字=72 字节放行 / 25 汉字=75 字节拒 / ASCII 72 放行·73 拒 / 129 字符拒）
  + 用户名纯规则 + 三条渲染用例（25 汉字 → **内联**中文提示且 `onSubmit` 未被调用；合规密码 →
  正常提交；**`stp-tester` → 正常提交**）。
- **红向反证**：摘掉字节判据 → **3 条红**（两条边界 + 那条渲染用例）；还原即绿。
- 用户名规则：前端用例 `usernameRuleError`（`stp-tester` 放行 / 点·下划线放行 / 太短拒 /
  空格与中文拒）+ 渲染用例「`stp-tester` + 合规密码 → 正常提交」；后端新增
  「接受连字符名」与「字符集外 422」两条；
- `python -m pytest backend/tests/api/test_users.py -q` → **12 passed**（含新增断言：
  422 响应里出现「72 字节」，且不再出现英文技术串）；
  `python -m pytest backend/tests/api/ -q` → **1191 passed**（`PasswordStr` 是注册/改密共用，
  故跑整目录）。
- `npx vitest run`（全量）→ **910 passed / 2 failed**；两条红在
  `src/pages/execution/PlanRunDetailPage.test.tsx`（JOB_STATUS 失效面），**在干净 origin/main 上
  同样复现**（另开 detach worktree 实测），与本单无关。
- `python scripts/run_gates.py check:quick` → **10 gates 绿**（首次因组件文件导出非组件被
  eslint 拦下 → 按 §1 拆模块后通过）。

## Revisit

- **CJK 用户名**：本单把字符集收紧到 `[A-Za-z0-9_.-]`（两端一致）。若将来要允许中文用户名，
  需先评估同形字冒充（Cyrillic `а` vs Latin `a`）与审计/日志可读性，另立单裁决。
- **注册路径对齐**：见 §4——`auth.py` 的 `register` 仍是 `min_length=2` 且无字符集。
- **规则的第二消费方**：注册页 / 改密页若也要前端预检，把 `passwordRules.ts` 上移到
  `frontend/src/utils/` 并加一条「与后端 `PasswordStr` 同源」的说明。
- **后端 `PasswordStr` 的业务文案**：本单把 72 字节那条改成了中文；若还有其它英文 `msg`
  会出现在用户界面（如 `min_length`/`max_length` 的默认 pydantic 文案），值得按同一把尺子
  过一遍——本单只动了触发本次现场问题的那一条。
