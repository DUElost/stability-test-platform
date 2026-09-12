# ADR-0038：主机退役语义（Host Retirement Semantics）

- 状态：**Proposed**
- 版本记录：v0.1（2026-09-12 初版，#796/#937 Revisit 触发）
- 优先级：P2
- 目标里程碑：M7
- 日期：2026-09-12
- 决策者：平台研发组
- 标签：生命周期, 软删, 数据保留, 主机, 运维
- 关联：[#796](https://github.com/DUElost/stability-test-platform/issues/796)（触发：DELETE 级联清历史）、[#937](https://github.com/DUElost/stability-test-platform/issues/937)（硬删预检，PR #1380）、[#827](https://github.com/DUElost/stability-test-platform/issues/827)（审查总表）、[#961](https://github.com/DUElost/stability-test-platform/issues/961)（R04 台账）、ADR-0025（存储归档闭环）、ADR-0035（主机身份与凭据）、ADR-0019（设备租约与容量）、#1249/#1250（维护窗口与升级门禁）

## 1. 背景

### 1.1 问题定性：不是「删除要不要挡」，而是用完的主机没有终态

#937（PR #1380）已让 `DELETE /hosts/{id}` 在**有历史依赖时 409 保数据**
（活跃 Job / 历史 Job / 设备 / PlanRunHost 投影，`hosts.py:477-549`）——这
与 #796 的决策一致且正确。但它同时产生一条运维死路：

- 跑过 Job 的主机 → 有 `job_instance` 行 → 永久 409；
- 有过设备的主机 → 有 `device` 行 → 永久 409（**设备行无任何删除入口**，
  `devices.py` 无 DELETE 路由，且 `device.serial` 全局唯一、为租约/Job 的
  历史锚点）；
- 即：**任何真实使用过的主机都不可删除，且当前没有任何状态能表达「这台
  机器已不再使用」**。`status` 是存活信号（ONLINE/OFFLINE/DEGRADED），
  OFFLINE 只表示「当前不可达」，会被下一次心跳改写。

现实需求（硬件退役/换新/下线）：机器不再参与调度，但历史必须完整保留。

### 1.2 事实核验（2026-09-12，静态盘点）

- `host.id` 为 IP 派生（`backend/core/host_identity.py:10`，`198.51.100.6`
  → `198-51-100-6`，冲突追加短后缀）；心跳按 id 找行、缺失即建、找到即原
  位复活（`agent_api.py:806-829`）——**退役语义必须处理「同 id 重连」**；
- 设备归属由心跳按 serial 全局 re-home（`heartbeat.py:403`，活跃租约阻断
  `:385-392`）——设备物理搬移至新主机无需本 ADR 额外机制；
- 派发与认领都按存活状态收口：dispatcher 排除 OFFLINE
  （`plan_dispatcher_sync.py:155`），claim 要求 ONLINE（`agent_api.py:401`）——
  退役只要同时被这两处识别即可落在真正的风险面上；
- 全模型无 `deleted_at` / `is_archived` / `retired_at` 先例（零命中）；
  全仓无 device 删除路径（零命中）；
- 命名冲突：`Host.extra['archive']` 已被 ADR-0025 存储归档遥测占用
  （`agent_api.py:3167`）——本 ADR 用「退役 retire」，不用「归档 archive」。

### 1.3 约束

- **历史不可清**（#796/#937 决策）：本 ADR 不得提供任何静默或显式清空
  job/device/plan_run_host 历史的路径；历史 retention/purge 属 ADR-0025
  存储生命周期议题；
- **生命周期与存活正交**：`status` 的 owner 是心跳（Agent 如实上报），
  生命周期不得复用 `status`，否则在线机器会静默改写运维决定（同
  ADR-0026 status/phase 正交先例）；
- 退役必须**可解除**（误操作可恢复）、全程审计；
- 不引入新的保留期/purge 机制。

## 2. 决策

- **D1 单一生命周期真源**：`host` 表新增可空列 `retired_at`
  (TIMESTAMPTZ) / `retired_by` (String) / `retire_reason` (Text)，
  `retired_at IS NOT NULL` 即退役；NULL = 在用。additive 迁移，无需回填
  （存量全部默认在用）。**不新增 `HostStatus` 枚举值**。
- **D2 「删除」与「退役」分家**：`DELETE` 语义与 #937 预检原样不变（硬删
  仅对干净主机开放）；新增 `POST /hosts/{id}/retire` 与
  `POST /hosts/{id}/unretire`（admin + 审计）。退役前置 = 现有预检中与
  瞬时状态有关的两条：非 ONLINE、无活跃 Job；**不检查**历史依赖（这正是
  退役要承接的场景）。unretire 无前置。
- **D3 退役即终态，不做带历史硬删**：退役不触发任何删除；有历史的退役主机
  保留全部历史行与 FK；不提供 `force` 硬删变体。彻底清除历史的需求一律
  回到 ADR-0025 存储生命周期裁决，**不回落本 ADR**。
- **D4 生命周期与存活正交（心跳联动）**：退役不改写 `status`。被退役主机
  Agent 再心跳（退役后又启动/重启）→ **如实记录**（`last_heartbeat`、
  `status`、版本等照常更新）、**保持退役**、触发**单次告警**（去重）+
  UI「已退役但仍在心跳」异常徽标。风险面（派发/认领）已 fail-closed
  （D5），心跳不制造新失败模式。
- **D5 派发面收口（不变量）**：退役主机不进入派发快照、不可 claim、不计入
  在线容量与库存统计、默认不出现在 `GET /hosts`（新增 `include_retired`
  参数才显示）；控制面动作（热更新/安装/批量刷新）对退役主机拒绝
  （仅 unretire 除外）。实现单必须为每个过滤点配回归测试。
- **D6 设备与身份**：退役主机的设备行保留（历史锚点），不随退役删除；设备
  物理搬移由现有心跳 re-home 处理（租约阻断语义不变）。**同 IP 换机 = 同一
  主机身份**（id 本就 IP 派生）→ 运维语义为 unretire，`boot_id` /
  `agent_instance_id` 变化在详情与审计可见；不做 successor 自动分裂。
  ADR-0035 的 per-host 凭据落地后重审换机注册流程（见 Revisit）。
- **D7 显式不做**：不做 `deleted_at` 软删列（见 §3）；不改 FK
  SET NULL/CASCADE；不引入保留期/purge；不做设备退役（独立议题，设备行
  当前无删除面，需求出现时单独提案）。

## 3. 备选与否决

- **复用 `status=RETIRED` 枚举值**：否决——`status` owner 是心跳，下一次
  心跳会把 RETIRED 改写回 ONLINE，生命周期被在线机器静默撤销；且 OFFLINE
  （不可达）与退役（不再使用）语义不同，合并后无法恢复任一。
- **`deleted_at` 软删列**：否决——命名暗示「待删队列/将来可 purge」，与
  「退役是历史终态」矛盾；过滤工作量与 `retired_at` 相同但语义错误。
- **带历史 force 硬删（双重确认）**：否决——与 #796/#937 的保历史决策直接
  冲突；审计与二次确认无法弥补历史不可回灌（Job/租约/投影引用会全部错位）。
- **导出归档包后再硬删（explicit archive flow）**：否决——需单独设计导出
  格式/回灌/一致性，平台本身已有历史查询能力，收益低；且「导出后删源」
  仍不满足可追溯。
- **FK 改 SET NULL**：否决——历史行 host 归属丢失，正是 #796 要保护的对象。
- **心跳 409 fail-closed**：本轮否决——心跳还承载 backpressure / 版本门禁
  等响应，拒绝会给 Agent 制造新的失败模式；风险面已由 D5 收口。保留为
  Revisit 第 1 条。
- **心跳自动复活（自动解除退役）**：否决——退役不 sticky，运维决定会被
  在线机器静默撤销。

## 4. 影响与不变量

- **不变量 1**：`retired_at IS NULL` ∧ `status=ONLINE` ⇔ 可被派发/认领；
  派发快照、claim、统计与容量、默认列表、控制面动作五类读/写路径必须
  显式过滤退役。
- **不变量 2**：retire/unretire 不改写任何历史行（job/device/plan_run_host/
  lease 及全部 FK 原样）；`DELETE` 预检不变。
- **不变量 3**：retire/unretire 必须审计（who/when/reason）；退役主机心跳
  告警按主机去重（只响一次，除非状态震荡——沿用现有告警去重惯例）。
- **迁移**：三列 additive nullable，无回填；回滚 = drop 三列（无状态依赖，
  退役期间不产生新数据形态）。
- **运维代价**：退役要求先停 Agent（非 ONLINE 前置）；同 IP 换机需
  unretire 一步；活体退役主机需人工处置（告警提示）。
- **前端面**：HostsPage 增加退役过滤/徽标与 retire/unretire 入口；现状
  `api.hosts.remove`（`frontend/src/utils/api/hosts.ts:25`）无 UI 调用点，
  随本 ADR 实现单决定保留或删除。

## 5. 验收与 Revisit

- **验收（实现单另开，ADR Accepted ≠ 实施启动）**：
  1. `POST /hosts/{id}/retire` / `unretire` 可用且审计齐全（含前置 409）；
  2. D5 五类过滤点各有回归测试，且**反例实证**成立（临时移除任一过滤点
     → 对应用例转红）；
  3. 活体退役主机心跳 → 保持退役 + 告警 + 徽标，有测试；
  4. #937 的 DELETE 预检无回归（现有用例保持全绿）。
- **Revisit**：
  1. 若观察窗口内「活体退役主机」长期残留或告警被证明无效，升级为心跳
     409 拒绝（Agent 侧失败模式需同步评估）；
  2. ADR-0035 per-host 凭据落地后，重审同 IP 换机/身份继承与 successor
     注册流程；
  3. 若出现高频「退役后彻底清除」需求，在 ADR-0025 存储生命周期下裁决
     purge 机制，不回落 force 硬删；
  4. 设备退役/清理入口出现真实需求时独立提案（本 ADR 只保证设备不因退役
     被删）。
