# B10 U1：心跳非本地 FS 非阻塞隔离与多 profile 固件上架摘要契约

Status: implemented
Class: feature

关联：[#4086](https://github.com/DUElost/stability-test-platform/issues/4086)（批次方案 v1.5）、
[#4054](https://github.com/DUElost/stability-test-platform/issues/4054)、
[#4042](https://github.com/DUElost/stability-test-platform/issues/4042)、
ADR-0064 Accepted v1.0 D1/D5（[PR #4085](https://github.com/DUElost/stability-test-platform/pull/4085)；合入 `main` 后路径为
`docs/adr/ADR-0064-host-firmware-cache-and-transfer-pacing.md`）、
PR（本单）。

## Decision

按 `#4086 §3 U1` 与 ADR-0064 D1、D5 落地两块互不耦合的前置契约，供后续 U2–U4 消费：

1. **D5 心跳非阻塞隔离（#4054）**
   - 新增 `backend/agent/mount_health_probe.py`：挂载在位只读 `/proc/self/mountinfo`
     （正确解码 `\040` 等转义）；可达性/可读写迁入后台线程（同构 `KernelUsbWatch`
     非阻塞快照），快照带 `probed_at` 与 `probe_stale`。
   - `HeartbeatThread._tick` 改为 `MountHealthProbe.snapshot()`；`heartbeat.check_mounts`
     改为 mountinfo 在位检查，禁止主循环 `ismount`/`exists` 等触达非本地 FS 的调用。
   - 不新增固件缓存明细或 vbus 静态字段到常规心跳载荷。

2. **D1 共享契约与上架摘要工具（协同 #4042）**
   - `backend/agent/contracts/firmware_manifest.py`：纯标准库、无包内相对导入；
     `schema_version=1`、多 profile 结构、确定性 `content_digest`（按 `relpath` 排序后的
     紧凑 JSON UTF-8 SHA-256，不含可执行位）；拒绝绝对路径、`..` 与越界软链。
   - `tools/dev/stage_firmware_manifest.py`：MTK V5/V6 → `mtk`；展锐 →
     `unisoc_fastboot`，具备完整 `fdl.fdl1/fdl2` 时同步 `unisoc_bsl` 并原子写入
     `images/bsl/super_trimmed.img`（算法与 `get_sparse_trimmed_size` 逐字节一致，
     不改源镜像）；存量目录只增不改首次封包，原子写入 `staging_manifest.json`。

## Alternatives

- **把 NFS 改为 soft mount**：否决（ADR-0064 §3）；`soft` 会把 `EIO` 传播到上送/归档链。
- **在各刷机脚本内复制摘要算法**：否决；上架工具与 Agent 必须共享同一契约实现（D1）。
- **把可达性探测留在心跳主循环加超时**：否决；NFS hard mount 上的 `D` 状态无法被
  用户态超时打断，只能后台隔离。

## Verification

- `./scripts/project_python.sh tools/dev/check_governance_surface.py --base origin/main --check`
  → **[OK] 治理面结构检查通过（阻塞项全绿：S1–S15、S5x）**
- `./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/test_mount_health_probe_b10_u1.py backend/agent/tests/test_firmware_manifest_b10_u1.py backend/agent/tests/test_heartbeat_thread_adb_server_conflict.py backend/agent/tests/test_heartbeat_parallel_probe.py backend/agent/tests/test_return_debts_u4_3729.py -q`
  → **28 passed**
- `./scripts/project_python.sh scripts/run_gates.py check:quick`
  → **[OK] check:quick (16 gates)**

## Revisit

- U2 合入后由 `FirmwareCacheManager` 消费本契约的 `profiles` / `content_digest`；
  未封包兼容与 `unisoc_bsl` fail-closed 语义属 U2/U4，本单元不实现缓存管理。
- 生产在用 BSL 目录的首次封包与计划重指属 `#4086 §5` 激活步骤，不在本 PR 执行。
