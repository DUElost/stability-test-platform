# 主机面两单合并交付：选机工作台的 host 新鲜度（#2599）+ 主机显示名唯一入口（#2601）

Status: implemented
Class: bug-fix

- 日期：2026-09-17（两单立案日；实现跨到 09-18）
- 关联：`#2599`（P2，host 维度只在挂载时取一次）、`#2601`（P3，主机显示名三套口径）、
  `#496`（host 事件通道与列表基础设施——本单不做）、`#2494`（对外词表统一——同族问题）

## Decision

同一个「主机事实」在**数据新鲜度**与**显示名**两个面上各有多份实现，本单把两处都收敛成单点。

### 1. 显示名：`hostLabel()` 是唯一入口（#2601）

新增 `frontend/src/utils/hostDisplay.ts`：

```ts
hostLabel(host, hostId?, fallback = '未知主机'): string   // name > ip > hostId
```

- **顺序取 name-first**：与主机页（`HostsPage:272/330/590`）、设备页（`DevicesPage:99` 的
  `host_name`）、报告页（`report.host?.name`）现有口径一致——选机工作台家族此前是
  ip-first（15 处里的 11 处），同一台 host 在导出 CSV 与页面上读数不同。
- `unassigned` 是「未归属设备」桶而不是主机，文案（`未分配节点`）也收敛到本文件。
- 参数取**结构化最小面**（`{ name?; ip? }`）：调用方持有的大多是 `{ip,name}` 的查找结果
  （`HostLabelLookup`），不是完整 `Host`。

**实际收敛面 15 处**（issue 正文列了 4 处）：`PlanExecutePage`（host 选项）、`DeviceMatrix`
（×2）、`DeviceTablePanel`、`SelectedMinimap`（×2）、`planExecuteExport`、`planExecuteTableSort`、
`planExecuteSelection`（×2）、`planExecuteReadiness`（×3，**其中 194 行原本就是 name-first**
——同一文件内两种顺序）、`DeviceFilterBar`（新增可选 `hostLabelFor`，缺省保持旧行为）、
`DeviceOverview`（run 详情设备总览此前直接渲染内部 slug）。

其中 **`DeviceOverview` 是 issue 的主症结**：它没有任何 host 解析，直接渲染 `d.host_id`；
本单复用了与选机工作台**同键**的 host 查询（`hostKeys.retiredList()`，共享缓存），查不到时
回落 host_id——与旧行为一致（权限不足/缓存未到不会变成空白）。

### 2. 新鲜度：三条一起做才成立（#2599）

| # | 改动 | 位置 |
|---|---|---|
| 1 | host 查询补 `refetchInterval: 20_000`（与同页 devices/activeJobs 同频） | `PlanExecutePage.tsx` hosts query |
| 2 | `online` 判据 fail-closed：host 记录缺失 → **`null`**（未知），展示层走中性灰点 + 「节点信息未知（主机记录缺失）」 | `PlanExecutePage.tsx` `nodeSummaries` + `DeviceNodeRail.tsx` |
| 3 | 「N 台节点容量未知」中性 chip：`evaluateCapacityOverflow` 对缺记录节点的**静默跳过**保留（那是「心跳未到不误报」的既有取舍），但不再伪装成容量充足 | `ExecuteCommandBar.tsx`（新增可选 `unknownHostCount`） |
| 4 | 跨端失效补 hosts：`invalidateCrossClientSyncQueries`（断线重连 + 后台恢复可见）一并失效 `['hosts']` | `useCrossClientSync.ts` |

**为什么 2 不能只改数据源**：旧判据 `!host || host.status === 'ONLINE'` 把「不知道」判成
「在线健康」——新节点以绿点进入选机决策；而容量核算一侧是**有意**静默跳过
（`planExecuteReadiness.ts:93` 写明避免心跳未到误报）。两者叠加的净效果是「未知主机=
健康且容量充足」。故 2（判假）与 3（把跳过说出来）必须同批落，缺一个就还是自相矛盾。

## Alternatives

- **只做 #2599 项 1（补轮询）**：能把窗口从「永远」缩到「≤20s」，但窗口内的判定仍是
  fail-open、容量仍静默——issue 自己把它列为三项之一而不是全部。否。
- **把 `online` 直接判 `false`（当成离线）**：比 fail-open 好，但会把「缓存还没到」显示成
  「主机离线」，同样是假信息（红点会让人去排查不存在的主机故障）。故选**第三态中性灰**。
- **给 `DeviceOverview` 走后端补 host 显示名字段**：要动 `/plan-runs/{id}/devices` 契约与
  `types.ts`（硬不变量面），收益只是省一次查询；issue 也把「复用页面已有 host 查询」列为
  首选。否（记入 Revisit）。
- **每个语系各自统一顺序（选机家族保持 ip-first、其余 name-first）**：那就是「两套口径」，
  正是本单要消灭的东西；且排序/导出/分组三处会继续各偏一边。否。
- **把 host 事件通道一起做（后端 emit host_changed）**：属 #496 的列表基础设施范围，issue
  明确排除。否。
- **`DeviceFilterBar` 内直接发查询**：会让共享展示组件依赖数据层；改为可选 `hostLabelFor`
  回调，缺省恒等（不影响其他调用方）。否（选了回调）。

## Verification

改动面：10 个源文件（含新增 `hostDisplay.ts`）+ 8 个测试文件（含新增 `hostDisplay.test.ts`、
`DeviceNodeRail.test.tsx`）。

- `npx tsc --noEmit -p tsconfig.json` → 通过；
- `npx eslint <改动文件> --max-warnings 0` → 通过；
- `npx vitest run`（受影响面：plan-execute / plan-run / utils / PlanExecutePage）→ **352 passed**；
- `npx vitest run --maxWorkers=3`（**前端全量**）→ **1014 passed / 126 files**（exit 0）；
  注：不限并发时本机在别的会话同时跑测试的情况下出现过大批「0 test」收集失败——那是机器负载，
  不是用例红；
- **6 处定向变异，逐条回退即红**（每条只动一处、跑完还原）：

  | 变异（回退到的旧形态） | 期望红的用例 | 实测 |
  |---|---|---|
  | `hostLabel` 顺序退回 ip-first | DeviceOverview「主机显示名」 | **1 failed** |
  | `online` 判据退回 `!host \|\| status==='ONLINE'` | PlanExecutePage「fail-closed」 | **1 failed** |
  | 删掉「N 台节点容量未知」chip | ExecuteCommandBar「容量未知」 | **1 failed** |
  | host 查询去掉 `refetchInterval` | PlanExecutePage「20s 轮询」 | **1 failed** |
  | `DeviceOverview` HOST 列退回裸 `host_id` | DeviceOverview「主机显示名」 | **1 failed** |
  | 节点圆点退回两态 | DeviceNodeRail「未知」 | **1 failed** |

- 既有用例更新 **2 条**（都是判据本身该跟着改的）：
  1. `planExecuteExport.test.ts` 的 CSV 断言因顺序变更而更新（原写 `10.0.x.x`、现为
     `node-a`），并补了「无 name → ip」「无 name/ip → hostId」两个分支把统一口径显式钉住；
  2. `useCrossClientSync.test.tsx` 的「失效根集合」计数 8 → 9（新增 `hosts`），并断言
     `hosts` 在列——它是这份清单的守卫，不更新就等于放行遗漏。

## Revisit

- **顺序若需反转**：改 `hostDisplay.ts` 一处即可（这正是单一入口的价值）；但要注意
  `planExecuteExport` 的 CSV 与排序面会跟着变——那 1 条已更新用例是它的判据。
- **`DeviceOverview` 的 host 查询权限**：查看者若无主机列表权限，查询失败 → 回落 host_id
  （与旧行为一致，不空白）。若将来想让它也有显示名，走「`/plan-runs/{id}/devices` 响应带
  host 显示名字段」，需同步 `frontend/src/utils/api/types.ts` 与后端 schema（硬不变量）。
- **host 事件通道**（后端 `host_changed` + 前端失效）：#496 范围内；本轮靠 20s 轮询 + 重连
  失效兜住。若将来主机规模（60+ host）让 20s 轮询的请求量成为问题，改推送是正解。
- **`online: boolean | null` 是新的类型契约**：目前只有 `DeviceNodeRail` 一个消费方
  （已 grep 确认）。若将来别处消费 `nodeSummaries.online`，必须显式处理 `null`——不要让
  `!node.online` 悄悄把「未知」当「离线」。
