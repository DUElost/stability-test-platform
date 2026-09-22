# hosts 全量消费方收口到 fetchAllPages 原语（#3152）

Status: implemented
Class: bug-fix

- 日期：2026-09-22
- 关联：`#3152`（本单）、`#3131`（devices 同族）、`#3147`（plans 同族 + 本单所用的
  `fetchAllPages` 原语；其 note 的一条 Revisit 由本单**更正**，见下）、
  `#3123`（offset 翻页的全序前提）、`ADR-0038 D5`（退役主机保留行）、`#496`（终态）
- 落点：`frontend/src/utils/api/hosts.ts`（`fetchAllHosts`）、
  `HostsPage.tsx`、`DevicesPage.tsx`、`DeviceOverview.tsx`、`PlanExecutePage.tsx`

## Decision

后端 `GET /hosts` 早已是 `PaginatedResponse`（`le=200`），但前端 `fetchHostList` 丢
`total` 只取一页，四个消费方按「一页=全部」用：`HostsPage` 主列表、`DevicesPage` 的
hostMap、`DeviceOverview` 与 `PlanExecutePage` 的含退役 hostMap。越界后果：管理页第
201 台起消失；设备行的主机名静默变「-」。含退役视图是最先撞线的——**退役主机不删除，
随换机世代单调累积**（ADR-0038「退役不是容量」保留行）。

新增 `fetchAllHosts(includeRetired)` = `fetchAllPages((skip, limit) => hosts.list(skip,
limit, includeRetired), 200).items`，四个消费方改用它。三处刻意**不动**：

- `HostsPage` 的 `retiredPeek` 探针（`fetchHostList(0, 1, true)`）是刻意的 limit=1
  存在性检查（#2362），不是"当全量"的误用——保留，`fetchHostList` 因此也有明确留守
  角色（取一页的原底）。
- `coerceHostList` **保留**（见下）。
- `HostsPage` 主查询返回 `Host[]` 维持 `hostKeys` 缓存形状契约（`queryKeys.ts` 注释
  同步改写为「queryFn 一律 fetchAllHosts()/fetchHostList()」）。

全序前提核对：`/hosts` 按 `order_by(Host.id)`（主键）排序，跨请求不变 ⇒
`fetchAllPages` 的 offset 前提成立（不同于 #3123 修复前的 devices）。

**紧迫度如实**：P3 预防性。当前 48 host / 上限 200，活跃增长目标（ADR-0026 的 60+）也
远在余量内。做的理由是同族机制已在 devices 上发作两次、修法现成，不该把已知的墙留在
原地——但本单没有"今天就在丢数据"的症状，不要过度解读。

## 对本家前一站的更正（#3147 note 的 Revisit）

`#3147` 的 Revisit 写了「`coerceHostList` 那份双形状容忍也应随之退休」——**不对，本单
更正**：`coerceHostList` 不是僵尸防御，它有测试背书（`HostsPage.test.tsx` 四处调用方
**专门往缓存写入 envelope 形状**证明容忍有效，`hosts.test.ts` 亦有直接用例），守的是
「未来某个 queryFn 又写错形状」这一**复发模式**（`d33d9368` 事故原型）。迁移后写入方
变干净 ≠ 守卫失去对象——恰相反，它现在守的是 `fetchAllHosts` 这条新通道。

## Alternatives

- **只改 `HostsPage` 一处（最容易撞线的那页）**：放弃。四处是同一契约误用，留三处就是
  留三个将来要考古的现场；且 `fetchAllHosts` 的边际成本在第一个消费者之后≈0。
- **把 `retiredPeek` 也改 `fetchAllHosts`**：错——探针的意义就是**不**拉全量
  （空态时多一台机器就多一整个 fleet 的载荷）。这类"limit=1 存在性检查"是 `limit`
  护栏的合法用途，收口时不能一刀切。
- **`pageLimit` 取更小值（如 100）减体积**：放弃。200 台 host ≈ 473 KB÷4（按 550 B/台
  外推更小），当前 fleet 一页就完；调小只会让常态路径变成两跳。

## Verification

- `hosts.test.ts` 两条新用例：**250 台跨 2 页**（断言第二跳 `skip=200`、`include_retired`
  逐页透传）；含退役探针参数。
- **变异自证**：`fetchAllHosts` 退化回单次 `hosts.list(0, 200)` ⇒ 翻页用例红，恢复后绿。
- 四个消费方的既有测试（含 `include_retired` 穿透、#2051 空态分流、#2053 退役门禁）
  经 mock 委托层原样通过——**断言未改写**（`fetchAllHosts` 在测试里委托回
  `mockFetchHostList(0, 200, x)`，记账参数与旧路径一致）。
- 全量前端 `vitest run`：**1098 passed**；`check:quick` 15 门全绿；tsc 无错。
- 未做：真机浏览器确认（现网 48 < 200，行为与迁移前逐请求一致——唯一可见差异是将来
  fleet 越过 200 那天，本单使它从"静默丢"变成"完整显示"）。

## Revisit

- **users / audit 两处 `list(0, 200)` 未迁移**（`UsersPage:31`、`AuditLogPage:132`）：
  users=4、audit 页已有服务端分页，离边界远；等有人真碰这两页时顺带收口，不单独立项。
- 设备数越过 1200 后 devices 页变成 2 请求/轮询——那是 #496 启动信号（#3131 同款注释）。
  host 越过 200 同理（本单保证不丢数据，但每 10s 两跳的体积会开始值得 #496 处理）。
