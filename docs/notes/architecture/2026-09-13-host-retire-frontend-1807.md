# ADR-0038 ⑥：前端退役入口 / 徽标 / 判定面（#1807）

Status: implemented
Class: architecture

## Decision

按 ADR-0038 v0.2 §4 前端面实现分解 **⑥/6**；依赖 ②（API）与 ③（读面过滤），
本分支堆叠在 ① 的链根上。

**API 层**（`utils/api/hosts.ts` / `queryKeys.ts`）：

- `hosts.list(skip, limit, includeRetired)` 增 `include_retired` 参数；
  `fetchHostList(..., includeRetired)` 同步透传；
- 新增 `hosts.retire(id, reason)` / `hosts.unretire(id, reason)`（请求体
  `retire_reason`，与 ② 的 schema 对齐）；
- 缓存键：`hostKeys.list()` 保持 `['hosts']`（三页共享、默认不含退役）；
  新增 `hostKeys.retiredList() = ['hosts', { includeRetired: true }]`——
  **分键不污染共享缓存**，且同前缀使既有 `invalidateQueries(['hosts'])` 同时覆盖两者。

**入口与展示**：

- `HostsPage`：新增「显示已退役」开关（切换查询键与 `include_retired`）；
  行内下拉新增「退役」/「解除退役」（admin），原因经 `window.prompt` 收集
  （必填；取消或空白不发请求并提示）；
- `ExpandableHostTable`：新增退役徽标——`retired_at` 且 `status=ONLINE` 显示
  **「已退役但仍在心跳」**（D4 异常态），否则「已退役」；退役主机主操作位不再
  提供热更新/安装（显示「已退役」占位）；
- 批量删除：`catch {}` 改为携带错误文案（409 详情透出给操作者，不再只报条数）；
- 批量安装：退役主机排除出目标集并显式提示「已跳过 N 台已退役主机」；
  批量热更新经 `bulkHotUpdate` 的 skip 枚举（见下）。

**判定面**：

- `bulkHotUpdate.ts`：新增 skip reason `retired`（label「主机已退役」），
  **先于**离线/未安装/活跃 Job 判断——退役是运维决定，热更新对它无意义；
- `planExecuteReadiness.ts`：`ReadinessHost` 增 `retired_at`；退役节点的设备
  报「节点已退役」（优先于「节点离线」）。

## Alternatives

- **`include_retired` 复用共享键 `['hosts']`**：弃——开关状态会写进三页共享缓存，
  HostsPage 打开开关后 DevicesPage/PlanExecutePage 会读到含退役的列表（口径漂移）；
- **退役原因用自研 Dialog**：本单取 `window.prompt`（最小可测）；Dialog 化列入
  Revisit——理由：本单验收集中在徽标/穿透/判定面，对话框属交互打磨；
- **退役主机隐去「删除」入口**：弃——ADR D2 明确 DELETE 语义不变（#937 预检保历史），
  是否可删由后端判定，前端不预先屏蔽（409 文案已可读）；
- **批量安装不做 skip 只依赖后端 ④**：弃——④ 尚未落地，且批量操作需要「跳过原因」
  的用户可见语义（issue 明列「批量操作 skip 枚举与提示」）；
- **判定面只改 bulkHotUpdate**：弃——PlanExecute 的就绪面板同样面向派发决策，
  ADR D5 的 UI 侧同口径应一致。

## Verification

- **新增/更新用例**：
  - `HostsPage.test.tsx`：`include_retired` 穿透（开关切换后 `fetchHostList(0,200,true)`）、
    退役/解除退役入口携带原因、取消不请求、批量删除透出 409 文案、批量安装跳过退役
    并提示（确认文案只含 1 台可安装）；
  - `ExpandableHostTable.test.tsx`：离线退役徽标、退役但仍在心跳徽标、下拉退役/
    解除退役入口、退役主机不再提供热更新；
  - `bulkHotUpdate.test.ts`：退役单独跳过（reason=retired，不与活跃 Job 混报）；
  - `planExecuteReadiness.test.ts`：退役节点「节点已退役」优先于「节点离线」；
  - `hosts.test.ts`：`include_retired` 参数穿透（true/false 两态）——既有断言同步更新；
- **反例实证**（逐面移除判据 → 对应用例转红，恢复后全绿）：
  徽标 / readiness / bulkHotUpdate / 批删文案 四类移除 → **6 failed**；
  `fetchHostList` 忽略 includeRetired → `hosts.test` **1 failed**；
- **回归**：前端全量 `npx vitest run` → **787 passed（105 files）**；
  `npm run type-check`、`npx eslint src --max-warnings 0`、
  `python scripts/run_gates.py check:quick`（7 gates）全绿。

未做：④ 的后端派发/claim 收口（前端只做同口径提示）；真实浏览器目视（组件级
用例覆盖行为）。

## Revisit

- **`window.prompt` 的交互质量**：原生 prompt 在部分浏览器被抑制且无样式；建议后续
  换成小 Dialog（含原因文本框与必填校验），本单已把原因校验逻辑独立成
  `askRetireReason`，替换成本低；
- **200 条静默截断**：`fetchHostList` 固定 `limit=200`，含退役后 fleet 超过 200 台时
  列表会静默截断（issue 已点名）。当前规模未触发；若要修，需要分页或 `total` 提示
  （后端 `total` 已可用，但 `fetchHostList` 只取 items）；
- **DevicesPage / PlanExecutePage 的共享列表**：两页仍用默认（不含退役）列表——
  退役主机将不在其"未分配/派发"视图中出现（D5 预期）；若将来需要「显示已退役」，
  两页需各自加开关（缓存键已预留）；
- **`matchMedia` 缺失**：本单新增的批量安装用例改以确认文案断言（避免触碰批量
  渲染链），测试环境缺 `matchMedia` 的既有问题未处理（与 ADR-0038 无关）；
- **DELETE 入口与退役的关系**：退役主机仍可点删除，由后端 #937 预检返回 409 与
  指引；若运维希望前端直接引导「先解除退役」，属交互优化，待 ④ 后端口径稳定后
  一并评估。
