# 起草 ADR-0064：Agent host 固件本地缓存、全局传输限流与心跳 NFS 隔离

Status: implemented
Class: architecture

## Decision

承接 #4076 专题讨论与 #4054 / #4075 / #4006 §8 / #4042 的统一问题面分析，按 Owner 2026-11-20 方向裁决起草 [ADR-0064](../../adr/ADR-0064-host-firmware-cache-and-transfer-pacing.md)（v0.1），经 U0 六轮独立复核通过（`0b85f3828`，PR #4085 复核评论 #7074284507）并于 2026-11-22 经 Owner 裁决转为 `Accepted v1.0`，同步登记 `docs/adr/README.md`、`docs/design/2026-storage-roles-and-aliases.md` 与 `docs/design/2026-semantic-ownership.md`（`flash-tool` 行补锚）：

- **D1 发布期多 profile 不可变摘要契约与完备 `profile × 封包状态` 规则**：上架工具生成单份多 profile `staging_manifest.json`（MTK 用 `mtk`，`P525AA`/`P532AA` 等展锐共用目录并存 `unisoc_fastboot` 与 `unisoc_bsl`；规范化摘要 `[[relpath, size, sha256], ...]` 收归共享契约模块 `backend/agent/contracts/firmware_manifest.py`，不引入可执行位；展锐 BSL 预裁剪镜像写入 `images/bsl/super_trimmed.img` 且不改原镜像，存量目录允许一次只增不改首次封包；已封包缺 profile 返回 `firmware_profile_not_found`，未封包 `mtk`/`unisoc_fastboot` 用 `<safe_label>_unsealed_<digest16>`，未封包 `unisoc_bsl` 显式 fail-closed 返回 `firmware_unsealed_not_supported` 且全量在用 BSL 目录封包为计划重指前置）。
- **D2 Agent L1 统一缓存基座与脚本运行契约**：`backend/agent/firmware_cache.py` 按本地块设备抽象为一级本地盘 + 异盘溢出（原生兼容纯 SSD、纯 HDD、SSD+HDD），配合 `avail_gb`/`writable_percent` 水位守卫、`<safe_label>_<profile>_<digest16>` 缓存键、`flock(LOCK_SH/LOCK_EX)` pin 安全 LRU（含 `LOCK_EX`→`LOCK_SH` 降级后持锁复核就绪标记）、写入 `ionice -c 3` 与后台定时 TTL（默认 14 天）清理；两盘均不足默认 fail-closed。`pipeline_engine.py` 显式注入 `<agent_dir>` + `<agent_dir.parent>` 双路径 `PYTHONPATH`，配合 `firmware_cache_v1` / `requires_agent_capabilities` 门控与脚本启动前 `FIRMWARE_CACHE_API_VERSION` 校验闭环版本交错。
- **D3 全局传输并发闸门**：控制面基于 Redis 提供带 `lease_token` 的瞬时传输并发许可（默认 2 台并发，不新增 PG 业务表）；Agent 以一级缓存根 `.host-transfer.lock` 串行本机跨源拉取（防止同机双固件并发互相误释许可）；许可接口不可达超限时默认 fail-closed（`transfer_permit_unavailable`），仅显式 `allow_permit_bypass=true` 才记录 `permit_degraded=true` 放行。
- **D4 全刷机族统一接入**：MTK（ADB / VBUS，V5 / V6）、Unisoc Fastboot、Unisoc BSL 统一在主机刷机锁外按对应 profile 调用 `agent.firmware_cache` 并持 `LOCK_SH`，退役 BSL 私有 `/var/tmp/stp_flash`（回滚窗口关闭后由运维在 B10 收尾步骤一次性清扫，闭环 #4006 §8），且不向 `pipeline_engine.py` 新增 `script_name` 特判（#3779）。
- **D5 心跳非阻塞隔离与载荷克制**：心跳挂载检查改读 `/proc/self/mountinfo` + 独立后台探测线程，禁止主循环同步调用任何触达非本地文件系统的系统调用（闭环 #4054 与 `2026-storage-roles-and-aliases.md` §4.4 复议触发器），维持 NFS `hard` mount，且不向心跳载荷新增固件缓存明细或 vbus 静态检查字段。
- **D6 范围裁剪**：第一期不做主机级事前后台预置 UI/编排与 vbus 全 Web UI 化（U2–U4 接口保持与触发方式解耦）；`#4075` 非缓存项与 `#3976` 拆出并行推进。

## Alternatives

- **由 `pipeline_engine.py` 在启动脚本前替脚本预拉固件**：放弃。设备型号分流（`latest.json`）与 `skip_if_current` 早退归各刷机族内部，引擎层解析会破坏 `#3779` 并浪费传输；改为引擎正式保证双路径 `PYTHONPATH` + 能力门控 + 脚本子进程锁外调用 L1 模块。
- **在刷机脚本内复制或抽共享 helper 实现缓存**：放弃。磁盘配额、跨盘溢出、Pin 安全 LRU、`.host-transfer.lock` 与定时清理属于 Agent L1 宿主能力（#3498），脚本层无法统一治理。
- **许可接口不可达时默认降级放行**：放弃（U0 第 1 轮复核指出控制面 I/O 饱和时许可接口最易超时，默认放行会在最需要限流时取消限流并放大惊群）；改为默认 fail-closed + 显式 `allow_permit_bypass=true` 逃生阀。
- **第一期同步上线控制面事前后台预置与前端面板**：推迟。15–20 台规模下全局 `K=2` 限流 + 本地缓存已消除 90% 痛点；待 50+ 台冷拉墙钟成为瓶颈时再按 Revisit #1 开启。
- **把 vbus 主机静态就绪与固件缓存明细塞进心跳**：放弃。加重高频心跳负担；静态依赖归 `flash-prereqs` 维护面，缓存详情按需查询。
- **把 NFS 改为 `soft` mount**：放弃。`EIO` 传播路径未经事件上送链验证，心跳非阻塞探针即可隔离故障且不改变挂载语义。

## Verification

- 本 PR 仅包含 ADR 草案、索引更新、存储角色说明同步与本 Agent Note，无生产代码改动。
- 运行 `check_governance_surface.py --base origin/main --check`、`check_tool_manifest.py --base origin/main`、`check_script_packages.py` 与 `scripts/run_gates.py check:quick`（`env-inventory`、`ruff`）验证 ADR 头部字段（S12/S15）、索引与 M7 看板一致、链接有效、Note 四节与 S10 头合规；`eslint` 与全部 required CI 交由 GitHub Actions 执行。
- 实机复核 `env -i PYTHONPATH=<agent_dir>:<agent_dir.parent>` 下 `import agent.settings` 与 `import config` 双可用；按 `docs/development/writing-conventions.md` §5 逐项自查 ADR-0064 `## 2. 决策` 节全部规则句的主语、强度词与术语推荐名。

## Revisit

- 50–150 台规模下波次错峰等待与步骤墙钟成为瓶颈时，启用 ADR-0064 §7 Revisit #1 的异步主机预置；千兆单源物理吞吐成为瓶颈时，启用 Revisit #2 的 HTTP/P2P 传输适配器；vbus 全 Web UI 闭环立项时，按 Revisit #3 扩展主机维护面 `flash-prereqs`。
