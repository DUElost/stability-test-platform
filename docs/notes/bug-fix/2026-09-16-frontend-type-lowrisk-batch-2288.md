# 前端/类型低危批 4 处（#2288）

Status: implemented
Class: bug-fix

## Decision

四处同源缺陷：**「上游事实变了，下游/消费侧按旧假设继续跑」**，一单收口。

### ① 站内链接正则漏 ASCII 控制字符（前后端同名判据）

#2054 把 `startsWith('/')` 收紧为「单个前导斜杠且第二个字符不是 `/` 或 `\`」，方向
正确但漏了 WHATWG URL 解析会**移除**输入里的 TAB/LF/CR 这一条：`/\t/evil.com` 的第二个
字符是 TAB，通过负向断言；被移除之后它就是 `//evil.com`（协议相对 → 跨源）。前端
`notificationTarget.ts` 与后端 `notification_service._INTERNAL_LINK_RE` 同步改为
`[/\\\t\n\r]`。

判据范围要说准：被移除的只有这三个字符，且只有落在**第二个位置**才可能拼出 `//`，
所以拒绝集就是它们，不必扩到全部 C0/DEL（扩了会误挡 `/ x` 这类合法路径，收益为零）。
可达性受限（`ctx.link` 只来自 Alertmanager webhook 的 `annotations.link`，该 webhook
另有共享密钥门禁），但修法便宜。

### ② `planRunRefreshKeys` 对 DLE 卡片改用前缀键

`planRunKeys.logEvents(id)` 是**含默认参数对象**的精确键，React Query 的部分匹配只能
命中仍处 `limit:200, platform:null` 的那条缓存；#2184 把 `platform` 扩进键之后命中面
进一步缩小 → 选过平台 chip 或点过「加载更多」后，头部「刷新」不再覆盖该卡片。
新增 `logEventsByRun(id)`（与既有 `logsByRun` 同形）并用于刷新。用例不止断言「换了键名」，
而是断言**前缀关系成立**：默认键、`{limit:500,platform:'UNISOC'}`、`{platform:'MTK'}`
三种组合都以该前缀开头。

### ③ `run_context.extract` / `merge_platforms` 的键登记进 `types.ts`

#2186 落的 `missing_items` / `missing_total` 与 #2174 落的 `merge_platforms` 前端已在
消费，但类型未登记（当时 `frontend/src/utils/api` 被在窗 Execution 声明，就地收窄并
留了「声明释放后正式登记」的收尾项）。本单把该收尾项结清：`RunContextExtractSummary`
补 4 键（含 `merge_xls_skipped_same_name` / `same_basename_left_remote`），新增
`RunContextMergePlatforms`，`DedupReportCard` 删掉两处平行本地类型。

`readMissingItems` 的运行时收窄**保留**：它挡的不是 TS 形状，而是 `run_context` 这段
无形状担保的 JSONB 里的历史行（缺键/旧键/类型漂移都可能真实存在）。同时把结构性缺口
写进类型注释：`run_context` 在后端是 `Optional[dict]`，
`tests/test_api_response_shape_contract.py` 的模型对拍**结构上覆盖不到它的键集合**，
同步守卫跟踪在 #2032 —— 不再让人以为这里已有门禁。

### ④ DLE 平台筛选改由服务端返回全集

症状是「最需要时不可用」：选项集派生自**已加载行**，最新一页恰好全是 MTK 时只剩一个
选项，而渲染条件 `platformOptions.length > 1` 于是把整行筛隐藏；「加载更多」受
`MAX_LIMIT=500` 所限也翻不到 UNISOC。

修在**事实源**：`PlanRunLogEventsOut` 新增 `platforms`（`DISTINCT` 自该 run 的全部 DLE，
不接受 `platform` 参数——全集不能被自己的筛选结果收窄；`state` 保留，因为它改变的是
「哪些行算数」而非「筛掉哪个平台」）。**渲染条件 `> 1` 一字未动**：真全集只有一个平台
时确实无事可筛，条件从来不是缺陷本体，集合来源才是。`platforms` 缺失时退回按已加载行
派生，不塌成空筛选。

## Alternatives

- ②：给 `logEvents` 精确键做「枚举所有可能参数组合」的失效推断，否决——前缀键是
  React Query 的原生语义，且 `logsByRun` 已是同形先例。
- ④：用 effect 记住「未筛选时的选项」，否决（setState-in-effect 被 eslint 禁，且会引入
  与行集不一致的陈旧选项）；把平台集合写成前端常量，也否决——那是**第二个事实源**，
  新平台接入时必然漂移（后端已有平台枚举）。
- ①：改成解析 URL 后比对 origin，否决——本单的语义就是「站内路径」白名单，
  拒绝集收紧比引入 URL 解析更小、更可判定。

## Verification

红绿自证（回退 8 个源文件、保留用例后重跑）：**后端 4 failed**（平台全集 1 条 +
控制字符 3 条参数化）+ **前端 3 failed**（控制字符链接、刷新前缀键、筛选可见性）
= 7 条对旧实现红；恢复后 **后端 40 + 47 passed**、**前端全量 859 passed (108 files)**、
`tsc --noEmit` 与 `eslint` 干净。

- 「run 只有一个平台时不显示筛选」这条两版皆绿——它防的是本单**改过头**（把条件放宽成
  `> 0` 会让单平台 run 出现无意义筛选行），不是复现旧缺陷。
- ③ 的同步由既有门禁机械验证：`PlanRunLogEventsOut ↔ PlanRunLogEventsPayload` 已在
  `tests/test_api_response_shape_contract.py` 的 `_MODEL_PAIRS` 登记（15 passed），
  只改一侧即红——**而 `run_context` 正因为不在这条覆盖面上才漂了两周**，这个对比写进
  类型注释与 PR，作为 #2032 的事实依据。
- `python scripts/run_gates.py check:quick`：见 PR。

## Revisit

- `frontend/src/utils/authRedirect.ts:13` 仍是 `startsWith('/') && !startsWith('//')`
  且注释自称提供同类防护 —— 属 #2081 的家族（另单跟踪），本单不顺手改。
- `HostsPage.tsx` 零主机时「所有主机都已退役」的文案过强（本单第 4 项相邻发现，未列入
  验收）：等 #2032 或下次触碰该文件时一并收。
- `run_context` 各段（`extract` / `merge_platforms` / `precheck` …）若一直停留在
  `Optional[dict]`，②③④ 里这类「后端加键、前端不知道」会反复发生。出口是把
  `run_context` 提升为 Pydantic 模型并纳入 `_MODEL_PAIRS`（#2032），而不是继续靠
  前端就地收窄兜底。
