# 轮询/失效批 4 处修正（#823）

Status: implemented
Class: bug-fix

## Decision

四处 query/lifecycle 缺陷（B×4），同批修复：

1. **助手操作卡 LogPanel 历史日志不可见**（`ActionCard` 门控 + `LogPanel.enabled: active`）：
   `enabled: active` 使终态动作展开后永不请求，历史日志永远为空。改为**挂载即可
   读**（是否挂载由 ActionCard 的 `logOpen || isActive` 门控），`active` 只决定是否
   2s 轮询。等价于 issue 建议的 `enabled: active || open`，且无需新增 prop。
2. **PlanRunLogsPage 终态后无限拉取**：`isTerminal` 由只读一次的 `runQ` 派生 →
   run 结束后永远为假，`eventsQ` 每 30s 持续请求终态 run。给 `runQ` 加函数式
   `refetchInterval`：**非终态慢轮询推进、终态即停**（与 eventsQ 的停更条件对齐）。
3. **DevicesPage 写后失效键不匹配**：查询键是
   `['devices', { projectKey, unassigned }]`，而 create/tagUpdate mutation 用
   `deviceKeys.list()` 无参形态失效（`{projectKey: null, unassigned: false}`）——
   RQ 前缀匹配在第 2 元素深比较不等 → **筛选态列表永远不被失效**。新增
   `deviceKeys.allLists() = ['devices']` 前缀键并在两处 mutation 使用（与同页
   assignProjectMutation 既有写法一致）。
4. **HostHotUpdateConfirmDialog 按钮永久 disabled**：`detailQ` 只取一次快照，
   abort 收口期间 `allAbortPending` 长期为真 → 确认按钮到用户手动重开前永久
   disabled。给 `detailQ` 加函数式 `refetchInterval`：**倒计时进行中或全部
   abort_pending 时按 5s 短轮询推进**，收口完成即停。

## Alternatives

- LogPanel 传 `open` prop 再 `enabled: active || open`：等价但多一个 prop 与传参面；
  挂载语义已由使用方门控，直接去掉 enabled 更小。
- PlanRunLogsPage 改为「事件侧判终态」：事件流不含 run 终态语义，需后端补字段；
  慢轮询复用既有 `SLOW_REFETCH_MS` 且与详情页 #1193 的推进策略一致。
- DevicesPage 统一改用 `['devices']` 字面量：可行；抽 `allLists()` 与 `planKeys.allLists()`
  既有模式对齐，语义更明确。
- 弹窗改 SSE/socket 推送收口完成：为单一按钮状态引入订阅面过大；短轮询在有界
  等待场景（abort reaper 秒级）足够。

## Verification

- 四文件 `npx vitest run`：**18/18 通过**（新增 4 条：LogPanel 终态可读 + 运行中
  轮询 / runQ 终态停更 / 设备列表前缀失效 / 收口短轮询）
- 红绿：未修复实现上 4 条新用例失败（LogPanel「运行中」为 guard，两态皆过）
- `npm run type-check`、全量前端套件、`check:quick` 通过
- 过程记录：DevicesPage 页面级用例需驱动 create——该文件将 `AddDeviceModal`
  mock 为 null，调整为「打开时渲染提交按钮」以覆盖 mutation 接线（弹窗自身行为
  仍由 `AddDeviceModal.test.tsx` 覆盖）

## Revisit

- 四处均为「有界推进」补丁；若同类轮询/失效缺陷再现，考虑抽共享的
  `terminalPolling` 工具或为 `deviceKeys` 增加失效断言测试模板。
