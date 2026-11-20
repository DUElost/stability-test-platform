# 起草 ADR-0064：Agent host 固件本地缓存、全局传输限流与心跳 NFS 隔离

Status: proposed
Class: architecture

## Decision

承接 #4076 专题讨论与 #4054 / #4075 / #4006 §8 / #4042 的统一问题面分析，按 Owner 2026-11-20 裁决起草 [ADR-0064](../../adr/ADR-0064-host-firmware-cache-and-transfer-pacing.md)（Proposed v0.1），并同步登记 `docs/adr/README.md` 与 `docs/design/2026-storage-roles-and-aliases.md`：

- **D1 发布期不可变摘要契约**：上架工具生成 `staging_manifest.json`（复用 `artifact_digest.py` 算法、精确最小文件集、展锐 BSL 发布期预裁剪 `super.img`），根治缓存命中重读 NFS 与选拷贝漏文件问题。
- **D2 Agent L1 统一缓存基座**：`backend/agent/firmware_cache.py` 按本地块设备抽象为一级本地盘 + 异盘溢出（原生兼容纯 SSD、纯 HDD、SSD+HDD），配合 `avail_gb`/`writable_percent` 水位守卫、`flock(LOCK_SH/LOCK_EX)` pin 安全 LRU、写入 `ionice -c 3` 与后台定时 TTL（默认 14 天）清理；两盘均不足默认 fail-closed，不静默回落 NFS。
- **D3 全局传输并发闸门**：控制面基于 Redis 提供瞬时传输并发许可（默认 2 台并发），仅在 cache miss 跨 NFS 拉取时获取，实现零惊群自动错峰流水线且不新增 PG 业务表。
- **D4 全刷机族统一接入**：MTK（ADB / VBUS，V5 / V6）、Unisoc Fastboot、Unisoc BSL 统一在主机刷机锁外缓存并持 `LOCK_SH`，退役 BSL 私有 `/var/tmp/stp_flash` 缓存，且不向 `pipeline_engine.py` 新增 `script_name` 特判（#3779）。
- **D5 心跳非阻塞隔离与载荷克制**：心跳挂载检查改读 `/proc/self/mountinfo` + 独立后台探测线程（闭环 #4054 与 `2026-storage-roles-and-aliases.md` §4.4 复议触发器），维持 NFS `hard` mount，且不向心跳载荷新增固件缓存明细或 vbus 静态检查字段。
- **D6 范围裁剪**：第一期不做主机级事前后台预置 UI/编排与 vbus 全 Web UI 化，仅预留后端接口；`#4075` 非缓存项与 `#3976` 拆出并行推进。

## Alternatives

- **在刷机脚本内复制或抽共享 helper 实现缓存**：放弃。磁盘配额、跨盘溢出、Pin 安全 LRU 与定时清理属于 Agent L1 宿主能力（#3498），脚本层无法统一治理。
- **第一期同步上线控制面事前后台预置与前端面板**：推迟。15–20 台规模下全局 `K=2` 限流 + 本地缓存已消除 90% 痛点；待 50+ 台换版耗时成为瓶颈时再按 Revisit #1 开启。
- **把 vbus 主机静态就绪与固件缓存明细塞进心跳**：放弃。加重高频心跳负担；静态依赖归 `flash-prereqs` 维护面，缓存详情按需查询。
- **把 NFS 改为 `soft` mount**：放弃。`EIO` 传播路径未经事件上送链验证，心跳非阻塞探针即可隔离故障且不改变挂载语义。

## Verification

- 本 PR 仅包含 ADR 草案、索引更新、存储角色说明同步与本 Agent Note，无生产代码改动。
- 运行 `check_governance_surface.py` 与 `scripts/run_gates.py check:quick` 验证 ADR 头部字段（S12/S15）、索引与 M7 看板一致、链接有效、Note 四节与 S10 头合规。
- 按 `docs/development/writing-conventions.md` §5 逐项自查 ADR-0064 `## 2. 决策` 节规则句强度词与术语推荐名。

## Revisit

- 独立复核通过并经 Owner 裁决后，将 ADR-0064 状态从 `Proposed v0.1` 转为 `Accepted v1.0`，并在 `docs/design/2026-semantic-ownership.md` `flash-tool` 行补锚。
- 50–150 台规模下波次错峰等待成为瓶颈时，启用 ADR-0064 §7 Revisit #1 的异步主机预置；千兆单源物理吞吐成为瓶颈时，启用 Revisit #2 的 HTTP/P2P 传输适配器。
