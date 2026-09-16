# 设备缓存前缀纠偏 + 回跳守卫归一化（#2068 / #2081）

Status: implemented
Class: bug-fix

## Decision

### 1. #2068：`deviceKeys.all()` 挂回 `['devices']` 前缀（把注释的断言变成结构事实）

`deviceKeys.all()` 原为 `['devices-all']`，与写后失效用的 `deviceKeys.allLists()`
（`['devices']`）**互不覆盖**——React Query 的失效按元素逐段前缀匹配（`'devices' !==
'devices-all'` 直接 false），于是「新增设备 / 改标签 / 归入项目」三处写后失效都打不到全量
缓存：计划执行页的设备矩阵（20s 轮询）与排程页设备选择器（`staleTime: 60s`、无轮询）长期
陈旧。`DevicesPage` 里那句「前缀匹配 `['devices']` 覆盖全量」的注释与实现不符。

改法（选结构而非补调用）：`all: () => ['devices', 'all']`——全量键落在同一前缀下，
`allLists()` 的失效天然覆盖它，新写的失效点不会再漏。同步两处连带：

- `QueryProvider` 的登出缓存清单删掉 `['devices-all']`（`['devices']` 已覆盖它，
  留着是死条目）；
- `DevicesPage` 归入项目处把字面量 `['devices']` 换成 `deviceKeys.allLists()`（同一语义，
  但让「失效面」只有一个来源）。

### 2. #2081：回跳守卫改为「按 URL 归一后必须同源」

`resolvePostLoginTarget` 原先只挡字面 `//`，而 WHATWG URL 对特殊协议会把 `\` 归一为 `/`、
把制表/换行剔除——实测（Node，与浏览器同规范）：

```text
"/\evil.com"    -> https://evil.com/   （跨源，漏网）
"/\\evil.com"   -> https://evil.com/   （跨源，漏网）
"/\/evil.com"   -> https://evil.com/   （跨源，漏网）
"/\t/evil.com"  -> https://evil.com/   （跨源，漏网）
```

后果不是站外跳转（`navigate` 会抛），而是 `SecurityError` 被 `LoginPage.handleSubmit` 的
`catch` 当成**登录失败**：会话已建立却停在登录页、弹出错误文案（用户刷新即可进入）。

改法：候选必须先以 `/` 开头，再 `new URL(candidate, window.location.origin)` 解析，
**origin 不等于本站即拒绝**；放行时返回**归一后的** `pathname+search+hash`，保证
「校验的对象」与「交给 navigate 的对象」是同一个。按形态枚举（`[1] === '\\'`）能挡住
issue 举的那一种，但挡不住 `\t` 之类的解析期剔除，故取归一化判据。

## Alternatives

- **#2068 在三处写后失效里同时 invalidate 两个键**（不改键结构）：改动分散、且下一个
  写路径仍可能只失效一个（本单就是这么漏的）；结构上让全量键落在前缀下是根治。
- **#2068 把 `all()` 改成 `['devices']` 本身**：会与 `allLists()` 同键，`setQueryData`
  语义上「全部筛选态」与「全量列表」混成一个缓存槽，等于把 #823 的教训反过来踩一遍。
- **#2081 只补 `candidate[1] === '\\'`**（issue 给的「至少」方案）：挡不住
  `/\t/evil.com` 这类在解析期被剔除的字符；既然有 `new URL` 这条同源判据，就用它。
- **#2081 在 `LoginPage` 的 catch 里区分 `SecurityError`**：能修「卡在登录页」的现象，
  但守卫本身仍可被绕过（把跨源目标交给 `navigate` 是错的源头），故修在守卫。

## Verification

worktree `.wt/stp-2068-2081-frontend`（base `b115b0b7`），2026-09-16：

```bash
cd frontend && npx vitest run        # 108 files / 849 passed
python scripts/run_gates.py check:quick   # [OK] 10 gates
python scripts/run_gates.py check:pr      # [OK] 18 gates
```

反例构造（还原源码到 HEAD 后重跑，共 7 条失败；恢复后 18 passed）：

- #2068：还原 `queryKeys.ts` → 新增两条用例失败（`deviceKeys.all()` 的
  `isInvalidated` 恒 false——添加设备 / 归入项目两条写路径各一条）；
- #2081：还原 `authRedirect.ts` → 5 条失败（`/\evil.com`、`/\\evil.com`、`/\/evil.com`、
  `/\t/evil.com` 四个跨源形态 + 「归一后返回值可安全交给 navigate」）。

`authRedirect` 此前**没有任何测试文件**，本次新建（含 state.from 优先级、缺省回落、
`/%5Cevil.com` 这类「编码形态不得被误判」的正向对照）。

## Revisit

- `resolvePostLoginTarget` 现在依赖 `window.location.origin` 作为解析基准：若将来该模块
  被用于非浏览器环境（SSR/测试里没有 location），需把 origin 作为参数注入。当前消费点
  只有 `LoginPage`（浏览器）。
- #2081 的「后果」（登录页卡住 + 错误文案）是**规范推导**，本机无浏览器未端到端复现；
  守卫绕过本身是实测的。真机复现方式：带 `?next=/\evil.com` 走一次登录。
- 设备缓存前缀现在是「`['devices']` 覆盖 `['devices','all']` 与各筛选态」：后续新增设备
  视图缓存务必落在这个前缀下，否则又会脱出写后失效面。
