# 设备列表：`limit` 是单次响应护栏，不是 fleet 总量（#3131）

Status: implemented
Class: bug-fix

- 日期：2026-09-22
- 关联：`#3131`（本单）、`#3123`（同页面的排序稳定性，#3126 已合入；其修复同时让
  `fetchAllDevices` 的翻页变正确）、`#496`（列表基础设施，终态归属）、
  `#2623`（同族先例：全量内嵌导致响应过大）、`ADR-0026`（规模目标）
- 落点：`frontend/src/utils/api/devices.ts`（`fetchAllDevicePages`）、
  `frontend/src/pages/devices/DevicesPage.tsx`、`frontend/src/components/device/ExpandableDeviceTable.tsx`、
  `frontend/src/components/schedule/DeviceMultiSelect.tsx`、
  `backend/api/routes/devices.py`（`_DEVICE_LIST_MAX_LIMIT`）

## Decision

**不动 `le`，改消费方。** 立一条不变量：

> `GET /devices` 的 `limit` 是**单次响应体积护栏**，不等于 fleet 总量。要全量必须按
> `skip` 翻页，并用响应里的 `total` 判「有没有拿全」——`items.length` 不是总数。

之前有两个消费方把一次请求当成全量，越界即**静默少设备**：

| 位置 | 旧写法 | 越界后果 |
|---|---|---|
| `DevicesPage` | `api.devices.list(0, 1200,…).then(r => r.items)` —— 丢弃 `total` | 「全部设备」卡显示已加载条数，用户看不出还差多少台 |
| `DeviceMultiSelect` | `api.devices.list(0, 1200).then(r => r.items)` —— 同样丢弃 `total` | **排程选设备静默少设备**——不报错，计划因此少挂设备 |

为什么这不是"将来"的事：

| 事实 | 值 |
|---|---|
| 实测（2026-09-22 只读） | 48 台主机 / 862 台设备，每主机 min 3 / max 46 / 均值 18 |
| 已挂 ≥25 台的主机 | 4 台（最多一台 46） |
| 按设计密度 25 台/主机 | 48 × 25 = **1200，与上限恰好相等**——现有主机挂满即用尽，第 49 台溢出 |
| ADR-0026 规模目标 | 60+ host / 1000+ device ⇒ 60 × 25 = **1500 > 1200** |
| 上限沿革 | `1736d98f`（2026-05-03）把 `le=200` 抬到 `le=1200`，理由是 "cover all devices in single page" |

即：**护栏值已落在平台自己承诺的规模包线之内**，而 4 个半月前才抬过一次——说明病症
是"消费方假设一次请求装得下整个 fleet"，抬数值只是把墙往后挪。

改动：

1. `fetchAllDevicePages(filters)` —— 按 `total` 翻页，并**连同 `total` 一起返回**
   （旧 `fetchAllDevices` 只返回数组，调用方拿不到总数，这正是上面两处误用 `items.length`
   的根源）。`fetchAllDevices(status?)` 保留为它的薄封装，签名不变。
2. `DevicesPage` 改用它，`total` 一路传到表格：「全部设备」卡显示**真实总数**；
   `items.length < total` 时表格顶部显示横幅，明说「状态统计与搜索/筛选只作用于已加载
   部分，未加载的设备不会出现在结果里」——否则「搜不到」会被读成「不存在」。
3. `DeviceMultiSelect` 改用 `fetchAllDevices()`。它原本与 `PlanExecutePage` **共用
   `deviceKeys.all()` 这个缓存键但 queryFn 语义不同**（一个被上限截断、一个翻页拉全），
   谁先挂载谁的数据进缓存——同一页面在不同访问路径下看到的设备集可能不同。统一后分歧消失。
4. 后端不改数值，只把 `le=1200` 提成 `_DEVICE_LIST_MAX_LIMIT` 并在定义处写明这条不变量，
   让下一个"我要拿全量"的人在读签名时就看到它（该注释即本单的主要防线）。

## Alternatives

- **按 ADR-0026 推导一个更大的 `le`（如 120 host × 25 = 3000）**：放弃。这是把墙往后挪
  而非拆墙——设备数继续长就继续撞；且单次响应会从 473 KB（实测 862 台）/ 220 ms 涨到
  ~1.6 MB，而 `/devices` 每 10 秒轮询一次。`#2623` 已把"全量内嵌导致响应过大"在本仓立为
  缺陷，说明**加体积不是可接受的解法**。
- **把 `le` 做成 env 可配**：放弃。本仓 limit 护栏的既有形态是模块常量
  （`plan_runs._MAX_EVENTS_LIMIT`），env 用于行为开关（超时/路径/鉴权）而非校验边界；
  更要紧的是 env 化会让运维**在不带 ADR 与密度推理的情况下**无声抬高护栏——这个数值的
  依据是"平台承诺的规模 + 单次响应体积"，不是部署参数。
- **只加截断提示、不翻页**：放弃。提示是必需的兜底（本单保留了），但它不是修复——
  设备页仍然看不到第 1201 台。
- **把 `/devices` 改服务端分页**：这是真终态，但属 `#496`（分页 + 虚拟滚动 + 轮询策略）
  的范畴，其写死的重议触发条件正是「60+ host / 1000+ device」。本单只解"撞上限即静默
  丢设备"这个正确性问题，不抢它的设计。

## Verification

- `frontend/src/utils/api/devices.test.ts`：`fetchAllDevicePages` 带回 `total`、
  翻页时逐页透传 `project_key`、`unassigned` 哨兵、以及**服务端 `total` 不可达时靠空页
  出口**（不死循环，返回 `items.length < total`）。
- `ExpandableDeviceTable.test.tsx`：「全部设备」卡显示 `totalCount` 而非已加载条数；
  `devices.length < totalCount` 时横幅出现且文案含「未加载的设备不会出现在结果里」；
  拿齐时**不出现**横幅。
- **变异自证**：把 `total: totalCount ?? devices.length` 改回 `devices.length`、并把横幅
  条件置 `false`，两条新用例**均红**（`Test Files 1 failed / Tests 2 failed`），恢复后绿。
- 全量前端 `vitest run`：**131 files / 1088 tests passed**；`pytest
  backend/tests/api/test_devices.py + test_host_retirement_read_filters_1804.py`：35 passed。
- `scripts/run_gates.py check:quick`：14 门全绿（首轮 eslint 抓到本单助手里的
  `no-useless-assignment`，已改为单页起始 + while 结构）。
- 未做：真机浏览器确认（横幅与卡片计数均由 jsdom 用例覆盖；生产 862 台 < 上限，
  横幅在当前数据下不会出现——它的守卫对象是越界那一刻）。

## Revisit

- **新增任何列表消费方一律禁用 `list(0, N)` 形态**：要么 `fetchAllDevicePages`/`fetchAllDevices`，
  要么显式把 `total` 用起来。`le` 不是"能装下 fleet"的承诺。
- **真要动 `le` 时，理由必须是"单次响应体积"**（如引入轻量投影），不能是"装不下 fleet"——
  后者是消费方缺陷，改护栏只会换个数字继续犯。
- **设备数越过 1200 之后**：`/devices` 变成每 10 秒 2 次请求（473 KB + 余量），体积成为
  主要矛盾，也就是 `#496` 该启动的时刻。届时 `fetchAllDevicePages` 应被服务端分页替换，
  而不是继续放大页大小。
- `DeviceMultiSelect` 目前**没有测试覆盖**（本次改动是 queryFn 换用已测助手，风险低）；
  若后续要给排程选择器加交互测试，从这里补。
