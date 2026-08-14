# 共享存储健康页聚焦化布局（全宽 + 按角色收敛指标）

Status: implemented
Class: feature

## Decision

`/storage` 前端改为全宽（`PageContainer fullBleed`，去掉 `max-w-7xl`
两侧留白，仅保留页内 `p-4 lg:p-6`），并按两个面板各自的功能定位
收敛信息，后端 `/api/v1/stats/file-server` 契约不变。

- 控制面面板只保留：CPU/内存负载条、中心存储客户端挂载
  （挂载状态/写权限/路径）、Prometheus 状态。移除 Load1m、CPU 核心、
  总内存、运行时间、磁盘读写、网络收发等主机噪声。
- 存储机面板只保留：4 张 KPI（存储使用率/可用容量/inode 使用率/
  NFS 请求）+ NFS 服务（状态/Export/RPC 错误/Stale handle）。
  移除存储机自身 CPU/内存等主机负载。
- 顶部原「可用容量/存储使用率/NFS 请求/Agent 挂载」混合卡并入
  两个面板与 Agent 摘要行。
- 历史图从「容量 + CPU + 内存」三线收敛为「存储容量趋势」单线
  （merge 按时间戳合并、不区分节点来源，多线含义含糊）。
- Agent 挂载与设备日志盘合并表保持全宽不变。

## Alternatives

- 只改全宽、保留各面板全部监控字段：不满足「去除不重要部分」，
  双栏信息密度依旧偏低。
- 历史图保留三线并按节点拆分：需要后端历史结构变更，收益低，暂缓。
- 存储机面板保留精简 CPU/内存：过渡期与控制面同源、信息重复；
  切盘后确有需要再补回。

## Verification

- `npm run type-check`、`npm run lint`、`npm run build`
- `npx vitest run src/pages/storage/FileServerPage.test.tsx`
  （挂载统计断言从独立值改为摘要句 `中心存储挂载 1/1`）

## Revisit

中心存储迁离 8.202 后（#205）：若存储机需要单独展示自身负载，
再在右栏补回精简 CPU/内存；容量趋势届时也一并切到存储机口径。
