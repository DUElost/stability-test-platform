# #105 B1：隔离控制面上的五实例冒烟

Status: implemented
Class: testing

## Decision

2026-09-26 在独立 checkout 的 Compose 项目 `stp-b1-105` 中完成 #105 的第一关：
独立 PostgreSQL/Redis 卷、后端端口 28000、5 个有独立 Docker 网络地址的 Agent 容器，
各上报 1 台**合成设备**。每个 Agent 使用独立 tmpfs 状态目录、512 MiB 内存硬限与
0.5 CPU 上限；生产控制面、数据库、Agent 机队与真机均未接入。

先尝试“同一 Linux 网络命名空间内 5 个进程 + 不同 `AGENT_INSTALL_DIR`/`HOST_ID`”：
5 个 WebSocket 身份均接入、5 台设备均上报，但数据库只有 **1 条 host / 5 条 device**。
原因在 `backend/api/routes/heartbeat.py` 的首次心跳路径：找不到新 `HOST_ID` 时先按
`host.ip`/`host.ip_address` 复用既有 Host；进程共享本机 IP 会折叠成同一条记录。
因此 #105 原文提出的两个 env 旋钮还不足以模拟多 host，必须再隔离网络地址或使用等价的
显式 host 身份夹具。本次选独立容器网络命名空间，不改生产身份语义。

## Alternatives

- 继续在同一网络命名空间里增加进程：只会增加 device，不能增加有效 host 数。
- 在生产控制面注册合成 host：会污染业务事实与在线规模指标，违反 #105 的隔离边界。
- 直接冲 44/60/100/150 档：五实例接入与资源预算未先证明，不能跳过第一关。

## Verification

- 基线 `origin/main=7301eaa4`；新 Compose 项目使用独立 DB 卷与端口，`/health=200`。
  dev 后端容器限 3 GiB、PG 限 768 MiB、Redis 限 256 MiB。
- 同宿主五进程反例：dev 库 `host=1`、`device=5`；代码落点为
  `backend/api/routes/heartbeat.py` 的 `_process_heartbeat_with_db` 中按 IP 查已有 Host。
- 容器复测：dev 库 `host=5`、`device=5`，**5/5 ONLINE**；Host IP 与
  `last_agent_instance_id` 各有 5 个不同值，设备分属 5 个 Host；每容器状态目录已创建。
- `docker stats --no-stream` 五个 Agent 为 48.12–50.12 MiB / 512 MiB；
  约 25 秒后再查仍 5/5 ONLINE，最老心跳距查询 12 秒，五个容器均在运行。
- 首个进程因主检出 venv 无 `websocket-client` 仅 HTTP 可用；改用独立 Agent 环境后，
  dev 后端默认 CORS 未包含专用后端端口而拒绝 WS。只在隔离 Compose 覆盖文件中加入
  `http://127.0.0.1:28000` 后，服务端记录五个不同 host ID 的 `/agent` 连接。
  这些是夹具配置问题，不计为产品容量缺陷。
- `check:quick` 首次因本分支落后于新增两个 tool 族的 `origin/main`，在 append-only
  manifest 检查处失败；rebase 到 `53da2a43` 后重跑 **17 gates 通过**。
  `schema-at-head` 因未配置 `DATABASE_URL` WARN 跳过，未据此宣称迁移验证。
- 取证后停止并移除本次五个 Agent 容器、隔离 Compose 项目/卷/镜像及临时凭据文件；
  原有 `stp-dev` 项目仍在运行。
- 本轮**未测**准入/claim/续租/批量终态、25 device/host、44 host 以上档位或真机 ADB；
  dev 后端的 SSH host health probe 对合成 host 报配置错误，后续阶梯须先给出隔离处理口径。

## Revisit

下一档先固定工作负载、观测窗与阈值，再在隔离控制面跑 44→60→100→150 host；
每档显式写合成设备数，测准入、续租、终态、DB 池、内存与故障恢复。
五实例稳态约 50 MiB/Agent 只是首轮下界，不能把它线性外推成 150 host 的资源保证；
本机同时承载生产控制面，扩大档位前需选择有足够硬限与余量的独立执行资源。
本记录只完成 #105 的“五实例状态隔离 + 内存”首关，不关闭 #105 或 #106。
