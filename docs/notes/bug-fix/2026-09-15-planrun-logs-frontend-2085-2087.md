# PlanRun 日志页前端批：#2085 / #2087 修复 + #2027 复核判定（前提不成立）

Status: implemented
Class: bug-fix

## Decision

### 1. #2085：自由文本搜索词 `all` 不再被 `cleanParams` 剥离

`cleanParams` 把「`all` = 不筛选」的**枚举**规则套到了所有参数上，`search` 是自由文本
（`planRuns.ts` 的 `search?: string`）——用户搜 `all` 时该参数被静默剥离，请求退回未过滤
数据，而 UI（输入框回显、查询键）与 CSV 导出都按「已应用搜索」呈现。

改法：把规则限定到**值域真含 `all` 的枚举键**白名单（`ALL_AS_NO_FILTER_KEYS =
stage / severity / status / link_status / host_id`，即 `ListPlanRunEventsParams` /
`ListPlanRunDevicesParams` 里显式写成 `X | 'all'` 的那几个）。新增枚举旋钮时在白名单登记。

### 2. #2087：导出失败可见 + 导出取可见关键词 + 防抖不再无条件复位分页

- `handleExportCsv` 只有 `try/finally`：最多 40 次分块请求里任一块失败都是 unhandled
  rejection——不下载、不提示、按钮只是重新可用。补 `catch` + `toast.error(\`导出失败: …\`)`
  （与 `PlanRunDetailPage` 的导出提示同口径）。
- 导出关键词从 `search`（防抖后的值）改为 `searchInput`（**输入框可见值**）：防抖窗口内
  点导出，原来导出的是上一个关键词，而 tooltip 承诺的是「当前筛选与搜索命中」。
- 防抖回调原先无条件 `setPage(0)`：输入后改回原词（净变化为零）也会在 300ms 后把用户
  刚翻到的页静默回退。改为用 `appliedSearch` ref 比对，值真变才 `setSearch` + `setPage(0)`。

### 3. #2027：复核后**不修**——前提不成立（本单不含代码改动）

issue 的结论是「`1e339766` 加的 `block` 压掉了同一行上的 `line-clamp-2`（同特异性、
构建产物里 `.block` 在后）→ 折叠态不再截断」。实测不成立：

```text
HEAD 组件渲染出的描述元素 class（jsdom 实测）：
mt-0.5 w-full cursor-pointer text-left text-xs leading-snug hover:text-foreground
text-muted-foreground line-clamp-2          ← 没有 block
```

原因是组件用 `cn(...)`，而 `cn = twMerge(clsx(...))`：`block` 与 `line-clamp-2` 属同一
conflict group，twMerge 在**进入 DOM 之前**就把先出现的 `block` 消掉了。twMerge 自
`76108357`（2026-02-07）起即在用，早于 `1e339766`（2026-09-14）。审计的推理链
「样式表里 `.block` 与 `.line-clamp-2` 两条规则都在、且 `.block` 在后」只证明了**样式表**
如此——`.block` 在别处被使用，与该元素无关；没核对渲染后的 class 是这条链的缺口。

**仍存疑的替代成因（本机无法验证，需真浏览器）**：`1e339766` 把描述容器从 `<div>` 换成
`<button>`，`-webkit-line-clamp` 在表单控件上的裁剪行为在个别内核里可能不同。若在真机上
仍能观察到「点击描述无视觉变化」，应从这条排查，而不是 display 工具类冲突。

## Alternatives

- **#2027 按 issue 正文改 class**（把 `block` 移进展开分支）：我最初就是这样改的——实测
  `cn` 输出**逐字节相同**（no-op），写的守卫断言在 HEAD 上也通过。属「修一个不存在的
  bug」，已整体回退，不留假修复、不用它去关单。
- **#2085 改由调用点传 `undefined`**：改动分散到多个调用点，且新增调用点容易再踩同一坑；
  保留集中判据 + 键白名单更稳。
- **#2085 干脆不判 `all`**：`stage=all` 会撞后端枚举校验（422），否决。
- **#2087 导出前 flush 防抖（立即把 `search` 同步为输入值）**：会多触发一次列表查询，且
  与「导出读可见值」相比没有任何额外收益，否决。

## Verification

worktree `.wt/stp-2027-2085-2087-frontend`（base `ff65ca78`），2026-09-16：

```bash
cd frontend && npm test -- --run          # 107 files / 835 passed
python scripts/run_gates.py check:quick   # [OK] 10 gates
python scripts/run_gates.py check:pr      # [OK] 18 gates
```

反例构造（逐条还原源码到 HEAD 后重跑）：

- #2085：还原 `planRuns.ts` → `planRuns.test.ts` **1 failed**（`search: 'all'` 被剥离）；
  恢复后 4 passed。
- #2087：还原 `PlanRunLogsPage.tsx` → 3 条新用例**全部失败**（无 catch / 导出用旧关键词 /
  分页被复位）；恢复后 13 passed。
- #2027：把组件还原到 HEAD → **新断言仍通过**（`not.toHaveClass('block')` 在旧代码上也
  成立）——这正是发现前提有误的入口；随后用 `twMerge` 直测与 DOM class 落盘确认。

一条环境噪声（如实记录）：`check:pr` 首轮 `agent-tests` 红 1 项
（`test_scan_runner_idle_exit_race_1706` 的 idle-exit race 用例），单跑 3/3 通过、复跑全量
`backend/agent/tests/` **2082 passed**、`check:pr` 复跑全绿——是高负载下的时序 flaky，
与本批（纯前端）改动无关。

## Revisit

- **#2027 若在真浏览器复现** → 从 `<button>` + `-webkit-line-clamp` 方向排查（必要时改回
  `<div>` 并补 role/键盘处理，或换用非 display 依赖的截断方案）。**不要**再从 display
  工具类冲突入手——那条路已被实测排除。
- `handleExportCsv` 是 #2028/#2219 与本次改动共同的落点：后续再改该函数时，`catch` +
  `toast.error` 与「导出读可见值」不要被重写掉（用例已覆盖这两条）。
- `ALL_AS_NO_FILTER_KEYS` 是显式白名单：新增带 `all` 哨兵的枚举参数时要登记，否则该参数
  的 `all` 会被发给后端（多为 422，能立刻发现）。
